from .models import Session
from .parsers import (
    parse_opencode,
    parse_claude,
    parse_gemini,
    parse_antigravity,
    parse_codex,
    parse_pi,
    parse_cursor,
    parse_freebuff
)

def get_all_sessions(limit=10, agent=None, cwd=None):
    sessions = []
    
    # Collect from all parsers
    if not agent or agent.lower() in ('opencode', 'oc'):
        sessions.extend(parse_opencode())
    if not agent or agent.lower() in ('claude', 'cc'):
        sessions.extend(parse_claude())
    if not agent or agent.lower() in ('gemini', 'gg'):
        sessions.extend(parse_gemini())
    if not agent or agent.lower() in ('antigravity', 'ag'):
        sessions.extend(parse_antigravity())
    if not agent or agent.lower() in ('codex', 'cx'):
        sessions.extend(parse_codex())
    if not agent or agent.lower() == 'pi':
        sessions.extend(parse_pi())
    if not agent or agent.lower() in ('cursor', 'cs'):
        sessions.extend(parse_cursor())
    if not agent or agent.lower() in ('freebuff', 'fb'):
        sessions.extend(parse_freebuff())

    # Filter by working directory if requested
    if cwd:
        # Resolve realpath to handle symlinks and relative parts
        import os
        target_path = os.path.realpath(cwd)
        filtered = []
        for s in sessions:
            # We match if the session path is the same or starts with target_path
            # To avoid prefix issues, add trailing slash if needed
            sp = os.path.realpath(s.path)
            if sp == target_path or sp.startswith(target_path + os.sep):
                filtered.append(s)
        sessions = filtered

    # Sort descending by timestamp
    sessions.sort(key=lambda s: s.timestamp, reverse=True)
    
    if limit is not None:
        return sessions[:limit]
    return sessions
