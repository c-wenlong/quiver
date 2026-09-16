"""Unit tests for newly added session parsers (copilot, continue, crush, grok)."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ParseCopilotTest(unittest.TestCase):
    def test_reads_sessions_and_title_from_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "session-store.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
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
                INSERT INTO sessions VALUES
                    ('s1', '/tmp/proj', '', '2026-07-01T12:00:00Z', '2026-07-01T11:00:00Z');
                INSERT INTO turns VALUES ('s1', 0, 'fix the login bug');
                """
            )
            conn.commit()
            conn.close()

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(db) if p.endswith("session-store.db") else p,
            ):
                from quiver.sessions.parsers import parse_copilot

                sessions = parse_copilot()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "copilot")
            self.assertEqual(sessions[0].path, "/tmp/proj")
            self.assertIn("login", sessions[0].title)
            self.assertEqual(sessions[0].session_id, "s1")


_DEVIN_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    working_directory TEXT NOT NULL,
    backend_type TEXT NOT NULL,
    model TEXT NOT NULL,
    agent_mode TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_activity_at INTEGER NOT NULL,
    title TEXT,
    main_chain_id INTEGER,
    hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE prompt_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    is_shell INTEGER NOT NULL DEFAULT 0
);
"""


class ParseDevinTest(unittest.TestCase):
    """Devin CLI sessions.db: titles, provenance from prompt_history, hidden rows."""

    def _parse(self, sessions, prompts):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"
            conn = sqlite3.connect(db)
            conn.executescript(_DEVIN_SCHEMA)
            conn.executemany(
                "INSERT INTO sessions VALUES (?, ?, 'windsurf', 'swe-2', 'normal', ?, ?, ?, NULL, ?)",
                sessions,
            )
            conn.executemany(
                "INSERT INTO prompt_history (content, timestamp, session_id, is_shell) VALUES (?, ?, ?, ?)",
                prompts,
            )
            conn.commit()
            conn.close()
            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(db) if p.endswith("devin/cli/sessions.db") else p,
            ):
                from quiver.sessions.parsers import parse_devin

                return {s.session_id: s for s in parse_devin()}

    def test_reads_sessions_with_seconds_timestamps_and_skips_hidden(self):
        found = self._parse(
            [
                ("shown", "/tmp/proj", 1789116383, 1789116437, "Create a connection string", 0),
                ("hidden", "/tmp/proj", 1789116383, 1789116437, "gone", 1),
            ],
            [],
        )
        self.assertEqual(set(found), {"shown"})
        s = found["shown"]
        self.assertEqual(s.tool_name, "devin")
        self.assertEqual(s.agent, "Devin")
        self.assertEqual(s.path, "/tmp/proj")
        self.assertEqual(s.title, "Create a connection string")
        self.assertEqual(s.timestamp, 1789116437000.0)

    def test_title_falls_back_to_first_prompt_of_the_session(self):
        found = self._parse(
            [("s1", "/tmp/proj", 1000, 1010, "", 0)],
            [
                ("/usage", 990, "s1", 0),            # typed before the session existed
                ("ls -la", 1000, "s1", 1),           # shell mode, not a prompt
                ("fix the login bug", 1000, "s1", 0),
                ("and the logout one", 1005, "s1", 0),
            ],
        )
        self.assertEqual(found["s1"].title, "fix the login bug")
        self.assertEqual(found["s1"].title_source, "")

    def test_title_equal_to_first_prompt_has_no_provenance(self):
        found = self._parse(
            [("s1", "/tmp/proj", 1000, 1010, "/exi", 0)],
            [("/login-status", 995, "s1", 0), ("/exi", 1000, "s1", 0), ("/exit", 1003, "s1", 0)],
        )
        self.assertEqual(found["s1"].title, "/exi")
        self.assertEqual(found["s1"].title_source, "")

    def test_title_differing_from_first_prompt_is_auto(self):
        found = self._parse(
            [("s1", "/tmp/proj", 1000, 1010, "CloudSQL table comparison", 0)],
            [("check on my cloudsql db and compare two tables", 1000, "s1", 0)],
        )
        self.assertEqual(found["s1"].title_source, "auto")

    def test_inline_rename_matching_title_is_rename(self):
        found = self._parse(
            [
                ("s1", "/tmp/proj", 1000, 1010, "agent-dropdown-sort", 0),
                ("s2", "/tmp/proj", 1000, 1010, "agent-dropdown-sort", 0),
            ],
            [
                ("sort the dropdown", 1000, "s1", 0),
                ("/rename agent-dropdown-sort", 1005, "s1", 0),
                ("sort the dropdown", 1000, "s2", 0),
                ("/rename-chat agent-dropdown-sort", 1005, "s2", 0),
            ],
        )
        self.assertEqual(found["s1"].title_source, "rename")
        self.assertEqual(found["s2"].title_source, "rename")

    def test_interactive_rename_without_argument_under_marks_as_auto(self):
        found = self._parse(
            [("s1", "/tmp/proj", 1000, 1010, "db-conn", 0)],
            [("use gcloud to build a connection string", 1000, "s1", 0), ("/rename-chat ", 1005, "s1", 0)],
        )
        self.assertEqual(found["s1"].title_source, "auto")

    def test_rename_command_is_never_the_title(self):
        found = self._parse(
            [("s1", "/tmp/proj", 1000, 1010, "", 0)],
            [("/rename first", 1000, "s1", 0), ("real prompt", 1001, "s1", 0)],
        )
        self.assertEqual(found["s1"].title, "real prompt")
        self.assertEqual(found["s1"].title_source, "")


class ParseContinueTest(unittest.TestCase):
    def test_reads_sessions_json_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sid = "abc-123"
            (base / "sessions.json").write_text(
                json.dumps(
                    [
                        {
                            "sessionId": sid,
                            "title": "Explore repo",
                            "workspaceDirectory": "/tmp/work",
                            "dateCreated": "2026-07-10T10:00:00.000Z",
                        }
                    ]
                )
            )
            (base / f"{sid}.json").write_text(
                json.dumps({"history": [{"message": {"role": "user", "content": "hello"}}]})
            )

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(base) if p.endswith("sessions") else p,
            ):
                from quiver.sessions.parsers import parse_continue

                sessions = parse_continue()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "continue")
            self.assertEqual(sessions[0].path, "/tmp/work")
            self.assertEqual(sessions[0].title, "Explore repo")


class ParseCrushTest(unittest.TestCase):
    def test_reads_projects_json_and_crush_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "projdata"
            data_dir.mkdir()
            db = data_dir / "crush.db"
            conn = sqlite3.connect(db)
            conn.execute(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    parent_session_id TEXT,
                    title TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0,
                    updated_at INTEGER,
                    created_at INTEGER,
                    summary_message_id TEXT,
                    todos TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO sessions (id, title, updated_at, created_at) VALUES (?, ?, ?, ?)",
                ("sess1", "Untitled Session", 1777032754, 1777032753),
            )
            conn.commit()
            conn.close()

            projects = Path(tmp) / "projects.json"
            projects.write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "path": "/Users/test/Downloads",
                                "data_dir": str(data_dir),
                                "last_accessed": 1777032754,
                            }
                        ]
                    }
                )
            )

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(projects) if p.endswith("projects.json") else p,
            ):
                from quiver.sessions.parsers import parse_crush

                sessions = parse_crush()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "crush")
            self.assertEqual(sessions[0].path, "/Users/test/Downloads")
            self.assertEqual(sessions[0].session_id, "sess1")


class ParseGrokTest(unittest.TestCase):
    def test_reads_encoded_cwd_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            enc = "%2FUsers%2Ftest"
            sess = base / enc / "sess-uuid"
            sess.mkdir(parents=True)
            (sess / "summary.json").write_text(
                json.dumps(
                    {
                        "generated_title": "hello grok",
                        "session_summary": "hello grok",
                        "created_at": "2026-07-06T12:17:43.838414Z",
                        "updated_at": "2026-07-06T12:19:08.646503Z",
                        "last_active_at": "2026-07-06T12:19:08.646503Z",
                    }
                )
            )
            (sess / "prompt_context.json").write_text(
                json.dumps({"working_directory": "/Users/test"})
            )

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(base) if p.endswith("sessions") else p,
            ):
                from quiver.sessions.parsers import parse_grok

                sessions = parse_grok()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "grok")
            self.assertEqual(sessions[0].path, "/Users/test")
            self.assertEqual(sessions[0].title, "hello grok")
            self.assertEqual(sessions[0].session_id, "sess-uuid")


class ParseClineTest(unittest.TestCase):
    def test_reads_task_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state = base / "data" / "state"
            state.mkdir(parents=True)
            (state / "taskHistory.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "1777068768553",
                            "ulid": "01KQ0",
                            "ts": 1777068920246,
                            "task": "Hello",
                            "cwdOnTaskInitialization": "/tmp/downloads",
                        }
                    ]
                )
            )

            def expand(p: str) -> str:
                if p.endswith("taskHistory.json"):
                    return str(state / "taskHistory.json")
                if p.endswith("data/sessions"):
                    return str(base / "data" / "sessions")
                return p

            with mock.patch("quiver.sessions.parsers.os.path.expanduser", side_effect=expand):
                from quiver.sessions.parsers import parse_cline

                sessions = parse_cline()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "cline")
            self.assertEqual(sessions[0].path, "/tmp/downloads")
            self.assertEqual(sessions[0].title, "Hello")

    def test_reads_session_dirs(self):
        # Cline 3.x: data/sessions/<id>/<id>.json beside a
        # <id>.messages.json transcript; only the metadata file counts.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sess_dir = base / "data" / "sessions" / "1789471031317_jp2hv"
            sess_dir.mkdir(parents=True)
            (sess_dir / "1789471031317_jp2hv.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "session_id": "1789471031317_jp2hv",
                        "source": "vscode",
                        "started_at": "2026-09-15T11:17:11.371Z",
                        "cwd": "/work/project",
                        "prompt": "hello!",
                        "metadata": {"title": "hello!"},
                    }
                )
            )
            (sess_dir / "1789471031317_jp2hv.messages.json").write_text(
                json.dumps(
                    {
                        "sessionId": "1789471031317_jp2hv",
                        "messages": [{"role": "user", "content": []}],
                    }
                )
            )

            def expand(p: str) -> str:
                if p.endswith("data/sessions"):
                    return str(base / "data" / "sessions")
                if p.endswith("taskHistory.json"):
                    return str(base / "data" / "state" / "taskHistory.json")
                return p

            with mock.patch("quiver.sessions.parsers.os.path.expanduser", side_effect=expand):
                from quiver.sessions.parsers import parse_cline

                sessions = parse_cline()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "cline")
            self.assertEqual(sessions[0].session_id, "1789471031317_jp2hv")
            self.assertEqual(sessions[0].path, "/work/project")
            self.assertEqual(sessions[0].title, "hello!")
            self.assertGreater(sessions[0].timestamp, 0)


class ParseForgeTest(unittest.TestCase):
    def test_reads_conversations_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / ".forge.db"
            conn = sqlite3.connect(db)
            conn.execute(
                """
                CREATE TABLE conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    workspace_id INTEGER,
                    context TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    metrics TEXT
                )
                """
            )
            ctx = json.dumps(
                {
                    "messages": [
                        {
                            "message": {
                                "text": {
                                    "role": "User",
                                    "content": "<task>fix the build</task>",
                                }
                            }
                        }
                    ]
                }
            )
            conn.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "c1",
                    "Build Fix",
                    1,
                    ctx,
                    "2026-07-01 10:00:00",
                    "2026-07-01 11:00:00",
                    None,
                ),
            )
            conn.commit()
            conn.close()

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(db) if p.endswith(".forge.db") else ("/Users/test" if p == "~" else p),
            ):
                from quiver.sessions.parsers import parse_forge

                sessions = parse_forge()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "forge")
            self.assertEqual(sessions[0].title, "Build Fix")
            self.assertEqual(sessions[0].session_id, "c1")


class TrackedCountsTest(unittest.TestCase):
    def test_session_counts_includes_zero_for_tracked(self):
        from quiver.sessions.usage import session_counts_100d, tracked_tool_names

        tracked = tracked_tool_names()
        self.assertIn("copilot", tracked)
        self.assertIn("grok", tracked)
        self.assertIn("cline", tracked)
        counts = session_counts_100d()
        for name in tracked:
            self.assertIn(name, counts)


if __name__ == "__main__":
    unittest.main()
