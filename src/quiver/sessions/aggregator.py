"""Aggregate sessions from all tool parsers."""

import json
import os
import time

from quiver.paths import SESSION_CACHE_FILE
from quiver.sessions.models import Session
from quiver.sessions.parsers import (
    parse_amp,
    parse_antigravity,
    parse_claude,
    parse_cline,
    parse_codex,
    parse_continue,
    parse_copilot,
    parse_crush,
    parse_cursor,
    parse_droid,
    parse_forge,
    parse_freebuff,
    parse_gemini,
    parse_grok,
    parse_hermes,
    parse_kimi,
    parse_mimo,
    parse_opencode,
    parse_pi,
    parse_tau,
)

# (parser_fn, agent_filter_keys)
PARSER_REGISTRY: list[tuple[str, callable, tuple[str, ...]]] = [
    ("opencode", parse_opencode, ("opencode", "oc")),
    ("claude", parse_claude, ("claude", "cc")),
    ("gemini", parse_gemini, ("gemini", "gg")),
    ("antigravity", parse_antigravity, ("antigravity", "ag")),
    ("codex", parse_codex, ("codex", "cx")),
    ("pi", parse_pi, ("pi",)),
    ("cursor", parse_cursor, ("cursor", "cs")),
    ("freebuff", parse_freebuff, ("freebuff", "fb")),
    ("droid", parse_droid, ("droid", "df", "factory")),
    ("copilot", parse_copilot, ("copilot", "cp")),
    ("continue", parse_continue, ("continue", "cn", "ct")),
    ("crush", parse_crush, ("crush", "cr")),
    ("amp", parse_amp, ("amp", "ap")),
    ("kimi", parse_kimi, ("kimi", "ki")),
    ("hermes", parse_hermes, ("hermes", "hs")),
    ("grok", parse_grok, ("grok", "gk")),
    ("cline", parse_cline, ("cline", "cl")),
    ("forge", parse_forge, ("forge", "fc")),
    ("mimo", parse_mimo, ("mimo",)),
    ("tau", parse_tau, ("tau",)),
]

# Cache TTL in seconds (60s default)
_CACHE_TTL = 60.0


def _agent_matches(agent_filter: str | None, keys: tuple[str, ...]) -> bool:
    if not agent_filter:
        return True
    needle = agent_filter.lower()
    return needle in keys


def _load_cached_sessions() -> list[Session] | None:
    """Load sessions from disk cache if fresh enough."""
    try:
        if not SESSION_CACHE_FILE.exists():
            return None
        with open(SESSION_CACHE_FILE) as f:
            data = json.load(f)
        cached_at = data.get("cached_at", 0)
        if time.time() - cached_at > _CACHE_TTL:
            return None
        raw = data.get("sessions", [])
        return [
            Session(
                timestamp=s["timestamp"],
                agent=s["agent"],
                path=s["path"],
                title=s["title"],
                session_id=s["session_id"],
                tool_name=s["tool_name"],
            )
            for s in raw
        ]
    except Exception:
        return None


def _save_cached_sessions(sessions: list[Session]) -> None:
    """Persist sessions to disk cache."""
    try:
        SESSION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at": time.time(),
            "sessions": [
                {
                    "timestamp": s.timestamp,
                    "agent": s.agent,
                    "path": s.path,
                    "title": s.title,
                    "session_id": s.session_id,
                    "tool_name": s.tool_name,
                }
                for s in sessions
            ],
        }
        with open(SESSION_CACHE_FILE, "w") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _run_all_parsers() -> list[Session]:
    """Execute all registered parsers and return raw session list."""
    sessions: list[Session] = []
    for _name, parser, _keys in PARSER_REGISTRY:
        sessions.extend(parser())
    return sessions


def invalidate_cache() -> None:
    """Delete the session cache file (forces re-parse on next call)."""
    try:
        if SESSION_CACHE_FILE.exists():
            SESSION_CACHE_FILE.unlink()
    except Exception:
        pass


def get_all_sessions(limit=10, agent=None, cwd=None, use_cache=False) -> list[Session]:
    """Return sessions from all parsers, optionally filtered.

    When use_cache=True, reads from disk cache if fresh (within _CACHE_TTL).
    """
    if use_cache:
        cached = _load_cached_sessions()
        if cached is not None:
            sessions = cached
        else:
            sessions = _run_all_parsers()
            _save_cached_sessions(sessions)
        # Apply agent filter post-hoc on cached sessions
        if agent:
            valid_names = {
                name for name, _p, keys in PARSER_REGISTRY
                if _agent_matches(agent, keys)
            }
            sessions = [s for s in sessions if s.tool_name in valid_names]
    else:
        # No cache: only run parsers matching the agent filter
        sessions: list[Session] = []
        for _name, parser, keys in PARSER_REGISTRY:
            if _agent_matches(agent, keys):
                sessions.extend(parser())

    if cwd:
        target_path = os.path.realpath(cwd)
        target_path_sep = target_path + os.sep
        filtered = []

        # ⚡ Bolt: Cache realpath results to avoid O(N) filesystem hits
        # since many sessions share the same base path
        realpath_cache: dict[str, str] = {}
        for session in sessions:
            if session.path not in realpath_cache:
                realpath_cache[session.path] = os.path.realpath(session.path)

            session_path = realpath_cache[session.path]
            if session_path == target_path or session_path.startswith(target_path_sep):
                filtered.append(session)
        sessions = filtered

    sessions.sort(key=lambda s: s.timestamp, reverse=True)

    if limit is not None:
        return sessions[:limit]
    return sessions
