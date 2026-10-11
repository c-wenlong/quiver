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

    def test_plugin_cache_skills_belong_to_their_harness(self):
        cache = self.home / ".claude" / "plugins" / "cache" / "mk" / "tool" / "1.0" / "skills"
        write_skill(cache, "from-plugin")
        skills = {s["name"]: s for s in self.data()["skills"]}
        self.assertEqual(skills["from-plugin"]["source"], "plugin")
        self.assertEqual(skills["from-plugin"]["harnesses"], ["claude"])

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
        # No ~/.codex here, so codex is not reported at all.
        self.assertNotIn("codex", plugin["install"])

    def test_registered_but_not_installed(self):
        self.register()
        (plugin,) = self.data()["plugins"]
        claude = plugin["install"]["claude"]
        self.assertEqual(claude["state"], "not-installed")
        self.assertEqual(claude["fix"], "claude plugin install toolkit@kit")

    def test_installed_and_current(self):
        self.register()
        copy = self.home / ".claude" / "plugins" / "cache" / "kit" / "toolkit" / "1.2.0"
        import shutil
        shutil.copytree(self.market / "toolkit", copy)
        (self.home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "plugins": {"toolkit@kit": [{"scope": "user", "version": "1.2.0",
                                         "installPath": str(copy)}]}}))
        (plugin,) = self.data()["plugins"]
        self.assertEqual(plugin["install"]["claude"], {"state": "installed", "fix": None})

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

    def test_unknown_harness_is_refused_with_empty_stdout(self):
        for verb in ("star", "archive"):
            with self.subTest(verb=verb):
                result = run_swe(self.home, "harness", verb, "nope", "--json")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("nope", result.stderr)


if __name__ == "__main__":
    unittest.main()
