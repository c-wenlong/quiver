"""The synced repo catalog behind `swe gh sync` and `swe gh repo search`.

`swe gh sync` fills a SQLite table with the repos the account can see —
its own plus its orgs' — so the Add-repository droplist searches local
state instead of hitting GitHub on every keystroke. The store is
write-once-per-sync: nothing else mutates it, and a resync updates rows
in place (upsert + prune of what the owners it covered no longer have).
"""

import json
import sqlite3
import time
from pathlib import Path

from swe import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos(
    full_name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    description TEXT,
    stars INTEGER,
    updated_at TEXT,
    is_private INTEGER NOT NULL DEFAULT 0,
    synced_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS repos_owner ON repos(owner);
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def write(rows: list[dict], owners_ok: list[str], path: Path | None = None,
          login: str | None = None) -> dict:
    """Upsert this sync's rows and return the status summary.

    ``owners_ok`` is the owners whose listing actually came back — rows
    they no longer return are pruned, while a failed owner's rows stay
    untouched so one bad `gh repo list` can't wipe its slice of the
    catalog. A different ``login`` than the last sync's means the whole
    snapshot belongs to another account: the store starts over instead
    of keeping repos the new account could never cover.
    """
    stamp = time.time()
    conn = _connect(path or paths.GITHUB_CATALOG)
    try:
        with conn:
            prev = conn.execute(
                "SELECT value FROM meta WHERE key='account'").fetchone()
            if login and prev and prev[0] != login:
                conn.execute("DELETE FROM repos")
            conn.executemany(
                "INSERT INTO repos(full_name, owner, description, stars,"
                "                updated_at, is_private, synced_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(full_name) DO UPDATE SET"
                "   owner=excluded.owner, description=excluded.description,"
                "   stars=excluded.stars, updated_at=excluded.updated_at,"
                "   is_private=excluded.is_private,"
                "   synced_at=excluded.synced_at",
                [(r["full_name"], r["owner"], r.get("description"),
                  r.get("stars"), r.get("updated_at"),
                  1 if r.get("is_private") else 0, stamp)
                 for r in rows])
            for owner in owners_ok:
                conn.execute(
                    "DELETE FROM repos WHERE owner=? AND synced_at<?",
                    (owner, stamp))
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('synced_at', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(stamp),))
            # Owners the catalog actually holds — a failed owner's kept
            # rows still count, an empty one drops off the list.
            held = [r[0] for r in conn.execute(
                "SELECT DISTINCT owner FROM repos ORDER BY 1")]
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('owners', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(held),))
            if login:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES('account', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (login,))
        return status(path) or {}
    finally:
        conn.close()


def status(path: Path | None = None) -> dict | None:
    """``{repos, synced_at, owners}`` or ``None`` when no sync has run —
    the same keys `swe gh --json` reports under ``catalog``."""
    db = path or paths.GITHUB_CATALOG
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            if "synced_at" not in meta:
                return None
            count = conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return None
    try:
        owners = json.loads(meta.get("owners") or "[]")
        if not isinstance(owners, list):
            owners = []
    except ValueError:
        owners = []
    try:
        synced_at = float(meta["synced_at"])
    except (TypeError, ValueError):
        synced_at = 0.0
    return {"repos": count, "synced_at": synced_at,
            "owners": [str(o) for o in owners]}


def search(query: str, limit: int, path: Path | None = None) -> list[dict]:
    """Catalog rows matching ``query`` as repo-search hits.

    Substring match on name and description; names containing the query
    rank above description-only hits, stars break ties. Returns the same
    shape `gh search repos` maps to so callers stay shape-blind.
    """
    db = path or paths.GITHUB_CATALOG
    if not db.exists():
        return []
    needle = "%" + query.replace("\\", "\\\\").replace(
        "%", "\\%").replace("_", "\\_") + "%"
    try:
        conn = sqlite3.connect(str(db), timeout=10)
        try:
            rows = conn.execute(
                "SELECT full_name, description, stars, updated_at FROM repos"
                " WHERE full_name LIKE ? ESCAPE '\\'"
                "    OR description LIKE ? ESCAPE '\\'"
                " ORDER BY CASE WHEN full_name LIKE ? ESCAPE '\\'"
                "             THEN 0 ELSE 1 END,"
                "          stars DESC, full_name ASC"
                " LIMIT ?",
                (needle, needle, needle, limit)).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return []
    return [
        {"name": r[0], "description": r[1], "stars": r[2],
         "updated_at": r[3], "url": f"https://github.com/{r[0]}"}
        for r in rows]
