"""On-disk paths for quiver user data.

Everything quiver owns lives under one root, ``~/.quiver``. That root holds two
kinds of thing and they are separated so the whole directory can be a git repo:

  authored     AGENTS.md, .linkignore, skills/, hooks/, config/   worth versioning
  regenerable  cache/, backups/              gitignored
  credentials  secrets/                      gitignored, mode 700

The root sits in ``$HOME`` rather than ``$HOME/.config`` on purpose. Quiver's
job is managing coding harnesses, and 24 of the 28 harness config directories
on a typical setup are ``$HOME/.<tool>``. Matching the neighbours it manages
beats matching the XDG spec here.

``~/.config/swe`` was the old root. ``legacy_config_dir`` is kept so a machine
that still has one can be migrated by ``swe init --migrate``.
"""

import os
from pathlib import Path

from quiver import DATA_DIR_NAME

# The layout, named once. The ``*_for`` helpers take an explicit home so tests
# can build a throwaway tree; the module-level constants are the same thing
# resolved against the real home, which is what runtime code should import.
QUIVER_DIRNAME = f".{DATA_DIR_NAME}"

AGENTS_BASENAME = "AGENTS.md"
LINKIGNORE_BASENAME = ".linkignore"
SKILLS_SUBDIR = "skills"
HOOKS_SUBDIR = "hooks"
PLUGINS_SUBDIR = "plugins"
CONFIG_SUBDIR = "config"
DATA_SUBDIR = "data"
SEARCH_SUBDIR = "search"
SEARCH_INDEX_BASENAME = "search.sqlite"
GITHUB_SUBDIR = "github"
GITHUB_CATALOG_BASENAME = "catalog.sqlite"
CACHE_SUBDIR = "cache"
MCP_SUBDIR = "mcp"
MCP_SERVERS_SUBDIR = "servers"
COMPLETIONS_SUBDIR = "completions"
REPORTS_SUBDIR = "reports"
BACKUPS_SUBDIR = "backups"
SECRETS_SUBDIR = "secrets"
# One shell-style `export NAME=value` file per store, each in its own
# folder under secrets/ so the two can never be confused or merged.
SECRETS_BASENAME = ".api_keys"
PROVIDER_SECRETS_SUBDIR = "providers"
MCP_SECRETS_SUBDIR = "mcp"


def quiver_dir_for(home: Path | None = None) -> Path:
    return (home or Path.home()) / QUIVER_DIRNAME


def agents_file_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / AGENTS_BASENAME


def linkignore_file_for(home: Path | None = None) -> Path:
    """Paths ``swe init`` must leave alone, one gitignore-style pattern per line."""
    return quiver_dir_for(home) / LINKIGNORE_BASENAME


def skills_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / SKILLS_SUBDIR


def hooks_dir_for(home: Path | None = None) -> Path:
    """Harness hook scripts, one subdirectory per harness registry name."""
    return quiver_dir_for(home) / HOOKS_SUBDIR


def plugins_dir_for(home: Path | None = None) -> Path:
    """The user's own plugin marketplaces, one subdirectory per marketplace."""
    return quiver_dir_for(home) / PLUGINS_SUBDIR


def config_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / CONFIG_SUBDIR


def cache_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / CACHE_SUBDIR


def data_dir_for(home: Path | None = None) -> Path:
    """``~/.quiver/data`` — app-owned stores (ledger, catalog, search index)."""
    return quiver_dir_for(home) / DATA_SUBDIR


def search_index_for(home: Path | None = None) -> Path:
    """The app's FTS5 transcript-search index. The Python side only reads it."""
    return data_dir_for(home) / SEARCH_SUBDIR / SEARCH_INDEX_BASENAME


def github_catalog_for(home: Path | None = None) -> Path:
    """The synced repo catalog `swe gh sync` fills and `repo search` reads."""
    return data_dir_for(home) / GITHUB_SUBDIR / GITHUB_CATALOG_BASENAME


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


def github_file_for(home: Path | None = None) -> Path:
    """The pinned-repo store behind `swe gh` and the app's GitHub section."""
    return config_dir_for(home) / "github.json"


def backups_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / BACKUPS_SUBDIR


def secrets_dir_for(home: Path | None = None) -> Path:
    """Credential values, mode 700 and gitignored. Nothing else goes here."""
    return quiver_dir_for(home) / SECRETS_SUBDIR


def provider_keys_file_for(home: Path | None = None) -> Path:
    """LLM provider API keys, read by `swe providers` and the app's key editor."""
    return secrets_dir_for(home) / PROVIDER_SECRETS_SUBDIR / SECRETS_BASENAME


def mcp_secrets_file_for(home: Path | None = None) -> Path:
    """The values behind the ``${NAME}`` references in mcp.json."""
    return secrets_dir_for(home) / MCP_SECRETS_SUBDIR / SECRETS_BASENAME


def legacy_provider_keys_for(home: Path | None = None) -> Path:
    """Where provider keys lived before secrets/providers/: ``~/.api_keys``.

    After `swe init` moves the file it leaves a symlink here, because shell
    profiles source this path. It may also be a directory of per-provider
    files, which `swe init` leaves in place.
    """
    return (home or Path.home()) / SECRETS_BASENAME


def legacy_mcp_secrets_file_for(home: Path | None = None) -> Path:
    """Where MCP secrets lived before secrets/mcp/."""
    return secrets_dir_for(home) / SECRETS_BASENAME


def _current_or_legacy(current: Path, legacy: Path) -> Path:
    # A machine `swe init` has not migrated yet keeps reading its old file.
    # Once the new one exists, or when neither does, the new one wins.
    if current.exists() or current.is_symlink():
        return current
    if legacy.exists():
        return legacy
    return current


def provider_keys_path_for(home: Path | None = None) -> Path:
    """The provider key store to read: the new file, else ``~/.api_keys``."""
    return _current_or_legacy(provider_keys_file_for(home), legacy_provider_keys_for(home))


def mcp_secrets_path_for(home: Path | None = None) -> Path:
    """The MCP secrets file to read: the new one, else secrets/.api_keys."""
    return _current_or_legacy(mcp_secrets_file_for(home), legacy_mcp_secrets_file_for(home))


QUIVER_DIR = quiver_dir_for()
AGENTS_FILE = agents_file_for()
LINKIGNORE_FILE = linkignore_file_for()
SKILLS_DIR = skills_dir_for()
HOOKS_DIR = hooks_dir_for()
BACKUPS_DIR = backups_dir_for()

CONFIG_DIR = config_dir_for()
CACHE_DIR = cache_dir_for()
MCP_DIR = mcp_dir_for()
MCP_SERVERS_DIR = mcp_servers_dir_for()
COMPLETION_DIR = QUIVER_DIR / COMPLETIONS_SUBDIR
REPORTS_DIR = QUIVER_DIR / REPORTS_SUBDIR

# Authored state: hand-edited or built up over time, worth keeping.
#
# HARNESS_FILE is the one file: what each harness is (command, description,
# aliases, tags) and what state it is in (active/starred/archived), keyed by
# name. It replaced three separate files that used to require a join by name
# to answer "show me everything about claude" — TOOLS_FILE, STARS_FILE and
# ARCHIVE_FILE below are kept defined only as the source `harness/registry.py`
# lazily migrates from on a fresh-enough install that has not been touched
# since; nothing should open them directly.
HARNESS_FILE = CONFIG_DIR / "harness.json"
TOOLS_FILE = CONFIG_DIR / "tools.json"          # legacy: migration source only
STARS_FILE = CONFIG_DIR / "stars.json"          # legacy: migration source only
ARCHIVE_FILE = CONFIG_DIR / "archived.json"     # legacy: migration source only
MCP_SOURCE_FILE = mcp_source_file_for()
SKILL_CATALOGS_FILE = CONFIG_DIR / "skill_catalogs.json"
SKILL_LINKS_FILE = CONFIG_DIR / "skill_links.json"
PROVIDERS_REGISTRY_FILE = CONFIG_DIR / "providers.json"
GITHUB_FILE = CONFIG_DIR / "github.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Regenerable state: delete any of these and quiver rebuilds them.
SESSION_CACHE_FILE = CACHE_DIR / "session_cache.json"
RATE_LIMITS_CACHE_FILE = CACHE_DIR / "rate_limits_cache.json"
SESSION_COUNTS_CACHE_FILE = CACHE_DIR / "session_counts.json"
# `swe gh repo search` owner scope (login + orgs), short TTL per login.
GITHUB_OWNERS_CACHE = CACHE_DIR / "github_owners.json"
# The synced catalog behind `swe gh sync`; only a resync rewrites it.
GITHUB_CATALOG = github_catalog_for()

# Pre-0.2.7 root. Only referenced by the migration path.
LEGACY_CONFIG_DIR = Path.home() / ".config" / "swe"

GITIGNORE_BODY = """# Regenerable. Everything else here is worth versioning.
cache/
backups/

# The app's usage ledger and its daily backups: one row per model response,
# with project paths, session ids and account homes. Personal, ~30 MB, and
# rewritten on every scan, so never versioned.
data/ledger/

# Devin cloud sessions the app syncs from the API: titles, links, repos and
# message times of your own sessions. Personal, and rewritten on each sync.
data/devin-cloud/

# The app's full-text search index: message text from every local session.
# Personal, and rebuilt by the app whenever it is missing.
data/search/

# Credential values: provider API keys in secrets/providers/ and the values
# behind mcp.json's ${NAME} references in secrets/mcp/. Keeping them out is
# what makes the rest of the hub safe to commit.
secrets/

# MCP server checkouts: ~2.7 GB of nested git repos and build output.
# mcp.json records what they are, so this can be rebuilt from a clone.
mcp/servers/
"""

# Written into secrets/ itself, so the folder stays out of git even when the
# root .gitignore above is missing the line or excepts a path under it.
SECRETS_GITIGNORE_BODY = """# Credential values. Never versioned, whatever ../.gitignore says.
*
"""


def legacy_config_dir() -> Path | None:
    """The old ~/.config/swe root, if this machine still has one."""
    return LEGACY_CONFIG_DIR if LEGACY_CONFIG_DIR.is_dir() else None


def expand_user(value: "str | Path") -> "Path":
    """``Path(value).expanduser()`` minus the ``~user`` ``RuntimeError``.

    ``Path.expanduser`` raises ``RuntimeError`` when the named account does
    not exist, so a ``~missing-user/...`` recorded in a registry root,
    catalog entry, session record or flag value took the whole command down
    with a traceback. ``os.path.expanduser`` leaves an unresolvable
    ``~user`` literal instead — the path then probes as missing, which is
    the answer every caller was built for. ``~`` and ``~/`` still expand,
    and a ``~user`` that does exist still resolves to its home.
    """
    return Path(os.path.expanduser(str(value)))


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

    # Write through a symlink instead of replacing it: os.replace on a
    # linked config (dotfiles layout) would silently drop the link and
    # split this file from the target everyone else still reads.
    if path.is_symlink():
        path = path.resolve()

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
