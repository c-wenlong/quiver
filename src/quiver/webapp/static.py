"""Where the web app's files live, and which names the hub may serve."""

from __future__ import annotations

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# A flat name with one known extension: no slashes, no dot segments, so
# a request can never reach outside STATIC_DIR.
_ASSET_NAME = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:html|js|css|svg|png|webmanifest)$")


def is_asset_name(name: str) -> bool:
    return bool(_ASSET_NAME.match(name)) and (STATIC_DIR / name).is_file()


# Other names the same harness goes by, as the iPhone app's
# HarnessIcon.aliases lists them (QuiverCore's HarnessIcons on the Mac).
_LOGO_ALIASES = {
    "claude-code": "claude", "claude-desktop": "claude",
    "github-copilot": "copilot", "factory": "droid", "qwen": "qwen-code",
    "mistral": "mistral-vibe", "codebuff": "freebuff",
    "command-code": "commandcode", "grok-bot": "grok",
}

_LOGO_PATH = re.compile(r"^/harness-([A-Za-z0-9_-]+)\.svg$")


def harness_logo(path: str) -> Path | None:
    """The bundled logo a ``/harness-<id>.svg`` request serves, picked as
    ``HarnessIcons.icon`` picks it: aliases resolve, colour wins. A stem
    ending ``-color`` means the colour variant only, so a client can
    tell mono from colour by which URL answers. ``None`` when the id has
    no logo — the harness's name is the caller's fallback then."""
    match = _LOGO_PATH.match(path)
    if match is None:
        return None
    stem = match.group(1).lower()
    if stem.endswith("-color"):
        hid = _LOGO_ALIASES.get(stem[:-6], stem[:-6])
        names = [f"harness-{hid}-color.svg"]
    else:
        hid = _LOGO_ALIASES.get(stem, stem)
        names = [f"harness-{hid}-color.svg", f"harness-{hid}.svg"]
    for name in names:
        if is_asset_name(name):
            return STATIC_DIR / name
    return None
