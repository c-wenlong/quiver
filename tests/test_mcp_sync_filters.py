"""Pattern filters for `swe mcp sync --only/--except`.

Server names carry a category prefix (dv__, pd__, rf__, so__, sr__), so
selecting a whole category, or every categorised server, is the common case.
Exact names must keep working, and a pattern that matches nothing must say so
rather than looking the same as an empty result.
"""

import unittest

from quiver.mcp.cli import _filter_by_patterns

SERVERS = {
    "dv__github": {}, "dv__linear": {}, "dv__sentry": {},
    "pd__notion": {}, "pd__gdrive": {},
    "so__reddit": {},
    "computer-use": {}, "node_repl": {}, "trigger": {},
}


class FilterByPatternsTest(unittest.TestCase):
    def test_exact_name_still_matches_only_itself(self):
        sel, un = _filter_by_patterns(SERVERS, ["dv__github"])
        self.assertEqual(set(sel), {"dv__github"})
        self.assertEqual(un, [])

    def test_category_glob(self):
        sel, _ = _filter_by_patterns(SERVERS, ["dv__*"])
        self.assertEqual(set(sel), {"dv__github", "dv__linear", "dv__sentry"})

    def test_every_prefixed_server(self):
        sel, _ = _filter_by_patterns(SERVERS, ["*__*"])
        self.assertEqual(
            set(sel),
            {"dv__github", "dv__linear", "dv__sentry", "pd__notion", "pd__gdrive", "so__reddit"},
        )
        # The tool-bound ones stay out, which is the whole point.
        for name in ("computer-use", "node_repl", "trigger"):
            self.assertNotIn(name, sel)

    def test_patterns_combine_as_a_union(self):
        sel, _ = _filter_by_patterns(SERVERS, ["dv__*", "so__reddit"])
        self.assertEqual(set(sel), {"dv__github", "dv__linear", "dv__sentry", "so__reddit"})

    def test_overlapping_patterns_do_not_duplicate(self):
        sel, _ = _filter_by_patterns(SERVERS, ["dv__*", "dv__github"])
        self.assertEqual(len(sel), 3)

    def test_unmatched_patterns_are_reported(self):
        sel, un = _filter_by_patterns(SERVERS, ["dv__github", "dv__githbu", "zz__*"])
        self.assertEqual(set(sel), {"dv__github"})
        self.assertEqual(un, ["dv__githbu", "zz__*"])

    def test_empty_pattern_list_selects_nothing(self):
        sel, un = _filter_by_patterns(SERVERS, [])
        self.assertEqual(sel, {})
        self.assertEqual(un, [])

    def test_matching_is_case_sensitive(self):
        # Server names are identifiers; DV__* silently matching dv__* would be
        # a nasty surprise on a case-insensitive filesystem.
        sel, un = _filter_by_patterns(SERVERS, ["DV__*"])
        self.assertEqual(sel, {})
        self.assertEqual(un, ["DV__*"])

    def test_values_are_carried_through_not_replaced(self):
        servers = {"dv__github": {"type": "local", "command": ["gh"]}}
        sel, _ = _filter_by_patterns(servers, ["dv__*"])
        self.assertEqual(sel["dv__github"], servers["dv__github"])

    def test_question_mark_wildcard(self):
        sel, _ = _filter_by_patterns({"a1": {}, "a2": {}, "b1": {}}, ["a?"])
        self.assertEqual(set(sel), {"a1", "a2"})


if __name__ == "__main__":
    unittest.main()


class HubAsSourceTest(unittest.TestCase):
    """~/.quiver/mcp.json is a sync source, never a sync target.

    Data flows into the hub via `discover --apply` and out of it via `sync`.
    Letting sync write into it would put tool-shaped configs in the one file
    everything else reads as canonical.
    """

    def test_hub_aliases_resolve(self):
        from quiver.mcp.cli import is_hub

        for name in ("quiver", "hub", "mcp.json", "."):
            self.assertTrue(is_hub(name), name)

    def test_alias_matching_ignores_case(self):
        from quiver.mcp.cli import is_hub

        self.assertTrue(is_hub("QUIVER"))
        self.assertTrue(is_hub("Hub"))

    def test_harness_names_are_not_the_hub(self):
        from quiver.mcp.cli import is_hub

        for name in ("cursor", "claude", "codex", "opencode", ""):
            self.assertFalse(is_hub(name), name)
        self.assertFalse(is_hub(None))

    def test_hub_format_is_canonical(self):
        from quiver.mcp.cli import get_tool_format

        self.assertEqual(get_tool_format("quiver"), "standard")

    def test_resolve_tool_arg_returns_the_canonical_hub_label(self):
        from quiver.mcp.cli import HUB_LABEL, resolve_tool_arg

        for alias in ("quiver", "hub", "mcp.json", "."):
            self.assertEqual(resolve_tool_arg({}, alias), HUB_LABEL)

    def test_servers_for_source_reads_the_hub_file(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from quiver.mcp import cli

        payload = {"mcpServers": {"dv__github": {"command": "gh"}}, "updated": "x"}
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "mcp.json"
            f.write_text(json.dumps(payload))
            with mock.patch.object(cli, "MCP_SOURCE_FILE", f):
                self.assertEqual(cli.servers_for_source("quiver"), payload["mcpServers"])
                self.assertEqual(cli.get_hub_servers(), payload["mcpServers"])

    def test_missing_hub_file_reads_as_empty_not_an_error(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from quiver.mcp import cli

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cli, "MCP_SOURCE_FILE", Path(tmp) / "nope.json"):
                self.assertEqual(cli.get_hub_servers(), {})

    def test_servers_for_source_falls_through_to_a_harness(self):
        from unittest import mock

        from quiver.mcp import cli

        # Raw, not canonical: --strict needs the unparsed original to spot a
        # lossy conversion, so parsing here would defeat it.
        with mock.patch.object(
            cli, "get_tool_servers", return_value={"x": {}}
        ) as raw:
            self.assertEqual(cli.servers_for_source("cursor"), {"x": {}})
            raw.assert_called_once_with("cursor")
