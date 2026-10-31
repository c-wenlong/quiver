"""The model each session's last reply ran, for the remote session rows.

``/v1/sessions`` rows carry it as ``model`` (#643): the phone shows it in
the chat header and the session list, and a model switch typed into a
tmux pane shows up there once the next reply lands. "Current" means the
model of the newest reply the transcript holds. A switch with no reply
since (Claude Code's ``/model`` writes only a display name, OpenCode a
``model-switched`` row) is left alone on purpose, so every harness
answers the same question.

Why not the usage ledger, which records the model of every reply: the
web ledger is scanned only when a web page asks (``webapp/api.py``), so
on a hub nobody browses it can be days behind, and a switch from the
phone would never show. The catalog refreshes every 10 s, so this reads
the transcripts themselves, as cheaply as the status column does:

- File harnesses (Claude Code, Codex, Pi) read the transcript's tail
  backward and parse only lines that can hold a model, stopping at the
  first one. The answer is cached per file under its stat signature in
  ``cache/session_model_cache.json`` (``parse_cache.standalone``), so an
  unchanged transcript costs one ``stat``, across hub restarts too.
- Database harnesses (OpenCode, Kilo, Devin) ask for the session's
  newest assistant row, one indexed query, cached in memory against the
  session's own last-activity time, which moves with every new row.

The names follow the ledger's rules: Claude's ``message.model``, the
model of Codex's newest ``turn_context``, OpenCode's ``provider/model``
(``ledger.parsers.opencode_model``), Devin's ``generation_model``. Names
no launch could take back are skipped as junk (``recent_models.usable``:
``<synthetic>``, ``unknown``, Devin's ``compactor``), and the read walks
on to the reply before. A harness with no reader answers "".
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from quiver.ledger.models import json_str
from quiver.ledger.parsers import opencode_model
from quiver.paths import session_model_cache_file_for
from quiver.remote.recent_models import usable
from quiver.sessions import parse_cache, status
from quiver.sessions.models import Session

# Tail windows tried in order: a reply larger than the window is cut off
# as the partial first line, so a window with no model widens. The last
# one bounds what a transcript that never names a model can cost.
_TAIL_WINDOWS = (16 << 10, 256 << 10, 4 << 20, 16 << 20)

# Modules whose code decides a cached answer: editing one discards the
# cache file (``parse_cache.standalone``).
_CACHE_MODULES = (
    "quiver.sessions.parse_cache",
    "quiver.remote.session_model",
    "quiver.remote.recent_models",
    "quiver.remote.protocol",
)


def _claude_model(obj: dict) -> str:
    """A main-thread assistant reply's ``message.model``."""
    if obj.get("type") != "assistant" or obj.get("isSidechain") is True:
        return ""
    message = obj.get("message")
    model = json_str(message.get("model")) if isinstance(message, dict) else None
    return model if model and usable("claude", model) else ""


def _codex_model(obj: dict) -> str:
    """The model a ``turn_context`` record set for its turn."""
    if obj.get("type") != "turn_context":
        return ""
    payload = obj.get("payload")
    model = json_str(payload.get("model")) if isinstance(payload, dict) else None
    return model if model and usable("codex", model) else ""


def _pi_model(obj: dict) -> str:
    """An assistant ``message`` record's ``message.model``."""
    if obj.get("type") != "message":
        return ""
    message = obj.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    model = json_str(message.get("model"))
    return model if model and usable("pi", model) else ""


# harness -> (bytes every line worth parsing contains, picker).
_FILE_READERS = {
    "claude": (b'"model"', _claude_model),
    "codex": (b'"turn_context"', _codex_model),
    "pi": (b'"assistant"', _pi_model),
}


def tail_model(path: str, marker: bytes, pick) -> str:
    """The newest model ``pick`` finds in ``path``, read from the end.

    Only lines containing ``marker`` are parsed. A window with no model
    widens until the whole file has been read or the last window is
    spent; "" when none names one.
    """
    size = os.path.getsize(path)
    for window in _TAIL_WINDOWS:
        start = max(0, size - window)
        with open(path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(size - start)
        lines = chunk.split(b"\n")
        if start > 0:
            lines = lines[1:]  # the first line is a partial record
        for line in reversed(lines):
            if marker not in line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            if isinstance(obj, dict):
                model = pick(obj)
                if model:
                    return model
        if start == 0:
            break
    return ""


def _file_model(session: Session, ctx: dict, store) -> str:
    marker, pick = _FILE_READERS[session.tool_name]
    path = status.transcript_path(session, ctx)
    if not path:
        return ""
    model = parse_cache.lookup_in(
        store, f"{session.tool_name}:model", path,
        lambda: tail_model(path, marker, pick),
        valid=lambda value: isinstance(value, str),
    )
    return model if isinstance(model, str) else ""


# --------------------------------------------------------------- databases

# Rows read per query while looking for a usable name: a run of junk
# (Devin's compactor turns) pages on rather than hiding an older reply.
_DB_PAGE = 20


def _newest_usable(conn: sqlite3.Connection, sql: str, args: tuple,
                   harness: str, name) -> tuple[float, str] | None:
    """``(sort key, model)`` of the newest row ``sql`` (newest first,
    sort key in column 0) yields whose ``name(row)`` is usable, paging
    through the rows until one is or they run out."""
    offset = 0
    while True:
        rows = conn.execute(f"{sql} LIMIT ? OFFSET ?",
                            (*args, _DB_PAGE, offset)).fetchall()
        for row in rows:
            model = name(row)
            if model and usable(harness, model):
                return float(row[0] or 0), model
        if len(rows) < _DB_PAGE:
            return None
        offset += _DB_PAGE


def _opencode_name(row) -> str:
    _created, provider, model_id = row
    if not isinstance(model_id, str) or not model_id:
        return ""
    return opencode_model(provider, model_id)


def _opencode_db_model(conn: sqlite3.Connection, session_id: str,
                       harness: str) -> str:
    """The newest assistant row's ``provider/model``: OpenCode 2.x's
    ``session_message`` or a legacy ``message`` row, whichever is newer."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    found = []
    if "session_message" in tables:
        found.append(_newest_usable(
            conn,
            "SELECT time_created, json_extract(data, '$.model.providerID'),"
            " json_extract(data, '$.model.id') FROM session_message"
            " WHERE session_id = ? AND type = 'assistant'"
            " ORDER BY time_created DESC", (session_id,), harness,
            _opencode_name))
    if "message" in tables:
        found.append(_newest_usable(
            conn,
            "SELECT time_created, json_extract(data, '$.providerID'),"
            " json_extract(data, '$.modelID') FROM message"
            " WHERE session_id = ? AND json_extract(data, '$.role') = 'assistant'"
            " ORDER BY time_created DESC", (session_id,), harness,
            _opencode_name))
    found = [hit for hit in found if hit is not None]
    return max(found, key=lambda hit: hit[0])[1] if found else ""


def _devin_db_model(conn: sqlite3.Connection, session_id: str,
                    harness: str) -> str:
    hit = _newest_usable(
        conn,
        "SELECT row_id, json_extract(chat_message, '$.metadata.generation_model')"
        " FROM message_nodes WHERE session_id = ? AND json_valid(chat_message)"
        " AND json_extract(chat_message, '$.role') = 'assistant'"
        " AND json_extract(chat_message, '$.metadata.generation_model') IS NOT NULL"
        " ORDER BY row_id DESC", (session_id,), harness,
        lambda row: row[1] if isinstance(row[1], str) else "")
    return hit[1] if hit else ""


# harness -> (database path under $HOME, reader).
_DB_READERS = {
    "opencode": ("~/.local/share/opencode/opencode.db", _opencode_db_model),
    "kilo": ("~/.local/share/kilo/kilo.db", _opencode_db_model),
    "devin": ("~/.local/share/devin/cli/sessions.db", _devin_db_model),
}

_db_lock = threading.Lock()
# (harness, session id) -> (session.timestamp when read, model).
_db_seen: dict[tuple[str, str], tuple[float, str]] = {}


def _open_ro(path: str) -> sqlite3.Connection | None:
    if not os.path.exists(path):
        return None
    try:
        # as_uri() percent-encodes "?", "#" and "%" in the path.
        return sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro",
                               uri=True, timeout=2)
    except (sqlite3.Error, ValueError):
        return None


def _db_models(sessions: list[Session]) -> dict[int, str]:
    """``index -> model`` for the database harnesses' sessions.

    A session whose last activity has not moved since its last read
    keeps that answer; only the others are queried, one connection per
    database per pass. Entries for sessions not in this pass are
    dropped, so the map never outgrows one listing.
    """
    out: dict[int, str] = {}
    wanted: dict[str, list[int]] = {}
    with _db_lock:
        seen = {}
        for i, session in enumerate(sessions):
            if session.tool_name not in _DB_READERS or not session.session_id:
                continue
            key = (session.tool_name, session.session_id)
            hit = _db_seen.get(key)
            if hit is not None and hit[0] == session.timestamp:
                out[i] = hit[1]
                seen[key] = hit
            else:
                wanted.setdefault(session.tool_name, []).append(i)
        for harness, indexes in wanted.items():
            raw_path, read = _DB_READERS[harness]
            conn = _open_ro(os.path.expanduser(raw_path))
            if conn is None:
                continue
            try:
                for i in indexes:
                    session = sessions[i]
                    try:
                        model = read(conn, session.session_id, harness)
                    except sqlite3.Error:
                        continue  # locked or reshaped: ask again next pass
                    out[i] = model
                    seen[(harness, session.session_id)] = (
                        session.timestamp, model)
            finally:
                conn.close()
        _db_seen.clear()
        _db_seen.update(seen)
    return out


# ------------------------------------------------------------------ entry

def session_models(sessions: list[Session]) -> list[str]:
    """One model name per session ("" when unknown), in input order."""
    out = [""] * len(sessions)
    for i, model in _db_models(sessions).items():
        out[i] = model
    ctx: dict = {}
    with parse_cache.standalone(session_model_cache_file_for,
                                _CACHE_MODULES) as store:
        for i, session in enumerate(sessions):
            if session.tool_name not in _FILE_READERS:
                continue
            try:
                out[i] = _file_model(session, ctx, store)
            except (OSError, ValueError):
                out[i] = ""
    return out


# harness -> the API channel whose injector takes the ``model`` op
# (``inject.switch_model``: the pick rides the channel's next turn).
_API_MODEL_MODES = {
    "claude": "headless",
    "codex": "appserver",
    "opencode": "serve",
}


def model_modes(harness: str) -> list[str]:
    """The supervised modes in which the ``model`` control op switches
    ``harness``'s model: ``tmux`` when its TUI takes ``/model <name>``
    (``slash_builtins.launch_op_ok``), plus the API channel that takes
    it on its next turn (``_API_MODEL_MODES``)."""
    from quiver.remote import slash_builtins

    modes = ["tmux"] if slash_builtins.launch_op_ok(harness, "model") else []
    if harness in _API_MODEL_MODES:
        modes.append(_API_MODEL_MODES[harness])
    return modes
