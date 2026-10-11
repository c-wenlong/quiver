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
    ARCHIVED_DETAIL,
    LinkIgnoreError,
    archived_names,
    classify_skill_root,
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


def _claude_plugin_loads(home: Path):
    """Whether claude loads a skill found in its plugin cache.

    Returns ``(folder) -> "loaded" | "unloaded" | "superseded"``. Claude keeps
    an old version's folder after an update (a running session may still hold
    it), so the cache lists every skill twice; the install record names the
    one version that loads, and anything else of that plugin is superseded.
    A version claude has installed loads unless settings.json disables the
    plugin. A cached plugin with no install record does not load.
    """
    base = home / ".claude" / "plugins"
    cache_real = _real_path(base / "cache")
    record = _load_json(base / "installed_plugins.json")
    plugins = record.get("plugins") if isinstance(record, dict) else None
    settings = _load_json(home / ".claude" / "settings.json")
    flags = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    enabled = flags if isinstance(flags, dict) else {}

    installed: dict[Path, str] = {}       # version dir -> "plugin@marketplace"
    by_plugin: set[tuple[str, str]] = set()
    for ref, entries in (plugins.items() if isinstance(plugins, dict) else []):
        for entry in entries if isinstance(entries, list) else [entries]:
            path = entry.get("installPath") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path or cache_real is None:
                continue
            real = _real_path(Path(path).expanduser())
            if real is None or not _under(real, cache_real):
                continue
            parts = real.relative_to(cache_real).parts
            if len(parts) >= 3:
                installed[Path(cache_real, *parts[:3])] = ref
                by_plugin.add(parts[:2])

    def state(folder: Path) -> str:
        real = _real_path(folder)
        if real is None or cache_real is None or not _under(real, cache_real):
            return "unloaded"
        parts = real.relative_to(cache_real).parts
        if len(parts) < 3:
            return "unloaded"
        ref = installed.get(Path(cache_real, *parts[:3]))
        if ref is None:
            return "superseded" if parts[:2] in by_plugin else "unloaded"
        return "unloaded" if enabled.get(ref) is False else "loaded"

    return state


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
    harnesses = []
    for s in roots:
        state, detail = s.state, s.detail
        if state == "ignored" and detail == ARCHIVED_DETAIL:
            # init leaves an archived harness's folder alone and says only
            # that. Linking it by hand still depends on what the folder
            # holds, so report that; `archived` keeps the init view.
            state, detail = classify_skill_root(s.path, home)
        harnesses.append({
            "name": registry_name(s.label),
            "label": s.label,
            "path": str(s.path),
            "state": state,
            "detail": detail,
            "archived": registry_name(s.label) in archived,
        })
    real_roots = [
        (registry_name(s.label), real)
        for s in roots
        if s.path.exists() and (real := _real_path(s.path)) is not None
    ]
    catalogs = {str(entry.get("label", "catalog")) for entry in load_skill_catalogs()}

    claude_loads = _claude_plugin_loads(home)
    skills = []
    for skill in discover_skills(home=home, cwd=cwd, visibility=False):
        folder = Path(skill["path"]).parent
        owner = PLUGIN_ROOTS.get(skill["scope"]) or BUILTIN_ROOTS.get(skill["scope"])
        if skill["scope"] == "claude-plugin":
            load = claude_loads(folder)
            if load == "superseded":
                continue
            if load == "unloaded":
                owner = None
        real = _real_path(folder)
        seen_by: list[str] = []
        for name, root in real_roots:
            if real is not None and _under(real, root) and name not in seen_by:
                seen_by.append(name)
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
    machine. Its ``state`` is ``installed``, the first gap ``swe find
    plugins`` reports (``unregistered``, ``not-installed``, ``stale``,
    ``disabled-here``), or ``disabled`` when it is disabled in every harness,
    with the command that closes it as ``fix`` and, when swe can run that
    command itself, the ``id`` that ``swe find plugins --fix`` takes.
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
            # plugin_drift reports a disabled plugin only when another
            # harness has it enabled; disabled everywhere it is silent.
            ref = f"{plugin.name}@{registered}"
            if registered is not None and view.enabled.get(ref) is False:
                return {"state": "disabled", "fix": view.fix_enable(ref).text, "id": None}
            return {"state": "installed", "fix": None, "id": None}
        first = min(mine, key=lambda f: rank[f.kind])
        # `id` is what `swe find plugins --fix=<id>` takes, given only when
        # that has commands to run: codex's enable is an edit by hand.
        return {"state": first.kind, "fix": first.fix,
                "id": first.id if first.argv else None}

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
