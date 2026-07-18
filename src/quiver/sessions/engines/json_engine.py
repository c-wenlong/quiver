"""JSON index / per-file / nested-dir family engine."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from quiver.sessions.engines.common import (
    clean_title,
    dig,
    expand_path,
    get_mtime,
    parse_iso_ts,
    strip_file_uri,
)
from quiver.sessions.models import Session


@dataclass
class JsonParserConfig:
    tool_name: str
    agent: str
    # Modes:
    # - index: load one JSON file (list or dict)
    # - files: glob JSON files under base_dir
    # - nested_dirs: base/<parent>/<session_id>/<session_file>
    # - project_map: index maps path→hash, sessions live under session_dir(path, hash)
    mode: str = "index"
    index_path: str = ""
    base_dir: str = ""
    file_glob: str = "*.json"
    # nested_dirs: primary JSON basename inside each session dir
    session_file: str = "summary.json"
    # nested_dirs: optional parent-dir → workspace path
    path_from_parent: Callable[[str], str] | None = None
    # project_map: iterate (path_key, hash_or_meta) pairs
    map_items: Callable[[Any], Iterable[Any]] | None = None
    # project_map: resolve session directory from map entry
    session_dir_from_item: Callable[[Any, Any], str] | None = None
    # How to iterate index contents
    index_items: Callable[[Any], Iterable[Any]] | None = None
    # Field extractors from each item/file payload
    get_id: Callable[[Any, str], str] | None = None
    get_path: Callable[[Any, str], str] | None = None
    get_title: Callable[[Any, str], str] | None = None
    get_ts: Callable[[Any, str], float] | None = None
    # Skip item/file
    include: Callable[[Any, str], bool] | None = None
    require_path: bool = True
    default_path: str | None = None
    title_max_len: int = 80
    # Optional enrichment with side files (fields, entry, file_or_dir)
    enrich: Callable[[dict, Any, str], None] | None = None
    skip_basenames: set[str] = field(default_factory=set)


def parse_json_store(config: JsonParserConfig) -> list[Session]:
    if config.mode == "files":
        return _parse_files(config)
    if config.mode == "nested_dirs":
        return _parse_nested_dirs(config)
    if config.mode == "project_map":
        return _parse_project_map(config)
    return _parse_index(config)


def _parse_index(config: JsonParserConfig) -> list[Session]:
    sessions: list[Session] = []
    path = expand_path(config.index_path)
    if not os.path.exists(path):
        return sessions
    try:
        with open(path) as f:
            data = json.load(f)
        items = list(config.index_items(data)) if config.index_items else _default_items(data)
        for item in items:
            entry, file_hint = _normalize_item(item)
            if config.include and not config.include(entry, file_hint or path):
                continue
            sess = _to_session(entry, file_hint or path, config)
            if sess:
                sessions.append(sess)
    except Exception:
        pass
    return sessions


def _parse_files(config: JsonParserConfig) -> list[Session]:
    import glob as _glob

    sessions: list[Session] = []
    base = expand_path(config.base_dir)
    if not os.path.exists(base):
        return sessions
    pattern = os.path.join(base, config.file_glob)
    try:
        for fp in _glob.glob(pattern, recursive=True):
            if not os.path.isfile(fp):
                continue
            if os.path.basename(fp) in config.skip_basenames:
                continue
            try:
                with open(fp) as f:
                    data = json.load(f)
            except Exception:
                continue
            if config.include and not config.include(data, fp):
                continue
            sess = _to_session(data, fp, config)
            if sess:
                sessions.append(sess)
    except Exception:
        pass
    return sessions


def _parse_nested_dirs(config: JsonParserConfig) -> list[Session]:
    """base/<parent>/<session_id>/<session_file> (+ optional enrich side files)."""
    sessions: list[Session] = []
    base = expand_path(config.base_dir)
    if not os.path.exists(base):
        return sessions
    try:
        for parent_name in os.listdir(base):
            parent_dir = os.path.join(base, parent_name)
            if not os.path.isdir(parent_dir):
                continue
            fallback = ""
            if config.path_from_parent:
                try:
                    fallback = config.path_from_parent(parent_name) or ""
                except Exception:
                    fallback = ""
            for sid in os.listdir(parent_dir):
                sess_dir = os.path.join(parent_dir, sid)
                if not os.path.isdir(sess_dir):
                    continue
                primary = os.path.join(sess_dir, config.session_file)
                entry: Any = {}
                file_path = primary if os.path.isfile(primary) else sess_dir
                if os.path.isfile(primary):
                    try:
                        with open(primary) as f:
                            entry = json.load(f)
                    except Exception:
                        entry = {}
                if config.include and not config.include(entry, file_path):
                    continue

                # Force session id from dir name unless get_id overrides
                def get_id_wrapped(e, fp, _sid=sid, _orig=config.get_id):
                    if _orig:
                        try:
                            val = _orig(e, fp)
                            if val:
                                return val
                        except Exception:
                            pass
                    return _sid

                # Inject fallback path via temporary get_path wrapper
                orig_get_path = config.get_path

                def get_path_wrapped(e, fp, _fb=fallback, _orig=orig_get_path):
                    path = ""
                    if _orig:
                        try:
                            path = _orig(e, fp) or ""
                        except Exception:
                            path = ""
                    return path or _fb

                # Build fields with wrappers without mutating shared config permanently
                saved_get_id = config.get_id
                saved_get_path = config.get_path
                config.get_id = get_id_wrapped
                config.get_path = get_path_wrapped
                try:
                    sess = _to_session(entry, file_path, config)
                finally:
                    config.get_id = saved_get_id
                    config.get_path = saved_get_path
                if sess:
                    # Prefer mtime of session dir contents if ts is only from missing file
                    if not sess.timestamp:
                        sess.timestamp = get_mtime(sess_dir)
                    sessions.append(sess)
    except Exception:
        pass
    return sessions


def _parse_project_map(config: JsonParserConfig) -> list[Session]:
    """Index maps workspace path → session-dir key; each dir is one session."""
    sessions: list[Session] = []
    index_path = expand_path(config.index_path)
    if not os.path.exists(index_path):
        return sessions
    try:
        with open(index_path) as f:
            data = json.load(f)
        items: Iterable[Any]
        if config.map_items:
            items = config.map_items(data)
        elif isinstance(data, dict) and isinstance(data.get("projects"), dict):
            items = data["projects"].items()
        else:
            items = _default_items(data)

        for item in items:
            if isinstance(item, tuple) and len(item) == 2:
                path_key, meta = item
            else:
                continue
            sess_dir = ""
            if config.session_dir_from_item:
                try:
                    sess_dir = config.session_dir_from_item(path_key, meta) or ""
                except Exception:
                    sess_dir = ""
            if not sess_dir:
                continue
            sess_dir = expand_path(sess_dir)
            if not os.path.exists(sess_dir):
                continue

            entry: Any = {"_key": path_key, "meta": meta, "path": path_key}
            # Prefer loading a primary session file if present
            primary = os.path.join(sess_dir, config.session_file) if config.session_file else ""
            file_path = primary if primary and os.path.isfile(primary) else sess_dir
            if primary and os.path.isfile(primary):
                try:
                    with open(primary) as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        entry = {**entry, **loaded}
                    else:
                        entry["data"] = loaded
                except Exception:
                    pass

            if config.include and not config.include(entry, file_path):
                continue

            orig_get_path = config.get_path

            def get_path_wrapped(e, fp, _pk=path_key, _orig=orig_get_path):
                if _orig:
                    try:
                        val = _orig(e, fp) or ""
                        if val:
                            return val
                    except Exception:
                        pass
                return str(_pk or "")

            saved = config.get_path
            config.get_path = get_path_wrapped
            try:
                sess = _to_session(entry, file_path, config)
            finally:
                config.get_path = saved
            if sess:
                sessions.append(sess)
    except Exception:
        pass
    return sessions


def _default_items(data: Any) -> Iterable[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("projects", "sessions", "items", "threads"):
            if isinstance(data.get(key), list):
                return data[key]
        return data.items()
    return []


def _normalize_item(item: Any) -> tuple[Any, str]:
    if isinstance(item, tuple) and len(item) == 2:
        k, v = item
        if isinstance(v, dict):
            merged = dict(v)
            merged.setdefault("_key", k)
            return merged, str(k)
        return {"_key": k, "value": v}, str(k)
    return item, ""


def _to_session(entry: Any, file_path: str, config: JsonParserConfig) -> Session | None:
    sid = ""
    path = ""
    title = ""
    ts = 0.0

    if config.get_id:
        try:
            sid = config.get_id(entry, file_path) or ""
        except Exception:
            sid = ""
    elif isinstance(entry, dict):
        sid = str(entry.get("id") or entry.get("sessionId") or entry.get("session_id") or "")

    if config.get_path:
        try:
            path = config.get_path(entry, file_path) or ""
        except Exception:
            path = ""
    elif isinstance(entry, dict):
        path = (
            entry.get("cwd")
            or entry.get("path")
            or entry.get("workspaceDirectory")
            or entry.get("directory")
            or ""
        )
        if isinstance(path, str):
            path = strip_file_uri(path)

    if config.get_title:
        try:
            title = config.get_title(entry, file_path) or ""
        except Exception:
            title = ""
    elif isinstance(entry, dict):
        title = entry.get("title") or entry.get("summary") or entry.get("task") or ""

    title = clean_title(str(title), config.title_max_len) if title else ""

    if config.get_ts:
        try:
            ts = float(config.get_ts(entry, file_path) or 0)
        except Exception:
            ts = 0.0
    elif isinstance(entry, dict):
        ts = (
            parse_iso_ts(entry.get("updated_at") or entry.get("updatedAt"))
            or parse_iso_ts(entry.get("created_at") or entry.get("dateCreated") or entry.get("ts"))
            or parse_iso_ts(entry.get("created") or entry.get("last_updated"))
        )

    if not ts:
        ts = get_mtime(file_path)
        if not ts and os.path.isdir(file_path):
            ts = get_mtime(file_path)

    if not sid and file_path:
        sid = os.path.basename(file_path)
        for suffix in (".json", ".jsonl"):
            if sid.endswith(suffix):
                sid = sid[: -len(suffix)]
        # nested_dirs: file is summary.json inside session dir
        if sid == config.session_file.removesuffix(".json") or sid == "summary":
            sid = os.path.basename(os.path.dirname(file_path))

    fields = {
        "timestamp": ts,
        "agent": config.agent,
        "path": path or config.default_path or "",
        "title": title,
        "session_id": sid,
        "tool_name": config.tool_name,
    }
    if config.enrich:
        try:
            config.enrich(fields, entry, file_path)
        except Exception:
            pass

    if config.require_path and not fields.get("path"):
        return None
    return Session(**fields)


__all__ = ["JsonParserConfig", "parse_json_store", "dig"]
