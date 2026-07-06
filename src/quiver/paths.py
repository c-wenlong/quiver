"""On-disk paths for quiver user configuration."""

from pathlib import Path

from quiver import CONFIG_DIR_NAME

CONFIG_DIR = Path.home() / ".config" / CONFIG_DIR_NAME
REGISTRY_FILE = CONFIG_DIR / "tools.json"
MCP_SOURCE_FILE = CONFIG_DIR / "mcp.json"
