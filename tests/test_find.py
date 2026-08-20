"""`swe find` is the read-only counterpart to `swe init`.

It answers "what is there", which is the question you ask when a slash command
has gone missing. These tests pin the classification it reports and, above all,
that it never writes: a diagnostic that mutates state is worse than none.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.find import commands as find_commands
from quiver.find.tree import agents_tree, flat_skills, plugin_tree, skills_tree


def _skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")


def _home(tmp: str) -> Path:
    home = Path(tmp)
    shared = home / ".quiver" / "skills"
    _skill(shared, "alpha")
    (home / ".quiver" / "AGENTS.md").write_text("rules\n")
    for rel in (".claude", ".codex"):
        (home / rel).mkdir(parents=True)
    return home


class AgentsTreeTest(unittest.TestCase):
    def test_reports_linked_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/CLAUDE.md").symlink_to(home / ".quiver/AGENTS.md")
            canonical, nodes = agents_tree(home)
            self.assertEqual(canonical, home / ".quiver/AGENTS.md")
            by = {n.label: n for n in nodes}
            self.assertEqual(by["claude"].state, "linked")
            self.assertEqual(by["claude"].kind, "symlink")
            self.assertEqual(by["codex"].state, "create")
            # A harness with no config dir is absent, not broken.
            self.assertEqual(by["amp"].state, "skipped")

    def test_symlink_target_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/CLAUDE.md").symlink_to(home / ".quiver/AGENTS.md")
            _, nodes = agents_tree(home)
            claude = next(n for n in nodes if n.label == "claude")
            self.assertEqual(claude.target, home / ".quiver/AGENTS.md")


class SkillsTreeTest(unittest.TestCase):
    def test_counts_follow_symlinks(self):
        # lazyweb's skills are symlinks into a vendor repo; a recursive glob
        # that ignores symlinks undercounted them, which is what this guards.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            vendor = home / "vendor" / "vskill"
            _skill(home / "vendor", "vskill")
            root = home / ".qwen" / "skills"
            root.mkdir(parents=True)
            (root / "vskill").symlink_to(vendor)
            _, nodes = skills_tree(home)
            qwen = next(n for n in nodes if n.label == "qwen")
            self.assertEqual(qwen.count, 1)

    def test_separate_library_is_flagged_not_absorbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".pane" / "skills", "only-here")
            _, nodes = skills_tree(home)
            pane = next(n for n in nodes if n.label == "pane")
            self.assertEqual(pane.state, "keep")
            self.assertIn("nowhere else", pane.detail)

    def test_flat_skills_lists_the_shared_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            self.assertEqual(flat_skills(home), ["alpha"])


class PluginTreeTest(unittest.TestCase):
    def _build(self, home: Path) -> None:
        for market, name in (("dv", "eng"), ("dv", "cloudflare"), ("rf", "reasoning")):
            plug = home / ".quiver" / "plugins" / market / name
            (plug / ".claude-plugin").mkdir(parents=True)
            _skill(plug / "skills", f"{name}-one")

    def test_groups_by_marketplace(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            self._build(home)
            plugins = plugin_tree(home)
            self.assertEqual(len(plugins), 3)
            self.assertEqual({p.marketplace for p in plugins}, {"dv", "rf"})
            eng = next(p for p in plugins if p.name == "eng")
            self.assertEqual(eng.skills, ["eng-one"])

    def test_handles_a_flat_plugin_layout(self):
        # Before the marketplace split, plugins sat directly under plugins/.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            plug = home / ".quiver" / "plugins" / "solo"
            (plug / ".claude-plugin").mkdir(parents=True)
            _skill(plug / "skills", "solo-one")
            plugins = plugin_tree(home)
            self.assertEqual(len(plugins), 1)
            self.assertEqual(plugins[0].marketplace, "")
            self.assertEqual(plugins[0].name, "solo")

    def test_no_plugins_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(plugin_tree(_home(tmp)), [])


class CmdFindTest(unittest.TestCase):
    def _run(self, home, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = find_commands.cmd_find(args)
        return code, buf.getvalue()

    def test_amd_renders_the_agents_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/CLAUDE.md").symlink_to(home / ".quiver/AGENTS.md")
            code, out = self._run(home, ["amd", "--root"])
            self.assertEqual(code, 0)
            self.assertIn("AGENTS.md", out)
            self.assertIn("synced", out)
            self.assertIn(".claude/CLAUDE.md", out)

    def test_agents_is_an_accepted_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(_home(tmp), ["agents", "--root"])
            self.assertEqual(code, 0)
            self.assertIn("AGENTS.md", out)

    def test_skills_renders_plugins_and_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            plug = home / ".quiver" / "plugins" / "dv" / "eng"
            (plug / ".claude-plugin").mkdir(parents=True)
            _skill(plug / "skills", "tdd")
            code, out = self._run(home, ["skills", "--root"])
            self.assertEqual(code, 0)
            self.assertIn("dv@", out)
            self.assertIn("eng", out)

    def test_bare_find_shows_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(_home(tmp), ["--root"])
            self.assertEqual(code, 0)
            self.assertIn("AGENTS.md", out)
            self.assertIn("Skills", out)

    def test_unknown_topic_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(_home(tmp), ["mcp"])
            self.assertEqual(code, 1)
            self.assertIn("Unknown topic", out)

    def test_help_exits_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(_home(tmp), ["--help"])
            self.assertEqual(code, 0)
            self.assertIn("swe find", out)

    def test_find_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            before = sorted(p.relative_to(home) for p in home.rglob("*"))
            for args in ([], ["amd"], ["skills"]):
                self._run(home, args)
            after = sorted(p.relative_to(home) for p in home.rglob("*"))
            self.assertEqual(before, after, "swe find must not touch the filesystem")


if __name__ == "__main__":
    unittest.main()


class ScopeTest(unittest.TestCase):
    """--root scans from where ~/.quiver lives; default scans the cwd."""

    def _tree(self, tmp: str) -> Path:
        home = _home(tmp)
        proj = home / "work" / "proj"
        (proj / "sub").mkdir(parents=True)
        (proj / "AGENTS.md").write_text("project rules\n")
        (proj / "sub" / "CLAUDE.md").write_text("nested rules\n")
        _skill(proj / ".claude" / "skills", "proj-skill")
        # Vendored trees must never be walked into.
        noisy = proj / "node_modules" / "pkg"
        noisy.mkdir(parents=True)
        (noisy / "AGENTS.md").write_text("vendor\n")
        # A file above the project, so the two scopes genuinely differ.
        (home / ".codex" / "AGENTS.md").write_text("global rules\n")
        return home

    def test_cwd_scope_finds_project_files(self):
        from quiver.find.tree import scan_agents

        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            found = scan_agents(home / "work" / "proj", home)
            names = {str(n.path.relative_to(home / "work" / "proj")) for n in found}
            self.assertEqual(names, {"AGENTS.md", "sub/CLAUDE.md"})

    def test_pruned_directories_are_never_walked(self):
        from quiver.find.tree import scan_agents

        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            found = scan_agents(home / "work" / "proj", home)
            self.assertFalse(any("node_modules" in str(n.path) for n in found))

    def test_root_scope_reaches_further_than_cwd(self):
        from quiver.find.tree import scan_agents

        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            deep = scan_agents(home / "work" / "proj", home)
            wide = scan_agents(home, home)
            self.assertGreater(len(wide), len(deep))

    def test_canonical_file_is_excluded_from_its_own_scan(self):
        from quiver.find.tree import scan_agents

        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            found = scan_agents(home, home)
            self.assertNotIn(home / ".quiver" / "AGENTS.md", [n.path for n in found])

    def test_symlink_to_canonical_reads_as_linked(self):
        from quiver.find.tree import scan_agents

        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            (home / ".claude/CLAUDE.md").symlink_to(home / ".quiver/AGENTS.md")
            found = {n.path: n for n in scan_agents(home, home)}
            self.assertEqual(found[home / ".claude/CLAUDE.md"].state, "linked")

    def test_empty_skills_dirs_are_skipped_in_a_scan(self):
        from quiver.find.tree import scan_skill_roots

        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            (home / "work" / "proj" / "empty" / "skills").mkdir(parents=True)
            found = scan_skill_roots(home / "work" / "proj", home)
            self.assertFalse(any("empty" in str(n.path) for n in found))

    def test_root_flag_is_stripped_before_topic_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            with mock.patch.object(Path, "home", staticmethod(lambda: home)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = find_commands.cmd_find(["--root", "amd"])
            self.assertEqual(code, 0)
            self.assertIn("Managed by quiver", buf.getvalue())

    def test_short_flag_works_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._tree(tmp)
            with mock.patch.object(Path, "home", staticmethod(lambda: home)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = find_commands.cmd_find(["amd", "-r"])
            self.assertEqual(code, 0)
            self.assertIn("Managed by quiver", buf.getvalue())


class ScopeClassificationTest(unittest.TestCase):
    """A file is global when a harness loads it into every session.

    That turns out to be a precise structural rule: it sits directly in a
    harness root. One level deeper is vendored content (plugin caches, editor
    extensions) that ships its own instructions and is invisible while coding,
    which makes it the surface worth watching for injected text.
    """

    def _nodes(self, home: Path):
        from quiver.find.tree import Node

        def n(rel, state="unlinked"):
            return Node("x", home / rel, "file", state)

        return [
            n(".claude/CLAUDE.md"),                                  # global
            n(".config/opencode/AGENTS.md"),                         # global
            n(".codex/plugins/cache/openai/sites/AGENTS.md"),        # vendored
            n(".antigravity/extensions/prettier/CLAUDE.md"),         # vendored
            n("Desktop/proj/AGENTS.md"),                             # local
            n("Documents/Sandbox/x/AGENTS.md"),                      # local
        ]

    def test_direct_child_of_a_harness_root_is_global(self):
        from quiver.find.tree import scope_of

        home = Path("/home/u")
        self.assertEqual(scope_of(home / ".claude/CLAUDE.md", home), "global")
        self.assertEqual(scope_of(home / ".config/opencode/AGENTS.md", home), "global")

    def test_one_level_deeper_is_vendored_not_global(self):
        from quiver.find.tree import scope_of

        home = Path("/home/u")
        self.assertEqual(
            scope_of(home / ".codex/plugins/cache/x/AGENTS.md", home), "vendored")
        self.assertEqual(
            scope_of(home / ".hermes/hermes-agent/AGENTS.md", home), "vendored")

    def test_project_paths_are_local(self):
        from quiver.find.tree import scope_of

        home = Path("/home/u")
        self.assertEqual(scope_of(home / "Desktop/proj/AGENTS.md", home), "local")
        self.assertEqual(scope_of(Path("/elsewhere/AGENTS.md"), home), "local")

    def test_a_synced_root_is_global_however_deep(self):
        # ~/.astrbot/data/skills is two levels down but quiver linked it, so it
        # loads everywhere. Link state has to beat the path rule.
        from quiver.find.tree import Node, node_scope

        home = Path("/home/u")
        deep = Node("astrbot", home / ".astrbot/data/skills", "symlink", "linked")
        self.assertEqual(node_scope(deep, home), "global")

    def test_filter_counts_vendored_even_when_hiding_them(self):
        from quiver.find.tree import filter_scope

        home = Path("/home/u")
        nodes = self._nodes(home)
        shown, vendored = filter_scope(nodes, "global", home)
        self.assertEqual(len(shown), 2)
        self.assertEqual(vendored, 2)

    def test_local_scope_excludes_global_and_vendored(self):
        from quiver.find.tree import filter_scope

        home = Path("/home/u")
        shown, _ = filter_scope(self._nodes(home), "local", home)
        self.assertEqual(len(shown), 2)
        self.assertTrue(all("Desktop" in str(n.path) or "Documents" in str(n.path)
                            for n in shown))

    def test_all_scope_hides_nothing(self):
        from quiver.find.tree import filter_scope

        home = Path("/home/u")
        shown, vendored = filter_scope(self._nodes(home), "all", home)
        self.assertEqual(len(shown), 6)
        self.assertEqual(vendored, 0)


class ScopeFlagTest(unittest.TestCase):
    def _run(self, home, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = find_commands.cmd_find(args)
        return code, buf.getvalue()

    def test_default_is_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _, out = self._run(home, ["amd", "--root"])
            self.assertIn("--scope=global", out)

    def test_scope_can_be_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for want in ("local", "all"):
                _, out = self._run(home, ["amd", "--root", f"--scope={want}"])
                self.assertIn(f"--scope={want}", out)

    def test_unknown_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(_home(tmp), ["amd", "--scope=wat"])
            self.assertEqual(code, 1)
            self.assertIn("Unknown scope", out)

    def test_scope_applies_to_skills_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _, out = self._run(home, ["skills", "--root", "--scope=all"])
            self.assertIn("--scope=all", out)


class ElideTest(unittest.TestCase):
    """Paths shorten from the middle so both ends survive.

    Left-truncation kept the filename but lost which harness a path belonged
    to, which made every vendored hit look identical in the listing.
    """

    def test_short_paths_are_untouched(self):
        from quiver.find.commands import PATH_WIDTH, _elide

        self.assertEqual(_elide("./.claude/CLAUDE.md", PATH_WIDTH), "./.claude/CLAUDE.md")

    def test_long_paths_keep_head_and_tail(self):
        from quiver.find.commands import PATH_WIDTH, _elide

        # Built from the constant so widening PATH_WIDTH cannot silently stop
        # exercising the elision path.
        text = "./.codex/plugins/" + "nested/" * PATH_WIDTH + "AGENTS.md"
        out = _elide(text, PATH_WIDTH)
        self.assertEqual(len(out), PATH_WIDTH)
        self.assertIn("…", out)
        self.assertTrue(out.startswith("./.codex/plugins"), out)
        self.assertTrue(out.endswith("AGENTS.md"), out)

    def test_result_never_exceeds_the_width(self):
        from quiver.find.commands import PATH_WIDTH, _elide

        for n in range(1, 200):
            self.assertLessEqual(len(_elide("x" * n, PATH_WIDTH)), PATH_WIDTH)

    def test_exactly_at_width_is_not_elided(self):
        from quiver.find.commands import PATH_WIDTH, _elide

        text = "a" * PATH_WIDTH
        self.assertEqual(_elide(text, PATH_WIDTH), text)

    def test_tiny_widths_do_not_crash(self):
        from quiver.find.commands import PATH_WIDTH, _elide

        for w in (1, 2, 3):
            self.assertLessEqual(len(_elide("some/long/path/AGENTS.md", w)), w)

    def test_rendered_column_is_the_declared_width(self):
        from quiver.find.commands import PATH_WIDTH, _rel

        home = Path("/home/u")
        deep = home / ("nested/" * 12) / "AGENTS.md"
        rendered = _rel(deep, home, home)
        self.assertEqual(len(rendered), PATH_WIDTH + 1)  # +1 column separator


class TreeRenderTest(unittest.TestCase):
    """Paths nest instead of repeating a shared prefix on every row."""

    def _rows(self, home: Path, rels):
        from quiver.find.commands import _build_trie, _collapse, _walk_trie
        from quiver.find.tree import Node

        nodes = [Node("x", home / r, "file", "unlinked") for r in rels]
        return list(_walk_trie(_collapse(_build_trie(nodes, home, home))))

    def test_shared_prefix_appears_once(self):
        rows = self._rows(Path("/h"), [
            ".hermes/context/kaichenpedia/AGENTS.md",
            ".hermes/context/kaichenpedia/CLAUDE.md",
        ])
        labels = [lbl for _i, lbl, _n in rows]
        # The whole single-child chain collapses onto the directory row, so
        # ".hermes/context/kaichenpedia/" is written once and the two files
        # hang off it.
        self.assertEqual(
            labels, [".hermes/context/kaichenpedia/", "AGENTS.md", "CLAUDE.md"])
        # The directory row carries no node; only files do.
        self.assertIsNone(rows[0][2])
        self.assertIsNotNone(rows[1][2])

    def test_single_file_directory_collapses_to_one_row(self):
        rows = self._rows(Path("/h"), [".amp/AGENTS.md"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], ".amp/AGENTS.md")

    def test_single_child_directory_chain_collapses(self):
        rows = self._rows(Path("/h"), [
            ".codex/plugins/cache/openai/sites/AGENTS.md",
            ".codex/plugins/cache/openai/other/AGENTS.md",
        ])
        labels = [lbl for _i, lbl, _n in rows]
        self.assertEqual(labels[0], ".codex/plugins/cache/openai/")
        self.assertEqual(sorted(labels[1:]), ["other/AGENTS.md", "sites/AGENTS.md"])

    def test_files_sort_before_subdirectories(self):
        rows = self._rows(Path("/h"), [
            ".codex/AGENTS.md",
            ".codex/plugins/a/AGENTS.md",
            ".codex/plugins/b/AGENTS.md",
        ])
        labels = [lbl for _i, lbl, _n in rows]
        self.assertEqual(labels[0], ".codex/")
        self.assertEqual(labels[1], "AGENTS.md")

    def test_branch_glyphs_mark_the_last_child(self):
        rows = self._rows(Path("/h"), [".a/AGENTS.md", ".b/AGENTS.md"])
        self.assertTrue(rows[0][0].endswith("├─ "))
        self.assertTrue(rows[-1][0].endswith("└─ "))

    def test_nesting_indents_with_a_continuation_bar(self):
        # .codex must not be the last row, otherwise its children correctly
        # indent with blanks rather than a continuation bar.
        rows = self._rows(Path("/h"), [
            ".codex/x/AGENTS.md", ".codex/y/AGENTS.md",
            ".zz/a/AGENTS.md", ".zz/b/AGENTS.md",
        ])
        child = [i for i, lbl, _n in rows if lbl.startswith("x/")][0]
        self.assertIn("│", child)

    def test_last_directory_children_indent_with_blanks(self):
        rows = self._rows(Path("/h"), [".a/AGENTS.md", ".z/x/A.md", ".z/y/A.md"])
        child = [i for i, lbl, _n in rows if lbl.startswith("x/")][0]
        self.assertNotIn("│", child)

    def test_no_duplicate_directory_rows(self):
        rows = self._rows(Path("/h"), [
            ".hermes/a/AGENTS.md", ".hermes/a/CLAUDE.md", ".hermes/b/AGENTS.md",
        ])
        dirs = [lbl for _i, lbl, n in rows if n is None]
        self.assertEqual(len(dirs), len(set(dirs)))


class ColumnWidthTest(unittest.TestCase):
    """Elide only when something genuinely overruns the cap."""

    def _width(self, home: Path, rels):
        from quiver.find.commands import (
            PATH_WIDTH, _build_trie, _collapse, _walk_trie)

        from quiver.find.tree import Node
        nodes = [Node("x", home / r, "file", "unlinked") for r in rels]
        rows = list(_walk_trie(_collapse(_build_trie(nodes, home, home))))
        lengths = sorted(len(i + l) for i, l, _ in rows)
        if lengths[-1] <= PATH_WIDTH:
            return lengths[-1], lengths
        p95 = lengths[min(len(lengths) - 1, int(len(lengths) * 0.95))]
        return max(min(p95, PATH_WIDTH), 24), lengths

    def test_short_rows_are_never_elided(self):
        # Everything fits, so the column is the true max and nothing is cut.
        home = Path("/h")
        width, lengths = self._width(home, [
            ".amp/skills", ".codeium/windsurf/skills", ".tabnine/agent/skills",
        ])
        self.assertEqual(width, lengths[-1])
        self.assertEqual(sum(1 for x in lengths if x > width), 0)

    def test_one_outlier_does_not_stretch_every_row(self):
        from quiver.find.commands import PATH_WIDTH

        home = Path("/h")
        rels = [f".t{i}/AGENTS.md" for i in range(40)]
        rels.append(".t0/" + "deep/" * 60 + "AGENTS.md")
        width, lengths = self._width(home, rels)
        self.assertLessEqual(width, PATH_WIDTH)
        self.assertLess(width, lengths[-1])
