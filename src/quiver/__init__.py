"""quiver — central manager for AI coding CLI tools.

The user-facing command is ``swe`` (see ``CLI_NAME``). To rename the project,
see the "Renaming" section of the README; the values below are the single
source of truth for the command name and the on-disk data directory.
"""

__version__ = "0.2.9"

# User-facing CLI command name (the console entry point registered in
# pyproject.toml). Kept as "swe" to preserve muscle memory.
CLI_NAME = "swe"

# Name of the single root directory quiver owns, at ~/.<DATA_DIR_NAME>. It
# holds the shared AGENTS.md and skills/ that harnesses symlink to, plus
# quiver's own config and cache. Named for the project rather than the command
# because the directory is what other tools link into and read; "swe" is only
# what you type.
DATA_DIR_NAME = "quiver"

# Deprecated alias. Was the directory name under ~/.config before 0.2.7.
CONFIG_DIR_NAME = DATA_DIR_NAME

__all__ = ["__version__", "CLI_NAME", "DATA_DIR_NAME", "CONFIG_DIR_NAME"]
