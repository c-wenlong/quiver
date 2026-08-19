import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.sessions.models_analytics import classify_provider, collect_model_usage


class ModelsAnalyticsTest(unittest.TestCase):
    @staticmethod
    def _expand_for(root: Path, paths: dict[str, Path]):
        def expand(path: str) -> str:
            return str(paths.get(path, root / "missing"))

        return expand

    def test_classify_openai_models(self):
        self.assertEqual(classify_provider("gpt-4.1"), "openai")
        self.assertEqual(classify_provider("openai/gpt-4o"), "openai")

    def test_classify_anthropic_models(self):
        self.assertEqual(classify_provider("claude-sonnet-4"), "anthropic")
        self.assertEqual(classify_provider("anthropic/claude-opus-4"), "anthropic")

    def test_classify_google_models(self):
        self.assertEqual(classify_provider("gemini-2.5-pro"), "google")

    def test_classify_unknown_returns_other(self):
        self.assertEqual(classify_provider("totally-custom-model"), "other")

    def test_collects_claude_models_without_glob_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_dir = root / "claude-projects"
            project_dir = claude_dir / "-Users-test-project"
            project_dir.mkdir(parents=True)
            (project_dir / "session.jsonl").write_text(
                json.dumps({"model": "claude-sonnet-4"}, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            def expand(path: str) -> str:
                if path == "~/.claude/projects/":
                    return str(claude_dir)
                return str(root / "missing")

            with mock.patch(
                "quiver.sessions.models_analytics.os.path.expanduser",
                side_effect=expand,
            ), mock.patch(
                "quiver.sessions.models_analytics.glob.glob",
                side_effect=AssertionError("Claude traversal must use os.scandir"),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["claude"][("", "claude-sonnet-4")], 1)

    def test_collects_opencode_models_from_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "opencode.db"
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE message (data TEXT)")
            connection.executemany(
                "INSERT INTO message VALUES (?)",
                [
                    (json.dumps({"model": {"providerID": "openai", "modelID": "gpt-5"}}),),
                    (json.dumps({"model": {"providerID": "openai", "modelID": "gpt-5"}}),),
                ],
            )
            connection.commit()
            connection.close()

            with mock.patch(
                "quiver.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root, {"~/.local/share/opencode/opencode.db": db}
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["opencode"][("openai", "gpt-5")], 2)

    def test_collects_codex_models_from_nested_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            session_dir = codex / "2026" / "08" / "01"
            session_dir.mkdir(parents=True)
            (session_dir / "session.jsonl").write_text(
                '{"model":"gpt-5-codex"}\n', encoding="utf-8"
            )

            with mock.patch(
                "quiver.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(root, {"~/.codex/sessions/": codex}),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["codex"][("", "gpt-5-codex")], 1)

    def test_collects_freebuff_models_from_chat_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            freebuff = root / "manicode"
            chat = freebuff / "project" / "chats" / "session"
            chat.mkdir(parents=True)
            (chat / "log.jsonl").write_text(
                '{"model":"claude-opus-4"}\n', encoding="utf-8"
            )

            with mock.patch(
                "quiver.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root, {"~/.config/manicode/projects/": freebuff}
                ),
            ):
                usage = collect_model_usage()

            self.assertEqual(usage["freebuff"][("", "claude-opus-4")], 1)

    def test_broken_opencode_database_does_not_hide_other_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            broken_db = root / "opencode.db"
            broken_db.write_text("not sqlite", encoding="utf-8")
            freebuff = root / "manicode"
            chat = freebuff / "project" / "chats" / "session"
            chat.mkdir(parents=True)
            (chat / "log.jsonl").write_text('{"model":"gpt-4.1"}\n', encoding="utf-8")

            with mock.patch(
                "quiver.sessions.models_analytics.os.path.expanduser",
                side_effect=self._expand_for(
                    root,
                    {
                        "~/.local/share/opencode/opencode.db": broken_db,
                        "~/.config/manicode/projects/": freebuff,
                    },
                ),
            ):
                usage = collect_model_usage()

            self.assertNotIn("opencode", usage)
            self.assertEqual(usage["freebuff"][("", "gpt-4.1")], 1)

    def test_opencode_connection_closes_when_query_fails(self):
        connection = mock.MagicMock()
        connection.cursor.return_value.execute.side_effect = sqlite3.DatabaseError(
            "unsupported schema"
        )

        with mock.patch(
            "quiver.sessions.models_analytics.os.path.expanduser",
            side_effect=lambda path: "/tmp/opencode.db" if "opencode" in path else "/missing",
        ), mock.patch(
            "quiver.sessions.models_analytics.os.path.exists",
            side_effect=lambda path: path == "/tmp/opencode.db",
        ), mock.patch(
            "quiver.sessions.models_analytics.sqlite3.connect", return_value=connection
        ):
            usage = collect_model_usage()

        self.assertEqual(usage, {})
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
