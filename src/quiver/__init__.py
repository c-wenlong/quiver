"""Compatibility shim for the pre-rename package name.

The package was renamed ``quiver`` -> ``swe``; this module only keeps
``import quiver`` and ``python -m quiver.<entry>`` resolving for configs
written before the rename. Everything lives in ``swe`` now.
"""

from swe import CLI_NAME, CONFIG_DIR_NAME, DATA_DIR_NAME, __version__

__all__ = ["__version__", "CLI_NAME", "DATA_DIR_NAME", "CONFIG_DIR_NAME"]
