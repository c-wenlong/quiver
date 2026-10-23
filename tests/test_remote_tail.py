"""tail.py: record decoders, byte-offset source, snapshot diff, manager."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.remote import tail
from quiver.sessions.models import Session


def _claude_user(text):
    return {"type": "user",
            "message": {"role": "user", "content": text}}


def _claude_assistant(blocks):
    return {"type": "assistant",
            "message": {"role": "assistant", "content": blocks}}


class TestDecoders(unittest.TestCase):
    def test_claude_user_text(self):
        out = tail.decode_record("claude", _claude_user("hello there"))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "user")
        self.assertEqual(out[0]["text"], "hello there")

    def test_claude_blocks_keep_detail(self):
        rec = _claude_assistant([
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "doing it"},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls -la"}},
        ])
        kinds = [m["kind"] for m in tail.decode_record("claude", rec)]
        self.assertEqual(kinds, ["thinking", "assistant", "tool_use"])
        tool = tail.decode_record("claude", rec)[2]["tool"]
        self.assertEqual(tool["call_id"], "t1")
        self.assertIn("ls -la", tool["args_summary"])

    def test_claude_tool_result_pairs(self):
        rec = _claude_user([
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "file1\nfile2"}
        ])
        out = tail.decode_record("claude", rec)
        self.assertEqual(out[0]["kind"], "tool_result")
        self.assertEqual(out[0]["result_ref"], "t1")

    def test_claude_compaction_and_error(self):
        rec = {"type": "system", "subtype": "compact_boundary"}
        self.assertEqual(tail.decode_record("claude", rec)[0]["kind"],
                         "compaction")
        rec = _claude_assistant([{"type": "text", "text": "boom"}])
        rec["isApiErrorMessage"] = True
        self.assertEqual(tail.decode_record("claude", rec)[0]["kind"],
                         "error")

    def test_codex_items(self):
        cases = [
            ({"type": "response_item",
              "payload": {"type": "message", "role": "assistant",
                          "content": [{"type": "output_text",
                                       "text": "done"}]}},
             "assistant"),
            ({"type": "response_item",
              "payload": {"type": "reasoning",
                          "summary": [{"type": "summary_text",
                                       "text": "planning"}]}},
             "thinking"),
            ({"type": "response_item",
              "payload": {"type": "function_call", "name": "exec",
                          "call_id": "c9", "arguments": "{}"}},
             "tool_use"),
            ({"type": "response_item",
              "payload": {"type": "function_call_output",
                          "call_id": "c9", "output": "ok"}},
             "tool_result"),
        ]
        for rec, kind in cases:
            out = tail.decode_record("codex", rec)
            self.assertTrue(out, rec)
            self.assertEqual(out[0]["kind"], kind, rec)
        self.assertEqual(
            tail.decode_record("codex", cases[3][0])[0]["result_ref"], "c9"
        )

    def test_generic_fallback_and_secrets(self):
        rec = {"type": "assistant",
               "message": {"role": "assistant",
                           "content": "token sk-proj-abcdefghijklmnop1234"}}
        out = tail.decode_record("unknown-tool", rec)
        self.assertEqual(out[0]["kind"], "assistant")
        self.assertIn("[REDACTED]", out[0]["text"])

    def test_args_full_is_redacted(self):
        # args_full lands in the journal verbatim — it must get the same
        # secret redaction as the one-line summary.
        rec = _claude_assistant([
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "export A=sk-proj-abcdefghijklmnop1234"}},
        ])
        tool = tail.decode_record("claude", rec)[0]["tool"]
        self.assertIn("[REDACTED]", tool["args_full"])
        self.assertNotIn("sk-proj", tool["args_full"])

    def test_decode_never_raises(self):
        self.assertEqual(tail.decode_record("claude", {"type": "weird"}), [])
        self.assertEqual(tail.decode_record("codex", {"payload": 1}), [])


class TestJsonlTailSource(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "t.jsonl"

    def _write(self, records):
        with open(self.path, "a") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def test_incremental_reads(self):
        self._write([_claude_user("one")])
        src = tail.JsonlTailSource(self.path, "claude")
        out = src.poll()
        self.assertEqual([m["text"] for m in out], ["one"])
        self.assertEqual(src.poll(), [])
        self._write([_claude_user("two")])
        self.assertEqual([m["text"] for m in src.poll()], ["two"])

    def test_partial_line_waits(self):
        with open(self.path, "w") as fh:
            fh.write(json.dumps(_claude_user("full")) + "\n")
            fh.write('{"type":"user","message":{"role":"user","conte')  # partial
        src = tail.JsonlTailSource(self.path, "claude")
        self.assertEqual(len(src.poll()), 1)
        with open(self.path, "a") as fh:
            fh.write('nt":"tail"}}')  # no newline yet
            fh.write("\n")
        out = src.poll()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "tail")

    def test_cursor_restore(self):
        self._write([_claude_user("one")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        state = src.state()
        src2 = tail.JsonlTailSource(self.path, "claude")
        src2.restore(state)
        self.assertEqual(src2.poll(), [])
        self._write([_claude_user("two")])
        self.assertEqual(src2.poll()[0]["text"], "two")

    def test_oversized_record_is_skipped_not_wedged(self):
        # A single JSONL record larger than the read chunk used to stall
        # the tailer on that offset forever; now it's skipped once.
        big = {"type": "user",
               "message": {"role": "user",
                           "content": "x" * (tail._MAX_CHUNK + 4096)}}
        self._write([_claude_user("before"), big, _claude_user("after")])
        src = tail.JsonlTailSource(self.path, "claude")
        self.assertEqual([m["text"] for m in src.poll()], ["before"])
        out = src.poll()
        self.assertEqual(out[0]["kind"], "system")
        self.assertIn("oversized", out[0]["text"])
        self.assertEqual([m["text"] for m in src.poll()], ["after"])
        self._write([_claude_user("next")])
        self.assertEqual(src.poll()[0]["text"], "next")


class TestSnapshotSource(unittest.TestCase):
    def _session(self):
        return Session(timestamp=0, agent="X", path="/tmp",
                       session_id="s1", tool_name="nope")

    def test_emits_only_appends(self):
        src = tail.SnapshotSource(self._session())

        class FakeTranscript:
            def __init__(self, texts):
                self.messages = [
                    _tr_msg("assistant", t) for t in texts
                ]

        def _tr_msg(role, text):
            from quiver.reports.transcripts import NormalizedMessage
            return NormalizedMessage(role=role, text=text, kind="message")

        with patch.object(tail._tr, "read_transcript",
                          return_value=FakeTranscript(["a", "b"])):
            self.assertEqual(len(src.poll()), 2)
            self.assertEqual(src.poll(), [])
        with patch.object(tail._tr, "read_transcript",
                          return_value=FakeTranscript(["a", "b", "c"])):
            out = src.poll()
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["text"], "c")


class _Transcript:
    def __init__(self, texts, sources=()):
        from quiver.reports.transcripts import NormalizedMessage
        self.messages = [NormalizedMessage(role="assistant", text=t, kind="message") for t in texts]
        self.source_paths = list(sources)


class TestSnapshotChangeGate(unittest.TestCase):
    """Snapshot stores re-read only when their change token moves."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store.json"
        self.store.write_text("{}")

    def _session(self, tool="nope"):
        return Session(timestamp=0, agent="X", path="/tmp", session_id="s1", tool_name=tool)

    def test_unchanged_files_skip_the_read(self):
        src = tail.SnapshotSource(self._session())
        reads = []

        def read(_s):
            reads.append(1)
            return _Transcript(["a", "b"] + ["c"] * (len(reads) > 1), [str(self.store)])

        with patch.object(tail._tr, "read_transcript", side_effect=read):
            self.assertEqual(len(src.poll()), 2)
            self.assertEqual(src.poll(), [])
            self.assertEqual(src.poll(), [])
            self.assertEqual(len(reads), 1, "polls after the first trust the unchanged token")
            # A write moves the token: the next poll reads and emits it.
            time.sleep(0.01)
            self.store.write_text('{"more": true}')
            out = src.poll()
            self.assertEqual([m["text"] for m in out], ["c"])
            self.assertEqual(len(reads), 2)

    def test_wal_write_counts_as_a_change(self):
        src = tail.SnapshotSource(self._session())
        with patch.object(tail._tr, "read_transcript", return_value=_Transcript(["a"], [str(self.store)])) as rt:
            src.poll(); src.poll()
            calls = rt.call_count
            (self.tmp / "store.json-wal").write_bytes(b"x")
            src.poll()
            self.assertEqual(rt.call_count, calls + 1)

    def test_full_reread_after_the_recheck_window(self):
        src = tail.SnapshotSource(self._session())
        with patch.object(tail._tr, "read_transcript", return_value=_Transcript(["a"], [str(self.store)])) as rt, \
                patch.object(tail, "SNAPSHOT_RECHECK_S", 0.0):
            src.poll(); src.poll(); src.poll()
            self.assertEqual(rt.call_count, 3)

    def test_devin_uses_its_row_token(self):
        src = tail.SnapshotSource(self._session("devin"))
        token = [((5, 5), (2,))]
        with patch.object(tail, "_devin_token", side_effect=lambda _sid: token[0]), \
                patch.object(tail._tr, "read_transcript_tail", return_value=_Transcript(["a"])) as rt:
            src.poll(); src.poll(); src.poll()
            self.assertEqual(rt.call_count, 1)
            token[0] = ((6, 6), (2,))
            src.poll()
            self.assertEqual(rt.call_count, 2)

    def test_devin_token_reads_one_sessions_rows(self):
        import sqlite3
        db = self.tmp / "sessions.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE sessions(id TEXT PRIMARY KEY, main_chain_id INTEGER);"
            "CREATE TABLE message_nodes(row_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,"
            " node_id INTEGER, parent_node_id INTEGER, chat_message TEXT, created_at INTEGER);"
            "INSERT INTO sessions VALUES ('s1', 1), ('s2', 1);"
            "INSERT INTO message_nodes(session_id, node_id, chat_message, created_at)"
            " VALUES ('s1', 1, '{}', 0), ('s2', 1, '{}', 0);"
        )
        conn.commit()
        with patch.object(tail, "DEVIN_DB", str(db)):
            before = tail._devin_token("s1")
            conn.execute("INSERT INTO message_nodes(session_id, node_id, chat_message, created_at) VALUES ('s2', 2, '{}', 0)")
            conn.commit()
            self.assertEqual(tail._devin_token("s1"), before, "another session's write leaves s1's token alone")
            conn.execute("INSERT INTO message_nodes(session_id, node_id, chat_message, created_at) VALUES ('s1', 2, '{}', 0)")
            conn.commit()
            self.assertNotEqual(tail._devin_token("s1"), before)
        conn.close()
        with patch.object(tail, "DEVIN_DB", str(self.tmp / "missing.db")):
            self.assertIsNone(tail._devin_token("s1"))


class TestSlidingWindowDiff(unittest.TestCase):
    def test_new_from(self):
        self.assertEqual(tail._new_from([], ["a", "b"]), 0)
        self.assertEqual(tail._new_from(["a", "b"], ["a", "b", "c"]), 2)  # grew
        self.assertEqual(tail._new_from(["a", "b", "c"], ["b", "c", "d", "e"]), 2)  # slid
        self.assertEqual(tail._new_from(["a", "b"], ["x", "y"]), 0)  # nothing shared
        self.assertEqual(tail._new_from(["a", "b", "c"], ["a", "x"]), 1)  # rewrite at the fork
        # A repeated message makes [a] a chance prefix; the slide overlap [a, c] wins.
        self.assertEqual(tail._new_from(["a", "b", "a", "c"], ["a", "c", "d", "e"]), 2)

    def test_windowed_snapshot_emits_only_the_slid_in_messages(self):
        src = tail.SnapshotSource(Session(timestamp=0, agent="X", path="/tmp", session_id="s", tool_name="nope"))
        with patch.object(tail._tr, "read_transcript", return_value=_Transcript(["m1", "m2", "m3"])):
            src.poll()
        with patch.object(tail._tr, "read_transcript", return_value=_Transcript(["m2", "m3", "m4", "m5"])):
            self.assertEqual([m["text"] for m in src.poll()], ["m4", "m5"])


class TestDevinChainTail(unittest.TestCase):
    def test_tail_walk_matches_the_full_chain_end(self):
        import sqlite3
        from quiver.reports import transcripts as tr
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE message_nodes(row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INTEGER,"
            " parent_node_id INTEGER, chat_message TEXT, created_at INTEGER, UNIQUE(session_id, node_id));"
        )
        # Chain 1<-2<-3<-5 with a stale sibling 4 (an earlier streamed copy) under 3.
        for node, parent in [(1, None), (2, 1), (3, 2), (4, 3), (5, 3)]:
            conn.execute("INSERT INTO message_nodes(session_id, node_id, parent_node_id, chat_message, created_at)"
                         " VALUES ('s', ?, ?, ?, 0)", (node, parent, f"n{node}"))
        self.assertEqual(tr._devin_chain(conn, "s", 5), ["n1", "n2", "n3", "n5"])
        self.assertEqual(tr._devin_chain_tail(conn, "s", 5, 2), ["n3", "n5"])
        self.assertEqual(tr._devin_chain_tail(conn, "s", 5, 99), ["n1", "n2", "n3", "n5"])
        self.assertEqual(tr._devin_chain_tail(conn, "s", None, 99), ["n1", "n2", "n3", "n5"], "no head: newest node")


class TestDevinBoundedRead(unittest.TestCase):
    def test_result_keeps_its_tool_name_after_its_call_leaves_the_window(self):
        import sqlite3
        from quiver.reports import transcripts as tr
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "sessions.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE sessions(id TEXT PRIMARY KEY, main_chain_id INTEGER);"
            "CREATE TABLE message_nodes(row_id INTEGER PRIMARY KEY, session_id TEXT, node_id INTEGER,"
            " parent_node_id INTEGER, chat_message TEXT, created_at INTEGER, UNIQUE(session_id, node_id));"
        )
        rows = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "read", "arguments": "{}"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "file body"},
            {"role": "assistant", "content": "done"},
        ]
        for i, rec in enumerate(rows, start=1):
            conn.execute("INSERT INTO message_nodes(session_id, node_id, parent_node_id, chat_message, created_at)"
                         " VALUES ('s', ?, ?, ?, 0)", (i, i - 1 if i > 1 else None, json.dumps(rec)))
        conn.execute("INSERT INTO sessions VALUES ('s', 3)")
        conn.commit(); conn.close()
        session = Session(timestamp=0, agent="Devin", path="/tmp", session_id="s", tool_name="devin")
        with patch("os.path.expanduser", side_effect=lambda p: str(db) if "devin" in p else p):
            full = tr.read_transcript(session).messages
            window = tr.read_transcript_tail(session, 2).messages
        self.assertEqual([m.text for m in window], [m.text for m in full][-len(window):])
        self.assertTrue(any("read" in m.text for m in window), "the result still names its call")


class TestBackfillCap(unittest.TestCase):
    """A session seen for the first time backfills recent history only."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_jsonl_starts_on_a_whole_line_near_the_end(self):
        path = self.tmp / "t.jsonl"
        with open(path, "w") as fh:
            for i in range(200):
                fh.write(json.dumps(_claude_user(f"m{i:03d}")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        src.start_near_end(max_bytes=600)
        texts = [m["text"] for m in src.poll()]
        self.assertTrue(0 < len(texts) < 20)
        self.assertEqual(texts[-1], "m199")
        self.assertTrue(all(t.startswith("m") and len(t) == 4 for t in texts), "no partial first line")
        with open(path, "a") as fh:
            fh.write(json.dumps(_claude_user("new")) + "\n")
        self.assertEqual([m["text"] for m in src.poll()], ["new"])

    def test_alignment_walks_past_a_record_bigger_than_a_chunk(self):
        path = self.tmp / "t.jsonl"
        big = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * (tail._MAX_CHUNK * 2)}})
        with open(path, "w") as fh:
            fh.write(big + "\n")
            fh.write(json.dumps(_claude_user("after")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        src.start_near_end(max_bytes=tail._MAX_CHUNK * 2)  # starts inside the big record
        texts = []
        for _ in range(6):
            texts += [m["text"] for m in src.poll()]
        self.assertEqual(texts, ["after"])

    def test_truncation_before_the_aligned_read_keeps_the_first_record(self):
        path = self.tmp / "t.jsonl"
        with open(path, "w") as fh:
            for i in range(100):
                fh.write(json.dumps(_claude_user(f"m{i:03d}")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        src.start_near_end(max_bytes=600)
        path.write_text(json.dumps(_claude_user("fresh")) + "\n")  # replaced before any poll
        self.assertEqual([m["text"] for m in src.poll()], ["fresh"])

    def test_small_jsonl_reads_whole(self):
        path = self.tmp / "t.jsonl"
        path.write_text(json.dumps(_claude_user("only")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        src.start_near_end(max_bytes=1 << 20)
        self.assertEqual([m["text"] for m in src.poll()], ["only"])

    def test_snapshot_emits_the_newest_messages_first_time_only(self):
        src = tail.SnapshotSource(Session(timestamp=0, agent="X", path="/tmp", session_id="s", tool_name="nope"))
        src.start_near_end(max_messages=2)
        with patch.object(tail._tr, "read_transcript", return_value=_Transcript(["a", "b", "c", "d"])):
            self.assertEqual([m["text"] for m in src.poll()], ["c", "d"])
        with patch.object(tail._tr, "read_transcript", return_value=_Transcript(["a", "b", "c", "d", "e"])):
            self.assertEqual([m["text"] for m in src.poll()], ["e"])

    def test_fresh_tailer_backfills_only_the_tail(self):
        path = self.tmp / "t.jsonl"
        with open(path, "w") as fh:
            for i in range(300):
                fh.write(json.dumps(_claude_user(f"m{i:03d}")) + "\n")
        with patch.object(tail, "BACKFILL_BYTES", 500):
            t = tail.Tailer("k", _session_for(), tail.JsonlTailSource(path, "claude"),
                            self.tmp / "cursors", has_journal=False)
        msgs = t.poll(time.time(), active=True)
        self.assertTrue(0 < len(msgs) < 20)
        self.assertEqual(msgs[-1]["text"], "m299")


class TestOpencodeSource(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        import sqlite3
        self.db = self.tmp / "opencode.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute(
            "CREATE TABLE part (id TEXT, message_id TEXT, "
            "session_id TEXT, time_created INTEGER, data TEXT)"
        )
        conn.execute(
            "INSERT INTO message VALUES ('m1', ?)",
            (json.dumps({"role": "assistant"}),),
        )
        conn.commit()
        conn.close()

    def _add_part(self, pid, created, data):
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO part VALUES (?, 'm1', 'sess1', ?, ?)",
            (pid, created, json.dumps(data)),
        )
        conn.commit()
        conn.close()

    def test_incremental_parts(self):
        self._add_part("p1", 1000, {"type": "text", "text": "hello"})
        self._add_part("p2", 1001, {"type": "reasoning", "text": "hmm"})
        self._add_part("p3", 1002, {
            "type": "tool", "tool": "Bash", "id": "c1",
            "state": {"status": "completed", "output": "done",
                      "input": {"command": "ls"}},
        })
        src = tail.OpencodeSource(self.db, "sess1")
        out = src.poll()
        kinds = [m["kind"] for m in out]
        self.assertEqual(kinds, ["assistant", "thinking",
                                 "tool_use", "tool_result"])
        self.assertEqual(src.poll(), [])
        self._add_part("p4", 1003, {"type": "text", "text": "more"})
        out = src.poll()
        self.assertEqual([m["text"] for m in out], ["more"])

    def test_cursor_persistence(self):
        self._add_part("p1", 1000, {"type": "text", "text": "a"})
        src = tail.OpencodeSource(self.db, "sess1")
        src.poll()
        state = src.state()
        src2 = tail.OpencodeSource(self.db, "sess1")
        src2.restore(state)
        self.assertEqual(src2.poll(), [])
        self._add_part("p2", 1001, {"type": "text", "text": "b"})
        self.assertEqual(src2.poll()[0]["text"], "b")


class TestTailer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_cursor_round_trip(self):
        path = self.tmp / "t.jsonl"
        path.write_text(json.dumps(_claude_user("a")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        cursors = self.tmp / "cursors"
        t1 = tail.Tailer("k", _session_for(), src, cursors, False)
        msgs = t1.poll(time.time(), active=True)
        self.assertEqual(len(msgs), 1)

        src2 = tail.JsonlTailSource(path, "claude")
        t2 = tail.Tailer("k", _session_for(), src2, cursors, True)
        self.assertEqual(t2.poll(time.time(), True), [])
        path.write_text(
            path.read_text() + json.dumps(_claude_user("b")) + "\n"
        )
        self.assertEqual(t2.poll(time.time(), True)[0]["text"], "b")

    def test_skip_to_end_when_journal_exists_no_cursor(self):
        path = self.tmp / "t.jsonl"
        path.write_text(json.dumps(_claude_user("old")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        t = tail.Tailer("k", _session_for(), src, self.tmp, has_journal=True)
        self.assertEqual(t.poll(time.time(), True), [])

    def test_cursor_commits_only_after_emit(self):
        # The cursor must lag the journal write: a crash between poll
        # and commit replays events (duplicates) instead of losing them.
        path = self.tmp / "t.jsonl"
        path.write_text(json.dumps(_claude_user("a")) + "\n")
        cursors = self.tmp / "cursors"
        t = tail.Tailer(
            "claude:k", _session_for(),
            tail.JsonlTailSource(path, "claude"), cursors, False,
        )
        msgs = t.poll(time.time(), active=True)
        self.assertEqual(len(msgs), 1)
        self.assertFalse(t._cursor_path.exists())  # not yet committed
        t.commit_cursor()
        self.assertTrue(t._cursor_path.exists())
        # Second poll with nothing new keeps the cursor settled.
        self.assertEqual(t.poll(time.time(), active=False), [])
        t.commit_cursor()


def _session_for():
    return Session(timestamp=0, agent="Claude Code", path="/tmp",
                   session_id="", tool_name="claude")


class TestTailManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.events = []

    def _emit(self, key, ev):
        self.events.append((key, ev))

    def _manager(self):
        return tail.TailManager(self._emit, "mbp", home=self.tmp)

    def test_sync_creates_tailer_and_status(self):
        path = self.tmp / "s.jsonl"
        path.write_text(json.dumps(_claude_user("hi")) + "\n")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(path), tool_name="claude")
        mgr = self._manager()
        mgr.sync([("claude:s1", sess, "active")])
        self.assertIn("claude:s1", mgr.tailers)
        types = [ev["type"] for _k, ev in self.events]
        self.assertIn("session.status", types)
        n = mgr.tick(time.time())
        self.assertEqual(n, 1)
        self.assertEqual(self.events[-1][1]["data"]["text"], "hi")

    def test_notify_only_on_transition(self):
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        with patch.object(tail, "make_source", return_value=None):
            # Source unresolvable: no tailer, but status transitions still
            # emit through sync? No — tailer-less keys are skipped.
            mgr.sync([("claude:x", sess, "active")])
        notify = [ev for evs in self.events for ev in [evs[1]]
                  if ev["type"] == "notify"]
        self.assertEqual(notify, [])

    def test_removed_session_drops_tailer(self):
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        with patch.object(tail, "make_source", return_value=None):
            mgr.sync([("claude:x", sess, "")])
        self.assertNotIn("claude:x", mgr.tailers)

    def test_notify_transitions(self):
        src = tail.JsonlTailSource(self.tmp / "x.jsonl", "claude")
        (self.tmp / "x.jsonl").write_text("")
        mgr = self._manager()
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:x", sess, "done")])
            notify = [e for _k, e in self.events if e["type"] == "notify"]
            self.assertEqual(notify, [])
            mgr.sync([("claude:x", sess, "active")])
            mgr.sync([("claude:x", sess, "followup")])
            notify = [e for _k, e in self.events if e["type"] == "notify"]
            self.assertEqual(notify[-1]["data"]["kind"], "needs_you")
            mgr.sync([("claude:x", sess, "active")])
            mgr.sync([("claude:x", sess, "done")])
            notify = [e for _k, e in self.events if e["type"] == "notify"]
            self.assertEqual(notify[-1]["data"]["kind"], "finished")
            mgr.sync([("claude:x", sess, "error")])
            notify = [e for _k, e in self.events if e["type"] == "notify"]
            self.assertEqual(notify[-1]["data"]["kind"], "error")

    def test_jsonl_truncation_restarts(self):
        path = self.tmp / "z.jsonl"
        path.write_text(json.dumps(_claude_user("a")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        src.poll()
        self.assertGreater(src.offset, 0)
        path.write_text(json.dumps(_claude_user("b")) + "\n")
        src.offset = 10**9  # simulate: file shrank below offset
        out = src.poll()
        self.assertEqual([m["text"] for m in out], ["b"])
        self.assertGreater(src.offset, 0)

    def test_compaction_and_interrupted_decode(self):
        compact = {"type": "system", "subtype": "compact_boundary"}
        out = tail.decode_record("claude", compact)
        self.assertEqual(out[0]["kind"], "compaction")
        aborted = {"type": "system", "subtype": "turn_aborted",
                   "content": "turn aborted"}
        out = tail.decode_record("claude", aborted)
        self.assertEqual(out[0]["kind"], "system")

    def test_opencode_skip_to_end(self):
        self._add_part_skip()
        src = tail.OpencodeSource(self.db2, "sess1")
        src.skip_to_end()
        self.assertGreater(src.cursor, 0)
        self.assertEqual(src.poll(), [])

    def _add_part_skip(self):
        import sqlite3
        self.db2 = self.tmp / "oc2.db"
        conn = sqlite3.connect(self.db2)
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute(
            "CREATE TABLE part (id TEXT, message_id TEXT, "
            "session_id TEXT, time_created INTEGER, data TEXT)"
        )
        conn.execute("INSERT INTO message VALUES ('m', ?)",
                     (json.dumps({"role": "assistant"}),))
        conn.execute("INSERT INTO part VALUES ('p', 'm', 'sess1', 5000, ?)",
                     (json.dumps({"type": "text", "text": "x"}),))
        conn.commit()
        conn.close()

    def test_codex_decode_variants(self):
        out = tail.decode_record("codex", {
            "type": "response_item",
            "timestamp": 1_700_000_000.0,
            "payload": {"type": "message", "role": "user",
                        "content": [{"type": "input_text",
                                     "text": "yo"}]},
        })
        self.assertEqual(out[0]["kind"], "user")
        out = tail.decode_record("codex", {
            "type": "response_item",
            "payload": {"type": "function_call_output",
                        "call_id": "c9", "output": "ok"},
        })
        self.assertEqual(out[0]["kind"], "tool_result")
        self.assertEqual(out[0]["result_ref"], "c9")
        out = tail.decode_record("codex", {
            "type": "event_msg",
            "payload": {"type": "turn_aborted"},
        })
        self.assertEqual(out[0]["kind"], "system")
        out = tail.decode_record("codex", {
            "type": "response_item",
            "payload": {"type": "ghost"},
        })
        self.assertEqual(out, [])

    def test_generic_nested_blocks(self):
        rec = {"message": {"role": "assistant",
                           "content": [{"type": "thinking",
                                        "thinking": "hmm"},
                                       {"type": "text",
                                        "text": "answer"}]}}
        out = tail.decode_record("droid", rec)
        self.assertEqual(
            [m["kind"] for m in out], ["thinking", "assistant"]
        )

    def test_resolve_jsonl_claude_direct_and_glob(self):
        direct = self.tmp / "abc.jsonl"
        direct.write_text("{}\n")
        sess = Session(timestamp=0, agent="a", path="/p",
                       session_id=str(direct), tool_name="claude")
        self.assertEqual(tail.resolve_jsonl_path(sess), direct)

        proj = self.tmp / "claude-home" / "projects" / "-p"
        proj.mkdir(parents=True)
        nested = proj / "sid9.jsonl"
        nested.write_text("{}\n")
        sess = Session(timestamp=0, agent="a", path="/p",
                       session_id="sid9", tool_name="claude")
        real_expand = tail.expand_user
        with patch("quiver.remote.tail.expand_user",
                   side_effect=lambda p: (
                       self.tmp / "claude-home" / "projects"
                   ) if p == "~/.claude/projects" else real_expand(p)):
            self.assertEqual(tail.resolve_jsonl_path(sess), nested)

    def test_reresolve_follows_new_transcript_path(self):
        # A resumed session's transcript can move (claude --resume from
        # a different cwd); sync must swap the source, not tail the dead
        # file.
        path_a = self.tmp / "a.jsonl"
        path_a.write_text(json.dumps(_claude_user("a0")) + "\n")
        path_b = self.tmp / "b.jsonl"
        path_b.write_text(json.dumps(_claude_user("b0")) + "\n")
        sess_a = Session(timestamp=0, agent="Claude Code", path="/tmp",
                         session_id=str(path_a), tool_name="claude")
        sess_b = Session(timestamp=0, agent="Claude Code", path="/tmp",
                         session_id=str(path_b), tool_name="claude")
        mgr = self._manager()
        mgr.sync([("claude:s", sess_a, "active")])
        mgr.tick(time.time())
        self.assertEqual(self.events[-1][1]["data"]["text"], "a0")
        mgr.sync([("claude:s", sess_b, "active")])
        src = mgr.tailers["claude:s"].source
        self.assertEqual(getattr(src, "path", None), path_b)
        with open(path_a, "a") as fh:
            fh.write(json.dumps(_claude_user("stale")) + "\n")
        with open(path_b, "a") as fh:
            fh.write(json.dumps(_claude_user("b1")) + "\n")
        mgr.tick(time.time() + 2)  # past next_poll_at
        texts = [e["data"]["text"] for _k, e in self.events
                 if e["type"] == "message.appended"]
        self.assertNotIn("stale", texts)
        self.assertIn("b1", texts)

    def test_ensure_on_demand_tailer_survives_cap(self):
        path = self.tmp / "s.jsonl"
        path.write_text(json.dumps(_claude_user("hi")) + "\n")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(path), tool_name="claude")
        mgr = self._manager()
        mgr.max_sessions = 1
        sess2 = Session(timestamp=0, agent="Claude Code", path="/tmp",
                        session_id="", tool_name="claude")
        with patch.object(tail, "make_source", return_value=None):
            mgr.sync([("claude:hot", sess2, "active")])
        # The capped-out session gets a tailer when a client opens it.
        mgr.ensure("claude:cold", sess, "done")
        self.assertIn("claude:cold", mgr.tailers)
        # A later background sync must not drop the on-demand tailer.
        mgr.sync([("claude:hot", sess2, "active"),
                  ("claude:cold", sess, "done")])
        self.assertIn("claude:cold", mgr.tailers)
        # ...but it does go away when the session leaves the catalog.
        mgr.sync([("claude:hot", sess2, "active")])
        self.assertNotIn("claude:cold", mgr.tailers)

    def test_make_source_dispatch(self):
        sess = _session_for()
        sess.tool_name = "opencode"
        sess.session_id = "s"
        with patch("quiver.remote.tail.expand_user",
                   return_value=self.tmp / "missing.db"):
            # db missing → falls back to the snapshot reader
            self.assertIsInstance(
                tail.make_source(sess), tail.SnapshotSource
            )
        with patch("quiver.remote.tail.expand_user",
                   return_value=self.tmp):
            db = self.tmp / "oc.db"
            import sqlite3 as _sq
            _sq.connect(db).close()
            self.assertIsInstance(
                tail.make_source(sess), tail.OpencodeSource
            )
        sess.tool_name = "freebuff"
        with patch.object(tail, "resolve_jsonl_path",
                          return_value=None):
            src = tail.make_source(sess)
        self.assertIsInstance(src, tail.SnapshotSource)

    def test_last_append_tracked(self):
        src = tail.JsonlTailSource(self.tmp / "y.jsonl", "claude")
        (self.tmp / "y.jsonl").write_text(
            json.dumps(_claude_user("hi")) + "\n"
        )
        mgr = self._manager()
        sess = _session_for()
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:y", sess, "active")])
            self.assertIsNone(mgr.last_append_at("claude:y"))
            n = mgr.tick(time.time())
        self.assertEqual(n, 1)
        self.assertIsNotNone(mgr.last_append_at("claude:y"))


class TestApprovalWatcher(unittest.TestCase):
    """TailManager._poll_approvals: pane prompt → requested/resolved."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.events = []
        self.mgr = tail.TailManager(
            lambda k, ev: self.events.append((k, ev)), "mbp",
            home=self.tmp)
        # Only the key matters to the watcher; no transcript needed.
        self.mgr.tailers["k1"] = object()
        self._t = time.time()

    def _poll(self, pane="", meta=None):
        # Each call lands past the PANE_APPROVAL_S throttle window.
        self._t += 10
        with patch.object(
            tail.supervise, "meta_for", return_value=meta
        ), patch.object(
            tail.supervise, "capture_pane", return_value=pane
        ):
            self.mgr._poll_approvals(self._t)

    def _approval_events(self):
        return [ev for _k, ev in self.events
                if ev["type"].startswith("approval.")]

    def test_prompt_appears_emits_requested(self):
        from quiver.remote import supervise
        pane = "Do you want to proceed?\n1. Yes\n2. No\nesc to cancel"
        self._poll(pane, meta={"mode": "tmux", "tmux": "qv-k1"})
        evs = self._approval_events()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["type"], "approval.requested")
        rid = evs[0]["data"]["request_id"]
        self.assertTrue(rid.startswith("req-"))
        self.assertEqual(supervise.pending_approval("k1"), rid)
        self.assertIn("Do you want", evs[0]["data"]["prompt"])

    def test_prompt_same_pane_no_duplicate(self):
        pane = "Do you want to proceed?\n1. Yes\n2. No"
        self._poll(pane, meta={"mode": "tmux"})
        self._poll(pane, meta={"mode": "tmux"})
        self.assertEqual(len(self._approval_events()), 1)

    def test_prompt_disappears_emits_resolved(self):
        from quiver.remote import supervise
        pane = "Do you want to proceed?\n1. Yes\n2. No"
        self._poll(pane, meta={"mode": "tmux"})
        rid = self._approval_events()[0]["data"]["request_id"]
        self._poll("❯ idle", meta={"mode": "tmux"})
        evs = self._approval_events()
        self.assertEqual(evs[-1]["type"], "approval.resolved")
        self.assertEqual(evs[-1]["data"]["request_id"], rid)
        self.assertEqual(evs[-1]["data"]["source"], "dismissed")
        self.assertIsNone(supervise.pending_approval("k1"))

    def test_detach_resolves_outstanding(self):
        from quiver.remote import supervise
        pane = "Do you want to proceed?\n1. Yes\n2. No"
        self._poll(pane, meta={"mode": "tmux"})
        rid = self._approval_events()[0]["data"]["request_id"]
        self._poll(meta=None)  # detached — registry row gone
        evs = self._approval_events()
        self.assertEqual(evs[-1]["type"], "approval.resolved")
        self.assertEqual(evs[-1]["data"]["source"], "detached")
        self.assertIsNone(supervise.pending_approval("k1"))

    def test_headless_meta_skips_pane_check(self):
        # mode != tmux → never captures a pane, never emits.
        with patch.object(tail.supervise, "meta_for",
                          return_value={"mode": "headless"}), \
             patch.object(tail.supervise, "capture_pane") as cap:
            self.mgr._poll_approvals(time.time() + 10)
        cap.assert_not_called()
        self.assertEqual(self._approval_events(), [])

    def test_none_home_uses_default_registry(self):
        # HubState() on the documented CLI path passes home=None — the
        # watcher must still consult the default remote dir, not skip.
        mgr = tail.TailManager(lambda k, ev: None, "mbp", home=None)
        mgr.tailers["k1"] = object()
        with patch.object(tail.supervise, "meta_for") as mf:
            mgr._poll_approvals(time.time() + 10)
        mf.assert_called_once_with(None, "k1")

    def test_prompt_swap_rotates_request_id(self):
        # A menu answered locally then replaced by another one must not
        # keep the old request_id — a delayed reply for prompt A must
        # never answer prompt B.
        from quiver.remote import supervise
        meta = {"mode": "tmux"}
        self._poll("Do you want to run ls?\n1. Yes\n2. No", meta=meta)
        evs = self._approval_events()
        rid_a = evs[0]["data"]["request_id"]
        self._poll("Do you want to run rm -rf?\n1. Yes\n2. No", meta=meta)
        evs = self._approval_events()
        self.assertEqual(evs[-2]["type"], "approval.resolved")
        self.assertEqual(evs[-2]["data"]["request_id"], rid_a)
        self.assertEqual(evs[-2]["data"]["source"], "superseded")
        self.assertEqual(evs[-1]["type"], "approval.requested")
        rid_b = evs[-1]["data"]["request_id"]
        self.assertNotEqual(rid_a, rid_b)
        self.assertEqual(supervise.pending_approval("k1"), rid_b)

    def test_poll_throttled(self):
        meta = {"mode": "tmux"}
        self._poll("", meta=meta)
        with patch.object(tail.supervise, "meta_for") as mf:
            # A second call inside the 3s window doesn't even look.
            self.mgr._poll_approvals(self._t + 1)
        mf.assert_not_called()

    def test_prompt_line_fallback(self):
        pane = "x\ny"  # no keyword line → generic prompt text
        self._poll("esc to cancel\n" + pane, meta={"mode": "tmux"})
        ev = self._approval_events()[0]
        self.assertEqual(ev["data"]["prompt"], "Approval requested")


class TestApprovalPromptLine(unittest.TestCase):
    def test_skips_blank_and_unmatched_lines(self):
        pane = "\n   \nsome random output\nDo you want to allow this?\n❯ yes"
        self.assertEqual(
            tail._approval_prompt_line(pane), "Do you want to allow this?")

    def test_fallback_when_no_marker(self):
        self.assertEqual(
            tail._approval_prompt_line("plain\noutput\n"),
            "Approval requested")
        self.assertEqual(tail._approval_prompt_line(""), "Approval requested")


if __name__ == "__main__":
    unittest.main()
