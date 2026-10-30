"""remote commands: flag parsing, subcommand dispatch, attach resolution."""

import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import commands, pairing, supervise
from quiver.sessions.models import Session


def _run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = commands.cmd_remote(list(args))
    return rc, out.getvalue(), err.getvalue()


class TestDispatch(unittest.TestCase):
    def test_bare_prints_usage(self):
        rc, out, _ = _run([])
        self.assertEqual(rc, 0)
        self.assertIn("remote serve", out)

    def test_unknown_subcommand(self):
        rc, _, err = _run(["frobnicate"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown subcommand", err)

    def test_unknown_flag(self):
        rc, _, err = _run(["status", "--bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("unknown flag", err)

    def test_flag_needs_value(self):
        rc, _, err = _run(["status", "--host"])
        self.assertEqual(rc, 1)
        self.assertIn("takes a value", err)

    def test_serve_rejects_positionals(self):
        rc, _, err = _run(["serve", "extra"])
        self.assertEqual(rc, 1)

    def test_devices_rejects_positionals(self):
        rc, _, err = _run(["devices", "extra"])
        self.assertEqual(rc, 1)
        self.assertIn("unexpected args", err)


class TestPair(unittest.TestCase):
    def test_pair_mints_and_prints_url(self):
        home = Path(tempfile.mkdtemp())
        with patch.object(pairing, "mint_pair_token",
                          return_value="tok123") as mint, \
             patch.object(commands.pairing, "mint_pair_token", mint):
            with patch("quiver.remote.commands._tailscale_ip",
                       return_value="100.1.2.3"), \
                 patch("quiver.remote.pairing.mint_pair_token",
                      return_value="tok123"):
                rc, out, _ = _run(["pair", "--name=iphone", "--port=9000"])
        self.assertEqual(rc, 0)
        self.assertIn("#pair=tok123", out)
        self.assertIn("100.1.2.3:9000", out)
        self.assertIn("iphone", out)


class TestStatus(unittest.TestCase):
    def setUp(self):
        # Without --host, status reads the saved hub.json; keep it from
        # reading the real ~/.quiver.
        patcher = patch.object(commands, "load_hub_config", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unreachable_hub(self):
        home = Path(tempfile.mkdtemp())
        with patch("quiver.remote.commands._probe", return_value=None), \
             patch.object(commands.pairing, "load_devices",
                          lambda *a: {}), \
             patch.object(commands.catalog_mod, "load_machines",
                          lambda *a: {}):
            rc, out, _ = _run(["status", "--host=127.0.0.1"])
        self.assertEqual(rc, 1)
        self.assertIn("unreachable", out)

    def test_status_json(self):
        info = {"ok": True, "version": "9.9", "uptime_s": 3,
                "machine": {"name": "srv", "id": "m1"}}
        with patch("quiver.remote.commands._probe", return_value=info), \
             patch.object(commands.pairing, "load_devices",
                          lambda *a: {"d1": {"name": "phone",
                                             "created_at": 1}}), \
             patch.object(commands.catalog_mod, "load_machines",
                          lambda *a: {"m1": {"name": "srv", "online": True}}):
            rc, out, _ = _run(["status", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["data"]["devices"][0]["name"], "phone")
        self.assertEqual(data["data"]["machines"][0]["name"], "srv")

    def test_status_json_exits_1_when_hub_unreachable(self):
        # The envelope still goes out; the exit code reports reachability,
        # as docs/json-contracts.md and the Mac app's caller expect.
        with patch("quiver.remote.commands._probe", return_value=None), \
             patch.object(commands.pairing, "load_devices", lambda *a: {}), \
             patch.object(commands.catalog_mod, "load_machines", lambda *a: {}):
            rc, out, _ = _run(["status", "--json", "--host=127.0.0.1"])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["data"]["hub"], {"ok": False})

    def test_status_pretty_online(self):
        info = {"ok": True, "version": "1.0.0", "uptime_s": 9,
                "machine": {"name": "srv", "id": "m1"}}
        with patch("quiver.remote.commands._probe", return_value=info), \
             patch.object(commands.pairing, "load_devices", lambda *a: {}), \
             patch.object(commands.catalog_mod, "load_machines", lambda *a: {}):
            rc, out, _ = _run(["status"])
        self.assertEqual(rc, 0)
        self.assertIn("online", out)


class TestDevices(unittest.TestCase):
    def test_empty_list(self):
        with patch.object(commands.pairing, "load_devices", lambda *a: {}):
            rc, out, _ = _run(["devices"])
        self.assertEqual(rc, 0)
        self.assertIn("no paired", out)

    def test_revoke(self):
        home = Path(tempfile.mkdtemp())
        tok = pairing.mint_pair_token(home)
        cred = pairing.exchange(home, tok, "phone")
        real_revoke = pairing.revoke_device
        with patch.object(commands.pairing, "revoke_device",
                          lambda _h, d: real_revoke(home, d)):
            rc, out, _ = _run(["devices", f"--revoke={cred['device_id']}"])
        self.assertEqual(rc, 0)
        self.assertIn("revoked", out)

    def test_revoke_unknown(self):
        with patch.object(commands.pairing, "revoke_device",
                          lambda *a: False):
            rc, out, _ = _run(["devices", "--revoke=nope"])
        self.assertEqual(rc, 1)
        # non-json errors go to stdout (stderr is for --json output)
        self.assertIn("no such device", out)


class TestAttachCmd(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.session = Session(timestamp=0, agent="Claude Code",
                               path="/tmp/p", session_id="abc",
                               tool_name="claude")

    def _patch_discovery(self, sessions):
        return (
            patch("quiver.sessions.aggregator.get_all_sessions",
                  return_value=sessions),
            patch("quiver.sessions.status.session_statuses",
                  lambda ss: ["done"] * len(ss)),
            patch.object(commands.tail, "resolve_jsonl_path",
                         return_value=None),
        )

    def test_no_match(self):
        p1, p2, p3 = self._patch_discovery([])
        with p1, p2, p3:
            rc, _, err = _run(["attach", "ghost"])
        self.assertEqual(rc, 1)
        self.assertIn("no session matches", err)

    def test_attach_headless(self):
        p1, p2, p3 = self._patch_discovery([self.session])
        with p1, p2, p3, \
             patch.object(commands.supervise, "attach",
                          return_value=(True, "attached (headless)")) as att:
            rc, out, _ = _run(["attach", "claude:abc", "--mode=headless"])
        self.assertEqual(rc, 0)
        self.assertIn("attached", out)
        self.assertEqual(att.call_args[1]["mode"], "headless")

    def test_attach_ambiguous(self):
        other = Session(timestamp=0, agent="Claude Code", path="/tmp/q",
                        session_id="abc2", tool_name="claude")
        p1, p2, p3 = self._patch_discovery([self.session, other])
        with p1, p2, p3:
            # substring "bc" matches both session ids (no exact match)
            rc, _, err = _run(["attach", "bc"])
        self.assertEqual(rc, 1)
        self.assertIn("matches", err)

    def test_attach_bad_mode(self):
        rc, _, err = _run(["attach", "x", "--mode=weird"])
        self.assertEqual(rc, 1)
        self.assertIn("--mode", err)

    def test_attach_missing_arg(self):
        rc, _, err = _run(["attach"])
        self.assertEqual(rc, 1)
        self.assertIn("needs a session", err)

    def test_attach_rejects_extra_positionals(self):
        rc, _, err = _run(["attach", "abc", "extra", "args"])
        self.assertEqual(rc, 1)
        self.assertIn("unexpected args: extra args", err)


class TestServe(unittest.TestCase):
    def test_serve_binds_tailscale(self):
        seen = {}

        def fake_serve(state, host, port, public_url=""):
            seen["host"], seen["port"] = host, port
            return 0

        state = MagicMock()
        state.machine = {"name": "srv"}
        state.machine_id = "m1"
        with patch.object(commands, "HubState", return_value=state), \
             patch.object(commands, "serve", fake_serve), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"):
            rc, out, _ = _run(["serve"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["host"], "100.1.2.3")
        self.assertIn("srv", out)

    def test_serve_localhost_fallback_warns(self):
        state = MagicMock()
        state.machine = {"name": "srv"}
        state.machine_id = "m1"
        with patch.object(commands, "HubState", return_value=state), \
             patch.object(commands, "serve", lambda *a: 0), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value=None):
            rc, _, err = _run(["serve"])
        self.assertEqual(rc, 0)
        self.assertIn("localhost", err)

    def test_serve_bind_failure(self):
        state = MagicMock()
        state.machine = {"name": "srv"}
        state.machine_id = "m1"
        with patch.object(commands, "HubState", return_value=state), \
             patch.object(commands, "serve",
                          side_effect=OSError("busy")), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"):
            rc, _, err = _run(["serve"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot bind", err)

    def test_serve_logs_timestamped_start_and_stop(self):
        state = MagicMock()
        state.machine = {"name": "srv"}
        state.machine_id = "m1"
        with patch.object(commands, "HubState", return_value=state), \
             patch.object(commands, "serve", lambda *a: 0), \
             patch.object(commands, "_stamp", return_value="2026-10-23 17:01:00"), \
             patch("quiver.remote.commands._tailscale_ip", return_value="100.1.2.3"):
            rc, out, _ = _run(["serve"])
        self.assertEqual(rc, 0)
        self.assertIn("2026-10-23 17:01:00  remote hub on", out)
        self.assertIn("2026-10-23 17:01:00  remote hub stopped", out)

    def test_serve_tailnet_exits_without_tailscale(self):
        with patch.object(commands, "HubState") as hub, \
             patch("quiver.remote.commands._tailscale_ip", return_value=None):
            rc, _, err = _run(["serve", "--tailnet"])
        self.assertEqual(rc, 1)
        self.assertIn("launchd retries", err)
        hub.assert_not_called()

    def _fake_state(self):
        state = MagicMock()
        state.machine = {"name": "srv"}
        state.machine_id = "m1"
        return state

    def test_serve_refuses_wildcard_bind(self):
        for host in ("0.0.0.0", "::"):
            with self.subTest(host):
                rc, _, err = _run(["serve", f"--host={host}"])
                self.assertEqual(rc, 1)
                self.assertIn("--unsafe-bind-any", err)

    def test_serve_refuses_a_lan_bind(self):
        for host in ("192.168.1.5", "10.0.0.4", "fe80::1"):
            with self.subTest(host):
                rc, _, err = _run(["serve", f"--host={host}"])
                self.assertEqual(rc, 1)
                self.assertIn("--unsafe-bind-any", err)

    def test_serve_refuses_a_name_off_the_tailnet(self):
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                  ("93.184.216.34", 8477))]
        with patch("socket.getaddrinfo", return_value=infos):
            rc, _, err = _run(["serve", "--host=box.example.com"])
        self.assertEqual(rc, 1)
        self.assertIn("--unsafe-bind-any", err)

    def test_serve_allows_tailnet_and_loopback_binds(self):
        for host in ("127.0.0.1", "localhost", "100.64.1.2",
                     "fd7a:115c:a1e0::1234"):
            with self.subTest(host):
                with patch.object(commands, "HubState",
                                  return_value=self._fake_state()), \
                     patch.object(commands, "serve", lambda *a: 0):
                    rc, _, err = _run(["serve", f"--host={host}"])
                self.assertEqual(rc, 0, err)

    def test_serve_tailnet_name_passes_after_resolving(self):
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                  ("100.64.1.2", 8477))]
        with patch.object(commands, "HubState",
                          return_value=self._fake_state()), \
             patch.object(commands, "serve", lambda *a: 0), \
             patch("socket.getaddrinfo", return_value=infos) as lookup:
            rc, _, err = _run(["serve", "--host=box.tail0000.ts.net"])
        self.assertEqual(rc, 0, err)
        lookup.assert_called_once()

    def test_serve_unsafe_bind_any_allows_wildcard(self):
        seen = {}

        def fake_serve(state, host, port, public_url=""):
            seen["host"] = host
            return 0

        with patch.object(commands, "HubState",
                          return_value=self._fake_state()), \
             patch.object(commands, "serve", fake_serve):
            rc, _, err = _run(
                ["serve", "--host=0.0.0.0", "--unsafe-bind-any"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(seen["host"], "0.0.0.0")

    def test_install_refuses_a_wildcard_bind(self):
        with patch.object(commands.sys, "platform", "darwin"):
            rc, _, err = _run(["serve", "--install", "--host=0.0.0.0"])
        self.assertEqual(rc, 1)
        self.assertIn("--unsafe-bind-any", err)

    def test_install_passes_the_unsafe_flag_to_the_service(self):
        seen = {}

        def fake_install(program, log_path, path_env, **_kw):
            seen["program"] = program
            return Path("/h/Library/LaunchAgents/x.plist")

        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.sys, "argv", ["/opt/swe/bin/swe"]), \
             patch("quiver.remote.commands.os.access", return_value=True), \
             patch.object(commands.launchd, "bundled_swe", return_value=None), \
             patch.object(commands.launchd, "loaded_pid",
                          return_value=(False, None)), \
             patch.object(commands.launchd, "install", fake_install), \
             patch.object(commands, "_probe", return_value={"ok": True}):
            rc, _, err = _run(["serve", "--install", "--host=0.0.0.0",
                               "--unsafe-bind-any"])
        self.assertEqual(rc, 0, err)
        self.assertIn("--unsafe-bind-any", seen["program"])

    def test_install_hands_the_hub_to_launchd(self):
        seen = {}

        def fake_install(program, log_path, path_env, **_kw):
            seen["program"], seen["log"] = program, log_path
            return Path("/h/Library/LaunchAgents/x.plist")

        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.sys, "argv", ["/opt/swe/bin/swe"]), \
             patch("quiver.remote.commands.os.access", return_value=True), \
             patch.object(commands.launchd, "bundled_swe", return_value=None), \
             patch.object(commands.launchd, "loaded_pid", return_value=(False, None)), \
             patch.object(commands.launchd, "install", fake_install), \
             patch.object(commands, "_probe", return_value={"ok": True}), \
             patch("quiver.remote.commands._tailscale_ip", return_value="100.1.2.3"):
            rc, out, err = _run(["serve", "--install", "--port=9000"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["program"], ["/opt/swe/bin/swe", "remote", "serve", "--tailnet", "--port=9000"])
        self.assertEqual(seen["log"].name, "serve.log")
        self.assertIn("installed", out)
        # A shell-started hub still holds the port.
        self.assertIn("outside launchd", err)

    def test_install_on_linux_without_systemd_says_so(self):
        with patch.object(commands.sys, "platform", "linux"), \
             patch.object(commands.systemd, "available", return_value=False):
            rc, _, err = _run(["serve", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("no systemd user manager", err)

    def test_install_on_linux_writes_a_systemd_unit(self):
        seen = {}

        def fake_install(program, log_path, path_env, **_kw):
            seen["program"] = program
            return Path("/h/.config/systemd/user/quiver-remote-hub.service"), "sudo loginctl enable-linger $USER"

        with patch.object(commands.sys, "platform", "linux"), \
             patch.object(commands.sys, "argv", ["/home/dev/.local/bin/swe"]), \
             patch("quiver.remote.commands.os.access", return_value=True), \
             patch.object(commands.systemd, "available", return_value=True), \
             patch.object(commands.systemd, "status", return_value=(None, False)), \
             patch.object(commands.systemd, "install", fake_install), \
             patch.object(commands, "_probe", side_effect=[None, {"ok": True}]):
            rc, out, err = _run(["serve", "--install", "--public-url=https://box.tail.ts.net:8477"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["program"], ["/home/dev/.local/bin/swe", "remote", "serve", "--host=127.0.0.1",
                                           "--public-url=https://box.tail.ts.net:8477"])
        self.assertIn("quiver-remote-hub.service", out)
        self.assertIn("starts at boot", out)
        self.assertIn("enable-linger", err)

    def test_install_elsewhere_is_refused(self):
        with patch.object(commands.sys, "platform", "win32"):
            rc, _, err = _run(["serve", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("macOS (launchd) and Linux (systemd) only", err)

    def test_uninstall_reports_a_failed_stop(self):
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.launchd, "uninstall", side_effect=RuntimeError("busy")):
            rc, _, err = _run(["serve", "--uninstall"])
        self.assertEqual(rc, 1)
        self.assertIn("could not stop the hub: busy", err)

    def test_install_prefers_the_app_bundle_and_checks_the_hub_started(self):
        seen = {}

        def fake_install(program, log_path, path_env, **_kw):
            seen["program"] = program
            return Path("/h/x.plist")

        shim = Path("/Users/k/Applications/Quiver.app/Contents/Resources/swe/bin/swe")
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.sys, "argv", ["/Users/k/Desktop/quiver/.venv/bin/swe"]), \
             patch.object(commands.launchd, "bundled_swe", return_value=shim), \
             patch.object(commands.launchd, "loaded_pid", return_value=(False, None)), \
             patch.object(commands.launchd, "install", fake_install), \
             patch.object(commands, "_probe", side_effect=[None, None, {"ok": True}]), \
             patch("quiver.remote.commands.time.sleep"), \
             patch("quiver.remote.commands._tailscale_ip", return_value="100.1.2.3"):
            rc, out, _ = _run(["serve", "--install"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen["program"][0], str(shim))
        self.assertIn("hub answering on 100.1.2.3:8477", out)

    def test_install_warns_when_the_hub_does_not_start(self):
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.launchd, "bundled_swe", return_value=Path("/A/swe")), \
             patch.object(commands.launchd, "loaded_pid", return_value=(False, None)), \
             patch.object(commands.launchd, "install", return_value=Path("/h/x.plist")), \
             patch.object(commands, "_probe", return_value=None), \
             patch("quiver.remote.commands.time.sleep"), \
             patch("quiver.remote.commands._tailscale_ip", return_value="100.1.2.3"):
            rc, _, err = _run(["serve", "--install"])
        self.assertEqual(rc, 0)
        self.assertIn("not answering on 100.1.2.3:8477 after 20 s", err)

    def test_install_refuses_a_swe_in_a_protected_folder(self):
        home = Path.home()
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.sys, "argv", [str(home / "Desktop/quiver/.venv/bin/swe")]), \
             patch("quiver.remote.commands.os.access", return_value=True), \
             patch.object(commands.launchd, "bundled_swe", return_value=None), \
             patch.object(commands.launchd, "install") as install:
            rc, _, err = _run(["serve", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("~/Desktop", err)
        install.assert_not_called()

    def test_install_needs_the_swe_executable(self):
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.launchd, "bundled_swe", return_value=None), \
             patch.object(commands.sys, "argv", ["-c"]), \
             patch("quiver.remote.commands.os.access", return_value=False):
            rc, _, err = _run(["serve", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot find the swe executable", err)

    def test_install_reports_a_failed_load(self):
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.launchd, "bundled_swe", return_value=Path("/A/swe")), \
             patch.object(commands.launchd, "loaded_pid", return_value=(True, 1)), \
             patch.object(commands.launchd, "install", side_effect=RuntimeError("denied")):
            rc, _, err = _run(["serve", "--install", "--host=100.64.1.2"])
        self.assertEqual(rc, 1)
        self.assertIn("could not load the hub: denied", err)

    def test_uninstall(self):
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.launchd, "uninstall", return_value=False):
            rc, out, _ = _run(["serve", "--uninstall"])
        self.assertEqual(rc, 0)
        self.assertIn("no hub LaunchAgent", out)

    def test_bad_port(self):
        rc, _, err = _run(["serve", "--port=abc"])
        self.assertEqual(rc, 1)
        self.assertIn("--port", err)


class TestProbeAndPair(unittest.TestCase):
    def test_probe_returns_json_or_none(self):
        import io as _io
        import urllib.error

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'

        with patch("urllib.request.urlopen", return_value=_Resp()):
            self.assertEqual(commands._probe("h", 1), {"ok": True})
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("down")):
            self.assertIsNone(commands._probe("h", 1))

    def test_probe_brackets_an_ipv6_host(self):
        # "http://::1:8477" doesn't parse; the literal needs brackets,
        # once, whether or not the caller already added them.
        seen = []

        def urlopen(url, timeout=None):
            seen.append(url)
            raise OSError("no hub")

        with patch("urllib.request.urlopen", side_effect=urlopen):
            for host in ("::1", "[::1]", "fd7a:115c::1", "100.64.0.1", "h"):
                commands._probe(host, 8477)
        self.assertEqual(seen, [
            "http://[::1]:8477/v1/status",
            "http://[::1]:8477/v1/status",
            "http://[fd7a:115c::1]:8477/v1/status",
            "http://100.64.0.1:8477/v1/status",
            "http://h:8477/v1/status",
        ])

    def test_pair_without_qrencode(self):
        with patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"), \
             patch("quiver.remote.pairing.mint_pair_token",
                   return_value="tk"), \
             patch("shutil.which", return_value=None):
            rc, out, _ = _run(["pair"])
        self.assertEqual(rc, 0)
        self.assertIn("qrencode", out)
        self.assertIn("tk", out)

    def test_pair_with_qrencode(self):
        with patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"), \
             patch("quiver.remote.pairing.mint_pair_token",
                   return_value="tk"), \
             patch("shutil.which", return_value="/usr/bin/qrencode"), \
             patch.object(commands.subprocess, "run") as run:
            rc, out, _ = _run(["pair"])
        self.assertEqual(rc, 0)
        run.assert_called_once()
        self.assertIn("#pair=tk", run.call_args[0][0][-1])


class TestDevicesList(unittest.TestCase):
    def test_lists_devices_and_json(self):
        devices = {"d1": {"name": "iphone", "created_at": 1,
                          "last_seen_at": 1_700_000_000}}
        with patch.object(commands.pairing, "load_devices",
                          lambda *a: devices):
            rc, out, _ = _run(["devices"])
            self.assertEqual(rc, 0)
            self.assertIn("iphone", out)
            self.assertIn("active", out)
            rc, out, _ = _run(["devices", "--json"])
            data = json.loads(out)
            self.assertEqual(data["data"]["devices"][0]["id"], "d1")

    def test_json_emits_device_metadata(self):
        devices = {"d1": {
            "name": "phone", "display_name": "Dev's",
            "kind": "iphone", "model": "iPhone16,2",
            "client": "Quiver Remote 1.0", "os": "iOS 18.5",
            "node_name": "test-iphone.ts.net", "paired_via": "tailscale",
            "credential_hash": "secret-hash",
            "created_at": 1, "last_seen_at": 1_700_000_000,
        }}
        with patch.object(commands.pairing, "load_devices",
                          lambda *a: devices):
            rc, out, _ = _run(["devices", "--json"])
        self.assertEqual(rc, 0)
        row = json.loads(out)["data"]["devices"][0]
        self.assertEqual(row["display_name"], "Dev's")
        self.assertEqual(row["kind"], "iphone")
        self.assertEqual(row["client"], "Quiver Remote 1.0")
        self.assertEqual(row["node_name"], "test-iphone.ts.net")
        self.assertNotIn("credential_hash", row)
        self.assertNotIn("credential", row)

    def test_pretty_list_shows_display_name_and_meta(self):
        devices = {"d1": {
            "name": "phone", "display_name": "Dev's",
            "kind": "iphone", "os": "iOS 18.5",
            "node_name": "test-iphone.ts.net", "created_at": 1,
        }}
        with patch.object(commands.pairing, "load_devices",
                          lambda *a: devices):
            rc, out, _ = _run(["devices"])
        self.assertEqual(rc, 0)
        self.assertIn("Dev's", out)
        self.assertIn("iphone", out)
        self.assertIn("iOS 18.5", out)
        self.assertIn("test-iphone.ts.net", out)


class TestAttachFail(unittest.TestCase):
    def test_supervise_failure(self):
        session = Session(timestamp=0, agent="Claude Code",
                          path="/tmp/p", session_id="abc",
                          tool_name="claude")
        with patch("quiver.sessions.aggregator.get_all_sessions",
                   return_value=[session]), \
             patch("quiver.sessions.status.session_statuses",
                   lambda ss: ["active"]), \
             patch.object(commands.tail, "resolve_jsonl_path",
                          return_value=None), \
             patch.object(commands.supervise, "attach",
                          return_value=(False, "session is busy")):
            rc, _, err = _run(["attach", "claude:abc"])
        self.assertEqual(rc, 1)
        self.assertIn("busy", err)


class TestPairApp(unittest.TestCase):
    def test_pair_app_encodes_deep_link(self):
        ran = []
        with patch("quiver.remote.pairing.mint_pair_token",
                   return_value="tok9"), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"), \
             patch.object(commands.shutil, "which",
                          return_value="/x/qrencode"), \
             patch.object(commands.subprocess, "run",
                          lambda cmd, **k: ran.append(cmd)):
            rc, out, _ = _run(["pair", "--app"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(ran), 1)
        self.assertIn("quiver://pair", ran[0][-1])

    def test_pair_json(self):
        with patch("quiver.remote.pairing.mint_pair_token",
                   return_value="tok9"), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"):
            rc, out, _ = _run(["pair", "--json", "--name=phone"])
        self.assertEqual(rc, 0)
        d = json.loads(out)["data"]
        self.assertEqual(d["host"], "100.1.2.3")
        self.assertEqual(d["port"], 8477)
        self.assertEqual(d["token"], "tok9")
        self.assertEqual(d["name"], "phone")
        self.assertIn("#pair=tok9", d["url"])
        self.assertTrue(d["deep_link"].startswith("quiver://pair?"))
        self.assertIn("token=tok9", d["deep_link"])
        self.assertEqual(d["expires_in_s"], 300)

    def test_pair_default_qr_uses_http_url(self):
        ran = []
        with patch("quiver.remote.pairing.mint_pair_token",
                   return_value="tok9"), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"), \
             patch.object(commands.shutil, "which",
                          return_value="/x/qrencode"), \
             patch.object(commands.subprocess, "run",
                          lambda cmd, **k: ran.append(cmd)):
            rc, out, _ = _run(["pair"])
        self.assertEqual(rc, 0)
        self.assertTrue(ran[0][-1].startswith("http"))


class TestNotifyCmd(unittest.TestCase):
    def test_notify_on_flag(self):
        with patch.object(commands.notify, "configure",
                          return_value={"enabled": True,
                                        "url": "https://ntfy.sh",
                                        "topic": "t"}) as conf:
            rc, out, _ = _run(["notify", "--on"])
        self.assertEqual(rc, 0)
        conf.assert_called_once_with(None, enabled=True)

    def test_notify_off_flag(self):
        with patch.object(commands.notify, "configure",
                          return_value={"enabled": False}) as conf:
            rc, out, _ = _run(["notify", "--off"])
        self.assertEqual(rc, 0)
        conf.assert_called_once_with(None, enabled=False)

    def test_notify_unexpected_args(self):
        rc, _, err = _run(["notify", "extra"])
        self.assertEqual(rc, 1)
        self.assertIn("unexpected args", err)

    def test_notify_bare_no_config(self):
        with patch.object(commands.notify, "load_config",
                          return_value={}):
            rc, out, _ = _run(["notify"])
        self.assertEqual(rc, 0)
        self.assertIn("not configured", out)

    def test_notify_json_redacts_tokens(self):
        cfg = {"enabled": True, "url": "https://ntfy.sh", "topic": "t",
               "ntfy_token": "sekrit", "action_credential": "cred"}
        with patch.object(commands.notify, "load_config",
                          return_value=cfg):
            rc, out, _ = _run(["notify", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]["notify"]
        self.assertEqual(data["ntfy_token"], "***")
        self.assertEqual(data["action_credential"], "***")


class TestTailscaleIp(unittest.TestCase):
    def test_no_binary(self):
        from pathlib import Path

        from quiver.remote import tsid

        # Neither on PATH nor inside Tailscale.app.
        with patch.object(commands.catalog_mod.shutil, "which",
                          return_value=None), \
             patch.object(tsid, "_APPSTORE_EXE", Path("/nonexistent/Tailscale")):
            self.assertEqual(commands.catalog_mod.tailscale_ip(), "")

    def test_first_ipv4_line(self):
        import subprocess as sp
        proc = sp.CompletedProcess([], 0, "100.64.2.3\n100.64.2.4\n", "")
        with patch.object(commands.catalog_mod.shutil, "which",
                          return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=proc):
            self.assertEqual(
                commands.catalog_mod.tailscale_ip(), "100.64.2.3")

    def test_tailscale_fails(self):
        import subprocess as sp
        proc = sp.CompletedProcess([], 1, "", "err")
        with patch.object(commands.catalog_mod.shutil, "which",
                          return_value="/usr/bin/tailscale"), \
             patch("subprocess.run", return_value=proc):
            self.assertEqual(commands.catalog_mod.tailscale_ip(), "")

    def test_tailscale_oserror(self):
        import subprocess as sp
        with patch.object(commands.catalog_mod.shutil, "which",
                          return_value="/usr/bin/tailscale"), \
             patch("subprocess.run",
                   side_effect=sp.TimeoutExpired("tailscale", 5)):
            self.assertEqual(commands.catalog_mod.tailscale_ip(), "")



_PEER = {
    "node_name": "test-iphone.tail0000.ts.net",
    "node_short": "test-iphone",
    "node_id": "nABC",
    "login": "dev@github",
    "ip": "100.66.1.2",
    "tagged": False,
}


class TestDeviceRequests(unittest.TestCase):
    """devices --requests/--approve/--deny act on the hub's own state."""

    def setUp(self):
        import os
        home = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"HOME": home})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_requests_empty_and_listed(self):
        rc, out, _ = _run(["devices", "--requests"])
        self.assertEqual(rc, 0)
        self.assertIn("no pending", out)
        r = pairing.request_pair(None, dict(_PEER), name="phone")
        rc, out, _ = _run(["devices", "--requests"])
        self.assertEqual(rc, 0)
        self.assertIn("phone", out)
        self.assertIn(r["request_id"], out)
        self.assertNotIn(r["code"], out)

    def test_requests_json(self):
        r = pairing.request_pair(None, dict(_PEER))
        rc, out, _ = _run(["devices", "--requests", "--json"])
        self.assertEqual(rc, 0)
        rows = json.loads(out)["data"]["requests"]
        self.assertEqual(rows[0]["request_id"], r["request_id"])
        self.assertNotIn("code", rows[0])

    def test_approve_and_deny(self):
        r = pairing.request_pair(None, dict(_PEER))
        rc, out, _ = _run(["devices", f"--approve={r['code']}"])
        self.assertEqual(rc, 0)
        self.assertIn("approved", out)
        r2 = pairing.request_pair(
            None, dict({**_PEER, "node_id": "nOTHER"})
        )
        rc, out, _ = _run(["devices", f"--deny={r2['code']}"])
        self.assertEqual(rc, 0)
        self.assertIn("denied", out)

    def test_approve_json_and_no_match(self):
        r = pairing.request_pair(None, dict(_PEER))
        rc, out, _ = _run(
            ["devices", f"--approve={r['code']}", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertTrue(data["ok"])
        self.assertTrue(data["device_id"])
        rc, out, _ = _run(["devices", "--approve=000000"])
        self.assertEqual(rc, 1)

    def test_request_id_holds_the_code_to_its_row(self):
        """The Mac app's per-row code field: a code typed against one
        request must not approve a different one, and wrong codes count
        toward that row's lockout."""
        a = pairing.request_pair(None, dict(_PEER), name="a")
        b = pairing.request_pair(
            None, dict({**_PEER, "node_id": "nOTHER"}), name="b")
        rc, out, _ = _run([
            "devices", f"--approve={b['code']}",
            f"--request-id={a['request_id']}", "--json"])
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["data"]["ok"])
        # Neither row was approved by the mismatched code.
        pending = {r["request_id"] for r in pairing.list_requests()}
        self.assertEqual(pending, {a["request_id"], b["request_id"]})
        rc, out, _ = _run([
            "devices", f"--approve={a['code']}",
            f"--request-id={a['request_id']}", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["data"]["device_id"])
        rc, out, _ = _run([
            "devices", f"--deny={b['code']}",
            f"--request-id={b['request_id']}", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["data"]["ok"])
        self.assertEqual(pairing.list_requests(), [])

    def test_request_id_wrong_codes_lock_the_row(self):
        r = pairing.request_pair(None, dict(_PEER))
        wrong = "000000" if r["code"] != "000000" else "111111"
        for _ in range(pairing.MAX_CODE_ATTEMPTS):
            _run(["devices", f"--approve={wrong}",
                  f"--request-id={r['request_id']}"])
        rc, _, _ = _run(["devices", f"--approve={r['code']}",
                         f"--request-id={r['request_id']}"])
        self.assertEqual(rc, 1)
        self.assertEqual(pairing.list_requests(), [])

    def test_request_id_alone_or_empty_is_refused(self):
        # Under --json the refusal goes to stderr, so stdout stays a
        # document the app can decode.
        rc, out, err = _run(["devices", "--request-id=abc", "--json"])
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("--approve", err)
        r = pairing.request_pair(None, dict(_PEER))
        rc, err, _ = _run(["devices", f"--approve={r['code']}",
                           "--request-id="])
        self.assertEqual(rc, 1)
        self.assertIn("--request-id", err)
        self.assertEqual(len(pairing.list_requests()), 1)


class TestUnwritableHome(unittest.TestCase):
    """#523: an unset, empty or read-only HOME is a one-line error and
    exit 1, not an OSError traceback."""

    def _locked_home(self) -> Path:
        """A HOME nothing can be created under; skips when the mode
        can't bite (root writes anyway)."""
        import os
        import stat

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        home.chmod(0o500)
        self.addCleanup(home.chmod, stat.S_IRWXU)
        if os.access(home, os.W_OK):
            self.skipTest("running as a user that ignores modes")
        return home

    def test_approve_request_errors_cleanly(self):
        import os

        home = self._locked_home()
        with patch.dict(os.environ, {"HOME": str(home)}):
            rc, out, err = _run(["devices", "--approve=123456"])
        self.assertEqual(rc, 1)
        self.assertIn("can't write", out)
        self.assertIn(".quiver", out)
        self.assertNotIn("Traceback", out + err)

    def test_error_moves_to_stderr_under_json(self):
        import os

        home = self._locked_home()
        with patch.dict(os.environ, {"HOME": str(home)}):
            rc, out, err = _run(["devices", "--approve=123456", "--json"])
        self.assertEqual(rc, 1)
        self.assertIn("can't write", err)
        # stdout stays clean of anything the app would try to decode.
        self.assertEqual("", out.strip())

    def test_pair_errors_cleanly(self):
        import os

        home = self._locked_home()
        with patch.dict(os.environ, {"HOME": str(home)}), \
             patch("quiver.remote.commands._tailscale_ip",
                   return_value="100.1.2.3"):
            rc, out, err = _run(["pair"])
        self.assertEqual(rc, 1)
        self.assertIn("can't write", out)
        self.assertNotIn("Traceback", out + err)

    def test_read_only_subcommand_still_works(self):
        # load_devices never writes; a locked HOME must not turn a
        # devices listing into an error.
        import os

        home = self._locked_home()
        with patch.dict(os.environ, {"HOME": str(home)}):
            rc, out, _ = _run(["devices"])
        self.assertEqual(rc, 0)
        self.assertIn("no paired", out)

    def test_error_names_the_path_that_failed(self):
        # The write that fails can sit outside the state dir —
        # `serve --install` writes under ~/Library/LaunchAgents or
        # ~/.config/systemd/user — so the line names exc.filename,
        # not the remote dir the error usually comes from.
        boom = PermissionError(13, "Permission denied",
                               "/h/Library/LaunchAgents/x.plist")
        with patch.object(commands.pairing, "list_requests",
                          side_effect=boom):
            rc, out, _ = _run(["devices", "--requests"])
        self.assertEqual(rc, 1)
        self.assertIn("/h/Library/LaunchAgents/x.plist", out)
        self.assertIn("Permission denied", out)
        self.assertNotIn("data/remote", out)

    def test_unresolvable_home_errors_cleanly(self):
        # HOME unset and the uid missing from passwd: Path.home() gives
        # up with RuntimeError before any mkdir is attempted.
        with patch.object(Path, "home",
                          side_effect=RuntimeError(
                              "Could not determine home directory")):
            rc, out, err = _run(["devices"])
        self.assertEqual(rc, 1)
        self.assertIn("can't write", out)
        self.assertIn("home directory", out)
        self.assertNotIn("Traceback", out + err)

    def test_other_runtime_errors_still_raise(self):
        with patch.object(commands.pairing, "load_devices",
                          side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                _run(["devices"])


if __name__ == "__main__":
    unittest.main()
