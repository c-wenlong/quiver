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
from unittest import mock

from swe.sessions import search_containers as containers
from swe.sessions import search_decode as decode
from swe.sessions import search_index
from swe.sessions import search_writer as writer
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


    def test_a_stamp_past_year_9999_is_none_not_an_exception(self):
        # 1e300 raised OverflowError, which the indexer's per-file guard
        # does not catch, so one junk line ended the whole pass.
        for value in (1e17, 1e20, 1e300):
            with self.subTest(value=value):
                self.assertIsNone(decode.stamp_iso(value))
        with self.assertRaises(ValueError):
            decode.iso_seconds(1e300)
        messages, _ = decode.Decoder("grok").decode_line(
            b'{"type":"user","timestamp":1e300,"content":"hello there"}')
        self.assertEqual([(m.text, m.ts) for m in messages], [("hello there", None)])

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


class JsonlDecodeTests(unittest.TestCase):
    """The Swift ``SearchDecoderTests`` (Pi), ``SearchMoreDecoderTests``
    (Cursor) and ``SearchNewHarnessDecoderTests`` (Qoder, CodeBuddy)
    cases."""

    def test_pi(self):
        _, hints = decode.Decoder("pi").decode_line(
            b'{"type": "session", "id": "pi-1", "cwd": "/repo/pi", "timestamp": "2026-05-12T06:40:00Z"}')
        self.assertEqual((hints.session_id, hints.cwd), ("pi-1", "/repo/pi"))
        decoder = decode.Decoder("pi")
        user, hints = decoder.decode_line(
            b'{"type": "message", "id": "m1", "timestamp": "t1", "message": {"role": "user",'
            b' "content": [{"type": "text", "text": "ls the dir"}]}}')
        self.assertEqual((user, hints.title), (_msgs([("user", "ls the dir", "t1")]), "ls the dir"))
        assistant, _ = decoder.decode_line(
            b'{"type": "message", "timestamp": "t2", "message": {"role": "assistant", "content":'
            b' [{"type": "thinking", "thinking": "hmm"}, {"type": "toolCall", "id": "call_1",'
            b' "name": "bash", "arguments": {"command": "ls -la"}}]}}')
        self.assertEqual(assistant, _msgs([("tool", "bash: ls -la", "t2")]))
        result, _ = decoder.decode_line(
            b'{"type": "message", "timestamp": "t3", "message": {"role": "toolResult",'
            b' "toolCallId": "call_1", "toolName": "bash", "content": [{"type": "text",'
            b' "text": "AGENTS.md\\nREADME.md"}], "isError": false}}')
        self.assertEqual(result, _msgs([("tool", "bash: AGENTS.md\nREADME.md", "t3")]))
        self.assertEqual(decode.Decoder("pi").decode_line(
            b'{"type": "model_change", "provider": "x", "modelId": "y"}')[0], [])
        _, info = decode.Decoder("pi").decode_line(b'{"type": "session_info", "name": "my session name"}')
        self.assertEqual(info.title, "my session name")

    def test_cursor(self):
        out, hints = decode.Decoder("cursor").decode_line(
            b'{"role":"user","message":{"content":[{"type":"text","text":"how do I fix the build?"}]}}')
        self.assertEqual((out, hints.title),
                         (_msgs([("user", "how do I fix the build?", None)]), "how do I fix the build?"))
        out, _ = decode.Decoder("cursor").decode_line(
            b'{"role":"assistant","message":{"content":[{"type":"text","text":"Checking the build."},'
            b'{"type":"tool_use","name":"Bash","input":{"command":"swift build"}}]}}')
        self.assertEqual(out, _msgs([("assistant", "Checking the build.", None),
                                     ("tool", "Bash: swift build", None)]))
        out, _ = decode.Decoder("cursor").decode_line(b'{"role":"user","content":"plain text body"}')
        self.assertEqual(out, _msgs([("user", "plain text body", None)]))
        for role in ("system", "turn_ended", "summary"):
            line = ('{"role":"%s","message":{"content":[{"type":"text","text":"x"}]}}' % role).encode()
            self.assertEqual(decode.Decoder("cursor").decode_line(line)[0], [])
        self.assertFalse(decode.Decoder("cursor").wants(b'{"kind":"progress"}'))

    def test_qoder(self):
        decoder = decode.Decoder("qoder")
        _, dirs = decoder.decode_line(b'{"type":"workspace-directories","directories":["/repo/qoder"]}')
        self.assertEqual(dirs.cwd, "/repo/qoder")
        user, hints = decoder.decode_line(
            b'{"type":"user","timestamp":"t1","sessionId":"q-1","message":{"role":"user","content":'
            b'[{"type":"text","text":"fix the flaky test"},{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}')
        self.assertEqual(user, _msgs([("user", "fix the flaky test", "t1")]))
        self.assertEqual((hints.session_id, hints.title), ("q-1", "fix the flaky test"))
        assistant, _ = decoder.decode_line(
            b'{"type":"assistant","timestamp":"t2","message":{"role":"assistant","content":"on it"}}')
        self.assertEqual(assistant, _msgs([("assistant", "on it", "t2")]))
        _, moved = decoder.decode_line(
            b'{"type":"user","sessionId":"q-1","cwd":"/elsewhere","message":{"role":"user","content":"again"}}')
        self.assertIsNone(moved.cwd)
        decoder = decode.Decoder("qoder")
        _, meta = decoder.decode_line(
            b'{"type":"user","sessionId":"q","isMeta":true,"message":{"role":"user",'
            b'"content":"<system-reminder>env</system-reminder>"}}')
        self.assertEqual((meta.injected_user_turn, meta.title), (True, None))
        _, title = decoder.decode_line(b'{"type":"custom-title","customTitle":"Named"}')
        self.assertEqual(title.title, "Named")

    def test_codebuddy(self):
        decoder = decode.Decoder("codebuddy")
        user, hints = decoder.decode_line(
            b'{"type":"message","sessionId":"cb-1","cwd":"/repo/cb","gitBranch":"main","timestamp":"t1",'
            b'"role":"user","content":[{"type":"input_text","text":"why failing?"},'
            b'{"type":"input_text","text":"part two"}]}')
        self.assertEqual(user, _msgs([("user", "why failing?\n\npart two", "t1")]))
        self.assertEqual((hints.session_id, hints.cwd, hints.title),
                         ("cb-1", "/repo/cb", "why failing? part two"))
        assistant, _ = decoder.decode_line(
            b'{"type":"message","role":"assistant","timestamp":"t2","content":[{"type":"output_text","text":"checking"}]}')
        self.assertEqual(assistant, _msgs([("assistant", "checking", "t2")]))
        plain, _ = decoder.decode_line(b'{"type":"message","role":"user","content":"plain body"}')
        self.assertEqual(plain, _msgs([("user", "plain body", None)]))

        decoder = decode.Decoder("codebuddy")
        decoder.decode_line(b'{"type":"ai-title","aiTitle":"Generated name"}')
        self.assertIsNone(decoder.decode_line(
            b'{"type":"message","role":"user","content":"typed ask"}')[1].title)
        self.assertEqual(decoder.decode_line(
            b'{"type":"summary","summary":"The CLI\'s title"}')[1].title, "The CLI's title")
        self.assertIsNone(decoder.decode_line(b'{"type":"ai-title","aiTitle":"revised"}')[1].title)

        decoder = decode.Decoder("codebuddy")
        skipped, hints = decoder.decode_line(
            b'{"type":"message","role":"user","content":[{"type":"input_text","text":"queued work"}],'
            b'"providerData":{"skipRun":true}}')
        self.assertEqual(([m.role for m in skipped], hints.injected_user_turn, hints.title),
                         (["user"], True, None))
        self.assertTrue(decoder.decode_line(
            b'{"type":"message","role":"user","content":"<task-notification>done</task-notification>"}'
        )[1].injected_user_turn)
        self.assertEqual(decoder.decode_line(
            b'{"type":"message","role":"user","content":"a real prompt"}')[1].title, "a real prompt")


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

    def test_a_session_the_app_adds_during_the_listing_is_never_retired(self):
        self.seed()
        self.run_pass()
        listing = containers.devin_sessions

        def app_indexes_a_new_session(conn):
            # The app lists after this side and commits a session this
            # side's listing never saw.
            out = listing(conn)
            index = sqlite3.connect(search_index.index_path(self.home))
            index.execute("INSERT INTO files(path, byte_offset, mtime_ms) VALUES (?, 0, 0)",
                          (self.db_path + "#brand-new",))
            index.commit()
            index.close()
            return out

        self.source.execute("UPDATE sessions SET last_activity_at = 1791300200")
        with mock.patch.object(containers, "devin_sessions", app_indexes_a_new_session):
            self.run_pass()
        self.assertIsNotNone(self.db().execute(
            "SELECT 1 FROM files WHERE path = ?", (self.db_path + "#brand-new",)).fetchone())

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


_OPENCODE_SCHEMA = """
CREATE TABLE session (id text PRIMARY KEY, parent_id text, directory text NOT NULL,
  title text NOT NULL, time_created integer NOT NULL, time_updated integer NOT NULL,
  time_archived integer);
CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
CREATE TABLE part (id text PRIMARY KEY, message_id text NOT NULL, session_id text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
CREATE TABLE session_v2 (id text PRIMARY KEY, parent_id text, directory text NOT NULL,
  title text, time_created integer NOT NULL, time_updated integer NOT NULL, time_archived integer);
CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL,
  seq integer NOT NULL, time_created integer NOT NULL, time_updated integer NOT NULL,
  data text NOT NULL);
"""


def build_jsonl_home(home: Path) -> None:
    """Pi, Cursor, Qoder, CodeBuddy and OpenCode sessions under ``home``,
    the same files ``SearchHarnessScanTests``-style fixtures write."""
    def write(path: Path, lines, compact=False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        separators = (",", ":") if compact else None
        path.write_text("".join(json.dumps(line, separators=separators) + "\n" for line in lines))

    write(home / ".pi/agent/sessions/--repo-pi--/2026-10-01_pi-1.jsonl", [
        {"type": "session", "id": "pi-1", "cwd": "/repo/pi", "timestamp": "2026-10-01T09:00:00Z"},
        {"type": "message", "timestamp": "2026-10-01T09:00:01Z", "message": {
            "role": "user", "content": [{"type": "text", "text": "list the pangolin files"}]}},
        {"type": "message", "timestamp": "2026-10-01T09:00:02Z", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Listing."}, {
                "type": "toolCall", "id": "c1", "name": "bash",
                "arguments": {"command": "ls pangolin/"}}]}},
        {"type": "message", "timestamp": "2026-10-01T09:00:03Z", "message": {
            "role": "toolResult", "toolName": "bash",
            "content": [{"type": "text", "text": "scales.md"}]}},
        {"type": "session_info", "name": "Pangolin files"},
    ])
    chat = "4f9d0c1e-0000-4000-8000-00000000c0de"
    write(home / f".cursor/projects/repo-cur/agent-transcripts/{chat}/{chat}.jsonl", [
        {"role": "user", "message": {"content": [
            {"type": "text", "text": "<user_query>\nwhy does the okapi build fail\n</user_query>"}]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "name": "Shell", "input": {"command": "make okapi"}}]}},
    ], compact=True)
    meta = home / f".cursor/chats/0123abcd/{chat}/meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"title": "Okapi  build\nfailure", "cwd": "/repo/cur",
                                "createdAtMs": 1791300000123, "updatedAtMs": 1791300099999}))
    write(home / ".cursor/projects/repo-cur/agent-transcripts/lone/lone.jsonl", [
        {"role": "user", "message": {"content": "a chat with no meta"}}], compact=True)
    write(home / ".qoder/projects/-repo-q/q-1.jsonl", [
        {"type": "workspace-directories", "directories": ["/repo/q"]},
        {"type": "user", "timestamp": "2026-10-02T09:00:00Z", "sessionId": "q-1", "cwd": "/repo/q2",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "tune the quokka cache"},
             {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "assistant", "timestamp": "2026-10-02T09:00:05Z",
         "message": {"role": "assistant", "content": "Tuned."}},
    ])
    write(home / ".qoder/projects/-repo-q/subagents/agent-1.jsonl", [
        {"type": "user", "sessionId": "sub", "message": {"role": "user", "content": "hidden"}}])
    write(home / ".codebuddy/projects/-repo-cb/cb-1.jsonl", [
        {"type": "message", "sessionId": "cb-1", "cwd": "/repo/cb", "timestamp": 1791300000,
         "role": "user", "content": [{"type": "input_text", "text": "why is the tapir slow"}]},
        {"type": "message", "role": "assistant", "timestamp": "2026-10-03T09:00:05Z",
         "content": [{"type": "output_text", "text": "Profiling the tapir."}]},
        {"type": "ai-title", "aiTitle": "Tapir slowness"},
    ])
    db = home / ".local/share/opencode/opencode.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript(_OPENCODE_SCHEMA)
    conn.executemany("INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?)", [
        ("ses_1", None, "/repo/oc", "Fix the flaky test", 1791100000000, 1791100003000, None),
        ("ses_child", "ses_1", "/repo/oc", "child", 1791100000000, 1791100003000, None),
        ("ses_old", None, "/repo/oc", "archived", 1791100000000, 1791100003000, 1791100009000),
    ])
    conn.executemany("INSERT INTO message VALUES (?, ?, ?, ?, ?)", [
        ("m_u1", "ses_1", 1791100000000, 1791100000000, '{"role":"user"}'),
        ("m_a1", "ses_1", 1791100001000, 1791100001000, '{"role":"assistant"}'),
    ])
    conn.executemany("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", [
        ("p1", "m_u1", "ses_1", 1791100000000, 1791100000000,
         '{"type":"text","text":"fix the flaky zebrafish test"}'),
        ("p2", "m_a1", "ses_1", 1791100001000, 1791100001000,
         '{"type":"reasoning","text":"thinking about the quokka"}'),
        ("p3", "m_a1", "ses_1", 1791100001500, 1791100001500, '{"type":"text","text":"found it"}'),
        ("p4", "m_a1", "ses_1", 1791100002000, 1791100002000,
         '{"type":"tool","tool":"read","state":{"status":"error","input":{"filePath":"/repo/x.yml"},'
         '"error":"Error: File not found"}}'),
    ])
    conn.execute("INSERT INTO session_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("ses_v2", None, "/repo/v2", "", 1792000000000, 1792000005000, None))
    conn.executemany("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)", [
        ("sm1", "ses_v2", "user", 1, 1792000000000, 1792000000000, '{"text":"deploy the ibex"}'),
        ("sm2", "ses_v2", "assistant", 2, 1792000001000, 1792000001000, json.dumps({"content": [
            {"type": "reasoning", "text": "plan"}, {"type": "text", "text": "Deploying."},
            {"type": "tool", "name": "bash", "state": {
                "input": {"command": "deploy ibex"}, "content": [{"type": "text", "text": "ok"}]}}]})),
        ("sm3", "ses_v2", "system", 3, 1792000002000, 1792000002000, '{"text":"bookkeeping"}'),
    ])
    conn.commit()
    conn.close()


class JsonlHarnessTests(_Home):
    def setUp(self):
        super().setUp()
        build_jsonl_home(self.home)

    def sessions(self):
        return {row[0]: row[1:] for row in self.db().execute(
            "SELECT session_id, agent, title, cwd, created_at, updated_at, message_count"
            " FROM sessions")}

    def test_every_harness_indexes(self):
        result = self.run_pass()
        self.assertEqual((result.failed, result.moved), (0, 0))
        got = self.sessions()
        chat = "4f9d0c1e-0000-4000-8000-00000000c0de"
        self.assertEqual(got["pi-1"], ("pi", "Pangolin files", "/repo/pi",
                                       "2026-10-01T09:00:01Z", "2026-10-01T09:00:03Z", 4))
        # meta.json fills only what the transcript never says: the first
        # prompt keeps the title (Orca's mergeCursorChatMeta order). The
        # stamp is cut, not rounded, the way Foundation formats it.
        self.assertEqual(got[chat], ("cursor", "<user_query> why does the okapi build fail </user_query>",
                                     "/repo/cur", "2026-10-06T15:20:00.122Z",
                                     "2026-10-06T15:21:39.999Z", 3))
        self.assertEqual(got["lone"][1:3], ("a chat with no meta", None))
        self.assertEqual(got["q-1"], ("qoder", "tune the quokka cache", "/repo/q",
                                      "2026-10-02T09:00:00Z", "2026-10-02T09:00:05Z", 2))
        self.assertNotIn("sub", got)
        self.assertEqual(got["cb-1"], ("codebuddy", "Tapir slowness", "/repo/cb",
                                       "2026-10-03T09:00:05Z", "2026-10-06T15:20:00.000Z", 2))
        self.assertEqual(got["ses_1"], ("opencode", "Fix the flaky test", "/repo/oc",
                                        "2026-10-04T07:46:40.000Z", "2026-10-04T07:46:43.000Z", 3))
        self.assertEqual(got["ses_v2"][:2], ("opencode", "deploy the ibex"))
        self.assertNotIn("ses_child", got)
        self.assertNotIn("ses_old", got)
        rows = [r[0] for r in self.db().execute(
            "SELECT f.tool_text FROM messages m JOIN messages_fts f ON f.rowid = m.id"
            " JOIN sessions s ON s.id = m.session_row_id WHERE s.agent = 'opencode'"
            " AND m.role = 'tool' ORDER BY m.id")]
        self.assertEqual(sorted(rows), ["bash: deploy ibex\nok", "read: /repo/x.yml\nError: File not found"])
        for phrase in ("pangolin", "okapi", "quokka cache", "tapir", "zebrafish", "ibex"):
            self.assertTrue(search_index.search(self.home, phrase), phrase)
        again = self.run_pass()
        self.assertEqual((again.indexed, again.current), (0, again.files))

    def test_a_meta_only_change_reads_the_cursor_chat_again(self):
        self.run_pass()
        chat = "4f9d0c1e-0000-4000-8000-00000000c0de"
        meta = self.home / f".cursor/chats/0123abcd/{chat}/meta.json"
        meta.write_text(json.dumps({"title": "Renamed okapi chat", "cwd": "/repo/moved"}))
        os.utime(meta, (2_000_000_000, 2_000_000_000))
        self.assertEqual(self.run_pass().indexed, 1)
        self.assertEqual(self.sessions()[chat][2:4], ("/repo/moved", None))

    def test_a_grown_qoder_transcript_is_read_whole(self):
        self.run_pass()
        path = self.home / ".qoder/projects/-repo-q/q-1.jsonl"
        with open(path, "a") as handle:
            handle.write(json.dumps({"type": "user", "sessionId": "q-1", "cwd": "/repo/other",
                                     "message": {"role": "user", "content": "and the koala"}}) + "\n")
        self.assertEqual(self.run_pass().indexed, 1)
        # A whole read keeps the first cwd and the first prompt's title.
        self.assertEqual(self.sessions()["q-1"][1:3], ("tune the quokka cache", "/repo/q"))
        self.assertEqual(self.db().execute("SELECT count(*) FROM sessions WHERE agent = 'qoder'")
                         .fetchone()[0], 1)


class PairedDecodeTests(unittest.TestCase):
    """The Swift ``SearchNewHarnessDecoderTests`` Grok, Hermes and Kiro
    cases."""

    def test_grok(self):
        _, hints = decode.Decoder("grok").decode_line(
            b'{"info":{"id":"gk-1","cwd":"/repo/g"},"generated_title":"Ship it",'
            b'"session_summary":"fallback","head_branch":"feat/x"}')
        self.assertEqual((hints.session_id, hints.cwd, hints.title), ("gk-1", "/repo/g", "Ship it"))
        _, hints = decode.Decoder("grok").decode_line(
            b'{"info":{"id":"gk-2"},"session_summary":"The summary"}')
        self.assertEqual(hints.title, "The summary")
        out, hints = decode.Decoder("grok").decode_line(
            b'{"type":"user","timestamp":"t1","content":"<user_info>who am i</user_info>\\n'
            b'<user_query>how do I deploy?</user_query>"}')
        self.assertEqual((out, hints.title), (_msgs([("user", "how do I deploy?", "t1")]), "how do I deploy?"))
        out, hints = decode.Decoder("grok").decode_line(
            b'{"type":"user","timestamp":"t0","content":"<user_info>ctx</user_info>"}')
        self.assertEqual((out, hints.title), ([], None))
        out, _ = decode.Decoder("grok").decode_line(
            b'{"type":"user","content":"<user_info>ctx</user_info><git_status>clean</git_status>"}')
        self.assertEqual(out, [])
        out, _ = decode.Decoder("grok").decode_line(
            b'{"type":"assistant","timestamp":"t2","content":"the answer"}')
        self.assertEqual(out, _msgs([("assistant", "the answer", "t2")]))
        out, _ = decode.Decoder("grok").decode_line(
            b'{"type":"assistant","content":[{"type":"text","text":"block one"},'
            b'{"type":"text","text":"block two"}]}')
        self.assertEqual(out, _msgs([("assistant", "block one\nblock two", None)]))
        self.assertEqual(decode.grok_strip_query("x <USER_QUERY> tail"), "tail")

    def test_hermes(self):
        out, hints = decode.Decoder("hermes").decode_line(json.dumps({
            "session_id": "h-1", "model": "opus", "cwd": "/repo/h",
            "session_start": "2026-09-01T08:00:00Z", "messages": [
                {"role": "user", "content": "where is the config?"},
                {"role": "assistant", "content": [{"type": "text", "text": "In settings.json."}]},
                {"role": "system", "content": "skip me"}]}, indent=1).encode())
        self.assertEqual((hints.session_id, hints.cwd, hints.title),
                         ("h-1", "/repo/h", "where is the config?"))
        self.assertEqual(out, _msgs([("user", "where is the config?", None),
                                     ("assistant", "In settings.json.", None)]))

    def test_kiro(self):
        decoder = decode.Decoder("kiro")
        _, manifest = decoder.decode_line(
            b'{"cwd":"/repo/kiro","title":"Named chat","session_state":{},"created_at":"t0"}')
        self.assertEqual((manifest.cwd, manifest.title), ("/repo/kiro", "Named chat"))
        prompt, _ = decoder.decode_line(
            b'{"kind":"Prompt","data":{"content":[{"kind":"text","data":"run the suite"}],'
            b'"meta":{"timestamp":1789471031}}}')
        self.assertEqual(prompt, _msgs([("user", "run the suite", "2026-09-15T11:17:11.000Z")]))
        assistant, _ = decoder.decode_line(
            b'{"kind":"AssistantMessage","data":{"content":[{"kind":"text","data":"running"},'
            b'{"kind":"toolUse","data":{"name":"Bash","input":{"command":"swift test"}}}]}}')
        self.assertEqual([(m.role, m.text) for m in assistant],
                         [("assistant", "running"), ("tool", "Bash: swift test")])
        results, _ = decoder.decode_line(
            b'{"kind":"ToolResults","data":{"content":[{"kind":"toolResult","data":'
            b'{"content":[{"kind":"text","data":"ok"}]}}]}}')
        self.assertEqual([m.role for m in results], ["tool"])
        blob, _ = decoder.decode_line(b'{"kind":"Prompt","data":{"content":[{"kind":"json","data":{"a":1}}]}}')
        self.assertEqual(blob[0].text, '{"a":1}')


KIRO_ID = "0f0e0d0c-1111-4222-8333-444455556666"


def build_paired_home(home: Path) -> None:
    """Hermes, Kiro and Grok sessions under ``home``."""
    hermes = home / ".hermes/sessions"
    hermes.mkdir(parents=True)
    (hermes / "session_20261001_h1.json").write_text(json.dumps({
        "session_id": "h-1", "cwd": "/repo/h", "messages": [
            {"role": "user", "content": "where does the heron config live"},
            {"role": "assistant", "content": [{"type": "text", "text": "In heron.toml."}]}]}, indent=2))
    (hermes / "request_dump_1.json").write_text(json.dumps({"session_id": "dump", "messages": [
        {"role": "user", "content": "not a session"}]}))
    kiro = home / ".kiro/sessions/cli"
    (kiro / KIRO_ID).mkdir(parents=True)
    (kiro / f"{KIRO_ID}.json").write_text(json.dumps({
        "cwd": "/repo/kiro", "title": "Kestrel  suite", "created_at": "2026-09-15T11:00:00Z",
        "updated_at": "2026-09-15T12:00:00Z"}))
    (kiro / f"{KIRO_ID}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"kind": "Prompt", "data": {"content": [{"kind": "text", "data": "run the kestrel suite"}],
                                    "meta": {"timestamp": 1789471031}}},
        {"kind": "AssistantMessage", "data": {"content": [
            {"kind": "text", "data": "running"},
            {"kind": "toolUse", "data": {"name": "Bash", "input": {"command": "make kestrel"}}}]}},
        {"kind": "ToolResults", "data": {"content": [
            {"kind": "toolResult", "data": {"content": [{"kind": "text", "data": "12 passed"}]}}]}},
    ]))
    child = "aaaaaaaa-1111-4222-8333-444455556666"
    (kiro / f"{child}.json").write_text(json.dumps({"parent_session_id": KIRO_ID, "cwd": "/x"}))
    (kiro / f"{child}.jsonl").write_text(json.dumps(
        {"kind": "Prompt", "data": {"content": [{"kind": "text", "data": "child turn"}]}}) + "\n")
    (kiro / KIRO_ID / "task.json").write_text("{}")
    grok = home / ".grok/sessions/ws-1/gk-dir"
    grok.mkdir(parents=True)
    (grok / "summary.json").write_text(json.dumps({
        "info": {"cwd": "/repo/g"}, "generated_title": "Gannet deploy",
        "created_at": 1791300000, "last_active_at": "2026-10-06T16:00:00Z"}))
    (grok / "chat_history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"type": "user", "timestamp": "2026-10-06T15:30:00Z",
         "content": "<user_info>ctx</user_info>\n<user_query>deploy the gannet</user_query>"},
        {"type": "assistant", "content": "Deploying the gannet."},
    ]) + '{"type": "user", "content": "half a li')
    broken = home / ".grok/sessions/ws-1/broken"
    broken.mkdir()
    (broken / "summary.json").write_text("{not json")
    (broken / "chat_history.jsonl").write_text(json.dumps({"type": "assistant", "content": "x"}) + "\n")


class PairedHarnessTests(_Home):
    def setUp(self):
        super().setUp()
        build_paired_home(self.home)

    def sessions(self):
        return {row[0]: row[1:] for row in self.db().execute(
            "SELECT session_id, agent, title, cwd, created_at, updated_at, message_count"
            " FROM sessions")}

    def test_every_harness_indexes(self):
        result = self.run_pass()
        self.assertEqual((result.failed, result.moved), (0, 0))
        got = self.sessions()
        self.assertEqual(got["h-1"], ("hermes", "where does the heron config live", "/repo/h",
                                      None, None, 2))
        self.assertNotIn("dump", got)
        self.assertEqual(got[KIRO_ID], ("kiro", "Kestrel suite", "/repo/kiro",
                                        "2026-09-15T11:00:00Z", "2026-09-15T12:00:00Z", 4))
        self.assertEqual(len(got), 3, got)
        self.assertEqual(got["gk-dir"], ("grok", "Gannet deploy", "/repo/g",
                                         "2026-10-06T15:20:00.000Z", "2026-10-06T16:00:00Z", 2))
        conn = self.db()
        kiro_path = os.path.realpath(self.home / f".kiro/sessions/cli/{KIRO_ID}.json")
        size = sum(os.path.getsize(kiro_path + suffix) for suffix in ("", "l"))
        self.assertEqual(conn.execute("SELECT byte_offset, size_bytes FROM files WHERE path = ?",
                                      (kiro_path,)).fetchone(), (size, size))
        # The child session and the malformed summary commit no session.
        states = conn.execute("SELECT count(*) FROM files WHERE session_row_id IS NULL").fetchone()[0]
        self.assertEqual(states, 2)
        for phrase in ("heron config", "kestrel suite", "12 passed", "gannet"):
            self.assertTrue(search_index.search(self.home, phrase), phrase)
        again = self.run_pass()
        self.assertEqual((again.indexed, again.current), (0, again.files))

    def test_a_finished_grok_tail_line_indexes_once(self):
        self.run_pass()
        self.assertEqual(search_index.search(self.home, "lemur"), [])
        history = self.home / ".grok/sessions/ws-1/gk-dir/chat_history.jsonl"
        with open(history, "a") as handle:
            handle.write('ne about the lemur"}\n')
        self.assertEqual(self.run_pass().indexed, 1)
        self.assertEqual(self.sessions()["gk-dir"][5], 3)
        self.assertEqual([h.session_id for h in search_index.search(self.home, "lemur")], ["gk-dir"])
        again = self.run_pass()
        self.assertEqual((again.indexed, again.current), (0, again.files))
        self.assertEqual(self.sessions()["gk-dir"][5], 3)

    def test_a_sidecar_only_change_reads_the_pair_again(self):
        self.run_pass()
        transcript = self.home / f".kiro/sessions/cli/{KIRO_ID}.jsonl"
        with open(transcript, "a") as handle:
            handle.write(json.dumps({"kind": "Prompt", "data": {"content": [
                {"kind": "text", "data": "and the kakapo"}]}}) + "\n")
        self.assertEqual(self.run_pass().indexed, 1)
        self.assertEqual(self.sessions()[KIRO_ID][5], 5)
        self.assertEqual(len(search_index.search(self.home, "kakapo")), 1)


if __name__ == "__main__":
    unittest.main()
