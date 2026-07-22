"""Rate limit fetchers for AI coding CLIs.

Pluggable architecture: each provider implements a fetch function that
returns a ``RateLimitInfo`` dataclass (or ``None`` if unavailable).
``get_all_rate_limits`` aggregates across all registered fetchers with
a short disk cache (60s TTL, same pattern as session cache).

Currently supported:
  - Codex   (ChatGPT backend-api wham/usage, OAuth from ~/.codex/auth.json)
  - Copilot (api.github.com/copilot_internal/user, OAuth via `gh` CLI token)

Both endpoints are internal/undocumented — they work today because the
official CLIs use them and they happen to be queryable. They can change
without notice. The same SSL-fallback logic that handles macOS python.org
builds without CA certificates applies to both.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
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
    plan_type: str             # e.g. "plus", "individual/edu", "—"
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


# ---------------------------------------------------------------------------
# HTTP helper with macOS-SSL fallback
# ---------------------------------------------------------------------------

def _fetch_json(req: urllib.request.Request, timeout: int = 5) -> dict | None:
    """Fetch JSON from a URL, with an SSL fallback for macOS python.org builds.

    Python 3.12+ from python.org on macOS ships without system CA
    certificates until the user runs "Install Certificates.command".
    This causes ``urlopen`` to fail with ``SSL: CERTIFICATE_VERIFY_FAILED``.
    As a pragmatic fallback (the connection is still encrypted, just
    without server-cert pinning), retry with an unverified SSL context.

    IMPORTANT: ``urllib.error.URLError`` is a subclass of ``OSError``, so
    it must be caught before ``OSError`` in the except chain or the SSL
    retry handler becomes dead code.
    """
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
# Codex fetcher
# ---------------------------------------------------------------------------

def _register_codex() -> None:
    """Register the Codex rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_codex()

    register("codex", fetch)


def _fetch_codex() -> RateLimitInfo | None:
    """Fetch Codex rate limits from the ChatGPT backend-api.

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
        # Codex's API returns reset_at as a numeric epoch today, but
        # fall back to ISO 8601 via the shared helper in case the
        # field ever arrives as a string. This also benefits from the
        # helper's Python 3.10 microseconds+offset support.
        reset_at = _parse_iso8601_to_epoch(reset_at)
    elif isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool):
        # ``bool`` subclasses ``int`` in Python — guard explicitly so a
        # pathological ``reset_at: True`` payload can't silently become
        # ``1.0``.
        reset_at = float(reset_at)
    else:
        reset_at = 0.0
    window_seconds = primary.get("limit_window_seconds", 0)
    plan_type = data.get("plan_type") or "—"

    return RateLimitInfo(
        tool_name="codex",
        used_percent=used_percent,
        limit_reached=limit_reached,
        # Branches above already guarantee `reset_at` is float (or
        # ``0.0``); no further coercion needed. The previous form
        # ``float(reset_at) if reset_at else 0`` was a leftover
        # defensive cast from the old inline-parser era and silently
        # collapsed ``0.0`` to ``0`` because ``0.0`` is falsy.
        reset_at=reset_at,
        plan_type=plan_type,
        window_seconds=window_seconds,
    )


# ---------------------------------------------------------------------------
# GitHub Copilot fetcher
# ---------------------------------------------------------------------------

def _register_github_copilot() -> None:
    """Register the GitHub Copilot rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_github_copilot()

    register("copilot", fetch)


def _get_gh_auth_token() -> str | None:
    """Return GitHub OAuth token from ``gh auth token``, or None.

    Silently returns None if ``gh`` is missing, not authenticated, or the
    subprocess fails for any reason.
    """
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _derive_copilot_fields(premium: dict) -> tuple[int, bool]:
    """Map a Copilot ``premium_interactions`` snapshot to
    ``(used_percent, limit_reached)`` suitable for ``RateLimitInfo``.

    Copilot's snapshot shape::

        {"percent_remaining": 88.5, "unlimited": false,
         "entitlement": 1500, "credits_used": 173,
         "has_quota": true}

    We invert ``percent_remaining`` to ``used_percent``. Values below 0
    (over quota) are clamped to 100 — the red color + ``limit_reached``
    conveys the overage; the on-screen percentage is intentionally
    capped at 100 to avoid misleading "101% used" displays.

    Pure function: does NOT mutate plan_type or interpret the access_sku.
    The caller decorates ``plan_type`` separately — that keeps this helper
    single-purpose and trivially testable.

    Defensive against malformed payloads: any non-numeric
    ``percent_remaining`` falls back to (0, False) rather than crashing
    ``swe list`` mid-render. ``None``/missing is treated as "no data"
    rather than masked as "100% remaining".
    """
    if bool(premium.get("unlimited")):
        return (0, False)

    raw = premium.get("percent_remaining")
    if raw is None:
        # "Don't know" — render as 0% but still honour entitlement/<has_quota>
        used_percent = 0
    else:
        try:
            used_raw = int(round(100 - float(raw)))
        except (TypeError, ValueError):
            # Malformed (e.g. string with "%" suffix). Don't crash.
            used_percent = 0
        else:
            used_percent = max(0, min(100, used_raw))

    has_quota = bool(premium.get("has_quota", True))
    try:
        entitlement = int(premium.get("entitlement", 0) or 0)
    except (TypeError, ValueError):
        entitlement = 0
    limit_reached = (not has_quota) or (entitlement > 0 and used_percent >= 100)

    return (used_percent, limit_reached)


def _decorate_copilot_plan_type(plan_type: str, access_sku: str) -> str:
    """Append ``/edu`` when the SKU signals an educational quota.

    Returns the original ``plan_type`` unchanged otherwise. Surfaces the
    educational tier in the CLI so users can tell a free educational
    account from a paid Copilot Pro plan at a glance.
    """
    if access_sku and "educational" in access_sku.lower() and plan_type == "individual":
        return "individual/edu"
    return plan_type


def _parse_iso8601_to_epoch(value) -> float:
    """Parse an ISO 8601 timestamp string into an epoch float, or 0.0.

    Compatible with Python 3.10+, which doesn't accept fractional
    seconds combined with a timezone offset in ``datetime.fromisoformat``
    (that was added in 3.11). Returns 0.0 for any unparseable or falsy
    input so a bad timestamp never breaks the whole ``swe list`` run.

    Naïve timestamps (no timezone designator) are treated as UTC to
    keep semantics consistent with offset-bearing variants — otherwise
    ``.timestamp()`` would silently apply local time and produce
    incorrect reset countdowns depending on the user's TZ.

    Examples that must parse successfully (all yielding 1785542400)::

        2026-08-01T00:00:00.000Z          # microseconds + Z (live API)
        2026-08-01T00:00:00Z              # no fractional
        2026-08-01T00:00:00+00:00         # explicit offset, naive base
        2026-08-01T00:00:00.123+00:00     # microseconds + offset (3.10+)
        2026-08-01T00:00:00               # naive (treated as UTC)
    """
    if not value:
        return 0.0
    s = str(value).strip()
    if not s:
        return 0.0

    def _to_epoch_utc(raw: str) -> float:
        dt = datetime.datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()

    # Normalise trailing 'Z' to '+00:00' so 3.11+ parses natively and
    # the fallback path below only kicks in when truly required.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        return _to_epoch_utc(s)
    except ValueError:
        pass

    # Python 3.10 fallback: when the string has fractional seconds AND a
    # timezone offset, strip the fractional part and retry. The date
    # hyphens occupy positions 4 and 7 — the first sign character at
    # position >= 10 is the genuine tz separator.
    if "." not in s:
        return 0.0
    tail = s[10:]
    if "+" not in tail and "-" not in tail:
        return 0.0
    for sep_pos in range(len(s) - 1, 9, -1):
        if s[sep_pos] not in ("+", "-"):
            continue
        if "T" not in s[:sep_pos]:
            continue
        head_no_ms, _, _ = s[:sep_pos].partition(".")
        try:
            return _to_epoch_utc(head_no_ms + s[sep_pos:])
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _fetch_github_copilot() -> RateLimitInfo | None:
    """Fetch GitHub Copilot premium-interaction quota.

    Uses the OAuth token returned by ``gh auth token`` (the user must
    already be authenticated to github.com) to query the GitHub internal
    endpoint::

        GET https://api.github.com/copilot_internal/user

    Returns ``None`` if:
      - ``gh`` CLI is not installed
      - ``gh`` is not authenticated
      - any HTTP/parse error occurs

    The endpoint is undocumented and may change without notice. Same
    fragility assumption as the Codex fetcher.

    Note that ``Editor-*`` headers impersonate the official VS Code
    Copilot Chat client — the endpoint gates access on those exact
    values. ``User-Agent: quiver/...`` alone would be rejected with
    403. Outside of the ``User-Agent``, the request is wired to look
    like the official client; the ``User-Agent`` itself is set to
    ``quiver`` for traceability.
    """
    token = _get_gh_auth_token()
    if not token:
        return None

    url = "https://api.github.com/copilot_internal/user"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Editor-Version": "vscode/1.95.0",
            "Editor-Plugin-Version": "copilot-chat/0.26.7",
            "User-Agent": "quiver/0.2.7",
        },
    )

    data = _fetch_json(req, timeout=10)
    if data is None:
        return None

    plan_type = str(data.get("copilot_plan") or "—")
    access_sku = str(data.get("access_type_sku") or "")
    plan_type = _decorate_copilot_plan_type(plan_type, access_sku)

    reset_at = _parse_iso8601_to_epoch(
        data.get("quota_reset_date_utc") or data.get("quota_reset_date")
    )

    # Focus on premium_interactions — this is the only quota that
    # actually limits individual Copilot users. (chat and completions
    # are typically unlimited on paid plans.) When the snapshot is
    # missing entirely, _derive_copilot_fields({}) returns (0, False)
    # which is exactly the right "no data" display state.
    snapshots = data.get("quota_snapshots") or {}
    premium = snapshots.get("premium_interactions") or {}
    used_percent, limit_reached = _derive_copilot_fields(premium)

    return RateLimitInfo(
        tool_name="copilot",
        used_percent=used_percent,
        limit_reached=limit_reached,
        reset_at=reset_at,
        plan_type=plan_type,
        window_seconds=0,
    )


# Register built-in fetchers at import time
_register_codex()
_register_github_copilot()


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
