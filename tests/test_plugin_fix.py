"""`swe find plugins --json` and `--fix=<id>`: plugin drift for the macOS app.

The app lists drift findings and, after the user confirms, asks swe to run
one finding's fix by id. swe recomputes drift and runs only the commands it
builds itself, without a shell, so the app never hands it a command line.
"""

import json
import os
import stat
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

from swe.find.plugin_drift import Finding, FixError, apply_fix, plugin_drift

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_json_output import SRC, decode
    from test_plugin_drift import _FakeHome
except ImportError:  # `python -m unittest tests.test_plugin_fix`
    from tests.test_json_output import SRC, decode
    from tests.test_plugin_drift import _FakeHome


def run_swe(home: Path, *args: str, path: str | None = None,
            extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key.startswith("COVERAGE_")}
    env.update(extra or {})
    env.update({
        "HOME": str(home),
        "PATH": path or os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(SRC),
        "NO_COLOR": "1",
    })
    return subprocess.run([sys.executable, "-m", "swe.cli", *args],
                          capture_output=True, text=True, env=env, timeout=60)


class Recorder:
    """Stands in for subprocess.run, failing on the command named ``fail``."""

    def __init__(self, fail: str | None = None):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.fail = fail

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        self.envs.append(kwargs.get("env"))
        code = 1 if self.fail and self.fail in argv else 0
        return subprocess.CompletedProcess(argv, code, "", "boom\n" if code else "")


class FindingArgvTest(_FakeHome, unittest.TestCase):
    def test_every_command_fix_has_matching_argv(self):
        self.known = {}
        self._write_claude()
        (finding,) = self.drift(harnesses=("claude",))
        self.assertEqual(finding.argv, (("claude", "plugin", "marketplace", "add", str(self.market)),))
        self.assertEqual(finding.fix, f"claude plugin marketplace add {self.market}")
        self.assertEqual(finding.id, f"claude:unregistered:{self.market}")

    def test_codex_update_is_two_commands(self):
        (self.codex_copy / "agents" / "helper.md").write_text("changed\n")
        (finding,) = self.drift(harnesses=("codex",))
        self.assertEqual(finding.kind, "stale")
        self.assertEqual(finding.argv, (("codex", "plugin", "remove", "tool@mk"),
                                        ("codex", "plugin", "add", "tool@mk")))
        self.assertEqual(finding.id, "codex:stale:tool@mk")

    def test_codex_enable_has_nothing_to_run(self):
        self.codex_enabled = "false"
        self._write_codex()
        (finding,) = self.drift(harnesses=("codex",))
        self.assertEqual(finding.kind, "disabled-here")
        self.assertEqual(finding.argv, ())
        self.assertIn("config.toml", finding.fix)


class ApplyFixTest(_FakeHome, unittest.TestCase):
    def test_runs_the_commands_of_the_named_finding(self):
        (self.codex_copy / "agents" / "helper.md").write_text("changed\n")
        run = Recorder()
        finding = apply_fix(self.home, "codex:stale:tool@mk", run=run)
        self.assertEqual(finding.kind, "stale")
        self.assertEqual(run.calls, [["codex", "plugin", "remove", "tool@mk"],
                                     ["codex", "plugin", "add", "tool@mk"]])

    def test_stops_at_the_first_failure(self):
        (self.codex_copy / "agents" / "helper.md").write_text("changed\n")
        run = Recorder(fail="remove")
        with self.assertRaisesRegex(FixError, "codex plugin remove tool@mk failed: boom"):
            apply_fix(self.home, "codex:stale:tool@mk", run=run)
        self.assertEqual(len(run.calls), 1)

    def test_an_id_that_no_longer_applies_runs_nothing(self):
        run = Recorder()
        with self.assertRaisesRegex(FixError, "no current plugin drift"):
            apply_fix(self.home, f"claude:unregistered:{self.market}", run=run)
        self.assertEqual(run.calls, [])

    def test_an_edit_fix_refuses_to_run(self):
        self.codex_enabled = "false"
        self._write_codex()
        run = Recorder()
        with self.assertRaisesRegex(FixError, "nothing to run"):
            apply_fix(self.home, "codex:disabled-here:tool@mk", run=run)
        self.assertEqual(run.calls, [])

    def test_a_missing_harness_cli_is_named(self):
        self.known = {}
        self._write_claude()

        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with self.assertRaisesRegex(FixError, "claude is not on PATH"):
            apply_fix(self.home, f"claude:unregistered:{self.market}", run=missing)

    def test_any_other_launch_error_is_a_fix_error_not_a_traceback(self):
        self.known = {}
        self._write_claude()

        def not_executable(argv, **kwargs):
            raise PermissionError(13, "Permission denied")

        with self.assertRaisesRegex(FixError, "couldn't run claude: Permission denied"):
            apply_fix(self.home, f"claude:unregistered:{self.market}", run=not_executable)

    def test_the_harness_runs_against_the_home_that_was_checked(self):
        (self.codex_copy / "agents" / "helper.md").write_text("changed\n")
        run = Recorder()
        with unittest.mock.patch.dict(os.environ, {"CODEX_HOME": "/elsewhere",
                                                   "CLAUDE_CONFIG_DIR": "/other"}):
            apply_fix(self.home, "codex:stale:tool@mk", run=run)
        for env in run.envs:
            self.assertEqual(env["HOME"], str(self.home))
            self.assertNotIn("CODEX_HOME", env)
            self.assertNotIn("CLAUDE_CONFIG_DIR", env)

    def test_two_directories_with_one_marketplace_name_get_different_ids(self):
        twin = self.home / ".quiver" / "plugins" / "twin"
        (twin / ".claude-plugin").mkdir(parents=True)
        (twin / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "mk", "plugins": [{"name": "other", "source": "./other"}]}))
        (twin / "other").mkdir()
        self.known = {}
        self._write_claude()
        ids = [f.id for f in self.drift(harnesses=("claude",)) if f.kind == "unregistered"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        run = Recorder()
        apply_fix(self.home, f"claude:unregistered:{twin}", run=run)
        self.assertEqual(run.calls, [["claude", "plugin", "marketplace", "add", str(twin)]])

    def test_an_ambiguous_id_runs_nothing(self):
        dup = Finding("claude", "mk", "tool", "stale", "d", "claude plugin update tool@mk",
                      argv=(("claude", "plugin", "update", "tool@mk"),))
        run = Recorder()
        with unittest.mock.patch("swe.find.plugin_drift.plugin_drift", return_value=[dup, dup]):
            with self.assertRaisesRegex(FixError, "2 findings share id"):
                apply_fix(self.home, dup.id, run=run)
        self.assertEqual(run.calls, [])


class PluginsJsonCliTest(_FakeHome, unittest.TestCase):
    def test_no_drift_is_an_empty_list_with_the_source_count(self):
        result = run_swe(self.home, "find", "plugins", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result)["data"], {"sources": 1, "drift": []})

    def test_findings_carry_id_fix_and_whether_it_can_run(self):
        self.known = {}
        self._write_claude()
        self.codex_enabled = "false"
        self._write_codex()
        result = run_swe(self.home, "find", "plugins", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["id"]: row for row in decode(result)["data"]["drift"]}
        self.assertEqual(sorted(rows), [f"claude:unregistered:{self.market}"])
        row = rows[f"claude:unregistered:{self.market}"]
        self.assertEqual(row["harness"], "claude")
        self.assertIsNone(row["plugin"])
        self.assertEqual(row["kind"], "unregistered")
        self.assertTrue(row["runnable"])
        self.assertEqual(row["paths"], [])

    def test_fix_runs_the_harness_cli_and_reports_the_finding(self):
        self.known = {}
        self._write_claude()
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        marker = self.home / "ran.txt"
        fake = bin_dir / "claude"
        fake.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\necho "${{CLAUDE_CONFIG_DIR:-unset}}" >> "{marker}"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = run_swe(self.home, "find", "plugins", f"--fix=claude:unregistered:{self.market}", "--json",
                         path=f"{bin_dir}:/usr/bin:/bin",
                         extra={"CLAUDE_CONFIG_DIR": "/some/other/account"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result)["data"]["fixed"]["id"], f"claude:unregistered:{self.market}")
        self.assertEqual(marker.read_text().splitlines(),
                         [f"plugin marketplace add {self.market}", "unset"])

    def test_fix_for_an_unknown_id_fails_with_nothing_on_stdout(self):
        result = run_swe(self.home, "find", "plugins", "--fix=claude:stale:nope", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("no current plugin drift", result.stderr)

    def test_fix_only_works_for_plugins(self):
        result = run_swe(self.home, "find", "skills", "--fix=x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("only available for plugins", result.stdout)


class LinkedManifestCliTest(_FakeHome, unittest.TestCase):
    """A symlinked .codex-plugin, end to end through the installed command."""

    def setUp(self):
        super().setUp()
        self.link_manifest(".claude-plugin")

    def test_reported_as_runnable_and_not_as_stale(self):
        result = run_swe(self.home, "find", "plugins", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        [row] = decode(result)["data"]["drift"]
        self.assertEqual((row["id"], row["kind"], row["runnable"], row["paths"]),
                         ("codex:linked-manifest:tool@mk", "linked-manifest", True, []))

    def test_fix_replaces_the_link_in_the_source(self):
        # The inherited PATH: rm and cp are not under /usr/bin in Nix's sandbox.
        result = run_swe(self.home, "find", "plugins", "--fix=codex:linked-manifest:tool@mk",
                         "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode(result)["data"]["fixed"]["kind"], "linked-manifest")
        manifest = self.plugin / ".codex-plugin"
        self.assertFalse(manifest.is_symlink())
        self.assertTrue((manifest / "plugin.json").is_file())

    def test_listing_names_the_kind(self):
        result = run_swe(self.home, "find", "plugins", "--full")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("linked manifest", result.stdout)
        self.assertIn("rm ", result.stdout)

    def test_hub_shows_the_link_before_a_stale_copy(self):
        from swe.skills.inventory import hub_plugins

        (self.plugin / "agents" / "helper.md").write_text("changed\n")
        self.assertEqual(self.kinds(), [("codex", "linked-manifest"), ("codex", "stale")])
        [plugin] = hub_plugins(self.home)["plugins"]
        codex = plugin["install"]["codex"]
        self.assertEqual((codex["state"], codex["id"]),
                         ("linked-manifest", "codex:linked-manifest:tool@mk"))


class KindLabelsTest(unittest.TestCase):
    def test_every_kind_reads_as_words_in_both_listings(self):
        from swe.find.commands import DRIFT_WORD
        from swe.find.plugin_drift import KINDS
        from swe.skills.commands import _HUB_STATES

        for kind in KINDS:
            self.assertIn(kind, DRIFT_WORD)
            self.assertIn(kind, _HUB_STATES)


if __name__ == "__main__":
    unittest.main()
