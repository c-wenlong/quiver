"""R3: satellite agent, remote keys, machine registry, command queue."""

import http.client
import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import agent as agent_mod
from quiver.remote import attachments, catalog as catalog_mod
from quiver.remote import inject, journal, pairing, protocol, supervise
from quiver.remote.hub import HubState, make_server
from quiver.sessions.models import Session


def _session(sid="s1", tool="claude", path="/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   title="test", session_id=sid, tool_name=tool)


class _Hub:
    """A real hub on localhost; satellites are plain HTTP clients."""

    def __init__(self, sessions=None):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)
        self.state.catalog._fetch = lambda: list(sessions or [])
        self.state.catalog._status_of = lambda ss: ["active"] * len(ss)
        self.state.catalog.refresh()
        self.server = make_server(self.state, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        cred = self._pair()
        self.auth = {"Authorization": f"Bearer {cred['credential']}"}

    def _pair(self):
        token = pairing.mint_pair_token(self.home)
        status, data = self.request(
            "POST", "/v1/pair", {"token": token, "name": "dev"}
        )
        assert status == 200, data
        return json.loads(data)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(
                method, path,
                body=json.dumps(body) if body is not None else None,
                headers=headers or {},
            )
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()


class TestRemoteKeys(unittest.TestCase):
    def test_round_trip(self):
        key = protocol.remote_key("m1", "claude:abc")
        self.assertEqual(key, "claude:abc@m1")
        local, mid = protocol.split_remote_key(key)
        self.assertEqual((local, mid), ("claude:abc", "m1"))

    def test_local_key_has_no_machine(self):
        local, mid = protocol.split_remote_key("claude:abc")
        self.assertEqual((local, mid), ("claude:abc", None))

    def test_trailing_at_is_local(self):
        local, mid = protocol.split_remote_key("claude:abc@")
        self.assertEqual((local, mid), ("claude:abc@", None))

    def test_command_envelope(self):
        cmd = protocol.command("message", "claude:a", "m1", "c7",
                               {"text": "hi"})
        self.assertEqual(cmd["type"], "command")
        self.assertEqual(cmd["session"], "claude:a")
        self.assertEqual(cmd["data"]["op"], "message")
        self.assertEqual(cmd["data"]["command_id"], "c7")
        self.assertEqual(cmd["data"]["text"], "hi")


class TestRemoteCatalog(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_merge_stamps_and_persists(self):
        rows = [{"key": "claude:a", "title": "t", "ownership": "observed"}]
        out = catalog_mod.merge_remote_rows(self.home, "m1", rows)
        self.assertEqual(out[0]["key"], "claude:a@m1")
        self.assertTrue(out[0]["remote"])
        self.assertEqual(out[0]["machine_id"], "m1")
        loaded = catalog_mod.load_remote_rows(self.home)
        self.assertEqual([r["key"] for r in loaded], ["claude:a@m1"])

    def test_refresh_includes_remote_rows_not_tailed(self):
        cat = catalog_mod.Catalog(self.home, "localmid",
                                  fetch_sessions=lambda: [],
                                  status_of=lambda ss: [])
        catalog_mod.merge_remote_rows(
            self.home, "m1",
            [{"key": "claude:a", "title": "remote one"}],
        )
        rows = cat.refresh()
        self.assertIn("claude:a@m1", [r["key"] for r in rows])
        self.assertEqual(cat.tail_rows(), [])
        self.assertIsNone(cat.session_for("claude:a@m1"))
        self.assertIsNotNone(cat.row_for("claude:a@m1"))

    def test_machine_upsert_round_trip(self):
        catalog_mod.upsert_machine(
            self.home, {"id": "m1", "name": "mbp", "online": True})
        catalog_mod.upsert_machine(
            self.home, {"id": "m1", "online": False})
        row = catalog_mod.load_machines(self.home)["m1"]
        self.assertEqual(row["name"], "mbp")
        self.assertFalse(row["online"])

    def test_detected_harnesses(self):
        with patch("quiver.remote.catalog.shutil.which",
                   side_effect=lambda b: "/bin/x" if b == "claude" else None):
            with patch(
                "quiver.sessions.resume.launch_tool",
                side_effect=lambda t: {"claude": "claude"}.get(t, t),
            ):
                found = catalog_mod.detected_harnesses()
        self.assertIn("claude", found)
        self.assertNotIn("opencode", found)

    def test_local_machine_offers_only_launchable(self):
        with patch.object(catalog_mod, "detected_harnesses",
                          return_value=["claude", "gemini", "opencode"]):
            row = catalog_mod.local_machine(self.home)
        self.assertEqual(row["harnesses"], ["claude", "gemini", "opencode"])
        self.assertEqual(row["launchable"], ["claude"])


class TestPresence(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)

    def test_connect_disconnect_events(self):
        self.state.sat_connected("m1")
        self.assertTrue(self.state.machine_online("m1"))
        row = catalog_mod.load_machines(self.home)["m1"]
        self.assertTrue(row["online"])
        events, _ = self.state.journal.read_since("_global", 0)
        types = [e["type"] for e in events]
        self.assertIn("machine.online", types)

        self.state.sat_disconnected("m1")
        self.assertFalse(self.state.machine_online("m1"))
        row = catalog_mod.load_machines(self.home)["m1"]
        self.assertFalse(row["online"])
        events, _ = self.state.journal.read_since("_global", 0)
        self.assertIn("machine.offline", [e["type"] for e in events])

    def test_double_connect_needs_double_disconnect(self):
        self.state.sat_connected("m1")
        self.state.sat_connected("m1")
        self.state.sat_disconnected("m1")
        self.assertTrue(self.state.machine_online("m1"))
        self.state.sat_disconnected("m1")
        self.assertFalse(self.state.machine_online("m1"))

    def test_hub_itself_always_online(self):
        self.assertTrue(self.state.machine_online(self.state.machine_id))


class TestCommandQueue(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)

    def test_queue_journals_and_replays(self):
        cmd = self.state.queue_command(
            "m1", "message", "claude:a@m1", {"text": "hi", "mode": "queue"})
        self.assertEqual(cmd["type"], "command")
        self.assertEqual(cmd["session"], "claude:a")  # local key only
        self.assertTrue(cmd["data"]["command_id"])

        replay, truncated = self.state.journal.read_since(
            "_sat_m1", 0)
        self.assertFalse(truncated)
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]["data"]["text"], "hi")

    def test_queued_event_on_session_journal(self):
        self.state.queue_command(
            "m1", "control", "claude:a@m1", {"ctl": "interrupt"})
        events, _ = self.state.journal.read_since("claude:a@m1", 0)
        self.assertEqual(events[0]["type"], "command.queued")
        self.assertEqual(events[0]["data"]["op"], "control")

    def test_live_publish_to_subscriber(self):
        q = self.state.bus.subscribe("_sat_m1")
        self.state.queue_command("m1", "control", "", {"ctl": "x"})
        ev = q.get(timeout=2)
        self.assertEqual(ev["type"], "command")
        self.assertEqual(ev["data"]["ctl"], "x")

    def test_offline_replay_only_after_last(self):
        self.state.queue_command("m1", "control", "", {"ctl": "a"})
        self.state.queue_command("m1", "control", "", {"ctl": "b"})
        replay, _ = self.state.journal.read_since("_sat_m1", 1)
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]["data"]["ctl"], "b")


class TestAgentState(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def _agent(self):
        return agent_mod.RemoteAgent("http://hub", "cred", home=self.home)

    def test_agent_file_round_trip(self):
        conf = {"hub": "http://x", "credential": "c", "device_id": "d"}
        agent_mod.save_agent(self.home, conf)
        self.assertEqual(agent_mod.load_agent(self.home), conf)
        mode = agent_mod.agent_file_for(self.home).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_cursor_persists(self):
        a = self._agent()
        a._done_cmd_seq = 42
        a._save_cmd_cursor()
        b = self._agent()
        self.assertEqual(b._done_cmd_seq, 42)
        self.assertEqual(b._last_cmd_seq, 42)

    def test_cursor_scoped_to_hub(self):
        a = self._agent()
        a._done_cmd_seq = 42
        a._save_cmd_cursor()
        # A re-paired hub must not skip its own command queue.
        other = agent_mod.RemoteAgent("http://other:9", "c", self.home)
        self.assertEqual(other._done_cmd_seq, 0)

    def test_handle_frame_skips_replay_overlap(self):
        a = self._agent()
        a._dispatch = MagicMock()
        a._handle_frame(json.dumps({
            "type": "command", "seq": 3,
            "data": {"op": "control", "command_id": "c1"},
        }).encode())
        a._handle_frame(json.dumps({
            "type": "command", "seq": 2,
            "data": {"op": "control", "command_id": "c2"},
        }).encode())
        self.assertEqual(a._dispatch.call_count, 1)
        self.assertEqual(a._last_cmd_seq, 3)

    def test_dispatch_reports_command_result(self):
        a = self._agent()
        a._execute = MagicMock(return_value=(True, "sent", False))
        a._dispatch({
            "type": "command", "session": "claude:a",
            "data": {"op": "message", "command_id": "c9"},
        })
        self.assertEqual(len(a.outbox), 1)
        ev = a.outbox[0]
        self.assertEqual(ev["type"], "command.result")
        self.assertEqual(ev["session"], "claude:a")
        self.assertEqual(ev["data"]["command_id"], "c9")
        self.assertTrue(ev["data"]["ok"])

    def test_dispatch_echoes_request_id(self):
        a = self._agent()
        a._execute = MagicMock(return_value=(True, "answered", False))
        a._dispatch({
            "type": "command", "session": "claude:a",
            "data": {"op": "approval", "command_id": "c2",
                     "request_id": "req-77"},
        })
        ev = a.outbox[0]
        self.assertEqual(ev["type"], "command.result")
        self.assertEqual(ev["data"]["request_id"], "req-77")
        self.assertFalse(ev["data"]["degraded"])

    def test_dispatch_echoes_client_id(self):
        a = self._agent()
        a._execute = MagicMock(return_value=(True, "sent", False))
        a._dispatch({
            "type": "command", "session": "claude:a",
            "data": {"op": "message", "command_id": "c3",
                     "client_id": "cid-9"},
        })
        ev = a.outbox[0]
        self.assertEqual(ev["type"], "command.result")
        self.assertEqual(ev["data"]["client_id"], "cid-9")

    def test_execute_message_uses_injector(self):
        a = self._agent()
        sess = _session()
        a.catalog.session_for = MagicMock(return_value=sess)
        fake = MagicMock()
        fake.send.return_value = inject.InjectResult(True, "ok")
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(supervise, "meta_for", return_value={}):
            ok, note, _deg = a._execute(
                "claude:s1", "message",
                {"text": "hello", "mode": "steer", "status": "active"})
        self.assertTrue(ok)
        args = fake.send.call_args[0]
        self.assertEqual(args[1]["text"], "hello")
        self.assertEqual(args[1]["mode"], "steer")

    def test_execute_attach_calls_supervise(self):
        a = self._agent()
        a.catalog.session_for = MagicMock(return_value=_session())
        with patch.object(
            supervise, "attach", return_value=(True, "attached")
        ) as att:
            ok, _, _deg = a._execute("claude:s1", "attach",
                               {"mode": "tmux", "status": "idle"})
        self.assertTrue(ok)
        self.assertEqual(att.call_args[1]["mode"], "tmux")

    def test_execute_unknown_session(self):
        a = self._agent()
        a.catalog.session_for = MagicMock(return_value=None)
        ok, note, _deg = a._execute("claude:ghost", "message", {"text": "x"})
        self.assertFalse(ok)
        self.assertIn("unknown session", note)

    def test_execute_unknown_op(self):
        a = self._agent()
        a.catalog.session_for = MagicMock(return_value=_session())
        with patch.object(supervise, "meta_for", return_value={}):
            ok, note, _deg = a._execute("claude:s1", "nonsense", {})
        self.assertFalse(ok)
        self.assertIn("unknown op", note)

    def test_execute_attachment_pulls_hub_key(self):
        a = self._agent()
        staged = []

        def fake_stage(home, key, name, data):
            staged.append((key, name, data))
            return {"ref": name}, None

        with patch.object(agent_mod, "_get_bytes",
                          return_value=b"PNG") as get, \
             patch.object(attachments, "place", side_effect=fake_stage):
            ok, note, _deg = a._execute(
                "claude:s1", "attachment",
                {"ref": "1-shot.png", "hub_key": "claude:s1@m1"})
        self.assertTrue(ok)
        url = get.call_args[0][1]
        self.assertIn("claude:s1@m1/1-shot.png", url)
        self.assertEqual(staged[0][1], "1-shot.png")


class _AgentHub(_Hub):
    """Hub + a paired satellite credential (no RemoteAgent needed)."""


class TestAgentRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _AgentHub()
        self.addCleanup(self.srv.stop)
        self.mid = "sat-m1"

    def _push_catalog(self, rows):
        return self.srv.request(
            "POST", "/v1/agent/catalog",
            {"machine_id": self.mid, "rows": rows},
            headers=self.srv.auth)

    def test_register_machine(self):
        status, data = self.srv.request(
            "POST", "/v1/agent/register",
            {"machine": {"id": self.mid, "name": "mbp", "os": "macos"}},
            headers=self.srv.auth)
        self.assertEqual(status, 200)
        row = catalog_mod.load_machines(self.srv.home)[self.mid]
        self.assertEqual(row["name"], "mbp")

    def test_register_requires_id(self):
        status, _ = self.srv.request(
            "POST", "/v1/agent/register", {"machine": {}},
            headers=self.srv.auth)
        self.assertEqual(status, 422)

    def test_catalog_push_merges_rows(self):
        status, _ = self._push_catalog([
            {"key": "claude:r1", "title": "remote", "status": "active",
             "ownership": "observed"},
        ])
        self.assertEqual(status, 200)
        _, data = self.srv.request(
            "GET", "/v1/sessions", headers=self.srv.auth)
        keys = [r["key"] for r in json.loads(data)["sessions"]]
        self.assertIn("claude:r1@sat-m1", keys)

    def test_catalog_push_emits_ownership_change(self):
        self._push_catalog([{"key": "claude:r1", "ownership": "observed"}])
        self._push_catalog([{"key": "claude:r1", "ownership": "attached"}])
        events, _ = self.srv.state.journal.read_since(
            "claude:r1@sat-m1", 0)
        own = [e for e in events if e["type"] == "session.ownership"]
        self.assertEqual(own[-1]["data"]["ownership"], "attached")

    def test_events_uplink_rekeys_session(self):
        status, _ = self.srv.request(
            "POST", "/v1/agent/events",
            {"machine_id": self.mid, "events": [
                protocol.event(
                    "message.appended", "claude:r1", self.mid,
                    protocol.message("user", "hi from satellite")),
            ]},
            headers=self.srv.auth)
        self.assertEqual(status, 200)
        events, _ = self.srv.state.journal.read_since(
            "claude:r1@sat-m1", 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session"], "claude:r1@sat-m1")
        self.assertEqual(events[0]["machine"], self.mid)
        self.assertEqual(events[0]["data"]["text"], "hi from satellite")

    def test_message_routes_to_command_journal(self):
        self._push_catalog([{
            "key": "claude:r1", "title": "r", "status": "idle",
            "ownership": "attached", "machine_id": self.mid,
        }])
        self.srv.state.catalog.refresh()
        status, data = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/message",
            {"text": "yo", "mode": "queue", "client_id": "cid-9"},
            headers=self.srv.auth)
        self.assertEqual(status, 202)
        body = json.loads(data)
        self.assertTrue(body["queued"])
        # Same shape as the local 202; the satellite re-reads status and
        # decides for itself, so waiting is always false here.
        self.assertEqual(body["client_id"], "cid-9")
        self.assertFalse(body["waiting"])
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]["session"], "claude:r1")
        self.assertEqual(replay[0]["data"]["op"], "message")
        self.assertEqual(replay[0]["data"]["text"], "yo")
        self.assertEqual(replay[0]["data"]["client_id"], "cid-9")

    def test_observed_remote_rejects_message_allows_attach(self):
        self._push_catalog([{
            "key": "claude:r1", "status": "idle",
            "ownership": "observed", "attachable": True,
        }])
        self.srv.state.catalog.refresh()
        key = "claude%3Ar1%40sat-m1"
        status, _ = self.srv.request(
            "POST", f"/v1/sessions/{key}/message",
            {"text": "x"}, headers=self.srv.auth)
        self.assertEqual(status, 409)
        status, _ = self.srv.request(
            "POST", f"/v1/sessions/{key}/control",
            {"op": "attach", "mode": "tmux"}, headers=self.srv.auth)
        self.assertEqual(status, 202)
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        self.assertEqual(replay[0]["data"]["op"], "attach")

    def test_launch_to_named_machine_queues(self):
        status, data = self.srv.request(
            "POST", "/v1/sessions",
            {"machine": "sat-m1", "harness": "claude",
             "cwd": "/tmp/x", "prompt": "go"},
            headers=self.srv.auth)
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(data)["queued"])
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        self.assertEqual(replay[0]["data"]["op"], "launch")
        self.assertEqual(replay[0]["data"]["harness"], "claude")
        launch_id = json.loads(data)["launch_id"]
        self.assertEqual(replay[0]["data"]["launch_id"], launch_id)

    def test_satellite_launch_settles_from_events(self):
        _, data = self.srv.request(
            "POST", "/v1/sessions",
            {"machine": "sat-m1", "harness": "claude",
             "cwd": "/tmp/x", "prompt": "go"},
            headers=self.srv.auth)
        launch_id = json.loads(data)["launch_id"]
        self._push_catalog([{"key": "claude:r9", "ownership": "attached"}])
        self.srv.state.catalog.refresh()
        self.srv.request(
            "POST", "/v1/agent/events",
            {"machine_id": self.mid, "events": [protocol.event(
                "session.launched", "claude:r9", self.mid,
                {"launch_id": launch_id})]},
            headers=self.srv.auth)
        _, view = self.srv.request(
            "GET", f"/v1/launches/{launch_id}", headers=self.srv.auth)
        view = json.loads(view)
        self.assertEqual(view["state"], "started")
        self.assertEqual(view["key"], "claude:r9@sat-m1")

    def test_satellite_launch_failure_settles(self):
        _, data = self.srv.request(
            "POST", "/v1/sessions",
            {"machine": "sat-m1", "harness": "claude",
             "cwd": "/tmp/x", "prompt": "go"},
            headers=self.srv.auth)
        launch_id = json.loads(data)["launch_id"]
        self.srv.request(
            "POST", "/v1/agent/events",
            {"machine_id": self.mid, "events": [protocol.event(
                "command.result", "", self.mid,
                {"op": "launch", "ok": False, "launch_id": launch_id,
                 "note": "no such folder on this machine: /tmp/x"})]},
            headers=self.srv.auth)
        launch = self.srv.state.launches[launch_id]
        self.assertEqual(launch["state"], "failed")
        self.assertIn("no such folder", launch["note"])

    def test_launch_refuses_harness_machine_lacks(self):
        catalog_mod.upsert_machine(
            self.srv.home, {"id": "sat-m1", "launchable": ["codex"]})
        status, data = self.srv.request(
            "POST", "/v1/sessions",
            {"machine": "sat-m1", "harness": "claude",
             "cwd": "/tmp/x", "prompt": "go"},
            headers=self.srv.auth)
        self.assertEqual(status, 422, data)
        self.assertEqual(json.loads(data)["error"]["code"], "not_launchable")

    def test_remote_attachment_stages_and_queues_pull(self):
        self._push_catalog([{
            "key": "claude:r1", "status": "idle",
            "ownership": "attached",
        }])
        self.srv.state.catalog.refresh()
        # Octet-stream upload by hand — request() JSON-encodes.
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request(
            "POST",
            "/v1/sessions/claude%3Ar1%40sat-m1/attachments?name=s.png",
            body=b"\x89PNG", headers={**self.srv.auth,
                                      "Content-Type": "application/octet-stream"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        conn.close()
        self.assertEqual(resp.status, 202)
        self.assertTrue(payload["queued"])
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        att = [c for c in replay if c["data"]["op"] == "attachment"]
        self.assertEqual(len(att), 1)
        self.assertEqual(att[0]["data"]["hub_key"], "claude:r1@sat-m1")
        # The staged file resolves under the hub key.
        ref = payload["ref"]
        self.assertIsNotNone(
            attachments.resolve(self.srv.home, "claude:r1@sat-m1", ref))


class TestAgentCommandStream(unittest.TestCase):
    """The command SSE endpoint: replay + live + presence."""

    def setUp(self):
        # Disconnect detection waits for a failed heartbeat write —
        # shrink the interval so tests see it fast.
        self._hb = patch("quiver.remote.hub._SSE_HEARTBEAT_S", 0.1)
        self._hb.start()
        self.addCleanup(self._hb.stop)
        self.srv = _AgentHub()
        self.addCleanup(self.srv.stop)
        self.mid = "sat-m1"

    def _open_stream(self, last=0):
        """Raw-socket SSE client; returns (sock, buffered reader)."""
        cred = self.srv.auth["Authorization"]
        sock = socket.create_connection(
            ("127.0.0.1", self.srv.port), timeout=10)
        sock.sendall(
            f"GET /v1/agent/commands?machine={self.mid}&last={last} "
            "HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\n"
            f"Authorization: {cred}\r\n"
            "Connection: keep-alive\r\n\r\n".encode()
        )
        reader = sock.makefile("rb")
        head = reader.readline()
        self.assertIn(b"200", head)
        while reader.readline() not in (b"\r\n", b"\n", b""):
            pass  # skip headers
        return sock, reader

    def _read_event(self, reader, timeout=5):
        """Read one SSE frame (blank-line terminated) or None."""
        sock = reader.raw._sock
        sock.settimeout(timeout)
        buf = b""
        while True:
            try:
                line = reader.readline()
            except (socket.timeout, TimeoutError):
                return None
            if not line:
                return None
            buf += line
            if line in (b"\r\n", b"\n"):
                return buf

    def test_stream_replays_queue_and_marks_online(self):
        self.srv.state.queue_command(
            self.mid, "control", "", {"ctl": "ping"})
        sock, reader = self._open_stream()
        try:
            frames = []
            while len(frames) < 2:
                fr = self._read_event(reader)
                if fr is None:
                    break
                frames.append(fr)
            text = b"".join(frames).decode()
            self.assertIn('"type": "command"', text)
            self.assertIn("journal.caught-up", text)
            # While open, the satellite reads as online.
            for _ in range(50):
                if self.srv.state.machine_online(self.mid):
                    break
                time.sleep(0.05)
            self.assertTrue(self.srv.state.machine_online(self.mid))
        finally:
            # makefile() dup'd the fd — the reader must close too before
            # the linger-RST on the socket actually reaches the hub.
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER,
                    struct.pack("ii", 1, 0))
            except OSError:
                pass
            try:
                reader.close()
            finally:
                sock.close()
        # Presence was registered while the stream was open; after close
        # the subscription drains and the machine flips offline.
        for _ in range(50):
            row = catalog_mod.load_machines(self.srv.home).get(self.mid)
            if row is not None and not row["online"]:
                break
            time.sleep(0.1)
        self.assertFalse(self.srv.state.machine_online(self.mid))
        row = catalog_mod.load_machines(self.srv.home)[self.mid]
        self.assertFalse(row["online"])

    def test_commands_requires_machine_param(self):
        status, _ = self.srv.request(
            "GET", "/v1/agent/commands", headers=self.srv.auth)
        self.assertEqual(status, 422)

    def test_pull_attachment_route(self):
        payload, err = attachments.stage(
            self.srv.home, "claude:r1@sat-m1", "shot.png", b"PNGDATA")
        self.assertIsNone(err)
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request(
            "GET",
            "/v1/agent/attachments/claude%3Ar1%40sat-m1/"
            + payload["ref"],
            headers=self.srv.auth)
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), b"PNGDATA")
        conn.close()

    def test_pull_attachment_404(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request(
            "GET",
            "/v1/agent/attachments/claude%3Anone%40sat-m1/missing.png",
            headers=self.srv.auth)
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        conn.close()


class TestEndToEnd(unittest.TestCase):
    """A real RemoteAgent against a real hub over a real socket.

    Register → catalog push → command SSE → dispatch → command.result
    journaled under the hub-scoped key. The whole R3 loop.
    """

    def test_full_loop(self):
        with patch("quiver.remote.hub._SSE_HEARTBEAT_S", 0.1):
            srv = _AgentHub()
            try:
                cred = srv.auth["Authorization"].split(" ", 1)[1]
                sat_home = Path(tempfile.mkdtemp())
                agent = agent_mod.RemoteAgent(
                    srv.base, cred, home=sat_home)
                sess = _session("r1")
                agent.catalog._fetch = lambda: [sess]
                agent.catalog._status_of = lambda ss: ["idle"]
                fake = MagicMock()
                fake.send.return_value = inject.InjectResult(True, "sent")
                mid = agent.machine_id

                done = {}
                thread = threading.Thread(
                    target=lambda: done.setdefault(
                        "rc", agent.run()), daemon=True)

                with patch.object(inject, "injector_for",
                                  return_value=fake), \
                     patch.object(supervise, "meta_for", return_value={}):
                    thread.start()
                    try:
                        # Catalog push: hub lists the satellite row.
                        hub_key = f"claude:r1@{mid}"
                        for _ in range(100):
                            row = srv.state.catalog.row_for(hub_key)
                            if row and row.get("remote"):
                                break
                            time.sleep(0.1)
                        self.assertIsNotNone(row)
                        self.assertEqual(row["machine_id"], mid)

                        # Presence: machine registered online once the
                        # command stream connected.
                        for _ in range(100):
                            if srv.state.machine_online(mid):
                                break
                            time.sleep(0.1)
                        self.assertTrue(srv.state.machine_online(mid))

                        # Phone → hub → journal → SSE → agent dispatch.
                        srv.state.queue_command(
                            mid, "message", hub_key,
                            {"text": "remote hello", "mode": "queue",
                             "status": "idle"})
                        for _ in range(100):
                            if fake.send.called:
                                break
                            time.sleep(0.1)
                        self.assertTrue(fake.send.called)
                        sent = fake.send.call_args[0][1]
                        self.assertEqual(sent["text"], "remote hello")

                        # command.result flows back and lands in the
                        # hub journal under the hub-scoped key.
                        for _ in range(100):
                            evs, _ = srv.state.journal.read_since(
                                hub_key, 0)
                            if any(e["type"] == "command.result"
                                   for e in evs):
                                break
                            time.sleep(0.1)
                        result = [
                            e for e in evs
                            if e["type"] == "command.result"][-1]
                        self.assertTrue(result["data"]["ok"])
                        self.assertEqual(result["data"]["op"], "message")
                        self.assertEqual(result["machine"], mid)
                    finally:
                        agent._stop.set()
                        thread.join(timeout=10)

                # Stream close flips the machine offline.
                for _ in range(100):
                    row = catalog_mod.load_machines(srv.home).get(mid)
                    if row is not None and not row["online"]:
                        break
                    time.sleep(0.1)
                self.assertFalse(row["online"])
            finally:
                srv.stop()


if __name__ == "__main__":
    unittest.main()


class TestAgentEdges(unittest.TestCase):
    """agent.py: HTTP helpers, pairing, frame handling, op edges."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def _agent(self):
        return agent_mod.RemoteAgent("http://h:1", "cred", self.home)

    def test_load_agent_corrupt(self):
        path = agent_mod.agent_file_for(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{bad")
        self.assertIsNone(agent_mod.load_agent(self.home))

    def test_post_http_error_json_and_plain(self):
        import urllib.error

        def raise_http(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "busy", {},
                io_bytes(b'{"err": 1}'))

        def io_bytes(data):
            import io
            return io.BytesIO(data)

        with patch("urllib.request.urlopen", raise_http):
            status, payload = agent_mod._post("http://h", "/x", "c", {})
        self.assertEqual(status, 429)
        self.assertEqual(payload, {"err": 1})

        def raise_plain(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "boom", {}, io_bytes(b'not json'))

        with patch("urllib.request.urlopen", raise_plain):
            status, payload = agent_mod._post("http://h", "/x", "c", {})
        self.assertEqual((status, payload), (500, {}))

    def test_post_network_error_returns_zero(self):
        with patch("urllib.request.urlopen", side_effect=OSError):
            self.assertEqual(
                agent_mod._post("http://h", "/x", "c", {}), (0, {}))

    def test_get_bytes(self):
        resp = MagicMock()
        resp.read.return_value = b"blob"
        resp.__enter__.return_value = resp
        with patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(
                agent_mod._get_bytes("http://h", "/x", "c"), b"blob")
        with patch("urllib.request.urlopen", side_effect=OSError):
            self.assertIsNone(agent_mod._get_bytes("http://h", "/x", "c"))

    def test_pair_agent_success_and_failure(self):
        with patch.object(agent_mod, "_post",
                          return_value=(200, {"credential": "c1",
                                              "device_id": "d1"})):
            conf = agent_mod.pair_agent("http://h/", "tok", "mbp",
                                        self.home)
        self.assertEqual(conf["credential"], "c1")
        self.assertEqual(conf["hub"], "http://h")
        self.assertEqual(agent_mod.load_agent(self.home), conf)
        with patch.object(agent_mod, "_post", return_value=(401, {})):
            self.assertIsNone(
                agent_mod.pair_agent("http://h", "tok", "", self.home))

    def test_push_events_requeues_on_failure(self):
        ag = self._agent()
        ag.outbox = [{"type": "notify"}]
        with patch.object(agent_mod, "_post", return_value=(500, {})):
            self.assertFalse(ag._push_events())
        self.assertEqual(ag.outbox, [{"type": "notify"}])
        with patch.object(agent_mod, "_post", return_value=(200, {})):
            self.assertTrue(ag._push_events())
        self.assertEqual(ag.outbox, [])

    def test_push_events_empty_ok(self):
        self.assertTrue(self._agent()._push_events())

    def test_push_catalog_posts_rows(self):
        ag = self._agent()
        ag.catalog._fetch = lambda: []
        ag.catalog._status_of = lambda ss: []
        calls = []
        with patch.object(
            agent_mod, "_post",
            side_effect=lambda *a: (calls.append(a) or (200, {}))):
            self.assertTrue(ag._push_catalog())
        self.assertEqual(calls[0][1], "/v1/agent/catalog")
        self.assertEqual(calls[0][3]["machine_id"], ag.machine_id)

    def test_handle_frame_malformed_and_noncommand(self):
        ag = self._agent()
        ag._handle_frame(b"{nope")                       # bad json
        ag._handle_frame(json.dumps({"type": "x"}).encode())  # not cmd
        ag._handle_frame(json.dumps(
            {"type": "command", "seq": "x", "data": {}}).encode())
        # A non-int seq still dispatches — no dedup cursor to advance.
        types = [e["type"] for e in ag.outbox]
        self.assertEqual(types, ["command.result"])

    def test_handle_frame_dedup_and_advance(self):
        ag = self._agent()
        ag._dispatch = lambda c: None
        cmd = {"type": "command", "seq": 5, "data": {"op": "x"}}
        ag._handle_frame(json.dumps(cmd).encode())
        self.assertEqual(ag._last_cmd_seq, 5)
        ag._handle_frame(json.dumps(cmd).encode())  # replay: no-op
        ag._handle_frame(json.dumps(
            {"type": "command", "seq": 4, "data": {"op": "x"}}).encode())
        self.assertEqual(ag._last_cmd_seq, 5)

    def test_execute_launch_snapshot(self):
        ag = self._agent()
        ag.catalog._fetch = lambda: []
        ag.catalog._status_of = lambda ss: []
        with patch.object(
            supervise, "launch_new", return_value=(True, "launched", {})
        ) as ln:
            ok, note, _deg = ag._execute(
                "k", "launch",
                {"harness": "claude", "cwd": "/tmp", "prompt": "hi",
                 "launch_id": "l1"})
        self.assertTrue(ok)
        self.assertEqual(ln.call_args[1]["known_ids"], [])
        self.assertEqual(ln.call_args[1]["launch_id"], "l1")

    def test_adopted_launch_reports_session_launched(self):
        ag = self._agent()
        ag.catalog.refresh = lambda: []
        with patch.object(supervise, "adopt_pending",
                          return_value=["claude:new"]), \
             patch.object(supervise, "meta_for",
                          return_value={"launch_id": "l7"}), \
             patch.object(agent_mod, "_post", return_value=(200, {})):
            ag._push_catalog()
        launched = [e for e in ag.outbox if e["type"] == "session.launched"]
        self.assertEqual(launched[0]["session"], "claude:new")
        self.assertEqual(launched[0]["data"]["launch_id"], "l7")

    def test_launch_result_echoes_launch_id(self):
        ag = self._agent()
        with patch.object(ag, "_execute", return_value=(False, "nope", False)):
            ag._dispatch({"session": "", "data": {
                "op": "launch", "command_id": "c1", "launch_id": "l3"}})
        result = [e for e in ag.outbox if e["type"] == "command.result"][-1]
        self.assertEqual(result["data"]["launch_id"], "l3")

    def test_execute_attachment_paths(self):
        ag = self._agent()
        ok, note, _deg = ag._execute("claude:k", "attachment", {"ref": "r.png"})
        self.assertFalse(ok)  # _get_bytes fails against no hub
        with patch.object(agent_mod, "_get_bytes", return_value=b"png"):
            ok, note, _deg = ag._execute("claude:k", "attachment",
                                   {"ref": "r.png", "hub_key": "hk"})
        self.assertTrue(ok)
        # place() keeps the hub's ref so a later message op resolves it.
        self.assertIsNotNone(
            attachments.resolve(self.home, "claude:k", "r.png"))

    def test_execute_message_unknown_ref(self):
        ag = self._agent()
        sess = _session()
        ag.catalog.session_for = lambda k: sess
        ok, note, _deg = ag._execute(
            "k", "message", {"text": "hi", "attachments": ["nope"]})
        self.assertFalse(ok)
        self.assertIn("unknown attachment", note)

    def test_execute_approval_and_control_and_error(self):
        ag = self._agent()
        sess = _session()
        ag.catalog.session_for = lambda k: sess
        fake = MagicMock()
        fake.approval.return_value = inject.InjectResult(True, "ok")
        fake.control.return_value = inject.InjectResult(False, "nope")
        with patch.object(inject, "injector_for", return_value=fake):
            ok, _, _deg = ag._execute(
                "k", "approval",
                {"request_id": "r", "decision": "allow"})
            self.assertTrue(ok)
            ok, note, _deg = ag._execute(
                "k", "control", {"ctl": "interrupt"})
            self.assertFalse(ok)
            fake.send.side_effect = RuntimeError("kaboom")
            ok, note, _deg = ag._execute("k", "message", {"text": "x"})
            self.assertFalse(ok)
            self.assertIn("kaboom", note)
        # Outside the patch: an unmarked session hits the detach guard.
        ok, note, _deg = ag._execute("k", "message", {"text": "x"})
        self.assertFalse(ok)
        self.assertIn("detached", note)


class TestAgentRouteValidation(unittest.TestCase):
    """hub.py satellite routes: malformed bodies and validation 422s."""

    def setUp(self):
        self.srv = _AgentHub()
        self.addCleanup(self.srv.stop)
        self.mid = "sat-m1"

    def _raw(self, method, path, data, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request(method, path, body=data,
                     headers={**self.srv.auth, "Content-Type": ctype})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def test_register_bad_body(self):
        status, _ = self._raw("POST", "/v1/agent/register", b"{bad")
        self.assertIn(status, (400, 422))

    def test_catalog_bad_body_and_missing_mid(self):
        status, _ = self._raw("POST", "/v1/agent/catalog", b"{bad")
        self.assertEqual(status, 400)
        status, _ = self.srv.request(
            "POST", "/v1/agent/catalog", {"rows": []},
            headers=self.srv.auth)
        self.assertEqual(status, 422)

    def test_events_bad_body_and_non_list(self):
        status, _ = self._raw("POST", "/v1/agent/events", b"{bad")
        self.assertEqual(status, 400)
        status, _ = self.srv.request(
            "POST", "/v1/agent/events",
            {"machine_id": self.mid, "events": "x"},
            headers=self.srv.auth)
        self.assertEqual(status, 422)
        # Events that aren't dicts are skipped, not fatal.
        status, data = self.srv.request(
            "POST", "/v1/agent/events",
            {"machine_id": self.mid, "events": [42, {"type": "notify"}]},
            headers=self.srv.auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["accepted"], 1)

    def test_forward_bad_command_bodies(self):
        self.srv.request(
            "POST", "/v1/agent/catalog",
            {"machine_id": self.mid, "rows": [
                {"key": "claude:r1", "ownership": "attached",
                 "status": "idle"}]},
            headers=self.srv.auth)
        self.srv.state.catalog.refresh()
        key = "claude%3Ar1%40sat-m1"
        status, _ = self.srv.request(
            "POST", f"/v1/sessions/{key}/message", {"text": ""},
            headers=self.srv.auth)
        self.assertEqual(status, 422)
        status, _ = self.srv.request(
            "POST", f"/v1/sessions/{key}/approval",
            {"decision": "allow"},  # no request_id
            headers=self.srv.auth)
        self.assertEqual(status, 422)
        status, _ = self.srv.request(
            "POST", f"/v1/sessions/{key}/control", {"op": "bogus"},
            headers=self.srv.auth)
        self.assertEqual(status, 422)
        status, _ = self.srv.request(
            "POST", f"/v1/sessions/{key}/nonsense", {},
            headers=self.srv.auth)
        self.assertEqual(status, 404)

    def test_forward_control_on_observed_is_409(self):
        self.srv.request(
            "POST", "/v1/agent/catalog",
            {"machine_id": self.mid, "rows": [
                {"key": "claude:r1", "ownership": "observed"}]},
            headers=self.srv.auth)
        self.srv.state.catalog.refresh()
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/control",
            {"op": "interrupt"}, headers=self.srv.auth)
        self.assertEqual(status, 409)

    def test_launch_unknown_machine_queues_anyway(self):
        # An unregistered machine still gets the command: it may simply
        # not have connected yet — the offline queue is the point.
        status, data = self.srv.request(
            "POST", "/v1/sessions",
            {"machine": "ghost", "harness": "claude", "cwd": "/x",
             "prompt": "go"},
            headers=self.srv.auth)
        self.assertEqual(status, 202)
        replay, _ = self.srv.state.journal.read_since("_sat_ghost", 0)
        self.assertEqual(replay[0]["data"]["op"], "launch")


class TestAgentCommand(unittest.TestCase):
    """commands._cmd_agent: pairing flags, missing conf, serve call."""

    def _run(self, flags):
        from quiver.remote import commands
        with patch("sys.stdout", __import__("io").StringIO()) as out, \
             patch("sys.stderr", __import__("io").StringIO()) as err:
            rc = commands._cmd_agent(flags)
        return rc, out.getvalue(), err.getvalue()

    def test_token_without_hub(self):
        rc, _o, err = self._run({"--token": "t"})
        self.assertEqual(rc, 1)
        self.assertIn("--hub", err)

    def test_pair_failure(self):
        with patch.object(agent_mod, "pair_agent", return_value=None):
            rc, _o, err = self._run(
                {"--token": "t", "--hub": "http://h:1"})
        self.assertEqual(rc, 1)
        self.assertIn("pairing failed", err)

    def test_no_conf(self):
        with patch.object(agent_mod, "load_agent", return_value=None):
            rc, _o, err = self._run({})
        self.assertEqual(rc, 1)
        self.assertIn("no hub credential", err)

    def test_paired_then_serve(self):
        conf = {"hub": "http://h:1", "credential": "c", "device_id": "d"}
        with patch.object(agent_mod, "pair_agent", return_value=conf), \
             patch.object(agent_mod, "serve", return_value=0) as srv:
            rc, out, _ = self._run(
                {"--token": "t", "--hub": "http://h:1", "--name": "mbp"})
        self.assertEqual(rc, 0)
        self.assertIn("paired", out)
        srv.assert_called_once_with("http://h:1", "c")

    def test_hub_flag_rewrites_saved_conf(self):
        conf = {"hub": "http://old:1", "credential": "c"}
        with patch.object(agent_mod, "load_agent", return_value=conf), \
             patch.object(agent_mod, "serve", return_value=0) as srv:
            rc, _o, _e = self._run({"--hub": "http://new:2"})
        self.assertEqual(rc, 0)
        srv.assert_called_once_with("http://new:2", "c")


class TestAgentLoopEdges(unittest.TestCase):
    """agent.py: stream loop, run(), save-cursor failure."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def _agent(self):
        return agent_mod.RemoteAgent("http://h:1", "cred", self.home)

    def test_save_cmd_cursor_oserror(self):
        ag = self._agent()
        ag._done_cmd_seq = 9
        with patch.object(Path, "write_text", side_effect=OSError):
            ag._save_cmd_cursor()  # must not raise

    def test_command_stream_reads_frames(self):
        ag = self._agent()
        handled = []

        def stop_after(blob):
            handled.append(blob)
            ag._stop.set()

        ag._handle_frame = stop_after
        frame = json.dumps({"type": "command", "seq": 1,
                            "data": {"op": "x"}}).encode()
        resp = MagicMock()
        resp.__iter__.return_value = iter(
            [b"data: " + frame + b"\n", b"\n"])
        resp.__enter__.return_value = resp
        with patch("urllib.request.urlopen", return_value=resp):
            ag._command_stream()
        self.assertEqual(len(handled), 1)

    def test_command_stream_network_error_retries(self):
        ag = self._agent()
        calls = []

        def boom(req, timeout=None):
            calls.append(1)
            ag._stop.set()
            raise OSError("down")

        with patch("urllib.request.urlopen", boom), \
             patch.object(agent_mod.time, "sleep"):
            ag._command_stream()
        self.assertEqual(calls, [1])

    def test_run_spawns_and_joins_threads(self):
        ag = self._agent()
        ag._register = lambda: None
        ag._push_catalog = lambda: True
        # Loop bodies exit instantly; run() then waits on _stop.
        ag._uplink_loop = ag._catalog_loop = ag._tail_loop = \
            ag._downlink_loop = lambda: None
        t = threading.Thread(target=ag.run, daemon=True)
        t.start()
        time.sleep(0.2)
        ag._stop.set()
        t.join(timeout=5)
        self.assertFalse(t.is_alive())

    def test_serve_builds_and_runs(self):
        fake = MagicMock()
        fake.run.return_value = 0
        with patch.object(agent_mod, "RemoteAgent", return_value=fake):
            self.assertEqual(agent_mod.serve("http://h", "c"), 0)


class TestCatalogEdge(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_merge_skips_non_dict_rows(self):
        out = catalog_mod.merge_remote_rows(
            self.home, "m1", ["bad", {"key": "claude:r1"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["key"], "claude:r1@m1")

    def test_merge_write_failure_tolerated(self):
        with patch.object(
            catalog_mod, "atomic_write_text", side_effect=OSError):
            out = catalog_mod.merge_remote_rows(
                self.home, "m1", [{"key": "claude:r1"}])
        self.assertEqual(len(out), 1)  # rows still returned

    def test_load_remote_rows_skips_corrupt(self):
        base = catalog_mod._satellite_dir(self.home)
        base.mkdir(parents=True)
        (base / "m1.json").write_text("{bad")
        (base / "m2.json").write_text(
            json.dumps({"rows": [{"key": "claude:r1@m2"}, "x"]}))
        rows = catalog_mod.load_remote_rows(self.home)
        self.assertEqual(rows, [{"key": "claude:r1@m2"}])


class TestUploadRouteEdges(unittest.TestCase):
    """hub.py upload/attachment-fetch error branches."""

    def setUp(self):
        self.srv = _AgentHub()
        self.addCleanup(self.srv.stop)
        # A satellite row so the remote attachments route engages.
        self.srv.request(
            "POST", "/v1/agent/catalog",
            {"machine_id": "sat-m1", "rows": [
                {"key": "claude:r1", "ownership": "attached"}]},
            headers=self.srv.auth)
        self.srv.state.catalog.refresh()

    def test_empty_upload_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/attachments?name=x.png",
            body=b"", headers={**self.srv.auth,
                               "Content-Type": "application/octet-stream"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 400)

    def test_too_large_413(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        with patch.object(attachments, "MAX_BYTES", 4):
            conn.request(
                "POST",
                "/v1/sessions/claude%3Ar1%40sat-m1/attachments?name=x.png",
                body=b"12345",
                headers={**self.srv.auth,
                         "Content-Type": "application/octet-stream"})
            resp = conn.getresponse()
            resp.read()
            conn.close()
        self.assertEqual(resp.status, 413)

    def test_pull_attachment_unreadable(self):
        # Stage on the hub key, then delete the file: read fails → 404.
        payload, err = attachments.stage(
            self.srv.home, "claude:k@m1", "x.png", b"PNG")
        self.assertIsNone(err)
        Path(payload["path"]).unlink()
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request(
            "GET",
            f"/v1/agent/attachments/claude%3Ak%40m1/{payload['ref']}",
            headers=self.srv.auth)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 404)


class TestAgentCmdDispatch(unittest.TestCase):
    def test_agent_subcommand_rejects_extra_args(self):
        from quiver.remote import commands
        with patch("sys.stderr", __import__("io").StringIO()) as err:
            rc = commands.cmd_remote(["agent", "extra"])
        self.assertEqual(rc, 1)
        self.assertIn("unexpected", err.getvalue())


class TestR3FinalEdges(unittest.TestCase):
    """Tail of the diff: loop bodies, read failures, upload variants."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_stream_sleep_between_reconnects(self):
        ag = agent_mod.RemoteAgent("http://h:1", "c", self.home)
        resp = MagicMock()
        resp.__iter__.return_value = iter([b"data: {}\n", b"\n"])
        resp.__enter__.return_value = resp

        def sleepy(_s):
            ag._stop.set()

        with patch("urllib.request.urlopen", return_value=resp), \
             patch.object(agent_mod.time, "sleep", sleepy):
            ag._command_stream()

    def test_loop_bodies_run_once(self):
        ag = agent_mod.RemoteAgent("http://h:1", "c", self.home)
        ag._push_events = lambda: ag._stop.set()
        ag._uplink_loop()
        ag._stop.clear()
        ag._push_catalog = lambda: None
        ag.tail_manager.sync = lambda rows: ag._stop.set()
        ag._catalog_loop()
        ag._stop.clear()
        ag.tail_manager.tick = lambda: ag._stop.set()
        ag._tail_loop()
        ag._stop.clear()

        def boom():
            ag._stop.set()
            raise OSError("x")

        ag._command_stream = boom
        with patch.object(agent_mod, "RECONNECT_S", 0):
            ag._downlink_loop()

    def test_load_remote_rows_glob_failure(self):
        with patch.object(Path, "glob", side_effect=OSError):
            self.assertEqual(catalog_mod.load_remote_rows(self.home), [])

    def test_message_op_resolves_attachment(self):
        ag = agent_mod.RemoteAgent("http://h:1", "c", self.home)
        sess = _session()
        ag.catalog.session_for = lambda k: sess
        payload, err = attachments.stage(self.home, "k", "a.png", b"PNG")
        self.assertIsNone(err)
        fake = MagicMock()
        fake.send.return_value = inject.InjectResult(True, "ok")
        with patch.object(supervise, "meta_for", return_value={}), \
             patch.object(inject, "injector_for", return_value=fake):
            ok, _, _deg = ag._execute(
                "k", "message",
                {"text": "hi", "attachments": [payload["ref"]]})
        self.assertTrue(ok)
        sent = fake.send.call_args[0][1]
        # resolve() returns the realpath form of the staged file.
        self.assertTrue(
            sent["attachments"][0].endswith(payload["ref"]))


class TestUploadVariants(unittest.TestCase):
    def setUp(self):
        self.srv = _AgentHub()
        self.addCleanup(self.srv.stop)
        self.srv.request(
            "POST", "/v1/agent/catalog",
            {"machine_id": "sat-m1", "rows": [
                {"key": "claude:r1", "ownership": "attached"}]},
            headers=self.srv.auth)
        self.srv.state.catalog.refresh()

    def _post_raw(self, path, data, ctype):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        conn.request("POST", path, body=data,
                     headers={**self.srv.auth, "Content-Type": ctype})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def test_stage_bad_extension_422(self):
        status, _ = self._post_raw(
            "/v1/sessions/claude%3Ar1%40sat-m1/attachments?name=x.exe",
            b"MZ", "application/octet-stream")
        self.assertEqual(status, 422)

    def test_multipart_unparseable_400(self):
        status, _ = self._post_raw(
            "/v1/sessions/claude%3Ar1%40sat-m1/attachments",
            b"garbage", "multipart/form-data; boundary=NOPE")
        self.assertEqual(status, 400)

    def test_pull_attachment_read_failure(self):
        payload, err = attachments.stage(
            self.srv.home, "claude:k@m1", "x.png", b"PNG")
        self.assertIsNone(err)
        with patch.object(Path, "read_bytes", side_effect=OSError):
            conn = http.client.HTTPConnection("127.0.0.1",
                                              self.srv.port, timeout=5)
            conn.request(
                "GET",
                f"/v1/agent/attachments/claude%3Ak%40m1/{payload['ref']}",
                headers=self.srv.auth)
            resp = conn.getresponse()
            resp.read()
            conn.close()
        self.assertEqual(resp.status, 404)

    def test_agent_dispatch(self):
        from quiver.remote import commands
        with patch.object(commands, "_cmd_agent", return_value=0) as ca:
            self.assertEqual(commands.cmd_remote(["agent"]), 0)
        ca.assert_called_once()


class TestReviewFixes(unittest.TestCase):
    """Coverage for the R3 review fixes: cursor semantics, durability,
    dispatch threading, backlog cap, path safety, tail op."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def _agent(self):
        return agent_mod.RemoteAgent("http://h:1", "cred", self.home)

    def test_message_op_goes_to_worker(self):
        ag = self._agent()
        ag._dispatch = MagicMock()
        ag._handle_frame(json.dumps({
            "type": "command", "seq": 1,
            "data": {"op": "message", "command_id": "c1"},
        }).encode())
        ag._dispatch.assert_not_called()
        seq, cmd = ag._work_q.get_nowait()
        self.assertEqual(seq, 1)
        # Control ops still run inline.
        ag._handle_frame(json.dumps({
            "type": "command", "seq": 2,
            "data": {"op": "control", "command_id": "c2"},
        }).encode())
        ag._dispatch.assert_called_once()

    def test_done_cursor_contiguous(self):
        ag = self._agent()
        ag._done_cmd_seq = 0
        ag._mark_done(2)
        self.assertEqual(ag._done_cmd_seq, 0)   # seq 1 unfinished
        ag._mark_done(1)
        self.assertEqual(ag._done_cmd_seq, 2)   # contiguous advance
        ag._mark_done(4)
        ag._mark_done(3)
        self.assertEqual(ag._done_cmd_seq, 4)

    def test_done_cursor_persists_as_json(self):
        ag = self._agent()
        ag._done_cmd_seq = 7
        ag._save_cmd_cursor()
        data = json.loads(ag._cmd_cursor_file().read_text())
        self.assertEqual(data, {"hub": "http://h:1", "seq": 7})

    def test_outbox_durable_across_restart(self):
        ag = self._agent()
        ag._capture("k", protocol.event(
            "message.appended", "k", ag.machine_id,
            protocol.message("user", "keep me")))
        b = self._agent()
        self.assertEqual(len(b.outbox), 1)
        self.assertEqual(b.outbox[0]["data"]["text"], "keep me")
        # Acked events don't requeue.
        ag2 = self._agent()
        ag2._outbox_ack = 1
        ag2._save_outbox_ack()
        c = self._agent()
        self.assertEqual(c.outbox, [])

    def test_push_events_ack_advances(self):
        ag = self._agent()
        ag._capture("k", protocol.event("notify", "k", ag.machine_id,
                                      {"kind": "finished"}))
        with patch.object(agent_mod, "_post", return_value=(200, {})):
            self.assertTrue(ag._push_events())
        self.assertEqual(ag._outbox_ack, 1)
        self.assertEqual(ag.outbox, [])

    def test_tail_op_starts_tailer(self):
        ag = self._agent()
        sess = _session()
        ag.catalog.session_for = lambda k: sess
        with patch.object(ag.tail_manager, "ensure") as en:
            ok, note, _deg = ag._execute("claude:s1", "tail",
                                   {"status": "idle"})
        self.assertTrue(ok)
        en.assert_called_once_with("claude:s1", sess, "idle")

    def test_message_op_uses_live_status(self):
        ag = self._agent()
        sess = _session()
        ag.catalog.session_for = lambda k: sess
        ag.tail_manager.statuses["k"] = "done"   # queue said "active"
        fake = MagicMock()
        fake.send.return_value = inject.InjectResult(True, "ok")
        with patch.object(supervise, "meta_for", return_value={}), \
             patch.object(inject, "injector_for", return_value=fake):
            ag._execute("k", "message",
                        {"text": "hi", "status": "active"})
        self.assertEqual(fake.send.call_args[0][2], "done")


class TestReviewHubFixes(unittest.TestCase):
    """Hub side: path safety, offline sweep, backlog cap."""

    def setUp(self):
        self.srv = _AgentHub()
        self.addCleanup(self.srv.stop)

    def test_machine_id_traversal_stays_in_satellite_dir(self):
        rows = [{"key": "claude:x", "title": "t"}]
        catalog_mod.merge_remote_rows(self.srv.home, "../devices", rows)
        base = catalog_mod._satellite_dir(self.srv.home)
        self.assertTrue(list(base.glob("*.json")))
        # devices.json untouched — the traversal wrote nowhere near it.
        self.assertNotEqual(
            pairing.load_devices(self.srv.home), {})

    def test_restart_marks_satellites_offline(self):
        catalog_mod.upsert_machine(
            self.srv.home, {"id": "sat-x", "online": True})
        self.srv.state._register_machine()
        row = catalog_mod.load_machines(self.srv.home)["sat-x"]
        self.assertFalse(row["online"])

    def test_backlog_cap_returns_429(self):
        st = self.srv.state
        st._sat_acked["sat-m1"] = 0
        with patch.object(st.journal, "last_seq", return_value=3000):
            self.assertIsNone(
                st.queue_command("sat-m1", "message", "k", {}))

    def test_acked_cursor_relaxes_cap(self):
        st = self.srv.state
        st._sat_acked["sat-m1"] = 2500
        with patch.object(st.journal, "last_seq", return_value=3000):
            self.assertIsNotNone(
                st.queue_command("sat-m1", "message", "k", {}))
