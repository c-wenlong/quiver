"""Write side of the FTS5 session-search index (ports the Swift writer).

The Quiver app on macOS fills ``~/.quiver/data/search/search.sqlite`` as
it scans transcripts (``QuiverCore/Search``: ``SearchSchema``,
``SearchStore``, ``SearchIndexer``). A machine without the app, such as a
Linux hub, had no index, so ``/v1/search`` and ``swe session --search``
matched titles only. This module writes the same file from Python, so the
reader in :mod:`quiver.sessions.search_index` works everywhere.

Sharing one file with the Swift writer rests on four rules:

- Same schema. The DDL below is ``SearchSchema.swift``'s, verbatim and
  ``IF NOT EXISTS``. ``schema_version`` stays ``1`` and
  ``index_incarnation`` is only ever inserted, never replaced. A file at
  any other version belongs to a newer writer and is left alone: Swift
  deletes an index whose version it doesn't know, and this side must
  never do that to an index the app wrote.
- Same fences. Every write is one ``BEGIN IMMEDIATE`` transaction that
  re-reads the file's ``files`` row and gives up when it moved, exactly as
  ``SearchStore.commitFile`` does. Two writers can interleave on the same
  transcript and the loser simply rolls back; the next pass decides again.
  SQLite's own write lock (WAL, ``busy_timeout`` 5s) serialises the commits.
- Same keys. A ``files`` row is keyed by the transcript's resolved path
  and records ``ino``, ``mtime_ms`` (seconds plus nanoseconds over 1e9,
  times 1000, the Swift arithmetic) and ``size_bytes``, so a file one side
  indexed reads as current to the other.
- One Python writer at a time, through an ``flock`` on ``index.lock``
  beside the database. The Swift app doesn't take it and doesn't need to.

The pass covers Claude Code and Codex transcripts (the ``HARNESSES`` of
:mod:`quiver.sessions.search_decode`). Rows for other harnesses, written by
the app, are never touched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from quiver.paths import quiver_dir_for, search_index_for
from quiver.sessions import search_decode as decode

try:  # POSIX only; without it a second Python pass is not excluded.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

SCHEMA_VERSION = 1
PARTIAL_OFFSET = -1
FAILURES_BEFORE_HOLDOUT = 3
DRAIN_BATCH_LIMIT = 64
DRAIN_BATCH_ROWS = 256
# Text buffered before a whole read commits mid-file, so a huge transcript
# is not held in memory whole (SearchIndexer.bufferBudgetChars).
BUFFER_BUDGET_CHARS = 32 * 1024 * 1024
MAX_LINE_BYTES = 64 << 20
# How often the remote hub runs a pass while indexing is on. An unchanged
# transcript costs one stat and one row lookup.
INDEX_INTERVAL_S = 120

STATE_FILE = "indexer.json"
LOCK_FILE = "index.lock"
# What each transcript held up to its cursor when this side last read it
# (``{path: {"offset", "hash"}}``), so an append can prove the prefix it
# skips is unchanged. Written only under ``index.lock``.
PRINTS_FILE = "prints.json"
PRINT_SAMPLE = 4096

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL, session_id TEXT NOT NULL, file_path TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', cwd TEXT, cwd_key TEXT,
  created_at TEXT, updated_at TEXT, message_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS sessions_agent ON sessions(agent);
CREATE INDEX IF NOT EXISTS sessions_updated_at ON sessions(updated_at);
CREATE INDEX IF NOT EXISTS sessions_cwd_key ON sessions(cwd_key);
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, dev INTEGER, ino INTEGER,
  byte_offset INTEGER NOT NULL,
  mtime_ms REAL NOT NULL, size_bytes INTEGER,
  session_row_id INTEGER,
  state TEXT NOT NULL DEFAULT 'current',
  fail_count INTEGER NOT NULL DEFAULT 0, failed_mtime_ms REAL);
CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime_ms);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, session_row_id INTEGER NOT NULL,
  role TEXT NOT NULL, ts TEXT);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_row_id);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  user_text, assistant_text, tool_text, identifiers,
  tokenize="unicode61 tokenchars '_.-/+'", detail=full);
"""

# SearchSchema.applyPragmas, in its order: busy_timeout before WAL, and
# auto_vacuum before any table exists.
_PRAGMAS = (
    "PRAGMA busy_timeout = 5000",
    "PRAGMA auto_vacuum = INCREMENTAL",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA journal_size_limit = 8388608",
    "PRAGMA cache_size = -64000",
    "PRAGMA temp_store = MEMORY",
)


class IndexUnavailable(Exception):
    """The index can't be written here: a newer schema, no FTS5, or an
    unreadable file. The message says which, for a person."""


class IndexBusy(Exception):
    """Another Python pass holds the lock."""


# -- settings and pass state ---------------------------------------------


def _state_path(home: Path | None) -> Path:
    return search_index_for(home).parent / STATE_FILE


def load_state(home: Path | None = None) -> dict:
    """``{"enabled": bool, "last_pass": {...}}``. ``enabled`` is whether
    the hub keeps the index current; ``swe session index`` runs a pass
    either way."""
    try:
        data = json.loads(_state_path(home).read_text())
    except (OSError, ValueError):
        return {"enabled": False}
    return data if isinstance(data, dict) else {"enabled": False}


def _write_json(path: Path, data: dict) -> None:
    """Atomic replace through a temp file of this writer's own, so two
    savers never share one."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _update_state(home: Path | None, change) -> dict:
    """Read, change and save ``indexer.json`` under its own lock, so a
    pass recording its counts never undoes an ``--off`` saved meanwhile."""
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(str(path) + ".lock", "a+") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = load_state(home)
        change(data)
        _write_json(path, data)
        return data


def is_enabled(home: Path | None = None) -> bool:
    return load_state(home).get("enabled") is True


def set_enabled(home: Path | None, enabled: bool) -> None:
    _update_state(home, lambda data: data.update(enabled=bool(enabled)))


def record_error(home: Path | None, message: str) -> None:
    """What stopped the hub's last pass, shown by ``--status`` until a
    pass succeeds."""
    _update_state(home, lambda data: data.update(
        last_error={"message": message, "at": time.time()}))


# -- schema --------------------------------------------------------------


def _remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(str(path) + suffix)
        except FileNotFoundError:
            pass


def _unusable(error: sqlite3.Error) -> bool:
    message = str(error).lower()
    return "not a database" in message or "malformed" in message


def _stored_version(conn: sqlite3.Connection) -> str | None:
    """The file's ``schema_version``; '' for a fresh file with no meta."""
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone() is None:
        return ""
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return None if row is None else str(row[0]).strip()


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.search_fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp.search_fts5_probe")
        return True
    except sqlite3.Error:
        return False


def _open_existing(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
    try:
        # The version is read before any pragma that writes: switching an
        # index this side must not touch to WAL already changes its header.
        conn.execute(_PRAGMAS[0])
        version = _stored_version(conn)
        if version not in ("", str(SCHEMA_VERSION)):
            raise IndexUnavailable(
                f"the index at {path} is schema version {version or 'unknown'}, "
                f"and this swe writes version {SCHEMA_VERSION}; update swe "
                "(the Quiver app rebuilds its own index)"
            )
        if not _fts5_available(conn):
            raise IndexUnavailable("this Python's SQLite has no FTS5")
        for pragma in _PRAGMAS[1:]:
            conn.execute(pragma)
        conn.executescript(TABLES_SQL)
        conn.executescript(FTS_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('index_incarnation', ?)",
            (str(uuid.uuid4()).upper(),),
        )
        return conn
    except BaseException:
        conn.close()
        raise


def open_index(path: Path) -> sqlite3.Connection:
    """The index at ``path``, created when missing.

    A torn file is deleted and rebuilt, as the Swift writer does: it is a
    cache and neither side can read it. A file at another schema version
    raises :class:`IndexUnavailable` and stays as it is."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        return _open_existing(path)
    except sqlite3.DatabaseError as error:
        if not _unusable(error):
            raise
        _remove_database(path)
        return _open_existing(path)


# -- store ---------------------------------------------------------------


@dataclass(frozen=True)
class FileStat:
    ino: int
    mtime_ms: float
    size: int


def file_stat(path: str) -> FileStat | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    sec, nsec = divmod(st.st_mtime_ns, 1_000_000_000)
    # Double(tv_sec) + Double(tv_nsec) / 1e9, then * 1000: the same float
    # Swift stores, so an unchanged file compares equal on both sides.
    return FileStat(ino=st.st_ino, mtime_ms=(sec + nsec / 1e9) * 1000, size=st.st_size)


@dataclass
class FileRow:
    path: str
    ino: int | None
    byte_offset: int
    mtime_ms: float
    size_bytes: int | None
    session_row_id: int | None
    state: str
    fail_count: int
    failed_mtime_ms: float | None


@dataclass
class SessionFields:
    agent: str
    session_id: str
    file_path: str
    title: str = ""
    cwd: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0

    def values(self) -> tuple:
        return (self.agent, self.session_id, self.file_path, self.title,
                self.cwd, self.cwd, self.created_at, self.updated_at,
                self.message_count)


# Session commit kinds (IndexSessionCommit).
NONE, EXISTING, FRESH, REPLACE = "none", "existing", "fresh", "replace"


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        # The session row the last successful commit_file wrote under, read
        # inside its transaction: re-reading the row after COMMIT could
        # pick up another writer's commit instead.
        self.last_session_row_id: int | None = None

    def file_row(self, path: str) -> FileRow | None:
        row = self.conn.execute(
            "SELECT path, ino, byte_offset, mtime_ms, size_bytes, session_row_id,"
            " state, fail_count, failed_mtime_ms FROM files WHERE path = ?",
            (path,),
        ).fetchone()
        return FileRow(*row) if row else None

    def fence(self, path: str) -> tuple[int | None, int] | None:
        row = self.conn.execute(
            "SELECT session_row_id, byte_offset FROM files WHERE path = ?", (path,)
        ).fetchone()
        return (row[0], row[1]) if row else None

    def session(self, row_id: int) -> SessionFields | None:
        row = self.conn.execute(
            "SELECT agent, session_id, file_path, title, cwd, created_at,"
            " updated_at, message_count FROM sessions WHERE id = ?",
            (row_id,),
        ).fetchone()
        return SessionFields(*row) if row else None

    def _create_session(self, fields: SessionFields) -> int:
        cur = self.conn.execute(
            "INSERT INTO sessions(agent, session_id, file_path, title, cwd, cwd_key,"
            " created_at, updated_at, message_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            fields.values(),
        )
        return cur.lastrowid

    def _delete_session(self, row_id: int) -> None:
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (row_id,))
        # Every session delete raises this in the same transaction, so the
        # drain knows to look for the messages it cut loose.
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('orphans_pending', '1')")

    def commit_file(self, path: str, expected: tuple | None, stat: FileStat,
                    byte_offset: int, kind: str, fields: SessionFields | None,
                    rows: list[decode.Row], updating: SessionFields | None = None,
                    session_row_id: int | None = None) -> bool:
        """``SearchStore.commitFile``: one transaction, fenced on the
        ``files`` row. False (and nothing written) when the row moved."""
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            actual = self.fence(path)
            if actual != expected:
                conn.execute("ROLLBACK")
                return False
            if kind == NONE:
                target = None
            elif kind == EXISTING:
                target = session_row_id
            else:
                target = self._create_session(fields)
                # Mint first, then cut the old row loose: retrieval never
                # sees the file without a session.
                if kind == REPLACE and actual is not None and actual[0] is not None:
                    self._delete_session(actual[0])
            if target is not None:
                for row in rows:
                    cur = conn.execute(
                        "INSERT INTO messages(session_row_id, role, ts) VALUES (?, ?, ?)",
                        (target, row.role, row.ts),
                    )
                    conn.execute(
                        "INSERT INTO messages_fts(rowid, user_text, assistant_text,"
                        " tool_text, identifiers) VALUES (?, ?, ?, ?, ?)",
                        (cur.lastrowid, row.user_text, row.assistant_text,
                         row.tool_text, row.identifiers),
                    )
                if updating is not None:
                    conn.execute(
                        "UPDATE sessions SET agent = ?, session_id = ?, file_path = ?,"
                        " title = ?, cwd = ?, cwd_key = ?, created_at = ?, updated_at = ?,"
                        " message_count = ? WHERE id = ?",
                        updating.values() + (target,),
                    )
            conn.execute(
                "INSERT INTO files(path, dev, ino, byte_offset, mtime_ms, size_bytes,"
                " session_row_id) VALUES (?, NULL, ?, ?, ?, ?, ?)"
                " ON CONFLICT(path) DO UPDATE SET"
                " ino = CASE WHEN excluded.ino IS NOT NULL THEN excluded.ino ELSE files.ino END,"
                " byte_offset = excluded.byte_offset, mtime_ms = excluded.mtime_ms,"
                " size_bytes = excluded.size_bytes, session_row_id = excluded.session_row_id,"
                " state = 'current', fail_count = 0, failed_mtime_ms = NULL",
                (path, stat.ino, byte_offset, stat.mtime_ms, stat.size, target),
            )
            conn.execute("COMMIT")
            self.last_session_row_id = target
            return True
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    def mark_failed(self, path: str, mtime_ms: float) -> None:
        self.conn.execute(
            "INSERT INTO files(path, byte_offset, mtime_ms, state, fail_count, failed_mtime_ms)"
            " VALUES (?, 0, ?, 'failed', 1, ?)"
            " ON CONFLICT(path) DO UPDATE SET state = 'failed',"
            " fail_count = files.fail_count + 1, failed_mtime_ms = excluded.failed_mtime_ms",
            (path, mtime_ms, mtime_ms),
        )

    def drain_orphans(self, limit: int = DRAIN_BATCH_ROWS) -> int:
        """``drainOrphanedMessages``: delete up to ``limit`` messages of one
        session that no longer exists. Call until it returns 0."""
        conn = self.conn
        flag = conn.execute("SELECT value FROM meta WHERE key = 'orphans_pending'").fetchone()
        if flag and flag[0] == "0":
            return 0
        # The probe and the flag reset share one write transaction: a
        # session the app deletes between them would otherwise raise the
        # flag just before this lowers it, and its messages would wait for
        # the next delete to be found.
        conn.execute("BEGIN IMMEDIATE")
        try:
            orphan = conn.execute(
                "SELECT session_row_id FROM messages"
                " WHERE session_row_id NOT IN (SELECT id FROM sessions) LIMIT 1"
            ).fetchone()
            if orphan is None:
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('orphans_pending', '0')")
                conn.execute("COMMIT")
                conn.execute("PRAGMA incremental_vacuum(2000)").fetchall()
                return 0
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM messages WHERE session_row_id = ? LIMIT ?", (orphan[0], limit))]
            for message_id in ids:
                conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (message_id,))
                conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        conn.execute("PRAGMA incremental_vacuum(2000)").fetchall()
        return len(ids)


# -- reading one transcript ----------------------------------------------

SKIP, CURRENT, APPEND, WHOLE = "skip", "current", "append", "whole"


def should_index(path: str) -> bool:
    """Claude ``subagents/`` transcripts are a session's own delegation
    chatter; the Swift indexer leaves them out too."""
    return "/subagents/" not in path


def read_need(row: FileRow | None, stat: FileStat) -> str:
    """``SearchIndexer.readNeed`` without a ledger cursor: an append is
    allowed when the row is current for the same inode and the file grew
    past the offset the row records."""
    if row is None:
        return WHOLE
    if (row.state == "failed" and row.fail_count >= FAILURES_BEFORE_HOLDOUT
            and row.failed_mtime_ms == stat.mtime_ms):
        return SKIP
    if row.state == "due" or row.byte_offset == PARTIAL_OFFSET:
        return WHOLE
    if (row.state == "current" and row.byte_offset == stat.size
            and row.size_bytes == stat.size and row.mtime_ms == stat.mtime_ms):
        return CURRENT
    if (row.state == "current" and row.ino == stat.ino
            and 0 < row.byte_offset < stat.size):
        return APPEND
    return WHOLE


def _lines(handle, start: int):
    """Complete lines from ``start``, each with the offset past its
    newline. A trailing line with no newline is still being written and is
    left for the next pass, as ``JSONLReader`` does. A line over
    ``MAX_LINE_BYTES`` is skipped (yielded as ``b""``) without ever being
    held whole."""
    handle.seek(start)
    offset = start
    while True:
        line = handle.readline(MAX_LINE_BYTES + 1)
        if line.endswith(b"\n"):
            offset += len(line)
            yield line, offset
            continue
        if len(line) <= MAX_LINE_BYTES:
            return  # end of file, or an unfinished last line
        skipped = len(line)
        while True:
            piece = handle.readline(1 << 20)
            skipped += len(piece)
            if piece.endswith(b"\n"):
                break
            if not piece:
                return
        offset += skipped
        yield b"", offset


@dataclass
class _Pending:
    path: str
    harness: str
    appending: bool
    row: FileRow | None
    decoder: decode.Decoder
    messages: list = field(default_factory=list)
    hints: decode.Hints = field(default_factory=decode.Hints)
    first_ts: str | None = None
    last_ts: str | None = None
    decoded: int = 0
    buffered: int = 0
    flushed_session: int | None = None
    expected: tuple | None = None
    has_expected: bool = False

    def fence(self) -> tuple | None:
        if self.has_expected:
            return self.expected
        return None if self.row is None else (self.row.session_row_id, self.row.byte_offset)

    def fold_ts(self, ts: str) -> None:
        incoming = decode.stamp_ms(ts)
        if incoming is None:
            if self.first_ts is None:
                self.first_ts = ts
            self.last_ts = ts
            return
        first = decode.stamp_ms(self.first_ts) if self.first_ts else None
        if first is None or incoming < first:
            self.first_ts = ts
        last = decode.stamp_ms(self.last_ts) if self.last_ts else None
        if last is None or incoming >= last:
            self.last_ts = ts

    def fields(self) -> SessionFields:
        return SessionFields(
            agent=self.harness,
            session_id=self.hints.session_id or Path(self.path).stem,
            file_path=self.path,
            title=self.hints.title or "",
            cwd=self.hints.cwd,
            created_at=self.first_ts,
            updated_at=self.last_ts,
            message_count=self.decoded,
        )


def fingerprint(handle, offset: int) -> str:
    """A hash of the first, middle and last ``PRINT_SAMPLE`` bytes before
    ``offset``: the Swift ledger's head/middle/tail check, so a same-length
    edit inside the indexed span still shows."""
    digest = hashlib.sha256(str(offset).encode())
    middle = max(0, offset // 2 - PRINT_SAMPLE // 2)
    for start in (0, middle, max(0, offset - PRINT_SAMPLE)):
        handle.seek(start)
        digest.update(handle.read(min(PRINT_SAMPLE, offset - start)))
    return digest.hexdigest()


def _load_prints(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Indexer:
    def __init__(self, store: Store, buffer_budget: int = BUFFER_BUDGET_CHARS,
                 prints: dict | None = None):
        self.store = store
        self.buffer_budget = buffer_budget
        self.prints = {} if prints is None else prints

    def index_file(self, path: str, harness: str) -> str:
        """Read one transcript into the index. Returns what happened:
        ``skip``, ``current``, ``indexed``, ``moved`` (another writer won the
        fence) or ``failed``."""
        if not should_index(path):
            return SKIP
        stat = file_stat(path)
        if stat is None:
            return SKIP
        row = self.store.file_row(path)
        need = read_need(row, stat)
        if need in (SKIP, CURRENT):
            return need
        # An appended span bigger than the buffer is read whole instead,
        # where mid-read flushes bound the memory it takes.
        appending = need == APPEND and stat.size - row.byte_offset <= self.buffer_budget
        try:
            committed = self._read(path, harness, stat, row, appending)
        except _Moved:
            return "moved"
        except (OSError, sqlite3.Error, ValueError, RecursionError):
            # ValueError and RecursionError are a transcript's own bytes
            # (text SQLite can't take, JSON nested past the parser's
            # limit): counted like an unreadable file, so it is held out
            # rather than stopping every pass at the same place.
            try:
                self.store.mark_failed(path, stat.mtime_ms)
            except sqlite3.Error:
                pass
            return "failed"
        return "indexed" if committed else "moved"

    def _read(self, path: str, harness: str, stat: FileStat,
              row: FileRow | None, appending: bool) -> bool:
        decoder = decode.Decoder(harness)
        with open(path, "rb") as handle:
            start = 0
            if appending:
                # Only a prefix this side read and fingerprinted, still
                # byte-identical, may be skipped. A row another writer
                # moved, or a file rewritten under the same inode, is read
                # whole.
                saved = self.prints.get(path)
                appending = (isinstance(saved, dict)
                             and saved.get("offset") == row.byte_offset
                             and saved.get("hash") == fingerprint(handle, row.byte_offset))
            if appending:
                start = row.byte_offset
                # Only an explicit title may replace the stored one.
                decoder.prompt_title_sent = True
                if harness == "codex":
                    for line, end in _lines(handle, 0):
                        if b'"session_meta"' in line:
                            obj = _parse(line)
                            if obj is not None:
                                decoder.prime(obj)
                                break
                        if end >= start:
                            break
            pending = _Pending(path=path, harness=harness, appending=appending,
                               row=row, decoder=decoder)
            end_offset = start
            for line, end_offset in _lines(handle, start):
                if not line or not decoder.wants(line):
                    continue
                obj = _parse(line)
                if obj is None:
                    continue
                self._record(pending, *decoder.decode_object(obj), stat)
            committed = self._finish(pending, stat, end_offset)
            if committed:
                self.prints[path] = {"offset": end_offset,
                                     "hash": fingerprint(handle, end_offset)}
        return committed

    def _record(self, pending: _Pending, messages, hints, stat: FileStat) -> None:
        for name in ("session_id", "cwd", "title"):
            value = getattr(hints, name)
            if value is not None:
                setattr(pending.hints, name, value)
        for message in messages:
            if message.ts:
                pending.fold_ts(message.ts)
            pending.messages.append(message)
            pending.buffered += len(message.text)
        pending.decoded += len(messages)
        if not pending.appending and pending.buffered >= self.buffer_budget:
            self._flush(pending, stat)

    def _flush(self, pending: _Pending, stat: FileStat) -> None:
        """Commit a whole read's rows so far, cursor parked at the partial
        sentinel so a pass that dies mid-file re-reads it whole."""
        if not pending.messages:
            return
        if pending.flushed_session is not None:
            kind = EXISTING
        elif pending.row is not None and pending.row.session_row_id is not None:
            kind = REPLACE
        else:
            kind = FRESH
        ok = self.store.commit_file(
            pending.path, pending.fence(), stat, PARTIAL_OFFSET, kind,
            pending.fields(), decode.message_rows(pending.messages),
            session_row_id=pending.flushed_session)
        if not ok:
            raise _Moved()
        pending.flushed_session = self.store.last_session_row_id
        pending.expected = (pending.flushed_session, PARTIAL_OFFSET)
        pending.has_expected = True
        pending.messages = []
        pending.buffered = 0

    def _finish(self, pending: _Pending, stat: FileStat, end_offset: int) -> bool:
        store = self.store
        rows = decode.message_rows(pending.messages)
        fields = pending.fields()
        has_session = pending.hints.session_id is not None or pending.decoded > 0
        fence = pending.fence()
        old = pending.row
        if pending.appending and old is not None:
            if old.session_row_id is not None:
                existing = store.session(old.session_row_id)
                if existing is not None:
                    fields = replace(
                        fields,
                        session_id=pending.hints.session_id or existing.session_id,
                        title=pending.hints.title or existing.title,
                        cwd=pending.hints.cwd or existing.cwd,
                        created_at=existing.created_at or fields.created_at,
                        updated_at=fields.updated_at or existing.updated_at,
                        message_count=fields.message_count + existing.message_count,
                    )
                return store.commit_file(pending.path, fence, stat, end_offset, EXISTING,
                                         None, rows, updating=fields,
                                         session_row_id=old.session_row_id)
            if not has_session:
                return store.commit_file(pending.path, fence, stat, end_offset, NONE, None, [])
            return store.commit_file(pending.path, fence, stat, end_offset, FRESH, fields, rows)
        if pending.flushed_session is not None:
            return store.commit_file(pending.path, fence, stat, end_offset, EXISTING,
                                     None, rows, updating=fields,
                                     session_row_id=pending.flushed_session)
        if old is not None and old.session_row_id is not None:
            return store.commit_file(pending.path, fence, stat, end_offset, REPLACE, fields, rows)
        if not has_session:
            return store.commit_file(pending.path, fence, stat, end_offset, NONE, None, [])
        return store.commit_file(pending.path, fence, stat, end_offset, FRESH, fields, rows)

    def drain(self) -> int:
        total = 0
        for _ in range(DRAIN_BATCH_LIMIT):
            removed = self.store.drain_orphans()
            if removed == 0:
                break
            total += removed
        return total


class _Moved(sqlite3.Error):
    """A mid-read flush lost its fence; the read stops and the next pass
    decides again. A ``sqlite3.Error`` so ``index_file`` counts it."""


def _parse(line: bytes):
    try:
        obj = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


# -- sources and the pass ------------------------------------------------


def _homes(home: Path, vendor: str, default: str) -> list[Path]:
    """The harness's own home, then each Quiver account profile's."""
    out = [home / default]
    out += sorted((quiver_dir_for(home) / "accounts" / vendor).glob("*/home"))
    return [p for p in out if p.is_dir()]


def _walk(root: Path) -> list[str]:
    """Resolved ``*.jsonl`` paths under ``root``: hidden entries and
    ``subagents`` directories pruned, as the Swift source table does."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d != "subagents")
        for name in sorted(filenames):
            if name.endswith(".jsonl") and not name.startswith("."):
                found.append(os.path.realpath(os.path.join(dirpath, name)))
    return found


def sources(home: Path) -> list[tuple[str, str]]:
    """``(path, harness)`` for every transcript this pass reads."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    roots = [("claude", h / "projects") for h in _homes(home, "claude", ".claude")]
    for codex_home in _homes(home, "codex", ".codex"):
        roots += [("codex", codex_home / "sessions"), ("codex", codex_home / "archived_sessions")]
    for harness, root in roots:
        for path in _walk(root):
            if path not in seen:
                seen.add(path)
                out.append((path, harness))
    return out


@dataclass
class PassResult:
    files: int = 0
    indexed: int = 0
    current: int = 0
    failed: int = 0
    moved: int = 0
    drained: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("files", "indexed", "current", "failed", "moved", "drained", "seconds")}


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    # ``is_running`` holds the lock for microseconds to probe it; a pass
    # that starts in that instant waits it out instead of giving up.
    RETRIES = 50
    RETRY_S = 0.01

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = open(self.path, "a+")
        if fcntl is None:
            return self
        for attempt in range(self.RETRIES):
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if attempt + 1 < self.RETRIES:
                    time.sleep(self.RETRY_S)
        self.handle.close()
        raise IndexBusy("another swe index pass is running")

    def __exit__(self, *exc):
        self.handle.close()


def is_running(home: Path | None = None) -> bool:
    """Whether a Python pass holds ``index.lock`` right now (the hub's
    loop, a ``swe session index``, or a pass Settings started)."""
    path = search_index_for(home).parent / LOCK_FILE
    if fcntl is None or not path.exists():
        return False
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


def run_pass(home: Path | None = None, *, should_stop=None) -> PassResult:
    """Index every Claude Code and Codex transcript under ``home`` once.

    Raises :class:`IndexBusy` when another Python pass holds the lock and
    :class:`IndexUnavailable` when the file can't be written here. Either
    way out, ``indexer.json`` holds the outcome (``last_pass``, or
    ``last_error``) before the lock is released, so a reader that sees no
    pass running also sees how the last one ended."""
    home = Path(home) if home is not None else Path.home()
    path = search_index_for(home)
    with _Lock(path.parent / LOCK_FILE):
        try:
            result = _pass(home, path, should_stop)
        except Exception as exc:
            with contextlib.suppress(OSError):
                record_error(home, str(exc) or type(exc).__name__)
            raise

        def record(data: dict) -> None:
            data["last_pass"] = {**result.as_dict(), "finished_at": time.time()}
            data.pop("last_error", None)

        _update_state(home, record)
    return result


def _pass(home: Path, path: Path, should_stop) -> PassResult:
    started = time.monotonic()
    result = PassResult()
    conn = open_index(path)
    prints_path = path.parent / PRINTS_FILE
    prints = _load_prints(prints_path)
    try:
        indexer = Indexer(Store(conn), prints=prints)
        for source, harness in sources(home):
            if should_stop is not None and should_stop():
                break
            result.files += 1
            outcome = indexer.index_file(source, harness)
            if outcome == "indexed":
                result.indexed += 1
            elif outcome == CURRENT:
                result.current += 1
            elif outcome == "failed":
                result.failed += 1
            elif outcome == "moved":
                result.moved += 1
        # A failed drain leaves the orphans for the next pass's.
        try:
            result.drained = indexer.drain()
        except sqlite3.Error:
            pass
    finally:
        conn.close()
        # Transcripts that are gone need no print.
        _write_json(prints_path, {p: v for p, v in prints.items() if os.path.exists(p)})
    result.seconds = round(time.monotonic() - started, 3)
    return result


def status(home: Path | None = None) -> dict:
    """What the index holds, read-only, plus this side's settings. Never
    creates the file."""
    state = load_state(home)
    body: dict = {
        "enabled": state.get("enabled") is True,
        "running": is_running(home),
        "last_pass": state.get("last_pass"),
        "last_error": state.get("last_error"),
        "exists": False,
    }
    path = search_index_for(home)
    if not path.exists():
        return body
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:
        body["error"] = str(error)
        return body
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        body.update(
            exists=True,
            schema_version=meta.get("schema_version"),
            files=dict(conn.execute("SELECT state, count(*) FROM files GROUP BY state")),
            sessions_by_agent=dict(conn.execute(
                "SELECT agent, count(*) FROM sessions GROUP BY agent")),
            sessions=conn.execute("SELECT count(*) FROM sessions").fetchone()[0],
            messages=conn.execute("SELECT count(*) FROM messages").fetchone()[0],
        )
    except sqlite3.Error as error:
        body["error"] = str(error)
    finally:
        conn.close()
    return body
