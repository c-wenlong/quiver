"""On-disk paths for quiver user data.

Everything quiver owns lives under one root, ``~/.quiver``. That root holds two
kinds of thing and they are separated so the whole directory can be a git repo:

  authored     AGENTS.md, skills/, config/   worth versioning
  regenerable  cache/, backups/              gitignored

The root sits in ``$HOME`` rather than ``$HOME/.config`` on purpose. Quiver's
job is managing coding harnesses, and 24 of the 28 harness config directories
on a typical setup are ``$HOME/.<tool>``. Matching the neighbours it manages
beats matching the XDG spec here.

``~/.config/swe`` was the old root. ``legacy_config_dir`` is kept so a machine
that still has one can be migrated by ``swe init --migrate``.
"""

from pathlib import Path

from quiver import DATA_DIR_NAME

# The layout, named once. The ``*_for`` helpers take an explicit home so tests
# can build a throwaway tree; the module-level constants are the same thing
# resolved against the real home, which is what runtime code should import.
QUIVER_DIRNAME = f".{DATA_DIR_NAME}"

AGENTS_BASENAME = "AGENTS.md"
SKILLS_SUBDIR = "skills"
CONFIG_SUBDIR = "config"
CACHE_SUBDIR = "cache"
COMPLETIONS_SUBDIR = "completions"
REPORTS_SUBDIR = "reports"
BACKUPS_SUBDIR = "backups"


def quiver_dir_for(home: Path | None = None) -> Path:
    return (home or Path.home()) / QUIVER_DIRNAME


def agents_file_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / AGENTS_BASENAME


def skills_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / SKILLS_SUBDIR


def config_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / CONFIG_SUBDIR


def cache_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / CACHE_SUBDIR


def backups_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / BACKUPS_SUBDIR


QUIVER_DIR = quiver_dir_for()
AGENTS_FILE = agents_file_for()
SKILLS_DIR = skills_dir_for()
BACKUPS_DIR = backups_dir_for()

CONFIG_DIR = config_dir_for()
CACHE_DIR = cache_dir_for()
COMPLETION_DIR = QUIVER_DIR / COMPLETIONS_SUBDIR
REPORTS_DIR = QUIVER_DIR / REPORTS_SUBDIR

# Authored state: hand-edited or built up over time, worth keeping.
REGISTRY_FILE = CONFIG_DIR / "tools.json"
STARS_FILE = CONFIG_DIR / "stars.json"
MCP_SOURCE_FILE = CONFIG_DIR / "mcp.json"
SKILL_CATALOGS_FILE = CONFIG_DIR / "skill_catalogs.json"
SKILL_LINKS_FILE = CONFIG_DIR / "skill_links.json"
PROVIDERS_REGISTRY_FILE = CONFIG_DIR / "providers.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Regenerable state: delete any of these and quiver rebuilds them.
SESSION_CACHE_FILE = CACHE_DIR / "session_cache.json"
RATE_LIMITS_CACHE_FILE = CACHE_DIR / "rate_limits_cache.json"

# Documented convention for the plain-text API-key directory. The actual
# runtime path can be overridden via `swe providers --api-keys-dir=DIR`.
DEFAULT_API_KEYS_DIRNAME = ".api_keys"
DEFAULT_API_KEYS_DIR = Path.home() / DEFAULT_API_KEYS_DIRNAME

# Pre-0.2.7 root. Only referenced by the migration path.
LEGACY_CONFIG_DIR = Path.home() / ".config" / "swe"

GITIGNORE_BODY = """# Regenerable. Everything else here is worth versioning.
cache/
backups/
"""


def legacy_config_dir() -> Path | None:
    """The old ~/.config/swe root, if this machine still has one."""
    return LEGACY_CONFIG_DIR if LEGACY_CONFIG_DIR.is_dir() else None
