"""Tests for ``quiver.sessions.status`` — the ``swe session`` STATUS column.

``~`` is redirected to a per-test temp dir via ``patch.dict`` on ``HOME``;
``now`` is fixed through the ``now`` parameter so the ACTIVE_WINDOW_S
aliveness check never depends on wall clock.
"""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from quiver.console import strip_ansi

from quiver.sessions.models import Session
from quiver.sessions.status import (
    ACTIVE,
    ACTIVE_WINDOW_S,
    DONE,
    ERROR,
    FOLLOWUP,
    INTERRUPTED,
    UNKNOWN,
    needs_followup,
    session_status,
    session_statuses,
)

NOW = 1_700_000_000.0  # seconds
NOW_MS = NOW * 1000


def _session(tool_name, session_id="sid", age_s=0):
    return Session(
        timestamp=NOW_MS - age_s * 1000,
        agent=tool_name,
        path="/tmp/proj",
        title="t",
        session_id=session_id,
        tool_name=tool_name,
    )


def _assistant(text=None, tool_use=False, api_error=False, stop="end_turn"):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_use:
        content.append({"type": "tool_use", "name": "Bash", "input": {}})
    rec = {
        "type": "assistant",
        "message": {"content": content, "stop_reason": stop},
    }
    if api_error:
        rec["isApiErrorMessage"] = True
    return rec


def _user(text=None, tool_result=False, meta=False):
    content = [{"type": "tool_result", "content": "ok"}] if tool_result else (
        [{"type": "text", "text": text}] if text is not None else []
    )
    rec = {"type": "user", "message": {"content": content}}
    if meta:
        rec["isMeta"] = True
    return rec


def _cursor_assistant(text=None, tool_use=False):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_use:
        content.append({"type": "tool_use", "name": "run"})
    return {"role": "assistant", "message": {"content": content}}


class StatusTestBase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._patch = patch.dict(os.environ, {"HOME": self.home})
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write_claude(self, sid, records):
        d = os.path.join(self.home, ".claude", "projects", "-proj")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, sid + ".jsonl")
        with open(path, "w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        return path

    def _write_claude_session_pid(self, sid, pid, name="1"):
        d = os.path.join(self.home, ".claude", "sessions")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name + ".json"), "w") as fh:
            json.dump({"pid": pid, "sessionId": sid}, fh)

    def _write_claude_job(self, sid, state, name="abc"):
        d = os.path.join(self.home, ".claude", "jobs", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "state.json"), "w") as fh:
            json.dump({"sessionId": sid, "state": state}, fh)

    def _write_cursor(self, sid, records):
        d = os.path.join(
            self.home, ".cursor", "projects", "enc",
            "agent-transcripts", sid,
        )
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")

    def _write_devin_db(self, sessions, nodes):
        d = os.path.join(self.home, ".local", "share", "devin", "cli")
        os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(os.path.join(d, "sessions.db"))
        conn.execute(
            "CREATE TABLE sessions (id TEXT, main_chain_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE message_nodes "
            "(node_id TEXT, parent_node_id TEXT, session_id TEXT, "
            " chat_message TEXT)"
        )
        conn.executemany(
            "INSERT INTO sessions (id, main_chain_id) VALUES (?, ?)",
            sessions,
        )
        conn.executemany(
            "INSERT INTO message_nodes "
            "(node_id, parent_node_id, session_id, chat_message) "
            "VALUES (?, ?, ?, ?)",
            nodes,
        )
        conn.commit()
        conn.close()


class ClaudeStatusTest(StatusTestBase):
    def test_done_when_last_record_is_plain_end_turn(self):
        self._write_claude("s1", [_assistant("Merged. CI green.")])
        s = _session("claude", "s1")
        self.assertEqual(DONE, session_status(s, now=NOW))

    def test_followup_when_last_text_asks_a_question(self):
        self._write_claude(
            "s2", [_assistant("Two options fit. Which one do you want?")]
        )
        s = _session("claude", "s2")
        self.assertEqual(FOLLOWUP, session_status(s, now=NOW))

    def test_error_on_api_error_message(self):
        self._write_claude(
            "s3", [_assistant(api_error=True, stop=None)]
        )
        s = _session("claude", "s3")
        self.assertEqual(ERROR, session_status(s, now=NOW))

    def test_midturn_active_when_timestamp_is_fresh(self):
        self._write_claude("s4", [_assistant(tool_use=True), _user(tool_result=True)])
        s = _session("claude", "s4", age_s=0)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))

    def test_midturn_interrupted_when_timestamp_is_stale(self):
        self._write_claude("s4b", [_assistant(tool_use=True), _user(tool_result=True)])
        s = _session("claude", "s4b", age_s=600)
        self.assertEqual(INTERRUPTED, session_status(s, now=NOW))

    def test_interrupted_by_user_marker(self):
        self._write_claude(
            "s5",
            [_assistant("Working on it.", stop="tool_use", tool_use=True),
             _user("[Request interrupted by user]")],
        )
        s = _session("claude", "s5", age_s=9999)
        self.assertEqual(INTERRUPTED, session_status(s, now=NOW))

    def test_live_pid_registry_counts_as_active(self):
        self._write_claude("s6", [_assistant(tool_use=True), _user(tool_result=True)])
        self._write_claude_session_pid("s6", os.getpid())
        s = _session("claude", "s6", age_s=9999)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))

    def test_dead_pid_is_not_active(self):
        self._write_claude("s6b", [_assistant(tool_use=True), _user(tool_result=True)])
        self._write_claude_session_pid("s6b", 2 ** 22)
        s = _session("claude", "s6b", age_s=9999)
        self.assertEqual(INTERRUPTED, session_status(s, now=NOW))

    def test_blocked_bg_job_overrides_finished(self):
        self._write_claude("s7", [_assistant("All done here.")])
        self._write_claude_job("s7", "blocked")
        s = _session("claude", "s7", age_s=9999)
        self.assertEqual(FOLLOWUP, session_status(s, now=NOW))

    def test_failed_bg_job_is_error(self):
        self._write_claude("s7f", [_assistant("All done here.")])
        self._write_claude_job("s7f", "failed")
        s = _session("claude", "s7f", age_s=9999)
        self.assertEqual(ERROR, session_status(s, now=NOW))

    def test_done_bg_job_falls_through_to_transcript(self):
        self._write_claude("s7d", [_assistant("All done here.")])
        self._write_claude_job("s7d", "done")
        s = _session("claude", "s7d", age_s=9999)
        self.assertEqual(DONE, session_status(s, now=NOW))

    def test_bookkeeping_records_after_end_turn_are_skipped(self):
        self._write_claude(
            "s8",
            [_assistant("Merged. CI green."),
             {"type": "system", "subtype": "turn_duration"},
             {"type": "last-prompt", "prompt": "x"},
             {"type": "mode", "mode": "normal"}],
        )
        s = _session("claude", "s8", age_s=9999)
        self.assertEqual(DONE, session_status(s, now=NOW))

    def test_slash_command_user_record_is_skipped(self):
        self._write_claude(
            "s9",
            [_assistant("Merged. CI green."),
             _user("<command-name>/clear</command-name>")],
        )
        s = _session("claude", "s9", age_s=9999)
        self.assertEqual(DONE, session_status(s, now=NOW))


    def test_huge_decisive_record_is_not_lost_to_the_window(self):
        # A >64KiB tool_result lands as the partial first line of the
        # smallest window; _walk_tail must widen until the walk decides.
        big = _user(tool_result=True)
        big["message"]["content"] = [
            {"type": "tool_result", "content": "x" * 100_000}
        ]
        self._write_claude("big1", [_assistant("ok"), big])
        s = _session("claude", "big1", age_s=0)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))

    def test_huge_decisive_record_with_trailing_bookkeeping(self):
        big = _user(tool_result=True)
        big["message"]["content"] = [
            {"type": "tool_result", "content": "x" * 100_000}
        ]
        self._write_claude(
            "big2", [_assistant("ok"), big, {"type": "last-prompt"}]
        )
        s = _session("claude", "big2", age_s=0)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))


class CursorStatusTest(StatusTestBase):
    def test_done_after_turn_ended_success(self):
        self._write_cursor(
            "c1",
            [{"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
             _cursor_assistant("Done."),
             {"type": "turn_ended", "status": "success"}],
        )
        s = _session("cursor", "c1", age_s=9999)
        self.assertEqual(DONE, session_status(s, now=NOW))

    def test_followup_after_turn_ended_success_with_question(self):
        self._write_cursor(
            "c2",
            [_cursor_assistant("All wired up. Should I proceed?"),
             {"type": "turn_ended", "status": "success"}],
        )
        s = _session("cursor", "c2", age_s=9999)
        self.assertEqual(FOLLOWUP, session_status(s, now=NOW))

    def test_error_on_turn_ended_error(self):
        self._write_cursor(
            "c3",
            [_cursor_assistant("trying"),
             {"type": "turn_ended", "status": "error", "error": "boom"}],
        )
        s = _session("cursor", "c3", age_s=9999)
        self.assertEqual(ERROR, session_status(s, now=NOW))

    def test_interrupted_on_turn_ended_aborted(self):
        self._write_cursor(
            "c4",
            [_cursor_assistant("trying"),
             {"type": "turn_ended", "status": "aborted"}],
        )
        s = _session("cursor", "c4", age_s=9999)
        self.assertEqual(INTERRUPTED, session_status(s, now=NOW))

    def test_text_scan_stops_at_the_previous_turn_boundary(self):
        # The finished turn produced no text; the earlier turn's question
        # belongs to a turn the user already answered.
        self._write_cursor(
            "c6",
            [_cursor_assistant("Which db do you want?"),
             {"type": "turn_ended", "status": "success"},
             {"role": "user", "message": {"content": [{"type": "text", "text": "postgres"}]}},
             _cursor_assistant(tool_use=True),
             {"type": "turn_ended", "status": "success"}],
        )
        s = _session("cursor", "c6", age_s=9999)
        self.assertEqual(DONE, session_status(s, now=NOW))

    def test_midturn_when_last_record_is_assistant(self):
        self._write_cursor("c5", [_cursor_assistant(tool_use=True)])
        s = _session("cursor", "c5", age_s=0)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))


class DevinStatusTest(StatusTestBase):
    def _mk(self, role, **kw):
        return json.dumps({"role": role, **kw})

    def test_done_when_head_is_plain_assistant(self):
        self._write_devin_db(
            [("d1", "n2")],
            [("n1", None, "d1", self._mk("user", content="hi")),
             ("n2", "n1", "d1", self._mk("assistant", content="All set."))],
        )
        s = _session("devin", "d1", age_s=9999)
        self.assertEqual(DONE, session_status(s, now=NOW))

    def test_active_when_head_is_tool_and_timestamp_fresh(self):
        self._write_devin_db(
            [("d2", "n2")],
            [("n1", None, "d2", self._mk("assistant", tool_calls=[{"id": "t"}])),
             ("n2", "n1", "d2", self._mk("tool", tool_call_id="t", content="out"))],
        )
        s = _session("devin", "d2", age_s=0)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))

    def test_interrupted_when_tool_head_is_stale(self):
        self._write_devin_db(
            [("d2b", "n2")],
            [("n2", "n1", "d2b", self._mk("tool", tool_call_id="t", content="out"))],
        )
        s = _session("devin", "d2b", age_s=9999)
        self.assertEqual(INTERRUPTED, session_status(s, now=NOW))

    def test_active_when_assistant_head_has_tool_calls(self):
        self._write_devin_db(
            [("d3", "n1")],
            [("n1", None, "d3", self._mk("assistant", tool_calls=[{"id": "t"}]))],
        )
        s = _session("devin", "d3", age_s=0)
        self.assertEqual(ACTIVE, session_status(s, now=NOW))

    def test_null_main_chain_falls_back_per_session(self):
        # node_id values are shared across sessions; the filter must hold.
        self._write_devin_db(
            [("d4", None), ("d5", None)],
            [("n1", None, "d5", self._mk("assistant", content="other session?")),
             ("n1", None, "d4", self._mk("assistant", content="All set."))],
        )
        s4 = _session("devin", "d4", age_s=9999)
        s5 = _session("devin", "d5", age_s=9999)
        self.assertEqual(DONE, session_status(s4, now=NOW))
        self.assertEqual(FOLLOWUP, session_status(s5, now=NOW))


class MiscStatusTest(StatusTestBase):
    def test_unknown_tool_shows_unknown(self):
        s = _session("codex", "x")
        self.assertEqual(UNKNOWN, session_status(s, now=NOW))

    def test_statuses_returns_one_label_per_input_in_order(self):
        self._write_claude("m1", [_assistant("Done. Bye.")])
        self._write_cursor(
            "m2", [{"type": "turn_ended", "status": "error"}]
        )
        sessions = [
            _session("claude", "m1", age_s=9999),
            _session("codex", "x"),
            _session("cursor", "m2", age_s=9999),
        ]
        self.assertEqual(
            [DONE, UNKNOWN, ERROR],
            session_statuses(sessions, now=NOW),
        )


class TailRecordsTest(unittest.TestCase):
    def test_small_file_is_complete(self):
        from quiver.sessions.status import _tail_records

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"a": 1}) + "\n" + json.dumps({"b": 2}) + "\n")
            path = fh.name
        records, complete = _tail_records(path, 65536)
        self.assertTrue(complete)
        self.assertEqual([{"a": 1}, {"b": 2}], records)

    def test_large_file_is_incomplete_and_drops_partial_first_line(self):
        from quiver.sessions.status import _tail_records

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"head": "x" * 500}) + "\n")
            fh.write(json.dumps({"tail": 1}) + "\n")
            path = fh.name
        records, complete = _tail_records(path, 64)
        self.assertFalse(complete)
        self.assertEqual([{"tail": 1}], records)


class StatusLegendTest(unittest.TestCase):
    def test_legend_is_none_when_everything_is_unknown(self):
        from quiver.sessions.commands import _status_legend

        self.assertIsNone(_status_legend(["", "", ""]))
        self.assertIsNone(_status_legend([]))

    def test_legend_lists_all_five_glyphs(self):
        from quiver.sessions.commands import _status_legend

        line = _status_legend(["done"])
        plain = strip_ansi(line)
        for glyph in ("●", "✓", "?", "✗", "■"):
            self.assertIn(glyph, plain)
        for label in ("active", "done", "followup", "error", "interrupted"):
            self.assertIn(label, plain)

    def test_cmd_session_prints_legend_only_when_a_status_is_known(self):
        from quiver.sessions.commands import cmd_session

        sessions = [_session("claude", "l1"), _session("codex", "l2")]
        for statuses, expect_legend in (
            (["done", ""], True),
            (["", ""], False),
        ):
            with patch(
                "quiver.sessions.commands.get_all_sessions",
                return_value=sessions,
            ), patch(
                "quiver.sessions.commands.session_statuses",
                return_value=statuses,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    cmd_session([])
            plain = strip_ansi(buf.getvalue())
            legend_lines = [
                ln for ln in plain.splitlines() if "·" in ln and "active" in ln
            ]
            if expect_legend:
                self.assertEqual(1, len(legend_lines))
                self.assertLess(
                    plain.index(legend_lines[0]), plain.index("LAST ACTIVE")
                )
            else:
                self.assertEqual([], legend_lines)


class NeedsFollowupTest(unittest.TestCase):
    def test_cases(self):
        cases = {
            "Should I proceed?": True,
            "Done.": False,
            "Let me know which branch to use.": True,
            "Needs input: pick a colour": True,
            "Blocked: waiting on CI. Nothing needed from you.": False,
            "OK?": False,
            "```\nx?\n```": False,
            "Waiting for your approval before pushing.": True,
        }
        for text, expected in cases.items():
            self.assertEqual(expected, needs_followup(text), text)


if __name__ == "__main__":
    unittest.main()
