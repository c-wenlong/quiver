"""Tests for session CLI helpers (resume mapping, search)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quiver.sessions.commands import (
    _display_title,
    _filter_search,
    _launch_tool_name,
    _parse_session_args,
    _resume_cmd_args,
)


class SessionCommandsTest(unittest.TestCase):
    def test_antigravity_launches_gemini(self):
        self.assertEqual(_launch_tool_name("antigravity"), "gemini")
        self.assertEqual(_launch_tool_name("droid"), "droid")

    def test_resume_flags(self):
        s = SimpleNamespace(tool_name="droid", session_id="abc", agent="Droid")
        self.assertEqual(_resume_cmd_args(s), ["droid", "--resume", "abc"])

        s = SimpleNamespace(tool_name="antigravity", session_id="x", agent="Antigravity")
        with patch("builtins.print"):
            args = _resume_cmd_args(s)
        self.assertEqual(args, ["gemini"])

    def test_parse_search_flag(self):
        parsed = _parse_session_args(["20", "--search", "login"])
        self.assertIsNotNone(parsed)
        limit, agent, cwd, use, search = parsed
        self.assertEqual(limit, 20)
        self.assertEqual(search, "login")
        self.assertIsNone(agent)
        self.assertIsNone(cwd)
        self.assertIsNone(use)

        parsed = _parse_session_args(["-q", "quiver", "--here"])
        _, _, cwd, _, search = parsed
        self.assertEqual(search, "quiver")
        self.assertTrue(cwd)

    def test_parse_date_filters(self):
        parsed = _parse_session_args(["-d", "5"])
        self.assertEqual(parsed.days, 5)
        self.assertFalse(parsed.limit_explicit)

        parsed = _parse_session_args(["20", "--weeks", "3"])
        self.assertEqual(parsed.weeks, 3)
        self.assertEqual(parsed.limit, 20)
        self.assertTrue(parsed.limit_explicit)

        parsed = _parse_session_args(["-s", "2026-07-01", "-e", "2026-07-30"])
        self.assertEqual(parsed.start, "2026-07-01")
        self.assertEqual(parsed.end, "2026-07-30")

    def test_rejects_invalid_date_filters(self):
        with patch("builtins.print"):
            self.assertIsNone(_parse_session_args(["-d", "0"]))
            self.assertIsNone(_parse_session_args(["-w", "nope"]))
            self.assertIsNone(_parse_session_args(["-s", "2026-07-01"]))

    def test_filter_search(self):
        sessions = [
            SimpleNamespace(
                agent="Droid",
                tool_name="droid",
                path="/tmp/a",
                title="fix login",
                session_id="1",
            ),
            SimpleNamespace(
                agent="Codex",
                tool_name="codex",
                path="/tmp/b",
                title="refactor",
                session_id="2",
            ),
        ]
        hits = _filter_search(sessions, "login")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "fix login")

    def test_display_title_fallback(self):
        s = SimpleNamespace(title="", session_id="aaaaaaaa-bbbb-cccc")
        text = _display_title(s, 50)
        self.assertIn("aaaaaaaa", text)


if __name__ == "__main__":
    unittest.main()
