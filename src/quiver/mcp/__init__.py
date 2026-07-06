"""MCP server sync subsystem."""


def main(argv=None):
    from quiver.mcp.cli import main as _cli_main

    return _cli_main(argv)


__all__ = ["main"]
