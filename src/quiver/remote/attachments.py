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
import time
from pathlib import Path

from quiver.paths import remote_dir_for
from quiver.remote.protocol import key_slug

MAX_BYTES = 20 << 20
MAX_AGE_S = 7 * 24 * 3600

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
    if Path(name).suffix.lower() not in ALLOWED_EXTS:
        return None, f"unsupported file type {Path(name).suffix or '?'}"
    folder = inbox_dir(home, session_key)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ref = f"{int(time.time())}-{name}"
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
