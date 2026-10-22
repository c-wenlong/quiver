"""hub.py: auth, pairing route, REST endpoints, SSE stream."""

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import protocol
from quiver.remote import tail
from quiver.remote.hub import HubState, make_server
from quiver.remote import pairing
from quiver.sessions.models import Session


def _session(sid="s1", tool="claude", path="/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   title="test", session_id=sid, tool_name=tool)


class _Server:
    def __init__(self, sessions=None, statuses=None):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)
        self.state.catalog._fetch = lambda: list(sessions or [])
        self.state.catalog._status_of = (
            statuses or (lambda ss: ["active"] * len(ss))
        )
        self.state.catalog.refresh()
        self.state.tail_manager.sync(self.state.catalog.tail_rows())
        self.server = make_server(self.state, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path,
                         body=json.dumps(body) if body is not None else None,
                         headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()

    def pair(self):
        token = pairing.mint_pair_token(self.home)
        status, data = self.request(
            "POST", "/v1/pair", {"token": token, "name": "test"}
        )
        assert status == 200, data
        return json.loads(data)


class TestHub(_Server, unittest.TestCase):
    pass  # placeholder so _Server isn't collected as a test


class HubBase(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)

    def auth(self):
        cred = self.srv.pair()
        return {"Authorization": f"Bearer {cred['credential']}"}


class TestAuth(HubBase):
    def test_status_is_public(self):
        status, data = self.srv.request("GET", "/v1/status")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["ok"])

    def test_sessions_requires_auth(self):
        status, _ = self.srv.request("GET", "/v1/sessions")
        self.assertEqual(status, 401)
        status, data = self.srv.request(
            "GET", "/v1/sessions", headers=self.auth()
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(data)["sessions"]), 1)

    def test_pair_rejects_bad_token(self):
        status, _ = self.srv.request(
            "POST", "/v1/pair", {"token": "bogus"}
        )
        self.assertEqual(status, 403)

    def test_revoked_device_is_401(self):
        cred = self.srv.pair()
        pairing.revoke_device(self.srv.home, cred["device_id"])
        status, _ = self.srv.request(
            "GET", "/v1/sessions",
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 401)


class TestEndpoints(HubBase):
    def test_session_row_shape(self):
        _, data = self.srv.request("GET", "/v1/sessions", headers=self.auth())
        row = json.loads(data)["sessions"][0]
        self.assertEqual(row["key"], "claude:s1")
        self.assertEqual(row["machine_id"], self.srv.state.machine_id)
        self.assertEqual(row["ownership"], "observed")
        self.assertIn("resume", row)

    def test_write_paths_reject_observed(self):
        auth = self.auth()
        for route, body in (
            ("message", {"text": "hi"}),
            ("approval", {"request_id": "r", "decision": "allow"}),
            ("control", {"op": "interrupt"}),
        ):
            status, data = self.srv.request(
                "POST", f"/v1/sessions/claude:s1/{route}",
                body=body, headers=auth,
            )
            self.assertEqual(status, 409, (route, data))
            self.assertEqual(
                json.loads(data)["error"]["code"], "not_attached"
            )

    def test_unknown_session_404(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:nope/message",
            body={"text": "x"}, headers=self.auth(),
        )
        self.assertEqual(status, 404)

    def test_messages_backfill(self):
        key = "claude:s1"
        self.srv.state.emit(
            key, protocol.event(
                "message.appended", key, "mbp",
                protocol.message("assistant", "hello"),
            )
        )
        status, data = self.srv.request(
            "GET", f"/v1/sessions/claude:s1/messages",
            headers=self.auth(),
        )
        self.assertEqual(status, 200)
        msgs = json.loads(data)["messages"]
        self.assertEqual(msgs[0]["data"]["text"], "hello")

    def test_inbox(self):
        status, data = self.srv.request(
            "GET", "/v1/inbox", headers=self.auth()
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["items"], [])


class _AttachedServer(_Server):
    """Hub with one session already marked supervised (headless)."""

    def __init__(self, **kw):
        super().__init__(sessions=[_session()],
                         statuses=lambda ss: ["done"])
        from quiver.remote import supervise
        supervise.mark(self.home, "claude:s1", {
            "mode": "headless", "tool_name": "claude",
            "session_id": "s1", "cwd": "/tmp/proj", "attached_at": 0,
        })
        self.state.catalog.refresh()
        self.state.tail_manager.sync(self.state.catalog.tail_rows())


class TestHubWriteRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _AttachedServer()
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        row = self.srv.state.catalog.row_for("claude:s1")
        self.assertEqual(row["ownership"], "attached")

    def test_message_success(self):
        from quiver.remote import inject
        with patch.object(
            inject.ClaudeHeadlessInjector, "_run",
            return_value=(True, "turn complete"),
        ):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:s1/message",
                body={"text": "hi", "mode": "steer"}, headers=self.hdr,
            )
        self.assertEqual(status, 200, data)
        self.assertTrue(json.loads(data)["ok"])

    def test_message_bad_command_and_body(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:s1/message",
            body={"text": ""}, headers=self.hdr,
        )
        self.assertEqual(status, 422)
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request(
                "POST", "/v1/sessions/claude:s1/message",
                body="{not json", headers=self.hdr,
            )
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()

    def test_message_unknown_attachment(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude:s1/message",
            body={"text": "hi", "attachments": ["nope.png"]},
            headers=self.hdr,
        )
        self.assertEqual(status, 422)
        self.assertEqual(
            json.loads(data)["error"]["code"], "bad_attachment"
        )

    def test_message_send_failure_maps_409(self):
        from quiver.remote import inject
        with patch.object(
            inject.ClaudeHeadlessInjector, "_run",
            return_value=(False, "claude exploded"),
        ):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:s1/message",
                body={"text": "go"}, headers=self.hdr,
            )
        self.assertEqual(status, 409)
        self.assertIn("claude exploded", data.decode())

    def test_approval_unsupported_headless(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude:s1/approval",
            body={"request_id": "r1", "decision": "allow"},
            headers=self.hdr,
        )
        self.assertEqual(status, 409)
        self.assertEqual(
            json.loads(data)["error"]["code"], "approval_not_supported"
        )

    def test_control_ops(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude:s1/control",
            body={"op": "compact"}, headers=self.hdr,
        )
        # headless rejects TUI-only ops
        self.assertEqual(status, 409)
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude:s1/control",
            body={"op": "detach"}, headers=self.hdr,
        )
        self.assertEqual(status, 200, data)
        row = self.srv.state.catalog.row_for("claude:s1")
        self.assertEqual(row["ownership"], "observed")
        # now writes are gated again
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:s1/message",
            body={"text": "hi"}, headers=self.hdr,
        )
        self.assertEqual(status, 409)

    def test_attach_idempotent_and_modes(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude:s1/control",
            body={"op": "attach"}, headers=self.hdr,
        )
        self.assertEqual(status, 200)  # already attached

    def test_attach_not_attachable(self):
        # gemini has no resume flags → attachable False
        srv = _Server(
            sessions=[_session(tool="gemini", sid="g1")],
            statuses=lambda ss: ["done"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/gemini:g1/control",
            body={"op": "attach"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 422)
        self.assertEqual(
            json.loads(data)["error"]["code"], "not_attachable"
        )

    def test_attach_headless_bad_mode(self):
        srv = _Server(
            sessions=[_session(tool="codex", sid="c9")],
            statuses=lambda ss: ["done"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, _ = srv.request(
            "POST", "/v1/sessions/codex:c9/control",
            body={"op": "attach", "mode": "headless"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 422)

    def test_attach_headless_success(self):
        srv = _Server(
            sessions=[_session(sid="hx")], statuses=lambda ss: ["done"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/claude:hx/control",
            body={"op": "attach", "mode": "headless"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 200, data)

    def test_attach_unknown_mode_422(self):
        srv = _Server(
            sessions=[_session(sid="bm")], statuses=lambda ss: ["done"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/claude:bm/control",
            body={"op": "attach", "mode": "bogus"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(data)["error"]["code"], "bad_mode")

    def test_attach_serve_needs_opencode(self):
        srv = _Server(
            sessions=[_session(sid="sc")], statuses=lambda ss: ["done"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/claude:sc/control",
            body={"op": "attach", "mode": "serve"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 422)

    def test_attach_appserver_needs_codex(self):
        srv = _Server(
            sessions=[_session(sid="ac")], statuses=lambda ss: ["done"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/claude:ac/control",
            body={"op": "attach", "mode": "appserver"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 422)

    def test_attachment_staging(self):
        import http.client as hc
        conn = hc.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request(
                "POST",
                "/v1/sessions/claude:s1/attachments?name=note.txt",
                body=b"hello file",
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type": "application/octet-stream",
                },
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            self.assertEqual(resp.status, 200, payload)
            self.assertIn("ref", payload)
        finally:
            conn.close()
        # extension cap
        conn = hc.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request(
                "POST",
                "/v1/sessions/claude:s1/attachments?name=evil.exe",
                body=b"MZ",
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type": "application/octet-stream",
                },
            )
            resp = conn.getresponse()
            self.assertEqual(resp.status, 422)
        finally:
            conn.close()

    def test_attachment_multipart_upload(self):
        boundary = "TESTBOUNDARY"
        blob = (
            b"--TESTBOUNDARY\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="shot.png"\r\n'
            b"Content-Type: image/png\r\n\r\n"
            b"\x89PNG-DATA\r\n--TESTBOUNDARY--\r\n"
        )
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request(
                "POST", "/v1/sessions/claude:s1/attachments",
                body=blob,
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type":
                        f"multipart/form-data; boundary={boundary}",
                },
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            self.assertEqual(resp.status, 200, payload)
            self.assertTrue(payload["ref"].endswith("shot.png"))
        finally:
            conn.close()

    def test_multipart_keeps_trailing_dash_newline_bytes(self):
        # The old parser rstripped "\r\n-" from the tail, corrupting
        # content that legitimately ended in dash or newline bytes.
        from quiver.remote import attachments as att

        boundary = "KEEPBYTES"
        content = b"log tail ends with dash-\r\n"
        blob = (
            b"--KEEPBYTES\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="note.log"\r\n'
            b"Content-Type: text/plain\r\n\r\n" + content +
            b"\r\n--KEEPBYTES--\r\n"
        )
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request(
                "POST", "/v1/sessions/claude:s1/attachments",
                body=blob,
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type":
                        f"multipart/form-data; boundary={boundary}",
                },
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            self.assertEqual(resp.status, 200, payload)
        finally:
            conn.close()
        staged = att.resolve(
            self.srv.state.home, "claude:s1", payload["ref"]
        )
        self.assertIsNotNone(staged)
        self.assertEqual(Path(staged).read_bytes(), content)

    def test_multipart_boundary_with_extra_params(self):
        # `boundary=X; charset=utf-8` must extract only X — the old
        # split-on-"boundary=" swallowed the trailing params into the
        # marker and no part ever matched.
        boundary = "PARAMS123"
        blob = (
            b"--PARAMS123\r\n"
            b'Content-Disposition: form-data; name="file"; '
            b'filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
            b"hello\r\n--PARAMS123--\r\n"
        )
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request(
                "POST", "/v1/sessions/claude:s1/attachments",
                body=blob,
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type":
                        f'multipart/form-data; boundary={boundary}; '
                        'charset=utf-8',
                },
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            self.assertEqual(resp.status, 200, payload)
            self.assertTrue(payload["ref"].endswith("a.txt"))
        finally:
            conn.close()

    def test_inbox_item_shape(self):
        srv = _Server(
            sessions=[_session(sid="f1")],
            statuses=lambda ss: ["followup"],
        )
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "GET", "/v1/inbox",
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        items = json.loads(data)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "followup")
        self.assertEqual(items[0]["key"], "claude:f1")

    def test_inbox_shows_pending_approval_on_active(self):
        # A pane-only prompt leaves the transcript "active" — the inbox
        # must still surface it, and drop it once resolved.
        srv = _Server(sessions=[_session(sid="a1")],
                      statuses=lambda ss: ["active"])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        hdr = {"Authorization": f"Bearer {cred['credential']}"}
        srv.state.emit("claude:a1", protocol.event(
            "approval.requested", "claude:a1", "mbp",
            {"request_id": "req-1", "prompt": "run it?"}))
        status, data = srv.request("GET", "/v1/inbox", headers=hdr)
        items = json.loads(data)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "approval")
        self.assertEqual(items[0]["request_id"], "req-1")
        srv.state.emit("claude:a1", protocol.event(
            "approval.resolved", "claude:a1", "mbp",
            {"request_id": "req-1"}))
        status, data = srv.request("GET", "/v1/inbox", headers=hdr)
        self.assertEqual(json.loads(data)["items"], [])

    def test_advertise_url_never_wildcard(self):
        from quiver.remote import catalog, hub
        with patch.object(
            catalog, "tailscale_ip", return_value="100.64.1.2"
        ):
            self.assertEqual(
                hub._advertise_url("0.0.0.0", 8477),
                "http://100.64.1.2:8477")
        with patch.object(
            catalog, "tailscale_ip", return_value=""
        ), patch("socket.gethostname", return_value="srv.lan"):
            self.assertEqual(
                hub._advertise_url("0.0.0.0", 8477),
                "http://srv.lan:8477")
        self.assertEqual(
            hub._advertise_url("100.64.9.9", 9000),
            "http://100.64.9.9:9000")

    def test_machines_lists_self(self):
        self.srv.state._register_machine()
        status, data = self.srv.request(
            "GET", "/v1/machines", headers=self.hdr
        )
        machines = json.loads(data)["machines"]
        self.assertTrue(
            any(m["id"] == self.srv.state.machine_id for m in machines)
        )

    def test_messages_before_limit(self):
        for i in range(5):
            self.srv.state.emit(
                "claude:s1",
                protocol.event(
                    "message.appended", "claude:s1", "mbp",
                    protocol.message("assistant", f"m{i}"),
                ),
            )
        status, data = self.srv.request(
            "GET", "/v1/sessions/claude:s1/messages?limit=2",
            headers=self.hdr,
        )
        msgs = json.loads(data)["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[-1]["data"]["text"], "m4")
        status, data = self.srv.request(
            "GET", "/v1/sessions/claude:s1/messages",
            headers=self.hdr,
        )
        all_msgs = json.loads(data)["messages"]
        cutoff = all_msgs[2]["seq"]
        status, data = self.srv.request(
            "GET",
            f"/v1/sessions/claude:s1/messages?before={cutoff}",
            headers=self.hdr,
        )
        msgs = json.loads(data)["messages"]
        self.assertEqual(
            [m["data"]["text"] for m in msgs], ["m0", "m1"]
        )

    def test_events_unknown_key_404(self):
        status, _ = self.srv.request(
            "GET", "/v1/sessions/ghost/events", headers=self.hdr
        )
        self.assertEqual(status, 404)

    def test_new_session_launch(self):
        # launch_new is mocked: without it this route could spawn a real
        # claude process wherever tmux happens to be installed.
        with patch(
            "quiver.remote.supervise.launch_new",
            return_value=(True, "launched", "qv-t"),
        ) as launch:
            status, data = self.srv.request(
                "POST", "/v1/sessions",
                body={"harness": "claude", "cwd": "/tmp", "prompt": "hi"},
                headers=self.hdr,
            )
        self.assertEqual(status, 202, data)
        launch.assert_called_once()
        with patch(
            "quiver.remote.supervise.launch_new",
            return_value=(False, "tmux is not available", None),
        ):
            status, data = self.srv.request(
                "POST", "/v1/sessions",
                body={"harness": "claude", "cwd": "/tmp", "prompt": "hi"},
                headers=self.hdr,
            )
        self.assertEqual(status, 422, data)

    def test_static_assets(self):
        for path in ("/", "/app.js", "/styles.css",
                     "/manifest.webmanifest", "/icon.svg"):
            status, _ = self.srv.request("GET", path)
            self.assertEqual(status, 200, path)
        status, _ = self.srv.request("GET", "/nope.js")
        self.assertEqual(status, 404)

    def test_bad_body_400(self):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request(
                "POST", "/v1/sessions/claude:s1/control",
                body="{bad", headers=self.hdr,
            )
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()


class TestServeEntry(unittest.TestCase):
    def test_serve_keyboard_interrupt_returns_0(self):
        from quiver.remote import hub as hub_mod

        state = MagicMock()
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(hub_mod, "make_server", return_value=server):
            rc = hub_mod.serve(state, "127.0.0.1", 0)
        self.assertEqual(rc, 0)
        state.start.assert_called_once()
        state.stop.assert_called_once()
        server.server_close.assert_called_once()

    def test_state_start_stop(self):
        state = HubState(Path(tempfile.mkdtemp()))
        state.start()
        try:
            self.assertTrue(state._threads)
            self.assertTrue(all(t.is_alive() for t in state._threads))
        finally:
            state.stop()
        # stop() joins each thread with a 2s cap; a thread caught
        # mid-refresh under suite load may outlive it briefly.
        deadline = time.time() + 5
        while any(t.is_alive() for t in state._threads):
            if time.time() > deadline:
                self.fail("state.stop() left live threads")
            time.sleep(0.05)


class TestGlobalEvents(HubBase):
    def test_global_events_use_global_seq(self):
        # Live global subscribers must see the *global* journal seq on
        # mirrored events, so their ?last= cursor stays aligned with
        # replay. A direct _global emit first makes the counters differ.
        self.srv.state.emit(
            "_global",
            protocol.event("machine.online", "_global", "mbp", {}),
        )
        self.srv.state.emit(
            "claude:s1",
            protocol.event("message.appended", "claude:s1", "mbp",
                           protocol.message("assistant", "m")),
        )
        events, _ = self.srv.state.journal.read_since("_global", 0)
        # [session.status from fixture sync, machine.online, mirror]
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))  # strictly global-numbered
        mirrored = events[-1]
        self.assertEqual(mirrored["type"], "message.appended")
        self.assertEqual(
            mirrored["seq"], self.srv.state.journal.last_seq("_global")
        )
        # The session copy keeps its own numbering.
        sess_events, _ = self.srv.state.journal.read_since("claude:s1", 0)
        self.assertEqual(sess_events[0]["seq"], 1)
        self.assertNotEqual(mirrored["seq"], sess_events[0]["seq"])

    def test_global_stream_replays(self):
        self.srv.state.emit(
            "claude:s1",
            protocol.event(
                "session.status", "claude:s1", "mbp",
                {"status": "done"},
            ),
        )
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request("GET", "/v1/events?last=0", headers=self.auth())
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream",
                          resp.getheader("Content-Type"))
            buf = b""
            for _ in range(20):
                chunk = resp.readline()
                if not chunk:
                    break
                buf += chunk
                if b"journal.caught-up" in buf:
                    break
            self.assertIn(b"session.status", buf)
        finally:
            conn.close()


class TestSSE(HubBase):
    def test_replay_and_live(self):
        auth = self.auth()
        key = "claude:s1"
        self.srv.state.emit(
            key, protocol.event(
                "message.appended", key, "mbp",
                protocol.message("assistant", "replay me"),
            )
        )
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request("GET", f"/v1/sessions/{key}/events?last=0",
                         headers=auth)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            lines = {}
            got_live = False
            emitted = False
            for _ in range(200):
                line = resp.readline()
                if not line:
                    break
                line = line.decode()
                if line.startswith("event:"):
                    cur = line.split(":", 1)[1].strip()
                    lines[cur] = lines.get(cur, 0) + 1
                if "journal.caught-up" in line and not emitted:
                    # After replay marker, emit a live event.
                    emitted = True
                    self.srv.state.emit(
                        key, protocol.event(
                            "message.appended", key, "mbp",
                            protocol.message("assistant", "live"),
                        )
                    )
                if '"live"' in line:
                    got_live = True
                    break
            self.assertIn("message.appended", lines)
            self.assertTrue(got_live)
        finally:
            conn.close()

    def test_event_during_replay_is_not_duplicated(self):
        # subscribe() runs before read_since(), so an event emitted
        # while the replay is being read lands in BOTH places; the
        # stream must send it once (seq-deduped).
        key = "claude:s1"
        self.srv.state.emit(
            key, protocol.event(
                "message.appended", key, "mbp",
                protocol.message("assistant", "first"),
            )
        )
        real_read = self.srv.state.journal.read_since

        def emit_mid_replay(journal_key, after_seq, limit=None):
            self.srv.state.emit(
                key, protocol.event(
                    "message.appended", key, "mbp",
                    protocol.message("assistant", "midflight"),
                )
            )
            return real_read(journal_key, after_seq, limit)

        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            with patch.object(
                self.srv.state.journal, "read_since",
                side_effect=emit_mid_replay,
            ):
                conn.request(
                    "GET", f"/v1/sessions/{key}/events?last=0",
                    headers=self.auth(),
                )
                resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            buf = b""
            for _ in range(60):
                line = resp.readline()
                if not line:
                    break
                buf += line
                if b"journal.caught-up" in buf:
                    break
            self.assertEqual(buf.count(b"midflight"), 1)
            self.assertEqual(buf.count(b"first"), 1)
        finally:
            conn.close()

    def test_revoked_device_drops_stream(self):
        from quiver.remote import hub as hub_mod
        cred = self.srv.pair()
        auth = {"Authorization": f"Bearer {cred['credential']}"}
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            with patch.object(hub_mod, "_SSE_HEARTBEAT_S", 0.1), \
                    patch.object(hub_mod, "_REVOCATION_CHECK_S", 0.05):
                conn.request(
                    "GET", "/v1/events?last=0", headers=auth
                )
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                pairing.revoke_device(self.srv.home, cred["device_id"])
                # Stream must end once the revocation check runs —
                # readline() returns "" at EOF.
                deadline = time.time() + 5
                closed = False
                while time.time() < deadline:
                    if resp.readline() == b"":
                        closed = True
                        break
                self.assertTrue(closed, "stream stayed open after revoke")
        finally:
            conn.close()


class TestServerHardening(HubBase):
    def test_stalled_request_times_out_and_hub_survives(self):
        from quiver.remote import hub as hub_mod
        with patch.object(hub_mod, "_REQ_TIMEOUT_S", 0.5):
            sock = socket.create_connection(
                ("127.0.0.1", self.srv.port), timeout=5
            )
            try:
                # Headers declare a body that never arrives. Without a
                # socket deadline the handler thread would wait forever.
                sock.sendall(
                    b"POST /v1/pair HTTP/1.1\r\n"
                    b"Host: x\r\nContent-Length: 4096\r\n\r\n"
                    b"{\"token\":"
                )
                # The server must end the connection — 408, a close, or
                # an error response — well inside the patched timeout.
                sock.settimeout(4)
                try:
                    sock.recv(256)
                except socket.timeout:
                    self.fail("stalled request never timed out")
                except OSError:
                    pass  # reset counts as "ended" too
            finally:
                sock.close()
        status, _ = self.srv.request("GET", "/v1/status")
        self.assertEqual(status, 200)

    def test_worker_pool_is_bounded(self):
        from quiver.remote import hub as hub_mod
        self.assertIsInstance(self.srv.server, hub_mod._BoundedHTTPServer)
        # Exhaust every slot; the next connection is refused at the
        # socket instead of spawning an unbounded thread.
        slots = self.srv.server._slots
        for _ in range(hub_mod._MAX_WORKERS):
            self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", self.srv.port, timeout=3
            )
            try:
                with self.assertRaises((http.client.HTTPException, OSError)):
                    conn.request("GET", "/v1/status")
                    conn.getresponse().read()
            finally:
                conn.close()
        finally:
            for _ in range(hub_mod._MAX_WORKERS):
                slots.release()
        status, _ = self.srv.request("GET", "/v1/status")
        self.assertEqual(status, 200)

    def test_open_session_gets_on_demand_tailer(self):
        # Drop all tailers (as if the session were past the cap), then
        # open the messages route — a tailer must appear.
        self.srv.state.tail_manager.tailers.clear()
        self.srv.state.tail_manager.statuses.clear()
        self.assertNotIn("claude:s1", self.srv.state.tail_manager.tailers)
        with patch.object(tail, "make_source",
                          return_value=MagicMock()):
            status, _ = self.srv.request(
                "GET", "/v1/sessions/claude:s1/messages",
                headers=self.auth(),
            )
        self.assertEqual(status, 200)
        self.assertIn("claude:s1", self.srv.state.tail_manager.tailers)


class TestStartNotify(unittest.TestCase):
    def test_start_spawns_notifier_when_enabled(self):
        from quiver.remote import hub as hub_mod, notify

        home = Path(tempfile.mkdtemp())
        state = HubState(home)
        state.catalog._fetch = lambda: []
        cfg = {"enabled": True, "url": "https://ntfy.sh",
               "topic": "t", "ntfy_token": "x"}
        with patch.object(notify, "load_config", return_value=cfg):
            state.start()
            try:
                names = [t.name for t in state._threads]
                self.assertIn("remote-notify", names)
            finally:
                state.stop()

    def test_start_skips_notifier_when_disabled(self):
        from quiver.remote import notify

        home = Path(tempfile.mkdtemp())
        state = HubState(home)
        state.catalog._fetch = lambda: []
        with patch.object(notify, "load_config", return_value={}):
            state.start()
            try:
                names = [t.name for t in state._threads]
                self.assertNotIn("remote-notify", names)
            finally:
                state.stop()


if __name__ == "__main__":
    unittest.main()
