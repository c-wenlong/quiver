"""Write path of the FTS5 session-search index.

``sessions/search_writer.py`` writes the file the Quiver app's Swift
indexer writes, so these tests pin the parts the two sides must agree on:
the schema and meta rows, the fenced commits, the read decisions, and the
rows a transcript turns into (``search_decode.py``, checked against the
expectations in the Swift ``SearchIdentifiersTests`` and
``SearchChunkerTests``). Every test runs in a throwaway HOME.
"""

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from quiver.sessions import search_decode as decode
from quiver.sessions import search_index
from quiver.sessions import search_writer as writer
from tests.test_json_output import decode as decode_json, run_swe


def claude_line(kind, text=None, session="sess-1", cwd="/work/proj", ts="2026-10-01T10:00:00.000Z", **extra):
    record = {"type": kind, "sessionId": session, "cwd": cwd, "timestamp": ts, **extra}
    if text is not None:
        record["message"] = {"role": kind, "content": text}
    return json.dumps(record, separators=(",", ":")) + "\n"


def codex_line(kind, payload, ts="2026-10-01T10:00:00.000Z"):
    return json.dumps({"timestamp": ts, "type": kind, "payload": payload}) + "\n"


class _Home(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        self.projects = self.home / ".claude" / "projects" / "-work-proj"
        self.projects.mkdir(parents=True)
        self.codex = self.home / ".codex" / "sessions" / "2026" / "10" / "01"
        self.codex.mkdir(parents=True)

    def write(self, path: Path, text: str, mode="w"):
        with open(path, mode) as handle:
            handle.write(text)
        return str(path)

    def db(self):
        conn = sqlite3.connect(search_index.index_path(self.home))
        self.addCleanup(conn.close)
        return conn

    def run_pass(self):
        return writer.run_pass(self.home)


class SchemaTests(_Home):
    def test_fresh_index_matches_the_reader_and_swift_schema(self):
        self.write(self.projects / "a.jsonl", claude_line("user", "fix the ledger cursor"))
        self.run_pass()
        conn = self.db()
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta["schema_version"], "1")
        self.assertRegex(meta["index_incarnation"], r"^[0-9A-F-]{36}$")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"meta", "sessions", "files", "messages", "messages_fts"} <= tables)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
        self.assertTrue(search_index.available(self.home))
        hits = search_index.search(self.home, "ledger cursor")
        self.assertEqual([h.session_id for h in hits], ["sess-1"])

    def test_reopen_keeps_the_incarnation(self):
        self.run_pass()
        first = dict(self.db().execute("SELECT key, value FROM meta"))["index_incarnation"]
        self.run_pass()
        self.assertEqual(dict(self.db().execute("SELECT key, value FROM meta"))["index_incarnation"], first)

    def test_a_newer_schema_is_left_alone(self):
        path = search_index.index_path(self.home)
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
        conn.commit()
        conn.close()
        before = path.read_bytes()
        with self.assertRaises(writer.IndexUnavailable):
            self.run_pass()
        self.assertEqual(path.read_bytes(), before)

    def test_a_torn_file_is_rebuilt(self):
        path = search_index.index_path(self.home)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"this is not sqlite" * 100)
        self.write(self.projects / "a.jsonl", claude_line("user", "hello torn"))
        self.run_pass()
        self.assertTrue(search_index.available(self.home))

    def test_a_second_python_pass_is_refused_while_one_runs(self):
        lock = writer._Lock(search_index.index_path(self.home).parent / writer.LOCK_FILE)
        with lock:
            with self.assertRaises(writer.IndexBusy):
                self.run_pass()


class ClaudeTests(_Home):
    def test_whole_read_fills_session_and_rows(self):
        path = self.write(self.projects / "a.jsonl", "".join([
            claude_line("user", "<system-reminder>injected</system-reminder>"),
            claude_line("user", "  please fix\n the ledger  "),
            claude_line("assistant", [
                {"type": "text", "text": "Looking at Ledger.swift"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "rg jsonlFiles"}},
            ], ts="2026-10-01T10:05:00.000Z"),
            claude_line("user", [{"type": "tool_result", "content": "src/a.py:1"}]),
        ]))
        self.run_pass()
        conn = self.db()
        session = conn.execute(
            "SELECT agent, session_id, file_path, title, cwd, cwd_key, created_at,"
            " updated_at, message_count FROM sessions").fetchone()
        self.assertEqual(session, (
            "claude", "sess-1", path, "please fix the ledger", "/work/proj", "/work/proj",
            "2026-10-01T10:00:00.000Z", "2026-10-01T10:05:00.000Z", 5))
        rows = conn.execute(
            "SELECT m.role, f.user_text, f.assistant_text, f.tool_text, f.identifiers"
            " FROM messages m JOIN messages_fts f ON f.rowid = m.id ORDER BY m.id").fetchall()
        self.assertEqual([r[0] for r in rows], ["user", "user", "assistant", "tool", "tool"])
        self.assertEqual(rows[3][3], "Bash: rg jsonlFiles")
        self.assertEqual(rows[3][4], "jsonlfiles jsonl files")
        self.assertEqual(rows[4][3], "src/a.py:1")
        st = os.stat(path)
        file_row = conn.execute(
            "SELECT ino, byte_offset, size_bytes, mtime_ms, state FROM files").fetchone()
        self.assertEqual(file_row[:3], (st.st_ino, st.st_size, st.st_size))
        sec, nsec = divmod(st.st_mtime_ns, 1_000_000_000)
        self.assertEqual(file_row[3], (sec + nsec / 1e9) * 1000)
        self.assertEqual(file_row[4], "current")

    def test_explicit_title_wins_wherever_it_lands(self):
        self.write(self.projects / "a.jsonl", "".join([
            claude_line("user", "first prompt"),
            json.dumps({"type": "custom-title", "customTitle": "My name", "sessionId": "sess-1"}) + "\n",
            claude_line("user", "second prompt"),
        ]))
        self.run_pass()
        self.assertEqual(self.db().execute("SELECT title FROM sessions").fetchone()[0], "My name")

    def test_subagent_transcripts_and_hidden_files_are_skipped(self):
        (self.projects / "sess-1" / "subagents").mkdir(parents=True)
        self.write(self.projects / "sess-1" / "subagents" / "agent-x.jsonl", claude_line("user", "sub"))
        self.write(self.projects / ".hidden.jsonl", claude_line("user", "hidden"))
        result = self.run_pass()
        self.assertEqual(result.files, 0)

    def test_unchanged_file_is_current_on_the_next_pass(self):
        self.write(self.projects / "a.jsonl", claude_line("user", "hello"))
        self.assertEqual(self.run_pass().indexed, 1)
        second = self.run_pass()
        self.assertEqual((second.indexed, second.current), (0, 1))

    def test_append_continues_the_session(self):
        path = self.projects / "a.jsonl"
        self.write(path, claude_line("user", "first prompt about cursors"))
        self.run_pass()
        conn = self.db()
        session_row = conn.execute("SELECT id FROM sessions").fetchone()[0]
        self.write(path, claude_line("assistant", "reply about offsets",
                                     ts="2026-10-02T09:00:00.000Z"), mode="a")
        self.write(path, claude_line("user", "later prompt"), mode="a")
        self.run_pass()
        rows = conn.execute("SELECT session_row_id, role FROM messages ORDER BY id").fetchall()
        self.assertEqual(rows, [(session_row, "user"), (session_row, "assistant"), (session_row, "user")])
        title, count, updated = conn.execute(
            "SELECT title, message_count, updated_at FROM sessions").fetchone()
        # The appended assistant stamp is the latest seen in that span.
        self.assertEqual((title, count, updated), ("first prompt about cursors", 3, "2026-10-02T09:00:00.000Z"))
        self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)

    def test_partial_last_line_waits_for_its_newline(self):
        path = self.projects / "a.jsonl"
        whole = claude_line("user", "complete line")
        self.write(path, whole + claude_line("user", "half")[:-10])
        self.run_pass()
        conn = self.db()
        self.assertEqual(conn.execute("SELECT byte_offset FROM files").fetchone()[0], len(whole))
        self.assertEqual(conn.execute("SELECT count(*) FROM messages").fetchone()[0], 1)

    def test_rewrite_replaces_the_generation_and_drains_orphans(self):
        path = self.projects / "a.jsonl"
        self.write(path, claude_line("user", "old words") + claude_line("user", "more old words"))
        self.run_pass()
        self.write(path, claude_line("user", "new words"))
        self.run_pass()
        conn = self.db()
        self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0], 1)
        self.assertEqual(search_index.search(self.home, "old"), [])
        self.assertEqual(len(search_index.search(self.home, "new")), 1)

    def test_mid_read_flush_commits_in_pieces(self):
        path = self.write(self.projects / "a.jsonl", "".join(
            claude_line("user", f"prompt number {i} " + "x" * 50) for i in range(20)))
        conn = writer.open_index(search_index.index_path(self.home))
        indexer = writer.Indexer(writer.Store(conn), buffer_budget=200)
        self.assertEqual(indexer.index_file(path, "claude"), "indexed")
        conn.close()
        conn = self.db()
        self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM messages").fetchone()[0], 20)
        self.assertEqual(conn.execute("SELECT message_count FROM sessions").fetchone()[0], 20)
        self.assertEqual(conn.execute("SELECT byte_offset FROM files").fetchone()[0], os.path.getsize(path))

    def test_failures_hold_a_file_out_until_its_stat_moves(self):
        path = self.write(self.projects / "a.jsonl", claude_line("user", "hello"))
        conn = writer.open_index(search_index.index_path(self.home))
        self.addCleanup(conn.close)
        store = writer.Store(conn)
        stat = writer.file_stat(path)
        for _ in range(writer.FAILURES_BEFORE_HOLDOUT):
            store.mark_failed(path, stat.mtime_ms)
        self.assertEqual(writer.read_need(store.file_row(path), stat), writer.SKIP)
        os.utime(path, ns=(0, stat.size * 10**9 + 7))
        self.assertEqual(writer.read_need(store.file_row(path), writer.file_stat(path)), writer.WHOLE)


class FenceTests(_Home):
    def test_a_moved_row_rolls_back(self):
        path = self.write(self.projects / "a.jsonl", claude_line("user", "hello"))
        conn = writer.open_index(search_index.index_path(self.home))
        self.addCleanup(conn.close)
        store = writer.Store(conn)
        stat = writer.file_stat(path)
        fields = writer.SessionFields("claude", "s", path)
        rows = decode.message_rows([decode.Message("user", "hello")])
        self.assertTrue(store.commit_file(path, None, stat, 10, writer.FRESH, fields, rows))
        # A second writer expecting no row loses: nothing new is written.
        self.assertFalse(store.commit_file(path, None, stat, 20, writer.FRESH, fields, rows))
        self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT byte_offset FROM files").fetchone()[0], 10)

    def test_rows_the_app_wrote_for_other_harnesses_are_untouched(self):
        self.run_pass()
        conn = self.db()
        conn.execute("INSERT INTO sessions(agent, session_id, file_path) VALUES ('cursor', 'c1', '/x')")
        conn.execute("INSERT INTO files(path, byte_offset, mtime_ms, session_row_id) VALUES ('/x', 5, 1, 1)")
        conn.commit()
        self.write(self.projects / "a.jsonl", claude_line("user", "hello"))
        self.run_pass()
        self.assertEqual(conn.execute(
            "SELECT byte_offset FROM files WHERE path = '/x'").fetchone()[0], 5)
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM sessions WHERE agent = 'cursor'").fetchone()[0], 1)

    def test_a_file_the_app_already_read_is_current(self):
        path = self.write(self.projects / "a.jsonl", claude_line("user", "hello"))
        stat = writer.file_stat(path)
        self.run_pass()
        conn = self.db()
        conn.execute("DELETE FROM messages")
        conn.execute("UPDATE files SET ino = NULL")  # Swift never refreshes it
        conn.commit()
        self.assertEqual(self.run_pass().current, 1)
        self.assertEqual(writer.file_stat(path), stat)


class CodexTests(_Home):
    def test_response_items_index_outside_paginated_history(self):
        self.write(self.codex / "rollout-2026-10-01T10-00-00-abc.jsonl", "".join([
            codex_line("session_meta", {"id": "thread-1", "cwd": "/work/c"}),
            codex_line("response_item", {"type": "message", "role": "developer",
                                         "content": [{"type": "input_text", "text": "rules"}]}),
            codex_line("response_item", {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": "add dark mode"}]}),
            codex_line("response_item", {"type": "local_shell_call",
                                         "action": {"type": "exec", "command": ["rg", "theme"]}}),
            codex_line("response_item", {"type": "function_call_output", "output": "found it"}),
        ]))
        self.run_pass()
        conn = self.db()
        self.assertEqual(conn.execute("SELECT session_id, cwd, title FROM sessions").fetchone(),
                         ("thread-1", "/work/c", "add dark mode"))
        texts = [r[0] for r in conn.execute(
            "SELECT user_text || assistant_text || tool_text FROM messages_fts ORDER BY rowid")]
        self.assertEqual(texts, ["add dark mode", "tool: rg theme", "found it"])

    def test_paginated_history_reads_completed_items_even_on_append(self):
        path = self.codex / "rollout-2026-10-01T10-00-00-def.jsonl"
        self.write(path, "".join([
            codex_line("session_meta", {"id": "thread-2", "history_mode": "paginated",
                                        "thread_name": "Dark mode"}),
            codex_line("event_msg", {"type": "item_completed",
                                     "item": {"type": "UserMessage", "content": "add dark mode"}}),
            codex_line("response_item", {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": "dup"}]}),
        ]))
        self.run_pass()
        self.write(path, "".join([
            codex_line("event_msg", {"type": "item_completed", "item": {
                "type": "CommandExecution", "command": ["ls", "-la"], "aggregated_output": "total 0"}}),
            codex_line("response_item", {"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": "dup reply"}]}),
        ]), mode="a")
        self.run_pass()
        conn = self.db()
        texts = [r[0] for r in conn.execute(
            "SELECT user_text || assistant_text || tool_text FROM messages_fts ORDER BY rowid")]
        self.assertEqual(texts, ["add dark mode", "shell: ls -la", "total 0"])
        self.assertEqual(conn.execute("SELECT title FROM sessions").fetchone()[0], "Dark mode")


class DecodeTests(unittest.TestCase):
    """Expectations copied from the Swift suites, so both writers agree."""

    def test_identifier_shadow_text(self):
        cases = {
            "jsonlFiles": "jsonlfiles jsonl files",
            "~/.claude/projects/foo": "claude projects foo",
            "MAX_RETRY": "max retry",
            "Ledger.swift": "ledger swift",
            "resolveTerminalPath": "resolveterminalpath resolve terminal path",
            "JSONParser": "",
            "plainword": "",
            "a.b.c": "",
            "": "",
            "Foo foo FOO": "foo",
            "fix jsonlFiles and MAX_RETRY today": "jsonlfiles jsonl files max retry",
            "xABCdy": "xabcdy ab cdy",
            "a1B2": "a1b2 a1",
        }
        for text, expected in cases.items():
            self.assertEqual(decode.identifier_shadow_text(text), expected, text)
        text = " ".join(f"ident_{i}" for i in range(5000))
        self.assertEqual(len(decode.identifier_shadow_text(text, 4000).split(" ")), 4000)

    def test_chunker(self):
        rows = decode.message_rows([decode.Message("user", "hi")])
        self.assertEqual([r.user_text for r in rows], ["hi"])
        self.assertEqual(decode.message_rows([decode.Message("user", "  \n\t ")]), [])
        tool = decode.message_rows([decode.Message("tool", "x" * 10000)])
        self.assertEqual(len(tool[0].tool_text), decode.TOOL_ROW_LIMIT)
        text = "a" * 7900 + " " + "b" * 100
        self.assertEqual(decode.chunks(text), ["a" * 7900 + " ", "b" * 100])
        text = "a" * 3000 + " " + "b" * 5001
        self.assertEqual([len(c) for c in decode.chunks(text)], [8000, 2])
        words = " ".join(f"word{i}" for i in range(3000))
        pieces = decode.chunks(words)
        self.assertGreaterEqual(len(pieces), 4)
        self.assertTrue(all(decode.utf16_len(p) <= decode.CHUNK_LIMIT for p in pieces))
        self.assertEqual("".join(pieces), words)
        text = "a" * 7998 + "\U0001F600" + "xxx"
        pieces = decode.chunks(text)
        self.assertEqual(pieces, ["a" * 7998 + "\U0001F600", "xxx"])

    def test_injected_turns(self):
        self.assertTrue(decode.is_injected_turn("  <command-name>/clear</command-name>"))
        self.assertTrue(decode.is_injected_turn("[Request interrupted by user]"))
        self.assertFalse(decode.is_injected_turn("<div> is a tag"))
        self.assertFalse(decode.is_injected_turn("please fix"))

    def test_tool_call_text(self):
        self.assertEqual(decode.tool_call_text("Read", {"file_path": "/a", "command": " "}), "Read: /a")
        self.assertEqual(decode.tool_call_text(None, "raw"), "raw")
        self.assertIsNone(decode.tool_call_text(None, {}))
        self.assertEqual(decode.tool_call_text("x", "y" * 3000), "x: " + "y" * decode.TOOL_ARGUMENT_LIMIT)


class CommandTests(_Home):
    def test_swe_session_index_runs_a_pass_and_turns_the_hub_on(self):
        self.write(self.projects / "a.jsonl", claude_line("user", "hello from the cli"))
        result = run_swe(self.home, "session", "index", "--on", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode_json(result)["data"]
        self.assertTrue(data["enabled"])
        self.assertEqual(data["pass"]["indexed"], 1)
        self.assertEqual(data["sessions"], 1)
        data = decode_json(run_swe(self.home, "session", "index", "--off", "--json"))["data"]
        self.assertFalse(data["enabled"])
        self.assertNotIn("pass", data)
        plain = run_swe(self.home, "session", "index")
        self.assertIn("1 session, 1 message indexed", plain.stdout)

    def test_status_never_creates_the_index(self):
        result = run_swe(self.home, "session", "index", "--status", "--json")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(decode_json(result)["data"]["exists"])
        self.assertFalse(search_index.index_path(self.home).exists())

    def test_unknown_flag_is_refused(self):
        self.assertEqual(run_swe(self.home, "session", "index", "--bogus").returncode, 1)
        self.assertEqual(run_swe(self.home, "session", "index", "--on", "--off").returncode, 1)


class HubLoopTests(_Home):
    def test_loop_indexes_only_while_enabled(self):
        from quiver.remote import hub

        class OneCycle(threading.Event):
            def wait(self, timeout=None):
                self.set()
                return True

        self.write(self.projects / "a.jsonl", claude_line("user", "hub indexed this"))
        state = mock.Mock(home=self.home, _stop=OneCycle())
        hub.HubState._search_index_loop(state)
        self.assertFalse(search_index.index_path(self.home).exists())
        writer.set_enabled(self.home, True)
        state._stop = OneCycle()
        hub.HubState._search_index_loop(state)
        self.assertEqual(len(search_index.search(self.home, "hub indexed")), 1)


if __name__ == "__main__":
    unittest.main()
