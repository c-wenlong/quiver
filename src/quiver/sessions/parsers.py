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

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="claude",
            agent="Claude Code",
            base_dir=os.path.expanduser("~/.claude/projects/"),
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

    return parse_jsonl_projects(
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
