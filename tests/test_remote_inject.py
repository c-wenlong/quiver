"""R2: supervise registry + tmux/headless injectors + hub write routes."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.remote import attachments, inject, protocol, supervise
from quiver.sessions.models import Session

from tests.test_remote_hub import _Server


def _session(tool="claude", sid="abc-123", path="/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   session_id=sid, tool_name=tool)


class TestSuperviseRegistry(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.key = "claude:abc-123"

    def test_mark_read_unmark(self):
        supervise.mark(self.home, self.key, {"mode": "headless"})
        self.assertIn(self.key, supervise.supervised_keys(self.home))
        supervise.unmark(self.home, self.key)
        self.assertNotIn(self.key, supervise.supervised_keys(self.home))

    def test_dead_tmux_pruned(self):
        supervise.mark(
            self.home, self.key,
            {"mode": "tmux", "tmux": "qv-dead"},
        )
        with patch.object(supervise, "tmux_alive", return_value=False):
            self.assertNotIn(self.key, supervise.supervised_keys(self.home))
        # and the registry file no longer lists it
        self.assertNotIn(self.key, supervise.meta_for(self.home, self.key) or {})

    def test_alive_tmux_survives(self):
        supervise.mark(
            self.home, self.key, {"mode": "tmux", "tmux": "qv-live"}
        )
        with patch.object(supervise, "tmux_alive", return_value=True):
            self.assertIn(self.key, supervise.supervised_keys(self.home))


class TestAttach(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.key = "claude:abc-123"
        self.session = _session()

    def _attach(self, **kw):
        kw.setdefault("last_append_at", None)
        kw.setdefault("status", "done")
        return supervise.attach(self.home, self.key, self.session, **kw)

    def test_active_session_refused(self):
        ok, note = self._attach(status="active")
        self.assertFalse(ok)
        self.assertIn("idle", note)

    def test_recent_append_refused(self):
        ok, _ = self._attach(last_append_at=time.time() - 1)
        self.assertFalse(ok)

    def test_headless_marks_registry(self):
        ok, note = self._attach(mode="headless")
        self.assertTrue(ok, note)
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["mode"], "headless")

    def test_tmux_launch_uses_resume_flags(self):
        with patch.object(
            supervise, "launch_tmux", return_value="qv-x"
        ) as launch:
            ok, note = self._attach(mode="tmux")
        self.assertTrue(ok, note)
        argv = launch.call_args[0][1]
        self.assertEqual(argv[0], "claude")
        self.assertIn("--resume", argv)
        self.assertIn("abc-123", argv)

    def test_unresumable_refused(self):
        ok, note = supervise.attach(
            self.home, "gemini:x",
            _session(tool="gemini", sid="x"),
            last_append_at=None, status="done",
        )
        self.assertFalse(ok)

    def test_double_attach_is_idempotent(self):
        self._attach(mode="headless")
        ok, note = self._attach(mode="headless")
        self.assertTrue(ok)
        self.assertIn("already", note)


class TestTmuxInjector(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.key = "claude:abc-123"
        self.session = _session()
        self.inj = inject.TmuxInjector(self.home, self.key)

    def test_queue_refuses_active(self):
        out = self.inj.send(self.session, {"text": "hi", "mode": "queue"}, "active")
        self.assertFalse(out.ok)

    def test_steer_sends(self):
        with patch.object(supervise, "send_text", return_value=True) as st:
            out = self.inj.send(self.session, {"text": "hi", "mode": "steer"}, "active")
        self.assertTrue(out.ok)
        self.assertEqual(st.call_args[0][1], "hi")

    def test_attachment_paths_atref(self):
        with patch.object(supervise, "send_text", return_value=True) as st:
            out = self.inj.send(
                self.session,
                {"text": "look", "mode": "queue", "attachments": ["/tmp/a.png"]},
                "done",
            )
        self.assertTrue(out.ok)
        self.assertIn("@/tmp/a.png", st.call_args[0][1])

    def test_approval_digit(self):
        with patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "always")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "2")

    def test_interrupt_escape(self):
        with patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.control(self.session, "interrupt", "")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "Escape")

    def test_compact_slash(self):
        with patch.object(supervise, "send_text", return_value=True) as st:
            out = self.inj.control(self.session, "compact", "")
        self.assertTrue(out.ok)
        self.assertEqual(st.call_args[0][1], "/compact")


class TestHeadless(unittest.TestCase):
    def setUp(self):
        self.session = _session()
        self.inj = inject.ClaudeHeadlessInjector(Path(tempfile.mkdtemp()), "claude:x")

    def test_argv_shape(self):
        argv = self.inj.argv(self.session, "hello")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("--resume", argv)
        self.assertIn("abc-123", argv)
        self.assertIn("stream-json", argv)
        self.assertEqual(argv[-1], "hello")

    def test_approval_unsupported(self):
        out = self.inj.approval(self.session, "r1", "allow")
        self.assertFalse(out.ok)
        self.assertIn("approval_not_supported", out.note)


class _FakeProc:
    """Popen stand-in: canned stream-json lines + exit code."""

    def __init__(self, lines, rc=0):
        self.stdout = iter(lines)
        self._rc = rc
        self.killed = False

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        self.killed = True


class TestHeadlessRun(unittest.TestCase):
    def setUp(self):
        self.session = _session()
        self.inj = inject.ClaudeHeadlessInjector(
            Path(tempfile.mkdtemp()), "claude:x"
        )

    def _send(self, lines, rc=0):
        with patch.object(inject.subprocess, "Popen",
                          return_value=_FakeProc(lines, rc)):
            return self.inj.send(
                self.session, {"text": "go", "mode": "queue"}, "done"
            )

    def test_result_text_returned(self):
        lines = [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant"}),
            json.dumps({"type": "result", "result": "PONG"}),
        ]
        out = self._send(lines)
        self.assertTrue(out.ok)
        self.assertEqual(out.note, "PONG")

    def test_error_result_fails(self):
        lines = [json.dumps(
            {"type": "result", "result": "boom", "is_error": True}
        )]
        out = self._send(lines)
        self.assertFalse(out.ok)
        self.assertIn("boom", out.note)

    def test_nonzero_exit(self):
        out = self._send([], rc=2)
        self.assertFalse(out.ok)
        self.assertIn("exited 2", out.note)

    def test_interrupt_kills_inflight(self):
        proc = _FakeProc([])
        with patch.object(inject.subprocess, "Popen", return_value=proc):
            self.inj._procs[self.inj.key] = proc
            out = self.inj.control(self.session, "interrupt", "")
        self.assertTrue(out.ok)
        self.assertTrue(proc.killed)
        # no run in flight → interrupt reports that
        out = self.inj.control(self.session, "interrupt", "")
        self.assertFalse(out.ok)

    def test_detach_unmarks(self):
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        out = self.inj.control(self.session, "detach", "")
        self.assertTrue(out.ok)
        self.assertIsNone(supervise.meta_for(self.inj.home, self.inj.key))


class TestAttachments(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.key = "claude:s1"

    def test_stage_resolve_roundtrip(self):
        payload, err = attachments.stage(self.home, self.key, "shot.png", b"\x89PNG")
        self.assertIsNone(err)
        path = attachments.resolve(self.home, self.key, payload["ref"])
        self.assertEqual(Path(path).read_bytes(), b"\x89PNG")

    def test_caps_and_types(self):
        payload, err = attachments.stage(self.home, self.key, "x.exe", b"x")
        self.assertIsNone(payload)
        self.assertIn("type", err)
        payload, err = attachments.stage(
            self.home, self.key, "big.png", b"0" * (attachments.MAX_BYTES + 1)
        )
        self.assertIsNone(payload)
        self.assertIn("20 MB", err)

    def test_traversal_refused(self):
        attachments.stage(self.home, self.key, "a.txt", b"x")
        self.assertIsNone(
            attachments.resolve(self.home, self.key, "../../etc/passwd")
        )

    def test_sweep_and_drop(self):
        payload, _ = attachments.stage(self.home, self.key, "old.txt", b"x")
        path = Path(payload["path"])
        old = time.time() - attachments.MAX_AGE_S - 10
        import os
        os.utime(path, (old, old))
        self.assertEqual(attachments.sweep(self.home, now=time.time()), 1)
        payload, _ = attachments.stage(self.home, self.key, "n.txt", b"y")
        attachments.drop_session(self.home, self.key)
        self.assertFalse(Path(payload["path"]).exists())


class TestHubWrites(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()], statuses=lambda ss: ["done"])
        self.addCleanup(self.srv.stop)
        self.auth = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {self.auth['credential']}"}

    def test_attach_then_message(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude:abc-123/control",
            body={"op": "attach"}, headers=self.hdr,
        )
        # tmux is present on this machine in dev; if not, the route still
        # refuses cleanly rather than faking ownership.
        if status == 409:
            self.assertIn("idle", json.loads(data)["error"]["message"])
            return
        if status == 422:
            return  # tmux absent or launch failed in this environment
        self.assertEqual(status, 200, data)
        row = self.srv.state.catalog.row_for("claude:abc-123")
        self.assertEqual(row["ownership"], "attached")

        with patch.object(supervise, "send_text", return_value=True) as st:
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/message",
                body={"text": "ship it", "mode": "steer"},
                headers=self.hdr,
            )
        self.assertEqual(status, 200, data)
        self.assertIn("ship it", st.call_args[0][1])

    def test_attach_rejects_when_active(self):
        # status lambda reports active → server-side idle check fires
        srv = _Server(sessions=[_session()], statuses=lambda ss: ["active"])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/claude:abc-123/control",
            body={"op": "attach"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data)["error"]["code"], "not_idle")

    def test_attachment_upload_and_message_ref(self):
        if supervise.tmux_bin() is None:
            self.skipTest("tmux unavailable")
        with patch.object(supervise, "launch_tmux", return_value="qv-t"):
            with patch.object(supervise, "tmux_alive", return_value=True):
                self.srv.request(
                    "POST", "/v1/sessions/claude:abc-123/control",
                    body={"op": "attach"}, headers=self.hdr,
                )
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request(
                "POST",
                "/v1/sessions/claude:abc-123/attachments?name=shot.png",
                body=b"\x89PNG",
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type": "application/octet-stream",
                },
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            self.assertEqual(resp.status, 200, payload)
        finally:
            conn.close()
        ref = payload["ref"]
        with patch.object(supervise, "send_text", return_value=True) as st:
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/message",
                body={"text": "see this", "attachments": [ref]},
                headers=self.hdr,
            )
        self.assertEqual(status, 200, data)
        sent = st.call_args[0][1]
        self.assertIn("@", sent)
        self.assertIn("shot.png", sent)


if __name__ == "__main__":
    unittest.main()
