"""`swe hooks new`: write a Python hook skeleton into the hub.

The skeleton follows the house contract the other hooks under
``~/.swe/hooks`` keep (prose-guard.py is the reference): read the event
payload as JSON on stdin, exit 0 to let the call through, exit 2 to block
it and feed stderr back to the model, and fail open — an internal error
exits 0 because a crashing hook wedges every session it guards.

``create`` writes only the hub file and makes it executable. Declaring the
hook stays with the user: ``settings_stanza`` renders the exact JSON block
to paste, and the command tells them which file it belongs in.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from pathlib import Path

from swe import paths as _paths
from swe.hooks.contracts import HookContract
from swe.init.hooks import hook_root

# File and harness names travel into paths and a JSON command string;
# keep them to characters nothing has to escape.
_HARNESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_TEMPLATE = '''\
#!/usr/bin/env python3
"""{name}: describe what this hook gates in one line.

Reads the hook payload on stdin as JSON. Exit 0 lets the harness continue;
exit 2 blocks the turn and feeds stderr back to the model so it can adjust.
Any internal error exits 0 (fail open) - a crashing hook would wedge every
session it guards, which is worse than letting one bad call through.
"""
import json
import sys


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # The payload carries the event's input: session ids, transcript path,
    # the tool call being gated (tool_name, tool_input for PreToolUse).
    # Inspect it here; to block, say why on stderr and exit 2 instead.
    _ = payload

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Fail open: sys.exit(2) above still blocks; an unexpected error
        # inside the checks must not.
        sys.exit(0)
'''

# @NAME@ substitutes by str.replace — the template's own JSON braces
# ({"decision": "block"} below) rule .format() out.
_SHARED_TEMPLATE = '''\
#!/usr/bin/env python3
# events: [PreToolUse]
"""@NAME@: describe what this shared hook gates in one line.

The events header above declares the normalized (Claude-style, PascalCase)
event names it wants. `swe hooks sync` deploys a generated wrapper into
every installed shim-able harness covering at least one of them; swe's
shim translates the harness's payload into the normalized shape below.

stdin is one JSON object: event, harness, session_id, transcript_path,
cwd, tool_name, tool_input, tool_output, prompt, stop_hook_active,
session_source, agent_id, agent_type, agent_transcript_path,
last_assistant_message, error, permission_mode, tool_use_id, timestamp,
and raw (the harness's own payload, untouched).

Exit 0 passes. A JSON object on stdout answers in Claude's shape —
hookSpecificOutput.permissionDecision allow|deny|ask, decision
approve|block, reason, continue, stopReason — and the shim maps it back
per contract. Exit 2 + stderr blocks where the harness supports it. Any
internal error exits 0 (fail open): a crashing hook is worse than one
bad call through.
"""
import json
import sys


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # payload["event"] is the normalized name; payload["raw"] keeps the
    # harness's native fields. Inspect here; to block, say why on stderr
    # and exit 2, or print {"decision": "block", "reason": "..."}.
    _ = payload

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
'''


def validate_args(harness: str, name: str) -> str | None:
    """None when both names are safe, else the reason."""
    if not _HARNESS_RE.fullmatch(harness):
        return (f"{harness!r} isn't a harness name: letters, digits, "
                "'_' and '-', letter or digit first")
    if not _NAME_RE.fullmatch(name):
        return (f"{name!r} isn't a hook name: letters, digits, '_' and '-', "
                "letter or digit first")
    return None


def _publish(target: Path, text: str, name: str) -> Path:
    """Write ``text`` at ``target`` executable, atomically, no clobber.

    link() fails when target exists, so two concurrent creates cannot
    overwrite each other (os.replace would silently replace the first).
    """
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{name}.py.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o755)
        try:
            os.link(tmp, target)
        except FileExistsError as exc:
            raise ValueError(f"{name} already exists at {target}") from exc
        os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def create(harness: str, name: str,
           home: Path | None = None) -> Path:
    """Write ``~/.swe/hooks/<harness>/<name>.py``, executable, atomically.

    Raises ValueError for anything the user can fix (bad names, existing
    file, missing hub); the command turns it into exit 1.
    """
    if problem := validate_args(harness, name):
        raise ValueError(problem)
    home = home or Path.home()
    root = _paths.hooks_dir_for(home)
    if not _paths.swe_dir_for(home).is_dir():
        raise ValueError(f"{root.parent} doesn't exist — run swe init first")
    harness_dir = root / harness
    harness_dir.mkdir(parents=True, exist_ok=True)
    target = harness_dir / f"{name}.py"
    if target.exists() or target.is_symlink():
        raise ValueError(f"{name} already exists at {target}")
    return _publish(target, _TEMPLATE.format(name=name), name)


def create_shared(name: str, home: Path | None = None) -> Path:
    """Write ``~/.swe/hooks/shared/<name>.py`` — the normalized contract.

    The template carries the ``events:`` header the deploy step reads and
    the normalized payload spec as its docstring. Same rules as
    ``create``: executable, atomic, refuses an existing file.
    """
    if problem := validate_args("shared", name):
        raise ValueError(problem)
    home = home or Path.home()
    if not _paths.swe_dir_for(home).is_dir():
        raise ValueError(
            f"{_paths.swe_dir_for(home)} doesn't exist — run swe init first")
    from swe.hooks import shared as _shared

    directory = _shared.shared_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.py"
    if target.exists() or target.is_symlink():
        raise ValueError(f"{name} already exists at {target}")
    return _publish(target, _SHARED_TEMPLATE.replace("@NAME@", name), name)


def _command_path(path: Path, home: Path) -> str:
    """A runnable spelling of ``path`` for a settings command string.

    ``~`` only expands unquoted, so a path under home that needs quoting
    (spaces, say) spells ``"${HOME}"/'...'`` instead; a path outside home is
    quoted whole. The relative part is single-quoted rather than pasted
    inside the double quotes, where a ``"``, ``$`` or backtick in a
    directory name would end the string or run as a command.
    """
    try:
        rel = path.relative_to(home).as_posix()
    except ValueError:
        return shlex.quote(str(path))
    if shlex.quote(rel) == rel:
        return "~/" + rel
    return '"${HOME}"/' + shlex.quote(rel)


def command_for(harness: str, script: str,
                registry: dict | None = None,
                home: Path | None = None) -> tuple[str, str | None]:
    """The command a settings file should run, plus why it may differ.

    The linked destination is the right command — that is what ``swe init``
    puts in the harness's hooks dir. With no hooks root for the harness the
    hub copy is the only path that exists; it still works (the file is
    executable), and the reason says so.
    """
    home = home or Path.home()
    root, _why = hook_root(registry or {}, harness, home)
    if root is not None:
        return _command_path(root / script, home), None
    hub = _paths.hooks_dir_for(home) / harness / script
    return _command_path(hub, home), \
        f"no hooks root known for {harness}: the command points at " \
        "the hub copy — set capabilities.hooks.root and run swe init " \
        "to link it in"


def shared_command_for(harness: str, script: str,
                       registry: dict | None = None,
                       home: Path | None = None) -> str | None:
    """The command a settings file should run for a shared hook's wrapper.

    ``swe hooks sync`` generates the wrapper at
    ``<hooks root>/_generated/<script>`` — that path is what a harness's
    settings file names. None when the harness has no hooks root.
    """
    from swe.hooks import shared as _shared

    home = home or Path.home()
    root, _why = hook_root(registry or {}, harness, home)
    if root is None:
        return None
    return _command_path(
        root / _shared.GENERATED_DIRNAME / script, home)


def settings_stanza(command: str,
                    contract: HookContract | None = None) -> str:
    """The ``hooks`` block to paste, in the contract's own schema.

    No contract means Claude's grouped shape (the ``swe hooks new``
    default). A flat contract (cursor's archetype) gets
    ``{version, hooks: {event: [{command}]}}`` instead; the event is the
    contract's native ``PreToolUse`` — gemini's ``BeforeTool`` — and the
    timeout obeys ``timeout_unit`` (gemini counts milliseconds).
    """
    event = "PreToolUse"
    timeout = 10
    if contract is not None:
        for native, normalized in zip(
                contract.events, contract.normalized_events()):
            if normalized == "PreToolUse":
                event = native
                break
        else:
            if contract.events:
                event = contract.events[0]
        if contract.timeout_unit == "ms":
            timeout = 10000
    if contract is not None and contract.schema == "flat":
        body = {
            "version": 1,
            "hooks": {
                event: [
                    {"command": command, "timeout": timeout}
                ]
            },
        }
    else:
        body = {
            "hooks": {
                event: [
                    {
                        "matcher": "*",
                        "hooks": [
                            {"type": "command", "command": command,
                             "timeout": timeout}
                        ],
                    }
                ]
            }
        }
    return json.dumps(body, indent=2)


def settings_targets(registry: dict, harness: str, home: Path) -> list[Path]:
    """Where the stanza goes, for the message the command prints."""
    from swe.hooks.settings import (
        SETTINGS_FALLBACK,
        configured_settings,
        settings_files,
    )

    files = settings_files(registry, harness, home)
    if files:
        return files
    # Nothing on disk yet: a configured settings path still wins — that is
    # the file the user declared, create it there. Then the file the
    # harness is known to use (claude's settings.json, codex's hooks.json),
    # then the conventional one beside a declared hooks root.
    configured = configured_settings(registry, harness, home)
    if configured:
        return configured
    tabled = SETTINGS_FALLBACK.get(harness)
    if tabled:
        return [home / tabled[0]]
    root, _why = hook_root(registry, harness, home)
    if root is not None and root.parent != home:
        return [root.parent / "settings.json"]
    return []
