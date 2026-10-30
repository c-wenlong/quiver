"""Attachment staging: phone uploads land on the session's machine.

``POST /v1/sessions/{key}/attachments`` writes bytes under
``data/remote/inbox/<slug>/`` and returns a ``ref`` the next message
command can list; the injector rewrites refs into the local path
(``@path`` for harnesses that support it). For satellite sessions the
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


def inbox_dir(home: Path | None, session_key: str) -> Path:
    return remote_dir_for(home) / "inbox" / key_slug(session_key)


def _safe_name(name: str) -> str:
    base = os.path.basename(name or "") or "file"
    cleaned = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in base
    )
    return cleaned[:80] or "file"


def _private_dir(folder: Path) -> None:
    """``folder`` (and the inbox above it) at 0700: phone uploads are
    the user's screenshots and files, not for other local accounts."""
    for path in (folder.parent, folder):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # One made before modes were set keeps its old ones; tighten it
        # before anything new is written inside.
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o700)


def _write_private(folder: Path, name: str, data: bytes) -> Path:
    """Write ``data`` to ``folder/name`` created 0600, in a 0700 folder.

    The mode is set on the descriptor before any byte is written, so the
    file is never readable by others, even one replaced in place.
    """
    _private_dir(folder)
    target = folder / name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(data)
    return target


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
        # The random segment keeps two same-named uploads in one second
        # from overwriting each other (phone photo batches all call
        # themselves image.png).
        ref = f"{int(time.time())}-{secrets.token_hex(3)}-{name}"
        target = _write_private(folder, ref, data)
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
        target = _write_private(folder, name, data)
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
