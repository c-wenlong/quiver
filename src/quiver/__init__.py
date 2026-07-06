"""quiver — central manager for AI coding CLI tools.

The user-facing command is ``swe`` (see ``CLI_NAME``). To rename the project,
see the "Renaming" section of the README; the values below are the single
source of truth for the command name and on-disk config directory.
"""

__version__ = "0.2.5"

# User-facing CLI command name (the console entry point registered in
# pyproject.toml). Kept as "swe" to preserve muscle memory.
CLI_NAME = "swe"

# Name of the config directory under ~/.config that holds the tool registry
# (tools.json) and MCP source-of-truth (mcp.json). Kept separate from CLI_NAME
# so the two can diverge if desired.
CONFIG_DIR_NAME = "swe"

__all__ = ["__version__", "CLI_NAME", "CONFIG_DIR_NAME"]
