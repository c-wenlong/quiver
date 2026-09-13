"""`swe find` prints section tallies by default and the tree under --full.

Same shape as `swe init`: a bold title per section, a comma-separated count
per state naming the harnesses in it, and a hint pointing at --full. These
tests pin both halves for every topic, because a view that silently kept its
tree (or lost it under --full) is exactly the drift nobody would notice.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.console import strip_ansi, visible_len
from quiver.find import commands as fc
from quiver.find.mcps import ToolView
from quiver.find.plugin_drift import Finding
from quiver.find.summary import Group, tally
from quiver.harness import registry

TREE_GLYPHS = ("├─", "└─")
LOCAL = {"command": "npx", "args": ["thing"]}


def _skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")


def _home(tmp: str) -> Path:
    """claude synced to the shared copy, codex holding its own AGENTS.md,
    a shared skill tree with one plugin, and one enabled claude plugin."""
    home = Path(tmp)
    quiver = home / ".quiver"
    _skill(quiver / "skills", "alpha")
    (quiver / "AGENTS.md").write_text("rules\n")
    plug = quiver / "plugins" / "dv" / "eng"
    (plug / ".claude-plugin").mkdir(parents=True)
    _skill(plug / "skills", "tdd")

    (home / ".claude").mkdir()
    (home / ".codex").mkdir()
    (home / ".claude" / "CLAUDE.md").symlink_to(quiver / "AGENTS.md")
    (home / ".codex" / "AGENTS.md").write_text("own rules\n")
    (home / ".claude" / "skills").symlink_to(quiver / "skills")

    pl = home / ".claude" / "plugins"
    pl.mkdir()
    (pl / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "eng@dv": [{"version": "1", "installPath": str(plug)}],
        "off@rf": [{"version": "2", "installPath": str(home / "off")}],
    }}))
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {"eng@dv": True, "off@rf": False}}))
    return home


class _FindHome(unittest.TestCase):
    """A throwaway home, cwd and registry, with the MCP hub stubbed."""

    hub = {"dv__github": LOCAL, "loose": LOCAL}
    views = [ToolView(name="codex", present={"dv__github"}, path=""),
             ToolView(name="claude", present={"dv__github", "loose"}, path="")]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = _home(tmp.name)
        patches = [
            mock.patch.object(Path, "home", staticmethod(lambda: self.home)),
            mock.patch.object(Path, "cwd", staticmethod(lambda: self.home)),
            mock.patch.object(registry, "HARNESS_FILE", Path("/nonexistent/harness.json")),
            mock.patch("quiver.console.terminal_width", return_value=200),
            mock.patch("quiver.mcp.cli.get_hub_servers", return_value=dict(self.hub)),
            mock.patch("quiver.find.mcps.tool_views", return_value=list(self.views)),
            mock.patch("quiver.find.mcps.scan_configs", return_value=[]),
            mock.patch("quiver.find.mcps.unmanaged", return_value={}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_find(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fc.cmd_find(list(args))
        self.assertEqual(code, 0, args)
        return strip_ansi(buf.getvalue())

    def assertSummary(self, out, hint):
        self.assertIn(hint, out)
        for glyph in TREE_GLYPHS:
            self.assertNotIn(glyph, out)

    def assertFull(self, out):
        self.assertTrue(any(g in out for g in TREE_GLYPHS), out)
        self.assertNotIn("--full lists every path", out)


class AgentsSummaryTest(_FindHome):
    def test_default_tallies_the_scan(self):
        out = self.run_find("amd")
        self.assertIn("Files found  1 synced (claude), 1 own copy (codex)", out)
        self.assertSummary(out, "swe find amd --full lists every path.")

    def test_root_flag_tallies_managed_files_and_the_rest(self):
        out = self.run_find("amd", "-r")
        self.assertIn("Managed by quiver", out)
        # codex's own AGENTS.md is a managed target in the way of a link.
        self.assertIn("1 synced (claude), 1 in the way (codex)", out)
        self.assertIn("not installed (cursor", out)
        self.assertIn("Other files        none here", out)
        self.assertSummary(out, "swe find amd -r --full lists every path.")

    def test_root_flag_hides_an_archived_absent_harness(self):
        reg = self.home / "harness.json"
        reg.write_text(json.dumps({"cursor": {"state": "archived"}}))
        with mock.patch.object(registry, "HARNESS_FILE", reg):
            out = self.run_find("amd", "-r")
        self.assertNotIn("cursor", out.split("Other files")[0])
        with mock.patch.object(registry, "HARNESS_FILE", reg):
            out = self.run_find("amd", "-r", "--harness=all")
        self.assertIn("cursor", out)

    def test_full_prints_the_tree(self):
        out = self.run_find("amd", "-r", "--full")
        self.assertFull(out)
        self.assertIn("~/.claude/CLAUDE.md", out)


class SkillsSummaryTest(_FindHome):
    def test_default_tallies_skill_roots(self):
        out = self.run_find("skills")
        self.assertIn("Skill roots  1 synced (claude)", out)
        self.assertSummary(out, "swe find skills --full lists every path.")

    def test_root_flag_tallies_the_shared_tree_and_harness_roots(self):
        out = self.run_find("skills", "-r")
        self.assertIn("Shared skills  1 always-on, 1 from 1 plugin (eng)", out)
        self.assertIn("Harness roots  1 synced (claude)", out)
        self.assertSummary(out, "swe find skills -r --full lists every path.")

    def test_full_prints_the_tree(self):
        out = self.run_find("skills", "-r", "--full")
        self.assertFull(out)
        self.assertIn("dv@", out)

    def test_swe_skills_tree_still_draws_the_tree(self):
        from quiver.skills.commands import cmd_skills

        with mock.patch("quiver.find.commands.cmd_find_skills", return_value=0) as m:
            with redirect_stdout(io.StringIO()):
                cmd_skills(["tree"])
        self.assertTrue(m.call_args.kwargs.get("full"))


class PluginsSummaryTest(_FindHome):
    def test_default_is_one_line_per_harness(self):
        out = self.run_find("plugins", "--scope=all")
        self.assertIn("claude  2 plugins in 2 marketplaces (dv, rf), 1 disabled (off)", out)
        # The totals line survives the summary.
        self.assertIn("2 plugins · 2 marketplaces · 1 harnesses", out)
        self.assertSummary(out, "swe find plugins --scope=all --full lists every path.")

    def test_scope_hidden_count_is_kept(self):
        out = self.run_find("plugins")
        self.assertIn("claude  1 plugin in 1 marketplace (dv)", out)
        self.assertIn("1 more not in this scope", out)

    def test_full_prints_the_tree(self):
        out = self.run_find("plugins", "--full")
        self.assertFull(out)
        self.assertIn("dv/", out)


class PluginDriftTest(_FindHome):
    """The drift section in `swe find plugins`, over stubbed findings."""

    findings = [
        Finding("codex", "learning", "learning", "stale",
                "installed 0.2.0 vs source 0.2.4, 3 files differ",
                "codex plugin remove learning@learning && codex plugin add learning@learning"),
        Finding("claude", "design", "", "unregistered",
                "~/.quiver/plugins/design is not a claude marketplace",
                "claude plugin marketplace add ~/.quiver/plugins/design"),
    ]

    def stub(self, sources=True, findings=None):
        found = self.findings if findings is None else findings
        for target, value in (("quiver_plugins", [object()] if sources else []),
                              ("plugin_drift", list(found))):
            p = mock.patch.object(fc, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_summary_tallies_drift_by_kind_naming_harness_and_plugin(self):
        self.stub()
        out = self.run_find("plugins")
        self.assertIn("Drift   1 unregistered (claude design), 1 stale (codex learning@learning)", out)

    def test_summary_says_none_when_every_copy_matches(self):
        self.stub(findings=[])
        out = self.run_find("plugins")
        self.assertIn(f"Drift   {fc.DRIFT_NONE}", out)

    def test_no_quiver_plugins_means_no_drift_section(self):
        self.stub(sources=False)
        out = self.run_find("plugins")
        self.assertNotIn("Drift", out)

    def test_full_lists_each_finding_with_its_fix(self):
        self.stub()
        out = self.run_find("plugins", "--full")
        self.assertIn("where ~/.quiver/plugins has not reached a harness", out)
        self.assertIn("learning@learning", out)
        self.assertIn("codex plugin remove learning@learning && codex plugin add learning@learning", out)
        self.assertIn("claude plugin marketplace add ~/.quiver/plugins/design", out)

    def test_archived_harness_findings_are_hidden(self):
        self.stub()
        reg = self.home / "harness.json"
        reg.write_text(json.dumps({"codex": {"state": "archived"}}))
        with mock.patch.object(registry, "HARNESS_FILE", reg):
            out = self.run_find("plugins")
        self.assertIn("1 unregistered (claude design)", out)
        self.assertNotIn("codex learning@learning", out)

    def test_drift_shows_even_when_no_plugin_is_installed(self):
        self.stub()
        with mock.patch.object(fc, "discover_plugins", return_value=[]):
            out = self.run_find("plugins")
        self.assertIn("1 unregistered (claude design)", out)


class McpsSummaryTest(_FindHome):
    def test_default_tallies_hub_and_configs(self):
        out = self.run_find("mcps")
        self.assertIn("Hub              2 servers (dv), 1 without a prefix (loose)", out)
        self.assertIn("Harness configs  1 up to date (claude), 1 behind (codex 1/2)", out)
        self.assertIn("Unmanaged        none", out)
        self.assertSummary(out, "swe find mcps --full lists every path.")

    def test_full_prints_the_tree(self):
        out = self.run_find("mcps", "--full")
        self.assertFull(out)
        self.assertIn("1 tools behind the hub", out)


class BareFindTest(_FindHome):
    def test_every_view_summarises_under_one_hint(self):
        out = self.run_find()
        for title in ("AGENTS.md", "Skills", "Plugins", "MCP servers"):
            self.assertIn(title, out)
        self.assertEqual(out.count("--full lists every path"), 1)
        self.assertTrue(out.rstrip().endswith("swe find --full lists every path."))
        for glyph in TREE_GLYPHS:
            self.assertNotIn(glyph, out)

    def test_hint_echoes_the_flags_that_change_the_full_view(self):
        out = self.run_find("-r", "--harness=all")
        self.assertIn("swe find -r --harness=all --full lists every path.", out)

    def test_full_prints_every_tree_and_no_hint(self):
        self.assertFull(self.run_find("-r", "--full"))

    def test_full_is_stripped_before_the_browser_sees_the_topic(self):
        with mock.patch.object(fc, "_browse", return_value=0) as m:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(fc.cmd_find(["--full", "plugins", "-i"]), 0)
        m.assert_called_once_with("plugins", "global", "active")


class TallyTest(unittest.TestCase):
    def test_counts_words_and_names(self):
        line = strip_ansi(tally([Group(2, "synced", "green", ["claude", "codex"]),
                                 Group(1, "missing", "cyan", ["qwen"])], 200))
        self.assertEqual(line, "2 synced (claude, codex), 1 missing (qwen)")

    def test_empty_groups_are_dropped_and_nothing_says_so(self):
        self.assertEqual(strip_ansi(tally([Group(0, "synced")], 200, "none here")),
                         "none here")

    def test_repeated_names_appear_once(self):
        line = strip_ansi(tally([Group(2, "own copy", "yellow", ["claude", "claude"])], 200))
        self.assertEqual(line, "2 own copy (claude)")

    def test_long_lists_give_way_before_short_ones(self):
        many = [f"harness{i}" for i in range(20)]
        line = strip_ansi(tally([Group(20, "synced", "green", many),
                                 Group(1, "in the way", "red", ["codex"])], 60))
        self.assertLessEqual(visible_len(line), 60)
        self.assertIn("…", line)
        self.assertTrue(line.endswith("1 in the way (codex)"), line)

    def test_counts_survive_any_width(self):
        line = strip_ansi(tally([Group(20, "synced", "green", ["a" * 30]),
                                 Group(3, "missing", "cyan", ["b" * 30])], 5))
        self.assertEqual(line, "20 synced, 3 missing")


if __name__ == "__main__":
    unittest.main()
