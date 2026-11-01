"""The Harnesses page's writes: ``POST /v1/app/harness/<action>``.

The rows themselves are read from ``GET /v1/app/harnesses``
(``swe.webapp.harnesses``); this plugin changes them, through the
same functions the ``swe … --json`` commands call (``registry.py`` is
the one module that writes harness.json). The state actions take
``{"name": ...}``:

- ``star`` / ``unstar``: pin to, or drop from, the top of the list. A
  harness already in the asked-for state is left as it is, so a second
  click can't undo the first the way the CLI's toggle would.
- ``archive``: also needs ``reason``, trimmed, one line, at most
  ``REASON_MAX`` characters. An archive with no reason is refused: the
  record is the point, and ``swe hs archive <name>`` with no reason
  restores instead.
- ``restore``: bring an archived harness back. Not archived is a no-op;
  the CLI's restore form would archive it instead.

Their reply is ``{"name", "harness"}``, where ``harness`` is the changed
row in the ``/v1/app/harnesses`` shape (``HarnessRow.to_json``), or
``null`` when the change left the name with nothing in the registry.

The Mac app's sheets:

- ``edit``: the row editor, ``swe edit <name> --field=value… --json``
  (``commands.edit_harness``). ``{"name", "fields": {field: text}}`` with
  only the fields that changed, each one of ``EDIT_FIELDS``; lists are
  comma-separated text and an empty value clears the field. An alias
  another harness answers to is refused (409 ``alias_taken``). The reply
  is ``swe edit``'s ``{"name", "changes", "harness"}``.
- ``register``: Detect's Register, ``swe harness discover --apply
  <names> --json``: ``{"names": [...]}``, each a new match the scan finds
  now. The reply is ``{"applied": {"added", "skipped"}, "harnesses"}``,
  every row afterwards. ``GET /v1/app/harness?view=discover`` is the scan
  itself (``swe harness discover --json``), which writes nothing.
- ``add``: Add harness, ``swe add <name> <command> … --no-replace
  --json`` (``commands.add_harness``): ``{"name", "command",
  "description", "aliases", "tags"}``; never overwrites. The reply is
  ``swe add``'s plus ``harness``, the new row.
"""

from __future__ import annotations

import threading

from swe.webapp.pages import PageError, tilde

ACTIONS = ("star", "unstar", "archive", "restore", "edit", "register", "add")
REASON_MAX = 500
NAME_MAX = 200
# The editor's fields, as swe edit spells them (EDITABLE_FIELDS, less the
# colour the Mac sheet doesn't show), and how long each may be.
EDIT_FIELDS = {"display_name": 200, "command": 1000, "description": 4000, "version": 100,
               "aliases": 1000, "tags": 1000, "notes": 10000}
# Fields that are one line in the Mac sheet; description and notes wrap.
ONE_LINE = {"display_name", "command", "version", "aliases", "tags"}

# The hub answers each request on its own thread, and every write here
# reads harness.json, changes one row and writes it back: two at once
# could lose one. The Mac app runs its swe calls in turn for the same
# reason (HarnessesModel's SerialGate).
# Doctor's fixes rewrite harness.json too and hold the same lock.
REGISTRY_LOCK = threading.Lock()


def get(ctx, args) -> dict:
    """``?view=discover``: Detect's scan, ``swe harness discover --json``."""
    if args.get("view") != "discover":
        raise PageError(404, "unknown_view", "the harness page reads ?view=discover")
    from swe.harness.discover import discover_harnesses
    from swe.harness.models import DiscoveryRow

    rows = []
    for finding in discover_harnesses():
        row = DiscoveryRow.of(finding).to_json()
        # The sheet shows the path with ~ for the home discovery scanned
        # (the process's own), as the app does.
        row["short_path"] = tilde(row.get("path") or "")
        rows.append(row)
    return {"findings": rows}


def post(ctx, action: str, body: dict) -> dict:
    if action not in ACTIONS:
        raise PageError(404, "unknown_action", f"unknown harness action: {action}")
    if action == "register":
        return _register(body.get("names"))
    if action == "add":
        return _add(body)
    key = body.get("name")
    if not isinstance(key, str) or not key.strip() or len(key) > NAME_MAX:
        raise PageError(400, "bad_name", "name must be a harness name")
    if action == "edit":
        return _edit(key.strip(), _fields(body.get("fields")))
    reason = _reason(body.get("reason")) if action == "archive" else ""

    from swe.harness import archive as archive_mod
    from swe.harness import stars
    from swe.harness.registry import load_registry, resolve, state_of

    with REGISTRY_LOCK:
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
    from swe.webapp import harnesses

    return next((r for r in harnesses.rows() if r["name"] == name), None)


# ------------------------------------------------------------- the sheets

def _fields(value) -> dict:
    """The editor's changed fields, checked, or a refusal."""
    if not isinstance(value, dict) or not value:
        raise PageError(422, "no_changes", "Change a field first.")
    fields = {}
    for field, text in value.items():
        if field not in EDIT_FIELDS:
            raise PageError(422, "bad_field", f"{field} isn't a field the editor changes")
        if not isinstance(text, str):
            raise PageError(422, "bad_field", f"{field} must be text")
        if len(text) > EDIT_FIELDS[field]:
            raise PageError(422, "too_long", f"Keep {field.replace('_', ' ')} under {EDIT_FIELDS[field]} characters.")
        if field in ONE_LINE and any(ch in text for ch in "\r\n\0"):
            raise PageError(422, "bad_field", f"{field.replace('_', ' ')} is one line")
        fields[field] = text
    if "command" in fields and not fields["command"].strip():
        raise PageError(422, "command_required", "Enter the command that runs it.")
    return fields


def _edit(key: str, fields: dict) -> dict:
    from swe.harness.commands import AliasCollision, HarnessRefusal, edit_harness

    with REGISTRY_LOCK:
        try:
            return edit_harness(key, fields).to_json()
        except LookupError:
            raise PageError(404, "unknown_harness", f"no harness named {key!r}") from None
        except AliasCollision as exc:
            raise PageError(409, "alias_taken", str(exc)) from None
        except HarnessRefusal as exc:
            raise PageError(422, "refused", str(exc)) from None


def _register(names) -> dict:
    """Detect's Register: exactly the ticked new matches."""
    if (not isinstance(names, list) or len(names) > 200
            or not all(isinstance(n, str) and 0 < len(n) <= NAME_MAX for n in names)):
        raise PageError(400, "bad_names", "names must be a list of harness names")
    if not names:
        raise PageError(422, "nothing_ticked", "Tick a harness to register.")
    from swe.harness.discover import apply_findings, discover_harnesses
    from swe.webapp import harnesses

    with REGISTRY_LOCK:
        findings = discover_harnesses()
        new = {f.name for f in findings if f.status == "new"}
        unknown = [n for n in names if n not in new]
        if unknown:
            raise PageError(404, "not_new", f"Not a new match: {', '.join(unknown)}. Detect again to see what is new.")
        added, skipped = apply_findings(findings, min_confidence="high", names=set(names))
        return {"applied": {"added": added, "skipped": skipped}, "harnesses": harnesses.rows()}


def _csv(value, label: str) -> list[str] | None:
    """A comma-separated field, as the Mac form splits it; None when empty
    (the form then leaves the flag off, so swe add uses its default)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PageError(422, "bad_field", f"{label} must be comma-separated text")
    items = [v.strip() for v in value if v.strip()]
    if any(len(v) > NAME_MAX or any(ch in v for ch in "\r\n\0") for v in items) or len(items) > 50:
        raise PageError(422, "bad_field", f"{label} must be short names")
    return items or None


def _add(body: dict) -> dict:
    """Add harness: swe add <name> <command> --no-replace --json."""
    name, command, description = body.get("name"), body.get("command"), body.get("description")
    if not isinstance(name, str) or not name or len(name) > NAME_MAX:
        raise PageError(422, "bad_name", "Letters, digits, then . _ -")
    if not isinstance(command, str) or not command.strip():
        raise PageError(422, "command_required", "Enter the command that runs it.")
    command = command.strip()
    if len(command) > EDIT_FIELDS["command"] or any(ch in command for ch in "\r\n\0"):
        raise PageError(422, "bad_command", "The command is one line.")
    if command.startswith("-"):
        raise PageError(422, "bad_command", "The command can't start with -.")
    if description is not None and (not isinstance(description, str)
                                    or len(description) > EDIT_FIELDS["description"]):
        raise PageError(422, "bad_field", "description must be text")
    aliases, tags = _csv(body.get("aliases"), "aliases"), _csv(body.get("tags"), "tags")

    from swe.harness.commands import AliasCollision, AlreadyRegistered, HarnessRefusal, add_harness

    with REGISTRY_LOCK:
        try:
            reply = add_harness(name, command, description or None, aliases, tags, no_replace=True)
        except (AlreadyRegistered, AliasCollision) as exc:
            raise PageError(409, "already_registered" if isinstance(exc, AlreadyRegistered)
                            else "alias_taken", str(exc)) from None
        except HarnessRefusal as exc:
            raise PageError(422, "bad_name", str(exc)) from None
        return {**reply, "harness": _row(name)}
