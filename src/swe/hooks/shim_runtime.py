#!/usr/bin/env python3
"""Shared-hook shim runtime — copied verbatim to ~/.swe/hooks/_runtime/shim.py.

It is deliberately stdlib-only and imports nothing from swe: a harness
runs the generated wrapper under whatever ``python3`` it finds, which
cannot import this package. Tests import it directly; ``swe init`` and
``swe hooks sync`` copy it into the hub so generated stubs can call it by
absolute path.

Invocation: ``python3 shim.py --contract '<json>' -- <shared> [args...]``

``--contract`` carries one JSON object the generator baked from the
harness's row in ``hooks/contracts.py``::

    {"harness": "cursor", "schema": "flat", "event_map": {...},
     "fields": {...}, "must_print_json": true}

stdin is the harness's hook payload. The runtime translates it to the
normalized shape documented in ``contracts.py``, execs the shared script
with it on stdin, then maps the script's exit code and stdout back into
the harness's contract. Anything after ``-- <shared>`` is passed to the
script as argv; the first such arg also serves as the native event name
when the payload does not carry ``hook_event_name`` (cursor declarations
can pass the event as an argument, the way superset's hooks do).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

# Normalized fields in output order. ``raw`` and ``timestamp`` are added
# separately: raw is the untouched payload, timestamp is the shim's own.
_FIELD_DEFAULTS = (
    "session_id", "transcript_path", "cwd", "tool_name", "tool_input",
    "tool_output", "prompt", "stop_hook_active", "session_source",
    "agent_id", "agent_type", "agent_transcript_path",
    "last_assistant_message", "error", "permission_mode", "tool_use_id",
)

# Normalized field -> native keys to try after the field's own name. The
# per-contract ``fields`` table adds more on top of these.
_ALIASES = {
    "session_id": ("conversation_id",),
    "tool_output": ("tool_response",),
    "session_source": ("source",),
    "agent_transcript_path": ("agent_transcript",),
    "error": ("error_message",),
}


def normalize(raw: dict, contract: dict, argv_event: str) -> tuple[dict, str]:
    """(normalized payload, native event name) for the shared script.

    Unknown event names pass through as themselves — the map only holds
    renames, so a harness event swe never surveyed still reaches the
    script with its native spelling intact.
    """
    if not isinstance(raw, dict):
        raw = {}
    # The payload's own event name wins; argv is the documented fallback
    # for harnesses whose payload lacks it (cursor declarations pass the
    # event as an argument). An option like --verbose must never become
    # the event name a real payload already carries.
    native = str(raw.get("hook_event_name") or "") or argv_event
    event_map = contract.get("event_map") or {}
    extra_fields = contract.get("fields") or {}

    roots = raw.get("workspace_roots")
    cwd = raw.get("cwd")
    if cwd is None and isinstance(roots, list) and roots:
        cwd = roots[0]

    norm: dict = {
        "event": event_map.get(native, native),
        "harness": contract.get("harness", ""),
        "cwd": cwd,
    }
    for name in _FIELD_DEFAULTS:
        if name == "cwd":
            continue
        value = raw.get(name)
        if value is None:
            for alias in _ALIASES.get(name, ()) + tuple(
                    extra_fields.get(name, ())):
                if raw.get(alias) is not None:
                    value = raw[alias]
                    break
        norm[name] = value
    norm["timestamp"] = time.time()
    norm["raw"] = raw
    return norm, native


def _is_permission_event(native: str) -> bool:
    """Cursor-style: permission-shaped answers for pre*/before*/subagent*."""
    return native.startswith(("pre", "before", "subagent"))


def _permission_of(parsed: dict) -> str | None:
    """allow|deny|ask from a normalized output object, or None."""
    hso = parsed.get("hookSpecificOutput")
    if isinstance(hso, dict):
        decision = hso.get("permissionDecision")
        if decision in ("allow", "deny", "ask"):
            return decision
    decision = parsed.get("decision")
    if decision in ("deny", "block"):
        return "deny"
    if decision in ("allow", "approve"):
        return "allow"
    if decision == "ask":
        return "ask"
    direct = parsed.get("permission")
    if direct in ("allow", "deny", "ask"):
        return direct
    # continue:false stops the turn — on a permission event that is a
    # deny, and it must not fall through to the allow fallback.
    if parsed.get("continue") is False:
        return "deny"
    return None


def default_output(contract: dict, native: str) -> str:
    """The contract's fail-safe stdout when nothing valid came back.

    Only called for contracts that MUST print JSON — cursor stalls on a
    pre*/before* event without a permission answer and gemini/qwen parse
    stdout unconditionally. Permission events answer allow (fail open);
    everything else continues. Both keys ride in one object for flat, so
    a stub that cannot see the event name still answers correctly.
    """
    if contract.get("schema") == "flat":
        if _is_permission_event(native):
            return '{"permission": "allow"}'
        return '{"continue": true}'
    return "{}"


def block_output(contract: dict, native: str) -> str:
    """The JSON an exit-2 block becomes for a must-print contract.

    The normalized contract's ``exit 2 = block`` maps to the harness's
    machine answer: deny for flat permission events, ``continue: false``
    for the rest, ``decision: block`` for the grouped schema. Both flat
    keys ride together, the same trick as ``default_output``.
    """
    if contract.get("schema") == "flat":
        return '{"permission": "deny", "continue": false}'
    return '{"decision": "block"}'


def translate(contract: dict, native: str, out: str) -> str:
    """Map a shared script's stdout into the harness's answer text.

    The normalized output contract is Claude's shape (``hookSpecificOutput``,
    ``decision``, ``continue``, ``reason``). Grouped-schema harnesses get it
    verbatim, except must-print contracts whose stdout has to be a JSON
    object (the decision is hoisted out of ``hookSpecificOutput`` so gemini
    sees it). The flat schema needs exactly the key it reads:
    ``permission`` on pre*/before* events, ``continue`` elsewhere.
    """
    schema = contract.get("schema")
    must_print = bool(contract.get("must_print_json"))
    stripped = (out or "").strip()
    parsed = None
    if stripped:
        try:
            candidate = json.loads(stripped)
        except ValueError:
            candidate = None
        if isinstance(candidate, dict):
            parsed = candidate

    if schema == "flat":
        if _is_permission_event(native):
            permission = _permission_of(parsed or {}) or "allow"
            return json.dumps({"permission": permission})
        cont = True
        if parsed:
            if parsed.get("continue") is not None:
                cont = bool(parsed["continue"])
            elif parsed.get("decision") == "block":
                cont = False
        return json.dumps({"continue": cont})

    if must_print:
        if parsed is None:
            return default_output(contract, native)
        if "decision" not in parsed:
            hso = parsed.get("hookSpecificOutput")
            if isinstance(hso, dict):
                decision = hso.get("permissionDecision")
                if decision in ("allow", "deny"):
                    parsed["decision"] = decision
                elif decision == "ask":
                    # No "ask" exists outside Claude; deny is the
                    # cautious read when the harness can't prompt.
                    parsed["decision"] = "deny"
        return json.dumps(parsed)

    return out


def _blocked(contract: dict, native: str, out: str) -> str:
    """The must-print answer for an exit-2 block.

    Flat contracts get the deny+continue:false union — a script's own
    stdout cannot weaken a block there. Grouped ones keep the script's
    JSON when it parses (``reason`` and friends still matter) but force
    ``decision: block`` over whatever it printed: exit 2 is the block
    signal, and a bare ``{}`` must not read as "no verdict".
    """
    if contract.get("schema") == "flat":
        return block_output(contract, native)
    stripped = (out or "").strip()
    if stripped:
        try:
            candidate = json.loads(stripped)
        except ValueError:
            candidate = None
        if isinstance(candidate, dict):
            candidate["decision"] = "block"
            return json.dumps(candidate)
    return block_output(contract, native)


def _split(argv: list[str]) -> tuple[dict, list[str]]:
    """(--contract value, rest) — everything after ``--`` is script argv."""
    try:
        sep = argv.index("--")
    except ValueError:
        raise SystemExit("shim: expected '--' before the script path")
    head, rest = argv[:sep], argv[sep + 1:]
    if not rest:
        raise SystemExit("shim: no shared script after '--'")
    contract: dict = {}
    for i, token in enumerate(head):
        if token == "--contract" and i + 1 < len(head):
            try:
                contract = json.loads(head[i + 1])
            except ValueError:
                contract = {}
    return contract if isinstance(contract, dict) else {}, rest


def main(argv: list[str]) -> int:
    contract, script_argv = _split(argv)
    script, extra = script_argv[0], script_argv[1:]
    argv_event = extra[0] if extra else ""

    try:
        raw_text = sys.stdin.read()
    except Exception:
        raw_text = ""
    try:
        raw = json.loads(raw_text)
    except ValueError:
        raw = {}
    norm, native = normalize(raw, contract, argv_event)

    must_print = bool(contract.get("must_print_json"))
    try:
        proc = subprocess.run(
            [sys.executable, script] + extra,
            input=json.dumps(norm), capture_output=True, text=True,
            close_fds=True)
    except Exception:
        if must_print:
            sys.stdout.write(default_output(contract, native) + "\n")
        return 0

    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if must_print:
        # These contracts stall on anything but valid JSON, so the shim
        # always answers. Exit 2 is the block signal and is checked
        # before stdout — a hook that prints {} and exits 2 still blocks.
        # Any other stdout is forwarded only through translate(), which
        # re-validates it: a prefix like '{"x' from a mid-write crash is
        # not valid JSON and must not reach the harness. Every other
        # failure fails open. Exit code is always 0 — the JSON carries
        # the verdict.
        if proc.returncode == 0:
            text = translate(contract, native, proc.stdout)
        elif proc.returncode == 2:
            text = _blocked(contract, native, proc.stdout)
        elif proc.stdout.strip().startswith("{"):
            text = translate(contract, native, proc.stdout)
        else:
            text = default_output(contract, native)
        if text:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
        return 0
    if proc.returncode == 0:
        text = translate(contract, native, proc.stdout)
        if text:
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
    elif proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc.returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        # A shim that dies must never block a session: say nothing and
        # pass, matching every contract's fail-open path.
        sys.exit(0)
