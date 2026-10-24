"""Sessions that live inside a database rather than a file of their own.

Devin CLI keeps every turn in ``sessions.db`` (``message_nodes``). The
index reads each session under a synthetic ``files.path`` of
``"<db path>#<session id>"``, the spelling ``SearchOpenCode.swift`` uses
for OpenCode, and reads it whole whenever its stat moves: a row in a
container has no byte offset to resume from. This module is the Python
side of ``SearchDevin.swift``; keep the two in step, since the stat and
the fields they compute are what let a session one writer read count as
current to the other.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import quote

from quiver.sessions import search_decode as decode


def synthetic_path(db_path: str, session_id: str) -> str:
    return f"{db_path}#{session_id}"


def open_read_only(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ms(value) -> int:
    """A Devin stamp in epoch milliseconds: seconds today, milliseconds
    past ``1e12`` (``SearchStamp``'s rule)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return 0
    return int(value) if value > 1_000_000_000_000 else int(value) * 1000


@dataclass
class DevinSession:
    id: str
    title: str
    directory: str | None
    created_ms: int
    activity_ms: int
    head: int | None
    # Every node the session holds, streamed copies included: a streamed
    # reply is stored again as it grows, so the count moves with it.
    nodes: int

    @property
    def stat_mtime_ms(self) -> float:
        return float(self.activity_ms or self.created_ms)


class SchemaError(sqlite3.Error):
    """The database lacks the tables this reader knows."""


def devin_sessions(conn: sqlite3.Connection) -> list[DevinSession]:
    """Every visible session, newest activity first."""
    session_columns = _columns(conn, "sessions")
    if not ({"id", "created_at", "last_activity_at"} <= session_columns
            and {"session_id", "node_id", "parent_node_id", "chat_message"}
            <= _columns(conn, "message_nodes")):
        raise SchemaError("lacks the sessions/message_nodes schema this build reads")

    def column(name: str) -> str:
        return f"s.{name}" if name in session_columns else "NULL"

    hidden = " WHERE s.hidden = 0" if "hidden" in session_columns else ""
    rows = conn.execute(
        f"SELECT s.id, {column('title')}, {column('working_directory')},"
        f" s.created_at, s.last_activity_at, {column('main_chain_id')},"
        " (SELECT COUNT(*) FROM message_nodes n WHERE n.session_id = s.id)"
        f" FROM sessions s{hidden}"
        " ORDER BY s.last_activity_at DESC, s.id"
    ).fetchall()
    out = []
    for sid, title, directory, created, activity, head, nodes in rows:
        if not isinstance(sid, str) or not sid:
            continue
        out.append(DevinSession(
            id=sid, title=title if isinstance(title, str) else "",
            directory=directory if isinstance(directory, str) else None,
            created_ms=_ms(created), activity_ms=_ms(activity),
            head=head if isinstance(head, int) else None, nodes=int(nodes or 0)))
    return out


def _chain(conn: sqlite3.Connection, session: DevinSession) -> list[int]:
    """The main chain's node ids, oldest first. The nodes form a forest
    (streamed copies are siblings); ``main_chain_id`` is the head of the
    chain holding each message's final version, and without one the
    newest node stands in. Only ids are loaded, never the forest's text."""
    parents: dict[int, int | None] = dict(conn.execute(
        "SELECT node_id, parent_node_id FROM message_nodes WHERE session_id = ?",
        (session.id,)))
    cursor = session.head if session.head in parents else (max(parents) if parents else None)
    ordered: list[int] = []
    seen: set[int] = set()
    while cursor in parents and cursor not in seen:
        seen.add(cursor)
        ordered.append(cursor)
        cursor = parents[cursor]
    ordered.reverse()
    return ordered


def devin_messages(conn: sqlite3.Connection, session: DevinSession) -> tuple[list, str | None]:
    """The session's messages through the Devin decoder, plus the
    first-prompt title candidate the decoder offered."""
    decoder = decode.Decoder("devin")
    messages: list = []
    title = None
    for node_id in _chain(conn, session):
        # As bytes, decoded the way Swift's `text()` does (a bad byte
        # becomes U+FFFD) rather than failing the whole session.
        row = conn.execute(
            "SELECT CAST(chat_message AS BLOB) FROM message_nodes"
            " WHERE session_id = ? AND node_id = ?",
            (session.id, node_id)).fetchone()
        raw = row[0] if row else None
        if not isinstance(raw, bytes):
            continue
        found, hints = decoder.decode_line(raw.decode("utf-8", "replace").encode("utf-8"))
        messages += found
        if title is None and hints.title is not None:
            title = hints.title
    return messages, title


def devin_title(session: DevinSession, prompt_title: str | None) -> str:
    """``sessions.title`` unless Devin left its placeholder, else the first
    prompt."""
    title = decode.json_string(session.title)
    if title is not None and title != "Untitled":
        return title
    return prompt_title or ""
