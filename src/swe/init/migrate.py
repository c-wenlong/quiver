"""Move a pre-0.2.7 ``~/.config/swe`` install into ``~/.swe``.

The old root mixed three lifetimes in one flat directory: authored state worth
versioning, regenerable caches, and generated completion scripts. The new root
separates them so ``~/.swe`` can be a git repo. This module only moves
files; deciding whether to run is ``cmd_init``'s job.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from swe import paths

# Where each file in the old flat directory belongs now.
CACHE_FILES = (
    "session_cache.json",
    "rate_limits_cache.json",
    "claude_usage_cache.json",
)
CONFIG_FILES = (
    "harness.json",
    # tools.json / stars.json / archived.json are pre-consolidation names
    # (see swe.harness.registry): a machine still on the old
    # ~/.config/swe root predates harness.json, so it can only ever have
    # these. Recognised here so they land in config/ explicitly rather than
    # through the "unknown, so authored" fallback below, and so that
    # harness/registry.py's lazy migration finds them there afterwards.
    "tools.json",
    "stars.json",
    "archived.json",
    "skill_catalogs.json",
    "skill_links.json",
    "providers.json",
    "config.json",
)
# Directories that keep their name, one level up from config/.
PASSTHROUGH_DIRS = ("completions", "reports")

# Credential-shaped names move under secrets/ rather than config/ — the
# sealed secrets/.gitignore keeps them out of a `git add .`, while config/
# is exactly the tree a user versions.
def _is_credential_file(name: str) -> bool:
    return name in (".env", ".envrc", ".netrc") or name.startswith(".env.")

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
    quiver = paths.swe_dir_for(home)

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
        elif name == "mcp.json":
            # The hub lives at ~/.swe/mcp.json, not in config/: every MCP
            # reader resolves the root path, so landing it under config/
            # stranded every server until the user found the file.
            plan.moved.append((entry, quiver / name))
        elif _is_credential_file(name):
            # A stray .env landing in config/ would be one `git add .` from
            # a committed key: config/ is the versioned tree, so credential
            # files go under the sealed secrets/ instead.
            plan.moved.append((entry, paths.secrets_dir_for(home) / name))
        else:
            # Anything unrecognised — dotfiles included — is authored until
            # proven otherwise, so it moves to config/ rather than being
            # dropped on the floor (a user's .env or notes are data).
            plan.moved.append((entry, config_dir / name))
    return plan


def apply_migration(plan: MigrationPlan, remove_source: bool = True) -> MigrationPlan:
    """Carry out a plan. Existing destinations are left alone, not clobbered."""
    import os

    home = plan.source.parent.parent
    secrets_dir = paths.secrets_dir_for(home)
    secrets_blocked = secrets_dir.is_symlink() or (
        os.path.lexists(secrets_dir) and not secrets_dir.is_dir()
    )
    sealed = False
    for src, dest in list(plan.moved):
        if os.path.lexists(dest):
            # lexists, not exists: a dangling symlink at the destination is
            # invisible to exists() and shutil.move would replace the link
            # with a standalone file, cutting it off from its real target.
            plan.moved.remove((src, dest))
            plan.skipped.append(dest)
            continue
        if dest.parent == secrets_dir:
            if secrets_blocked:
                # Moving through a linked or non-dir secrets/ lands keys in
                # an arbitrary place the seal never protects. Keep the file
                # at its legacy source and report the destination.
                plan.moved.remove((src, dest))
                plan.skipped.append(dest)
                continue
            if not sealed:
                # The catch-all .gitignore must be in place before the key
                # file lands, not after — a crash between the two would
                # leave a credential sitting in an unsealed directory.
                seal_secrets_dir(home)
                sealed = True
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            target = os.readlink(src)
            if not os.path.isabs(target):
                # Moving a relative link re-anchors it at the destination:
                # ../../dotfiles/mcp.json resolved under the legacy root
                # would point outside the home from the hub. Point the new
                # link at where the old one actually reached instead.
                os.symlink(os.path.abspath(src.parent / target), dest)
                src.unlink()
                continue
        shutil.move(str(src), str(dest))

    for entry in plan.dropped:
        if entry.is_symlink() or not entry.is_dir():
            # A symlinked directory is a link, not a tree: rmtree on it
            # raises mid-apply and strands the migration half-done.
            entry.unlink(missing_ok=True)
        else:
            shutil.rmtree(entry, ignore_errors=True)

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
    target = paths.swe_dir_for(home) / ".gitignore"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(paths.GITIGNORE_BODY, encoding="utf-8")
    return target


def move_secrets(home: Path | None = None, *, apply: bool = True) -> list[str]:
    """Move both credential stores into their own folders under secrets/.

    Provider keys go from ``~/.api_keys`` to ``secrets/providers/.api_keys``
    and MCP secrets from ``secrets/.api_keys`` to ``secrets/mcp/.api_keys``.
    Each leaves a symlink behind: shell profiles source ``~/.api_keys``, and
    a machine can run an older swe beside this one (a Nix-pinned build, say)
    whose `swe mcp sync` still reads ``secrets/.api_keys``. Without the link
    that sync would write every ``${NAME}`` into harness configs unresolved.

    A move happens only when the old path is a regular file and the new one
    does not exist, so a second run, a directory of per-provider files, or
    a machine holding both copies is left alone and reported instead. The
    readers fall back to the old paths, so skipping a move breaks nothing.
    Before anything lands in secrets/, the folder is sealed (see
    ``seal_secrets_dir``). Returns one note per change made (or, with
    ``apply=False``, planned).
    """
    import os

    notes: list[str] = []
    moves = (
        (paths.legacy_provider_keys_for(home), paths.provider_keys_file_for(home),
         "~/.api_keys", "secrets/providers/.api_keys"),
        (paths.legacy_mcp_secrets_file_for(home), paths.mcp_secrets_file_for(home),
         "secrets/.api_keys", "secrets/mcp/.api_keys"),
    )
    planned = []
    for old, new, old_label, new_label in moves:
        if old.is_symlink() or not old.is_file():
            continue
        if new.exists() or new.is_symlink():
            notes.append(f"{old_label} and {new_label} both exist, merge them by hand")
            continue
        planned.append((old, new, f"move {old_label} to {new_label}"))

    secrets_dir = paths.secrets_dir_for(home)
    secrets_blocked = secrets_dir.is_symlink() or (
        os.path.lexists(secrets_dir) and not secrets_dir.is_dir()
    )
    if secrets_blocked:
        # Path ops follow links: mkdir/chmod/writes under it would land in
        # an arbitrary external directory. The seal below reports it.
        planned = []
    if planned or secrets_blocked or secrets_dir.is_dir():
        # is_dir() follows the link, so a dangling symlink or a regular
        # file must trigger the seal call itself or the skip goes unreported.
        notes.extend(seal_secrets_dir(home, apply=apply))
    for old, new, note in planned:
        notes.append(note)
        if not apply:
            continue
        new.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(new.parent, 0o700)
        # Same volume (both under $HOME), so this is a rename: the file
        # keeps its inode and is never copied. Hand-made stores are not
        # always 600, and these hold every key, so tighten on the way in.
        shutil.move(str(old), str(new))
        os.chmod(new, 0o600)
        old.symlink_to(new)

    # A root moved by a swe that predates the link has secrets/mcp/ and no
    # old path at all. Backfill the link there; ~/.api_keys is left alone,
    # since a home that never had one gains nothing from a new file in it.
    old_mcp, new_mcp = paths.legacy_mcp_secrets_file_for(home), paths.mcp_secrets_file_for(home)
    if (
        not secrets_dir.is_symlink()
        and new_mcp.is_file()
        and not (old_mcp.exists() or old_mcp.is_symlink())
    ):
        notes.append("link secrets/.api_keys to secrets/mcp/.api_keys")
        if apply:
            old_mcp.symlink_to(new_mcp)
    return notes


def seal_secrets_dir(home: Path | None = None, *, apply: bool = True) -> list[str]:
    """Give secrets/ (mode 700) its own ``.gitignore`` that ignores everything.

    The root .gitignore is the user's to edit and `swe init` never rewrites
    it, so an older hub may lack secrets/ or carry a ``!secrets/...``
    exception. Git lets a deeper .gitignore override the root one, so this
    file keeps every key out of a `git add .` whatever the root says.
    """
    import os

    secrets_dir = paths.secrets_dir_for(home)
    if secrets_dir.is_symlink():
        # A foreign directory must never be chmod 0700'd or handed a
        # catch-all .gitignore through the link.
        return ["secrets/ is a symlink — left alone"]
    if os.path.lexists(secrets_dir) and not secrets_dir.is_dir():
        return ["secrets/ is a file, not a directory — left alone"]
    seal = secrets_dir / ".gitignore"
    if seal.is_file() and seal.read_text(encoding="utf-8", errors="replace") == paths.SECRETS_GITIGNORE_BODY:
        return []
    if apply:
        secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(secrets_dir, 0o700)
        seal.write_text(paths.SECRETS_GITIGNORE_BODY, encoding="utf-8")
    return ["seal secrets/ with its own .gitignore"]
