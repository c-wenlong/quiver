"""Cursor sessions, and what happens when a parser crashes.

`swe list` reported cursor at 0 sessions for as long as two NameErrors sat in
its parser. A commit refactoring the loop to os.scandir renamed the loop
variables and missed two references:

    session_id=uuid_dir     ->  uuid_entry.name
    path = enc_dir          ->  enc_entry.path

Both were swallowed by bare `except Exception` blocks, so a crashing parser
was indistinguishable from a harness with no history. These tests pin the
parser's real output, and pin that a failure is now reported rather than
silently returning nothing.
"""

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.sessions import failures
from quiver.sessions.parsers import parse_cursor


def _cursor_home(tmp: str, uuid: str = "f36fd647-90f5-454e-b460-1f37ce0653b9",
                 with_paths: bool = True) -> Path:
    """A cursor layout: projects/<enc>/agent-transcripts/<uuid>/<uuid>.jsonl"""
    home = Path(tmp)
    enc = home / ".cursor" / "projects" / "Users-kaichen-Desktop-proj"
    d = enc / "agent-transcripts" / uuid
    d.mkdir(parents=True)
    lines = [{"role": "user", "message": {"content": "fix the parser"}}]
    if with_paths:
        lines.append({"role": "assistant", "message": {"content": [
            {"input": {"path": str(home / "Desktop" / "proj" / "a.py")}},
            {"input": {"path": str(home / "Desktop" / "proj" / "b.py")}},
        ]}})
    (d / f"{uuid}.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n")
    return home


class CursorParserTest(unittest.TestCase):
    def setUp(self):
        failures.clear()

    def test_finds_a_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _cursor_home(tmp)
            with mock.patch.dict("os.environ", {"HOME": str(home)}):
                got = parse_cursor()
            self.assertEqual(len(got), 1)

    def test_session_id_is_the_transcript_directory_name(self):
        # The first NameError: session_id referenced a variable the scandir
        # refactor had renamed, so every session raised on construction.
        uuid = "aaaaaaaa-1111-2222-3333-444444444444"
        with tempfile.TemporaryDirectory() as tmp:
            home = _cursor_home(tmp, uuid=uuid)
            with mock.patch.dict("os.environ", {"HOME": str(home)}):
                got = parse_cursor()
            self.assertEqual(got[0].session_id, uuid)

    def test_path_falls_back_to_the_project_dir(self):
        # The second NameError: only reachable when a session mentions no file
        # paths, which is why fixing the first took cursor from 0 to 17 rather
        # than straight to 86.
        with tempfile.TemporaryDirectory() as tmp:
            home = _cursor_home(tmp, with_paths=False)
            with mock.patch.dict("os.environ", {"HOME": str(home)}):
                got = parse_cursor()
            self.assertEqual(len(got), 1)
            self.assertIn("Users-kaichen-Desktop-proj", got[0].path)

    def test_path_is_inferred_from_mentioned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _cursor_home(tmp)
            with mock.patch.dict("os.environ", {"HOME": str(home)}):
                got = parse_cursor()
            self.assertTrue(got[0].path.endswith("Desktop/proj"), got[0].path)

    def test_title_comes_from_the_first_user_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _cursor_home(tmp)
            with mock.patch.dict("os.environ", {"HOME": str(home)}):
                got = parse_cursor()
            self.assertIn("fix the parser", got[0].title)

    def test_a_parse_that_succeeds_records_no_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _cursor_home(tmp)
            with mock.patch.dict("os.environ", {"HOME": str(home)}):
                parse_cursor()
            self.assertEqual(failures.snapshot(), {})

    def test_missing_cursor_directory_is_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"HOME": tmp}):
                self.assertEqual(parse_cursor(), [])
            self.assertEqual(failures.snapshot(), {})


class FailureReportingTest(unittest.TestCase):
    def setUp(self):
        failures.clear()

    def test_a_raising_parser_is_recorded_not_swallowed(self):
        import quiver.sessions.aggregator as agg

        def boom():
            raise NameError("name 'uuid_dir' is not defined")

        with mock.patch.object(agg, "PARSER_REGISTRY",
                               [("cursor", boom, ("cursor",))]):
            self.assertEqual(agg._run_parser("cursor", boom), [])
        self.assertIn("cursor", failures.snapshot())
        self.assertIn("NameError", failures.snapshot()["cursor"])

    def test_one_broken_parser_does_not_stop_the_others(self):
        import quiver.sessions.aggregator as agg
        from quiver.sessions.models import Session

        def boom():
            raise ValueError("bad file")

        def fine():
            return [Session(timestamp=1, agent="OK", path="/p", title="t",
                            session_id="s", tool_name="ok")]

        with mock.patch.object(agg, "PARSER_REGISTRY",
                               [("broken", boom, ("broken",)),
                                ("ok", fine, ("ok",))]):
            got = agg._run_all_parsers()
        self.assertEqual([s.tool_name for s in got], ["ok"])
        self.assertIn("broken", failures.snapshot())

    def test_failures_are_printed_rather_than_looking_like_no_history(self):
        import quiver.sessions.aggregator as agg
        from quiver.sessions.commands import cmd_session

        def boom():
            raise NameError("name 'uuid_dir' is not defined")

        with mock.patch.object(agg, "PARSER_REGISTRY",
                               [("cursor", boom, ("cursor", "cs"))]):
            agg.invalidate_cache()
            failures.clear()
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_session([])
        out = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("parser(s) failed", out)
        self.assertIn("cursor", out)
        self.assertIn("NameError", out)

    def test_the_last_error_per_tool_wins(self):
        failures.record("x", ValueError("first"))
        failures.record("x", KeyError("second"))
        self.assertIn("KeyError", failures.snapshot()["x"])

    def test_clear_empties_the_record(self):
        failures.record("x", ValueError("boom"))
        failures.clear()
        self.assertEqual(failures.snapshot(), {})


if __name__ == "__main__":
    unittest.main()
