#!/usr/bin/env python3
"""Optional FastMCP server exposing recent AI sessions as an MCP tool.

Requires the optional ``mcp`` dependency: ``pip install -e ".[server]"``
from a Quiver checkout. Not ``pip install quiver[server]``: the PyPI
package named quiver is an unrelated project.
"""
import sys
import time
from pathlib import Path

from quiver.sessions import search_index
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.commands import relative_time
from quiver.sessions.query import SessionQuery

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "The 'mcp' package is required to run the MCP server. From your "
        "Quiver checkout, run: pip install -e \".[server]\" "
        "(the PyPI package named quiver is a different project).",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = FastMCP("SWE Agent History")


@mcp.tool()
def get_recent_sessions(limit: int = 10, agent: str = None, directory_path: str = None, search: str = None) -> str:
    """
    Retrieve the most recent AI coding agent sessions across the entire system.
    Returns a formatted text table of sessions including timestamps, agent name, project directory, and summary/title.

    Args:
        limit: Number of sessions to return (default 10)
        agent: Filter by a specific agent (e.g., 'claude', 'opencode', 'gemini')
        directory_path: Filter to only show sessions that occurred within this absolute directory path or its subdirectories.
        search: Optional text filter. When the Quiver app's search index exists it matches transcript content and each
                matched session carries a 'match:' snippet line; otherwise it filters title/path/agent/session id.
    """
    sessions = get_all_sessions(
        limit=None if search else limit, agent=agent, cwd=directory_path
    )

    snippets: dict[str, str] = {}
    if search:
        search_ids = None
        home = Path.home()
        if search_index.available(home):
            hits = search_index.search(
                home,
                search,
                limit=max(limit, search_index.DEFAULT_LIMIT),
                agents=search_index.tool_names_for(agent),
            )
            if hits:
                search_ids = {h.session_id for h in hits if h.session_id}
                hit_by_key = {
                    key: h
                    for h in hits
                    for key in search_index.match_keys(h.session_id)
                }
                for s in sessions:
                    if not s.session_id:
                        continue
                    hit = next(
                        (
                            hit_by_key[k]
                            for k in search_index.match_keys(s.session_id)
                            if k in hit_by_key
                        ),
                        None,
                    )
                    if hit is not None:
                        search_ids.add(s.session_id)
                        snippets[s.session_id] = hit.snippet
                sessions.extend(search_index.resolve_hit_sessions(hits, sessions))
                for h in hits:
                    if h.session_id:
                        snippets.setdefault(h.session_id, h.snippet)
        sessions = SessionQuery(
            search=search,
            search_ids=frozenset(search_ids) if search_ids else None,
            agent=agent,
            cwd=directory_path,
            limit=limit,
        ).apply(sessions)

    if not sessions:
        criteria = f"limit={limit}, agent={agent}, directory_path={directory_path}"
        if search:
            criteria += f", search={search!r}"
        return f"No sessions found matching criteria ({criteria})."

    result = ["LAST ACTIVE | AGENT | DIRECTORY | TITLE"]
    result.append("-" * 80)

    now = time.time()

    for s in sessions:
        title = s.title if s.title else "-"
        result.append(f"{relative_time(now - (s.timestamp / 1000))} | {s.agent} | {s.path} | {title}")
        snippet = snippets.get(s.session_id)
        if snippet:
            result.append(f"    match: {' '.join(snippet.split())}")

    return "\n".join(result)

if __name__ == "__main__":
    mcp.run()
