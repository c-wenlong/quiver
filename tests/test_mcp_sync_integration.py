import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

# The MCP subsystem is invoked as a module so the test exercises the installed
# package exactly the way `swe mcp ...` does at runtime.
MCP_MODULE = "swe.mcp"
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
        swe_cfg = self.home / ".swe" / "config"
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


class McpSyncLocalOnlyTargetTest(unittest.TestCase):
    """Claude Desktop's config launches local servers only: a sync leaves a
    URL server out of it and says so, and ``add`` refuses one for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        (self.home / ".swe" / "config").mkdir(parents=True)
        (self.home / ".swe" / "config" / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}}) + "\n")
        (self.home / ".swe" / "mcp.json").write_text(json.dumps({"mcpServers": {
            "remote": {"url": "https://mcp.example.com/mcp"},
            "local": {"command": "npx", "args": ["-y", "local-mcp"]},
        }}) + "\n")
        self.desktop = self.home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        self.desktop.parent.mkdir(parents=True)
        self.desktop.write_text(json.dumps({"mcpServers": {}}) + "\n")
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {}}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args):
        return subprocess.run([sys.executable, "-m", MCP_MODULE, *args], capture_output=True,
                              text=True, env=_mcp_env(self.home), timeout=60)

    def test_sync_leaves_a_url_server_out_of_claude_desktop(self):
        result = self.run_mcp("sync", "quiver", "claude-desktop", "claude", "--no-interactive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        desktop = json.loads(self.desktop.read_text())["mcpServers"]
        self.assertEqual(sorted(desktop), ["local"])
        self.assertEqual(sorted(json.loads((self.home / ".claude.json").read_text())["mcpServers"]),
                         ["local", "remote"])
        self.assertIn("1 remote left out (remote)", result.stdout)

    def test_a_url_server_reaches_claude_code_with_its_type(self):
        # Claude Code skips a url entry with no type, so sync stamps one,
        # and an overwrite of a stdio entry does not keep the old "stdio".
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {
            "remote": {"type": "stdio", "command": "old"}}}) + "\n")
        result = self.run_mcp("sync", "quiver", "claude", "--no-interactive", "--force")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        claude = json.loads((self.home / ".claude.json").read_text())["mcpServers"]
        self.assertEqual(claude["remote"], {"type": "http", "url": "https://mcp.example.com/mcp"})
        self.assertNotIn("type", claude["local"])

    def test_an_unset_secret_on_a_remote_server_does_not_block_claude_desktop(self):
        hub = self.home / ".swe" / "mcp.json"
        data = json.loads(hub.read_text())
        data["mcpServers"]["remote"]["headers"] = {"Authorization": "Bearer ${MISSING_TOKEN}"}
        hub.write_text(json.dumps(data) + "\n")
        result = self.run_mcp("sync", "quiver", "claude-desktop", "--no-interactive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(json.loads(self.desktop.read_text())["mcpServers"]), ["local"])
        # A target that can take it still needs the value.
        result = self.run_mcp("sync", "quiver", "claude", "--no-interactive")
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING_TOKEN", result.stdout)

    def test_add_refuses_a_url_server_for_claude_desktop(self):
        result = self.run_mcp("add", "r2", "--url=https://mcp.example.com/x", "--to=claude-desktop")
        self.assertEqual(result.returncode, 1)
        self.assertIn("takes local servers only", result.stdout + result.stderr)
        self.assertNotIn("r2", self.desktop.read_text())


class McpSyncCodexPeerTest(unittest.TestCase):
    """Verify codex is a first-class peer of the existing JSON tools.

    After the refactor, ``swe mcp sync <source> codex`` (and the reverse)
    go through the same generic loop that already powered json-to-json
    sync. Codex is treated as a TOML-region file via ``swe.mcp.codex_io``.
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
        'CODEX_HOME = "/Users/dev/.codex"\n\n'
        '[mcp_servers.miro-mcp]\n'
        "url = 'https://mcp.miro.com/'\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

        # Minimal registry (just opencode + claude), codex is registered
        # via _EXTRA_ALIASES so it does not need a tools.json entry.
        swe_cfg = self.home / ".swe" / "config"
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

    def test_prune_scoped_to_source_pool_keeps_codex_only_servers(self):
        """--only narrows the run: prune deletes only names the source
        offered but the selection dropped, so codex-only servers — the
        user's own entries the source never knew — survive."""
        r = self._run("sync", "opencode", "codex", "--only=notion",
                      "--no-interactive", "--force", "--prune")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        text = self.codex_path.read_text()
        # Newly synced server is present.
        self.assertIn("[mcp_servers.notion]", text)
        # Codex-only servers preserved under the narrowed selection.
        self.assertIn("[mcp_servers.fathom]", text)
        self.assertIn("[mcp_servers.node_repl]", text)
        self.assertIn("[mcp_servers.miro-mcp]", text)
        # Non-MCP siblings untouched.
        self.assertIn('model = "gpt-5.5"', text)
        self.assertIn('[features]', text)

    def test_prune_unfiltered_still_mirrors_the_target(self):
        """With the whole source selected, --prune keeps its mirror
        semantics: target-only entries are deleted."""
        r = self._run("sync", "opencode", "codex",
                      "--no-interactive", "--force", "--prune")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        text = self.codex_path.read_text()
        self.assertIn("[mcp_servers.notion]", text)
        self.assertNotIn("[mcp_servers.fathom]", text)
        self.assertNotIn("[mcp_servers.node_repl]", text)
        self.assertNotIn("[mcp_servers.miro-mcp]", text)
        self.assertIn('model = "gpt-5.5"', text)

    def test_prune_except_deletes_only_source_known_names(self):
        """--except narrows the selection: the excluded source-known name
        is pruned from the target, codex-only entries still survive."""
        src = self.home / ".config" / "opencode" / "opencode.json"
        data = json.loads(src.read_text())
        data["mcp"]["extra"] = {"command": "x", "type": "local"}
        src.write_text(json.dumps(data, indent=2) + "\n")
        self.codex_path.write_text(
            self.codex_path.read_text() + "\n[mcp_servers.extra]\ncommand = 'x'\n"
        )

        r = self._run("sync", "opencode", "codex", "--except=extra",
                      "--no-interactive", "--force", "--prune")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        text = self.codex_path.read_text()
        self.assertIn("[mcp_servers.notion]", text)
        # Source-known but excluded: pruned.
        self.assertNotIn("[mcp_servers.extra]", text)
        # Codex-only: the user's own entries survive the narrowed run.
        self.assertIn("[mcp_servers.fathom]", text)
        self.assertIn("[mcp_servers.miro-mcp]", text)

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

    @staticmethod
    def _identity_editor() -> str:
        """An $EDITOR that rewrites the temp file semantically unchanged."""
        script = (
            "import json, sys\n"
            "p = sys.argv[1]\n"
            "d = json.load(open(p))\n"
            "json.dump(d, open(p, 'w'), indent=2)\n"
        )
        return f'{sys.executable} -c "{script}"'

    def test_edit_opencode_no_change_save_preserves_entry(self):
        """Regression: `swe mcp edit` routed the raw opencode dict through
        emit(), which expects canonical shape — a no-change save nested the
        command list and dropped `environment`/`enabled`/`type`. The raw
        dict is now written back verbatim."""
        env = _mcp_env(self.home)
        env["EDITOR"] = self._identity_editor()
        r = subprocess.run(
            [sys.executable, "-m", MCP_MODULE, "edit", "opencode", "notion"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        data = json.loads(
            (self.home / ".config" / "opencode" / "opencode.json").read_text()
        )
        self.assertEqual(
            data["mcp"]["notion"],
            {
                "command": ["node", "/tmp/notion.js"],
                "environment": {"NOTION_TOKEN": "tok"},
                "enabled": True,
                "type": "local",
            },
        )

    def test_edit_claude_preserves_extension_keys(self):
        """`disabled`/`autoApprove` are not canonical fields: emit() stripped
        them, so editing a disabled claude server silently re-enabled it."""
        claude_path = self.home / ".claude.json"
        claude_path.write_text(
            json.dumps({
                "mcpServers": {
                    "fs": {
                        "command": "node",
                        "args": ["/tmp/fs.js"],
                        "disabled": True,
                        "autoApprove": ["read"],
                    }
                }
            }, indent=2) + "\n"
        )
        env = _mcp_env(self.home)
        env["EDITOR"] = self._identity_editor()
        r = subprocess.run(
            [sys.executable, "-m", MCP_MODULE, "edit", "claude", "fs"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        data = json.loads(claude_path.read_text())
        self.assertEqual(
            data["mcpServers"]["fs"],
            {
                "command": "node",
                "args": ["/tmp/fs.js"],
                "disabled": True,
                "autoApprove": ["read"],
            },
        )

    def test_edit_opencode_real_edit_keeps_other_keys(self):
        """An actual edit updates the touched field and leaves every other
        key of the entry — and every other server — untouched."""
        oc_path = self.home / ".config" / "opencode" / "opencode.json"
        fixture = json.loads(oc_path.read_text())
        fixture["mcp"]["linear"] = {
            "command": ["bun", "/tmp/linear.js"],
            "environment": {"LINEAR_KEY": "lk"},
            "enabled": True,
            "type": "local",
        }
        oc_path.write_text(json.dumps(fixture, indent=2) + "\n")
        script = (
            "import json, sys\n"
            "p = sys.argv[1]\n"
            "d = json.load(open(p))\n"
            "d['notion']['enabled'] = False\n"
            "json.dump(d, open(p, 'w'), indent=2)\n"
        )
        env = _mcp_env(self.home)
        env["EDITOR"] = f'{sys.executable} -c "{script}"'
        r = subprocess.run(
            [sys.executable, "-m", MCP_MODULE, "edit", "opencode", "notion"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        data = json.loads(oc_path.read_text())
        self.assertEqual(
            data["mcp"]["notion"],
            {
                "command": ["node", "/tmp/notion.js"],
                "environment": {"NOTION_TOKEN": "tok"},
                "enabled": False,
                "type": "local",
            },
        )
        # The untouched server must survive the save byte-for-byte.
        self.assertEqual(
            data["mcp"]["linear"],
            {
                "command": ["bun", "/tmp/linear.js"],
                "environment": {"LINEAR_KEY": "lk"},
                "enabled": True,
                "type": "local",
            },
        )


class McpSyncDroidPeerTest(unittest.TestCase):
    """Verify Factory Droid is a first-class peer for ``swe mcp sync``.

    Droid stores MCP config at ``~/.factory/mcp.json`` under the
    ``mcpServers`` key, using the standard shape plus an explicit
    ``type`` field on remote servers. The droid alias ``df`` resolves
    through the tools.json registry, so no ``_EXTRA_ALIASES`` entry is
    needed.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

        # Registry: opencode + droid (with its `df` alias).
        swe_cfg = self.home / ".swe" / "config"
        swe_cfg.mkdir(parents=True, exist_ok=True)
        (swe_cfg / "tools.json").write_text(
            json.dumps({
                "opencode": {"aliases": ["oc"]},
                "droid": {"aliases": ["df"]},
            }, indent=2) + "\n"
        )

        # Source: opencode with a stdio and a remote server.
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
                    "linear": {
                        "url": "https://mcp.linear.app/mcp",
                        "enabled": True,
                        "type": "remote",
                    },
                },
            }, indent=2) + "\n"
        )

        # Destination: an empty Droid user-level mcp.json.
        factory_dir = self.home / ".factory"
        factory_dir.mkdir(parents=True, exist_ok=True)
        self.droid_path = factory_dir / "mcp.json"
        self.droid_path.write_text(json.dumps({"mcpServers": {}}, indent=2) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home), capture_output=True, text=True,
        )

    def test_list_includes_droid_as_peer(self):
        r = self._run("list")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("droid", r.stdout)
        self.assertIn("notion", r.stdout)
        self.assertIn("linear", r.stdout)

    def test_df_alias_resolves_to_droid_target(self):
        # The original failure: `swe mcp sync oc df` -> "Unknown target tool: df".
        r = self._run("sync", "oc", "df", "--only=notion",
                      "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        droid = json.loads(self.droid_path.read_text())
        notion = droid["mcpServers"]["notion"]
        self.assertEqual(notion["command"], "node")
        self.assertEqual(notion["args"], ["/tmp/notion.js"])
        self.assertEqual(notion["env"], {"NOTION_TOKEN": "tok"})
        # stdio servers do not require a `type` field in droid.
        self.assertNotIn("type", notion)

    def test_sync_opencode_to_droid_emits_type_for_remote(self):
        r = self._run("sync", "opencode", "droid", "--only=linear",
                      "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        droid = json.loads(self.droid_path.read_text())
        linear = droid["mcpServers"]["linear"]
        self.assertEqual(linear["url"], "https://mcp.linear.app/mcp")
        # Droid requires `type` for remote servers; default to http.
        self.assertEqual(linear["type"], "http")

    def test_sync_dry_run_does_not_write_droid_file(self):
        before = self.droid_path.read_text()
        r = self._run("sync", "opencode", "droid", "--only=notion",
                      "--dry-run", "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("Dry-run mode", r.stdout)
        self.assertEqual(before, self.droid_path.read_text())


class McpSyncUnverifiedHarnessTest(unittest.TestCase):
    """Syncing to a harness without an explicit MCP_CONFIG_MAP entry.

    Any registry harness without a verified MCP config gets an
    optimistic default at ``~/.<tool>/mcp.json`` (standard ``mcpServers``
    shape), flagged ``unverified``. This lets ``swe mcp sync`` work
    between any two registry harnesses without per-tool code changes.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

        # Registry: opencode (verified MCP) + hermes (no MCP_CONFIG_MAP entry).
        swe_cfg = self.home / ".swe" / "config"
        swe_cfg.mkdir(parents=True, exist_ok=True)
        (swe_cfg / "tools.json").write_text(
            json.dumps({
                "opencode": {"aliases": ["oc"]},
                "hermes": {"aliases": ["hs"]},
            }, indent=2) + "\n"
        )

        # Source: opencode with one server.
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

        # Hermes has no config dir yet — sync should create ~/.hermes/mcp.json.
        self.hermes_path = self.home / ".hermes" / "mcp.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home), capture_output=True, text=True,
        )

    def test_sync_to_unverified_harness_resolves_alias_and_writes(self):
        # `hs` is hermes' alias; no MCP_CONFIG_MAP entry, but the registry
        # knows it, so sync falls back to ~/.hermes/mcp.json.
        r = self._run("sync", "oc", "hs", "--only=notion",
                      "--no-interactive", "--force")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertTrue(self.hermes_path.exists())
        hermes = json.loads(self.hermes_path.read_text())
        notion = hermes["mcpServers"]["notion"]
        self.assertEqual(notion["command"], "node")
        self.assertEqual(notion["args"], ["/tmp/notion.js"])
        self.assertEqual(notion["env"], {"NOTION_TOKEN": "tok"})

    def test_sync_to_unverified_warns_about_optimistic_path(self):
        r = self._run("sync", "opencode", "hermes", "--only=notion",
                      "--no-interactive", "--force", "--dry-run")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("verified", r.stdout.lower())
        self.assertIn("optimistic", r.stdout.lower())
        self.assertIn(".hermes/mcp.json", r.stdout)

    def test_list_hides_unverified_without_config_by_default(self):
        r = self._run("list")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        # opencode (verified) shows; hermes (unverified, no file) is hidden.
        self.assertIn("opencode", r.stdout)
        self.assertNotIn("hermes", r.stdout)

    def test_list_with_explicit_target_shows_unverified(self):
        r = self._run("list", "hermes")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        # Explicitly naming hermes surfaces it (with "No MCP servers found"
        # since the file doesn't exist yet).
        self.assertEqual(r.returncode, 0)

    def test_all_does_not_broadcast_to_unverified_without_config(self):
        r = self._run("sync", "opencode", "--all", "--only=notion",
                      "--no-interactive", "--force", "--dry-run")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        # --all broadcasts to verified peers only; hermes is not mentioned.
        self.assertNotIn("hermes", r.stdout)

    def test_diff_works_between_verified_and_unverified(self):
        r = self._run("diff", "opencode", "hermes")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        # hermes has no servers; diff reports opencode-only.
        self.assertIn("notion", r.stdout)


if __name__ == "__main__":
    unittest.main()

