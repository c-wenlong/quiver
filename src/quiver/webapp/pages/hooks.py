"""The Hooks page: ``swe hooks list``/``info``/``check`` built in process,
plus ``swe init`` for linking and ``swe hooks new`` for a skeleton.

``GET /v1/app/hooks`` is the Mac app's HooksPage list: every script under
``~/.quiver/hooks`` (harness, shared and foreign tiers) with its link
state and declarations, the ``swe hooks check`` findings, the paths a
plain ``swe init`` would leave a real file over, and the harness names
the New hook sheet offers. A ``.linkignore`` that cannot be read sets
``warning`` and the list is built without it, the way ``swe hooks``
reports the same thing; a check that fails sets ``check_error`` and the
list still shows, the Mac app's own rule.

``GET /v1/app/hooks?hook=<name>[&harness=<h>][&tier=<t>]`` is the detail: one row,
found the way ``swe hooks info`` finds it (exact filename, else stem;
``harness`` scopes it through the registry's aliases), plus the docstring
summary, whether the source exists and sits inside ``~/.quiver/hooks``
(the Mac app's edit rule — only those scripts may be edited, and the web
app edits nothing), the declarations in full, and the script's text when
it previews.

``POST /v1/app/hooks/link`` runs ``swe init`` and ``POST
/v1/app/hooks/replace`` runs ``swe init --force``, in process through the
Doctor page's ``run_init_now`` — the AGENTS.md page's pattern — so every
page shares the one-fix lock (409 ``busy``). ``POST /v1/app/hooks/new``
``{"harness", "name"}`` is ``swe hooks new``: the skeleton only, never a
settings file, so the reply carries the stanza to paste. ``harness`` of
``shared`` is ``swe hooks new shared``; anything else resolves through
the registry's aliases first. Everything else is a 404.
"""

from __future__ import annotations

import threading
from pathlib import Path

from quiver.webapp.pages import PageError, home_path
from quiver.webapp.pages.doctor import blocked_now
from quiver.webapp.pages.skills import _inside, read_text

# ``swe hooks new`` writes one hub file and reads the registry; serialize
# it with every other write the page makes, as skills.py does.
_WRITE_LOCK = threading.Lock()


def get(ctx, args: dict) -> dict:
    home = home_path(ctx)
    if args.get("hook"):
        return detail(ctx, home, args)
    return listing(ctx, home)


def post(ctx, action: str, body: dict) -> dict:
    if action == "new":
        if not isinstance(body, dict):
            raise PageError(422, "bad_body", "Send a JSON object.")
        return _new(ctx, home_path(ctx), body)
    force = {"link": False, "replace": True}.get(action)
    if force is None:
        raise PageError(404, "unknown_action", f"unknown hooks action: {action}")
    from quiver.webapp.pages.doctor import run_init_now

    home = home_path(ctx)
    error = run_init_now(home, force=force)
    return {"ran": action, "error": error, **listing(ctx, home)}


# -- the list ------------------------------------------------------------


def _inventory(home: Path):
    """All rows, their registry and the .linkignore patterns (with the
    warning a bad one sets), mirroring ``commands._rows``."""
    from quiver.hooks.inventory import all_rows, declarations_by_harness
    from quiver.init.hooks import plan_hooks
    from quiver.init.layout import (
        LinkIgnoreError,
        load_linkignore,
        load_registry,
    )

    registry = load_registry(home)
    warning = None
    try:
        patterns = load_linkignore(home)
    except LinkIgnoreError as exc:
        warning = str(exc) or type(exc).__name__
        patterns = []
    statuses = plan_hooks(home, patterns=patterns, registry=registry)
    declared = declarations_by_harness(home, registry)
    return all_rows(home, registry, statuses, declared), registry, patterns, warning


def _row_json(r) -> dict:
    """``commands._row_json`` plus the row's own ``detail`` note."""
    return {
        "name": r.script,
        "harness": r.harness,
        "tier": r.tier,
        "source_path": str(r.source),
        "dest_path": str(r.dest),
        "link_status": r.state,
        "enabled": r.enabled,
        "detail": r.detail,
        "declared": [{
            "event": d.event,
            "matcher": d.matcher,
            "file": str(d.settings),
        } for d in r.declared],
        "events_supported": r.events_supported,
    }


def _harnesses(registry: dict, rows) -> list[str]:
    """The New hook sheet's options: every non-archived registry key plus
    the harness names the rows carry (a dormant shared row's is
    ``shared``)."""
    names = {key for key, entry in registry.items()
             if not isinstance(entry, dict) or entry.get("state") != "archived"}
    names |= {r.harness for r in rows}
    return sorted(names)


def listing(ctx, home: Path) -> dict:
    """The list payload, ``GET /v1/app/hooks`` with no arguments."""
    rows, registry, patterns, warning = _inventory(home)
    try:
        from quiver.hooks.inventory import hook_findings

        findings = [{"kind": f.kind, "harness": f.harness, "message": f.message}
                    for f in hook_findings(home, registry=registry, patterns=patterns)]
        check_error = None
    except Exception as exc:  # noqa: BLE001 - the Mac app's checkError
        findings, check_error = [], str(exc) or type(exc).__name__
    return {
        "machine": ctx.machine.get("name"),
        "home": str(home),
        "hooks": [_row_json(r) for r in rows],
        "findings": findings,
        "check_error": check_error,
        "warning": warning,
        "blocked": blocked_now(home),
        "harnesses": _harnesses(registry, rows),
    }


# -- one hook -------------------------------------------------------------


def detail(ctx, home: Path, args: dict) -> dict:
    """``GET ?hook=<name>[&harness=<h>]``: the row plus the script itself."""
    from quiver import paths as _paths
    from quiver.hooks.commands import _resolve_harness
    from quiver.hooks.inventory import find_script, script_summary

    rows, registry, _patterns, _warning = _inventory(home)
    matches = find_script(args["hook"], rows)
    if args.get("harness"):
        wanted = _resolve_harness(args["harness"], registry, {r.harness for r in rows})
        matches = [r for r in matches if r.harness == wanted]
    # A shared script and a per-harness one can share name and harness:
    # only the tier tells them apart.
    if args.get("tier"):
        matches = [r for r in matches if r.tier == args["tier"]]
    if not matches:
        raise PageError(404, "unknown_hook",
                        f"No hook script named {args['hook']} under ~/.quiver/hooks.")
    row = matches[0]
    source = Path(row.source)
    exists = source.exists()
    # A foreign hook's command can name any file on disk; a paired
    # device reads its contents (header or text) only under home.
    readable = _inside(source, home)
    return {
        "machine": ctx.machine.get("name"),
        "home": str(home),
        **_row_json(row),
        "declared": [{
            "event": d.event,
            "matcher": d.matcher,
            "timeout": d.timeout,
            "command": d.command,
            "file": str(d.settings),
        } for d in row.declared],
        "summary": script_summary(source) if readable and source.is_file() else "",
        "exists": exists,
        "is_dir": source.is_dir(),
        # HookScript.editable: the source resolves inside the hooks root.
        "editable": exists and _inside(source, _paths.hooks_dir_for(home)),
        "text": read_text(source) if readable else None,
    }


# -- new -------------------------------------------------------------------


def _new(ctx, home: Path, body: dict) -> dict:
    from quiver.hooks import create
    from quiver.hooks.commands import _resolve_harness
    from quiver.init.layout import load_registry

    harness, name = body.get("harness"), body.get("name")
    for key, value in (("harness", harness), ("name", name)):
        if not isinstance(value, str) or len(value) > 256:
            raise PageError(422, "bad_hook", f"{key} must be the new hook's {key}")
    with _WRITE_LOCK:
        registry = load_registry(home)
        try:
            if harness == "shared":
                created = _new_shared(name, home, registry)
            else:
                # plan_hooks treats the folder name as a registry key, so an
                # alias resolves first, as in commands._new.
                resolved = _resolve_harness(harness, registry, set(registry))
                if problem := create.validate_args(resolved, name):
                    raise ValueError(problem)
                created = _new_hook(resolved, name, home, registry)
        except ValueError as exc:
            raise PageError(422, "bad_hook", str(exc)) from exc
        reply = listing(ctx, home)
    return {"action": "new", "created": created, **reply}


def _new_hook(harness: str, name: str, home: Path, registry: dict) -> dict:
    """``commands._new``: the skeleton plus the stanza to paste."""
    from quiver.hooks.contracts import contract_for
    from quiver.hooks.create import (
        command_for,
        create,
        settings_stanza,
        settings_targets,
    )

    target = create(harness, name, home)
    command, _note = command_for(harness, target.name, registry, home)
    stanza = settings_stanza(command, contract_for(registry, harness, home))
    targets = settings_targets(registry, harness, home)
    return {
        "path": str(target),
        "harness": harness,
        "command": command,
        "settings": [str(t) for t in targets],
        "stanza": stanza,
    }


def _new_shared(name: str, home: Path, registry: dict) -> dict:
    """``commands._new_shared``: the normalized-contract skeleton and the
    first deployable harness's stanza, when one exists."""
    from quiver.hooks import shared as _shared
    from quiver.hooks.contracts import contract_for
    from quiver.hooks.create import (
        create_shared,
        settings_stanza,
        shared_command_for,
    )

    target = create_shared(name, home)
    targets = [t for t in _shared.deploy_targets(home, registry) if t.source == target]
    commands = {t.harness: shared_command_for(t.harness, t.source.name, registry, home)
                for t in targets}
    stanza = None
    for harness_name in sorted(commands):
        command = commands[harness_name]
        if command:
            stanza = settings_stanza(command, contract_for(registry, harness_name, home))
            break
    return {
        "path": str(target),
        "harness": "shared",
        "tier": "shared",
        "commands": commands,
        "deploys_to": sorted(commands),
        "stanza": stanza,
    }
