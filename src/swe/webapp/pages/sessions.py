"""Sessions page writes: ``POST /v1/app/sessions/<action>``.

Reads stay with ``swe.webapp.api``'s sessions builder (the GET route
is claimed before the page dispatch); this module exists for the one
write the page needs: opening a local terminal on the hub machine's tmux
pane.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from swe.webapp.pages import PageError

# tmux session names are ``qv-`` + hex by construction; the match is a
# belt for whatever a registry file may hold, so the AppleScript line
# can't smuggle a quote.
_TMUX_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _tmux_for(home: Path | None, key: str) -> str | None:
    """The supervised or kept tmux session name for ``key``, else None."""
    from swe.remote import supervise

    meta = supervise.meta_for(home, key) or {}
    if meta.get("mode") == "tmux" and meta.get("tmux"):
        return str(meta["tmux"])
    kept = supervise.kept(home).get(key) or {}
    return str(kept.get("tmux") or "") or None


def post(ctx, action: str, body: dict) -> dict:
    if action != "open_terminal":
        raise PageError(404, "not_found", "unknown page action")
    if sys.platform != "darwin":
        raise PageError(
            422, "unsupported",
            "opening a terminal is only wired for macOS",
        )
    name = _tmux_for(ctx.home, str(body.get("key") or ""))
    if not name or not _TMUX_NAME.match(name):
        raise PageError(
            409, "no_tmux", "this session has no tmux pane to open")
    # LaunchPlanner's .terminal path: type the attach line into a fresh
    # Terminal window, as the Mac app's terminal launches do.
    try:
        proc = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e", 'tell application "Terminal"',
                "-e", "activate",
                "-e", f'do script "tmux attach -t {name}"',
                "-e", "end tell",
            ],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise PageError(
            504, "open_timeout",
            "Terminal took too long to answer — check for a "
            "permission dialog, then try again") from None
    if proc.returncode != 0:
        raise PageError(
            502, "open_failed",
            (proc.stderr or "Terminal did not open").strip()[:200])
    return {"ok": True, "tmux": name}
