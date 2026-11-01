"""Exec a harness with one provider's keys in its environment.

A launch with a provider runs this file in the tmux pane in front of
the harness::

    python3 -I provider_exec.py --keys=<keys file> --spec=<json> -- argv...

``--spec`` is the provider entry from ``launch_provider.capability``:
variable names and fixed values such as a base URL, never a key. This
process reads the keys file itself, sets exactly the entry's variables,
drops every other variable the keys file names (a hub started from a
shell that sourced the keys file would otherwise pass them all down),
and ``exec``s the harness under the same pid. So a key value is never
on any process's argv, in the tmux command, in a log, or in a file
other than the keys file.

The file runs by path with ``-I``, so it puts its own package root on
``sys.path`` rather than trusting the pane's PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def build_env(base: dict[str, str], spec: dict,
              keys: dict[str, str]) -> dict[str, str]:
    """``base`` minus every keys-file variable, plus ``spec``'s
    variables filled from ``keys``. Raises ValueError (names only) when
    a key is missing."""
    env = {k: v for k, v in base.items() if k not in keys}
    for target, sources in (spec.get("keys") or {}).items():
        value = next((keys[s] for s in sources if keys.get(s)), None)
        if value is None:
            raise ValueError(f"no key for {target}: set "
                             f"{' or '.join(sources)} in the keys file")
        env[target] = value
    for target, value in (spec.get("set") or {}).items():
        env[target] = value
    return env


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("provider_exec: missing -- before the command", file=sys.stderr)
        return 2
    split = argv.index("--")
    opts, command = argv[:split], argv[split + 1:]
    keys_path = spec_text = ""
    for opt in opts:
        if opt.startswith("--keys="):
            keys_path = opt[len("--keys="):]
        elif opt.startswith("--spec="):
            spec_text = opt[len("--spec="):]
    if not command or not keys_path or not spec_text:
        print("provider_exec: needs --keys, --spec and a command",
              file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from swe.remote.launch_provider import read_keys

    try:
        spec = json.loads(spec_text)
        env = build_env(dict(os.environ), spec, read_keys(Path(keys_path)))
    except (ValueError, TypeError, AttributeError) as exc:
        print(f"swe: can't start with this provider: {exc}", file=sys.stderr)
        return 1
    try:
        os.execvpe(command[0], command, env)
    except OSError as exc:
        print(f"swe: can't start {command[0]}: {exc.strerror or exc}",
              file=sys.stderr)
        return 127
    return 0  # pragma: no cover - execvpe does not return


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
