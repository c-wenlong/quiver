"""``swe providers`` — entry point for the providers subcommand group."""

from __future__ import annotations

import sys

from quiver.console import c
from quiver.providers.commands import PROVIDERS_COMMANDS


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        from quiver.providers.help_text import print_providers_help

        print_providers_help()
        return 0
    cmd = args[0]
    if cmd not in PROVIDERS_COMMANDS:
        print(c("red", f"  Unknown providers subcommand: '{cmd}'"))
        print(c("dim", f"  Available: {', '.join(PROVIDERS_COMMANDS.keys())}"))
        return 1
    result = PROVIDERS_COMMANDS[cmd](args[1:])
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
