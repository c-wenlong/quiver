"""Wire schema for ``swe remote``: one protocol for phone↔hub and satellite↔hub.

Everything that crosses a socket is a plain dict built here so the shape
has one home. Two flows share it: the phone talks REST + SSE to the hub
(``swe remote serve``) and satellites forward the same event envelopes
upstream from ``swe remote agent``.

Envelope::

    {"v": 1, "type": "message.appended", "session": "claude:abc",
     "machine": "macbook", "seq": 41, "ts": 1760000000.0,
     "data": {...}}

``seq`` is assigned per session key by :mod:`journal` at write time; it is
what ``?last=seq`` replays from.
"""

from __future__ import annotations

import hashlib
import time

PROTOCOL_VERSION = 1

EVENT_TYPES = frozenset(
    {
        "message.appended",
        "message.delta",
        "session.status",
        "approval.requested",
        "approval.resolved",
        "compaction",
        "machine.online",
        "machine.offline",
        "notify",
    }
)

# RemoteMessage.kind values. ``tool`` payloads pair over ``call_id``;
# ``result_ref`` on a tool_result names the call it answers.
MESSAGE_KINDS = frozenset(
    {
        "user",
        "assistant",
        "thinking",
        "tool_use",
        "tool_result",
        "compaction",
        "system",
        "approval",
        "error",
    }
)

# POST /v1/sessions/{id}/message ``mode``: queue waits for the turn to end;
# steer injects mid-turn where the channel allows, else degrades to queue.
MESSAGE_MODES = frozenset({"queue", "steer"})

# POST /v1/sessions/{id}/control ``op`` values.
CONTROL_OPS = frozenset(
    {"interrupt", "compact", "clear", "model", "effort", "attach",
     "detach", "kill"}
)

NOTIFY_KINDS = frozenset({"needs_you", "finished", "error", "limit"})

# Session ownership: ``attached`` runs under our supervisor (read+write),
# ``observed`` belongs to a human terminal (read + notify only).
OWNERSHIP_ATTACHED = "attached"
OWNERSHIP_OBSERVED = "observed"


def session_key(tool_name: str, session_id: str, path: str = "") -> str:
    """``tool:sid`` — the hub-scoped name one session is streamed under.

    A session with no recorded id (Pi keys on path, a blank claude row)
    keys on a path digest so it still lands in one journal.
    """
    sid = (session_id or "").strip()
    if not sid:
        digest = hashlib.sha1((path or "").encode("utf-8")).hexdigest()[:12]
        sid = f"path-{digest}"
    return f"{tool_name}:{sid}"


def key_slug(key: str) -> str:
    """Filesystem/URL-safe name for a session key (journal files, urls)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def event(
    event_type: str,
    session: str,
    machine: str,
    data: dict,
    *,
    ts: float | None = None,
) -> dict:
    """Build one wire event. ``seq`` is stamped by the journal on append."""
    return {
        "v": PROTOCOL_VERSION,
        "type": event_type,
        "session": session,
        "machine": machine,
        "seq": 0,
        "ts": ts if ts is not None else time.time(),
        "data": data,
    }


def message(
    kind: str,
    text: str,
    *,
    ts: float | None = None,
    tool: dict | None = None,
    result_ref: str | None = None,
) -> dict:
    """The ``data`` payload of a ``message.appended`` event."""
    return {
        "kind": kind,
        "text": text,
        "ts": ts if ts is not None else time.time(),
        "tool": tool,
        "result_ref": result_ref,
    }


def error(code: str, message_text: str) -> dict:
    """The REST error body: ``{"error": {"code", "message"}}``."""
    return {"error": {"code": code, "message": message_text}}


def parse_message_command(body: dict) -> dict:
    """Validate a ``POST .../message`` body; raises ValueError on bad input."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("message.text must be a non-empty string")
    mode = body.get("mode") or "queue"
    if mode not in MESSAGE_MODES:
        raise ValueError(f"message.mode must be one of {sorted(MESSAGE_MODES)}")
    attachments = body.get("attachments") or []
    if not isinstance(attachments, list) or not all(
        isinstance(a, str) for a in attachments
    ):
        raise ValueError("message.attachments must be a list of staged refs")
    return {"text": text, "mode": mode, "attachments": attachments}


def parse_approval_command(body: dict) -> dict:
    """Validate a ``POST .../approval`` body."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    request_id = body.get("request_id")
    decision = body.get("decision")
    if not request_id or not isinstance(request_id, str):
        raise ValueError("approval.request_id is required")
    if decision not in {"allow", "deny", "always"}:
        raise ValueError("approval.decision must be allow|deny|always")
    return {"request_id": request_id, "decision": decision}


def parse_control_command(body: dict) -> dict:
    """Validate a ``POST .../control`` body."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    op = body.get("op")
    if op not in CONTROL_OPS:
        raise ValueError(f"control.op must be one of {sorted(CONTROL_OPS)}")
    value = body.get("value")
    if value is not None and not isinstance(value, str):
        raise ValueError("control.value must be a string when given")
    return {"op": op, "value": value}
