"""SQLite family engine for session stores."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from quiver.sessions.engines.common import (
    clean_title,
    expand_path,
    get_mtime,
    open_sqlite_ro,
    parse_iso_ts,
)
from quiver.sessions.models import Session


@dataclass
class SqliteParserConfig:
    tool_name: str
    agent: str
    db_path: str
    query: str
    # Column indexes in query result
    session_id: int | None = None
    path: int | None = None
    title: int | None = None
    updated: int | None = None
    created: int | None = None
    # Optional path fallback column if primary path empty
    path_fallback: int | None = None
    require_path: bool = True
    default_path: str | None = None
    # Optional row post-processor: (conn, row, session_fields_dict) -> None
    enrich: Callable[[Any, tuple, dict], None] | None = None
    # Optional transform of raw title
    title_transform: Callable[[str], str] | None = None
    params: tuple = field(default_factory=tuple)


def parse_sqlite(config: SqliteParserConfig) -> list[Session]:
    sessions: list[Session] = []
    db_path = expand_path(config.db_path)
    conn = open_sqlite_ro(db_path)
    if conn is None:
        return sessions
    db_mtime = get_mtime(db_path)
    try:
        cur = conn.cursor()
        rows = cur.execute(config.query, config.params).fetchall()
        for row in rows:
            path = _col(row, config.path) or _col(row, config.path_fallback) or ""
            if not path and config.default_path:
                path = config.default_path
            if config.require_path and not path:
                continue

            title_raw = _col(row, config.title) or ""
            if config.title_transform:
                title_raw = config.title_transform(str(title_raw))
            title = clean_title(str(title_raw)) if title_raw else ""

            ts = (
                parse_iso_ts(_col(row, config.updated))
                or parse_iso_ts(_col(row, config.created))
                or db_mtime
            )
            sid = str(_col(row, config.session_id) or "")

            fields = {
                "timestamp": ts,
                "agent": config.agent,
                "path": path,
                "title": title,
                "session_id": sid,
                "tool_name": config.tool_name,
            }
            if config.enrich:
                try:
                    config.enrich(conn, row, fields)
                except Exception:
                    pass
            if config.require_path and not fields.get("path"):
                continue
            sessions.append(Session(**fields))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return sessions


def _col(row: tuple, idx: int | None) -> Any:
    if idx is None:
        return None
    try:
        return row[idx]
    except Exception:
        return None
