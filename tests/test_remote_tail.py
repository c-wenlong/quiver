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


if __name__ == "__main__":
    unittest.main()
