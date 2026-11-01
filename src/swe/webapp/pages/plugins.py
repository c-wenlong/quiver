"""The Plugins page: the Mac app's PluginsPage.

Two views, as the app's two tabs:

- ``?view=health`` is ``swe find plugins --json``: where Claude Code and
  Codex no longer match ``~/.swe/plugins``, each finding with the fix
  swe would run.
- ``?view=explore`` is ``swe plugins catalog --json`` arranged the way the
  app's Explore tab shows it (``PluginExploreIndex``): sources (Local, each
  harness, Not registered), their marketplaces, and the plugin rows under
  each, with every plugin's state per harness worked out once.

Reading every marketplace can take a while on a machine with many, so each
view is kept here for ``CACHE_TTL_S``; ``?refresh=1`` reads it again.

The writes (``post``) are Health's Fix and Fix all, which run a finding's
harness CLI commands the way ``swe find plugins --fix=<id>`` does, and the
detail sheet's Install, Enable, Disable and Uninstall per harness, which
run ``swe plugins <action> <ref> --harness=<h>`` (``plugin_action``). Add
marketplace stays in ``swe`` and the Mac app (see docs/web-app/plugins.md).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from swe.webapp.pages import PageError, home_path

# The app's SnapshotLoader maxAge for both models.
CACHE_TTL_S = 60.0
VIEWS = ("health", "explore")

# One lock per view: a slow catalog read never holds up Health.
_locks = {view: threading.Lock() for view in VIEWS}
_cache: dict = {}

# PluginDrift.kindTitle
KIND_TITLES = {
    "unregistered": "Marketplace not added",
    "not-installed": "Not installed",
    "linked-manifest": "Linked manifest",
    "stale": "Out of date",
    "disabled-here": "Disabled here",
}

# PluginDriftState.Kind: title and severity (lower is worse).
DRIFT_STATES = {
    "unregistered": ("Marketplace not added", 0),
    "not-installed": ("Not installed", 1),
    "stale": ("Out of date", 2),
    "linked-manifest": ("Linked manifest", 3),
    "disabled-here": ("Disabled", 4),
    "disabled": ("Disabled", 5),
    "unknown": ("Needs a look", 6),
    "installed": ("Installed", 7),
}

# PluginHarnessState.Status values that mean something of it is there.
INSTALLED = {"enabled", "disabled", "installed", "cached"}

# HarnessMark names the source headings use.
_NAMES = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor"}


def get(ctx, args: dict) -> dict:
    view = args.get("view") or "health"
    if view not in VIEWS:
        raise PageError(400, "bad_view", f"view must be one of {', '.join(VIEWS)}")
    home = home_path(ctx)
    body, built_at = cached(view, home, refresh=args.get("refresh") == "1")
    return {**body, "view": view, "built_at": built_at,
            "machine": (ctx.machine or {}).get("name")}


def cached(view: str, home: Path, refresh: bool = False, now=time.time) -> tuple[dict, float]:
    """The view's body and when it was read. One read of a view at a time:
    a second request waits for the first and gets its answer."""
    key = (view, str(home))
    with _locks[view]:
        hit = _cache.get(key)
        if hit is not None and not refresh and now() - hit[1] < CACHE_TTL_S:
            return hit
        if refresh and view == "explore":
            _drop_catalog(home)
        body = health(home) if view == "health" else explore(home)
        _cache[key] = (body, now())
        return _cache[key]


# ``build_catalog`` walks every marketplace and plugin folder; Explore and
# the Skills page's placement both need it, so they share one read.
_catalog_lock = threading.Lock()


def shared_catalog(home: Path, now=time.time) -> dict:
    """``build_catalog(home)``, read at most once per ``CACHE_TTL_S``."""
    from swe.plugins.catalog import build_catalog

    key = ("catalog", str(home))
    with _catalog_lock:
        hit = _cache.get(key)
        if hit is not None and now() - hit[1] < CACHE_TTL_S:
            return hit[0]
        built = build_catalog(home)
        _cache[key] = (built, now())
        return built


def _drop_catalog(home: Path) -> None:
    with _catalog_lock:
        _cache.pop(("catalog", str(home)), None)


def clear_cache() -> None:
    for view in VIEWS:
        with _locks[view]:
            for key in [k for k in _cache if k[0] == view]:
                del _cache[key]
    with _catalog_lock:
        for key in [k for k in _cache if k[0] == "catalog"]:
            del _cache[key]


# ---------------------------------------------------------------- Health


def health(home: Path) -> dict:
    """``swe find plugins --json``, each finding with the app's title."""
    from swe.find.commands import _plugins_json
    from swe.find.roots import HARNESS_DEFAULT

    report = _plugins_json(home, HARNESS_DEFAULT)
    for row in report["drift"]:
        kind = row.get("kind") or ""
        row["kind_title"] = KIND_TITLES.get(kind, kind[:1].upper() + kind[1:])
        row["severe"] = kind == "unregistered"
    return report


# ---------------------------------------------------------------- Fixes

ACTIONS = ("fix", "fix_all", "plugin")
# A fix id is ``<harness>:<kind>:<ref or marketplace dir>``; a path can be
# long, but not this long.
ID_MAX = 4096
# More than any hub has findings; a body past it is not one the page sent.
IDS_MAX = 500

# One fix at a time, across every request: two harness CLIs editing the
# same config at once could lose a change. The Mac app holds PluginWork
# for the same reason. A second request is refused rather than queued, as
# a fix can take minutes.
_WRITE_LOCK = threading.Lock()

# What runs a command; tests put a fake here. ``None`` is apply_fix's own
# subprocess.run.
_run = None


def post(ctx, action: str, body: dict) -> dict:
    """``fix`` (``{"id"}``) runs one finding's fix; ``fix_all``
    (``{"ids": [...]}``) runs the ones the confirmation listed, one at a
    time in report order, stopping at the first that fails.

    Every id must be a finding Health lists right now and one swe can run;
    the commands are the ones ``swe.find.plugin_drift`` builds, never
    anything from the request. The answer has each id's result and the
    refreshed Health view."""
    if action not in ACTIONS:
        raise PageError(404, "unknown_action", f"unknown plugins action: {action}")
    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "the body must be a JSON object")
    if action == "plugin":
        return _plugin_action(ctx, body)
    ids = [_fix_id(body.get("id"))] if action == "fix" else _fix_ids(body.get("ids"))
    home = home_path(ctx)
    if not _WRITE_LOCK.acquire(blocking=False):
        raise PageError(409, "busy", "A fix is already running. Wait for it, then refresh.")
    try:
        drift = health(home)["drift"]
        current = {f["id"]: f for f in drift}
        for fid in ids:
            finding = current.get(fid)
            if finding is None:
                raise PageError(404, "unknown_finding",
                                f"No current plugin drift has id {fid}. Refresh and try again.")
            if not finding.get("runnable"):
                raise PageError(422, "not_runnable",
                                f"No command does {fid}, so make the edit by hand: {finding.get('fix')}")
        try:
            results = _run_fixes(home, ids, drift)
        finally:
            # Even a failed fix may have changed something; both views
            # read again on their next request.
            _invalidate(home)
        view, built_at = cached("health", home)
    finally:
        _WRITE_LOCK.release()
    return {
        "action": action,
        "results": results,
        "ran": sum(1 for r in results if r["status"] == "fixed"),
        "health": {**view, "view": "health", "built_at": built_at,
                   "machine": (ctx.machine or {}).get("name")},
    }


def _plugin_action(ctx, body: dict) -> dict:
    """``plugin`` (``{"verb", "ref", "harness"}``): the detail sheet's
    Install, Enable, Disable or Uninstall, ``swe plugins <verb> <ref>
    --harness=<h> --json`` in process. Only a verb Explore offers that
    plugin in that harness right now (``harness_actions``); the command is
    the one ``plugin_action`` builds. The answer is its ``ActionResult``
    and the Explore view read again."""
    from swe.plugins.actions import ActionError, plugin_action

    verb, ref, harness = body.get("verb"), body.get("ref"), body.get("harness")
    for value in (verb, ref, harness):
        if not isinstance(value, str) or not value or len(value) > ID_MAX:
            raise PageError(422, "bad_action", "verb, ref and harness must name an action the page shows")
    home = home_path(ctx)
    if not _WRITE_LOCK.acquire(blocking=False):
        raise PageError(409, "busy", "A plugin change is already running. Wait for it, then refresh.")
    try:
        view, _ = cached("explore", home)
        plugin = view["plugins"].get(ref)
        if plugin is None:
            raise PageError(404, "unknown_plugin", f"No plugin {ref} in the catalog. Refresh and try again.")
        state = plugin["harnesses"].get(harness) or {}
        if verb not in state.get("actions", []):
            raise PageError(409, "not_offered",
                            f"Can't {verb} {ref} in {harness} now. Refresh to see what it offers.")
        try:
            result, error = plugin_action(home, verb, ref, harness, run=_run), None
        except ActionError as exc:
            result, error = None, str(exc)
        finally:
            _invalidate(home)
        view, built_at = cached("explore", home)
    finally:
        _WRITE_LOCK.release()
    return {
        "action": "plugin",
        "result": result,
        "error": error,
        "explore": {**view, "view": "explore", "built_at": built_at,
                    "machine": (ctx.machine or {}).get("name")},
    }


def _fix_id(value) -> str:
    if not isinstance(value, str) or not value or len(value) > ID_MAX:
        raise PageError(422, "bad_id", "id must be a fix id Health lists")
    return value


def _fix_ids(value) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PageError(422, "bad_ids", "ids must list the fixes to run")
    if len(value) > IDS_MAX:
        raise PageError(422, "bad_ids", f"ids can list at most {IDS_MAX} fixes")
    ids = [_fix_id(v) for v in value]
    if len(set(ids)) != len(ids):
        raise PageError(422, "bad_ids", "ids lists a fix twice")
    return ids


def _run_fixes(home: Path, ids: list[str], drift: list[dict]) -> list[dict]:
    """PluginsModel.fixAll, from ``drift`` (the findings as just read): in
    report order, and the next fix is taken from a fresh read, so one
    an earlier fix already cleared is skipped (``gone``) rather than run.
    Only ``ids`` run: a finding a fix newly exposes waits for its own
    confirmation. The first failure stops the rest (``not-run``)."""
    from swe.find.models import DriftRow
    from swe.find.plugin_drift import FixError, apply_fix

    wanted = set(ids)
    done: dict = {}
    while True:
        pick = next((f["id"] for f in drift if f["id"] in wanted and f["id"] not in done
                     and f.get("runnable")), None)
        if pick is None:
            break
        try:
            finding = apply_fix(home, pick, run=_run)
        except FixError as exc:
            done[pick] = {"id": pick, "status": "failed", "fixed": None, "error": str(exc)}
            break
        done[pick] = {"id": pick, "status": "fixed", "fixed": DriftRow.of(finding).to_json(),
                      "error": None}
        if len(done) == len(ids):
            break
        # The caller read ``drift`` just before; after a fix, read again.
        drift = health(home)["drift"]
    failed = any(r["status"] == "failed" for r in done.values())
    rest = [{"id": i, "status": "not-run" if failed else "gone", "fixed": None, "error": None}
            for i in ids if i not in done]
    return list(done.values()) + rest


def _invalidate(home: Path) -> None:
    """Drop both views for ``home``: a fix changes what Explore shows too.
    Each view's lock waits out a read in flight, so a read that began
    before the fix cannot land after this and keep its old answer."""
    for view in VIEWS:
        with _locks[view]:
            if view == "explore":
                # Under Explore's lock, before its answer goes: a read
                # waiting on the lock must not rebuild from the old
                # catalog. The Skills page's placement reads it too.
                _drop_catalog(home)
            _cache.pop((view, str(home)), None)


# ---------------------------------------------------------------- Explore


def status(state: dict | None) -> str:
    """PluginHarnessState.status: enabled, disabled, installed, available,
    cached or absent."""
    if not state:
        return "absent"
    installed, enabled = state.get("installed"), state.get("enabled")
    if installed is True:
        return "enabled" if enabled is True else "disabled" if enabled is False else "installed"
    if installed is False:
        return "available" if state.get("available") else "absent"
    if state.get("path") or state.get("copies"):
        return "cached"
    return "available" if state.get("available") else "absent"


def worst_drift(drift: dict | None) -> dict | None:
    """CatalogPlugin.worstDrift: the worst gap Health reports for a hub
    plugin, and the harnesses it is in."""
    if not drift:
        return None
    states = {h: (d or {}).get("state") for h, d in drift.items()}
    states = {h: s if s in DRIFT_STATES else "unknown" for h, s in states.items()}
    gaps = {h: s for h, s in states.items() if s != "installed"}
    if not gaps:
        return None
    worst = min(gaps.values(), key=lambda s: DRIFT_STATES[s][1])
    return {"state": worst, "title": DRIFT_STATES[worst][0],
            "harnesses": sorted(h for h, s in gaps.items() if s == worst)}


def display_names(home: Path) -> dict:
    """The names the Mac app lets a user give marketplaces and plugins,
    ``~/.swe/config/plugin-display-names.json``; empty when missing or
    unreadable, as the app reads it. Never written here."""
    from swe.paths import config_dir_for

    try:
        data = json.loads((config_dir_for(home) / "plugin-display-names.json").read_text())
    except (OSError, ValueError):
        return {"marketplaces": {}, "plugins": {}}
    out = {}
    for key in ("marketplaces", "plugins"):
        value = data.get(key) if isinstance(data, dict) else None
        out[key] = {k: v for k, v in value.items() if isinstance(v, str) and v.strip()} \
            if isinstance(value, dict) else {}
    return out


def _tag(source_type) -> str | None:
    """ExploreMarketplace.tag: the source type in a word, a folder is local."""
    if not source_type:
        return None
    return "local" if source_type == "directory" else source_type


def harness_actions(state: dict | None, actionable: bool) -> list[str]:
    """PluginCatalog.actions(for:in:): what can be done to a plugin in one
    harness now, in menu order. None where swe can't act (Cursor), and an
    install only from a marketplace that harness lists."""
    if not actionable or not state:
        return []
    installed, enabled = state.get("installed"), state.get("enabled")
    if installed is True:
        return ["disable", "uninstall"] if enabled is True else (
            ["enable", "uninstall"] if enabled is False else ["uninstall"])
    if installed is False and state.get("available"):
        return ["install"]
    return []


def _plugin(plugin: dict, names: dict, actionable=()) -> dict:
    """One plugin as the page needs it: each harness's status and the
    actions it offers there, its worst drift, and the skills it ships
    (name and description)."""
    states = {}
    for harness, state in (plugin.get("harnesses") or {}).items():
        states[harness] = {
            "status": status(state),
            "actions": harness_actions(state, harness in actionable),
            "version": state.get("version"),
            "scopes": state.get("scopes") or [],
            "path": state.get("path"),
            "copies": len(state.get("copies") or []),
        }
    name = plugin["name"]
    ref = plugin.get("ref") or f"{name}@{plugin['marketplace']}"
    return {
        "name": name,
        "ref": ref,
        "marketplace": plugin["marketplace"],
        "title": names["plugins"].get(ref) or plugin.get("display_name") or name,
        "description": plugin.get("description"),
        "version": plugin.get("version"),
        "category": plugin.get("category"),
        "tags": plugin.get("tags") or [],
        "author": plugin.get("author"),
        "homepage": plugin.get("homepage"),
        "listed": plugin.get("listed", True),
        "source_path": plugin.get("source_path"),
        "components": plugin.get("components") or {},
        "skills": [{"name": s["name"], "description": s.get("description") or ""}
                   for s in plugin.get("skills") or []],
        "harnesses": states,
        "drift": worst_drift(plugin.get("drift")),
    }


def _error(error: dict, home: Path) -> dict:
    """One catalog problem, with its folder written from ``~`` when it has one."""
    if not error.get("path"):
        return error
    path = Path(error["path"])
    short = f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)
    return {**error, "short_path": short}


def explore(home: Path) -> dict:
    """The Explore tab's data: the catalog's plugins once, keyed by ref,
    and the sources, marketplaces and rows that place them."""
    catalog = shared_catalog(home)
    names = display_names(home)
    plugins: dict = {}
    for market in catalog["marketplaces"]:
        for plugin in market["plugins"]:
            row = _plugin(plugin, names, catalog["actions"])
            plugins.setdefault(row["ref"], row)
    return {
        "harnesses": catalog["harnesses"],
        "actions": catalog["actions"],
        "errors": [_error(e, home) for e in catalog["errors"]],
        "total": len(plugins),
        "plugins": plugins,
        "sources": sources(catalog, plugins, names),
    }


def sources(catalog: dict, plugins: dict, names: dict | None = None) -> list[dict]:
    """PluginExploreIndex.sources: Local first, then each harness in swe's
    order, then Not registered; marketplaces and plugins in the catalog's
    order. Each row is ``{"ref", "harnesses"}``: the plugin and the
    harnesses whose chip it shows."""
    names = names or {"marketplaces": {}, "plugins": {}}
    actions = catalog["actions"]
    extra = sorted({h for m in catalog["marketplaces"] for h in m.get("harnesses") or {}}
                   - set(catalog["harnesses"]))
    order = list(catalog["harnesses"]) + extra

    def st(ref: str, harness: str) -> str:
        state = plugins[ref]["harnesses"].get(harness) if ref in plugins else None
        return state["status"] if state else "absent"

    def has(ref: str, harness: str) -> bool:
        return ref in plugins and harness in plugins[ref]["harnesses"]

    def market_row(source: str, market: dict, tag, rows: list) -> dict:
        return {"id": f"{source}/{market['name']}", "name": market["name"],
                "title": names["marketplaces"].get(market["name"]) or market["name"],
                "tag": tag, "plugins": rows}

    local, unregistered = [], []
    by_harness: dict = {}
    for market in catalog["marketplaces"]:
        registrations = market.get("harnesses") or {}
        refs = [p.get("ref") or f"{p['name']}@{market['name']}" for p in market["plugins"]]
        refs = [r for r in refs if r in plugins]
        if market.get("hub"):
            # Both harnesses' chips: the hub is meant for every harness swe
            # acts in, so a missing one is worth seeing.
            tag = next((registrations[h].get("source_type") for h in order
                        if h in registrations and registrations[h].get("source_type")), None)
            rows = [{"ref": r, "harnesses": [h for h in order if has(r, h)
                                             and (h in actions or st(r, h) != "absent")]}
                    for r in refs]
            local.append(market_row("local", market, _tag(tag or "directory"), rows))
        elif not registrations:
            rows = [{"ref": r, "harnesses": [h for h in order if st(r, h) != "absent"]} for r in refs]
            unregistered.append(market_row("unregistered", market, None, rows))
        else:
            registered = [h for h in order if h in registrations]
            # Under each harness, the plugins it lists or has. A copy
            # installed where the marketplace is no longer registered goes
            # under Not registered with that harness's chip; one no harness
            # lists or has goes under the first, so none is lost.
            placed: dict = {}
            orphans = []
            for r in refs:
                homes = [h for h in registered if st(r, h) != "absent"]
                left = [h for h in order if h not in registrations and st(r, h) in INSTALLED]
                if left:
                    orphans.append({"ref": r, "harnesses": left})
                if not homes and not left and registered:
                    homes = [registered[0]]
                for h in homes:
                    placed.setdefault(h, []).append(r)
            if orphans:
                unregistered.append(market_row("unregistered", market, None, orphans))
            for h in registered:
                if placed.get(h):
                    by_harness.setdefault(h, []).append(market_row(
                        h, market, _tag(registrations[h].get("source_type")),
                        [{"ref": r, "harnesses": [h]} for r in placed[h]]))

    out = []
    if local:
        out.append({"id": "local", "kind": "local", "harness": None, "title": "Local",
                    "note": "Your SWE hub, ~/.swe/plugins", "marketplaces": local})
    for h in order:
        if h not in by_harness:
            continue
        name = _NAMES.get(h) or h[:1].upper() + h[1:]
        out.append({"id": h, "kind": "harness", "harness": h, "title": name,
                    "note": f"Marketplaces {name} registers" if h in actions else "Read only",
                    "marketplaces": by_harness[h]})
    if unregistered:
        out.append({"id": "unregistered", "kind": "unregistered", "harness": None,
                    "title": "Not registered",
                    "note": "Installed from a marketplace its harness no longer lists",
                    "marketplaces": unregistered})
    return out
