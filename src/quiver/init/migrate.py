"""Move a pre-0.2.7 ``~/.config/swe`` install into ``~/.quiver``.

The old root mixed three lifetimes in one flat directory: authored state worth
versioning, regenerable caches, and generated completion scripts. The new root
separates them so ``~/.quiver`` can be a git repo. This module only moves
files; deciding whether to run is ``cmd_init``'s job.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from quiver import paths

# Where each file in the old flat directory belongs now.
CACHE_FILES = (
    "session_cache.json",
    "rate_limits_cache.json",
    "claude_usage_cache.json",
)
CONFIG_FILES = (
    "harness.json",
    # tools.json / stars.json / archived.json are pre-consolidation names
    # (see quiver.harness.registry): a machine still on the old
    # ~/.config/swe root predates harness.json, so it can only ever have
    # these. Recognised here so they land in config/ explicitly rather than
    # through the "unknown, so authored" fallback below, and so that
    # harness/registry.py's lazy migration finds them there afterwards.
    "tools.json",
    "stars.json",
    "archived.json",
    "mcp.json",
    "skill_catalogs.json",
    "skill_links.json",
    "providers.json",
    "config.json",
)
# Directories that keep their name, one level up from config/.
PASSTHROUGH_DIRS = ("completions", "reports")

# Dead weight from the single-file era, deleted rather than carried across.
DROP = ("mcp.py", "mcp_formats.py", "mcp_server.py", "tests", "__pycache__", "archive")


@dataclass
class MigrationPlan:
    source: Path
    moved: list[tuple[Path, Path]] = field(default_factory=list)
    dropped: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.moved or self.dropped)


def plan_migration(home: Path | None = None) -> MigrationPlan | None:
    """Work out what would move. Returns None when there is no old root."""
    home = home or Path.home()
    source = home / ".config" / "swe"
    if not source.is_dir():
        return None

    plan = MigrationPlan(source=source)
    config_dir = paths.config_dir_for(home)
    cache_dir = paths.cache_dir_for(home)
    quiver = paths.quiver_dir_for(home)

    for entry in sorted(source.iterdir()):
        name = entry.name
        if name in DROP:
            plan.dropped.append(entry)
        elif name in CACHE_FILES:
            plan.moved.append((entry, cache_dir / name))
        elif name in CONFIG_FILES:
            plan.moved.append((entry, config_dir / name))
        elif name in PASSTHROUGH_DIRS:
            plan.moved.append((entry, quiver / name))
        elif name.startswith("."):
            plan.dropped.append(entry)  # .DS_Store and friends
        else:
            # Anything unrecognised is authored until proven otherwise, so it
            # goes to config/ rather than being dropped on the floor.
            plan.moved.append((entry, config_dir / name))
    return plan


def apply_migration(plan: MigrationPlan, remove_source: bool = True) -> MigrationPlan:
    """Carry out a plan. Existing destinations are left alone, not clobbered."""
    for src, dest in list(plan.moved):
        if dest.exists():
            plan.moved.remove((src, dest))
            plan.skipped.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))

    for entry in plan.dropped:
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)

    if remove_source and plan.source.is_dir():
        leftovers = list(plan.source.iterdir())
        if not leftovers:
            plan.source.rmdir()
            parent = plan.source.parent
            # Tidy ~/.config too, but only if quiver was the only thing in it.
            if parent.name == ".config" and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    return plan


def write_gitignore(home: Path | None = None) -> Path:
    """Make the root safe to `git init` without committing 300 KB of cache."""
    target = paths.quiver_dir_for(home) / ".gitignore"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(paths.GITIGNORE_BODY, encoding="utf-8")
    return target


def move_secrets(home: Path | None = None, *, apply: bool = True) -> list[str]:
    """Move both credential stores into their own folders under secrets/.

    Provider keys go from ``~/.api_keys`` to ``secrets/providers/.api_keys``
    and leave a symlink behind, since shell profiles source the old path.
    MCP secrets go from ``secrets/.api_keys`` to ``secrets/mcp/.api_keys``;
    only quiver reads that file, so nothing is left behind.

    A move happens only when the old path is a regular file and the new one
    does not exist, so a second run, a directory of per-provider files, or
    a machine holding both copies is left alone and reported instead. The
    readers fall back to the old paths, so skipping a move breaks nothing.
    Returns one note per move made (or, with ``apply=False``, planned).
    """
    import os

    notes: list[str] = []
    moves = (
        (paths.legacy_provider_keys_for(home), paths.provider_keys_file_for(home),
         True, "~/.api_keys", "secrets/providers/.api_keys"),
        (paths.legacy_mcp_secrets_file_for(home), paths.mcp_secrets_file_for(home),
         False, "secrets/.api_keys", "secrets/mcp/.api_keys"),
    )
    for old, new, link_back, old_label, new_label in moves:
        if old.is_symlink() or not old.is_file():
            continue
        if new.exists() or new.is_symlink():
            notes.append(f"{old_label} and {new_label} both exist, merge them by hand")
            continue
        # ~/.api_keys sits outside ~/.quiver. Moving it in must not
        # put provider keys into a tree the user versions, so a root whose
        # .gitignore predates secrets/ keeps its keys where they are.
        # A plan-only run on a fresh root has no .gitignore yet; the real
        # run writes GITIGNORE_BODY, which lists secrets/, before moving.
        will_be_ignored = not apply and not (paths.quiver_dir_for(home) / ".gitignore").exists()
        if link_back and not (will_be_ignored or secrets_ignored(home)):
            notes.append(f"{old_label} stays put until ~/.quiver/.gitignore lists secrets/")
            continue
        notes.append(f"move {old_label} to {new_label}")
        if not apply:
            continue
        for folder in (new.parent.parent, new.parent):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(folder, 0o700)
        # Same volume (both under $HOME), so this is a rename: the file
        # keeps its inode and is never copied. Hand-made stores are not
        # always 600, and these hold every key, so tighten on the way in.
        shutil.move(str(old), str(new))
        os.chmod(new, 0o600)
        if link_back:
            old.symlink_to(new)
    return notes


_SECRETS_IGNORE_LINES = {"secrets", "secrets/", "/secrets", "/secrets/",
                         "secrets/*", "/secrets/*", "secrets/**", "/secrets/**"}


def secrets_ignored(home: Path | None = None) -> bool:
    """Whether ~/.quiver keeps secrets/ out of git.

    True when its .gitignore lists secrets/ outright, or when the root is a
    git checkout and git itself ignores a file there (a global excludes
    file, or a pattern this check does not spell out).
    """
    import subprocess

    root = paths.quiver_dir_for(home)
    try:
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        lines = []
    if any(line.strip() in _SECRETS_IGNORE_LINES for line in lines):
        return True
    if not (root / ".git").exists():
        return False
    probe = paths.provider_keys_file_for(home).relative_to(root)
    try:
        result = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(probe)],
                                capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
