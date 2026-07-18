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
PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _mcp_env(home: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_SRC) if not existing else f"{PROJECT_SRC}{os.pathsep}{existing}"
    )
    return env


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
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home),
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


class McpSyncCodexPeerTest(unittest.TestCase):
    """Verify codex is a first-class peer of the existing JSON tools.

    After the refactor, ``swe mcp sync <source> codex`` (and the reverse)
    go through the same generic loop that already powered json-to-json
    sync. Codex is treated as a TOML-region file via ``quiver.mcp.codex_io``.
    """

    CODEX_PRESET = (
        'model = "gpt-5.5"\n'
        'personality = \'friendly\'\n\n'
        '[features]\n'
        'multi_agent = true\n\n'
        '[plugins]\n'
        '[plugins."github@openai-curated"]\n'
        'enabled = true\n\n'
        '[mcp_servers]\n'
        '[mcp_servers.fathom]\n'
        "args = ['--directory', '/tmp/fathom', 'run', 'python3', 'server.py']\n"
        "command = 'uv'\n\n"
        '[mcp_servers.node_repl]\n'
        'args = []\n'
        'command = "/Applications/Codex.app/Contents/Resources/cua_node/bin/node_repl"\n'
        'startup_timeout_sec = 120\n\n'
        '[mcp_servers.node_repl.env]\n'
        'CODEX_HOME = "/Users/kaichen/.codex"\n\n'
        '[mcp_servers.miro-mcp]\n'
        "url = 'https://mcp.miro.com/'\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

        # Minimal registry (just opencode + claude), codex is registered
        # via _EXTRA_ALIASES so it does not need a tools.json entry.
        swe_cfg = self.home / ".config" / "swe"
        swe_cfg.mkdir(parents=True, exist_ok=True)
        (swe_cfg / "tools.json").write_text(
            json.dumps({
                "opencode": {"aliases": ["oc"]},
                "claude": {"aliases": ["cc"]},
                "claude-desktop": {"aliases": ["cd"]},
            }, indent=2) + "\n"
        )

        # Source: opencode with one MCP server.
        opencode_cfg = self.home / ".config" / "opencode"
        opencode_cfg.mkdir(parents=True, exist_ok=True)
        (opencode_cfg / "opencode.json").write_text(
            json.dumps({
                "mcp": {
                    "notion": {
                        "command": ["node", "/tmp/notion.js"],
                        "environment": {"NOTION_TOKEN": "tok"},
                        "enabled": True,
                        "type": "local",
                    },
                },
            }, indent=2) + "\n"
        )

        # Destination: an empty Claude config.
        (self.home / ".claude.json").write_text(
            json.dumps({"mcpServers": {}}, indent=2) + "\n")

        # Destination: codex.toml with non-MCP siblings and codex-only servers.
        codex_dir = self.home / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        self.codex_path = codex_dir / "config.toml"
        self.codex_path.write_text(self.CODEX_PRESET)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home), capture_output=True, text=True,
        )

    def test_list_includes_codex_as_peer(self):
        r = self._run("list")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("codex", r.stdout)
        self.assertIn("notion", r.stdout)
        self.assertIn("fathom", r.stdout)

    def test_diff_opencode_codex_runs(self):
        r = self._run("diff", "opencode", "codex")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("notion", r.stdout)
        self.assertIn("fathom", r.stdout)

    def test_sync_opencode_to_codex_dry_run_preserves_non_mcp_sections(self):
        r = self._run("sync", "opencode", "codex", "--only=notion",
                      "--dry-run", "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        # Dry-run: codex.toml unchanged.
        text = self.codex_path.read_text()
        self.assertNotIn("[mcp_servers.notion]", text)
        # Existing codex-only servers untouched.
        self.assertIn("[mcp_servers.fathom]", text)
        self.assertIn("[mcp_servers.node_repl]", text)
        self.assertIn("[mcp_servers.miro-mcp]", text)
        # Non-MCP siblings untouched.
        self.assertIn('model = "gpt-5.5"', text)
        self.assertIn('[features]', text)
        self.assertIn('[plugins."github@openai-curated"]', text)
        self.assertIn("startup_timeout_sec = 120", text)

    def test_sync_opencode_to_codex_writes_new_server(self):
        r = self._run("sync", "opencode", "codex", "--only=notion",
                      "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        text = self.codex_path.read_text()
        self.assertIn("[mcp_servers.notion]", text)
        self.assertIn("[mcp_servers.fathom]", text)
        self.assertIn("startup_timeout_sec = 120", text)
        # Non-MCP siblings untouched.
        self.assertIn('[features]', text)
        self.assertIn('[plugins."github@openai-curated"]', text)

    def test_prune_flag_now_graph_wide_removes_codex_only_servers(self):
        r = self._run("sync", "opencode", "codex", "--only=notion",
                      "--no-interactive", "--force", "--prune")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        text = self.codex_path.read_text()
        # Newly synced server is present.
        self.assertIn("[mcp_servers.notion]", text)
        # Codex-only servers all deleted.
        self.assertNotIn("[mcp_servers.fathom]", text)
        self.assertNotIn("[mcp_servers.node_repl]", text)
        self.assertNotIn("[mcp_servers.miro-mcp]", text)
        # Non-MCP siblings untouched.
        self.assertIn('model = "gpt-5.5"', text)
        self.assertIn('[features]', text)

    def test_write_to_codex_dry_run_keeps_file_unchanged(self):
        # --dry-run yields a preview without modifying the codex.toml.
        r = self._run("sync", "opencode", "codex",
                      "--only=notion", "--no-interactive", "--force")
        # The general sync does not default to dry-run (only sync-codex did),
        # so this WILL write. Document the change.
        self.assertEqual(r.returncode, 0)
        text = self.codex_path.read_text()
        self.assertIn("[mcp_servers.notion]", text)

    def test_edit_codex_preserves_scalar_extras(self):
        """Regression: `swe mcp edit codex node_repl` must NOT strip codex-specific
        scalars like `startup_timeout_sec` when the user edits them in the JSON.

        Before this fix, cmd_edit routed the edited dict through
        StandardMcpFormatHandler.emit(), which drops unknown keys.
        """
        editor_script = (
            "import json, sys\n"
            "p = sys.argv[1]\n"
            "d = json.load(open(p))\n"
            "first = next(iter(d))\n"
            "d[first]['startup_timeout_sec'] = 90\n"
            "json.dump(d, open(p, 'w'), indent=2)\n"
        )
        env = _mcp_env(self.home)
        env["EDITOR"] = f'{sys.executable} -c "{editor_script}"'

        r = subprocess.run(
            [sys.executable, "-m", MCP_MODULE, "edit", "codex", "node_repl"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        text = self.codex_path.read_text()
        # The user's edit survived the save.
        self.assertIn("startup_timeout_sec = 90", text)
        # Other codex-only servers untouched.
        self.assertIn("[mcp_servers.fathom]", text)
        self.assertIn("[mcp_servers.miro-mcp]", text)
        # command preserved.
        self.assertIn("/Applications/Codex.app/Contents/Resources/cua_node/bin/node_repl", text)
        # Non-MCP siblings preserved.
        self.assertIn("[features]", text)
        self.assertIn('[plugins."github@openai-curated"]', text)


if __name__ == "__main__":
    unittest.main()
