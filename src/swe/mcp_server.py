"""Backward-compatible entry (prefer ``python -m swe.mcp.server``)."""

from swe.mcp.server import *  # noqa: F403
from swe.mcp.server import mcp

if __name__ == "__main__":
    mcp.run()
