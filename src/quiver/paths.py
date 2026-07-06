"""On-disk paths for quiver user configuration."""

from pathlib import Path

from quiver import CONFIG_DIR_NAME

CONFIG_DIR = Path.home() / ".config" / CONFIG_DIR_NAME
REGISTRY_FILE = CONFIG_DIR / "tools.json"
MCP_SOURCE_FILE = CONFIG_DIR / "mcp.json"
SKILL_CATALOGS_FILE = CONFIG_DIR / "skill_catalogs.json"
SKILL_LINKS_FILE = CONFIG_DIR / "skill_links.json"
