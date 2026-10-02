"""Malformed-on-disk config must degrade on reads and refuse on writes.

A config file that fails to parse is never "empty": the next save would
wipe whatever it actually held (``~/.claude.json`` carries OAuth tokens and
project state, not just the MCP slice). Every loader in this file's scope
follows the same contract — reads warn and degrade, writes raise
``CorruptConfigurationError`` before touching the file.
"""

import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.configuration import (
    CorruptConfigurationError,
    parse_json_object,
    read_json_object,
)

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


class ParseJsonObjectTest(unittest.TestCase):
    def test_valid_object(self):
        self.assertEqual(parse_json_object('{"a": 1}', Path("x.json")), {"a": 1})

    def test_malformed_raises(self):
        with self.assertRaises(CorruptConfigurationError):
            parse_json_object("{nope", Path("x.json"))

    def test_non_object_raises(self):
        for text in ("[1,2]", '"str"', "42"):
            with self.assertRaises(CorruptConfigurationError, msg=text):
                parse_json_object(text, Path("x.json"))


class ReadJsonObjectTest(unittest.TestCase):
    def test_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_json_object(Path(tmp) / "gone.json"), {})

    def test_malformed_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text("{nope")
            with self.assertRaises(CorruptConfigurationError):
                read_json_object(p)

    def test_non_utf8_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_bytes(b"\xff\xfe{}")
            with self.assertRaises(CorruptConfigurationError):
                read_json_object(p)

    def test_missing_with_allow_missing_false_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                read_json_object(Path(tmp) / "gone.json", allow_missing=False)


class HarnessRegistryCorruptTest(unittest.TestCase):
    def _patch(self, config_dir):
        return (
            patch("quiver.harness.registry.CONFIG_DIR", config_dir),
            patch("quiver.harness.registry.HARNESS_FILE", config_dir / "harness.json"),
            patch("quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
        )

    def test_corrupt_harness_json_raises(self):
        from quiver.harness.registry import load_registry

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "harness.json").write_text("{corrupt")
            patches = self._patch(config_dir)
            with patches[0], patches[1], patches[2]:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry()

    def test_non_object_harness_json_raises(self):
        from quiver.harness.registry import load_registry

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "harness.json").write_text("[1, 2]")
            patches = self._patch(config_dir)
            with patches[0], patches[1], patches[2]:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry()

    def test_save_registry_is_atomic_and_leaves_no_tmp(self):
        from quiver.harness.registry import load_registry, save_registry

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            patches = self._patch(config_dir)
            with patches[0], patches[1], patches[2]:
                save_registry({"claude": {"command": "claude"}})
                loaded = load_registry()
                self.assertEqual(loaded["claude"]["command"], "claude")
                leftovers = list(config_dir.glob("*.tmp"))
                self.assertEqual(leftovers, [])


class ProvidersRegistryCorruptTest(unittest.TestCase):
    def _patch(self, config_dir):
        return (
            patch("quiver.providers.registry.CONFIG_DIR", config_dir),
            patch(
                "quiver.providers.registry.PROVIDERS_REGISTRY_FILE",
                config_dir / "providers.json",
            ),
        )

    def test_corrupt_providers_json_raises(self):
        from quiver.providers.registry import load_registry

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "providers.json").write_text("{corrupt")
            patches = self._patch(config_dir)
            with patches[0], patches[1]:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry()

    def test_corrupt_file_is_never_overwritten_by_seed(self):
        """The old code's 'read-only' promise was fake: a corrupt file read
        as empty, then the seed branch overwrote it with defaults. The file
        must now survive any load attempt unchanged."""
        from quiver.providers.registry import load_registry

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            reg = config_dir / "providers.json"
            reg.write_text("{corrupt")
            patches = self._patch(config_dir)
            with patches[0], patches[1]:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry()
                self.assertEqual(reg.read_text(), "{corrupt")


class SkillCatalogsCorruptTest(unittest.TestCase):
    def test_save_refuses_to_overwrite_malformed_file(self):
        from quiver.skills.catalogs import save_skill_catalogs

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            cat_file = config_dir / "skill_catalogs.json"
            cat_file.write_text("{corrupt")
            with patch(
                "quiver.skills.catalogs.CONFIG_DIR", config_dir
            ), patch(
                "quiver.skills.catalogs.SKILL_CATALOGS_FILE", cat_file
            ):
                with self.assertRaises(CorruptConfigurationError):
                    save_skill_catalogs([{"label": "x", "path": "/tmp"}])
                self.assertEqual(cat_file.read_text(), "{corrupt")

    def test_save_writes_when_missing_or_valid(self):
        from quiver.skills.catalogs import load_skill_catalogs, save_skill_catalogs

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            cat_file = config_dir / "skill_catalogs.json"
            with patch(
                "quiver.skills.catalogs.CONFIG_DIR", config_dir
            ), patch(
                "quiver.skills.catalogs.SKILL_CATALOGS_FILE", cat_file
            ):
                save_skill_catalogs([{"label": "x", "path": "/tmp/x"}])
                self.assertEqual(load_skill_catalogs()[0]["label"], "x")


class McpLoadJsonStrictTest(unittest.TestCase):
    def test_malformed_raises_and_non_object_raises(self):
        from quiver.mcp.cli import load_json

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{corrupt")
            with self.assertRaises(CorruptConfigurationError):
                load_json(bad)
            arr = Path(tmp) / "arr.json"
            arr.write_text("[1]")
            with self.assertRaises(CorruptConfigurationError):
                load_json(arr)

    def test_missing_returns_empty(self):
        from quiver.mcp.cli import load_json

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_json(Path(tmp) / "gone.json"), {})

    def test_jsonc_comments_still_parse(self):
        from quiver.mcp.cli import load_json

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cfg.jsonc"
            p.write_text('// comment\n{"mcpServers": {}}\n')
            self.assertEqual(load_json(p), {"mcpServers": {}})


class McpSaverRefuseTest(unittest.TestCase):
    """The write path must refuse to touch a malformed target file."""

    def _tool(self, path):
        cfg = {"mcp_servers": {"x": {}}}
        with patch(
            "quiver.mcp.cli.get_tool_config",
            return_value={"path": path, "key": "mcpServers", "format": "standard"},
        ):
            yield cfg

    def test_save_refuses_corrupt_target(self):
        from quiver.mcp.cli import get_tool_saver

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".claude.json"
            original = "{corrupt but precious"
            target.write_text(original)
            with patch(
                "quiver.mcp.cli.get_tool_config",
                return_value={"path": target, "key": "mcpServers", "format": "standard"},
            ):
                saver = get_tool_saver("claude")
                with self.assertRaises(CorruptConfigurationError):
                    saver({"x": {"command": "npx"}}, target)
            self.assertEqual(target.read_text(), original)

    def test_load_degrades_gracefully_on_corrupt(self):
        from quiver.mcp.cli import get_tool_loader

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".claude.json"
            target.write_text("{corrupt")
            with patch(
                "quiver.mcp.cli.get_tool_config",
                return_value={"path": target, "key": "mcpServers", "format": "standard"},
            ):
                loader = get_tool_loader("claude")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    out = loader(target)
                self.assertEqual(out, {})
                self.assertIn("Cannot parse JSON", buf.getvalue())

    def test_load_warns_when_slice_is_not_object(self):
        from quiver.mcp.cli import get_tool_loader

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".cursor" / "mcp.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"mcpServers": [1,2]}')
            with patch(
                "quiver.mcp.cli.get_tool_config",
                return_value={"path": target, "key": "mcpServers", "format": "standard"},
            ):
                loader = get_tool_loader("cursor")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    out = loader(target)
                self.assertEqual(out, {})
                self.assertIn("not an object", buf.getvalue())


class HubCorruptTest(unittest.TestCase):
    def test_get_hub_servers_warns_and_returns_empty(self):
        from quiver.mcp.cli import get_hub_servers

        with tempfile.TemporaryDirectory() as tmp:
            hub = Path(tmp) / "mcp.json"
            hub.write_text("{corrupt")
            with patch("quiver.mcp.cli.MCP_SOURCE_FILE", hub):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(get_hub_servers(), {})
                self.assertIn("Cannot parse JSON", buf.getvalue())

    def test_save_hub_servers_refuses_corrupt_file(self):
        from quiver.mcp.cli import save_hub_servers

        with tempfile.TemporaryDirectory() as tmp:
            hub = Path(tmp) / "mcp.json"
            hub.write_text("{corrupt")
            with patch("quiver.mcp.cli.MCP_SOURCE_FILE", hub):
                with self.assertRaises(CorruptConfigurationError):
                    save_hub_servers({"x": {}})
                self.assertEqual(hub.read_text(), "{corrupt")

    def test_apply_findings_refuses_corrupt_hub(self):
        from quiver.mcp.discover import apply_mcp_findings, McpFinding

        with tempfile.TemporaryDirectory() as tmp:
            hub = Path(tmp) / "mcp.json"
            hub.write_text("{corrupt")
            finding = McpFinding(
                name="x", tools=("claude",), status="new",
                source_tool="claude", server={"command": "npx"},
            )
            with patch("quiver.mcp.discover.MCP_SOURCE_FILE", hub):
                with self.assertRaises(CorruptConfigurationError):
                    apply_mcp_findings([finding])
                self.assertEqual(hub.read_text(), "{corrupt")


class CliTopLevelTest(unittest.TestCase):
    def test_corrupt_registry_exits_1_with_clean_error(self):
        from quiver import cli

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "harness.json").write_text("{corrupt")
            with patch(
                "quiver.harness.registry.CONFIG_DIR", config_dir
            ), patch(
                "quiver.harness.registry.HARNESS_FILE",
                config_dir / "harness.json",
            ), patch(
                "quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"
            ), patch.object(sys, "argv", ["swe", "list"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli.main()
                self.assertEqual(rc, 1)
                self.assertIn("Cannot parse JSON", buf.getvalue())
                self.assertNotIn("Traceback", buf.getvalue())

    def _harness_patches(self, config_dir):
        return (
            patch("quiver.harness.registry.CONFIG_DIR", config_dir),
            patch(
                "quiver.harness.registry.HARNESS_FILE",
                config_dir / "harness.json",
            ),
            patch("quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
        )

    def test_bare_swe_help_failure_exits_1(self):
        from quiver import cli

        with patch.object(
            cli, "cmd_help", side_effect=CorruptConfigurationError("broken")
        ), patch.object(sys, "argv", ["swe"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main()
            self.assertEqual(rc, 1)
            self.assertIn("broken", buf.getvalue())

    def test_unknown_command_help_failure_still_reports(self):
        from quiver import cli

        with patch.object(
            cli, "cmd_help", side_effect=CorruptConfigurationError("broken")
        ), patch.object(sys, "argv", ["swe", "bogus"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main()
            self.assertEqual(rc, 1)
            self.assertIn("Unknown command", buf.getvalue())
            self.assertIn("broken", buf.getvalue())

    def test_providers_main_catches_corrupt_registry(self):
        from quiver.providers import cli as providers_cli

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "providers.json").write_text("{corrupt")
            with patch(
                "quiver.providers.registry.CONFIG_DIR", config_dir
            ), patch(
                "quiver.providers.registry.PROVIDERS_REGISTRY_FILE",
                config_dir / "providers.json",
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = providers_cli.main(["list"])
                self.assertEqual(rc, 1)
                self.assertIn("Cannot parse JSON", buf.getvalue())

    def test_mcp_main_catches_corrupt_registry(self):
        from quiver.mcp import cli as mcp_cli

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir()
            (config_dir / "harness.json").write_text("{corrupt")
            p1, p2, p3 = self._harness_patches(config_dir)
            with p1, p2, p3:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = mcp_cli.main(["list"])
                self.assertEqual(rc, 1)
                self.assertIn("Cannot parse JSON", buf.getvalue())


class McpSyncCorruptTargetIntegrationTest(unittest.TestCase):
    """End to end: `swe mcp sync` refuses to overwrite a malformed target."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        swe_cfg = self.home / ".quiver" / "config"
        swe_cfg.mkdir(parents=True, exist_ok=True)
        (swe_cfg / "tools.json").write_text(
            json.dumps({
                "opencode": {"aliases": ["oc"]},
                "claude": {"aliases": ["cc"]},
            }, indent=2) + "\n"
        )
        opencode_cfg = self.home / ".config" / "opencode"
        opencode_cfg.mkdir(parents=True, exist_ok=True)
        (opencode_cfg / "opencode.json").write_text(
            json.dumps({
                "mcp": {
                    "notion": {
                        "command": ["node", "/tmp/notion.js"],
                        "enabled": True,
                        "type": "local",
                    }
                }
            }, indent=2) + "\n"
        )
        self.claude_path = self.home / ".claude.json"
        # Precious non-MCP content that must survive.
        self.claude_path.write_text('{corrupt "oauth": "token"')
        (self.home / ".quiver" / "mcp.json").write_text(
            json.dumps({"mcpServers": {}}, indent=2) + "\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args):
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home), capture_output=True, text=True,
        )

    def test_sync_refuses_corrupt_target(self):
        before = self.claude_path.read_text()
        r = self.run_mcp(
            "sync", "opencode", "claude", "--no-interactive", "--force"
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("Cannot parse JSON", r.stdout + r.stderr)
        self.assertEqual(self.claude_path.read_text(), before)

    def test_list_warns_and_continues(self):
        r = self.run_mcp("list")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("Cannot parse JSON", r.stdout)
        self.assertIn("notion", r.stdout)

    def test_discover_warns_on_corrupt_hub_and_apply_refuses(self):
        # --apply only ever writes the hub file, so refusal kicks in when
        # mcp.json itself is malformed.
        hub = self.home / ".quiver" / "mcp.json"
        hub.write_text("{corrupt")
        r = self.run_mcp("discover")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("Cannot parse JSON", r.stdout)
        r = self.run_mcp("discover", "--apply")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(hub.read_text(), "{corrupt")


class CorruptRegistryIntegrationTest(unittest.TestCase):
    """`swe list` on a corrupt harness.json: clean error, not a traceback."""

    def test_list_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            swe_cfg = home / ".quiver" / "config"
            swe_cfg.mkdir(parents=True, exist_ok=True)
            (swe_cfg / "harness.json").write_text("{corrupt")
            env = os.environ.copy()
            env["HOME"] = str(home)
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(PROJECT_SRC)
                if not existing
                else f"{PROJECT_SRC}{os.pathsep}{existing}"
            )
            r = subprocess.run(
                [sys.executable, "-m", "quiver.cli", "list"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("Cannot parse JSON", r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
