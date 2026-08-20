"""`swe find mcps`: what the hub holds, and which harnesses are behind it.

`swe mcp list` is the tool-by-server matrix, useful once you know what you
are looking for. This answers the earlier question, so it groups by the
prefix taxonomy and names the harnesses that have not caught up.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from quiver.console import strip_ansi
from quiver.find.mcps import ToolView, _kind, _signature, hub_view, prefix_of

LOCAL = {"command": "npx", "args": ["thing"]}
REMOTE = {"url": "https://example.test/mcp"}


class PrefixTest(unittest.TestCase):
    def test_a_prefixed_name_reports_its_prefix(self):
        self.assertEqual(prefix_of("dv__github"), "dv")

    def test_an_unprefixed_name_reports_empty(self):
        self.assertEqual(prefix_of("lazyweb"), "")

    def test_only_the_first_segment_counts(self):
        self.assertEqual(prefix_of("pd__g__docs"), "pd")


class TransportTest(unittest.TestCase):
    def test_a_url_is_remote(self):
        self.assertEqual(_kind(REMOTE), "remote")

    def test_a_command_is_local(self):
        self.assertEqual(_kind(LOCAL), "local")

    def test_an_sse_type_is_remote(self):
        self.assertEqual(_kind({"type": "sse", "command": "x"}), "remote")

    def test_a_malformed_entry_does_not_raise(self):
        self.assertEqual(_kind(None), "local")
        self.assertEqual(_signature(None), "?")


class DuplicateRuleTest(unittest.TestCase):
    """One local and one remote copy of a server is deliberate; those are
    two ways to reach it. Two locals or two remotes is the same thing
    filed twice."""

    def _hub(self, servers):
        with mock.patch("quiver.mcp.cli.get_hub_servers", return_value=servers):
            return hub_view()

    def test_two_locals_under_different_names_are_flagged(self):
        view = self._hub({"notion": LOCAL, "pd__notion": dict(LOCAL)})
        self.assertEqual(view.duplicates, [["notion", "pd__notion"]])

    def test_two_remotes_under_different_names_are_flagged(self):
        view = self._hub({"lazyweb": REMOTE, "rf__lazyweb": dict(REMOTE)})
        self.assertEqual(view.duplicates, [["lazyweb", "rf__lazyweb"]])

    def test_one_local_and_one_remote_is_allowed(self):
        view = self._hub({"notion": LOCAL, "pd__notion": REMOTE})
        self.assertEqual(view.duplicates, [])

    def test_genuinely_different_servers_are_not_flagged(self):
        view = self._hub({"a": LOCAL, "b": {"command": "other"}})
        self.assertEqual(view.duplicates, [])

    def test_grouping_puts_unfiled_servers_in_their_own_bucket(self):
        view = self._hub({"dv__github": LOCAL, "loose": {"command": "x"}})
        self.assertEqual(view.by_prefix["dv"], ["dv__github"])
        self.assertEqual(view.by_prefix[""], ["loose"])


class ToolCoverageTest(unittest.TestCase):
    def test_a_harness_with_no_config_on_disk_is_skipped(self):
        """Every registry harness gets an optimistic config path so sync
        works; listing 20 that have no MCP config at all as "missing 33
        servers" buries the ones really behind."""
        from quiver.find import mcps

        with mock.patch("quiver.mcp.cli.load_registry", return_value={"ghost": {}}), \
             mock.patch("quiver.mcp.cli.get_mcp_tools", return_value={"ghost": {}}), \
             mock.patch("quiver.mcp.cli.get_tool_servers", return_value={}), \
             mock.patch("quiver.mcp.cli.get_tool_config",
                        return_value={"path": "/nope/does/not/exist.json"}):
            self.assertEqual(mcps.tool_views({"a": LOCAL}), [])

    def test_servers_are_split_into_from_hub_and_only_here(self):
        from quiver.find import mcps

        with mock.patch("quiver.mcp.cli.load_registry", return_value={"t": {}}), \
             mock.patch("quiver.mcp.cli.get_mcp_tools", return_value={"t": {}}), \
             mock.patch("quiver.mcp.cli.get_tool_servers",
                        return_value={"shared": LOCAL, "localonly": LOCAL}), \
             mock.patch("quiver.mcp.cli.get_tool_config", return_value={"path": ""}):
            views = mcps.tool_views({"shared": LOCAL})
        self.assertEqual(views[0].present, {"shared"})
        self.assertEqual(views[0].only_here, {"localonly"})

    def test_a_parser_error_does_not_take_down_the_view(self):
        from quiver.find import mcps

        with mock.patch("quiver.mcp.cli.load_registry", return_value={"t": {}}), \
             mock.patch("quiver.mcp.cli.get_mcp_tools", return_value={"t": {}}), \
             mock.patch("quiver.mcp.cli.get_tool_servers", side_effect=ValueError), \
             mock.patch("quiver.mcp.cli.get_tool_config", return_value={"path": ""}):
            self.assertEqual(mcps.tool_views({"a": LOCAL}), [])


class RenderTest(unittest.TestCase):
    def _render(self, servers, views):
        from quiver.find.commands import cmd_find_mcps

        with mock.patch("quiver.mcp.cli.get_hub_servers", return_value=servers), \
             mock.patch("quiver.find.mcps.tool_views", return_value=views):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cmd_find_mcps()
        return code, strip_ansi(buf.getvalue())

    def test_an_empty_hub_says_so_rather_than_printing_a_blank_tree(self):
        code, out = self._render({}, [])
        self.assertEqual(code, 0)
        self.assertIn("no hub yet", out)

    def test_it_groups_by_prefix_and_names_the_taxonomy(self):
        code, out = self._render({"dv__github": LOCAL}, [])
        self.assertIn("dv@", out)
        self.assertIn("development", out)

    def test_it_marks_unfiled_servers(self):
        code, out = self._render({"loose": LOCAL}, [])
        self.assertIn("(none)", out)
        self.assertIn("outside the taxonomy", out)

    def test_it_reports_how_far_each_harness_is_behind(self):
        views = [ToolView(name="cline", present=set(), only_here=set(), path="")]
        code, out = self._render({"a": LOCAL, "b": LOCAL}, views)
        self.assertIn("cline", out)
        self.assertIn("0/2", out)
        self.assertIn("1 behind the hub", out)

    def test_a_fully_synced_harness_is_not_counted_as_behind(self):
        views = [ToolView(name="codex", present={"a"}, only_here=set(), path="")]
        code, out = self._render({"a": LOCAL}, views)
        self.assertIn("0 behind the hub", out)

    def test_servers_only_in_a_harness_are_called_out(self):
        views = [ToolView(name="codex", present=set(), only_here={"stray"}, path="")]
        code, out = self._render({"a": LOCAL}, views)
        self.assertIn("only here", out)
        self.assertIn("stray", out)

    def test_duplicates_are_shown_with_the_rule(self):
        code, out = self._render({"notion": LOCAL, "pd__notion": dict(LOCAL)}, [])
        self.assertIn("notion == pd__notion", out)
        self.assertIn("two of a kind is not", out)


if __name__ == "__main__":
    unittest.main()
