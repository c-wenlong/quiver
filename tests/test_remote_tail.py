"""tail.py: record decoders, byte-offset source, snapshot diff, manager."""

import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.remote import tail
from quiver.sessions.models import Session

from tests._tempdir import temp_dir


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
        self.tmp = temp_dir(self)
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

    def test_a_deeply_nested_record_is_skipped(self):
        deep = ("[" * 100_000).encode()
        with open(self.path, "ab") as fh:
            fh.write(deep + b"\n")
        self._write([_claude_user("after")])
        src = tail.JsonlTailSource(self.path, "claude")
        self.assertEqual([m["text"] for m in src.poll()], ["after"])

    def test_skipping_a_vanished_oversized_record_does_not_raise(self):
        src = tail.JsonlTailSource(self.path, "claude")
        self.path.unlink(missing_ok=True)
        self.assertEqual(src._skip_oversized(), src.offset + tail._MAX_CHUNK)

    def test_truncate_to_a_prefix_emits_nothing_until_appends(self):
        # A file cut in place to a strict prefix of what was read
        # (copytruncate before the next write lands) holds no new
        # content — re-reading it would re-emit old messages.
        self._write([_claude_user("one"), _claude_user("two"),
                     _claude_user("three")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        first_two = b"".join(
            (json.dumps(_claude_user(t)) + "\n").encode()
            for t in ("one", "two"))
        self.path.write_bytes(first_two)
        self.assertEqual(src.poll(), [])
        # Appends after the cut still stream — the stream continues.
        self._write([_claude_user("four")])
        self.assertEqual([m["text"] for m in src.poll()], ["four"])

    def test_copytruncate_keeps_head_emits_only_the_tail(self):
        # Copytruncate: the head survives, the rest was cut, and the
        # writer's new records land right after it. Only that tail is
        # new — the head must not come through again.
        self._write([
            _claude_user("h" * tail._HEAD_SAMPLE),
            _claude_user("old"),
            _claude_user("older"),
        ])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        kept = self.path.read_bytes()[: tail._HEAD_SAMPLE]
        self.path.write_bytes(kept)
        self.assertEqual(src.poll(), [])
        # Appends after the cut still stream — the stream continues.
        self._write([_claude_user("fresh")])
        self.assertEqual([m["text"] for m in src.poll()], ["fresh"])

    def test_truncate_past_the_head_sample_replays_nothing(self):
        # A file cut in place to a prefix LONGER than the head sample
        # keeps several complete records the fingerprint can't cover;
        # resuming right after the sample would replay them (and start
        # mid-record), so the cut point is where the tail resumes.
        self._write([
            _claude_user("h" * tail._HEAD_SAMPLE),
            _claude_user("kept-a"), _claude_user("kept-b"),
            _claude_user("kept-c"), _claude_user("gone"),
        ])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        lines = self.path.read_bytes().splitlines(keepends=True)
        kept = b"".join(lines[:4])  # the big record + three more
        self.assertGreater(len(kept), tail._HEAD_SAMPLE)
        self.path.write_bytes(kept)
        self.assertEqual(src.poll(), [])
        self._write([_claude_user("new")])
        self.assertEqual([m["text"] for m in src.poll()], ["new"])

    def test_rewrite_with_a_new_head_reemits(self):
        # A rotated or rewritten file whose head differs is a new
        # stream: it is re-read from the start.
        self._write([_claude_user("old1"), _claude_user("old2")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        self.path.write_text(json.dumps(_claude_user("new1")) + "\n")
        out = src.poll()
        self.assertEqual([m["text"] for m in out], ["new1"])

    def test_head_fingerprint_survives_a_cursor_round_trip(self):
        # The truncation check re-arms on restore: the head hash in the
        # cursor is verified against the file, so a copytruncate across
        # a restart is still not re-emitted.
        self._write([_claude_user("one"), _claude_user("two")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        src2 = tail.JsonlTailSource(self.path, "claude")
        src2.restore(src.state())
        first = (json.dumps(_claude_user("one")) + "\n").encode()
        self.path.write_bytes(first)
        self.assertEqual(src2.poll(), [])

    def test_restore_rejects_a_head_that_changed_while_down(self):
        # A file rewritten while the tailer was down fails the head
        # check: the stream is re-read instead of resumed mid-file.
        self._write([_claude_user("one"), _claude_user("two")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        state = src.state()
        self.path.write_text(json.dumps(_claude_user("replaced")) + "\n")
        src2 = tail.JsonlTailSource(self.path, "claude")
        src2.restore(state)
        self.assertEqual(
            [m["text"] for m in src2.poll()], ["replaced"])

    def test_restore_verifies_a_file_cut_shorter_than_the_sample(self):
        # Save the cursor, then cut the file to a prefix shorter than
        # the stored head before restoring: every remaining byte still
        # matches the stored prefix, so the kept record must not
        # replay. A hash of the full sample could not check this — the
        # file never reaches head_len — which is why the bytes ride in
        # the cursor.
        self._write([_claude_user("one"), _claude_user("two")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        state = src.state()
        first = (json.dumps(_claude_user("one")) + "\n").encode()
        self.path.write_bytes(first)
        src2 = tail.JsonlTailSource(self.path, "claude")
        src2.restore(state)
        self.assertEqual(src2.poll(), [])
        self._write([_claude_user("three")])
        self.assertEqual([m["text"] for m in src2.poll()], ["three"])

    def test_restore_after_truncate_to_a_prefix_past_the_sample(self):
        # Same cut while down, but the surviving prefix is longer than
        # the head sample: the file is checked only as far as the
        # stored bytes reach.
        self._write([
            _claude_user("h" * tail._HEAD_SAMPLE),
            _claude_user("kept"), _claude_user("kept2"),
            _claude_user("dropped"),
        ])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        state = src.state()
        lines = self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b"".join(lines[:3]))  # drop the last record
        src2 = tail.JsonlTailSource(self.path, "claude")
        src2.restore(state)
        self.assertEqual(src2.poll(), [])
        self._write([_claude_user("new")])
        self.assertEqual([m["text"] for m in src2.poll()], ["new"])


def _at(rec, iso):
    return dict(rec, timestamp=iso)


class TestThinkingDuration(unittest.TestCase):
    """``duration_s`` on thinking: the gap from the record before."""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.path = self.tmp / "t.jsonl"

    def _write(self, records):
        with open(self.path, "a") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def _thinking(self, out):
        return [m for m in out if m["kind"] == "thinking"]

    def test_claude_timed_from_the_record_before(self):
        self._write([
            _at(_claude_user("go"), "2026-10-30T13:35:21.000Z"),
            {"type": "api-request", "timestamp": "2026-10-30T13:35:21.500Z"},
            # Claude Code writes an empty thinking block first.
            _at(_claude_assistant([{"type": "thinking", "thinking": ""}]),
                "2026-10-30T13:35:33.900Z"),
            _at(_claude_assistant([{"type": "thinking", "thinking": "hmm"}]),
                "2026-10-30T13:35:34.000Z"),
            _at(_claude_assistant([{"type": "text", "text": "done"}]),
                "2026-10-30T13:35:40.000Z"),
        ])
        out = tail.JsonlTailSource(self.path, "claude").poll()
        [thought] = self._thinking(out)
        self.assertEqual(thought["duration_s"], 12.5)
        self.assertNotIn("duration_s", out[-1])

    def test_unknown_when_nothing_came_before_or_blocks_mix(self):
        self._write([
            _at(_claude_assistant([{"type": "thinking", "thinking": "a"}]),
                "2026-10-30T13:00:00Z"),
            _at(_claude_user("go"), "2026-10-30T13:00:05Z"),
            # One record holding the whole reply: no split to time.
            _at(_claude_assistant([{"type": "thinking", "thinking": "b"},
                                   {"type": "text", "text": "ok"}]),
                "2026-10-30T13:00:09Z"),
        ])
        out = tail.JsonlTailSource(self.path, "claude").poll()
        for thought in self._thinking(out):
            self.assertNotIn("duration_s", thought)

    def test_untimed_records_and_other_harnesses_leave_it_out(self):
        self._write([
            _claude_user("go"),
            _claude_assistant([{"type": "thinking", "thinking": "a"}]),
        ])
        out = tail.JsonlTailSource(self.path, "claude").poll()
        self.assertNotIn("duration_s", self._thinking(out)[0])
        path = self.tmp / "pi.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in [
            _at(_claude_user("go"), "2026-10-30T13:00:00Z"),
            _at(_claude_assistant([{"type": "thinking", "thinking": "a"}]),
                "2026-10-30T13:00:04Z"),
        ]))
        out = tail.JsonlTailSource(path, "pi").poll()
        self.assertNotIn("duration_s", self._thinking(out)[0])

    def test_codex_skips_the_reasoning_companion_events(self):
        self._write([
            {"timestamp": "2026-10-31T03:50:10.000Z", "type": "response_item",
             "payload": {"type": "function_call_output", "call_id": "c1",
                         "output": "ok"}},
            {"timestamp": "2026-10-31T03:50:21.900Z", "type": "event_msg",
             "payload": {"type": "agent_reasoning", "text": "plan"}},
            {"timestamp": "2026-10-31T03:50:22.100Z", "type": "event_msg",
             "payload": {"type": "item_completed",
                         "item": {"type": "Reasoning"}}},
            {"timestamp": "2026-10-31T03:50:22.600Z", "type": "response_item",
             "payload": {"type": "reasoning",
                         "summary": [{"type": "summary_text",
                                      "text": "plan"}]}},
        ])
        out = tail.JsonlTailSource(self.path, "codex").poll()
        self.assertEqual(self._thinking(out)[0]["duration_s"], 12.6)

    def test_mark_survives_a_cursor_restore(self):
        self._write([_at(_claude_user("go"), "2026-10-30T13:00:00Z")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        state = json.loads(json.dumps(src.state()))
        self._write([
            _at(_claude_assistant([{"type": "thinking", "thinking": "a"}]),
                "2026-10-30T13:01:05Z"),
        ])
        src2 = tail.JsonlTailSource(self.path, "claude")
        self.assertTrue(src2.restore(state))
        self.assertEqual(self._thinking(src2.poll())[0]["duration_s"], 65.0)

    def test_untimed_shown_record_clears_the_mark(self):
        self._write([
            _at(_claude_assistant([{"type": "text", "text": "hi"}]),
                "2026-10-30T13:00:00Z"),
            {"type": "agent-name", "name": "x"},  # bookkeeping: kept
            _claude_user("an untimed prompt"),
            _at(_claude_assistant([{"type": "thinking", "thinking": "a"}]),
                "2026-10-30T15:00:00Z"),
        ])
        out = tail.JsonlTailSource(self.path, "claude").poll()
        self.assertNotIn("duration_s", self._thinking(out)[0])
        path = self.tmp / "b.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in [
            _at(_claude_user("go"), "2026-10-30T13:00:00Z"),
            {"type": "custom-title", "customTitle": "t"},
            _at(_claude_assistant([{"type": "thinking", "thinking": "a"}]),
                "2026-10-30T13:00:03Z"),
        ]))
        out = tail.JsonlTailSource(path, "claude").poll()
        self.assertEqual(self._thinking(out)[0]["duration_s"], 3.0)

    def test_mark_from_a_replaced_file_is_dropped(self):
        self._write([_at(_claude_user("go"), "2026-10-30T13:00:00Z")])
        src = tail.JsonlTailSource(self.path, "claude")
        src.poll()
        state = src.state()
        self.path.write_text(json.dumps(
            _at(_claude_user("other file"), "2026-10-30T09:00:00Z")) + "\n")
        reused = tail.JsonlTailSource(self.path, "claude")
        self.assertFalse(reused.restore(state))
        reused.skip_to_end()
        self._write([
            _at(_claude_assistant([{"type": "thinking", "thinking": "a"}]),
                "2026-10-30T13:01:05Z"),
        ])
        self.assertNotIn("duration_s", self._thinking(reused.poll())[0])
        # A rotation found mid-poll restarts the stream with no mark.
        src2 = tail.JsonlTailSource(self.path, "claude")
        src2.poll()
        self.assertIsNotNone(src2.state()["since"])
        self.path.write_text(json.dumps(
            _at(_claude_assistant([{"type": "thinking", "thinking": "b"}]),
                "2026-10-30T13:02:00Z")) + "\n")
        self.assertNotIn("duration_s", self._thinking(src2.poll())[0])

    def test_opencode_spans(self):
        self.assertEqual(tail._span_ms({"start": 1000, "end": 13_250}), 12.2)
        self.assertEqual(
            tail._span_ms({"created": 1000, "completed": 66_000}), 65.0)
        self.assertIsNone(tail._span_ms({"start": 1000}))
        self.assertIsNone(tail._span_ms({"start": 5000, "end": 1000}))
        self.assertIsNone(tail._span_ms(None))


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
        self.tmp = temp_dir(self)
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
            # The size changes, so this needs no wait for a new mtime.
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
        tmp = temp_dir(self)
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
        self.tmp = temp_dir(self)

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
        self.tmp = temp_dir(self)
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
        self._add_part("p2", 1001, {"type": "reasoning", "text": "hmm",
                                    "time": {"start": 1001, "end": 4501}})
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
        self.assertEqual(out[1]["duration_s"], 3.5)
        self.assertNotIn("duration_s", out[0])
        self.assertEqual(src.poll(), [])
        self._add_part("p4", 1003, {"type": "text", "text": "more"})
        out = src.poll()
        self.assertEqual([m["text"] for m in out], ["more"])

    def test_start_near_end_reads_only_recent_parts(self):
        # First sight of a long session used to journal all of it.
        for i in range(5):
            self._add_part(f"p{i}", 1000 + i, {"type": "text", "text": f"t{i}"})
        src = tail.OpencodeSource(self.db, "sess1")
        src.start_near_end(2)
        self.assertEqual([m["text"] for m in src.poll()], ["t3", "t4"])
        self._add_part("p5", 1005, {"type": "text", "text": "t5"})
        self.assertEqual([m["text"] for m in src.poll()], ["t5"])

    def test_start_near_end_on_a_short_session_reads_it_all(self):
        self._add_part("p1", 1000, {"type": "text", "text": "a"})
        src = tail.OpencodeSource(self.db, "sess1")
        src.start_near_end(2)
        self.assertEqual([m["text"] for m in src.poll()], ["a"])

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

    def test_same_millisecond_parts_all_delivered(self):
        # The cursor is a (time_created, id) position: a part sharing
        # the last row's millisecond is new, not a replay to skip.
        self._add_part("p1", 1000, {"type": "text", "text": "one"})
        src = tail.OpencodeSource(self.db, "sess1")
        self.assertEqual([m["text"] for m in src.poll()], ["one"])
        self._add_part("p2", 1000, {"type": "text", "text": "two"})
        self.assertEqual([m["text"] for m in src.poll()], ["two"])
        # A row that sorts behind the cursor stays behind it.
        self._add_part("p0", 1000, {"type": "text", "text": "backfill"})
        self.assertEqual(src.poll(), [])

    def test_same_millisecond_rows_emit_in_id_order(self):
        self._add_part("p2", 1000, {"type": "text", "text": "late"})
        self._add_part("p1", 1000, {"type": "text", "text": "early"})
        src = tail.OpencodeSource(self.db, "sess1")
        self.assertEqual(
            [m["text"] for m in src.poll()], ["early", "late"])

    def test_cursor_stays_before_unread_rows_on_error(self):
        # An error mid-iteration must not move the persisted cursor
        # past rows it never emitted.
        import sqlite3
        self._add_part("p1", 1000, {"type": "text", "text": "one"})
        self._add_part("p2", 1001, {"type": "text", "text": "two"})
        src = tail.OpencodeSource(self.db, "sess1")
        real = tail.OpencodeSource._role_for
        calls = []

        def flaky(self_, conn, mid):
            calls.append(mid)
            if len(calls) == 2:
                raise sqlite3.Error("db gone")
            return real(self_, conn, mid)

        with patch.object(tail.OpencodeSource, "_role_for", flaky):
            out = src.poll()
        self.assertEqual([m["text"] for m in out], ["one"])
        self.assertEqual(src.cursor, (1000, "p1"))
        self.assertEqual([m["text"] for m in src.poll()], ["two"])

    def test_lookup_error_retries_the_row_not_the_role(self):
        # A SQLite error inside _role_for's own message lookup must
        # propagate: caching the "assistant" fallback would pin a user
        # message's role forever once the cursor moved past the row.
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO message VALUES ('mu', ?)",
                     (json.dumps({"role": "user"}),))
        conn.execute(
            "INSERT INTO part VALUES ('p1', 'mu', 'sess1', 1000, ?)",
            (json.dumps({"type": "text", "text": "user words"}),))
        conn.execute(
            "INSERT INTO part VALUES ('p2', 'm1', 'sess1', 1001, ?)",
            (json.dumps({"type": "text", "text": "reply"}),))
        conn.commit()
        conn.close()

        src = tail.OpencodeSource(self.db, "sess1")
        real_open = src._open
        break_lookup = {"on": True}

        class FlakyConn:
            """Proxies the read-only conn; dies on message lookups."""

            def __init__(self, real):
                self._real = real

            def execute(self, sql, params=()):
                if break_lookup["on"] and "FROM message" in sql:
                    raise sqlite3.Error("database is locked")
                return self._real.execute(sql, params)

            def close(self):
                self._real.close()

        def flaky_open():
            conn = real_open()
            return None if conn is None else FlakyConn(conn)

        src._open = flaky_open
        # The first part's lookup dies: nothing consumed, cursor unmoved.
        self.assertEqual(src.poll(), [])
        self.assertEqual(src.cursor, (0, ""))
        break_lookup["on"] = False
        out = src.poll()
        self.assertEqual(
            [(m["kind"], m["text"]) for m in out],
            [("user", "user words"), ("assistant", "reply")],
        )

    def test_scalar_cursor_from_before_the_fix_still_restores(self):
        # A cursor file holding the old bare time_created resumes at
        # that millisecond's start: the rows there are re-read once —
        # duplicates, never a skip.
        self._add_part("p1", 1000, {"type": "text", "text": "one"})
        src = tail.OpencodeSource(self.db, "sess1")
        src.restore({"source": "opencode", "cursor": 1000})
        self.assertEqual(src.cursor, (1000, ""))
        self._add_part("p2", 1000, {"type": "text", "text": "two"})
        self.assertEqual(
            [m["text"] for m in src.poll()], ["one", "two"])


class TestTailer(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)

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

    def test_cursor_for_another_path_does_not_replay_history(self):
        # A resume in another folder moves the transcript: the old
        # cursor names the old path. It used to leave the offset at 0,
        # so the whole transcript was journaled again.
        old = self.tmp / "old.jsonl"
        old.write_text(json.dumps(_claude_user("a")) + "\n")
        cursors = self.tmp / "cursors"
        t1 = tail.Tailer("k", _session_for(),
                         tail.JsonlTailSource(old, "claude"), cursors, False)
        t1.poll(time.time(), True)
        t1.commit_cursor()
        moved = self.tmp / "moved.jsonl"
        moved.write_text(json.dumps(_claude_user("a")) + "\n"
                         + json.dumps(_claude_user("b")) + "\n")
        src = tail.JsonlTailSource(moved, "claude")
        t2 = tail.Tailer("k", _session_for(), src, cursors, True)
        self.assertEqual(t2.poll(time.time(), True), [])
        with open(moved, "a") as fh:
            fh.write(json.dumps(_claude_user("c")) + "\n")
        self.assertEqual([m["text"] for m in t2.poll(time.time(), True)],
                         ["c"])

    def test_unverified_cursor_without_journal_backfills_recent(self):
        # No journal yet: a cursor that fails its head check starts
        # near the end like a first sight, not at byte 0.
        path = self.tmp / "t.jsonl"
        path.write_text(json.dumps(_claude_user("a")) + "\n")
        cursors = self.tmp / "cursors"
        t1 = tail.Tailer("k", _session_for(),
                         tail.JsonlTailSource(path, "claude"), cursors, False)
        t1.poll(time.time(), True)
        t1.commit_cursor()
        path.write_text("".join(json.dumps(_claude_user(f"m{i}")) + "\n"
                                for i in range(50)))
        src = tail.JsonlTailSource(path, "claude")
        with patch.object(tail, "BACKFILL_BYTES", 200):
            t2 = tail.Tailer("k", _session_for(), src, cursors, False)
        self.assertGreater(src.offset, 0)
        texts = [m["text"] for m in t2.poll(time.time(), True)]
        self.assertTrue(texts)
        self.assertNotIn("m0", texts)

    def test_opencode_cursor_for_another_session_is_not_restored(self):
        src = tail.OpencodeSource(self.tmp / "none.db", "s2")
        self.assertFalse(src.restore(
            {"source": "opencode", "session_id": "s1", "cursor": [5, "x"]}))
        self.assertEqual(src.cursor, (0, ""))
        self.assertTrue(src.restore(
            {"source": "opencode", "session_id": "s2", "cursor": [5, "x"]}))

    def test_poll_reads_the_source_state_once(self):
        path = self.tmp / "t.jsonl"
        path.write_text(json.dumps(_claude_user("a")) + "\n")
        src = tail.JsonlTailSource(path, "claude")
        t = tail.Tailer("k", _session_for(), src, self.tmp / "c", False)
        t.poll(time.time(), True)
        with patch.object(src, "state", wraps=src.state) as state:
            t.poll(time.time(), True)
            self.assertEqual(state.call_count, 1)
            self.assertFalse(t.moved)
            with open(path, "a") as fh:
                fh.write(json.dumps(_claude_user("b")) + "\n")
            t.poll(time.time(), True)
            self.assertTrue(t.moved)
        # A replaced source is read fresh, not compared with the old
        # one's state: an empty new file reads as not moved.
        empty = self.tmp / "empty.jsonl"
        empty.write_text("")
        t.source = tail.JsonlTailSource(empty, "claude")
        self.assertEqual(t.poll(time.time(), True), [])
        self.assertFalse(t.moved)

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
        settled = t._cursor_path.read_bytes()
        self.assertEqual(t.poll(time.time(), active=False), [])
        t.commit_cursor()
        self.assertEqual(t._cursor_path.read_bytes(), settled)


def _session_for():
    return Session(timestamp=0, agent="Claude Code", path="/tmp",
                   session_id="", tool_name="claude")


class TestTailManager(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
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

    def _jsonl_session(self, name):
        path = self.tmp / f"{name}.jsonl"
        path.write_text(json.dumps(_claude_user(name)) + "\n")
        return Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(path), tool_name="claude")

    def test_ensure_during_sync_does_not_break_the_walk(self):
        # A phone opening a session calls ensure() on a request thread
        # while the catalog thread walks the tailers in sync(); inserting
        # into the dict mid-walk raised RuntimeError and ended the sync.
        mgr = tail.TailManager(self._emit, "mbp", home=self.tmp,
                               max_sessions=1)
        a, b, c = (self._jsonl_session(n) for n in "abc")
        mgr.ensure("claude:b", b, "")
        rows = [("claude:a", a, ""), ("claude:b", b, "")]
        mgr.sync(rows)
        real = mgr._reresolve

        def reresolve(tailer, session):
            # The on-demand walk (b is past the cap) is mid-iteration.
            if session is b and "claude:c" not in mgr.tailers:
                mgr.ensure("claude:c", c, "")
            real(tailer, session)

        with patch.object(mgr, "_may_have_moved", return_value=True), \
             patch.object(mgr, "_reresolve", side_effect=reresolve):
            mgr.sync(rows + [("claude:c", c, "")])
        self.assertEqual(set(mgr.tailers), {"claude:a", "claude:b",
                                            "claude:c"})

    def test_racing_inserts_keep_the_first_tailer(self):
        # sync and ensure both saw no tailer for the key: the second
        # insert must not replace the first (its cursor and emits).
        mgr = self._manager()
        a = self._jsonl_session("a")
        mgr.ensure("claude:a", a, "")
        first = mgr.tailers["claude:a"]
        src = tail.make_source(a)
        self.assertFalse(mgr._add_tailer("claude:a", a, src))
        self.assertIs(mgr.tailers["claude:a"], first)

    def test_notify_only_on_transition(self):
        # A first sighting in followup is state, not news, and a repeat
        # of the same status is nothing; only a change notifies.
        (self.tmp / "x.jsonl").write_text("")
        src = tail.JsonlTailSource(self.tmp / "x.jsonl", "claude")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        notify = lambda: [e for _k, e in self.events if e["type"] == "notify"]
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:x", sess, "followup")])
            mgr.sync([("claude:x", sess, "followup")])
            self.assertEqual(notify(), [])
            mgr.sync([("claude:x", sess, "active")])
            mgr.sync([("claude:x", sess, "followup")])
            mgr.sync([("claude:x", sess, "followup")])
        self.assertEqual([e["data"]["kind"] for e in notify()], ["needs_you"])

    def test_an_unresolvable_source_gets_no_tailer(self):
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        with patch.object(tail, "make_source", return_value=None):
            mgr.sync([("claude:x", sess, "active")])
        self.assertNotIn("claude:x", mgr.tailers)
        self.assertEqual(self.events, [])

    def test_removed_session_drops_tailer(self):
        (self.tmp / "x.jsonl").write_text("")
        src = tail.JsonlTailSource(self.tmp / "x.jsonl", "claude")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:x", sess, "active")])
        self.assertIn("claude:x", mgr.tailers)
        mgr.sync([])
        self.assertNotIn("claude:x", mgr.tailers)
        self.assertNotIn("claude:x", mgr.statuses)

    def test_a_gone_session_retires_its_approval_and_state(self):
        (self.tmp / "x.jsonl").write_text("")
        src = tail.JsonlTailSource(self.tmp / "x.jsonl", "claude")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:x", sess, "active")])
        mgr._approval_outstanding["claude:x"] = ("req-1", "fp")
        mgr._approval_checked_at["claude:x"] = 1.0
        mgr.last_append["claude:x"] = 1.0
        with patch.object(tail.supervise, "set_pending_approval") as pending:
            mgr.sync([])
        pending.assert_called_once_with("claude:x", None)
        resolved = [e for _k, e in self.events if e["type"] == "approval.resolved"]
        self.assertEqual(resolved[-1]["data"]["request_id"], "req-1")
        for kept in (mgr._approval_outstanding, mgr._approval_checked_at,
                     mgr.last_append):
            self.assertNotIn("claude:x", kept)

    def test_a_session_dropped_mid_pane_read_raises_no_card(self):
        (self.tmp / "x.jsonl").write_text("")
        src = tail.JsonlTailSource(self.tmp / "x.jsonl", "claude")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id="", tool_name="claude")
        mgr = self._manager()
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:x", sess, "active")])

        def capture(_name):
            mgr.sync([])  # the catalog drops it while the pane is read
            return "pane"

        with patch.object(tail.supervise, "meta_for", return_value={"mode": "tmux"}), \
                patch.object(tail.supervise, "channel_name", return_value="qv-1"), \
                patch.object(tail.supervise, "capture_pane", side_effect=capture), \
                patch.object(tail.supervise, "pane_shows_approval", return_value=True), \
                patch.object(tail.supervise, "set_pending_approval"):
            mgr._poll_approvals(1000.0)
        self.assertNotIn("claude:x", mgr._approval_outstanding)
        self.assertEqual([e for _k, e in self.events
                          if e["type"] == "approval.requested"], [])

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
        self.assertGreater(src.cursor[0], 0)
        self.assertEqual(src.poll(), [])
        # The skip covers every row at that millisecond: a straggler
        # written late under the same time_created stays skipped too.
        self._add_part_skip_late()
        self.assertEqual(src.poll(), [])

    def _add_part_skip_late(self):
        import sqlite3
        conn = sqlite3.connect(self.db2)
        conn.execute("INSERT INTO part VALUES ('p-late', 'm', 'sess1', 5000, ?)",
                     (json.dumps({"type": "text", "text": "late"}),))
        conn.commit()
        conn.close()

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
        src = mgr.tailers["claude:s"].next_source()
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

    def test_reresolve_runs_only_when_the_catalog_saw_a_write(self):
        # Resolving globs every Claude project folder; an idle session
        # (same timestamp) or one whose tailed file is the newest copy
        # must not pay for it on every catalog refresh.
        import os
        path = self.tmp / "s.jsonl"
        path.write_text(json.dumps(_claude_user("hi")) + "\n")
        mtime_ms = path.stat().st_mtime * 1000
        sess = Session(timestamp=mtime_ms, agent="Claude Code", path="/tmp",
                       session_id=str(path), tool_name="claude")
        mgr = self._manager()
        mgr.sync([("claude:s", sess, "active")])
        with patch.object(tail, "resolve_jsonl_path",
                          wraps=tail.resolve_jsonl_path) as resolve:
            mgr.sync([("claude:s", sess, "active")])  # unchanged
            with open(path, "a") as fh:
                fh.write(json.dumps(_claude_user("more")) + "\n")
            now = path.stat().st_mtime
            os.utime(path, (now + 5, now + 5))
            grown = Session(timestamp=(now + 5) * 1000, agent="Claude Code",
                            path="/tmp", session_id=str(path),
                            tool_name="claude")
            mgr.sync([("claude:s", grown, "active")])  # own file is newest
            self.assertEqual(resolve.call_count, 0)
            # The catalog saw a write newer than the tailed file: look.
            newer = Session(timestamp=(now + 60) * 1000, agent="Claude Code",
                            path="/tmp", session_id=str(path),
                            tool_name="claude")
            mgr.sync([("claude:s", newer, "active")])
            self.assertEqual(resolve.call_count, 1)
    def _resume_pair(self):
        # Claude --resume from another folder: the new file starts as a
        # line-for-line copy of the old one.
        path_a = self.tmp / "a.jsonl"
        path_a.write_text("".join(
            json.dumps(_claude_user(f"a{i}")) + "\n" for i in range(3)))
        path_b = self.tmp / "b.jsonl"
        sess_a = Session(timestamp=0, agent="Claude Code", path="/tmp",
                         session_id=str(path_a), tool_name="claude")
        sess_b = Session(timestamp=0, agent="Claude Code", path="/tmp",
                         session_id=str(path_b), tool_name="claude")
        mgr = self._manager()
        mgr.sync([("claude:s", sess_a, "active")])
        mgr.tick(time.time())
        return mgr, path_a, path_b, sess_b

    def _texts(self):
        return [e["data"]["text"] for _k, e in self.events
                if e["type"] == "message.appended"]

    def test_reresolve_keeps_turns_written_past_the_copy(self):
        # The catalog sees the new file a refresh late: a turn already
        # appended after the copy must still stream, history must not.
        mgr, path_a, path_b, sess_b = self._resume_pair()
        path_b.write_bytes(path_a.read_bytes()
                           + (json.dumps(_claude_user("b1")) + "\n").encode())
        mgr.sync([("claude:s", sess_b, "active")])
        mgr.tick(time.time() + 2)
        self.assertEqual(self._texts(), ["a0", "a1", "a2", "b1"])

    def test_reresolve_finds_the_last_line_when_the_copy_shifts(self):
        # A copy with an extra record ahead of it: no longer at the same
        # offset, so the line is found by scanning.
        mgr, path_a, path_b, sess_b = self._resume_pair()
        path_b.write_bytes(
            (json.dumps({"type": "worktree-state"}) + "\n").encode()
            + path_a.read_bytes()
            + (json.dumps(_claude_user("b1")) + "\n").encode())
        mgr.sync([("claude:s", sess_b, "active")])
        mgr.tick(time.time() + 2)
        self.assertEqual(self._texts(), ["a0", "a1", "a2", "b1"])

    def test_reresolve_streams_old_lines_never_read(self):
        # Lines the old file got after its last poll were copied too and
        # were never sent: they stream from the new file.
        mgr, path_a, path_b, sess_b = self._resume_pair()
        with open(path_a, "a") as fh:
            fh.write(json.dumps(_claude_user("a3")) + "\n")
        path_b.write_bytes(path_a.read_bytes()
                           + (json.dumps(_claude_user("b1")) + "\n").encode())
        mgr.sync([("claude:s", sess_b, "active")])
        mgr.tick(time.time() + 2)
        self.assertEqual(self._texts(), ["a0", "a1", "a2", "a3", "b1"])

    def test_reresolve_past_a_large_last_record(self):
        # The old source's last record is bigger than any line window:
        # the boundary still matches, so the new turn streams.
        mgr, path_a, path_b, sess_b = self._resume_pair()
        big = json.dumps(_claude_user("x" * (3 << 20))) + "\n"
        with open(path_a, "a") as fh:
            fh.write(big)
        mgr.tick(time.time() + 2)
        path_b.write_bytes(path_a.read_bytes()
                           + (json.dumps(_claude_user("b1")) + "\n").encode())
        mgr.sync([("claude:s", sess_b, "active")])
        mgr.tick(time.time() + 4)
        self.assertEqual(self._texts()[-1], "b1")
        self.assertEqual(self._texts().count("b1"), 1)
        self.assertEqual(len(self._texts()), 5)

    def test_reresolve_shifted_copy_with_repeated_lines(self):
        # A shifted copy whose last read line ("ok") repeats after the
        # new turns: the cursor must land after the copied "ok", not the
        # later one, or the turns in between are lost.
        path_a = self.tmp / "a.jsonl"
        ok = json.dumps(_claude_user("ok")) + "\n"
        path_a.write_text(json.dumps(_claude_user("a0")) + "\n" + ok)
        path_b = self.tmp / "b.jsonl"
        sess_a = Session(timestamp=0, agent="Claude Code", path="/tmp",
                         session_id=str(path_a), tool_name="claude")
        sess_b = Session(timestamp=0, agent="Claude Code", path="/tmp",
                         session_id=str(path_b), tool_name="claude")
        mgr = self._manager()
        mgr.sync([("claude:s", sess_a, "active")])
        mgr.tick(time.time())
        path_b.write_bytes(
            (json.dumps({"type": "worktree-state"}) + "\n").encode()
            + path_a.read_bytes()
            + (json.dumps(_claude_user("b1")) + "\n" + ok).encode())
        mgr.sync([("claude:s", sess_b, "active")])
        mgr.tick(time.time() + 2)
        self.assertEqual(self._texts(), ["a0", "ok", "b1", "ok"])

    def test_reresolve_hands_off_on_the_tail_thread(self):
        # The catalog thread only queues the new source: the old one is
        # still the one polled until the tail thread swaps them, so its
        # boundary can't move after being read.
        mgr, path_a, path_b, sess_b = self._resume_pair()
        path_b.write_bytes(path_a.read_bytes())
        old = mgr.tailers["claude:s"].source
        mgr.sync([("claude:s", sess_b, "active")])
        self.assertIs(mgr.tailers["claude:s"].source, old)
        mgr.tick(time.time() + 2)
        self.assertEqual(mgr.tailers["claude:s"].source.path, path_b)
        self.assertEqual(
            json.loads((mgr.cursors_dir / (tail.protocol.key_slug("claude:s")
                                           + ".json")).read_text())["path"],
            str(path_b))

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
        db = self.tmp / "oc.db"
        import sqlite3 as _sq
        _sq.connect(db).close()
        with patch("quiver.remote.tail.expand_user", return_value=db):
            src = tail.make_source(sess)
        self.assertIsInstance(src, tail.OpencodeSource)
        self.assertEqual((src.db_path, src.session_id), (db, "s"))
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


def _claude_limit(text, resets_at=1793323800, window="five_hour",
                  ts="2026-10-29T22:55:25.053Z"):
    """The 429 record Claude Code writes on a usage limit (real shape)."""
    rec = _claude_assistant([{"type": "text", "text": text}])
    rec.update({"isApiErrorMessage": True, "error": "rate_limit",
                "apiErrorStatus": 429, "timestamp": ts})
    if resets_at is not None:
        rec["quotaLimits"] = {
            "status": "rejected", "resetsAt": resets_at,
            "rateLimitType": window, "overageStatus": "rejected",
            "isUsingOverage": False}
    return rec


def _codex_limit(ts="2026-10-20T02:55:34.068Z"):
    """A codex turn that ended on a usage limit (real shape)."""
    return {"timestamp": ts, "type": "event_msg", "payload": {
        "type": "task_complete", "turn_id": "t1", "last_agent_message": None,
        "error": {"message": "You hit your spend cap set by the owner of "
                             "your workspace.",
                  "codex_error_info": "usage_limit_exceeded"}}}


def _iso_ts(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


class TestLimitNotify(unittest.TestCase):
    """notify kind ``limit`` from Claude Code and Codex limit records."""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.events = []
        self.path = self.tmp / "t.jsonl"
        self.path.write_text("")

    def _manager(self, tool="claude"):
        mgr = tail.TailManager(
            lambda k, ev: self.events.append((k, ev)), "mbp", home=self.tmp)
        sess = Session(timestamp=0, agent="x", path="/tmp",
                       session_id="", tool_name=tool)
        src = tail.JsonlTailSource(self.path, tool)
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([(f"{tool}:s", sess, "active")])
        return mgr

    def _append(self, *recs):
        with open(self.path, "a") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")

    def _limits(self):
        return [ev["data"] for _k, ev in self.events
                if ev["type"] == "notify" and ev["data"]["kind"] == "limit"]

    def test_claude_decode_marks_rate_limit_only(self):
        out = tail.decode_record("claude", _claude_limit(
            "You've hit your session limit · resets 9:30am"))
        self.assertEqual(out[0]["kind"], "error")
        self.assertEqual(out[0][tail.LIMIT_MARK],
                         {"resets_at": 1793323800.0, "window": "five_hour"})
        # Older records without quotaLimits still count.
        out = tail.decode_record("claude", _claude_limit("limit", None))
        self.assertEqual(out[0][tail.LIMIT_MARK], {})
        # Another API error is just an error.
        rec = _claude_limit("Internal server error", None)
        rec["error"] = "server_error"
        self.assertNotIn(tail.LIMIT_MARK, tail.decode_record("claude", rec)[0])

    def test_codex_decode_marks_usage_limit_only(self):
        out = tail.decode_record("codex", _codex_limit())
        self.assertEqual(out[0]["kind"], "error")
        self.assertIn("spend cap", out[0]["text"])
        self.assertEqual(out[0][tail.LIMIT_MARK], {})
        plain = {"type": "event_msg", "payload": {
            "type": "task_complete", "last_agent_message": "done"}}
        self.assertEqual(tail.decode_record("codex", plain), [])

    def test_claude_limit_rings_once_with_reset(self):
        mgr = self._manager()
        now = time.time()
        text = "You've hit your session limit · resets 9:30am"
        self._append(_claude_limit(text, resets_at=now + 3600,
                                   ts=_iso_ts(now)))
        mgr.tick(now)
        [data] = self._limits()
        self.assertEqual(data["message"], text)
        self.assertAlmostEqual(data["resets_at"], now + 3600)
        self.assertEqual(data["window"], "five_hour")
        self.assertEqual(data["status"], "active")
        # The marker never reaches the wire.
        appended = [ev for _k, ev in self.events
                    if ev["type"] == "message.appended"]
        self.assertNotIn(tail.LIMIT_MARK, appended[0]["data"])
        # A retry into the same limit is quiet; nothing repeats per poll.
        self._append(_claude_limit(text, resets_at=now + 3600,
                                   ts=_iso_ts(now + 60)))
        mgr.tick(now + 60)
        mgr.tick(now + 120)
        self.assertEqual(len(self._limits()), 1)
        # The next window's limit rings again.
        self._append(_claude_limit(text, resets_at=now + 9000,
                                   ts=_iso_ts(now + 4000)))
        mgr.tick(now + 4000)
        self.assertEqual(len(self._limits()), 2)

    def test_codex_limit_repeats_only_after_window(self):
        mgr = self._manager("codex")
        now = time.time()
        self._append(_codex_limit(_iso_ts(now)))
        mgr.tick(now)
        self.assertEqual(len(self._limits()), 1)
        self.assertNotIn("resets_at", self._limits()[0])
        self._append(_codex_limit(_iso_ts(now + 30)))
        mgr.tick(now + 30)
        self.assertEqual(len(self._limits()), 1)
        later = now + tail.LIMIT_REPEAT_S + 10
        self._append(_codex_limit(_iso_ts(later)))
        mgr.tick(later)
        self.assertEqual(len(self._limits()), 2)

    def test_stale_limit_record_is_quiet(self):
        mgr = self._manager()
        now = time.time()
        self._append(_claude_limit(
            "old limit", ts=_iso_ts(now - tail.LIMIT_FRESH_S - 60)))
        mgr.tick(now)
        self.assertEqual(self._limits(), [])
        # Still shown in the chat.
        appended = [ev["data"]["text"] for _k, ev in self.events
                    if ev["type"] == "message.appended"]
        self.assertEqual(appended, ["old limit"])


class TestSweepStale(unittest.TestCase):
    """``sweep_stale``: old journal + cursor pairs go, live ones stay."""

    def setUp(self):
        from quiver.remote.journal import Journal

        self.tmp = temp_dir(self)
        self.journal = Journal(self.tmp)
        self.mgr = tail.TailManager(lambda k, e: None, "mbp", home=self.tmp)
        self.mgr.cursors_dir.mkdir(parents=True, exist_ok=True)
        self.now = time.time()
        self.old = self.now - tail.STALE_FILE_AGE_S - 3600

    def _files(self, key, journal_at=None, cursor_at=None):
        import os

        slug = tail.protocol.key_slug(key)
        j = self.journal.dir / f"{slug}.jsonl"
        c = self.mgr.cursors_dir / f"{slug}.json"
        if journal_at is not None:
            self.journal.append(key, {"type": "t"})
            os.utime(j, (journal_at, journal_at))
        if cursor_at is not None:
            c.write_text("{}")
            os.utime(c, (cursor_at, cursor_at))
        return j, c

    def test_old_pair_goes(self):
        j, c = self._files("claude:old", self.old, self.old)
        self.assertEqual(self.mgr.sweep_stale(self.journal, now=self.now), 2)
        self.assertFalse(j.exists())
        self.assertFalse(c.exists())
        # The journal forgot the key: it starts over, as after a restart.
        self.assertEqual(self.journal.last_seq("claude:old"), 0)

    def test_fresh_journal_or_cursor_keeps_both(self):
        j1, c1 = self._files("claude:a", self.now, self.old)
        j2, c2 = self._files("claude:b", self.old, self.now)
        self.assertEqual(self.mgr.sweep_stale(self.journal, now=self.now), 0)
        for path in (j1, c1, j2, c2):
            self.assertTrue(path.exists(), path)

    def test_lone_old_files_go(self):
        j, _ = self._files("claude:j", journal_at=self.old)
        _, c = self._files("claude:c", cursor_at=self.old)
        self.assertEqual(self.mgr.sweep_stale(self.journal, now=self.now), 2)
        self.assertFalse(j.exists())
        self.assertFalse(c.exists())

    def test_tailed_and_kept_keys_stay(self):
        sess_path = self.tmp / "t.jsonl"
        sess_path.write_text(json.dumps(_claude_user("hi")) + "\n")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(sess_path), tool_name="claude")
        self.mgr.ensure("claude:tailed", sess, "")
        j1, c1 = self._files("claude:tailed", self.old, self.old)
        j2, _ = self._files("_global", journal_at=self.old)
        self.assertEqual(
            self.mgr.sweep_stale(self.journal, keep=["_global"], now=self.now),
            0)
        for path in (j1, c1, j2):
            self.assertTrue(path.exists(), path)


    def test_a_tailer_starting_mid_sweep_keeps_its_files(self):
        # A phone opening an idle session while the sweep runs: the
        # tailer either starts before the sweep (and its key is held)
        # or after it (with neither file); never on files then deleted.
        # As the hub wires it: a journal on disk means skip backfill.
        self.mgr = tail.TailManager(
            lambda k, e: None, "mbp", home=self.tmp,
            journal_has=lambda k: self.journal.last_seq(k) > 0)
        _j, c = self._files("claude:back", self.old, self.old)
        sess_path = self.tmp / "back.jsonl"
        sess_path.write_text(json.dumps(_claude_user("hi")) + "\n")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(sess_path), tool_name="claude")
        started = threading.Event()
        real = self.journal.sweep

        def slow_sweep(cutoff, keep):
            started.set()
            time.sleep(0.2)  # the ensure below runs meanwhile
            return real(cutoff, keep)

        with patch.object(self.journal, "sweep", side_effect=slow_sweep):
            t = threading.Thread(
                target=self.mgr.sweep_stale, args=(self.journal,),
                kwargs={"now": self.now})
            t.start()
            self.assertTrue(started.wait(2))
            self.mgr.ensure("claude:back", sess, "")
            t.join(5)
            self.assertFalse(t.is_alive())
        # The ensure waited for the sweep: both files went first, and
        # the tailer started fresh, so recent history backfills (one
        # started on the doomed journal would skip to the end).
        self.assertFalse(c.exists())
        self.assertIn("claude:back", self.mgr.tailers)
        sent = []
        self.mgr.emit = lambda k, e: sent.append(e)
        self.mgr.tick(time.time())
        self.assertEqual([e["data"]["text"] for e in sent
                          if e["type"] == "message.appended"], ["hi"])


class TestApprovalWatcher(unittest.TestCase):
    """TailManager._poll_approvals: pane prompt → requested/resolved."""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.events = []
        self.mgr = tail.TailManager(
            lambda k, ev: self.events.append((k, ev)), "mbp",
            home=self.tmp)
        # Only the key matters to the watcher; no transcript needed.
        self.mgr.tailers["k1"] = object()
        self._t = time.time()
        # The watcher records pending prompts in supervise's module-level
        # dict: start empty and leave nothing for later tests.
        p = patch.dict(tail.supervise._PENDING_APPROVALS, clear=True)
        p.start()
        self.addCleanup(p.stop)

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

    def test_devin_menu_requests_with_command_as_prompt(self):
        # Devin's footer says "esc cancel" and numbers rows with a bare
        # space — detection and the card's prompt line both rely on it.
        pane = (
            "● Running command\n"
            "$ git -C /repo pull origin main\n"
            ") 1 Yes  (Approve once)\n"
            "  2 Yes, allow `git` commands\n"
            "  8 No\n"
            "  ↑↓ select · ↵ confirm · esc cancel"
        )
        self._poll(pane, meta={"mode": "tmux", "tmux": "qv-k1"})
        evs = self._approval_events()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["type"], "approval.requested")
        self.assertIn("git -C /repo pull origin main",
                      evs[0]["data"]["prompt"])

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
        # The id alone, as the request carried it: the hub and the
        # phone match the resolve to the open card by this string.
        self.assertEqual(evs[-1]["data"]["request_id"], rid)
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

    def test_request_carries_the_menu_options(self):
        # The card shows the pane's real rows, so the event lists them —
        # a client can answer "6 Edit command" with its own digit.
        pane = (
            "● Running command\n"
            "$ sudo -n ls /var/root\n"
            ") 1 Yes  (Approve once)\n"
            "  2 Yes, allow `sudo` commands\n"
            "  6 Edit command\n"
            "  8 No\n"
            "  ↑↓ select · ↵ confirm · esc cancel"
        )
        self._poll(pane, meta={"mode": "tmux", "tmux": "qv-k1"})
        ev = self._approval_events()[0]
        opts = ev["data"]["options"]
        self.assertEqual(
            [(o["n"], o["label"]) for o in opts],
            [("1", "Yes  (Approve once)"),
             ("2", "Yes, allow `sudo` commands"),
             ("6", "Edit command"),
             ("8", "No")])
        self.assertIn("sudo -n ls /var/root", ev["data"]["prompt"])

    def test_prompt_line_fallback(self):
        pane = "x\ny"  # no keyword line → generic prompt text
        self._poll("esc to cancel\n" + pane, meta={"mode": "tmux"})
        ev = self._approval_events()[0]
        self.assertEqual(ev["data"]["prompt"], "Approval requested")

    def test_request_carries_choice_menu_options(self):
        # An unnumbered ask-question list rides the event as positional
        # rows — the card lists the real labels instead of the
        # allow/always/deny guess, and the injector walks them with
        # arrow keys.
        pane = (
            " Which engine?\n"
            "\n"
            " ❭ SQLite\n"
            "   Postgres\n"
            "   Other (type your own)\n"
            "\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        self._poll(pane, meta={"mode": "tmux", "tmux": "qv-k1"})
        ev = self._approval_events()[0]
        opts = ev["data"]["options"]
        self.assertEqual(
            [(o["n"], o["label"]) for o in opts],
            [("1", "SQLite"), ("2", "Postgres"),
             ("3", "Other (type your own)")])
        self.assertEqual(ev["data"]["prompt"], "Which engine?")

    def test_request_carries_devin_select_options(self):
        # Devin's select widget marks the selected row "❭" — every row
        # reaches the card, option 1 included.
        pane = (
            " How would you like to log in?\n"
            "\n"
            " ❭ 1 Log in with browser\n"
            "   2 Paste a token manually\n"
            "   3 Back\n"
            "\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        self._poll(pane, meta={"mode": "tmux", "tmux": "qv-k1"})
        ev = self._approval_events()[0]
        self.assertEqual(
            [(o["n"], o["label"]) for o in ev["data"]["options"]],
            [("1", "Log in with browser"),
             ("2", "Paste a token manually"), ("3", "Back")])
        self.assertEqual(ev["data"]["prompt"],
                         "How would you like to log in?")


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

    def test_stale_menu_above_live_choices_keeps_live_question(self):
        # The numbered menu scrolled up is dead; the choice list below
        # is what the card answers, so the question must come from the
        # zone above that live block, not above the stale one.
        pane = (
            " Which branch?\n"
            " 1. main\n"
            " 2. dev\n"
            "\n"
            " Which engine?\n"
            " ❭ SQLite\n"
            "   Postgres\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        self.assertEqual(
            tail._approval_prompt_line(pane), "Which engine?")

    def test_menu_row_is_never_the_prompt(self):
        # The banner scrolled off: "2 Yes, allow `sudo` commands" is a
        # menu answer, not the question — the fallback must skip it.
        pane = (
            ") 1 Yes  (Approve once)\n"
            "  2 Yes, allow `sudo` commands\n"
            "  8 No\n"
            "  ↑↓ select · ↵ confirm · esc cancel"
        )
        self.assertEqual(
            tail._approval_prompt_line(pane), "Approval requested")

    def test_question_mark_line_is_the_prompt(self):
        pane = "output\nRun sudo -n ls /var/root?\n1. Yes\n2. No"
        self.assertEqual(
            tail._approval_prompt_line(pane), "Run sudo -n ls /var/root?")

    def test_question_nearest_the_menu_wins(self):
        # An earlier conversation line can end in "?" too — the card
        # must name what its buttons answer, not the older question.
        pane = "Which branch?\nDo you want to proceed?\n1. Yes\n2. No"
        self.assertEqual(
            tail._approval_prompt_line(pane), "Do you want to proceed?")


class TestTailWake(unittest.TestCase):
    """A write to a session wakes its tailer and polls it fast a while."""

    KEY = "claude:s1"

    def setUp(self):
        self.tmp = temp_dir(self)
        self.events = []
        self.path = self.tmp / "s.jsonl"
        self.path.write_text(json.dumps(_claude_user("old")) + "\n")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(self.path), tool_name="claude")
        self.mgr = tail.TailManager(
            lambda key, ev: self.events.append(ev), "mbp", home=self.tmp)
        self.mgr.sync([(self.KEY, sess, "done")])
        self.t0 = 1_000_000.0
        self.mgr.tick(self.t0)  # first poll; idle from here on
        self.tailer = self.mgr.tailers[self.KEY]

    def _append(self, text):
        with open(self.path, "a") as fh:
            fh.write(json.dumps(_claude_user(text)) + "\n")

    def _texts(self):
        return [e["data"]["text"] for e in self.events
                if e["type"] == "message.appended"]

    def test_idle_session_waits_out_its_interval_without_a_wake(self):
        self.assertEqual(self.tailer.next_poll_at, self.t0 + tail.POLL_IDLE_S)
        self._append("hi")
        self.mgr.tick(self.t0 + 1)
        self.assertNotIn("hi", self._texts())

    def test_send_to_idle_session_polls_now_then_fast(self):
        self._append("hi")
        self.mgr.wake(self.KEY, now=self.t0 + 1)
        self.mgr.tick(self.t0 + 1)
        self.assertIn("hi", self._texts())
        self.assertEqual(self.tailer.next_poll_at,
                         self.t0 + 1 + tail.POLL_HOT_S)
        self.assertLess(tail.POLL_HOT_S, tail.POLL_ACTIVE_S)
        # The reply lands a moment later and is picked up on the fast
        # cadence, not 8 s on.
        self._append("reply")
        self.mgr.tick(self.t0 + 1 + tail.POLL_HOT_S)
        self.assertIn("reply", self._texts())

    def test_unchanged_hot_poll_writes_no_cursor(self):
        self.mgr.statuses[self.KEY] = "active"
        self.mgr.wake(self.KEY, now=self.t0)
        with patch.object(self.tailer, "save_cursor",
                          wraps=self.tailer.save_cursor) as save:
            for i in range(8):
                self.mgr.tick(self.t0 + i * tail.POLL_HOT_S)
            save.assert_not_called()
            # A real append moves the cursor and is saved once.
            self._append("hi")
            self.mgr.tick(self.t0 + 8 * tail.POLL_HOT_S)
            self.assertEqual(save.call_count, 1)
            self.mgr.tick(self.t0 + 9 * tail.POLL_HOT_S)
            self.assertEqual(save.call_count, 1)
        self.assertIn("hi", self._texts())

    def test_window_expires_back_to_normal_cadence(self):
        self.mgr.wake(self.KEY, now=self.t0)
        self.mgr.tick(self.t0)
        self.assertTrue(self.mgr.is_hot(self.KEY, self.t0))
        later = self.t0 + tail.HOT_WINDOW_S + 0.01
        self.mgr.tick(later)
        self.assertFalse(self.mgr.is_hot(self.KEY, later))
        self.assertEqual(self.tailer.next_poll_at, later + tail.POLL_IDLE_S)
        # Expired windows are dropped, not kept forever.
        self.assertEqual(self.mgr._hot_until, {})
        # An active session falls back to the active cadence instead.
        self.mgr.statuses[self.KEY] = "active"
        self.mgr.wake(self.KEY, now=later)
        self.mgr.tick(later + tail.HOT_WINDOW_S + 1)
        self.assertEqual(self.tailer.next_poll_at,
                         later + tail.HOT_WINDOW_S + 1 + tail.POLL_ACTIVE_S)

    def test_each_write_rearms_the_window(self):
        self.mgr.wake(self.KEY, now=self.t0)
        self.mgr.wake(self.KEY, now=self.t0 + 10)
        # Past the first window, inside the second.
        self.mgr.tick(self.t0 + 20)
        self.assertEqual(self.tailer.next_poll_at,
                         self.t0 + 20 + tail.POLL_HOT_S)
        # An older wake arriving late never shortens the window.
        self.mgr.wake(self.KEY, now=self.t0 + 1)
        self.assertTrue(self.mgr.is_hot(self.KEY, self.t0 + 24))
        self.mgr.tick(self.t0 + 26)
        self.assertEqual(self.tailer.next_poll_at,
                         self.t0 + 26 + tail.POLL_IDLE_S)

    def test_wake_interrupts_a_parked_loop(self):
        import threading
        got = []
        t = threading.Thread(target=lambda: got.append(self.mgr.wait(30)))
        t.start()
        self.mgr.wake(self.KEY)
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(got, [True])
        # The next tick consumes the wake; the loop parks again after.
        self.mgr.tick(self.t0 + 1)
        self.assertFalse(self.mgr.wait(0))

    def test_wake_landing_mid_tick_is_not_lost(self):
        mgr = self.mgr
        real = self.tailer.source.poll
        calls = []

        def poll():
            calls.append(1)
            if len(calls) == 1:
                # The harness reacts while this poll is reading.
                mgr.wake(self.KEY, now=self.t0 + 1)
            return real()

        self.tailer.source.poll = poll
        mgr.wake(self.KEY, now=self.t0 + 1)
        mgr.tick(self.t0 + 1)
        self.assertEqual(len(calls), 1)
        # The mid-tick wake keeps the event set, so the loop does not
        # park, and the next tick polls again although nothing is due.
        self.assertTrue(mgr.wait(0))
        mgr.tick(self.t0 + 1.01)
        self.assertEqual(len(calls), 2)

    def test_interrupt_wakes_without_a_poll(self):
        self.mgr.interrupt()
        self.assertTrue(self.mgr.wait(0))
        self._append("hi")
        self.mgr.tick(self.t0 + 1)
        self.assertNotIn("hi", self._texts())

    def test_wake_before_tailer_exists_starts_it_hot(self):
        self.mgr.wake("claude:new", now=self.t0)
        path = self.tmp / "n.jsonl"
        path.write_text(json.dumps(_claude_user("first")) + "\n")
        sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                       session_id=str(path), tool_name="claude")
        self.mgr.ensure("claude:new", sess, "done")
        self.mgr.tick(self.t0 + 1)
        self.assertIn("first", self._texts())
        self.assertEqual(self.mgr.tailers["claude:new"].next_poll_at,
                         self.t0 + 1 + tail.POLL_HOT_S)

    def test_snapshot_sources_stay_at_active_cadence_when_hot(self):
        sess = Session(timestamp=0, agent="Devin", path="/tmp",
                       session_id="d1", tool_name="devin")
        tailer = tail.Tailer("devin:d1", sess, tail.SnapshotSource(sess),
                             self.tmp / "cursors", has_journal=True)
        self.assertEqual(tailer.interval(active=False, hot=True),
                         tail.POLL_ACTIVE_S)
        self.assertEqual(tailer.interval(active=False, hot=False),
                         tail.POLL_IDLE_S)
        self.assertEqual(self.tailer.interval(active=True, hot=True),
                         tail.POLL_HOT_S)

    def test_waking_injector_wakes_around_writes_only(self):
        from unittest.mock import MagicMock
        inner = MagicMock()
        order = []
        inner.send.side_effect = lambda *a: order.append("send") or "ok"
        inner.control.side_effect = RuntimeError("boom")
        inner.mode = "tmux"
        wrapped = self.mgr.waking(self.KEY, inner)
        with patch.object(self.mgr, "wake",
                          side_effect=lambda k: order.append(f"wake:{k}")):
            self.assertEqual(wrapped.send("sess", {"text": "x"}, "done"),
                             "ok")
            self.assertEqual(order, [f"wake:{self.KEY}", "send",
                                     f"wake:{self.KEY}"])
            order.clear()
            # A failed write still re-arms: it may have half landed.
            with self.assertRaises(RuntimeError):
                wrapped.control("sess", "interrupt", "")
            self.assertEqual(order, [f"wake:{self.KEY}", f"wake:{self.KEY}"])
            order.clear()
            self.assertEqual(wrapped.mode, "tmux")
            self.assertEqual(order, [])
        inner.send.assert_called_once_with("sess", {"text": "x"}, "done")


class TestStatusProbe(unittest.TestCase):
    """Hot or growing sessions get their status re-probed between
    catalog refreshes, and a stale catalog read cannot undo it."""

    KEY = "claude:s1"

    def setUp(self):
        self.tmp = temp_dir(self)
        self.events = []
        self.noted = []
        self.probed = []
        self.live = "done"
        self.path = self.tmp / "s.jsonl"
        self.path.write_text(json.dumps(_claude_user("old")) + "\n")
        self.sess = Session(timestamp=0, agent="Claude Code", path="/tmp",
                            session_id=str(self.path), tool_name="claude")

        def status_of(sessions):
            self.probed.append(sessions[0])
            return [self.live]

        self.status_of = status_of
        self.t0 = 1_000_000.0
        # Reads are stamped by this clock; _tick keeps it at the tick's
        # time unless a test moves it.
        self.read_now = self.t0
        self.mgr = tail.TailManager(
            lambda key, ev: self.events.append(ev), "mbp", home=self.tmp,
            status_of=lambda ss: self.status_of(ss),
            on_status=lambda *a: self.noted.append(a),
            read_clock=lambda: self.read_now,
        )
        self.mgr.sync([(self.KEY, self.sess, "done")], as_of=self.t0 - 5)
        self._tick(self.t0)
        self.events.clear()
        self.noted.clear()

    def _tick(self, now):
        self.read_now = now
        self.mgr.tick(now)

    def _append(self, rec):
        with open(self.path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _statuses(self):
        return [e["data"]["status"] for e in self.events
                if e["type"] == "session.status"]

    def _notifies(self):
        return [e["data"]["kind"] for e in self.events
                if e["type"] == "notify"]

    def _go_active(self):
        self.live = "active"
        self.mgr.wake(self.KEY, now=self.t0 + 2)
        self._append(_claude_user("hi"))
        self._tick(self.t0 + 2)

    def test_send_to_idle_session_flips_active_within_hot_window(self):
        self._go_active()
        self.assertEqual(self.mgr.statuses[self.KEY], "active")
        self.assertEqual(self._statuses(), ["active"])
        self.assertEqual(self.noted[-1], (self.KEY, "active", self.t0 + 2))
        # Active now, so the cadence stays fast after the window too.
        self.assertTrue(self.mgr.tailers[self.KEY].next_poll_at
                        <= self.t0 + 2 + tail.POLL_HOT_S)

    def test_stale_catalog_sync_does_not_revert_it(self):
        self._go_active()
        # A refresh that read its statuses before the probe says done.
        self.mgr.sync([(self.KEY, self.sess, "done")], as_of=self.t0 + 1)
        self.assertEqual(self.mgr.statuses[self.KEY], "active")
        self.assertEqual(self._statuses(), ["active"])
        # One that read after it wins.
        self.mgr.sync([(self.KEY, self.sess, "done")], as_of=self.t0 + 3)
        self.assertEqual(self.mgr.statuses[self.KEY], "done")
        self.assertEqual(self._statuses(), ["active", "done"])

    def test_turn_ending_flips_back_promptly(self):
        self._go_active()
        self.live = "done"
        self._append(_claude_assistant([{"type": "text", "text": "ok"}]))
        # Read at once on the hot cadence; the probe gate is still shut.
        self._tick(self.t0 + 2 + tail.POLL_HOT_S)
        self.assertEqual(self.mgr.statuses[self.KEY], "active")
        # The growth is owed a probe, which runs once the gate opens.
        self._tick(self.t0 + 2 + tail.STATUS_PROBE_S)
        self.assertEqual(self.mgr.statuses[self.KEY], "done")
        self.assertEqual(self._statuses(), ["active", "done"])
        self.assertEqual(self._notifies(), ["finished"])

    def test_growth_after_the_window_is_still_probed_when_not_due(self):
        self._go_active()
        later = self.t0 + 2 + tail.HOT_WINDOW_S + 5
        self._tick(later)  # window over; active cadence
        self.live = "done"
        self._append(_claude_assistant([{"type": "text", "text": "ok"}]))
        self._tick(later + tail.POLL_ACTIVE_S)  # reads the reply
        self.assertEqual(self.mgr.statuses[self.KEY], "done")

    def test_probes_are_bounded(self):
        before = len(self.probed)
        # Hot but unchanged: at most one probe per STATUS_PROBE_S.
        self.mgr.wake(self.KEY, now=self.t0 + 2)
        for i in range(8):
            self._tick(self.t0 + 2 + i * tail.POLL_HOT_S)
        self.assertEqual(len(self.probed) - before, 2)
        # Neither hot nor growing: no probes at all.
        quiet = self.t0 + 100
        before = len(self.probed)
        for i in range(5):
            self._tick(quiet + i * tail.POLL_IDLE_S)
        self.assertEqual(len(self.probed), before)
        # An unchanged accepted probe still reaches the catalog.
        self.assertTrue(all(n[1] == "done" for n in self.noted))

    def test_probe_sees_the_transcript_mtime_not_the_catalog_time(self):
        self._go_active()
        mtime_ms = self.path.stat().st_mtime * 1000
        self.assertGreaterEqual(self.probed[-1].timestamp, mtime_ms)
        # The catalog's Session object itself is left alone.
        self.assertEqual(self.sess.timestamp, 0)

    def test_untimed_sync_always_applies(self):
        self._go_active()
        self.mgr.sync([(self.KEY, self.sess, "done")])
        self.assertEqual(self.mgr.statuses[self.KEY], "done")

    def test_no_status_of_means_no_probes(self):
        mgr = tail.TailManager(lambda k, e: None, "mbp", home=self.tmp)
        mgr.sync([(self.KEY, self.sess, "done")], as_of=self.t0)
        mgr.wake(self.KEY, now=self.t0)
        mgr.tick(self.t0)
        self.assertEqual(mgr.statuses[self.KEY], "done")


    def test_silent_turn_end_record_still_gets_a_probe(self):
        # A turn can end on a record that decodes to no message (Codex
        # task_complete, Claude turn_duration), after the hot window.
        self._go_active()
        later = self.t0 + 2 + tail.HOT_WINDOW_S + 5
        self._tick(later)
        self.live = "done"
        n = len(self.events)
        self._append({"type": "system", "subtype": "turn_duration",
                      "durationMs": 5})
        self._tick(later + tail.POLL_ACTIVE_S)
        self.assertFalse([e for e in self.events[n:]
                          if e["type"] == "message.appended"])
        self.assertEqual(self.mgr.statuses[self.KEY], "done")

    def test_probe_is_stamped_when_read_not_when_the_tick_began(self):
        # A catalog refresh read "done" at t0+4; this tick began at t0+2
        # but the probe read at t0+5, after it, so the probe wins.
        self.mgr.sync([(self.KEY, self.sess, "done")], as_of=self.t0 + 4)
        self.live = "active"
        self.mgr.wake(self.KEY, now=self.t0 + 2)
        self.read_now = self.t0 + 5
        self.mgr.tick(self.t0 + 2)
        self.assertEqual(self.mgr.statuses[self.KEY], "active")
        self.assertEqual(self.noted[-1], (self.KEY, "active", self.t0 + 5))
        # And the reverse: a read taken before the refresh loses.
        self.live = "done"
        self.mgr.sync([(self.KEY, self.sess, "active")], as_of=self.t0 + 9)
        self.read_now = self.t0 + 8
        self.mgr.tick(self.t0 + 7)
        self.assertEqual(self.mgr.statuses[self.KEY], "active")

    def test_session_dropped_mid_probe_is_not_resurrected(self):
        self.live = "active"

        def status_of(sessions):
            # The catalog thread drops the session while this reads.
            self.mgr.sync([], as_of=self.t0 + 1)
            return ["active"] * len(sessions)

        self.status_of = status_of
        self.mgr.wake(self.KEY, now=self.t0 + 2)
        self._tick(self.t0 + 2)
        self.assertNotIn(self.KEY, self.mgr.tailers)
        self.assertNotIn(self.KEY, self.mgr.statuses)
        self.assertEqual(self._statuses(), [])
        self.assertEqual(self.noted, [])

    def test_session_replaced_mid_probe_ignores_the_old_read(self):
        def status_of(sessions):
            # Dropped and tailed again: a new tailer, same key.
            self.mgr.sync([], as_of=self.t0 + 1)
            self.mgr.ensure(self.KEY, self.sess, "done")
            return ["active"] * len(sessions)

        self.status_of = status_of
        self.mgr.wake(self.KEY, now=self.t0 + 2)
        self._tick(self.t0 + 2)
        self.assertEqual(self.mgr.statuses[self.KEY], "done")
        self.assertEqual(self._statuses(), [])

    def test_due_probes_share_one_status_call(self):
        calls = []

        def status_of(sessions):
            calls.append([s.session_id for s in sessions])
            return ["active"] * len(sessions)

        rows = [(self.KEY, self.sess, "done")]
        for i in range(2, 5):
            path = self.tmp / f"s{i}.jsonl"
            path.write_text(json.dumps(_claude_user("x")) + "\n")
            rows.append((f"claude:s{i}", Session(
                timestamp=0, agent="Claude Code", path="/tmp",
                session_id=str(path), tool_name="claude"), "done"))
        self.mgr.sync(rows, as_of=self.t0)
        self.status_of = status_of
        for key, _s, _st in rows:
            self.mgr.wake(key, now=self.t0 + 2)
        self._tick(self.t0 + 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 4)
        self.assertEqual(set(self.mgr.statuses.values()), {"active"})


class TestFreshSession(unittest.TestCase):
    """``fresh_session`` brings a catalog copy's activity time up to date."""

    def test_fresh_session_uses_tailer_activity(self):
        tmp = temp_dir(self)
        path = tmp / "t.jsonl"
        path.write_text("")
        mgr = tail.TailManager(lambda k, ev: None, "mbp", home=tmp)
        stale = Session(timestamp=0, agent="x", path="/tmp",
                        session_id="", tool_name="claude")
        # Nothing tails the key: unchanged.
        self.assertIs(mgr.fresh_session("claude:s", stale), stale)
        src = tail.JsonlTailSource(path, "claude")
        with patch.object(tail, "make_source", return_value=src):
            mgr.sync([("claude:s", stale, "done")])
        fresh = mgr.fresh_session("claude:s", stale)
        self.assertGreater(fresh.timestamp, 0)  # the file's mtime
        self.assertEqual(stale.timestamp, 0)    # the copy is untouched
        mgr.last_append["claude:s"] = time.time() + 100
        self.assertAlmostEqual(
            mgr.fresh_session("claude:s", stale).timestamp,
            mgr.last_append["claude:s"] * 1000)


if __name__ == "__main__":
    unittest.main()
