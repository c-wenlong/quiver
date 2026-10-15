import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from quiver.harness.commands import cmd_doctor, cmd_install, cmd_remove, cmd_use
from quiver.harness.path_health import NodeEnv, OffPathHit


class HarnessProcessCommandTest(unittest.TestCase):
    def test_use_preserves_extra_argument_boundaries(self):
        tools = {"claude": {"command": "claude", "aliases": ["cc"]}}
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.is_installed", return_value=True
        ), patch("quiver.harness.commands.os.execvp") as execvp:
            cmd_use(["cc", "--model", "claude sonnet"])

        execvp.assert_called_once_with(
            "claude", ["claude", "--model", "claude sonnet"]
        )

    def test_use_does_not_exec_missing_binary(self):
        tools = {"claude": {"command": "claude", "aliases": []}}
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.is_installed", return_value=False
        ), patch("quiver.harness.commands.os.execvp") as execvp:
            cmd_use(["claude"])

        execvp.assert_not_called()

    def test_failed_install_does_not_update_registry(self):
        with patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.subprocess.run",
            return_value=subprocess.CompletedProcess([], 2),
        ), patch("quiver.harness.commands.save_registry") as save:
            result = cmd_install(["demo", "--package=demo-package"])

        self.assertEqual(result, 2)
        save.assert_not_called()

    def test_successful_install_registers_resolved_command(self):
        with patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run, patch(
            "quiver.harness.commands.shutil.which", return_value="/usr/local/bin/demo"
        ), patch(
            "quiver.harness.tools.live_version", return_value="1.2.3"
        ), patch("quiver.harness.commands.save_registry") as save:
            result = cmd_install(
                ["demo", "--package=@scope/demo", "--command=demo-bin"]
            )

        self.assertEqual(result, 0)
        run.assert_called_once_with(
            ["/usr/bin/npm", "install", "-g", "@scope/demo"],
            capture_output=False,
            text=True,
            timeout=300,
        )
        saved = save.call_args.args[0]["demo"]
        self.assertEqual(saved["command"], "demo-bin")
        self.assertEqual(saved["version"], "1.2.3")

    def test_reinstall_keeps_the_display_name(self):
        existing = {"demo": {"command": "demo", "display_name": "Demo Code"}}
        with patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"
        ), patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0),
        ), patch(
            "quiver.harness.commands.shutil.which", return_value="/usr/local/bin/demo"
        ), patch(
            "quiver.harness.tools.live_version", return_value="1.2.3"
        ), patch("quiver.harness.commands.save_registry") as save:
            result = cmd_install(["demo", "--package=@scope/demo"])

        self.assertEqual(result, 0)
        self.assertEqual(save.call_args.args[0]["demo"]["display_name"], "Demo Code")

    def test_install_dry_run_has_no_process_or_registry_write(self):
        with patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.subprocess.run"
        ) as run, patch("quiver.harness.commands.save_registry") as save:
            result = cmd_install(["demo", "--package=demo-package", "--dry-run"])

        self.assertEqual(result, 0)
        run.assert_not_called()
        save.assert_not_called()

    def _run_install(self, argv, which=None, npm="/usr/bin/npm", tools=None):
        """Run cmd_install with every side effect patched; return (code, out)."""
        which = which if which is not None else lambda name: "/fake/bin/" + name
        out = io.StringIO()
        with patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value=npm
        ), patch(
            "quiver.harness.commands.load_registry", return_value=tools or {}
        ), patch(
            "quiver.harness.commands.load_registry_readonly", return_value=tools or {}
        ), patch(
            "quiver.harness.commands.shutil.which", side_effect=which
        ), patch(
            "quiver.harness.commands.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run, patch(
            "quiver.harness.tools.live_version", return_value="1.0.0"
        ), patch("quiver.harness.commands.save_registry") as save, redirect_stdout(out):
            code = cmd_install(argv)
        return code, out.getvalue(), run, save

    def test_install_lists_methods_and_runs_nothing(self):
        def which(name):
            return "/usr/bin/brew" if name == "brew" else None

        code, out, run, save = self._run_install(["gemini"], which=which, npm=None)

        self.assertEqual(code, 0)
        self.assertIn("brew install gemini-cli", out)
        self.assertIn("npm install -g @google/gemini-cli", out)
        self.assertIn("nix profile install nixpkgs#gemini-cli", out)
        self.assertIn("npm not on PATH", out)
        self.assertIn("nix not on PATH", out)
        self.assertIn("swe install gemini --via=<method>", out)
        run.assert_not_called()
        save.assert_not_called()

    def test_install_listing_notes_an_installed_command(self):
        code, out, run, save = self._run_install(["gemini"])

        self.assertEqual(code, 0)
        self.assertIn("already installed at /fake/bin/gemini", out)
        self.assertIn("swe discover --apply", out)
        run.assert_not_called()
        save.assert_not_called()

    def test_install_unknown_name_refuses_to_guess(self):
        code, out, run, save = self._run_install(["nosuch"])

        self.assertEqual(code, 1)
        self.assertIn("No known installer for 'nosuch'", out)
        self.assertIn("--package=<npm-name>", out)
        run.assert_not_called()
        save.assert_not_called()

    def test_install_via_brew_runs_brew(self):
        code, out, run, save = self._run_install(["gemini", "--via=brew"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["brew", "install", "gemini-cli"],
            capture_output=False, text=True, timeout=600,
        )
        self.assertIn("gemini", save.call_args.args[0])

    def test_install_via_cask_runs_brew_cask(self):
        code, out, run, save = self._run_install(["claude", "--via=cask"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["brew", "install", "--cask", "claude-code"],
            capture_output=False, text=True, timeout=600,
        )

    def test_install_via_npm_uses_preferred_npm(self):
        code, out, run, save = self._run_install(["gemini", "--via=npm"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["/usr/bin/npm", "install", "-g", "@google/gemini-cli"],
            capture_output=False, text=True, timeout=300,
        )

    def test_install_via_script_pipes_curl_to_sh(self):
        code, out, run, save = self._run_install(["droid", "--via=script"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["/bin/bash", "-o", "pipefail", "-c",
             "curl -fsSL https://app.factory.ai/cli | sh"],
            capture_output=False, text=True, timeout=600,
        )
        self.assertIn("droid", save.call_args.args[0])

    def test_install_via_method_the_harness_lacks(self):
        code, out, run, save = self._run_install(["cursor", "--via=npm"])

        self.assertEqual(code, 1)
        self.assertIn("no npm installer", out)
        self.assertIn("cask", out)
        run.assert_not_called()
        save.assert_not_called()

    def test_install_via_invalid_method(self):
        code, out, run, save = self._run_install(["gemini", "--via=winget"])

        self.assertEqual(code, 1)
        self.assertIn("brew, cask, npm, uv, nix, script", out)
        run.assert_not_called()

    def test_install_via_missing_tool(self):
        code, out, run, save = self._run_install(
            ["gemini", "--via=brew"], which=lambda name: None)

        self.assertEqual(code, 1)
        self.assertIn("'brew' is needed", out)
        run.assert_not_called()

    def test_install_package_with_non_npm_via(self):
        code, out, run, save = self._run_install(
            ["gemini", "--via=brew", "--package=x"])

        self.assertEqual(code, 1)
        run.assert_not_called()

    def test_install_via_dry_run_prints_the_command(self):
        code, out, run, save = self._run_install(
            ["gemini", "--via=npm", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("/usr/bin/npm install -g @google/gemini-cli", out)
        run.assert_not_called()
        save.assert_not_called()

    def test_install_json_for_one_name(self):
        def which(name):
            return "/usr/bin/brew" if name == "brew" else None

        code, out, run, save = self._run_install(["gemini", "--json"], which=which)

        self.assertEqual(code, 0)
        doc = json.loads(out)
        harness = doc["data"]["harnesses"][0]
        self.assertEqual(harness["name"], "gemini")
        self.assertEqual(harness["command"], "gemini")
        self.assertFalse(harness["installed"])
        self.assertIsNone(harness["path"])
        self.assertEqual(
            [m["via"] for m in harness["methods"]], ["brew", "npm", "nix"])
        self.assertEqual(harness["methods"][0]["command"], "brew install gemini-cli")
        self.assertEqual([m["available"] for m in harness["methods"]],
                         [True, False, False])
        run.assert_not_called()
        save.assert_not_called()

    def test_install_json_for_every_name(self):
        code, out, run, save = self._run_install(["--json"])

        self.assertEqual(code, 0)
        from quiver.harness.installers import INSTALLERS
        doc = json.loads(out)
        self.assertEqual([h["name"] for h in doc["data"]["harnesses"]],
                         sorted(INSTALLERS))
        run.assert_not_called()
        save.assert_not_called()

    def test_install_json_rejects_run_flags(self):
        err = io.StringIO()
        with patch("quiver.harness.commands.load_registry", return_value={}),                 redirect_stderr(err):
            code = cmd_install(["gemini", "--json", "--via=npm"])

        self.assertEqual(code, 1)
        self.assertIn("--json", err.getvalue())

    def test_install_via_npm_with_package_overrides_the_table(self):
        # cursor has no npm entry, but --package already says what to install.
        code, out, run, save = self._run_install(
            ["cursor", "--via=npm", "--package=curzr"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["/usr/bin/npm", "install", "-g", "curzr"],
            capture_output=False, text=True, timeout=300,
        )
        self.assertIn("cursor", save.call_args.args[0])

    def test_install_listing_and_json_leave_no_registry_behind(self):
        # Read-only paths must not create harness.json on a fresh machine.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "config" / "harness.json"
            legacy = Path(tmp) / "config" / "tools.json"
            with patch("quiver.harness.registry.HARNESS_FILE", missing), patch(
                "quiver.harness.registry.TOOLS_FILE", legacy
            ), patch(
                "quiver.harness.commands.shutil.which", return_value=None
            ), patch("quiver.harness.commands.subprocess.run") as run, patch(
                "quiver.harness.commands.save_registry") as save:
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(cmd_install(["gemini"]), 0)
                    self.assertEqual(cmd_install(["--json"]), 0)
                self.assertFalse(missing.exists())
                run.assert_not_called()
                save.assert_not_called()

    def test_install_lookup_reads_a_legacy_tools_json_without_migrating(self):
        # An upgraded machine with only tools.json keeps its alias and its
        # custom command, and a dry run still writes nothing.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "config" / "harness.json"
            legacy = Path(tmp) / "config" / "tools.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(json.dumps(
                {"claude": {"command": "claude-custom", "aliases": ["cc"]}}))
            with patch("quiver.harness.registry.HARNESS_FILE", missing), patch(
                "quiver.harness.registry.TOOLS_FILE", legacy
            ), patch(
                "quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"
            ), patch("quiver.harness.commands.subprocess.run") as run, patch(
                "quiver.harness.commands.save_registry") as save:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = cmd_install(["cc", "--via=npm", "--dry-run"])
                self.assertFalse(missing.exists())
                self.assertTrue(legacy.exists())

        self.assertEqual(code, 0)
        self.assertIn("@anthropic-ai/claude-code", out.getvalue())
        self.assertIn("claude-custom", out.getvalue())
        run.assert_not_called()
        save.assert_not_called()

    def test_install_json_unknown_name_errors(self):
        err = io.StringIO()
        with patch("quiver.harness.commands.load_registry", return_value={}),                 redirect_stderr(err):
            code = cmd_install(["nosuch", "--json"])

        self.assertEqual(code, 1)
        self.assertIn("No known installer", err.getvalue())

    def test_remove_persists_registry_without_selected_tool(self):
        tools = {
            "claude": {"command": "claude", "aliases": ["cc"]},
            "codex": {"command": "codex", "aliases": []},
        }
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save:
            cmd_remove(["cc"])

        self.assertEqual(set(save.call_args.args[0]), {"codex"})


class HarnessDoctorCommandTest(unittest.TestCase):
    def _run_doctor(self, env, orphans):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"NVM_DIR": str(Path(tmp) / "missing-nvm")}, clear=False
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.path_health.probe_node_env", return_value=env
        ), patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value=env.npm
        ), patch("quiver.harness.path_health.nvm_bin_dirs", return_value=[]), patch(
            "quiver.harness.path_health.find_off_path_tools", return_value=orphans
        ), patch("quiver.harness.commands.shutil.which", return_value=env.npm):
            output = io.StringIO()
            with redirect_stdout(output):
                result = cmd_doctor([])
        return result, output.getvalue()

    def test_doctor_returns_zero_for_healthy_environment(self):
        env = NodeEnv(
            node="/usr/bin/node",
            npm="/usr/bin/npm",
            node_version="22.0.0",
            npm_version="10.0.0",
            global_prefix="/usr/local",
            global_bin="/usr/local/bin",
            global_bin_on_path=True,
        )

        result, output = self._run_doctor(env, [])

        self.assertEqual(result, 0)
        self.assertIn("Environment looks healthy", output)

    def test_doctor_returns_one_for_off_path_harness(self):
        env = NodeEnv(None, None, None, None, None, None, False)
        hit = OffPathHit("claude", "/tmp/npm/bin/claude", "nvm")

        result, output = self._run_doctor(env, [("claude", "claude", hit)])

        self.assertEqual(result, 1)
        self.assertIn("Off-PATH installs", output)


if __name__ == "__main__":
    unittest.main()
