"""`--json` for the app's Skills and Harnesses pages.

`swe skills --json` (installed skills and each harness's skills-root link),
`swe skills hub --json` (what the ~/.quiver plugin marketplaces offer),
`swe skills link|unlink --json`, and `swe harness star|archive --json`.

Same contract as test_json_output: one enveloped document on stdout and
nothing else, a refusal leaves stdout empty and says why on stderr. Each
test runs the real CLI against a throwaway HOME whose states are known by
construction, and asserts those literal states.
"""

import json
import tempfile
import unittest
from pathlib import Path

try:
    from test_json_output import REGISTRY, decode, run_swe, write_registry
    from test_link_json import build_machine, snapshot, write_skill
except ImportError:  # run as `python3 -m unittest tests.test_skills_harness_json`
    from tests.test_json_output import REGISTRY, decode, run_swe, write_registry
    from tests.test_link_json import build_machine, snapshot, write_skill


def write_description(folder: Path, name: str, description: str) -> None:
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n")


class SkillsListJsonTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.shared = build_machine(self.home)
        write_description(self.shared / "alpha", "alpha", "Does the alpha thing")

    def data(self, *args):
        result = run_swe(self.home, "skills", *args, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        return doc["data"]

    def test_each_harness_root_carries_its_link_state(self):
        rows = {row["name"]: row for row in self.data()["harnesses"]}
        self.assertEqual(rows["claude"]["state"], "linked")
        self.assertEqual(rows["claude"]["path"], str(self.home / ".claude" / "skills"))
        # codex keeps a skill nobody else has, so it must never be replaced.
        self.assertEqual(rows["codex"]["state"], "keep")
        self.assertIn("exist nowhere else", rows["codex"]["detail"])

    def test_an_archived_harness_is_marked_so(self):
        registry = json.loads(json.dumps(REGISTRY))
        registry["codex"].update(state="archived", archived={"reason": "x"})
        write_registry(self.home, registry)
        rows = {row["name"]: row for row in self.data()["harnesses"]}
        self.assertTrue(rows["codex"]["archived"])
        # init reports it `ignored`; the folder still holds a skill nobody
        # else has, so linking it by hand would refuse. Say that instead.
        self.assertEqual(rows["codex"]["state"], "keep")
        self.assertIn("exist nowhere else", rows["codex"]["detail"])
        self.assertFalse(rows["claude"]["archived"])

    def test_shared_skills_list_the_harnesses_that_see_them(self):
        skills = {s["name"]: s for s in self.data()["skills"]}
        alpha = skills["alpha"]
        self.assertEqual(alpha["source"], "shared")
        self.assertEqual(alpha["description"], "Does the alpha thing")
        self.assertEqual(alpha["path"], str((self.shared / "alpha").resolve()))
        # claude's root links to the shared tree; codex's is its own folder.
        self.assertEqual(alpha["harnesses"], ["claude"])

    def test_a_harness_private_skill_belongs_to_that_harness_only(self):
        skills = {s["name"]: s for s in self.data()["skills"]}
        own = skills["only-codex"]
        self.assertEqual(own["source"], "harness")
        self.assertEqual(own["root"], "codex")
        self.assertEqual(own["harnesses"], ["codex"])

    def install_plugin(self, enabled=None):
        """tool@mk 1.0 in claude's cache, with or without an install record."""
        version = self.home / ".claude" / "plugins" / "cache" / "mk" / "tool" / "1.0"
        write_skill(version / "skills", "from-plugin")
        if enabled is not None:
            (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
                "version": 2,
                "plugins": {"tool@mk": [{"scope": "user", "installPath": str(version)}]}}))
            (self.home / ".claude" / "settings.json").write_text(
                json.dumps({"enabledPlugins": {"tool@mk": enabled}}))
        return {s["name"]: s for s in self.data()["skills"]}["from-plugin"]

    def test_an_installed_plugin_skill_belongs_to_its_harness(self):
        skill = self.install_plugin(enabled=True)
        self.assertEqual(skill["source"], "plugin")
        self.assertEqual(skill["harnesses"], ["claude"])

    def test_a_disabled_plugin_skill_loads_nowhere(self):
        self.assertEqual(self.install_plugin(enabled=False)["harnesses"], [])

    def test_a_cached_plugin_with_no_install_record_loads_nowhere(self):
        skill = self.install_plugin()
        self.assertEqual(skill["source"], "plugin")
        self.assertEqual(skill["harnesses"], [])

    def test_only_the_installed_version_of_a_cached_plugin_is_listed(self):
        # Claude keeps an old version's folder after an update; it no longer loads.
        cache = self.home / ".claude" / "plugins" / "cache" / "mk" / "tool"
        write_skill(cache / "1.0" / "skills", "from-plugin")
        write_skill(cache / "2.0" / "skills", "from-plugin")
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {"tool@mk": [{"scope": "user", "installPath": str(cache / "2.0")}]}}))
        paths = [s["path"] for s in self.data()["skills"] if s["name"] == "from-plugin"]
        self.assertEqual(len(paths), 1)
        self.assertIn("/2.0/", paths[0])

    def test_a_filter_narrows_the_skills(self):
        names = [s["name"] for s in self.data("only")["skills"]]
        self.assertEqual(names, ["only-codex"])
        # A harness name matches every skill it loads.
        names = [s["name"] for s in self.data("list", "claude")["skills"]]
        self.assertEqual(names, ["alpha", "beta"])

    def test_two_filters_are_refused(self):
        result = run_swe(self.home, "skills", "a", "b", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unexpected args: b", result.stderr)

    def test_builtin_and_catalog_skills_say_where_they_came_from(self):
        write_skill(self.home / ".cursor" / "skills-cursor", "cursor-own")
        catalog = self.home / "work" / "kit"
        write_skill(catalog, "from-catalog")
        config = self.home / ".quiver" / "config"
        (config / "skill_catalogs.json").write_text(json.dumps(
            {"catalogs": [{"label": "kit", "path": str(catalog)}]}))
        skills = {s["name"]: s for s in self.data()["skills"]}
        self.assertEqual(skills["cursor-own"]["source"], "builtin")
        self.assertEqual(skills["cursor-own"]["harnesses"], ["cursor"])
        self.assertEqual(skills["from-catalog"]["source"], "catalog")
        self.assertEqual(skills["from-catalog"]["harnesses"], [])

    def test_an_unreadable_linkignore_warns_and_still_lists(self):
        ignore = self.home / ".quiver" / ".linkignore"
        ignore.mkdir()  # a directory where a file belongs cannot be read
        result = run_swe(self.home, "skills", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning", result.stderr)
        rows = {row["name"]: row for row in decode(result)["data"]["harnesses"]}
        self.assertEqual(rows["claude"]["state"], "linked")

    def test_a_malformed_install_record_is_read_as_no_record(self):
        version = self.home / ".claude" / "plugins" / "cache" / "mk" / "tool" / "1.0"
        write_skill(version / "skills", "from-plugin")
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "plugins": {"a@mk": [{"installPath": ""}, "junk"],
                        "b@mk": {"installPath": str(self.home / "elsewhere")},
                        "c@mk": [{"installPath": str(self.home / ".claude" / "plugins" / "cache" / "mk")}]}}))
        skills = {s["name"]: s for s in self.data()["skills"]}
        self.assertEqual(skills["from-plugin"]["harnesses"], [])

    def test_the_listing_writes_nothing(self):
        before = snapshot(self.home)
        self.data()
        self.assertEqual(snapshot(self.home), before)

    def test_unknown_flags_are_refused_with_empty_stdout(self):
        result = run_swe(self.home, "skills", "--bogus", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)


class SkillsLinkJsonTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.shared = build_machine(self.home)

    def test_unlink_then_link_round_trips_in_the_envelope(self):
        root = self.home / ".claude" / "skills"
        result = run_swe(self.home, "skills", "unlink", str(root), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result)["data"],
                         {"action": "unlink", "harness": "claude",
                          "path": str(root), "mkdir": False})
        self.assertFalse(root.exists() or root.is_symlink())

        result = run_swe(self.home, "skills", "link", str(root), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode(result)["data"]
        self.assertEqual(data["action"], "link")
        self.assertEqual(data["harness"], "claude")
        self.assertEqual(root.resolve(), self.shared.resolve())

    def test_a_root_without_a_label_is_named_after_its_harness(self):
        # hermes is not in swe skills' label table, so only a path reaches it.
        root = self.home / ".hermes" / "skills"
        root.parent.mkdir()
        result = run_swe(self.home, "skills", "link", str(root), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result)["data"]["harness"], "hermes")
        self.assertEqual(root.resolve(), self.shared.resolve())

        result = run_swe(self.home, "skills", "unlink", str(root), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result)["data"]["harness"], "hermes")
        self.assertFalse(root.is_symlink())
        self.assertTrue((self.shared / "alpha" / "SKILL.md").is_file())

    def test_a_bare_word_never_resolves_to_a_symlink_in_the_cwd(self):
        import subprocess, sys, os
        work = self.home / "work"
        work.mkdir()
        (work / "codx").symlink_to(self.shared)
        env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "NO_COLOR": "1"}
        env.update({k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")})
        result = subprocess.run([sys.executable, "-m", "quiver.cli", "skills", "unlink", "codx", "--json"],
                                capture_output=True, text=True, env=env, cwd=work, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown skill root", result.stderr)
        self.assertTrue((work / "codx").is_symlink())

    def test_a_refused_link_leaves_stdout_empty(self):
        # codex holds a skill nobody else has: linking without --force refuses.
        root = self.home / ".codex" / "skills"
        result = run_swe(self.home, "skills", "link", str(root), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("non-empty directory", result.stderr)
        self.assertTrue((root / "only-codex" / "SKILL.md").is_file())


def write_marketplace(home: Path) -> Path:
    """~/.quiver/plugins/kit: one plugin, `toolkit`, shipping two skills."""
    market = home / ".quiver" / "plugins" / "kit"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "kit",
        "plugins": [{
            "name": "toolkit", "source": "./toolkit",
            "description": "Tools for testing",
        }],
    }))
    plugin = market / "toolkit"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "toolkit", "version": "1.2.0"}))
    write_description(write_skill(plugin / "skills", "hammer"), "hammer", "Hits things")
    write_skill(plugin / "skills", "saw")
    return market


class SkillsHubJsonTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)
        self.market = write_marketplace(self.home)
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {}}))
        (plugins / "known_marketplaces.json").write_text(json.dumps({}))

    def data(self):
        result = run_swe(self.home, "skills", "hub", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return decode(result)["data"]

    def register(self):
        (self.home / ".claude" / "plugins" / "known_marketplaces.json").write_text(
            json.dumps({"kit": {"source": {"source": "directory",
                                           "path": str(self.market)}}}))

    def test_lists_each_plugin_with_its_skills(self):
        data = self.data()
        self.assertEqual(data["hub"], str(self.home / ".quiver"))
        (plugin,) = data["plugins"]
        self.assertEqual(plugin["name"], "toolkit")
        self.assertEqual(plugin["marketplace"], "kit")
        self.assertEqual(plugin["description"], "Tools for testing")
        self.assertEqual(plugin["version"], "1.2.0")
        self.assertEqual([s["name"] for s in plugin["skills"]], ["hammer", "saw"])
        self.assertEqual(plugin["skills"][0]["description"], "Hits things")

    def test_an_unregistered_marketplace_says_how_to_add_it(self):
        (plugin,) = self.data()["plugins"]
        claude = plugin["install"]["claude"]
        self.assertEqual(claude["state"], "unregistered")
        self.assertIn("claude plugin marketplace add", claude["fix"])
        self.assertEqual(claude["id"], f"claude:unregistered:{self.market}")
        # No ~/.codex here, so codex is not reported at all.
        self.assertNotIn("codex", plugin["install"])

    def test_registered_but_not_installed(self):
        self.register()
        (plugin,) = self.data()["plugins"]
        claude = plugin["install"]["claude"]
        self.assertEqual(claude["state"], "not-installed")
        self.assertEqual(claude["fix"], "claude plugin install toolkit@kit")
        self.assertEqual(claude["id"], "claude:not-installed:toolkit@kit")

    def install_current(self):
        self.register()
        copy = self.home / ".claude" / "plugins" / "cache" / "kit" / "toolkit" / "1.2.0"
        import shutil
        shutil.copytree(self.market / "toolkit", copy)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "plugins": {"toolkit@kit": [{"scope": "user", "version": "1.2.0",
                                         "installPath": str(copy)}]}}))

    def test_installed_and_current(self):
        self.install_current()
        (plugin,) = self.data()["plugins"]
        self.assertEqual(plugin["install"]["claude"], {"state": "installed", "fix": None, "id": None})

    def test_the_id_runs_the_same_fix_through_find_plugins(self):
        self.register()
        (plugin,) = self.data()["plugins"]
        fix_id = plugin["install"]["claude"]["id"]
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        log = self.home / "ran.txt"
        fake = bin_dir / "claude"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
        fake.chmod(0o755)
        import os, subprocess, sys
        env = {"HOME": str(self.home), "NO_COLOR": "1",
               "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        env.update({k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")})
        result = subprocess.run(
            [sys.executable, "-m", "quiver.cli", "find", "plugins", f"--fix={fix_id}", "--json"],
            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().strip(), "plugin install toolkit@kit")

    def test_disabled_in_the_only_harness_is_not_installed(self):
        self.install_current()
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"toolkit@kit": False}}))
        (plugin,) = self.data()["plugins"]
        self.assertEqual(plugin["install"]["claude"],
                         {"state": "disabled", "fix": "claude plugin enable toolkit@kit", "id": None})

    def test_the_listing_reads_every_state(self):
        self.install_current()
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"toolkit@kit": False}}))
        result = run_swe(self.home, "skills", "hub")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("toolkit@kit", result.stdout)
        self.assertIn("2 skills", result.stdout)
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("claude disabled", plain)
        self.assertIn("Tools for testing", result.stdout)

    def test_help_and_stray_args(self):
        result = run_swe(self.home, "skills", "hub", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("swe skills hub", result.stdout)
        result = run_swe(self.home, "skills", "hub", "extra", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("extra", result.stderr)

    def test_a_plugin_without_a_marketplace_description_uses_its_own(self):
        manifest = self.market / ".claude-plugin" / "marketplace.json"
        data = json.loads(manifest.read_text())
        del data["plugins"][0]["description"]
        manifest.write_text(json.dumps(data))
        (self.market / "toolkit" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "toolkit", "version": "1.2.0", "description": "From the plugin"}))
        (plugin,) = self.data()["plugins"]
        self.assertEqual(plugin["description"], "From the plugin")

    def test_an_empty_hub_says_so(self):
        import shutil
        shutil.rmtree(self.market)
        result = run_swe(self.home, "skills", "hub")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No plugins", result.stdout)

    def test_an_empty_hub_is_an_empty_list(self):
        import shutil
        shutil.rmtree(self.market)
        self.assertEqual(self.data()["plugins"], [])


class HarnessJsonTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        write_registry(self.home, REGISTRY)

    def run_json(self, *args):
        result = run_swe(self.home, *args, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return decode(result)["data"]

    def rows(self):
        return {row["name"]: row for row in
                self.run_json("list", "--scope=all")["harnesses"]}

    def test_list_says_whether_the_command_is_installed(self):
        registry = dict(REGISTRY, ghost={"command": "no-such-binary-anywhere-7f3a"})
        write_registry(self.home, registry)
        rows = {row["name"]: row for row in self.run_json("list")["harnesses"]}
        self.assertIs(rows["ghost"]["installed"], False)
        self.assertIsInstance(rows["codex"]["installed"], bool)
        self.assertIsNone(rows["codex"]["archived"])

    def test_star_toggles_and_reports_the_new_state(self):
        self.assertEqual(self.run_json("harness", "star", "codex"),
                         {"name": "codex", "state": "starred"})
        self.assertEqual(self.rows()["codex"]["state"], "starred")
        self.assertEqual(self.run_json("hs", "star", "codex"),
                         {"name": "codex", "state": "active"})
        self.assertEqual(self.rows()["codex"]["state"], "active")

    def test_archive_records_the_reason_and_restores(self):
        data = self.run_json("harness", "archive", "codex", "too slow")
        self.assertEqual(data["name"], "codex")
        self.assertEqual(data["state"], "archived")
        self.assertEqual(data["archived"]["reason"], "too slow")
        row = self.rows()["codex"]
        self.assertEqual(row["state"], "archived")
        self.assertEqual(row["archived"]["reason"], "too slow")

        data = self.run_json("harness", "archive", "codex")
        self.assertEqual(data, {"name": "codex", "state": "active", "archived": None})
        self.assertEqual(self.rows()["codex"]["state"], "active")

    def test_archive_without_a_name_lists_the_archive(self):
        self.run_json("harness", "archive", "codex", "too slow")
        data = self.run_json("harness", "archive")
        self.assertEqual(list(data["archived"]), ["codex"])
        self.assertEqual(data["archived"]["codex"]["reason"], "too slow")

    def test_star_json_needs_exactly_one_name(self):
        for args in ([], ["clear"], ["a", "b"]):
            with self.subTest(args=args):
                result = run_swe(self.home, "harness", "star", *args, "--json")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_unknown_harness_is_refused_with_empty_stdout(self):
        for verb in ("star", "archive"):
            with self.subTest(verb=verb):
                result = run_swe(self.home, "harness", verb, "nope", "--json")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("nope", result.stderr)


if __name__ == "__main__":
    unittest.main()
