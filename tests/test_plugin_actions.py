"""`swe plugins install|uninstall|enable|disable <ref> --harness=...`.

The harness CLIs are faked: in-process by a ``run`` callable that records
argv and edits the fake home the way the real CLI would, and end to end by
a script on PATH. Nothing here touches a real ~/.claude or ~/.codex.
"""

import json
import os
import stat
import subprocess
import unittest
import unittest.mock
from pathlib import Path

from swe.plugins.actions import (
    ActionError,
    _edit_enabled,
    plugin_action,
    set_codex_enabled,
)

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_json_output import decode
    from test_plugin_drift import _FakeHome
    from test_plugin_fix import run_swe
except ImportError:  # `python -m unittest tests.test_plugin_actions`
    from tests.test_json_output import decode
    from tests.test_plugin_drift import _FakeHome
    from tests.test_plugin_fix import run_swe


class FakeCli:
    """Stands in for subprocess.run; ``effect`` mutates the home like the CLI."""

    def __init__(self, effect=None, code=0, stdout="", stderr=""):
        self.calls, self.envs = [], []
        self.effect, self.code, self.stdout, self.stderr = effect, code, stdout, stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        self.envs.append(kwargs.get("env"))
        if self.effect and self.code == 0:
            self.effect(argv)
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


class _ActionHome(_FakeHome):
    def act(self, action, ref="tool@mk", harness="claude", run=None):
        return plugin_action(self.home, action, ref, harness, run=run or FakeCli())

    def config(self) -> Path:
        return self.home / ".codex" / "config.toml"


class ClaudeTest(_ActionHome, unittest.TestCase):
    def test_install_runs_the_cli_and_reports_the_new_state(self):
        self.installed["plugins"].pop("tool@mk")
        self._write_claude()

        def install(argv):
            self.installed["plugins"]["tool@mk"] = [
                {"scope": "user", "installPath": str(self.claude_copy), "version": "1.0.0"}]
            self._write_claude()

        run = FakeCli(install, stdout="✔ Successfully installed tool@mk\n")
        result = self.act("install", run=run)
        self.assertEqual(run.calls, [["claude", "plugin", "install", "tool@mk"]])
        self.assertTrue(result["changed"])
        self.assertIsNone(result["detail"])
        self.assertEqual(result["ran"], [["claude", "plugin", "install", "tool@mk"]])
        self.assertEqual(result["state"], {"installed": True, "enabled": True, "version": "1.0.0"})
        self.assertIn("Successfully installed", result["output"])

    def test_each_verb(self):
        self.settings["enabledPlugins"]["tool@mk"] = False
        self._write_claude()
        for action, verb in (("enable", "enable"), ("uninstall", "uninstall")):
            run = FakeCli()
            self.act(action, run=run)
            self.assertEqual(run.calls, [["claude", "plugin", verb, "tool@mk"]])
        self.settings["enabledPlugins"]["tool@mk"] = True
        self._write_claude()
        run = FakeCli()
        self.act("disable", run=run)
        self.assertEqual(run.calls, [["claude", "plugin", "disable", "tool@mk"]])

    def test_already_there_runs_nothing(self):
        for action, detail in (("install", "already installed"), ("enable", "already enabled")):
            run = FakeCli()
            result = self.act(action, run=run)
            self.assertEqual(run.calls, [])
            self.assertFalse(result["changed"])
            self.assertEqual(result["detail"], detail)
        self.installed["plugins"].pop("tool@mk")
        self._write_claude()
        run = FakeCli()
        self.assertEqual(self.act("uninstall", run=run)["detail"], "not installed")
        self.assertEqual(run.calls, [])

    def test_enable_needs_an_install(self):
        self.installed["plugins"].pop("tool@mk")
        self._write_claude()
        with self.assertRaisesRegex(ActionError, "not installed in claude"):
            self.act("enable")

    def test_install_from_a_marketplace_swe_cannot_see_is_left_to_the_cli(self):
        run = FakeCli(code=1, stderr="Marketplace nope not found\n")
        with self.assertRaisesRegex(ActionError, "Marketplace nope not found"):
            self.act("install", ref="x@nope", run=run)
        self.assertEqual(run.calls, [["claude", "plugin", "install", "x@nope"]])

    def test_failure_reports_the_output_tail(self):
        self.installed["plugins"].pop("tool@mk")
        self._write_claude()
        run = FakeCli(code=1, stderr="line 1\nline 2\n✘ Plugin tool not found\n")
        with self.assertRaisesRegex(ActionError, r"(?s)claude plugin install tool@mk failed "
                                                 r"with exit status 1:\n.*Plugin tool not found"):
            self.act("install", run=run)

    def test_missing_cli_and_timeouts(self):
        self.installed["plugins"].pop("tool@mk")
        self._write_claude()

        def missing(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        with self.assertRaisesRegex(ActionError, "claude is not on PATH"):
            self.act("install", run=missing)

        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 300)

        with self.assertRaisesRegex(ActionError, "did not finish"):
            self.act("install", run=slow)

    def test_runs_against_the_home_that_was_read(self):
        self.installed["plugins"].pop("tool@mk")
        self._write_claude()
        run = FakeCli()
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/other",
                                                   "CODEX_HOME": "/elsewhere"}):
            self.act("install", run=run)
        (env,) = run.envs
        self.assertEqual(env["HOME"], str(self.home))
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertNotIn("CODEX_HOME", env)

    def test_a_project_only_install_still_installs_for_the_user(self):
        self.installed["plugins"]["tool@mk"][0]["scope"] = "project"
        self._write_claude()
        run = FakeCli()
        self.act("install", run=run)
        self.assertEqual(run.calls, [["claude", "plugin", "install", "tool@mk"]])

    def test_bad_refs_and_harnesses_run_nothing(self):
        run = FakeCli()
        for ref in ("-rf@mk", "tool", "tool@", "@mk", "a b@mk", "tool@mk;ls"):
            with self.assertRaisesRegex(ActionError, "not a plugin ref"):
                self.act("install", ref=ref, run=run)
        with self.assertRaisesRegex(ActionError, "--harness must be one of claude, codex"):
            self.act("install", harness="cursor", run=run)
        with self.assertRaisesRegex(ActionError, "unknown action"):
            self.act("update", run=run)
        self.assertEqual(run.calls, [])


class CodexTest(_ActionHome, unittest.TestCase):
    def test_install_and_uninstall_are_add_and_remove(self):
        run = FakeCli()
        self.act("uninstall", harness="codex", run=run)
        self.assertEqual(run.calls, [["codex", "plugin", "remove", "tool@mk"]])
        self._write_codex()
        self.config().write_text(self.config().read_text().replace('[plugins."tool@mk"]\nenabled = true\n', ""))
        run = FakeCli()
        self.act("install", harness="codex", run=run)
        self.assertEqual(run.calls, [["codex", "plugin", "add", "tool@mk"]])

    def test_disable_and_enable_edit_config_toml_and_run_nothing(self):
        run = FakeCli()
        result = self.act("disable", harness="codex", run=run)
        self.assertEqual(run.calls, [])
        self.assertEqual(result["edited"], str(self.config().resolve()))
        self.assertTrue(result["changed"])
        self.assertEqual(result["state"]["enabled"], False)
        self.assertIn('[plugins."tool@mk"]\nenabled = false\n', self.config().read_text())
        result = self.act("enable", harness="codex", run=run)
        self.assertEqual(result["state"]["enabled"], True)
        self.assertEqual(run.calls, [])

    def test_already_enabled_writes_nothing(self):
        before = self.config().stat().st_mtime_ns
        result = self.act("enable", harness="codex")
        self.assertEqual(result["detail"], "already enabled")
        self.assertIsNone(result["edited"])
        self.assertEqual(self.config().stat().st_mtime_ns, before)


class CodexTomlEditTest(_ActionHome, unittest.TestCase):
    BODY = (
        '# my codex config\n'
        'model = "gpt-5"  # keep\n\n'
        '[mcp_servers.x]\ncommand = "x"\nenv = { TOKEN = "s" }\n\n'
        "[plugins.'tool@mk'] # installed by hand\n"
        'enabled   =   true   # trailing comment\n'
        'note = "[plugins.\\"tool@mk\\"]"\n\n'
        '[plugins."other@mk"]\nenabled = true\n'
    )

    def write(self, text):
        self.config().write_text(text)

    def test_only_the_one_flag_changes(self):
        self.write(self.BODY)
        set_codex_enabled(self.home, "tool@mk", False)
        self.assertEqual(self.config().read_text(),
                         self.BODY.replace("enabled   =   true   #", "enabled   =   false   #"))

    def test_missing_flag_is_added_under_the_header(self):
        self.write('[plugins."tool@mk"]\nsource = "x"\n')
        set_codex_enabled(self.home, "tool@mk", False)
        self.assertEqual(self.config().read_text(),
                         '[plugins."tool@mk"]\nenabled = false\nsource = "x"\n')

    def test_crlf_and_mode_survive(self):
        self.write('[plugins."tool@mk"]\r\nenabled = true\r\n')
        self.config().chmod(0o600)
        set_codex_enabled(self.home, "tool@mk", False)
        self.assertEqual(self.config().read_bytes(), b'[plugins."tool@mk"]\r\nenabled = false\r\n')
        self.assertEqual(stat.S_IMODE(self.config().stat().st_mode), 0o600)

    def test_a_symlinked_config_is_edited_at_its_target(self):
        real = self.home / "dotfiles" / "config.toml"
        real.parent.mkdir()
        real.write_text('[plugins."tool@mk"]\nenabled = true\n')
        self.config().unlink()
        self.config().symlink_to(real)
        set_codex_enabled(self.home, "tool@mk", False)
        self.assertTrue(self.config().is_symlink())
        self.assertIn("enabled = false", real.read_text())

    def test_a_layout_it_cannot_prove_is_left_alone_with_the_edit_by_hand(self):
        for body in ('[plugins]\n"tool@mk" = { enabled = true }\n',
                     '[plugins]\n"tool@mk".enabled = true\n',
                     '[plugins."tool@mk"]\nenabled = true\nenabled = false\n'):
            self.write(body)
            with self.assertRaisesRegex(ActionError, r'set \[plugins\."tool@mk"\] enabled = false '
                                                     r'in ~/.codex/config.toml'):
                set_codex_enabled(self.home, "tool@mk", False)
            self.assertEqual(self.config().read_text(), body)

    def test_not_installed_is_refused(self):
        self.write('model = "x"\n')
        with self.assertRaisesRegex(ActionError, "no \\[plugins\\] table"):
            set_codex_enabled(self.home, "tool@mk", True)

    def test_an_unwritable_directory_fails_without_a_trace(self):
        self.write('[plugins."tool@mk"]\nenabled = true\n')
        parent = self.config().parent
        parent.chmod(0o500)
        self.addCleanup(parent.chmod, 0o700)
        if os.access(parent, os.W_OK):   # running as root
            self.skipTest("directory permissions are not enforced")
        with self.assertRaisesRegex(ActionError, "couldn't write"):
            set_codex_enabled(self.home, "tool@mk", False)
        self.assertEqual([p.name for p in parent.iterdir() if p.name.startswith(".config")], [])

    def test_edits_wait_for_another_swe_holding_the_lock(self):
        import fcntl
        import threading
        import time

        from swe.paths import cache_dir_for

        self.write('[plugins."tool@mk"]\nenabled = true\n')
        lock = cache_dir_for(self.home) / "codex-config.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "a") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            worker = threading.Thread(target=set_codex_enabled,
                                      args=(self.home, "tool@mk", False))
            worker.start()
            time.sleep(0.3)
            self.assertTrue(worker.is_alive())
            self.assertIn("enabled = true", self.config().read_text())
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertIn("enabled = false", self.config().read_text())

    def test_no_file_locking_refuses_with_the_edit_by_hand(self):
        import sys

        body = '[plugins."tool@mk"]\nenabled = true\n'
        self.write(body)
        with unittest.mock.patch.dict(sys.modules, {"fcntl": None}):   # as on Windows
            with self.assertRaisesRegex(ActionError, r"no file locking.*set \[plugins\."):
                set_codex_enabled(self.home, "tool@mk", False)
        self.assertEqual(self.config().read_text(), body)

    def test_edit_text_returns_none_without_the_table(self):
        self.assertIsNone(_edit_enabled('[plugins."x@y"]\nenabled = true\n', "tool@mk", True))


class ActionCliTest(_ActionHome, unittest.TestCase):
    def fake_codex(self, script: str) -> str:
        bin_dir = self.home / "bin"
        bin_dir.mkdir(exist_ok=True)
        fake = bin_dir / "codex"
        fake.write_text("#!/bin/sh\n" + script)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return f"{bin_dir}:/usr/bin:/bin"

    def test_install_json_runs_the_fake_cli(self):
        self.config().write_text(self.config().read_text().replace(
            '[plugins."tool@mk"]\nenabled = true\n', ""))
        config = self.config()
        path = self.fake_codex(
            f'printf \'[plugins."%s"]\\nenabled = true\\n\' "$3" >> "{config}"\necho "installed $3"\n')
        result = run_swe(self.home, "plugins", "install", "tool@mk", "--harness=codex", "--json",
                         path=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode(result)["data"]
        self.assertEqual(data["ran"], [["codex", "plugin", "add", "tool@mk"]])
        self.assertTrue(data["changed"])
        self.assertEqual(data["state"]["installed"], True)
        self.assertEqual(data["output"], "installed tool@mk")

    def test_failure_leaves_stdout_empty_and_says_why_on_stderr(self):
        path = self.fake_codex('echo "network down" >&2\nexit 3\n')
        result = run_swe(self.home, "plugins", "uninstall", "tool@mk", "--harness=codex", "--json",
                         path=path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("codex plugin remove tool@mk failed with exit status 3", result.stderr)
        self.assertIn("network down", result.stderr)

    def test_usage_errors(self):
        for args in (("plugins", "install", "tool@mk", "--json"),
                     ("plugins", "install", "--harness=claude", "--json"),
                     ("plugins", "install", "tool@mk", "--harness", "--json"),
                     ("plugins", "nope", "--json")):
            result = run_swe(self.home, *args)
            self.assertEqual(result.returncode, 1, args)
            self.assertEqual(result.stdout, "", args)

    def test_codex_disable_from_the_cli(self):
        result = run_swe(self.home, "plugins", "disable", "tool@mk", "--harness=codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disabled", result.stdout)
        self.assertIn("enabled = false", self.config().read_text())


if __name__ == "__main__":
    unittest.main()
