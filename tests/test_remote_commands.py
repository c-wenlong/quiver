"""remote commands: flag parsing, subcommand dispatch, attach resolution."""

import io
import json
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


class TestServe(unittest.TestCase):
    def test_serve_binds_tailscale(self):
        seen = {}

        def fake_serve(state, host, port):
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


if __name__ == "__main__":
    unittest.main()
