"""remote/setup.py: the steps from nothing to a paired phone."""

import contextlib
import io
import json
import plistlib
import subprocess
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from swe.remote import commands, setup

from tests._tempdir import temp_dir


NAME = "mac.tail1234.ts.net"
URL = f"https://{NAME}:8477"


def running(**over):
    """`tailscale status --json` for a signed-in node with everything on."""
    status = {
        "BackendState": "Running",
        "AuthURL": "",
        "Self": {"DNSName": NAME + "."},
        "MagicDNSSuffix": "tail1234.ts.net",
        "CurrentTailnet": {"Name": "devbox", "MagicDNSEnabled": True},
        "CertDomains": [NAME],
    }
    status.update(over)
    return status


SERVE = {
    "TCP": {"8477": {"HTTPS": True}},
    "Web": {f"{NAME}:8477": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8477"}}}},
}
GOOD_ARGS = ["/A/swe", "remote", "serve", "--host=127.0.0.1", f"--public-url={URL}"]


def facts(**over):
    """A machine where every step is done; tests take pieces away.
    ``devices=N`` stands for N devices paired at time 1000."""
    n = over.pop("devices", 1)
    f = setup.Facts(platform="darwin", exe="/usr/local/bin/tailscale", version="1.102.4",
                    status=running(), serve=SERVE, agent_args=GOOD_ARGS, agent_running=True,
                    loopback_ok=True, https_ok=True, device_times=[1000.0] * n,
                    service_available=True)
    for k, v in over.items():
        setattr(f, k, v)
    return f


def states(report):
    return {s["id"]: s["state"] for s in report["steps"]}


def step(report, sid):
    return next(s for s in report["steps"] if s["id"] == sid)


class EvaluateTest(unittest.TestCase):
    def test_a_finished_machine_is_ready(self):
        report = setup.evaluate(facts())
        self.assertTrue(report["ready"])
        self.assertEqual(report["url"], URL)
        self.assertEqual(set(states(report).values()), {setup.OK})
        self.assertEqual(step(report, "tailscale.signed_in")["detail"], "devbox as mac")

    def test_no_tailscale_points_at_the_download_and_blocks_the_rest(self):
        report = setup.evaluate(facts(exe=None, status=None, https_ok=False, devices=0))
        first = step(report, "tailscale.installed")
        self.assertEqual(first["state"], setup.NEEDS_YOU)
        self.assertEqual(first["action"], {"kind": "open", "label": "Download Tailscale",
                                           "url": "https://tailscale.com/download/mac"})
        later = [s["state"] for s in report["steps"][1:]]
        self.assertEqual(set(later), {setup.BLOCKED})
        self.assertFalse(report["ready"])

    def test_signing_in_uses_the_login_link_tailscale_reports(self):
        f = facts(status=running(BackendState="NeedsLogin", AuthURL="https://login.tailscale.com/a/x"),
                  https_ok=False)
        s = step(setup.evaluate(f), "tailscale.signed_in")
        self.assertEqual(s["state"], setup.NEEDS_YOU)
        self.assertEqual(s["action"]["url"], "https://login.tailscale.com/a/x")

    def test_tailscale_off_on_a_mac_opens_the_app(self):
        f = facts(status=running(BackendState="Stopped"), https_ok=False)
        s = step(setup.evaluate(f), "tailscale.signed_in")
        self.assertEqual(s["action"], {"kind": "launch", "label": "Open Tailscale", "app": "Tailscale"})
        self.assertIn("off", s["detail"])

    def test_tailscale_off_on_linux_gives_the_command(self):
        f = facts(platform="linux", status=None, https_ok=False)
        s = step(setup.evaluate(f), "tailscale.signed_in")
        self.assertEqual(s["action"]["command"], "sudo tailscale up")

    def test_magicdns_and_https_send_an_admin_to_the_dns_page(self):
        off = running(CurrentTailnet={"Name": "devbox", "MagicDNSEnabled": False})
        r = setup.evaluate(facts(status=off, https_ok=False))
        self.assertEqual(step(r, "tailscale.magicdns")["action"]["url"], setup.ADMIN_DNS_URL)
        self.assertEqual(step(r, "tailscale.https")["state"], setup.BLOCKED)

        r = setup.evaluate(facts(status=running(CertDomains=[]), https_ok=False))
        self.assertEqual(states(r)["tailscale.magicdns"], setup.OK)
        self.assertEqual(step(r, "tailscale.https")["state"], setup.NEEDS_YOU)
        self.assertEqual(step(r, "tailscale.https")["action"]["url"], setup.ADMIN_DNS_URL)

    def test_a_certificate_not_yet_fetched_is_quivers_to_do(self):
        r = setup.evaluate(facts(https_ok=False, cert=None, agent_args=None))
        self.assertEqual(states(r)["tailscale.cert"], setup.TODO)
        self.assertEqual(states(r)["hub.service"], setup.BLOCKED, "never move the hub without a cert")

    def test_a_dns_failure_says_what_to_change(self):
        err = ('500 Internal Server Error: acme.GetReg: Get "https://acme-v02.api.letsencrypt.org/directory": '
               "dial tcp: lookup acme-v02.api.letsencrypt.org: i/o timeout")
        r = setup.evaluate(facts(https_ok=False, cert=(False, err), agent_args=None))
        cert = step(r, "tailscale.cert")
        self.assertEqual(cert["state"], setup.FAILED)
        self.assertIn("DNS server", cert["detail"])
        self.assertIn("1.1.1.1", cert["detail"])
        self.assertEqual(states(r)["hub.service"], setup.BLOCKED)

    def test_an_unknown_cert_failure_keeps_tailscales_last_line(self):
        self.assertEqual(setup.cert_advice("first\nrate limited"),
                         "Tailscale couldn't get a certificate: rate limited")

    def test_a_hub_on_its_tailnet_address_is_moved_saying_who_must_pair_again(self):
        old = ["/A/swe", "remote", "serve", "--tailnet"]
        r = setup.evaluate(facts(agent_args=old, loopback_ok=False, https_ok=False, cert=(True, ""),
                                 devices=3, satellites=1))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.TODO)
        self.assertEqual(hub["action"], {"kind": "apply", "label": "Move hub"})
        self.assertIn("changes its address", hub["detail"])
        self.assertIn("the 3 paired devices and the satellite", hub["detail"])

    def test_devices_paired_before_a_move_must_pair_again(self):
        r = setup.evaluate(facts(devices=2, moved_at=2000.0))
        paired = step(r, "phone.paired")
        self.assertEqual(paired["state"], setup.NEEDS_YOU)
        self.assertIn("The hub moved to", paired["detail"])
        self.assertIn("the 2 paired at the old address", paired["detail"])
        self.assertFalse(r["ready"])
        after = facts(moved_at=2000.0)
        after.device_times = [1000.0, 2500.0]
        self.assertEqual(states(setup.evaluate(after))["phone.paired"], setup.OK)

    def test_a_hub_started_from_a_shell_must_be_stopped_first(self):
        r = setup.evaluate(facts(agent_args=None, agent_running=False, loopback_ok=True,
                                 https_ok=False, cert=(True, "")))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.NEEDS_YOU)
        self.assertIn("outside the background service", hub["detail"])
        self.assertEqual(hub["action"]["command"], "pkill -f 'swe remote serve'")

    def test_a_hub_that_keeps_stopping_points_at_its_log(self):
        r = setup.evaluate(facts(loopback_ok=False, https_ok=False, cert=(True, "")))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.FAILED)
        self.assertIn("serve.log", hub["detail"])

    def test_a_linux_hub_is_installed_as_a_systemd_unit(self):
        r = setup.evaluate(facts(platform="linux", agent_args=None, agent_running=False, loopback_ok=False,
                                 https_ok=False, cert=(True, "")))
        self.assertEqual(step(r, "hub.service")["state"], setup.TODO)
        ok = setup.evaluate(facts(platform="linux", linger=True))
        self.assertIn("boot", step(ok, "hub.service")["detail"])

    def test_unknown_lingering_is_not_claimed_to_start_at_boot(self):
        hub = step(setup.evaluate(facts(platform="linux", linger=None)), "hub.service")
        self.assertEqual(hub["state"], setup.OK)
        self.assertNotIn("Starts at boot", hub["detail"])
        self.assertIn("enable-linger", hub["detail"])

    def test_another_platform_gets_the_command_not_an_install(self):
        self.assertFalse(setup.Facts().service_available, "only gather on Linux turns it on")
        f = facts(platform="freebsd", agent_args=None, loopback_ok=False, https_ok=False,
                  cert=(True, ""), hub_config={})
        f.service_available = False
        hub = step(setup.evaluate(f), "hub.service")
        self.assertEqual(hub["state"], setup.NEEDS_YOU)
        self.assertEqual(hub["action"]["kind"], "command")

    def test_a_linux_hub_without_lingering_says_how_to_keep_it(self):
        r = setup.evaluate(facts(platform="linux", linger=False))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.OK, "it works; it just stops at logout")
        self.assertIn("enable-linger", hub["detail"])

    def test_linux_without_systemd_gets_the_command(self):
        r = setup.evaluate(facts(platform="linux", service_available=False, agent_args=None,
                                 https_ok=False, cert=(True, ""), hub_config={}))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.NEEDS_YOU)
        self.assertEqual(hub["action"]["command"], f"swe remote serve --public-url={URL}")
        other = setup.evaluate(facts(platform="linux", service_available=False, agent_args=None,
                                     https_ok=False, cert=(True, ""), hub_config={}, port=9000))
        self.assertIn("--port=9000 ", step(other, "hub.service")["action"]["command"])
        ok = setup.evaluate(facts(platform="linux", service_available=False, agent_args=None,
                                  hub_config={"public_url": URL}))
        self.assertEqual(states(ok)["hub.service"], setup.OK)

    def test_serve_missing_is_quivers_to_do_and_a_dead_serve_says_why(self):
        r = setup.evaluate(facts(serve={}, https_ok=False, cert=(True, "")))
        self.assertEqual(states(r)["hub.reachable"], setup.TODO)
        r = setup.evaluate(facts(https_ok=False, https_error="timed out", cert=(True, "")))
        reach = step(r, "hub.reachable")
        self.assertEqual(reach["state"], setup.FAILED)
        self.assertIn("timed out", reach["detail"])

    def test_no_device_yet_offers_pairing(self):
        r = setup.evaluate(facts(devices=0))
        paired = step(r, "phone.paired")
        self.assertEqual(paired["action"], {"kind": "pair", "label": "Pair a phone"})
        self.assertFalse(r["ready"])

    def test_serve_configured_reads_tailscales_json(self):
        self.assertTrue(setup.serve_configured(SERVE, NAME, 8477))
        self.assertFalse(setup.serve_configured(SERVE, NAME, 9000))
        other = {"TCP": {"8477": {"HTTPS": True}},
                 "Web": {f"{NAME}:8477": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}}
        self.assertFalse(setup.serve_configured(other, NAME, 8477))
        self.assertFalse(setup.serve_configured({}, NAME, 8477))


class ApplyTest(unittest.TestCase):
    """apply drives a fake machine that changes when actions run."""

    def setUp(self):
        # A move apply records without a ``record`` of its own would go
        # to the real ~/.quiver/remote/setup-state.json.
        p = patch.object(setup, "record_move", lambda *_a, **_kw: None)
        p.start()
        self.addCleanup(p.stop)

    def machine(self, **over):
        self.f = facts(agent_args=["/A/swe", "remote", "serve", "--tailnet"], serve={},
                       https_ok=False, cert=(True, ""), devices=0)
        for k, v in over.items():
            setattr(self.f, k, v)
        self.calls = []

        def install_hub(url, port):
            self.calls.append(("hub", url, port))
            self.f.agent_args = GOOD_ARGS
            return None

        def start_serve(exe, port):
            self.calls.append(("serve", port))
            self.f.serve = SERVE
            self.f.https_ok = True
            return None

        return setup.Actions(install_hub=install_hub, start_serve=start_serve, wait_hub=lambda _p: True)

    def gather(self, **_kw):
        return self.f

    def test_it_moves_the_hub_then_sets_up_serve(self):
        acts = self.machine()
        moved = []
        report = setup.apply(actions=acts, gather_fn=self.gather, record=moved.append)
        self.assertEqual(self.calls, [("hub", URL, 8477), ("serve", 8477)])
        self.assertEqual(moved, [URL], "the move is remembered for re-pairing")
        self.assertEqual(states(report)["hub.reachable"], setup.OK)
        self.assertEqual(states(report)["phone.paired"], setup.NEEDS_YOU, "a person pairs")

    def test_it_never_moves_the_hub_when_the_certificate_fails(self):
        acts = self.machine(cert=(False, "lookup acme-v02.api.letsencrypt.org: i/o timeout"))
        report = setup.apply(actions=acts, gather_fn=self.gather)
        self.assertEqual(self.calls, [], "the working tailnet-IP hub stays where it is")
        self.assertEqual(states(report)["tailscale.cert"], setup.FAILED)

    def test_it_stops_at_a_step_that_needs_a_person(self):
        acts = self.machine(status=running(CertDomains=[]))
        report = setup.apply(actions=acts, gather_fn=self.gather)
        self.assertEqual(self.calls, [])
        self.assertEqual(states(report)["tailscale.https"], setup.NEEDS_YOU)

    def test_a_failed_action_is_reported_not_assumed(self):
        acts = self.machine()
        acts.install_hub = lambda _u, _p: "launchctl could not load the hub: boom"
        report = setup.apply(actions=acts, gather_fn=self.gather)
        hub = step(report, "hub.service")
        self.assertEqual(hub["state"], setup.FAILED)
        self.assertIn("boom", hub["detail"])
        self.assertFalse(report["ready"])

    def test_a_hub_that_never_answers_is_a_failure(self):
        acts = self.machine()
        acts.wait_hub = lambda _p: False
        report = setup.apply(actions=acts, gather_fn=self.gather)
        self.assertIn("didn't answer", step(report, "hub.service")["detail"])

    def test_a_hub_that_keeps_stopping_is_restarted_once(self):
        acts = self.machine(agent_args=GOOD_ARGS, loopback_ok=False)
        report = setup.apply(actions=acts, gather_fn=self.gather, record=lambda _u: self.fail("not a move"))
        self.assertEqual(self.calls, [("hub", URL, 8477)], "one restart, then it reports")
        self.assertEqual(states(report)["hub.service"], setup.FAILED)

    def test_recheck_fetches_the_certificate_afresh_once(self):
        # Three gathers (move the hub, set up serve, pair): only the
        # first fetches afresh.
        acts = self.machine()
        seen = []

        def gather(**kw):
            seen.append(kw.get("fresh_cert"))
            return self.f

        setup.apply(actions=acts, gather_fn=gather, fresh_cert=True)
        self.assertEqual(seen, [True, False, False])

    def test_linux_serve_without_an_operator_gives_the_fix(self):
        acts = self.machine(agent_args=GOOD_ARGS)
        acts.start_serve = lambda _e, _p: "Access denied: serve config denied"
        report = setup.apply(actions=acts, gather_fn=self.gather)
        reach = step(report, "hub.reachable")
        self.assertEqual(reach["state"], setup.FAILED)
        self.assertEqual(reach["action"]["command"], "sudo tailscale set --operator=$USER")


class CertCheckTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.runs = []

    def tailscale(self, rc, err=""):
        def fake(argv, **_kw):
            self.runs.append(argv)
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr=err)
        return fake

    def test_a_good_fetch_is_cached_for_a_day(self):
        ok = setup.check_cert("/ts", NAME, self.home, self.tailscale(0), now=1000)
        self.assertEqual(ok, (True, ""))
        self.assertEqual(self.runs[0][:2], ["/ts", "cert"])
        self.assertEqual(self.runs[0][-1], NAME)
        setup.check_cert("/ts", NAME, self.home, self.tailscale(0), now=1000 + 3600)
        self.assertEqual(len(self.runs), 1, "served from the cache")

    def test_a_failure_is_retried_after_a_minute(self):
        fail = self.tailscale(1, "lookup acme: i/o timeout")
        self.assertEqual(setup.check_cert("/ts", NAME, self.home, fail, now=1000)[0], False)
        setup.check_cert("/ts", NAME, self.home, fail, now=1030)
        self.assertEqual(len(self.runs), 1)
        setup.check_cert("/ts", NAME, self.home, fail, now=1000 + setup.CERT_FAIL_TTL_S + 1)
        self.assertEqual(len(self.runs), 2)

    def test_recheck_skips_the_cache(self):
        setup.check_cert("/ts", NAME, self.home, self.tailscale(0), now=1000)
        setup.check_cert("/ts", NAME, self.home, self.tailscale(0), now=1001, fresh=True)
        self.assertEqual(len(self.runs), 2)


    def test_a_tailscale_that_cannot_run_is_a_failure(self):
        def broken(argv, **_kw):
            raise subprocess.TimeoutExpired(argv, 30)

        ok, err = setup.check_cert("/ts", NAME, self.home, broken, now=1000)
        self.assertFalse(ok)
        self.assertIn("timed out", err)

    def test_the_cache_is_read_for_status_only_while_fresh(self):
        self.assertIsNone(setup._cached_cert(self.home, NAME))
        setup.check_cert("/ts", NAME, self.home, self.tailscale(0), now=time.time())
        self.assertEqual(setup._cached_cert(self.home, NAME), (True, ""))
        self.assertIsNone(setup._cached_cert(self.home, "other.ts.net"))
        setup.check_cert("/ts", NAME, self.home, self.tailscale(1, "no"), now=time.time() - 3600, fresh=True)
        self.assertIsNone(setup._cached_cert(self.home, NAME), "a failure an hour old is stale")

    def test_advice_names_the_cause(self):
        self.assertIn("DNS server", setup.cert_advice("lookup acme-v02: i/o timeout"))
        self.assertIn("HTTPS certificates are off", setup.cert_advice("your tailnet does not have HTTPS enabled; not enabled"))
        self.assertEqual(setup.cert_advice(""), "Tailscale couldn't get a certificate: unknown error")
        self.assertEqual(setup.cert_advice("a\nboom"), "Tailscale couldn't get a certificate: boom")

class GatherTest(unittest.TestCase):
    """gather reads tailscale, the service, the hub and devices.json."""

    def setUp(self):
        self.home = temp_dir(self)
        # Linux would ask systemctl too; these tests are about tailscale.
        pin = patch.object(setup.sys, "platform", "darwin")
        pin.start()
        self.addCleanup(pin.stop)

    def fake_run(self, argv, **_kw):
        if argv[1:3] == ["status", "--json"]:
            out = json.dumps(running(Version="1.102.4-t3c"))
        elif argv[1:4] == ["serve", "status", "--json"]:
            out = json.dumps(SERVE)
        else:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    def test_it_reads_the_machine(self):
        from swe.remote import pairing

        devices = {"a": {"created_at": 500.0, "revoked_at": None},
                   "b": {"created_at": 600.0, "revoked_at": 700.0}}
        with patch.object(setup, "tailscale_exe", return_value="/ts"), \
             patch.object(pairing, "load_devices", return_value=devices), \
             patch.object(setup, "_agent", return_value=(GOOD_ARGS, True)), \
             patch.object(setup.sys, "platform", "darwin"):
            f = setup.gather(home=self.home, run=self.fake_run, get=lambda url, _t: (True, ""))
        self.assertEqual(f.version, "1.102.4")
        self.assertEqual(f.dns_name, NAME)
        self.assertEqual(f.serve, SERVE)
        self.assertEqual(f.device_times, [500.0], "a revoked device can't reach the hub")
        self.assertTrue(f.loopback_ok and f.https_ok)
        self.assertTrue(setup.evaluate(f)["ready"])

    def test_satellites_exclude_the_hubs_own_row(self):
        from swe.remote import catalog
        own = catalog.load_machine_id(self.home)
        catalog.upsert_machine(self.home, {"id": own, "name": "hub"})
        with patch.object(setup, "tailscale_exe", return_value=None), \
             patch.object(setup, "_agent", return_value=(None, False)):
            f = setup.gather(home=self.home, run=self.fake_run,
                             get=lambda url, _t: (False, ""))
        self.assertEqual(f.satellites, 0)
        catalog.upsert_machine(self.home, {"id": "sat-1", "name": "box"})
        with patch.object(setup, "tailscale_exe", return_value=None), \
             patch.object(setup, "_agent", return_value=(None, False)):
            f = setup.gather(home=self.home, run=self.fake_run,
                             get=lambda url, _t: (False, ""))
        self.assertEqual(f.satellites, 1)

    def test_a_recorded_move_applies_to_its_own_address(self):
        setup.record_move(self.home, URL, now=123.0)
        with patch.object(setup, "tailscale_exe", return_value="/ts"), \
             patch.object(setup, "_agent", return_value=(None, False)):
            f = setup.gather(home=self.home, run=self.fake_run, get=lambda url, _t: (False, "down"))
        self.assertEqual(f.moved_at, 123.0)
        setup.record_move(self.home, "https://elsewhere:8477", now=5.0)
        with patch.object(setup, "tailscale_exe", return_value="/ts"), \
             patch.object(setup, "_agent", return_value=(None, False)):
            f = setup.gather(home=self.home, run=self.fake_run, get=lambda url, _t: (False, "down"))
        self.assertIsNone(f.moved_at)

    def test_no_tailscale_reads_nothing_from_it(self):
        with patch.object(setup, "tailscale_exe", return_value=None), \
             patch.object(setup, "_agent", return_value=(None, False)):
            f = setup.gather(home=self.home, run=lambda *a, **k: self.fail("ran tailscale"),
                             get=lambda url, _t: (False, ""))
        self.assertIsNone(f.status)
        self.assertEqual(f.public_url, "")

    def test_https_retries_against_the_os_ca_bundle(self):
        from swe.remote import agent

        seen = []

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with patch.object(agent, "_urlopen", lambda req, timeout: seen.append(req.full_url) or Resp()):
            self.assertEqual(setup._get(URL + "/v1/status", 5), (True, ""))
        self.assertEqual(seen, [URL + "/v1/status"])


class DefaultActionsTest(unittest.TestCase):
    def test_serve_runs_tailscale_serve_in_the_background(self):
        calls = []

        def run(argv, **_kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        acts = setup.default_actions(temp_dir(self), run=run)
        self.assertIsNone(acts.start_serve("/ts", 8477))
        self.assertEqual(calls, [["/ts", "serve", "--bg", "--https=8477", "http://127.0.0.1:8477"]])

    def test_a_refused_serve_returns_its_message(self):
        def run(argv, **_kw):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Access denied: serve config denied")

        acts = setup.default_actions(temp_dir(self), run=run)
        self.assertIn("Access denied", acts.start_serve("/ts", 8477))

    def test_install_hub_builds_the_loopback_program(self):
        home = temp_dir(self)
        with patch.object(commands, "hub_program", return_value=(GOOD_ARGS, None)) as prog, \
             patch.object(commands, "install_service", return_value=(Path("/x"), "")) as install:
            err = setup.default_actions(home).install_hub(URL, 8477)
        self.assertIsNone(err)
        prog.assert_called_once_with("127.0.0.1", 8477, URL)
        self.assertEqual(install.call_args.args[0], GOOD_ARGS)
        self.assertEqual(install.call_args.kwargs, {"home": home, "run": subprocess.run})
        fake = object()
        with patch.object(commands, "hub_program", return_value=(GOOD_ARGS, None)), \
             patch.object(commands, "install_service", return_value=(Path("/x"), "")) as install:
            setup.default_actions(home, run=fake).install_hub(URL, 8477)
        self.assertIs(install.call_args.kwargs["run"], fake, "a fake runner reaches the service manager")

    def test_install_hub_reports_a_load_failure(self):
        with patch.object(commands, "hub_program", return_value=(GOOD_ARGS, None)), \
             patch.object(commands, "install_service",
                          side_effect=RuntimeError("launchctl could not load the hub: EIO")):
            err = setup.default_actions(temp_dir(self)).install_hub(URL, 8477)
        self.assertIn("EIO", err)


class HelpersTest(unittest.TestCase):
    def test_run_json_reports_each_failure(self):
        def raises(argv, **_kw):
            raise OSError("no such file")

        def code(rc, out):
            return lambda argv, **_kw: subprocess.CompletedProcess(argv, rc, stdout=out, stderr="bad")

        self.assertEqual(setup._run_json(raises, ["ts"]), (None, "no such file"))
        self.assertEqual(setup._run_json(code(1, ""), ["ts"]), (None, "bad"))
        self.assertEqual(setup._run_json(code(0, "{nope"), ["ts"]), (None, "unreadable JSON"))
        self.assertEqual(setup._run_json(code(0, "[]"), ["ts"]), (None, ""))

    def test_get_says_why_it_failed(self):
        from swe.remote import agent

        def refuse(req, timeout):
            raise urllib.error.URLError("connection refused")

        with patch.object(agent, "_urlopen", refuse):
            self.assertEqual(setup._get("http://127.0.0.1:1/v1/status", 1), (False, "connection refused"))

    def test_agent_reads_the_plist_and_launchd(self):
        home = temp_dir(self)
        self.assertEqual(setup._agent(home, run=lambda *a, **k: self.fail("no plist")), (None, False))
        path = setup.launchd.plist_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({"ProgramArguments": GOOD_ARGS}))
        with patch.object(setup.launchd, "loaded_pid", return_value=(True, 42)):
            self.assertEqual(setup._agent(home, run=None), (GOOD_ARGS, True))

    def test_state_survives_a_bad_file(self):
        home = temp_dir(self)
        self.assertEqual(setup.load_state(home), {})
        setup._state_file(home).parent.mkdir(parents=True, exist_ok=True)
        setup._state_file(home).write_text("[1]")
        self.assertEqual(setup.load_state(home), {})

    def test_serve_fix_only_for_a_permission_error(self):
        self.assertIsNone(setup.serve_fix("listener already in use"))
        self.assertEqual(setup.serve_fix("Access denied")["command"], "sudo tailscale set --operator=$USER")


class DefaultActionsMoreTest(unittest.TestCase):
    def test_a_program_that_cannot_be_built_is_reported(self):
        with patch.object(commands, "hub_program", return_value=(None, "swe isn't installed")):
            err = setup.default_actions(temp_dir(self)).install_hub(URL, 8477)
        self.assertEqual(err, "swe isn't installed")

    def test_serve_that_cannot_run_or_says_nothing(self):
        def raises(argv, **_kw):
            raise OSError("not found")

        acts = setup.default_actions(temp_dir(self), run=raises)
        self.assertEqual(acts.start_serve("/ts", 8477), "not found")
        quiet = setup.default_actions(temp_dir(self),
                                      run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 3, "", ""))
        self.assertEqual(quiet.start_serve("/ts", 8477), "tailscale serve exited 3")

    def test_wait_hub_polls_until_the_hub_answers(self):
        answers = iter([(False, "down"), (True, "")])
        with patch.object(setup, "_get", lambda url, t: next(answers)), patch.object(setup.time, "sleep"):
            self.assertTrue(setup.default_actions(temp_dir(self)).wait_hub(8477))
        with patch.object(setup, "_get", lambda url, t: (False, "down")), patch.object(setup.time, "sleep"):
            self.assertFalse(setup.default_actions(temp_dir(self)).wait_hub(8477))


class CommandTest(unittest.TestCase):
    def test_a_port_out_of_range_changes_nothing(self):
        err = io.StringIO()
        with patch.object(setup, "apply", side_effect=AssertionError("applied")), \
             contextlib.redirect_stdout(err):
            rc = commands.cmd_remote(["setup", "--apply", "--port=84770"])
        self.assertEqual(rc, 1)
        self.assertIn("1 to 65535", err.getvalue())

    def test_json_is_the_report(self):
        out = io.StringIO()
        with patch.object(setup, "gather", return_value=facts(devices=0)), \
             contextlib.redirect_stdout(out):
            rc = commands.cmd_remote(["setup", "--json"])
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())["data"]
        self.assertFalse(report["ready"])
        self.assertEqual(report["steps"][-1]["id"], "phone.paired")

    def test_the_text_view_says_where_to_go(self):
        out = io.StringIO()
        f = facts(status=running(CertDomains=[]), https_ok=False)
        with patch.object(setup, "gather", return_value=f), contextlib.redirect_stdout(out):
            commands.cmd_remote(["setup"])
        self.assertIn("HTTPS certificates on", out.getvalue())
        self.assertIn(setup.ADMIN_DNS_URL, out.getvalue())


if __name__ == "__main__":
    unittest.main()
