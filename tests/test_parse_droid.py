import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.parsers import parse_droid


class ParseDroidTest(unittest.TestCase):
    def test_parse_droid_reads_session_start_and_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            project_dir = sessions_root / "-Users-kaichen-project"
            project_dir.mkdir(parents=True)
            session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            jsonl = project_dir / f"{session_id}.jsonl"
            records = [
                {
                    "type": "session_start",
                    "id": session_id,
                    "title": "Wire droid session support",
                    "cwd": "/Users/kaichen/project",
                },
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [],
                        "hookEventName": "SessionStart",
                        "visibility": "user_only",
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "Please implement parse_droid"}],
                    },
                },
            ]
            with open(jsonl, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")
            # Ensure stable mtime ordering in case FS resolution is coarse
            os.utime(jsonl, (time.time(), time.time()))

            with patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(sessions_root) + "/" if p.endswith("sessions/") else os.path.expanduser(p),
            ):
                # Expanduser only for the sessions path we care about
                def fake_expanduser(path: str) -> str:
                    if path in ("~/.factory/sessions/", "~/.factory/sessions"):
                        return str(sessions_root) + "/"
                    return os.path.expanduser(path)

                with patch("quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
                    sessions = parse_droid()

            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.tool_name, "droid")
            self.assertEqual(session.agent, "Droid")
            self.assertEqual(session.session_id, session_id)
            self.assertEqual(session.path, "/Users/kaichen/project")
            self.assertEqual(session.title, "Wire droid session support")

    def test_parse_droid_falls_back_to_encoded_dir_and_user_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_root = Path(tmp) / "sessions"
            project_dir = sessions_root / "-Users-kaichen-project"
            project_dir.mkdir(parents=True)
            session_id = "11111111-2222-3333-4444-555555555555"
            jsonl = project_dir / f"{session_id}.jsonl"
            records = [
                {
                    "type": "session_start",
                    "id": session_id,
                    "title": "New Session",
                },
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": "Add droid resume support",
                    },
                },
            ]
            with open(jsonl, "w") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")

            def fake_expanduser(path: str) -> str:
                if path in ("~/.factory/sessions/", "~/.factory/sessions"):
                    return str(sessions_root) + "/"
                return os.path.expanduser(path)

            with patch("quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
                sessions = parse_droid()

            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.path, "/Users/kaichen/project")
            self.assertEqual(session.title, "Add droid resume support")
            self.assertEqual(session.session_id, session_id)


if __name__ == "__main__":
    unittest.main()
