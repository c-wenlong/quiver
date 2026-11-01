import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from swe.init import commands as init_commands
from swe.init.layout import (
    agents_file,
    inspect,
    link_states,
    load_registry,
    plan,
    swe_dir,
    skills_dir,
)


def _fake_home(tmp: str) -> Path:
    """A home with a few harness dirs present and the rest absent."""
    home = Path(tmp)
    for rel in (".claude", ".codex", ".qwen", ".config/crush"):
        (home / rel).mkdir(parents=True)
    return home


def _pin_columns(test):
    """Isolate from the user's saved column set.

    `swe list` columns are configurable, so a developer who ran
    `swe list edit` would otherwise see these tests fail against their own
    preference rather than a real regression.
    """
    from unittest import mock

    from swe.harness import columns as _columns

    patch = mock.patch.object(
        _columns, "load_columns", lambda: list(_columns.DEFAULT_COLUMNS))
    patch.start()
    test.addCleanup(patch.stop)
    patch2 = mock.patch(
        "swe.harness.commands.load_columns",
        lambda: list(_columns.DEFAULT_COLUMNS))
    patch2.start()
    test.addCleanup(patch2.stop)


def _seed_registry(test, names=("claude",)):
    """Give `swe list` something to render.

    harness.json ships empty now: what a machine has is only knowable by
    looking, so `swe discover` fills it in. A test that wants a table has to
    say which harnesses it is rendering, or it gets the empty state instead.
    """
    from unittest import mock

    from swe.harness.catalog import HARNESS_CATALOG

    registry = {n: dict(HARNESS_CATALOG[n]) for n in names}
    patch = mock.patch(
        "swe.harness.commands.load_registry", lambda: dict(registry))
    patch.start()
    test.addCleanup(patch.stop)
    return registry



class InspectTest(unittest.TestCase):
    def test_skips_harness_that_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            status = inspect("gemini", Path(".gemini/GEMINI.md"), agents_file(home), home)
            self.assertEqual(status.state, "skipped")
            self.assertFalse(status.changed)

    def test_create_when_dir_exists_but_file_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            status = inspect("claude", Path(".claude/CLAUDE.md"), agents_file(home), home)
            self.assertEqual(status.state, "create")
            self.assertTrue(status.changed)

    def test_linked_when_symlink_already_points_at_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            canonical = agents_file(home)
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_text("rules\n")
            (home / ".claude/CLAUDE.md").symlink_to(canonical)

            status = inspect("claude", Path(".claude/CLAUDE.md"), canonical, home)
            self.assertEqual(status.state, "linked")

    def test_relink_when_symlink_points_somewhere_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            other = home / "elsewhere.md"
            other.write_text("x\n")
            (home / ".claude/CLAUDE.md").symlink_to(other)

            status = inspect("claude", Path(".claude/CLAUDE.md"), agents_file(home), home)
            self.assertEqual(status.state, "relink")
            self.assertIn("elsewhere.md", status.detail)

    def test_conflict_when_a_real_file_is_in_the_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            (home / ".claude/CLAUDE.md").write_text("hand written rules\n")

            status = inspect("claude", Path(".claude/CLAUDE.md"), agents_file(home), home)
            self.assertEqual(status.state, "conflict")
            self.assertIn("--force", status.detail)

    def test_absorb_when_a_real_file_already_matches_the_shared_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            agents_file(home).parent.mkdir(parents=True, exist_ok=True)
            agents_file(home).write_text("same rules\n")
            (home / ".claude/CLAUDE.md").write_text("same rules\n")

            status = inspect("claude", Path(".claude/CLAUDE.md"), agents_file(home), home)
            self.assertEqual(status.state, "absorb")
            self.assertIn("same text", status.detail)

    def test_no_absorb_when_the_shared_copy_is_a_link_to_the_file_itself(self):
        # Absorbing would replace CLAUDE.md with a link to AGENTS.md, which
        # links back to CLAUDE.md: a loop with no text at either end.
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            claude_md = home / ".claude/CLAUDE.md"
            claude_md.write_text("rules\n")
            agents_file(home).parent.mkdir(parents=True, exist_ok=True)
            agents_file(home).symlink_to(claude_md)

            status = inspect("claude", Path(".claude/CLAUDE.md"), agents_file(home), home)
            self.assertEqual(status.state, "conflict")

    def test_a_looping_shared_copy_is_a_conflict_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            (home / ".claude/CLAUDE.md").write_text("rules\n")
            loop = home / ".swe/loop"
            agents_file(home).parent.mkdir(parents=True, exist_ok=True)
            agents_file(home).symlink_to(loop)
            loop.symlink_to(agents_file(home))

            status = inspect("claude", Path(".claude/CLAUDE.md"), agents_file(home), home)
            self.assertEqual(status.state, "conflict")

    def test_plan_returns_both_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            instructions, skills = plan(home)
            self.assertTrue(any(s.label == "claude" for s in instructions))
            self.assertTrue(any(s.label == "claude" for s in skills))


class CmdInitTest(unittest.TestCase):
    def _run(self, home: Path, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
            return code, buf.getvalue()

    def test_check_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            code, out = self._run(home, ["--check"])
            self.assertEqual(code, 0)
            self.assertFalse(swe_dir(home).exists())
            self.assertIn("would-create", out)

    def test_init_creates_scaffold_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            code, _ = self._run(home, [])
            self.assertEqual(code, 0)
            self.assertTrue(agents_file(home).is_file())
            self.assertTrue(skills_dir(home).is_dir())

            link = home / ".claude/CLAUDE.md"
            self.assertTrue(link.is_symlink())
            self.assertEqual(Path(link.readlink()), agents_file(home))

            skills_link = home / ".codex/skills"
            self.assertTrue(skills_link.is_symlink())
            self.assertEqual(Path(skills_link.readlink()), skills_dir(home))

    def test_init_creates_every_hub_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            code, _ = self._run(home, [])
            self.assertEqual(code, 0)
            for name in ("skills", "plugins", "hooks", "mcp", "backups"):
                self.assertTrue((swe_dir(home) / name).is_dir(), name)

    def test_init_repairs_a_half_built_hub(self):
        # #696: a second machine had ~/.swe with only AGENTS.md and an
        # empty skills/. Init adds the missing folders and keeps the rest.
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            skills_dir(home).mkdir(parents=True)
            agents_file(home).write_text("mine\n")
            _, out = self._run(home, ["--check"])
            self.assertIn("plugins/", out)
            self.assertFalse((swe_dir(home) / "plugins").exists())
            code, _ = self._run(home, [])
            self.assertEqual(code, 0)
            for name in ("plugins", "hooks", "mcp"):
                self.assertTrue((swe_dir(home) / name).is_dir(), name)
            self.assertEqual(agents_file(home).read_text(), "mine\n")

    def test_existing_file_blocks_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            target = home / ".claude/CLAUDE.md"
            target.write_text("hand written\n")

            code, out = self._run(home, [])
            self.assertEqual(code, 1)
            self.assertIn("blocked", out)
            self.assertEqual(target.read_text(), "hand written\n")
            self.assertFalse(target.is_symlink())

    def test_force_backs_up_then_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            target = home / ".claude/CLAUDE.md"
            target.write_text("hand written\n")

            code, _ = self._run(home, ["--force"])
            self.assertEqual(code, 0)
            self.assertTrue(target.is_symlink())

            backups = list((swe_dir(home) / "backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "hand written\n")

    def test_force_backs_up_a_real_skills_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            existing = home / ".qwen/skills/demo"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("---\nname: demo\n---\n")

            code, _ = self._run(home, ["--force"])
            self.assertEqual(code, 0)
            self.assertTrue((home / ".qwen/skills").is_symlink())

            saved = list((swe_dir(home) / "backups").glob("*qwen_skills*"))
            self.assertEqual(len(saved), 1)
            self.assertTrue((saved[0] / "demo" / "SKILL.md").is_file())

    def test_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            self._run(home, [])
            code, out = self._run(home, [])
            self.assertEqual(code, 0)
            # The summary always prints the word, so assert the count instead.
            self.assertIn("0 blocked", out)
            self.assertNotIn("conflict", out)
            self.assertEqual(len(list((swe_dir(home) / "backups").iterdir())), 0)

    def test_unknown_option_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            code, out = self._run(home, ["--wat"])
            self.assertEqual(code, 1)
            self.assertIn("Unknown option", out)


if __name__ == "__main__":
    unittest.main()


class LinkStatesTest(unittest.TestCase):
    def test_maps_registry_names_not_short_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            states = link_states(home)
            # quiver calls it "qwen", tools.json calls it "qwen-code".
            self.assertIn("qwen-code", states)
            self.assertNotIn("qwen", states)

    def test_reports_linked_after_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            with mock.patch.object(Path, "home", staticmethod(lambda: home)):
                init_commands.cmd_init([])
            states = link_states(home)
            self.assertEqual(states["claude"]["agents"], "linked")
            self.assertEqual(states["claude"]["skills"], "linked")

    def test_reports_conflict_for_a_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            (home / ".claude/CLAUDE.md").write_text("hand written\n")
            states = link_states(home)
            self.assertEqual(states["claude"]["agents"], "conflict")

    def test_uninstalled_harness_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)  # no ~/.gemini
            # PATH must not leak the developer's own installs in: a machine
            # with gemini installed counts it installed by binary, which is
            # the answer this test asserts stays "skipped".
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                self.assertEqual(link_states(home)["gemini"]["agents"], "skipped")

    def test_legacy_agents_alias_is_not_a_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            self.assertNotIn("agents-legacy", link_states(home))


class ListLinksViewTest(unittest.TestCase):

    def setUp(self):
        _pin_columns(self)
        _seed_registry(self)

    def test_links_flag_skips_the_rate_limit_fetch(self):
        from swe.harness import commands as harness_commands

        called = []

        def _boom(*a, **k):
            called.append(1)
            return {}

        with mock.patch(
            "swe.harness.rate_limits.get_all_rate_limits", side_effect=_boom
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                harness_commands.cmd_list(["--links"])
            self.assertEqual(called, [], "links view must not hit the network")
            out = buf.getvalue()

        self.assertIn("AGENTS.MD", out)
        self.assertIn("SKILLS", out)
        self.assertNotIn("QUOTA", out)

    def test_default_view_shows_neither_usage_nor_links(self):
        from swe.harness import commands as harness_commands

        buf = io.StringIO()
        with redirect_stdout(buf):
            harness_commands.cmd_list([])
        out = buf.getvalue()
        self.assertNotIn("AGENTS.MD", out)
        self.assertNotIn("QUOTA", out)
        self.assertIn("DESCRIPTION", out)


class ListUsageOptInTest(unittest.TestCase):
    """Usage is the only networked part of `swe list`, so it must be opt-in."""

    def setUp(self):
        _pin_columns(self)
        _seed_registry(self)

    def _run(self, args):
        from swe.harness import commands as harness_commands

        calls = []

        def _spy(*a, **k):
            calls.append((a, k))
            return {}

        with mock.patch(
            "swe.harness.rate_limits.get_all_rate_limits", side_effect=_spy
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                harness_commands.cmd_list(args)
        return calls, buf.getvalue()

    def test_plain_list_never_fetches_rate_limits(self):
        calls, out = self._run([])
        self.assertEqual(calls, [])
        self.assertNotIn("QUOTA", out)
        self.assertIn("DESCRIPTION", out)

    def test_usage_flag_fetches(self):
        calls, out = self._run(["--usage"])
        self.assertEqual(len(calls), 1)
        self.assertIn("QUOTA", out)
        self.assertIn("100d", out)

    def test_short_usage_flag(self):
        calls, _ = self._run(["-u"])
        self.assertEqual(len(calls), 1)

    def test_refresh_implies_usage(self):
        calls, out = self._run(["--refresh"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["use_cache"], "refresh must bypass the cache")
        self.assertIn("QUOTA", out)

    def test_links_wins_over_usage_and_stays_offline(self):
        calls, out = self._run(["--links", "--usage"])
        self.assertEqual(calls, [])
        self.assertIn("AGENTS.MD", out)
        self.assertNotIn("QUOTA", out)

    def test_tag_filter_still_works_alongside_flags(self):
        _, out = self._run(["--usage", "agentic"])
        self.assertIn("claude", out)


class RateLimitNegativeCacheTest(unittest.TestCase):
    def test_no_data_names_round_trip_through_the_cache(self):
        from swe.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits_cache.json"
            with mock.patch.object(rl, "RATE_LIMITS_CACHE_FILE", cache):
                rl._save_cached({}, {}, no_data={"antigravity"})
                self.assertEqual(rl._load_cached_no_data(), {"antigravity"})

    def test_no_data_expires_with_the_ttl(self):
        from swe.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits_cache.json"
            with mock.patch.object(rl, "RATE_LIMITS_CACHE_FILE", cache):
                rl._save_cached({}, {}, no_data={"antigravity"})
                with mock.patch.object(rl, "_CACHE_TTL", -1):
                    self.assertEqual(rl._load_cached_no_data(), set())

    def test_missing_cache_file_is_not_an_error(self):
        from swe.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                rl, "RATE_LIMITS_CACHE_FILE", Path(tmp) / "nope.json"
            ):
                self.assertEqual(rl._load_cached_no_data(), set())


class ChainLandsOnCanonicalTest(unittest.TestCase):
    """A Home-Manager-style two-hop chain (harness file -> store link ->
    quiver file) counts as linked: quiver owns the only real content, so
    neither init nor find should fight nix over the first hop."""

    def test_two_hop_chain_reads_linked(self):
        import tempfile
        from swe.init.layout import inspect

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            canonical = home / ".swe" / "AGENTS.md"
            canonical.parent.mkdir(parents=True)
            canonical.write_text("# canon\n")
            store = home / "nix-store" / "hm-files"
            store.mkdir(parents=True)
            (store / "CLAUDE.md").symlink_to(canonical)
            harness_dir = home / ".claude"
            harness_dir.mkdir()
            (harness_dir / "CLAUDE.md").symlink_to(store / "CLAUDE.md")

            status = inspect("claude", Path(".claude/CLAUDE.md"), canonical, home)
            self.assertEqual(status.state, "linked")

    def test_chain_landing_elsewhere_still_relinks(self):
        import tempfile
        from swe.init.layout import inspect

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            canonical = home / ".swe" / "AGENTS.md"
            canonical.parent.mkdir(parents=True)
            canonical.write_text("# canon\n")
            other = home / "elsewhere.md"
            other.write_text("# other\n")
            middle = home / "middle.md"
            middle.symlink_to(other)
            harness_dir = home / ".claude"
            harness_dir.mkdir()
            (harness_dir / "CLAUDE.md").symlink_to(middle)

            status = inspect("claude", Path(".claude/CLAUDE.md"), canonical, home)
            self.assertEqual(status.state, "relink")


class InstalledByBinaryTest(unittest.TestCase):
    """#68: a binary on PATH is install evidence; a config dir only proves
    first run.

    The ``~/.config/<tool>`` shape (crush, opencode, kilo, devin) used to
    read as "harness not installed" because the directory itself was the
    evidence — so a fresh Linux + Nix install, binary present but nothing
    launched yet, was skipped by `swe init` entirely.
    """

    @staticmethod
    def _which(name: str):
        return f"/fake/bin/{name}" if name == "crush" else None

    def test_binary_on_path_plans_create_with_no_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            with mock.patch("swe.init.layout.shutil.which",
                            side_effect=self._which):
                instructions, skills = plan(home, registry={})
            inst = {s.label: s for s in instructions}
            self.assertEqual(inst["crush"].state, "create")
            self.assertEqual(inst["crush"].path, home / ".config/crush/CRUSH.md")
            skill = {str(s.path.relative_to(home)): s for s in skills}
            self.assertEqual(skill[".config/crush/skills"].state, "create")

    def test_init_links_a_never_run_crush(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch("swe.init.layout.shutil.which",
                            side_effect=self._which):
                with mock.patch.object(Path, "home", staticmethod(lambda: home)):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = init_commands.cmd_init([])
            self.assertEqual(code, 0)
            # The whole ~/.config/crush tree is made, like ~/.gemini is.
            link = home / ".config/crush/CRUSH.md"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), agents_file(home).resolve())
            skills_link = home / ".config/crush/skills"
            self.assertTrue(skills_link.is_symlink())
            self.assertEqual(skills_link.resolve(), skills_dir(home).resolve())

    def test_missing_binary_and_no_dirs_stays_skipped(self):
        # The floor did not move: no binary and no evidence is still
        # "harness not installed", never a littered directory.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / "skills").mkdir(parents=True)
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                instructions, skills = plan(home, registry={})
            inst = {s.label: s for s in instructions}
            self.assertEqual(inst["crush"].state, "skipped")
            self.assertEqual(inst["crush"].detail, "harness not installed")
            self.assertFalse(any("crush" in s.label for s in skills))


class CmdInitViewTest(unittest.TestCase):
    """The default view is a tally per section; --full lists every path."""

    def _run(self, home: Path, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
            return code, buf.getvalue()

    def test_default_prints_counts_not_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            self._run(home, [])
            code, out = self._run(home, [])
            self.assertEqual(code, 0)
            self.assertNotIn(".claude/CLAUDE.md", out)
            self.assertNotIn(".codex/skills", out)
            self.assertIn("Instructions", out)
            self.assertIn("Skills", out)
            self.assertRegex(out, r"Instructions\S*\s+\S*\d+ linked")
            self.assertIn("swe init --full", out)

    def test_full_lists_every_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            self._run(home, [])
            code, out = self._run(home, ["--full"])
            self.assertEqual(code, 0)
            self.assertIn(".claude/CLAUDE.md", out)
            self.assertIn(".codex/skills", out)
            self.assertNotIn("swe init --full", out)

    def test_default_still_names_blocked_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            target = home / ".claude/CLAUDE.md"
            target.write_text("hand written\n")
            code, out = self._run(home, [])
            self.assertEqual(code, 1)
            self.assertIn("1 blocked", out)
            self.assertIn("Blocked", out)
            self.assertIn(".claude/CLAUDE.md", out)
            self.assertNotIn(".codex/skills", out)

    def test_check_tallies_would_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            code, out = self._run(home, ["--check"])
            self.assertEqual(code, 0)
            self.assertRegex(out, r"\d+ would-create")
            self.assertNotIn(".codex/skills", out)

    def test_check_conflict_is_blocked_not_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            (home / ".claude/CLAUDE.md").write_text("hand written\n")
            code, out = self._run(home, ["--check"])
            self.assertEqual(code, 1)
            plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
            self.assertIn("1 would-conflict", plain)
            total = re.search(r"(\d+) linked, (\d+) blocked", plain)
            self.assertEqual(int(total.group(2)), 1)
            # The total counts only what is or would become a link; the
            # conflict tallied in the section must not also count as linked.
            sections = plain.split(" linked, ")[0]
            expected = sum(
                int(n) for n in re.findall(r"(\d+) (?:linked|would-create|would-relink|would-absorb)", sections)
            )
            self.assertEqual(int(total.group(1)), expected)


class InitRegistryReadTest(unittest.TestCase):
    """init's own registry read must not degrade corrupt files to {} —
    the callers hand the result to register() and link-state writes."""

    def _home_with_registry(self, tmp: str, body: str) -> Path:
        home = Path(tmp)
        cfg = home / ".swe" / "config"
        cfg.mkdir(parents=True)
        (cfg / "harness.json").write_text(body, encoding="utf-8")
        return home

    def test_missing_registry_still_reads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_registry(Path(tmp)), {})

    def test_corrupt_registry_raises_instead_of_reading_empty(self):
        from swe.configuration import CorruptConfigurationError

        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_registry(tmp, "{corrupt")
            with self.assertRaises(CorruptConfigurationError):
                load_registry(home)

    def test_non_object_registry_raises_instead_of_reading_empty(self):
        from swe.configuration import CorruptConfigurationError

        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_registry(tmp, "[1, 2]")
            with self.assertRaises(CorruptConfigurationError):
                load_registry(home)

    def test_valid_registry_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_registry(
                tmp, '{"claude": {"command": "claude"}}')
            self.assertEqual(
                load_registry(home), {"claude": {"command": "claude"}})
