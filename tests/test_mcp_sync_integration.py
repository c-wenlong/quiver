import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

# The MCP subsystem is invoked as a module so the test exercises the installed
# package exactly the way `swe mcp ...` does at runtime.
MCP_MODULE = "quiver.mcp"


class McpSyncIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

        # Registry
        swe_cfg = self.home / ".config" / "swe"
        swe_cfg.mkdir(parents=True, exist_ok=True)
        (swe_cfg / "tools.json").write_text(
            json.dumps(
                {
                    "opencode": {"aliases": ["oc"]},
                    "claude": {"aliases": ["cc"]},
                    "copilot": {"aliases": ["cp"]},
                    "cursor": {"aliases": ["cs"]},
                },
                indent=2,
            )
            + "\n"
        )

        # Source (opencode)
        opencode_cfg = self.home / ".config" / "opencode"
        opencode_cfg.mkdir(parents=True, exist_ok=True)
        (opencode_cfg / "opencode.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "notion": {
                            "command": ["node", "/tmp/notion.js"],
                            "environment": {"NOTION_TOKEN": "token"},
                            "enabled": True,
                            "type": "local",
                        }
                    }
                },
                indent=2,
            )
            + "\n"
        )

        # Targets
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {}}, indent=2) + "\n")
        copilot_cfg = self.home / ".copilot"
        copilot_cfg.mkdir(parents=True, exist_ok=True)
        (copilot_cfg / "mcp-config.json").write_text(json.dumps({"mcpServers": {}}, indent=2) + "\n")
        cursor_cfg = self.home / ".cursor"
        cursor_cfg.mkdir(parents=True, exist_ok=True)
        (cursor_cfg / "mcp.json").write_text(json.dumps({"mcpServers": {}}, indent=2) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=env,
            capture_output=True,
            text=True,
        )

    def read_json(self, rel_path):
        return json.loads((self.home / rel_path).read_text())

    def test_sync_dry_run_does_not_write(self):
        before = (self.home / ".cursor" / "mcp.json").read_text()

        result = self.run_mcp(
            "sync",
            "opencode",
            "cursor",
            "--only=notion",
            "--dry-run",
            "--no-interactive",
            "--force",
        )

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Dry-run mode", result.stdout)
        self.assertIn("would add", result.stdout)

        after = (self.home / ".cursor" / "mcp.json").read_text()
        self.assertEqual(before, after)

    def test_sync_writes_and_converts_format(self):
        result = self.run_mcp(
            "sync",
            "opencode",
            "claude",
            "--only=notion",
            "--no-interactive",
            "--force",
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        claude = self.read_json(".claude.json")
        notion = claude["mcpServers"]["notion"]
        self.assertEqual(notion["command"], "node")
        self.assertEqual(notion["args"], ["/tmp/notion.js"])
        self.assertEqual(notion["env"], {"NOTION_TOKEN": "token"})

    def test_sync_to_copilot_emits_empty_args_for_command_only_server(self):
        opencode = self.read_json(".config/opencode/opencode.json")
        opencode["mcp"]["gdocs"] = {
            "command": ["/tmp/google-docs-mcp-local"],
            "enabled": True,
            "type": "local",
        }
        (self.home / ".config" / "opencode" / "opencode.json").write_text(
            json.dumps(opencode, indent=2) + "\n"
        )

        result = self.run_mcp(
            "sync",
            "opencode",
            "copilot",
            "--only=gdocs",
            "--no-interactive",
            "--force",
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        copilot = self.read_json(".copilot/mcp-config.json")
        gdocs = copilot["mcpServers"]["gdocs"]
        self.assertEqual(gdocs["command"], "/tmp/google-docs-mcp-local")
        self.assertEqual(gdocs["args"], [])

    def test_sync_strict_blocks_unparseable_server(self):
        opencode = self.read_json(".config/opencode/opencode.json")
        opencode["mcp"]["__bad"] = {"unsupported": True}
        (self.home / ".config" / "opencode" / "opencode.json").write_text(
            json.dumps(opencode, indent=2) + "\n"
        )

        result = self.run_mcp(
            "sync",
            "opencode",
            "claude",
            "--only=__bad",
            "--strict",
            "--dry-run",
            "--no-interactive",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Strict mode blocked sync", result.stdout)

    def test_validate_reports_bad_entry(self):
        opencode = self.read_json(".config/opencode/opencode.json")
        opencode["mcp"]["__bad"] = {"unsupported": True}
        (self.home / ".config" / "opencode" / "opencode.json").write_text(
            json.dumps(opencode, indent=2) + "\n"
        )

        result = self.run_mcp("validate", "opencode")
        self.assertEqual(result.returncode, 1)
        self.assertIn("__bad", result.stdout)
        self.assertIn("Validation failed", result.stdout)

    def test_doctor_strict_fails_for_unhealthy_server(self):
        claude = self.read_json(".claude.json")
        claude.setdefault("mcpServers", {})["__badbin"] = {
            "command": "__no_such_binary__",
            "args": ["--version"],
        }
        (self.home / ".claude.json").write_text(json.dumps(claude, indent=2) + "\n")

        result = self.run_mcp("doctor", "--strict")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Doctor strict failed", result.stdout)
        self.assertIn("__badbin", result.stdout)

    def test_sync_rejects_unknown_flag(self):
        result = self.run_mcp("sync", "opencode", "claude", "--wat")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown flag(s)", result.stdout)

    def test_doctor_rejects_unknown_arg(self):
        result = self.run_mcp("doctor", "--wat")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown arg(s)", result.stdout)


if __name__ == "__main__":
    unittest.main()
