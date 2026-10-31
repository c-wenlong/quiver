"""`swe find plugins` reads six different registry formats.

claude and factory keep an installed_plugins.json, codex declares
[plugins."ref"] blocks with an explicit enabled flag, cursor and grok expose
no install record at all, and devin locks the resolved set in lock.json.
The scope words carry over from the rest of swe find, but a plugin has no
file sitting in a harness root, so:

  global   installed AND enabled
  local    installed but disabled
  all      every manifest on disk, cached-but-uninstalled included
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.find.plugins import (
    count_components,
    discover_plugins,
    filter_plugins,
)
from quiver.harness import registry


class _NoRegistry:
    """discover_plugins() now consults harness.json's capabilities, which
    means it would otherwise pick up whatever the machine running the tests
    happens to have on disk. Pointing HARNESS_FILE at a path that does not
    exist makes ``load_registry_if_present()`` return {}, the same "no
    harness.json yet" state these tests were written against, so the five
    hardcoded PLUGIN_FALLBACK harnesses stay unconditionally scanned."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(registry, "HARNESS_FILE",
                                    Path("/nonexistent/harness.json"))
        patcher.start()
        self.addCleanup(patcher.stop)


def _plugin(root: Path, name: str, skills=(), commands=(), version="1.0.0"):
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}))
    for s in skills:
        d = root / "skills" / s
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {s}\n---\n")
    for cmd in commands:
        d = root / "commands"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cmd}.md").write_text("cmd\n")


def _claude_home(tmp: str, enabled: dict, installs: dict) -> Path:
    home = Path(tmp)
    pl = home / ".claude" / "plugins"
    pl.mkdir(parents=True)
    (pl / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": installs}))
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}))
    return home


class ComponentCountTest(unittest.TestCase):
    def test_counts_each_component_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            _plugin(root, "p", skills=("a", "b"), commands=("go",))
            self.assertEqual(count_components(root), {"skills": 2, "commands": 1})

    def test_counts_skills_nested_deeper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            (root / "skills" / "group" / "deep").mkdir(parents=True)
            (root / "skills" / "group" / "deep" / "SKILL.md").write_text("x")
            self.assertEqual(count_components(root), {"skills": 1})

    def test_falls_back_to_the_marketplace_source(self):
        # A Directory-source marketplace installs by copying, and a copy of a
        # symlinked skill lands as an empty directory. The installed lazyweb
        # plugin has zero SKILL.md while Claude reports seven.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "p"
            (cache / "skills" / "hollow").mkdir(parents=True)   # no SKILL.md
            source = Path(tmp) / "src" / "p"
            _plugin(source, "p", skills=("real",))
            self.assertEqual(count_components(cache), {})
            self.assertEqual(count_components(cache, source), {"skills": 1})

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(count_components(None), {})
        self.assertEqual(count_components(Path("/nope/nothing")), {})


class RegistryReadTest(_NoRegistry, unittest.TestCase):
    def test_reads_claude_installs_and_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp,
                enabled={"a@dv": True, "b@dv": False},
                installs={"a@dv": [{"version": "1.0", "installPath": tmp + "/x"}],
                          "b@dv": [{"version": "2.0", "installPath": tmp + "/y"}]},
            )
            got = {p.ref: p for p in discover_plugins(home)}
            self.assertEqual(got["a@dv"].enabled, True)
            self.assertEqual(got["b@dv"].enabled, False)
            self.assertEqual(got["a@dv"].harness, "claude")
            self.assertEqual(got["a@dv"].version, "1.0")

    def test_reads_codex_toml_enabled_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".codex" / "config.toml").write_text(
                '[plugins."on@mk"]\nenabled = true\n\n'
                '[plugins."off@mk"]\nenabled = false\n')
            got = {p.ref: p.enabled for p in discover_plugins(home)}
            self.assertEqual(got, {"on@mk": True, "off@mk": False})

    def test_a_harness_with_no_registry_is_skipped_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_plugins(Path(tmp)), [])

    def test_cached_plugins_report_unknown_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = home / ".cursor" / "plugins" / "cache" / "mk" / "p"
            _plugin(cache, "p", skills=("s",))
            got = discover_plugins(home)
            self.assertEqual(len(got), 1)
            self.assertIsNone(got[0].enabled)
            self.assertEqual(got[0].harness, "cursor")


class DevinReadTest(_NoRegistry, unittest.TestCase):
    """devin's store: ``lock.json`` names the resolved install set under
    ``~/.local/share/devin/cli/plugins``, and ``cache/<key>/<version>``
    holds the fetched content (``.devin-plugin/plugin.json`` inside).
    """

    def _entry(self, name="default", source="account-upload", version="0.1.0"):
        return {"name": name,
                "spec": {"source": source, "bundleId": name},
                "identity": f"{source}:{name}",
                "version_dir": version}

    def _home(self, tmp: str, resolved=(), cached=()) -> Path:
        home = Path(tmp)
        pl = home / ".local" / "share" / "devin" / "cli" / "plugins"
        (pl / "cache").mkdir(parents=True)
        (pl / "lock.json").write_text(json.dumps({"resolved": list(resolved)}))
        for key, version, name, skills in cached:
            d = pl / "cache" / key / version / ".devin-plugin"
            d.mkdir(parents=True)
            (d / "plugin.json").write_text(json.dumps({"name": name}))
            for s in skills:
                sk = d.parent / "skills" / s
                sk.mkdir(parents=True)
                (sk / "SKILL.md").write_text("x")
        return home

    def test_resolved_entries_are_enabled_with_their_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(
                tmp, resolved=[self._entry()],
                cached=[("account-upload_default-f95236b7", "0.1.0", "default", ("a",))])
            got = discover_plugins(home)
            self.assertEqual(len(got), 1)
            p = got[0]
            self.assertEqual((p.harness, p.name, p.marketplace, p.version),
                             ("devin", "default", "account-upload", "0.1.0"))
            self.assertTrue(p.enabled)
            self.assertEqual(p.components, {"skills": 1})
            self.assertEqual(p.path, home / ".local/share/devin/cli/plugins"
                             / "cache/account-upload_default-f95236b7/0.1.0")

    def test_hooks_json_file_counts_as_a_hooks_component(self):
        # devin ships hooks as one hooks.json at the plugin root, not a dir.
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(
                tmp, resolved=[self._entry()],
                cached=[("account-upload_default-f95236b7", "0.1.0", "default", ())])
            (home / ".local/share/devin/cli/plugins/cache"
             / "account-upload_default-f95236b7/0.1.0/hooks.json").write_text("{}")
            self.assertEqual(discover_plugins(home)[0].components, {"hooks": 1})

    def test_cached_but_unresolved_reports_unknown_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(
                tmp, resolved=[],
                cached=[("account-upload_default-f95236b7", "0.1.0", "default", ("a",))])
            got = discover_plugins(home)
            self.assertEqual(len(got), 1)
            self.assertIsNone(got[0].enabled)
            self.assertEqual(got[0].marketplace, "account-upload")

    def test_a_cache_key_the_identity_does_not_prefix_still_resolves(self):
        # Key derivation is devin's own; falling back to any version match
        # beats reporting an installed plugin as content-less.
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(
                tmp, resolved=[self._entry()],
                cached=[("renamed_dir", "0.1.0", "default", ())])
            self.assertIsNotNone(discover_plugins(home)[0].path)

    def test_a_lock_with_no_resolved_key_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home(tmp)
            self.assertEqual(discover_plugins(home), [])


class MalformedRecordTest(_NoRegistry, unittest.TestCase):
    """Valid JSON of the wrong shape costs its rows, never the command.

    These files belong to claude, droid, cursor and grok; a list where an
    object belongs used to raise AttributeError out of `swe find plugins`.
    """

    def _write(self, home: Path, rel: str, data) -> None:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_wrong_shapes_do_not_raise(self):
        cases = [
            (".claude/plugins/installed_plugins.json", []),
            (".claude/plugins/installed_plugins.json", {"plugins": ["a@dv"]}),
            (".claude/plugins/installed_plugins.json", {"plugins": {"a@dv": ["x"]}}),
            (".claude/plugins/installed_plugins.json", {"plugins": {"a@dv": "x"}}),
            (".claude/settings.json", ["enabledPlugins"]),
            (".claude/settings.json", {"enabledPlugins": ["a@dv"]}),
            (".claude/plugins/known_marketplaces.json", ["dv"]),
            (".claude/plugins/known_marketplaces.json", {"dv": "github"}),
            (".claude/plugins/known_marketplaces.json", {"dv": {"source": "x"}}),
            (".factory/plugins/installed_plugins.json", "plugins"),
            (".cursor/plugins/cache/mk/.claude-plugin/marketplace.json", ["mk"]),
            (".cursor/plugins/cache/mk/p/.claude-plugin/plugin.json", ["p"]),
            (".cursor/plugins/cache/mk/p/.claude-plugin/plugin.json", {"name": 3}),
            (".local/share/devin/cli/plugins/lock.json", []),
            (".local/share/devin/cli/plugins/lock.json", {"resolved": "x"}),
            (".local/share/devin/cli/plugins/lock.json", {"resolved": ["x"]}),
        ]
        for rel, data in cases:
            with self.subTest(rel=rel, data=data), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                (home / ".claude" / "plugins").mkdir(parents=True)
                self._write(home, rel, data)
                for p in discover_plugins(home):
                    self.assertIsInstance(p.name, str)

    def test_a_single_object_record_and_user_scope_are_read(self):
        """plugin_drift accepts one record object as well as a list, and
        prefers the user-scope record; the listing now agrees with it."""
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp, enabled={"a@dv": True, "b@dv": True},
                installs={
                    "a@dv": {"version": "1.0", "installPath": tmp + "/a"},
                    "b@dv": [{"scope": "project", "version": "0.9", "installPath": tmp + "/p"},
                             {"scope": "user", "version": "2.0", "installPath": tmp + "/u"}],
                })
            got = {p.ref: p for p in discover_plugins(home)}
            self.assertEqual(got["a@dv"].version, "1.0")
            self.assertEqual(got["b@dv"].version, "2.0")
            self.assertEqual(got["b@dv"].path, Path(tmp) / "u")

    def test_a_malformed_user_record_does_not_hide_a_valid_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp, enabled={},
                installs={"b@dv": [
                    {"scope": "project", "version": "0.9", "installPath": tmp + "/p"},
                    {"scope": "user", "version": "2.0", "installPath": []},
                ]})
            got = discover_plugins(home)[0]
            self.assertEqual((got.version, got.path), ("0.9", Path(tmp) / "p"))

    def test_a_home_relative_install_path_is_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(tmp, enabled={},
                                installs={"a@dv": [{"installPath": "~/cache/a"}]})
            got = discover_plugins(home)
            self.assertEqual(got[0].path, home / "cache" / "a")

    def test_a_github_marketplace_is_not_read_as_a_folder(self):
        """Its source word ("github") used to be tested as a directory
        relative to the cwd."""
        from quiver.find.plugins import _marketplace_sources

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._write(home, ".claude/plugins/known_marketplaces.json", {
                "gh": {"source": {"source": "github", "repo": "o/r"}},
                "here": {"source": {"source": "directory", "path": "~/mk"}},
            })
            (home / "mk").mkdir()
            (home / "github").mkdir()
            # A real chdir: relative paths resolve against the process's
            # working directory, which patching os.getcwd does not move.
            cwd = os.getcwd()
            os.chdir(home)
            try:
                self.assertEqual(_marketplace_sources(home, "claude"),
                                 {"here": home / "mk"})
            finally:
                os.chdir(cwd)


class ScopeTest(unittest.TestCase):
    def _mixed(self):
        from quiver.find.plugins import Plugin
        return [
            Plugin("claude", "on", "dv", enabled=True),
            Plugin("claude", "off", "dv", enabled=False),
            Plugin("cursor", "cached", "mk", enabled=None),
        ]

    def test_global_is_only_confirmed_enabled(self):
        # Unknown state must not count as enabled: cursor and grok cannot say,
        # and treating cached copies as running overstates what is loaded.
        shown, hidden = filter_plugins(self._mixed(), "global")
        self.assertEqual([p.name for p in shown], ["on"])
        self.assertEqual(hidden, 2)

    def test_local_is_installed_but_disabled(self):
        shown, _ = filter_plugins(self._mixed(), "local")
        self.assertEqual([p.name for p in shown], ["off"])

    def test_all_hides_nothing(self):
        shown, hidden = filter_plugins(self._mixed(), "all")
        self.assertEqual(len(shown), 3)
        self.assertEqual(hidden, 0)

    def test_ref_joins_name_and_marketplace(self):
        from quiver.find.plugins import Plugin
        self.assertEqual(Plugin("h", "n", "mk").ref, "n@mk")
        self.assertEqual(Plugin("h", "n", "").ref, "n")


if __name__ == "__main__":
    unittest.main()


class MarketplaceGroupingTest(_NoRegistry, unittest.TestCase):
    """Plugins group by marketplace, mirroring the on-disk layout.

    ~/.quiver/plugins/dv/cloudflare, and claude's cache mirrors it at
    ~/.claude/plugins/cache/dv/cloudflare/0.1.0.
    """

    def _render(self, home: Path, scope="all"):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from quiver.find import commands as fc

        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                fc.cmd_find_plugins([], False, scope, full=True)
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())

    def test_marketplaces_appear_as_directory_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp, enabled={"a@dv": True, "b@rf": True},
                installs={"a@dv": [{"version": "1", "installPath": tmp + "/a"}],
                          "b@rf": [{"version": "1", "installPath": tmp + "/b"}]})
            out = self._render(home)
            self.assertIn("dv/", out)
            self.assertIn("rf/", out)

    def test_plugin_rows_drop_the_marketplace_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp, enabled={"cloudflare@dv": True},
                installs={"cloudflare@dv": [{"version": "1", "installPath": tmp + "/c"}]})
            out = self._render(home)
            # The name sits under its marketplace row, so "@dv" would be noise.
            self.assertIn("cloudflare", out)
            self.assertNotIn("cloudflare@dv", out)

    def test_two_marketplaces_are_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp, enabled={"x@dv": True, "x@rf": True},
                installs={"x@dv": [{"version": "1", "installPath": tmp + "/1"}],
                          "x@rf": [{"version": "2", "installPath": tmp + "/2"}]})
            out = self._render(home)
            self.assertEqual(out.count("dv/"), 1)
            self.assertEqual(out.count("rf/"), 1)

    def test_summary_counts_marketplaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp, enabled={"a@dv": True, "b@pd": True},
                installs={"a@dv": [{"version": "1", "installPath": tmp + "/a"}],
                          "b@pd": [{"version": "1", "installPath": tmp + "/b"}]})
            self.assertIn("2 marketplaces", self._render(home))

    def test_hashed_marketplace_dirs_resolve_to_their_real_name(self):
        # grok names marketplace directories by content hash; each carries a
        # marketplace.json with the name you would actually recognise.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = home / ".grok" / "marketplace-cache" / "783232b622f8182e"
            (cache / ".claude-plugin").mkdir(parents=True)
            (cache / ".claude-plugin" / "marketplace.json").write_text(
                json.dumps({"name": "claude-plugins-official"}))
            _plugin(cache / "plugins" / "demo", "demo", skills=("s",))
            got = discover_plugins(home)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].marketplace, "claude-plugins-official")

    def test_an_unhashed_marketplace_keeps_its_directory_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = home / ".cursor" / "plugins" / "cache" / "cursor-public"
            _plugin(cache / "slack", "slack", skills=("s",))
            got = discover_plugins(home)
            self.assertEqual(got[0].marketplace, "cursor-public")
