"""On-disk paths for quiver user configuration."""

from pathlib import Path

from quiver import CONFIG_DIR_NAME

CONFIG_DIR = Path.home() / ".config" / CONFIG_DIR_NAME
REGISTRY_FILE = CONFIG_DIR / "tools.json"
STARS_FILE = CONFIG_DIR / "stars.json"
MCP_SOURCE_FILE = CONFIG_DIR / "mcp.json"
SKILL_CATALOGS_FILE = CONFIG_DIR / "skill_catalogs.json"
SKILL_LINKS_FILE = CONFIG_DIR / "skill_links.json"
SESSION_CACHE_FILE = CONFIG_DIR / "session_cache.json"
COMPLETION_DIR = CONFIG_DIR / "completions"
PROVIDERS_REGISTRY_FILE = CONFIG_DIR / "providers.json"
RATE_LIMITS_CACHE_FILE = CONFIG_DIR / "rate_limits_cache.json"

# Documented convention for the plain-text API-key directory. The actual
# runtime path can be overridden via `swe providers --api-keys-dir=DIR`.
DEFAULT_API_KEYS_DIRNAME = ".api_keys"
DEFAULT_API_KEYS_DIR = Path.home() / DEFAULT_API_KEYS_DIRNAME
PROVIDERS_REGISTRY_FILE = CONFIG_DIR / "providers.json"
