"""remote/setup.py: the steps from nothing to a paired phone."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.remote import commands, setup


NAME = "mac.tail1234.ts.net"
URL = f"https://{NAME}:8477"


def running(**over):
    """`tailscale status --json` for a signed-in node with everything on."""
    status = {
        "BackendState": "Running",
        "AuthURL": "",
        "Self": {"DNSName": NAME + "."},
        "MagicDNSSuffix": "tail1234.ts.net",
        "CurrentTailnet": {"Name": "kai.github", "MagicDNSEnabled": True},
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
    """A machine where every step is done; tests take pieces away."""
    f = setup.Facts(platform="darwin", exe="/usr/local/bin/tailscale", version="1.102.4",
                    status=running(), serve=SERVE, agent_args=GOOD_ARGS, agent_running=True,
                    loopback_ok=True, https_ok=True, devices=1)
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
        self.assertEqual(step(report, "tailscale.signed_in")["detail"], "kai.github as mac")

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
        off = running(CurrentTailnet={"Name": "kai.github", "MagicDNSEnabled": False})
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

    def test_a_hub_on_its_tailnet_address_is_moved(self):
        old = ["/A/swe", "remote", "serve", "--tailnet"]
        r = setup.evaluate(facts(agent_args=old, https_ok=False, cert=(True, "")))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.TODO)
        self.assertEqual(hub["action"]["kind"], "apply")
        self.assertIn("tailnet address", hub["detail"])

    def test_a_hub_that_keeps_stopping_points_at_its_log(self):
        r = setup.evaluate(facts(loopback_ok=False, https_ok=False, cert=(True, "")))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.FAILED)
        self.assertIn("serve.log", hub["detail"])

    def test_a_linux_hub_gets_its_command(self):
        r = setup.evaluate(facts(platform="linux", agent_args=None, https_ok=False, cert=(True, ""),
                                 hub_config={}))
        hub = step(r, "hub.service")
        self.assertEqual(hub["state"], setup.NEEDS_YOU)
        self.assertEqual(hub["action"]["command"], f"swe remote serve --public-url={URL}")
        ok = setup.evaluate(facts(platform="linux", agent_args=None, hub_config={"public_url": URL}))
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
        report = setup.apply(actions=acts, gather_fn=self.gather)
        self.assertEqual(self.calls, [("hub", URL, 8477), ("serve", 8477)])
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

    def test_linux_serve_without_an_operator_gives_the_fix(self):
        acts = self.machine(agent_args=GOOD_ARGS)
        acts.start_serve = lambda _e, _p: "Access denied: serve config denied"
        report = setup.apply(actions=acts, gather_fn=self.gather)
        reach = step(report, "hub.reachable")
        self.assertEqual(reach["state"], setup.FAILED)
        self.assertEqual(reach["action"]["command"], "sudo tailscale set --operator=$USER")


class CertCheckTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.runs = []

    def run(self, rc, err=""):
        def fake(argv, **_kw):
            self.runs.append(argv)
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr=err)
        return fake

    def test_a_good_fetch_is_cached_for_a_day(self):
        ok = setup.check_cert("/ts", NAME, self.home, self.run(0), now=1000)
        self.assertEqual(ok, (True, ""))
        self.assertEqual(self.runs[0][:2], ["/ts", "cert"])
        self.assertEqual(self.runs[0][-1], NAME)
        setup.check_cert("/ts", NAME, self.home, self.run(0), now=1000 + 3600)
        self.assertEqual(len(self.runs), 1, "served from the cache")

    def test_a_failure_is_retried_after_a_minute(self):
        fail = self.run(1, "lookup acme: i/o timeout")
        self.assertEqual(setup.check_cert("/ts", NAME, self.home, fail, now=1000)[0], False)
        setup.check_cert("/ts", NAME, self.home, fail, now=1030)
        self.assertEqual(len(self.runs), 1)
        setup.check_cert("/ts", NAME, self.home, fail, now=1000 + setup.CERT_FAIL_TTL_S + 1)
        self.assertEqual(len(self.runs), 2)

    def test_recheck_skips_the_cache(self):
        setup.check_cert("/ts", NAME, self.home, self.run(0), now=1000)
        setup.check_cert("/ts", NAME, self.home, self.run(0), now=1001, fresh=True)
        self.assertEqual(len(self.runs), 2)


class CommandTest(unittest.TestCase):
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
