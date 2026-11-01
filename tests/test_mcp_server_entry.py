"""``python -m swe.mcp_server`` must start the server, not just import it."""

import importlib
import runpy
import sys
import types
import unittest
from unittest import mock


def _have_fastmcp():
    # mcp 2.x still ships mcp.server.fastmcp but raises on import, so probe by importing.
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
    except Exception:
        return False
    return True


class _FakeFastMCP:
    """Stands in for FastMCP so the shim can be exercised without the extra."""

    run = mock.Mock()

    def __init__(self, name):
        self.name = name

    def tool(self):
        return lambda fn: fn


def _fake_mcp_modules():
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = _FakeFastMCP
    server = types.ModuleType("mcp.server")
    server.fastmcp = fastmcp
    pkg = types.ModuleType("mcp")
    pkg.server = server
    return {"mcp": pkg, "mcp.server": server, "mcp.server.fastmcp": fastmcp}


class TestMcpServerShimWithoutExtra(unittest.TestCase):
    """Runs everywhere: the core CI jobs do not install the ``server`` extra."""

    def setUp(self):
        _FakeFastMCP.run.reset_mock()
        patcher = mock.patch.dict(sys.modules, _fake_mcp_modules())
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("swe.mcp.server", "swe.mcp_server"):
            sys.modules.pop(name, None)

    def test_running_shim_as_main_starts_the_server(self):
        runpy.run_module("swe.mcp_server", run_name="__main__")
        _FakeFastMCP.run.assert_called_once_with()

    def test_importing_shim_does_not_start_the_server(self):
        runpy.run_module("swe.mcp_server", run_name="swe.mcp_server")
        _FakeFastMCP.run.assert_not_called()



class TestMcpServerMissingExtra(unittest.TestCase):
    def test_the_install_hint_never_names_the_pypi_package(self):
        # PyPI's "quiver" is someone else's project: `pip install
        # quiver[server]` would install it, not the mcp dependency.
        import contextlib
        import io

        err = io.StringIO()
        with mock.patch.dict(sys.modules, {"mcp": None, "mcp.server": None,
                                           "mcp.server.fastmcp": None}):
            sys.modules.pop("swe.mcp.server", None)
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                importlib.import_module("swe.mcp.server")
        sys.modules.pop("swe.mcp.server", None)
        self.assertIn('pip install -e ".[server]"', err.getvalue())
        self.assertNotIn("pip install quiver[server]", err.getvalue())

@unittest.skipUnless(_have_fastmcp(), "FastMCP (quiver[server]) not installed")
class TestMcpServerShimWithFastMCP(unittest.TestCase):
    def test_running_shim_as_main_starts_the_real_server(self):
        # Not `from swe.mcp import server`: the stub tests can leave a stale package attribute.
        server = importlib.import_module("swe.mcp.server")

        with mock.patch.object(server.mcp, "run") as run:
            runpy.run_module("swe.mcp_server", run_name="__main__")
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
