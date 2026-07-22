"""Rate limit fetchers for AI coding CLIs.

Pluggable architecture: each provider implements a fetch function that
returns a ``RateLimitInfo`` dataclass (or ``None`` if unavailable).
``get_all_rate_limits`` aggregates across all registered fetchers with
a short disk cache (60s TTL, same pattern as session cache).

Currently supported:
  - Codex (via ChatGPT backend-api wham/usage endpoint using OAuth tokens)
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Callable

from quiver.console import c
from quiver.paths import RATE_LIMITS_CACHE_FILE


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RateLimitInfo:
    """Normalised rate limit data for a single tool."""

    tool_name: str
    used_percent: int          # 0-100
    limit_reached: bool
    reset_at: float            # epoch seconds, 0 if unknown
    plan_type: str             # e.g. "plus", "pro", "—"
    window_seconds: int        # e.g. 604800 for weekly, 0 if unknown

    @property
    def reset_in_human(self) -> str:
        """Human-readable reset countdown (e.g. '5d12h', '3h45m')."""
        if self.reset_at <= 0:
            return "—"
        remaining = self.reset_at - time.time()
        if remaining <= 0:
            return "now"
        days = int(remaining // 86400)
        hours = int((remaining % 86400) // 3600)
        minutes = int((remaining % 3600) // 60)
        if days > 0:
            return f"{days}d{hours}h"
        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"

    def format_column(self) -> str:
        """One-line display string for the ``swe list`` column."""
        pct = f"{self.used_percent}%"
        if self.limit_reached:
            pct_str = c("red", pct)
        elif self.used_percent >= 80:
            pct_str = c("yellow", pct)
        else:
            pct_str = c("green", pct)
        reset = self.reset_in_human
        return f"{pct_str} {c('dim', reset)}"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

_CACHE_TTL = 60.0  # seconds — same as session cache

# Registry: tool_name → fetcher callable
RateLimitFetcher = Callable[[], RateLimitInfo | None]
_FETCHERS: dict[str, RateLimitFetcher] = {}


def register(tool_name: str, fetcher: RateLimitFetcher) -> None:
    """Register a rate limit fetcher for a tool."""
    _FETCHERS[tool_name] = fetcher


def _register_codex() -> None:
    """Register the Codex rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_codex()

    register("codex", fetch)


def _fetch_codex() -> RateLimitInfo | None:
    """Fetch Codex rate limits from ChatGPT backend-api.

    Uses the OAuth ``access_token`` from ``~/.codex/auth.json`` to query
    ``https://chatgpt.com/backend-api/wham/usage``.
    """
    auth_path = os.path.expanduser("~/.codex/auth.json")
    if not os.path.exists(auth_path):
        return None

    try:
        with open(auth_path) as f:
            auth = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token")
    if not access_token:
        return None

    url = "https://chatgpt.com/backend-api/wham/usage"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "codex-cli",
        },
    )

    data = _fetch_json(req)
    if data is None:
        return None

    rate_limit = data.get("rate_limit") or {}
    primary = rate_limit.get("primary_window") or {}

    used_percent = primary.get("used_percent", 0)
    limit_reached = rate_limit.get("limit_reached", False)
    reset_at = primary.get("reset_at", 0)
    if isinstance(reset_at, str):
        # ISO 8601 fallback — parse to epoch
        try:
            import datetime
            reset_at = datetime.datetime.fromisoformat(
                reset_at.replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError):
            reset_at = 0
    window_seconds = primary.get("limit_window_seconds", 0)
    plan_type = data.get("plan_type") or "—"

    return RateLimitInfo(
        tool_name="codex",
        used_percent=used_percent,
        limit_reached=limit_reached,
        reset_at=float(reset_at) if reset_at else 0,
        plan_type=plan_type,
        window_seconds=window_seconds,
    )


# Register built-in fetchers at import time
_register_codex()


def _fetch_json(req: urllib.request.Request, timeout: int = 5) -> dict | None:
    """Fetch JSON from a URL, with an SSL fallback for macOS python.org builds.

    Python 3.12+ from python.org on macOS ships without system CA certificates
    until the user runs "Install Certificates.command".  This causes
    ``urlopen`` to fail with ``SSL: CERTIFICATE_VERIFY_FAILED``.  As a
    pragmatic fallback (the connection is still encrypted, just without
    server-cert pinning), retry with an unverified SSL context.
    """
    # First attempt: normal SSL verification.
    # IMPORTANT: URLError is a subclass of OSError, so catch it first.
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        # SSL cert verification failure — retry with unverified context
        if not isinstance(exc.reason, ssl.SSLError):
            return None
    except ssl.SSLError:
        pass  # fall through to retry
    except (urllib.error.HTTPError, json.JSONDecodeError, OSError, TimeoutError):
        return None

    # Fallback: unverified SSL context (encrypted but no cert pinning)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            OSError, TimeoutError, ssl.SSLError):
        return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cached() -> dict[str, dict] | None:
    """Load rate limits from disk cache if fresh enough."""
    try:
        if not RATE_LIMITS_CACHE_FILE.exists():
            return None
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        cached_at = data.get("cached_at", 0)
        if time.time() - cached_at > _CACHE_TTL:
            return None
        return data.get("limits", {})
    except Exception:
        return None


def _save_cached(limits: dict[str, dict]) -> None:
    """Persist rate limits to disk cache."""
    try:
        RATE_LIMITS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cached_at": time.time(), "limits": limits}
        with open(RATE_LIMITS_CACHE_FILE, "w") as f:
            json.dump(payload, f)
    except Exception:
        pass


def invalidate_cache() -> None:
    """Delete the rate limits cache file."""
    try:
        if RATE_LIMITS_CACHE_FILE.exists():
            RATE_LIMITS_CACHE_FILE.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_rate_limits(use_cache: bool = True) -> dict[str, RateLimitInfo]:
    """Fetch rate limits for all registered tools.

    Returns a dict mapping ``tool_name`` → ``RateLimitInfo``.
    Tools without a fetcher or whose fetch fails are omitted.
    """
    result: dict[str, RateLimitInfo] = {}

    if use_cache:
        cached = _load_cached()
        if cached is not None:
            for name, raw in cached.items():
                try:
                    result[name] = RateLimitInfo(**raw)
                except (TypeError, ValueError):
                    pass
            return result

    raw_cache: dict[str, dict] = {}
    for tool_name, fetcher in _FETCHERS.items():
        try:
            info = fetcher()
        except Exception:
            info = None
        if info:
            result[tool_name] = info
            raw_cache[tool_name] = asdict(info)

    if raw_cache:
        _save_cached(raw_cache)

    return result


def get_rate_limit(tool_name: str, use_cache: bool = True) -> RateLimitInfo | None:
    """Fetch rate limit for a single tool."""
    return get_all_rate_limits(use_cache=use_cache).get(tool_name)
