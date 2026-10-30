"""Presentation and dispatch for ``swe instructions``."""

from __future__ import annotations

import sys

from quiver.instructions import compiler, watcher

SUBCOMMANDS = ("build", "check", "watch")


def cmd_instructions(args) -> int:
    args = list(args or [])
    if not args or args in (["help"], ["--help"], ["-h"]):
        from quiver.help_text import cmd_help

        cmd_help(["instructions"])
        return 0
    sub, rest = args[0], args[1:]
    if sub not in SUBCOMMANDS:
        print(f"Unknown instructions command: {sub!r}", file=sys.stderr)
        return 1
    if rest in (["--help"], ["-h"]):
        from quiver.help_text import cmd_help

        cmd_help(["instructions"])
        return 0
    if rest:
        print(f"Unexpected args: {' '.join(rest)}", file=sys.stderr)
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
        changed = compiler.build(check=sub == "check")
    except KeyboardInterrupt:
        if sub == "watch":
            print("Stopped watching instructions.")
            return 0
        return 130
    except (compiler.InstructionError, OSError) as exc:
        report_error(exc)
        return 1

    if sub == "check":
        if changed:
            print("Stale or missing: ~/.quiver/AGENTS.md. Run swe instructions build.")
            return 1
        print("Current: ~/.quiver/AGENTS.md")
    else:
        report(changed)
    return 0
