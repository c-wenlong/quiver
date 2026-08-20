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
MCP_SUBDIR = "mcp"
MCP_SERVERS_SUBDIR = "servers"
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


def mcp_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / MCP_SUBDIR


def mcp_servers_dir_for(home: Path | None = None) -> Path:
    """Where the MCP server implementations themselves live.

    These are checked-out repositories and build output, ~2.7 GB across 21
    nested git repos, so the directory is gitignored even though it sits in
    the versioned half of the tree. Harness configs point at absolute paths
    inside it, which is why moving it means rewriting those configs.
    """
    return mcp_dir_for(home) / MCP_SERVERS_SUBDIR


def mcp_source_file_for(home: Path | None = None) -> Path:
    """The registry of which servers exist and where they are configured."""
    return quiver_dir_for(home) / "mcp.json"


def backups_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / BACKUPS_SUBDIR


QUIVER_DIR = quiver_dir_for()
AGENTS_FILE = agents_file_for()
SKILLS_DIR = skills_dir_for()
BACKUPS_DIR = backups_dir_for()

CONFIG_DIR = config_dir_for()
CACHE_DIR = cache_dir_for()
MCP_DIR = mcp_dir_for()
MCP_SERVERS_DIR = mcp_servers_dir_for()
COMPLETION_DIR = QUIVER_DIR / COMPLETIONS_SUBDIR
REPORTS_DIR = QUIVER_DIR / REPORTS_SUBDIR

# Authored state: hand-edited or built up over time, worth keeping.
REGISTRY_FILE = CONFIG_DIR / "tools.json"
STARS_FILE = CONFIG_DIR / "stars.json"
ARCHIVE_FILE = CONFIG_DIR / "archived.json"
MCP_SOURCE_FILE = mcp_source_file_for()
SKILL_CATALOGS_FILE = CONFIG_DIR / "skill_catalogs.json"
SKILL_LINKS_FILE = CONFIG_DIR / "skill_links.json"
PROVIDERS_REGISTRY_FILE = CONFIG_DIR / "providers.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Regenerable state: delete any of these and quiver rebuilds them.
SESSION_CACHE_FILE = CACHE_DIR / "session_cache.json"
RATE_LIMITS_CACHE_FILE = CACHE_DIR / "rate_limits_cache.json"
SESSION_COUNTS_CACHE_FILE = CACHE_DIR / "session_counts.json"

# Documented convention for the plain-text API-key directory. The actual
# runtime path can be overridden via `swe providers --api-keys-dir=DIR`.
DEFAULT_API_KEYS_DIRNAME = ".api_keys"
DEFAULT_API_KEYS_DIR = Path.home() / DEFAULT_API_KEYS_DIRNAME

# Pre-0.2.7 root. Only referenced by the migration path.
LEGACY_CONFIG_DIR = Path.home() / ".config" / "swe"

GITIGNORE_BODY = """# Regenerable. Everything else here is worth versioning.
cache/
backups/

# MCP server checkouts: ~2.7 GB of nested git repos and build output.
# mcp.json records what they are, so this can be rebuilt from a clone.
mcp/servers/
"""


def legacy_config_dir() -> Path | None:
    """The old ~/.config/swe root, if this machine still has one."""
    return LEGACY_CONFIG_DIR if LEGACY_CONFIG_DIR.is_dir() else None


def atomic_write_text(path: "Path", text: str, *, private: bool = False) -> None:
    """Write ``text`` to ``path`` atomically, preserving its permissions.

    The tmp-file-then-rename pattern this replaces created the temp file
    fresh under the process umask, so renaming it over a 0600 file left
    the result 0644. Harness MCP configs hold resolved API tokens and are
    0600 for that reason, and `swe mcp sync` was quietly widening them to
    world-readable on every run.

    An existing file's mode is carried over. ``private=True`` floors a new
    file at 0600, for anything that may hold credentials.
    """
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o600 if private else None

    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        # mkstemp is 0600; widen only to whatever the file already was.
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_tree(path: "Path", home: "Path | None" = None) -> "Path":
    """Copy a file or directory into ~/.quiver/backups before it is replaced.

    Hardened against three ways the previous version failed:

    * ``relative_to(home)`` raised ValueError for anything outside $HOME,
      aborting mid-run. Paths outside home now fall back to a flattened
      absolute name.
    * ``copytree`` defaults to following symlinks, so one broken link
      anywhere in the tree raised and aborted the whole operation. Links
      are copied as links, which is also what you want when restoring.
    * Two backups of the same path in one second collided and raised
      FileExistsError. A counter disambiguates.
    """
    import shutil
    from datetime import datetime

    home = home or Path.home()
    dest_dir = backups_dir_for(home)
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        flat = str(path.relative_to(home)).replace("/", "_")
    except ValueError:
        flat = str(path).lstrip("/").replace("/", "_")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{flat}.{stamp}"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{flat}.{stamp}.{n}"
        n += 1

    if path.is_dir() and not path.is_symlink():
        shutil.copytree(path, dest, symlinks=True)
    else:
        shutil.copy2(path, dest, follow_symlinks=False)
    return dest
