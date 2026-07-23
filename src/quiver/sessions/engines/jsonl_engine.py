"""JSONL / project-directory family engine."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from quiver.sessions.engines.common import (
    clean_title,
    expand_path,
    extract_user_text,
    get_mtime,
    parse_iso_ts,
    path_from_encoded_dir,
)
from quiver.sessions.models import Session


@dataclass
class JsonlParserConfig:
    tool_name: str
    agent: str
    base_dir: str
    # Discovery modes:
    # - nested_jsonl: walk project dirs, find *.jsonl
    # - glob: session_glob under base_dir
    # - session_dirs: base/<project>/[chats_subdir/]<session_id>/(jsonl)
    # - index_jsonl: base/<project>/index.jsonl lists sessions
    mode: str = "nested_jsonl"
    session_glob: str = "**/*.jsonl"
    # For index_jsonl mode
    index_basename: str = "index.jsonl"
    # Project dir filter: callable(name, path) -> bool
    project_filter: Callable[[str, str], bool] | None = None
    # Optional intermediate dir under each project (e.g. freebuff "chats")
    chats_subdir: str | None = None
    # If set, only these basenames are treated as session files
    primary_files: set[str] | None = None
    # Skip these basenames
    skip_basenames: set[str] = field(default_factory=set)
    # Path resolution
    path_from_event: Callable[[dict], str] | None = None
    path_from_project_dir: Callable[[str], str] | None = None
    default_path: str | None = None
    require_path: bool = True
    # Title extraction from event stream
    title_from_event: Callable[[dict], str] | None = None
    title_max_len: int = 80
    # Session id from path
    session_id_from_path: Callable[[str], str] | None = None
    # Max lines to scan per file for path/title
    path_scan_lines: int = 40
    title_scan_full: bool = True
    # Whether each jsonl is its own session (True) or one session per project dir (False)
    one_session_per_file: bool = True
    # Optional post-process for session_dirs (fields, sess_dir, project_name)
    enrich_session: Callable[[dict, str, str], None] | None = None
    # index_jsonl field extractors
    get_id: Callable[[dict], str] | None = None
    get_path: Callable[[dict], str] | None = None
    get_title: Callable[[dict], str] | None = None
    get_ts: Callable[[dict], float] | None = None
    # Resolve side session file for title enrichment: (entry, project_dir) -> path
    session_file_from_entry: Callable[[dict, str], str] | None = None
    # Escape hatch for layouts that still need a bespoke walk
    custom: Callable[[], list[Session]] | None = None


def parse_jsonl_projects(config: JsonlParserConfig) -> list[Session]:
    if config.custom:
        try:
            return config.custom()
        except Exception:
            return []

    base = expand_path(config.base_dir)
    if not os.path.exists(base):
        return []

    if config.mode == "glob":
        return _parse_glob(base, config)
    if config.mode == "session_dirs":
        return _parse_session_dirs(base, config)
    if config.mode == "index_jsonl":
        return _parse_index_jsonl(base, config)
    return _parse_nested(base, config)


def _parse_nested(base: str, config: JsonlParserConfig) -> list[Session]:
    sessions: list[Session] = []
    try:
        # ⚡ Bolt: Using os.scandir to reduce stat syscalls
        for name_entry in os.scandir(base):
            if not name_entry.is_dir():
                continue
            proj = name_entry.path
            name = name_entry.name
            if config.project_filter and not config.project_filter(name, proj):
                continue
            fallback_path = ""
            if config.path_from_project_dir:
                try:
                    fallback_path = config.path_from_project_dir(name) or ""
                except Exception:
                    fallback_path = ""
            jsonl_files = _list_jsonl(proj, config)
            if config.one_session_per_file:
                for fp in jsonl_files:
                    sess = _session_from_jsonl(fp, config, fallback_path)
                    if sess:
                        sessions.append(sess)
            else:
                if jsonl_files:
                    latest = max(jsonl_files, key=get_mtime)
                    sess = _session_from_jsonl(latest, config, fallback_path)
                    if sess:
                        sessions.append(sess)
                elif fallback_path or config.default_path:
                    # Preserve legacy behavior: project dir with no jsonl still counts
                    mtime = get_mtime(proj)
                    if mtime > 0:
                        path = fallback_path or config.default_path or ""
                        if path or not config.require_path:
                            sessions.append(
                                Session(
                                    timestamp=mtime,
                                    agent=config.agent,
                                    path=path,
                                    title="",
                                    session_id="",
                                    tool_name=config.tool_name,
                                )
                            )
    except Exception:
        pass
    return sessions


def _parse_glob(base: str, config: JsonlParserConfig) -> list[Session]:
    import glob as _glob

    sessions: list[Session] = []
    pattern = os.path.join(base, config.session_glob)
    try:
        for fp in _glob.glob(pattern, recursive=True):
            if not os.path.isfile(fp):
                continue
            if os.path.basename(fp) in config.skip_basenames:
                continue
            if config.primary_files and os.path.basename(fp) not in config.primary_files:
                continue
            sess = _session_from_jsonl(fp, config, "")
            if sess:
                sessions.append(sess)
    except Exception:
        pass
    return sessions


def _parse_session_dirs(base: str, config: JsonlParserConfig) -> list[Session]:
    """base/<project>/[chats_subdir/]<session_id>/(primary jsonl)."""
    sessions: list[Session] = []
    try:
        # ⚡ Bolt: Using os.scandir to reduce stat syscalls
        for proj_entry in os.scandir(base):
            if not proj_entry.is_dir():
                continue
            proj = proj_entry.path
            proj_name = proj_entry.name
            if config.project_filter and not config.project_filter(proj_name, proj):
                continue
            fallback = ""
            if config.path_from_project_dir:
                try:
                    fallback = config.path_from_project_dir(proj_name) or ""
                except Exception:
                    fallback = ""
            sessions_root = (
                os.path.join(proj, config.chats_subdir)
                if config.chats_subdir
                else proj
            )
            if not os.path.isdir(sessions_root):
                continue
            # ⚡ Bolt: Using os.scandir to reduce stat syscalls
            for sid_entry in os.scandir(sessions_root):
                if not sid_entry.is_dir():
                    continue
                sess_dir = sid_entry.path
                sid = sid_entry.name
                jsonl_files = _list_jsonl(sess_dir, config)
                if not jsonl_files:
                    continue
                primary = max(jsonl_files, key=get_mtime)
                sess = _session_from_jsonl(primary, config, fallback, forced_id=sid)
                if not sess:
                    continue
                if config.enrich_session:
                    fields = {
                        "timestamp": sess.timestamp,
                        "agent": sess.agent,
                        "path": sess.path,
                        "title": sess.title,
                        "session_id": sess.session_id,
                        "tool_name": sess.tool_name,
                    }
                    try:
                        config.enrich_session(fields, sess_dir, proj_name)
                    except Exception:
                        pass
                    if config.require_path and not fields.get("path"):
                        continue
                    sess = Session(**fields)
                sessions.append(sess)
    except Exception:
        pass
    return sessions


def _parse_index_jsonl(base: str, config: JsonlParserConfig) -> list[Session]:
    """base/<project>/index.jsonl — each line describes a session."""
    sessions: list[Session] = []
    try:
        # ⚡ Bolt: Using os.scandir to reduce stat syscalls
        for proj_entry in os.scandir(base):
            if not proj_entry.is_dir():
                continue
            proj = proj_entry.path
            proj_name = proj_entry.name
            if config.project_filter and not config.project_filter(proj_name, proj):
                continue
            index_path = os.path.join(proj, config.index_basename)
            if not os.path.isfile(index_path):
                continue
            fallback = ""
            if config.path_from_project_dir:
                try:
                    fallback = config.path_from_project_dir(proj_name) or ""
                except Exception:
                    fallback = ""
            try:
                with open(index_path) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(entry, dict):
                            continue
                        sess = _session_from_index_entry(
                            entry, index_path, proj, fallback, config
                        )
                        if sess:
                            sessions.append(sess)
            except Exception:
                pass
    except Exception:
        pass
    return sessions


def _session_from_index_entry(
    entry: dict,
    index_path: str,
    proj_dir: str,
    fallback: str,
    config: JsonlParserConfig,
) -> Session | None:
    sid = ""
    if config.get_id:
        try:
            sid = config.get_id(entry) or ""
        except Exception:
            sid = ""
    else:
        sid = str(entry.get("id") or entry.get("session_id") or "")

    path = ""
    if config.get_path:
        try:
            path = config.get_path(entry) or ""
        except Exception:
            path = ""
    else:
        path = entry.get("cwd") or entry.get("path") or entry.get("directory") or ""

    title = ""
    if config.get_title:
        try:
            title = config.get_title(entry) or ""
        except Exception:
            title = ""
    else:
        title = entry.get("title") or ""
    title = clean_title(str(title), config.title_max_len) if title else ""

    ts = 0.0
    if config.get_ts:
        try:
            ts = float(config.get_ts(entry) or 0)
        except Exception:
            ts = 0.0
    else:
        ts = (
            parse_iso_ts(entry.get("updated_at"))
            or parse_iso_ts(entry.get("created_at"))
            or parse_iso_ts(entry.get("timestamp"))
        )
    if not ts:
        ts = get_mtime(index_path)

    sess_file = ""
    if config.session_file_from_entry:
        try:
            sess_file = config.session_file_from_entry(entry, proj_dir) or ""
        except Exception:
            sess_file = ""
    elif sid:
        sess_file = os.path.join(proj_dir, f"{sid}.jsonl")

    if sess_file and os.path.exists(sess_file):
        if not title and config.title_from_event:
            title = _title_from_jsonl(sess_file, config) or title
        ts = max(ts, get_mtime(sess_file))

    if not path:
        path = fallback or config.default_path or ""
    if config.require_path and not path:
        return None

    return Session(
        timestamp=ts,
        agent=config.agent,
        path=path,
        title=title,
        session_id=sid,
        tool_name=config.tool_name,
    )


def _title_from_jsonl(fp: str, config: JsonlParserConfig) -> str:
    if not config.title_from_event:
        return ""
    try:
        with open(fp) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                try:
                    raw = config.title_from_event(data) or ""
                except Exception:
                    raw = ""
                if raw:
                    return clean_title(raw, config.title_max_len)
    except Exception:
        pass
    return ""


def _list_jsonl(directory: str, config: JsonlParserConfig) -> list[str]:
    out: list[str] = []
    try:
        # ⚡ Bolt: Using os.scandir to reduce stat syscalls
        for name_entry in os.scandir(directory):
            name = name_entry.name
            if not name.endswith(".jsonl"):
                continue
            if name in config.skip_basenames:
                continue
            if config.primary_files and name not in config.primary_files:
                continue
            if name == config.index_basename and config.mode == "index_jsonl":
                continue
            out.append(name_entry.path)
    except Exception:
        pass
    return out


def _session_from_jsonl(
    fp: str,
    config: JsonlParserConfig,
    fallback_path: str,
    forced_id: str | None = None,
) -> Session | None:
    mtime = get_mtime(fp)
    if mtime == 0:
        return None

    if forced_id is not None:
        sid = forced_id
    elif config.session_id_from_path:
        sid = config.session_id_from_path(fp)
    else:
        sid = os.path.basename(fp).removesuffix(".jsonl")

    path = ""
    title = ""
    try:
        with open(fp) as f:
            for i, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                if not path and config.path_from_event:
                    try:
                        path = config.path_from_event(data) or ""
                    except Exception:
                        path = ""
                if not title and config.title_from_event:
                    try:
                        raw = config.title_from_event(data) or ""
                        if raw:
                            title = clean_title(raw, config.title_max_len)
                    except Exception:
                        pass
                if path and title:
                    break
                if not config.title_scan_full and i >= config.path_scan_lines:
                    break
                if path and not config.title_from_event and i >= config.path_scan_lines:
                    break
    except Exception:
        pass

    if not path:
        path = fallback_path or config.default_path or ""
    if config.require_path and not path:
        return None

    return Session(
        timestamp=mtime,
        agent=config.agent,
        path=path,
        title=title,
        session_id=sid,
        tool_name=config.tool_name,
    )


# --- common event helpers used by adapters ---

def event_cwd(data: dict, *keys: str) -> str:
    for key in keys or ("cwd", "working_directory", "directory"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
        payload = data.get("payload")
        if isinstance(payload, dict):
            val = payload.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def first_user_title(
    data: dict,
    *,
    type_field: str = "type",
    type_values: Iterable[str] = ("user", "message"),
    role_path: tuple[str, ...] = ("message", "role"),
    content_path: tuple[str, ...] = ("message", "content"),
    role_value: str = "user",
) -> str:
    """Generic title extractor; adapters can wrap with tool-specific checks."""
    t = data.get(type_field)
    if type_values and t is not None and t not in type_values and role_path:
        # still allow role-based match
        pass

    # role at top level
    if data.get("role") == role_value:
        return extract_user_text(data.get("content"))

    # nested message
    msg = data
    for key in role_path[:-1] if role_path else ():
        msg = msg.get(key) if isinstance(msg, dict) else None
        if not isinstance(msg, dict):
            return ""
    if role_path:
        role = msg.get(role_path[-1]) if isinstance(msg, dict) else None
        if role != role_value:
            # type-only match (e.g. type==user)
            if t not in type_values:
                return ""
        content = data
        for key in content_path:
            if not isinstance(content, dict):
                return ""
            content = content.get(key)
        return extract_user_text(content)

    if t in type_values:
        return extract_user_text(data.get("message") or data.get("content") or "")
    return ""


def encoded_dash_path(name: str) -> str:
    return path_from_encoded_dir(name, strip_prefix="-")
