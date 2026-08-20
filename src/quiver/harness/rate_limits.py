"""Rate limit fetchers for AI coding CLIs.

Pluggable architecture: each provider implements a fetch function that
returns a ``RateLimitInfo`` dataclass (or ``None`` if unavailable).
``get_all_rate_limits`` aggregates across all registered fetchers with
a short disk cache (60s TTL, same pattern as session cache).

Currently supported:
  - Codex       (ChatGPT backend-api wham/usage, OAuth from ~/.codex/auth.json)
  - Copilot     (api.github.com/copilot_internal/user, OAuth via `gh` CLI token)
  - Claude      (api.anthropic.com/api/oauth/usage, OAuth from Claude Code creds)
  - Droid       (api.factory.ai/api/billing/limits, FACTORY_API_KEY / keychain)
  - Antigravity (running app/CLI's loopback RetrieveUserQuotaSummary RPC)
  - Freebuff    (codebuff.com free-session status, local Freebuff auth token)

These interfaces are internal/undocumented — they work today because the
official CLIs use them and they happen to be queryable. They can change
without notice. The same SSL-fallback logic that handles macOS python.org
builds without CA certificates applies to the remote HTTP fetchers.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import math
import os
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from typing import Callable, Iterable

from quiver import __version__
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
    window: str = ""           # e.g. "5h"/"7d"/"7d_sonnet" — Claude returns
                                # 3 windows in one request; we surface the
                                # most-restrictive (highest utilization) and
                                # label it via this short abbreviation in
                                # format_column(). Empty for Codex / Copilot
                                # (single-window response). Default keeps
                                # all existing call sites + cache
                                # reconstruction (RateLimitInfo(**raw))
                                # backward-compatible.
    remaining_units: float | None = None
    total_units: float | None = None

    @property
    def remaining_percent(self) -> int:
        """Percentage of the selected quota window still available."""
        return max(0, min(100, 100 - self.used_percent))

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
        # Sentinel plan type for tools whose rate-limit endpoint is
        # intentionally not polled (e.g. Claude with no subscription).
        # Renders a compact label instead of a misleading "0% —" figure.
        if self.plan_type == "no-sub":
            return c("dim", "no-sub")
        if self.plan_type == "auth-required":
            return c("red", "re-login")
        remaining = self.remaining_percent
        if self.remaining_units is not None and self.total_units is not None:
            def compact(value: float) -> str:
                return (
                    str(int(value)) if value.is_integer()
                    else f"{value:.1f}".rstrip("0").rstrip(".")
                )

            pct = (
                f"{compact(float(self.remaining_units))}/"
                f"{compact(float(self.total_units))}"
            )
        else:
            pct = f"{remaining}%"
        if self.limit_reached or remaining == 0:
            pct_str = c("red", pct)
        elif remaining <= 20:
            pct_str = c("yellow", pct)
        else:
            pct_str = c("green", pct)
        reset = self.reset_in_human
        if self.window:
            # Compact form (`5h`, `7d`, `7ds`) keeps the column width
            # budget intact while letting the user tell which window
            # the figure came from. Examples:
            #     20% 5h:3h12m  ← most-restrictive is the 5h rolling
            #      9% 7d:4d3h   ← most-restrictive is the weekly
            return f"{pct_str} {c('dim', self.window + ':')} {c('dim', reset)}"
        return f"{pct_str} {c('dim', reset)}"


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _env_cache_ttl(default: float = 300.0) -> float:
    """Resolve the rate-limits cache TTL, honouring an env-var override.

    ``SWE_RATE_LIMITS_TTL`` sets the TTL in seconds (e.g. ``600`` for 10
    minutes). Unset / unparseable / non-positive values fall back to
    ``default`` (300s = 5 minutes). Read once at import time so each
    ``swe`` process picks up the current value without runtime cost.
    """
    raw = os.environ.get("SWE_RATE_LIMITS_TTL")
    if raw:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return default
        return val if val > 0 else default
    return default


# Rate limits are fetched from undocumented per-provider endpoints that
# rate-limit aggressively (e.g. Anthropic 429s the usage endpoint). The
# 5-minute default keeps ``swe list`` fast and avoids spamming those
# endpoints on every invocation; ``swe list --refresh`` (or ``-r``/``-n``)
# bypasses the cache for a force-refetch. Override the TTL at runtime
# with ``SWE_RATE_LIMITS_TTL=<seconds>``.
_CACHE_TTL = _env_cache_ttl()

# Registry: tool_name → fetcher callable
RateLimitFetcher = Callable[[], RateLimitInfo | None]
_FETCHERS: dict[str, RateLimitFetcher] = {}


def register(tool_name: str, fetcher: RateLimitFetcher) -> None:
    """Register a rate limit fetcher for a tool."""
    _FETCHERS[tool_name] = fetcher


# ---------------------------------------------------------------------------
# HTTP helper with macOS-SSL fallback
# ---------------------------------------------------------------------------

def _parse_retry_after_to_seconds(ra_value) -> float | None:
    """Parse a ``Retry-After`` header value to seconds, or ``None``.

    Accepts RFC 7231 syntax — either non-negative integer seconds
    (e.g. ``"120"``) OR an IMF-fixdate HTTP-date (e.g.
    ``"Wed, 21 Oct 2026 07:28:00 GMT"``). Returns ``None`` for any
    unparseable input so callers degrade silently on servers that omit
    the header or emit non-RFC variants.

    Rejects non-string inputs outright (rather than coercing via
    ``str(...)``) so a typo like passing the int ``120`` instead of
    the string ``"120"`` doesn't silently round-trip into a
    misleading 120-second wait. ``b"120"`` etc are also rejected so
    callers don't accidentally hit the bytes-vs-str code path.

    The HTTP-date branch subtracts ``time.time()`` from the parsed
    timestamp so the result is "seconds until retry" rather than an
    absolute epoch — callers don't need to know the difference.
    """
    if not isinstance(ra_value, str):
        return None
    s = ra_value.strip()
    if not s:
        return None
    # Numeric seconds (RFC 7231 §7.1.3 first form).
    try:
        return max(0.0, float(s))
    except (TypeError, ValueError):
        pass
    # IMF-fixdate (RFC 7231 §7.1.3 second form).
    try:
        dt = datetime.datetime.strptime(
            s, "%a, %d %b %Y %H:%M:%S GMT",
        ).replace(tzinfo=datetime.timezone.utc)
        return max(0.0, dt.timestamp() - time.time())
    except (ValueError, TypeError):
        return None


def _fetch_json(
    req: urllib.request.Request,
    timeout: int = 5,
    on_401: Callable | None = None,
    on_http_error: Callable[[int, float | None], None] | None = None,
) -> dict | None:
    """Fetch JSON from a URL, with an SSL fallback for macOS python.org builds.

    Python 3.12+ from python.org on macOS ships without system CA
    certificates until the user runs "Install Certificates.command".
    This causes ``urlopen`` to fail with ``SSL: CERTIFICATE_VERIFY_FAILED``.
    As a pragmatic fallback (the connection is still encrypted, just
    without server-cert pinning), retry with an unverified SSL context.

    IMPORTANT: ``urllib.error.URLError`` is a subclass of ``OSError``, so
    it must be caught before ``OSError`` in the except chain or the SSL
    retry handler becomes dead code.

    Diagnostic callbacks (fired exactly once each before ``None`` return):

    - ``on_401()`` — server replied with HTTP 401. Use cases: stale
      ``anthropic-beta`` for Claude, or an expired/invalid token from
      ``Factory Safe Storage`` for Droid. 401 is NOT routed through
      ``on_http_error`` because it has historically meant different
      things and we want callers to be able to swap their hint without
      touching the generic path.
    - ``on_http_error(code: int, retry_after_seconds: float | None)`` —
      server replied with any other HTTP error (403, 404, 429, 5xx).
      The ``code`` and parsed ``Retry-After`` value let the caller
      produce an actionable one-liner (e.g. "rate-limited, retry in
      235s" or "403 — endpoint may have moved"). ``retry_after_seconds``
      is ``None`` when the server omits the header.

    Callbacks that raise are swallowed — a diagnostic bug must never
    flip the result away from ``None``. Network errors do not trigger
    either callback. HTTP errors from the SSL fallback do.
    """
    def handle_http_error(exc: urllib.error.HTTPError) -> None:
        if exc.code == 401 and on_401 is not None:
            try:
                on_401()
            except Exception:
                pass
        elif on_http_error is not None and exc.code != 401:
            try:
                retry_after = None
                if exc.headers is not None:
                    retry_after = _parse_retry_after_to_seconds(
                        exc.headers.get("Retry-After"),
                    )
                on_http_error(exc.code, retry_after)
            except Exception:
                pass

    try:
        # Mitigate SSRF/LFI by restricting allowed URL schemes
        if not req.full_url.lower().startswith(("http://", "https://")):
            return None
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
        return None
    except urllib.error.URLError as exc:
        # SSL cert verification failure — retry with unverified context
        if not isinstance(exc.reason, ssl.SSLError):
            return None
    except ssl.SSLError:
        pass  # fall through to retry
    except (json.JSONDecodeError, OSError, TimeoutError):
        return None

    # Fallback: unverified SSL context (encrypted but no cert pinning)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError, TimeoutError,
            ssl.SSLError):
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
            "User-Agent": f"quiver/{__version__}",
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


# ---------------------------------------------------------------------------
# Claude Code fetcher
# ---------------------------------------------------------------------------

# Dated beta-header version baked into the OAuth usage request. Anthropic
# bumps this string periodically without documentation; a 401 from the
# endpoint is the canonical signal that the version is stale and the
# string below needs to be updated. Kept as a `dict` so future versions
# can fall through intelligently — for now we rely on a human picking
# the new value when they see a 401. We do NOT auto-rotate because
# Anthropic doesn't publish new values and a stale auto-rotation would
# mask real auth failures.
_BETA_VERSIONS: dict[str, str] = {
    "claude-oauth-usage": "oauth-2025-04-20",
}

# Compact abbreviations for the three Claude windows coming off the wire
# (`five_hour`, `seven_day`, `seven_day_sonnet`). `format_column()`
# surfaces the most-restrictive window's abbreviation so users can tell
# the 5h from the 7d at a glance inside the 14-char pre-padded column.
_CLAUDE_WINDOWS: dict[str, str] = {
    "five_hour": "5h",
    "seven_day": "7d",
    "seven_day_sonnet": "7ds",
}


def _get_claude_oauth_credentials() -> dict | None:
    """Return Claude Code's OAuth credential mapping from the best source.

    Order of resolution (first hit wins):
      1. Portable file path — ``~/.claude/.credentials.json`` direct
         JSON file. Same shape as the macOS Keychain value and works
         on Linux / docker / WSL identically.
      2. macOS Keychain — ``security find-generic-password -l "Claude
         Code-credentials"`` returns the credential JSON as the
         password field.

    Returns ``None`` if neither source is reachable / parseable. Both
    paths silently degrade: a missing credentials file or missing
    ``security`` binary should never break ``swe list`` rendering.
    """
    # 1. Portable file path. Always tried first because it's free (no
    #    subprocess) and works identically on macOS + Linux + WSL.
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(creds_path):
        try:
            with open(creds_path) as f:
                creds = json.load(f)
        except (OSError, json.JSONDecodeError):
            creds = None
        if isinstance(creds, dict):
            oauth = creds.get("claudeAiOauth") or {}
            if isinstance(oauth, dict):
                if isinstance(oauth.get("accessToken"), str):
                    return oauth

    # 2. macOS Keychain. ``security`` ships with macOS; check via
    #    shutil.which to avoid FileNotFoundError on non-Apple platforms.
    if not shutil.which("security"):
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-l", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        creds = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(creds, dict):
        return None
    oauth = creds.get("claudeAiOauth") or {}
    if not isinstance(oauth, dict):
        return None
    return oauth if isinstance(oauth.get("accessToken"), str) else None


def _get_claude_access_token() -> str | None:
    """Return the access token from Claude Code's OAuth credentials."""
    oauth = _get_claude_oauth_credentials()
    if not oauth:
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _fetch_claude_url(
    req: urllib.request.Request,
    on_rate_limited: Callable[[float | None], None] | None = None,
) -> dict | None:
    """Claude-specific fetch — like ``_fetch_json`` but emits two
    targeted diagnostics:

    - HTTP 401 → suggests renewing Claude authentication first, then
      checking ``_BETA_VERSIONS`` if a fresh login still fails.
    - HTTP 429 → Anthropic is rate-limiting the endpoint itself;
      surfaces the parsed ``Retry-After`` so the user knows when to
      try again instead of debugging the wrong thing.
    """
    beta_header = _BETA_VERSIONS["claude-oauth-usage"]

    def _warn_beta_stale() -> None:
        # Print directly to stderr so the hint surfaces in CI logs and
        # interactive sessions alike. We avoid using the `quiver.console`
        # helpers / logging module so the diagnostic is unambiguously
        # stderr-bound and not accidentally stripped.
        import sys
        print(
            f"Claude usage endpoint returned 401. Run `claude auth login` "
            f"to renew the OAuth credential. If a fresh login still fails, "
            f"the `anthropic-beta` value (`{beta_header}`) may need updating.",
            file=sys.stderr,
        )

    def _warn_claude_429(code: int, retry_after_seconds: float | None) -> None:
        import sys
        if code == 429 and on_rate_limited is not None:
            on_rate_limited(retry_after_seconds)
        if retry_after_seconds and retry_after_seconds > 0:
            minutes = int(retry_after_seconds // 60)
            seconds = int(retry_after_seconds % 60)
            wait_str = (f"{minutes}m{seconds}s" if minutes
                        else f"{int(retry_after_seconds)}s")
        else:
            wait_str = "a few minutes"
        print(
            f"Claude usage endpoint returned 429 (Anthropic is "
            f"rate-limiting the endpoint itself, not your account). Try "
            f"`swe list --refresh` again in {wait_str}.",
            file=sys.stderr,
        )

    return _fetch_json(
        req,
        timeout=10,
        on_401=_warn_beta_stale,
        on_http_error=_warn_claude_429,
    )


def _claude_cache_file():
    return RATE_LIMITS_CACHE_FILE.with_name("claude_usage_cache.json")


def _claude_credential_fingerprint(token: str) -> str:
    """Return a non-reversible identifier for cooldown ownership."""
    return hashlib.sha256(token.encode()).hexdigest()


def _load_claude_state() -> tuple[RateLimitInfo | None, float, str]:
    try:
        raw = json.loads(_claude_cache_file().read_text())
        info_raw = raw.get("info")
        info = RateLimitInfo(**info_raw) if isinstance(info_raw, dict) else None
        retry_at = float(raw.get("retry_at") or 0)
        fingerprint = raw.get("credential_fingerprint") or ""
        return info, retry_at, str(fingerprint)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, 0.0, ""


def _save_claude_state(
    info: RateLimitInfo | None,
    retry_at: float = 0.0,
    credential_fingerprint: str = "",
) -> None:
    path = _claude_cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "info": asdict(info) if info is not None else None,
            "retry_at": retry_at,
            "credential_fingerprint": credential_fingerprint,
        }))
    except OSError:
        pass


def _fetch_claude() -> RateLimitInfo | None:
    """Return Claude Code's most limiting subscription usage window."""
    oauth = _get_claude_oauth_credentials()
    token = oauth.get("accessToken") if oauth else None
    if not token:
        return None

    expires_at = oauth.get("expiresAt")
    try:
        expires_at = float(expires_at)
        expires_at_seconds = (
            expires_at / 1000 if expires_at > 100_000_000_000 else expires_at
        )
    except (TypeError, ValueError):
        expires_at_seconds = 0.0
    if (
        expires_at_seconds
        and expires_at_seconds <= time.time()
        and not oauth.get("refreshToken")
    ):
        return RateLimitInfo(
            tool_name="claude",
            used_percent=0,
            limit_reached=False,
            reset_at=0.0,
            plan_type="auth-required",
            window_seconds=0,
        )

    credential_fingerprint = _claude_credential_fingerprint(token)
    stale_info, retry_at, cooldown_fingerprint = _load_claude_state()
    if (
        retry_at > time.time()
        and cooldown_fingerprint == credential_fingerprint
    ):
        return stale_info

    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _BETA_VERSIONS["claude-oauth-usage"],
            "Accept": "application/json",
            "User-Agent": f"quiver/{__version__}",
        },
    )
    retry_after: list[float | None] = []
    data = _fetch_claude_url(req, on_rate_limited=retry_after.append)
    if not isinstance(data, dict):
        if retry_after:
            wait = retry_after[-1] if retry_after[-1] is not None else 300.0
            _save_claude_state(
                stale_info,
                time.time() + max(60.0, wait),
                credential_fingerprint,
            )
        return stale_info

    best_window_key = ""
    best_utilization = -1.0
    best_reset_at = 0.0
    for key in _CLAUDE_WINDOWS:
        window = data.get(key)
        if not isinstance(window, dict):
            continue
        try:
            utilization = float(window.get("utilization"))
        except (TypeError, ValueError):
            continue
        if utilization > best_utilization:
            best_window_key = key
            best_utilization = utilization
            best_reset_at = _parse_iso8601_to_epoch(window.get("resets_at"))

    if best_utilization < 0:
        return None

    used_percent = int(round(min(100.0, max(0.0, best_utilization))))
    info = RateLimitInfo(
        tool_name="claude",
        used_percent=used_percent,
        limit_reached=best_utilization >= 100,
        reset_at=best_reset_at,
        plan_type="—",
        window_seconds=0,
        window=_CLAUDE_WINDOWS[best_window_key],
    )
    _save_claude_state(info, credential_fingerprint=credential_fingerprint)
    return info


def _register_claude() -> None:
    """Register the Claude Code rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_claude()

    register("claude", fetch)


# ---------------------------------------------------------------------------
# Droid (Factory AI) fetcher
# ---------------------------------------------------------------------------

_DROID_BASE_URL = "https://api.factory.ai/api/billing/limits"
_DROID_APP_ORIGIN = "https://app.factory.ai"
_DROID_APP_REFERER = "https://app.factory.ai/"


# ---------------------------------------------------------------------------
# AES-256-GCM decryption of ~/.factory/auth.v2.file via system libcrypto
# ---------------------------------------------------------------------------
#
# The droid CLI persists OAuth credentials in an encrypted pair:
#   ~/.factory/auth.v2.key  — base64(32-byte AES-256 key)
#   ~/.factory/auth.v2.file — base64(iv):base64(tag):base64(ciphertext)
# decrypted with AES-256-GCM. The plaintext is JSON with
# ``access_token`` / ``refresh_token`` / ``active_organization_id``.
#
# We decrypt via ``ctypes`` against the system libcrypto (the same one
# Python's own ``hashlib`` / ``ssl`` link against), so the CLI stays
# stdlib-only — no ``cryptography`` dependency. Two non-default GCM
# parameters: the IV is 16 bytes (GCM default is 12, so
# ``EVP_CTRL_GCM_SET_IVLEN`` must be called before key/iv init) and the
# 16-byte auth tag is set via ``EVP_CTRL_GCM_SET_TAG`` before the
# update step. ``EVP_CipherFinal_ex`` returning 1 means the tag
# verified; anything else is treated as a corrupt/wrong-key file.
#
# The libcrypto handle is resolved once and cached module-level; if no
# libcrypto is reachable the decryptor returns ``None`` and the fetcher
# falls back to the next rung of the auth ladder.

_LIBCRYPTO = None
_LIBCRYPTO_TRIED = False
_EVP_CTRL_GCM_SET_IVLEN = 0x9
_EVP_CTRL_GCM_SET_TAG = 0x11


def _load_libcrypto():
    """Locate and cache a libcrypto with the EVP AES-GCM entry points.

    Returns the ``ctypes.CDLL`` handle or ``None``.

    Resolution order (first loadable + symbol-present wins):

    1. macOS python.org framework-local ``libcrypto*.dylib`` — these
       ship INSIDE the Python framework and are signed by the same
       team as the Python binary, so they pass macOS library
       validation on hardened python.org builds. Tried FIRST because
       ``ctypes.util.find_library('crypto')`` on those builds resolves
       to homebrew's differently-signed ``libcrypto.dylib``, whose
       ``dlopen`` aborts the process with SIGABRT ("loading libcrypto
       in an unsafe way"). Framework paths are skipped automatically
       on homebrew / system Python (the glob returns nothing).
    2. ``ctypes.util.find_library('crypto')`` — cross-platform; on
       homebrew Python and Linux it returns a loadable, same-context
       libcrypto (no library validation on those runtimes).
    3. Common sonames (``libcrypto.3.dylib`` / ``libcrypto.so.3`` /
       ``libcrypto.so`` etc.).
    4. Explicit homebrew paths — last resort; may abort on hardened
       python.org builds but those are already handled by step 1.

    ``ctypes.CDLL(None)`` (process global namespace) is intentionally
    NOT used: on python.org 3.12 framework it resolves ``EVP_*`` to a
    different-ABI libcrypto already loaded by a system component and
    segfaults inside ``EVP_CipherInit_ex``.

    Function signatures are configured once on the cached handle.
    """
    global _LIBCRYPTO, _LIBCRYPTO_TRIED
    if _LIBCRYPTO_TRIED:
        return _LIBCRYPTO
    _LIBCRYPTO_TRIED = True
    import ctypes
    import ctypes.util
    import glob

    candidates: list[str] = []
    # 1. macOS python.org framework-local libcrypto (same-team signed).
    candidates.extend(sorted(
        glob.glob("/Library/Frameworks/Python.framework/Versions/*/lib/libcrypto*.dylib"),
        reverse=True,  # prefer newer versions / Current
    ))
    # 2. find_library — cross-platform.
    found = ctypes.util.find_library("crypto")
    if found:
        candidates.append(found)
    # 3. Common sonames.
    candidates.extend([
        "libcrypto.3.dylib", "libcrypto.dylib",
        "libcrypto.so.3", "libcrypto.so.1.1", "libcrypto.so",
        "/opt/homebrew/lib/libcrypto.dylib",
        "/usr/local/opt/openssl/lib/libcrypto.dylib",
    ])

    lib = None
    for cand in candidates:
        try:
            cand_lib = ctypes.CDLL(cand)
        except OSError:
            continue
        if hasattr(cand_lib, "EVP_aes_256_gcm") and hasattr(
            cand_lib, "EVP_CIPHER_CTX_new"
        ):
            lib = cand_lib
            break
    if lib is None:
        return None

    lib.EVP_aes_256_gcm.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_CipherInit_ex.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int,
    ]
    lib.EVP_CipherInit_ex.restype = ctypes.c_int
    lib.EVP_CIPHER_CTX_ctrl.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
    ]
    lib.EVP_CIPHER_CTX_ctrl.restype = ctypes.c_int
    lib.EVP_CipherUpdate.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int), ctypes.c_char_p, ctypes.c_int,
    ]
    lib.EVP_CipherUpdate.restype = ctypes.c_int
    lib.EVP_CipherFinal_ex.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
    ]
    lib.EVP_CipherFinal_ex.restype = ctypes.c_int
    _LIBCRYPTO = lib
    return lib


def _decrypt_droid_auth_file() -> str | None:
    """Decrypt ``~/.factory/auth.v2.file`` and return the ``access_token``.

    Returns ``None`` for any failure (missing files, missing libcrypto,
    bad base64, wrong key, failed auth-tag verification, unparseable
    JSON). Never raises — a corrupt credential file must never break
    ``swe list`` rendering; the caller falls back to the next auth
    ladder rung.
    """
    lib = _load_libcrypto()
    if lib is None:
        return None
    key_path = os.path.expanduser("~/.factory/auth.v2.key")
    file_path = os.path.expanduser("~/.factory/auth.v2.file")
    if not (os.path.exists(key_path) and os.path.exists(file_path)):
        return None
    try:
        with open(key_path, "rb") as f:
            raw_key = f.read().strip()
            cipher_bytes = base64.b64decode(raw_key)
        with open(file_path, "rb") as f:
            parts = f.read().strip().split(b":")
        if len(parts) != 3:
            return None
        iv, tag, ct = (base64.b64decode(p) for p in parts)
    except (OSError, ValueError):
        return None
    if len(cipher_bytes) != 32 or len(iv) == 0 or len(tag) == 0 or len(ct) == 0:
        return None

    import ctypes
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        return None
    pt = b""
    try:
        if lib.EVP_CipherInit_ex(ctx, lib.EVP_aes_256_gcm(), None, None, None, 0) != 1:
            return None
        if lib.EVP_CIPHER_CTX_ctrl(ctx, _EVP_CTRL_GCM_SET_IVLEN, len(iv), None) != 1:
            return None
        if lib.EVP_CipherInit_ex(ctx, None, None, cipher_bytes, iv, 0) != 1:
            return None
        tag_buf = ctypes.create_string_buffer(tag)
        if lib.EVP_CIPHER_CTX_ctrl(ctx, _EVP_CTRL_GCM_SET_TAG, len(tag), tag_buf) != 1:
            return None
        out = ctypes.create_string_buffer(len(ct) + 16)
        outl = ctypes.c_int(0)
        if lib.EVP_CipherUpdate(ctx, out, ctypes.byref(outl), ct, len(ct)) != 1:
            return None
        pt = out.raw[: outl.value]
        fin = ctypes.create_string_buffer(16)
        finl = ctypes.c_int(0)
        if lib.EVP_CipherFinal_ex(ctx, fin, ctypes.byref(finl)) != 1:
            return None  # auth-tag verification failed — wrong key / corrupt file
        pt += fin.raw[: finl.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)

    try:
        obj = json.loads(pt.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    tok = obj.get("access_token")
    return tok if isinstance(tok, str) and tok else None


def _get_droid_access_token() -> str | None:
    """Return the Droid (Factory AI) auth token from the first available source.

    Ladder (first hit wins, all silently degrade on failure):
      1. ``FACTORY_API_KEY`` env var — 12-factor override, perfect for
         CI / Daisy-disk testing / explicit "use this key" workflows.
         Checked before any subprocess/file I/O so a set env var
         completely bypasses the decryption + keychain round-trips.
      2. Decrypted ``~/.factory/auth.v2.file`` — the canonical browser-
         auth path used by the droid CLI itself. The file is
         AES-256-GCM encrypted with ``~/.factory/auth.v2.key``; we
         decrypt via ``ctypes`` against the system libcrypto (stdlib-
         only, no ``cryptography`` dependency). This is the path that
         actually works for users who signed in via `droid` login.
      3. macOS Keychain ``Factory Safe Storage`` then ``Factory Key``
         — last-resort fallback. NOTE: on modern droid installs the
         ``Factory Safe Storage`` value is an Electron ``safeStorage``
         encrypted blob, NOT a raw Bearer token, so this rung usually
         401s. It is retained for older installs that did store a raw
         token there.

    Returns ``None`` when no source yields a token. Never raises.
    """
    # 1. Env var — free, no subprocess, highest-priority user intent.
    env_token = os.environ.get("FACTORY_API_KEY", "")
    if isinstance(env_token, str) and env_token.strip():
        return env_token.strip()

    # 2. Encrypted auth.v2.file — the droid CLI's own credential store.
    token = _decrypt_droid_auth_file()
    if token:
        return token

    if not shutil.which("security"):
        return None

    # 3. macOS Keychain fallback (see NOTE in docstring).
    for label in ("Factory Safe Storage", "Factory Key"):
        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-l", label, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode != 0:
            continue
        token = (result.stdout or "").strip()
        if token:
            return token

    return None


def _droid_request(url: str, token: str) -> urllib.request.Request:
    """Build the Factory request with the web-app client signature.

    Origin and Referer are hardcoded to ``https://app.factory.ai`` per
    CodexBar's canonical reference — they must reflect the web-app
    browser context even for ``api.factory.ai`` endpoints, otherwise
    the WAF gates the request with a 403. The Accept and
    Content-Type headers double as both a JSON contract hint and a
    browser-like signal that helps blend in with normal traffic.
    """
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": _DROID_APP_ORIGIN,
            "Referer": _DROID_APP_REFERER,
            "x-factory-client": "web-app",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"quiver/{__version__}",
        },
    )


def _warn_droid_invalid_token() -> None:
    """Stderr hint when the Factory access token is rejected (401)."""
    import sys
    print(
        "Droid usage endpoint returned 401 — the Factory access token "
        "is invalid or expired. Re-authenticate via the `droid` CLI "
        "(run `droid` and sign in again), OR set `FACTORY_API_KEY` as "
        "an env-var override.",
        file=sys.stderr,
    )


def _warn_droid_http_error(code: int, retry_after_seconds: float | None) -> None:
    """Stderr hint for non-401 Droid HTTP errors (429, 403/404, 5xx)."""
    import sys
    if code == 429:
        if retry_after_seconds and retry_after_seconds > 0:
            wait_str = (f"{int(retry_after_seconds // 60)}m"
                        f"{int(retry_after_seconds % 60)}s")
        else:
            wait_str = "a few minutes"
        print(
            f"Droid usage endpoint returned 429 (rate-limited). Try "
            f"`swe list --refresh` again in {wait_str}.",
            file=sys.stderr,
        )
    elif code in (403, 404):
        print(
            f"Droid usage endpoint returned {code} — the URL or "
            f"`x-factory-client` header may need updating. Verify "
            f"`https://api.factory.ai/api/billing/limits` is still the "
            f"current endpoint (compare to CodexBar/docs/factory.md).",
            file=sys.stderr,
        )
    else:
        print(
            f"Droid usage endpoint returned {code} — this is an "
            f"upstream or network error from Factory, not a quiver "
            f"bug. If it persists, check https://status.factory.ai.",
            file=sys.stderr,
        )


_DROID_FETCH_TIMEOUT = 2.0
_DROID_WINDOW_LABELS = {
    "fiveHour": "5h",
    "weekly": "7d",
    "monthly": "30d",
}


def _droid_fetch(req: urllib.request.Request) -> dict | None:
    """Droid-specific fetch — like ``_fetch_json`` but emits targeted
    diagnostics for the 401 (stale keychain token) and 429/403/5xx paths
    found in real-world droid installations.
    """
    result: list[dict | None] = []
    timed_out = threading.Event()

    def warn_401() -> None:
        if not timed_out.is_set():
            _warn_droid_invalid_token()

    def warn_http_error(code: int, retry_after_seconds: float | None) -> None:
        if not timed_out.is_set():
            _warn_droid_http_error(code, retry_after_seconds)

    def fetch() -> None:
        result.append(_fetch_json(
            req,
            timeout=_DROID_FETCH_TIMEOUT,
            on_401=warn_401,
            on_http_error=warn_http_error,
        ))

    # DNS resolution can outlive urllib's socket timeout. Keep this optional
    # status lookup from holding the entire `swe list` command hostage.
    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(_DROID_FETCH_TIMEOUT)
    if worker.is_alive():
        timed_out.set()
        return None
    return result[0] if result else None


def _fetch_droid() -> RateLimitInfo | None:
    """Fetch Droid (Factory AI) billing-window quota.

    Hits ``GET https://api.factory.ai/api/billing/limits`` and surfaces
    the window that currently gates Droid usage. Exhausted longer-term
    windows take priority (monthly, then weekly, then fiveHour). When no
    window is exhausted, fiveHour remains the default. Percentages are
    averaged across the parallel core and standard budgets for the
    selected window.

    Factory's current API reports ``usedPercent`` as the consumed
    percentage directly. Values are clamped to 0-100 before selection
    and averaging so malformed overage values cannot break rendering.

    The reset countdown comes from the selected window. For an exhausted
    window, the latest exhausted-budget reset is the real unlock time;
    otherwise the earliest future reset is the next refresh.

    ``limit_reached`` is True when either budget for the selected window
    reports 100% used. The average alone must not mask a cutoff.

    Single-endpoint strategy (no second call to /subscription/usage or
    /app/auth/me): the REMAINING column budget is unforgiving and we already
    know the user has droid installed + authenticated. Plan type is
    intentionally default ``"—"`` to mirror Claude's behaviour — the
    web-app quota dashboard exposes plan tier if the user wants it.

    The real response shape (verified against the live endpoint) is::

        {"usesTokenRateLimitsBilling": true,
         "limits": {
           "standard": {"fiveHour": {"usedPercent": 19,
                                     "windowEnd": "2026-07-25T22:31:13Z",
                                     "secondsRemaining": 15091},
                        "weekly":   {...}, "monthly": {...}},
           "core":     {"fiveHour": {...}, "weekly": {...}, "monthly": {...}}},
         ...}

    Returns ``None`` when:
      - no auth token resolvable from env var, auth.v2.file, or keychain
      - HTTP error fires a diagnostic callback (401/429/5xx) and the
        server's response is dropped
      - response is not a dict, or carries no ``limits`` map
      - no supported window has a numeric ``usedPercent``

    The endpoint is undocumented and may change without notice. Same
    fragility assumption as the Codex / Claude fetchers.
    """
    token = _get_droid_access_token()
    if not token:
        return None

    req = _droid_request(_DROID_BASE_URL, token)

    data = _droid_fetch(req)
    if not isinstance(data, dict):
        return None

    limits = data.get("limits")
    if not isinstance(limits, dict) or not limits:
        return None

    now = time.time()
    window_samples: dict[str, list[tuple[int, float]]] = {
        key: [] for key in _DROID_WINDOW_LABELS
    }

    for category, cat_windows in limits.items():
        if not isinstance(category, str) or not isinstance(cat_windows, dict):
            continue
        for win_key, win in cat_windows.items():
            if not isinstance(win_key, str) or not isinstance(win, dict):
                continue
            if win_key not in window_samples:
                continue
            raw = win.get("usedPercent")
            if raw is None:
                continue
            try:
                used = max(0, min(100, int(round(float(raw)))))
            except (TypeError, ValueError):
                # Malformed payload — skip rather than crash ``swe list``.
                continue
            window_samples[win_key].append((
                used,
                _parse_iso8601_to_epoch(win.get("windowEnd")),
            ))

    exhausted_order = ("monthly", "weekly", "fiveHour")
    selected_key = next((
        key for key in exhausted_order
        if any(used >= 100 for used, _reset in window_samples[key])
    ), None)
    if selected_key is None:
        selected_key = next((
            key for key in ("fiveHour", "weekly", "monthly")
            if window_samples[key]
        ), None)
    if selected_key is None:
        return None

    selected_samples = window_samples[selected_key]
    used_pcts = [used for used, _reset in selected_samples]
    used_percent = int(round(sum(used_pcts) / len(used_pcts)))
    used_percent = max(0, min(100, used_percent))
    limit_reached = any(p >= 100 for p in used_pcts)

    future_resets = [
        reset for _used, reset in selected_samples if reset > now
    ]
    exhausted_resets = [
        reset for used, reset in selected_samples
        if used >= 100 and reset > now
    ]
    if limit_reached and exhausted_resets:
        reset_at = max(exhausted_resets)
    elif future_resets:
        reset_at = min(future_resets)
    else:
        reset_at = 0.0

    return RateLimitInfo(
        tool_name="droid",
        used_percent=used_percent,
        limit_reached=limit_reached,
        reset_at=reset_at,
        plan_type="—",
        window_seconds=0,
        window=_DROID_WINDOW_LABELS[selected_key],
    )


def _register_droid() -> None:
    """Register the Droid (Factory AI) rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_droid()

    register("droid", fetch)


# ---------------------------------------------------------------------------
# Antigravity fetcher
# ---------------------------------------------------------------------------

_ANTIGRAVITY_QUOTA_ROUTE = (
    "/exa.language_server_pb.LanguageServerService/"
    "RetrieveUserQuotaSummary"
)
_ANTIGRAVITY_RPC_TIMEOUT = 0.35


def _antigravity_csrf_tokens() -> dict[str, str]:
    """Return Antigravity CSRF tokens keyed by language-server PID."""
    try:
        result = subprocess.run(
            ["ps", "-ww", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}

    tokens: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        pid, separator, command = line.strip().partition(" ")
        if not separator or not pid.isdigit() or "--csrf_token" not in command:
            continue
        args = command.split()
        for index, arg in enumerate(args):
            if arg == "--csrf_token" and index + 1 < len(args):
                tokens[pid] = args[index + 1]
                break
            if arg.startswith("--csrf_token="):
                tokens[pid] = arg.split("=", 1)[1]
                break
    return tokens


def _antigravity_rpc_endpoints() -> list[tuple[str, str]]:
    """Return loopback listeners and per-process CSRF tokens."""
    lsof = shutil.which("lsof")
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []

    listeners: list[tuple[str, str]] = []
    current_pid = ""
    is_antigravity = False
    for line in (result.stdout or "").splitlines():
        if line.startswith("p"):
            current_pid = line[1:]
            is_antigravity = False
        elif line.startswith("c"):
            command = line[1:].lower()
            is_antigravity = (
                command == "agy"
                or "antigravity" in command
                or command.startswith("language_server")
            )
        elif line.startswith("n") and is_antigravity:
            address = line[1:]
            if address.startswith("[::1]:"):
                port = address.rsplit(":", 1)[-1]
            else:
                try:
                    host, port = address.rsplit(":", 1)
                except ValueError:
                    continue
                if host not in ("127.0.0.1", "localhost", "::1"):
                    continue
            if not port.isdigit():
                continue
            url = f"http://127.0.0.1:{port}"
            if all(existing_url != url for existing_url, _pid in listeners):
                listeners.append((url, current_pid))

    tokens = _antigravity_csrf_tokens()
    return [(url, tokens.get(pid, "")) for url, pid in listeners]


def _parse_antigravity_quota(data: dict) -> RateLimitInfo | None:
    """Select the currently most restrictive Antigravity quota bucket."""
    response = data.get("response")
    if not isinstance(response, dict):
        return None
    groups = response.get("groups")
    if not isinstance(groups, list):
        return None

    candidates: list[tuple[float, str, float]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            window = str(bucket.get("window") or "")
            window_label = {"weekly": "7d", "5h": "5h"}.get(window)
            if not window_label:
                continue
            try:
                remaining = float(bucket.get("remainingFraction"))
            except (TypeError, ValueError):
                continue
            remaining = min(1.0, max(0.0, remaining))
            used = (1.0 - remaining) * 100.0
            reset_at = _parse_iso8601_to_epoch(bucket.get("resetTime"))
            candidates.append((used, window_label, reset_at))

    if not candidates:
        return None

    # Highest utilization wins. At equal non-exhausted utilization, the 5h
    # window is the useful default; if both are exhausted, the weekly reset is
    # the longer-lived gate and therefore the actionable one to display.
    def rank(candidate: tuple[float, str, float]) -> tuple[float, int]:
        used, window, _reset = candidate
        preferred = window == ("7d" if used >= 100 else "5h")
        return used, int(preferred)

    used, window, reset_at = max(candidates, key=rank)
    used_percent = int(round(used))
    return RateLimitInfo(
        tool_name="antigravity",
        used_percent=used_percent,
        limit_reached=used >= 100,
        reset_at=reset_at,
        plan_type="—",
        window_seconds=0,
        window=window,
    )


def _fetch_antigravity() -> RateLimitInfo | None:
    """Read quota from a running Antigravity app or CLI over loopback."""
    for base_url, csrf_token in _antigravity_rpc_endpoints()[:8]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"quiver/{__version__}",
        }
        if csrf_token:
            headers["X-Codeium-Csrf-Token"] = csrf_token
        req = urllib.request.Request(
            base_url + _ANTIGRAVITY_QUOTA_ROUTE,
            data=b"{}",
            headers=headers,
        )
        data = _fetch_json(req, timeout=_ANTIGRAVITY_RPC_TIMEOUT)
        if isinstance(data, dict):
            info = _parse_antigravity_quota(data)
            if info is not None:
                return info
    return None


def _register_antigravity() -> None:
    """Register the Antigravity loopback rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_antigravity()

    register("antigravity", fetch)


# ---------------------------------------------------------------------------
# Freebuff fetcher
# ---------------------------------------------------------------------------

_FREEBUFF_SESSION_URL = "https://www.codebuff.com/api/v1/freebuff/session"


def _get_freebuff_access_token() -> str | None:
    """Return Freebuff's locally stored auth token without exposing it."""
    credentials_path = os.path.expanduser(
        "~/.config/manicode/credentials.json",
    )
    try:
        with open(credentials_path) as file:
            credentials = json.load(file)
    except (json.JSONDecodeError, OSError):
        return None

    default = credentials.get("default")
    if not isinstance(default, dict):
        return None
    token = default.get("authToken")
    return token if isinstance(token, str) and token else None


def _parse_freebuff_quota(data: dict) -> RateLimitInfo | None:
    """Parse the referral-unlocked GLM 5.2 session allowance."""
    def is_glm(model: object) -> bool:
        return isinstance(model, str) and (
            model == "z-ai/glm-5.2" or model.startswith("z-ai/glm-5.2-")
        )

    quota = None
    by_model = data.get("rateLimitsByModel")
    if isinstance(by_model, dict):
        quota = next(
            (
                value for model, value in by_model.items()
                if is_glm(model) and isinstance(value, dict)
            ),
            None,
        )
    active_quota = data.get("rateLimit")
    if quota is None and isinstance(active_quota, dict) and is_glm(
        active_quota.get("model") or data.get("model"),
    ):
        quota = active_quota

    if quota is not None:
        try:
            total_units = float(quota.get("limit"))
            used_units = float(quota.get("recentCount"))
        except (TypeError, ValueError):
            return None
        remaining_units = max(0.0, total_units - used_units)
        reset_at = quota.get("resetAt")
    else:
        referral = data.get("referral")
        if not isinstance(referral, dict):
            return None
        try:
            remaining_units = float(referral.get("weeklySessionsRemaining"))
            total_units = float(referral.get("qualifiedCount"))
        except (TypeError, ValueError):
            return None
        reset_at = referral.get("resetAt")

    if not (
        math.isfinite(remaining_units)
        and math.isfinite(total_units)
        and remaining_units >= 0
        and total_units > 0
        and remaining_units <= total_units
    ):
        return None

    used_percent = int(round((1.0 - remaining_units / total_units) * 100.0))
    return RateLimitInfo(
        tool_name="freebuff",
        used_percent=used_percent,
        limit_reached=remaining_units <= 0,
        reset_at=_parse_iso8601_to_epoch(reset_at),
        plan_type="free",
        window_seconds=0,
        remaining_units=remaining_units,
        total_units=total_units,
    )


def _fetch_freebuff() -> RateLimitInfo | None:
    """Read Freebuff's GLM 5.2 session allowance without joining a session."""
    token = _get_freebuff_access_token()
    if not token:
        return None
    request = urllib.request.Request(
        _FREEBUFF_SESSION_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"quiver/{__version__}",
        },
    )
    data = _fetch_json(request)
    return _parse_freebuff_quota(data) if isinstance(data, dict) else None


def _register_freebuff() -> None:
    """Register the Freebuff GLM session-quota fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_freebuff()

    register("freebuff", fetch)


# Register built-in fetchers at import time
_register_codex()
_register_github_copilot()
_register_claude()
_register_droid()
_register_antigravity()
_register_freebuff()


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


def _load_cached_no_data() -> set[str]:
    """Names whose fetcher recently returned nothing.

    Without this a provider that never reports usage counts as a permanent
    cache miss: it is absent from ``limits``, so every call re-runs its
    fetcher. For providers that shell out (antigravity spawns two
    subprocesses) that cost lands on every ``swe list``. Remembering the
    negative answer for the same TTL keeps the miss to once per window.
    """
    try:
        if not RATE_LIMITS_CACHE_FILE.exists():
            return set()
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("cached_at", 0) > _CACHE_TTL:
            return set()
        names = data.get("no_data", [])
        return set(names) if isinstance(names, list) else set()
    except Exception:
        return set()


def _save_cached(
    limits: dict[str, dict],
    updated_at: dict[str, float] | None = None,
    no_data: Iterable[str] | None = None,
) -> None:
    """Persist rate limits to disk cache."""
    try:
        RATE_LIMITS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        payload = {
            "cached_at": now,
            "limits": limits,
            "updated_at": updated_at or {name: now for name in limits},
            "no_data": sorted(no_data or ()),
        }
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

_RATE_LIMIT_FETCH_DEADLINE = 2.0
_STALE_CACHE_TTL = 24 * 60 * 60


def _load_stale_cached() -> tuple[dict[str, dict], dict[str, float]]:
    """Load the last snapshot for outage fallback, up to 24 hours old."""
    try:
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        cached_at = float(data.get("cached_at", 0))
        limits = data.get("limits", {})
        timestamps = data.get("updated_at", {})
        if not isinstance(limits, dict) or not isinstance(timestamps, dict):
            return {}, {}
        usable: dict[str, dict] = {}
        usable_timestamps: dict[str, float] = {}
        for name, raw in limits.items():
            # Authentication failures are transient status, not a successful
            # usage reading. A forced refresh after login must be able to
            # replace or remove this marker immediately.
            if raw.get("plan_type") == "auth-required":
                continue
            try:
                provider_updated_at = float(timestamps.get(name, cached_at))
            except (TypeError, ValueError):
                continue
            if time.time() - provider_updated_at <= _STALE_CACHE_TTL:
                usable[name] = raw
                usable_timestamps[name] = provider_updated_at
        return usable, usable_timestamps
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}, {}


def get_all_rate_limits(
    use_cache: bool = True,
    tool_names: Iterable[str] | None = None,
) -> dict[str, RateLimitInfo]:
    """Fetch rate limits for the selected registered tools.

    Returns a dict mapping ``tool_name`` → ``RateLimitInfo``.
    ``tool_names=None`` retains the low-level all-fetchers behavior for tests
    and diagnostics. CLI callers pass the starred harness set so unselected
    usage scripts never start. Tools without a fetcher or whose fetch fails
    are omitted.
    """
    result: dict[str, RateLimitInfo] = {}
    requested = None if tool_names is None else set(tool_names)
    fetchers = [
        (name, fetcher)
        for name, fetcher in _FETCHERS.items()
        if requested is None or name in requested
    ]
    fetcher_names = {name for name, _fetcher in fetchers}
    raw_cache: dict[str, dict] = {}
    cache_updated_at: dict[str, float] = {}

    if use_cache:
        cached = _load_cached()
        if cached is not None:
            for name, raw in cached.items():
                if name not in fetcher_names:
                    continue
                try:
                    result[name] = RateLimitInfo(**raw)
                except (TypeError, ValueError):
                    pass
            # A provider that answered "nothing to report" last time is
            # covered for this window too, otherwise it is refetched forever.
            known_empty = _load_cached_no_data() & fetcher_names
            missing = fetcher_names - result.keys() - known_empty
            if not missing:
                return result
            fetchers = [item for item in fetchers if item[0] in missing]
            now = time.time()
            raw_cache = {name: asdict(info) for name, info in result.items()}
            cache_updated_at = {name: now for name in result}

    if not raw_cache:
        stale_limits, stale_updated_at = _load_stale_cached()
        for name, raw in stale_limits.items():
            if name not in fetcher_names:
                continue
            try:
                info = RateLimitInfo(**raw)
            except (TypeError, ValueError):
                continue
            result[name] = info
            raw_cache[name] = raw
            cache_updated_at[name] = stale_updated_at[name]

    fetched: dict[str, RateLimitInfo | None] = {}
    fetched_lock = threading.Lock()

    def fetch_one(tool_name: str, fetcher: Callable) -> None:
        try:
            info = fetcher()
        except Exception:
            info = None
        with fetched_lock:
            fetched[tool_name] = info

    workers = [
        threading.Thread(target=fetch_one, args=item, daemon=True)
        for item in fetchers
    ]
    deadline = time.monotonic() + _RATE_LIMIT_FETCH_DEADLINE
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))

    with fetched_lock:
        completed = fetched.copy()
    empty_fetchers: set[str] = set()
    for tool_name, _fetcher in fetchers:
        info = completed.get(tool_name)
        if info:
            result[tool_name] = info
            raw_cache[tool_name] = asdict(info)
            cache_updated_at[tool_name] = time.time()
        elif tool_name in completed:
            # Ran to completion and produced nothing. Distinct from a worker
            # that hit the deadline, which is absent from ``completed`` and
            # should be retried rather than remembered as empty.
            empty_fetchers.add(tool_name)

    # Cache an empty result only when no recent successful snapshot exists.
    # Partial outages retain each provider's last known value for up to 24h.
    _save_cached(raw_cache, cache_updated_at, no_data=empty_fetchers)

    return result


def get_rate_limit(tool_name: str, use_cache: bool = True) -> RateLimitInfo | None:
    """Fetch one starred tool's rate limit without polling other providers."""
    from quiver.harness.stars import is_starred

    if not is_starred(tool_name):
        return None
    return get_all_rate_limits(
        use_cache=use_cache,
        tool_names={tool_name},
    ).get(tool_name)
