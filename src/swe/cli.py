#!/usr/bin/env python3
"""swe — Central manager for AI coding CLI tools."""

import platform
import sys

from swe import __version__
from swe.bug import cmd_bug
from swe.json_output import emit, error_stream, take_json_flag
from swe.config_commands import cmd_config
from swe.configuration import ConfigurationError
from swe.console import c
from swe.harness.commands import (
    cmd_add,
    cmd_aliases,
    cmd_check,
    cmd_doctor,
    cmd_edit,
    cmd_info,
    cmd_install,
    cmd_list,
    cmd_remove,
    cmd_tags,
    cmd_use,
)
from swe.find import cmd_find
from swe.help_text import cmd_help
from swe.hooks import cmd_hooks
from swe.init import cmd_init
from swe.instructions.commands import cmd_instructions
from swe.github import main as github_main
from swe.mcp import main as mcp_main
from swe.plugins import cmd_plugins
from swe.providers import cli as providers_cli
from swe.reports.commands import cmd_report
from swe.sessions.commands import cmd_models, cmd_session
from swe.setup.commands import cmd_harness, cmd_setup
from swe.skills.commands import cmd_skills
from swe.sync import cmd_sync


def cmd_mcp(args):
    return mcp_main(args)


def cmd_gh(args):
    return github_main(args)


def cmd_providers(args):
    return providers_cli.main(args)


def cmd_remote(args):
    # Function-local on purpose: swe.remote pulls sessions, reports and
    # the hub in, and `swe version` shouldn't pay for any of them.
    from swe.remote import cmd_remote as remote_main
    return remote_main(args)


_HELP_NAME = {"-v": "version", "-V": "version", "--version": "version"}


def cmd_version(args):
    """swe version / -v / -V / --version. Reads no config, so it works even
    when ~/.swe is unparseable."""
    args, json_view = take_json_flag(args)
    if args:
        print(c("red", f"Unexpected args: {' '.join(args)}"),
              file=error_stream(json_view))
        return 1
    if json_view:
        return emit({"name": "swe", "version": __version__,
                     "python": platform.python_version()})
    print(f"swe {__version__}")
    return 0


def cmd_complete(args):
    """Hidden command for shell completion. Outputs candidates to stdout."""
    from swe.completion import get_completions
    from swe.console import sanitize

    completions = get_completions(args)
    for candidate, desc in completions:
        # One candidate per line is the whole protocol — a server name or
        # description carrying \n or \t would inject extra lines into it.
        print(f"{sanitize(candidate)}\t{sanitize(desc)}")
    # Directive: 4 = NoFileComp (suppress file completion fallback)
    print(":4")
    return 0


def cmd_autocomplete(args):
    """Generate and inject shell completion script."""
    from swe.completion_scripts import SHELL_CONFIGS
    from swe.console import c
    from swe.paths import COMPLETION_DIR

    supported = list(SHELL_CONFIGS.keys())
    if not args or args[0] not in supported:
        print(f"Usage: swe autocomplete [{'|'.join(supported)}]")
        if args and args[0] not in supported:
            print(c("red", f"Unsupported shell: '{args[0]}'"))
        return 1
    if len(args) > 1:
        print(c("red", f"Unexpected args: {' '.join(args[1:])}"))
        return 1

    shell = args[0]
    config = SHELL_CONFIGS[shell]
    import os

    # 1. Write completion script to ~/.swe/completions/
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
    # Shortcuts for `swe harness list`, which is where the harness verbs
    # live. Kept at the top level because it is the command run most.
    "list": cmd_list,
    "ls": cmd_list,
    "info": cmd_info,
    "add": cmd_add,
    "edit": cmd_edit,
    "remove": cmd_remove,
    "rm": cmd_remove,
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
    "gh": cmd_gh,
    "harness": cmd_harness,
    "hooks": cmd_hooks,
    "hs": cmd_harness,
    "setup": cmd_setup,
    "init": cmd_init,
    "instructions": cmd_instructions,
    "find": cmd_find,
    "plugins": cmd_plugins,
    "providers": cmd_providers,
    "pv": cmd_providers,
    "config": cmd_config,
    "remote": cmd_remote,
    "sync": cmd_sync,
    "report": cmd_report,
    "discover": lambda args: cmd_harness(["discover", *args]),
    "autocomplete": cmd_autocomplete,
    "bug": cmd_bug,
    "version": cmd_version,
    "-v": cmd_version,
    "-V": cmd_version,
    "--version": cmd_version,
    "help": cmd_help,
    "--help": cmd_help,
    "-h": cmd_help,
    # Hidden completion command (not shown in help)
    "__complete": cmd_complete,
}


def main():
    argv = sys.argv[1:]
    if not argv:
        try:
            cmd_help([])
        except ConfigurationError as exc:
            print(c("red", str(exc)))
            return 1
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd in COMMANDS:
        try:
            if rest and rest[0] in ("--help", "-h"):
                # providers carries its own fuller help text; routing
                # through cmd_help would print the summary that ends by
                # pointing back at `swe providers --help` itself.
                if cmd in ("providers", "pv"):
                    return COMMANDS[cmd](rest)
                # The version flags are spellings of `version`; the help
                # topic lives under the canonical name.
                cmd_help([_HELP_NAME.get(cmd, cmd)])
                return 0
            result = COMMANDS[cmd](rest)
        except ConfigurationError as exc:
            # Under --json the app decodes stdout whole, so the message goes
            # to stderr there; everywhere else it stays where it always was.
            print(c("red", str(exc)), file=error_stream("--json" in rest))
            return 1
        return result if isinstance(result, int) else 0
    print(f"Unknown command: '{cmd}'")
    try:
        cmd_help([])
    except ConfigurationError as exc:
        print(c("red", str(exc)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
