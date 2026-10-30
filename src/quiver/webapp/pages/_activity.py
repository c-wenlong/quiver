"""The Calendar's file activity sources: harnesses the ledger does not
draw from, read from their own session files (ports
``FileActivitySources.swift`` and ``ActivityFileIndex.swift``).

Each source lists its files with a stamp, re-parses only new or changed
ones, and hands back one entry per session: ``{"session", "account",
"cwd", "timestamps"}`` with the response times as epoch seconds, sorted.
``calendar.file_pieces`` turns them into blocks with the same rule as the
ledger's. The index lives in memory for the hub's lifetime; the Mac's
on-disk copy under ``~/.quiver/cache/calendar`` is the app's own and
stays unread.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path
from urllib.parse import quote

from quiver.ledger.models import parse_timestamp
from quiver.ledger.parsers import iter_lines
from quiver.paths import quiver_dir_for

# Sessions the ledger also logs, so the Calendar drops the accidental
# ones the way the block query judges the rest
# (``Ledger.filterTrivialActivitySessions``).
TRIVIAL_FILTERED = ("droid", "pi")
# How often a source lists its files again; a rescan skips the wait
# (``ActivityFileIndex.refreshInterval``).
REFRESH_S = 30.0


class FileIndex:
    """``ActivityFileIndex``: path to its stamp and the sessions parsed
    out of it. Files whose stamp is unchanged keep their sessions; a
    file that fails to parse stays out, so the next read tries again.
    The Mac logs that failure at debug level and shows nothing; the hub
    has no log, so it only retries."""

    def __init__(self, now=time.monotonic):
        self._entries: dict[str, tuple[tuple, list[dict]]] = {}
        self._lock = threading.Lock()
        self._now = now
        self._refreshed: float | None = None

    def sessions(self, files, parse, force: bool = False) -> list[dict]:
        """Every session every file holds. Within ``REFRESH_S`` of the last
        refresh, without ``force``, the cached sessions come back and
        ``files`` (the source's listing, a callable) is never called."""
        with self._lock:
            if not force and self._refreshed is not None \
                    and self._now() - self._refreshed < REFRESH_S:
                return [s for entry in self._entries.values() for s in entry[1]]
            files = files()
            self._refreshed = self._now()
            for path in [p for p in self._entries if p not in files]:
                del self._entries[path]
            for path, stamp in files.items():
                entry = self._entries.get(path)
                if entry is not None and entry[0] == stamp:
                    continue
                # One bad file must not sink the rest of the source.
                try:
                    self._entries[path] = (stamp, parse(path))
                except Exception:  # noqa: BLE001 - any parse failure drops the file
                    self._entries.pop(path, None)
            return [s for path in sorted(files) for s in self._entries.get(path, ((), []))[1]]


_INDEXES: dict[tuple[str, str], FileIndex] = {}
_INDEXES_LOCK = threading.Lock()


def _index(harness: str, root: str) -> FileIndex:
    with _INDEXES_LOCK:
        return _INDEXES.setdefault((harness, root), FileIndex())


def _hidden(entry: os.DirEntry) -> bool:
    """``.skipsHiddenFiles``: a dot name, or the BSD hidden flag macOS
    also counts as hidden."""
    if entry.name.startswith("."):
        return True
    try:
        flags = getattr(entry.stat(follow_symlinks=False), "st_flags", 0)
    except OSError:
        return False
    return bool(flags & getattr(stat, "UF_HIDDEN", 0))


def walk(root: str):
    """Every non-directory under ``root``, recursively, as
    ``FileManager``'s enumerator lists it with ``.skipsHiddenFiles``:
    hidden names are neither listed nor descended into, symlinked folders
    are not followed, and unreadable folders are skipped."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for entry in entries:
        if _hidden(entry):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            yield from walk(entry.path)
        else:
            yield entry.path


def stamp(path: str) -> tuple | None:
    """``ActivityFileIndex.stamp``: mtime and size, None when unstattable."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def jsonl_files(root: str) -> dict[str, tuple]:
    """``ActivityParsers.jsonlFiles``: every ``*.jsonl`` under the root
    with its stamp; files that fail to stat are skipped."""
    files = {}
    for path in walk(root):
        if os.path.splitext(os.path.basename(path))[1] != ".jsonl":
            continue
        found = stamp(path)
        if found is not None:
            files[path] = found
    return files


def _no_constants(name: str):
    # JSONSerialization has no NaN or Infinity; a line holding one is broken.
    raise ValueError(name)


def json_object(raw) -> dict | None:
    """One JSON object out of ``raw`` (a line or a file's bytes), None
    when it is broken or not an object."""
    try:
        obj = json.loads(raw, parse_constant=_no_constants)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def jsonl(path: str):
    """``ActivityParsers.jsonl``: every object line of a JSONL file through
    the ledger's line reader (newline-terminated lines only, 64 MiB cap);
    broken or non-object lines are skipped. Raises ``OSError`` when the
    file can't be opened, which drops the file from the index."""
    for line in iter_lines(Path(path)):
        obj = json_object(line)
        if obj is not None:
            yield obj


def _session_log(path: str, account: str, session: str, cwd_type: str) -> list[dict]:
    """Droid and Pi share a shape: the first ``cwd_type`` line names the
    folder and every ``message`` line's ISO ``timestamp`` is a response."""
    cwd = None
    timestamps: list[float] = []
    for line in jsonl(path):
        kind = line.get("type")
        if kind == cwd_type:
            if cwd is None and isinstance(line.get("cwd"), str):
                cwd = line["cwd"]
        elif kind == "message":
            ts = parse_timestamp(line.get("timestamp"))
            if ts is not None:
                timestamps.append(ts)
    if not timestamps:
        return []
    return [{"session": session, "account": account, "cwd": cwd,
             "timestamps": sorted(timestamps)}]


def parse_droid(path: str, account: str) -> list[dict]:
    """``DroidActivity.parse``: ``<sessions>/<project>/<uuid>.jsonl``, the
    file stem is swe's session id and ``session_start`` carries the cwd."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return _session_log(path, account, stem, "session_start")


def parse_pi(path: str, account: str) -> list[dict]:
    """``PiActivity.parse``: ``<sessions>/<project>/<ts>_<uuid>.jsonl``,
    swe's session id is the full file path and a ``session`` line carries
    the cwd."""
    return _session_log(path, account, path, "session")


def freebuff_files(root: str) -> dict[str, tuple]:
    """``FreebuffActivity.files``: every ``<project>/chats/<chat>/run-state.json``
    under the root, at any depth, with its stamp."""
    files = {}
    for path in walk(root):
        p = Path(path)
        if p.name != "run-state.json" or p.parent.parent.name != "chats":
            continue
        found = stamp(path)
        if found is not None:
            files[path] = found
    return files


def parse_freebuff(path: str, account: str) -> list[dict]:
    """``FreebuffActivity.parse``: ``sessionState.mainAgentState.messageHistory``
    carries ``sentAt`` epoch milliseconds (any JSON number, a bool too, as
    ``as? NSNumber`` takes it), the chat folder's name is swe's session id,
    and the cwd is the agent's ``cwd`` or ``initialCwd``, else the
    state's, else the file's ``initialCwd``. An unreadable or malformed
    file, or a history holding anything but objects, is no session."""
    try:
        root = json_object(Path(path).read_bytes())
    except OSError:
        return []
    state = root.get("sessionState") if root is not None else None
    agent = state.get("mainAgentState") if isinstance(state, dict) else None
    history = agent.get("messageHistory") if isinstance(agent, dict) else None
    if not isinstance(history, list) or not all(isinstance(m, dict) for m in history):
        return []
    timestamps = sorted(m["sentAt"] / 1000 for m in history
                        if isinstance(m.get("sentAt"), (int, float)))
    if not timestamps:
        return []
    cwd = next((c for c in (agent.get("cwd"), agent.get("initialCwd"),
                            state.get("initialCwd"), root.get("initialCwd"))
                if isinstance(c, str)), None)
    return [{"session": Path(path).parent.name, "account": account, "cwd": cwd,
             "timestamps": timestamps}]


# ---------------------------------------------------------------- Cursor app

# Every bubble row. A range on the key, not ``LIKE 'bubbleId:%'``: LIKE
# is case-insensitive, so SQLite can't use the key's index for it.
# ``;`` is the character after ``:``.
CURSOR_BUBBLES = ("SELECT key, json_extract(value, '$.createdAt')"
                  " FROM cursorDiskKV WHERE key >= 'bubbleId:' AND key < 'bubbleId;'")
CURSOR_DB = "User/globalStorage/state.vscdb"
# What ``Double(String)`` takes: a decimal, inf/infinity/nan, or a hex
# float, signed or not, with no whitespace and no ``_`` (``float`` takes both).
_SWIFT_DOUBLE = re.compile(r"[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?|inf|infinity|nan)", re.I)
_SWIFT_HEX = re.compile(r"[+-]?0x(?:[0-9a-f]+\.?[0-9a-f]*|\.[0-9a-f]+)(?:p[+-]?\d+)?", re.I)


def swift_double(text: str) -> float | None:
    """``Double(text)``, None where Swift would give nil."""
    if _SWIFT_DOUBLE.fullmatch(text):
        return float(text)
    if _SWIFT_HEX.fullmatch(text):
        return float.fromhex(text)
    return None


def cursor_created_at(value) -> float | None:
    """``CursorAppActivity.createdAt``: epoch seconds from an ISO string or
    a millisecond number. Swift reads the column as text, so a number
    arrives as its decimal text and a string holding a number counts too;
    zero and below are no time."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 0 else None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    ts = parse_timestamp(value)
    if ts is not None:
        return ts
    ms = swift_double(value)
    return ms / 1000 if ms is not None and ms > 0 else None


def cursor_composer(key: str) -> str | None:
    """The composer id out of ``bubbleId:<composer>:<bubble>``, splitting
    as Swift's ``split(separator:)`` does (empty pieces dropped); None
    for a key with no bubble part or a ``task-`` (subagent) composer."""
    parts = [p for p in key.split(":") if p]
    if len(parts) < 3 or parts[0] != "bubbleId":
        return None
    return None if parts[1].startswith("task-") else parts[1]


def cursor_user_data_dirs(home: Path, profiles: list[dict]) -> list[str]:
    """The account roots ``AppModel`` hands ``CursorAppActivity``: the
    app's own ``~/Library/Application Support/Cursor`` plus each extra
    Cursor profile's ``desktop`` folder (``ProfileStore.paths``)."""
    root = quiver_dir_for(home) / "accounts"
    dirs = [str(home / "Library/Application Support/Cursor")]
    dirs += [str(root / "cursor" / p["id"] / "desktop") for p in profiles
             if p.get("vendor") == "cursor" and not p.get("isDefault")]
    return dirs


def cursor_stamp(db: str) -> tuple | None:
    """The db's stamp folded with its WAL's: a live WAL-mode database
    changes in the -wal file while the main file's mtime stands still."""
    found = stamp(db)
    if found is None:
        return None
    wal = stamp(db + "-wal")
    if wal is None:
        return found
    return (max(found[0], wal[0]), found[1] + wal[1])


def cursor_files(dirs: list[str]) -> dict[str, tuple]:
    """One ``state.vscdb`` per account root; two roots naming one
    database read it once."""
    files: dict[str, tuple] = {}
    for d in dirs:
        db = str(Path(d) / CURSOR_DB)
        found = cursor_stamp(db)
        if found is not None:
            files.setdefault(db, found)
    return files


def parse_cursor(path: str) -> list[dict]:
    """``CursorAppActivity.parse``: one session per composer id, the
    database opened strictly read-only and queried once, retrying a
    single BUSY. The account is the user-data dir three levels up."""
    rows = []
    for attempt in range(2):
        try:
            con = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=2)
            try:
                rows = con.execute(CURSOR_BUBBLES).fetchall()
            finally:
                con.close()
            break
        except sqlite3.OperationalError as exc:
            # WAL readers can collide with a checkpoint; once is enough.
            if attempt == 0 and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY:
                time.sleep(0.1)
                continue
            raise
    sessions: dict[str, list[float]] = {}
    for key, value in rows:
        if not isinstance(key, str):
            continue
        created, composer = cursor_created_at(value), cursor_composer(key)
        if created is None or composer is None:
            continue
        sessions.setdefault(composer, []).append(created)
    account = str(Path(path).parent.parent.parent)
    return [{"session": composer, "account": account, "cwd": None, "timestamps": sorted(stamps)}
            for composer, stamps in sessions.items()]


class Source:
    """One file source: its harness, the account home it reports, where
    its files live under that home, and how to list and parse them."""

    def __init__(self, harness: str, account: str, root: str, files, parse):
        self.harness, self.account, self.root = harness, account, root
        self._files, self._parse = files, parse

    def sessions(self, home: Path, force: bool = False) -> list[dict]:
        account = str(home / self.account)
        root = str(home / self.root)
        return _index(self.harness, root).sessions(
            lambda: self._files(root), lambda path: self._parse(path, account), force)


# AppModel's file providers, with the same homes and roots.
SOURCES = (
    Source("droid", ".factory", ".factory/sessions", jsonl_files, parse_droid),
    Source("pi", ".pi", ".pi/agent/sessions", jsonl_files, parse_pi),
    Source("freebuff", ".config/manicode", ".config/manicode/projects",
           freebuff_files, parse_freebuff),
)


def read(home: Path | None, stats: dict[str, dict], trivial,
         profiles: list[dict] = (), force: bool = False) -> list[tuple[str, dict]]:
    """``(harness, session)`` for every file source's sessions and the
    Cursor app's chats, the accidental Pi and Droid ones dropped.
    ``stats`` maps a filtered harness to
    ``LedgerStore.activity_session_stats``, keyed by account and session;
    ``trivial`` is ``sessions.is_trivial``. A session the ledger has no
    rows for stays: absent rows prove nothing about a file's responses.
    ``profiles`` are ``~/.quiver/accounts/profiles.json``'s, for the extra
    Cursor accounts. ``force`` (a rescan) lists the files again now."""
    user = Path(home) if home is not None else Path.home()
    dirs = cursor_user_data_dirs(user, list(profiles))
    out = [("cursor", s) for s in _index("cursor", str(user)).sessions(
        lambda: cursor_files(dirs), parse_cursor, force)]
    for source in SOURCES:
        usage = stats.get(source.harness)
        for session in source.sessions(user, force):
            totals = (usage or {}).get((session["account"], session["session"]))
            if totals is not None and trivial(source.harness, totals, True):
                continue
            out.append((source.harness, session))
    return out
