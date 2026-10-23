"""hub.py: auth, pairing route, REST endpoints, SSE stream."""

import http.client
import json
import os
import queue
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


def _session(sid="s1", tool="claude", path="/tmp/proj", ts=0):
    """``ts`` is seconds since the epoch; Session keeps milliseconds."""
    return Session(timestamp=int(ts * 1000), agent="Claude Code", path=path,
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
        q = self.srv.state.bus.subscribe("claude:s1")
        with patch.object(
            inject.ClaudeHeadlessInjector, "_run",
            return_value=(True, "turn complete"),
        ):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:s1/message",
                body={"text": "hi", "mode": "steer"}, headers=self.hdr,
            )
            self.assertEqual(status, 202, data)
            ev = q.get(timeout=5)
        self.assertTrue(json.loads(data)["ok"])
        self.assertEqual(ev["type"], "command.result")
        self.assertTrue(ev["data"]["ok"])

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

    def test_message_send_failure_lands_in_result(self):
        # The POST itself still answers 202; the injector's failure lands
        # as a command.result event on the session key.
        from quiver.remote import inject
        q = self.srv.state.bus.subscribe("claude:s1")
        with patch.object(
            inject.ClaudeHeadlessInjector, "_run",
            return_value=(False, "claude exploded"),
        ):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:s1/message",
                body={"text": "go"}, headers=self.hdr,
            )
            self.assertEqual(status, 202, data)
            ev = q.get(timeout=5)
        self.assertFalse(ev["data"]["ok"])
        self.assertIn("claude exploded", ev["data"]["note"])

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
        with patch("shutil.which", return_value="/x/claude"), \
             patch("quiver.remote.supervise._stop_local_writers"):
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
            sessions=[_session(sid="f1", ts=time.time())],
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

    def test_inbox_old_approval_never_cut_or_dismissed(self):
        # A pending approval blocks its agent, so neither the age cutoff
        # nor a dismissal may hide it.
        old = time.time() - 30 * 86_400
        srv = _Server(sessions=[_session(sid="a2", ts=old)],
                      statuses=lambda ss: ["followup"])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        hdr = {"Authorization": f"Bearer {cred['credential']}"}
        status, data = srv.request("GET", "/v1/inbox", headers=hdr)
        self.assertEqual(json.loads(data)["items"], [])
        srv.state.emit("claude:a2", protocol.event(
            "approval.requested", "claude:a2", "mbp",
            {"request_id": "req-2", "prompt": "run it?"}))
        srv.request("POST", "/v1/inbox/dismiss", headers=hdr, body={
            "key": "claude:a2", "last_active_at": old})
        status, data = srv.request("GET", "/v1/inbox", headers=hdr)
        items = json.loads(data)["items"]
        self.assertEqual([i["status"] for i in items], ["approval"])

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

    def _launch(self, body, installed=True):
        with patch("quiver.remote.catalog.launch_command",
                   return_value="claude" if installed else None), \
             patch("quiver.remote.supervise.tmux_bin",
                   return_value="/usr/bin/tmux"):
            status, data = self.srv.request(
                "POST", "/v1/sessions", body=body, headers=self.hdr)
        return status, json.loads(data)

    def _wait_launch_threads(self):
        for t in threading.enumerate():
            if t.name.startswith("remote-launch-"):
                t.join(timeout=5)

    def _launch_view(self, launch_id):
        status, data = self.srv.request(
            "GET", f"/v1/launches/{launch_id}", headers=self.hdr)
        return status, json.loads(data)

    def test_new_session_launch(self):
        # launch_new is mocked: without it this route could spawn a real
        # claude process wherever tmux happens to be installed.
        with patch(
            "quiver.remote.supervise.launch_new",
            return_value=(True, "launched", "qv-t"),
        ) as launch:
            status, body = self._launch(
                {"harness": "claude", "cwd": "/tmp", "prompt": "hi"})
            self._wait_launch_threads()
        self.assertEqual(status, 202, body)
        launch_id = body["launch_id"]
        self.assertEqual(body["state"], "pending")
        self.assertEqual(launch.call_args[1]["launch_id"], launch_id)
        self.assertEqual(launch.call_args[1]["binary"], "claude")
        status, view = self._launch_view(launch_id)
        self.assertEqual((status, view["state"]), (200, "pending"))
        # Adoption names the key; the launch reads started once the
        # catalog has that row (claude:s1 is in this server's catalog).
        self.srv.state.emit("_global", protocol.event(
            "session.launched", "claude:s1", "m", {"launch_id": launch_id}))
        _, view = self._launch_view(launch_id)
        self.assertEqual((view["state"], view["key"]), ("started", "claude:s1"))

    def test_launch_started_waits_for_catalog_row(self):
        with patch("quiver.remote.supervise.launch_new",
                   return_value=(True, "launched", "qv-t")):
            _, body = self._launch(
                {"harness": "claude", "cwd": "/tmp", "prompt": "hi"})
            self._wait_launch_threads()
        self.srv.state.emit("claude:new", protocol.event(
            "session.launched", "claude:new", "m",
            {"launch_id": body["launch_id"]}))
        _, view = self._launch_view(body["launch_id"])
        self.assertEqual(view["state"], "pending")

    def test_launch_failure_after_reply_is_remembered(self):
        # launch_new runs after the 202, so its failure lands on the
        # launch the phone is polling.
        with patch(
            "quiver.remote.supervise.launch_new",
            return_value=(False, "tmux launch failed", None),
        ):
            status, body = self._launch(
                {"harness": "claude", "cwd": "/tmp", "prompt": "hi"})
            self._wait_launch_threads()
        self.assertEqual(status, 202, body)
        _, view = self._launch_view(body["launch_id"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("tmux", view["note"])

    def test_missing_binary_is_422_before_launch(self):
        with patch("quiver.remote.supervise.launch_new") as launch:
            status, body = self._launch(
                {"harness": "claude", "cwd": "/tmp", "prompt": "hi"},
                installed=False)
        self.assertEqual((status, body["error"]["code"]),
                         (422, "not_launchable"))
        launch.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})

    def test_launch_validation(self):
        with patch("quiver.remote.supervise.launch_new") as launch:
            cases = [
                ({"harness": "Not A Name", "cwd": "/tmp", "prompt": "hi"},
                 "bad_command"),
                ({"harness": "claude", "cwd": "tmp", "prompt": "hi"},
                 "bad_command"),
                ({"harness": "claude", "cwd": "/tmp", "prompt": "  "},
                 "bad_command"),
                ({"harness": "claude", "cwd": "/no/such/dir", "prompt": "hi"},
                 "bad_cwd"),
            ]
            for req, code in cases:
                status, body = self._launch(req)
                self.assertEqual((status, body["error"]["code"]),
                                 (422, code), req)
        launch.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})

    def test_unknown_launch_404(self):
        status, _ = self._launch_view("lnope")
        self.assertEqual(status, 404)

    def test_launches_require_auth(self):
        status, _ = self.srv.request("GET", "/v1/launches/lx")
        self.assertEqual(status, 401)

    def test_old_launches_are_pruned(self):
        old = self.srv.state.new_launch("m", "claude", "/tmp")
        self.srv.state.launches[old]["created_at"] -= 2 * 86400
        self.srv.state.new_launch("m", "claude", "/tmp")
        self.assertNotIn(old, self.srv.state.launches)

    def test_adoption_leaves_the_sessions_journal_empty(self):
        # The tailer skips history when the session already has a
        # journal, so session.launched must not land there first.
        lid = self.srv.state.new_launch("m", "claude", "/tmp")
        with patch("quiver.remote.supervise.adopt_pending",
                   return_value=["claude:new"]), \
             patch("quiver.remote.supervise.meta_for",
                   return_value={"launch_id": lid}):
            self.srv.state._adopt_launches()
        self.assertEqual(self.srv.state.journal.last_seq("claude:new"), 0)
        self.assertEqual(self.srv.state.launches[lid]["key"], "claude:new")

    def test_folders_lists_subfolders_only(self):
        root = Path(tempfile.mkdtemp())
        for name in ("beta", "Alpha", ".hidden"):
            (root / name).mkdir()
        (root / "notes.txt").write_text("x")
        status, data = self.srv.request(
            "GET", f"/v1/folders?path={root}", headers=self.hdr)
        body = json.loads(data)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["path"], os.path.realpath(root))
        self.assertEqual([f["name"] for f in body["folders"]],
                         ["Alpha", "beta"])
        self.assertEqual(body["parent"],
                         os.path.dirname(os.path.realpath(root)))
        self.assertFalse(body["truncated"])

    def test_folders_defaults_home_and_refuses_bad_paths(self):
        home = Path(tempfile.mkdtemp())
        with patch.dict(os.environ, {"HOME": str(home)}):
            status, data = self.srv.request(
                "GET", "/v1/folders", headers=self.hdr)
        self.assertEqual(json.loads(data)["path"], os.path.realpath(home))
        for query, code in (("path=relative", "bad_cwd"),
                            ("path=/no/such/dir", "bad_cwd"),
                            ("machine=other", "browse_unavailable")):
            status, data = self.srv.request(
                "GET", f"/v1/folders?{query}", headers=self.hdr)
            self.assertEqual((status, json.loads(data)["error"]["code"]),
                             (422, code), query)

    def test_folders_root_has_no_parent_and_needs_auth(self):
        status, data = self.srv.request(
            "GET", "/v1/folders?path=/", headers=self.hdr)
        self.assertIsNone(json.loads(data)["parent"])
        status, _ = self.srv.request("GET", "/v1/folders?path=/")
        self.assertEqual(status, 401)

    def test_settled_launch_stays_settled(self):
        lid = self.srv.state.new_launch("m", "claude", "/tmp")
        self.srv.state.settle_launch(lid, state="started", key="claude:s1")
        self.srv.state.settle_launch(lid, state="failed", note="late")
        self.assertEqual(self.srv.state.launches[lid]["state"], "started")

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


class TestInboxCutoff(unittest.TestCase):
    """catalog.inbox(): the age cutoff, dismissals and finished-today."""

    NOW = 1_800_000_000.0

    def _catalog(self, rows):
        from quiver.remote.catalog import Catalog
        home = Path(tempfile.mkdtemp())
        sessions = [_session(sid=sid, ts=ts) for sid, ts, _st in rows]
        cat = Catalog(home, machine_id="mbp",
                      fetch_sessions=lambda: sessions,
                      status_of=lambda ss: [st for _s, _t, st in rows])
        cat.refresh()
        return cat

    def keys(self, items):
        return sorted(i["key"] for i in items)

    def test_cutoff_drops_old_followups_errors_interrupts(self):
        from quiver.remote.catalog import INBOX_MAX_AGE_S
        fresh = self.NOW - INBOX_MAX_AGE_S + 60
        stale = self.NOW - INBOX_MAX_AGE_S - 60
        cat = self._catalog([
            ("f-new", fresh, "followup"), ("f-old", stale, "followup"),
            ("e-new", fresh, "error"), ("e-old", stale, "error"),
            ("i-new", fresh, "interrupted"), ("i-old", stale, "interrupted"),
            ("active", fresh, "active"),
        ])
        self.assertEqual(
            self.keys(cat.inbox(now=self.NOW)),
            ["claude:e-new", "claude:f-new", "claude:i-new"],
        )

    def test_approval_never_cut(self):
        cat = self._catalog([("a", 0, "active")])
        items = cat.inbox(pending={"claude:a": "r1"}, now=self.NOW)
        self.assertEqual(
            [(i["status"], i["request_id"]) for i in items],
            [("approval", "r1")],
        )

    def test_dismissal_hides_until_new_activity(self):
        t = self.NOW - 3600
        cat = self._catalog([("f", t, "followup")])
        self.assertEqual(
            cat.inbox(dismissed={"claude:f": t}, now=self.NOW), [])
        self.assertEqual(
            cat.inbox(dismissed={"claude:f": t + 1}, now=self.NOW), [])
        self.assertEqual(
            self.keys(cat.inbox(dismissed={"claude:f": t - 1},
                                now=self.NOW)),
            ["claude:f"],
        )

    def test_done_today_is_opt_in(self):
        from quiver.remote.catalog import _local_midnight
        midnight = _local_midnight(self.NOW)
        cat = self._catalog([
            ("today", midnight + 1, "done"),
            ("yesterday", midnight - 1, "done"),
        ])
        self.assertEqual(cat.inbox(now=self.NOW), [])
        items = cat.inbox(include_done=True, now=self.NOW)
        self.assertEqual([(i["key"], i["status"]) for i in items],
                         [("claude:today", "done")])
        self.assertEqual(
            cat.inbox(include_done=True, now=self.NOW,
                      dismissed={"claude:today": midnight + 1}),
            [],
        )


class TestInboxDismiss(unittest.TestCase):
    """POST /v1/inbox/dismiss: auth, per-device storage, reappearing."""

    def setUp(self):
        self.t = time.time() - 3600
        self.srv = _Server(sessions=[_session(sid="f1", ts=self.t)],
                           statuses=lambda ss: ["followup"])
        self.addCleanup(self.srv.stop)

    def hdr(self, cred):
        return {"Authorization": f"Bearer {cred['credential']}"}

    def inbox_keys(self, cred):
        status, data = self.srv.request("GET", "/v1/inbox",
                                        headers=self.hdr(cred))
        self.assertEqual(status, 200)
        return [i["key"] for i in json.loads(data)["items"]]

    def dismiss(self, cred, body):
        return self.srv.request("POST", "/v1/inbox/dismiss", body=body,
                                headers=self.hdr(cred) if cred else {})

    def test_requires_auth(self):
        status, _ = self.dismiss(
            None, {"key": "claude:f1", "last_active_at": self.t})
        self.assertEqual(status, 401)
        cred = self.srv.pair()
        pairing.revoke_device(self.srv.home, cred["device_id"])
        status, _ = self.dismiss(
            cred, {"key": "claude:f1", "last_active_at": self.t})
        self.assertEqual(status, 401)

    def test_rejects_bad_body(self):
        cred = self.srv.pair()
        for body in ({}, {"key": "claude:f1"},
                     {"key": "", "last_active_at": 1.0},
                     {"key": "claude:f1", "last_active_at": "1"},
                     {"key": "claude:f1", "last_active_at": True},
                     {"key": 5, "last_active_at": 1.0},
                     {"key": "claude:f1", "last_active_at": 10 ** 400}):
            status, data = self.dismiss(cred, body)
            self.assertEqual(status, 422, body)
            self.assertEqual(json.loads(data)["error"]["code"], "bad_dismiss")

    def test_dismiss_is_per_device_and_persists(self):
        from quiver.remote import dismissals
        phone, tablet = self.srv.pair(), self.srv.pair()
        self.assertEqual(self.inbox_keys(phone), ["claude:f1"])
        status, data = self.dismiss(
            phone, {"key": "claude:f1", "last_active_at": self.t})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.inbox_keys(phone), [])
        self.assertEqual(self.inbox_keys(tablet), ["claude:f1"])
        path = dismissals.dismissals_file(self.srv.home)
        self.assertEqual(path.parent, pairing.devices_file(
            self.srv.home).parent)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # Read straight off disk: what a restarted hub would see.
        self.assertEqual(
            dismissals.for_device(self.srv.home, phone["device_id"]),
            {"claude:f1": self.t},
        )

    def test_new_activity_brings_item_back(self):
        cred = self.srv.pair()
        self.dismiss(cred, {"key": "claude:f1", "last_active_at": self.t})
        self.assertEqual(self.inbox_keys(cred), [])
        later = [_session(sid="f1", ts=self.t + 60)]
        self.srv.state.catalog._fetch = lambda: later
        self.srv.state.catalog.refresh()
        self.assertEqual(self.inbox_keys(cred), ["claude:f1"])

    def test_store_prunes_by_dismissal_time(self):
        from quiver.remote import dismissals
        home = self.srv.home
        now = time.time()
        dismissals.dismiss(home, "d1", "claude:old", 5.0,
                           now=now - dismissals.KEEP_S - 1)
        dismissals.dismiss(home, "d1", "claude:new", 5.0, now=now)
        self.assertEqual(dismissals.for_device(home, "d1", now=now),
                         {"claude:new": 5.0})
        raw = json.loads(dismissals.dismissals_file(home).read_text())
        self.assertEqual(list(raw["devices"]["d1"]), ["claude:new"])

    def test_write_prunes_every_device(self):
        # An idle (or revoked) device's expired rows go on any write.
        from quiver.remote import dismissals
        home = self.srv.home
        now = time.time()
        dismissals.dismiss(home, "idle", "k", 5.0,
                           now=now - dismissals.KEEP_S - 1)
        dismissals.dismiss(home, "busy", "k", 5.0, now=now)
        raw = json.loads(dismissals.dismissals_file(home).read_text())
        self.assertEqual(list(raw["devices"]), ["busy"])

    def test_stale_retry_keeps_newer_dismissal(self):
        from quiver.remote import dismissals
        home = self.srv.home
        dismissals.dismiss(home, "d1", "k", 20.0)
        dismissals.dismiss(home, "d1", "k", 10.0)
        self.assertEqual(dismissals.for_device(home, "d1"), {"k": 20.0})


class TestSendQueue(unittest.TestCase):
    """POST message → 202, then the send runs on the per-key worker."""

    def setUp(self):
        self.srv = _AttachedServer()
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        self.key = "claude:s1"
        # Fresh-status reads stub out to this box; tests flip it.
        self.live = ["done"]
        self.srv.state.status_of = lambda sessions: list(self.live)

    def _post(self, body):
        return self.srv.request(
            "POST", f"/v1/sessions/{self.key}/message",
            body=body, headers=self.hdr,
        )

    def _injector(self):
        from quiver.remote import inject
        fake = MagicMock()
        fake.send.return_value = inject.InjectResult(True, "sent")
        return fake

    def test_message_202_then_result_event(self):
        from quiver.remote import inject
        fake = self._injector()
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake):
            status, data = self._post(
                {"text": "hi", "client_id": "cid-1"}
            )
            self.assertEqual(status, 202, data)
            body = json.loads(data)
            ev = q.get(timeout=5)
        self.assertTrue(body["ok"])
        self.assertTrue(body["queued"])
        self.assertEqual(body["client_id"], "cid-1")
        self.assertFalse(body["waiting"])
        self.assertEqual(ev["type"], "command.result")
        self.assertTrue(ev["data"]["ok"])
        self.assertEqual(ev["data"]["op"], "message")
        self.assertEqual(ev["data"]["client_id"], "cid-1")
        # Same event is durable: replaying the journal sees it.
        evs, _ = self.srv.state.journal.read_since(self.key, 0)
        self.assertEqual(evs[-1]["type"], "command.result")

    def test_queue_waits_for_idle(self):
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        polled = threading.Event()
        flip = threading.Event()

        def status_of(sessions):
            polled.set()
            return ["done" if flip.is_set() else "active"]

        self.srv.state.status_of = status_of
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.05):
            status, data = self._post({"text": "hi"})
            self.assertEqual(status, 202, data)
            self.assertTrue(json.loads(data)["waiting"])
            self.assertTrue(polled.wait(2))
            self.assertFalse(fake.send.called)
            flip.set()
            ev = q.get(timeout=5)
        self.assertTrue(ev["data"]["ok"])
        fake.send.assert_called_once()
        # The injector saw the fresh status, not the stale row's.
        self.assertEqual(fake.send.call_args[0][2], "done")

    def test_queue_timeout_reports_failure(self):
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        self.live = ["active"]
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_WAIT_MAX_S", 0.1), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.02):
            status, _ = self._post({"text": "hi"})
            self.assertEqual(status, 202)
            ev = q.get(timeout=5)
        self.assertFalse(ev["data"]["ok"])
        self.assertIn("stayed busy", ev["data"]["note"])
        fake.send.assert_not_called()

    def test_steer_sends_immediately_while_active(self):
        from quiver.remote import inject
        fake = self._injector()
        self.live = ["active"]
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake):
            status, data = self._post({"text": "go", "mode": "steer"})
            self.assertEqual(status, 202, data)
            self.assertFalse(json.loads(data)["waiting"])
            ev = q.get(timeout=5)
        self.assertTrue(ev["data"]["ok"])
        fake.send.assert_called_once()
        self.assertEqual(fake.send.call_args[0][2], "active")

    def test_second_queue_send_rechecks_status_at_run(self):
        # Job 2 submitted while its key still read idle must not inject
        # into the turn job 1 just started: the wait uses a fresh read
        # taken when the job runs, not the submit-time one.
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        job1_in_send = threading.Event()
        release_job1 = threading.Event()
        turn_idle = threading.Event()

        def send(session, cmd, status):
            if cmd["text"] == "one":
                job1_in_send.set()
                release_job1.wait(5)
            return inject.InjectResult(True, "sent")

        fake.send.side_effect = send

        def status_of(sessions):
            busy = job1_in_send.is_set() and not turn_idle.is_set()
            return ["active" if busy else "done"]

        self.srv.state.status_of = status_of
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.05):
            self.assertEqual(self._post({"text": "one"})[0], 202)
            self.assertTrue(job1_in_send.wait(2))
            # Job 1 is mid-turn now; queueing a second message reports it.
            status, data = self._post({"text": "two"})
            self.assertEqual(status, 202)
            self.assertTrue(json.loads(data)["waiting"])
            release_job1.set()
            ev1 = q.get(timeout=5)
            self.assertTrue(ev1["data"]["ok"])
            # No result for job 2 while the turn is still hot.
            with self.assertRaises(queue.Empty):
                q.get(timeout=0.5)
            turn_idle.set()
            ev2 = q.get(timeout=5)
        self.assertTrue(ev2["data"]["ok"])
        sent = [c[0] for c in fake.send.call_args_list]
        self.assertEqual([s[1]["text"] for s in sent], ["one", "two"])
        # Job 2 waited for the flip, then sent with the fresh status.
        self.assertEqual(sent[1][2], "done")

    def test_sends_run_fifo_per_key(self):
        from quiver.remote import inject
        fake = self._injector()
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake):
            self.assertEqual(self._post({"text": "first"})[0], 202)
            self.assertEqual(self._post({"text": "second"})[0], 202)
            evs = [q.get(timeout=5), q.get(timeout=5)]
        self.assertTrue(all(e["data"]["ok"] for e in evs))
        sent = [c[0][1]["text"] for c in fake.send.call_args_list]
        self.assertEqual(sent, ["first", "second"])

    def test_steer_bypasses_a_waiting_queue_job(self):
        # Steers get their own queue key: one queue job parked on an
        # active session must not hold a steer back.
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        self.live = ["active"]
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.05):
            status, data = self._post({"text": "q1"})
            self.assertEqual(status, 202)
            self.assertTrue(json.loads(data)["waiting"])
            status, data = self._post({"text": "s1", "mode": "steer"})
            self.assertEqual(status, 202)
            self.assertFalse(json.loads(data)["waiting"])
            ev = q.get(timeout=5)
            self.assertTrue(ev["data"]["ok"])
            # The queue job is still parked; only the steer has sent.
            self.assertEqual(
                [c[0][1]["text"] for c in fake.send.call_args_list],
                ["s1"],
            )
            self.live = ["done"]
            ev2 = q.get(timeout=5)
        self.assertTrue(ev2["data"]["ok"])
        self.assertEqual(
            [c[0][1]["text"] for c in fake.send.call_args_list],
            ["s1", "q1"],
        )

    def test_job_refetches_session_during_wait(self):
        # The Session captured at submit can go stale mid-wait — each
        # poll and the final send must see the catalog's current one.
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        newer = _session(sid="s1")
        flip = threading.Event()
        job_started = threading.Event()
        seen = []

        def status_of(sessions):
            seen.append(sessions[0])
            if len(seen) >= 2:  # handler's read plus the job's first
                job_started.set()
            return ["done" if flip.is_set() else "active"]

        self.srv.state.status_of = status_of
        real_session_for = self.srv.state.catalog.session_for
        self.srv.state.catalog.session_for = (
            lambda k: newer if flip.is_set() else real_session_for(k)
        )
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.05):
            self.assertEqual(self._post({"text": "hi"})[0], 202)
            self.assertTrue(job_started.wait(2))
            flip.set()
            ev = q.get(timeout=5)
        self.assertTrue(ev["data"]["ok"])
        self.assertIs(seen[-1], newer)
        self.assertIs(fake.send.call_args[0][0], newer)

    def test_close_cancels_queued_and_stop_fails_running(self):
        # Restart semantics: queued jobs are cancelled outright; a job
        # already in its wait loop exits on _stop with the same note.
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        calls = [0]
        job_started = threading.Event()

        def status_of(sessions):
            calls[0] += 1
            if calls[0] >= 2:
                job_started.set()
            return ["active"]

        self.srv.state.status_of = status_of
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.05):
            self.assertEqual(
                self._post({"text": "one", "client_id": "j1"})[0], 202
            )
            self.assertTrue(job_started.wait(2))
            self.assertEqual(
                self._post({"text": "two", "client_id": "j2"})[0], 202
            )
            self.srv.state.sendq.close()
            cancelled = q.get(timeout=5)
            self.assertEqual(cancelled["data"]["client_id"], "j2")
            self.assertFalse(cancelled["data"]["ok"])
            self.assertEqual(
                cancelled["data"]["note"],
                "hub restarted; message not sent",
            )
            self.srv.state._stop.set()
            stopped = q.get(timeout=5)
        self.assertEqual(stopped["data"]["client_id"], "j1")
        self.assertFalse(stopped["data"]["ok"])
        self.assertEqual(
            stopped["data"]["note"], "hub restarted; message not sent"
        )
        fake.send.assert_not_called()

    def test_send_backlog_cap_429s(self):
        from quiver.remote import hub as hub_mod
        from quiver.remote import inject
        fake = self._injector()
        self.live = ["active"]
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(hub_mod, "QUEUE_POLL_S", 0.05), \
             patch.object(hub_mod, "MAX_SEND_BACKLOG", 3):
            for i in range(3):
                self.assertEqual(self._post({"text": f"m{i}"})[0], 202)
            status, data = self._post({"text": "m3"})
        self.assertEqual(status, 429)
        self.assertEqual(
            json.loads(data)["error"]["code"], "backlog_full"
        )

    def test_injector_exception_fails_job_not_worker(self):
        from quiver.remote import inject
        fake = self._injector()
        fake.send.side_effect = [
            RuntimeError("boom"), inject.InjectResult(True, "sent")
        ]
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake):
            self.assertEqual(self._post({"text": "one"})[0], 202)
            bad = q.get(timeout=5)
            self.assertEqual(self._post({"text": "two"})[0], 202)
            good = q.get(timeout=5)
        self.assertFalse(bad["data"]["ok"])
        self.assertIn("boom", bad["data"]["note"])
        self.assertTrue(good["data"]["ok"])


_PHONE = {
    "node_name": "iphone-13.taild6a23f.ts.net",
    "node_short": "iphone-13",
    "node_id": "nPHONE",
    "login": "kai@github",
    "display_name": "Kai",
    "tagged": False,
}
_MAC = {
    "node_name": "macbook.taild6a23f.ts.net",
    "node_short": "macbook",
    "node_id": "nMAC",
    "login": "kai@github",
    "display_name": "Kai",
    "tagged": False,
}


class TestPairRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[])
        self.addCleanup(self.srv.stop)
        # Peer identity per request IP: phone asks, mac approves.
        self.srv.state.whois = (
            lambda ip: dict(_PHONE) if ip == "127.0.0.1" else None
        )
        self.srv.state.owner_login = "kai@github"

    def _req(self, name=""):
        return self.srv.request(
            "POST", "/v1/pair/request",
            body={"name": name} if name else {},
        )

    def _poll(self, rid, secret):
        return self.srv.request(
            "GET", f"/v1/pair/request/{rid}?secret={secret}"
        )

    _CSRF = {"Content-Type": "application/json", "X-Quiver-Pair": "1"}

    def _approve(self, code, rid=None, node=None, auth=None, headers=None):
        body = {"code": code}
        if rid:
            body["request_id"] = rid
        hdrs = dict(self._CSRF)
        hdrs.update(headers or {})
        if auth:
            hdrs.pop("X-Quiver-Pair", None)
            hdrs.update(auth)
        if node is not None:
            self.srv.state.whois = lambda ip: dict(node)
        try:
            return self.srv.request(
                "POST", "/v1/pair/requests/approve",
                body=body, headers=hdrs,
            )
        finally:
            if node is not None:
                self.srv.state.whois = (
                    lambda ip: dict(_PHONE) if ip == "127.0.0.1" else None
                )

    def test_request_403_without_tailnet_identity(self):
        self.srv.state.whois = lambda ip: None
        status, data = self._req()
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(data)["error"]["code"], "not_tailnet")

    def test_request_201_shape_and_poll(self):
        status, data = self._req(name="e2e")
        self.assertEqual(status, 201, data)
        r = json.loads(data)
        self.assertTrue(r["request_id"])
        self.assertTrue(r["poll_secret"])
        self.assertTrue(r["code"].isdigit() and len(r["code"]) == 6)
        status, pdata = self._poll(r["request_id"], r["poll_secret"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(pdata)["status"], "pending")
        status, _ = self._poll(r["request_id"], "wrong")
        self.assertEqual(status, 404)

    def test_owner_identity_approve_and_delivered_credential(self):
        _, data = self._req(name="phone")
        r = json.loads(data)
        # Approve from a DIFFERENT node sharing the owner's login.
        status, d = self._approve(r["code"], r["request_id"], node=_MAC)
        self.assertEqual(status, 200, d)
        self.assertTrue(json.loads(d)["ok"])
        status, pdata = self._poll(r["request_id"], r["poll_secret"])
        out = json.loads(pdata)
        self.assertEqual(out["status"], "approved")
        self.assertTrue(out["credential"])
        # The minted credential authenticates real routes.
        auth = {"Authorization": f"Bearer {out['credential']}"}
        status, _ = self.srv.request("GET", "/v1/sessions", headers=auth)
        self.assertEqual(status, 200)

    def test_same_node_cannot_self_approve(self):
        _, data = self._req()
        r = json.loads(data)
        status, d = self._approve(r["code"], r["request_id"], node=_PHONE)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(d)["error"]["code"], "self_approval")

    def test_bearer_can_approve(self):
        cred = self.srv.pair()
        auth = {"Authorization": f"Bearer {cred['credential']}"}
        _, data = self._req()
        r = json.loads(data)
        # whois for this peer maps to the PHONE (the requester) — the
        # bearer path must work without a valid owner identity.
        self.srv.state.owner_login = "other@github"
        status, d = self._approve(
            r["code"], r["request_id"], node=_MAC, auth=auth)
        self.assertEqual(status, 200, d)

    def test_approve_unauthenticated_401(self):
        _, data = self._req()
        r = json.loads(data)
        self.srv.state.owner_login = ""  # no owner identity possible
        status, _ = self._approve(r["code"], r["request_id"])
        self.assertEqual(status, 401)

    def test_tagged_peer_cannot_approve(self):
        _, data = self._req()
        r = json.loads(data)
        tagged = {**_MAC, "tagged": True, "login": "kai@github"}
        status, _ = self._approve(r["code"], r["request_id"], node=tagged)
        self.assertEqual(status, 401)

    def test_approve_wrong_code_404(self):
        _, data = self._req()
        r = json.loads(data)
        wrong = "000000" if r["code"] != "000000" else "999999"
        status, d = self._approve(wrong, r["request_id"], node=_MAC)
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(d)["error"]["code"], "no_match")

    def test_deny_route(self):
        _, data = self._req()
        r = json.loads(data)
        # No bearer and no resolvable owner identity → 401.
        self.srv.state.owner_login = ""
        status, _ = self.srv.request(
            "POST", "/v1/pair/requests/deny",
            body={"code": r["code"], "request_id": r["request_id"]},
            headers=dict(self._CSRF),
        )
        self.assertEqual(status, 401)
        status, d = self.srv.request(
            "POST", "/v1/pair/requests/deny",
            body={"code": r["code"], "request_id": r["request_id"]},
            headers={
                "Content-Type": "application/json",
                "Authorization":
                    f"Bearer {self.srv.pair()['credential']}",
            },
        )
        self.assertEqual(status, 200, d)
        status, pdata = self._poll(r["request_id"], r["poll_secret"])
        self.assertEqual(json.loads(pdata)["status"], "denied")

    def test_requests_list_requires_auth_or_owner(self):
        self._req()
        self.srv.state.owner_login = ""
        status, _ = self.srv.request("GET", "/v1/pair/requests")
        self.assertEqual(status, 401)
        self.srv.state.owner_login = "kai@github"
        # Owner identity alone (no bearer) lists them too — with the
        # CSRF header a cross-site page can't send.
        status, data = self.srv.request(
            "GET", "/v1/pair/requests", headers={"X-Quiver-Pair": "1"})
        self.assertEqual(status, 200)
        status, data = self.srv.request(
            "GET", "/v1/pair/requests",
            headers={
                "Authorization":
                    f"Bearer {self.srv.pair()['credential']}"
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(data)["requests"]), 1)

    def test_decide_requires_json_content_type(self):
        _, data = self._req()
        r = json.loads(data)
        status, d = self.srv.request(
            "POST", "/v1/pair/requests/approve",
            body={"code": r["code"], "request_id": r["request_id"]},
            headers={"X-Quiver-Pair": "1",
                     "Content-Type": "text/plain"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(d)["error"]["code"], "bad_origin")

    def test_decide_requires_pair_header_on_identity(self):
        _, data = self._req()
        r = json.loads(data)
        status, d = self.srv.request(
            "POST", "/v1/pair/requests/approve",
            body={"code": r["code"], "request_id": r["request_id"]},
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(d)["error"]["code"], "bad_origin")

    def test_decide_rejects_foreign_origin_and_host(self):
        _, data = self._req()
        r = json.loads(data)
        status, d = self._approve(
            r["code"], r["request_id"], node=_MAC,
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(d)["error"]["code"], "bad_origin")
        # With an allowed-host set, a foreign Host is refused too.
        self.srv.state.allowed_hosts = {"100.1.2.3:8477"}
        status, d = self._approve(
            r["code"], r["request_id"], node=_MAC,
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(d)["error"]["code"], "bad_origin")

    def test_bearer_from_same_node_is_self_approval(self):
        _, data = self._req()
        r = json.loads(data)
        # Bearer auth, but the peer IS the requesting node — the
        # whois check must still refuse self-approval.
        self.srv.state.whois = lambda ip: dict(_PHONE)
        cred = self.srv.pair()
        status, d = self.srv.request(
            "POST", "/v1/pair/requests/approve",
            body={"code": r["code"], "request_id": r["request_id"]},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cred['credential']}",
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(d)["error"]["code"], "self_approval")

    def test_rate_limited_after_five(self):
        for _ in range(5):
            status, _ = self._req()
            self.assertEqual(status, 201)
        status, data = self._req()
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(data)["error"]["code"], "rate_limited")


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

    def test_serve_sigterm_takes_the_shutdown_path(self):
        # SIGTERM must reach state.stop(), not kill the process outright —
        # otherwise accepted sends drop without their command.result.
        import signal
        from quiver.remote import hub as hub_mod

        state = MagicMock()
        server = MagicMock()
        shutdown_called = threading.Event()
        server.shutdown.side_effect = shutdown_called.set

        def forever(**_kw):
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)

        server.serve_forever.side_effect = forever
        prev = signal.getsignal(signal.SIGTERM)
        try:
            with patch.object(hub_mod, "make_server",
                              return_value=server):
                rc = hub_mod.serve(state, "127.0.0.1", 0)
            self.assertEqual(rc, 0)
            self.assertTrue(shutdown_called.wait(2))
            state.stop.assert_called_once()
            server.server_close.assert_called_once()
            self.assertIs(signal.getsignal(signal.SIGTERM), prev)
        finally:
            signal.signal(signal.SIGTERM, prev)

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
