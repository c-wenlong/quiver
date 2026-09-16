"""Registering harnesses `swe init` finds but harness.json does not know.

Skills roots are discovered by scanning the home directory, so a harness
installed after the registry was last touched shows up in the plan with no
entry to its name. This module decides which of those are new, and writes
the entries a tick (managed) or an untick (declined, archived) produces.
Printing and prompting stay in ``commands.py``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from quiver import paths as _paths
from quiver.init.layout import (
    HARNESS_SIGNATURES,
    LinkStatus,
    aliases_of,
    registry_name,
    skill_root_label,
)


def _valid_filename(value: str) -> bool:
    """True only for a plain filename: no separators, no dot aliases.

    ``.`` or ``..`` would resolve the instruction target to the harness
    directory itself, which ``--force`` would then back up and delete.
    """
    return (
        bool(value)
        and value not in (".", "..")
        and not any(ch in value for ch in ("/", "\\", "\0"))
    )


def new_harnesses(skills: list[LinkStatus], registry: dict) -> list[LinkStatus]:
    """Discovered skills roots nothing already describes, one per label.

    ``~/.config/agents/skills`` is a shared dir, not a harness, and an
    ``ignored`` root needs no decision. A ``linked`` one still does: a
    machine that ran init before this feature linked every root already,
    and those harnesses would never get a registry entry otherwise —
    declining one just archives it, and the link stays, since init never
    unlinks. A root whose label resolves to a registry key or one of its
    aliases is known — and so is one a signature already covers: offering
    claude as "new" would let a default AGENTS.md answer replace its real
    CLAUDE.md target, and declining it would archive a harness init already
    manages. The signature's own skills dir labels count too: ``.kilo``
    reads as "kilo", ``.factory`` as "droid" through the alias table.
    """
    known = set(registry) | {
        alias
        for entry in registry.values()
        for alias in aliases_of(entry)
    }
    known |= {registry_name(label) for label in HARNESS_SIGNATURES}
    known |= {
        registry_name(skill_root_label(sig.skills, registry=registry))
        for sig in HARNESS_SIGNATURES.values()
        if sig.skills is not None
    }
    found: dict[str, LinkStatus] = {}
    for status in skills:
        if status.label == "agents" or status.state == "ignored":
            continue
        if registry_name(status.label) in known:
            continue
        found.setdefault(status.label, status)
    return [found[label] for label in sorted(found)]


def instruction_target(root: Path, filename: str) -> Path:
    """The instruction file next to a skills root: ~/.config/devin/AGENTS.md."""
    return root.parent / filename


def home_relative(path: Path, home: Path) -> str:
    """``~/.config/devin/skills`` form, absolute path when outside ``home``."""
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def legacy_registry_pending(home: Path) -> bool:
    """True when tools.json still waits for harness.json's lazy migration.

    Writing a fresh harness.json now would make ``harness/registry.py``
    skip that migration and strand the legacy entries, so init refuses to.
    """
    config_dir = _paths.config_dir_for(home)
    return (
        not (config_dir / "harness.json").exists()
        and (config_dir / "tools.json").exists()
    )


def _entry(label: str, root: Path, home: Path, now: str) -> dict:
    return {
        "command": label if shutil.which(label) else "",
        "description": f"Registered by swe init from {home_relative(root, home)}",
        "version": None,
        "tags": ["agentic", "coding"],
        "aliases": [],
        "added": now,
        "discovered_via": "init",
    }


def register(
    home: Path,
    registry: dict,
    managed: dict[str, tuple[Path, str | None]],
    declined: dict[str, Path],
) -> dict:
    """Write harness.json entries for newly found harnesses; return the registry.

    ``managed`` maps label -> (skills root, instruction filename or None);
    each becomes an active entry with ``capabilities.skills`` and, when a
    filename was given, ``capabilities.instructions``. ``declined`` maps
    label -> skills root and becomes an archived entry — recorded as
    evaluated, left alone, never asked about again. The skills root stays
    in its capabilities so ``swe list --scope archived`` can still show it.

    Returns the registry unchanged without writing when a legacy tools.json
    still awaits migration (see ``legacy_registry_pending``).
    """
    if legacy_registry_pending(home):
        return registry
    config_dir = _paths.config_dir_for(home)
    registry = dict(registry)
    now = datetime.now().isoformat()
    for label, (root, filename) in managed.items():
        entry = _entry(label, root, home, now)
        capabilities: dict = {
            "skills": {"supported": True, "root": home_relative(root, home)},
        }
        if filename is not None:
            if not _valid_filename(filename):
                raise ValueError(f"invalid instruction filename: {filename!r}")
            capabilities["instructions"] = {
                "file": home_relative(instruction_target(root, filename), home),
            }
        entry["capabilities"] = capabilities
        registry[label] = entry
    for label, root in declined.items():
        entry = _entry(label, root, home, now)
        entry["state"] = "archived"
        entry["archived"] = {
            "reason": "declined in swe init",
            "archived_at": now,
            "usage": "none",
        }
        entry["capabilities"] = {
            "skills": {"supported": True, "root": home_relative(root, home)},
        }
        registry[label] = entry
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / "harness.json"
    _paths.atomic_write_text(target, json.dumps(registry, indent=2))
    return registry
