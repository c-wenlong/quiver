import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.aggregator import PARSER_REGISTRY, get_all_sessions
from quiver.sessions.models import Session


class SessionsAggregatorTest(unittest.TestCase):
    def test_parser_registry_has_expected_tools(self):
        names = {name for name, _parser, _keys in PARSER_REGISTRY}
        for expected in (
            "claude",
            "codex",
            "opencode",
            "droid",
            "copilot",
            "continue",
            "crush",
            "amp",
            "kimi",
            "hermes",
            "grok",
            "cline",
            "forge",
            "mimo",
            "tau",
        ):
            self.assertIn(expected, names)

    def test_get_all_sessions_sorts_by_timestamp_desc(self):
        fake = [
            Session(timestamp=100, agent="a", path="/a", tool_name="claude"),
            Session(timestamp=300, agent="b", path="/b", tool_name="codex"),
            Session(timestamp=200, agent="c", path="/c", tool_name="opencode"),
        ]

        def one_parser():
            return list(fake)

        registry = [("fake", one_parser, ("fake",))]

        with patch("quiver.sessions.aggregator.PARSER_REGISTRY", registry):
            sessions = get_all_sessions(limit=None)
        self.assertEqual([s.timestamp for s in sessions], [300, 200, 100])

    def test_get_all_sessions_filters_by_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            nested = project / "nested"
            nested.mkdir(parents=True)

            fake = [
                Session(timestamp=100, agent="a", path=str(project), tool_name="claude"),
                Session(timestamp=200, agent="b", path=str(nested), tool_name="codex"),
                Session(timestamp=300, agent="c", path="/elsewhere", tool_name="gemini"),
            ]

            def one_parser():
                return list(fake)

            registry = [("fake", one_parser, ("fake",))]

            with patch("quiver.sessions.aggregator.PARSER_REGISTRY", registry):
                sessions = get_all_sessions(limit=None, cwd=str(project))

            paths = {s.path for s in sessions}
            self.assertIn(str(project), paths)
            self.assertIn(str(nested), paths)
            self.assertNotIn("/elsewhere", paths)

    def test_get_all_sessions_agent_filter(self):
        fake_claude = Session(timestamp=100, agent="claude", path="/a", tool_name="claude")
        fake_codex = Session(timestamp=200, agent="codex", path="/b", tool_name="codex")

        registry = [
            ("claude", lambda: [fake_claude], ("claude", "cc")),
            ("codex", lambda: [fake_codex], ("codex", "cx")),
        ]

        with patch("quiver.sessions.aggregator.PARSER_REGISTRY", registry):
            sessions = get_all_sessions(limit=None, agent="cc")

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].tool_name, "claude")

    def test_cache_roundtrip(self):
        """Cache stores sessions to disk and loads them back."""
        import json
        from quiver.sessions.aggregator import _save_cached_sessions, _load_cached_sessions

        fake = [
            Session(timestamp=100, agent="a", path="/a", title="t1", session_id="s1", tool_name="claude"),
            Session(timestamp=200, agent="b", path="/b", title="t2", session_id="s2", tool_name="codex"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "session_cache.json"
            with patch("quiver.sessions.aggregator.SESSION_CACHE_FILE", cache_file):
                _save_cached_sessions(fake)
                self.assertTrue(cache_file.exists())

                loaded = _load_cached_sessions()
                self.assertIsNotNone(loaded)
                self.assertEqual(len(loaded), 2)
                self.assertEqual(loaded[0].tool_name, "claude")
                self.assertEqual(loaded[1].timestamp, 200)

    def test_cache_expires_after_ttl(self):
        """Stale cache returns None, forcing re-parse."""
        import json
        import time as _time
        from quiver.sessions.aggregator import _save_cached_sessions, _load_cached_sessions

        fake = [Session(timestamp=100, agent="a", path="/a", tool_name="claude")]

        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "session_cache.json"
            with patch("quiver.sessions.aggregator.SESSION_CACHE_FILE", cache_file):
                _save_cached_sessions(fake)
                # Backdate the cache
                with open(cache_file) as f:
                    data = json.load(f)
                data["cached_at"] = _time.time() - 999
                with open(cache_file, "w") as f:
                    json.dump(data, f)

                loaded = _load_cached_sessions()
                self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
