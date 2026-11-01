"""Presentation and dispatch for ``swe instructions``."""

from __future__ import annotations

import sys

from swe.instructions import compiler, watcher
from swe.json_output import emit, take_json_flag

SUBCOMMANDS = ("build", "check", "init", "watch")


def cmd_instructions(args) -> int:
    args = list(args or [])
    if not args or args in (["help"], ["--help"], ["-h"]):
        from swe.help_text import cmd_help

        cmd_help(["instructions"])
        return 0
    sub, rest = args[0], args[1:]
    if sub not in SUBCOMMANDS:
        print(f"Unknown instructions command: {sub!r}", file=sys.stderr)
        return 1
    if rest in (["--help"], ["-h"]):
        from swe.help_text import cmd_help

        cmd_help(["instructions"])
        return 0
    rest, json_view = take_json_flag(rest)
    if rest:
        print(f"Unexpected args: {' '.join(rest)}", file=sys.stderr)
        return 1
    if json_view and sub == "watch":
        print("--json only works with build, check and init: "
              "swe instructions check --json", file=sys.stderr)
        return 1

    def report(changed: bool) -> None:
        action = "Built" if changed else "Unchanged:"
        print(f"{action} ~/.quiver/AGENTS.md", flush=True)

    def report_error(exc: Exception) -> None:
        print(f"Instructions: {exc}", file=sys.stderr, flush=True)

    try:
        if sub == "watch":
            print("Watching ~/.quiver/AGENTS_MD (Ctrl-C to stop)", flush=True)
            watcher.watch(on_result=report, on_error=report_error)
            return 0
        if sub == "init":
            result = compiler.init_sources()
        elif sub == "check" and json_view:
            document = compiler.survey()
            emit(document)
            return 1 if document["error"] or document["stale"] else 0
        else:
            changed = compiler.build(check=sub == "check")
    except KeyboardInterrupt:
        if sub == "watch":
            print("Stopped watching instructions.")
            return 0
        return 130
    except (compiler.InstructionError, OSError) as exc:
        report_error(exc)
        return 1

    if sub == "init":
        if json_view:
            return emit(result)
        print(f"Created ~/.quiver/AGENTS_MD/{result['chapter']} from {result['source']}")
        report(result["built"])
        return 0
    if sub == "build" and json_view:
        return emit({"changed": changed})
    if sub == "check":
        if changed:
            print("Stale or missing: ~/.quiver/AGENTS.md. Run swe instructions build.")
            return 1
        print("Current: ~/.quiver/AGENTS.md")
    else:
        report(changed)
    return 0
