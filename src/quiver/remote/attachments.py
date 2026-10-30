"""Attachment staging: phone uploads land on the session's machine.

``POST /v1/sessions/{key}/attachments`` writes bytes under
``data/remote/inbox/<slug>/`` and returns a ``ref`` the next message
command can list; the injector rewrites refs into the local path
(``prompt_text``: ``@path`` for harnesses whose prompt expands it, the
plain path in brackets for the rest). For satellite sessions the
agent holds the same store and the hub streams bytes down the existing
connection — the file never lives anywhere but the session's machine.

Caps: 20 MB per file, image/pdf/text extensions only, swept after seven
days or at session end (``detach``/``kill`` removes the directory).
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from quiver.paths import remote_dir_for
from quiver.remote.protocol import key_slug

MAX_BYTES = 20 << 20
MAX_AGE_S = 7 * 24 * 3600

# stage() builds refs as "<ts>-<rand>-<name>"; the name is capped so the
# ref stays under _safe_name's 80-char limit and resolve() round-trips.
_NAME_MAX = 60

ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic",
    ".pdf",
    ".txt", ".md", ".csv", ".json", ".log", ".diff", ".patch",
}


# How a harness's prompt takes an attached file, declared in harness.json
# as ``capabilities.attachments``:
#
#   at_refs  true: the harness expands ``@/abs/path`` in a submitted
#            prompt into the file itself (Claude Code in its TUI and in
#            ``-p``; Gemini CLI and Qwen Code through their ``@`` command
#            processor). False or absent: the path goes in as plain text,
#            ``[Attached file: /abs/path]``, and the agent reads it with
#            its own tools. Codex's and OpenCode's ``@`` is a picker in
#            the TUI only: typed text stays literal, and over their
#            app-server / serve APIs it never runs at all.
#
# ``ATTACH_FALLBACK`` is the read-only floor for a harness whose registry
# entry declares nothing, like ``launch_model.MODEL_FALLBACK``: a
# registry ``capabilities.attachments`` entry's fields override it, and
# ``supported: false`` turns ``@`` refs off.
ATTACH_FALLBACK: dict[str, dict] = {
    "claude": {"at_refs": True},
    "gemini": {"at_refs": True},
    "qwen": {"at_refs": True},
}


def capability(name: str, reg: dict | None = None) -> dict:
    """``name``'s attachment capability: the registry's
    ``capabilities.attachments`` over ``ATTACH_FALLBACK``. ``reg`` is the
    loaded registry; None reads it (read-only)."""
    if reg is None:
        from quiver.harness import registry

        try:
            reg = registry.load_registry_readonly()
        except (OSError, ValueError):
            reg = {}
    entry = reg.get(name) if isinstance(reg, dict) else None
    # A hand-edited registry can hold anything here; a malformed
    # declaration is ignored rather than failing the send.
    caps = (entry if isinstance(entry, dict) else {}).get("capabilities")
    caps = caps.get("attachments") if isinstance(caps, dict) else None
    base = dict(ATTACH_FALLBACK.get(name) or {})
    if isinstance(caps, dict):
        if "supported" in caps and not caps["supported"]:
            return {"at_refs": False}
        base.update(caps)
    return base


def at_refs(name: str, reg: dict | None = None) -> bool:
    """Whether ``name``'s prompt expands ``@/abs/path`` refs."""
    return capability(name, reg).get("at_refs") is True


def prompt_text(
    text: str, paths: list[str], harness: str, reg: dict | None = None
) -> str:
    """The prompt line with ``paths`` referenced the way ``harness``
    takes them. One line either way: a tmux pane would submit at a
    newline."""
    if not paths:
        return text
    if at_refs(harness, reg):
        refs = " ".join(f"@{path}" for path in paths)
    else:
        refs = " ".join(f"[Attached file: {path}]" for path in paths)
    return f"{refs} {text}" if text else refs


def inbox_dir(home: Path | None, session_key: str) -> Path:
    return remote_dir_for(home) / "inbox" / key_slug(session_key)


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "") or "file"
    cleaned = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in base
    )
    return cleaned[:80] or "file"


def stage(
    home: Path | None,
    session_key: str,
    filename: str,
    data: bytes,
) -> tuple[dict | None, str | None]:
    """Persist ``data`` and return ``(ref_payload, error)``."""
    if not data:
        return None, "empty upload"
    if len(data) > MAX_BYTES:
        return None, "attachment exceeds 20 MB"
    name = _safe_name(filename)
    # The extension check must look at the raw basename: _safe_name
    # truncates, and a long enough name loses its suffix entirely.
    suffix = Path(os.path.basename(filename or "")).suffix.lower()
    if suffix not in ALLOWED_EXTS:
        return None, f"unsupported file type {suffix or '?'}"
    if len(name) > _NAME_MAX:
        # Truncate the stem, keep the suffix — a blunt [:N] can eat the
        # extension entirely and leave an unusable file.
        name = name[: _NAME_MAX - len(suffix)] + suffix
    folder = inbox_dir(home, session_key)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # The random segment keeps two same-named uploads in one second
        # from overwriting each other (phone photo batches all call
        # themselves image.png).
        ref = f"{int(time.time())}-{secrets.token_hex(3)}-{name}"
        target = folder / ref
        target.write_bytes(data)
    except OSError as exc:
        return None, f"could not stage file: {exc}"
    return {
        "ref": ref,
        "name": name,
        "path": str(target),
        "size": len(data),
    }, None


def place(
    home: Path | None,
    session_key: str,
    ref: str,
    data: bytes,
) -> tuple[dict | None, str | None]:
    """Persist ``data`` under the *existing* ``ref`` a hub staged.

    Satellite pull path: the hub already minted the ref when the phone
    uploaded; the message command that follows references that same
    name, so this must not re-mint the way ``stage`` does.
    """
    if not data:
        return None, "empty payload"
    if len(data) > MAX_BYTES:
        return None, "attachment exceeds 20 MB"
    name = _safe_name(ref)
    if not name or name in {".", ".."}:
        return None, "bad ref"
    folder = inbox_dir(home, session_key)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(data)
    except OSError as exc:
        return None, f"could not stage file: {exc}"
    return {"ref": name, "name": name, "path": str(target),
            "size": len(data)}, None


def resolve(
    home: Path | None, session_key: str, ref: str
) -> str | None:
    """``ref`` → absolute staged path, or None when it is not ours."""
    folder = inbox_dir(home, session_key).resolve()
    target = (folder / _safe_name(ref)).resolve()
    try:
        if target.is_file() and folder in target.parents:
            return str(target)
    except OSError:
        pass
    return None


def drop_session(home: Path | None, session_key: str) -> None:
    """Session end: remove the staged files for ``session_key``."""
    import shutil

    shutil.rmtree(inbox_dir(home, session_key), ignore_errors=True)


def sweep(home: Path | None, now: float | None = None) -> int:
    """Delete staged files older than seven days; returns count."""
    now = time.time() if now is None else now
    root = remote_dir_for(home) / "inbox"
    removed = 0
    try:
        folders = list(root.iterdir())
    except OSError:
        return 0
    import shutil

    for folder in folders:
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if now - entry.stat().st_mtime > MAX_AGE_S:
                    entry.unlink()
                    removed += 1
            except OSError:
                pass
        try:
            if not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass
    return removed
