"""Read path: transcript bytes → ``message.appended`` events.

Copy of the ``search_index`` files-row idea: a byte-offset cursor per
transcript, stat → read appended bytes → decode new records → emit
``RemoteMessage`` dicts. The upgrade over ``reports/transcripts.py`` is
detail: that layer strips thinking and collapses tool calls for reports;
a chat UI wants the raw block detail (Claude ``thinking`` /
``redacted_thinking`` blocks and compact boundaries, Codex reasoning
items and ``compacted`` payloads, opencode part types).

Three source kinds cover every parser the registry knows:

- ``JsonlTailSource`` — incremental byte-offset reads over a transcript
  file (claude, codex, pi, cursor, droid, grok, freebuff).
- ``OpencodeSource`` — SQLite ``part`` rows with a ``time_created``
  cursor, plus opencode 2.x ``session_message`` rows with a ``seq``
  cursor (opencode, kilo share the drizzle schema).
- ``SnapshotSource`` — ``read_transcript`` re-reads diffed by message
  fingerprint, for the remaining JSON/SQLite stores (continue, cline,
  gemini, copilot, devin, forge, crush).

Polling is "~1s while ``sessions/status.py`` says active, back off idle",
plus a short hot window after the hub or a satellite writes to a session
(``TailManager.wake``), and ``_redact_secrets`` runs on every text before it can leave the
process — transcript text is not user-originated.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from quiver.paths import expand_user, remote_dir_for
from quiver.remote import injected, protocol, supervise
from quiver.reports import transcripts as _tr
from quiver.sessions import opencode_store
from quiver.sessions.models import Session

# Poll cadence: tight while the session is mid-turn, relaxed otherwise.
POLL_ACTIVE_S = 1.0
POLL_IDLE_S = 8.0
# After a write to a session (a message going out, an approval answer, a
# control op, an attach) its tailer polls at once and then this fast for
# HOT_WINDOW_S, so the echo and first reply show up without waiting out
# an idle interval the catalog has not yet flipped to active. Each new
# write re-arms the window. Snapshot sources cap this at POLL_ACTIVE_S
# (see SnapshotSource.hot_poll_s).
POLL_HOT_S = 0.25
HOT_WINDOW_S = 15.0
# Tmux-pane approval prompt watch — a subprocess per attached session,
# so slower than the transcript poll.
PANE_APPROVAL_S = 3.0


def _approval_prompt_line(pane: str) -> str:
    """A one-line summary of the on-screen permission question."""
    for line in pane.splitlines():
        text = line.strip()
        if not text:
            continue
        low = text.lower()
        if "do you want" in low or "allow" in low or "permission" in low:
            return text[:140]
    return "Approval requested"

# How many of the most recent sessions get a live tailer.
TAIL_MAX_SESSIONS = 100

# Partial-line safety: never decode more than this many bytes of tail at
# once; the remainder waits for the next poll.
_MAX_CHUNK = 4 << 20

# A session the hub starts tailing with no cursor and no journal gets its
# recent history, not all of it: the phone shows the last 500 messages,
# and reading a hundred whole transcripts at startup held >1 GB.
BACKFILL_BYTES = 2 << 20
BACKFILL_MESSAGES = 500

# Snapshot stores (no byte cursor) re-read only when something changed;
# this is how long a "nothing changed" answer is trusted before a full
# re-read anyway, in case a store writes somewhere the check can't see.
SNAPSHOT_RECHECK_S = 60.0
# A bounded read (Devin) returns this many newest chain nodes, so a poll
# sees a sliding window; more than a poll interval ever adds.
SNAPSHOT_WINDOW = 1000


def _rec_ts(rec: dict) -> float | None:
    """Epoch seconds from a record's timestamp field, whatever its shape."""
    for key in ("timestamp", "ts", "time", "created_at"):
        value = rec.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Engines store ms; a value too large for seconds is ms.
            v = float(value)
            return v / 1000 if v > 1e12 else v
        if isinstance(value, str):
            from quiver.sessions.engines.common import parse_iso_ts

            parsed = parse_iso_ts(value)
            if parsed:
                return parsed / 1000
    return None


def _role_kind(role: Any) -> str:
    role = str(role or "").lower()
    return "user" if role in {"user", "human"} else "assistant"


_ARG_HINT_KEYS = ("command", "cmd", "path", "file_path", "filePath", "filename")


def _summarize_args(name: str, args: Any) -> tuple[str, str]:
    """(one-line card summary, full JSON) for a tool call's arguments."""
    if isinstance(args, str):
        try:
            decoded = json.loads(args)
            if isinstance(decoded, (dict, list)):
                args = decoded
        except (json.JSONDecodeError, RecursionError):
            pass
    try:
        full = json.dumps(args, ensure_ascii=False) if args is not None else ""
    except (TypeError, ValueError):
        full = str(args)
    # args_full lands in the journal and on phones — redact like the
    # one-line summary does, or a `KEY=sk-...` Bash arg leaks verbatim.
    full = _tr._redact_secrets(full)
    if isinstance(args, dict):
        for key in _ARG_HINT_KEYS:
            value = args.get(key)
            if value:
                return f"{name} · {_tr._redact_secrets(str(value))[:160]}", full
    return f"{name}", full


def _decode_blocks(role: str, content: Any, ts: float | None) -> list[dict]:
    """Block-detail decode for ``message.content`` lists.

    Covers the Claude/pi/Cursor shape where content is a list of typed
    blocks; unknown block dicts fall back to their text member.
    """
    if isinstance(content, str):
        text = _tr._clean_text(content)
        return [protocol.message(_role_kind(role), text, ts=ts)] if text else []
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    pending_text: list[Any] = []

    def flush_text() -> None:
        # Consecutive text-ish blocks merge into one role message, emitted
        # at the position where they appeared (block order matters).
        text = _tr._clean_text(pending_text)
        pending_text.clear()
        if text:
            out.append(protocol.message(_role_kind(role), text, ts=ts))

    for block in content:
        if not isinstance(block, dict):
            pending_text.append(block)
            continue
        btype = str(block.get("type") or "").lower()
        if btype in {"thinking", "redacted_thinking", "reasoning"}:
            flush_text()
            text = _tr._clean_text(
                block.get("thinking") or block.get("text") or block.get("summary")
            )
            if text:
                out.append(protocol.message("thinking", text, ts=ts))
        elif btype in {"tool_use", "tool-call", "toolcall", "function_call"}:
            flush_text()
            name = str(block.get("name") or block.get("tool") or "tool")
            args = block.get("input") or block.get("arguments") or block.get("args")
            summary, full = _summarize_args(name, args)
            out.append(protocol.message(
                "tool_use", "",
                ts=ts,
                tool={
                    "name": name,
                    "call_id": str(block.get("id") or block.get("call_id") or ""),
                    "args_summary": summary,
                    "args_full": full,
                },
            ))
        elif btype in {
            "tool_result", "toolresult", "tool-result", "function_call_output",
        }:
            flush_text()
            body = block.get("content") or block.get("output") or block.get("text")
            text = _tr._clean_text(body)
            out.append(protocol.message(
                "tool_result", text,
                ts=ts,
                result_ref=str(
                    block.get("tool_use_id")
                    or block.get("call_id")
                    or block.get("id")
                    or ""
                ) or None,
            ))
        else:
            pending_text.append(block)
    flush_text()
    return out


def _decode_claude(rec: dict) -> list[dict]:
    ts = _rec_ts(rec)
    rtype = rec.get("type")
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    if rtype == "user":
        if rec.get("isMeta"):
            return []
        return _decode_blocks("user", msg.get("content"), ts)
    if rtype == "assistant":
        if rec.get("isApiErrorMessage"):
            text = _tr._clean_text(msg.get("content")) or "API error"
            return [protocol.message("error", text, ts=ts)]
        return _decode_blocks("assistant", msg.get("content"), ts)
    if rtype == "summary":
        text = _tr._clean_text(rec.get("summary"))
        return [protocol.message("compaction", text, ts=ts)] if text else []
    if rtype == "system":
        if rec.get("subtype") == "compact_boundary" or rec.get("isCompactBoundary"):
            return [protocol.message("compaction", "context compacted", ts=ts)]
        text = _tr._clean_text(rec.get("content") or rec.get("text"))
        return [protocol.message("system", text, ts=ts)] if text else []
    return []


_CODEX_TEXT_BLOCKS = {"input_text", "output_text", "text"}


def _codex_text(blocks: Any) -> str:
    parts = [
        str(b.get("text") or "")
        for b in (blocks if isinstance(blocks, list) else [])
        if isinstance(b, dict) and b.get("type") in _CODEX_TEXT_BLOCKS
    ]
    return _tr._clean_text("\n".join(p for p in parts if p))


def _decode_codex(rec: dict) -> list[dict]:
    ts = _rec_ts(rec)
    rtype = rec.get("type")
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    ptype = str(payload.get("type") or "")
    if rtype == "response_item":
        if ptype == "message":
            # Codex's own instructions (developer/system turns: skills,
            # time context, collaboration mode) are not the agent talking.
            if str(payload.get("role") or "").lower() in {"developer", "system"}:
                return []
            return [m for m in [
                protocol.message(_role_kind(payload.get("role")),
                                 _codex_text(payload.get("content")), ts=ts)
            ] if m["text"]]
        if ptype == "reasoning":
            summary = payload.get("summary") or []
            text = _tr._clean_text(
                [b.get("text") for b in summary if isinstance(b, dict)]
            ) or _codex_text(payload.get("content"))
            return [protocol.message("thinking", text, ts=ts)] if text else []
        if ptype in {"function_call", "custom_tool_call", "local_shell_call"}:
            name = str(payload.get("name") or "tool")
            args = payload.get("arguments") or payload.get("input")
            summary, full = _summarize_args(name, args)
            return [protocol.message(
                "tool_use", "",
                ts=ts,
                tool={
                    "name": name,
                    "call_id": str(payload.get("call_id") or payload.get("id") or ""),
                    "args_summary": summary,
                    "args_full": full,
                },
            )]
        if ptype in {"function_call_output", "custom_tool_call_output"}:
            return [protocol.message(
                "tool_result",
                _tr._clean_text(payload.get("output")),
                ts=ts,
                result_ref=str(payload.get("call_id") or "") or None,
            )]
        if "compact" in ptype:
            return [protocol.message("compaction", "context compacted", ts=ts)]
        return []
    if rtype == "event_msg":
        if ptype == "turn_aborted":
            return [protocol.message("system", "turn interrupted", ts=ts)]
        if "compact" in ptype:
            return [protocol.message("compaction", "context compacted", ts=ts)]
        return []
    return []


def _decode_generic(rec: dict) -> list[dict]:
    """Fallback: normalize via ``_messages_from_record`` when no block
    structure is obvious (droid, grok, freebuff and any new shape)."""
    nested = rec.get("message") if isinstance(rec.get("message"), dict) else None
    if nested is not None:
        role = nested.get("role") or rec.get("role")
        content = nested.get("content", nested.get("text"))
        if isinstance(content, list):
            got = _decode_blocks(role, content, _rec_ts(rec))
            if got:
                return got
    out: list[dict] = []
    for msg in _tr._messages_from_record(rec):
        out.append(_normalized_to_payload(msg))
    return out


def _normalized_to_payload(msg) -> dict:
    """One ``NormalizedMessage`` → a RemoteMessage payload."""
    if msg.role == "tool":
        label, _, _sep = msg.text.partition(":")
        name = label.strip()[:40] or "tool"
        return protocol.message(
            "tool_use", "",
            ts=msg.timestamp,
            tool={
                "name": name,
                "call_id": "",
                "args_summary": msg.text[:160],
                "args_full": msg.text[:8000],
            },
        )
    return protocol.message(
        "user" if msg.role == "human" else "assistant",
        msg.text, ts=msg.timestamp,
    )


_DECODERS: dict[str, Callable[[dict], list[dict]]] = {
    "claude": _decode_claude,
    "codex": _decode_codex,
}


def decode_record(tool_name: str, record: dict) -> list[dict]:
    """One transcript record → zero or more RemoteMessage payloads."""
    try:
        decoder = _DECODERS.get(tool_name, _decode_generic)
        return injected.clean_all(decoder(record))
    except Exception:
        return []


# ── transcript file resolution ────────────────────────────────────────────

# Roots mirror transcripts.py's reader registry; ``_find_session_file``
# resolves ``session_id`` to a file under each (stem or path-part match).
_JSONL_ROOTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "claude": ("~/.claude/projects", ("*.jsonl",)),
    "droid": ("~/.factory/sessions", ("*.jsonl",)),
    "codex": ("~/.codex/sessions", ("*.jsonl",)),
    "pi": ("~/.pi/agent/sessions", ("*.jsonl",)),
    "cursor": ("~/.cursor/projects", ("*.jsonl",)),
    "grok": ("~/.grok/sessions", ("chat_history.jsonl",)),
    "freebuff": ("~/.config/manicode/projects", ("log.jsonl",)),
}

_OPENCODE_DBS = {
    "opencode": "~/.local/share/opencode/opencode.db",
    "kilo": "~/.local/share/kilo/kilo.db",
}


def resolve_jsonl_path(session: Session) -> Path | None:
    entry = _JSONL_ROOTS.get(session.tool_name)
    if entry is None:
        return None
    if session.session_id:
        direct = expand_user(session.session_id)
        if direct.is_file():
            return direct
    root, patterns = entry
    root = expand_user(root)
    if session.tool_name == "claude" and session.session_id:
        # Resume-from-another-cwd leaves one copy per project dir; the
        # newest write is live (same rule as sessions/status.py).
        hits = glob.glob(
            os.path.join(str(root), "*", session.session_id + ".jsonl")
        )
        if hits:
            hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return Path(hits[0])
        return None
    return _tr._find_session_file(root, session.session_id, patterns)


# ── sources ──────────────────────────────────────────────────────────────


class JsonlTailSource:
    """Incremental byte-offset reader over one JSONL transcript."""

    def __init__(self, path: Path, tool_name: str) -> None:
        self.path = path
        self.tool_name = tool_name
        self.offset = 0
        # Set by start_near_end: the first read begins mid-line, so the
        # partial line up to the first newline is dropped.
        self._align = False

    def state(self) -> dict:
        return {"source": "jsonl", "path": str(self.path), "offset": self.offset}

    def restore(self, state: dict) -> None:
        if state.get("source") == "jsonl" and state.get("path") == str(self.path):
            try:
                self.offset = int(state.get("offset") or 0)
            except (TypeError, ValueError):
                self.offset = 0

    def skip_to_end(self) -> None:
        try:
            self.offset = self.path.stat().st_size
        except OSError:
            pass

    def start_near_end(self, max_bytes: int | None = None) -> None:
        """Backfill only the last ``max_bytes`` (default BACKFILL_BYTES)."""
        max_bytes = BACKFILL_BYTES if max_bytes is None else max_bytes
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size > max_bytes:
            self.offset = size - max_bytes
            self._align = True

    def poll(self) -> list[dict]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size <= self.offset:
            if size < self.offset:  # truncated/rotated: restart
                self.offset = 0
                # A new file starts on a whole line; nothing to align.
                self._align = False
            else:
                return []
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read(min(size - self.offset, _MAX_CHUNK))
        except OSError:
            return []
        if self._align:
            # Mid-line start: drop through the first newline.
            first_nl = chunk.find(b"\n")
            if first_nl < 0:
                if len(chunk) >= _MAX_CHUNK:
                    # Still inside one huge record: step past this chunk
                    # and keep looking, rather than re-reading it forever.
                    self.offset += len(chunk)
                return []
            self.offset += first_nl + 1
            chunk = chunk[first_nl + 1:]
            self._align = False
        # offset only advances past complete lines; an incomplete tail line
        # is re-read from the same offset on the next poll.
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            if size - self.offset <= _MAX_CHUNK:
                return []  # writer still mid-line; wait for the rest
            # One record bigger than the chunk cap: consume through its
            # newline so it can't wedge every later message forever.
            self.offset = self._skip_oversized()
            return [protocol.message(
                "system",
                "[skipped an oversized transcript record]",
            )]
        self.offset += last_nl + 1
        out: list[dict] = []
        for raw in chunk[:last_nl].split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.extend(decode_record(self.tool_name, rec))
        return out

    def _skip_oversized(self) -> int:
        """Advance past a record larger than the chunk cap.

        Scans forward from the current offset to the next newline so a
        multi-MB single line can't starve everything appended after it.
        """
        offset = self.offset + _MAX_CHUNK
        try:
            size = self.path.stat().st_size
            with open(self.path, "rb") as fh:
                while offset < size:
                    fh.seek(offset)
                    piece = fh.read(_MAX_CHUNK)
                    idx = piece.find(b"\n")
                    if idx >= 0:
                        return offset + idx + 1
                    offset += len(piece)
                    if not piece:
                        break
        except OSError:
            pass
        return size


def _completed(data: dict) -> bool:
    """Whether an opencode 2.x assistant row has finished streaming."""
    times = data.get("time")
    return isinstance(times, dict) and bool(times.get("completed"))


class OpencodeSource:
    """Incremental poll over opencode/kilo's drizzle ``part`` table, and
    the ``session_message`` rows opencode 2.x writes instead.

    The two stores keep separate cursors: ``cursor`` is the newest
    ``part.time_created`` (epoch ms) seen, ``seq`` the last v2 row
    emitted. A v2 assistant row is written when its reply starts and
    rewritten as it streams, so the v2 cursor stops before the first
    assistant row with no ``time.completed`` and reads it again next poll.
    """

    def __init__(self, db_path: Path, session_id: str) -> None:
        self.db_path = db_path
        self.session_id = session_id
        self.cursor = 0  # max time_created (epoch ms) seen
        self.seq: int | None = None  # last v2 row emitted; None = not placed yet
        self._roles: dict[str, str] = {}

    def state(self) -> dict:
        return {
            "source": "opencode",
            "session_id": self.session_id,
            "cursor": self.cursor,
            "seq": self.seq,
        }

    def restore(self, state: dict) -> None:
        if state.get("source") == "opencode":
            try:
                self.cursor = int(state.get("cursor") or 0)
            except (TypeError, ValueError):
                self.cursor = 0
            try:
                seq = state.get("seq")
                self.seq = None if seq is None else int(seq)
            except (TypeError, ValueError):
                self.seq = None

    def skip_to_end(self) -> None:
        conn = self._open()
        if conn is None:
            return
        try:
            row = conn.execute(
                "SELECT MAX(time_created) FROM part WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            if row and row[0]:
                self.cursor = int(row[0])
        except sqlite3.Error:
            pass
        try:
            self.seq = self._settled_seq(opencode_store.v2_rows(conn, self.session_id), -1)
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    @staticmethod
    def _settled_seq(rows: list, seq: int) -> int:
        """The last seq before the first assistant row still streaming:
        that row is rewritten in place when it finishes, so a cursor past
        it would never read the finished reply."""
        for _id, kind, row_seq, _created, data in rows:
            if kind == "assistant" and not _completed(data):
                break
            seq = row_seq
        return seq

    def _place_seq(self, conn: sqlite3.Connection) -> int:
        """The v2 cursor for a source restored from a cursor file written
        before it existed: every v2 row at or before the ``part`` cursor's
        time counts as already sent, so an upgrade replays nothing."""
        if self.seq is None:
            self.seq = -1  # seq starts at 0
            if self.cursor:
                sent = [r for r in opencode_store.v2_rows(conn, self.session_id) if r[3] <= self.cursor]
                self.seq = self._settled_seq(sent, -1)
        return self.seq

    def _open(self) -> sqlite3.Connection | None:
        try:
            return sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True
            )
        except sqlite3.Error:
            return None

    def _role_for(self, conn: sqlite3.Connection, message_id: str) -> str:
        role = self._roles.get(message_id)
        if role is None:
            role = ""
            try:
                row = conn.execute(
                    "SELECT data FROM message WHERE id = ?", (message_id,)
                ).fetchone()
                if row:
                    data = json.loads(row[0])
                    role = _role_kind(data.get("role"))
            except Exception:
                role = ""
            self._roles[message_id] = role or "assistant"
        return self._roles[message_id]

    def poll(self) -> list[dict]:
        conn = self._open()
        if conn is None:
            return []
        out: list[dict] = []
        try:
            rows = conn.execute(
                "SELECT id, message_id, time_created, data FROM part "
                "WHERE session_id = ? AND time_created > ? "
                "ORDER BY time_created, id",
                (self.session_id, self.cursor),
            ).fetchall()
            for part_id, message_id, created, raw in rows:
                self.cursor = max(self.cursor, int(created))
                try:
                    part = json.loads(raw)
                except (TypeError, ValueError, RecursionError):
                    continue
                if not isinstance(part, dict):
                    continue
                ts = float(created) / 1000
                ptype = str(part.get("type") or "").lower()
                role = self._role_for(conn, str(message_id))
                if ptype == "text":
                    text = _tr._clean_text(part.get("text"))
                    if text:
                        out.append(protocol.message(
                            _role_kind(role), text, ts=ts))
                elif ptype in {"reasoning", "thinking"}:
                    text = _tr._clean_text(part.get("text"))
                    if text:
                        out.append(protocol.message("thinking", text, ts=ts))
                elif ptype in {"tool", "tool-invocation", "tool_result"}:
                    state = part.get("state")
                    state = state if isinstance(state, dict) else {}
                    name = str(part.get("tool") or "tool")
                    call_id = str(part.get("id") or part_id or "")
                    summary, full = _summarize_args(name, state.get("input"))
                    out.append(protocol.message(
                        "tool_use", "",
                        ts=ts,
                        tool={
                            "name": name,
                            "call_id": call_id,
                            "args_summary": summary,
                            "args_full": full,
                        },
                    ))
                    status = str(state.get("status") or "")
                    if status in {"completed", "error"} or state.get("output"):
                        out.append(protocol.message(
                            "tool_result",
                            _tr._clean_text(state.get("output")),
                            ts=ts,
                            result_ref=call_id or None,
                        ))
                elif "compact" in ptype:
                    out.append(protocol.message(
                        "compaction", "context compacted", ts=ts))
            out.extend(self._poll_v2(conn))
        except sqlite3.Error:
            return injected.clean_all(out)
        finally:
            conn.close()
        return injected.clean_all(out)

    def _poll_v2(self, conn: sqlite3.Connection) -> list[dict]:
        out: list[dict] = []
        after = self._place_seq(conn)
        for _id, kind, seq, created, data in opencode_store.v2_rows(
            conn, self.session_id, after_seq=after
        ):
            ts = float(created) / 1000
            if kind in opencode_store.PROMPT_TYPES:
                text = _tr._clean_text(data.get("text"))
                if text:
                    out.append(protocol.message("user", text, ts=ts))
            elif kind == "assistant":
                if not _completed(data):
                    break  # still streaming: read it again next poll
                out.extend(self._v2_assistant(data, ts))
            elif kind == "compaction":
                out.append(protocol.message(
                    "compaction", "context compacted", ts=ts))
            self.seq = seq
        return out

    def _v2_assistant(self, data: dict, ts: float) -> list[dict]:
        out: list[dict] = []
        for block in opencode_store.content_blocks(data):
            btype = str(block.get("type") or "").lower()
            if btype == "text":
                text = _tr._clean_text(block.get("text"))
                if text:
                    out.append(protocol.message("assistant", text, ts=ts))
            elif btype in {"reasoning", "thinking"}:
                text = _tr._clean_text(block.get("text"))
                if text:
                    out.append(protocol.message("thinking", text, ts=ts))
            elif btype == "tool":
                state = block.get("state")
                state = state if isinstance(state, dict) else {}
                name = str(block.get("name") or "tool")
                call_id = str(block.get("id") or "")
                summary, full = _summarize_args(name, state.get("input"))
                out.append(protocol.message(
                    "tool_use", "",
                    ts=ts,
                    tool={
                        "name": name,
                        "call_id": call_id,
                        "args_summary": summary,
                        "args_full": full,
                    },
                ))
                if str(state.get("status") or "") in {"completed", "error"}:
                    out.append(protocol.message(
                        "tool_result",
                        _tr._clean_text(state.get("content") or state.get("error")),
                        ts=ts,
                        result_ref=call_id or None,
                    ))
        return out


class SnapshotSource:
    """Diff ``read_transcript`` output by message fingerprint.

    For stores without a byte cursor (json files, sqlite schemas we do
    not track per-record), the whole normalized stream is re-read and
    new tail items emitted. Cheap enough at the idle poll cadence.
    """

    # A changed snapshot store is re-read whole (Devin: up to
    # SNAPSHOT_WINDOW chain nodes), so a hot window polls these no
    # faster than an active session already does (issue #515).
    hot_poll_s = POLL_ACTIVE_S

    def __init__(self, session: Session) -> None:
        self.session = session
        self._fps: list[str] = []
        self._sources: list[str] = []
        self._token: tuple | None = None
        self._token_at = 0.0
        # Set by start_near_end: the first poll emits only the newest
        # BACKFILL_MESSAGES instead of the whole history.
        self._backfill_cap: int | None = None

    def state(self) -> dict:
        return {"source": "snapshot", "fps": self._fps}

    def restore(self, state: dict) -> None:
        if state.get("source") == "snapshot":
            fps = state.get("fps")
            if isinstance(fps, list):
                self._fps = [str(f) for f in fps]

    def skip_to_end(self) -> None:
        try:
            tr = _tr.read_transcript_tail(self.session, SNAPSHOT_WINDOW)
        except Exception:
            return
        self._fps = [self._fp(m) for m in tr.messages]

    def start_near_end(self, max_messages: int | None = None) -> None:
        self._backfill_cap = BACKFILL_MESSAGES if max_messages is None else max_messages

    def change_token(self) -> tuple | None:
        """A cheap stand-in for "did this session change", or None when
        there is nothing to check yet (the first read).

        Devin keeps every session in one shared SQLite file, so its token
        is that session's newest message row and chain head (an indexed
        query). Other stores use the stat of the files the last read
        listed, their ``-wal`` files and their folders, so a write, a
        checkpoint or a new file each change it.
        """
        if self.session.tool_name == "devin":
            return _devin_token(self.session.session_id)
        if not self._sources:
            return None
        parts: list[tuple] = []
        for src in self._sources:
            for path in (src, src + "-wal", os.path.dirname(src)):
                try:
                    st = os.stat(path)
                    parts.append((path, st.st_mtime_ns, st.st_size))
                except OSError:
                    parts.append((path, None, None))
        return tuple(parts)

    @staticmethod
    def _fp(msg) -> str:
        return hashlib.sha1(
            f"{msg.role}\x00{msg.kind}\x00{msg.text}".encode("utf-8")
        ).hexdigest()[:16]

    def poll(self) -> list[dict]:
        now = time.monotonic()
        token = self.change_token()
        if (token is not None and token == self._token
                and now - self._token_at < SNAPSHOT_RECHECK_S):
            return []
        try:
            tr = _tr.read_transcript_tail(self.session, SNAPSHOT_WINDOW)
        except Exception:
            return []
        self._sources = list(getattr(tr, "source_paths", None) or [])
        # The token from before the read is the one kept: a write that
        # lands mid-read changes the next token, so it is picked up then.
        self._token = token if token is not None else self.change_token()
        self._token_at = now
        fps = [self._fp(m) for m in tr.messages]
        new = tr.messages[_new_from(self._fps, fps):]
        if self._backfill_cap is not None:
            new = new[-self._backfill_cap:]
            self._backfill_cap = None
        out = injected.clean_all([_normalized_to_payload(msg) for msg in new])
        self._fps = fps
        return out


# Where the Devin CLI keeps every session (read by reports.transcripts too).
DEVIN_DB = "~/.local/share/devin/cli/sessions.db"


def _new_from(old: list[str], new: list[str]) -> int:
    """Index in ``new`` where messages not seen before start.

    A full read grows at the end, so the shared prefix is the answer (a
    rewrite re-emits from the fork). A bounded read slides: the old
    window's tail reappears at the head of the new one, so the longest
    such overlap is the answer. No overlap at all means everything is new.
    """
    common = 0
    for a, b in zip(old, new):
        if a != b:
            break
        common += 1
    if common == len(old) or not old or not new:
        return common
    # Fingerprints are content, so a repeated message can make a short
    # prefix match by chance after the window slid ([a,b,a,c] then
    # [a,c,d,e]). The longest slide overlap is checked too, and the
    # larger answer wins. Only positions holding the new window's first
    # message can start an overlap; the earliest is the longest.
    overlap = 0
    first = new[0]
    for i, fp in enumerate(old):
        if fp == first and old[i:] == new[: len(old) - i]:
            overlap = len(old) - i
            break
    return max(common, overlap)


def _devin_token(session_id: str) -> tuple | None:
    """Devin's newest message row and chain head for one session."""
    path = Path(os.path.expanduser(DEVIN_DB))
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT MAX(row_id), COUNT(*) FROM message_nodes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        head = conn.execute(
            "SELECT main_chain_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return (tuple(rows or ()), tuple(head or ()))
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def make_source(session: Session) -> "JsonlTailSource | OpencodeSource | SnapshotSource | None":
    """A live source for ``session``, or None when nothing is readable."""
    path = resolve_jsonl_path(session)
    if path is not None:
        return JsonlTailSource(path, session.tool_name)
    db = _OPENCODE_DBS.get(session.tool_name)
    if db is not None and session.session_id:
        db_path = expand_user(db)
        if db_path.exists():
            return OpencodeSource(db_path, session.session_id)
    if session.tool_name in _tr.READER_REGISTRY:
        return SnapshotSource(session)
    return None


# ── cursor persistence + the poll loop ────────────────────────────────────


def _cursor_path(cursors_dir: Path, key: str) -> Path:
    return cursors_dir / f"{protocol.key_slug(key)}.json"


class Tailer:
    """One session's source plus its durable cursor."""

    def __init__(
        self,
        key: str,
        session: Session,
        source,
        cursors_dir: Path,
        has_journal: bool,
    ) -> None:
        self.key = key
        self.session = session
        self.source = source
        self._cursor_path = _cursor_path(cursors_dir, key)
        state = self._load_cursor()
        if state:
            source.restore(state)
        elif has_journal:
            # Journal exists but the cursor is gone: skip backfill so
            # history is not duplicated; live appends still land.
            source.skip_to_end()
        elif hasattr(source, "start_near_end"):
            # First sight of this session: recent history only.
            source.start_near_end()
        self.next_poll_at = 0.0
        self._pending_save = False
        # What the cursor file holds now. A poll that leaves the source
        # here writes nothing, so the 0.25 s hot cadence and the 1 s
        # active one cost a read, not a file write, while nothing moves.
        self._saved: dict | None = state or None

    def _load_cursor(self) -> dict:
        try:
            data = json.loads(self._cursor_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_cursor(self) -> None:
        state = self.source.state()
        try:
            self._cursor_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cursor_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, self._cursor_path)
        except OSError:
            return
        self._saved = state

    def due(self, now: float) -> bool:
        return now >= self.next_poll_at

    def interval(self, active: bool, hot: bool = False) -> float:
        """Seconds until the next poll: hot beats active beats idle."""
        base = POLL_ACTIVE_S if active else POLL_IDLE_S
        if hot:
            return min(base, getattr(self.source, "hot_poll_s", POLL_HOT_S))
        return base

    def poll(self, now: float, active: bool, hot: bool = False) -> list[dict]:
        """Poll the source; the cursor is only marked dirty here.

        ``commit_cursor`` runs the save — the manager calls it after the
        events are journaled, so a crash between poll and write replays
        (duplicates, recoverable) instead of skipping messages forever.
        """
        self.next_poll_at = now + self.interval(active, hot)
        out = self.source.poll()
        # A source can move without emitting (records that decode to
        # nothing, an opencode row still streaming), so compare the
        # state rather than trusting ``out`` alone.
        if out or self.source.state() != self._saved:
            self._pending_save = True
        return out

    def commit_cursor(self) -> None:
        if self._pending_save:
            self._pending_save = False
            self.save_cursor()


class TailManager:
    """Owns the tailers; ``emit`` is the one funnel events leave through.

    ``emit(session_key, event_dict)`` is supplied by the caller — the hub
    journals + broadcasts it, a satellite forwards it upstream — so this
    class never touches the network.
    """

    def __init__(
        self,
        emit: Callable[[str, dict], None],
        machine_id: str,
        home: Path | str | None = None,
        journal_has: Callable[[str], bool] | None = None,
        max_sessions: int = TAIL_MAX_SESSIONS,
    ) -> None:
        self.emit = emit
        self.machine_id = machine_id
        self.home = Path(home) if home is not None else None
        self.cursors_dir = remote_dir_for(self.home) / "cursors"
        self._journal_has = journal_has or (lambda key: False)
        self.max_sessions = max_sessions
        self.tailers: dict[str, Tailer] = {}
        self.statuses: dict[str, str] = {}
        # Last time each key emitted a transcript append; the attach
        # idle check reads this server-side.
        self.last_append: dict[str, float] = {}
        # key -> (request_id, prompt fingerprint) of the approval the
        # pane watcher last issued; key -> last pane check time.
        self._approval_outstanding: dict[str, tuple[str, str]] = {}
        self._approval_checked_at: dict[str, float] = {}
        # Wake state, written by request threads (``wake``) and read by
        # the tail thread (``tick``); nothing else here is shared that
        # way, so this lock guards only these three. ``_woken`` holds
        # keys owed an immediate poll; ``tick`` swaps it out and clears
        # the event in one step, so a wake landing mid-tick stays set
        # and the next ``wait`` returns at once instead of being lost.
        self._wake_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._woken: set[str] = set()
        self._hot_until: dict[str, float] = {}

    def last_append_at(self, key: str) -> float | None:
        return self.last_append.get(key)

    # -- waking --------------------------------------------------------------

    def wake(self, key: str, now: float | None = None) -> None:
        """Something was just written to ``key``'s session: poll it now.

        Safe from any thread. Opens (or extends) the key's hot window
        and wakes a loop parked in ``wait``. A key with no tailer yet
        keeps its window, so an ``ensure`` that follows starts hot.
        """
        now = time.time() if now is None else now
        with self._wake_lock:
            until = now + HOT_WINDOW_S
            if until > self._hot_until.get(key, 0.0):
                self._hot_until[key] = until
            self._woken.add(key)
            self._wake_event.set()

    def interrupt(self) -> None:
        """End a ``wait`` early without scheduling a poll (shutdown)."""
        self._wake_event.set()

    def wait(self, timeout: float) -> bool:
        """Park the tail loop until ``timeout`` or the next wake."""
        return self._wake_event.wait(timeout)

    def is_hot(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._wake_lock:
            return self._hot_until.get(key, 0.0) > now

    def waking(self, key: str, injector):
        """``injector`` with a wake before and after each write it makes."""
        return _WakingInjector(injector, lambda: self.wake(key))

    def _take_wakes(self, now: float) -> tuple[set[str], dict[str, float]]:
        with self._wake_lock:
            woken, self._woken = self._woken, set()
            self._wake_event.clear()
            self._hot_until = {
                k: t for k, t in self._hot_until.items() if t > now
            }
            return woken, dict(self._hot_until)

    def ensure(self, key: str, session: Session, status: str) -> None:
        """Start a tailer on demand — a client opened this session.

        Background tailing stays capped at ``max_sessions``; a phone
        opening an older session gets history and live updates anyway.
        """
        if key in self.tailers:
            return
        source = make_source(session)
        if source is None:
            return
        self.tailers[key] = Tailer(
            key, session, source, self.cursors_dir,
            self._journal_has(key),
        )
        self.statuses[key] = status

    def _source_path(self, source) -> Path | None:
        path = getattr(source, "path", None)
        return Path(path) if path else None

    def _reresolve(self, tailer: Tailer, session: Session) -> None:
        """Follow a resumed session into its new transcript file.

        Claude ``--resume`` from another folder writes a new JSONL under
        that project; the old source would keep polling a dead path.
        The journal already holds the history the new file rewrites, so
        the fresh source starts at its end — only new turns stream.
        """
        new_path = resolve_jsonl_path(session)
        if new_path is None or new_path == self._source_path(tailer.source):
            return
        source = make_source(session)
        if source is None:
            return
        source.skip_to_end()
        tailer.source = source
        tailer.save_cursor()

    def sync(self, rows: list[tuple[str, Session, str]]) -> None:
        """Reconcile tailers with the catalog.

        ``rows`` is ``(session_key, session, status)`` newest-first. New
        keys in the top slice get a source when resolvable; gone keys
        drop their tailer; status transitions emit ``session.status``
        and ``notify``. On-demand tailers (``ensure``) outside the top
        slice keep running while their session exists.
        """
        by_key = {key: (s, st) for key, s, st in rows}
        wanted = rows[: self.max_sessions]
        wanted_keys = {key for key, _s, _st in wanted}
        for key in list(self.tailers):
            if key not in by_key:
                self.tailers.pop(key, None)
                self.statuses.pop(key, None)
        for key, session, status in wanted:
            if key not in self.tailers:
                source = make_source(session)
                if source is None:
                    continue
                self.tailers[key] = Tailer(
                    key, session, source, self.cursors_dir,
                    self._journal_has(key),
                )
            else:
                # Refresh the session object: title/cwd can change as the
                # aggregator re-parses; the source may need re-resolving
                # when a resume moved the transcript file.
                tailer = self.tailers[key]
                tailer.session = session
                self._reresolve(tailer, session)
            if self.statuses.get(key) != status:
                previous = self.statuses.get(key)
                self.statuses[key] = status
                if previous is not None or status:
                    self._emit_status(key, status)
                # A notify only fires on a real transition; the first
                # sighting of a session already in error/followup is
                # state, not news (and would spam push at hub startup).
                if previous is not None:
                    self._emit_notify(key, previous, status)
        # On-demand tailers past the cap: refresh session/status but
        # keep them alive while their session still exists.
        for key in self.tailers:
            if key in wanted_keys:
                continue
            session, status = by_key.get(key, (None, None))
            if session is None:
                continue
            self.tailers[key].session = session
            self._reresolve(self.tailers[key], session)
            if self.statuses.get(key) != status:
                previous = self.statuses.get(key)
                self.statuses[key] = status
                if previous is not None:
                    self._emit_status(key, status)
                    self._emit_notify(key, previous, status)

    def _emit_status(self, key: str, status: str) -> None:
        self.emit(
            key,
            protocol.event(
                "session.status", key, self.machine_id, {"status": status}
            ),
        )

    def _emit_notify(self, key: str, old: str, new: str) -> None:
        # needs_you on a fresh followup, finished on active→done,
        # error whenever a session turns over to error.
        if new == "followup" and old != "followup":
            kind = "needs_you"
        elif new == "done" and old == "active":
            kind = "finished"
        elif new == "error" and old != "error":
            kind = "error"
        else:
            return
        self.emit(
            key,
            protocol.event(
                "notify", key, self.machine_id,
                {"kind": kind, "status": new},
            ),
        )

    def tick(self, now: float | None = None) -> int:
        """Poll every due tailer; returns events emitted."""
        now = time.time() if now is None else now
        woken, hot_until = self._take_wakes(now)
        n = 0
        for key, tailer in list(self.tailers.items()):
            if key not in woken and not tailer.due(now):
                continue
            try:
                messages = tailer.poll(
                    now, active=self.statuses.get(key) == "active",
                    hot=key in hot_until,
                )
            except Exception:
                tailer.next_poll_at = now + POLL_IDLE_S
                continue
            for msg in messages:
                self.emit(
                    key,
                    protocol.event(
                        "message.appended", key, self.machine_id, msg
                    ),
                )
                n += 1
            if messages:
                self.last_append[key] = now
            tailer.commit_cursor()
        self._poll_approvals(now)
        return n

    def _poll_approvals(self, now: float) -> None:
        """Watch supervised tmux panes for permission prompts.

        The transcript never shows a tmux-mode approval menu, so a
        phone notification has to come from the pane itself. A prompt
        appearing emits ``approval.requested`` with a fresh request_id;
        it disappearing emits ``approval.resolved``. Cheap: one
        capture-pane per attached session every few seconds.
        """
        for key in list(self.tailers):
            if (
                now - self._approval_checked_at.get(key, 0)
                < PANE_APPROVAL_S
            ):
                continue
            self._approval_checked_at[key] = now
            meta = supervise.meta_for(self.home, key)
            if not meta or meta.get("mode") != "tmux":
                if key in self._approval_outstanding:
                    rid = self._approval_outstanding.pop(key)
                    supervise.set_pending_approval(key, None)
                    self._emit_approval(
                        "approval.resolved", key,
                        {"request_id": rid, "source": "detached"},
                    )
                continue
            pane = supervise.capture_pane(
                supervise.channel_name(self.home, key)
            )
            if supervise.pane_shows_approval(pane):
                fp = supervise.approval_fingerprint(pane)
                outstanding = self._approval_outstanding.get(key)
                if outstanding is not None and outstanding[1] != fp:
                    # Prompt A was dismissed and prompt B appeared
                    # between polls — retiring A's id so a delayed
                    # reply cannot answer B.
                    supervise.set_pending_approval(key, None)
                    self._emit_approval(
                        "approval.resolved", key,
                        {"request_id": outstanding[0],
                         "source": "superseded"},
                    )
                    outstanding = None
                if outstanding is None:
                    rid = f"req-{secrets.token_hex(6)}"
                    self._approval_outstanding[key] = (rid, fp)
                    supervise.set_pending_approval(key, rid, fp)
                    prompt = _approval_prompt_line(pane)
                    self._emit_approval(
                        "approval.requested", key,
                        {"request_id": rid, "prompt": prompt},
                    )
            else:
                cur = self._approval_outstanding.pop(key, None)
                if cur is not None:
                    supervise.set_pending_approval(key, None)
                    self._emit_approval(
                        "approval.resolved", key,
                        {"request_id": cur[0], "source": "dismissed"},
                    )

    def _emit_approval(self, etype: str, key: str, data: dict) -> None:
        self.emit(
            key,
            protocol.event(etype, key, self.machine_id, data),
        )


class _WakingInjector:
    """Wraps an injector so its writes wake the session's tailer.

    The wake goes out before the write (the harness can react at once)
    and again after it returns: a headless claude ``send`` blocks for
    the whole turn, and the reply that follows deserves its own window.
    Everything else passes through untouched.
    """

    _WRITES = frozenset({"send", "approval", "control"})

    def __init__(self, inner, wake: Callable[[], None]) -> None:
        self._inner = inner
        self._wake = wake

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if name not in self._WRITES or not callable(attr):
            return attr

        def write(*args, **kwargs):
            self._wake()
            try:
                return attr(*args, **kwargs)
            finally:
                self._wake()

        return write
