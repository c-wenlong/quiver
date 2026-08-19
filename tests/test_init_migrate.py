"""Regression cover for the 0.2.7 root merge.

Two roots (``~/.quiver`` for harness-facing assets, ``~/.config/swe`` for
quiver's own state) became one. These tests pin the resulting layout, prove the
migration moves everything without losing or clobbering data, and check the
things most likely to rot: a path constant escaping the root, a cache landing
in the versioned half, or a second run undoing the first.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver import paths
from quiver.init import commands as init_commands
from quiver.init.migrate import (
    CACHE_FILES,
    CONFIG_FILES,
    DROP,
    apply_migration,
    plan_migration,
    write_gitignore,
)


class LayoutInvariantTest(unittest.TestCase):
    """Things that must stay true no matter how the layout is edited."""

    ALL_PATHS = (
        "AGENTS_FILE", "SKILLS_DIR", "BACKUPS_DIR", "CONFIG_DIR", "CACHE_DIR",
        "COMPLETION_DIR", "REPORTS_DIR", "REGISTRY_FILE", "STARS_FILE",
        "MCP_SOURCE_FILE", "SKILL_CATALOGS_FILE", "SKILL_LINKS_FILE",
        "PROVIDERS_REGISTRY_FILE", "CONFIG_FILE", "SESSION_CACHE_FILE",
        "RATE_LIMITS_CACHE_FILE",
    )

    def test_every_path_lives_under_the_single_root(self):
        for name in self.ALL_PATHS:
            value = getattr(paths, name)
            self.assertTrue(
                str(value).startswith(str(paths.QUIVER_DIR)),
                f"{name} = {value} escaped {paths.QUIVER_DIR}",
            )

    def test_no_path_points_into_dot_config(self):
        for name in self.ALL_PATHS:
            self.assertNotIn(
                "/.config/", str(getattr(paths, name)), f"{name} still uses ~/.config"
            )

    def test_root_is_named_for_the_project_not_the_command(self):
        self.assertEqual(paths.QUIVER_DIR.name, ".quiver")
        self.assertEqual(paths.QUIVER_DIR.parent, Path.home())

    def test_authored_state_sits_in_config(self):
        for name in ("REGISTRY_FILE", "STARS_FILE", "MCP_SOURCE_FILE",
                     "PROVIDERS_REGISTRY_FILE", "SKILL_LINKS_FILE",
                     "SKILL_CATALOGS_FILE", "CONFIG_FILE"):
            self.assertEqual(getattr(paths, name).parent, paths.CONFIG_DIR, name)

    def test_regenerable_state_sits_in_cache(self):
        for name in ("SESSION_CACHE_FILE", "RATE_LIMITS_CACHE_FILE"):
            self.assertEqual(getattr(paths, name).parent, paths.CACHE_DIR, name)

    def test_cache_is_never_inside_config(self):
        # Otherwise `git add config/` would sweep up 300 KB of cache.
        self.assertFalse(str(paths.CACHE_DIR).startswith(str(paths.CONFIG_DIR)))

    def test_helpers_and_constants_agree(self):
        self.assertEqual(paths.quiver_dir_for(), paths.QUIVER_DIR)
        self.assertEqual(paths.agents_file_for(), paths.AGENTS_FILE)
        self.assertEqual(paths.skills_dir_for(), paths.SKILLS_DIR)
        self.assertEqual(paths.config_dir_for(), paths.CONFIG_DIR)
        self.assertEqual(paths.cache_dir_for(), paths.CACHE_DIR)
        self.assertEqual(paths.backups_dir_for(), paths.BACKUPS_DIR)

    def test_helpers_respect_an_explicit_home(self):
        fake = Path("/tmp/does-not-exist-home")
        self.assertEqual(paths.quiver_dir_for(fake), fake / ".quiver")
        self.assertEqual(paths.config_dir_for(fake), fake / ".quiver" / "config")

    def test_init_layout_reexports_the_same_helpers(self):
        from quiver.init import layout

        self.assertEqual(layout.agents_file(), paths.AGENTS_FILE)
        self.assertEqual(layout.skills_dir(), paths.SKILLS_DIR)
        self.assertEqual(layout.backups_dir(), paths.BACKUPS_DIR)

    def test_gitignore_body_covers_the_regenerable_dirs(self):
        body = paths.GITIGNORE_BODY
        self.assertIn(f"{paths.CACHE_DIR.name}/", body)
        self.assertIn(f"{paths.BACKUPS_DIR.name}/", body)


def _legacy_home(tmp: str, extra: dict[str, str] | None = None) -> Path:
    """A home that still has a fully populated pre-0.2.7 ~/.config/swe."""
    home = Path(tmp)
    old = home / ".config" / "swe"
    old.mkdir(parents=True)
    (old / "tools.json").write_text('{"claude": {"command": "claude"}}')
    (old / "stars.json").write_text('["claude"]')
    (old / "providers.json").write_text('{"anthropic": {}}')
    (old / "session_cache.json").write_text('{"sessions": []}')
    (old / "rate_limits_cache.json").write_text('{"limits": {}}')
    (old / "completions").mkdir()
    (old / "completions" / "swe.zsh").write_text("# completion\n")
    (old / "reports").mkdir()
    (old / "reports" / "r1.json").write_text("{}")
    # Dead weight from the single-file era.
    (old / "mcp.py").write_text("# old script\n")
    (old / "__pycache__").mkdir()
    (old / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    for name, body in (extra or {}).items():
        (old / name).write_text(body)
    return home


class PlanMigrationTest(unittest.TestCase):
    def test_returns_none_without_a_legacy_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(plan_migration(Path(tmp)))

    def test_routes_config_cache_and_passthrough_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            plan = plan_migration(home)
            dest = {src.name: dst for src, dst in plan.moved}

            self.assertEqual(dest["tools.json"].parent, paths.config_dir_for(home))
            self.assertEqual(dest["providers.json"].parent, paths.config_dir_for(home))
            self.assertEqual(dest["session_cache.json"].parent, paths.cache_dir_for(home))
            self.assertEqual(
                dest["rate_limits_cache.json"].parent, paths.cache_dir_for(home)
            )
            self.assertEqual(dest["completions"], home / ".quiver" / "completions")
            self.assertEqual(dest["reports"], home / ".quiver" / "reports")

    def test_dead_source_is_dropped_not_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = plan_migration(_legacy_home(tmp))
            dropped = {p.name for p in plan.dropped}
            self.assertIn("mcp.py", dropped)
            self.assertIn("__pycache__", dropped)
            self.assertNotIn("mcp.py", {s.name for s, _ in plan.moved})

    def test_unknown_files_are_kept_not_dropped(self):
        # Losing an unrecognised file silently is the worst failure here.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={"something_new.json": "{}"})
            plan = plan_migration(home)
            dest = {src.name: dst for src, dst in plan.moved}
            self.assertIn("something_new.json", dest)
            self.assertEqual(dest["something_new.json"].parent, paths.config_dir_for(home))

    def test_drop_list_and_move_lists_do_not_overlap(self):
        self.assertEqual(set(DROP) & set(CONFIG_FILES), set())
        self.assertEqual(set(DROP) & set(CACHE_FILES), set())
        self.assertEqual(set(CONFIG_FILES) & set(CACHE_FILES), set())


class ApplyMigrationTest(unittest.TestCase):
    def test_moves_files_and_removes_the_old_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            apply_migration(plan_migration(home))

            self.assertFalse((home / ".config" / "swe").exists())
            self.assertTrue((paths.config_dir_for(home) / "tools.json").is_file())
            self.assertTrue((paths.cache_dir_for(home) / "session_cache.json").is_file())
            self.assertTrue((home / ".quiver" / "completions" / "swe.zsh").is_file())
            self.assertTrue((home / ".quiver" / "reports" / "r1.json").is_file())

    def test_file_contents_survive_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            before = (home / ".config/swe/tools.json").read_text()
            apply_migration(plan_migration(home))
            after = (paths.config_dir_for(home) / "tools.json").read_text()
            self.assertEqual(before, after)
            self.assertEqual(json.loads(after)["claude"]["command"], "claude")

    def test_dead_source_is_actually_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            apply_migration(plan_migration(home))
            for name in ("mcp.py", "__pycache__"):
                self.assertFalse(
                    (paths.config_dir_for(home) / name).exists(),
                    f"{name} was carried across",
                )

    def test_existing_destination_is_not_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            cfg = paths.config_dir_for(home)
            cfg.mkdir(parents=True)
            (cfg / "tools.json").write_text('{"already": "here"}')

            plan = apply_migration(plan_migration(home))
            self.assertEqual(
                json.loads((cfg / "tools.json").read_text())["already"], "here"
            )
            self.assertIn(cfg / "tools.json", plan.skipped)

    def test_second_run_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            apply_migration(plan_migration(home))
            snapshot = sorted(p.name for p in paths.config_dir_for(home).iterdir())

            self.assertIsNone(plan_migration(home))
            self.assertEqual(
                sorted(p.name for p in paths.config_dir_for(home).iterdir()), snapshot
            )

    def test_source_is_kept_when_remove_is_declined(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            apply_migration(plan_migration(home), remove_source=False)
            self.assertTrue((home / ".config" / "swe").is_dir())


class GitignoreTest(unittest.TestCase):
    def test_written_once_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = write_gitignore(home)
            self.assertIn("cache/", target.read_text())
            self.assertIn("backups/", target.read_text())

            target.write_text("# hand edited\n")
            write_gitignore(home)
            self.assertEqual(target.read_text(), "# hand edited\n")


class CmdInitMigrationTest(unittest.TestCase):
    def _run(self, home, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
        return code, buf.getvalue()

    def test_reports_an_unmigrated_root_without_touching_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            _, out = self._run(home, [])
            self.assertIn("swe init --migrate", out)
            self.assertTrue((home / ".config" / "swe").is_dir())

    def test_migrate_flag_moves_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            _, out = self._run(home, ["--migrate"])
            self.assertIn("migrated", out)
            self.assertFalse((home / ".config" / "swe").exists())

    def test_check_never_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            self._run(home, ["--check", "--migrate"])
            self.assertTrue((home / ".config" / "swe").is_dir())
            self.assertFalse((home / ".quiver" / "config").exists())

    def test_init_writes_a_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            self._run(home, [])
            self.assertTrue((home / ".quiver" / ".gitignore").is_file())


if __name__ == "__main__":
    unittest.main()
