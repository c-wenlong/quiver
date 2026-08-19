import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
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
        ), patch(
            "quiver.harness.path_health.resolve_npm_package", return_value="demo-package"
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.subprocess.run",
            return_value=subprocess.CompletedProcess([], 2),
        ), patch("quiver.harness.commands.save_registry") as save:
            result = cmd_install(["demo"])

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
                ["demo", "--package", "@scope/demo", "--command", "demo-bin"]
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

    def test_install_dry_run_has_no_process_or_registry_write(self):
        with patch(
            "quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"
        ), patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.subprocess.run"
        ) as run, patch("quiver.harness.commands.save_registry") as save:
            result = cmd_install(["demo", "--dry-run"])

        self.assertEqual(result, 0)
        run.assert_not_called()
        save.assert_not_called()

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
