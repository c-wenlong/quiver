import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.parsers import parse_antigravity


CONV_A = "aaaaaaaa-1111-2222-3333-444444444444"
CONV_B = "bbbbbbbb-1111-2222-3333-444444444444"


class ParseAntigravityTitleSourceTest(unittest.TestCase):
    def _make_home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _make_conversation(self, home: Path, conv_id: str, summary: str | None, cwd: str):
        conv_dir = home / ".gemini" / "antigravity" / "brain" / conv_id
        logs = conv_dir / ".system_generated" / "logs"
        logs.mkdir(parents=True)
        # Real overview.txt embeds a JSON string with escaped quotes: "Cwd":"\"/path\""
        (logs / "overview.txt").write_text('{"Cwd":"\\"' + cwd + '\\""}')
        if summary is not None:
            (conv_dir / "task.metadata.json").write_text(json.dumps({"summary": summary}))

    def _make_db(self, home: Path, rows, with_title_column: bool = True):
        cli_dir = home / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(cli_dir / "conversation_summaries.db"))
        if with_title_column:
            conn.execute(
                "CREATE TABLE conversation_summaries ("
                "conversation_id TEXT PRIMARY KEY, "
                'title TEXT NOT NULL DEFAULT "", '
                'preview TEXT NOT NULL DEFAULT "", '
                "workspace_uris TEXT NOT NULL DEFAULT '', "
                "last_user_input_time datetime)"
            )
            conn.executemany(
                "INSERT INTO conversation_summaries (conversation_id, title, preview) VALUES (?, ?, ?)",
                rows,
            )
        else:
            conn.execute(
                "CREATE TABLE conversation_summaries ("
                "conversation_id TEXT PRIMARY KEY, "
                'preview TEXT NOT NULL DEFAULT "")'
            )
            conn.executemany(
                "INSERT INTO conversation_summaries (conversation_id, preview) VALUES (?, ?)",
                [(cid, preview) for cid, _title, preview in rows],
            )
        conn.commit()
        conn.close()

    def _make_mirror(self, home: Path, entries):
        cache_dir = home / ".gemini" / "antigravity-cli" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "conversations": {
                cid: {"summary": {"ID": cid, "Title": title, "Preview": preview}}
                for cid, title, preview in entries
            }
        }
        (cache_dir / "conversation_metadata.json").write_text(json.dumps(data))

    def _parse(self, home: Path):
        def fake_expanduser(path: str) -> str:
            if path.startswith("~/"):
                return str(home / path[2:])
            if path == "~":
                return str(home)
            return path

        with patch("quiver.sessions.parsers.os.path.expanduser", side_effect=fake_expanduser):
            return {s.path: s for s in parse_antigravity()}

    def test_renamed_row_sets_rename_source(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, "Artifact summary A", "/Users/kai/proj-a")
        self._make_db(home, [(CONV_A, "My renamed session", "first prompt text")])

        sessions = self._parse(home)

        self.assertEqual(len(sessions), 1)
        session = sessions["/Users/kai/proj-a"]
        self.assertEqual(session.title, "My renamed session")
        self.assertEqual(session.title_source, "rename")
        self.assertEqual(session.tool_name, "antigravity")

    def test_unrenamed_row_uses_preview_only_without_artifact_summary(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, "Artifact summary A", "/Users/kai/proj-a")
        self._make_conversation(home, CONV_B, None, "/Users/kai/proj-b")
        self._make_db(
            home,
            [
                (CONV_A, "", "Preview for A"),
                (CONV_B, "", "Preview for B"),
            ],
        )

        sessions = self._parse(home)

        self.assertEqual(len(sessions), 2)
        with_summary = sessions["/Users/kai/proj-a"]
        self.assertEqual(with_summary.title, "Artifact summary A")
        self.assertEqual(with_summary.title_source, "")
        without_summary = sessions["/Users/kai/proj-b"]
        self.assertEqual(without_summary.title, "Preview for B")
        self.assertEqual(without_summary.title_source, "")

    def test_missing_db_keeps_artifact_summary(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, "Artifact summary A", "/Users/kai/proj-a")

        sessions = self._parse(home)

        self.assertEqual(len(sessions), 1)
        session = sessions["/Users/kai/proj-a"]
        self.assertEqual(session.title, "Artifact summary A")
        self.assertEqual(session.title_source, "")

    def test_db_without_title_column_is_a_noop(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, "Artifact summary A", "/Users/kai/proj-a")
        self._make_conversation(home, CONV_B, None, "/Users/kai/proj-b")
        self._make_db(
            home,
            [(CONV_A, "ignored", "Preview A"), (CONV_B, "ignored", "Preview B")],
            with_title_column=False,
        )

        sessions = self._parse(home)

        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions["/Users/kai/proj-a"].title, "Artifact summary A")
        self.assertEqual(sessions["/Users/kai/proj-a"].title_source, "")
        self.assertEqual(sessions["/Users/kai/proj-b"].title, "")
        self.assertEqual(sessions["/Users/kai/proj-b"].title_source, "")

    def test_corrupt_db_falls_back_to_artifact_summary(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, "Artifact summary A", "/Users/kai/proj-a")
        cli_dir = home / ".gemini" / "antigravity-cli"
        cli_dir.mkdir(parents=True)
        (cli_dir / "conversation_summaries.db").write_bytes(b"not a sqlite file")

        sessions = self._parse(home)

        self.assertEqual(sessions["/Users/kai/proj-a"].title, "Artifact summary A")
        self.assertEqual(sessions["/Users/kai/proj-a"].title_source, "")

    def test_json_mirror_fallback_when_db_missing(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, "Artifact summary A", "/Users/kai/proj-a")
        self._make_conversation(home, CONV_B, None, "/Users/kai/proj-b")
        self._make_mirror(
            home,
            [(CONV_A, "Renamed via mirror", "Preview A"), (CONV_B, "", "Preview B")],
        )

        sessions = self._parse(home)

        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions["/Users/kai/proj-a"].title, "Renamed via mirror")
        self.assertEqual(sessions["/Users/kai/proj-a"].title_source, "rename")
        self.assertEqual(sessions["/Users/kai/proj-b"].title, "Preview B")
        self.assertEqual(sessions["/Users/kai/proj-b"].title_source, "")

    def test_db_wins_over_mirror(self):
        home = self._make_home()
        self._make_conversation(home, CONV_A, None, "/Users/kai/proj-a")
        self._make_db(home, [(CONV_A, "DB name", "DB preview")])
        self._make_mirror(home, [(CONV_A, "Mirror name", "Mirror preview")])

        sessions = self._parse(home)

        self.assertEqual(sessions["/Users/kai/proj-a"].title, "DB name")
        self.assertEqual(sessions["/Users/kai/proj-a"].title_source, "rename")


if __name__ == "__main__":
    unittest.main()
