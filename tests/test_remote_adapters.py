"""Tier-A adapter tests: opencode serve + codex app-server injectors.

No real harness binaries — ``_spawn`` is patched with scripted fakes
(os.pipe for the app-server's stdio so the real reader thread runs).
"""

import io
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
        # opencode's API never expands an @ ref: the path goes in plain.
        self.assertEqual(sent, "[Attached file: /tmp/a.png] see this")

    def test_send_rides_the_attach_pick_on_wire_fields(self):
        session = _session()
        meta = {**_meta("serve", "opencode"),
                "model": "acme/kimi-k3", "effort": "high"}
        supervise.mark(self.home, "opencode:s1", meta)
        res = self._injector().send(
            session, {"text": "hi", "mode": "queue"}, "idle")
        self.assertTrue(res.ok)
        payload = self.calls[-1][2]
        self.assertEqual(payload["model"],
                         {"providerID": "acme", "modelID": "kimi-k3"})
        self.assertEqual(payload["variant"], "high")

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


    def test_release_after_respawn_keeps_the_new_serve(self):
        # A and B share serve 1; it dies and B's next send spawns
        # serve 2. A never subscribed to serve 2, so A detaching must
        # not drop B's hold and kill it.
        procs = [_FakeServeProc(), _FakeServeProc()]
        spawned = iter(procs)
        supervise.mark(self.home, "k1", _meta("serve", "opencode"))
        supervise.mark(self.home, "k2", _meta("serve", "opencode"))
        a, b = _session(sid="ses_a"), _session(sid="ses_b")
        inj_a = inject.OpencodeServeInjector(self.home, "k1")
        inj_b = inject.OpencodeServeInjector(self.home, "k2")
        with patch.object(inject.OpencodeServeInjector, "_spawn",
                          staticmethod(lambda *a_, **k: next(spawned))):
            inj_a.send(a, {"text": "a", "mode": "queue"}, "idle")
            inj_b.send(b, {"text": "b", "mode": "queue"}, "idle")
            procs[0].killed = True  # serve 1 exits on its own
            inj_b.send(b, {"text": "b2", "mode": "queue"}, "idle")
        inj_a._release(a)
        self.assertFalse(procs[1].killed)
        serve = next(iter(inject.OpencodeServeInjector._serves.values()))
        self.assertIs(serve.proc, procs[1])
        self.assertEqual(serve.refs, 1)
        inj_b._release(b)
        self.assertTrue(procs[1].killed)

    def test_ready_probe_after_another_waiter_gave_up_is_refused(self):
        # Waiter B times out and kills the proc while waiter A's probe
        # is in flight; A must not subscribe to (and send through) it.
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))

        def probe(base, method, path, payload, timeout=20.0):
            serves = inject.OpencodeServeInjector._serves
            for cwd, serve in list(serves.items()):
                serve.dead = True  # B's cleanup lands mid-probe
                serves.pop(cwd, None)
            return (200, b"[]")

        with patch.object(inject, "_serve_call", probe):
            res = self._injector().send(
                _session(), {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)

    def test_serve_exiting_before_ready_is_unregistered(self):
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        self.proc.killed = True  # poll() reports it exited
        res = self._injector().send(
            _session(), {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertEqual(inject.OpencodeServeInjector._serves, {})

    def test_timed_out_waiter_leaves_a_held_serve_alone(self):
        # Another session already holds the proc: a waiter that gives
        # up must not kill it under them.
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        held = inject._ServeProc(self.proc, "http://127.0.0.1:1")
        held.refs = 1
        held.reader_started = True
        cwd = os.path.realpath("/tmp/proj")
        inject.OpencodeServeInjector._serves[cwd] = held
        self.routes = {"session": (0, b"")}
        with patch.object(inject, "_SERVE_READY_S", 0.3):
            res = self._injector().send(
                _session(), {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertFalse(self.proc.killed)
        self.assertIs(inject.OpencodeServeInjector._serves[cwd], held)

    def test_kill_failure_on_timeout_still_unregisters(self):
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        self.routes = {"session": (0, b"")}
        with patch.object(inject, "_SERVE_READY_S", 0.3), \
             patch.object(self.proc, "kill", side_effect=OSError("gone")):
            res = self._injector().send(
                _session(), {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertEqual(inject.OpencodeServeInjector._serves, {})

    def test_serve_that_never_gets_ready_is_killed(self):
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        self.routes = {"session": (0, b"")}  # never answers
        with patch.object(inject, "_SERVE_READY_S", 0.3):
            res = self._injector().send(
                _session(), {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertTrue(self.proc.killed)
        self.assertEqual(inject.OpencodeServeInjector._serves, {})

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

    def test_send_rides_the_attach_pick_as_turn_overrides(self):
        meta = {**_meta("appserver", "codex"),
                "model": "gpt-5", "effort": "high"}
        supervise.mark(self.home, "codex:k", meta)
        res = self._injector().send(
            self.session, {"text": "go", "mode": "queue"}, "idle")
        self.assertTrue(res.ok, res.note)
        start = self.procs[0].writes[-1]
        self.assertEqual(start["method"], "turn/start")
        self.assertEqual(start["params"]["model"], "gpt-5")
        self.assertEqual(start["params"]["effort"], "high")

    def test_model_switch_rides_the_next_turn_start(self):
        supervise.mark(self.home, "codex:k",
                       {**_meta("appserver", "codex"), "model": "gpt-5"})
        inj = self._injector()
        res = inj.control(self.session, "model", "gpt-5.1-codex")
        self.assertTrue(res.ok, res.note)
        self.assertIn("next turn", res.note)
        # nothing is sent now: no app-server was even spawned
        self.assertEqual(self.procs, [])
        self.assertEqual(
            supervise.meta_for(self.home, "codex:k")["model"],
            "gpt-5.1-codex")
        res = inj.send(self.session, {"text": "go", "mode": "queue"}, "idle")
        self.assertTrue(res.ok, res.note)
        start = self.procs[0].writes[-1]
        self.assertEqual(start["method"], "turn/start")
        self.assertEqual(start["params"]["model"], "gpt-5.1-codex")

    def test_model_switch_refusals(self):
        inj = self._injector()
        # detached: nothing to record it on
        res = inj.control(self.session, "model", "gpt-5")
        self.assertFalse(res.ok)
        self.assertIn("detached", res.note)
        self.assertIsNone(supervise.meta_for(self.home, "codex:k"))
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        res = inj.control(self.session, "model", "")
        self.assertFalse(res.ok)
        self.assertIn("needs a value", res.note)
        # the launch rule holds here too, not only at the route
        res = inj.control(self.session, "model", "gpt-5\n/clear")
        self.assertFalse(res.ok)
        self.assertNotIn("model", supervise.meta_for(self.home, "codex:k"))

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


class TestOpencodeServeEdges(unittest.TestCase):
    """Error/lifecycle branches of the opencode serve adapter."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        inject.OpencodeServeInjector._serves.clear()

    def tearDown(self):
        inject.OpencodeServeInjector._serves.clear()

    def _injector(self):
        return inject.OpencodeServeInjector(self.home, "opencode:s1")

    def test_serve_call_error_paths(self):
        import urllib.error
        err = urllib.error.HTTPError("http://x", 409, "c", {}, io.BytesIO(b"no"))
        with patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(inject._serve_call("http://x", "GET", "/p", None),
                             (409, b"no"))
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertEqual(inject._serve_call("http://x", "GET", "/p", None),
                             (0, b""))
        # A 200 with a body passes through.
        ok = MagicMock()
        ok.status = 200
        ok.read.return_value = b"{}"
        ok.__enter__.return_value = ok
        with patch("urllib.request.urlopen", return_value=ok):
            self.assertEqual(inject._serve_call("http://x", "GET", "/p", {}),
                             (200, b"{}"))
        # HTTPError whose error body itself can't be read → code + b"".
        fp = MagicMock()
        fp.read.side_effect = OSError("gone")
        err2 = urllib.error.HTTPError("http://x", 500, "c", {}, fp)
        with patch("urllib.request.urlopen", side_effect=err2):
            self.assertEqual(inject._serve_call("http://x", "GET", "/p", None),
                             (500, b""))

    def test_event_loop_urlopen_failure_returns(self):
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            # must return quietly, not raise
            inject._serve_event_loop(
                inject._ServeProc(_FakeServeProc(), "http://x"))

    def test_event_loop_read_error_swallowed(self):
        class BadResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                raise OSError("reset")

        with patch("urllib.request.urlopen", return_value=BadResp()):
            inject._serve_event_loop(
                inject._ServeProc(_FakeServeProc(), "http://x"))

    def test_event_loop_parses_sse_frames(self):
        events = []
        serve = inject._ServeProc(_FakeServeProc(), "http://x")
        serve.subs["ses_1"] = ("k", lambda k, ev: events.append(ev), "m")
        blob = json.dumps({
            "type": "permission.asked",
            "properties": {"id": "p1", "sessionID": "ses_1",
                           "title": "run it"},
        }).encode()
        resp = io.BytesIO(b"data: " + blob + b"\n\n")
        ctx = MagicMock()
        ctx.__enter__.return_value = resp
        ctx.__exit__.return_value = False
        ctx.__iter__.return_value = iter(resp.readlines())
        with patch("urllib.request.urlopen", return_value=ctx):
            inject._serve_event_loop(serve)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["request_id"], "p1")

    def test_serve_event_garbage_and_missing_bits(self):
        serve = inject._ServeProc(_FakeServeProc(), "http://x")
        serve.subs["ses_1"] = ("k", None, "m")  # emit=None path
        inject._serve_event(serve, b"not json")
        inject._serve_event(serve, b"[1,2]")
        inject._serve_event(serve, json.dumps({"type": "x"}).encode())
        # known session but emit=None returns quietly — nothing tracks
        inject._serve_event(serve, json.dumps({
            "type": "permission.asked",
            "properties": {"id": "p1", "sessionID": "ses_1"}}).encode())
        self.assertNotIn("p1", serve.permissions)

    def test_serve_event_asked_without_id(self):
        events = []
        serve = inject._ServeProc(_FakeServeProc(), "http://x")
        serve.subs["ses_1"] = ("k", lambda k, ev: events.append(ev), "m")
        inject._serve_event(serve, json.dumps({
            "type": "permission.asked",
            "properties": {"sessionID": "ses_1"}}).encode())
        self.assertEqual(events, [])
        # a resolved event pops the tracked permission id
        serve.permissions["p9"] = "ses_1"
        inject._serve_event(serve, json.dumps({
            "type": "permission.replied",
            "properties": {"id": "p9", "sessionID": "ses_1"}}).encode())
        self.assertNotIn("p9", serve.permissions)
        self.assertEqual(events[0]["data"]["request_id"], "p9")

    def test_serve_for_evicts_dead_and_spawn_errors(self):
        cwd = os.path.realpath("/tmp/proj")
        dead = _FakeServeProc()
        dead.killed = True
        inject.OpencodeServeInjector._serves[cwd] = (
            inject._ServeProc(dead, "http://x"))

        def spawn_fail(*a, **k):
            raise OSError("no opencode")

        inj = self._injector()
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(spawn_fail),
        ):
            self.assertIsNone(inj._serve_for(_session()))

    def test_serve_for_proc_dies_during_readiness(self):
        proc = _FakeServeProc()
        inj = self._injector()

        calls = {"n": 0}

        def kill_then_serve(base, method, path, payload, timeout=20.0):
            calls["n"] += 1
            if calls["n"] == 1:
                proc.killed = True   # dies before it ever answers
            return (0, b"")

        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: proc),
        ), patch.object(
            inject, "_serve_call", kill_then_serve
        ), patch.object(inject, "_SERVE_READY_S", 0.6):
            self.assertIsNone(inj._serve_for(_session()))

    def test_serve_for_readiness_timeout(self):
        proc = _FakeServeProc()
        inj = self._injector()
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: proc),
        ), patch.object(
            inject, "_serve_call", lambda *a, **k: (0, b"")
        ), patch.object(inject, "_SERVE_READY_S", 0.3):
            self.assertIsNone(inj._serve_for(_session()))

    def test_release_without_and_with_refs(self):
        cwd = os.path.realpath("/tmp/proj")
        inj = self._injector()
        inj._release(_session())  # no serve → quiet no-op
        proc = _FakeServeProc()
        serve = inject._ServeProc(proc, "http://x")
        serve.refs = 2
        serve.subs["ses_a"] = ("k1", None, "m")
        serve.subs["ses_b"] = ("k2", None, "m")
        inject.OpencodeServeInjector._serves[cwd] = serve
        inj._release(_session(sid="ses_a"))   # 2→1: no kill
        self.assertFalse(proc.killed)
        inj._release(_session(sid="ses_b"))   # 1→0: kill + evict
        self.assertTrue(proc.killed)
        self.assertEqual(inject.OpencodeServeInjector._serves, {})

    def test_release_kill_failure_swallowed(self):
        cwd = os.path.realpath("/tmp/proj")

        class BadKill(_FakeServeProc):
            def kill(self):
                raise OSError("already dead")

        serve = inject._ServeProc(BadKill(), "http://x")
        serve.refs = 1
        serve.subs["ses_1"] = ("k", None, "m")
        inject.OpencodeServeInjector._serves[cwd] = serve
        self._injector()._release(_session())
        self.assertEqual(inject.OpencodeServeInjector._serves, {})

    def test_send_empty_and_refused(self):
        session = _session()
        res = self._injector().send(
            session, {"text": "  ", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("empty", res.note)
        # supervised but serve spawn fails → refused
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: (_ for _ in ()).throw(OSError())),
        ):
            res = self._injector().send(
                session, {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("failed to start", res.note)

    def test_send_prompt_refused_status(self):
        session = _session()
        supervise.mark(self.home, "opencode:s1", _meta("serve", "opencode"))
        calls = []

        def fake(base, method, path, payload, timeout=20.0):
            calls.append(path)
            if "prompt_async" in path:
                return (429, b"busy")
            return (200, b"[]")   # /session readiness probe

        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: _FakeServeProc()),
        ), patch.object(inject, "_serve_call", fake):
            res = self._injector().send(
                session, {"text": "x", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("429", res.note)

    def test_approval_serve_fail_and_legacy_fail(self):
        session = _session()
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: (_ for _ in ()).throw(OSError())),
        ):
            res = self._injector().approval(session, "p1", "allow")
        self.assertFalse(res.ok)
        # serve up but both endpoints reject
        def fake(base, method, path, payload, timeout=20.0):
            if "/session" in path and "permissions" not in path:
                return (200, b"[]")
            return (500, b"")
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: _FakeServeProc()),
        ), patch.object(inject, "_serve_call", fake):
            res = self._injector().approval(session, "p1", "allow")
        self.assertFalse(res.ok)
        self.assertIn("500", res.note)

    def test_control_paths(self):
        session = _session()
        inj = self._injector()
        # interrupt with no serve proc → spawn fails → refused
        def spawn_fail(*a, **k):
            raise OSError("no opencode")
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(spawn_fail),
        ):
            res = inj.control(session, "interrupt", "")
        self.assertFalse(res.ok)
        # serve up but the abort call itself is refused
        def fake(base, method, path, payload, timeout=20.0):
            if "/session" in path and "abort" not in path:
                return (200, b"[]")
            return (500, b"")
        with patch.object(
            inject.OpencodeServeInjector, "_spawn",
            staticmethod(lambda *a, **k: _FakeServeProc()),
        ), patch.object(inject, "_serve_call", fake):
            res = inj.control(session, "interrupt", "")
        self.assertFalse(res.ok)
        self.assertIn("refused abort", res.note)
        # unsupported control op
        res = inj.control(session, "compact", "")
        self.assertFalse(res.ok)
        self.assertIn("unsupported", res.note)


class TestCodexAppServerEdges(unittest.TestCase):
    """Error/handshake branches of the codex app-server adapter."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        inject.CodexAppServerInjector._servers.clear()
        self.session = _session(
            tool="codex",
            sid="rollout-2026-10-22T01-00-00-"
                "123e4567-e89b-42d3-a456-426614174000",
        )

    def tearDown(self):
        inject.CodexAppServerInjector._servers.clear()

    def _injector(self, emit=None):
        return inject.CodexAppServerInjector(
            self.home, "codex:k", emit=emit, machine="m1")

    def _spawn_patch(self, proc):
        return patch.object(
            inject.CodexAppServerInjector, "_spawn",
            staticmethod(lambda *a, **k: proc))

    def test_rpc_line_broken_pipe(self):
        proc = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError()
        self.assertFalse(inject._rpc_line(proc, {"a": 1}))

    def test_read_loop_demuxes_and_skips_garbage(self):
        proc = _FakeAppProc()
        server = inject._AppServer(proc, "thr")
        import queue as qmod
        reply_q = qmod.Queue()
        server.replies[5] = reply_q
        # garbage, non-dict, response, notification, approval without emit
        proc.stdout = io.BytesIO(
            b"garbage\n[1,2]\n"
            + json.dumps({"id": 5, "result": {"ok": 1}}).encode() + b"\n"
            + json.dumps({"method": "turn/started",
                          "params": {"turn": {"id": "t-7"}}}).encode() + b"\n"
            + json.dumps({"method": "turn/completed"}).encode() + b"\n"
            + json.dumps({"id": 42, "method": "x/requestApproval",
                          "params": {"reason": "run?"}}).encode() + b"\n")
        inject._appserver_read_loop(server, "k", None, "m1")
        self.assertEqual(reply_q.get_nowait()["id"], 5)
        self.assertEqual(server.turn_id, "")   # completed cleared it
        self.assertIn("42", server.approvals)  # tracked even without emit
        self.assertTrue(server.dead)           # EOF → marked dead

    def test_read_loop_stdout_error_marks_dead(self):
        class BadOut:
            def __iter__(self):
                raise OSError("reset")

        proc = _FakeAppProc()
        proc.stdout = BadOut()
        server = inject._AppServer(proc, "thr")
        inject._appserver_read_loop(server, "k", None, "m1")
        self.assertTrue(server.dead)

    def test_server_for_evicts_dead_server(self):
        dead = _FakeAppProc()
        dead.killed = True
        inject.CodexAppServerInjector._servers["codex:k"] = (
            inject._AppServer(dead, "old-thread"))
        proc = _FakeAppProc({
            "initialize": {"result": {}},
            "thread/resume": {"result": {"thread": {"id": "t-new"}}},
        })
        with self._spawn_patch(proc):
            server = self._injector()._server_for(self.session)
        self.assertIsNotNone(server)
        self.assertIs(server.proc, proc)  # dead one evicted, fresh spawn
        self.assertIs(
            inject.CodexAppServerInjector._servers["codex:k"], server)

    def test_handshake_cleanup_kill_failure_swallowed(self):
        proc = _FakeAppProc({"initialize": {"error": {"message": "no"}}})
        proc.kill = lambda: (_ for _ in ()).throw(OSError("x"))
        with self._spawn_patch(proc):
            self.assertIsNone(self._injector()._server_for(self.session))

    def test_server_for_no_thread_id_and_spawn_error(self):
        inj = self._injector()
        bad = _session(tool="codex", sid="rollout-no-uuid")
        self.assertIsNone(inj._server_for(bad))

        def spawn_fail(*a, **k):
            raise OSError("no codex")

        with patch.object(
            inject.CodexAppServerInjector, "_spawn",
            staticmethod(spawn_fail)):
            self.assertIsNone(inj._server_for(self.session))

    def test_handshake_error_kills_server(self):
        proc = _FakeAppProc({"initialize": {"error": {"message": "no"}}})
        with self._spawn_patch(proc):
            inj = self._injector()
            self.assertIsNone(inj._server_for(self.session))
        self.assertTrue(proc.killed)
        self.assertEqual(inject.CodexAppServerInjector._servers, {})

    def test_resume_error_kills_server(self):
        proc = _FakeAppProc({
            "initialize": {"result": {}},
            "thread/resume": {"error": {"message": "gone"}},
        })
        with self._spawn_patch(proc):
            self.assertIsNone(self._injector()._server_for(self.session))
        self.assertTrue(proc.killed)

    def test_call_write_failure_and_timeout(self):
        proc = _FakeAppProc({})  # no scripted replies
        proc.stdin = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError()
        server = inject._AppServer(proc, "thr")
        self.assertIsNone(self._injector()._call(server, "m", {}))
        # reply never arrives → timeout path
        proc2 = _FakeAppProc({})
        server2 = inject._AppServer(proc2, "thr")
        self.assertIsNone(
            self._injector()._call(server2, "m", {}, timeout=0.05))
        self.assertEqual(server2.replies, {})

    def test_concurrent_caller_waits_for_ready(self):
        # A _server_for finding a live-but-mid-handshake server waits on
        # `ready` and returns None when the handshake failed.
        proc = _FakeAppProc()
        server = inject._AppServer(proc, "thr-x")
        server.ready.clear()          # another caller "spawned" it
        server.handshake_ok = False
        inject.CodexAppServerInjector._servers["codex:k"] = server
        # the spawner finishes its (failed) handshake slightly later
        threading.Timer(0.05, server.ready.set).start()
        self.assertIsNone(self._injector()._server_for(self.session))

    def test_send_empty_detached_and_bad_responses(self):
        inj = self._injector()
        res = inj.send(self.session, {"text": " ", "mode": "queue"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("empty", res.note)
        # detached
        res = inj.send(
            self.session, {"text": "x", "mode": "steer"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("detached", res.note)

    def test_send_timeout_and_error_reply(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        # proc with no turn/start reply → _call times out
        proc = _FakeAppProc({
            "initialize": {"result": {}},
            "thread/resume": {"result": {"thread": {"id": "t"}}},
            # no "turn/start" reply → the send call blocks then times out
        })
        with self._spawn_patch(proc):
            inj = self._injector()
            with patch.object(inject, "_APPSERVER_CALL_S", 0.1):
                res = inj.send(
                    self.session, {"text": "x", "mode": "steer"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("timed out", res.note)

    def test_send_error_reply(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        proc = _FakeAppProc({
            "initialize": {"result": {}},
            "thread/resume": {"result": {"thread": {"id": "t"}}},
            "turn/start": {"error": {"message": "sandbox denied"}},
        })
        with self._spawn_patch(proc):
            res = self._injector().send(
                self.session, {"text": "x", "mode": "steer"}, "idle")
        self.assertFalse(res.ok)
        self.assertIn("sandbox denied", res.note)

    def test_send_attachments_prefix(self):
        supervise.mark(self.home, "codex:k", _meta("appserver", "codex"))
        proc = _FakeAppProc({
            "initialize": {"result": {}},
            "thread/resume": {"result": {"thread": {"id": "t"}}},
            "turn/start": {"result": {"turn": {"id": "u1"}}},
        })
        with self._spawn_patch(proc):
            res = self._injector().send(
                self.session,
                {"text": "look", "mode": "steer",
                 "attachments": ["/tmp/a.png"]},
                "idle")
        self.assertTrue(res.ok)
        start_call = next(
            w for w in proc.writes if w.get("method") == "turn/start")
        self.assertEqual(start_call["params"]["input"][0]["text"],
                         "[Attached file: /tmp/a.png] look")

    def test_approval_edges(self):
        inj = self._injector()
        res = inj.approval(self.session, "1", "bogus")
        self.assertFalse(res.ok)
        self.assertIn("unknown decision", res.note)
        res = inj.approval(self.session, "1", "allow")
        self.assertFalse(res.ok)
        self.assertIn("not running", res.note)
        # dead server → same refusal
        proc = _FakeAppProc()
        proc.killed = True
        server = inject._AppServer(proc, "t")
        inject.CodexAppServerInjector._servers["codex:k"] = server
        res = inj.approval(self.session, "1", "allow")
        self.assertFalse(res.ok)

    def test_approval_write_failure_requeues(self):
        proc = _FakeAppProc()
        server = inject._AppServer(proc, "t")
        server.approvals["req-1"] = 77
        proc.stdin = MagicMock()
        proc.stdin.write.side_effect = BrokenPipeError()
        inject.CodexAppServerInjector._servers["codex:k"] = server
        res = self._injector().approval(self.session, "req-1", "allow")
        self.assertFalse(res.ok)
        self.assertIn("unreachable", res.note)
        # the request id went back so a retry can still answer
        self.assertEqual(server.approvals["req-1"], 77)

    def test_control_edges(self):
        inj = self._injector()
        res = inj.control(self.session, "interrupt", "")
        self.assertFalse(res.ok)
        self.assertIn("not running", res.note)
        # interrupt reply with error → fails
        proc = _FakeAppProc()
        server = inject._AppServer(proc, "t")
        inject.CodexAppServerInjector._servers["codex:k"] = server
        with patch.object(
            inj, "_call", return_value={"error": {"message": "x"}}
        ):
            res = inj.control(self.session, "interrupt", "")
        self.assertFalse(res.ok)
        self.assertIn("interrupt failed", res.note)
        # unsupported op
        res = inj.control(self.session, "compact", "")
        self.assertFalse(res.ok)

    def test_detach_kill_failure_swallowed(self):
        proc = _FakeAppProc()
        proc.kill = lambda: (_ for _ in ()).throw(OSError("x"))
        server = inject._AppServer(proc, "t")
        inject.CodexAppServerInjector._servers["codex:k"] = server
        res = self._injector().control(self.session, "detach", "")
        self.assertTrue(res.ok)
        self.assertNotIn(
            "codex:k", inject.CodexAppServerInjector._servers)


class TestTmuxApprovalGates(unittest.TestCase):
    def test_stale_request_id_refused(self):
        home = Path(tempfile.mkdtemp())
        supervise.mark(
            home, "k-stale", {"mode": "tmux", "tool_name": "claude"})
        supervise.set_pending_approval("k-stale", "req-new", "fp")
        inj = inject.TmuxInjector(home, "k-stale")
        res = inj.approval(_session(tool="claude"), "req-old", "allow")
        self.assertFalse(res.ok)
        self.assertIn("stale", res.note)


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
