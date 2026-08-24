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
