"""Unit tests for newly added session parsers (copilot, continue, crush, amp, kimi, hermes, grok)."""

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


class ParseAmpTest(unittest.TestCase):
    def test_reads_thread_with_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            thread = {
                "id": "T-1",
                "created": 1720000000000,
                "env": {
                    "initial": {
                        "trees": [{"uri": "file:///Users/test/project"}]
                    }
                },
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hello amp"}]}
                ],
            }
            (base / "T-1.json").write_text(json.dumps(thread))
            # empty placeholder should still be skipped when no env and no msgs
            (base / "empty.json").write_text(json.dumps({"id": "empty"}))

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(base) if p.endswith("threads") else p,
            ):
                from quiver.sessions.parsers import parse_amp

                sessions = parse_amp()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "amp")
            self.assertEqual(sessions[0].path, "/Users/test/project")
            self.assertIn("hello", sessions[0].title)


class ParseKimiTest(unittest.TestCase):
    def test_md5_path_lookup_and_context(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            work_path = "/Users/test/code"
            digest = hashlib.md5(work_path.encode()).hexdigest()
            sessions_root = home / "sessions"
            sess_dir = sessions_root / digest / "sid-1"
            sess_dir.mkdir(parents=True)
            (sess_dir / "context.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"role": "_system_prompt", "content": "You are Kimi"}),
                        json.dumps({"role": "user", "content": "refactor the parser"}),
                    ]
                )
            )
            (home / "kimi.json").write_text(
                json.dumps({"work_dirs": [{"path": work_path, "kaos": "local"}]})
            )

            def expand(p: str) -> str:
                if p.endswith("kimi.json"):
                    return str(home / "kimi.json")
                if p.endswith("sessions"):
                    return str(sessions_root)
                return p

            with mock.patch("quiver.sessions.parsers.os.path.expanduser", side_effect=expand):
                from quiver.sessions.parsers import parse_kimi

                sessions = parse_kimi()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "kimi")
            self.assertEqual(sessions[0].path, work_path)
            self.assertIn("refactor", sessions[0].title)


class ParseHermesTest(unittest.TestCase):
    def test_reads_session_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "session_abc.json").write_text(
                json.dumps(
                    {
                        "session_id": "abc",
                        "session_start": "2026-07-01T10:00:00",
                        "last_updated": "2026-07-01T11:00:00",
                        "platform": "cli",
                        "messages": [
                            {"role": "user", "content": "summarize the README"},
                            {"role": "assistant", "content": "ok"},
                        ],
                    }
                )
            )
            (base / "request_dump_x.json").write_text("{}")

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(base) if p.endswith("sessions") else p,
            ):
                from quiver.sessions.parsers import parse_hermes

                sessions = parse_hermes()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "hermes")
            self.assertIn("README", sessions[0].title)
            self.assertEqual(sessions[0].session_id, "abc")


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
                return p

            with mock.patch("quiver.sessions.parsers.os.path.expanduser", side_effect=expand):
                from quiver.sessions.parsers import parse_cline

                sessions = parse_cline()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "cline")
            self.assertEqual(sessions[0].path, "/tmp/downloads")
            self.assertEqual(sessions[0].title, "Hello")


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


class ParseMimoTest(unittest.TestCase):
    def test_reads_mimocode_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mimocode.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE project (
                    id TEXT PRIMARY KEY,
                    worktree TEXT,
                    vcs TEXT,
                    name TEXT,
                    icon_url TEXT,
                    icon_color TEXT,
                    time_created INTEGER,
                    time_updated INTEGER,
                    time_initialized INTEGER,
                    sandboxes TEXT,
                    commands TEXT
                );
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    parent_id TEXT,
                    slug TEXT,
                    directory TEXT,
                    title TEXT,
                    version TEXT,
                    share_url TEXT,
                    summary_additions INTEGER,
                    summary_deletions INTEGER,
                    summary_files INTEGER,
                    summary_diffs TEXT,
                    revert TEXT,
                    permission TEXT,
                    time_created INTEGER,
                    time_updated INTEGER,
                    time_compacting INTEGER,
                    time_archived INTEGER,
                    workspace_id TEXT,
                    context_from TEXT,
                    context_watermark TEXT,
                    last_checkpoint_message_id TEXT
                );
                CREATE TABLE part (
                    id TEXT PRIMARY KEY,
                    message_id TEXT,
                    session_id TEXT,
                    time_created INTEGER,
                    time_updated INTEGER,
                    data TEXT
                );
                INSERT INTO project (id, worktree, time_created, time_updated)
                VALUES ('p1', '/tmp/proj', 1000, 2000);
                INSERT INTO session (id, project_id, directory, title, time_created, time_updated)
                VALUES ('ses_1', 'p1', '/tmp/proj', 'hello', 1000, 2000);
                """
            )
            conn.commit()
            conn.close()

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(db) if p.endswith("mimocode.db") else p,
            ):
                from quiver.sessions.parsers import parse_mimo

                sessions = parse_mimo()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "mimo")
            self.assertEqual(sessions[0].path, "/tmp/proj")
            self.assertEqual(sessions[0].title, "hello")


class ParseTauTest(unittest.TestCase):
    def test_reads_index_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            proj = base / "home-2bddf4"
            proj.mkdir()
            sid = "sess-abc"
            sess_file = proj / f"{sid}.jsonl"
            sess_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_info",
                                "cwd": "/Users/test",
                                "timestamp": 1784324840.1,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "timestamp": 1784324840.3,
                                "message": {"role": "user", "content": "version"},
                            }
                        ),
                    ]
                )
            )
            (proj / "index.jsonl").write_text(
                json.dumps(
                    {
                        "id": sid,
                        "path": str(sess_file),
                        "cwd": "/Users/test",
                        "title": None,
                        "created_at": 1784324840.3,
                        "updated_at": 1784324840.3,
                    }
                )
                + "\n"
            )

            with mock.patch(
                "quiver.sessions.parsers.os.path.expanduser",
                side_effect=lambda p: str(base) if p.endswith("sessions") else p,
            ):
                from quiver.sessions.parsers import parse_tau

                sessions = parse_tau()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].tool_name, "tau")
            self.assertEqual(sessions[0].path, "/Users/test")
            self.assertEqual(sessions[0].title, "version")


class TrackedCountsTest(unittest.TestCase):
    def test_session_counts_includes_zero_for_tracked(self):
        from quiver.sessions.usage import session_counts_100d, tracked_tool_names

        tracked = tracked_tool_names()
        self.assertIn("copilot", tracked)
        self.assertIn("hermes", tracked)
        self.assertIn("grok", tracked)
        self.assertIn("cline", tracked)
        self.assertIn("tau", tracked)
        counts = session_counts_100d()
        for name in tracked:
            self.assertIn(name, counts)


if __name__ == "__main__":
    unittest.main()
