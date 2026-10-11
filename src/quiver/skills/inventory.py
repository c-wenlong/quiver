"""What skills a machine has, and what the quiver hub offers.

Two read-only documents for ``swe skills --json`` and ``swe skills hub
--json``, which the macOS app's Skills page renders. Nothing here prints or
writes.

quiver links a whole skills directory, not single skills, so "which harness
sees this skill" is derived: a skill is visible to a harness when the skill's
real folder sits under that harness's real skills root. A shared skill is
therefore visible to every harness whose root links to ``~/.quiver/skills``,
and a skill in a harness's own folder to that harness alone.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from quiver import paths
from quiver.find.plugin_drift import KINDS, _READERS, _real, plugin_drift, quiver_plugins
from quiver.find.plugins import _load_json, count_components
from quiver.init.layout import (
    LinkIgnoreError,
    archived_names,
    load_linkignore,
    load_registry,
    plan,
    registry_name,
)
from quiver.skills.catalogs import load_skill_catalogs
from quiver.skills.discovery import discover_skills, parse_skill_md

# Roots discover_skills adds that sit outside every harness's skills
# directory but load into one harness all the same.
PLUGIN_ROOTS = {"claude-plugin": "claude", "cursor-plugin": "cursor"}
BUILTIN_ROOTS = {"cursor-builtin": "cursor"}

# ~/.agents/skills is a shared convention several tools read, not a harness
# of its own; `link_states` leaves it out for the same reason.
NOT_A_HARNESS = {"agents"}


def _plan_skill_roots(home: Path):
    """init's view of every harness skills root, with its link state.

    A broken .linkignore must not take a read-only listing down, but it must
    not be silently treated as empty either: say so, then carry on without it.
    """
    try:
        patterns = load_linkignore(home)
    except LinkIgnoreError as exc:
        print(f"warning: {exc}; showing link states without it", file=sys.stderr)
        patterns = []
    _instructions, skills = plan(home, patterns)
    return [s for s in skills if s.label not in NOT_A_HARNESS]


def _real_path(path: Path) -> Path | None:
    try:
        return Path(os.path.realpath(path))
    except (OSError, ValueError):
        return None


def _under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _superseded_plugin_dirs(home: Path):
    """A test for claude plugin-cache skills from a version no longer installed.

    Claude keeps the old version's folder after an update (a running session
    may still hold it), so the cache lists every skill twice. The install
    record names the one version that loads; a plugin it does not mention is
    kept as found rather than guessed at.
    """
    cache = home / ".claude" / "plugins" / "cache"
    record = _load_json(home / ".claude" / "plugins" / "installed_plugins.json")
    plugins = record.get("plugins") if isinstance(record, dict) else None
    installed: dict[tuple[str, str], set[Path]] = {}
    for entries in (plugins.values() if isinstance(plugins, dict) else []):
        for entry in entries if isinstance(entries, list) else [entries]:
            path = entry.get("installPath") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path:
                continue
            real = _real_path(Path(path).expanduser())
            cache_real = _real_path(cache)
            if real is None or cache_real is None or not _under(real, cache_real):
                continue
            parts = real.relative_to(cache_real).parts
            if len(parts) >= 3:
                installed.setdefault(parts[:2], set()).add(real)

    def superseded(folder: Path) -> bool:
        real, cache_real = _real_path(folder), _real_path(cache)
        if real is None or cache_real is None or not _under(real, cache_real):
            return False
        parts = real.relative_to(cache_real).parts
        if len(parts) < 3 or parts[:2] not in installed:
            return False
        return Path(cache_real, *parts[:3]) not in installed[parts[:2]]

    return superseded


def _source(label: str, catalogs: set[str]) -> str:
    if label == "shared":
        return "shared"
    if label in PLUGIN_ROOTS:
        return "plugin"
    if label in BUILTIN_ROOTS:
        return "builtin"
    if label == "project":
        return "project"
    if label in catalogs:
        return "catalog"
    return "harness"


def installed_skills(home: Path | None = None, cwd: Path | None = None) -> dict:
    """The ``swe skills --json`` document.

    ``harnesses`` is one row per harness skills root: its registry name, the
    root label ``swe skills link`` accepts, the path, and the link state in
    init's vocabulary (``linked``, ``relink``, ``create``, ``absorb``,
    ``keep``, ``ignored``, ``skipped``) with init's reason. ``skills`` is
    every SKILL.md on the machine, each with where it came from and which
    harnesses can see it.
    """
    home = home or Path.home()
    roots = _plan_skill_roots(home)
    archived = archived_names(load_registry(home))
    harnesses = [
        {
            "name": registry_name(s.label),
            "label": s.label,
            "path": str(s.path),
            "state": s.state,
            "detail": s.detail,
            # init leaves an archived harness alone, so its root reads
            # `ignored` once unlinked; the app still lets you link it back.
            "archived": registry_name(s.label) in archived,
        }
        for s in roots
    ]
    real_roots = [
        (registry_name(s.label), real)
        for s in roots
        if s.path.exists() and (real := _real_path(s.path)) is not None
    ]
    catalogs = {str(entry.get("label", "catalog")) for entry in load_skill_catalogs()}

    superseded = _superseded_plugin_dirs(home)
    skills = []
    for skill in discover_skills(home=home, cwd=cwd, visibility=False):
        folder = Path(skill["path"]).parent
        if skill["scope"] == "claude-plugin" and superseded(folder):
            continue
        real = _real_path(folder)
        seen_by: list[str] = []
        for name, root in real_roots:
            if real is not None and _under(real, root) and name not in seen_by:
                seen_by.append(name)
        owner = PLUGIN_ROOTS.get(skill["scope"]) or BUILTIN_ROOTS.get(skill["scope"])
        if owner and owner not in seen_by:
            seen_by.append(owner)
        skills.append({
            "name": skill["name"],
            "description": skill["description"],
            "path": str(folder),
            "root": skill["scope"],
            "source": _source(skill["scope"], catalogs),
            "harnesses": sorted(seen_by),
        })
    skills.sort(key=lambda s: (s["name"].lower(), s["path"]))
    return {
        "shared_dir": str(paths.skills_dir_for(home)),
        "harnesses": harnesses,
        "skills": skills,
    }


def _plugin_skills(plugin_dir: Path) -> list[dict]:
    """Every skill a plugin ships, by its SKILL.md name, in name order."""
    root = plugin_dir / "skills"
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if "SKILL.md" in filenames:
            name, description = parse_skill_md(Path(dirpath) / "SKILL.md")
            found.append({"name": name, "description": description,
                          "path": dirpath})
    return sorted(found, key=lambda s: s["name"].lower())


def _plugin_description(plugin) -> str:
    market = _load_json(plugin.marketplace_dir / ".claude-plugin" / "marketplace.json")
    for entry in (market or {}).get("plugins", []) if isinstance(market, dict) else []:
        if isinstance(entry, dict) and entry.get("name") == plugin.name:
            text = entry.get("description")
            if isinstance(text, str) and text.strip():
                return text.strip()
    manifest = _load_json(plugin.path / ".claude-plugin" / "plugin.json")
    text = manifest.get("description") if isinstance(manifest, dict) else None
    return text.strip() if isinstance(text, str) else ""


def hub_plugins(home: Path | None = None) -> dict:
    """The ``swe skills hub --json`` document: the plugins ``~/.quiver`` offers.

    ``install`` is keyed by each harness that runs plugins and exists on this
    machine. Its ``state`` is ``installed`` or the first gap ``swe find
    plugins`` reports (``unregistered``, ``not-installed``, ``stale``,
    ``disabled-here``), with the command that closes it as ``fix``.
    """
    home = home or Path.home()
    sources = quiver_plugins(home)
    views = {name: view for name, reader in _READERS.items()
             if (view := reader(home)) is not None}
    findings = plugin_drift(home) if sources else []
    rank = {kind: i for i, kind in enumerate(KINDS)}

    def gap(harness: str, plugin) -> dict:
        view = views[harness]
        registered = view.registered.get(_real(plugin.marketplace_dir))
        mine = [
            f for f in findings
            if f.harness == harness and (
                (f.kind == "unregistered" and registered is None
                 and f.marketplace == plugin.marketplace)
                or (f.plugin == plugin.name and f.marketplace == registered))
        ]
        if not mine:
            return {"state": "installed", "fix": None}
        first = min(mine, key=lambda f: rank[f.kind])
        return {"state": first.kind, "fix": first.fix}

    plugins = []
    for plugin in sources:
        plugins.append({
            "name": plugin.name,
            "marketplace": plugin.marketplace,
            "description": _plugin_description(plugin),
            "version": plugin.version or None,
            "path": str(plugin.path),
            "components": count_components(plugin.path),
            "skills": _plugin_skills(plugin.path),
            "install": {name: gap(name, plugin) for name in sorted(views)},
        })
    plugins.sort(key=lambda p: (p["marketplace"].lower(), p["name"].lower()))
    return {"hub": str(paths.quiver_dir_for(home)), "plugins": plugins}
