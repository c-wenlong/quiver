"""Machine-readable output shared by every ``--json`` flag.

The macOS app under ``app/mac/`` decodes stdout whole, so a ``--json`` command
prints exactly one document, ``{"schema": SCHEMA, "data": ...}``, and
nothing else. Bump SCHEMA only for a breaking change to an existing shape;
adding a field is not one.
"""

import json
import sys

SCHEMA = 1


def take_json_flag(args: list[str]) -> tuple[list[str], bool]:
    """Strip ``--json`` from ``args``; report whether it was there."""
    rest = [arg for arg in args if arg != "--json"]
    return rest, len(rest) != len(args)


def emit(data) -> int:
    """Print the envelope for ``data`` and return exit status 0."""
    sys.stdout.write(
        json.dumps({"schema": SCHEMA, "data": data}, indent=2, default=str)
        + "\n"
    )
    sys.stdout.flush()
    return 0


def error_stream(json_view: bool):
    """stderr under ``--json``, stdout otherwise.

    A refusal must not corrupt the document on stdout, which the app
    decodes whole, so errors switch streams when ``--json`` is set.
    """
    return sys.stderr if json_view else sys.stdout
