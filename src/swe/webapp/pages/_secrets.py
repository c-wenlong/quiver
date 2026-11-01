"""How the web app shows a stored secret: masked as the Mac app's
``APIKeyMask`` masks it, so a key reads the same on both. The full value
leaves the hub only when the page asks for it (Reveal or Copy), never in a
page payload."""

from __future__ import annotations

DOTS = "••••••••"


def mask(value: str) -> str:
    """``APIKeyMask.mask``: empty stays empty, under 16 characters is dots
    alone, else the vendor prefix (up to the last dash in the first eight),
    dots, and the last four."""
    if not value:
        return ""
    if len(value) < 16:
        return DOTS
    head = value[:8]
    dash = head.rfind("-")
    return (head[:dash + 1] if dash >= 0 else "") + DOTS + value[-4:]
