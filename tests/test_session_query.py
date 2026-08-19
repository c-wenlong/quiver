import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quiver.sessions.models import Session
from quiver.sessions.query import SessionQuery, calendar_range_ms, resolve_project_root


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class CalendarRangeTest(unittest.TestCase):
    def test_days_use_local_calendar_boundaries_across_dst(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2024, 3, 10, 15, 30, tzinfo=zone)

        start_ms, end_ms = calendar_range_ms(days=2, now=now, zone=zone)

        self.assertEqual(start_ms, _ms(datetime(2024, 3, 9, tzinfo=zone)))
        self.assertEqual(end_ms, _ms(datetime(2024, 3, 11, tzinfo=zone)) - 1)
        self.assertEqual(end_ms - start_ms + 1, 47 * 60 * 60 * 1000)

    def test_weeks_include_today_and_preceding_dates(self):
        zone = ZoneInfo("Asia/Singapore")
        now = datetime(2025, 1, 15, 12, tzinfo=zone)

        start_ms, end_ms = calendar_range_ms(weeks=1, now=now, zone=zone)

        self.assertEqual(start_ms, _ms(datetime(2025, 1, 9, tzinfo=zone)))
        self.assertEqual(end_ms, _ms(datetime(2025, 1, 16, tzinfo=zone)) - 1)

    def test_explicit_range_end_is_inclusive(self):
        zone = ZoneInfo("UTC")
        start_ms, end_ms = calendar_range_ms(
            start="2025-02-01", end="2025-02-02", zone=zone
        )

        self.assertEqual(start_ms, _ms(datetime(2025, 2, 1, tzinfo=zone)))
        self.assertEqual(end_ms, _ms(datetime(2025, 2, 3, tzinfo=zone)) - 1)
        sessions = [
            Session(end_ms, "codex", "/tmp", session_id="included"),
            Session(end_ms + 1, "codex", "/tmp", session_id="excluded"),
        ]
        self.assertEqual(
            [session.session_id for session in SessionQuery(start_ms, end_ms).apply(sessions)],
            ["included"],
        )

    def test_rejects_mixed_incomplete_and_invalid_ranges(self):
        invalid = (
            {"days": 1, "weeks": 1},
            {"days": 1, "start": "2025-01-01", "end": "2025-01-02"},
            {"start": "2025-01-01"},
            {"start": "2025-01-02", "end": "2025-01-01"},
            {"days": 0},
            {"weeks": -1},
            {"start": "01/01/2025", "end": "2025-01-02"},
            {},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                calendar_range_ms(**arguments)


class SessionQueryTest(unittest.TestCase):
    def test_composes_filters_and_matches_metadata_case_insensitively(self):
        sessions = [
            Session(300, "Claude", "/work/app/api", "Fix LOGIN", "abc", "claude"),
            Session(250, "Claude", "/work/app", "unrelated", "def", "claude"),
            Session(200, "Codex", "/work/app", "fix login", "ghi", "codex"),
            Session(150, "Claude", "/elsewhere", "fix login", "jkl", "claude"),
        ]
        query = SessionQuery(
            start_ms=100,
            end_ms=300,
            agent="cc",
            cwd="/work/app",
            search="login",
        )

        self.assertEqual([session.session_id for session in query.apply(sessions)], ["abc"])

    def test_search_matches_all_current_metadata_fields(self):
        session = Session(100, "Claude", "/repo", "Implement parser", "session-42", "cc")
        for term in ("CLAUDE", "CC", "/REPO", "PARSER", "SESSION-42"):
            with self.subTest(term=term):
                self.assertEqual(SessionQuery(search=term).apply([session]), [session])

    def test_limit_is_applied_after_filters_and_newest_first(self):
        sessions = [
            Session(100, "codex", "/repo", title="match", session_id="old"),
            Session(400, "codex", "/repo", title="other", session_id="newest"),
            Session(300, "codex", "/repo", title="match", session_id="new"),
            Session(200, "codex", "/repo", title="match", session_id="middle"),
        ]

        result = SessionQuery(search="match", limit=2).apply(sessions)

        self.assertEqual([session.session_id for session in result], ["new", "middle"])

    def test_query_is_immutable_and_validates_bounds_and_limit(self):
        query = SessionQuery(limit=1)
        with self.assertRaises(Exception):
            query.limit = 2
        with self.assertRaises(ValueError):
            SessionQuery(start_ms=1)
        with self.assertRaises(ValueError):
            SessionQuery(start_ms=2, end_ms=1)
        with self.assertRaises(ValueError):
            SessionQuery(limit=0)


class ResolveProjectRootTest(unittest.TestCase):
    def test_returns_git_root_for_nested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            nested = root / "src" / "pkg"
            nested.mkdir(parents=True)
            subprocess.run(
                ["git", "-C", os.fspath(root), "init", "-q"],
                check=True,
                capture_output=True,
            )

            self.assertEqual(resolve_project_root(nested), str(root.resolve()))

    def test_non_git_path_falls_back_to_recorded_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp) / "plain"
            working_dir.mkdir()

            self.assertEqual(resolve_project_root(working_dir), str(working_dir.resolve()))


if __name__ == "__main__":
    unittest.main()
