"""Allow ``python -m swe.mcp``."""

from swe.mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
