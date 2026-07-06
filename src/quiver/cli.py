#!/usr/bin/env python3
"""swe — Central manager for AI coding CLI tools."""

import sys

from quiver.harness.commands import (
    cmd_add,
    cmd_aliases,
    cmd_check,
    cmd_info,
    cmd_list,
    cmd_remove,
    cmd_tags,
    cmd_use,
)
from quiver.help_text import cmd_help
from quiver.mcp import main as mcp_main
from quiver.sessions.commands import cmd_models, cmd_session
from quiver.skills.commands import cmd_skills


def cmd_mcp(args):
    return mcp_main(args)


COMMANDS = {
    "list": cmd_list,
    "ls": cmd_list,
    "info": cmd_info,
    "add": cmd_add,
    "remove": cmd_remove,
    "rm": cmd_remove,
    "use": cmd_use,
    "run": cmd_use,
    "check": cmd_check,
    "session": cmd_session,
    "models": cmd_models,
    "skills": cmd_skills,
    "sk": cmd_skills,
    "tags": cmd_tags,
    "aliases": cmd_aliases,
    "mcp": cmd_mcp,
    "help": cmd_help,
    "--help": cmd_help,
    "-h": cmd_help,
}


def main():
    argv = sys.argv[1:]
    if not argv:
        cmd_help([])
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd in COMMANDS:
        if rest and rest[0] in ("--help", "-h"):
            cmd_help([cmd])
            return 0
        result = COMMANDS[cmd](rest)
        return result if isinstance(result, int) else 0
    print(f"Unknown command: '{cmd}'")
    cmd_help([])
    return 1


if __name__ == "__main__":
    sys.exit(main())
