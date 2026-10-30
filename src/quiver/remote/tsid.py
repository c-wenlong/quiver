"""Tailscale node identity: ``whois`` for a peer IP.

The hub asks Tailscale which node an HTTP peer is, and pairing uses
that identity for the approve-from-another-device rule. Any failure —
no binary, non-zero exit, unparseable JSON — is ``None``, never an
exception.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

# App-store Tailscale has no CLI on PATH; the binary lives in the .app.
_APPSTORE_EXE = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def tailscale_exe() -> str | None:
    """The Tailscale CLI: on PATH, else inside Tailscale.app."""
    found = shutil.which("tailscale")
    if found:
        return found
    return str(_APPSTORE_EXE) if _APPSTORE_EXE.exists() else None


def whois(ip: str) -> dict | None:
    """Node identity for ``ip``, or None when it isn't a tailnet peer."""
    exe = tailscale_exe()
    if exe is None:
        return None
    try:
        cp = subprocess.run(
            [exe, "whois", "--json", ip],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    node = data.get("Node") or {}
    profile = data.get("UserProfile") or {}
    tagged = bool(node.get("Tags"))
    name = str(node.get("Name") or "").rstrip(".")
    return {
        "node_name": name,
        "node_short": name.split(".", 1)[0] if name else "",
        "node_id": str(node.get("StableID") or ""),
        # A tagged node belongs to no user — its UserProfile names the
        # tag's creator, not a device owner to trust.
        "login": "" if tagged else str(profile.get("LoginName") or ""),
        "display_name": str(profile.get("DisplayName") or ""),
        "tagged": tagged,
    }
