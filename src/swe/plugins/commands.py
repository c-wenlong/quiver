"""``swe plugins``: the plugin catalog, and per-harness install actions.

A domain command of its own rather than more flags on ``swe find plugins``:
``find`` is the read-only view of what is installed, and its ``--harness``
already means active|all. Here ``--harness`` names the harness to act in.
"""

from __future__ import annotations

from pathlib import Path

from swe.console import c, sanitize, truncate
from swe.flags import expand_value_flags
from swe.json_output import emit, error_stream, take_json_flag
from swe.plugins.actions import ACTIONS, ActionError, plugin_action
from swe.plugins.catalog import ACTIONABLE, build_catalog
from swe.table import Table

SUBCOMMANDS = ("catalog", "marketplace", *ACTIONS)
_DONE = {"install": "installed", "uninstall": "uninstalled",
         "enable": "enabled", "disable": "disabled"}


def print_plugins_help() -> None:
    from swe.help_text import HELP

    summary, detail = HELP["plugins"]
    print(f"\n  {c('bold', 'swe plugins')} — {summary}\n")
    print(detail)
    print()


def cmd_plugins(args) -> int:
    args = list(args or [])
    if args and args[0] in ("-h", "--help", "help"):
        print_plugins_help()
        return 0
    args, json_view = take_json_flag(args)

    def fail(message: str) -> int:
        # --json promises stdout holds one document or nothing.
        print(message, file=error_stream(json_view))
        return 1

    sub = args[0] if args else "catalog"
    rest = args[1:]
    if sub == "catalog":
        return _catalog(rest, json_view, fail)
    if sub == "marketplace":
        return _marketplace(rest, json_view, fail)
    if sub in ACTIONS:
        return _action(sub, rest, json_view, fail)
    return fail(c("red", f"Unknown subcommand: {sub}. Use one of {', '.join(SUBCOMMANDS)}."))


def _marketplace(rest: list[str], json_view: bool, fail) -> int:
    """Only `add` for now: register a source in each --harness."""
    from swe.plugins.actions import ActionError, marketplace_add

    if not rest or rest[0] != "add":
        return fail(c("red", "Usage: swe plugins marketplace add <source> "
                             "--harness=<claude|codex>[,…] [--json]"))
    rest = rest[1:]
    source, harnesses = None, []
    for arg in rest:
        if arg.startswith("--harness="):
            harnesses = [h for h in arg[10:].split(",") if h]
        elif arg.startswith("--"):
            return fail(c("red", f"Unknown flag: {arg}"))
        elif source is None:
            source = arg
        else:
            return fail(c("red", f"Unexpected arg: {arg}"))
    if source is None:
        return fail(c("red", "Usage: swe plugins marketplace add <source> "
                             "--harness=<claude|codex>[,…] [--json]"))
    if not harnesses:
        return fail(c("red", f"--harness is required: --harness={'|'.join(ACTIONABLE)}"))
    try:
        result = marketplace_add(Path.home(), source, harnesses)
    except ActionError as exc:
        return fail(c("red", str(exc)))
    if json_view:
        emit(result)
    else:
        for r in result["results"]:
            mark = c("green", "✓") if r["ok"] else c("red", "✗")
            print(f"  {mark} {r['harness']}: {r['detail']}")
    return 0 if all(r["ok"] for r in result["results"]) else 1


def _catalog(rest: list[str], json_view: bool, fail) -> int:
    if len(rest) > 1 or any(a.startswith("-") for a in rest):
        return fail(c("red", f"Unexpected args: {' '.join(rest)}"))
    data = build_catalog(Path.home())
    if rest:
        wanted = rest[0]
        data["marketplaces"] = [m for m in data["marketplaces"] if m["name"] == wanted]
        if not data["marketplaces"]:
            return fail(c("red", f"No marketplace named {wanted}"))
    if json_view:
        return emit(data)
    if rest:
        _render_plugins(data, data["marketplaces"][0])
    else:
        _render_marketplaces(data)
    return 0


def _action(action: str, rest: list[str], json_view: bool, fail) -> int:
    try:
        rest = expand_value_flags(rest, {"--harness"})
    except ValueError as exc:
        return fail(c("red", str(exc)))
    harness = None
    refs = []
    i = 0
    while i < len(rest):
        if rest[i] == "--harness":
            harness = rest[i + 1]
            i += 2
        elif rest[i].startswith("-"):
            return fail(c("red", f"Unknown flag: {rest[i]}"))
        else:
            refs.append(rest[i])
            i += 1
    if len(refs) != 1:
        return fail(c("red", f"Usage: swe plugins {action} <plugin>@<marketplace> "
                             f"--harness={'|'.join(ACTIONABLE)}"))
    if harness is None:
        return fail(c("red", f"--harness is required: --harness={'|'.join(ACTIONABLE)}"))
    try:
        result = plugin_action(Path.home(), action, refs[0], harness)
    except ActionError as exc:
        return fail(c("red", str(exc)))
    if json_view:
        return emit(result)
    if result["changed"]:
        print(f"  {c('green', '✓')} {harness} {result['ref']}: {_DONE[action]}")
    else:
        print(f"  {c('dim', '·')} {harness} {result['ref']}: {result['detail']}")
    return 0


# --------------------------------------------------------------------------
# Rendering


def _installed_counts(market: dict, harnesses: list[str]) -> str:
    parts = []
    for harness in harnesses:
        states = [p["harnesses"][harness] for p in market["plugins"]]
        n = sum(1 for s in states if s["installed"])
        if n:
            parts.append(f"{harness} {n}")
    return " · ".join(parts) or "-"


def _render_marketplaces(data: dict) -> None:
    markets = data["marketplaces"]
    if not markets:
        print(c("dim", "\n  No plugin marketplaces registered in claude, codex or cursor.\n"))
        return
    harnesses = data["harnesses"]
    table = Table()
    table.add_column("name", "MARKETPLACE", width=12, max_width=30)
    table.add_column("source", "SOURCE", width=10, max_width=12)
    table.add_column("in", "REGISTERED IN", width=13, max_width=22)
    table.add_column("plugins", "PLUGINS", width=7, kind="number")
    table.add_column("installed", "INSTALLED", width=10, max_width=40)
    for market in markets:
        sources = sorted({h["source_type"] for h in market["harnesses"].values()})
        table.add_row({
            "name": sanitize(market["name"]) + (" (hub)" if market["hub"] else ""),
            "source": sanitize(", ".join(sources)) or "-",
            "in": ", ".join(market["harnesses"]) or "nowhere",
            "plugins": len(market["plugins"]),
            "installed": _installed_counts(market, harnesses),
        })
    total = sum(len(m["plugins"]) for m in markets)
    print(f"\n{c('bold', 'Plugin catalog')}  "
          f"{c('dim', f'{total} plugins in {len(markets)} marketplaces')}\n")
    for line in table.render():
        print(f"  {line}")
    _render_errors(data["errors"])
    print(f"\n  {c('dim', 'swe plugins catalog <marketplace> lists its plugins  ·  --json for the app')}\n")


_STATE_WORD = {True: ("green", "on"), False: ("yellow", "off"), None: ("dim", "?")}


def _state_cell(state: dict) -> str:
    if state["installed"] is None:
        return c("dim", "cached") if state["path"] else c("dim", "-")
    if not state["installed"]:
        return c("dim", "available") if state["available"] else c("dim", "-")
    colour, word = _STATE_WORD[state["enabled"]]
    return c(colour, word)


def _render_plugins(data: dict, market: dict) -> None:
    harnesses = data["harnesses"]
    table = Table()
    table.add_column("name", "PLUGIN", width=12, max_width=34)
    for harness in harnesses:
        table.add_column(harness, harness.upper(), width=9, kind="preformatted")
    table.add_column("desc", "DESCRIPTION", width=20, max_width=70)
    for plugin in market["plugins"]:
        row = {"name": sanitize(plugin["name"]),
               "desc": truncate(sanitize(plugin["description"] or ""), 70)}
        for harness in harnesses:
            row[harness] = _state_cell(plugin["harnesses"][harness])
        table.add_row(row)
    where = sanitize(", ".join(f"{h} {m['source_type']} {m['location']}"
                               for h, m in market["harnesses"].items()) or "registered nowhere")
    print(f"\n{c('bold', sanitize(market['name']))}  {c('dim', where)}\n")
    for line in table.render():
        print(f"  {line}")
    _render_errors([e for e in data["errors"] if e["marketplace"] == market["name"]])
    print(f"\n  {c('dim', 'on/off: installed and enabled or disabled · available: listed, not installed')}")
    print(f"  {c('dim', 'swe plugins install <plugin>@' + sanitize(market['name']) + ' --harness=claude')}\n")


def _render_errors(errors: list) -> None:
    for err in errors:
        where = err["harness"] + (f" {err['marketplace']}" if err["marketplace"] else "")
        print(f"  {c('yellow', '!')} {c('dim', sanitize(where + ': ' + err['detail']))}")
