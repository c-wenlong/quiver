"""Sessions that live inside a database rather than a file of their own.

Devin CLI keeps every turn in ``sessions.db`` (``message_nodes``) and
OpenCode in ``opencode.db``. The index reads each session under a
synthetic ``files.path`` of ``"<db path>#<session id>"`` and reads it whole
whenever its stat moves: a row in a container has no byte offset to resume
from. This module is the Python side of ``SearchDevin.swift`` and
``SearchOpenCode.swift``; keep them in step, since the stat and the fields
they compute are what let a session one writer read count as current to
the other.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from urllib.parse import quote

from swe.sessions import search_decode as decode


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


# -- OpenCode (SearchOpenCode.swift) -------------------------------------


@dataclass
class OpenCodeSession:
    id: str
    title: str
    directory: str | None
    created_ms: int
    updated_ms: int
    # `message` rows plus 2.x `session_message` rows not copied from them:
    # with the activity time, the whole change detector.
    count: int

    @property
    def activity_ms(self) -> int:
        return self.updated_ms if self.updated_ms > 0 else self.created_ms


def _int(value) -> int:
    """``sqlite3_column_int64``: NULL is 0, a REAL truncates."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def opencode_sessions(conn: sqlite3.Connection) -> list[OpenCodeSession]:
    """Top-level, unarchived sessions, newest activity first: the legacy
    ``session`` table plus 2.x ``session_v2`` (whose row wins for a session
    in both)."""
    session_columns = _columns(conn, "session")
    if not ({"id", "time_created", "time_updated"} <= session_columns
            and {"id", "session_id", "time_created", "data"} <= _columns(conn, "message")
            and {"message_id", "time_created", "data"} <= _columns(conn, "part")):
        raise SchemaError("lacks the session/message/part schema this build reads")

    def column(name: str) -> str:
        return f"s.{name}" if name in session_columns else "NULL"

    filters = ((" AND s.parent_id IS NULL" if "parent_id" in session_columns else "")
               + (" AND s.time_archived IS NULL" if "time_archived" in session_columns else ""))
    v2_columns = _columns(conn, "session_v2")
    has_v2 = {"id", "time_created", "time_updated"} <= v2_columns
    if {"id", "session_id"} <= _columns(conn, "session_message"):
        def v2_count(ref: str) -> str:
            return (f"(SELECT COUNT(*) FROM session_message sm WHERE sm.session_id = {ref}"
                    " AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id))")
    else:
        def v2_count(ref: str) -> str:
            return "0"
    legacy = f"""
        SELECT s.id AS id, s.time_created AS time_created, s.time_updated AS time_updated,
               {column("title")} AS title, {column("directory")} AS directory,
               (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) + {v2_count("s.id")} AS n
        FROM session s
        WHERE 1=1{filters}"""
    if has_v2:
        def v2_column(name: str) -> str:
            return f"v.{name}" if name in v2_columns else "NULL"
        v2_filters = ((" AND v.parent_id IS NULL" if "parent_id" in v2_columns else "")
                      + (" AND v.time_archived IS NULL" if "time_archived" in v2_columns else ""))
        rows = f"""
        SELECT v.id AS id, v.time_created AS time_created,
               MAX(v.time_updated, COALESCE(s.time_updated, 0)) AS time_updated,
               COALESCE(NULLIF({v2_column("title")}, ''), {column("title")}) AS title,
               COALESCE(NULLIF({v2_column("directory")}, ''), {column("directory")}) AS directory,
               (SELECT COUNT(*) FROM message m WHERE m.session_id = v.id) + {v2_count("v.id")} AS n
        FROM session_v2 v LEFT JOIN session s ON s.id = v.id
        WHERE 1=1{v2_filters}
        UNION ALL
        {legacy} AND NOT EXISTS (SELECT 1 FROM session_v2 v WHERE v.id = s.id)"""
    else:
        rows = legacy
    out = []
    for sid, created, updated, title, directory, n in conn.execute(
        f"SELECT id, time_created, time_updated, title, directory, n FROM ({rows})"
        " ORDER BY CASE WHEN time_updated > 0 THEN time_updated ELSE time_created END DESC"
    ):
        if not isinstance(sid, str):
            continue
        out.append(OpenCodeSession(
            id=sid, title=title if isinstance(title, str) else "",
            directory=directory if isinstance(directory, str) else None,
            created_ms=_int(created), updated_ms=_int(updated), count=_int(n)))
    return out


def _json_object(raw) -> dict | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _tool_outcome(state: dict | None):
    """``state.error`` when the call failed or was cancelled, else
    ``state.output``."""
    if state is None:
        return None
    error = state.get("error")
    if isinstance(error, str) and error.strip():
        return error
    output = state.get("output")
    return output if isinstance(output, str) else None


def _tool_text(part: dict) -> str | None:
    """One ``tool`` part: what ran, then what came back, worded the way
    every other harness's tool rows are."""
    state = part.get("state") if isinstance(part.get("state"), dict) else None
    tool_input = state.get("input") if state is not None else None
    # OpenCode spells the file argument `filePath`.
    if (isinstance(tool_input, dict) and isinstance(tool_input.get("filePath"), str)
            and not isinstance(tool_input.get("file_path"), str)):
        tool_input = {**tool_input, "file_path": tool_input["filePath"]}
    pieces = [piece for piece in (decode.tool_call_text(part.get("tool"), tool_input),
                                  _tool_outcome(state)) if piece is not None]
    return decode.bounded("\n".join(pieces))


def _iso_ms(ms: int) -> str:
    return decode.iso_seconds(ms / 1000)


def opencode_messages(conn: sqlite3.Connection, session: OpenCodeSession) -> list:
    """One session's turns, legacy rows first: a turn's ``text`` and
    ``reasoning`` parts join into its one message, and each ``tool`` part
    follows as its own."""
    messages: list = []
    open_id = None
    open_role = None
    open_ts = None
    words: list[str] = []
    tools: list = []

    def flush():
        if open_role is not None:
            text = decode.bounded("\n".join(words))
            if text is not None:
                messages.append(decode.Message(open_role, text, open_ts))
        messages.extend(tools)

    for message_id, role, kind, data, created in conn.execute(
        """SELECT m.id, json_extract(m.data, '$.role'),
                  json_extract(p.data, '$.type'), p.data, m.time_created
           FROM message m JOIN part p ON p.message_id = m.id
           WHERE m.session_id = ?
             AND json_extract(m.data, '$.role') IN ('user','assistant')
             AND json_extract(p.data, '$.type') IN ('text','reasoning','tool')
           ORDER BY m.time_created, m.id, p.time_created, p.rowid""",
        (session.id,),
    ):
        message_id = message_id if isinstance(message_id, str) else ""
        if message_id != open_id:
            flush()
            words, tools = [], []
            open_id = message_id
            open_role = role if role in (decode.USER, decode.ASSISTANT, decode.TOOL) else None
            open_ts = _iso_ms(_int(created))
        if not isinstance(data, str):
            continue
        part = _json_object(data)
        if kind == "tool":
            text = _tool_text(part) if part is not None else None
            if text is not None:
                tools.append(decode.Message(decode.TOOL, text, open_ts))
        elif part is not None and isinstance(part.get("text"), str):
            words.append(part["text"])
    flush()
    return messages + _opencode_v2_messages(conn, session)


def _opencode_v2_messages(conn: sqlite3.Connection, session: OpenCodeSession) -> list:
    """OpenCode 2.x turns: one ``session_message`` row per turn, rows the
    migration copied from ``message`` left to the legacy read."""
    if not ({"id", "session_id", "type", "seq", "time_created", "data"}
            <= _columns(conn, "session_message")):
        return []
    messages: list = []
    for kind, raw, created in conn.execute(
        """SELECT sm.type, sm.data, sm.time_created FROM session_message sm
           WHERE sm.session_id = ? AND sm.type IN ('user','synthetic','assistant')
             AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id)
           ORDER BY sm.seq""",
        (session.id,),
    ):
        data = _json_object(raw)
        if data is None:
            continue
        ts = _iso_ms(_int(created))
        if kind != "assistant":
            text = decode.bounded(data["text"]) if isinstance(data.get("text"), str) else None
            if text is not None:
                messages.append(decode.Message(decode.USER, text, ts))
            continue
        words: list[str] = []
        tools: list = []
        content = data.get("content")
        # Swift's `as? [[String: Any]]`: one non-object block voids the list.
        blocks = content if isinstance(content, list) and all(
            isinstance(block, dict) for block in content) else []
        for block in blocks:
            kind_of = block.get("type")
            if kind_of in ("text", "reasoning"):
                if isinstance(block.get("text"), str):
                    words.append(block["text"])
            elif kind_of == "tool":
                state = dict(block["state"]) if isinstance(block.get("state"), dict) else {}
                inner = state.get("content")
                if "output" not in state and isinstance(inner, list) and all(
                        isinstance(item, dict) for item in inner):
                    state["output"] = "\n".join(
                        item["text"] for item in inner if isinstance(item.get("text"), str))
                text = _tool_text({"tool": block.get("name"), "state": state})
                if text is not None:
                    tools.append(decode.Message(decode.TOOL, text, ts))
        text = decode.bounded("\n".join(words))
        if text is not None:
            messages.append(decode.Message(decode.ASSISTANT, text, ts))
        messages.extend(tools)
    return messages


def opencode_title(session: OpenCodeSession, messages: list) -> str:
    """The session row's title, else the first-user-prompt fallback."""
    if session.title:
        return session.title
    first = next((m for m in messages if m.role == decode.USER), None)
    return (decode.title_candidate(first.text) or "") if first else ""
