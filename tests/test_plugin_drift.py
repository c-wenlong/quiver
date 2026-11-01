"""`plugin_drift` reports where ~/.swe/plugins has not reached a harness.

Every test builds a whole fake HOME: a marketplace under ~/.swe/plugins,
claude's known_marketplaces.json / installed_plugins.json / settings.json and
codex's config.toml plus plugin cache, each an exact copy of the source unless
the test says otherwise. The baseline is therefore drift-free, and each test
breaks one thing and asserts that exactly that thing is reported.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from swe.find.plugin_drift import (
    Finding,
    plugin_drift,
    swe_plugins,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _codex_copy(src: Path, dst: Path) -> None:
    """Copy a plugin the way ``codex plugin add`` does: every symlink dropped.

    Seen with codex-cli 0.162.1: a symlinked manifest folder, a symlinked
    skill folder or file, and a dangling link are all left out of the copy.
    """
    shutil.copytree(src, dst, ignore=lambda d, names: [
        n for n in names if os.path.islink(os.path.join(d, n))])


class _FakeHome:
    """A home with marketplace ``mk`` holding plugin ``tool`` 1.0.0, installed
    and enabled in both claude and codex, every copy identical to the source
    as that harness copies it."""

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.market = self.home / ".swe" / "plugins" / "mk"
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
        # codex reads its own manifest folder, so it has to be a real one;
        # a symlinked .codex-plugin is LinkedManifestTest's subject. Other
        # harnesses' folders can stay links: codex drops them, harmlessly.
        shutil.copytree(self.plugin / ".claude-plugin", self.plugin / ".codex-plugin")
        os.symlink(".claude-plugin", self.plugin / ".cursor-plugin")

        # claude: registered, installed as a copy that keeps the symlink
        self.claude_copy = self.home / ".claude" / "plugins" / "cache" / "mk" / "tool" / "1.0.0"
        shutil.copytree(self.plugin, self.claude_copy, symlinks=True)
        self.known = {"mk": {"source": {"source": "directory", "path": str(self.market)},
                             "installLocation": str(self.market)}}
        self.installed = {"version": 2, "plugins": {"tool@mk": [
            {"scope": "user", "installPath": str(self.claude_copy), "version": "1.0.0"}]}}
        self.settings = {"enabledPlugins": {"tool@mk": True}}
        self._write_claude()

        # codex: registered, installed as a copy with every symlink dropped
        self.codex_copy = self.home / ".codex" / "plugins" / "cache" / "mk" / "tool" / "1.0.0"
        _codex_copy(self.plugin, self.codex_copy)
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

    def link_manifest(self, target: str) -> None:
        """Make the source's .codex-plugin a symlink to ``target`` and
        reinstall into codex the way codex does then."""
        manifest = self.plugin / ".codex-plugin"
        if manifest.is_symlink():
            manifest.unlink()
        else:
            shutil.rmtree(manifest)
        os.symlink(target, manifest)
        self.reinstall_codex("local")
        # what codex-cli 0.162.1 generated for a plugin like this one
        _write(self.codex_copy / ".codex-plugin" / "plugin.json", json.dumps(
            {"name": "tool", "interface": {"developerName": "me"}}, indent=2))

    def reinstall_codex(self, version: str) -> None:
        shutil.rmtree(self.codex_copy.parent)
        self.codex_copy = self.codex_copy.parent / version
        _codex_copy(self.plugin, self.codex_copy)

    def drift(self, **kw):
        return plugin_drift(self.home, **kw)

    def kinds(self, **kw):
        return [(f.harness, f.kind) for f in self.drift(**kw)]


class SwePluginsTest(_FakeHome, unittest.TestCase):
    def test_lists_local_plugins_with_version(self):
        found = swe_plugins(self.home)
        self.assertEqual(len(found), 1)
        sp = found[0]
        self.assertEqual((sp.marketplace, sp.name, sp.version), ("mk", "tool", "1.0.0"))
        self.assertEqual(sp.marketplace_dir, self.market)
        self.assertEqual(sp.path, self.plugin)

    def test_url_sourced_plugin_is_skipped(self):
        names = [sp.name for sp in swe_plugins(self.home)]
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
        self.assertEqual([sp.name for sp in swe_plugins(self.home)], ["tool"])

    def test_no_plugins_dir(self):
        shutil.rmtree(self.home / ".swe")
        self.assertEqual(swe_plugins(self.home), [])
        self.assertEqual(self.drift(), [])

    def test_malformed_marketplace_json(self):
        _write(self.market / ".claude-plugin" / "marketplace.json", "{not json")
        self.assertEqual(swe_plugins(self.home), [])
        (self.market / ".claude-plugin" / "marketplace.json").write_bytes(b"\xff\xfe{")
        self.assertEqual(swe_plugins(self.home), [])
        _write(self.market / ".claude-plugin" / "marketplace.json",
               json.dumps({"name": "mk", "plugins": "nope"}))
        self.assertEqual(swe_plugins(self.home), [])


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

    def test_a_remote_only_marketplace_is_unregistered_too(self):
        # Every plugin comes from git, so swe_plugins finds nothing in it,
        # but the marketplace itself still needs registering.
        remote = self.home / ".swe" / "plugins" / "far"
        _write(remote / ".claude-plugin" / "marketplace.json", json.dumps(
            {"name": "far", "plugins": [{"name": "r", "source": {
                "source": "github", "repo": "o/r"}}]}))
        found = [(f.harness, f.marketplace, f.kind) for f in self.drift()]
        self.assertEqual(found, [("claude", "far", "unregistered"),
                                 ("codex", "far", "unregistered")])

    def test_dir_with_spaces_is_quoted(self):
        spaced = self.home / ".swe" / "plugins" / "my mk"
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
        self.assertEqual([(f.harness, f.kind) for f in found], [("codex", "stale")])
        codex = found[0]
        self.assertEqual(codex.paths, ("agents/helper.md",))
        self.assertEqual(codex.detail, "installed 1.0.0 vs source 1.0.0, 1 file differs")
        self.assertEqual(codex.fix, "codex plugin remove tool@mk && codex plugin add tool@mk")

    def test_claude_reads_a_directory_marketplace_live(self):
        # Claude Code loads a directory-marketplace plugin from its folder; the
        # copy under plugins/cache is a leftover it never reads, and
        # `claude plugin update` reports nothing to update. Comparing against
        # that copy flagged drift no fix could clear.
        _write(self.plugin / "agents" / "helper.md", "HELPER\n")
        _write(self.claude_copy / "leftover.md", "old\n")
        self.assertEqual([f for f in self.drift() if f.harness == "claude"], [])

    def test_version_bump_and_new_file(self):
        _write(self.plugin / ".claude-plugin" / "plugin.json",
               json.dumps({"name": "tool", "version": "1.1.0"}))
        _write(self.plugin / "skills" / "new" / "SKILL.md", "new\n")
        codex = [f for f in self.drift() if f.harness == "codex"][0]
        self.assertEqual(codex.kind, "stale")
        # the symlinked .cursor-plugin is not compared: codex never copies it
        self.assertEqual(codex.detail, "installed 1.0.0 vs source 1.1.0, 2 files differ")
        self.assertEqual(codex.paths, (".claude-plugin/plugin.json",
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

    def test_symlinks_codex_drops_are_not_drift(self):
        # codex copies no symlink at all, so a fresh install of a source
        # holding these lacks them, and the source must not count them
        # either: a reinstall could never make the copy match.
        os.symlink("nowhere", self.plugin / "agents" / "ghost.md")
        os.symlink("../agents", self.plugin / "skills" / "linked")
        os.symlink("../../agents/helper.md", self.plugin / "skills" / "go" / "helper.md")
        self.assertEqual(self.drift(), [])

    def test_symlink_in_the_copy_is_not_followed(self):
        # The source's file is compared with what codex wrote, not with a
        # link in the cache that happens to point back at the source.
        _write(self.plugin / "agents" / "helper.md", "HELPER\n")
        (self.codex_copy / "agents" / "helper.md").unlink()
        os.symlink(self.plugin / "agents" / "helper.md",
                   self.codex_copy / "agents" / "helper.md")
        found = self.drift()
        self.assertEqual([(f.harness, f.kind, f.paths) for f in found],
                         [("codex", "stale", ("agents/helper.md",))])


class LinkedManifestTest(_FakeHome, unittest.TestCase):
    """A symlinked ``.codex-plugin``: codex skips it when it copies the plugin
    and writes a manifest of its own from the marketplace entry, with no
    version, into a copy named ``local``. Reinstalling changes nothing, so
    the finding must point at the link, not at the copy."""

    def setUp(self):
        super().setUp()
        self.link_manifest(".claude-plugin")

    def test_reported_instead_of_stale(self):
        [finding] = self.drift()
        self.assertEqual((finding.harness, finding.kind, finding.ref),
                         ("codex", "linked-manifest", "tool@mk"))
        self.assertEqual(finding.id, "codex:linked-manifest:tool@mk")
        self.assertEqual(finding.detail,
                         ".codex-plugin is a symlink to .claude-plugin; codex doesn't "
                         "follow symlinks, so it installs a generated manifest "
                         "without the version")
        self.assertEqual(finding.paths, ())

    def test_fix_replaces_the_link_with_a_copy_of_its_target(self):
        [finding] = self.drift()
        real = Path(os.path.realpath(self.plugin))
        link, target = str(real / ".codex-plugin"), str(real / ".claude-plugin")
        staged = link + ".swe-tmp"
        # copy to a new path first, so a copy that fails leaves the link and
        # its finding; the link is the only thing removed
        self.assertEqual(finding.argv, (("cp", "-R", target, staged),
                                        ("rm", link),
                                        ("mv", staged, link)))
        self.assertEqual(finding.fix, f"cp -R {target} {staged} && rm {link} && mv {staged} {link}")

    def test_a_failed_copy_keeps_the_link_and_the_finding(self):
        from swe.find.plugin_drift import FixError, apply_fix

        def fail_on_cp(argv, **kwargs):
            if argv[0] == "cp":
                return subprocess.CompletedProcess(argv, 1, "", "cp: Permission denied\n")
            return subprocess.run(argv, **kwargs)

        with self.assertRaisesRegex(FixError, "Permission denied"):
            apply_fix(self.home, "codex:linked-manifest:tool@mk", run=fail_on_cp)
        self.assertTrue((self.plugin / ".codex-plugin").is_symlink())
        [finding] = self.drift()
        self.assertEqual(finding.kind, "linked-manifest")
        self.assertTrue(finding.argv)

    def test_anything_at_the_staging_path_makes_it_a_hand_fix(self):
        # cp into an existing folder writes through any link in it, and
        # clearing the path first could delete a real folder, so neither
        # happens: the fix names what is in the way and runs nothing.
        from swe.find.plugin_drift import FixError, apply_fix

        staged = self.plugin / ".codex-plugin.swe-tmp"
        outside = self.home / "outside"
        _write(outside / "plugin.json", "keep me\n")

        def leftover():
            _write(staged / "plugin.json", "half-copied\n")

        def link_out():
            os.symlink(outside, staged)

        def link_to_it():
            # .codex-plugin -> .codex-plugin.swe-tmp, a real folder
            _write(staged / "plugin.json", "keep me\n")
            self.link_manifest(staged.name)

        for setup in (leftover, link_out, link_to_it):
            setup()
            before = sorted((str(q), q.lstat().st_mtime_ns) for q in self.home.rglob("*"))
            # a real folder there is also a stale path; that one is honest
            [finding] = [f for f in self.drift() if f.kind == "linked-manifest"]
            self.assertEqual(finding.argv, (), setup.__name__)
            in_the_way = os.path.join(os.path.realpath(self.plugin), staged.name)
            self.assertTrue(finding.fix.endswith(f"{in_the_way} is in the way"))
            with self.assertRaisesRegex(FixError, "nothing to run"):
                apply_fix(self.home, "codex:linked-manifest:tool@mk")
            after = sorted((str(q), q.lstat().st_mtime_ns) for q in self.home.rglob("*"))
            self.assertEqual(after, before, setup.__name__)
            self.assertEqual((outside / "plugin.json").read_text(), "keep me\n")
            if staged.is_symlink():
                staged.unlink()
            else:
                shutil.rmtree(staged)

    def test_target_outside_the_plugin_is_resolved(self):
        shared = self.market / "shared-manifest"
        shutil.copytree(self.plugin / ".claude-plugin", shared)
        self.link_manifest("../shared-manifest")
        [finding] = self.drift()
        self.assertEqual(finding.argv[0][2], os.path.realpath(shared))
        self.assertIn("a symlink to ../shared-manifest;", finding.detail)

    def test_no_command_for_a_target_outside_the_marketplace_or_around_the_link(self):
        # Copying a folder from anywhere on the machine into the plugin, or a
        # folder into itself, is not something a click should do.
        elsewhere = self.home / "elsewhere"
        shutil.copytree(self.plugin / ".claude-plugin", elsewhere)
        for target in (str(elsewhere), "."):
            self.link_manifest(target)
            [finding] = self.drift()
            self.assertEqual((finding.kind, finding.argv), ("linked-manifest", ()), target)

    def test_paths_with_spaces_and_quotes_stay_one_word(self):
        import shlex

        odd = self.market / "it's my tool"
        self.plugin.rename(odd)
        self.plugin = odd
        self.manifest["plugins"][0]["source"] = "./it's my tool"
        self._write_manifest()
        [finding] = self.drift()
        self.assertEqual([shlex.split(part) for part in finding.fix.split(" && ")],
                         [list(argv) for argv in finding.argv])
        self.assertEqual(finding.argv[1][0], "rm")
        self.assertTrue(finding.argv[1][1].endswith("/it's my tool/.codex-plugin"))

    def test_dangling_link_has_no_command(self):
        self.link_manifest("nowhere")
        [finding] = self.drift()
        self.assertEqual(finding.kind, "linked-manifest")
        self.assertEqual(finding.argv, ())
        self.assertIn("with a folder holding plugin.json", finding.fix)

    def test_generated_manifest_is_not_also_stale(self):
        # One finding per cause: other drift is still stale, but the
        # manifest codex wrote itself is not one of its paths.
        _write(self.plugin / "agents" / "helper.md", "HELPER\n")
        found = self.drift()
        self.assertEqual([(f.kind, f.paths) for f in found],
                         [("linked-manifest", ()), ("stale", ("agents/helper.md",))])
        self.assertEqual(found[1].detail, "installed local vs source 1.0.0, 1 file differs")

    def test_not_installed_comes_first(self):
        shutil.rmtree(self.codex_copy.parent)
        self.assertEqual(self.kinds(), [("codex", "not-installed")])

    def test_running_the_fix_then_reinstalling_clears_it(self):
        from swe.find.plugin_drift import apply_fix

        apply_fix(self.home, "codex:linked-manifest:tool@mk")
        manifest = self.plugin / ".codex-plugin"
        self.assertFalse(manifest.is_symlink())
        self.assertEqual((manifest / "plugin.json").read_bytes(),
                         (self.plugin / ".claude-plugin" / "plugin.json").read_bytes())
        # the source is right now, and the copy codex made from the link is
        # an ordinary stale copy whose fix is the reinstall
        [stale] = self.drift()
        self.assertEqual((stale.kind, stale.paths), ("stale", (".codex-plugin/plugin.json",)))
        self.reinstall_codex("1.0.0")
        self.assertEqual(self.drift(), [])


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
