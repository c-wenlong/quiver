import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.sessions.aggregator import PARSER_REGISTRY, get_all_sessions
from quiver.sessions.models import Session


class SessionsAggregatorTest(unittest.TestCase):
    def test_parser_registry_has_expected_tools(self):
        names = {name for name, _parser, _keys in PARSER_REGISTRY}
        self.assertIn("claude", names)
        self.assertIn("codex", names)
        self.assertIn("opencode", names)

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


if __name__ == "__main__":
    unittest.main()
