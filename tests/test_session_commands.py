"""Tests for session CLI helpers (resume mapping, search)."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quiver.console import visible_len
from quiver.sessions.commands import (
    _display_title,
    _filter_search,
    _launch_tool_name,
    _parse_session_args,
    _resume_cmd_args,
    cmd_session,
)
from quiver.console import strip_ansi
from quiver.sessions.models import Session


class SessionCommandsTest(unittest.TestCase):
    def test_launch_tool_defaults_to_tool_name(self):
        self.assertEqual(_launch_tool_name("droid"), "droid")

    def test_resume_flags(self):
        s = SimpleNamespace(tool_name="droid", session_id="abc", agent="Droid")
        self.assertEqual(_resume_cmd_args(s), ["droid", "--resume", "abc"])

        s = SimpleNamespace(tool_name="devin", session_id="bald-trust", agent="Devin")
        self.assertEqual(_resume_cmd_args(s), ["devin", "--resume", "bald-trust"])

        s = SimpleNamespace(tool_name="gemini", session_id="x", agent="Gemini")
        with patch("builtins.print") as note:
            args = _resume_cmd_args(s)
        self.assertEqual(args, ["gemini"])
        self.assertIn("/resume", note.call_args[0][0])

        s = SimpleNamespace(tool_name="grok", session_id="x", agent="Grok")
        with patch("builtins.print") as note:
            args = _resume_cmd_args(s)
        self.assertEqual(args, ["grok"])
        self.assertIn("launching in session directory", note.call_args[0][0])

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

    def test_display_title_renamed_is_italic_and_others_are_dim(self):
        italic, dim = "\033[3m", "\033[2m"
        renamed = SimpleNamespace(title="My name", session_id="x", title_source="rename")
        text = _display_title(renamed, 50)
        self.assertIn(italic, text)
        self.assertNotIn(dim, text)
        for source in ("", "auto"):
            plain = SimpleNamespace(title="My name", session_id="x", title_source=source)
            text = _display_title(plain, 50)
            self.assertIn(dim, text)
            self.assertNotIn(italic, text)
        legacy = SimpleNamespace(title="My name", session_id="x")  # no attribute
        self.assertIn(dim, _display_title(legacy, 50))

    def test_parse_interactive_flag(self):
        parsed = _parse_session_args(["-i"])
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.interactive)

        parsed = _parse_session_args(["--interactive", "5"])
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.interactive)
        self.assertEqual(parsed.limit, 5)

        with patch("builtins.print"):
            self.assertIsNone(_parse_session_args(["use", "2", "-i"]))


class SessionInteractiveTest(unittest.TestCase):
    def setUp(self):
        self._tmpdirs = [tempfile.mkdtemp() for _ in range(3)]
        self.sessions = [
            Session(
                timestamp=1000.0 * (i + 1),
                agent="Claude",
                path=self._tmpdirs[i],
                title=f"session {i}",
                session_id=f"id-{i}",
                tool_name="claude",
            )
            for i in range(3)
        ]

    @patch("quiver.sessions.commands.terminal_width", return_value=200)
    @patch("quiver.harness.commands.cmd_use")
    @patch("os.chdir")
    @patch("quiver.sessions.commands.pick_session")
    @patch("quiver.sessions.commands.get_all_sessions")
    def test_interactive_resumes_chosen_session(
        self, mock_get_all, mock_pick, mock_chdir, mock_cmd_use, _mock_width
    ):
        mock_get_all.return_value = self.sessions
        mock_pick.return_value = 1
        mock_cmd_use.return_value = 0

        result = cmd_session(["-i"])

        self.assertEqual(result, 0)
        self.assertTrue(mock_pick.called)
        rows_arg = mock_pick.call_args.args[0]
        header_arg = mock_pick.call_args.kwargs.get("header")
        self.assertEqual(len(rows_arg), 3)
        self.assertEqual(len(header_arg), 2)
        mock_chdir.assert_called_once_with(self._tmpdirs[1])
        self.assertTrue(mock_cmd_use.called)

    @patch("quiver.sessions.commands.terminal_width", return_value=200)
    @patch("quiver.harness.commands.cmd_use")
    @patch("os.chdir")
    @patch("quiver.sessions.commands.pick_session")
    @patch("quiver.sessions.commands.get_all_sessions")
    def test_interactive_cancelled_does_not_resume(
        self, mock_get_all, mock_pick, mock_chdir, mock_cmd_use, _mock_width
    ):
        mock_get_all.return_value = self.sessions
        mock_pick.return_value = None

        result = cmd_session(["-i"])

        self.assertEqual(result, 0)
        mock_chdir.assert_not_called()
        mock_cmd_use.assert_not_called()

    @patch("quiver.sessions.commands.terminal_width", return_value=100)
    @patch("quiver.harness.commands.cmd_use")
    @patch("os.chdir")
    @patch("quiver.sessions.commands.pick_session")
    @patch("quiver.sessions.commands.get_all_sessions")
    def test_interactive_rows_leave_room_for_the_picker_pointer(
        self, mock_get_all, mock_pick, mock_chdir, mock_cmd_use, _mock_width
    ):
        # The picker prefixes every row with a 2-char pointer + space, so a
        # row built at the full 100-column width would wrap once the
        # pointer is added and break the redraw. Rows must leave that
        # room, i.e. be no wider than 100 - 2 = 98.
        mock_get_all.return_value = self.sessions
        mock_pick.return_value = None

        cmd_session(["-i"])

        rows_arg = mock_pick.call_args.args[0]
        header_arg = mock_pick.call_args.kwargs.get("header")
        for line in rows_arg + header_arg:
            self.assertLessEqual(visible_len(line), 98)


if __name__ == "__main__":
    unittest.main()


class SessionPreviewTest(unittest.TestCase):
    """The space-bar peek reads the transcript tail through the report readers."""

    def setUp(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.home_patch = patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.addCleanup(self.temp.cleanup)
        path = self.home / ".claude/projects/-work-project/fork-a.jsonl"
        path.parent.mkdir(parents=True)
        records = [
            {"type": "user", "message": {"role": "user", "content": "fix the\nlogin bug"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Looking at auth.py now."},
                {"type": "tool_use", "name": "Read", "input": {"path": "auth.py"}},
            ]}},
            {"type": "user", "message": {"role": "user", "content": "ship it"}},
        ]
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.session = Session(
            timestamp=1.0, agent="Claude Code", path="/work/project",
            title="fix the login bug", session_id="fork-a", tool_name="claude",
        )

    def test_preview_is_the_conversation_without_labels_or_tool_detail(self):
        from quiver.sessions.commands import _session_preview

        lines = [strip_ansi(line) for line in _session_preview(self.session)]
        self.assertEqual(lines, [
            "4 messages",
            "~/.claude/projects/-work-project/fork-a.jsonl",
            "",
            "fix the",
            "login bug",
            "",
            "Looking at auth.py now.",
            "  called 1 tool",
            "",
            "ship it",
        ])

    def test_a_run_of_tool_calls_collapses_to_one_count(self):
        import json

        from quiver.sessions.commands import _session_preview

        path = self.home / ".claude/projects/-work-project/fork-a.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in [
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "On it."},
                {"type": "tool_use", "name": "Read", "input": {"path": "a.py"}},
                {"type": "tool_use", "name": "Read", "input": {"path": "b.py"}},
                {"type": "tool_use", "name": "Edit", "input": {"path": "c.py"}},
            ]}},
        ]))
        lines = [strip_ansi(line) for line in _session_preview(self.session)]
        self.assertIn("  called 3 tools", lines)
        self.assertEqual(1, sum(1 for line in lines if "called" in line))
        # No argument survives: their bulk is what made the view unreadable.
        self.assertFalse([line for line in lines if ".py" in line])

    def test_a_trailing_run_of_tool_calls_is_still_counted(self):
        import json

        from quiver.sessions.commands import _session_preview

        path = self.home / ".claude/projects/-work-project/fork-a.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in [
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Read", "input": {"path": "a.py"}},
            ]}},
        ]))
        lines = [strip_ansi(line) for line in _session_preview(self.session)]
        self.assertEqual("  called 1 tool", lines[-1])

    def test_a_prompt_is_painted_and_nothing_else_is(self):
        from quiver.console import COLORS

        from quiver.sessions.commands import _session_preview

        lines = _session_preview(self.session)
        self.assertNotIn("\x1b[", lines[1])                 # source path, never cut
        painted = [strip_ansi(x) for x in lines if x.startswith(COLORS["user_bg"])]
        self.assertEqual(["fix the", "login bug", "ship it"], painted)

    def test_an_assistant_body_is_rendered_as_markdown(self):
        import json

        from quiver.sessions.commands import _session_preview

        path = self.home / ".claude/projects/-work-project/fork-a.jsonl"
        path.write_text(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "The **root cause** is here."},
            ]},
        }) + "\n")
        body = _session_preview(self.session)[-1]
        self.assertIn("\x1b[", body)                # markdown now carries colour
        self.assertEqual(strip_ansi(body), "The root cause is here.")

    def test_missing_transcript_explains_instead_of_raising(self):
        from quiver.sessions.commands import _session_preview

        self.session.session_id = "nope"
        lines = _session_preview(self.session)
        self.assertEqual(lines, ["(no preview: transcript file not found)"])

    @patch("quiver.sessions.commands.terminal_width", return_value=200)
    @patch("quiver.sessions.commands.pick_session", return_value=None)
    @patch("quiver.sessions.commands.get_all_sessions")
    def test_interactive_hands_the_picker_a_preview_for_each_row(
        self, mock_get_all, mock_pick, _width
    ):
        mock_get_all.return_value = [self.session]
        self.assertEqual(cmd_session(["-i"]), 0)
        preview = mock_pick.call_args.kwargs["preview"]
        self.assertEqual(strip_ansi(preview(0)[-1]), "ship it")
        with patch("quiver.sessions.commands._session_preview") as again:
            preview(0)
            again.assert_not_called()          # memoised per picker run
