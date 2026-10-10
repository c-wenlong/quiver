"""Backward-compatible entry (prefer ``python -m quiver.mcp.server``)."""

from quiver.mcp.server import *  # noqa: F403
from quiver.mcp.server import mcp

if __name__ == "__main__":
    mcp.run()
