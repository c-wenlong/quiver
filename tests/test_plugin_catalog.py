"""`swe plugins catalog`: every plugin in every registered marketplace.

The fake home is test_plugin_drift's (hub marketplace ``mk`` with plugin
``tool`` installed and enabled in claude and codex), plus the outside world:
a claude github marketplace and a codex git marketplace (one plugin
installed, one not), a cursor cache, and an install record whose marketplace
is gone. Each test then breaks or adds one thing.
"""

import json
import os
import unittest
from pathlib import Path

from swe.plugins.catalog import _public, build_catalog, owners, plugin_dirs

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_json_output import decode
    from test_plugin_drift import _FakeHome, _write
    from test_plugin_fix import run_swe
except ImportError:  # `python -m unittest tests.test_plugin_catalog`
    from tests.test_json_output import decode
    from tests.test_plugin_drift import _FakeHome, _write
    from tests.test_plugin_fix import run_swe


class _CatalogHome(_FakeHome):
    def setUp(self):
        super().setUp()
        # claude: a github marketplace, cloned under plugins/marketplaces
        self.official = self.home / ".claude" / "plugins" / "marketplaces" / "official"
        _write(self.official / ".claude-plugin" / "marketplace.json", json.dumps({
            "name": "official",
            "description": "Popular plugins",
            "plugins": [
                {"name": "lsp", "description": "Language server", "version": "1.0.0",
                 "category": "development", "tags": ["lsp"], "author": {"name": "Anthropic"},
                 "homepage": "https://example.com/lsp", "source": "./plugins/lsp"},
                {"name": "remote-one", "description": "Fetched at install time",
                 "source": {"source": "url", "url": "https://example.com/r.git"}},
                "not a plugin entry",
            ],
        }))
        _write(self.official / "plugins" / "lsp" / "skills" / "hover" / "SKILL.md",
               "---\nname: hover\ndescription: Hover docs\n---\n")
        lsp_copy = self.home / ".claude" / "plugins" / "cache" / "official" / "lsp" / "1.0.0"
        _write(lsp_copy / "skills" / "hover" / "SKILL.md",
               "---\nname: hover\ndescription: Hover docs\n---\n")
        _write(lsp_copy / ".mcp.json", json.dumps(
            {"mcpServers": {"lsp": {"command": "x", "env": {"TOKEN": "sk-secret"}}}}))
        self.known["official"] = {
            "source": {"source": "github", "repo": "acme/official"},
            "installLocation": str(self.official)}
        self.installed["plugins"]["lsp@official"] = [
            {"scope": "user", "installPath": str(lsp_copy), "version": "1.0.0"}]
        # installed, but its marketplace is no longer registered anywhere
        gone_copy = self.home / ".claude" / "plugins" / "cache" / "gone" / "old" / "2.0.0"
        _write(gone_copy / "skills" / "relic" / "SKILL.md", "---\nname: relic\n---\n")
        self.installed["plugins"]["old@gone"] = [
            {"scope": "user", "installPath": str(gone_copy), "version": "2.0.0"}]
        self.settings["enabledPlugins"]["lsp@official"] = False
        self._write_claude()

        # codex: the same github marketplace, cloned by codex itself
        clone = self.home / ".codex" / ".tmp" / "marketplaces" / "official"
        _write(clone / ".claude-plugin" / "marketplace.json", json.dumps({
            "name": "official", "plugins": [{"name": "lsp", "source": "./plugins/lsp"}]}))
        self.codex_extra = (
            '[marketplaces.official]\nsource_type = "git"\n'
            'source = "https://user:hunter2@example.com/acme/official.git?token=abc"\n\n')
        self._write_codex()

        # cursor: a cache and nothing else
        self.cursor_copy = (self.home / ".cursor" / "plugins" / "cache" / "cursor-public"
                            / "slack" / "abc123")
        _write(self.cursor_copy / "skills" / "post" / "SKILL.md", "---\nname: post\n---\n")
        _write(self.cursor_copy / ".cursor-plugin" / "plugin.json",
               json.dumps({"name": "slack", "description": "Slack for cursor"}))

    def _write_codex(self, register=True):
        super()._write_codex(register)
        path = self.home / ".codex" / "config.toml"
        path.write_text(path.read_text() + "\n" + getattr(self, "codex_extra", ""))

    def catalog(self):
        return build_catalog(self.home)

    def mkt(self, name, catalog=None):
        catalog = catalog or self.catalog()
        return next(m for m in catalog["marketplaces"] if m["name"] == name)

    def plug(self, ref, catalog=None):
        catalog = catalog or self.catalog()
        name, _, market = ref.partition("@")
        return next(p for p in self.mkt(market, catalog)["plugins"] if p["name"] == name)


class MarketplacesTest(_CatalogHome, unittest.TestCase):
    def test_every_registered_marketplace_once_with_where_it_is_registered(self):
        catalog = self.catalog()
        names = [m["name"] for m in catalog["marketplaces"]]
        self.assertEqual(names, ["cursor-public", "gone", "mk", "official", "other"])
        self.assertEqual(catalog["harnesses"], ["claude", "codex", "cursor"])
        self.assertEqual(catalog["actions"], ["claude", "codex"])
        official = self.mkt("official", catalog)
        self.assertEqual(official["harnesses"]["claude"]["source_type"], "github")
        self.assertEqual(official["harnesses"]["claude"]["location"], "acme/official")
        self.assertEqual(official["harnesses"]["codex"]["source_type"], "git")
        self.assertEqual(official["description"], "Popular plugins")
        self.assertFalse(official["hub"])
        self.assertEqual(self.mkt("gone", catalog)["harnesses"], {})

    def test_hub_marketplace_is_marked_with_its_folder(self):
        mk = self.mkt("mk")
        self.assertTrue(mk["hub"])
        self.assertEqual(mk["path"], str(self.market))
        self.assertEqual(mk["harnesses"]["claude"]["source_type"], "directory")

    def test_url_credentials_never_reach_the_output(self):
        doc = json.dumps(self.catalog())
        self.assertNotIn("hunter2", doc)
        self.assertNotIn("token=abc", doc)
        self.assertNotIn("sk-secret", doc)
        self.assertEqual(self.mkt("official")["harnesses"]["codex"]["location"],
                         "https://example.com/acme/official.git")
        self.assertEqual(_public("git@github.com:a/b.git"), "git@github.com:a/b.git")

    def test_unregistered_hub_marketplace_still_listed(self):
        self.known.pop("mk")
        self._write_claude()
        self._write_codex(register=False)
        mk = self.mkt("mk")
        self.assertTrue(mk["hub"])
        self.assertEqual(mk["harnesses"], {})
        tool = next(p for p in mk["plugins"] if p["name"] == "tool")
        self.assertTrue(tool["listed"])
        self.assertFalse(tool["harnesses"]["claude"]["available"])
        self.assertEqual(tool["drift"]["claude"]["state"], "unregistered")

    def test_extra_known_marketplace_counts_as_registered(self):
        self.known.pop("mk")
        self.settings["extraKnownMarketplaces"] = {
            "mk": {"source": {"source": "directory", "path": str(self.market)}}}
        self._write_claude()
        self.assertIn("claude", self.mkt("mk")["harnesses"])


class PluginsTest(_CatalogHome, unittest.TestCase):
    def test_installed_and_uninstalled_plugins_with_metadata(self):
        lsp = self.plug("lsp@official")
        self.assertEqual(lsp["description"], "Language server")
        self.assertEqual(lsp["version"], "1.0.0")
        self.assertEqual(lsp["category"], "development")
        self.assertEqual(lsp["tags"], ["lsp"])
        self.assertEqual(lsp["author"], "Anthropic")
        self.assertEqual(lsp["homepage"], "https://example.com/lsp")
        claude = lsp["harnesses"]["claude"]
        self.assertEqual((claude["available"], claude["installed"], claude["enabled"],
                          claude["version"], claude["scopes"]),
                         (True, True, False, "1.0.0", ["user"]))
        codex = lsp["harnesses"]["codex"]
        self.assertEqual((codex["available"], codex["installed"], codex["enabled"]),
                         (True, False, None))
        remote = self.plug("remote-one@official")
        self.assertEqual(remote["harnesses"]["claude"]["installed"], False)
        self.assertFalse(remote["harnesses"]["codex"]["available"])  # codex's clone lacks it
        self.assertEqual(remote["skills"], [])

    def test_components_and_skills_come_from_the_loaded_copy(self):
        lsp = self.plug("lsp@official")
        self.assertEqual(lsp["components"], {"skills": 1, "mcp_servers": 1})
        (skill,) = lsp["skills"]
        self.assertEqual(skill["name"], "hover")
        self.assertEqual(skill["description"], "Hover docs")
        self.assertEqual(skill["harnesses"], ["claude"])
        self.assertIn(str(Path(".claude") / "plugins" / "cache"), skill["path"])

    def test_claude_reads_a_directory_marketplace_from_its_folder(self):
        # a skill added to the source after install: claude loads it live,
        # codex runs its own copy and does not have it
        _write(self.plugin / "skills" / "new" / "SKILL.md", "---\nname: new\n---\n")
        tool = self.plug("tool@mk")
        self.assertEqual(tool["harnesses"]["claude"]["path"], str(self.plugin))
        self.assertEqual(tool["harnesses"]["claude"]["copies"], [str(self.claude_copy)])
        self.assertEqual(tool["harnesses"]["codex"]["path"], str(self.codex_copy))
        rows = {s["name"]: s["harnesses"] for s in tool["skills"]}
        self.assertEqual(rows, {"go": ["claude", "codex"], "new": ["claude"]})
        self.assertEqual(tool["source_path"], str(self.plugin))

    def test_uninstalled_plugin_shows_skills_from_its_marketplace_folder(self):
        self.installed["plugins"].pop("lsp@official")
        self._write_claude()
        lsp = self.plug("lsp@official")
        (skill,) = lsp["skills"]
        self.assertEqual(skill["harnesses"], [])
        self.assertEqual(skill["path"], str(self.official / "plugins" / "lsp" / "skills" / "hover"))

    def test_installed_with_no_marketplace_is_still_listed(self):
        old = self.plug("old@gone")
        self.assertFalse(old["listed"])
        self.assertTrue(old["harnesses"]["claude"]["installed"])
        self.assertEqual([s["name"] for s in old["skills"]], ["relic"])

    def test_cursor_is_read_only_and_never_guessed(self):
        slack = self.plug("slack@cursor-public")
        cursor = slack["harnesses"]["cursor"]
        self.assertIsNone(cursor["installed"])
        self.assertIsNone(cursor["enabled"])
        self.assertEqual(cursor["path"], str(self.cursor_copy))
        self.assertEqual(slack["description"], "Slack for cursor")
        self.assertNotIn("cursor", self.catalog()["actions"])

    def test_hub_plugin_carries_the_hub_cards_drift(self):
        (self.codex_copy / "agents" / "helper.md").write_text("changed\n")
        tool = self.plug("tool@mk")
        self.assertEqual(tool["drift"]["claude"], {"state": "installed", "fix": None, "id": None})
        self.assertEqual(tool["drift"]["codex"]["state"], "stale")
        self.assertEqual(tool["drift"]["codex"]["id"], "codex:stale:tool@mk")
        self.assertIsNone(self.plug("lsp@official")["drift"])

    def test_a_project_only_claude_install_is_not_installed(self):
        self.installed["plugins"]["lsp@official"] = [
            {"scope": "project", "projectPath": "/work/app",
             "installPath": str(self.home / ".claude" / "plugins" / "cache" / "official"
                                / "lsp" / "1.0.0"), "version": "1.0.0"}]
        self._write_claude()
        claude = self.plug("lsp@official")["harnesses"]["claude"]
        self.assertFalse(claude["installed"])
        self.assertEqual(claude["scopes"], ["project"])
        self.assertIsNone(claude["path"])

    def test_hub_drift_found_when_another_harness_registered_a_clone(self):
        clone = self.home / ".claude" / "plugins" / "marketplaces" / "mk"
        _write(clone / ".claude-plugin" / "marketplace.json", json.dumps(self.manifest))
        (clone / "tool").mkdir()
        self.known["mk"] = {"source": {"source": "github", "repo": "me/mk"},
                            "installLocation": str(clone)}
        self._write_claude()
        tool = self.plug("tool@mk")
        self.assertEqual(tool["source_path"], str(clone / "tool"))
        self.assertIsNotNone(tool["drift"])
        self.assertIn("codex", tool["drift"])

    def test_codex_install_record_is_the_config_table(self):
        tool = self.plug("tool@mk")
        self.assertEqual(tool["harnesses"]["codex"]["version"], "1.0.0")
        self.codex_enabled = "false"
        self._write_codex()
        self.assertIs(self.plug("tool@mk")["harnesses"]["codex"]["enabled"], False)


class MalformedTest(_CatalogHome, unittest.TestCase):
    def test_malformed_manifest_drops_only_that_listing(self):
        (self.official / ".claude-plugin" / "marketplace.json").write_text("{nope")
        catalog = self.catalog()
        official = self.mkt("official", catalog)
        self.assertFalse(official["harnesses"]["claude"]["readable"])
        self.assertTrue(official["harnesses"]["codex"]["readable"])
        lsp = self.plug("lsp@official", catalog)
        self.assertFalse(lsp["harnesses"]["claude"]["available"])
        self.assertTrue(lsp["harnesses"]["claude"]["installed"])  # the record still says so
        self.assertNotIn("remote-one", [p["name"] for p in official["plugins"]])
        self.assertTrue(any(e["marketplace"] == "official" and e["harness"] == "claude"
                            for e in catalog["errors"]))

    def test_registered_marketplace_missing_on_disk(self):
        # A settings.json synced from another Mac names a folder this one
        # never had. The app groups these by this exact wording
        # (CatalogProblems.missingManifest), so it is pinned here.
        self.settings["extraKnownMarketplaces"] = {
            "elsewhere": {"source": {"source": "directory",
                                     "path": str(self.home / ".quiver" / "plugins" / "elsewhere")}}}
        self._write_claude()
        catalog = self.catalog()
        self.assertIn({"harness": "claude", "marketplace": "elsewhere",
                       "detail": "no marketplace manifest on disk",
                       "path": str(self.home / ".quiver" / "plugins" / "elsewhere")},
                      catalog["errors"])
        # A hub path, so it would be the app's Local section, with nothing in it.
        elsewhere = self.mkt("elsewhere", catalog)
        self.assertTrue(elsewhere["hub"])
        self.assertEqual(elsewhere["plugins"], [])

    def test_unreadable_install_record_leaves_that_harness_out(self):
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text("[]")
        catalog = self.catalog()
        self.assertEqual(catalog["harnesses"], ["codex", "cursor"])
        self.assertEqual(catalog["actions"], ["codex"])
        self.assertTrue(any(e["harness"] == "claude" for e in catalog["errors"]))

    def test_malformed_codex_toml_leaves_codex_out(self):
        (self.home / ".codex" / "config.toml").write_text("[[nope")
        self.assertNotIn("codex", self.catalog()["harnesses"])

    def test_malformed_settings_only_costs_enabled_flags(self):
        (self.home / ".claude" / "settings.json").write_text("{")
        tool = self.plug("tool@mk")
        self.assertTrue(tool["harnesses"]["claude"]["installed"])
        self.assertIsNone(tool["harnesses"]["claude"]["enabled"])

    def test_empty_home(self):
        empty = self.home / "empty"
        empty.mkdir()
        self.assertEqual(build_catalog(empty),
                         {"harnesses": [], "actions": [], "marketplaces": [], "errors": []})

    def test_never_writes(self):
        def tree():
            return sorted((str(p), p.stat().st_mtime_ns) for p in self.home.rglob("*")
                          if not p.is_symlink())
        before = tree()
        self.catalog()
        self.assertEqual(tree(), before)


class OwnersTest(_CatalogHome, unittest.TestCase):
    def test_both_path_shapes_of_a_directory_plugin_skill_belong_to_it(self):
        # swe skills reports a claude directory-marketplace skill from the
        # cache copy today, and from the source folder once it reads what
        # claude loads; either way it is the same one plugin.
        dirs = plugin_dirs(self.catalog())
        self.assertEqual(owners(dirs, self.claude_copy / "skills" / "go"), ["tool@mk"])
        self.assertEqual(owners(dirs, self.plugin / "skills" / "go"), ["tool@mk"])
        self.assertEqual(owners(dirs, self.codex_copy / "skills" / "go"), ["tool@mk"])
        self.assertEqual(owners(dirs, self.home / "elsewhere"), [])

    def test_deepest_folder_wins_over_a_root_sourced_sibling(self):
        self.manifest["plugins"].append({"name": "whole", "source": "./"})
        self._write_manifest()
        dirs = plugin_dirs(self.catalog())
        self.assertEqual(owners(dirs, self.plugin / "skills" / "go"), ["tool@mk"])
        self.assertEqual(owners(dirs, self.market / "README.md"), ["whole@mk"])


class CoverageTest(_CatalogHome, unittest.TestCase):
    """Every plugin skill `swe skills --json` reports belongs to exactly one
    catalog plugin, so the Plugins page can show all of them."""

    def test_every_plugin_skill_has_exactly_one_catalog_plugin(self):
        skills = decode(run_swe(self.home, "skills", "--json"))["data"]["skills"]
        plugin_skills = [s for s in skills if s["source"] == "plugin"]
        self.assertGreaterEqual(len(plugin_skills), 4)   # go, hover, relic, post
        catalog = decode(run_swe(self.home, "plugins", "catalog", "--json"))["data"]
        dirs = plugin_dirs(catalog)
        for skill in plugin_skills:
            self.assertEqual(len(owners(dirs, skill["path"])), 1, skill["path"])


class CatalogCliTest(_CatalogHome, unittest.TestCase):
    def test_json_is_one_envelope(self):
        result = run_swe(self.home, "plugins", "catalog", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(set(doc["data"]), {"harnesses", "actions", "marketplaces", "errors"})

    def test_one_marketplace(self):
        result = run_swe(self.home, "plugins", "catalog", "official", "--json")
        self.assertEqual([m["name"] for m in decode(result)["data"]["marketplaces"]], ["official"])
        missing = run_swe(self.home, "plugins", "catalog", "nope", "--json")
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(missing.stdout, "")

    def test_human_views(self):
        result = run_swe(self.home, "plugins")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("official", result.stdout)
        self.assertIn("mk (hub)", result.stdout)
        result = run_swe(self.home, "plugins", "catalog", "official")
        self.assertIn("lsp", result.stdout)
        self.assertIn("Language server", result.stdout)

    def test_hostile_marketplace_names_cannot_drive_the_terminal(self):
        evil = "mk\x1b]52;c;aGk=\x07"
        self.manifest["name"] = evil
        self._write_manifest()
        self.known.pop("mk")
        self._write_claude()
        self._write_codex(register=False)
        result = run_swe(self.home, "plugins", "catalog", evil)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("\x1b]52", result.stdout)
        self.assertNotIn("\x07", result.stdout)

    def test_help(self):
        for args in (("plugins", "--help"), ("plugins", "help"), ("help", "plugins")):
            result = run_swe(self.home, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--harness=claude|codex", result.stdout)


if __name__ == "__main__":
    unittest.main()
