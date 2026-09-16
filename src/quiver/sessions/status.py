"""Per-session status for ``swe session``: where each conversation stands.

Seven harnesses carry enough on-disk state to say more than "a session
existed": Claude Code (transcript tail, live-pid registry, bg-job state),
Cursor (terminal ``turn_ended`` record), Devin (main-chain head row in
its SQLite store, plus the ``session_locks`` pid files for liveness),
Codex (decisive ``event_msg`` in the rollout tail, with a
``response_item`` fallback), OpenCode (last ``message`` row's ``finish``
field in its SQLite store), Kilo (same drizzle schema, ``kilo.db``),
Cline (the ``status``/``pid`` lifecycle fields in the session metadata
file) and Pi (the trailing ``message`` record in the JSONL transcript).
Every other harness shows ``-``.

``followup`` is the label for a finished turn that ends by asking the user
something. Claude Code's own ``claude agents`` derives its blocked /
needs-input state by regex over the final assistant text;
``needs_followup`` is the same idea, deliberately under-matching so the
column never cries wolf.
"""

import glob
import json
import os
import re
import sqlite3
import time

from quiver.sessions.models import Session

ACTIVE = "active"
DONE = "done"
ERROR = "error"
FOLLOWUP = "followup"
INTERRUPTED = "interrupted"
UNKNOWN = ""

# A mid-turn session touched within this many seconds counts as generating.
ACTIVE_WINDOW_S = 120

# Tail-read windows tried in order: a decisive record larger than the
# window is dropped as the partial first line, so a small window alone can
# leave the probe seeing nothing. ``_walk_tail`` widens until it decides.
_TAIL_WINDOWS = (65536, 1 << 20, 16 << 20)

_DISCLAIMER_RE = re.compile(
    r"\bnothing (?:needed|required) from you\b"
    r"|\bno(?: user)? action (?:needed|required)\b",
    re.I,
)
_FENCES_RE = re.compile(r"```.*?```", re.S)
_TRAILING_Q_RE = re.compile(r"[?？]\s*$")
_NEEDS_INPUT_RE = re.compile(
    r"(?:^|\n)\s*(?:needs input|blocked|I'?m blocked)\s*[:—–-]\s*\S", re.I
)
_AWAITING_RE = re.compile(
    r"\b(?:awaiting|waiting (?:for|on)|pending)\s+"
    r"(?:your\s+(?:feedback|input|decision|response|approval|direction"
    r"|guidance|go-ahead)|you\b|the user\b)",
    re.I,
)
_ASK_RE = re.compile(
    r"\b(?:please (?:run|provide|confirm|clarify|choose|let me know)"
    r"|let me know (?:which|what|how|when)"
    r"|which (?:option|approach|one)"
    r"|should I (?:proceed|continue|use))\b",
    re.I,
)


def needs_followup(text: str) -> bool:
    """True when a finished assistant turn ends by asking the user something."""
    t = _FENCES_RE.sub("", text or "").strip()
    if not t:
        return False
    tail800 = t[-800:]
    tail200 = t[-200:]
    if _DISCLAIMER_RE.search(tail800):
        return False
    m = _TRAILING_Q_RE.search(t)
    if m:
        head = t[: m.start()]
        cut = max(head.rfind("\n"), head.rfind(". "),
                  head.rfind("! "), head.rfind("? "))
        sentence = (head[cut + 1:] if cut >= 0 else head).strip(" \t\n.!?")
        if len(sentence) >= 4:
            return True
    return bool(
        _NEEDS_INPUT_RE.search(tail800)
        or _AWAITING_RE.search(tail200)
        or _ASK_RE.search(tail200)
    )


def _tail_records(path: str, tail_bytes: int) -> tuple[list[dict], bool]:
    """(JSON objects from the file's tail in order, window-covers-file)."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        start = max(0, size - tail_bytes)
        fh.seek(start)
        chunk = fh.read().decode("utf-8", errors="ignore")
    lines = chunk.split("\n")
    if start > 0 and lines:
        lines = lines[1:]  # first line is a partial record
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out, start == 0


def _walk_tail(path: str, walk):
    """Run ``walk(records)`` over widening tail windows until it decides.

    ``walk`` returns ``(phase, text)``; a ``None`` phase means "nothing
    decisive in this window". The walk may simply have missed the decisive
    record because it sits beyond the window, so an indecisive result on a
    partial window retries with the next size up. A complete window (the
    whole file was read) is authoritative and stops the loop.
    """
    result = (None, "")
    for tail_bytes in _TAIL_WINDOWS:
        records, complete = _tail_records(path, tail_bytes)
        result = walk(records)
        if result[0] is not None or complete:
            break
    return result


def _blocks_text(blocks) -> str:
    return "\n".join(
        str(b.get("text") or "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _probe_claude(session: Session, ctx: dict):
    """(phase, last-assistant-text) from the transcript's decisive tail record."""
    root = os.path.expanduser("~/.claude/projects")
    hits = glob.glob(os.path.join(root, "*", session.session_id + ".jsonl"))
    if not hits:
        return None, ""

    def walk(records):
        for rec in reversed(records):
            kind = rec.get("type")
            if kind == "assistant":
                if rec.get("isApiErrorMessage"):
                    return "error", ""
                msg = rec.get("message") or {}
                content = msg.get("content")
                blocks = content if isinstance(content, list) else []
                if msg.get("stop_reason") == "tool_use" or any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in blocks
                ):
                    return "midturn", ""
                # A record with no text blocks and no tool_use (e.g. a
                # thinking-only record carrying end_turn) is still finished.
                return "finished", _blocks_text(blocks)
            if kind == "user":
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    if any(
                        isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in content
                    ):
                        return "midturn", ""
                    text = _blocks_text(content)
                else:
                    text = content if isinstance(content, str) else ""
                if "[Request interrupted by user" in text:
                    return "aborted", ""
                # Slash-command bookkeeping the model does not answer.
                if (
                    rec.get("isMeta")
                    or "<command-name>" in text
                    or "<local-command-" in text
                ):
                    continue
                return "midturn", ""  # a prompt awaiting the model
        return None, ""

    return _walk_tail(hits[0], walk)


def _probe_cursor(session: Session, ctx: dict):
    """(phase, text) from the terminal ``turn_ended`` record."""
    root = os.path.expanduser("~/.cursor/projects")
    sid = session.session_id
    hits = glob.glob(
        os.path.join(root, "*", "agent-transcripts", sid, sid + ".jsonl")
    )
    if not hits:
        return None, ""
    def walk(records):
        if not records:
            return None, ""
        last = records[-1]
        if last.get("type") != "turn_ended":
            # An assistant or user record with no turn_ended after it
            # means the turn never closed.
            if last.get("role") in ("user", "assistant"):
                return "midturn", ""
            return None, ""
        status = last.get("status")
        if status == "error":
            return "error", ""
        if status == "aborted":
            return "aborted", ""
        # success: walk back to the nearest assistant record that has
        # text, but no further than this turn's own boundary — a
        # tool_use-only turn does not inherit the previous turn's text.
        for rec in reversed(records[:-1]):
            if rec.get("type") == "turn_ended" or rec.get("role") == "user":
                break
            if rec.get("role") != "assistant":
                continue
            msg = rec.get("message") or {}
            blocks = msg.get("content")
            text = _blocks_text(blocks if isinstance(blocks, list) else [])
            if text:
                return "finished", text
        return "finished", ""

    return _walk_tail(hits[0], walk)


def _pid_alive(pid: int) -> bool:
    """True when ``pid`` still names a running process."""
    if pid < 1:
        # kill(0, 0) would signal this process's own group.
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours to signal
    except OSError:
        return False


def _devin_lock_alive(session_id: str) -> bool:
    """True when the session's ``session_locks`` pid is still running.

    ``devin acp`` writes ``~/.local/share/devin/cli/session_locks/<id>.lock``
    holding its decimal pid for as long as the session process runs.
    """
    path = os.path.join(
        os.path.expanduser("~/.local/share/devin/cli/session_locks"),
        session_id + ".lock",
    )
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except Exception:
        return False
    return _pid_alive(pid)


def _probe_devin(session: Session, ctx: dict):
    """(phase, text) from the session's main-chain head message."""
    conn = ctx.get("devin_conn")
    if conn is None:
        return None, ""
    sid = session.session_id
    row = conn.execute(
        "SELECT main_chain_id FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    head = row[0] if row else None
    chat = None
    if head is not None:
        r = conn.execute(
            "SELECT chat_message FROM message_nodes "
            "WHERE session_id = ? AND node_id = ?",
            (sid, head),
        ).fetchone()
        chat = r[0] if r else None
    if chat is None:
        r = conn.execute(
            "SELECT chat_message FROM message_nodes "
            "WHERE session_id = ? ORDER BY node_id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if not r:
            return None, ""
        chat = r[0]
    msg = json.loads(chat)
    role = msg.get("role")
    if role == "assistant":
        if msg.get("tool_calls"):
            return "midturn", ""
        content = msg.get("content")
        return "finished", content if isinstance(content, str) else ""
    if role in ("tool", "user"):
        return "midturn", ""
    return None, ""


# Rollout files sit at ``~/.codex/sessions/Y/M/D/rollout-<iso>-<uuid>.jsonl``
# and a codex ``Session.session_id`` is that file's stem, so the date in
# the stem reconstructs the path directly; the glob is only a fallback for
# a file that was moved.
_CODEX_ROLLOUT_DATE_RE = re.compile(r"rollout-(\d{4})-(\d{2})-(\d{2})T")


def _codex_rollout_path(session_id: str) -> str:
    root = os.path.expanduser("~/.codex/sessions")
    match = _CODEX_ROLLOUT_DATE_RE.match(session_id or "")
    if match:
        path = os.path.join(
            root, match.group(1), match.group(2), match.group(3),
            session_id + ".jsonl",
        )
        if os.path.exists(path):
            return path
    hits = glob.glob(os.path.join(root, "*", "*", "*", session_id + ".jsonl"))
    return hits[0] if hits else ""


def _codex_message_text(payload: dict) -> str:
    blocks = payload.get("content")
    return "\n".join(
        str(b.get("text") or "")
        for b in (blocks if isinstance(blocks, list) else [])
        if isinstance(b, dict) and b.get("type") == "output_text"
    )


def _probe_codex(session: Session, ctx: dict):
    """(phase, text) from the rollout tail's decisive ``event_msg``.

    ``task_complete`` closes a turn (its ``last_agent_message`` is the
    final assistant text), ``turn_aborted`` marks an interrupt and
    ``task_started`` opens one; every other event_msg is bookkeeping.
    A window with no decisive event falls back to the last response_item.
    Codex records no error signal in the rollout.
    """
    path = _codex_rollout_path(session.session_id)
    if not path:
        return None, ""

    def walk(records):
        for rec in reversed(records):
            if rec.get("type") != "event_msg":
                continue
            payload = rec.get("payload") or {}
            kind = payload.get("type")
            if kind == "task_complete":
                text = payload.get("last_agent_message")
                return "finished", text if isinstance(text, str) else ""
            if kind == "turn_aborted":
                return "aborted", ""
            if kind == "task_started":
                return "midturn", ""
            # token_count, item_completed, agent_message and friends are
            # bookkeeping that says nothing about where the turn stands.
        for rec in reversed(records):
            if rec.get("type") != "response_item":
                continue
            payload = rec.get("payload") or {}
            kind = payload.get("type")
            if kind == "message":
                role = payload.get("role")
                if role == "assistant":
                    return "finished", _codex_message_text(payload)
                if role in ("user", "developer"):
                    return "midturn", ""
                continue
            if kind in (
                "function_call",
                "custom_tool_call",
                # A tool output as the last item means the tool answered
                # and the model has not replied yet: still mid-turn.
                "function_call_output",
                "custom_tool_call_output",
            ):
                return "midturn", ""
            # Reasoning and other state-free items are skipped, not
            # decisive; an earlier message or call may still classify.
        return None, ""

    return _walk_tail(path, walk)


def _probe_opencode(session: Session, ctx: dict):
    """(phase, text) from the session's last ``message`` row.

    The drizzle schema keeps the payload in a JSON ``data`` column: a
    ``user`` head means the model has not replied, an assistant ``finish``
    of ``tool-calls`` (or none at all) is a turn still running, ``error``
    is a failure and any other value closes the turn. The reply text
    lives in the message's ``part`` rows, newest first.
    """
    conn = ctx.get("opencode_conn")
    if conn is None:
        return None, ""
    sid = session.session_id
    row = conn.execute(
        "SELECT id, data FROM message WHERE session_id = ? "
        "ORDER BY time_created DESC, id DESC LIMIT 1",
        (sid,),
    ).fetchone()
    if not row:
        return None, ""
    msg = json.loads(row[1])
    role = msg.get("role")
    if role == "user":
        return "midturn", ""
    if role != "assistant":
        return None, ""
    finish = msg.get("finish")
    if finish == "error":
        return "error", ""
    if not finish or finish == "tool-calls":
        return "midturn", ""
    # "stop" or any other non-empty finish: the turn closed. Only this
    # message's own parts may carry its final text.
    text = ""
    for (pdata,) in conn.execute(
        "SELECT data FROM part WHERE message_id = ? AND session_id = ? "
        "ORDER BY time_created DESC LIMIT 8",
        (row[0], sid),
    ):
        try:
            part = json.loads(pdata)
        except Exception:
            continue
        if part.get("type") == "text" and part.get("text"):
            text = part["text"]
            break
    return "finished", text


def _probe_kilo(session: Session, ctx: dict):
    """Kilo is an opencode fork: same drizzle schema, different db path."""
    return _probe_opencode(session, {"opencode_conn": ctx.get("kilo_conn")})


# Cline 3.x session metadata (``data/sessions/<id>/<id>.json``) carries a
# ``status`` lifecycle field. ``waiting`` means blocked on user input, so
# it joins the finished phases: the last assistant text decides between
# followup and done. ``paused`` is a user-initiated stop, closer to an
# interrupt than to a finished turn.
_CLINE_PHASES = {
    "running": "midturn",
    "streaming": "midturn",
    "waiting": "finished",
    "idle": "finished",
    "completed": "finished",
    "failed": "error",
    "paused": "aborted",
    "canceled": "aborted",
    "cancelled": "aborted",
}


def _cline_meta_path(session_id: str) -> str:
    return os.path.join(
        os.path.expanduser("~/.cline/data/sessions"),
        session_id,
        session_id + ".json",
    )


def _cline_pid_alive(session_id: str) -> bool:
    """True when the session metadata's ``pid`` still names a process.

    ``started_at`` never moves past session start, so the timestamp
    window alone would call every turn older than two minutes dead;
    the recorded pid is the real liveness signal.
    """
    try:
        with open(_cline_meta_path(session_id), encoding="utf-8") as fh:
            pid = json.load(fh).get("pid")
    except Exception:
        return False
    return isinstance(pid, int) and _pid_alive(pid)


def _cline_last_assistant_text(session_id: str) -> str:
    path = os.path.join(
        os.path.expanduser("~/.cline/data/sessions"),
        session_id,
        session_id + ".messages.json",
    )
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return ""
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        text = _blocks_text(msg.get("content") or [])
        if text:
            return text
    return ""


def _probe_cline(session: Session, ctx: dict):
    """(phase, text) from the session metadata's ``status`` field.

    Cline 3.x stamps ``running``/``streaming`` mid-turn, ``waiting`` when
    blocked on user input, ``idle``/``completed`` on a closed turn and
    ``failed``/``canceled``/``paused`` on terminal stops. Closed and
    blocked phases tail the messages file for the final assistant text
    so ``needs_followup`` can tell a question from a sign-off. Legacy
    ``taskHistory.json`` sessions have no metadata file and stay unknown.
    """
    try:
        with open(
            _cline_meta_path(session.session_id), encoding="utf-8"
        ) as fh:
            meta = json.load(fh)
    except Exception:
        return None, ""
    if not isinstance(meta, dict):
        return None, ""
    phase = _CLINE_PHASES.get(meta.get("status"))
    if phase is None:
        return None, ""
    if phase == "finished":
        return "finished", _cline_last_assistant_text(session.session_id)
    return phase, ""


def _probe_pi(session: Session, ctx: dict):
    """(phase, text) from the transcript's trailing ``message`` record.

    A pi ``Session.session_id`` is the transcript's full path. Message
    records carry ``message.role`` and a ``content`` block list; every
    other record type (``session``, ``session_info``, ``model_change``,
    ``thinking_level_change``, ...) is bookkeeping and says nothing about
    where the turn stands. Pi appends an empty-content assistant record
    when a reply starts, so one at the tail means the reply never landed.
    There is no error or abort signal.
    """
    path = session.session_id
    if not path or not os.path.isfile(path):
        return None, ""

    def walk(records):
        for rec in reversed(records):
            if rec.get("type") != "message":
                continue
            msg = rec.get("message") or {}
            role = msg.get("role")
            if role == "assistant":
                content = msg.get("content")
                blocks = content if isinstance(content, list) else []
                if any(
                    isinstance(b, dict) and b.get("type") == "toolCall"
                    for b in blocks
                ):
                    return "midturn", ""
                texts = [
                    str(b.get("text") or "")
                    for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    return "finished", "\n".join(texts)
                # Empty-content placeholder: the reply never landed.
                return "midturn", ""
            if role in ("toolResult", "user"):
                return "midturn", ""
            # Other roles carry no turn state; keep walking.
        return None, ""

    return _walk_tail(path, walk)


_PROBES = {
    "claude": _probe_claude,
    "cline": _probe_cline,
    "codex": _probe_codex,
    "cursor": _probe_cursor,
    "devin": _probe_devin,
    "opencode": _probe_opencode,
    "kilo": _probe_kilo,
    "pi": _probe_pi,
}


def _claude_live_session_ids() -> set:
    """Session ids whose owning process is still alive."""
    out = set()
    root = os.path.expanduser("~/.claude/sessions")
    for path in glob.glob(os.path.join(root, "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        sid, pid = data.get("sessionId"), data.get("pid")
        if not sid or not isinstance(pid, int):
            continue
        if _pid_alive(pid):
            out.add(sid)
    return out


def _claude_job_states() -> dict:
    """sessionId -> bg-job state from ``~/.claude/jobs/*/state.json``."""
    out = {}
    root = os.path.expanduser("~/.claude/jobs")
    for path in glob.glob(os.path.join(root, "*", "state.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        sid, state = data.get("sessionId"), data.get("state")
        if sid and state in ("working", "blocked", "done", "failed"):
            out[sid] = state
    return out


def _open_devin_db() -> sqlite3.Connection | None:
    path = os.path.expanduser("~/.local/share/devin/cli/sessions.db")
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return None


def _open_opencode_db() -> sqlite3.Connection | None:
    path = os.path.expanduser("~/.local/share/opencode/opencode.db")
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return None


def _open_kilo_db() -> sqlite3.Connection | None:
    path = os.path.expanduser("~/.local/share/kilo/kilo.db")
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        return None


def session_statuses(sessions: list[Session], now: float | None = None) -> list[str]:
    """One status label per session, same order as the input."""
    if now is None:
        now = time.time()
    # The per-call snapshots every claude row shares: the live-pid
    # registry and the bg-job table. The Devin and OpenCode DBs likewise
    # open once.
    ctx = {
        "claude_live": _claude_live_session_ids(),
        "claude_jobs": _claude_job_states(),
        "devin_conn": _open_devin_db()
        if any(s.tool_name == "devin" for s in sessions)
        else None,
        "opencode_conn": _open_opencode_db()
        if any(s.tool_name == "opencode" for s in sessions)
        else None,
        "kilo_conn": _open_kilo_db()
        if any(s.tool_name == "kilo" for s in sessions)
        else None,
    }
    try:
        out = []
        for session in sessions:
            out.append(_one_status(session, now, ctx))
        return out
    finally:
        for key in ("devin_conn", "opencode_conn", "kilo_conn"):
            conn = ctx.get(key)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def _one_status(session: Session, now: float, ctx: dict) -> str:
    probe = _PROBES.get(session.tool_name)
    if probe is None:
        return UNKNOWN
    try:
        phase, text = probe(session, ctx)
    except Exception:
        return UNKNOWN
    if phase == "error":
        return ERROR
    if phase == "aborted":
        return INTERRUPTED
    if phase == "midturn":
        alive = now - session.timestamp / 1000 < ACTIVE_WINDOW_S
        if session.tool_name == "claude":
            alive = alive or session.session_id in ctx["claude_live"]
        elif session.tool_name == "devin":
            alive = alive or _devin_lock_alive(session.session_id)
        elif session.tool_name == "cline":
            alive = alive or _cline_pid_alive(session.session_id)
        return ACTIVE if alive else INTERRUPTED
    if phase == "finished":
        if session.tool_name == "claude":
            state = ctx["claude_jobs"].get(session.session_id)
            if state == "blocked":
                return FOLLOWUP
            if state == "failed":
                return ERROR
        return FOLLOWUP if needs_followup(text) else DONE
    return UNKNOWN


def session_status(session: Session, now: float | None = None) -> str:
    """The status label for a single session."""
    return session_statuses([session], now=now)[0]
