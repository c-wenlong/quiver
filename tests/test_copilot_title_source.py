"""title_source for Copilot sessions, driven by session-state/<id>/workspace.yaml."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.sessions.parsers import parse_copilot


_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    cwd TEXT,
    summary TEXT,
    updated_at TEXT,
    created_at TEXT
);
CREATE TABLE turns (
    session_id TEXT,
    turn_index INTEGER,
    user_message TEXT
);
CREATE TABLE checkpoints (
    session_id TEXT,
    checkpoint_number INTEGER,
    title TEXT
);
"""


class CopilotTitleSourceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "session-store.db"
        self.state = self.root / "session-state"
        self.state.mkdir()
        conn = sqlite3.connect(self.db)
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def add_session(self, sid, summary="", first_turn=None):
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (sid, "/tmp/proj", summary, "2026-07-01T12:00:00Z", "2026-07-01T11:00:00Z"),
        )
        if first_turn is not None:
            conn.execute("INSERT INTO turns VALUES (?, 0, ?)", (sid, first_turn))
        conn.commit()
        conn.close()

    def write_yaml(self, sid, text):
        d = self.state / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "workspace.yaml").write_text(text, encoding="utf-8")

    def parse(self):
        db = str(self.db)
        state = str(self.state)

        def fake_expanduser(p):
            if p.endswith("session-store.db"):
                return db
            if p.endswith("session-state"):
                return state
            return p

        with mock.patch("quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
            return {s.session_id: s for s in parse_copilot()}

    # -- cases -----------------------------------------------------------

    def test_user_named_true_marks_summary_title_as_rename(self):
        self.add_session("s1", summary="Auth refactor")
        self.write_yaml(
            "s1",
            "id: s1\ncwd: /tmp/proj\nname: Auth refactor\nuser_named: true\nsummary_count: 1\n",
        )
        s = self.parse()["s1"]
        self.assertEqual(s.title, "Auth refactor")
        self.assertEqual(s.title_source, "rename")

    def test_user_named_true_is_case_insensitive(self):
        self.add_session("s1", summary="Auth refactor")
        self.write_yaml("s1", "name: Auth refactor\nuser_named: True\n")
        self.assertEqual(self.parse()["s1"].title_source, "rename")

    def test_user_named_false_with_summary_is_auto(self):
        self.add_session("s1", summary="Update GitHub Copilot CLI")
        self.write_yaml("s1", "name: Update GitHub Copilot CLI\nuser_named: false\n")
        s = self.parse()["s1"]
        self.assertEqual(s.title, "Update GitHub Copilot CLI")
        self.assertEqual(s.title_source, "auto")

    def test_user_named_false_with_fallback_title_stays_blank(self):
        self.add_session("s1", summary="", first_turn="fix the login bug")
        self.write_yaml("s1", "user_named: false\nsummary_count: 0\n")
        s = self.parse()["s1"]
        self.assertEqual(s.title, "fix the login bug")
        self.assertEqual(s.title_source, "")

    def test_missing_key_treated_as_false(self):
        self.add_session("s1", summary="Implement Auth Login")
        self.write_yaml("s1", "id: s1\nsummary: Implement Auth Login\nsummary_count: 0\n")
        self.assertEqual(self.parse()["s1"].title_source, "auto")

        self.add_session("s2", summary="", first_turn="hello there")
        self.write_yaml("s2", "id: s2\nsummary_count: 0\n")
        s2 = self.parse()["s2"]
        self.assertEqual(s2.title, "hello there")
        self.assertEqual(s2.title_source, "")

    def test_missing_file_treated_as_false(self):
        self.add_session("s1", summary="Some generated title")
        self.add_session("s2", summary="", first_turn="first prompt")
        parsed = self.parse()
        self.assertEqual(parsed["s1"].title_source, "auto")
        self.assertEqual(parsed["s2"].title_source, "")
        self.assertEqual(parsed["s2"].title, "first prompt")

    def test_name_from_yaml_when_summary_empty(self):
        self.add_session("s1", summary="", first_turn="please rename me later")
        self.write_yaml("s1", 'name: "My renamed session"\nuser_named: true\n')
        s = self.parse()["s1"]
        self.assertEqual(s.title, "My renamed session")
        self.assertEqual(s.title_source, "rename")

    def test_name_from_yaml_block_scalar(self):
        self.add_session("s1", summary="")
        self.write_yaml(
            "s1",
            "id: s1\nname: |-\n  line one\n  line two\nuser_named: true\nsummary_count: 0\n",
        )
        s = self.parse()["s1"]
        self.assertEqual(s.title, "line one line two")
        self.assertEqual(s.title_source, "rename")

    def test_user_named_true_but_no_title_anywhere_stays_blank(self):
        self.add_session("s1", summary="")
        self.write_yaml("s1", "user_named: true\n")
        s = self.parse()["s1"]
        self.assertEqual(s.title, "")
        self.assertEqual(s.title_source, "")

    def test_malformed_yaml_falls_back_to_existing_behaviour(self):
        self.add_session("s1", summary="Generated summary")
        self.write_yaml("s1", "{{{ not: [yaml\n\x00\xff\n  user_named: true\nuser_named: maybe\n")
        s = self.parse()["s1"]
        self.assertEqual(s.title, "Generated summary")
        self.assertEqual(s.title_source, "auto")

        self.add_session("s2", summary="", first_turn="first prompt")
        (self.state / "s2").mkdir()
        (self.state / "s2" / "workspace.yaml").write_bytes(b"\x00\x01\x02\xff\xfe" * 100)
        s2 = self.parse()["s2"]
        self.assertEqual(s2.title, "first prompt")
        self.assertEqual(s2.title_source, "")

    def test_workspace_yaml_is_a_directory(self):
        self.add_session("s1", summary="Generated summary")
        (self.state / "s1" / "workspace.yaml").mkdir(parents=True)
        self.assertEqual(self.parse()["s1"].title_source, "auto")

    def test_nested_user_named_key_is_ignored(self):
        self.add_session("s1", summary="Generated summary")
        self.write_yaml("s1", "meta:\n  user_named: true\n  name: nested\n")
        self.assertEqual(self.parse()["s1"].title_source, "auto")


if __name__ == "__main__":
    unittest.main()
