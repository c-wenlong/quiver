"""Unit tests for session family engines."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from quiver.sessions.engines.common import (
    clean_title,
    extract_user_text,
    parse_iso_ts,
    path_from_encoded_dir,
    strip_file_uri,
)
from quiver.sessions.engines.json_engine import JsonParserConfig, parse_json_store
from quiver.sessions.engines.jsonl_engine import JsonlParserConfig, parse_jsonl_projects
from quiver.sessions.engines.sqlite_engine import SqliteParserConfig, parse_sqlite


class CommonHelpersTest(unittest.TestCase):
    def test_parse_iso_ts_units(self):
        self.assertAlmostEqual(parse_iso_ts(1_700_000_000), 1_700_000_000_000, delta=1)
        self.assertAlmostEqual(parse_iso_ts(1_700_000_000_000), 1_700_000_000_000, delta=1)
        self.assertGreater(parse_iso_ts("2026-07-01T12:00:00Z"), 0)

    def test_clean_title_and_user_text(self):
        self.assertEqual(clean_title("<task>hi there</task>"), "hi there")
        self.assertEqual(extract_user_text([{"type": "text", "text": "hello"}]), "hello")
        self.assertEqual(extract_user_text("plain"), "plain")

    def test_path_helpers(self):
        self.assertEqual(path_from_encoded_dir("-Users-foo-bar"), "/Users/foo/bar")
        self.assertEqual(strip_file_uri("file:///Users/test%20x"), "/Users/test x")


class SqliteEngineTest(unittest.TestCase):
    def test_parse_sqlite_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE sessions (id TEXT, cwd TEXT, title TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO sessions VALUES ('s1', '/tmp/p', 'Hello', '2026-07-01T10:00:00Z')"
            )
            conn.commit()
            conn.close()

            sessions = parse_sqlite(
                SqliteParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    db_path=str(db),
                    query="SELECT id, cwd, title, updated_at FROM sessions",
                    session_id=0,
                    path=1,
                    title=2,
                    updated=3,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].path, "/tmp/p")
            self.assertEqual(sessions[0].title, "Hello")
            self.assertEqual(sessions[0].tool_name, "demo")


class JsonlEngineTest(unittest.TestCase):
    def test_nested_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "-Users-test"
            proj.mkdir()
            fp = proj / "abc.jsonl"
            fp.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session_start", "cwd": "/Users/test"}),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {"role": "user", "content": "hi engine"},
                            }
                        ),
                    ]
                )
            )
            sessions = parse_jsonl_projects(
                JsonlParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    base_dir=tmp,
                    path_from_event=lambda d: d.get("cwd") or "",
                    path_from_project_dir=lambda n: path_from_encoded_dir(n),
                    title_from_event=lambda d: (
                        (d.get("message") or {}).get("content")
                        if d.get("type") == "message"
                        and (d.get("message") or {}).get("role") == "user"
                        else ""
                    ),
                    one_session_per_file=True,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].path, "/Users/test")
            self.assertIn("hi", sessions[0].title)


class JsonEngineTest(unittest.TestCase):
    def test_index_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "sessions.json"
            index.write_text(
                json.dumps(
                    [
                        {
                            "sessionId": "x1",
                            "title": "Explore",
                            "workspaceDirectory": "/tmp/w",
                            "dateCreated": "2026-07-10T10:00:00.000Z",
                        }
                    ]
                )
            )
            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="index",
                    index_path=str(index),
                    index_items=lambda data: data if isinstance(data, list) else [],
                    get_id=lambda e, _f: e.get("sessionId") or "",
                    get_path=lambda e, _f: e.get("workspaceDirectory") or "",
                    get_title=lambda e, _f: e.get("title") or "",
                    get_ts=lambda e, _f: parse_iso_ts(e.get("dateCreated")),
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].title, "Explore")
            self.assertEqual(sessions[0].path, "/tmp/w")

    def test_files_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t1.json").write_text(
                json.dumps({"id": "t1", "cwd": "/tmp/a", "title": "one"})
            )
            sessions = parse_json_store(
                JsonParserConfig(
                    tool_name="demo",
                    agent="Demo",
                    mode="files",
                    base_dir=tmp,
                    file_glob="*.json",
                    get_id=lambda e, _f: e.get("id") or "",
                    get_path=lambda e, _f: e.get("cwd") or "",
                    get_title=lambda e, _f: e.get("title") or "",
                    get_ts=lambda e, f: 0,
                )
            )
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0].session_id, "t1")


if __name__ == "__main__":
    unittest.main()
