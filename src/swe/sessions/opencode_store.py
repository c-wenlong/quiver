"""The two session stores inside ``opencode.db`` (and Kilo's ``kilo.db``).

OpenCode 1.x keeps a session in ``session`` and its messages in
``message`` + ``part`` rows. OpenCode 2.x writes new sessions only to
``session_v2``, with every message a ``session_message`` row whose JSON
``data`` carries its whole content (an assistant row's ``content`` list
holds its text, reasoning and tool blocks). The 2.x migration copied
the old sessions into ``session_v2`` and most of their messages into
``session_message`` under the same ids, so readers take the legacy rows
first and the v2 rows only where their id is not already a legacy one.
Kilo has an empty ``session_message`` and no ``session_v2`` today; the
same reads cover it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# ``session_message.type`` values that decide where a turn stands. The
# rest (system, compaction, model-switched, agent-selected, ...) are
# bookkeeping.
PROMPT_TYPES = frozenset({"user", "synthetic"})
DECISIVE_TYPES = PROMPT_TYPES | {"assistant", "idle"}


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def has_v2_messages(conn: sqlite3.Connection) -> bool:
    return {"session_id", "type", "seq", "data"} <= table_columns(conn, "session_message")


def sessions_query(conn: sqlite3.Connection) -> str:
    """``(time_updated, title, directory, id)`` for every session, once.

    A session in both tables reads its ``session_v2`` row (the one 2.x
    keeps current), falling back to the legacy row's title and directory
    where the v2 row left them empty. ``workspace.directory`` is the last
    fallback where that column exists: opencode 2.x reshaped
    ``workspace`` into provider bindings with no ``directory``, so the
    join is only added when the column is there.
    """
    legacy = table_columns(conn, "session")
    v2 = table_columns(conn, "session_v2")
    workspace = {"id", "directory"} <= table_columns(conn, "workspace")

    def directory(alias: str, cols: set[str]) -> str:
        if workspace and "workspace_id" in cols:
            return (
                f"COALESCE(NULLIF({alias}.directory, ''), "
                f"(SELECT w.directory FROM workspace w WHERE w.id = {alias}.workspace_id))"
            )
        return f"{alias}.directory"

    legacy_sql = (
        f"SELECT s.time_updated, s.title, {directory('s', legacy)}, s.id FROM session s"
    )
    if not {"id", "directory", "time_updated"} <= v2:
        return legacy_sql
    if not legacy:
        return f"SELECT v.time_updated, v.title, {directory('v', v2)}, v.id FROM session_v2 v"
    return f"""
        SELECT MAX(v.time_updated, COALESCE(s.time_updated, 0)),
               COALESCE(NULLIF(v.title, ''), s.title),
               COALESCE(NULLIF({directory('v', v2)}, ''), {directory('s', legacy)}),
               v.id
        FROM session_v2 v LEFT JOIN session s ON s.id = v.id
        UNION ALL
        {legacy_sql}
        WHERE NOT EXISTS (SELECT 1 FROM session_v2 v WHERE v.id = s.id)
    """


def _not_legacy(conn: sqlite3.Connection) -> str:
    if "id" in table_columns(conn, "message"):
        return "AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id)"
    return ""


def v2_rows(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    after_seq: int | None = None,
    newest: int | None = None,
) -> list[tuple[str, str, int, int, dict]]:
    """``(id, type, seq, time_created, data)`` of the session's v2-only rows.

    Oldest first. ``after_seq`` keeps rows past a cursor; ``newest`` keeps
    only the last that many. Rows whose JSON does not decode to an object
    are dropped.
    """
    if not has_v2_messages(conn):
        return []
    sql = (
        "SELECT sm.id, sm.type, sm.seq, sm.time_created, sm.data "
        "FROM session_message sm WHERE sm.session_id = ? "
        f"{_not_legacy(conn)}"
    )
    params: list[Any] = [session_id]
    if after_seq is not None:
        sql += " AND sm.seq > ?"
        params.append(after_seq)
    if newest is not None:
        sql = f"SELECT * FROM ({sql} ORDER BY sm.seq DESC LIMIT ?) ORDER BY seq"
        params.append(newest)
    else:
        sql += " ORDER BY sm.seq"
    out = []
    for row_id, kind, seq, created, raw in conn.execute(sql, params):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            continue
        if isinstance(data, dict):
            out.append((str(row_id), str(kind or ""), int(seq or 0), int(created or 0), data))
    return out


def content_blocks(data: dict) -> list[dict]:
    blocks = data.get("content")
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []


def last_text(data: dict) -> str:
    """The last non-empty ``text`` block of an assistant row."""
    for block in reversed(content_blocks(data)):
        if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
            return block["text"]
    return ""


def v2_phase(rows: list[tuple[str, str, int, int, dict]]) -> tuple[str | None, str]:
    """(phase, text) from v2 rows, oldest first, read from the newest back.

    A prompt (``user``, ``synthetic``) means the model has not replied.
    An assistant ``finish`` of ``tool-calls`` (or none) is a turn still
    running, ``error`` a failure, any other value a closed turn. ``idle``
    is OpenCode's own end-of-turn record: ``failed`` and ``interrupted``
    map to error and aborted, ``succeeded`` closes the turn with the text
    of the assistant row before it.
    """
    decisive = [r for r in rows if r[1] in DECISIVE_TYPES]
    if not decisive:
        return None, ""
    kind, data = decisive[-1][1], decisive[-1][4]
    if kind in PROMPT_TYPES:
        return "midturn", ""
    if kind == "idle":
        outcome = data.get("outcome")
        if outcome == "failed":
            return "error", ""
        if outcome == "interrupted":
            return "aborted", ""
        for _id, k, _seq, _ts, d in reversed(decisive[:-1]):
            if k == "assistant":
                return "finished", last_text(d)
            if k in PROMPT_TYPES:
                break
        return "finished", ""
    finish = data.get("finish")
    if finish == "error":
        return "error", ""
    if not finish or finish == "tool-calls":
        return "midturn", ""
    return "finished", last_text(data)
