"""hub.py: auth, pairing route, REST endpoints, SSE stream."""

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

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
            ("control", {"op": "attach"}),
            ("attachments", {}),
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
