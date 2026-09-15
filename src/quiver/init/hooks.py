"""Harness hook scripts: ~/.quiver/hooks/<harness>/<script> into each harness.

Hooks do not share the shape of instructions or skills. One AGENTS.md can be
linked to nine destinations because every harness reads plain Markdown, but a
Claude Code Stop hook and a Codex hook speak different JSON contracts, and most
harnesses have no hooks at all. So there is no shared hook: each harness gets
its own subdirectory, named by its harness.json key, and nothing in it is ever
linked anywhere else.

Each script is linked on its own, never the whole directory. A harness hooks
directory also holds scripts other installers write and rewrite (herdr keeps
one in ~/.claude/hooks), and replacing the directory with a link would pull
those into ~/.quiver or break them.

Only the file is linked. Declaring the hook, in ~/.claude/settings.json or
~/.codex/hooks.json, stays with whatever manages that file: the event, matcher
and timeout are harness-specific, and on a Nix machine the settings file is a
read-only link into the store that swe must not fight.

Opt-in by presence: a harness gets hooks only once ~/.quiver/hooks/<harness>/
exists. Where they go comes from ``capabilities.hooks.root`` in harness.json,
then ``HOOK_FALLBACK`` for a harness the registry does not describe.
"""

from __future__ import annotations

import filecmp
from pathlib import Path

from quiver import paths as _paths
from quiver.init.layout import (
    IGNORED_DETAIL,
    LinkStatus,
    hooks_dir,
    is_linkignored,
    load_linkignore,
    load_registry,  # re-exported: lives in layout.py, which hooks imports
)

# Where a harness keeps hook scripts, relative to home, for a harness whose
# harness.json entry records no capabilities.hooks. Both are the directory
# the harness's own hook documentation writes scripts into. Keyed by registry
# name (droid, not its ~/.factory home), like the plugin fallback.
HOOK_FALLBACK: dict[str, Path] = {
    "claude": Path(".claude/hooks"),
    "droid": Path(".factory/hooks"),
}

# Entries in a harness hooks directory that are never hooks.
_SKIP_NAMES = frozenset({"__pycache__", "node_modules"})

NO_ROOT_DETAIL = "no hooks root, set capabilities.hooks.root in harness.json"
UNSUPPORTED_DETAIL = "harness.json says this harness has no hooks"
INSIDE_QUIVER_DETAIL = "hooks root is inside ~/.quiver, would link a script to itself"


def _expand_root(root: str, home: Path) -> Path:
    raw = str(root)
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def hook_root(registry: dict, name: str, home: Path) -> tuple[Path | None, str]:
    """Where ``name`` keeps hook scripts, or (None, why not).

    harness.json wins when it says anything: ``supported: false`` turns hooks
    off even for a harness in the fallback table, and a ``root`` overrides the
    table. A supported entry with no root falls through to the table.
    """
    entry = registry.get(name)
    caps = ((entry if isinstance(entry, dict) else {}).get("capabilities") or {}).get("hooks")
    if isinstance(caps, dict):
        if "supported" in caps and not caps["supported"]:
            return None, UNSUPPORTED_DETAIL
        if caps.get("root"):
            return _expand_root(caps["root"], home), ""
    fallback = HOOK_FALLBACK.get(name)
    if fallback is not None:
        return home / fallback, ""
    return None, NO_ROOT_DETAIL


def hook_sources(harness_dir: Path) -> list[Path]:
    """The scripts in one ~/.quiver/hooks/<harness>/, top level only.

    A subdirectory counts as one entry and is linked whole, so a hook with
    helper modules can keep them beside it. Dotfiles and build litter are
    skipped: .DS_Store has no business in ~/.claude/hooks.
    """
    return sorted(
        entry for entry in harness_dir.iterdir()
        if not entry.name.startswith(".") and entry.name not in _SKIP_NAMES
    )


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        return a.is_file() and b.is_file() and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _installed(root: Path, home: Path) -> bool:
    """Whether the harness that owns ``root`` is on this machine.

    The root itself counts, and so does its parent (``~/.claude`` for
    ``~/.claude/hooks``), since a hooks directory is often absent until the
    first hook. Home is not a parent that counts: a root directly under it
    says nothing about any harness, so it must exist already.
    """
    if root.is_dir():
        return True
    return root.parent != home and root.parent.is_dir()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def classify_hook(label: str, dest: Path, source: Path, home: Path) -> LinkStatus:
    """Classify one hook destination without touching the filesystem.

    Differs from ``layout.inspect`` in two ways. The harness counts as
    installed when its config directory exists, since the hooks directory
    itself is often absent until the first hook. And a plain file with the
    same bytes as the source is ``absorb``, not left alone: a copy where the
    link should be is exactly how edits end up in an unversioned file, and
    replacing it loses nothing.
    """
    if not _installed(dest.parent, home):
        return LinkStatus(label, dest, "skipped", "harness not installed", source)

    if dest.is_symlink():
        try:
            current = Path(dest.readlink())
        except OSError:
            return LinkStatus(label, dest, "relink", "unreadable symlink", source)
        try:
            lands = dest.resolve() == source.resolve()
        except OSError:
            lands = False
        if current == source or lands:
            return LinkStatus(label, dest, "linked", "", source)
        return LinkStatus(label, dest, "relink", f"points at {current}", source)

    if dest.exists():
        try:
            itself = dest.samefile(source)
        except OSError:
            itself = False
        if itself:
            # The same file reached another way (a hard link, a symlinked
            # parent). Replacing it would delete the source.
            return LinkStatus(label, dest, "skipped", INSIDE_QUIVER_DETAIL, source)
        if _same_bytes(dest, source):
            return LinkStatus(label, dest, "absorb", "identical copy", source)
        kind = "directory" if dest.is_dir() else "file"
        return LinkStatus(label, dest, "conflict", f"real {kind}, needs --force", source)

    return LinkStatus(label, dest, "create", "", source)


def plan_hooks(
    home: Path | None = None,
    patterns: list[str] | None = None,
    registry: dict | None = None,
) -> list[LinkStatus]:
    """One status per hook script under ~/.quiver/hooks/.

    ``.linkignore`` patterns match the destination, the same as for
    instructions and skills, so ``.claude/hooks/prose-guard.py`` leaves that
    one script alone and ``.claude/hooks`` leaves them all.
    """
    home = home or Path.home()
    if patterns is None:
        patterns = load_linkignore(home)
    if registry is None:
        registry = load_registry(home)

    root_dir = hooks_dir(home)
    if not root_dir.is_dir():
        return []

    statuses: list[LinkStatus] = []
    for harness_dir in sorted(root_dir.iterdir()):
        name = harness_dir.name
        if not harness_dir.is_dir() or name.startswith(".") or name in _SKIP_NAMES:
            continue
        root, why = hook_root(registry, name, home)
        for source in hook_sources(harness_dir):
            if root is None:
                # Nowhere to put it: report the source so the user can see
                # which scripts are waiting on a harness.json entry.
                statuses.append(LinkStatus(name, source, "skipped", why, source))
                continue
            if _inside(root, _paths.quiver_dir_for(home)):
                # A root inside ~/.quiver would make the destination the
                # source itself: "absorbing" it deletes the only copy.
                statuses.append(LinkStatus(name, source, "skipped", INSIDE_QUIVER_DETAIL, source))
                continue
            dest = root / source.name
            if is_linkignored(dest, home, patterns):
                statuses.append(LinkStatus(name, dest, "ignored", IGNORED_DETAIL, source))
            else:
                statuses.append(classify_hook(name, dest, source, home))
    return statuses
