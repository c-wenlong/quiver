"""hub.py: auth, pairing route, REST endpoints, SSE stream."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import protocol
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


class TestReadRoutes(HubBase):
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

    def test_machines_lists_self(self):
        self.srv.state._register_machine()
        status, data = self.srv.request(
            "GET", "/v1/machines", headers=self.auth()
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
            headers=self.auth(),
        )
        msgs = json.loads(data)["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[-1]["data"]["text"], "m4")
        cutoff = msgs[0]["seq"]
        status, data = self.srv.request(
            "GET",
            f"/v1/sessions/claude:s1/messages?before={cutoff}",
            headers=self.auth(),
        )
        msgs = json.loads(data)["messages"]
        self.assertEqual(
            [m["data"]["text"] for m in msgs], ["m0", "m1", "m2"]
        )

    def test_events_unknown_key_404(self):
        status, _ = self.srv.request(
            "GET", "/v1/sessions/ghost/events", headers=self.auth()
        )
        self.assertEqual(status, 404)

    def test_messages_unknown_key_404(self):
        status, _ = self.srv.request(
            "GET", "/v1/sessions/ghost/messages", headers=self.auth()
        )
        self.assertEqual(status, 404)

    def test_bad_pair_json(self):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5
        )
        try:
            conn.request("POST", "/v1/pair", body="{bad",
                         headers={"Content-Type": "application/json"})
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()

    def test_static_assets(self):
        for path in ("/", "/app.js", "/styles.css",
                     "/manifest.webmanifest", "/icon.svg"):
            status, _ = self.srv.request("GET", path)
            self.assertEqual(status, 200, path)
        status, _ = self.srv.request("GET", "/nope.js")
        self.assertEqual(status, 404)


class TestWriteStubs(HubBase):
    """R1: write routes exist but return the documented stubs."""

    def test_post_sessions_501(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions", body={"harness": "claude"},
            headers=self.auth(),
        )
        self.assertEqual(status, 501)
        self.assertEqual(
            json.loads(data)["error"]["code"], "not_implemented"
        )

    def test_post_unknown_action_404(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:s1/bogus",
            body={}, headers=self.auth(),
        )
        self.assertEqual(status, 404)

    def test_post_unknown_session_404(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/ghost/message",
            body={"text": "x"}, headers=self.auth(),
        )
        self.assertEqual(status, 404)


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
        self.assertTrue(
            all(not t.is_alive() for t in state._threads)
        )


class TestGlobalEvents(HubBase):
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


if __name__ == "__main__":
    unittest.main()
