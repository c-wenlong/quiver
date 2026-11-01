"""What skills a machine has, and what the quiver hub offers.

Two read-only documents for ``swe skills --json`` and ``swe skills hub
--json``, which the macOS app's Skills page renders. Nothing here prints or
writes.

quiver links a whole skills directory, not single skills, so "which harness
sees this skill" is derived: a skill is visible to a harness when the skill's
real folder sits under that harness's real skills root. A shared skill is
therefore visible to every harness whose root links to ``~/.swe/skills``,
and a skill in a harness's own folder to that harness alone.

A plugin's skills are read from where its harness loads them. Claude runs a
plugin from a directory marketplace (every one in ``~/.swe/plugins``)
straight from the marketplace's folder, so those skills are listed from the
plugin's source; the copy under ``~/.claude/plugins/cache`` is a snapshot
it never loads. Plugins claude fetched (github, git, a URL) run from that
cache, and are listed from it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from swe import paths
from swe.find.plugin_drift import (
    KINDS,
    _READERS,
    _local_source,
    _real,
    plugin_drift,
    swe_plugins,
)
from swe.find.plugins import _expand_root, _load_json, count_components
from swe.init.layout import (
    ARCHIVED_DETAIL,
    LinkIgnoreError,
    archived_names,
    classify_skill_root,
    load_linkignore,
    load_registry,
    plan,
    registry_name,
)
from swe.skills.catalogs import load_skill_catalogs
from swe.skills.discovery import discover_skills, parse_skill_md

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


def _claude_directory_marketplaces(home: Path, settings) -> dict[str, Path]:
    """Marketplace name -> folder, for each one claude reads from a directory.

    known_marketplaces.json is what claude has materialised, and wins over
    settings' extraKnownMarketplaces, which it materialises at startup.
    """
    known = _load_json(home / ".claude" / "plugins" / "known_marketplaces.json")
    extra = settings.get("extraKnownMarketplaces") if isinstance(settings, dict) else None
    found: dict[str, Path] = {}
    for table in (known, extra):
        for name, entry in (table.items() if isinstance(table, dict) else []):
            if name in found or not isinstance(entry, dict):
                continue
            src = entry.get("source")
            if not isinstance(src, dict):
                continue
            path = src.get("path")
            if src.get("source") == "directory" and isinstance(path, str) and path:
                found[name] = _expand_root(path, home)
            else:
                # Known as something else (github, git, url): claude fetched
                # it, so a declaration in settings must not turn it back
                # into a directory.
                found[name] = None
    return {name: path for name, path in found.items() if path is not None}


def _claude_live_source(markets: dict[str, Path], ref: str) -> Path | None:
    """The folder claude reads plugin ``ref`` from live, or None.

    Claude Code loads a plugin from a directory marketplace straight from
    the marketplace's folder (``claude plugin update`` says "read from its
    folder ... nothing to update"), at the path the marketplace manifest's
    ``source`` gives, which need not be the plugin's name. A plugin the
    manifest fetches from elsewhere (git-subdir, github, a URL) runs from
    claude's cached copy, as does one whose folder is not there.
    """
    plugin, _, market = ref.partition("@")
    mdir = markets.get(market)
    if mdir is None or not plugin:
        return None
    manifest = _load_json(mdir / ".claude-plugin" / "marketplace.json")
    entries = manifest.get("plugins") if isinstance(manifest, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("name") == plugin:
            rel = _local_source(entry.get("source"))
            if rel is None:
                return None
            pdir = mdir / rel
            return pdir if pdir.is_dir() else None
    return None


def _claude_plugin_loads(home: Path):
    """Whether claude loads a skill found in its plugin cache, and what it
    reads live instead.

    Returns ``(state, live)``. ``state`` is ``(folder) -> "loaded" |
    "unloaded" | "superseded" | "live"``. Claude keeps an old version's
    folder after an update (a running session may still hold it), so the
    cache lists every skill twice; the install record names the one version
    that loads, and anything else of that plugin is superseded. A version
    claude has installed loads unless settings.json disables the plugin. A
    cached plugin with no install record does not load.

    A plugin installed from a directory marketplace is read from its source
    folder, so every cached version of it is ``live``: a snapshot claude
    never loads. ``live`` maps each such source folder (realpath) to whether
    claude loads it, which is the same enabled test.
    """
    base = home / ".claude" / "plugins"
    cache_real = _real_path(base / "cache")
    record = _load_json(base / "installed_plugins.json")
    plugins = record.get("plugins") if isinstance(record, dict) else None
    settings = _load_json(home / ".claude" / "settings.json")
    flags = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    enabled = flags if isinstance(flags, dict) else {}
    markets = _claude_directory_marketplaces(home, settings)

    installed: dict[Path, str] = {}       # version dir -> "plugin@marketplace"
    by_plugin: set[tuple[str, str]] = set()
    live_cache: set[tuple[str, str]] = set()   # (marketplace, plugin) cache dirs
    live: dict[Path, bool] = {}
    for ref, entries in (plugins.items() if isinstance(plugins, dict) else []):
        install_paths = [entry["installPath"]
                 for entry in (entries if isinstance(entries, list) else [entries])
                 if isinstance(entry, dict) and isinstance(entry.get("installPath"), str)
                 and entry["installPath"]]
        if not install_paths:
            continue   # no usable install record: claude has not installed it
        source = _claude_live_source(markets, ref)
        if source is not None:
            real_source = _real_path(source)
            if real_source is not None:
                loads = enabled.get(ref) is not False
                live[real_source] = live.get(real_source, False) or loads
                plugin, _, market = ref.partition("@")
                live_cache.add((market, plugin))
        for path in install_paths:
            if cache_real is None:
                continue
            real = _real_path(paths.expand_user(path))
            if real is None or not _under(real, cache_real):
                continue
            parts = real.relative_to(cache_real).parts
            if len(parts) >= 3:
                installed[Path(cache_real, *parts[:3])] = ref
                by_plugin.add(parts[:2])
                if source is not None:
                    live_cache.add(parts[:2])

    def state(folder: Path) -> str:
        real = _real_path(folder)
        if real is None or cache_real is None or not _under(real, cache_real):
            return "unloaded"
        parts = real.relative_to(cache_real).parts
        if len(parts) < 3:
            return "unloaded"
        if parts[:2] in live_cache:
            return "live"
        ref = installed.get(Path(cache_real, *parts[:3]))
        if ref is None:
            return "superseded" if parts[:2] in by_plugin else "unloaded"
        return "unloaded" if enabled.get(ref) is False else "loaded"

    return state, live


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

    claude_loads, claude_live = _claude_plugin_loads(home)
    # Claude reads these plugins from their source folder, so their skills
    # are listed from there and from nowhere else: not from the cache
    # snapshot, and not a second time under another root that reaches the
    # same folder (a catalog, a second plugin linking to it). Such a row's
    # harness still sees the skill, so its owner joins the one row.
    live: dict[Path, list] = {}   # real folder -> [name, description, owners]
    for source, loads in sorted(claude_live.items()):
        for folder, name, description in _walk_skills(source / "skills"):
            real = _real_path(folder)
            if real is None:
                continue
            row = live.setdefault(real, [name, description, set()])
            if loads:
                row[2].add("claude")

    rows = []   # (name, description, folder, root label, harnesses that own it)
    for skill in discover_skills(home=home, cwd=cwd, visibility=False):
        folder = Path(skill["path"]).parent
        owner = PLUGIN_ROOTS.get(skill["scope"]) or BUILTIN_ROOTS.get(skill["scope"])
        real = _real_path(folder)
        if real in live:
            if owner and skill["scope"] != "claude-plugin":
                live[real][2].add(owner)
            continue
        if skill["scope"] == "claude-plugin":
            load = claude_loads(folder)
            if load in ("superseded", "live"):
                continue
            if load == "unloaded":
                owner = None
        rows.append((skill["name"], skill["description"], folder, skill["scope"],
                     {owner} if owner else set()))
    for real, (name, description, owners) in live.items():
        rows.append((name, description, real, "claude-plugin", owners))

    skills = []
    for name, description, folder, label, owners in rows:
        real = _real_path(folder)
        seen_by: list[str] = []
        for harness, root in real_roots:
            if real is not None and _under(real, root) and harness not in seen_by:
                seen_by.append(harness)
        seen_by += [owner for owner in owners if owner not in seen_by]
        skills.append({
            "name": name,
            "description": description,
            "path": str(folder),
            "root": label,
            "source": _source(label, catalogs),
            "harnesses": sorted(seen_by),
        })
    skills.sort(key=lambda s: (s["name"].lower(), s["path"]))
    return {
        "shared_dir": str(paths.skills_dir_for(home)),
        "harnesses": harnesses,
        "skills": skills,
    }


def _walk_skills(root: Path):
    """``(folder, name, description)`` for each SKILL.md under ``root``.

    The same walk ``discover_skills`` makes of a root, so a plugin read from
    its source shows exactly the skills its cached copy would have.
    """
    try:
        root_real = root.resolve()
    except (OSError, RuntimeError):
        return
    for dirpath, _dirnames, filenames in os.walk(root_real):
        if "SKILL.md" in filenames:
            name, description = parse_skill_md(Path(dirpath) / "SKILL.md")
            yield Path(dirpath), name, description


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
    """The ``swe skills hub --json`` document: the plugins ``~/.swe`` offers.

    ``install`` is keyed by each harness that runs plugins and exists on this
    machine. Its ``state`` is ``installed``, the first gap ``swe find
    plugins`` reports (``unregistered``, ``not-installed``,
    ``linked-manifest``, ``stale``, ``disabled-here``), or ``disabled`` when
    it is disabled in every harness, with the command that closes it as
    ``fix`` and, when swe can run that command itself, the ``id`` that
    ``swe find plugins --fix`` takes.
    """
    home = home or Path.home()
    sources = swe_plugins(home)
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
    return {"hub": str(paths.swe_dir_for(home)), "plugins": plugins}
