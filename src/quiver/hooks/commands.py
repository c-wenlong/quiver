"""``swe hooks`` — manage the per-harness hook scripts under ~/.quiver/hooks.

``swe init`` links each script; the harness's settings file declares it.
Those are the two halves every subcommand joins: ``list`` shows link state
next to declaration, ``check`` reports where they disagree, ``info`` and
``edit`` work on one script, ``new`` writes the skeleton plus the stanza to
paste. The settings files are read-only here — declaring stays with you.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from quiver import paths as _paths
from quiver.console import c, sanitize, terminal_width
from quiver.flags import expand_value_flags
from quiver.init.hooks import plan_hooks
from quiver.init.layout import (
    LinkIgnoreError,
    aliases_of,
    load_linkignore,
    load_registry,
)
from quiver.json_output import emit, error_stream, take_json_flag
from quiver.hooks.create import (
    command_for,
    create,
    settings_stanza,
    settings_targets,
)
from quiver.hooks.inventory import (
    declarations_by_harness,
    find_script,
    hook_findings,
    hook_rows,
    script_summary,
)
from quiver.hooks.models import HookRow
from quiver.table import Table

SUBCOMMANDS = ("list", "info", "edit", "new", "check")

# Same vocabulary swe init prints; colour is what you would do about it.
_STATE_COLOR = {
    "linked": "green", "create": "cyan", "absorb": "cyan",
    "relink": "yellow", "conflict": "red", "keep": "yellow",
    "skipped": "dim", "ignored": "dim",
}


def print_hooks_help() -> None:
    from quiver.help_text import HELP

    summary, detail = HELP["hooks"]
    print(f"\n  {c('bold', 'swe hooks')} — {summary}\n")
    print(detail)
    print()


def cmd_hooks(args) -> int:
    args = list(args or [])
    if args and args[0] in ("-h", "--help", "help"):
        print_hooks_help()
        return 0
    args, json_view = take_json_flag(args)

    def fail(message: str) -> int:
        # --json promises stdout holds one document or nothing.
        print(message, file=error_stream(json_view))
        return 1

    # A leading flag means the default subcommand: `swe hooks --harness=x`
    # is `swe hooks list --harness=x`.
    if args and not args[0].startswith("-"):
        sub, rest = args[0], args[1:]
    else:
        sub, rest = "list", args
    if sub in ("list", "ls"):
        return _list(rest, json_view, fail)
    if sub == "info":
        return _info(rest, json_view, fail)
    if sub == "edit":
        return _edit(rest, fail)
    if sub == "new":
        return _new(rest, json_view, fail)
    if sub == "check":
        return _check(rest, json_view, fail)
    return fail(c("red", f"Unknown subcommand: {sub}. "
                       f"Use one of {', '.join(SUBCOMMANDS)}."))


# --- shared plumbing --------------------------------------------------------


def _harness_arg(args: list[str]) -> tuple[list[str], str | None]:
    """Pull a ``--harness=<name>`` option out; positionals stay behind.

    ``expand_value_flags`` has already split ``--harness=x`` into the pair
    ``("--harness", "x")`` by the time this runs, so both spellings land
    here — the pair form wins, the compact form is kept for callers that
    skipped expansion.
    """
    rest: list[str] = []
    harness = None
    i = 0
    while i < len(args):
        if args[i] == "--harness" and i + 1 < len(args):
            harness = args[i + 1]
            i += 2
        elif args[i].startswith("--harness="):
            harness = args[i].split("=", 1)[1]
            i += 1
        else:
            rest.append(args[i])
            i += 1
    return rest, harness


def _resolve_harness(name: str, registry: dict, known: set[str]) -> str:
    """The registry key ``--harness=<name>`` means; aliases resolve too."""
    if name in known:
        return name
    matches = [
        key for key, entry in registry.items()
        if isinstance(entry, dict) and name in aliases_of(entry)
    ]
    return matches[0] if len(matches) == 1 else name


def _rows(home: Path) -> tuple[list[HookRow], dict]:
    """All rows joined with declarations; a bad .linkignore warns, not dies."""
    registry = load_registry(home)
    try:
        patterns = load_linkignore(home)
    except LinkIgnoreError as exc:
        print(f"warning: {exc}; showing link states without it",
              file=sys.stderr)
        patterns = []
    statuses = plan_hooks(home, patterns=patterns, registry=registry)
    declared = declarations_by_harness(home, registry)
    return hook_rows(statuses, declared), registry


def _short(path: Path, home: Path) -> str:
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _declared_cell(row: HookRow) -> str:
    if not row.declared:
        return c("dim", "-")
    # sanitize strips our ANSI too, so it goes on the raw labels.
    labels = sorted({sanitize(d.label()) for d in row.declared})
    return ", ".join(labels)


def _state_cell(row: HookRow) -> str:
    colour = _STATE_COLOR.get(row.state, "dim")
    word = row.state
    if row.detail and row.state in ("skipped", "ignored", "relink", "conflict"):
        word += f" ({row.detail})"
    return c(colour, word)


# --- list -------------------------------------------------------------------


def _list(rest: list[str], json_view: bool, fail) -> int:
    try:
        rest = expand_value_flags(rest, {"--harness"})
    except ValueError as exc:
        return fail(c("red", str(exc)))
    rest, harness = _harness_arg(rest)
    if rest:
        return fail(c("red", f"Unexpected args: {' '.join(rest)}"))
    home = Path.home()
    rows, registry = _rows(home)
    if harness:
        harness = _resolve_harness(harness, registry, {r.harness for r in rows})
        rows = [r for r in rows if r.harness == harness]
        if not rows:
            return fail(c("red", f"No hook scripts under "
                                 f"~/.quiver/hooks/{harness}/"))

    if json_view:
        return emit({
            "hooks": [{
                "script": r.script,
                "harness": r.harness,
                "source": str(r.source),
                "destination": str(r.dest),
                "state": r.state,
                "detail": r.detail,
                "declared": [{
                    "event": d.event,
                    "matcher": d.matcher,
                    "timeout": d.timeout,
                    "command": d.command,
                    "settings": str(d.settings),
                } for d in r.declared],
            } for r in rows],
        })

    if not rows:
        print(c("dim", "\n  No hook scripts in ~/.quiver/hooks yet."))
        print(c("dim", "  swe hooks new <harness> <name> writes a skeleton.\n"))
        return 0

    print(f"\n{c('bold', 'Hooks')}  "
          f"{c('dim', _short(_paths.hooks_dir_for(home), home))}\n")
    table = Table()
    table.add_column("script", "SCRIPT", width=10, max_width=34)
    table.add_column("harness", "HARNESS", width=7, max_width=14)
    table.add_column("state", "LINK", width=5, max_width=44,
                     kind="preformatted")
    table.add_column("declared", "DECLARED", width=8, max_width=60)
    for r in rows:
        table.add_row({
            "script": sanitize(r.script),
            "harness": sanitize(r.harness),
            "state": _state_cell(r),
            "declared": _declared_cell(r),
        })
    for line in table.render():
        print(f"  {line}")
    linked = sum(1 for r in rows if r.state == "linked")
    summary = (f"{len(rows)} scripts · {linked} linked · "
               "swe hooks check finds the gaps")
    print(f"\n  {c('dim', summary)}\n")
    return 0


# --- info -------------------------------------------------------------------


def _info(rest: list[str], json_view: bool, fail) -> int:
    try:
        rest = expand_value_flags(rest, {"--harness"})
    except ValueError as exc:
        return fail(c("red", str(exc)))
    rest, harness = _harness_arg(rest)
    if len(rest) != 1:
        return fail(c("red", "Usage: swe hooks info <name> [--harness=<h>]"))
    home = Path.home()
    rows, registry = _rows(home)
    matches = find_script(rest[0], rows)
    if harness:
        harness = _resolve_harness(harness, registry, {r.harness for r in rows})
        matches = [r for r in matches if r.harness == harness]
    if not matches:
        return fail(c("red", f"No hook script named {rest[0]} under "
                             "~/.quiver/hooks."))

    if json_view:
        return emit({
            "script": rest[0],
            "harnesses": [{
                "harness": r.harness,
                "source": str(r.source),
                "destination": str(r.dest),
                "state": r.state,
                "detail": r.detail,
                "summary": script_summary(r.source),
                "declared": [{
                    "event": d.event,
                    "matcher": d.matcher,
                    "timeout": d.timeout,
                    "command": d.command,
                    "settings": str(d.settings),
                } for d in r.declared],
            } for r in matches],
        })

    print(f"\n{c('bold', sanitize(rest[0]))}\n")
    for r in matches:
        print(f"  {c('cyan', r.harness)}")
        print(f"    hub:    {_short(r.source, home)}")
        if r.source.is_dir():
            print(f"    {c('dim', 'a directory hook — scripts inside it')}")
        summary = script_summary(r.source) if r.source.is_file() else ""
        if summary:
            for line in _wrap(summary, terminal_width() - 12):
                print(f"    doc:    {c('dim', sanitize(line))}")
        dest = f"{_short(r.dest, home)}  {_state_cell(r)}"
        print(f"    dest:   {dest}")
        if r.declared:
            for d in r.declared:
                timeout = f", timeout {d.timeout:g}" if d.timeout else ""
                print(f"    decl:   {sanitize(d.label())}{c('dim', timeout)}"
                      f"  {c('dim', _short(d.settings, home))}")
        else:
            print(f"    decl:   {c('dim', 'not declared — the hook will not fire')}")
        print()
    return 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, max(20, width)) or [text]


# --- edit -------------------------------------------------------------------


def _editor_argv() -> list[str]:
    command = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        return shlex.split(command) or ["vi"]
    except ValueError:
        return ["vi"]


def _edit_target(row: HookRow) -> Path | None:
    """The real file to hand the editor — never a symlinked path.

    The hub copy is the file: a destination link resolves to it anyway, and
    an editor that saves by rename would otherwise replace the link with a
    regular file and split the hook in two. A hub path that is itself a
    symlink is resolved through; one that stays a symlink or does not exist
    is refused.
    """
    target = row.source
    try:
        if target.is_symlink():
            target = target.resolve()
    except OSError:
        return None
    if target.is_symlink() or not target.is_file():
        return None
    return target


def _edit(rest: list[str], fail) -> int:
    try:
        rest = expand_value_flags(rest, {"--harness"})
    except ValueError as exc:
        return fail(c("red", str(exc)))
    rest, harness = _harness_arg(rest)
    if len(rest) != 1:
        return fail(c("red", "Usage: swe hooks edit <name> [--harness=<h>]"))
    name = rest[0]
    home = Path.home()
    rows, registry = _rows(home)
    matches = find_script(name, rows)
    if harness:
        harness = _resolve_harness(harness, registry, {r.harness for r in rows})
        matches = [r for r in matches if r.harness == harness]
    if not matches:
        return fail(c("red", f"No hook script named {name} under ~/.quiver/hooks."))
    row = matches[0]
    if len(matches) > 1:
        if not sys.stdin.isatty():
            names = ", ".join(sorted({r.harness for r in matches}))
            return fail(c("red", f"{name} exists under {names} — "
                                 "which one? --harness=<name>"))
        print(f"\n  {name} exists under several harnesses:")
        for i, r in enumerate(matches, 1):
            print(f"    {i}. {r.harness}  {c('dim', _short(r.source, home))}")
        from quiver.prompt import read_line

        try:
            choice = read_line("  Edit which? [1]: ").strip() or "1"
        except EOFError:
            print()
            return 1
        try:
            index = int(choice)
            if not 1 <= index <= len(matches):
                raise IndexError
            row = matches[index - 1]
        except (ValueError, IndexError):
            return fail(c("red", f"No such choice: {choice}"))

    if row.source.is_dir():
        return fail(c("red", f"{name} is a directory hook — "
                             f"edit files inside {row.source}"))
    target = _edit_target(row)
    if target is None:
        return fail(c("red", f"{name} resolves to no real file — "
                             "refusing to open a symlinked path."))
    argv = _editor_argv() + [str(target)]
    try:
        done = subprocess.run(argv, check=False)
    except OSError as exc:
        return fail(c("red", f"Cannot run {argv[0]}: {exc}"))
    return int(done.returncode)


# --- new --------------------------------------------------------------------


def _new(rest: list[str], json_view: bool, fail) -> int:
    if len(rest) != 2:
        return fail(c("red", "Usage: swe hooks new <harness> <name>"))
    harness, name = rest
    home = Path.home()
    registry = load_registry(home)
    # plan_hooks treats the folder name as a registry key: `new cc gate`
    # must land under claude/, not an orphaned cc/ that never links.
    harness = _resolve_harness(harness, registry, set(registry))
    try:
        target = create(harness, name, home)
    except ValueError as exc:
        return fail(c("red", str(exc)))
    command, note = command_for(harness, target.name, registry, home)
    stanza = settings_stanza(command)
    targets = settings_targets(registry, harness, home)

    if json_view:
        return emit({
            "path": str(target),
            "harness": harness,
            "command": command,
            "settings": [str(t) for t in targets],
            "stanza": stanza,
        })

    print(f"\n  {c('green', '✓')} Created {_short(target, home)} (executable)\n")
    where = " or ".join(_short(t, home) for t in targets) \
        if targets else f"~/.{harness}/settings.json"
    print(f"  Declare it yourself — quiver never writes a settings file.")
    print(f"  Paste into {c('cyan', where)} (merge with events already there):\n")
    for line in stanza.splitlines():
        print(f"  {c('dim', line)}")
    print()
    if note:
        print(f"  {c('yellow', '!')} {note}")
    print(f"  {c('dim', 'Then swe init links it into place; swe hooks list shows both halves.')}\n")
    return 0


# --- check ------------------------------------------------------------------


def _check(rest: list[str], json_view: bool, fail) -> int:
    if rest:
        return fail(c("red", f"Unexpected args: {' '.join(rest)}"))
    home = Path.home()
    try:
        patterns = load_linkignore(home)
    except LinkIgnoreError as exc:
        print(f"warning: {exc}; checking without it", file=sys.stderr)
        patterns = []
    findings = hook_findings(home, patterns=patterns)
    if json_view:
        emit({
            "findings": [
                {"kind": f.kind, "harness": f.harness, "message": f.message}
                for f in findings
            ]
        })
        return 1 if findings else 0
    print(f"\n{c('bold', 'Hook drift')}\n")
    if not findings:
        print(f"  {c('green', '✓')} declared, linked and present all agree\n")
        return 0
    for f in findings:
        print(f"  {c('yellow', '!')} [{f.harness}] {sanitize(f.message)}")
    note = (f"{len(findings)} findings — swe init fixes the link "
            "side; declarations stay yours to edit")
    print(f"\n  {c('dim', note)}\n")
    return 1
