"""Where the web app's files live, and which names the hub may serve."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# A flat name with one known extension: no slashes, no dot segments, so
# a request can never reach outside STATIC_DIR.
_ASSET_NAME = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:html|js|css|svg|png|ico|webmanifest)$")


def is_asset_name(name: str) -> bool:
    return bool(_ASSET_NAME.match(name)) and (STATIC_DIR / name).is_file()
