"""Per-harness session parsers (thin adapters over family engines)."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from urllib.parse import unquote

from quiver.sessions.engines import (
    JsonParserConfig,
    JsonlParserConfig,
    SqliteParserConfig,
    clean_title,
    extract_user_text,
    get_mtime,
    parse_iso_ts,
    parse_json_store,
    parse_jsonl_projects,
    parse_sqlite,
    path_from_encoded_dir,
    strip_file_uri,
)
from quiver.sessions.engines.jsonl_engine import event_cwd
from quiver.sessions import failures
from quiver.sessions.models import Session


# ---------------------------------------------------------------------------
# SQLite family
# ---------------------------------------------------------------------------

def parse_opencode():
    return parse_sqlite(
        SqliteParserConfig(
            tool_name="opencode",
            agent="OpenCode",
            db_path=os.path.expanduser("~/.local/share/opencode/opencode.db"),
            query="""
                SELECT s.time_updated, s.title,
                       COALESCE(NULLIF(s.directory, ''), w.directory), s.id
                FROM session s
                LEFT JOIN workspace w ON s.workspace_id = w.id
            """,
            updated=0,
            title=1,
            path=2,
            session_id=3,
            require_path=True,
        )
    )


_COPILOT_USER_NAMED_RE = re.compile(r"^user_named:\s*(true|false)\s*$", re.IGNORECASE)
_COPILOT_NAME_RE = re.compile(r"^name:\s*(.*)$")
_COPILOT_BLOCK_SCALAR_RE = re.compile(r"^[|>][-+]?\d*$")
_COPILOT_YAML_MAX_LINES = 512


def _copilot_workspace_meta(state_root: str, sid: str) -> tuple[bool, str]:
    """Read ``user_named`` and ``name`` from a Copilot session's workspace.yaml.

    Stdlib only: scans top-level lines for the two keys and stops as soon as
    both are found. Missing file, missing key, or unreadable content all mean
    ``(False, "")``. A ``name: |-`` block scalar is folded onto one line.
    """
    if not sid or not state_root:
        return False, ""
    path = os.path.join(state_root, sid, "workspace.yaml")
    user_named: bool | None = None
    name: str | None = None
    block: list[str] | None = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh):
                if lineno >= _COPILOT_YAML_MAX_LINES:
                    break
                if block is not None:
                    if not line.strip() or line[0] in " \t":
                        block.append(line.strip())
                        continue
                    name = " ".join(part for part in block if part)
                    block = None
                if user_named is None:
                    m = _COPILOT_USER_NAMED_RE.match(line)
                    if m:
                        user_named = m.group(1).lower() == "true"
                        if name is not None:
                            break
                        continue
                if name is None:
                    m = _COPILOT_NAME_RE.match(line)
                    if m:
                        value = m.group(1).strip()
                        if _COPILOT_BLOCK_SCALAR_RE.match(value):
                            block = []
                            continue
                        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                            value = value[1:-1]
                        name = value
                        if user_named is not None:
                            break
        if block is not None and name is None:
            name = " ".join(part for part in block if part)
    except Exception:
        return False, ""
    return bool(user_named), name or ""


def _copilot_fallback_title(conn, sid: str, fields: dict) -> None:
    """Fill ``fields["title"]`` from the first turn, then the first checkpoint."""
    try:
        r = conn.execute(
            """
            SELECT user_message FROM turns
            WHERE session_id = ? AND user_message IS NOT NULL AND user_message != ''
            ORDER BY turn_index ASC LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if r and r[0]:
            fields["title"] = clean_title(r[0])
            return
        r = conn.execute(
            """
            SELECT title FROM checkpoints
            WHERE session_id = ?
            ORDER BY checkpoint_number ASC LIMIT 1
            """,
            (sid,),
        ).fetchone()
        if r and r[0]:
            fields["title"] = clean_title(r[0])
    except Exception:
        pass


def parse_copilot():
    state_root = os.path.expanduser("~/.copilot/session-state")

    def enrich(conn, row, fields):
        sid = fields.get("session_id") or ""
        from_summary = bool(fields.get("title"))
        if not from_summary:
            _copilot_fallback_title(conn, sid, fields)
        try:
            user_named, yaml_name = _copilot_workspace_meta(state_root, sid)
        except Exception:
            user_named, yaml_name = False, ""
        if user_named:
            if not from_summary and yaml_name:
                fields["title"] = clean_title(yaml_name)
            if fields.get("title"):
                fields["title_source"] = "rename"
                return
        if from_summary:
            fields["title_source"] = "auto"

    return parse_sqlite(
        SqliteParserConfig(
            tool_name="copilot",
            agent="GitHub Copilot",
            db_path=os.path.expanduser("~/.copilot/session-store.db"),
            query="SELECT id, cwd, summary, updated_at, created_at FROM sessions",
            session_id=0,
            path=1,
            title=2,
            updated=3,
            created=4,
            require_path=True,
            enrich=enrich,
        )
    )


_DEVIN_DB = "~/.local/share/devin/cli/sessions.db"
# ``/rename <name>`` and ``/rename-chat <name>`` both rename; with no
# argument the name is typed into a prompt that ``prompt_history`` never
# sees, so only an inline name can be matched against the title.
_DEVIN_RENAME_RE = re.compile(r"^/rename(?:-chat)?(?:\s+(.*?))?\s*$", re.S)


def parse_devin():
    """Devin CLI sessions from ``~/.local/share/devin/cli/sessions.db``.

    ``sessions.title`` is filled either by Devin's auto-titler or by the
    user's ``/rename``; both write the same column. The only on-disk
    discriminator is ``prompt_history``, which records every prompt the
    user typed, ``/rename <name>`` included. A session whose latest
    inline ``/rename`` argument matches the title is a rename; a title
    that differs from the first prompt without one is the auto-titler's;
    a title equal to the first prompt (Devin's default) has no provenance.
    A rename typed into the interactive prompt leaves no argument on disk
    and so reads as auto, which under-marks rather than over-marks.

    ``prompt_history`` also holds prompts typed at the REPL before the
    session existed (``/usage``, ``/login-status``); only rows stamped at
    or after ``created_at`` belong to the session.
    """

    def enrich(conn, row, fields):
        sid = fields.get("session_id")
        if not sid:
            return
        created_at = row[4] if len(row) > 4 else None
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
            created_at = 0
        prompts = conn.execute(
            "SELECT content FROM prompt_history "
            "WHERE session_id = ? AND is_shell = 0 AND timestamp >= ? "
            "ORDER BY timestamp, id",
            (sid, created_at),
        ).fetchall()
        first_prompt = ""
        renamed_to = ""
        for (content,) in prompts:
            text = str(content or "")
            match = _DEVIN_RENAME_RE.match(text)
            if match:
                renamed_to = clean_title(match.group(1) or "")
                continue
            if not first_prompt and text.strip():
                first_prompt = clean_title(text)
        title = fields.get("title") or ""
        if not title and first_prompt:
            fields["title"] = title = first_prompt
        if not title:
            return
        if renamed_to and renamed_to == title:
            fields["title_source"] = "rename"
        elif first_prompt and title != first_prompt:
            fields["title_source"] = "auto"

    return parse_sqlite(
        SqliteParserConfig(
            tool_name="devin",
            agent="Devin",
            db_path=os.path.expanduser(_DEVIN_DB),
            query="""
                SELECT id, working_directory, title, last_activity_at, created_at
                FROM sessions
                WHERE hidden = 0
            """,
            session_id=0,
            path=1,
            title=2,
            updated=3,
            created=4,
            require_path=True,
            enrich=enrich,
        )
    )


def parse_forge():
    def enrich(conn, row, fields):
        if fields.get("title"):
            return
        context = row[4] if len(row) > 4 else None
        if not context:
            return
        try:
            ctx = json.loads(context)
        except Exception:
            return
        for msg in ctx.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            inner = msg.get("message") if isinstance(msg.get("message"), dict) else msg
            text = inner.get("text") if isinstance(inner, dict) else None
            if isinstance(text, dict) and str(text.get("role", "")).lower() == "user":
                content = text.get("content") or ""
                m = re.search(r"<task>\s*(.*?)\s*</task>", content, re.S | re.I)
                fields["title"] = clean_title(m.group(1) if m else content)
                break

    return parse_sqlite(
        SqliteParserConfig(
            tool_name="forge",
            agent="Forge",
            db_path=os.path.expanduser("~/.forge/.forge.db"),
            query=(
                "SELECT conversation_id, title, created_at, updated_at, context "
                "FROM conversations"
            ),
            session_id=0,
            title=1,
            created=2,
            updated=3,
            require_path=False,
            default_path=os.path.expanduser("~"),
            enrich=enrich,
        )
    )


def parse_mimo():
    def enrich(conn, row, fields):
        if fields.get("title"):
            return
        sid = fields.get("session_id") or ""
        try:
            parts = conn.execute(
                """
                SELECT data FROM part
                WHERE session_id = ? AND data LIKE '%"type":"text"%'
                ORDER BY time_created ASC LIMIT 5
                """,
                (sid,),
            ).fetchall()
            for (raw,) in parts:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if data.get("type") == "text" and data.get("text"):
                    fields["title"] = clean_title(data["text"])
                    break
        except Exception:
            pass

    return parse_sqlite(
        SqliteParserConfig(
            tool_name="mimo",
            agent="Mimo",
            db_path=os.path.expanduser("~/.local/share/mimocode/mimocode.db"),
            query="""
                SELECT s.id, s.directory, s.title, s.time_created, s.time_updated,
                       p.worktree
                FROM session s
                LEFT JOIN project p ON s.project_id = p.id
            """,
            session_id=0,
            path=1,
            title=2,
            created=3,
            updated=4,
            path_fallback=5,
            require_path=True,
            enrich=enrich,
        )
    )


def parse_crush():
    """Crush: projects.json index + per-project crush.db (sqlite family + index)."""
    sessions: list[Session] = []
    projects_json = os.path.expanduser("~/.local/share/crush/projects.json")
    if not os.path.exists(projects_json):
        return sessions
    try:
        with open(projects_json) as f:
            data = json.load(f)
        projects = data.get("projects") if isinstance(data, dict) else data
        if not isinstance(projects, list):
            return sessions
        for proj in projects:
            if not isinstance(proj, dict):
                continue
            path = proj.get("path") or ""
            data_dir = proj.get("data_dir") or ""
            if not path or not data_dir:
                continue
            db_path = os.path.join(os.path.expanduser(data_dir), "crush.db")
            if not os.path.exists(db_path):
                ts = parse_iso_ts(proj.get("last_accessed"))
                if ts:
                    sessions.append(
                        Session(
                            timestamp=ts,
                            agent="Crush",
                            path=path,
                            title="",
                            session_id="",
                            tool_name="crush",
                        )
                    )
                continue

            def enrich_path(conn, row, fields, _path=path):
                fields["path"] = _path

            sessions.extend(
                parse_sqlite(
                    SqliteParserConfig(
                        tool_name="crush",
                        agent="Crush",
                        db_path=db_path,
                        query="SELECT id, title, updated_at, created_at FROM sessions",
                        session_id=0,
                        title=1,
                        updated=2,
                        created=3,
                        require_path=False,
                        default_path=path,
                        enrich=enrich_path,
                    )
                )
            )
    except Exception:
        pass
    return sessions


# ---------------------------------------------------------------------------
# JSONL family
# ---------------------------------------------------------------------------

# A transcript that ended by handing off has the marker as its last record,
# so a small tail is enough to find one. That is the whole test: a marker
# sitting behind more conversation is not in the tail to begin with.
_CLAUDE_HANDOFF_TAIL_BYTES = 8192


def _claude_transcript_paths(base: str) -> dict[str, str]:
    """Session id -> transcript path, across every project dir under ``base``.

    A ``Session`` carries the id and the cwd but not the file it came from,
    and the project dir name is a lossy encoding of that cwd, so the mapping
    is read off the directory rather than reconstructed. Two scandirs and no
    file reads.
    """
    out: dict[str, str] = {}
    try:
        with os.scandir(base) as projects:
            for project in projects:
                if not project.is_dir() or not project.name.startswith("-"):
                    continue
                with os.scandir(project.path) as entries:
                    for entry in entries:
                        if entry.name.endswith(".jsonl"):
                            out[entry.name.removesuffix(".jsonl")] = entry.path
    except Exception:
        return out
    return out


def _claude_handed_off(path: str) -> str:
    """The session ``path`` handed off to and then stopped, or "".

    Claude Code can continue a session in a fresh transcript, and writes
    ``{"type": "continued-in", "continuedInSessionId": ...}`` into the old
    file the moment it happens. That marker alone does not end the old
    session: work often carries on there afterwards, and both files are then
    real, separate sessions. One machine here has both shapes, a handoff
    with nothing after it and a handoff followed by 860 more turns.

    So a session is superseded only when no user or assistant record follows
    the marker. Records the harness writes for its own bookkeeping do not
    count; only conversation does.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            start = max(0, size - _CLAUDE_HANDOFF_TAIL_BYTES)
            fh.seek(start)
            chunk = fh.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    lines = chunk.split("\n")
    if start > 0 and lines:
        lines = lines[1:]  # first line is a partial record
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        kind = data.get("type")
        if kind in ("user", "assistant"):
            return ""  # the conversation outlived the handoff
        if kind == "continued-in":
            successor = data.get("continuedInSessionId")
            return successor if isinstance(successor, str) and successor else ""
    return ""


def parse_claude():
    def path_from_event(data: dict) -> str:
        # cwd often only appears as raw field in early lines
        return event_cwd(data, "cwd")

    def title_from_event(data: dict) -> str:
        if data.get("type") != "user":
            return ""
        msg = data.get("message") or {}
        return extract_user_text(msg.get("content"))

    # `/rename` appends {"type": "custom-title", "customTitle": ...} and Claude
    # Code's own auto-title appends {"type": "ai-title", "aiTitle": ...}, both
    # after the first prompt and re-emitted on later turns. A rename beats the
    # auto-title, which beats the first prompt.
    def tail_title_from_event(data: dict) -> tuple[int, str, str] | None:
        kind = data.get("type")
        if kind == "custom-title":
            return (2, str(data.get("customTitle") or ""), "rename")
        if kind == "ai-title":
            return (1, str(data.get("aiTitle") or ""), "auto")
        return None

    base = os.path.expanduser("~/.claude/projects/")
    sessions = parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="claude",
            agent="Claude Code",
            base_dir=base,
            mode="nested_jsonl",
            project_filter=lambda name, _p: name.startswith("-"),
            path_from_event=path_from_event,
            path_from_project_dir=lambda name: path_from_encoded_dir(name, "-"),
            title_from_event=title_from_event,
            tail_title_from_event=tail_title_from_event,
            title_max_len=50,
            # One row per jsonl so multi-chat projects all appear in swe session
            one_session_per_file=True,
            require_path=True,
        )
    )

    # One conversation continued across two transcripts is one session, and
    # both files carry the same title, so listing both reads as a duplicate.
    # The predecessor is dropped only when its successor is really on disk;
    # otherwise the whole conversation would vanish from the listing.
    by_id = {sess.session_id: sess for sess in sessions if sess.session_id}
    superseded: set[str] = set()
    for session_id, file_path in _claude_transcript_paths(base).items():
        if session_id not in by_id:
            continue
        successor = _claude_handed_off(file_path)
        if successor and successor in by_id:
            superseded.add(session_id)
    if not superseded:
        return sessions
    return [s for s in sessions if s.session_id not in superseded]


def parse_droid():
    def path_from_event(data: dict) -> str:
        if data.get("type") == "session_start":
            return data.get("cwd") or ""
        return ""

    # Line 1 is a ``session_start`` event rewritten in place. ``/rename``
    # sets ``isSessionTitleManuallySet: true``; otherwise droid's auto-titler
    # fills ``title`` after the first message or file edit. Older files only
    # carry the "New Session" placeholder, which falls through to the first
    # user prompt.
    def title_from_event(data: dict) -> str | tuple[str, str]:
        if data.get("type") == "session_start":
            start_title = str(data.get("title") or "").strip()
            if start_title and start_title.lower() != "new session":
                if data.get("isSessionTitleManuallySet") is True:
                    return (start_title, "rename")
                return (start_title, "auto")
            return ""
        if data.get("type") != "message":
            return ""
        msg = data.get("message") or {}
        if msg.get("role") != "user":
            return ""
        if msg.get("hookEventName") or msg.get("visibility") == "user_only":
            text = extract_user_text(msg.get("content"))
            if not text.strip():
                return ""
        return extract_user_text(msg.get("content"))

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="droid",
            agent="Droid",
            base_dir=os.path.expanduser("~/.factory/sessions/"),
            mode="nested_jsonl",
            path_from_event=path_from_event,
            path_from_project_dir=lambda name: path_from_encoded_dir(name, "-"),
            title_from_event=title_from_event,
            one_session_per_file=True,
            require_path=True,
        )
    )


# Rollout files are named ``rollout-<iso timestamp>-<thread uuid>.jsonl``, and
# the uuid is the id the session index and ``codex resume`` both use.
_CODEX_ROLLOUT_ID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def codex_thread_id(session_id: str) -> str:
    """The thread uuid inside a codex session id, lower-cased, or "".

    A codex ``Session.session_id`` is the rollout file's stem, because that
    is what the transcript readers look a session up by. Everything else
    codex exposes — the session index, the thread store, ``codex resume`` —
    is keyed by the uuid at the end of it.
    """
    match = _CODEX_ROLLOUT_ID_RE.search(session_id or "")
    return match.group(1).lower() if match else ""


# Codex's auto-titler runs the moment the first turn completes: across every
# thread on disk it landed 4-7 seconds after the thread's first name record,
# while every observed ``/name`` came 7 minutes to 2 hours later. A minute is
# well clear of both clusters.
_CODEX_AUTO_TITLE_WINDOW_MS = 60_000


def _codex_state_db() -> str:
    """Newest ``~/.codex/state_<n>.sqlite``, or "" when codex has none.

    The number in the filename is a schema version codex bumps on migration
    and the old file is left behind, so the highest one is the live store.
    """
    best, best_n = "", -1
    for path in glob.glob(os.path.expanduser("~/.codex/state_*.sqlite")):
        match = re.search(r"state_(\d+)\.sqlite$", path)
        if not match:
            continue
        version = int(match.group(1))
        if version > best_n:
            best, best_n = path, version
    return best


def _codex_thread_store() -> dict[str, tuple[str, str]]:
    """Thread id -> ``(name, preview)`` from codex's own thread store.

    ``name`` is the thread's display name, whatever wrote it. ``preview`` is
    codex's record of the first message the *user* actually sent, which beats
    scanning the rollout for one: a transcript's first user-role item is
    often injected context (a plugin roster, an AGENTS.md) that the user
    never typed, and codex knows which was which.

    An empty ``preview`` means codex considers the thread empty, and its own
    listing hides those (there are partial indexes named
    ``idx_threads_visible_*`` filtering on ``preview <> ''``). They are
    threads opened and never typed into, so there is no title to salvage.

    An unreadable or absent store yields an empty map, which turns both the
    naming and the hiding back off.
    """
    from quiver.sessions.engines.common import open_sqlite_ro

    db_path = _codex_state_db()
    if not db_path:
        return {}
    conn = open_sqlite_ro(db_path)
    if conn is None:
        return {}
    out: dict[str, tuple[str, str]] = {}
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(threads)")}
        if not {"id", "name", "preview"} <= cols:
            return {}
        for tid, name, preview in conn.execute(
            "SELECT id, name, COALESCE(NULLIF(preview, ''), first_user_message) "
            "FROM threads"
            if "first_user_message" in cols
            else "SELECT id, name, preview FROM threads"
        ):
            if not isinstance(tid, str) or not tid.strip():
                continue
            out[tid.strip().lower()] = (
                name.strip() if isinstance(name, str) else "",
                preview.strip() if isinstance(preview, str) else "",
            )
    except Exception:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _codex_index_history() -> dict[str, tuple[list[str], float]]:
    """Thread id -> ``(names, span_ms)`` from ``~/.codex/session_index.jsonl``.

    Codex keeps thread names outside the rollout transcript and appends
    ``{"id", "thread_name", "updated_at"}`` here every time one changes, so
    the last record for an id is the name its TUI shows and the ones before
    it are the names it replaced.

    ``names`` drops consecutive duplicates, because codex re-emits the
    current name on events that did not rename anything, and ``span_ms`` is
    the wall time from a thread's first record to its last. Ids are
    lower-cased so a lookup keyed off the rollout filename matches whatever
    case either side wrote.
    """
    path = os.path.expanduser("~/.codex/session_index.jsonl")
    names: dict[str, list[str]] = {}
    span: dict[str, list[float]] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                sid = entry.get("id")
                name = entry.get("thread_name")
                if not isinstance(sid, str) or not sid.strip():
                    continue
                if not isinstance(name, str) or not name.strip():
                    continue
                sid = sid.strip().lower()
                name = name.strip()
                seq = names.setdefault(sid, [])
                if not seq or seq[-1] != name:
                    seq.append(name)
                ends = span.setdefault(sid, [])
                ts = parse_iso_ts(entry.get("updated_at")) or 0.0
                if not ends:
                    ends.append(ts)
                elif len(ends) == 1:
                    ends.append(ts)
                else:
                    ends[1] = ts
    except Exception:
        return {}
    return {
        sid: (seq, (span[sid][-1] - span[sid][0]) if sid in span else 0.0)
        for sid, seq in names.items()
    }


def _codex_is_rename(names: list[str], span_ms: float, first_prompt: str) -> bool:
    """Whether a codex thread's current name was typed rather than generated.

    Three writers share the one ``thread_name`` field with no marker: codex
    stamps a prefix of the first prompt when the turn starts, replaces it
    with a generated title seconds later, and ``/name`` overwrites it
    whenever the user asks. What separates them is that codex only ever
    writes those two, in that order, and does so immediately:

    - A sequence longer than codex's own two entries has to end in a rename.
      When the first entry is not a prefix of the first prompt, codex skipped
      the placeholder and its budget is one entry, not two.
    - Codex's generated title lands within seconds of the placeholder. A last
      record a minute or more after the first one outlived that window, so a
      user wrote it.

    Both clauses are timing and shape arguments rather than a flag codex
    records, so this under-marks rather than over-marks: a rename that leaves
    a single record inside the window reads as a generated title. Across the
    threads this was calibrated on it marked every rename it claimed and
    missed none that had a second record.
    """
    if not names:
        return False
    prompt = (first_prompt or "").strip().lower()
    placeholder = bool(prompt) and prompt.startswith(names[0].strip().lower())
    if len(names) > (2 if placeholder else 1):
        return True
    return span_ms > _CODEX_AUTO_TITLE_WINDOW_MS


def parse_codex():
    def path_from_event(data: dict) -> str:
        if data.get("type") == "session_meta":
            payload = data.get("payload") or {}
            return payload.get("cwd") or ""
        return ""

    def title_from_event(data: dict) -> str:
        if data.get("type") != "response_item":
            return ""
        payload = data.get("payload") or {}
        if payload.get("type") != "message" or payload.get("role") != "user":
            return ""
        return extract_user_text(payload.get("content"))

    sessions = parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="codex",
            agent="Codex CLI",
            base_dir=os.path.expanduser("~/.codex/sessions/"),
            mode="glob",
            session_glob="*/*/*/*.jsonl",
            path_from_event=path_from_event,
            title_from_event=title_from_event,
            title_max_len=80,
            require_path=True,
        )
    )

    # A codex thread's name lives outside its rollout, in two places that say
    # different halves of the truth: the store holds the current name and
    # whether the thread was ever used, the index holds the history that says
    # whether a name was typed or generated. Either being unreadable degrades
    # to what the other knows, and a thread neither has heard of keeps the
    # first-prompt title the transcript scan produced.
    store = _codex_thread_store()
    history = _codex_index_history()
    if not store and not history:
        return sessions

    kept: list[Session] = []
    for sess in sessions:
        tid = codex_thread_id(sess.session_id)
        stored_name, preview = store.get(tid, ("", ""))
        # Only the store can say a thread was never used, so a thread it has
        # never heard of is kept rather than guessed at.
        if tid in store and not preview:
            continue
        names, span_ms = history.get(tid, ([], 0.0))
        # The store holds the live name; the index is the audit trail behind
        # it and stands in when the store has none. With no name at all,
        # codex's own note of the first user message still beats the
        # transcript scan, which cannot tell a typed prompt from injected
        # context. That fallback is nobody's chosen title, so it stays
        # unmarked however the name above it was written.
        title = clean_title(stored_name or (names[-1] if names else ""), 80)
        if title:
            sess.title = title
            if _codex_is_rename(names, span_ms, preview):
                sess.title_source = "rename"
        elif preview:
            fallback = clean_title(preview, 80)
            if fallback:
                sess.title = fallback
        kept.append(sess)
    return kept


def parse_pi():
    def path_from_event(data: dict) -> str:
        if data.get("type") == "session":
            return data.get("cwd") or ""
        return event_cwd(data, "cwd", "workspace")

    def title_from_event(data: dict) -> str:
        if data.get("type") != "message":
            return ""
        msg = data.get("message") or {}
        if msg.get("role") != "user":
            return ""
        return extract_user_text(msg.get("content"))

    def project_filter(name: str, _path: str) -> bool:
        return name.startswith("--") and name.endswith("--")

    def path_from_project_dir(name: str) -> str:
        inner = name[2:-2] if name.startswith("--") and name.endswith("--") else name
        if inner.startswith("-"):
            inner = inner[1:]
        return "/" + inner.replace("-", "/")

    # `/name <name>` (and `pi --name`) appends {"type": "session_info",
    # "name": ...} after the first prompt; it can be repeated and pi's own
    # reader takes the latest one. Pi has no auto-titler, so every
    # session_info name is user-set. Limitation: pi clears a name with
    # {"name": ""}, but the engine drops empty text, so a clear that follows
    # an earlier name still surfaces that earlier name as a rename.
    def tail_title_from_event(data: dict) -> tuple[int, str, str] | None:
        if data.get("type") != "session_info":
            return None
        name = data.get("name")
        if not isinstance(name, str):
            return None
        name = name.strip()
        if not name:
            return None
        return (2, name, "rename")

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="pi",
            agent="Pi CLI",
            base_dir=os.path.expanduser("~/.pi/agent/sessions/"),
            mode="nested_jsonl",
            project_filter=project_filter,
            path_from_event=path_from_event,
            path_from_project_dir=path_from_project_dir,
            title_from_event=title_from_event,
            tail_title_from_event=tail_title_from_event,
            title_max_len=50,
            one_session_per_file=False,
            session_id_from_path=lambda fp: fp,  # historical: full path as id
            require_path=True,
        )
    )


def _tau_index_title(entry: dict) -> tuple[str, str]:
    """``(title, source)`` from a tau ``index.jsonl`` record.

    Tau has no auto-titler: ``/name <text>`` is the only writer of a
    record's ``title``, so a non-empty string is a user rename. The one
    exception is ``get_or_create_default_session``, which stamps the
    literal ``"Default session"`` on ids of the form ``default-<hash>``;
    that placeholder yields ``("", "")`` so the transcript's first prompt
    stands in, the same as any untitled session.
    """
    try:
        title = entry.get("title")
        sid = entry.get("id")
    except Exception:
        return "", ""
    if not isinstance(title, str) or not title.strip():
        return "", ""
    if title == "Default session" and isinstance(sid, str) and sid.startswith("default-"):
        return "", ""
    return title, "rename"


def parse_tau():
    """Tau uses base/<project>/index.jsonl per-project index."""
    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="tau",
            agent="Tau",
            base_dir=os.path.expanduser("~/.tau/sessions"),
            mode="index_jsonl",
            index_basename="index.jsonl",
            get_id=lambda e: str(e.get("id") or ""),
            get_path=lambda e: e.get("cwd") or e.get("path") or "",
            get_title=_tau_index_title,
            get_ts=lambda e: (
                parse_iso_ts(e.get("updated_at"))
                or parse_iso_ts(e.get("created_at"))
                or 0
            ),
            session_file_from_entry=lambda e, d: e.get("path")
            or os.path.join(d, f"{e.get('id')}.jsonl"),
            title_from_event=_tau_title_from_event,
            require_path=True,
        )
    )


def _tau_title_from_event(data: dict) -> str:
    if data.get("type") != "message":
        return ""
    msg = data.get("message") or {}
    if msg.get("role") != "user":
        return ""
    return extract_user_text(msg.get("content"))


def _kimi_title_state(sess_dir: str) -> tuple[str, bool]:
    """``(custom_title, title_generated)`` from a kimi session's sidecar.

    kimi-cli >= 1.39 writes ``<sess_dir>/state.json`` with ``custom_title``
    and ``title_generated``; ``/title`` (alias ``/rename``) sets both, the
    automatic first-prompt title sets ``custom_title`` only. Older builds
    kept the same pair as ``title`` / ``title_generated`` in
    ``metadata.json``, which the CLI migrates away, so it is read only when
    ``state.json`` is absent. Any unreadable or titleless file yields
    ``("", False)`` so the first-message title stands.
    """
    for name, key in (("state.json", "custom_title"), ("metadata.json", "title")):
        path = os.path.join(sess_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return "", False
        if not isinstance(data, dict):
            return "", False
        title = data.get(key)
        if not isinstance(title, str) or not title.strip():
            return "", False
        return title, data.get("title_generated") is True
    return "", False


def parse_kimi():
    def load_hash_map() -> dict[str, str]:
        work_dirs_path = os.path.expanduser("~/.kimi/kimi.json")
        out: dict[str, str] = {}
        if not os.path.exists(work_dirs_path):
            return out
        try:
            with open(work_dirs_path) as f:
                cfg = json.load(f)
            for entry in cfg.get("work_dirs") or []:
                p = entry.get("path") if isinstance(entry, dict) else None
                if not p:
                    continue
                out[hashlib.md5(p.encode(), usedforsecurity=False).hexdigest()] = p
        except Exception:
            pass
        return out

    hash_to_path = load_hash_map()

    def title_from_event(data: dict) -> str:
        role = data.get("role")
        if role in ("_system_prompt", "system", "assistant"):
            return ""
        if role == "user" or (role and role != "assistant"):
            text = extract_user_text(data.get("content"))
            if text.strip() and not text.startswith("You are"):
                return text
        return ""

    def path_from_project_dir(name: str) -> str:
        return hash_to_path.get(name, "") or ""

    def enrich_session(fields: dict, sess_dir: str, proj_name: str) -> None:
        # A rename never touches context.jsonl; it lives in the sidecar.
        custom_title, generated = _kimi_title_state(sess_dir)
        if not custom_title:
            return
        title = clean_title(custom_title)
        if not title:
            return
        fields["title"] = title
        fields["title_source"] = "rename" if generated else ""

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="kimi",
            agent="Kimi",
            base_dir=os.path.expanduser("~/.kimi/sessions"),
            mode="session_dirs",
            path_from_project_dir=path_from_project_dir,
            title_from_event=title_from_event,
            primary_files={"context.jsonl"},
            enrich_session=enrich_session,
            require_path=False,
            default_path=os.path.expanduser("~"),
        )
    )


def _cursor_meta(session_id: str) -> dict:
    """The Cursor CLI's own record for a session, or {} when there is none.

    The CLI writes ``~/.cursor/chats/<workspace-hash>/<session-id>/meta.json``
    with ``cwd``, ``title``, ``createdAtMs`` and ``updatedAtMs``. The
    workspace hash is opaque, so the file is found by scanning one level.
    """
    chats_dir = os.path.expanduser("~/.cursor/chats")
    if not session_id or not os.path.isdir(chats_dir):
        return {}
    try:
        with os.scandir(chats_dir) as it:
            for entry in it:
                meta_path = os.path.join(entry.path, session_id, "meta.json")
                if not os.path.isfile(meta_path):
                    continue
                with open(meta_path) as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _tool_paths_dir(tool_paths: list[str]) -> str:
    """A directory-shaped path from the first tool-call paths, or "".

    The common prefix of two or more distinct paths is a directory by
    construction. When the prefix is one of the paths itself (a single call,
    or the same file repeated) it is the file the agent touched, so its parent
    is used. Existence is checked where it can be, but a project that has
    since moved still yields a directory rather than a file.
    """
    if not tool_paths:
        return ""
    try:
        common = os.path.commonpath(tool_paths)
    except ValueError:
        return ""
    if not common:
        return ""
    if os.path.isdir(common):
        return common
    if os.path.isfile(common) or common in tool_paths:
        return os.path.dirname(common)
    return common


def _under(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or lives inside it."""
    if not path or not root:
        return False
    root = root.rstrip(os.sep)
    return path == root or path.startswith(root + os.sep)


def _cursor_session_dir(
    enc_entry_path: str, tool_paths: list[str], meta: dict
) -> str:
    """Directory a Cursor session ran in. Never a file.

    Signals in priority order:

    1. ``meta["cwd"]``, recorded by the Cursor CLI in the session's meta.json.
    2. The common prefix of the first tool-call paths, collapsed to a
       directory. Anything under ``~/.cursor`` is the harness's own config
       and is kept only as a last resort.
    3. The encoded project folder name (``Users-foo-bar`` -> ``/Users/foo/bar``)
       when that path exists. Decoding is lossy, so it is only trusted when
       the directory is really there. An existing decoded folder outranks a
       tool-path directory that no longer exists.
    4. ``enc_entry_path`` itself, so nothing regresses.
    """
    cwd = meta.get("cwd", "") if isinstance(meta, dict) else ""
    if isinstance(cwd, str) and cwd.strip():
        cwd = cwd.strip()
        if os.path.isfile(cwd):
            cwd = os.path.dirname(cwd)
        return cwd

    cursor_home = os.path.expanduser("~/.cursor")
    tool_dir = _tool_paths_dir(tool_paths)
    tool_dir_is_project = bool(tool_dir) and not _under(tool_dir, cursor_home)
    if tool_dir_is_project and os.path.isdir(tool_dir):
        return tool_dir

    decoded = path_from_encoded_dir(os.path.basename(enc_entry_path), strip_prefix="")
    if decoded and os.path.isdir(decoded) and not _under(decoded, cursor_home):
        return decoded

    if tool_dir_is_project:
        return tool_dir
    if tool_dir and os.path.isdir(tool_dir):
        return tool_dir
    return enc_entry_path


_CURSOR_TIMESTAMP_RE = re.compile(r"<timestamp>.*?</timestamp>", re.DOTALL)
_CURSOR_USER_QUERY_RE = re.compile(r"</?user_query>")


def _cursor_session_title(first_user_text: str, meta: dict) -> str:
    """Title for a Cursor session.

    Prefer the CLI's own ``title`` from ``meta.json``. Otherwise the Cursor
    CLI wraps each stored prompt as ``<timestamp>...</timestamp>`` followed
    by ``<user_query>...</user_query>``; drop the timestamp element with its
    content and unwrap the query before the usual cleaning. Legacy
    transcripts without the wrapper pass straight through ``clean_title``.
    """
    meta_title = meta.get("title") if isinstance(meta, dict) else None
    if isinstance(meta_title, str) and meta_title.strip():
        return clean_title(meta_title, 80)
    text = _CURSOR_TIMESTAMP_RE.sub("", first_user_text or "")
    text = _CURSOR_USER_QUERY_RE.sub("", text)
    return clean_title(text, 80)


def parse_cursor():
    """Cursor agent-transcripts layout is unique; keep custom scanner."""

    def custom() -> list[Session]:
        sessions: list[Session] = []
        projects_dir = os.path.expanduser("~/.cursor/projects")
        if not os.path.exists(projects_dir):
            return sessions
        home = os.path.expanduser("~")
        try:
            # ⚡ Bolt: Using os.scandir to reduce stat syscalls
            with os.scandir(projects_dir) as enc_entry_it:
                for enc_entry in enc_entry_it:
                    transcripts_dir = os.path.join(enc_entry.path, "agent-transcripts")
                    if not os.path.isdir(transcripts_dir):
                        continue
                    with os.scandir(transcripts_dir) as uuid_entry_it:
                        for uuid_entry in uuid_entry_it:
                            if not uuid_entry.is_dir():
                                continue
                            jsonl_path = os.path.join(uuid_entry.path, f"{uuid_entry.name}.jsonl")
                            if not os.path.isfile(jsonl_path):
                                continue
                            mtime = get_mtime(jsonl_path)
                            if mtime == 0:
                                continue
                            first_user_text = ""
                            all_paths: list[str] = []
                            try:
                                with open(jsonl_path) as f:
                                    for line in f:
                                        if not line.strip():
                                            continue
                                        data = json.loads(line)
                                        role = data.get("role", "")
                                        content = data.get("message", {}).get("content", "")
                                        if role == "user" and not first_user_text:
                                            first_user_text = extract_user_text(content)
                                        if isinstance(content, list):
                                            for block in content:
                                                inp = block.get("input", {})
                                                if isinstance(inp, dict):
                                                    p = inp.get("path", "")
                                                    if p and p.startswith(home):
                                                        all_paths.append(p)
                                        if first_user_text and len(all_paths) >= 5:
                                            break
                            except Exception:
                                pass
                            meta = _cursor_meta(uuid_entry.name)
                            sessions.append(
                                Session(
                                    timestamp=mtime,
                                    agent="Cursor",
                                    path=_cursor_session_dir(enc_entry.path, all_paths, meta),
                                    title=_cursor_session_title(first_user_text, meta),
                                    session_id=uuid_entry.name,
                                    tool_name="cursor",
                                )
                            )
        except Exception as exc:
            failures.record("cursor", exc)
        return sessions

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="cursor",
            agent="Cursor",
            base_dir="~/.cursor/projects",
            custom=custom,
        )
    )


def parse_freebuff():
    base = os.path.expanduser("~/.config/manicode/projects/")

    def enrich_session(fields: dict, sess_dir: str, project_name: str) -> None:
        if not fields.get("path"):
            fields["path"] = os.path.join(base, project_name)
        state_path = os.path.join(sess_dir, "run-state.json")
        if not os.path.exists(state_path):
            return
        try:
            with open(state_path) as f:
                state = json.load(f)
            mas = state.get("sessionState", {}).get("mainAgentState", {}) or {}
            cwd = mas.get("cwd") or mas.get("initialCwd")
            if cwd:
                fields["path"] = cwd
            for msg in mas.get("messageHistory", []) or []:
                if msg.get("role") != "user":
                    continue
                text = extract_user_text(msg.get("content"))
                if text:
                    fields["title"] = clean_title(text, 50)
                    break
        except Exception:
            pass

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="freebuff",
            agent="Freebuff",
            base_dir=base,
            mode="session_dirs",
            chats_subdir="chats",
            primary_files={"log.jsonl"},
            enrich_session=enrich_session,
            require_path=False,
            default_path="",
        )
    )


# ---------------------------------------------------------------------------
# JSON family
# ---------------------------------------------------------------------------

def parse_continue():
    sessions_dir = os.path.expanduser("~/.continue/sessions")
    index_path = os.path.join(sessions_dir, "sessions.json")

    def enrich(fields, entry, file_path):
        sid = fields.get("session_id") or ""
        session_file = os.path.join(sessions_dir, f"{sid}.json") if sid else ""
        if session_file and os.path.exists(session_file):
            fields["timestamp"] = max(fields["timestamp"], get_mtime(session_file))
            if not fields.get("title") or fields["title"].lower() in (
                "untitled session",
                "new chat session open",
            ):
                try:
                    with open(session_file) as sf:
                        data = json.load(sf)
                    for item in data.get("history") or []:
                        msg = item.get("message") if isinstance(item, dict) else None
                        if not isinstance(msg, dict) or msg.get("role") != "user":
                            continue
                        text = extract_user_text(msg.get("content"))
                        if text.strip():
                            fields["title"] = clean_title(text)
                            break
                except Exception:
                    pass

    return parse_json_store(
        JsonParserConfig(
            tool_name="continue",
            agent="Continue",
            mode="index",
            index_path=index_path,
            index_items=lambda data: data if isinstance(data, list) else [],
            get_id=lambda e, _f: str(e.get("sessionId") or e.get("id") or ""),
            get_path=lambda e, _f: strip_file_uri(
                e.get("workspaceDirectory") or e.get("cwd") or ""
            ),
            get_title=lambda e, _f: e.get("title") or "",
            get_ts=lambda e, _f: parse_iso_ts(
                e.get("dateCreated") or e.get("date") or 0
            ),
            require_path=True,
            enrich=enrich,
        )
    )


def parse_cline():
    return parse_json_store(
        JsonParserConfig(
            tool_name="cline",
            agent="Cline",
            mode="index",
            index_path=os.path.expanduser("~/.cline/data/state/taskHistory.json"),
            index_items=lambda data: data if isinstance(data, list) else [],
            get_id=lambda e, _f: str(e.get("id") or e.get("ulid") or ""),
            get_path=lambda e, _f: e.get("cwdOnTaskInitialization") or e.get("cwd") or "",
            get_title=lambda e, _f: e.get("task") or "",
            get_ts=lambda e, _f: parse_iso_ts(e.get("ts") or e.get("id") or 0),
            require_path=True,
        )
    )


def parse_amp():
    def get_path(entry, _fp):
        env = entry.get("env") or {}
        initial = env.get("initial") if isinstance(env, dict) else {}
        trees = (initial or {}).get("trees") if isinstance(initial, dict) else None
        if isinstance(trees, list) and trees:
            uri = trees[0].get("uri") if isinstance(trees[0], dict) else ""
            return strip_file_uri(uri) if uri else ""
        return ""

    def get_title(entry, _fp):
        for msg in entry.get("messages") or []:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            text = extract_user_text(msg.get("content"))
            if text.strip():
                return text
        return ""

    def include(entry, _fp):
        if not isinstance(entry, dict):
            return False
        msgs = entry.get("messages") or []
        return bool(msgs or entry.get("env"))

    return parse_json_store(
        JsonParserConfig(
            tool_name="amp",
            agent="Amp",
            mode="files",
            base_dir=os.path.expanduser("~/.local/share/amp/threads"),
            file_glob="*.json",
            get_id=lambda e, fp: str(e.get("id") or os.path.basename(fp).removesuffix(".json")),
            get_path=get_path,
            get_title=get_title,
            get_ts=lambda e, fp: parse_iso_ts(e.get("created")) or get_mtime(fp),
            include=include,
            require_path=False,
            default_path=os.path.expanduser("~"),
        )
    )


def parse_hermes():
    def include(entry, fp):
        name = os.path.basename(fp)
        if name.startswith("request_dump"):
            return False
        return name.startswith("session_") and name.endswith(".json")

    def get_title(entry, _fp):
        for msg in entry.get("messages") or []:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            text = content.strip()
            if text.startswith("[IMPORTANT:"):
                # Skip boilerplate first line, find the actual task
                lines = text.split("\n")
                task = ""
                for line in lines[1:]:
                    line = line.strip()
                    if line and not line.startswith("[IMPORTANT:") and not line.startswith("DELIVERY:"):
                        task = line
                        break
                platform = entry.get("platform") or "cron"
                if task:
                    return f"[{platform}] {task}"
                if "MCP servers have been reloaded" in text:
                    return "[system] MCP reload"
                return f"[{platform}] (no task)"
            if text.startswith("You are running as Hermes' background skill"):
                m = re.search(r"background skill (\w+)", text)
                if m:
                    return f"[skill] {m.group(1)}"
                return "[skill] background"
            return text
        return ""

    return parse_json_store(
        JsonParserConfig(
            tool_name="hermes",
            agent="Hermes",
            mode="files",
            base_dir=os.path.expanduser("~/.hermes/sessions"),
            file_glob="session_*.json",
            get_id=lambda e, fp: str(
                e.get("session_id") or os.path.basename(fp).removesuffix(".json")
            ),
            get_path=lambda e, _f: (
                e.get("cwd") or e.get("working_directory") or os.path.expanduser("~")
            ),
            get_title=get_title,
            get_ts=lambda e, fp: (
                parse_iso_ts(e.get("last_updated"))
                or parse_iso_ts(e.get("session_start"))
                or get_mtime(fp)
            ),
            include=include,
            require_path=False,
            default_path=os.path.expanduser("~"),
        )
    )


def _grok_flag_is_true(value) -> bool:
    """summary.json ``title_is_manual`` is a bool; accept "true"/"1" defensively."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return False


def _grok_title_source(entry) -> str:
    """"rename" for a manual /rename, "auto" for a generated title, else ""."""
    if not isinstance(entry, dict):
        return ""
    generated = entry.get("generated_title")
    summary = entry.get("session_summary")
    title = generated or summary or ""
    if not isinstance(title, str) or not title.strip():
        return ""
    if _grok_flag_is_true(entry.get("title_is_manual")):
        return "rename"
    if isinstance(generated, str) and generated.strip():
        return "auto"
    return ""


def parse_grok():
    def path_from_parent(enc: str) -> str:
        decoded = unquote(enc)
        if decoded.startswith("/"):
            return decoded
        if decoded.startswith("Users/"):
            return "/" + decoded
        return ""

    def get_title(entry, _fp):
        return entry.get("generated_title") or entry.get("session_summary") or ""

    def get_ts(entry, _fp):
        return (
            parse_iso_ts(entry.get("last_active_at"))
            or parse_iso_ts(entry.get("updated_at"))
            or parse_iso_ts(entry.get("created_at"))
            or 0
        )

    def enrich(fields, entry, file_or_dir):
        sess_dir = (
            os.path.dirname(file_or_dir)
            if os.path.isfile(file_or_dir)
            else file_or_dir
        )
        ctx_path = os.path.join(sess_dir, "prompt_context.json")
        if os.path.exists(ctx_path):
            try:
                with open(ctx_path) as f:
                    ctx = json.load(f)
                cwd = ctx.get("working_directory") or ""
                if cwd:
                    fields["path"] = cwd
            except Exception:
                pass
        if not fields.get("path"):
            fields["path"] = os.path.expanduser("~")
        chat_path = os.path.join(sess_dir, "chat_history.jsonl")
        ts = fields.get("timestamp") or 0
        for candidate in (chat_path, sess_dir):
            ts = max(ts, get_mtime(candidate))
        if ts:
            fields["timestamp"] = ts
        if fields.get("title"):
            fields["title_source"] = _grok_title_source(entry)

    return parse_json_store(
        JsonParserConfig(
            tool_name="grok",
            agent="Grok",
            mode="nested_dirs",
            base_dir=os.path.expanduser("~/.grok/sessions"),
            session_file="summary.json",
            path_from_parent=path_from_parent,
            get_title=get_title,
            get_ts=get_ts,
            enrich=enrich,
            require_path=False,
            default_path=os.path.expanduser("~"),
        )
    )


def parse_gemini():
    def session_dir_from_item(path_key, hash_dir):
        if not hash_dir:
            return ""
        return os.path.expanduser(f"~/.gemini/tmp/{hash_dir}")

    def enrich(fields, entry, file_or_dir):
        sess_dir = (
            os.path.dirname(file_or_dir)
            if os.path.isfile(file_or_dir)
            else file_or_dir
        )
        log_path = os.path.join(sess_dir, "logs.json")
        if not os.path.exists(log_path):
            return
        try:
            with open(log_path) as lf:
                logs = json.load(lf)
            for item in logs:
                if item.get("type") == "user":
                    msg = (item.get("message") or "").strip()
                    if msg:
                        fields["title"] = clean_title(msg, 50)
                        break
        except Exception:
            pass

    def map_items(data):
        projects = data.get("projects", {}) if isinstance(data, dict) else {}
        return list(projects.items())

    def get_ts(entry, file_or_dir):
        sess_dir = (
            os.path.dirname(file_or_dir)
            if os.path.isfile(file_or_dir)
            else file_or_dir
        )
        ts = 0.0
        try:
            # ⚡ Bolt: Using os.scandir to reduce stat syscalls
            with os.scandir(sess_dir) as name_entry_it:
                for name_entry in name_entry_it:
                    ts = max(ts, get_mtime(name_entry.path))
        except Exception:
            pass
        return ts or get_mtime(sess_dir)

    return parse_json_store(
        JsonParserConfig(
            tool_name="gemini",
            agent="Gemini CLI",
            mode="project_map",
            index_path=os.path.expanduser("~/.gemini/projects.json"),
            session_file="",  # no primary JSON; enrich reads logs.json
            map_items=map_items,
            session_dir_from_item=session_dir_from_item,
            get_id=lambda e, _f: str(e.get("_key") or ""),
            get_path=lambda e, _f: str(e.get("_key") or e.get("path") or ""),
            get_ts=get_ts,
            require_path=True,
            enrich=enrich,
        )
    )


def _antigravity_conversation_names() -> dict[str, tuple[str, str]]:
    """Map conversation_id -> (title, preview) from the antigravity-cli store.

    ``title`` is set only by the user's ``/rename``; ``preview`` is either an
    auto-generated title or the raw first prompt, so it cannot be told apart.
    Reads the SQLite sidecar first and falls back to the JSON cache mirror.
    Any failure degrades to an empty map.
    """
    from quiver.sessions.engines.common import open_sqlite_ro

    names: dict[str, tuple[str, str]] = {}
    db_path = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
    conn = open_sqlite_ro(db_path)
    if conn is not None:
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(conversation_summaries)")}
            if {"conversation_id", "title", "preview"} <= cols:
                rows = conn.execute(
                    "SELECT conversation_id, title, preview FROM conversation_summaries"
                )
                for cid, title, preview in rows:
                    if not cid:
                        continue
                    names[str(cid)] = (
                        clean_title(title) if isinstance(title, str) else "",
                        clean_title(preview) if isinstance(preview, str) else "",
                    )
        except Exception:
            names = {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if names:
            return names
    mirror = os.path.expanduser("~/.gemini/antigravity-cli/cache/conversation_metadata.json")
    try:
        with open(mirror) as f:
            data = json.load(f)
        convs = data.get("conversations") if isinstance(data, dict) else None
        if isinstance(convs, dict):
            for cid, entry in convs.items():
                summary = entry.get("summary") if isinstance(entry, dict) else None
                if not isinstance(summary, dict):
                    continue
                title = summary.get("Title")
                preview = summary.get("Preview")
                names[str(cid)] = (
                    clean_title(title) if isinstance(title, str) else "",
                    clean_title(preview) if isinstance(preview, str) else "",
                )
    except Exception:
        pass
    return names


def parse_antigravity():
    def custom() -> list[Session]:
        sessions: list[Session] = []
        brain_dir = os.path.expanduser("~/.gemini/antigravity/brain/")
        if not os.path.exists(brain_dir):
            return sessions
        names = _antigravity_conversation_names()
        try:
            # ⚡ Bolt: Using os.scandir to reduce stat syscalls
            with os.scandir(brain_dir) as d_entry_it:
                for d_entry in d_entry_it:
                    if not d_entry.is_dir():
                        continue
                    mtime = 0.0
                    title = ""
                    with os.scandir(d_entry.path) as metadata_it:
                        for metadata_entry in metadata_it:
                            if not metadata_entry.is_file() or not metadata_entry.name.endswith(
                                ".metadata.json"
                            ):
                                continue
                            mt = get_mtime(metadata_entry.path)
                            if mt > mtime:
                                mtime = mt
                                try:
                                    with open(metadata_entry.path) as f:
                                        data = json.load(f)
                                    title = data.get("summary", title)
                                except Exception:
                                    pass
                    if mtime == 0:
                        mtime = get_mtime(d_entry.path)
                    path = ""
                    overview_path = os.path.join(
                        d_entry.path, ".system_generated", "logs", "overview.txt"
                    )
                    if os.path.exists(overview_path):
                        try:
                            with open(overview_path) as f:
                                content = f.read()
                            match = re.search(r'"Cwd":"\\?"([^"\\]+)', content)
                            if match:
                                path = match.group(1)
                        except Exception:
                            pass
                    title_source = ""
                    renamed, preview = names.get(d_entry.name, ("", ""))
                    if renamed:
                        title = renamed
                        title_source = "rename"
                    elif not title and preview:
                        title = preview
                    if path:
                        sessions.append(
                            Session(
                                timestamp=mtime,
                                agent="Antigravity",
                                path=path,
                                title=title,
                                session_id="",
                                tool_name="antigravity",
                                title_source=title_source,
                            )
                        )
        except Exception:
            pass
        return sessions

    return custom()
