"""The search writer's harnesses beyond Claude Code and Codex.

Each decoder here ports one from the Swift indexer, so the cases copy the
expectations in the Swift suites (``SearchDecoderTests``,
``SearchDevinTests``): the two writers share one index file and have to
turn a transcript into the same rows. Every test runs in a throwaway HOME
with made-up content.
"""

import json
import os
import sqlite3
import unittest
from pathlib import Path

from quiver.sessions import search_containers as containers
from quiver.sessions import search_decode as decode
from quiver.sessions import search_index
from quiver.sessions import search_writer as writer
from tests.test_search_writer import _Home


def _msgs(pairs):
    return [decode.Message(role, text, ts) for role, text, ts in pairs]


class StampTests(unittest.TestCase):
    def test_iso_matches_foundation(self):
        # ISO8601FormatStyle cuts the double's fraction to milliseconds;
        # 1700000000.123 is stored as .12299999..., so Swift prints .122.
        self.assertEqual(decode.stamp_iso(1700000000.123), "2023-11-14T22:13:20.122Z")
        self.assertEqual(decode.stamp_iso(1791308163592), "2026-10-06T17:36:03.592Z")
        self.assertEqual(decode.stamp_iso(1792428953.001), "2026-10-19T16:55:53.000Z")
        self.assertEqual(decode.stamp_iso(" 2026-01-01 "), " 2026-01-01 ")
        for value in (True, 0, -5, " ", None, float("nan"), [1]):
            self.assertIsNone(decode.stamp_iso(value))


class DevinDecodeTests(unittest.TestCase):
    def test_chat_message_rows(self):
        decoder = decode.Decoder("devin")
        user, _ = decoder.decode_line(
            b'{"message_id":"m1","role":"user","content":"what drives exist",'
            b'"metadata":{"created_at":"2026-09-15T15:48:00Z"}}')
        self.assertEqual(user, _msgs([("user", "what drives exist", "2026-09-15T15:48:00Z")]))
        calls, _ = decoder.decode_line(
            b'{"message_id":"m2","role":"assistant","content":"","tool_calls":[{"id":"c1",'
            b'"name":"exec","arguments":{"command":"ls /Drives"}}],'
            b'"metadata":{"created_at":"2026-09-15T15:48:56Z"}}')
        self.assertEqual(calls, _msgs([("tool", "exec: ls /Drives", "2026-09-15T15:48:56Z")]))
        result, _ = decoder.decode_line(
            b'{"message_id":"m3","role":"tool","content":"output here","tool_call_id":"c1",'
            b'"metadata":{"created_at":"2026-09-15T15:48:57Z"}}')
        self.assertEqual(result, _msgs([("tool", "exec: output here", "2026-09-15T15:48:57Z")]))
        system, _ = decoder.decode_line(
            b'{"message_id":"m0","role":"system","content":"<system_info>x</system_info>"}')
        self.assertEqual(system, [])

    def test_atif_document_and_bare_step(self):
        document = json.dumps({
            "schema_version": "ATIF-v1.7", "session_id": "alluring-collard",
            "working_directory": "/repo/demo", "agent": {"name": "devin"},
            "steps": [
                {"step_id": 1, "timestamp": "2026-10-19T14:40:53Z", "source": "system",
                 "message": "You are Devin..."},
                {"step_id": 9, "timestamp": "2026-10-19T14:41:15Z", "source": "user",
                 "message": "which drives exist?"},
                {"step_id": 11, "timestamp": "2026-10-19T14:41:19Z", "source": "agent",
                 "message": "", "tool_calls": [{"tool_call_id": "c1", "function_name": "exec",
                                                "arguments": {"command": "ls -la /Drives"}}],
                 "observation": {"results": [{"source_call_id": "c1",
                                              "content": "finetuned official seafile"}]}},
                {"step_id": 13, "timestamp": "2026-10-19T14:41:28Z", "source": "agent",
                 "message": "Found them: a and b."},
            ],
        }, indent=2).encode()
        messages, hints = decode.Decoder("devin").decode_line(document)
        self.assertEqual(hints.session_id, "alluring-collard")
        self.assertEqual(hints.cwd, "/repo/demo")
        self.assertEqual(hints.title, "which drives exist?")
        self.assertEqual(messages, _msgs([
            ("user", "which drives exist?", "2026-10-19T14:41:15Z"),
            ("tool", "exec: ls -la /Drives", "2026-10-19T14:41:19Z"),
            ("tool", "finetuned official seafile", "2026-10-19T14:41:19Z"),
            ("assistant", "Found them: a and b.", "2026-10-19T14:41:28Z"),
        ]))
        step, _ = decode.Decoder("devin").decode_line(
            b'{"step_id": 5, "timestamp": "t", "source": "user", "message": {"content": "hi there"}}')
        self.assertEqual(step, _msgs([("user", "hi there", "t")]))


_DEVIN_SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT NOT NULL,
  backend_type TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  agent_mode TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
  last_activity_at INTEGER NOT NULL, title TEXT, main_chain_id INTEGER,
  hidden INTEGER NOT NULL DEFAULT 0);
CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, node_id INTEGER NOT NULL, parent_node_id INTEGER,
  chat_message TEXT NOT NULL, created_at INTEGER NOT NULL, metadata TEXT,
  UNIQUE(session_id, node_id));
"""


class DevinTests(_Home):
    """``SearchDevinTests``' fixture and expectations, on swe's writer."""

    def setUp(self):
        super().setUp()
        self.devin = self.home / ".local/share/devin/cli"
        self.devin.mkdir(parents=True)
        self.source = sqlite3.connect(self.devin / "sessions.db", isolation_level=None)
        self.addCleanup(self.source.close)
        self.source.executescript(_DEVIN_SCHEMA)
        self.db_path = os.path.realpath(self.devin / "sessions.db")

    def add_session(self, sid, title, head, created, activity, hidden=False):
        self.source.execute(
            "INSERT INTO sessions(id, working_directory, created_at, last_activity_at,"
            " title, main_chain_id, hidden) VALUES (?, '/repo/devin', ?, ?, ?, ?, ?)",
            (sid, created, activity, title, head, int(hidden)))

    def add_node(self, sid, node, parent, message):
        self.source.execute(
            "INSERT INTO message_nodes(session_id, node_id, parent_node_id, chat_message,"
            " created_at) VALUES (?, ?, ?, ?, 0)", (sid, node, parent, message))

    def seed(self):
        self.add_session("brave-otter", "Untitled", 6, 1_791_300_000, 1_791_300_090)
        self.add_node("brave-otter", 0, None,
                      '{"role":"system","content":"Current workspace directories"}')
        self.add_node("brave-otter", 1, 0,
                      '{"role":"user","content":"find the walrus config",'
                      '"metadata":{"created_at":"2026-10-06T10:00:00Z"}}')
        self.add_node("brave-otter", 2, 1,
                      '{"role":"assistant","content":"Look",'
                      '"metadata":{"created_at":"2026-10-06T10:00:05Z"}}')
        self.add_node("brave-otter", 3, 1,
                      '{"role":"assistant","content":"Looking for it.","tool_calls":[{"id":"c1",'
                      '"name":"exec","arguments":{"command":"grep -r walrus ."}}],'
                      '"metadata":{"created_at":"2026-10-06T10:00:06Z"}}')
        self.add_node("brave-otter", 4, 3,
                      '{"role":"tool","content":"conf/walrus.toml","tool_call_id":"c1",'
                      '"metadata":{"created_at":"2026-10-06T10:00:07Z"}}')
        self.add_node("brave-otter", 6, 4,
                      '{"role":"assistant","content":"It is conf/walrus.toml.",'
                      '"metadata":{"created_at":"2026-10-06T10:00:09Z"}}')

    def test_sessions_index_along_the_main_chain(self):
        self.seed()
        result = self.run_pass()
        self.assertEqual(result.indexed, 1)
        conn = self.db()
        path = self.db_path + "#brave-otter"
        row = conn.execute("SELECT ino, byte_offset, mtime_ms, size_bytes, session_row_id"
                           " FROM files WHERE path = ?", (path,)).fetchone()
        self.assertEqual(row[:4], (None, 6, 1_791_300_090_000.0, 6))
        fields = conn.execute(
            "SELECT agent, session_id, file_path, title, cwd, cwd_key, created_at, updated_at,"
            " message_count FROM sessions WHERE id = ?", (row[4],)).fetchone()
        self.assertEqual(fields, (
            "devin", "brave-otter", path, "find the walrus config", "/repo/devin",
            "/repo/devin", "2026-10-06T15:20:00.000Z", "2026-10-06T15:21:30.000Z", 5))
        rows = conn.execute(
            "SELECT m.role, f.user_text || f.assistant_text || f.tool_text FROM messages m"
            " JOIN messages_fts f ON f.rowid = m.id ORDER BY m.id").fetchall()
        self.assertEqual([f"{r}: {t}" for r, t in rows], [
            "user: find the walrus config",
            "assistant: Looking for it.",
            "tool: exec: grep -r walrus .",
            "tool: exec: conf/walrus.toml",
            "assistant: It is conf/walrus.toml.",
        ])
        hits = search_index.search(self.home, "walrus")
        self.assertEqual([(h.agent, h.session_id) for h in hits], [("devin", "brave-otter")])
        again = self.run_pass()
        self.assertEqual((again.indexed, again.current), (0, 1))

    def test_a_grown_session_is_read_again_and_a_gone_one_retires(self):
        self.seed()
        self.add_session("quiet-heron", "Heron work", None, 1_791_300_000, 1_791_300_000)
        self.add_node("quiet-heron", 0, None, '{"role":"user","content":"count the herons"}')
        self.assertEqual(self.run_pass().indexed, 2)
        self.add_node("brave-otter", 7, 6, '{"role":"user","content":"now the narwhal"}')
        self.source.execute("UPDATE sessions SET main_chain_id = 7, last_activity_at = 1791300100"
                            " WHERE id = 'brave-otter'")
        self.source.execute("UPDATE sessions SET hidden = 1 WHERE id = 'quiet-heron'")
        self.assertEqual(self.run_pass().indexed, 1)
        conn = self.db()
        self.assertIsNone(conn.execute("SELECT 1 FROM files WHERE path = ?",
                                       (self.db_path + "#quiet-heron",)).fetchone())
        self.assertEqual(conn.execute("SELECT session_id, message_count FROM sessions").fetchall(),
                         [("brave-otter", 6)])
        self.assertEqual(search_index.search(self.home, "herons"), [])
        self.assertEqual(len(search_index.search(self.home, "narwhal")), 1)

    def test_a_real_title_wins_and_a_headless_session_walks_from_the_newest_node(self):
        self.add_session("calm-lynx", "Lynx deploy", None, 1_791_300_000, 0)
        self.add_node("calm-lynx", 0, None, '{"role":"user","content":"ship the lynx"}')
        self.add_node("calm-lynx", 1, 0, '{"role":"assistant","content":"draft"}')
        self.add_node("calm-lynx", 2, 0, '{"role":"assistant","content":"shipped"}')
        conn = containers.open_read_only(self.db_path)
        self.addCleanup(conn.close)
        (session,) = containers.devin_sessions(conn)
        self.assertEqual((session.stat_mtime_ms, session.nodes), (1_791_300_000_000.0, 3))
        messages, prompt = containers.devin_messages(conn, session)
        self.assertEqual([m.text for m in messages], ["ship the lynx", "shipped"])
        self.assertEqual(containers.devin_title(session, prompt), "Lynx deploy")

    def test_an_unreadable_database_is_skipped(self):
        self.source.execute("DROP TABLE message_nodes")
        self.write(self.projects / "a.jsonl", '{"type":"user","sessionId":"s",'
                   '"message":{"role":"user","content":"still indexed"}}\n')
        result = self.run_pass()
        self.assertEqual((result.indexed, result.failed), (1, 0))

    def test_atif_transcripts_index_whole(self):
        transcripts = self.devin / "transcripts"
        transcripts.mkdir()
        doc = {"session_id": "alluring-collard", "working_directory": "/repo/demo", "steps": [
            {"step_id": 9, "timestamp": "2026-10-19T14:41:15Z", "source": "user",
             "message": "which drives exist?"},
            {"step_id": 13, "timestamp": "2026-10-19T14:41:28Z", "source": "agent",
             "message": "Found them: aardvark and bobcat."}]}
        path = transcripts / "alluring-collard.json"
        path.write_text(json.dumps(doc, indent=2))
        self.assertEqual(self.run_pass().indexed, 1)
        conn = self.db()
        fields = conn.execute("SELECT agent, session_id, title, message_count FROM sessions").fetchone()
        self.assertEqual(fields, ("devin", "alluring-collard", "which drives exist?", 2))
        self.assertEqual(conn.execute("SELECT byte_offset FROM files").fetchone()[0],
                         path.stat().st_size)
        # A grown document is one JSON value: read whole, never appended.
        doc["steps"].append({"step_id": 15, "source": "user", "message": "and the capybara?"})
        path.write_text(json.dumps(doc, indent=2))
        self.assertEqual(self.run_pass().indexed, 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT message_count FROM sessions").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
