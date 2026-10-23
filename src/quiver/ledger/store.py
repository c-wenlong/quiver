"""The ledger's SQLite store (ports ``LedgerStore.swift``).

Rows keep token counts, never dollars, and outlive the logs they came
from: Claude Code prunes old transcripts, and the totals must not shrink
when it does. Prices are applied when a page asks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from quiver.ledger.models import LedgerRecord
from quiver.paths import data_dir_for

SCHEMA = 1
_TOTAL = "input + output + cache_read + cache_write + reasoning"


def ledger_path_for(home: Path | None = None) -> Path:
    """``~/.quiver/data/web-ledger/ledger.sqlite``. Apart from the Mac
    app's ``data/ledger/`` on purpose: the two schemas differ."""
    return data_dir_for(home) / "web-ledger" / "ledger.sqlite"


class LedgerStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS events (
                key TEXT PRIMARY KEY, msg TEXT, sidechain INTEGER NOT NULL DEFAULT 0,
                harness TEXT NOT NULL, account TEXT NOT NULL, session TEXT NOT NULL,
                cwd TEXT, model TEXT NOT NULL, tier TEXT, ts REAL NOT NULL,
                input INTEGER NOT NULL, output INTEGER NOT NULL,
                cache_read INTEGER NOT NULL, cache_write INTEGER NOT NULL,
                cache_write_1h INTEGER NOT NULL, reasoning INTEGER NOT NULL, cost REAL
            );
            CREATE INDEX IF NOT EXISTS events_msg ON events(msg) WHERE msg IS NOT NULL;
            CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, inode INTEGER, size INTEGER,
                mtime_ns INTEGER, offset INTEGER, fp TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            INSERT OR IGNORE INTO meta (k, v) VALUES ('schema', '{SCHEMA}');
        """)
        # Stores made before read cursors were fingerprinted.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(files)")}
        if "fp" not in columns:
            self.db.execute("ALTER TABLE files ADD COLUMN fp TEXT")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- writes ------------------------------------------------------------

    def upsert(self, rec: LedgerRecord) -> None:
        """Keep one copy per response.

        Same key: a non-sidechain copy beats a sidechain one; otherwise
        the larger total wins and a tie keeps the first seen. A sidechain
        copy also collides on its message id alone, because a sub-agent
        transcript can replay a parent message under a new request id.
        """
        new_total = rec.event.total
        row = self.db.execute(
            f"SELECT sidechain, {_TOTAL} FROM events WHERE key = ?", (rec.key,)
        ).fetchone()
        if row is not None:
            if _wins(rec.sidechain, new_total, bool(row[0]), row[1]):
                self._write(rec)
            return
        if rec.msg is not None:
            row = self.db.execute(
                f"SELECT key, sidechain, {_TOTAL} FROM events"
                " WHERE msg = ? AND (sidechain = 1 OR ?) LIMIT 1",
                (rec.msg, int(rec.sidechain)),
            ).fetchone()
            if row is not None:
                if _wins(rec.sidechain, new_total, bool(row[1]), row[2]):
                    self.db.execute("DELETE FROM events WHERE key = ?", (row[0],))
                    self._write(rec)
                return
        self._write(rec)

    def _write(self, rec: LedgerRecord) -> None:
        e = rec.event
        self.db.execute(
            "INSERT OR REPLACE INTO events (key, msg, sidechain, harness, account, session,"
            " cwd, model, tier, ts, input, output, cache_read, cache_write, cache_write_1h,"
            " reasoning, cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.key, rec.msg, int(rec.sidechain), e.harness, e.account, e.session, e.cwd,
             e.model, e.tier, e.ts, e.input, e.output, e.cache_read, e.cache_write,
             e.cache_write_1h, e.reasoning, e.cost),
        )

    def file_cursor(self, path: str) -> tuple | None:
        """``(inode, size, mtime_ns, offset, fp)`` from the last read;
        ``fp`` fingerprints the bytes read so far (see scan.py)."""
        return self.db.execute(
            "SELECT inode, size, mtime_ns, offset, fp FROM files WHERE path = ?", (path,)
        ).fetchone()

    def set_file_cursor(self, path: str, inode: int, size: int, mtime_ns: int,
                        offset: int, fp: str | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO files (path, inode, size, mtime_ns, offset, fp)"
            " VALUES (?,?,?,?,?,?)", (path, inode, size, mtime_ns, offset, fp),
        )

    def meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))

    # -- reads -------------------------------------------------------------

    def groups(self, start: float | None, end: float | None,
               thresholds: dict[str, int]) -> list[tuple]:
        """Token sums for pricing, one row per (harness, model, tier,
        15-minute bucket, long context, logged cost).

        A request whose prompt passes its model's long-context threshold
        is billed whole at the long rates, so ``thresholds`` (model to
        tokens) splits those requests into their own groups. Buckets are
        15 minutes because every time zone offset is a multiple of that,
        so a bucket never straddles two local days.
        """
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS thresholds (model TEXT PRIMARY KEY, n INTEGER)")
        self.db.execute("DELETE FROM thresholds")
        self.db.executemany("INSERT INTO thresholds VALUES (?, ?)", list(thresholds.items()))
        where, args = [], []
        if start is not None:
            where.append("e.ts >= ?")
            args.append(start)
        if end is not None:
            where.append("e.ts < ?")
            args.append(end)
        sql = f"""
            SELECT e.harness, e.model, COALESCE(e.tier, ''), CAST(e.ts / 900 AS INTEGER),
                CASE WHEN t.n IS NOT NULL
                     AND e.input + e.cache_read + e.cache_write > t.n THEN 1 ELSE 0 END,
                e.cost IS NOT NULL,
                SUM(e.input), SUM(e.output), SUM(e.cache_read), SUM(e.cache_write),
                SUM(e.cache_write_1h), SUM(e.reasoning), SUM(COALESCE(e.cost, 0))
            FROM events e LEFT JOIN thresholds t ON t.model = e.model
            {"WHERE " + " AND ".join(where) if where else ""}
            GROUP BY 1, 2, 3, 4, 5, 6
        """
        return self.db.execute(sql, args).fetchall()

    def models(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT model FROM events")]

    def first_ts(self) -> float | None:
        row = self.db.execute("SELECT MIN(ts) FROM events").fetchone()
        return row[0] if row else None


def _wins(new_sidechain: bool, new_total: int, old_sidechain: bool, old_total: int) -> bool:
    if new_sidechain != old_sidechain:
        return not new_sidechain
    return new_total > old_total
