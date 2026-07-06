#!/usr/bin/env python3
"""Optional FastMCP server exposing recent AI sessions as an MCP tool.

Requires the optional ``mcp`` dependency: ``pip install quiver[server]``.
"""
import sys

from quiver.sessions.aggregator import get_all_sessions

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "The 'mcp' package is required to run the MCP server. "
        "Please run: pip install quiver[server]",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = FastMCP("SWE Agent History")

@mcp.tool()
def get_recent_sessions(limit: int = 10, agent: str = None, directory_path: str = None) -> str:
    """
    Retrieve the most recent AI coding agent sessions across the entire system.
    Returns a formatted text table of sessions including timestamps, agent name, project directory, and summary/title.
    
    Args:
        limit: Number of sessions to return (default 10)
        agent: Filter by a specific agent (e.g., 'claude', 'opencode', 'gemini', 'antigravity')
        directory_path: Filter to only show sessions that occurred within this absolute directory path or its subdirectories.
    """
    sessions = get_all_sessions(limit=limit, agent=agent, cwd=directory_path)
    
    if not sessions:
        return f"No sessions found matching criteria (limit={limit}, agent={agent}, directory_path={directory_path})."
        
    result = ["LAST ACTIVE | AGENT | DIRECTORY | TITLE"]
    result.append("-" * 80)
    
    import time
    now = time.time()
    
    for s in sessions:
        diff = (now - (s.timestamp / 1000))
        if diff < 60:
            t_str = "Just now"
        elif diff < 3600:
            t_str = f"{int(diff/60)}m ago"
        elif diff < 86400:
            t_str = f"{int(diff/3600)}h ago"
        else:
            t_str = f"{int(diff/86400)}d ago"
            
        title = s.title if s.title else "-"
        
        result.append(f"{t_str} | {s.agent} | {s.path} | {title}")
        
    return "\n".join(result)

if __name__ == "__main__":
    mcp.run()
