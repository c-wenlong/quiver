"""notify.py: config, action credential, ntfy formatting, bus loop."""

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import notify, pairing, protocol
from quiver.remote.hub import GLOBAL_KEY, HubState


def _state(home: Path | None = None) -> HubState:
    home = home or Path(tempfile.mkdtemp())
    state = HubState(home)
    state.base_url = "http://100.64.1.2:8477"
    return state


def _cfg(home: Path, **over):
    base = {"enabled": True, "url": "https://ntfy.test", "topic": "qw"}
    base.update(over)
    notify.save_config(home, base)
    return base


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_load_missing_returns_empty(self):
        self.assertEqual(notify.load_config(self.home), {})

    def test_load_corrupt_returns_empty(self):
        path = self.home / "remote" / "notify.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.assertEqual(notify.load_config(self.home), {})

    def test_configure_merges_and_strips_url(self):
        notify.configure(self.home, url="https://ntfy.test/", topic="t1")
        cfg = notify.configure(self.home, enabled=True)
        self.assertEqual(
            cfg, {"enabled": True, "url": "https://ntfy.test", "topic": "t1"}
        )

    def test_configure_empty_token_clears(self):
        notify.configure(self.home, url="u", topic="t", ntfy_token="abc")
        cfg = notify.configure(self.home, ntfy_token="")
        self.assertIsNone(cfg["ntfy_token"])

    def test_saved_config_is_private(self):
        notify.save_config(self.home, {"enabled": True})
        from quiver.paths import remote_dir_for
        path = remote_dir_for(self.home) / "notify.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class TestActionCredential(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.cfg = _cfg(self.home)

    def test_mints_device_row_and_stores(self):
        cred = notify._action_credential(self.home, self.cfg)
        self.assertEqual(len(cred) > 20, True)
        device_id = self.cfg["action_device_id"]
        row = pairing.load_devices(self.home)[device_id]
        self.assertEqual(row["name"], "ntfy-actions")
        self.assertEqual(row["credential_hash"],
                         pairing._hash_credential(cred))
        # Persisted credential survives a reload.
        self.assertEqual(notify.load_config(self.home)
                         ["action_credential"], cred)

    def test_reuses_existing_device(self):
        cred = notify._action_credential(self.home, self.cfg)
        again = notify._action_credential(self.home, self.cfg)
        self.assertEqual(cred, again)
        self.assertEqual(
            len(pairing.load_devices(self.home)), 1)

    def test_remints_after_revocation(self):
        notify._action_credential(self.home, self.cfg)
        old_id = self.cfg["action_device_id"]
        pairing.revoke_device(self.home, old_id)
        new_cred = notify._action_credential(self.home, self.cfg)
        self.assertNotEqual(self.cfg["action_device_id"], old_id)
        devices = pairing.load_devices(self.home)
        self.assertEqual(len(devices), 2)
        self.assertTrue(devices[old_id]["revoked_at"])
        self.assertTrue(new_cred)


class TestHandle(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = _state(self.home)
        self.cfg = _cfg(self.home)
        self.n = notify.Notifier(self.state, "http://h:8477",
                                 subscribe_key=GLOBAL_KEY)
        self.posts = []
        self.n._post = lambda cfg, payload: self.posts.append(payload)

    def test_approval_event_sends_actions(self):
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "allow rm?"}}
        self.n._handle(ev, self.cfg, "CRED")
        self.assertEqual(len(self.posts), 1)
        post = self.posts[0]
        self.assertEqual(post["topic"], "qw")
        self.assertEqual(post["priority"], 5)
        self.assertIn("approval", post["title"])
        self.assertEqual(post["message"], "allow rm?")
        labels = [a["label"] for a in post["actions"]]
        self.assertEqual(labels, ["Allow", "Deny", "Always"])
        act = post["actions"][0]
        self.assertEqual(act["action"], "http")
        self.assertEqual(act["method"], "POST")
        self.assertEqual(
            act["url"], "http://h:8477/v1/sessions/k1/approval")
        self.assertEqual(
            act["headers"]["Authorization"], "Bearer CRED")
        body = json.loads(act["body"])
        self.assertEqual(body, {"request_id": "req-1",
                                "decision": "allow"})

    def test_action_url_escapes_session_key(self):
        act = self.n._action("a b@c/d", "r", "allow", "Allow", "c")
        self.assertIn("a%20b%40c%2Fd", act["url"])

    def test_notify_kinds(self):
        cases = [
            ("needs_you", "Needs you", 4, "bell"),
            ("finished", "Finished", 3, "bell"),
            ("error", "Errored", 4, "rotating_light"),
            ("limit", "Limit hit", 3, "bell"),
        ]
        for kind, label, prio, tag in cases:
            self.posts.clear()
            self.n._handle({"type": "notify", "session": "k1",
                            "data": {"kind": kind}}, self.cfg, "c")
            self.assertEqual(len(self.posts), 1, kind)
            post = self.posts[0]
            self.assertIn(label.lower(), post["title"])
            self.assertEqual(post["priority"], prio)
            self.assertEqual(post["tags"], [tag])

    def test_unknown_notify_kind_skipped(self):
        self.n._handle({"type": "notify", "session": "k1",
                        "data": {"kind": "mystery"}}, self.cfg, "c")
        self.assertEqual(self.posts, [])

    def test_unrelated_event_skipped(self):
        self.n._handle({"type": "message.appended", "session": "k1",
                        "data": {}}, self.cfg, "c")
        self.assertEqual(self.posts, [])

    def test_session_title_prefers_catalog(self):
        row = {"title": "Fix the thing", "cwd": "/x/proj"}
        with patch.object(self.state.catalog, "row_for",
                          return_value=row):
            self.assertEqual(
                self.n._session_title("k1"), "Fix the thing")
        row = {"title": "", "cwd": "/x/proj"}
        with patch.object(self.state.catalog, "row_for",
                          return_value=row):
            self.assertEqual(self.n._session_title("k1"), "proj")
        with patch.object(self.state.catalog, "row_for",
                          return_value=None):
            self.assertEqual(self.n._session_title("k1"), "Quiver")


class TestPost(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.n = notify.Notifier(_state(self.home), "http://h:1",
                                 subscribe_key=GLOBAL_KEY)

    def _captured(self, cfg):
        reqs = []

        def fake(req, timeout=None):
            reqs.append(req)
            m = MagicMock()
            m.__enter__.return_value = m
            return m

        with patch("urllib.request.urlopen", fake):
            self.n._post(cfg, {"topic": "t"})
        return reqs[0]

    def test_post_shape(self):
        cfg = {"url": "https://n.test", "topic": "t"}
        req = self._captured(cfg)
        self.assertEqual(req.full_url, "https://n.test")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(
            req.get_header("Content-type"), "application/json")
        self.assertFalse(req.has_header("Authorization"))

    def test_post_sends_ntfy_token(self):
        cfg = {"url": "https://n.test", "topic": "t",
               "ntfy_token": "tok"}
        req = self._captured(cfg)
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")

    def test_post_propagates_http_errors(self):
        cfg = {"url": "https://n.test", "topic": "t"}
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                self.n._post(cfg, {"topic": "t"})


class TestRun(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_disabled_config_returns_immediately(self):
        state = _state(self.home)
        n = notify.Notifier(state, "http://h:1", subscribe_key=GLOBAL_KEY)
        done = threading.Event()
        threading.Thread(
            target=lambda: (n.run(), done.set()), daemon=True).start()
        self.assertTrue(done.wait(3))

    def test_run_consumes_and_unsubscribes(self):
        _cfg(self.home)
        state = _state(self.home)
        n = notify.Notifier(state, "http://h:1", subscribe_key=GLOBAL_KEY)
        posts = []
        n._post = lambda cfg, payload: posts.append(payload)
        with patch.object(notify, "_action_credential", return_value="c"):
            t = threading.Thread(target=n.run, daemon=True)
            t.start()
            # Wait for subscribe before publishing.
            for _ in range(100):
                if state.bus._subs:
                    break
                time.sleep(0.01)
            state.emit("k1", protocol.event(
                "notify", "k1", "m1", {"kind": "finished"}))
            for _ in range(100):
                if posts:
                    break
                time.sleep(0.01)
            self.assertEqual(len(posts), 1)
            self.assertIn("finished", posts[0]["title"].lower())
            state._stop.set()
            t.join(timeout=3)
            self.assertFalse(t.is_alive())
            self.assertEqual(state.bus._subs, [])

    def test_early_subscribe_captures_events_before_run(self):
        # HubState.start() calls subscribe() before the producers spawn —
        # an approval emitted in that gap must still reach _handle when
        # run() finally starts consuming.
        _cfg(self.home)
        state = _state(self.home)
        n = notify.Notifier(state, "http://h:1", subscribe_key=GLOBAL_KEY)
        posts = []
        n._post = lambda cfg, payload: posts.append(payload)
        n.subscribe()
        state.emit("k1", protocol.event(
            "approval.requested", "k1", "m1",
            {"request_id": "req-early", "prompt": "run it?"}))
        with patch.object(notify, "_action_credential",
                          return_value="c"):
            t = threading.Thread(target=n.run, daemon=True)
            t.start()
            for _ in range(100):
                if posts:
                    break
                time.sleep(0.01)
            state._stop.set()
            t.join(timeout=3)
        self.assertEqual(len(posts), 1)
        self.assertIn("approval", posts[0]["title"])

    def test_handle_exception_does_not_kill_loop(self):
        _cfg(self.home)
        state = _state(self.home)
        n = notify.Notifier(state, "http://h:1", subscribe_key=GLOBAL_KEY)
        calls = []

        def boom(cfg, payload):
            calls.append(payload)
            raise OSError("ntfy down")

        n._post = boom
        with patch.object(notify, "_action_credential", return_value="c"):
            t = threading.Thread(target=n.run, daemon=True)
            t.start()
            for _ in range(100):
                if state.bus._subs:
                    break
                time.sleep(0.01)
            for _ in range(2):
                state.emit("k1", protocol.event(
                    "notify", "k1", "m1", {"kind": "finished"}))
            for _ in range(100):
                if len(calls) >= 2:
                    break
                time.sleep(0.01)
            self.assertEqual(len(calls), 2)
            self.assertTrue(t.is_alive())
            state._stop.set()
            t.join(timeout=3)


class TestCmdNotify(unittest.TestCase):
    """commands._cmd_notify: merge, redaction, bare display."""

    def setUp(self):
        from quiver.remote import commands
        self.commands = commands
        self.home = Path(tempfile.mkdtemp())
        self.paths_patcher = patch(
            "quiver.remote.notify.remote_dir_for",
            side_effect=lambda home=None: self.home / "remote")
        self.paths_patcher.start()
        self.addCleanup(self.paths_patcher.stop)

    def _run(self, flags):
        with patch("sys.stdout", io.StringIO()) as out:
            rc = self.commands._cmd_notify(flags, json_view=False)
        return rc, out.getvalue()

    def test_unconfigured_prints_hint(self):
        rc, out = self._run({})
        self.assertEqual(rc, 0)
        self.assertIn("--ntfy", out)

    def test_configure_and_show(self):
        rc, out = self._run(
            {"--ntfy": "https://n.test", "--topic": "qw"})
        self.assertEqual(rc, 0)
        self.assertIn("https://n.test/qw", out)
        cfg = notify.load_config(self.home)
        self.assertEqual(cfg["url"], "https://n.test")
        self.assertTrue(cfg["enabled"])  # configuring implies on

    def test_off_disables(self):
        self._run({"--ntfy": "https://n.test", "--topic": "qw"})
        rc, out = self._run({"--off": "1"})
        self.assertEqual(rc, 0)
        self.assertIn("off", out)
        self.assertFalse(notify.load_config(self.home)["enabled"])

    def test_json_redacts_secrets(self):
        self._run({"--ntfy": "https://n.test", "--topic": "qw",
                   "--ntfy-token": "sekret"})
        with patch("sys.stdout", io.StringIO()) as out:
            rc = self.commands._cmd_notify({}, json_view=True)
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())["data"]
        self.assertEqual(payload["notify"]["ntfy_token"], "***")
        self.assertEqual(payload["notify"]["url"], "https://n.test")
        self.assertNotIn("sekret", out.getvalue())
