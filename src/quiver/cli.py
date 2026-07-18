#!/usr/bin/env python3
"""swe — Central manager for AI coding CLI tools."""

import sys

from quiver.harness.commands import (
    cmd_add,
    cmd_aliases,
    cmd_check,
    cmd_doctor,
    cmd_edit,
    cmd_info,
    cmd_install,
    cmd_list,
    cmd_remove,
    cmd_star,
    cmd_tags,
    cmd_unstar,
    cmd_use,
)
from quiver.help_text import cmd_help
from quiver.mcp import main as mcp_main
from quiver.providers import cli as providers_cli
from quiver.sessions.commands import cmd_models, cmd_session
from quiver.setup.commands import cmd_harness, cmd_setup
from quiver.skills.commands import cmd_skills


def cmd_mcp(args):
    return mcp_main(args)


def cmd_providers(args):
    return providers_cli.main(args)


def cmd_complete(args):
    """Hidden command for shell completion. Outputs candidates to stdout."""
    from quiver.completion import get_completions

    completions = get_completions(args)
    for candidate, desc in completions:
        print(f"{candidate}\t{desc}")
    # Directive: 4 = NoFileComp (suppress file completion fallback)
    print(":4")
    return 0


def cmd_autocomplete(args):
    """Generate and inject shell completion script."""
    from quiver.completion_scripts import SHELL_CONFIGS
    from quiver.console import c
    from quiver.paths import COMPLETION_DIR

    supported = list(SHELL_CONFIGS.keys())
    if not args or args[0] not in supported:
        print(f"Usage: swe autocomplete [{'|'.join(supported)}]")
        if args and args[0] not in supported:
            print(c("red", f"Unsupported shell: '{args[0]}'"))
        return 1

    shell = args[0]
    config = SHELL_CONFIGS[shell]
    import os

    # 1. Write completion script to ~/.config/swe/completions/
    script_dir = os.path.expanduser(str(COMPLETION_DIR))
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, config["filename"])
    try:
        with open(script_path, "w") as f:
            f.write(config["script"])
    except Exception as e:
        print(c("red", f"Failed to write completion script: {e}"))
        return 1

    # 2. Inject source line into shell profile (idempotent)
    profile_path = os.path.expanduser(config["profile"])
    source_line = f'source "{script_path}"  # swe autocomplete ({shell})'

    already_present = False
    try:
        if os.path.exists(profile_path):
            with open(profile_path) as f:
                content = f.read()
            if script_path in content or f"swe autocomplete ({shell})" in content:
                already_present = True
    except Exception:
        pass

    if not already_present:
        try:
            # Create profile if it doesn't exist
            os.makedirs(os.path.dirname(profile_path), exist_ok=True)
            with open(profile_path, "a") as f:
                f.write(f"\n{source_line}\n")
        except Exception as e:
            print(c("red", f"Failed to update {config['profile']}: {e}"))
            print(f"  Completion script written to: {script_path}")
            print(f"  Manually add this line to {config['profile']}:")
            print(f"    {source_line}")
            return 1

    # 3. Print success message
    print(f"Autocomplete script generated and injected successfully.")
    print(f"Please source your {shell} profile to apply the changes or restart your terminal.")
    print(f"For manual sourcing, use: {config['profile_instructions']}")
    return 0


COMMANDS = {
    "list": cmd_list,
    "ls": cmd_list,
    "info": cmd_info,
    "add": cmd_add,
    "edit": cmd_edit,
    "remove": cmd_remove,
    "rm": cmd_remove,
    "star": cmd_star,
    "favourite": cmd_star,
    "favorite": cmd_star,
    "unstar": cmd_unstar,
    "use": cmd_use,
    "run": cmd_use,
    "check": cmd_check,
    "doctor": cmd_doctor,
    "install": cmd_install,
    "session": cmd_session,
    "models": cmd_models,
    "skills": cmd_skills,
    "sk": cmd_skills,
    "tags": cmd_tags,
    "aliases": cmd_aliases,
    "mcp": cmd_mcp,
    "harness": cmd_harness,
    "setup": cmd_setup,
    "providers": cmd_providers,
    "pv": cmd_providers,
    "discover": lambda args: cmd_harness(["discover", *args]),
    "autocomplete": cmd_autocomplete,
    "help": cmd_help,
    "--help": cmd_help,
    "-h": cmd_help,
    # Hidden completion command (not shown in help)
    "__complete": cmd_complete,
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
