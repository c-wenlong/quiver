"""`plugin_drift` reports where ~/.quiver/plugins has not reached a harness.

Every test builds a whole fake HOME: a marketplace under ~/.quiver/plugins,
claude's known_marketplaces.json / installed_plugins.json / settings.json and
codex's config.toml plus plugin cache, each an exact copy of the source unless
the test says otherwise. The baseline is therefore drift-free, and each test
breaks one thing and asserts that exactly that thing is reported.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from quiver.find.plugin_drift import (
    Finding,
    plugin_drift,
    quiver_plugins,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _FakeHome:
    """A home with marketplace ``mk`` holding plugin ``tool`` 1.0.0, installed
    and enabled in both claude and codex, every copy identical to the source."""

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.market = self.home / ".quiver" / "plugins" / "mk"
        self.plugin = self.market / "tool"
        self.manifest = {
            "name": "mk",
            "plugins": [
                {"name": "tool", "source": "./tool"},
                # fetched from git at install time: nothing local to compare
                {"name": "remote", "source": {"source": "git-subdir",
                                              "url": "https://example.com/r.git",
                                              "path": "plugins/remote"}},
            ],
        }
        self._write_manifest()
        _write(self.plugin / ".claude-plugin" / "plugin.json",
               json.dumps({"name": "tool", "version": "1.0.0"}))
        _write(self.plugin / "skills" / "go" / "SKILL.md", "---\nname: go\n---\nbody\n")
        _write(self.plugin / "agents" / "helper.md", "helper\n")
        os.symlink(".claude-plugin", self.plugin / ".codex-plugin")

        # claude: registered, installed as a copy that keeps the symlink
        self.claude_copy = self.home / ".claude" / "plugins" / "cache" / "mk" / "tool" / "1.0.0"
        shutil.copytree(self.plugin, self.claude_copy, symlinks=True)
        self.known = {"mk": {"source": {"source": "directory", "path": str(self.market)},
                             "installLocation": str(self.market)}}
        self.installed = {"version": 2, "plugins": {"tool@mk": [
            {"scope": "user", "installPath": str(self.claude_copy), "version": "1.0.0"}]}}
        self.settings = {"enabledPlugins": {"tool@mk": True}}
        self._write_claude()

        # codex: registered, installed as a copy with the symlink dereferenced
        self.codex_copy = self.home / ".codex" / "plugins" / "cache" / "mk" / "tool" / "1.0.0"
        shutil.copytree(self.plugin, self.codex_copy, symlinks=False)
        self.codex_enabled = "true"
        self._write_codex()

    def _write_manifest(self):
        _write(self.market / ".claude-plugin" / "marketplace.json", json.dumps(self.manifest))

    def _write_claude(self):
        base = self.home / ".claude"
        _write(base / "plugins" / "known_marketplaces.json", json.dumps(self.known))
        _write(base / "plugins" / "installed_plugins.json", json.dumps(self.installed))
        _write(base / "settings.json", json.dumps(self.settings))

    def _write_codex(self, register=True):
        body = ""
        if register:
            body += f'[marketplaces.mk]\nsource_type = "local"\nsource = "{self.market}"\n\n'
        body += '[marketplaces.other]\nsource_type = "git"\nsource = "https://example.com/o.git"\n\n'
        body += f'[plugins."tool@mk"]\nenabled = {self.codex_enabled}\n'
        _write(self.home / ".codex" / "config.toml", body)

    def drift(self, **kw):
        return plugin_drift(self.home, **kw)

    def kinds(self, **kw):
        return [(f.harness, f.kind) for f in self.drift(**kw)]


class QuiverPluginsTest(_FakeHome, unittest.TestCase):
    def test_lists_local_plugins_with_version(self):
        found = quiver_plugins(self.home)
        self.assertEqual(len(found), 1)
        sp = found[0]
        self.assertEqual((sp.marketplace, sp.name, sp.version), ("mk", "tool", "1.0.0"))
        self.assertEqual(sp.marketplace_dir, self.market)
        self.assertEqual(sp.path, self.plugin)

    def test_url_sourced_plugin_is_skipped(self):
        names = [sp.name for sp in quiver_plugins(self.home)]
        self.assertNotIn("remote", names)

    def test_escaping_and_absolute_sources_are_skipped(self):
        outside = self.home / "elsewhere"
        outside.mkdir()
        self.manifest["plugins"] += [
            {"name": "up", "source": "../../elsewhere"},
            {"name": "abs", "source": str(outside)},
            {"name": "url", "source": "https://example.com/x.git"},
        ]
        self._write_manifest()
        self.assertEqual([sp.name for sp in quiver_plugins(self.home)], ["tool"])

    def test_no_plugins_dir(self):
        shutil.rmtree(self.home / ".quiver")
        self.assertEqual(quiver_plugins(self.home), [])
        self.assertEqual(self.drift(), [])

    def test_malformed_marketplace_json(self):
        _write(self.market / ".claude-plugin" / "marketplace.json", "{not json")
        self.assertEqual(quiver_plugins(self.home), [])
        (self.market / ".claude-plugin" / "marketplace.json").write_bytes(b"\xff\xfe{")
        self.assertEqual(quiver_plugins(self.home), [])
        _write(self.market / ".claude-plugin" / "marketplace.json",
               json.dumps({"name": "mk", "plugins": "nope"}))
        self.assertEqual(quiver_plugins(self.home), [])


class NoDriftTest(_FakeHome, unittest.TestCase):
    def test_identical_copies_report_nothing(self):
        self.assertEqual(self.drift(), [])

    def test_litter_in_copies_is_ignored(self):
        for copy in (self.claude_copy, self.codex_copy):
            _write(copy / ".DS_Store", "finder")
            _write(copy / "skills" / "__pycache__" / "x.pyc", "bytecode")
            _write(copy / ".orphaned_at", "123")
        (self.claude_copy / ".in_use").mkdir()
        _write(self.claude_copy / ".in_use" / "pid", "42")
        _write(self.plugin / "skills" / ".DS_Store", "finder on the source too")
        self.assertEqual(self.drift(), [])

    def test_never_writes(self):
        def tree():
            return sorted((str(p), p.lstat().st_mtime_ns) for p in self.home.rglob("*"))
        _write(self.plugin / "agents" / "helper.md", "changed\n")
        before = tree()
        self.drift()
        self.assertEqual(tree(), before)


class UnregisteredTest(_FakeHome, unittest.TestCase):
    def test_claude(self):
        self.known = {}
        self._write_claude()
        found = self.drift()
        self.assertEqual([(f.harness, f.kind, f.plugin) for f in found],
                         [("claude", "unregistered", "")])
        self.assertEqual(found[0].fix, f"claude plugin marketplace add {self.market}")

    def test_codex(self):
        self._write_codex(register=False)
        found = self.drift()
        self.assertEqual([(f.harness, f.kind) for f in found], [("codex", "unregistered")])
        self.assertEqual(found[0].fix, f"codex plugin marketplace add {self.market}")

    def test_one_finding_per_marketplace(self):
        _write(self.market / "second" / ".claude-plugin" / "plugin.json", "{}")
        self.manifest["plugins"].append({"name": "second", "source": "./second"})
        self._write_manifest()
        self.known = {}
        self._write_claude()
        found = self.drift(harnesses=("claude",))
        self.assertEqual([f.kind for f in found], ["unregistered"])

    def test_dir_with_spaces_is_quoted(self):
        spaced = self.home / ".quiver" / "plugins" / "my mk"
        shutil.copytree(self.market, spaced, symlinks=True)
        found = [f for f in self.drift(harnesses=("claude",)) if f.kind == "unregistered"]
        self.assertEqual(found[0].fix, f"claude plugin marketplace add '{spaced}'")

    def test_extra_known_marketplaces_counts_as_registered(self):
        self.settings["extraKnownMarketplaces"] = self.known
        self.known = {}
        self._write_claude()
        self.assertEqual(self.drift(), [])

    def test_registered_through_a_symlinked_path(self):
        alias = self.home / "alias"
        os.symlink(self.market, alias)
        self.known["mk"]["source"]["path"] = str(alias)
        self._write_claude()
        self.assertEqual(self.drift(), [])

    def test_missing_harness_dir_reports_nothing_for_it(self):
        shutil.rmtree(self.home / ".codex")
        self.assertEqual(self.drift(), [])


class NotInstalledTest(_FakeHome, unittest.TestCase):
    def test_claude(self):
        self.installed["plugins"] = {}
        self._write_claude()
        found = self.drift()
        self.assertEqual([(f.harness, f.kind) for f in found], [("claude", "not-installed")])
        self.assertEqual(found[0].fix, "claude plugin install tool@mk")

    def test_claude_record_pointing_at_missing_copy(self):
        shutil.rmtree(self.claude_copy)
        self.assertEqual(self.kinds(), [("claude", "not-installed")])

    def test_codex(self):
        shutil.rmtree(self.codex_copy.parent)
        found = self.drift()
        self.assertEqual([(f.harness, f.kind) for f in found], [("codex", "not-installed")])
        self.assertEqual(found[0].fix, "codex plugin add tool@mk")


class StaleTest(_FakeHome, unittest.TestCase):
    def test_changed_bytes_same_size(self):
        _write(self.plugin / "agents" / "helper.md", "HELPER\n")
        found = self.drift()
        self.assertEqual([(f.harness, f.kind) for f in found],
                         [("claude", "stale"), ("codex", "stale")])
        claude, codex = found
        self.assertEqual(claude.paths, ("agents/helper.md",))
        self.assertEqual(claude.detail, "installed 1.0.0 vs source 1.0.0, 1 file differs")
        self.assertEqual(claude.fix, "claude plugin update tool@mk")
        self.assertEqual(codex.fix, "codex plugin remove tool@mk && codex plugin add tool@mk")

    def test_version_bump_and_new_file(self):
        _write(self.plugin / ".claude-plugin" / "plugin.json",
               json.dumps({"name": "tool", "version": "1.1.0"}))
        _write(self.plugin / "skills" / "new" / "SKILL.md", "new\n")
        found = [f for f in self.drift() if f.harness == "claude"]
        self.assertEqual(found[0].kind, "stale")
        # the symlinked .codex-plugin reads as its target, so it differs too
        self.assertEqual(found[0].detail, "installed 1.0.0 vs source 1.1.0, 3 files differ")
        codex = [f for f in self.drift() if f.harness == "codex"][0]
        self.assertEqual(codex.paths, (".claude-plugin/plugin.json",
                                       ".codex-plugin/plugin.json",
                                       "skills/new/SKILL.md"))

    def test_file_only_in_copy(self):
        _write(self.codex_copy / "leftover.md", "gone from source\n")
        found = self.drift()
        self.assertEqual([(f.harness, f.kind, f.paths) for f in found],
                         [("codex", "stale", ("leftover.md",))])

    def test_codex_multiple_versions_picks_newest_by_mtime(self):
        old = self.codex_copy.parent / "0.9.0"
        shutil.copytree(self.codex_copy, old)
        _write(old / "agents" / "helper.md", "old\n")
        past = time.time() - 3600
        os.utime(old, (past, past))
        now = time.time()
        os.utime(self.codex_copy, (now, now))
        self.assertEqual(self.drift(), [])

        # now make the stale one the most recent write
        os.utime(old, (now + 60, now + 60))
        found = self.drift()
        self.assertEqual([(f.harness, f.kind) for f in found], [("codex", "stale")])
        self.assertIn("installed 0.9.0 vs source 1.0.0", found[0].detail)

    def test_symlink_loop_does_not_hang(self):
        os.symlink("..", self.plugin / "skills" / "loop")
        os.symlink("..", self.claude_copy / "skills" / "loop")
        os.symlink("..", self.codex_copy / "skills" / "loop")
        self.assertEqual(self.drift(), [])

    def test_dangling_symlink(self):
        os.symlink("nowhere", self.plugin / "agents" / "ghost.md")
        found = self.drift()
        self.assertEqual([(f.kind, f.paths) for f in found],
                         [("stale", ("agents/ghost.md",)), ("stale", ("agents/ghost.md",))])


class DisabledHereTest(_FakeHome, unittest.TestCase):
    def test_disabled_in_codex_enabled_in_claude(self):
        self.codex_enabled = "false"
        self._write_codex()
        found = self.drift()
        self.assertEqual(found, [Finding(
            "codex", "mk", "tool", "disabled-here",
            "disabled in codex, enabled in claude",
            'set [plugins."tool@mk"] enabled = true in ~/.codex/config.toml')])

    def test_disabled_in_claude_enabled_in_codex(self):
        self.settings["enabledPlugins"]["tool@mk"] = False
        self._write_claude()
        found = self.drift()
        self.assertEqual([(f.harness, f.kind, f.fix) for f in found],
                         [("claude", "disabled-here", "claude plugin enable tool@mk")])

    def test_compares_against_harness_not_asked_for(self):
        self.codex_enabled = "false"
        self._write_codex()
        self.assertEqual(self.kinds(harnesses=("codex",)), [("codex", "disabled-here")])
        self.assertEqual(self.kinds(harnesses=("claude",)), [])

    def test_disabled_everywhere_is_not_drift(self):
        self.codex_enabled = "false"
        self._write_codex()
        self.settings["enabledPlugins"]["tool@mk"] = False
        self._write_claude()
        self.assertEqual(self.drift(), [])

    def test_enabled_elsewhere_but_not_installed_there(self):
        self.codex_enabled = "false"
        self._write_codex()
        shutil.rmtree(self.claude_copy)
        self.assertEqual(self.kinds(), [("claude", "not-installed")])

    def test_stale_and_disabled_both_reported(self):
        self.codex_enabled = "false"
        self._write_codex()
        _write(self.codex_copy / "agents" / "helper.md", "HELPER\n")
        self.assertEqual(self.kinds(), [("codex", "stale"), ("codex", "disabled-here")])


class FixQuotingTest(_FakeHome, unittest.TestCase):
    """Names come from a marketplace.json anyone can write, and every fix is
    meant to be pasted into a shell, so a hostile name must stay one word."""

    def test_shell_metacharacters_in_a_name_are_quoted(self):
        import shlex

        self.manifest["plugins"][0]["name"] = "tool; touch pwned"
        self._write_manifest()
        [claude, codex] = sorted(
            (f for f in self.drift() if f.kind == "not-installed"), key=lambda f: f.harness)
        self.assertEqual(shlex.split(claude.fix),
                         ["claude", "plugin", "install", "tool; touch pwned@mk"])
        self.assertEqual(shlex.split(codex.fix),
                         ["codex", "plugin", "add", "tool; touch pwned@mk"])

    def test_codex_enable_key_is_valid_toml_for_any_name(self):
        import tomllib

        self.manifest["plugins"][0]["name"] = 'to"ol'
        self._write_manifest()
        ref = 'to"ol@mk'
        self.settings = {"enabledPlugins": {ref: True}}
        self.installed["plugins"] = {ref: self.installed["plugins"]["tool@mk"]}
        self._write_claude()
        shutil.copytree(self.codex_copy, self.codex_copy.parent.parent / 'to"ol' / "1.0.0")
        _write(self.home / ".codex" / "config.toml",
               f'[marketplaces.mk]\nsource_type = "local"\nsource = "{self.market}"\n\n'
               f'[plugins."to\\"ol@mk"]\nenabled = false\n')
        [finding] = [f for f in self.drift() if f.kind == "disabled-here"]
        edit = finding.fix.removeprefix("set ").removesuffix(" in ~/.codex/config.toml")
        self.assertEqual(tomllib.loads(edit.replace("] enabled", "]\nenabled")),
                         {"plugins": {ref: {"enabled": True}}})


class MalformedRecordsTest(_FakeHome, unittest.TestCase):
    def test_malformed_known_marketplaces(self):
        _write(self.home / ".claude" / "plugins" / "known_marketplaces.json", "{oops")
        self._break_codex_copy()
        self.assertEqual(self.kinds(), [("codex", "stale")])

    def test_malformed_installed_plugins(self):
        _write(self.home / ".claude" / "plugins" / "installed_plugins.json", "[1, 2]")
        self.assertEqual(self.drift(), [])
        _write(self.home / ".claude" / "plugins" / "installed_plugins.json",
               json.dumps({"plugins": ["not", "a", "dict"]}))
        self.assertEqual(self.drift(), [])

    def test_malformed_install_entries(self):
        self.installed["plugins"]["tool@mk"] = [None, {"scope": "user"}, "x"]
        self._write_claude()
        self.assertEqual(self.kinds(), [("claude", "not-installed")])

    def test_malformed_settings_only_costs_enabled_flags(self):
        _write(self.home / ".claude" / "settings.json", "\xff not json")
        self.codex_enabled = "false"
        self._write_codex()
        self.assertEqual(self.drift(), [])

    def test_malformed_codex_toml(self):
        _write(self.home / ".codex" / "config.toml", "[marketplaces.mk\nsource = ")
        self._break_codex_copy()
        self.assertEqual(self.drift(), [])
        (self.home / ".codex" / "config.toml").write_bytes(b"\xff\xfe[")
        self.assertEqual(self.drift(), [])

    def test_codex_tables_of_wrong_shape(self):
        _write(self.home / ".codex" / "config.toml",
               'marketplaces = "nope"\nplugins = 3\n')
        self.assertEqual(self.kinds(), [("codex", "unregistered")])

    def test_record_that_is_a_directory(self):
        path = self.home / ".claude" / "plugins" / "installed_plugins.json"
        path.unlink()
        path.mkdir()
        self.assertEqual(self.drift(harnesses=("claude",)), [])

    def test_unknown_harness_name_is_ignored(self):
        self.assertEqual(self.drift(harnesses=("gemini",)), [])

    def _break_codex_copy(self):
        _write(self.codex_copy / "agents" / "helper.md", "HELPER\n")


if __name__ == "__main__":
    unittest.main()
