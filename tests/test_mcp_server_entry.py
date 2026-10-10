"""``python -m quiver.mcp_server`` must start the server, not just import it."""

import runpy
import unittest
from unittest import mock


def _have_fastmcp():
    # mcp 2.x still ships mcp.server.fastmcp but raises on import, so probe by importing.
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
    except Exception:
        return False
    return True


HAVE_MCP = _have_fastmcp()


@unittest.skipUnless(HAVE_MCP, "FastMCP (quiver[server]) not installed")
class TestMcpServerShim(unittest.TestCase):
    def test_running_shim_as_main_starts_the_server(self):
        from quiver.mcp import server

        with mock.patch.object(server.mcp, "run") as run:
            runpy.run_module("quiver.mcp_server", run_name="__main__")
        run.assert_called_once_with()

    def test_importing_shim_does_not_start_the_server(self):
        from quiver.mcp import server

        with mock.patch.object(server.mcp, "run") as run:
            runpy.run_module("quiver.mcp_server", run_name="quiver.mcp_server")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
