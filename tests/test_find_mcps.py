"""`swe find mcps`: what the hub holds, and which harnesses are behind it.

`swe mcp list` is the tool-by-server matrix, useful once you know what you
are looking for. This answers the earlier question, so it groups by the
prefix taxonomy and names the harnesses that have not caught up.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.console import strip_ansi
from quiver.find.mcps import ToolView, _kind, _signature, hub_view, prefix_of
from quiver.harness import registry

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
    def _render(self, servers, views, configs=(), stray=None):
        """Render with the disk scan stubbed out.

        The scan walks every harness directory, which takes over a second;
        a render test should not pay that, and should not depend on what
        happens to be installed on the machine running it — including
        which harnesses that machine's own harness.json has archived, now
        that --harness=active filters views by registry state. Pointing
        HARNESS_FILE at a path that does not exist keeps the registry
        empty, so every harness here resolves as unknown and nothing is
        filtered by it.
        """
        from quiver.find.commands import cmd_find_mcps

        with mock.patch("quiver.mcp.cli.get_hub_servers", return_value=servers), \
             mock.patch("quiver.find.mcps.tool_views", return_value=views), \
             mock.patch("quiver.find.mcps.scan_configs", return_value=list(configs)), \
             mock.patch("quiver.find.mcps.unmanaged", return_value=stray or {}), \
             mock.patch.object(registry, "HARNESS_FILE", Path("/nonexistent/harness.json")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cmd_find_mcps()
        return code, strip_ansi(buf.getvalue())

    def test_an_empty_hub_says_so_rather_than_printing_a_blank_tree(self):
        code, out = self._render({}, [])
        self.assertEqual(code, 0)
        self.assertIn("no hub yet", out)

    def test_it_groups_by_prefix(self):
        """The prefixes carry no gloss: you chose them, and a word per
        group cost horizontal room on every line."""
        code, out = self._render({"dv__github": LOCAL}, [])
        self.assertIn("dv@", out)
        self.assertIn("github", out)

    def test_it_marks_unfiled_servers(self):
        code, out = self._render({"loose": LOCAL}, [])
        self.assertIn("(none)", out)
        self.assertIn("outside the taxonomy", out)

    def test_it_reports_how_far_each_harness_is_behind(self):
        views = [ToolView(name="cline", present=set(), only_here=set(), path="")]
        code, out = self._render({"a": LOCAL, "b": LOCAL}, views)
        self.assertIn("cline", out)
        self.assertIn("0/2", out)
        self.assertIn("1 tools behind the hub", out)

    def test_a_fully_synced_harness_is_not_counted_as_behind(self):
        views = [ToolView(name="codex", present={"a"}, only_here=set(), path="")]
        code, out = self._render({"a": LOCAL}, views)
        self.assertIn("0 tools behind the hub", out)

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


class ScanTest(unittest.TestCase):
    """The registered-paths view cannot see a config nobody registered.

    Scanning the disk found four such files here, holding three servers the
    hub had never seen, while `swe mcp discover` reported none.
    """

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path

        self.home = Path(tempfile.mkdtemp())
        self.json = json

    def _write(self, rel, payload, raw=None):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw if raw is not None else self.json.dumps(payload))
        return p

    def _scan(self, **kw):
        from quiver.find.mcps import scan_configs

        return scan_configs(self.home, **kw)

    def test_it_finds_a_config_inside_a_harness_directory(self):
        self._write(".someharness/mcp.json", {"mcpServers": {"a": LOCAL}})
        self.assertEqual([c.harness for c in self._scan()], ["someharness"])

    def test_it_finds_a_config_sitting_directly_in_home(self):
        """~/.claude.json is not inside any harness directory, so a plain
        directory walk misses it entirely."""
        self._write(".claude.json", {"mcpServers": {"a": LOCAL}})
        self.assertEqual([c.harness for c in self._scan()], ["claude"])

    def test_it_reads_toml_as_well_as_json(self):
        self._write(".codex/config.toml", None,
                    raw='[mcp_servers.github]\ncommand = "npx"\n')
        found = self._scan()
        self.assertEqual(len(found), 1)
        self.assertIn("github", found[0].servers)

    def test_it_finds_configs_under_dot_config(self):
        self._write(".config/opencode/opencode.json", {"mcp": {"a": LOCAL}})
        self.assertEqual([c.harness for c in self._scan()], ["opencode"])

    def test_local_and_remote_are_split(self):
        self._write(".h/mcp.json", {"mcpServers": {"l": LOCAL, "r": REMOTE}})
        cfg = self._scan()[0]
        self.assertEqual(cfg.local, ["l"])
        self.assertEqual(cfg.remote, ["r"])

    def test_a_file_with_the_key_but_no_server_shapes_is_ignored(self):
        """"servers" and "mcp" are broad enough to match unrelated files."""
        self._write(".h/other.json", {"servers": {"web": {"port": 8080}}})
        self.assertEqual(self._scan(), [])

    def test_malformed_json_is_skipped_not_raised(self):
        self._write(".h/mcp.json", None, raw="{mcpServers: oops")
        self.assertEqual(self._scan(), [])

    def test_malformed_toml_is_skipped_not_raised(self):
        self._write(".h/config.toml", None, raw="[mcp_servers\nbroken")
        self.assertEqual(self._scan(), [])

    def test_an_empty_server_table_is_not_a_config(self):
        self._write(".h/mcp.json", {"mcpServers": {}})
        self.assertEqual(self._scan(), [])

    def test_vendored_configs_are_dropped_from_global_scope(self):
        self._write(".ide/extensions/some.ext-1.0/.mcp.json",
                    {"mcpServers": {"a": LOCAL}})
        self.assertEqual(self._scan(scope="global"), [])
        self.assertEqual(len(self._scan(scope="all")), 1)

    def test_source_checkouts_are_not_treated_as_installs(self):
        """~/.mcp-servers holds server source; its example configs describe
        how to install a server, not that one is installed."""
        self._write(".mcp-servers/official/x/claude_desktop_config.example.json",
                    {"mcpServers": {"x": LOCAL}})
        self.assertEqual(self._scan(scope="all"), [])

    def test_the_same_file_reached_twice_is_only_reported_once(self):
        import os

        real = self._write(".h/mcp.json", {"mcpServers": {"a": LOCAL}})
        link = self.home / ".other"
        os.symlink(self.home / ".h", link)
        self.assertEqual(len(self._scan()), 1)
        self.assertTrue(real.exists())

    def test_unmanaged_lists_only_what_the_hub_lacks(self):
        from quiver.find.mcps import unmanaged

        self._write(".h/mcp.json", {"mcpServers": {"known": LOCAL, "stray": LOCAL}})
        out = unmanaged(self.home, {"known": LOCAL})
        self.assertEqual(sorted(out), ["stray"])

    def test_unmanaged_records_which_config_declared_it(self):
        from quiver.find.mcps import unmanaged

        self._write(".h/mcp.json", {"mcpServers": {"stray": REMOTE}})
        out = unmanaged(self.home, {})
        self.assertEqual(out["stray"][0].harness, "h")
        self.assertEqual(out["stray"][0].remote, ["stray"])


class FlowLayoutTest(unittest.TestCase):
    """Names are packed onto lines, not laid out in a fixed grid.

    A grid sizes every column to the longest name in the set, so one long
    entry pads every short one and a dozen short names span four rows they
    do not need. Flowing costs the alignment down the columns, which
    nothing was reading, and buys back the vertical space.
    """

    def _lines(self, items, width, **kw):
        from quiver.find import commands

        with mock.patch.object(commands, "terminal_width", return_value=width):
            buf = io.StringIO()
            with redirect_stdout(buf):
                commands._flow(items, **kw)
        return [strip_ansi(ln) for ln in buf.getvalue().splitlines()]

    NAMES = ["github", "linear", "playwright", "sentry", "supabase", "vercel"]

    def test_short_lists_fit_one_line(self):
        self.assertEqual(len(self._lines(self.NAMES, 200)), 1)

    def test_a_narrow_window_wraps(self):
        self.assertGreater(len(self._lines(self.NAMES, 40)), 1)

    def test_no_line_exceeds_the_window(self):
        for width in (40, 60, 100, 200):
            for line in self._lines(self.NAMES, width, indent="      "):
                self.assertLessEqual(len(line), width, (width, line))

    def test_every_name_survives_the_wrap(self):
        joined = " ".join(self._lines(self.NAMES, 40))
        for name in self.NAMES:
            self.assertIn(name, joined)

    def test_the_label_shares_the_first_line(self):
        """Putting it on a line of its own gives back a row per group,
        which is what the reflow was meant to save."""
        lines = self._lines(self.NAMES, 40, indent="       ", head="  dv@  ")
        self.assertTrue(lines[0].startswith("  dv@"))
        self.assertIn("github", lines[0])

    def test_continuation_lines_hang_under_the_label(self):
        lines = self._lines(self.NAMES, 40, indent="       ", head="  dv@  ")
        self.assertTrue(lines[1].startswith("       "), repr(lines[1]))

    def test_a_limit_reports_what_it_dropped(self):
        """Silent truncation reads as "that is all of them"."""
        lines = self._lines([f"s{i}" for i in range(50)], 200, limit=10)
        self.assertIn("40 more", " ".join(lines))

    def test_an_empty_list_prints_nothing(self):
        self.assertEqual(self._lines([], 100), [])

    def test_one_very_long_name_does_not_loop(self):
        lines = self._lines(["x" * 300], 60)
        self.assertEqual(len(lines), 1)
