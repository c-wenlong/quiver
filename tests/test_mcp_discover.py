import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers


def _registry_patches(config_dir: Path, registry_file: Path, mcp_file: Path):
    return (
        patch("quiver.harness.registry.CONFIG_DIR", config_dir),
        patch("quiver.harness.registry.REGISTRY_FILE", registry_file),
        patch("quiver.paths.CONFIG_DIR", config_dir),
        patch("quiver.paths.MCP_SOURCE_FILE", mcp_file),
        patch("quiver.mcp.discover.MCP_SOURCE_FILE", mcp_file),
    )


class McpDiscoverTest(unittest.TestCase):
    def test_discovers_servers_not_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            config_dir.mkdir(parents=True)
            registry_file = config_dir / "tools.json"
            mcp_file = config_dir / "mcp.json"
            registry_file.write_text(
                json.dumps({"opencode": {"aliases": ["oc"]}, "claude": {"aliases": ["cc"]}})
            )
            mcp_file.write_text(json.dumps({"mcpServers": {}}, indent=2))

            opencode_cfg = tmp_path / ".config" / "opencode"
            opencode_cfg.mkdir(parents=True)
            opencode_json = opencode_cfg / "opencode.json"
            opencode_json.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "notion": {
                                "command": ["node", "/tmp/notion.js"],
                                "environment": {},
                                "enabled": True,
                                "type": "local",
                            }
                        }
                    }
                )
            )
            claude_json = tmp_path / ".claude.json"
            claude_json.write_text(json.dumps({"mcpServers": {}}, indent=2))

            mcp_map = {
                "opencode": {
                    "path": opencode_json,
                    "key": "mcp",
                    "label": "opencode",
                    "format": "opencode",
                },
                "claude": {
                    "path": claude_json,
                    "key": "mcpServers",
                    "label": "Claude Code",
                },
            }

            p1, p2, p3, p4, p5 = _registry_patches(config_dir, registry_file, mcp_file)
            with p1, p2, p3, p4, p5, patch(
                "quiver.mcp.cli.MCP_CONFIG_MAP", mcp_map
            ), patch("quiver.mcp.cli.get_mcp_tools") as mock_tools:
                mock_tools.return_value = {"opencode": mcp_map["opencode"], "claude": mcp_map["claude"]}
                findings = discover_mcp_servers()
                notion = [f for f in findings if f.name == "notion"]
                self.assertEqual(len(notion), 1)
                self.assertEqual(notion[0].status, "new")
                self.assertIn("opencode", notion[0].tools)

    def test_apply_writes_to_mcp_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            config_dir.mkdir(parents=True)
            registry_file = config_dir / "tools.json"
            mcp_file = config_dir / "mcp.json"
            registry_file.write_text(json.dumps({"opencode": {"aliases": ["oc"]}}))
            mcp_file.write_text(json.dumps({"mcpServers": {}}, indent=2))

            opencode_cfg = tmp_path / ".config" / "opencode"
            opencode_cfg.mkdir(parents=True)
            opencode_json = opencode_cfg / "opencode.json"
            opencode_json.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "linear": {
                                "command": ["npx", "mcp-remote", "https://example.com"],
                                "enabled": True,
                                "type": "local",
                            }
                        }
                    }
                )
            )

            mcp_map = {
                "opencode": {
                    "path": opencode_json,
                    "key": "mcp",
                    "label": "opencode",
                    "format": "opencode",
                },
            }

            p1, p2, p3, p4, p5 = _registry_patches(config_dir, registry_file, mcp_file)
            with p1, p2, p3, p4, p5, patch(
                "quiver.mcp.cli.MCP_CONFIG_MAP", mcp_map
            ), patch("quiver.mcp.cli.get_mcp_tools") as mock_tools:
                mock_tools.return_value = {"opencode": mcp_map["opencode"]}
                findings = discover_mcp_servers()
                added = apply_mcp_findings(findings)
                self.assertIn("linear", added)
                data = json.loads(mcp_file.read_text())
                self.assertIn("linear", data["mcpServers"])


if __name__ == "__main__":
    unittest.main()
