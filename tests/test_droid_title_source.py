import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.parsers import parse_droid


SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _user_message(text: str) -> dict:
    return {
        "type": "message",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }


def _parse_with(session_start: dict, tmp: str):
    """Write one droid transcript under a fake ~/.factory/sessions and parse it."""
    sessions_root = Path(tmp) / "sessions"
    project_dir = sessions_root / "-Users-kaichen-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    jsonl = project_dir / f"{SESSION_ID}.jsonl"
    records = [
        session_start,
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [],
                "hookEventName": "SessionStart",
                "visibility": "user_only",
            },
        },
        _user_message("Please implement parse_droid"),
    ]
    with open(jsonl, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    def fake_expanduser(path: str) -> str:
        if path in ("~/.factory/sessions/", "~/.factory/sessions"):
            return str(sessions_root) + "/"
        return os.path.expanduser(path)

    with patch("quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
        return parse_droid()


class DroidTitleSourceTest(unittest.TestCase):
    def test_manual_title_is_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "title": "My renamed session",
                    "isSessionTitleManuallySet": True,
                    "sessionTitleAutoStage": "first_message",
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "My renamed session")
        self.assertEqual(sessions[0].title_source, "rename")

    def test_generated_title_is_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "title": "Wire droid session support",
                    "isSessionTitleManuallySet": False,
                    "sessionTitleAutoStage": "first_file_edit",
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Wire droid session support")
        self.assertEqual(sessions[0].title_source, "auto")

    def test_title_without_flag_is_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "title": "Titled but unflagged",
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Titled but unflagged")
        self.assertEqual(sessions[0].title_source, "auto")

    def test_new_session_placeholder_falls_back_to_first_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "title": "New Session",
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Please implement parse_droid")
        self.assertEqual(sessions[0].title_source, "")

    def test_missing_title_key_falls_back_to_first_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Please implement parse_droid")
        self.assertEqual(sessions[0].title_source, "")

    def test_manual_flag_with_placeholder_title_still_falls_back(self):
        # A stray flag on the placeholder must not promote "New Session".
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "title": "New Session",
                    "isSessionTitleManuallySet": True,
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Please implement parse_droid")
        self.assertEqual(sessions[0].title_source, "")

    def test_non_bool_flag_is_not_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = _parse_with(
                {
                    "type": "session_start",
                    "id": SESSION_ID,
                    "title": "Odd flag value",
                    "isSessionTitleManuallySet": "yes",
                    "cwd": "/Users/kaichen/project",
                },
                tmp,
            )
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].title, "Odd flag value")
        self.assertEqual(sessions[0].title_source, "auto")


if __name__ == "__main__":
    unittest.main()
