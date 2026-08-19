"""CLI integration for Quiver's general configuration."""

from __future__ import annotations

import json
import shutil

from quiver.configuration import (
    CONFIG_FILE,
    ConfigurationError,
    check_config,
    dotted_get,
    dotted_set,
    dotted_unset,
    edit_config,
    interactive_report_setup,
    load_config,
    load_resolved_config,
    parse_config_value,
    report_setup_complete,
    save_config,
)
from quiver.console import c


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _setup_report() -> int:
    installed = [name for name in ("claude", "codex") if shutil.which(name)]
    if installed:
        print(c("dim", f"  Installed report harnesses: {', '.join(installed)}"))
    else:
        print(c("yellow", "  Claude and Codex were not found on PATH."))
    print(c("dim", "  Models are explicit; credentials remain owned by each harness.\n"))
    try:
        configured = interactive_report_setup(load_config())
        save_config(configured)
    except (ConfigurationError, EOFError, KeyboardInterrupt) as exc:
        print(c("red", f"  Report setup cancelled: {exc}"))
        return 1
    print(c("green", f"  Report configuration saved to {CONFIG_FILE}"))
    return 0


def cmd_config(args: list[str]) -> int:
    """View and update credential-free Quiver configuration."""

    if not args or args[0] in ("show", "list"):
        _print_json(load_resolved_config())
        return 0

    command = args[0]
    try:
        if command == "get":
            if len(args) != 2:
                raise ConfigurationError("Usage: swe config get <key>")
            value = dotted_get(load_resolved_config(), args[1], None)
            if value is None:
                print(c("red", f"  Unknown configuration key: {args[1]}"))
                return 1
            if isinstance(value, (dict, list)):
                _print_json(value)
            else:
                print(value)
            return 0

        if command == "set":
            if len(args) != 3:
                raise ConfigurationError("Usage: swe config set <key> <value>")
            updated = dotted_set(load_config(), args[1], parse_config_value(args[2]))
            save_config(updated)
            print(c("green", f"  Set {args[1]}"))
            return 0

        if command == "unset":
            if len(args) != 2:
                raise ConfigurationError("Usage: swe config unset <key>")
            save_config(dotted_unset(load_config(), args[1]))
            print(c("green", f"  Unset {args[1]}"))
            return 0

        if command == "edit":
            result = edit_config()
            if result:
                return result
            issues = check_config()
            if issues:
                for issue in issues:
                    print(c("red", f"  {issue}"))
                return 1
            return 0

        if command == "check":
            issues = check_config()
            if issues:
                for issue in issues:
                    print(c("red", f"  {issue}"))
                return 1
            resolved = load_resolved_config()
            if not report_setup_complete(resolved):
                print(c("yellow", "  Configuration is valid; report setup is incomplete."))
                print(c("dim", "  Run: swe config setup report"))
                return 1
            print(c("green", "  Configuration is valid."))
            return 0

        if command == "setup" and args[1:] == ["report"]:
            return _setup_report()

        raise ConfigurationError(f"Unknown config command: {command}")
    except ConfigurationError as exc:
        print(c("red", f"  {exc}"))
        return 1
