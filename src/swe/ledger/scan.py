"""Reading every harness log on this machine into the store
(ports the source walk in ``Ledger.swift``).

Claude transcripts only grow, so each is read on from where the last scan
stopped. A Codex rollout carries state from its first line (model, replay
gate, the totals it has seen), so a changed rollout is read whole; its
keys make that idempotent. Pi logs and the Droid and Cline session files
are read whole when they change, also keyed — the session files hold one
authoritative total each, so a re-read replaces and a now-empty file
deletes. OpenCode and Devin databases are read whole when their files
change. Cursor's usage is not a file: the web app polls it in the
background on the feed's own cadence (``CursorUsageFeed.keepFresh``), so
a scan never waits on network replies and a missed round keeps the
stored rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from swe.ledger import parsers
from swe.ledger.models import LedgerRecord
from swe.ledger.store import LedgerStore
from swe.paths import swe_dir_for


@dataclass
class ScanResult:
    files_read: int = 0
    records: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def _homes(home: Path, vendor: str, default: str) -> list[Path]:
    """The vendor's own home, then each extra Quiver account profile's
    (``~/.quiver/accounts/<vendor>/<id>/home``)."""
    out = [home / default]
    out += sorted((swe_dir_for(home) / "accounts" / vendor).glob("*/home"))
    return [p for p in out if p.is_dir()]


def _files(root: Path, suffix: str) -> list[Path]:
    """Every file under ``root`` whose name ends in ``suffix``, skipping
    hidden entries, by path."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        found += [Path(dirpath) / f for f in filenames
                  if f.endswith(suffix) and not f.startswith(".")]
    return sorted(found)


def _jsonl_files(root: Path) -> list[Path]:
    """Every ``*.jsonl`` under ``root``, skipping hidden entries, by path."""
    return _files(root, ".jsonl")


def _stat(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


def scan(store: LedgerStore, home: Path | None = None) -> ScanResult:
    home = Path(home) if home is not None else Path.home()
    result = ScanResult()
    started = time.monotonic()
    seen: set[str] = set()

    for claude_home in _homes(home, "claude", ".claude"):
        for path in _jsonl_files(claude_home / "projects"):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            _scan_claude_file(store, path, str(claude_home), result)

    for codex_home in _homes(home, "codex", ".codex"):
        for sub in ("sessions", "archived_sessions"):
            for path in _jsonl_files(codex_home / sub):
                real = os.path.realpath(path)
                if real in seen:
                    continue
                seen.add(real)
                _scan_whole(store, path, result,
                            lambda p=path, a=str(codex_home): parsers.codex_file_records(p, a))

    for pi_home in _homes(home, "pi", ".pi"):
        for path in _jsonl_files(pi_home / "agent" / "sessions"):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            # Read whole like a Codex rollout: the cwd a `session` line
            # sets must reach the message lines after it, and the keys
            # make the re-read idempotent.
            _scan_whole(store, path, result,
                        lambda p=path, a=str(pi_home): parsers.pi_file_records(p, a))

    # The opencode cursor's key names its reader: one saved by a build that
    # read only the legacy tables (no suffix) must not skip the 2.x rows it
    # missed, and one saved before models carried their provider (``#v2``)
    # must not skip rows now named ``provider/model``. Every row is keyed
    # by message id, so the re-read renames events in place.
    for db, reader, key in (
        (home / ".local/share/opencode/opencode.db", parsers.opencode_records, "#v3"),
        (home / ".local/share/devin/cli/sessions.db", parsers.devin_records, ""),
    ):
        if db.is_file():
            _scan_whole(store, db, result, lambda d=db, r=reader: r(d),
                        companions=[Path(str(db) + "-wal")], key_suffix=key)

    for droid_home in _homes(home, "droid", ".factory"):
        for path in _files(droid_home / "sessions", ".settings.json"):
            _scan_session_file(store, path, str(droid_home), result,
                               parsers.droid_session_totals,
                               watched=parsers.droid_transcript(path))

    for cline_home in _homes(home, "cline", ".cline"):
        for path in _files(cline_home / "data" / "sessions", ".json"):
            # ``<id>/<id>.json`` only: the stem must equal its session
            # directory, which the sibling ``.messages.json`` fails — it
            # is the transcript, not the file totals are read from.
            if path.stem != path.parent.name:
                continue
            _scan_session_file(store, path, str(cline_home), result,
                               parsers.cline_session_totals)

    store.set_meta("scanned_at", str(time.time()))
    store.db.commit()
    result.seconds = time.monotonic() - started
    return result


def _scan_claude_file(store: LedgerStore, path: Path, account: str,
                      result: ScanResult) -> None:
    st = _stat(path)
    if st is None:
        return
    inode, size, mtime = st
    key = str(path)
    cursor = store.file_cursor(key)
    offset = 0
    try:
        if cursor is not None:
            c_inode, _c_size, _c_mtime, c_offset, c_fp, _c_state = cursor
            if tuple(cursor[:3]) == (inode, size, mtime):
                return
            # Appended to: read on. Replaced, truncated or rewritten in
            # place (the bytes already read no longer match): from the start.
            c_offset = c_offset or 0
            if (c_inode == inode and size >= c_offset
                    and c_fp == _fingerprint(path, c_offset)):
                offset = c_offset
        end: list[int] = []
        for line in parsers.iter_lines(path, offset, end):
            for rec in parsers.claude_records(line, account):
                store.upsert(rec)
                result.records += 1
        done = end[0] if end else offset
        fp = _fingerprint(path, done)
    except OSError as exc:
        result.errors.append(f"{path}: {exc}")
        return
    store.set_file_cursor(key, inode, size, mtime, done, fp)
    result.files_read += 1
    store.db.commit()


_FP_SPAN = 4096


def _fingerprint(path: Path, offset: int) -> str:
    """A hash of the head of the file and of the bytes just before
    ``offset``, the native ledger's check that what was read is still
    there before reading on from it."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(min(_FP_SPAN, offset)))
        f.seek(max(0, offset - _FP_SPAN))
        h.update(f.read(min(_FP_SPAN, offset)))
    return f"{offset}:{h.hexdigest()}"


def _scan_whole(store: LedgerStore, path: Path, result: ScanResult, read,
                companions: list[Path] | None = None, key_suffix: str = "") -> None:
    st = _stat(path)
    if st is None:
        return
    inode, size, mtime = st
    # A database's WAL grows without touching the main file's mtime.
    for extra in companions or []:
        est = _stat(extra)
        if est is not None:
            size += est[1]
            mtime = max(mtime, est[2])
    key = str(path) + key_suffix
    cursor = store.file_cursor(key)
    if cursor is not None and tuple(cursor[:3]) == (inode, size, mtime):
        return
    try:
        records = read()
    except Exception as exc:  # a corrupt or locked source must not stop the scan
        result.errors.append(f"{path}: {exc}")
        return
    for rec in records:
        store.upsert(rec)
    result.records += len(records)
    store.set_file_cursor(key, inode, size, mtime, size)
    result.files_read += 1
    store.db.commit()


def _stamp(path: Path) -> list | None:
    """``WatchedStamp``: a sibling's ``(inode, size, mtime, ctime)``, or
    ``None`` when it is absent — its absence is part of the stamp, so the
    file appearing or vanishing re-reads its session file too."""
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def _scan_session_file(store: LedgerStore, path: Path, account: str,
                       result: ScanResult, parse,
                       watched: Path | None = None) -> None:
    """One session-total file, re-read whole whenever it could have
    changed (``scanSessionFile``).

    The cursor's ``state`` slot keeps the file's own ctime — inode, size
    and mtime can all survive an in-place rewrite, ctime cannot — and the
    watched sibling's stamp, so a transcript that moved re-reads the pair.
    A record replaces whatever the file said before (the key is
    authoritative); a file that now reports no usage deletes its old
    event. The cursor is saved only after a clean parse, so a corrupt
    file is retried, never skipped."""
    try:
        st = path.stat()
    except OSError:
        return
    if not stat.S_ISREG(st.st_mode):
        return
    key = str(path)
    state = json.dumps({"changed": st.st_ctime_ns,
                        "watched": _stamp(watched) if watched is not None else None})
    cursor = store.file_cursor(key)
    if (cursor is not None
            and tuple(cursor[:3]) == (st.st_ino, st.st_size, st.st_mtime_ns)
            and cursor[5] == state):
        return
    try:
        totals = parse(path, account)
    except Exception as exc:  # a corrupt file must not stop the scan
        result.errors.append(f"{path}: {exc}")
        return
    if isinstance(totals, LedgerRecord):
        store.upsert(totals)
        result.records += 1
    elif isinstance(totals, parsers.Empty):
        store.delete(totals.key)
    store.set_file_cursor(key, st.st_ino, st.st_size, st.st_mtime_ns,
                          st.st_size, state=state)
    result.files_read += 1
    store.db.commit()
