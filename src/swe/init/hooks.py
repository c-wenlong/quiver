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
then ``HOOK_FALLBACK`` for a harness the registry does not describe. A hub dir
named after no harness at all (a hook bundle a plugin runs straight from the
hub) is not a harness dir: it plans as one skipped entry, never per file.
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path

from swe import paths as _paths
from swe.init.layout import (
    IGNORED_DETAIL,
    LinkStatus,
    archived_names,
    archived_override,
    command_on_path,
    hooks_dir,
    is_linkignored,
    load_linkignore,
    load_registry,  # re-exported: lives in layout.py, which hooks imports
)

# Where a harness keeps hook scripts, relative to home, for a harness whose
# harness.json entry records no capabilities.hooks. Keyed by registry name
# (droid, not its ~/.factory home), like the plugin fallback. The values
# come from the contract registry in hooks/contracts.py: cline's real hook
# dir is ~/Documents/Cline/Hooks (extensionless executables named by
# event), never ~/.cline/hooks.
def _fallback_roots() -> dict[str, Path]:
    from swe.hooks.contracts import CONTRACTS

    return {
        name: contract.root
        for name, contract in CONTRACTS.items()
        if contract.root is not None
    }


# Lazily filled by __getattr__ — see below. Annotated here so readers and
# type checkers still see the module attribute.
HOOK_FALLBACK: dict[str, Path]


def __getattr__(name: str):
    # HOOK_FALLBACK resolves on first use, never at import time: computing
    # it imports hooks.contracts, whose package __init__ pulls in
    # hooks.commands, which imports this very module back. An eager table
    # deadlocks that cycle whenever init.hooks loads first
    # (init.commands -> init.hooks -> hooks.contracts -> hooks.commands ->
    # init.hooks); first use always lands after both modules finished.
    if name == "HOOK_FALLBACK":
        global HOOK_FALLBACK
        HOOK_FALLBACK = _fallback_roots()
        return HOOK_FALLBACK
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Entries in a harness hooks directory that are never hooks.
_SKIP_NAMES = frozenset({"__pycache__", "node_modules"})

# Hub subdirectory names that are machinery, not harnesses: shared/ holds
# the cross-harness scripts the deploy step reads, _runtime/ the shim it
# generates into. hooks/shared.py owns both contents.
RESERVED_HUB_DIRS = frozenset({"shared", "_runtime"})

# A ".disabled" file inside a harness's hub dir lists script basenames
# `swe hooks disable` switched off: the plan reports them ignored so a
# later init never relinks one. Dotfile, so hook_sources skips it anyway.
DISABLED_BASENAME = ".disabled"
DISABLED_DETAIL = "disabled via swe hooks disable"

NO_ROOT_DETAIL = "no hooks root, set capabilities.hooks.root in harness.json"
UNSUPPORTED_DETAIL = "harness.json says this harness has no hooks"
INSIDE_SWE_DETAIL = "hooks root is inside ~/.quiver, would link a script to itself"
UNKNOWN_HARNESS_DETAIL = "not a known harness"


def _reserved_dirs() -> frozenset[str]:
    return RESERVED_HUB_DIRS


def _known_harness(registry: dict, name: str) -> bool:
    """Whether ``name`` is a harness: a registry entry or a hooks contract.

    A hub dir that is neither holds no harness's scripts, so there is no
    harness.json fix to point at per file.
    """
    from swe.hooks.contracts import CONTRACTS

    return name in registry or name in CONTRACTS


def _disabled_hook_names(harness_dir: Path) -> set[str]:
    """Basenames in ``harness_dir/.disabled``; an absent file means none."""
    try:
        text = (harness_dir / DISABLED_BASENAME).read_text(encoding="utf-8")
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines()
            if line.strip() and not line.startswith("#")}


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
    # Through __getattr__, not the bare global: the table is filled lazily,
    # and a first call from inside this module would hit a NameError.
    table = globals().get("HOOK_FALLBACK") or __getattr__("HOOK_FALLBACK")
    fallback = table.get(name)
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


def classify_hook(
    label: str,
    dest: Path,
    source: Path,
    home: Path,
    *,
    installed: bool | None = None,
) -> LinkStatus:
    """Classify one hook destination without touching the filesystem.

    Differs from ``layout.inspect`` in two ways. The harness counts as
    installed when its config directory exists or its command resolves on
    PATH, since a binary in a Nix profile never makes ``~/.claude`` — the
    caller passes the combined answer as ``installed``. And a plain file
    with the same bytes as the source is ``absorb``, not left alone: a
    copy where the link should be is exactly how edits end up in an
    unversioned file, and replacing it loses nothing.
    """
    if installed is None:
        installed = _installed(dest.parent, home)
    if not installed:
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
            return LinkStatus(label, dest, "skipped", INSIDE_SWE_DETAIL, source)
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
    archived = archived_names(registry)

    root_dir = hooks_dir(home)
    if not root_dir.is_dir():
        return []

    statuses: list[LinkStatus] = []
    for harness_dir in sorted(root_dir.iterdir()):
        name = harness_dir.name
        if not harness_dir.is_dir() or name.startswith(".") \
                or name in _SKIP_NAMES or name in _reserved_dirs():
            continue
        if not _known_harness(registry, name):
            # Something else's bundle (command-guard/ holds a script, its
            # rules and tests): one entry for the dir, not one per file.
            statuses.append(LinkStatus(
                name, harness_dir, "skipped", UNKNOWN_HARNESS_DETAIL,
                harness_dir))
            continue
        root, why = hook_root(registry, name, home)
        # A dir that exists proves the harness; so does its binary on PATH.
        # Dir evidence alone read a fresh Nix/uv install — nothing run yet,
        # no config dirs — as "harness not installed" and skipped it.
        installed = root is not None and (
            _installed(root, home) or command_on_path(name, registry))
        disabled = _disabled_hook_names(harness_dir)
        for source in hook_sources(harness_dir):
            if source.name in disabled:
                # Switched off by `swe hooks disable`: keep the file, keep
                # any declaration, never relink. enable clears the marker.
                # path carries the destination (when the harness has a
                # root) so apply_hook_status can unlink a leftover link;
                # a rootless harness has no destination to point at.
                dest = root / source.name if root is not None else source
                statuses.append(LinkStatus(
                    name, dest, "ignored", DISABLED_DETAIL, source))
                continue
            if root is None:
                # Nowhere to put it: report the source so the user can see
                # which scripts are waiting on a harness.json entry.
                statuses.append(LinkStatus(name, source, "skipped", why, source))
                continue
            if _inside(root, _paths.swe_dir_for(home)):
                # A root inside ~/.quiver would make the destination the
                # source itself: "absorbing" it deletes the only copy.
                statuses.append(LinkStatus(name, source, "skipped", INSIDE_SWE_DETAIL, source))
                continue
            dest = root / source.name
            if is_linkignored(dest, home, patterns):
                statuses.append(LinkStatus(name, dest, "ignored", IGNORED_DETAIL, source))
            else:
                # An archived harness is unmanaged: leave its hooks alone
                # too. A hook already linked stays linked, as for skills.
                status = classify_hook(name, dest, source, home, installed=installed)
                archived_override(status, archived)
                statuses.append(status)
    return statuses


def _safe_to_replace(path: Path, home: Path) -> bool:
    """Same guard as init/commands._apply: inside $HOME, outside ~/.quiver.

    Duplicated here because init/commands imports this module — the check
    itself is two lines and moving it down would churn every caller.
    """
    try:
        resolved = path.resolve()
        return (
            resolved.is_relative_to(home.resolve())
            and not resolved.is_relative_to(
                _paths.swe_dir_for(home).resolve())
        )
    except OSError:
        return False


def apply_hook_status(status: LinkStatus, home: Path, force: bool = False) -> str:
    """Carry out one plan_hooks status; returns a result word.

    The hooks-scoped twin of ``_apply`` in init/commands.py, kept here so
    ``swe hooks sync`` / ``enable`` / ``disable`` / ``delete`` can act on a
    single name without re-running the whole init report. Same contract:
    linked/skipped/ignored pass through, keep and conflict need force,
    real content that is safe to replace is backed up before the link
    goes in, and the symlink's target is ``status.source``. The one
    addition: a disabled script's link is removed, since that is what
    disabling means — the marker alone never touches the destination.
    """
    if status.state in ("linked", "skipped"):
        return status.state
    if status.state == "ignored":
        if status.detail == DISABLED_DETAIL and status.path.is_symlink():
            try:
                if status.source is not None and \
                        status.path.resolve() == status.source.resolve():
                    status.path.unlink()
                    return "unlinked"
            except OSError:
                pass
        return status.state
    if status.state == "keep" and not force:
        return "protected"
    if status.state == "conflict" and not force:
        return "blocked"

    path = status.path
    if status.state in ("conflict", "absorb", "keep") \
            and not path.is_symlink():
        if path.exists():
            if not _safe_to_replace(path, home):
                return "blocked"
            _paths.backup_tree(path, home)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    elif path.is_symlink():
        path.unlink()

    if status.source is None:
        return "blocked"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(status.source)
    return "linked"
