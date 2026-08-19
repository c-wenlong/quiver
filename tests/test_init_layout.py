import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.init import commands as init_commands
from quiver.init.layout import (
    agents_file,
    inspect,
    link_states,
    plan,
    quiver_dir,
    skills_dir,
)


def _fake_home(tmp: str) -> Path:
    """A home with a few harness dirs present and the rest absent."""
    home = Path(tmp)
    for rel in (".claude", ".codex", ".qwen", ".config/crush"):
        (home / rel).mkdir(parents=True)
    return home


class InspectTest(unittest.TestCase):
    def test_skips_harness_that_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            status = inspect("amp", Path(".amp/AGENTS.md"), agents_file(home), home)
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
            self.assertFalse(quiver_dir(home).exists())
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

            backups = list((quiver_dir(home) / "backups").iterdir())
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

            saved = list((quiver_dir(home) / "backups").glob("*qwen_skills*"))
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
            self.assertEqual(len(list((quiver_dir(home) / "backups").iterdir())), 0)

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
            home = _fake_home(tmp)  # no ~/.amp
            self.assertEqual(link_states(home)["amp"]["agents"], "skipped")

    def test_legacy_agents_alias_is_not_a_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _fake_home(tmp)
            self.assertNotIn("agents-legacy", link_states(home))


class ListLinksViewTest(unittest.TestCase):
    def test_links_flag_skips_the_rate_limit_fetch(self):
        from quiver.harness import commands as harness_commands

        called = []

        def _boom(*a, **k):
            called.append(1)
            return {}

        with mock.patch(
            "quiver.harness.rate_limits.get_all_rate_limits", side_effect=_boom
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                harness_commands.cmd_list(["--links"])
            self.assertEqual(called, [], "links view must not hit the network")
            out = buf.getvalue()

        self.assertIn("AGENTS.MD", out)
        self.assertIn("SKILLS", out)
        self.assertNotIn("REMAINING", out)

    def test_default_view_shows_neither_usage_nor_links(self):
        from quiver.harness import commands as harness_commands

        buf = io.StringIO()
        with redirect_stdout(buf):
            harness_commands.cmd_list([])
        out = buf.getvalue()
        self.assertNotIn("AGENTS.MD", out)
        self.assertNotIn("REMAINING", out)
        self.assertIn("DESCRIPTION", out)


class ListUsageOptInTest(unittest.TestCase):
    """Usage is the only networked part of `swe list`, so it must be opt-in."""

    def _run(self, args):
        from quiver.harness import commands as harness_commands

        calls = []

        def _spy(*a, **k):
            calls.append((a, k))
            return {}

        with mock.patch(
            "quiver.harness.rate_limits.get_all_rate_limits", side_effect=_spy
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                harness_commands.cmd_list(args)
        return calls, buf.getvalue()

    def test_plain_list_never_fetches_rate_limits(self):
        calls, out = self._run([])
        self.assertEqual(calls, [])
        self.assertNotIn("REMAINING", out)
        self.assertIn("DESCRIPTION", out)

    def test_usage_flag_fetches(self):
        calls, out = self._run(["--usage"])
        self.assertEqual(len(calls), 1)
        self.assertIn("REMAINING", out)
        self.assertIn("100d", out)

    def test_short_usage_flag(self):
        calls, _ = self._run(["-u"])
        self.assertEqual(len(calls), 1)

    def test_refresh_implies_usage(self):
        calls, out = self._run(["--refresh"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["use_cache"], "refresh must bypass the cache")
        self.assertIn("REMAINING", out)

    def test_links_wins_over_usage_and_stays_offline(self):
        calls, out = self._run(["--links", "--usage"])
        self.assertEqual(calls, [])
        self.assertIn("AGENTS.MD", out)
        self.assertNotIn("REMAINING", out)

    def test_tag_filter_still_works_alongside_flags(self):
        _, out = self._run(["--usage", "agentic"])
        self.assertIn("claude", out)


class RateLimitNegativeCacheTest(unittest.TestCase):
    def test_no_data_names_round_trip_through_the_cache(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits_cache.json"
            with mock.patch.object(rl, "RATE_LIMITS_CACHE_FILE", cache):
                rl._save_cached({}, {}, no_data={"antigravity"})
                self.assertEqual(rl._load_cached_no_data(), {"antigravity"})

    def test_no_data_expires_with_the_ttl(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits_cache.json"
            with mock.patch.object(rl, "RATE_LIMITS_CACHE_FILE", cache):
                rl._save_cached({}, {}, no_data={"antigravity"})
                with mock.patch.object(rl, "_CACHE_TTL", -1):
                    self.assertEqual(rl._load_cached_no_data(), set())

    def test_missing_cache_file_is_not_an_error(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                rl, "RATE_LIMITS_CACHE_FILE", Path(tmp) / "nope.json"
            ):
                self.assertEqual(rl._load_cached_no_data(), set())
