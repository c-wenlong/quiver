"""parse_pi sets ``title_source`` from pi's ``session_info`` rename records."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.parsers import parse_pi


def _session_record():
    return {"type": "session", "id": "s1", "cwd": "/Users/kaichen/project"}


def _user_message(text: str):
    return {
        "type": "message",
        "id": 1,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _session_info(name):
    return {
        "type": "session_info",
        "id": 9,
        "parentId": 1,
        "timestamp": "2026-09-01T00:00:00.000Z",
        "name": name,
    }


class ParsePiTitleSourceTest(unittest.TestCase):
    def _run(self, records):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            project_dir = sessions_root / "--Users-kaichen-project--"
            project_dir.mkdir(parents=True)
            jsonl = project_dir / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
            with open(jsonl, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")

            def fake_expanduser(path: str) -> str:
                if path in ("~/.pi/agent/sessions/", "~/.pi/agent/sessions"):
                    return str(sessions_root) + "/"
                return os.path.expanduser(path)

            with patch("quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
                sessions = parse_pi()
        self.assertEqual(len(sessions), 1)
        return sessions[0]

    def test_no_session_info_uses_first_prompt(self):
        sess = self._run([_session_record(), _user_message("Fix the login bug")])
        self.assertEqual(sess.tool_name, "pi")
        self.assertEqual(sess.path, "/Users/kaichen/project")
        self.assertEqual(sess.title, "Fix the login bug")
        self.assertEqual(sess.title_source, "")

    def test_one_name_is_a_rename(self):
        sess = self._run(
            [
                _session_record(),
                _user_message("Fix the login bug"),
                _session_info("  login fix  "),
            ]
        )
        self.assertEqual(sess.title, "login fix")
        self.assertEqual(sess.title_source, "rename")

    def test_latest_name_wins(self):
        sess = self._run(
            [
                _session_record(),
                _user_message("Fix the login bug"),
                _session_info("first name"),
                _user_message("now the signup page"),
                _session_info("second name"),
            ]
        )
        self.assertEqual(sess.title, "second name")
        self.assertEqual(sess.title_source, "rename")

    def test_whitespace_only_name_is_ignored(self):
        sess = self._run(
            [
                _session_record(),
                _user_message("Fix the login bug"),
                _session_info("   "),
            ]
        )
        self.assertEqual(sess.title, "Fix the login bug")
        self.assertEqual(sess.title_source, "")

    def test_non_string_name_is_ignored(self):
        sess = self._run(
            [
                _session_record(),
                _user_message("Fix the login bug"),
                _session_info(None),
                _session_info(42),
            ]
        )
        self.assertEqual(sess.title, "Fix the login bug")
        self.assertEqual(sess.title_source, "")

    def test_trailing_clear_still_surfaces_earlier_name(self):
        # Documented limitation: pi clears a name with {"name": ""}, but the
        # engine skips empty text, so the earlier name survives. If this ever
        # changes, update the comment in parse_pi as well.
        sess = self._run(
            [
                _session_record(),
                _user_message("Fix the login bug"),
                _session_info("first name"),
                _session_info(""),
            ]
        )
        self.assertEqual(sess.title, "first name")
        self.assertEqual(sess.title_source, "rename")


if __name__ == "__main__":
    unittest.main()
