"""Plugin drift and broken links folded into `swe doctor`, with their fixes."""

import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

from swe.harness import doctor_fix
from swe.harness.doctor import plugin_findings
from swe.harness.doctor_fix import FixRefused
from swe.harness.drift import check_dangling_symlinks

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from _tempdir import temp_dir
    from test_json_output import decode, write_registry
    from test_plugin_drift import _FakeHome
    from test_plugin_fix import run_swe
except ImportError:  # `python -m unittest tests.test_doctor_fix_folds`
    from tests._tempdir import temp_dir
    from tests.test_json_output import decode, write_registry
    from tests.test_plugin_drift import _FakeHome
    from tests.test_plugin_fix import run_swe

# A stand-in claude CLI: `plugin marketplace add <dir>` registers the
# folder in known_marketplaces.json the way the real one does; anything
# else succeeds and does nothing.
FAKE_CLAUDE = """#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("2.0.0 (Claude Code)")
elif args[:2] == ["plugin", "install"]:
    plugin, market = args[2].split("@")
    known = json.loads((Path(os.environ["HOME"]) / ".claude" / "plugins" / "known_marketplaces.json").read_text())
    folder = Path(known[market]["installLocation"]) / plugin
    record = Path(os.environ["HOME"]) / ".claude" / "plugins" / "installed_plugins.json"
    data = json.loads(record.read_text()) if record.exists() else {{"version": 2, "plugins": {{}}}}
    data["plugins"][args[2]] = [{{"scope": "user", "installPath": str(folder), "version": "1.0.0"}}]
    record.write_text(json.dumps(data))
elif args[:3] == ["plugin", "marketplace", "add"]:
    folder = Path(args[3])
    name = json.loads((folder / ".claude-plugin" / "marketplace.json").read_text())["name"]
    known = Path(os.environ["HOME"]) / ".claude" / "plugins" / "known_marketplaces.json"
    known.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(known.read_text()) if known.exists() else {{}}
    data[name] = {{"source": {{"source": "directory", "path": str(folder)}},
                  "installLocation": str(folder)}}
    known.write_text(json.dumps(data))
"""


def fake_bin(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE.format(python=sys.executable))
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    return bin_dir


class PluginFindingsTest(_FakeHome, unittest.TestCase):
    def which(self, present):
        return mock.patch("shutil.which",
                          side_effect=lambda t: f"/bin/{t}" if t in present else None)

    def test_an_unregistered_marketplace_gets_a_batch_fix(self):
        self.known = {}
        self._write_claude()
        with self.which({"claude", "codex"}):
            (found,) = [f for f in plugin_findings(self.home) if f.harness == "claude"]
        self.assertEqual((found.check, found.action), ("plugins.unregistered", "plugin-fix"))
        self.assertEqual(found.remedies[0]["id"], f"claude:unregistered:{self.market}")
        self.assertEqual(doctor_fix.batch_action(found.to_json()), "plugin-fix")

    def test_no_cli_on_path_means_no_run(self):
        self.known = {}
        self._write_claude()
        with self.which(set()):
            (found,) = [f for f in plugin_findings(self.home) if f.harness == "claude"]
        self.assertIsNone(found.action)
        self.assertEqual(found.remedies[0]["kind"], "command")

    def test_a_stale_copy_waits_for_a_click(self):
        (self.codex_copy / "agents" / "helper.md").write_text("changed\n")
        with self.which({"codex"}):
            (found,) = plugin_findings(self.home)
        self.assertEqual((found.check, found.action), ("plugins.stale", "plugin-reinstall"))
        self.assertIsNone(doctor_fix.batch_action(found.to_json()))

    def test_a_disabled_plugin_keeps_its_command_only(self):
        self.codex_enabled = "false"
        self._write_codex()
        with self.which({"codex", "claude"}):
            (found,) = plugin_findings(self.home)
        self.assertEqual(found.check, "plugins.disabled-here")
        self.assertIsNone(found.action)

    def test_a_broken_drift_read_is_one_warning(self):
        with mock.patch("swe.find.plugin_drift.plugin_drift", side_effect=OSError("boom")):
            (found,) = plugin_findings(self.home)
        self.assertEqual(found.check, "plugins.unchecked")

    def test_the_runner_reruns_drift_by_id(self):
        with mock.patch("swe.find.plugin_drift.apply_fix") as apply:
            error = doctor_fix.RUNNERS["plugin-fix"](
                self.home, {}, {"id": "claude:unregistered:/x"})
        apply.assert_called_once_with(self.home, "claude:unregistered:/x")
        self.assertIsNone(error)
        with self.assertRaises(FixRefused):
            doctor_fix.RUNNERS["plugin-fix"](self.home, {}, {})


class DanglingLinkTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.hub = self.home / ".swe"
        self.hub.mkdir()
        self.link = self.hub / "old"
        self.link.symlink_to(self.home / "nowhere")

    def run_fix(self, path):
        return doctor_fix.RUNNERS["dangling-link"](self.home, {}, {"path": str(path)})

    def test_only_hub_links_get_a_run(self):
        repo = temp_dir(self)
        (repo / "stray").symlink_to(repo / "gone")
        found = check_dangling_symlinks([repo, self.hub], fixable=[self.hub])
        self.assertEqual([f.action for f in found], [None, None])
        self.assertIsNone(found[0].remedies)
        actions = [r for r in found[1].remedies if r["kind"] == "action"]
        self.assertEqual(actions[0]["path"], str(self.link))
        self.assertIn("dangling-link", doctor_fix.CLICK)

    def test_backs_up_then_removes(self):
        self.assertIsNone(self.run_fix(self.link))
        self.assertFalse(self.link.is_symlink())
        (backup,) = (self.hub / "backups").iterdir()
        self.assertTrue(backup.is_symlink())
        self.assertEqual(os.readlink(backup), str(self.home / "nowhere"))

    def test_a_link_that_works_again_is_left(self):
        (self.home / "nowhere").write_text("back\n")
        with self.assertRaisesRegex(FixRefused, "points at something"):
            self.run_fix(self.link)
        self.assertTrue(self.link.is_symlink())

    def test_a_real_file_or_a_path_elsewhere_is_refused(self):
        real = self.hub / "real"
        real.write_text("keep\n")
        elsewhere = self.home / "outside"
        elsewhere.symlink_to(self.home / "gone")
        deep = self.hub / "sub"
        deep.mkdir()
        (deep / "x").symlink_to(self.home / "gone")
        for path in (real, elsewhere, deep / "x", self.hub / ".." / "outside"):
            with self.assertRaises(FixRefused):
                self.run_fix(path)
        self.assertTrue(real.is_file() and elsewhere.is_symlink() and (deep / "x").is_symlink())


class AcceptanceTest(unittest.TestCase):
    """#529's acceptance: no shared AGENTS.md, an unlinked harness and a
    plugin marketplace claude has not registered. One --fix leaves no
    fixable finding and exits 0 with only manual ones left."""

    def test_fix_clears_every_fixable_finding(self):
        home = temp_dir(self)
        market = home / ".swe" / "plugins" / "mk"
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "mk", "plugins": [{"name": "tool", "source": "./tool"}]}))
        (market / "tool" / ".claude-plugin").mkdir(parents=True)
        (market / "tool" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "tool", "version": "1.0.0"}))
        (home / ".claude").mkdir()
        # Only the fake claude and the system dirs: no real harness CLI runs.
        path = f"{fake_bin(temp_dir(self))}:/usr/bin:/bin"

        before = decode(run_swe(home, "doctor", "--json", path=path))["data"]
        self.assertIn("setup.agents-md", [f["check"] for f in before["findings"]])

        done = run_swe(home, "doctor", "--fix", "--json", path=path)
        data = decode(done)["data"]
        actions = [s["action"] for s in data["ran"]]
        self.assertEqual(actions[0], "setup")
        # Registering the folder shows its uninstalled plugin; the next
        # pass of the same run installs it.
        self.assertEqual(actions.count("plugin-fix"), 2, data["ran"])
        self.assertTrue(all(s["error"] is None for s in data["ran"]), data["ran"])
        self.assertEqual(doctor_fix.fixable_left(data), [])
        self.assertNotIn("plugins.unregistered",
                         [f["check"] for f in data["still_failing"]])
        self.assertEqual(done.returncode, 0, done.stderr)
        known = json.loads((home / ".claude" / "plugins" / "known_marketplaces.json").read_text())
        self.assertIn("mk", known)
        installed = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text())
        self.assertIn("tool@mk", installed["plugins"])
        self.assertEqual([f for f in data["still_failing"] if f["check"].startswith("plugins.")], [])

    def test_an_archived_harness_gets_no_plugin_findings(self):
        home = temp_dir(self)
        market = home / ".swe" / "plugins" / "mk"
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "mk", "plugins": []}))
        (home / ".swe" / "AGENTS.md").write_text("# shared\n")
        (home / ".claude").mkdir()
        write_registry(home, {"claude": {"command": "claude", "state": "archived"}})
        # Only the fake claude and the system dirs: no real harness CLI runs.
        path = f"{fake_bin(temp_dir(self))}:/usr/bin:/bin"
        data = decode(run_swe(home, "doctor", "--json", path=path))["data"]
        self.assertEqual([f for f in data["findings"] if f["check"].startswith("plugins.")], [])


class WebRunTest(unittest.TestCase):
    """The web Run for a finding's own plugin action finds its remedy."""

    def test_a_run_without_an_action_carries_the_remedy(self):
        from types import SimpleNamespace

        from swe.harness.models import DoctorFinding, DoctorReport
        from swe.webapp.pages import doctor as page

        remedy = {"kind": "action", "action": "plugin-fix", "id": "claude:unregistered:/m"}
        report = DoctorReport([DoctorFinding("plugins.unregistered", "warning", "m", "fix",
                                             harness="claude", action="plugin-fix",
                                             remedies=(remedy,))])
        home = temp_dir(self)
        ctx = SimpleNamespace(home=home, machine={"name": "box"})
        with mock.patch("swe.harness.commands.doctor_report", return_value=report), \
                mock.patch("swe.webapp.pages.repositories.harnesses", return_value=[]), \
                mock.patch("swe.find.plugin_drift.apply_fix") as apply, \
                mock.patch("swe.webapp.pages.plugins.clear_cache") as cleared:
            reply = page.post(ctx, "run", {"check": "plugins.unregistered", "message": "m"})
        apply.assert_called_once_with(home, "claude:unregistered:/m")
        cleared.assert_called_once()
        self.assertEqual((reply["ran"], reply["error"]), ("plugin-fix", None))


if __name__ == "__main__":
    unittest.main()
