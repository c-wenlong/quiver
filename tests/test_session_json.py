"""`swe session --json`: the session listing the macOS app reads.

Runs the real CLI against a throwaway HOME holding fixture transcripts, for
the reasons given in test_json_output.py. Every expected value below is a
literal taken from the fixture, never recomputed the way the code does.

A session's timestamp is the transcript file's mtime, so each fixture pins
its mtime with os.utime to make "started_at" a known number.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.test_json_output import decode, run_swe

CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
CODEX_UUID = "0199aaaa-bbbb-cccc-dddd-eeeeffff0001"
CODEX_STEM = f"rollout-2026-09-01T10-00-00-{CODEX_UUID}"

# Whole seconds well in the past, so no session reads as live.
CLAUDE_MTIME = 1_790_000_000
CODEX_MTIME = 1_780_000_000


def write_jsonl(path: Path, records: list[dict], mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(rec) + "\n" for rec in records))
    os.utime(path, (mtime, mtime))


def add_claude_session(home: Path, session_id: str = CLAUDE_ID,
                       cwd: str = "/work/proj", prompt: str = "fix the login bug",
                       mtime: int = CLAUDE_MTIME,
                       extra: tuple[dict, ...] = ()) -> None:
    write_jsonl(
        home / ".claude" / "projects" / "-work-proj" / f"{session_id}.jsonl",
        [
            {"type": "user", "cwd": cwd,
             "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "message": {
                "content": [{"type": "text", "text": "Merged. CI green."}],
                "stop_reason": "end_turn"}},
            *extra,
        ],
        mtime,
    )


def add_codex_session(home: Path, stem: str = CODEX_STEM,
                      cwd: str = "/work/other", prompt: str = "add dark mode",
                      mtime: int = CODEX_MTIME) -> None:
    write_jsonl(
        home / ".codex" / "sessions" / "2026" / "09" / "01" / f"{stem}.jsonl",
        [
            {"type": "session_meta", "payload": {"cwd": cwd}},
            {"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": prompt}]}},
        ],
        mtime,
    )


class SessionJsonTestBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def sessions(self, *args: str) -> list[dict]:
        result = run_swe(self.home, "session", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        return doc["data"]["sessions"]


class ClaudeSessionJsonTest(SessionJsonTestBase):
    def test_a_claude_session_is_one_row_with_its_identity(self):
        add_claude_session(self.home)
        rows = self.sessions()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], CLAUDE_ID)
        self.assertEqual(row["harness"], "claude")
        self.assertEqual(row["agent"], "Claude Code")
        self.assertEqual(row["cwd"], "/work/proj")
        self.assertEqual(row["title"], "fix the login bug")

    def test_started_at_is_epoch_seconds_not_milliseconds(self):
        add_claude_session(self.home)
        self.assertEqual(self.sessions()[0]["started_at"], 1_790_000_000.0)

    def test_status_comes_from_the_transcript(self):
        add_claude_session(self.home)
        self.assertEqual(self.sessions()[0]["status"], "done")

    def test_claude_resume_is_the_resume_flag_in_the_session_directory(self):
        add_claude_session(self.home)
        self.assertEqual(
            self.sessions()[0]["resume"],
            {"argv": ["claude", "--resume", CLAUDE_ID], "cwd": "/work/proj"})


class TitleSourceJsonTest(SessionJsonTestBase):
    def test_a_first_prompt_title_has_no_source(self):
        add_claude_session(self.home)
        self.assertIsNone(self.sessions()[0]["title_source"])

    def test_a_user_rename_is_reported_as_rename(self):
        add_claude_session(self.home, extra=(
            {"type": "custom-title", "customTitle": "login rework"},))
        row = self.sessions()[0]
        self.assertEqual(row["title"], "login rework")
        self.assertEqual(row["title_source"], "rename")

    def test_a_harness_generated_title_is_reported_as_auto(self):
        add_claude_session(self.home, extra=(
            {"type": "ai-title", "aiTitle": "Fix login bug"},))
        row = self.sessions()[0]
        self.assertEqual(row["title"], "Fix login bug")
        self.assertEqual(row["title_source"], "auto")


def add_grok_session(home: Path) -> None:
    """Grok can be launched in a directory but has no resume flag."""
    session = home / ".grok" / "sessions" / "%2Fwork%2Fgrokked" / "grok-sess-1"
    session.mkdir(parents=True)
    (session / "summary.json").write_text(json.dumps({
        "generated_title": "hello grok",
        "created_at": "2026-07-06T12:17:43Z",
    }))
    (session / "prompt_context.json").write_text(
        json.dumps({"working_directory": "/work/grokked"}))


class UnknownCapabilityJsonTest(SessionJsonTestBase):
    def test_a_harness_without_resume_has_null_resume_and_status(self):
        add_grok_session(self.home)
        row = self.sessions()[0]
        self.assertEqual(row["harness"], "grok")
        self.assertEqual(row["id"], "grok-sess-1")
        self.assertEqual(row["cwd"], "/work/grokked")
        self.assertIsNone(row["resume"])
        self.assertIsNone(row["status"])


class CodexSessionJsonTest(SessionJsonTestBase):
    def test_codex_resumes_by_thread_uuid_not_by_rollout_stem(self):
        add_codex_session(self.home)
        row = self.sessions()[0]
        self.assertEqual(row["id"], CODEX_STEM)
        self.assertEqual(row["harness"], "codex")
        self.assertEqual(row["agent"], "Codex CLI")
        self.assertEqual(row["cwd"], "/work/other")
        self.assertEqual(row["started_at"], 1_780_000_000.0)
        self.assertEqual(
            row["resume"],
            {"argv": ["codex", "resume", CODEX_UUID], "cwd": "/work/other"})

    def test_a_codex_rollout_with_no_uuid_cannot_resume(self):
        add_codex_session(self.home, stem="rollout-without-a-thread-id")
        self.assertIsNone(self.sessions()[0]["resume"])


class FilterJsonTest(SessionJsonTestBase):
    def setUp(self):
        super().setUp()
        add_claude_session(self.home)
        add_codex_session(self.home)

    def test_rows_are_newest_first(self):
        self.assertEqual(
            [row["harness"] for row in self.sessions()], ["claude", "codex"])

    def test_agent_filter_keeps_only_that_harness(self):
        rows = self.sessions("--agent=codex")
        self.assertEqual([row["harness"] for row in rows], ["codex"])

    def test_agent_alias_filters_too(self):
        rows = self.sessions("--agent=cc")
        self.assertEqual([row["harness"] for row in rows], ["claude"])

    def test_search_filters_by_title(self):
        rows = self.sessions("-q", "dark mode")
        self.assertEqual([row["harness"] for row in rows], ["codex"])

    def test_date_range_keeps_only_sessions_inside_it(self):
        rows = self.sessions("--start=2026-09-01", "--end=2026-09-30")
        self.assertEqual([row["harness"] for row in rows], ["claude"])
        rows = self.sessions("--start=2026-05-01", "--end=2026-06-30")
        self.assertEqual([row["harness"] for row in rows], ["codex"])

    def test_limit_caps_the_rows(self):
        rows = self.sessions("1")
        self.assertEqual([row["harness"] for row in rows], ["claude"])


class EmptyJsonTest(SessionJsonTestBase):
    def test_a_home_with_no_sessions_is_an_empty_list(self):
        result = run_swe(self.home, "session", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            decode(result), {"schema": 1, "data": {"sessions": []}})


class RefusalJsonTest(SessionJsonTestBase):
    """A failed run must leave stdout empty: the app decodes it whole."""

    def assertRefused(self, *args: str) -> None:
        add_claude_session(self.home)
        result = run_swe(self.home, "session", *args)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_an_unknown_argument_is_reported_on_stderr(self):
        self.assertRefused("--json", "--bogus")

    def test_use_cannot_be_combined_with_json(self):
        self.assertRefused("use", "1", "--json")

    def test_the_picker_never_runs_under_json(self):
        self.assertRefused("--json", "-i")


if __name__ == "__main__":
    unittest.main()
