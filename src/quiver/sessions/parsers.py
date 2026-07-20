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


def parse_copilot():
    def enrich(conn, row, fields):
        if fields.get("title"):
            return
        sid = fields.get("session_id") or ""
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

    def title_from_event(data: dict) -> str:
        if data.get("type") == "session_start":
            start_title = (data.get("title") or "").strip()
            if start_title and start_title.lower() != "new session":
                return start_title
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
            title_max_len=50,
            one_session_per_file=False,
            session_id_from_path=lambda fp: fp,  # historical: full path as id
            require_path=True,
        )
    )


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
            get_title=lambda e: e.get("title") or "",
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
                # bandit: usedforsecurity=False tells FIPS-compliant hashlib this is just for
                # cache keys, not crypto. Avoids a B324 security warning.
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

    return parse_jsonl_projects(
        JsonlParserConfig(
            tool_name="kimi",
            agent="Kimi",
            base_dir=os.path.expanduser("~/.kimi/sessions"),
            mode="session_dirs",
            path_from_project_dir=path_from_project_dir,
            title_from_event=title_from_event,
            primary_files={"context.jsonl"},
            require_path=False,
            default_path=os.path.expanduser("~"),
        )
    )


def parse_cursor():
    """Cursor agent-transcripts layout is unique; keep custom scanner."""

    def custom() -> list[Session]:
        sessions: list[Session] = []
        projects_dir = os.path.expanduser("~/.cursor/projects")
        if not os.path.exists(projects_dir):
            return sessions
        home = os.path.expanduser("~")
        try:
            for enc_dir in os.listdir(projects_dir):
                transcripts_dir = os.path.join(projects_dir, enc_dir, "agent-transcripts")
                if not os.path.isdir(transcripts_dir):
                    continue
                for uuid_dir in os.listdir(transcripts_dir):
                    uuid_path = os.path.join(transcripts_dir, uuid_dir)
                    if not os.path.isdir(uuid_path):
                        continue
                    jsonl_path = os.path.join(uuid_path, f"{uuid_dir}.jsonl")
                    if not os.path.isfile(jsonl_path):
                        continue
                    mtime = get_mtime(jsonl_path)
                    if mtime == 0:
                        continue
                    title = ""
                    all_paths: list[str] = []
                    try:
                        with open(jsonl_path) as f:
                            for line in f:
                                if not line.strip():
                                    continue
                                data = json.loads(line)
                                role = data.get("role", "")
                                content = data.get("message", {}).get("content", "")
                                if role == "user" and not title:
                                    title = clean_title(extract_user_text(content), 80)
                                if isinstance(content, list):
                                    for block in content:
                                        inp = block.get("input", {})
                                        if isinstance(inp, dict):
                                            p = inp.get("path", "")
                                            if p and p.startswith(home):
                                                all_paths.append(p)
                                if title and len(all_paths) >= 5:
                                    break
                    except Exception:
                        pass
                    path = ""
                    if all_paths:
                        try:
                            path = os.path.commonpath(all_paths)
                        except ValueError:
                            pass
                    if not path:
                        path = enc_dir
                    sessions.append(
                        Session(
                            timestamp=mtime,
                            agent="Cursor",
                            path=path,
                            title=title,
                            session_id=uuid_dir,
                            tool_name="cursor",
                        )
                    )
        except Exception:
            pass
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
            for name in os.listdir(sess_dir):
                ts = max(ts, get_mtime(os.path.join(sess_dir, name)))
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


def parse_antigravity():
    def custom() -> list[Session]:
        sessions: list[Session] = []
        brain_dir = os.path.expanduser("~/.gemini/antigravity/brain/")
        if not os.path.exists(brain_dir):
            return sessions
        try:
            for d in os.listdir(brain_dir):
                dp = os.path.join(brain_dir, d)
                if not os.path.isdir(dp):
                    continue
                mdata_files = glob.glob(os.path.join(dp, "*.metadata.json"))
                mtime = 0.0
                title = ""
                for mf in mdata_files:
                    mt = get_mtime(mf)
                    if mt > mtime:
                        mtime = mt
                        try:
                            with open(mf) as f:
                                data = json.load(f)
                            title = data.get("summary", title)
                        except Exception:
                            pass
                if mtime == 0:
                    mtime = get_mtime(dp)
                path = ""
                overview_path = os.path.join(dp, ".system_generated", "logs", "overview.txt")
                if os.path.exists(overview_path):
                    try:
                        with open(overview_path) as f:
                            content = f.read()
                        match = re.search(r'"Cwd":"\\?"([^"\\]+)', content)
                        if match:
                            path = match.group(1)
                    except Exception:
                        pass
                if path:
                    sessions.append(
                        Session(
                            timestamp=mtime,
                            agent="Antigravity",
                            path=path,
                            title=title,
                            session_id="",
                            tool_name="antigravity",
                        )
                    )
        except Exception:
            pass
        return sessions

    return custom()
