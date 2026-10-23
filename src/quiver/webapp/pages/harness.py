"""The Harnesses page's writes: ``POST /v1/app/harness/<action>``.

The rows themselves are read from ``GET /v1/app/harnesses``
(``quiver.webapp.harnesses``); this plugin only changes a harness's
state, through the same functions ``swe harness star|archive --json``
call (the ``stars`` and ``archive`` shims over ``registry.py``, the one
module that writes harness.json). Each action takes ``{"name": ...}``:

- ``star`` / ``unstar``: pin to, or drop from, the top of the list. A
  harness already in the asked-for state is left as it is, so a second
  click can't undo the first the way the CLI's toggle would.
- ``archive``: also needs ``reason``, trimmed, one line, at most
  ``REASON_MAX`` characters. An archive with no reason is refused: the
  record is the point, and ``swe hs archive <name>`` with no reason
  restores instead.
- ``restore``: bring an archived harness back. Not archived is a no-op;
  the CLI's restore form would archive it instead.

The reply is ``{"name", "harness"}``, where ``harness`` is the changed
row in the ``/v1/app/harnesses`` shape (``HarnessRow.to_json``), or
``null`` when the change left the name with nothing in the registry.
"""

from __future__ import annotations

import threading

from quiver.webapp.pages import PageError

ACTIONS = ("star", "unstar", "archive", "restore")
REASON_MAX = 500
NAME_MAX = 200

# The hub answers each request on its own thread, and every write here
# reads harness.json, changes one row and writes it back: two at once
# could lose one. The Mac app runs its swe calls in turn for the same
# reason (HarnessesModel's SerialGate).
_WRITE_LOCK = threading.Lock()


def post(ctx, action: str, body: dict) -> dict:
    if action not in ACTIONS:
        raise PageError(404, "unknown_action", f"unknown harness action: {action}")
    key = body.get("name")
    if not isinstance(key, str) or not key.strip() or len(key) > NAME_MAX:
        raise PageError(400, "bad_name", "name must be a harness name")
    reason = _reason(body.get("reason")) if action == "archive" else ""

    from quiver.harness import archive as archive_mod
    from quiver.harness import stars
    from quiver.harness.registry import load_registry, resolve, state_of

    with _WRITE_LOCK:
        tools = load_registry()
        name = resolve(tools, key.strip())
        if name is None:
            raise PageError(404, "unknown_harness", f"no harness named {key.strip()!r}")
        state = state_of(tools[name])
        if action == "star":
            if state == "archived":
                # stars.star would quietly drop the archive record and its
                # reason. The page offers Restore on archived rows instead.
                raise PageError(409, "archived", f"{name} is archived. Restore it first.")
            if state != "starred":
                stars.star(name)
        elif action == "unstar":
            if state == "starred":
                stars.unstar(name)
        elif action == "archive":
            archive_mod.archive(name, reason)
        elif action == "restore":
            if state == "archived":
                archive_mod.unarchive(name)
        return {"name": name, "harness": _row(name)}


def _reason(value) -> str:
    """The archive reason as one trimmed line, or a refusal."""
    if value is not None and not isinstance(value, str):
        raise PageError(400, "bad_reason", "reason must be text")
    # One line, like the Mac app's field: newlines and runs of spaces
    # collapse, so the record reads the same in swe's one-line list.
    reason = " ".join((value or "").split())
    if not reason:
        raise PageError(400, "reason_required", "Say why you're archiving it.")
    if len(reason) > REASON_MAX:
        raise PageError(400, "reason_too_long",
                        f"Keep the reason under {REASON_MAX} characters.")
    return reason


def _row(name: str) -> dict | None:
    """``name``'s row as ``GET /v1/app/harnesses`` lists it."""
    from quiver.webapp import harnesses

    return next((r for r in harnesses.rows() if r["name"] == name), None)
