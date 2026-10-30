import io
import json
import shlex
import os
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

        code, out, run, save = self._run_install(["kilo"], which=which, npm=None)

        self.assertEqual(code, 0)
        self.assertIn("brew install kilo-org/tap/kilo", out)
        self.assertIn("npm install -g @kilocode/cli", out)
        self.assertIn("nix profile install nixpkgs#kilo", out)
        self.assertIn("npm not on PATH", out)
        self.assertIn("nix not on PATH", out)
        self.assertIn("swe install kilo --via=<method>", out)
        run.assert_not_called()
        save.assert_not_called()

    def test_an_id_wins_over_another_harness_alias(self):
        # A later harness listing "gemini" among its aliases must not take
        # `swe install gemini` (the Doctor's fix names the id).
        tools = {"gemini": {"command": "gemini", "aliases": []},
                 "other": {"command": "other", "aliases": ["gemini"]}}
        code, out, run, save = self._run_install(["gemini"], tools=tools)
        self.assertEqual(code, 0)
        self.assertIn("name:     gemini", out)
        self.assertIn("swe install gemini --via=<method>", out)
        run.assert_not_called()

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
        code, out, run, save = self._run_install(["kilo", "--via=brew"])

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["brew", "install", "kilo-org/tap/kilo"],
            capture_output=False, text=True, timeout=600,
        )
        self.assertIn("kilo", save.call_args.args[0])

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
            ["kilo", "--via=brew"], which=lambda name: None)

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

        code, out, run, save = self._run_install(["kilo", "--json"], which=which)

        self.assertEqual(code, 0)
        doc = json.loads(out)
        harness = doc["data"]["harnesses"][0]
        self.assertEqual(harness["name"], "kilo")
        self.assertEqual(harness["command"], "kilo")
        self.assertFalse(harness["installed"])
        self.assertIsNone(harness["path"])
        self.assertEqual(harness["description"],
                         "Kilo Code's CLI, an opencode fork; extension and CLI share ~/.config/kilo")
        self.assertFalse(harness["registered"])
        self.assertEqual(
            [m["via"] for m in harness["methods"]], ["brew", "npm", "nix"])
        self.assertEqual(harness["methods"][0]["command"], "brew install kilo-org/tap/kilo")
        self.assertEqual([m["available"] for m in harness["methods"]],
                         [True, False, False])
        self.assertIsNone(harness["uninstall"])
        run.assert_not_called()
        save.assert_not_called()

    def test_install_json_marks_registered_and_descriptionless_entries(self):
        tools = {"claude": {"command": "claude"}}
        code, out, run, save = self._run_install(
            ["claude", "--json"], tools=tools)

        self.assertEqual(code, 0)
        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertTrue(harness["registered"])
        self.assertTrue(harness["description"])

        # A name with no catalog entry decodes with an empty description.
        code, out, run, save = self._run_install(
            ["mastracode", "--json"], tools=tools)
        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertFalse(harness["registered"])
        self.assertEqual(harness["description"], "")

    def test_install_json_gui_app_reads_the_bundle_and_caskroom(self):
        # zcode is a GUI app: no PATH binary, so evidence is the .app
        # bundle, and the Caskroom record names the remover.
        with patch("quiver.harness.installers.installed_app",
                   return_value="/Applications/ZCode.app"), patch(
                "quiver.harness.installers.installed_cask",
                return_value="/opt/homebrew/Caskroom/zcode"):
            code, out, run, save = self._run_install(
                ["zcode", "--json"], which=lambda name: None)

        self.assertEqual(code, 0)
        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertTrue(harness["installed"])
        self.assertEqual(harness["path"], "/Applications/ZCode.app")
        self.assertEqual(harness["app"], "/Applications/ZCode.app")
        self.assertEqual(harness["website"], "https://zcode.z.ai")
        self.assertEqual(harness["uninstall"]["via"], "cask")
        self.assertEqual(harness["uninstall"]["command"],
                         "brew uninstall --cask zcode")

    def test_install_json_app_bundle_does_not_hide_a_missing_cli(self):
        # antigravity the provider is a catalog harness (command `agy`); the
        # IDE's bundle on disk must not mark its CLI installed. The row stays
        # installable, reports the app, and the Caskroom record still names
        # the remover brew owns.
        with patch("quiver.harness.installers.installed_app",
                   return_value="/Applications/Antigravity.app"), patch(
                "quiver.harness.installers.installed_cask",
                return_value="/opt/homebrew/Caskroom/antigravity"):
            code, out, run, save = self._run_install(
                ["antigravity", "--json"], which=lambda name: None)

        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertFalse(harness["installed"])
        self.assertIsNone(harness["path"])
        self.assertEqual(harness["app"], "/Applications/Antigravity.app")
        self.assertEqual(harness["uninstall"]["command"],
                         "brew uninstall --cask antigravity")

    def test_install_json_binary_wins_the_uninstaller_over_a_caskroom(self):
        # codex through npm plus a stale Caskroom record: the reported path
        # is the npm executable, so the remover must be npm's too.
        npm_bin = "/usr/local/lib/node_modules/@openai/codex/bin/codex"

        def which(name):
            return npm_bin if name == "codex" else None

        with patch("quiver.harness.installers.installed_app", return_value=None), \
                patch("quiver.harness.installers.installed_cask",
                      return_value="/opt/homebrew/Caskroom/codex"):
            code, out, run, save = self._run_install(
                ["codex", "--json"], which=which)

        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertEqual(harness["path"], npm_bin)
        self.assertEqual(harness["uninstall"]["via"], "npm")
        self.assertEqual(harness["uninstall"]["package"], "@openai/codex")

    def test_install_json_gui_app_without_caskroom_has_no_uninstall(self):
        # A dmg install leaves no brew record: the sheet gets the website,
        # and no remover rather than a guessed one.
        with patch("quiver.harness.installers.installed_app",
                   return_value="/Applications/ZCode.app"), patch(
                "quiver.harness.installers.installed_cask", return_value=None):
            code, out, run, save = self._run_install(
                ["zcode", "--json"], which=lambda name: None)

        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertTrue(harness["installed"])
        self.assertIsNone(harness["uninstall"])

    def test_install_json_emits_the_vendor_site(self):
        # Every catalogued provider carries a website so the sheet can
        # link out; gemini had none before the catalog audit.
        code, out, run, save = self._run_install(["gemini", "--json"])
        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertEqual(harness["website"], "https://geminicli.com")

        code, out, run, save = self._run_install(["kiro", "--json"])
        harness = json.loads(out)["data"]["harnesses"][0]
        self.assertEqual(harness["website"], "https://kiro.dev")

    def test_install_listing_notes_an_installed_gui_app(self):
        with patch("quiver.harness.installers.installed_app",
                   return_value="/Applications/ZCode.app"), patch(
                "quiver.harness.installers.installed_cask", return_value=None):
            code, out, run, save = self._run_install(
                ["zcode"], which=lambda name: None)

        self.assertEqual(code, 0)
        self.assertIn("already installed at /Applications/ZCode.app", out)
        self.assertNotIn("discover --apply", out)

    def test_install_gui_app_via_cask_succeeds_on_the_bundle(self):
        def which(name):
            return "/usr/bin/brew" if name == "brew" else None

        with patch("quiver.harness.installers.installed_app",
                   return_value="/Applications/ZCode.app"), patch(
                "quiver.harness.installers.installed_cask", return_value=None):
            code, out, run, save = self._run_install(["zcode", "--via=cask"],
                                                     which=which)

        self.assertEqual(code, 0)
        run.assert_called_once_with(
            ["brew", "install", "--cask", "zcode"],
            capture_output=False, text=True, timeout=600,
        )
        self.assertIn("installed at /Applications/ZCode.app", out)

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


class HarnessUsageCommandTest(unittest.TestCase):
    """``swe harness usage <devin|cursor> --account-dir=<dir>``."""

    def _run(self, args):
        from quiver.harness.commands import cmd_harness_usage

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cmd_harness_usage(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_missing_arguments_fail_with_nothing_on_stdout(self):
        code, out, err = self._run(["devin", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("account-dir", err)

    def test_unsupported_name_fails_with_empty_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self._run(
                ["gemini", f"--account-dir={tmp}", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("devin", err)

    def test_missing_dir_fails_with_empty_stdout(self):
        code, out, _ = self._run(
            ["devin", "--account-dir=/nonexistent/quiver-dir", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def _verdict(self, status, limit=None, detail=None):
        from quiver.harness.rate_limits import UsageOutcome

        return UsageOutcome(status=status, limit=limit, detail=detail)

    def test_json_emits_null_limit_when_signed_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict("signed-out"),
            ):
                code, out, _ = self._run(
                    ["devin", f"--account-dir={tmp}", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["name"], "devin")
        self.assertEqual(data["account_dir"], tmp)
        self.assertEqual(data["status"], "signed-out")
        self.assertIsNone(data["limit"])
        self.assertEqual(data["remedy"]["kind"], "relogin")

    def test_json_emits_the_list_row_limit_shape(self):
        from quiver.harness.models import RateLimitInfo

        limit = RateLimitInfo(
            tool_name="cursor", used_percent=42, limit_reached=False,
            reset_at=1_789_190_193.0, plan_type="pro", window_seconds=2678400,
            window="included",
            windows=[{"window": "included", "used_percent": 42,
                      "limit_reached": False, "reset_at": 1_789_190_193.0,
                      "window_seconds": 2678400}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict("ok", limit),
            ):
                code, out, _ = self._run(
                    ["cursor", f"--account-dir={tmp}", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)["data"]["limit"]
        self.assertEqual(payload["used_percent"], 42)
        self.assertEqual(payload["plan_type"], "pro")
        self.assertEqual(payload["windows"][0]["window"], "included")

    def test_human_output_says_not_signed_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict("signed-out"),
            ):
                code, out, _ = self._run(["devin", f"--account-dir={tmp}"])
        self.assertEqual(code, 0)
        self.assertIn("not signed in", out)

    def test_an_outage_is_a_typed_status_not_a_signed_out_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict(
                    "unavailable",
                    detail="couldn't read devin usage for this account"),
            ):
                code, out, err = self._run(
                    ["devin", f"--account-dir={tmp}", "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["status"], "unavailable")
        self.assertIsNone(data["limit"])
        self.assertEqual(data["remedy"]["kind"], "retry")
        # An outage is not a login problem: the account's remedy keeps
        # the retry wording and offers no sign-in command.
        self.assertEqual(data["remedy"]["summary"],
                         "Devin's usage couldn't be fetched")
        self.assertNotIn("command", data["remedy"])

    def test_an_auth_failure_names_its_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict("auth-required"),
            ):
                code, out, err = self._run(
                    ["devin", f"--account-dir={tmp}", "--json"])
        # Failures still exit 1 like the old error path: readers from
        # before `status` keep seeing a failed call, not a signed-out
        # account. The document is emitted first either way.
        self.assertEqual(code, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["status"], "auth-required")
        # The account's own login is stale, so the remedy points at the
        # account's data dir, not the default credentials file.
        self.assertEqual(
            data["remedy"]["command"],
            f"XDG_DATA_HOME={shlex.quote(tmp)} devin auth login")

    def test_human_output_prints_the_remedy_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict("auth-required"),
            ):
                code, out, _ = self._run(["devin", f"--account-dir={tmp}"])
        self.assertEqual(code, 1)
        self.assertIn("devin auth login", out)
        self.assertNotIn("not signed in", out)

    def test_stdout_stays_pure_json_when_a_fetch_helper_warns(self):
        from quiver.harness.models import RateLimitInfo

        limit = RateLimitInfo(
            tool_name="devin", used_percent=10, limit_reached=False,
            reset_at=0.0, plan_type="teams", window_seconds=0,
        )

        def noisy(name, account_dir):
            print("warning: something chatty on stdout")
            return self._verdict("ok", limit)

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                side_effect=noisy,
            ):
                code, out, _ = self._run(
                    ["devin", f"--account-dir={tmp}", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["limit"]["used_percent"], 10)
        self.assertNotIn("chatty", out)

    def test_human_output_lists_windows_and_marks_unavailable(self):
        from quiver.harness.models import RateLimitInfo

        limit = RateLimitInfo(
            tool_name="devin", used_percent=40, limit_reached=False,
            reset_at=1_789_190_193.0, plan_type="teams",
            window_seconds=86400, window="daily",
            windows=[
                {"window": "daily", "used_percent": 40,
                 "limit_reached": False, "reset_at": 1_789_190_193.0,
                 "window_seconds": 86400},
                {"window": "weekly", "used_percent": 0,
                 "limit_reached": False, "reset_at": 0.0,
                 "window_seconds": 604800, "unavailable": True},
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.get_account_usage",
                return_value=self._verdict("ok", limit),
            ):
                code, out, _ = self._run(["devin", f"--account-dir={tmp}"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        weekly = next(l for l in lines if "weekly" in l)
        # Unavailable renders as unknown, never as a real 0% figure.
        self.assertIn("—", weekly)
        self.assertNotIn("100%", weekly)


class HarnessUsageEventsCommandTest(unittest.TestCase):
    """``swe harness usage-events cursor``."""

    def _run(self, args):
        from quiver.harness.commands import cmd_harness_usage_events

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cmd_harness_usage_events(list(args))
        return code, out.getvalue(), err.getvalue()

    def _events(self, count=2):
        return [
            {"timestamp": 1789190193.0, "model": "claude-4-sonnet",
             "kind": "USAGE_EVENT_KIND_FREE_CREDIT", "conversation_id": "c1",
             "headless": False, "input_tokens": 10, "output_tokens": 5,
             "cache_read_tokens": 0, "cache_write_tokens": 0,
             "cost_usd": 0.01}
            for _ in range(count)
        ]

    def test_only_cursor_is_supported(self):
        code, out, err = self._run(["gemini", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("cursor", err)

    def test_json_emits_status_and_events(self):
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("ok", self._events(), "uid-1"),
        ) as fetch:
            code, out, _ = self._run(["cursor", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["status"], "ok")
        self.assertEqual(len(data["events"]), 2)
        # The default account: no account dir means a None state_db.
        fetch.assert_called_once_with(None, since=None)
        self.assertEqual(data["account_id"], "uid-1")

    def test_since_and_account_dir_pass_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "quiver.harness.rate_limits.fetch_cursor_usage_events",
                return_value=("ok", [], "uid-1"),
            ) as fetch:
                code, _, _ = self._run(
                    ["cursor", f"--account-dir={tmp}", "--since=1700000000",
                     "--json"])
        self.assertEqual(code, 0)
        fetch.assert_called_once_with(
            os.path.join(tmp, "User", "globalStorage", "state.vscdb"),
            since=1700000000.0)

    def test_unavailable_exits_nonzero_with_clean_json(self):
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("unavailable", [], "uid-1"),
        ):
            code, out, _ = self._run(["cursor", "--json"])
        # The status lands in the document; a transport failure exits
        # nonzero only in human mode, matching `swe harness usage`.
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["data"]["status"], "unavailable")
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("unavailable", [], "uid-1"),
        ):
            code, out, err = self._run(["cursor"])
        self.assertEqual(code, 1)
        self.assertNotIn("events", out)

    def test_signed_out_and_auth_required_are_zero_exit(self):
        for status in ("signed-out", "auth-required"):
            with patch(
                "quiver.harness.rate_limits.fetch_cursor_usage_events",
                return_value=(status, [], "uid-1"),
            ):
                code, out, _ = self._run(["cursor", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["data"]["status"], status)

    def test_stdout_stays_pure_json_when_a_helper_warns(self):
        def noisy(state_db, since=None):
            print("warning: something chatty on stdout")
            return "ok", self._events(1), "uid-1"

        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            side_effect=noisy,
        ):
            code, out, _ = self._run(["cursor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["data"]["events"]), 1)
        self.assertNotIn("chatty", out)

    def test_signed_out_and_auth_required_human_messages(self):
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("signed-out", [], None),
        ):
            code, out, _ = self._run(["cursor"])
        self.assertEqual(code, 0)
        self.assertIn("not signed in", out)
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("auth-required", [], "uid-1"),
        ):
            code, out, _ = self._run(["cursor"])
        self.assertEqual(code, 0)
        self.assertIn("sign in again", out)

    def test_incomplete_keeps_the_events_and_warns(self):
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("incomplete", self._events(), "uid-1"),
        ):
            code, out, err = self._run(["cursor"])
        self.assertEqual(code, 0)
        self.assertIn("2 events", out)
        # Human-mode diagnostics print on the console stream (stdout).
        self.assertIn("page cap", out)
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("incomplete", self._events(1), "uid-1"),
        ):
            code, out, _ = self._run(["cursor", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["status"], "incomplete")
        self.assertEqual(len(data["events"]), 1)

    def test_bad_since_unknown_flag_and_missing_account_dir(self):
        code, out, _ = self._run(["cursor", "--since=abc"])
        self.assertEqual(code, 1)
        self.assertIn("Bad --since", out)
        code, out, _ = self._run(["cursor", "--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown argument", out)
        code, out, _ = self._run(
            ["cursor", "--account-dir=/definitely/missing/dir"])
        self.assertEqual(code, 1)
        self.assertIn("No such directory", out)

    def test_human_output_summarises_by_model(self):
        with patch(
            "quiver.harness.rate_limits.fetch_cursor_usage_events",
            return_value=("ok", self._events(), "uid-1"),
        ):
            code, out, _ = self._run(["cursor"])
        self.assertEqual(code, 0)
        self.assertIn("2 events", out)
        self.assertIn("claude-4-sonnet", out)


if __name__ == "__main__":
    unittest.main()
