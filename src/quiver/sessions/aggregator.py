"""Aggregate sessions from all tool parsers."""

import os

from quiver.sessions.models import Session
from quiver.sessions.parsers import (
    parse_antigravity,
    parse_claude,
    parse_codex,
    parse_cursor,
    parse_freebuff,
    parse_gemini,
    parse_opencode,
    parse_pi,
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
]


def _agent_matches(agent_filter: str | None, keys: tuple[str, ...]) -> bool:
    if not agent_filter:
        return True
    needle = agent_filter.lower()
    return needle in keys


def get_all_sessions(limit=10, agent=None, cwd=None) -> list[Session]:
    sessions: list[Session] = []

    for _name, parser, keys in PARSER_REGISTRY:
        if _agent_matches(agent, keys):
            sessions.extend(parser())

    if cwd:
        target_path = os.path.realpath(cwd)
        filtered = []
        for session in sessions:
            session_path = os.path.realpath(session.path)
            if session_path == target_path or session_path.startswith(target_path + os.sep):
                filtered.append(session)
        sessions = filtered

    sessions.sort(key=lambda s: s.timestamp, reverse=True)

    if limit is not None:
        return sessions[:limit]
    return sessions
