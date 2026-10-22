"""Tier-A adapter tests: opencode serve + codex app-server injectors.

No real harness binaries — ``_spawn`` is patched with scripted fakes
(os.pipe for the app-server's stdio so the real reader thread runs).
"""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import inject, protocol, supervise
from quiver.sessions.models import Session


def _session(tool="opencode", sid="ses_1", cwd="/tmp/proj"):
    return Session(
        timestamp=0, agent=tool, path=cwd, title="t",
        session_id=sid, tool_name=tool,
    )


def _meta(mode, tool):
    return {"mode": mode, "tool_name": tool, "session_id": "s1"}


# -- opencode serve -----------------------------------------------------


class _FakeServeProc:
    def __init__(self):
        self.killed = False

    def poll(self):
        return None if not self.killed else 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class TestOpencodeServe(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        inject.OpencodeServeInjector._serves.clear()
        self.calls = []          # (method, path, payload)
        self.proc = _FakeServeProc()
        # scripted replies keyed by path fragment
        self.routes = {"session": (200, b"[]")}

        def fake_call(base, method, path, payload, timeout=20.0):
            self.calls.append((method, path, payload))
            frags = sorted(self.routes, key=len, reverse=True)
            for frag in frags:
                if frag in path:
                    return self.routes[frag]
            return (404, b"{}")

        proc = self.proc
        self._patchers = [
            patch.object(
                inject.OpencodeServeInjector, "_spawn",
                staticmethod(lambda *a, **k: proc),
            ),
            patch.object(inject, "_serve_call", fake_call),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        inject.OpencodeServeInjector._serves.clear()

    def _injector(self, emit=None):
        return inject.OpencodeServeInjector(
            self.home, "opencode:s1", emit=emit, machine="m1"
        )

    def test_injector_for_dispatch(self):
        inj = inject.injector_for(
            self.home, "k", _meta("serve", "opencode"))
        self.assertIsInstance(inj, inject.OpencodeServeInjector)
        inj2 = inject.injector_for(
            self.home, "k", _meta("serve", "claude"))
        self.assertIsInstance(inj2, inject.TmuxInjector)

    def test_send_posts_prompt_async(self):
        session = _session()
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        res = self._injector().send(
            session, {"text": "hi there", "mode": "queue"}, "idle")
        self.assertTrue(res.ok)
        self.assertEqual(
            self.calls[-1],
            ("POST", "/session/ses_1/prompt_async",
             {"parts": [{"type": "text", "text": "hi there"}]}),
        )

    def test_send_attachments_prefix(self):
        session = _session()
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        self._injector().send(
            session,
            {"text": "see this", "mode": "queue",
             "attachments": ["/tmp/a.png"]},
            "idle")
        sent = self.calls[-1][2]["parts"][0]["text"]
        self.assertTrue(sent.startswith("@/tmp/a.png"))

    def test_send_steer_degrades_when_active(self):
        session = _session()
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        res = self._injector().send(
            session, {"text": "x", "mode": "steer"}, "active")
        self.assertTrue(res.ok)
        self.assertTrue(res.degraded)

    def test_send_requires_supervised(self):
        session = _session()
        res = self._injector().send(
            session, {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("detached", res.note)

    def test_send_spawn_failure(self):
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda cwd, port: (_ for _ in ()).throw(OSError())),
        ):
            supervise.mark(self.home, "k2", _meta("serve", "opencode"))
            res = self._injector().send(
                _session(), {"text": "x", "mode": "queue"}, "idle")
            # unsupervised sessions refuse before spawning
            self.assertFalse(res.ok)

    def test_approval_posts_mapped_decision(self):
        session = _session()
        supervise.mark(self.home, "k", _meta("serve", "opencode"))
        self.routes["permissions"] = (200, b"{}")
        res = self._injector().approval(session, "perm-9", "always")
        self.assertTrue(res.ok)
        method, path, payload = self.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("/session/ses_1/permissions/perm-9", path)
        self.assertEqual(payload, {"response": "always"})

    def test_approval_falls_back_to_legacy_reply(self):
        session = _session()
        supervise.mark(self.home, "k", _meta("serve", "opencode"))
        # /permissions 404s; the legacy /permission/{id}/reply answers.
        self.routes["permissions"] = (404, b"")
        self.routes["reply"] = (200, b"{}")
        res = self._injector().approval(session, "perm-1", "deny")
        self.assertTrue(res.ok)
        self.assertIn("/permission/perm-1/reply", self.calls[-1][1])
        self.assertEqual(self.calls[-1][2], {"reply": "reject"})

    def test_approval_unknown_decision(self):
        res = self._injector().approval(_session(), "p1", "bogus")
        self.assertFalse(res.ok)

    def test_interrupt_posts_abort(self):
        session = _session()
        supervise.mark(self.home, "k", _meta("serve", "opencode"))
        self.routes["abort"] = (200, b"{}")
        res = self._injector().control(session, "interrupt", "")
        self.assertTrue(res.ok)
        self.assertIn("/session/ses_1/abort", self.calls[-1][1])

    def test_detach_releases_and_unmarks(self):
        session = _session()
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        inj = self._injector()
        inj.send(session, {"text": "x", "mode": "queue"}, "idle")
        res = inj.control(session, "detach", "")
        self.assertTrue(res.ok)
        self.assertIsNone(supervise.meta_for(self.home, "opencode:s1"))
        self.assertTrue(self.proc.killed)
        self.assertEqual(inject.OpencodeServeInjector._serves, {})

    def test_serve_event_permission_asked_emits(self):
        events = []
        serve = inject._ServeProc(_FakeServeProc(), "http://x")
        serve.subs["ses_1"] = (
            "k", lambda k, ev: events.append(ev), "m1")
        inject._serve_event(serve, json.dumps({
            "type": "permission.asked",
            "properties": {
                "id": "perm-7", "sessionID": "ses_1",
                "title": "run bash", "patterns": ["ls *"],
            },
        }).encode())
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["type"], "approval.requested")
        self.assertEqual(ev["session"], "k")
        self.assertEqual(ev["data"]["request_id"], "perm-7")
        self.assertIn("run bash", ev["data"]["prompt"])

    def test_serve_event_replied_resolves(self):
        events = []
        serve = inject._ServeProc(_FakeServeProc(), "http://x")
        serve.subs["ses_1"] = (
            "k", lambda k, ev: events.append(ev), "m1")
        serve.permissions["perm-3"] = "ses_1"
        inject._serve_event(serve, json.dumps({
            "type": "permission.replied",
            "properties": {"id": "perm-3", "sessionID": "ses_1"},
        }).encode())
        self.assertEqual(events[0]["type"], "approval.resolved")
        self.assertEqual(events[0]["data"]["request_id"], "perm-3")
        self.assertNotIn("perm-3", serve.permissions)

    def test_serve_event_ignores_other_types_and_sessions(self):
        events = []
        serve = inject._ServeProc(_FakeServeProc(), "http://x")
        serve.subs["ses_1"] = (
            "k", lambda k, ev: events.append(ev), "m1")
        for blob in (
            {"type": "session.idle", "properties": {"sessionID": "s"}},
            {"type": "permission.asked",
             "properties": {"id": "p", "sessionID": "other"}},
            {"type": "permission.asked", "properties": {}},
        ):
            inject._serve_event(serve, json.dumps(blob).encode())
        self.assertEqual(events, [])

    def test_serve_shared_per_cwd(self):
        supervise.mark(self.home, "k1", _meta("serve", "opencode"))
        supervise.mark(self.home, "k2", _meta("serve", "opencode"))
        s1, s2 = _session(sid="ses_a"), _session(sid="ses_b")
        inject.OpencodeServeInjector(self.home, "k1").send(
            s1, {"text": "a", "mode": "queue"}, "idle")
        inject.OpencodeServeInjector(self.home, "k2").send(
            s2, {"text": "b", "mode": "queue"}, "idle")
        serve = next(iter(inject.OpencodeServeInjector._serves.values()))
        self.assertEqual(serve.refs, 2)
        self.assertEqual(set(serve.subs), {"ses_a", "ses_b"})


# -- codex app-server ---------------------------------------------------


class _Pipe:
    """Write end of the fake proc's stdin; feeds canned responses."""

    def __init__(self, proc):
        self.proc = proc
        self.buf = b""

    def write(self, data):
        self.proc._on_stdin(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass


class _FakeAppProc:
    """`codex app-server` stand-in: os.pipe stdout so the injector's
    real reader thread runs; scripted replies keyed by method."""

    def __init__(self, script=None):
        self.script = script or {}
        self.writes = []
        self.killed = False
        self._rfd, self._wfd = os.pipe()
        self.stdout = os.fdopen(self._rfd, "rb", buffering=0)
        self.stdin = _Pipe(self)

    def _on_stdin(self, data):
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            self.writes.append(msg)
            method = msg.get("method")
            if method and "id" in msg and method in self.script:
                resp = dict(self.script[method])
                resp.setdefault("jsonrpc", "2.0")
                resp["id"] = msg["id"]
                self.push(resp)

    def push(self, obj):
        """A server->client line: response, request or notification."""
        os.write(self._wfd, json.dumps(obj).encode() + b"\n")

    def poll(self):
        return None if not self.killed else 0

    def kill(self):
        self.killed = True
        try:
            os.close(self._wfd)
        except OSError:
            pass

    def wait(self, timeout=None):
        return 0


_HANDSHAKE = {
    "initialize": {"result": {"serverInfo": {"name": "codex"}}},
    "thread/resume": {"result": {"thread": {"id": "thr-1"}}},
    "turn/start": {"result": {"turn": {"id": "turn-9", "status": "ok"}}},
    "turn/interrupt": {"result": {}},
}


class TestCodexAppServer(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        inject.CodexAppServerInjector._servers.clear()
        self.procs = []

        def spawn(cwd):
            proc = _FakeAppProc(dict(_HANDSHAKE))
            self.procs.append(proc)
            return proc

        self._patch = patch.object(
            inject.CodexAppServerInjector, "_spawn",
            staticmethod(spawn),
        )
        self._patch.start()
        # `thread/resume` needs a uuid inside the session id, same as
        # `codex resume <uuid>` — the parser lifts it out of the stem.
        self.session = _session(
            tool="codex",
            sid="rollout-2026-10-22T01-00-00-"
                "123e4567-e89b-42d3-a456-426614174000",
            cwd="/tmp/proj",
        )

    def tearDown(self):
        self._patch.stop()
        for key, server in list(inject.CodexAppServerInjector._servers.items()):
            try:
                server.proc.kill()
            except Exception:
                pass
        inject.CodexAppServerInjector._servers.clear()

    def _injector(self, emit=None):
        return inject.CodexAppServerInjector(
            self.home, "codex:k", emit=emit, machine="m1"
        )

    def test_injector_for_dispatch(self):
        inj = inject.injector_for(
            self.home, "k", _meta("appserver", "codex"))
        self.assertIsInstance(inj, inject.CodexAppServerInjector)
        inj2 = inject.injector_for(
            self.home, "k", _meta("appserver", "claude"))
        self.assertIsInstance(inj2, inject.TmuxInjector)

    def test_send_handshakes_then_turn_start(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        res = self._injector().send(
            self.session, {"text": "hello", "mode": "queue"}, "idle")
        self.assertTrue(res.ok, res.note)
        methods = [w["method"] for w in self.procs[0].writes]
        self.assertEqual(
            methods[:4],
            ["initialize", "initialized", "thread/resume", "turn/start"],
        )
        start = self.procs[0].writes[-1]
        self.assertEqual(
            start["params"]["input"],
            [{"type": "text", "text": "hello"}],
        )

    def test_send_queue_refuses_when_active(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        res = self._injector().send(
            self.session, {"text": "x", "mode": "queue"}, "active")
        self.assertFalse(res.ok)
        self.assertIn("mid-turn", res.note)

    def test_send_detached_refuses(self):
        res = self._injector().send(
            self.session, {"text": "x", "mode": "steer"}, "idle")
        self.assertFalse(res.ok)

    def test_send_no_thread_id(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        bad = _session(tool="codex", sid="rollout-no-uuid-here")
        res = self._injector().send(
            bad, {"text": "x", "mode": "steer"}, "idle")
        self.assertFalse(res.ok)

    def test_server_approval_request_surfaces_and_answers(self):
        events = []
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        inj = self._injector(emit=lambda k, ev: events.append(ev))
        res = inj.send(self.session, {"text": "go", "mode": "steer"}, "idle")
        self.assertTrue(res.ok)
        proc = self.procs[0]
        # Server initiates an approval request mid-turn.
        proc.push({
            "jsonrpc": "2.0", "id": 777,
            "method": "item/commandExecution/requestApproval",
            "params": {"reason": "wants to run rm -rf", "command": "rm -rf"},
        })
        for _ in range(100):
            if events:
                break
            import time as _t
            _t.sleep(0.05)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["type"], "approval.requested")
        self.assertEqual(ev["data"]["request_id"], "777")
        self.assertIn("rm -rf", ev["data"]["prompt"])

        res = inj.approval(self.session, "777", "always")
        self.assertTrue(res.ok, res.note)
        reply = self.procs[0].writes[-1]
        self.assertEqual(reply["id"], 777)
        self.assertEqual(reply["result"], {"decision": "acceptForSession"})
        # A replayed request_id is refused.
        res = inj.approval(self.session, "777", "allow")
        self.assertFalse(res.ok)

    def test_approval_unknown_request(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        inj = self._injector()
        self.assertTrue(
            inj.send(self.session, {"text": "x", "mode": "steer"},
                     "idle").ok)
        res = inj.approval(self.session, "999", "allow")
        self.assertFalse(res.ok)

    def test_interrupt_sends_turn_interrupt(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        inj = self._injector()
        self.assertTrue(
            inj.send(self.session, {"text": "x", "mode": "steer"},
                     "idle").ok)
        res = inj.control(self.session, "interrupt", "")
        self.assertTrue(res.ok)
        last = self.procs[0].writes[-1]
        self.assertEqual(last["method"], "turn/interrupt")
        self.assertEqual(last["params"]["turnId"], "turn-9")

    def test_detach_kills_and_unmarks(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        inj = self._injector()
        self.assertTrue(
            inj.send(self.session, {"text": "x", "mode": "steer"},
                     "idle").ok)
        res = inj.control(self.session, "detach", "")
        self.assertTrue(res.ok)
        self.assertIsNone(supervise.meta_for(self.home, "codex:k"))
        self.assertTrue(self.procs[0].killed)
        self.assertEqual(inject.CodexAppServerInjector._servers, {})

    def test_unsupported_control(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        res = self._injector().control(self.session, "compact", "")
        self.assertFalse(res.ok)
        self.assertIn("unsupported", res.note)


# -- mode resolution + attach --------------------------------------------


class TestAttachModes(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_resolve_mode_matrix(self):
        oc = _session(tool="opencode")
        cx = _session(tool="codex", sid="x")
        cl = _session(tool="claude", sid="x")
        with patch("shutil.which", return_value="/bin/x"):
            self.assertEqual(supervise.resolve_mode(oc, "auto"), "serve")
            self.assertEqual(supervise.resolve_mode(cx, "auto"), "appserver")
            self.assertEqual(supervise.resolve_mode(cl, "auto"), "tmux")
            self.assertEqual(supervise.resolve_mode(oc, "tmux"), "tmux")
        with patch("shutil.which", return_value=None):
            self.assertEqual(supervise.resolve_mode(oc, "auto"), "tmux")
            self.assertEqual(supervise.resolve_mode(cx, "auto"), "tmux")

    def test_attach_serve_mode_marks_registry(self):
        session = _session(tool="opencode")
        with patch("shutil.which", return_value="/bin/opencode"), \
                patch.object(supervise, "_stop_local_writers"):
            ok, note = supervise.attach(
                self.home, "opencode:s1", session,
                last_append_at=None, status="idle", mode="serve",
            )
        self.assertTrue(ok, note)
        meta = supervise.meta_for(self.home, "opencode:s1")
        self.assertEqual(meta["mode"], "serve")
        self.assertNotIn("tmux", meta)

    def test_attach_appserver_mode(self):
        session = _session(
            tool="codex",
            sid="rollout-2026-10-22T01-00-00-"
                "123e4567-e89b-42d3-a456-426614174000",
        )
        with patch("shutil.which", return_value="/bin/codex"), \
                patch.object(supervise, "_stop_local_writers"):
            ok, note = supervise.attach(
                self.home, "codex:x", session,
                last_append_at=None, status="idle", mode="appserver",
            )
        self.assertTrue(ok, note)
        self.assertEqual(
            supervise.meta_for(self.home, "codex:x")["mode"], "appserver")

    def test_attach_auto_picks_api_when_binary_exists(self):
        session = _session(tool="opencode")
        with patch("shutil.which", return_value="/bin/opencode"), \
                patch.object(supervise, "_stop_local_writers"):
            ok, note = supervise.attach(
                self.home, "opencode:s1", session,
                last_append_at=None, status="idle", mode="auto",
            )
        self.assertTrue(ok, note)
        self.assertEqual(
            supervise.meta_for(self.home, "opencode:s1")["mode"], "serve")

    def test_attach_serve_rejects_wrong_harness(self):
        session = _session(tool="claude", sid="x")
        ok, note = supervise.attach(
            self.home, "claude:x", session,
            last_append_at=None, status="idle", mode="serve",
        )
        self.assertFalse(ok)
        self.assertIn("opencode", note)

    def test_attach_serve_requires_binary(self):
        session = _session(tool="opencode")
        with patch("shutil.which", return_value=None):
            ok, note = supervise.attach(
                self.home, "opencode:s1", session,
                last_append_at=None, status="idle", mode="serve",
            )
        self.assertFalse(ok)
        self.assertIn("not installed", note)

    def test_supervised_keeps_api_modes_alive(self):
        # non-tmux rows are never pruned by the tmux liveness check.
        supervise.mark(self.home, "k", _meta("serve", "opencode"))
        self.assertIn("k", supervise.supervised(self.home))


class TestInjectResultDegraded(unittest.TestCase):
    def test_headless_steer_marks_degraded(self):
        home = Path(tempfile.mkdtemp())
        supervise.mark(home, "k", _meta("headless", "claude"))
        inj = inject.ClaudeHeadlessInjector(home, "k")
        session = _session(tool="claude", sid="s1")
        import io
        proc = MagicMock()
        proc.stdout = io.BytesIO(
            b'{"type":"result","result":"done"}\n')
        proc.wait.return_value = 0
        with patch.object(inject.subprocess, "Popen", return_value=proc):
            res = inj.send(
                session, {"text": "x", "mode": "steer"}, "idle")
        self.assertTrue(res.ok)
        self.assertTrue(res.degraded)
        self.assertIn("steer queued", res.note)

    def test_tmux_steer_not_degraded(self):
        home = Path(tempfile.mkdtemp())
        supervise.mark(home, "k", _meta("tmux", "claude"))
        inj = inject.TmuxInjector(home, "k")
        session = _session(tool="claude", sid="s1")
        with patch.object(supervise, "send_text", return_value=True):
            res = inj.send(
                session, {"text": "x", "mode": "steer"}, "active")
        self.assertTrue(res.ok)
        self.assertFalse(res.degraded)


if __name__ == "__main__":
    unittest.main()
