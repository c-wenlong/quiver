"""ledger/parsers.py and models.py: the Swift ledger's line rules."""

import json
import tempfile
import unittest
from pathlib import Path

from swe.ledger import parsers
from swe.ledger.models import MAX_TOKENS, parse_timestamp


def _claude(**over):
    usage = over.pop("usage", {"input_tokens": 10, "output_tokens": 20,
                               "cache_read_input_tokens": 300,
                               "cache_creation_input_tokens": 40})
    line = {
        "type": "assistant", "timestamp": "2026-10-03T10:00:03.123Z",
        "sessionId": "s1", "requestId": "req1", "cwd": "/w", "version": "2.1.0",
        "message": {"id": "msg1", "model": "claude-opus-5", "usage": usage},
    }
    line.update(over)
    return json.dumps(line).encode() + b"\n"


class TestTimestamp(unittest.TestCase):
    def test_utc_fast_path_any_fraction(self):
        self.assertEqual(parse_timestamp("1970-01-01T00:00:01Z"), 1.0)
        self.assertAlmostEqual(parse_timestamp("1970-01-01T00:00:01.123456789Z"), 1.123456789)

    def test_offset_and_garbage(self):
        self.assertEqual(parse_timestamp("1970-01-01T08:00:00+08:00"), 0.0)
        self.assertIsNone(parse_timestamp("yesterday"))
        self.assertIsNone(parse_timestamp(None))
        self.assertIsNone(parse_timestamp("2026-10-03T10:00:03"))  # no zone


class TestClaude(unittest.TestCase):
    def test_key_and_tokens(self):
        [rec] = parsers.claude_records(_claude(), "/h")
        self.assertEqual(rec.key, "claude:msg1:req1")
        self.assertEqual(rec.msg, "msg1")
        e = rec.event
        self.assertEqual((e.input, e.output, e.cache_read, e.cache_write), (10, 20, 300, 40))
        self.assertEqual(e.total, 370)
        self.assertEqual((e.session, e.cwd, e.model, e.account), ("s1", "/w", "claude-opus-5", "/h"))

    def test_absurd_counts_are_capped(self):
        """A corrupt count (1e19) is capped, so the event's sums and the
        store's 64-bit integers can't overflow and abort the scan."""
        usage = {"input_tokens": 1e19, "output_tokens": 5,
                 "cache_read_input_tokens": 2 ** 70}
        [rec] = parsers.claude_records(_claude(usage=usage), "/h")
        self.assertEqual(rec.event.input, MAX_TOKENS)
        self.assertEqual(rec.event.cache_read, MAX_TOKENS)
        self.assertEqual(rec.event.output, 5)

    def test_key_without_request_or_message_id(self):
        line = json.loads(_claude())
        del line["requestId"]
        [rec] = parsers.claude_records(json.dumps(line).encode(), "/h")
        self.assertEqual(rec.key, "claude:msg1")
        del line["message"]["id"]
        [rec] = parsers.claude_records(json.dumps(line).encode(), "/h")
        self.assertEqual(rec.key, "claude:anon:s1:2026-10-03T10:00:03.123Z:claude-opus-5")
        self.assertIsNone(rec.msg)

    def test_thinking_comes_out_of_output(self):
        [rec] = parsers.claude_records(_claude(usage={
            "input_tokens": 1, "output_tokens": 100,
            "output_tokens_details": {"thinking_tokens": 30}}), "/h")
        self.assertEqual((rec.event.output, rec.event.reasoning), (70, 30))

    def test_cache_creation_split(self):
        [rec] = parsers.claude_records(_claude(usage={
            "input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 50,
            "cache_creation": {"ephemeral_5m_input_tokens": 20, "ephemeral_1h_input_tokens": 40}}), "/h")
        self.assertEqual((rec.event.cache_write, rec.event.cache_write_1h), (60, 40))

    def test_rejected_lines(self):
        self.assertEqual(parsers.claude_records(b'{"type": "user"}\n', "/h"), [])
        self.assertEqual(parsers.claude_records(_claude(version="abc"), "/h"), [])
        self.assertEqual(parsers.claude_records(_claude(requestId=""), "/h"), [])
        self.assertEqual(parsers.claude_records(_claude(timestamp="nope"), "/h"), [])
        self.assertEqual(parsers.claude_records(_claude(usage={"input_tokens": 0, "output_tokens": 0}), "/h"), [])

    def test_logged_cost_fast_tier_sidechain(self):
        line = json.loads(_claude(costUSD=1.5, isSidechain=True))
        line["message"]["usage"]["speed"] = "fast"
        [rec] = parsers.claude_records(json.dumps(line).encode(), "/h")
        self.assertEqual((rec.event.cost, rec.event.tier, rec.sidechain), (1.5, "fast", True))

    def test_advisor_iterations(self):
        usage = {"input_tokens": 1, "output_tokens": 1, "iterations": [
            {"type": "message", "input_tokens": 5, "output_tokens": 5},
            {"type": "advisor_message", "model": "claude-fable-5", "input_tokens": 7, "output_tokens": 3},
        ]}
        recs = parsers.claude_records(_claude(usage=usage), "/h")
        self.assertEqual([r.key for r in recs], ["claude:msg1:req1", "claude:msg1:req1:advisor:0"])
        self.assertEqual(recs[1].event.model, "claude-fable-5")
        self.assertEqual(recs[1].msg, "msg1:advisor:0")


def _codex(kind, payload, ts="2026-10-03T10:00:00.000Z"):
    return {"timestamp": ts, "type": kind, "payload": payload}


def _tc(total, last=None, ts="2026-10-03T10:00:00.000Z"):
    info = {"total_token_usage": total}
    if last is not None:
        info["last_token_usage"] = last
    return _codex("event_msg", {"type": "token_count", "info": info}, ts)


class TestCodex(unittest.TestCase):
    def setUp(self):
        self.p = parsers.CodexFileParser(Path("/x/rollout-abc.jsonl"), "/c")

    def test_cumulative_totals_become_deltas_and_repeats_are_dropped(self):
        self.p.records(_codex("turn_context", {"model": "gpt-5.5", "cwd": "/w"}))
        t1 = {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10,
              "reasoning_output_tokens": 4, "total_tokens": 110}
        t2 = {"input_tokens": 150, "cached_input_tokens": 80, "output_tokens": 30,
              "reasoning_output_tokens": 4, "total_tokens": 180}
        [a] = self.p.records(_tc(t1, ts="2026-10-03T10:00:01Z"))
        self.assertEqual(a.key, "codex:rollout-abc:2026-10-03T10:00:01Z")
        self.assertEqual((a.event.input, a.event.cache_read, a.event.output, a.event.reasoning),
                         (40, 60, 6, 4))
        self.assertEqual((a.event.model, a.event.cwd, a.event.session), ("gpt-5.5", "/w", "rollout-abc"))
        [b] = self.p.records(_tc(t2, ts="2026-10-03T10:00:02Z"))
        self.assertEqual((b.event.input, b.event.cache_read, b.event.output), (30, 20, 20))
        # A replayed total emits nothing.
        self.assertEqual(self.p.records(_tc(t1, ts="2026-10-03T10:00:03Z")), [])

    def test_last_usage_wins_over_delta(self):
        total = {"input_tokens": 500, "output_tokens": 50}
        [r] = self.p.records(_tc(total, last={"input_tokens": 9, "output_tokens": 1}))
        self.assertEqual((r.event.input, r.event.output, r.event.model), (9, 1, "gpt-5"))

    def test_child_waits_for_its_own_turn(self):
        self.p.records(_codex("session_meta", {"forked_from_id": "parent", "timestamp": "2026-10-03T10:00:00Z"}))
        self.assertEqual(self.p.records(_tc({"input_tokens": 5, "output_tokens": 5})), [])
        self.p.records(_codex("event_msg", {"type": "task_started", "started_at": parse_timestamp("2026-10-03T10:00:30Z")}))
        [r] = self.p.records(_tc({"input_tokens": 8, "output_tokens": 6}, ts="2026-10-03T10:01:00Z"))
        self.assertEqual(r.event.input, 3)

    def test_records_mode_ignores_token_counts(self):
        [r] = self.p.records(_codex("token_usage_record", {
            "response_id": "resp_1", "usage": {"input_tokens": 10, "output_tokens": 2}}))
        self.assertEqual(r.key, "codex:resp:resp_1")
        self.assertEqual(self.p.records(_tc({"input_tokens": 99, "output_tokens": 9})), [])

    def test_a_malformed_record_does_not_suppress_token_counts(self):
        # No `usage`: the line can't count, so token_count lines stay live.
        self.assertEqual(self.p.records(_codex("token_usage_record", {
            "response_id": "resp_1"})), [])
        [a] = self.p.records(_tc({"input_tokens": 7, "output_tokens": 3},
                                 ts="2026-10-03T10:00:01Z"))
        self.assertEqual(a.event.input, 7)
        # An unparseable timestamp is the same.
        self.assertEqual(self.p.records(
            _codex("token_usage_record",
                   {"usage": {"input_tokens": 1, "output_tokens": 1}},
                   ts="not-a-time")), [])
        [b] = self.p.records(_tc({"input_tokens": 12, "output_tokens": 4},
                                 ts="2026-10-03T10:00:02Z"))
        self.assertEqual(b.event.input, 5)
        # A record that does count still flips the file into record mode.
        self.assertEqual(len(self.p.records(_codex("token_usage_record", {
            "response_id": "resp_2", "usage": {"input_tokens": 1, "output_tokens": 1}}))), 1)
        self.assertEqual(self.p.records(_tc({"input_tokens": 50, "output_tokens": 8})), [])

    def test_service_tier(self):
        self.p.records(_codex("event_msg", {"type": "thread_settings_applied",
                                            "thread_settings": {"service_tier": "fast"}}))
        [r] = self.p.records(_tc({"input_tokens": 1, "output_tokens": 1}))
        self.assertEqual(r.event.tier, "priority")
        self.assertIsNone(parsers.normalized_tier("Default"))

    def test_file_reader_skips_partial_last_line(self):
        d = Path(tempfile.mkdtemp())
        f = d / "rollout-z.jsonl"
        lines = [_codex("turn_context", {"model": "gpt-5.5"}),
                 _tc({"input_tokens": 3, "output_tokens": 1})]
        f.write_text("\n".join(json.dumps(x) for x in lines) + "\n" + '{"type": "event_msg"')
        recs = parsers.codex_file_records(f, "/c")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].event.model, "gpt-5.5")


if __name__ == "__main__":
    unittest.main()
