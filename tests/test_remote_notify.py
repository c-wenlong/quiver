"""notify.py: config, scoped approval tokens, ntfy formatting, bus loop."""

import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from swe.remote import notify, pairing, protocol, sshkeys
from swe.remote.hub import GLOBAL_KEY, HubState

from tests._tempdir import temp_dir


def _state(home: Path) -> HubState:
    state = HubState(home)
    state.base_url = "http://100.64.1.2:8477"
    return state


def _until(predicate, timeout=5.0):
    """Poll ``predicate`` until it holds; False once ``timeout`` runs out."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _cfg(home: Path, **over):
    base = {"enabled": True, "url": "https://ntfy.test", "topic": "qw"}
    base.update(over)
    notify.save_config(home, base)
    return base


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)

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
        from swe.paths import remote_dir_for
        path = remote_dir_for(self.home) / "notify.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class TestReady(unittest.TestCase):
    def test_ready_requires_enabled_url_topic(self):
        self.assertTrue(notify.ready(
            {"enabled": True, "url": "u", "topic": "t"}))
        for cfg in (
            {},
            {"enabled": False, "url": "u", "topic": "t"},
            {"enabled": True, "topic": "t"},
            {"enabled": True, "url": "u"},
            {"enabled": True, "url": "", "topic": "t"},
        ):
            self.assertFalse(notify.ready(cfg), cfg)


class TestApprovalTokens(unittest.TestCase):
    def setUp(self):
        self.tokens = notify.ApprovalTokens()

    def test_consume_bound_request_once(self):
        tok = self.tokens.mint("k1", "r1")
        self.assertTrue(self.tokens.consume(tok, "k1", "r1"))
        # Single use: a second spend fails even with the right binding.
        self.assertFalse(self.tokens.consume(tok, "k1", "r1"))

    def test_wrong_session_or_request_rejected(self):
        tok = self.tokens.mint("k1", "r1")
        self.assertFalse(self.tokens.consume(tok, "k2", "r1"))
        self.assertFalse(self.tokens.consume(tok, "k1", "r2"))
        self.assertFalse(self.tokens.consume("bogus", "k1", "r1"))

    def test_expired_token_rejected(self):
        now = time.time()
        tok = self.tokens.mint("k1", "r1", now=now)
        later = now + self.tokens.TTL_S + 1
        self.assertFalse(self.tokens.consume(tok, "k1", "r1", now=later))

    def test_remint_same_request_supersedes(self):
        old = self.tokens.mint("k1", "r1")
        new = self.tokens.mint("k1", "r1")
        self.assertFalse(self.tokens.consume(old, "k1", "r1"))
        self.assertTrue(self.tokens.consume(new, "k1", "r1"))

    def test_other_requests_tokens_coexist(self):
        # Codex app-server approvals overlap on one session: minting B
        # must not kill A's buttons.
        a = self.tokens.mint("k1", "r1")
        b = self.tokens.mint("k1", "r2")
        self.assertTrue(self.tokens.consume(a, "k1", "r1"))
        self.assertTrue(self.tokens.consume(b, "k1", "r2"))
        # Other sessions' tokens are untouched either way.
        other = self.tokens.mint("k2", "r9")
        self.assertTrue(self.tokens.consume(other, "k2", "r9"))

    def test_drop_retires_only_the_resolved_request(self):
        a = self.tokens.mint("k1", "r1")
        b = self.tokens.mint("k1", "r2")
        self.tokens.drop("k1", "r1")
        self.assertFalse(self.tokens.consume(a, "k1", "r1"))
        self.assertTrue(self.tokens.consume(b, "k1", "r2"))

    def test_burn_on_mismatch(self):
        # A mismatched request_id is a failed spend, not a free probe:
        # the token is gone for its real binding too.
        tok = self.tokens.mint("k1", "r1")
        self.assertFalse(self.tokens.consume(tok, "k1", "rX"))
        self.assertFalse(self.tokens.consume(tok, "k1", "r1"))


class TestRetireActionCredential(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)

    def _legacy_cfg(self):
        device_id = "deadbeef01"
        cred = "legacy-cred"
        with pairing._state_lock(self.home):
            devices = pairing.load_devices(self.home)
            devices[device_id] = {
                "name": "ntfy-actions",
                "credential_hash": pairing._hash_credential(cred),
                "created_at": time.time(),
                "last_seen_at": None,
                "revoked_at": None,
            }
            pairing._save_devices(self.home, devices)
        cfg = _cfg(self.home, action_device_id=device_id,
                   action_credential=cred)
        return cfg, device_id

    def test_revokes_row_and_scrubs_config(self):
        cfg, device_id = self._legacy_cfg()
        notify.retire_action_credential(self.home, cfg)
        row = pairing.load_devices(self.home)[device_id]
        self.assertTrue(row["revoked_at"])
        # The credential no longer resolves as a device.
        self.assertIsNone(
            pairing.device_for_credential(self.home, "legacy-cred"))
        saved = notify.load_config(self.home)
        self.assertNotIn("action_device_id", saved)
        self.assertNotIn("action_credential", saved)

    def test_noop_without_legacy_keys(self):
        cfg = _cfg(self.home)
        notify.retire_action_credential(self.home, cfg)
        self.assertEqual(pairing.load_devices(self.home), {})

    def test_unknown_device_still_scrubs(self):
        cfg = _cfg(self.home, action_device_id="ghost",
                   action_credential="c")
        notify.retire_action_credential(self.home, cfg)
        saved = notify.load_config(self.home)
        self.assertNotIn("action_credential", saved)

    def test_failed_revoke_keeps_fields_for_retry(self):
        # Revoking before the save fails must not scrub the config —
        # the fields are the only handle the next start has, and the
        # exposed credential would stay live forever without them.
        cfg, device_id = self._legacy_cfg()
        with patch.object(
            pairing, "revoke_device", side_effect=OSError("disk full")
        ):
            with patch("sys.stderr", io.StringIO()) as err:
                notify.retire_action_credential(self.home, cfg)
        self.assertIn("could not revoke", err.getvalue())
        self.assertFalse(
            pairing.load_devices(self.home)[device_id]["revoked_at"])
        saved = notify.load_config(self.home)
        self.assertEqual(saved["action_device_id"], device_id)
        self.assertEqual(saved["action_credential"], "legacy-cred")
        # The credential still resolves — retirement must retry.
        self.assertIsNotNone(
            pairing.device_for_credential(self.home, "legacy-cred"))

    def test_keys_not_synced_still_scrubs(self):
        # KeysNotSynced is raised *after* the revocation is saved, so
        # the credential is dead and the fields can go; the leftover
        # authorized_keys line is warned about, not retried here.
        cfg, device_id = self._legacy_cfg()
        with patch.object(
            pairing, "revoke_device",
            side_effect=sshkeys.KeysNotSynced("boom"),
        ):
            with patch("sys.stderr", io.StringIO()) as err:
                notify.retire_action_credential(self.home, cfg)
        self.assertIn("SSH key", err.getvalue())
        saved = notify.load_config(self.home)
        self.assertNotIn("action_device_id", saved)
        self.assertNotIn("action_credential", saved)


class TestHandle(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.state = _state(self.home)
        self.cfg = _cfg(self.home)
        self.n = notify.Notifier(self.state, "http://h:8477",
                                 subscribe_key=GLOBAL_KEY)
        self.posts = []
        self.n._post = lambda cfg, payload: self.posts.append(payload)

    def test_approval_event_sends_actions(self):
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "allow rm?"}}
        self.n._handle(ev, self.cfg)
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
        body = json.loads(act["body"])
        self.assertEqual(body, {"request_id": "req-1",
                                "decision": "allow"})

    def test_action_bearer_is_scoped_single_use_token(self):
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "allow rm?"}}
        self.n._handle(ev, self.cfg)
        # All three buttons share one minted token, not a device cred.
        auths = {
            a["headers"]["Authorization"] for a in self.posts[0]["actions"]
        }
        self.assertEqual(len(auths), 1)
        token = auths.pop().removeprefix("Bearer ")
        # Bound to exactly this (session, request_id); one spend.
        self.assertTrue(
            self.state.approval_tokens.consume(token, "k1", "req-1"))
        self.assertFalse(
            self.state.approval_tokens.consume(token, "k1", "req-1"))
        # It never resolves as a device credential.
        self.assertIsNone(
            pairing.device_for_credential(self.home, token))

    def test_approval_prompt_redacted(self):
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1",
                       "prompt": 'Allow Bash: curl -H "Authorization: '
                                 'Bearer sk-abcdefghijklmnop" x'}}
        self.n._handle(ev, self.cfg)
        msg = self.posts[0]["message"]
        self.assertNotIn("sk-abcdefghijklmnop", msg)
        self.assertIn("[REDACTED]", msg)

    def test_approval_prompt_capped(self):
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "x" * 500}}
        self.n._handle(ev, self.cfg)
        self.assertEqual(len(self.posts[0]["message"]), 280)

    def test_approval_options_become_buttons(self):
        # The pane's own menu rows are the buttons — the push answers
        # the real choices, not the allow/deny/always guess.
        ev = {"type": "approval.requested", "session": "claude:s1",
              "data": {"request_id": "req-1", "prompt": "Pick?",
                       "options": [{"n": "1", "label": "SQLite"},
                                   {"n": "2", "label": "Postgres"}]}}
        self.n._handle(ev, self.cfg)
        actions = self.posts[0]["actions"]
        self.assertEqual([a["label"] for a in actions],
                         ["SQLite", "Postgres"])
        self.assertTrue(all(a["action"] == "http" for a in actions))
        bodies = [json.loads(a["body"])["decision"] for a in actions]
        self.assertEqual(bodies, ["1", "2"])
        # Tapping the push itself opens the session's chat, key escaped.
        self.assertEqual(
            self.posts[0]["click"],
            "http://h:8477/#/sessions?chat=claude%3As1")

    def test_approval_long_menu_trades_tail_for_chat_link(self):
        # More rows than a push holds: the first stay buttons, the last
        # slot links to the chat where all of them answer.
        opts = [{"n": str(i), "label": f"Row {i}"} for i in range(1, 8)]
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "Pick?",
                       "options": opts}}
        self.n._handle(ev, self.cfg)
        actions = self.posts[0]["actions"]
        self.assertEqual(len(actions), 3)
        self.assertEqual([a["label"] for a in actions[:2]],
                         ["Row 1", "Row 2"])
        view = actions[2]
        self.assertEqual(view["action"], "view")
        self.assertEqual(view["label"], "All 7 options…")
        self.assertEqual(view["url"], "http://h:8477/#/sessions?chat=k1")

    def test_approval_colliding_buttons_link_to_chat(self):
        # Two rows sharing their first 40 chars would render identical
        # buttons sending different answers — the chat decides instead.
        same = "Allow `git -C /very/long/path/to/some/repo "
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "Pick?",
                       "options": [{"n": "1", "label": same + "push`"},
                                   {"n": "2", "label": same + "reset`"},
                                   {"n": "3", "label": "Deny"}]}}
        self.n._handle(ev, self.cfg)
        actions = self.posts[0]["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "view")
        self.assertEqual(
            actions[0]["url"], "http://h:8477/#/sessions?chat=k1")

    def test_approval_options_fill_the_push(self):
        # Exactly at the cap every row stays a button — no link needed.
        opts = [{"n": str(i), "label": f"Row {i}"} for i in range(1, 4)]
        ev = {"type": "approval.requested", "session": "k1",
              "data": {"request_id": "req-1", "prompt": "Pick?",
                       "options": opts}}
        self.n._handle(ev, self.cfg)
        actions = self.posts[0]["actions"]
        self.assertEqual(len(actions), 3)
        self.assertTrue(all(a["action"] == "http" for a in actions))

    def test_action_url_escapes_session_key(self):
        act = self.n._action("a b@c/d", "r", "allow", "Allow", "c")
        self.assertIn("a%20b%40c%2Fd", act["url"])

    def test_notify_kinds(self):
        cases = [
            ("needs_you", "Needs you", 4, "bell"),
            ("finished", "Finished", 3, "bell"),
            ("error", "Errored", 4, "rotating_light"),
            ("limit", "Limit hit", 4, "hourglass"),
        ]
        for kind, label, prio, tag in cases:
            self.posts.clear()
            self.n._handle({"type": "notify", "session": "k1",
                            "data": {"kind": kind}}, self.cfg)
            self.assertEqual(len(self.posts), 1, kind)
            post = self.posts[0]
            self.assertIn(label.lower(), post["title"])
            self.assertEqual(post["priority"], prio)
            self.assertEqual(post["tags"], [tag])

    def test_limit_carries_harness_line_redacted(self):
        self.n._handle({"type": "notify", "session": "k1", "data": {
            "kind": "limit",
            "message": "You've hit your session limit · resets 9:30am "
                       "sk-abcdefghijklmnop " + "x " * 200}}, self.cfg)
        msg = self.posts[0]["message"]
        self.assertTrue(msg.startswith("You've hit your session limit"))
        self.assertNotIn("sk-abcdefghijklmnop", msg)
        self.assertEqual(len(msg), 280)
        # Other kinds still post an empty body.
        self.posts.clear()
        self.n._handle({"type": "notify", "session": "k1", "data": {
            "kind": "finished", "message": "ignored"}}, self.cfg)
        self.assertEqual(self.posts[0]["message"], "")

    def test_unknown_notify_kind_skipped(self):
        self.n._handle({"type": "notify", "session": "k1",
                        "data": {"kind": "mystery"}}, self.cfg)
        self.assertEqual(self.posts, [])

    def test_unrelated_event_skipped(self):
        self.n._handle({"type": "message.appended", "session": "k1",
                        "data": {}}, self.cfg)
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


    def test_session_title_redacted_in_every_post(self):
        secret = "sk-ant-abcdefghijklmnopqrstuvwxyz0123"
        row = {"title": f"deploy with ANTHROPIC_API_KEY={secret} now"}
        with patch.object(self.state.catalog, "row_for",
                          return_value=row):
            self.n._handle({"type": "notify", "session": "k1",
                            "data": {"kind": "finished"}}, self.cfg)
            self.n._handle({"type": "approval.requested", "session": "k1",
                            "data": {"request_id": "r1",
                                     "prompt": "Allow Bash"}}, self.cfg)
        self.assertEqual(len(self.posts), 2)
        for post in self.posts:
            self.assertNotIn(secret, post["title"])
            self.assertNotIn(secret[:20], post["title"])
            self.assertIn("[REDACTED]", post["title"])

    def test_session_title_redacts_before_the_cut(self):
        # A bare token straddling character 60: cut first, its visible
        # prefix is too short for the token pattern and would leak.
        token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1"
        row = {"title": "x" * 52 + " " + token}
        with patch.object(self.state.catalog, "row_for",
                          return_value=row):
            title = self.n._session_title("k1")
        self.assertNotIn("ghp_", title)
        self.assertNotIn("A1b2", title)
        self.assertTrue(title.startswith("x" * 52 + " [REDACT"), title)

class TestPost(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
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

    def test_post_retries_a_cert_failure_on_the_os_bundle(self):
        # python.org builds ship no CA store: the default context fails
        # every https push until the OS bundle is loaded.
        import ssl
        import urllib.error
        from swe.remote import agent as agent_mod
        self.addCleanup(setattr, agent_mod, "_fallback_ctx", None)
        ctx = object()
        calls = []

        def fake(req, timeout=None, context=None):
            calls.append(context)
            if context is None:
                raise urllib.error.URLError(
                    ssl.SSLCertVerificationError("no CA"))
            m = MagicMock()
            m.__enter__.return_value = m
            return m

        with patch("urllib.request.urlopen", fake), \
             patch("swe.harness.rate_limits._verified_context",
                   return_value=ctx):
            self.n._post({"url": "https://n.test", "topic": "t"},
                         {"topic": "t"})
        self.assertEqual(calls, [None, ctx])

    def test_post_failure_warns_once_per_outage(self):
        cfg = {"url": "https://n.test", "topic": "t"}
        with patch("urllib.request.urlopen", side_effect=OSError("down")), \
             patch.object(notify, "_warn") as warn:
            for _ in range(3):
                with self.assertRaises(OSError):
                    self.n._post(cfg, {"topic": "t"})
        self.assertEqual(warn.call_count, 1)
        self._captured(cfg)  # a success re-arms the warning
        with patch("urllib.request.urlopen", side_effect=OSError("down")), \
             patch.object(notify, "_warn") as warn:
            with self.assertRaises(OSError):
                self.n._post(cfg, {"topic": "t"})
        self.assertEqual(warn.call_count, 1)


class TestRun(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)

    def test_disabled_config_returns_immediately(self):
        state = _state(self.home)
        n = notify.Notifier(state, "http://h:1", subscribe_key=GLOBAL_KEY)
        done = threading.Event()
        threading.Thread(
            target=lambda: (n.run(), done.set()), daemon=True).start()
        self.assertTrue(done.wait(3))

    def test_partial_config_leaves_no_dead_subscriber(self):
        # enabled but missing url/topic: run() must not leave the queue
        # it subscribed sitting on the bus filling to the drop cap.
        for cfg in ({"enabled": True},
                    {"enabled": True, "url": "https://n.test"},
                    {"enabled": True, "topic": "t"}):
            notify.save_config(self.home, cfg)
            state = _state(self.home)
            n = notify.Notifier(state, "http://h:1",
                                subscribe_key=GLOBAL_KEY)
            n.subscribe()  # start() subscribes before producers spawn
            done = threading.Event()
            threading.Thread(
                target=lambda: (n.run(), done.set()),
                daemon=True).start()
            self.assertTrue(done.wait(3))
            self.assertEqual(state.bus._subs, [])

    def test_run_consumes_and_unsubscribes(self):
        _cfg(self.home)
        state = _state(self.home)
        n = notify.Notifier(state, "http://h:1", subscribe_key=GLOBAL_KEY)
        posts = []
        n._post = lambda cfg, payload: posts.append(payload)
        t = threading.Thread(target=n.run, daemon=True)
        self.addCleanup(state._stop.set)
        t.start()
        # Wait for run() to subscribe before publishing.
        self.assertTrue(_until(lambda: state.bus._subs))
        state.emit("k1", protocol.event(
            "notify", "k1", "m1", {"kind": "finished"}))
        self.assertTrue(_until(lambda: posts))
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
        t = threading.Thread(target=n.run, daemon=True)
        self.addCleanup(state._stop.set)
        t.start()
        self.assertTrue(_until(lambda: posts))
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
        t = threading.Thread(target=n.run, daemon=True)
        self.addCleanup(state._stop.set)
        t.start()
        self.assertTrue(_until(lambda: state.bus._subs))
        for _ in range(2):
            state.emit("k1", protocol.event(
                "notify", "k1", "m1", {"kind": "finished"}))
        self.assertTrue(_until(lambda: len(calls) >= 2))
        self.assertEqual(len(calls), 2)
        self.assertTrue(t.is_alive())
        state._stop.set()
        t.join(timeout=3)


class TestCmdNotify(unittest.TestCase):
    """commands._cmd_notify: merge, redaction, bare display."""

    def setUp(self):
        from swe.remote import commands
        self.commands = commands
        self.home = temp_dir(self)
        self.paths_patcher = patch(
            "swe.remote.notify.remote_dir_for",
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
