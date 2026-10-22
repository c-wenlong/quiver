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
        a._last_cmd_seq = 42
        a._save_cmd_cursor()
        b = self._agent()
        self.assertEqual(b._last_cmd_seq, 42)

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
        a._execute = MagicMock(return_value=(True, "sent"))
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

    def test_execute_message_uses_injector(self):
        a = self._agent()
        sess = _session()
        a.catalog.session_for = MagicMock(return_value=sess)
        fake = MagicMock()
        fake.send.return_value = inject.InjectResult(True, "ok")
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(supervise, "meta_for", return_value={}):
            ok, note = a._execute(
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
            ok, _ = a._execute("claude:s1", "attach",
                               {"mode": "tmux", "status": "idle"})
        self.assertTrue(ok)
        self.assertEqual(att.call_args[1]["mode"], "tmux")

    def test_execute_unknown_session(self):
        a = self._agent()
        a.catalog.session_for = MagicMock(return_value=None)
        ok, note = a._execute("claude:ghost", "message", {"text": "x"})
        self.assertFalse(ok)
        self.assertIn("unknown session", note)

    def test_execute_unknown_op(self):
        a = self._agent()
        a.catalog.session_for = MagicMock(return_value=_session())
        with patch.object(supervise, "meta_for", return_value={}):
            ok, note = a._execute("claude:s1", "nonsense", {})
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
             patch.object(attachments, "stage", side_effect=fake_stage):
            ok, note = a._execute(
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
            {"text": "yo", "mode": "queue"}, headers=self.srv.auth)
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(data)["queued"])
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0]["session"], "claude:r1")
        self.assertEqual(replay[0]["data"]["op"], "message")
        self.assertEqual(replay[0]["data"]["text"], "yo")

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
