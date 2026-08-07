"""Rate limit fetchers for AI coding CLIs.

Pluggable architecture: each provider implements a fetch function that
returns a ``RateLimitInfo`` dataclass (or ``None`` if unavailable).
``get_all_rate_limits`` aggregates across all registered fetchers with
a short disk cache (60s TTL, same pattern as session cache).

Currently supported:
  - Codex   (ChatGPT backend-api wham/usage, OAuth from ~/.codex/auth.json)
  - Copilot (api.github.com/copilot_internal/user, OAuth via `gh` CLI token)
  - Claude  (api.anthropic.com/api/oauth/usage, OAuth from Claude Code creds)
  - Droid   (api.factory.ai/api/billing/limits, FACTORY_API_KEY / keychain)

All four endpoints are internal/undocumented — they work today because the
official CLIs use them and they happen to be queryable. They can change
without notice. The same SSL-fallback logic that handles macOS python.org
builds without CA certificates applies to all four.
"""

from __future__ import annotations

import base64
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
    window: str = ""           # e.g. "5h"/"7d"/"7d_sonnet" — Claude returns
                                # 3 windows in one request; we surface the
                                # most-restrictive (highest utilization) and
                                # label it via this short abbreviation in
                                # format_column(). Empty for Codex / Copilot
                                # (single-window response). Default keeps
                                # all existing call sites + cache
                                # reconstruction (RateLimitInfo(**raw))
                                # backward-compatible.

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
        pct = f"{self.used_percent}%"
        if self.limit_reached:
            pct_str = c("red", pct)
        elif self.used_percent >= 80:
            pct_str = c("yellow", pct)
        else:
            pct_str = c("green", pct)
        reset = self.reset_in_human
        if self.window:
            # Compact form (`5h`, `7d`, `7ds`) keeps the column width
            # budget intact while letting the user tell which window
            # the figure came from. Examples:
            #     80% 5h:3h12m  ← most-restrictive is the 5h rolling
            #     91% 7d:4d3h   ← most-restrictive is the weekly
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
# endpoints on every invocation; ``swe list --refresh`` (or ``-r``)
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
    flip the result away from ``None``. Network errors and the SSL
    fallback path do NOT trigger either callback (no signal there).
    """
    # Mitigate SSRF/LFI by restricting allowed URL schemes
    if not req.full_url.startswith(("http://", "https://")):
        raise ValueError("Invalid URL scheme")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
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


def _get_claude_access_token() -> str | None:
    """Return the Claude Code OAuth ``accessToken`` from the best known source.

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
                tok = oauth.get("accessToken")
                if isinstance(tok, str) and tok:
                    return tok

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
    tok = oauth.get("accessToken")
    return tok if isinstance(tok, str) and tok else None


def _fetch_claude_url(req: urllib.request.Request) -> dict | None:
    """Claude-specific fetch — like ``_fetch_json`` but emits two
    targeted diagnostics:

    - HTTP 401 → suggests ``_BETA_VERSIONS`` is stale (Anthropic
      silently rotates ``anthropic-beta`` without docs).
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
            f"Claude usage endpoint returned 401 — the `anthropic-beta` "
            f"value (`{beta_header}`) is most likely stale. Update "
            f"`_BETA_VERSIONS` in `src/quiver/harness/rate_limits.py`.",
            file=sys.stderr,
        )

    def _warn_claude_429(code: int, retry_after_seconds: float | None) -> None:
        import sys
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


def _fetch_claude() -> RateLimitInfo | None:
    """Return Claude Code's rate-limit status for the ``swe list`` column.

    Currently **disabled** — the live polling of
    ``api.anthropic.com/api/oauth/usage`` is commented out below. The
    endpoint is undocumented and Anthropic rate-limits it aggressively
    (a 429 from the endpoint itself, not the account), which made the
    fetcher hammer the API on every ``swe list --refresh`` and surface
    a misleading ``—`` for no-subscription accounts. While we're not
    polling, we render ``no-sub`` for any account that has Claude Code
    credentials installed, and ``None`` (``—``) when there are no
    credentials at all (Claude Code not logged in / not installed).

    The original HTTP + parsing logic is preserved as a commented block
    so it can be re-enabled once the endpoint stabilises or a paid
    subscription is detected. Restore by uncommenting the block and
    removing the ``no-sub`` return below.
    """
    token = _get_claude_access_token()
    if not token:
        # No Claude Code credentials → not logged in / not installed.
        # Render ``—`` (the default for tools with no rate data).
        return None

    # --- no-subscription / polling-disabled return ----------------------
    # While the endpoint polling is disabled, every account with Claude
    # Code credentials renders ``no-sub``. ``format_column()`` checks
    # ``plan_type == "no-sub"`` and renders the literal label, so the
    # numeric fields are inert placeholders.
    return RateLimitInfo(
        tool_name="claude",
        used_percent=0,
        limit_reached=False,
        reset_at=0.0,
        plan_type="no-sub",
        window_seconds=0,
        window="",
    )

    # --- original live-fetch logic (disabled) ---------------------------
    # url = "https://api.anthropic.com/api/oauth/usage"
    # beta_header = _BETA_VERSIONS["claude-oauth-usage"]
    # req = urllib.request.Request(
    #     url,
    #     headers={
    #         "Authorization": f"Bearer {token}",
    #         "anthropic-beta": beta_header,
    #         "Accept": "application/json",
    #         "User-Agent": "quiver/0.2.7",
    #     },
    # )
    #
    # data = _fetch_claude_url(req)
    # if not isinstance(data, dict):
    #     return None
    #
    # best_window_key = ""
    # best_utilization = -1.0
    # best_reset_at = 0.0
    # limit_reached = False
    #
    # # Iteration order doesn't matter; we pick by raw ``utilization``
    # # magnitude so a sub-1.0 value never beats a 1.0 value (which would
    # # signal "rate-limited").
    # for key in ("five_hour", "seven_day", "seven_day_sonnet"):
    #     window = data.get(key)
    #     if not isinstance(window, dict):
    #         continue
    #     try:
    #         util = float(window.get("utilization", 0))
    #     except (TypeError, ValueError):
    #         # Malformed payload — skip rather than crash ``swe list``.
    #         continue
    #     if util > best_utilization:
    #         best_utilization = util
    #         best_window_key = key
    #         best_reset_at = _parse_iso8601_to_epoch(window.get("resets_at"))
    #         # The endpoint doesn't expose an explicit ``limit_reached``
    #         # flag; treat utilization >= 1.0 (= 100% consumed in the
    #         # window) as the canonical "you're cut off" signal.
    #         limit_reached = util >= 1.0
    #
    # if best_utilization < 0:
    #     return None
    #
    # window_label = _CLAUDE_WINDOWS.get(best_window_key, "")
    # used_percent = int(round(min(100.0, max(0.0, best_utilization * 100))))
    #
    # return RateLimitInfo(
    #     tool_name="claude",
    #     used_percent=used_percent,
    #     limit_reached=limit_reached,
    #     reset_at=best_reset_at,
    #     plan_type="—",  # usage endpoint doesn't expose plan_type
    #     window_seconds=0,  # usage endpoint doesn't expose this either
    #     window=window_label,
    # )


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
            "User-Agent": "quiver/0.2.7",
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


def _droid_fetch(req: urllib.request.Request) -> dict | None:
    """Droid-specific fetch — like ``_fetch_json`` but emits targeted
    diagnostics for the 401 (stale keychain token) and 429/403/5xx paths
    found in real-world droid installations.
    """
    return _fetch_json(
        req,
        timeout=10,
        on_401=_warn_droid_invalid_token,
        on_http_error=_warn_droid_http_error,
    )


def _fetch_droid() -> RateLimitInfo | None:
    """Fetch Droid (Factory AI) billing-window quota.

    Hits ``GET https://api.factory.ai/api/billing/limits`` and surfaces
    the **average USED percent of the core and standard fiveHour
    windows** as the RATE percentage. Factory exposes two parallel
    budgets (``core`` and ``standard``), each with three windows
    (``fiveHour`` / ``weekly`` / ``monthly``). The 5h rolling window is
    the most actionable short-term signal, and averaging the two
    budgets gives a single blended figure that reflects how much of
    BOTH budgets is consumed rather than only the one that happens to
    be more restrictive.

    **The API field is named ``usedPercent`` but is actually REMAINING
    percent.** We invert it (``used = 100 - usedPercent``) before
    averaging. Example: standard/5h reports 46 (remaining) → 54% used,
    core/5h reports 100 (remaining, a freshly-rolled-over window) → 0%
    used; the column shows ``(54 + 0) / 2 = 27%``. Both fiveHour windows
    are included in the average regardless of their ``windowEnd`` — a
    just-rolled-over window legitimately reports 100% remaining (0%
    used), which is the current state, not stale data.

    The reset countdown is the **earliest *future* ``windowEnd`` across
    all six windows** (the nearest reset that hasn't already happened).
    Past ``windowEnd`` values are skipped for the reset because a
    rolled-over window's stale ``windowEnd`` would render a meaningless
    "now" — the soonest future refresh is the actionable signal.

    ``limit_reached`` is True when EITHER 5h window reports 0% remaining
    (i.e. 100% used) — the average alone could mask a cutoff (e.g.
    core/5h=0% remaining, standard/5h=80% remaining averages to 10%
    used, but core is actually cut off), so the flag honours the
    per-budget signal.

    Single-endpoint strategy (no second call to /subscription/usage or
    /app/auth/me): the RATE column budget is unforgiving and we already
    know the user has droid installed + authenticated. Plan type is
    intentionally default ``"—"`` to mirror Claude's behaviour — the
    web-app quota dashboard exposes plan tier if the user wants it.

    The real response shape (verified against the live endpoint) is::

        {"usesTokenRateLimitsBilling": true,
         "limits": {
           "standard": {"fiveHour": {"usedPercent": 19,   # REMAINING
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
      - neither ``core`` nor ``standard`` has a ``fiveHour`` window with
        a numeric ``usedPercent``

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
    # Collect USED percent (inverted from the API's REMAINING value)
    # from every fiveHour window with a numeric usedPercent, and track
    # the earliest FUTURE windowEnd across all windows for the reset
    # countdown. Past windowEnds are skipped for the reset (a
    # rolled-over window's stale windowEnd would show a meaningless
    # "now"), but the window's usedPercent is still included in the
    # average — a rolled-over 5h window legitimately reports 100%
    # remaining (0% used), which is the current state.
    used_pcts: list[int] = []
    earliest_reset_at = 0.0

    for category, cat_windows in limits.items():
        if not isinstance(category, str) or not isinstance(cat_windows, dict):
            continue
        for win_key, win in cat_windows.items():
            if not isinstance(win_key, str) or not isinstance(win, dict):
                continue
            reset_at = _parse_iso8601_to_epoch(win.get("windowEnd"))
            if reset_at > now:
                # Earliest future reset across every active window.
                if earliest_reset_at == 0.0 or reset_at < earliest_reset_at:
                    earliest_reset_at = reset_at
            # Only fiveHour windows feed the averaged percentage.
            if win_key != "fiveHour":
                continue
            raw = win.get("usedPercent")
            if raw is None:
                continue
            try:
                remaining = max(0, min(100, int(round(float(raw)))))
            except (TypeError, ValueError):
                # Malformed payload — skip rather than crash ``swe list``.
                continue
            # API field is REMAINING; invert to USED.
            used_pcts.append(100 - remaining)

    if not used_pcts:
        return None

    used_percent = int(round(sum(used_pcts) / len(used_pcts)))
    used_percent = max(0, min(100, used_percent))
    # A budget is exhausted when any single 5h window reports 0%
    # remaining (100% used); the average alone can hide that, so honour
    # the per-budget signal.
    limit_reached = any(p >= 100 for p in used_pcts)

    return RateLimitInfo(
        tool_name="droid",
        used_percent=used_percent,
        limit_reached=limit_reached,
        reset_at=earliest_reset_at,
        plan_type="—",
        window_seconds=0,
        # Blended 5h figure across core + standard; ``5h`` conveys the
        # rolling window without implying a single category.
        window="5h",
    )


def _register_droid() -> None:
    """Register the Droid (Factory AI) rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_droid()

    register("droid", fetch)


# Register built-in fetchers at import time
_register_codex()
_register_github_copilot()
_register_claude()
_register_droid()


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
