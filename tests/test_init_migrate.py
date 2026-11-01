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

from swe import paths
from swe.init import commands as init_commands
from swe.init.migrate import (
    CACHE_FILES,
    CONFIG_FILES,
    DROP,
    apply_migration,
    plan_migration,
    move_secrets,
    write_gitignore,
)


class LayoutInvariantTest(unittest.TestCase):
    """Things that must stay true no matter how the layout is edited."""

    ALL_PATHS = (
        "AGENTS_FILE", "SKILLS_DIR", "BACKUPS_DIR", "CONFIG_DIR", "CACHE_DIR",
        "COMPLETION_DIR", "REPORTS_DIR", "HARNESS_FILE", "TOOLS_FILE", "STARS_FILE",
        "ARCHIVE_FILE",
        "MCP_SOURCE_FILE", "SKILL_CATALOGS_FILE", "SKILL_LINKS_FILE",
        "PROVIDERS_REGISTRY_FILE", "CONFIG_FILE", "SESSION_CACHE_FILE",
        "RATE_LIMITS_CACHE_FILE", "MCP_DIR", "MCP_SERVERS_DIR",
    )

    def test_every_path_lives_under_the_single_root(self):
        for name in self.ALL_PATHS:
            value = getattr(paths, name)
            self.assertTrue(
                str(value).startswith(str(paths.SWE_DIR)),
                f"{name} = {value} escaped {paths.SWE_DIR}",
            )

    def test_no_path_points_into_dot_config(self):
        for name in self.ALL_PATHS:
            self.assertNotIn(
                "/.config/", str(getattr(paths, name)), f"{name} still uses ~/.config"
            )

    def test_root_is_named_for_the_project_not_the_command(self):
        self.assertEqual(paths.SWE_DIR.name, ".quiver")
        self.assertEqual(paths.SWE_DIR.parent, Path.home())

    def test_authored_state_sits_in_config(self):
        for name in ("HARNESS_FILE", "TOOLS_FILE", "STARS_FILE", "ARCHIVE_FILE",
                     "PROVIDERS_REGISTRY_FILE",
                     "SKILL_LINKS_FILE", "SKILL_CATALOGS_FILE", "CONFIG_FILE"):
            self.assertEqual(getattr(paths, name).parent, paths.CONFIG_DIR, name)

    def test_mcp_registry_sits_at_the_root_beside_agents_md(self):
        # mcp.json is the control plane for a whole subsystem, not one more
        # settings file, so it sits alongside AGENTS.md rather than in config/.
        self.assertEqual(paths.MCP_SOURCE_FILE.parent, paths.SWE_DIR)
        self.assertEqual(paths.MCP_SOURCE_FILE.name, "mcp.json")

    def test_mcp_server_checkouts_are_gitignored(self):
        # 2.7 GB across 21 nested git repos. Committing that would make the
        # root unclonable, and mcp.json already records how to rebuild it.
        self.assertEqual(paths.MCP_SERVERS_DIR.parent, paths.MCP_DIR)
        self.assertIn("mcp/servers/", paths.GITIGNORE_BODY)

    def test_usage_ledger_is_gitignored(self):
        # The app keeps it under data/, which is otherwise versioned, but it
        # holds personal usage (project paths, session ids) and ~30 MB of
        # SQLite rewritten on every scan.
        self.assertIn("data/ledger/", paths.GITIGNORE_BODY)

    def test_devin_cloud_sessions_are_gitignored(self):
        # Titles and links of the user's own Devin sessions.
        self.assertIn("data/devin-cloud/", paths.GITIGNORE_BODY)

    def test_web_ledger_is_gitignored(self):
        # The web app's ledger: the same personal rows as data/ledger/. A hub
        # clone is moved in around it, so the hub must never track it.
        self.assertIn("data/web-ledger/", paths.GITIGNORE_BODY.splitlines())

    def test_mcp_secrets_are_gitignored(self):
        # Resolved MCP tokens. A user who runs `git init` in ~/.quiver must
        # not commit them on the first `git add .`.
        from swe.mcp import secrets

        self.assertIn(f"{secrets.SECRETS_DIRNAME}/", paths.GITIGNORE_BODY.splitlines())

    def test_mcp_helpers_and_constants_agree(self):
        self.assertEqual(paths.mcp_dir_for(), paths.MCP_DIR)
        self.assertEqual(paths.mcp_servers_dir_for(), paths.MCP_SERVERS_DIR)
        self.assertEqual(paths.mcp_source_file_for(), paths.MCP_SOURCE_FILE)

    def test_mcp_helpers_respect_an_explicit_home(self):
        fake = Path("/tmp/does-not-exist-home")
        self.assertEqual(paths.mcp_servers_dir_for(fake), fake / ".quiver" / "mcp" / "servers")
        self.assertEqual(paths.mcp_source_file_for(fake), fake / ".quiver" / "mcp.json")

    def test_regenerable_state_sits_in_cache(self):
        for name in ("SESSION_CACHE_FILE", "RATE_LIMITS_CACHE_FILE"):
            self.assertEqual(getattr(paths, name).parent, paths.CACHE_DIR, name)

    def test_cache_is_never_inside_config(self):
        # Otherwise `git add config/` would sweep up 300 KB of cache.
        self.assertFalse(str(paths.CACHE_DIR).startswith(str(paths.CONFIG_DIR)))

    def test_helpers_and_constants_agree(self):
        self.assertEqual(paths.swe_dir_for(), paths.SWE_DIR)
        self.assertEqual(paths.agents_file_for(), paths.AGENTS_FILE)
        self.assertEqual(paths.skills_dir_for(), paths.SKILLS_DIR)
        self.assertEqual(paths.config_dir_for(), paths.CONFIG_DIR)
        self.assertEqual(paths.cache_dir_for(), paths.CACHE_DIR)
        self.assertEqual(paths.backups_dir_for(), paths.BACKUPS_DIR)

    def test_helpers_respect_an_explicit_home(self):
        fake = Path("/tmp/does-not-exist-home")
        self.assertEqual(paths.swe_dir_for(fake), fake / ".quiver")
        self.assertEqual(paths.config_dir_for(fake), fake / ".quiver" / "config")

    def test_init_layout_reexports_the_same_helpers(self):
        from swe.init import layout

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

    def test_mcp_json_lands_at_the_hub_path(self):
        # The MCP hub is ~/.quiver/mcp.json — routing it into config/
        # stranded every server where no reader looks.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={"mcp.json": '{"mcpServers":{}}'})
            plan = plan_migration(home)
            dest = {src.name: dst for src, dst in plan.moved}
            self.assertEqual(dest["mcp.json"], paths.mcp_source_file_for(home))

    def test_dotfiles_are_moved_not_dropped(self):
        # A user notes file in the old root is data, not .DS_Store.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={".notes": "remember this"})
            plan = plan_migration(home)
            dest = {src.name: dst for src, dst in plan.moved}
            self.assertEqual(
                dest[".notes"], paths.config_dir_for(home) / ".notes")
            self.assertNotIn(".notes", {p.name for p in plan.dropped})
            apply_migration(plan)
            self.assertEqual(
                (paths.config_dir_for(home) / ".notes").read_text(),
                "remember this")

    def test_env_lands_under_sealed_secrets_not_versioned_config(self):
        # config/ is the tree a user versions — a stray .env there is one
        # `git add .` from a committed key. Credential files move into the
        # sealed secrets/ instead.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={".env": "TOKEN=x"})
            plan = plan_migration(home)
            dest = {src.name: dst for src, dst in plan.moved}
            self.assertEqual(
                dest[".env"], paths.secrets_dir_for(home) / ".env")
            apply_migration(plan)
            self.assertEqual(
                (paths.secrets_dir_for(home) / ".env").read_text(), "TOKEN=x")
            # Sealed at apply time: the move cannot outrun the .gitignore
            # that keeps it out of the hub's git.
            self.assertTrue(
                (paths.secrets_dir_for(home) / ".gitignore").is_file())

    def test_env_stays_at_source_when_secrets_is_a_symlink(self):
        # Moving through a linked secrets/ would drop the key into a
        # foreign directory the seal refuses to protect.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={".env": "TOKEN=x"})
            secrets_dir = paths.secrets_dir_for(home)
            secrets_dir.parent.mkdir(parents=True, exist_ok=True)
            secrets_dir.symlink_to(home / "foreign")
            plan = apply_migration(plan_migration(home))
            self.assertIn(secrets_dir / ".env", plan.skipped)
            # The credential was not moved through the link; it stays in
            # the legacy root for the user to deal with.
            self.assertFalse((home / "foreign" / ".env").exists())
            self.assertTrue((home / ".config" / "swe" / ".env").is_file())

    def test_a_dangling_symlink_at_the_destination_is_not_replaced(self):
        # exists() misses dangling links; shutil.move would swap the user's
        # dotfiles link for a standalone file.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={"mcp.json": '{"mcpServers":{}}'})
            dest = paths.mcp_source_file_for(home)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(home / "dotfiles" / "mcp.json")
            plan = apply_migration(plan_migration(home))
            self.assertIn(dest, plan.skipped)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(dest.readlink(), home / "dotfiles" / "mcp.json")
            self.assertTrue((home / ".config" / "swe" / "mcp.json").is_file())

    def test_envrc_lands_under_sealed_secrets_not_versioned_config(self):
        # direnv files hold credentials exactly like .env; routing them to
        # config/ puts a key in the tree a user versions.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp, extra={".envrc": "export KEY=x"})
            plan = plan_migration(home)
            dest = {src.name: dst for src, dst in plan.moved}
            self.assertEqual(
                dest[".envrc"], paths.secrets_dir_for(home) / ".envrc")
            apply_migration(plan)
            self.assertEqual(
                (paths.secrets_dir_for(home) / ".envrc").read_text(),
                "export KEY=x")
            self.assertTrue(
                (paths.secrets_dir_for(home) / ".gitignore").is_file())

    def test_a_relative_symlink_moves_with_its_target_reanchored(self):
        # ../../dotfiles/mcp.json resolved inside the legacy root, but the
        # same bytes under the hub root would point outside the home. The
        # moved link must point at where the old one reached.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            real = home / "dotfiles" / "mcp.json"
            real.parent.mkdir()
            real.write_text('{"mcpServers":{}}')
            link = home / ".config" / "swe" / "mcp.json"
            link.symlink_to(Path("..") / ".." / "dotfiles" / "mcp.json")
            plan = apply_migration(plan_migration(home))
            dest = paths.mcp_source_file_for(home)
            self.assertIn((link, dest), plan.moved)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(dest.resolve(), real.resolve())
            self.assertFalse(link.exists())

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

    def test_a_symlinked_dir_in_the_drop_list_unlinks_not_crashes(self):
        # rmtree on a symlinked dir raises OSError mid-apply and strands a
        # half-migrated tree: the link is removed, the target untouched.
        with tempfile.TemporaryDirectory() as tmp:
            home = _legacy_home(tmp)
            outside = home / "elsewhere"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep me")
            (home / ".config" / "swe" / "archive").symlink_to(
                outside, target_is_directory=True)
            apply_migration(plan_migration(home))
            self.assertFalse((home / ".config" / "swe" / "archive").exists())
            self.assertTrue((outside / "keep.txt").is_file())


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


def _keys(path: Path, text: str = 'export OPENAI_API_KEY="sk-test"\n') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


class SecretsLayoutTest(unittest.TestCase):
    def test_each_store_has_its_own_folder_under_secrets(self):
        home = Path("/tmp/does-not-exist-home")
        secrets_dir = home / ".quiver" / "secrets"
        self.assertEqual(paths.provider_keys_file_for(home), secrets_dir / "providers" / ".api_keys")
        self.assertEqual(paths.mcp_secrets_file_for(home), secrets_dir / "mcp" / ".api_keys")

    def test_a_fresh_machine_reads_the_new_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(paths.provider_keys_path_for(home), paths.provider_keys_file_for(home))
            self.assertEqual(paths.mcp_secrets_path_for(home), paths.mcp_secrets_file_for(home))

    def test_an_unmigrated_machine_keeps_reading_its_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old_keys = _keys(home / ".api_keys")
            old_mcp = _keys(home / ".quiver" / "secrets" / ".api_keys")
            self.assertEqual(paths.provider_keys_path_for(home), old_keys)
            self.assertEqual(paths.mcp_secrets_path_for(home), old_mcp)

    def test_mcp_secrets_load_from_either_place(self):
        from swe.mcp import secrets

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            _keys(home / ".quiver" / "secrets" / ".api_keys", "export TOKEN=old\n")
            self.assertEqual(secrets.load_secrets(home), {"TOKEN": "old"})
            _keys(paths.mcp_secrets_file_for(home), "export TOKEN=new\n")
            self.assertEqual(secrets.load_secrets(home), {"TOKEN": "new"})


class MoveSecretsTest(unittest.TestCase):
    def _home(self, tmp: str) -> Path:
        home = Path(tmp)
        write_gitignore(home)
        return home

    def test_moves_both_stores_and_links_the_provider_file_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            _keys(home / ".api_keys")
            loose = _keys(home / ".quiver" / "secrets" / ".api_keys", "export TOKEN=x\n")
            loose.chmod(0o644)

            notes = move_secrets(home)

            self.assertEqual(len(notes), 3)
            new_keys = paths.provider_keys_file_for(home)
            self.assertEqual(new_keys.read_text(), 'export OPENAI_API_KEY="sk-test"\n')
            self.assertEqual(new_keys.stat().st_mode & 0o777, 0o600)
            self.assertEqual(new_keys.parent.stat().st_mode & 0o777, 0o700)
            # Shell profiles that `source ~/.api_keys` keep working.
            self.assertTrue((home / ".api_keys").is_symlink())
            self.assertEqual((home / ".api_keys").resolve(), new_keys.resolve())
            self.assertEqual(paths.mcp_secrets_file_for(home).read_text(), "export TOKEN=x\n")
            self.assertEqual(paths.mcp_secrets_file_for(home).stat().st_mode & 0o777, 0o600)
            # An older swe on the same machine still reads the old MCP path.
            old_mcp = home / ".quiver" / "secrets" / ".api_keys"
            self.assertTrue(old_mcp.is_symlink())
            self.assertEqual(old_mcp.read_text(), "export TOKEN=x\n")

    def test_a_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            _keys(home / ".api_keys")
            move_secrets(home)
            self.assertEqual(move_secrets(home), [])

    def test_a_root_moved_without_the_mcp_link_gets_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            secrets_dir = paths.secrets_dir_for(home)
            _keys(paths.mcp_secrets_file_for(home), "export TOKEN=x\n")
            secrets_dir.joinpath(".gitignore").write_text(paths.SECRETS_GITIGNORE_BODY)
            self.assertEqual(move_secrets(home, apply=False),
                             ["link secrets/.api_keys to secrets/mcp/.api_keys"])
            self.assertFalse((secrets_dir / ".api_keys").is_symlink())
            move_secrets(home)
            self.assertEqual((secrets_dir / ".api_keys").read_text(), "export TOKEN=x\n")
            self.assertEqual(move_secrets(home), [])
            # No provider file was ever there, so none appears in $HOME.
            self.assertFalse((home / ".api_keys").exists())

    def test_plan_only_moves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            _keys(home / ".api_keys")
            notes = move_secrets(home, apply=False)
            self.assertEqual(notes, ["seal secrets/ with its own .gitignore",
                                     "move ~/.api_keys to secrets/providers/.api_keys"])
            self.assertFalse(paths.secrets_dir_for(home).exists())
            self.assertFalse((home / ".api_keys").is_symlink())
            self.assertFalse(paths.provider_keys_file_for(home).exists())

    def test_both_copies_are_left_for_the_user_to_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            _keys(home / ".api_keys", "export A=old\n")
            _keys(paths.provider_keys_file_for(home), "export A=new\n")
            notes = move_secrets(home)
            self.assertIn("merge them by hand", notes[0])
            self.assertEqual((home / ".api_keys").read_text(), "export A=old\n")
            self.assertFalse((home / ".api_keys").is_symlink())

    def test_a_directory_of_key_files_stays_put(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            _keys(home / ".api_keys" / "openai", "sk-test\n")
            self.assertEqual(move_secrets(home), [])
            self.assertTrue((home / ".api_keys").is_dir())
            self.assertEqual(paths.provider_keys_path_for(home), home / ".api_keys")

    def test_moved_keys_stay_out_of_git_whatever_the_root_gitignore_says(self):
        # An older hand-edited .gitignore without secrets/, plus an explicit
        # exception: the folder's own .gitignore still wins.
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / ".quiver"
            root.mkdir()
            (root / ".gitignore").write_text("cache/\n!secrets/providers/.api_keys\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            _keys(home / ".api_keys")
            move_secrets(home)
            self.assertTrue((home / ".api_keys").is_symlink())
            for rel in ("secrets/providers/.api_keys", "secrets/.gitignore"):
                ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", rel])
                self.assertEqual(ignored.returncode, 0, rel)

    def test_an_existing_secrets_dir_is_sealed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            paths.secrets_dir_for(home).mkdir(parents=True)
            self.assertEqual(move_secrets(home), ["seal secrets/ with its own .gitignore"])
            self.assertEqual((paths.secrets_dir_for(home) / ".gitignore").read_text(),
                             paths.SECRETS_GITIGNORE_BODY)
            self.assertEqual(paths.secrets_dir_for(home).stat().st_mode & 0o777, 0o700)
            self.assertEqual(move_secrets(home), [])

    def test_the_app_seals_secrets_with_the_same_text(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "mac" / "Sources" / "QuiverCore"
                  / "APIKeys" / "APIKeys.swift")
        if not source.is_file():
            self.skipTest("app sources are not in this tree (the Nix build ships src/ and tests/)")
        swift = source.read_text()
        body = paths.SECRETS_GITIGNORE_BODY.replace("\n", "\\n")
        self.assertIn(f'static let sealBody = "{body}"', swift)

    def test_nothing_to_seal_on_a_machine_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            self.assertEqual(move_secrets(home), [])
            self.assertFalse(paths.secrets_dir_for(home).exists())

    def test_a_symlinked_secrets_dir_is_never_sealed_or_written_through(self):
        # Path ops follow links: sealing a symlinked secrets/ used to chmod
        # 0700 and plant a catch-all .gitignore in a foreign directory.
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            outside = home / "foreign"
            outside.mkdir()
            paths.secrets_dir_for(home).parent.mkdir(parents=True, exist_ok=True)
            paths.secrets_dir_for(home).symlink_to(
                outside, target_is_directory=True)
            _keys(home / ".api_keys")
            notes = move_secrets(home)
            self.assertIn("left alone", " ".join(notes))
            self.assertFalse((outside / ".gitignore").exists())
            self.assertFalse((outside / "providers" / ".api_keys").exists())
            self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
            self.assertFalse((home / ".api_keys").is_symlink())

    def test_a_dangling_secrets_symlink_still_warns(self):
        # is_dir() follows the link, so a dangling one used to skip the
        # seal call entirely — the credential move was dropped silently.
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            secrets_dir = paths.secrets_dir_for(home)
            secrets_dir.parent.mkdir(parents=True, exist_ok=True)
            secrets_dir.symlink_to(home / "gone")
            _keys(home / ".api_keys")
            notes = move_secrets(home)
            self.assertIn("left alone", " ".join(notes))
            # The key file was not moved anywhere through the dead link.
            self.assertTrue((home / ".api_keys").is_file())
            self.assertFalse((home / ".api_keys").is_symlink())

    def test_a_regular_file_at_secrets_path_warns_and_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            secrets_dir = paths.secrets_dir_for(home)
            secrets_dir.parent.mkdir(parents=True, exist_ok=True)
            secrets_dir.write_text("not a dir")
            _keys(home / ".api_keys")
            notes = move_secrets(home)
            self.assertIn("left alone", " ".join(notes))
            self.assertTrue((home / ".api_keys").is_file())


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

    def test_init_moves_the_key_stores_and_check_only_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            _keys(home / ".api_keys")
            _, out = self._run(home, ["--check"])
            self.assertIn("move ~/.api_keys to secrets/providers/.api_keys", out)
            self.assertFalse((home / ".api_keys").is_symlink())
            self._run(home, [])
            self.assertTrue((home / ".api_keys").is_symlink())
            self.assertTrue(paths.provider_keys_file_for(home).is_file())

    def test_init_writes_a_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            self._run(home, [])
            self.assertTrue((home / ".quiver" / ".gitignore").is_file())

    def test_a_dangling_scaffold_symlink_is_not_written_through(self):
        # exists() is false for a dangling link, so the AGENTS.md seed used
        # to follow it and create the file at the link's target.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            target = home / "foreign" / "AGENTS.md"
            paths.agents_file_for(home).parent.mkdir(parents=True)
            paths.agents_file_for(home).symlink_to(target)
            code, _out = self._run(home, [])
            self.assertIn(code, (0, 1))
            self.assertFalse(target.exists(), "seed wrote through a symlink")
            self.assertTrue(paths.agents_file_for(home).is_symlink())


if __name__ == "__main__":
    unittest.main()
