"""Compatibility entry: ``python -m quiver.mcp_server`` runs the MCP server."""

from swe.mcp.server import *  # noqa: F403
from swe.mcp.server import mcp

if __name__ == "__main__":
    mcp.run()
