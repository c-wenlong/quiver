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
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from quiver import __version__
from quiver.harness import remedies
from quiver.console import c
from quiver.harness.models import RateLimitInfo, _window
from quiver.paths import RATE_LIMITS_CACHE_FILE


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

# How long a provider's last successful reading may stand in for a failed
# fetch. Shared by the aggregator's outage fallback and the Claude
# fetcher's 429 cooldown so neither can show a figure older than a day.
_STALE_CACHE_TTL = 24 * 60 * 60

# Registry: tool_name → fetcher callable
RateLimitFetcher = Callable[[], RateLimitInfo | None]
_FETCHERS: dict[str, RateLimitFetcher] = {}
# tool_name → (fetcher, signed_in, empty_is_normal), as ``register`` was
# given them. ``signed_in`` says whether a credential the fetcher could use
# is there; it is asked only after that same fetcher came back empty, to
# tell the two kinds of empty apart. Kept beside the fetcher it describes so
# a stand-in fetcher (a test's) never runs the real credential lookup.
_SIGNED_IN: dict[str, tuple[RateLimitFetcher, Callable[[], bool], bool]] = {}

# Why a tool has no reading, as ``get_all_rate_limits`` reports it through
# ``no_reading`` and ``swe list --json`` as a row's ``no_reading``.
SIGNED_OUT = "signed_out"
FETCH_ERROR = "error"

# tool_name → (fetcher, diagnose): a probe answering "which credential
# problem explains this empty fetch" as a remedies status — signed-out,
# auth-required, permission-required — or "ok"/None when a credential is
# there and the fetch itself is what failed. Kept beside the fetcher it
# describes so a stand-in fetcher never runs the real credential probe.
_DIAGNOSE: dict[str, tuple[RateLimitFetcher, Callable[[], str | None]]] = {}


def register(
    tool_name: str,
    fetcher: RateLimitFetcher,
    signed_in: Callable[[], bool] | None = None,
    empty_is_normal: bool = False,
    diagnose: Callable[[], str | None] | None = None,
) -> None:
    """Register a rate limit fetcher for a tool.

    ``signed_in`` checks for the credential the fetcher reads, without
    using it. With one, an empty fetch reads as signed out when it says no
    and as a failed fetch when it says yes; without one an empty fetch
    carries no reason. ``empty_is_normal`` is for a fetcher that can come
    back empty while signed in and healthy (Freebuff on an account with no
    GLM quota): there an empty answer is "nothing to report".

    ``diagnose`` goes further than ``signed_in``: it names the credential
    problem itself (``signed-out``, ``auth-required``,
    ``permission-required``) so an empty fetch becomes an actionable
    status, not just "error". It is asked only after an empty fetch, like
    ``signed_in``.
    """
    _FETCHERS[tool_name] = fetcher
    if signed_in is not None:
        _SIGNED_IN[tool_name] = (fetcher, signed_in, empty_is_normal)
    if diagnose is not None:
        _DIAGNOSE[tool_name] = (fetcher, diagnose)


def _no_reading_reason(
    tool_name: str, fetcher: RateLimitFetcher, raised: bool = False,
) -> str | None:
    """Why ``fetcher`` came back empty for ``tool_name``: ``SIGNED_OUT``,
    ``FETCH_ERROR``, or None when that can't be told."""
    entry = _SIGNED_IN.get(tool_name)
    if entry is None or entry[0] is not fetcher:
        return FETCH_ERROR if raised else None
    _fetcher, probe, empty_is_normal = entry
    try:
        signed_in = bool(probe())
    except Exception:
        return None
    if not signed_in:
        return SIGNED_OUT
    if empty_is_normal and not raised:
        return None
    return FETCH_ERROR


def _failure_status(
    tool_name: str,
    fetcher: RateLimitFetcher,
    raised: bool = False,
    kinds: Iterable[str] = (),
) -> str | None:
    """The status an empty fetch reports on the issue boundary:
    ``signed-out``, ``auth-required``, ``permission-required`` or
    ``unavailable`` — or None when empty is normal for the tool
    (Freebuff's no-quota account) or genuinely unknowable.

    Credential verdicts win over fetch verdicts: a 401 on the way out is
    noise next to "there is no token to send". ``kinds`` is what the
    fetch's ``_fetch_json`` calls recorded on its own thread (see
    ``_json_failures``): ``auth`` for a 401, anything else for an outage.
    """
    entry = _DIAGNOSE.get(tool_name)
    credential_known = False
    if entry is not None and entry[0] is fetcher:
        try:
            verdict = entry[1]()
        except Exception:
            verdict = None
        if verdict in (remedies.SIGNED_OUT, remedies.AUTH_REQUIRED,
                       remedies.PERMISSION_REQUIRED):
            return verdict
        # "ok" says a credential is there, so the fetch is what failed.
        credential_known = verdict == remedies.OK
    else:
        probe_entry = _SIGNED_IN.get(tool_name)
        if probe_entry is not None and probe_entry[0] is fetcher:
            _fetcher, signed_in, empty_is_normal = probe_entry
            try:
                present = bool(signed_in())
            except Exception:
                present = None
            if present is False:
                return remedies.SIGNED_OUT
            credential_known = present is True
            if (present is True and empty_is_normal and not raised
                    and "auth" not in kinds):
                return None
    if "auth" in kinds:
        return remedies.AUTH_REQUIRED
    if credential_known or kinds or raised:
        return remedies.UNAVAILABLE
    # No probe to judge by and nothing recorded: the old "unknown".
    return None


# A probe answers "is a credential there", never by reading a secret: a
# slow lookup raises (unknown, not absent), so a busy keychain or ``gh``
# never tells a signed-in user to sign in again.
_PROBE_TIMEOUT = 1.0


def _keychain_item_exists(*query: str) -> bool:
    """Whether a generic-password item matching ``query`` is in the
    keychain. Without ``-w`` the secret is never read, so no access prompt;
    off macOS there is no keychain and the answer is no."""
    if not shutil.which("security"):
        return False
    result = subprocess.run(
        ["security", "find-generic-password", *query],
        capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
    )
    return result.returncode == 0


def _gh_signed_in() -> bool:
    """Whether ``gh auth token`` has a token; a timeout raises."""
    if not shutil.which("gh"):
        return False
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True,
        timeout=_PROBE_TIMEOUT,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


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
    # Numeric seconds (RFC 7231 §7.1.3 first form). A header of
    # ``Retry-After: inf`` parses to a float that returns here and
    # later kills ``int(retry // 60)`` with OverflowError.
    try:
        seconds = float(s)
    except (TypeError, ValueError):
        pass
    else:
        return max(0.0, seconds) if math.isfinite(seconds) else None
    # IMF-fixdate (RFC 7231 §7.1.3 second form).
    try:
        dt = datetime.datetime.strptime(
            s, "%a, %d %b %Y %H:%M:%S GMT",
        ).replace(tzinfo=datetime.timezone.utc)
        return max(0.0, dt.timestamp() - time.time())
    except (ValueError, TypeError):
        return None


_CA_WARNED = False


# CA bundles the operating system ships, in the order they are tried when
# Python's own default store is empty. A python.org build on macOS has no
# certificates of its own, but the OS trust store at ``/etc/ssl/cert.pem``
# is always there; the rest cover the common Linux distributions.
_SYSTEM_CA_BUNDLES: tuple[str, ...] = (
    "/etc/ssl/cert.pem",                     # macOS, BSD, Alpine
    "/etc/ssl/certs/ca-certificates.crt",    # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",      # RHEL, Fedora, CentOS
    "/etc/ssl/ca-bundle.pem",                # openSUSE
)


def _ca_bundle_candidates() -> list[str]:
    """CA bundle files worth trying, existing ones only, OS store first.

    certifi comes last: it is not a dependency of this package, so on
    most machines the OS bundle is the only one present, and when both
    exist the OS store is what every other tool on the machine trusts.
    """
    found = [path for path in _SYSTEM_CA_BUNDLES if os.path.isfile(path)]
    try:
        import certifi
    except ImportError:
        return found
    try:
        bundle = certifi.where()
    except Exception:
        return found
    if bundle and os.path.isfile(bundle):
        found.append(bundle)
    return found


def _verified_context() -> "ssl.SSLContext | None":
    """A context that still verifies, from the OS trust store or certifi.

    Returns None when no usable CA bundle exists, which the caller must
    treat as "give up", never as "connect anyway".
    """
    for cafile in _ca_bundle_candidates():
        try:
            return ssl.create_default_context(cafile=cafile)
        except (OSError, ssl.SSLError):
            continue
    return None


def _warn_untrusted_ca() -> None:
    """Say why usage is blank, once per process rather than once per harness."""
    global _CA_WARNED
    if _CA_WARNED:
        return
    _CA_WARNED = True
    # stderr: `swe list --json --usage` is parsed by the app, so a warning
    # on stdout would corrupt the payload it expects there.
    print(c("yellow", "  Could not verify the server certificate, so no token was sent."
                      " Usage for every starred harness is blank until this is fixed."),
          file=sys.stderr)
    print(c("dim", "  Install the CA bundle: /Applications/Python\\ 3.x/Install\\ Certificates.command"),
          file=sys.stderr)
    print(c("dim", "  or: python3 -m pip install certifi"), file=sys.stderr)


def _fetch_json(
    req: urllib.request.Request,
    timeout: int = 5,
    on_401: Callable | None = None,
    on_http_error: Callable[[int, float | None], None] | None = None,
) -> dict | None:
    """Fetch JSON from a URL, retrying once with a bundled CA store.

    Python from python.org on macOS ships without system CA certificates
    until the user runs "Install Certificates.command", so ``urlopen``
    fails with ``SSL: CERTIFICATE_VERIFY_FAILED`` on a healthy connection.

    This used to retry with ``verify_mode = CERT_NONE``. Every request
    through here carries an ``Authorization: Bearer`` header, and an
    unverified context accepts any certificate, so anyone able to
    intercept the connection could present their own certificate and read
    the token. The first attempt failing is exactly what an interception
    looks like, so the fallback triggered precisely when it was least
    safe. The retry now supplies a real CA bundle via ``certifi`` when it
    is importable, and otherwise gives up and tells the caller how to fix
    the trust store. Verification is never disabled.

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
        # "auth" tells the caller's status probe the credential was
        # rejected; any other HTTP error is an outage like the network's.
        _note_json_failure("auth" if exc.code == 401 else "network")
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
        return None
    except urllib.error.URLError as exc:
        # SSL cert verification failure — retry with unverified context
        if not isinstance(exc.reason, ssl.SSLError):
            _note_json_failure("network")
            return None
    except ssl.SSLError:
        pass  # fall through to retry
    except (ValueError, RecursionError, OSError, TimeoutError):
        # ValueError covers both parse failures: a non-UTF-8 body dies in
        # decode() as UnicodeDecodeError, a non-JSON body in json.loads()
        # as JSONDecodeError. RecursionError is a deeply nested but valid
        # body. All three mean the reply could not be read, same as a
        # network failure — not a traceback at the caller.
        _note_json_failure("network")
        return None

    # Retry with a bundled CA store, still fully verified. Without one
    # there is nothing safe left to try: sending the bearer token over a
    # connection we cannot authenticate is worse than reporting no data.
    ctx = _verified_context()
    if ctx is None:
        _warn_untrusted_ca()
        _note_json_failure("network")
        return None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
        return None
    except (urllib.error.URLError, ValueError, RecursionError, OSError,
            TimeoutError, ssl.SSLError):
        # Same body-parse coverage as the first attempt: decode()
        # (UnicodeDecodeError) and json.loads() (JSONDecodeError, a
        # ValueError) must not escape as tracebacks.
        _note_json_failure("network")
        return None


# ---------------------------------------------------------------------------
# Codex fetcher
# ---------------------------------------------------------------------------

def _register_codex() -> None:
    """Register the Codex rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_codex()

    register("codex", fetch,
             signed_in=lambda: _read_codex_access_token() is not None)


def _read_codex_auth() -> dict | None:
    """The parsed ``~/.codex/auth.json``, or None when absent or unreadable."""
    auth_path = os.path.expanduser("~/.codex/auth.json")
    if not os.path.exists(auth_path):
        return None
    try:
        with open(auth_path) as f:
            auth = json.load(f)
    except (json.JSONDecodeError, OSError, RecursionError):
        return None
    return auth if isinstance(auth, dict) else None


def _read_codex_access_token() -> str | None:
    """The ChatGPT OAuth ``access_token`` from ``~/.codex/auth.json``."""
    auth = _read_codex_auth()
    tokens = auth.get("tokens") if auth else None
    access_token = tokens.get("access_token") if isinstance(tokens, dict) else None
    return access_token or None


def _fetch_codex() -> RateLimitInfo | None:
    """Fetch Codex rate limits from the ChatGPT backend-api.

    Uses the OAuth ``access_token`` from ``~/.codex/auth.json`` to query
    ``https://chatgpt.com/backend-api/wham/usage``. The sign-in's address
    rides the ``id_token`` claims, as the app's Codex reader takes it.
    """
    auth = _read_codex_auth()
    tokens = auth.get("tokens") if auth else None
    tokens = tokens if isinstance(tokens, dict) else {}
    access_token = tokens.get("access_token")
    if not access_token:
        return None
    claims = _jwt_payload(tokens.get("id_token"))
    email = claims.get("email") if claims else None
    email = email.strip() if isinstance(email, str) and email.strip() else None

    url = "https://chatgpt.com/backend-api/wham/usage"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "codex-cli",
        },
    )

    data = _fetch_json(req)
    if not isinstance(data, dict):
        return None

    rate_limit = data.get("rate_limit")
    rate_limit = rate_limit if isinstance(rate_limit, dict) else {}
    primary = rate_limit.get("primary_window")
    primary = primary if isinstance(primary, dict) else {}

    # Coerced the way every other fetcher does: the reply is passed to the
    # table renderer as is, and a string percent there raised TypeError out
    # of `swe list`, while a "false" string read as a reached limit.
    try:
        raw_used = float(primary.get("used_percent") or 0)
    except (TypeError, ValueError, OverflowError):
        raw_used = 0.0
    used_percent = (int(round(min(100.0, max(0.0, raw_used))))
                    if math.isfinite(raw_used) else 0)
    limit_reached = rate_limit.get("limit_reached") is True
    try:
        window_seconds = max(0, int(primary.get("limit_window_seconds") or 0))
    except (TypeError, ValueError, OverflowError):
        window_seconds = 0
    reset_at = primary.get("reset_at", 0)
    if isinstance(reset_at, str):
        # Codex's API returns reset_at as a numeric epoch today, but
        # fall back to ISO 8601 via the shared helper in case the
        # field ever arrives as a string.
        reset_at = _parse_iso8601_to_epoch(reset_at)
    elif isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool):
        # ``bool`` subclasses ``int`` in Python — guard explicitly so a
        # pathological ``reset_at: True`` payload can't silently become
        # ``1.0``. A 400-digit JSON int overflows float().
        try:
            reset_at = float(reset_at)
        except OverflowError:
            reset_at = 0.0
    else:
        reset_at = 0.0
    plan_type = data.get("plan_type")
    plan_type = plan_type if isinstance(plan_type, str) and plan_type else "—"

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
        email=email,
    )


# ---------------------------------------------------------------------------
# GitHub Copilot fetcher
# ---------------------------------------------------------------------------

def _register_github_copilot() -> None:
    """Register the GitHub Copilot rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_github_copilot()

    register("copilot", fetch, signed_in=_gh_signed_in)


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
        except (TypeError, ValueError, OverflowError):
            # Malformed (e.g. string with "%" suffix, or an inf payload).
            # Don't crash.
            used_percent = 0
        else:
            used_percent = max(0, min(100, used_raw))

    has_quota = bool(premium.get("has_quota", True))
    try:
        entitlement = int(premium.get("entitlement", 0) or 0)
    except (TypeError, ValueError, OverflowError):
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

    Returns 0.0 for any unparseable or falsy input so a bad timestamp
    never breaks the whole ``swe list`` run.

    Naïve timestamps (no timezone designator) are treated as UTC to
    keep semantics consistent with offset-bearing variants — otherwise
    ``.timestamp()`` would silently apply local time and produce
    incorrect reset countdowns depending on the user's TZ.

    Examples that must parse successfully (all yielding 1785542400)::

        2026-08-01T00:00:00.000Z          # microseconds + Z (live API)
        2026-08-01T00:00:00Z              # no fractional
        2026-08-01T00:00:00+00:00         # explicit offset, naive base
        2026-08-01T00:00:00.123+00:00     # microseconds + offset
        2026-08-01T00:00:00               # naive (treated as UTC)

    A malformed fractional part next to a timezone offset is salvaged
    to whole-second precision rather than discarded, see the retry at
    the bottom of the function.
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

    # Salvage pass. When the string carries BOTH a fractional part that
    # `fromisoformat` refuses and a timezone offset, drop the fraction
    # and retry, so a garbled sub-second field costs us precision
    # rather than the whole timestamp. Reachable on every supported
    # interpreter: a non-numeric fraction ("...00.abc+00:00") or a
    # second stray dot is rejected by `fromisoformat` on all of them.
    # The date hyphens occupy positions 4 and 7, so the first sign
    # character at position >= 10 is the genuine tz separator.
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
# the 5h from the 7d at a glance inside the narrow quota column.
_CLAUDE_WINDOWS: dict[str, str] = {
    "five_hour": "5h",
    "seven_day": "7d",
    "seven_day_sonnet": "7ds",
}

# The aggregator stops waiting for a fetcher after
# ``_RATE_LIMIT_FETCH_DEADLINE`` (2s). A Keychain prompt that hangs must
# not eat that budget, so the lookup gives up well inside it and the
# fetch reports no reading for this window rather than promoting a
# possibly stale file.
_CLAUDE_KEYCHAIN_TIMEOUT = 1.0


def _read_claude_credentials_file() -> dict | None:
    """The ``claudeAiOauth`` mapping from ``~/.claude/.credentials.json``,
    if it has a str ``accessToken``."""
    # Portable file path, works identically on macOS + Linux + WSL.
    creds_path = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(creds_path):
        try:
            with open(creds_path) as f:
                creds = json.load(f)
        except (OSError, json.JSONDecodeError, RecursionError):
            creds = None
        if isinstance(creds, dict):
            oauth = creds.get("claudeAiOauth") or {}
            if isinstance(oauth, dict):
                if isinstance(oauth.get("accessToken"), str):
                    return oauth
    return None


def _read_claude_keychain_credentials() -> dict | None:
    """The ``claudeAiOauth`` mapping from the macOS Keychain, if it has a
    str ``accessToken``."""
    # macOS Keychain. ``security`` ships with macOS; check via
    # shutil.which to avoid FileNotFoundError on non-Apple platforms.
    if not shutil.which("security"):
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-l", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_KEYCHAIN_TIMEOUT,
        )
    # TimeoutExpired propagates: the caller must not read a slow
    # Keychain as an empty one.
    except OSError:
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        creds = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(creds, dict):
        return None
    oauth = creds.get("claudeAiOauth") or {}
    if not isinstance(oauth, dict):
        return None
    return oauth if isinstance(oauth.get("accessToken"), str) else None


def _read_claude_email() -> str | None:
    """The signed-in address from ``~/.claude.json``'s ``oauthAccount``."""
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError, RecursionError):
        return None
    account = config.get("oauthAccount") if isinstance(config, dict) else None
    email = account.get("emailAddress") if isinstance(account, dict) else None
    return email.strip() if isinstance(email, str) and email.strip() else None


def _claude_expires_at_seconds(oauth: dict) -> float:
    """``expiresAt`` as epoch seconds; accepts ms or s, 0.0 when
    missing/unparseable."""
    expires_at = oauth.get("expiresAt")
    try:
        expires_at = float(expires_at)
        return expires_at / 1000 if expires_at > 100_000_000_000 else expires_at
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _get_claude_oauth_credentials() -> dict | None:
    """Return Claude Code's OAuth credential mapping from the best source.

    Both sources are read and the one with the later ``expiresAt``
    wins; on a tie (e.g. neither records ``expiresAt``) the file keeps
    its preference since it comes first. Claude Code on macOS refreshes
    the Keychain entry and leaves the file behind, and a re-login or
    account switch has to win even while the old file token is still
    unexpired. The Keychain reader is a no-op off macOS
    (``shutil.which("security")`` is ``None``), and the result is cached
    for the rate-limit TTL, so the subprocess cost is one call per
    window.

    A Keychain timeout yields ``None`` rather than the file: the lookup
    failed, so the file's freshness is unknown. ``None`` is also the
    answer if neither source is reachable / parseable. Both paths
    silently degrade: a missing credentials file or missing
    ``security`` binary should never break ``swe list`` rendering.
    """
    file_creds = _read_claude_credentials_file()
    try:
        keychain_creds = _read_claude_keychain_credentials()
    except subprocess.TimeoutExpired:
        # Unknown, not absent. Promoting the file here would make a stale
        # token authoritative, so report no reading for this window; the
        # aggregator keeps the last good value for up to 24h.
        return None
    candidates = [creds for creds in (file_creds, keychain_creds) if creds]
    return max(candidates, key=_claude_expires_at_seconds) if candidates else None


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


def _load_claude_state() -> tuple[RateLimitInfo | None, float, str, float]:
    """Return ``(info, retry_at, credential_fingerprint, fetched_at)``.

    ``info`` is the last successful reading and ``fetched_at`` when it was
    taken. A reading older than ``_STALE_CACHE_TTL``, or one saved before
    ``fetched_at`` was recorded, comes back as ``None``: the fetcher must
    never hand the aggregator a figure it cannot date, because the
    aggregator stamps whatever a fetcher returns as read just now.
    """
    try:
        raw = json.loads(_claude_cache_file().read_text())
        info_raw = raw.get("info")
        info = RateLimitInfo(**info_raw) if isinstance(info_raw, dict) else None
        retry_at = float(raw.get("retry_at") or 0)
        fingerprint = raw.get("credential_fingerprint") or ""
        fetched_at = float(raw.get("fetched_at") or 0)
    except (OSError, ValueError, TypeError, OverflowError,
            json.JSONDecodeError, RecursionError):
        return None, 0.0, "", 0.0
    if not fetched_at or time.time() - fetched_at > _STALE_CACHE_TTL:
        info = None
        fetched_at = 0.0
    return info, retry_at, str(fingerprint), fetched_at


def _save_claude_state(
    info: RateLimitInfo | None,
    retry_at: float = 0.0,
    credential_fingerprint: str = "",
    fetched_at: float | None = None,
) -> None:
    """Persist the last reading. ``fetched_at`` defaults to now when
    ``info`` is a fresh reading; pass the loaded value through when
    re-saving an old one alongside a 429 cooldown."""
    path = _claude_cache_file()
    if fetched_at is None:
        fetched_at = time.time() if info is not None else 0.0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "info": asdict(info) if info is not None else None,
            "retry_at": retry_at,
            "credential_fingerprint": credential_fingerprint,
            "fetched_at": fetched_at,
        }))
    except OSError:
        pass


def _fetch_claude() -> RateLimitInfo | None:
    """Return Claude Code's most limiting subscription usage window."""
    oauth = _get_claude_oauth_credentials()
    token = oauth.get("accessToken") if oauth else None
    if not token:
        return None
    # The OAuth credential itself names no account; the global config
    # does, same file the app's Claude reader asks.
    email = _read_claude_email()

    # quiver never refreshes tokens, so an expired token always 401s.
    # Whether the login is actually dead is the refreshToken's call:
    # Claude Code refreshes transparently on its next call, so an expired
    # access token beside a refreshToken is a live login, and re-login is
    # the wrong advice. Serve the last reading from claude's own cache
    # (≤24h) the way the 429 cooldown does until the CLI self-heals.
    # re-login is kept for an expired token with no refreshToken — the
    # case that genuinely needs `claude auth login`.
    expires_at_seconds = _claude_expires_at_seconds(oauth)
    if expires_at_seconds and expires_at_seconds <= time.time():
        if oauth.get("refreshToken"):
            info, _retry_at, _fp, fetched_at = _load_claude_state()
            if info is not None:
                info.fetched_at = fetched_at
                info.email = info.email or email
            return info
        return RateLimitInfo(
            tool_name="claude",
            used_percent=0,
            limit_reached=False,
            reset_at=0.0,
            plan_type="auth-required",
            window_seconds=0,
            email=email,
        )

    credential_fingerprint = _claude_credential_fingerprint(token)
    stale_info, retry_at, cooldown_fingerprint, fetched_at = (
        _load_claude_state()
    )
    if (
        retry_at > time.time()
        and cooldown_fingerprint == credential_fingerprint
    ):
        if stale_info is not None:
            stale_info.fetched_at = fetched_at
            stale_info.email = stale_info.email or email
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
            # Anthropic is throttling the endpoint: back off, and keep the
            # last reading visible for the cooldown since it is at most a
            # day old (``_load_claude_state`` already dropped anything
            # older).
            wait = retry_after[-1] if retry_after[-1] is not None else 300.0
            _save_claude_state(
                stale_info,
                time.time() + max(60.0, wait),
                credential_fingerprint,
                fetched_at=fetched_at,
            )
            if stale_info is not None:
                stale_info.fetched_at = fetched_at
                stale_info.email = stale_info.email or email
            return stale_info
        # Any other failure (TLS, 401, timeout, network) is reported as no
        # reading. The aggregator's own 24h fallback then shows the last
        # good value with its real age. Returning ``stale_info`` here made
        # a three-week-old figure display as current for as long as the
        # failure lasted, because the aggregator dates a fetcher's return
        # value to the moment it was returned.
        return None

    best_window_key = ""
    best_utilization = -1.0
    best_reset_at = 0.0
    for key in _CLAUDE_WINDOWS:
        window = data.get(key)
        if not isinstance(window, dict):
            continue
        try:
            utilization = float(window.get("utilization"))
        except (TypeError, ValueError, OverflowError):
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
        email=email,
    )
    _save_claude_state(info, credential_fingerprint=credential_fingerprint)
    return info


def _register_claude() -> None:
    """Register the Claude Code rate limit fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_claude()

    def signed_in() -> bool:
        return (_read_claude_credentials_file() is not None
                or _keychain_item_exists("-l", "Claude Code-credentials"))

    register("claude", fetch, signed_in=signed_in)


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
# Apple's unversioned libcrypto stub. Any dlopen of it aborts the process
# with SIGABRT ("loading libcrypto in an unsafe way") rather than raising
# OSError, so it can never be probed. ``find_library('crypto')`` returns it
# on nixpkgs and system Python, and the bare soname resolves to it.
_DARWIN_SYSTEM_LIBCRYPTO = frozenset({"/usr/lib/libcrypto.dylib", "libcrypto.dylib"})


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

    ``ctypes.CDLL(None)`` (process global namespace) is not a general
    candidate: on python.org 3.12 framework it resolves ``EVP_*`` to a
    different-ABI libcrypto already loaded by a system component and
    segfaults inside ``EVP_CipherInit_ex``.

    The bundled python-build-standalone interpreter links OpenSSL
    statically — ``CDLL(None)`` finds EVP_* symbols but the real calls
    segfault, and an external ``/usr/lib`` load under the app's
    signature aborts instead of raising OSError ("loading libcrypto in
    an unsafe way"). The bundled swe therefore sets
    ``QUIVER_BUNDLED_PYTHON`` and only tries dylibs inside the bundle's
    own lib dir, which build-app.sh signs with the app.

    Function signatures are configured once on the cached handle.
    """
    global _LIBCRYPTO, _LIBCRYPTO_TRIED
    if _LIBCRYPTO_TRIED:
        return _LIBCRYPTO
    _LIBCRYPTO_TRIED = True
    import ctypes
    import ctypes.util
    import glob
    import sys
    from pathlib import Path

    if os.environ.get("QUIVER_BUNDLED_PYTHON"):
        # Bundled build: only dylibs the bundle itself ships are safe —
        # they're signed with the app; anything outside can abort the
        # process rather than raise.
        bundle_lib = Path(sys.executable).resolve().parent.parent / "lib"
        candidates = sorted(
            glob.glob(str(bundle_lib / "libcrypto*.dylib")), reverse=True
        )
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
        _configure_evp(lib)
        _LIBCRYPTO = lib
        return lib

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
        if sys.platform == "darwin" and cand in _DARWIN_SYSTEM_LIBCRYPTO:
            continue
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

    _configure_evp(lib)
    _LIBCRYPTO = lib
    return lib


def _configure_evp(lib) -> None:
    import ctypes
    lib.EVP_aes_256_gcm.restype = ctypes.c_void_p
    lib.EVP_aes_128_cbc.restype = ctypes.c_void_p  # Cursor's safeStorage
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
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
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
    /app/auth/me): the QUOTA column budget is unforgiving and we already
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
            except (TypeError, ValueError, OverflowError):
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

    def signed_in() -> bool:
        if os.environ.get("FACTORY_API_KEY", "").strip() or _decrypt_droid_auth_file():
            return True
        return any(_keychain_item_exists("-l", label)
                   for label in ("Factory Safe Storage", "Factory Key"))

    register("droid", fetch, signed_in=signed_in)


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
    except (json.JSONDecodeError, OSError, RecursionError):
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
        except (TypeError, ValueError, OverflowError):
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
        except (TypeError, ValueError, OverflowError):
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

    register("freebuff", fetch,
             signed_in=lambda: bool(_get_freebuff_access_token()),
             empty_is_normal=True)


# ---------------------------------------------------------------------------
# Cursor fetcher
# ---------------------------------------------------------------------------

_CURSOR_USAGE_URL = "https://cursor.com/api/dashboard/get-current-period-usage"
_CURSOR_HARD_LIMIT_URL = "https://cursor.com/api/dashboard/get-hard-limit"
_CURSOR_PLAN_INFO_URL = "https://cursor.com/api/dashboard/get-plan-info"
# Grok Bot's own weekly allowance, apart from the billing cycle. A Connect
# RPC on the editor's API host that takes the session JWT as a bearer token.
_CURSOR_GROK_BOT_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetSandUsageStatus"
_WEEK_SECONDS = 7 * 24 * 60 * 60
# How long the plan, hard-limit and Grok Bot calls may take, counted from the
# start of the fetch. Grok Bot's host is a second TLS connection, which on a
# cold fetch can run past a second; waiting costs nothing when they are fast.
# A slower extra is dropped rather than waited for.
_CURSOR_EXTRAS_BUDGET = 3.0

# The aggregator's cutoff (``time.monotonic()``) for the fetch running on
# this thread, so a fetcher's optional calls can end before its caller stops
# waiting. Unset outside ``get_all_rate_limits``.
_fetch_cutoff = threading.local()
# Why the fetch running on this thread failed, as ``_fetch_json`` saw it:
# "auth" on an HTTP 401, "network" for everything else (unreachable host,
# timeout, unreadable body). A fetch worker reads it after an empty fetch
# to tell a dead credential from an outage.
_json_failures = threading.local()


def _note_json_failure(kind: str) -> None:
    kinds = getattr(_json_failures, "kinds", None)
    if kinds is None:
        kinds = _json_failures.kinds = []
    kinds.append(kind)


def _reset_json_failures() -> None:
    _json_failures.kinds = []


def _json_failure_kinds() -> list:
    return list(getattr(_json_failures, "kinds", ()))

# How long before that cutoff the optional calls are given up on, so the
# reading is back in time.
_EXTRAS_CUTOFF_MARGIN = 0.15

# Where the Cursor editor keeps its session tokens, per platform. The
# ``ItemTable`` row ``cursorAuth/accessToken`` is the same session JWT the
# ``cursor-agent`` CLI stores in the macOS keychain, so either source works.
_CURSOR_STATE_DB_CANDIDATES: tuple[str, ...] = (
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
    "~/.config/Cursor/User/globalStorage/state.vscdb",
    "~/AppData/Roaming/Cursor/User/globalStorage/state.vscdb",
)

# Cursor's usage figures, as payload field and column label.
# ``totalPercentUsed`` is the plan's Included figure (the dashboard's "You've
# used 50% of your included total usage" against ``totalPercentUsed: 50.2``), ``autoPercentUsed`` the Auto model's and ``apiPercentUsed``
# a named model's, the three rows `cursor-agent usage` shows. All are listed;
# the most-used one is surfaced, as for Claude.
_CURSOR_BUCKETS: tuple[tuple[str, str], ...] = (
    ("totalPercentUsed", "included"),
    ("autoPercentUsed", "auto"),
    ("apiPercentUsed", "api"),
)


def _read_cursor_state_row(path: str, keys: tuple[str, ...]) -> dict:
    """The given ``ItemTable`` values from one ``state.vscdb`` file."""
    uri = "file:" + urllib.parse.quote(path) + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error:
        return {}
    out = {}
    try:
        for key in keys:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                continue
            value = row[0]
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
    except sqlite3.Error:
        pass
    finally:
        connection.close()
    return out


def _read_cursor_state_value(
    key: str,
    candidates: tuple[str, ...] | None = None,
) -> str | None:
    """Return one ``ItemTable`` value from the editor's ``state.vscdb``.

    ``candidates`` overrides the default search list — an extra account's
    ``--user-data-dir`` db is passed as the sole candidate so an empty
    account can never fall through to the default account's data.
    """
    for candidate in (
        candidates if candidates is not None else _CURSOR_STATE_DB_CANDIDATES
    ):
        path = os.path.expanduser(candidate)
        if not os.path.isfile(path):
            continue
        value = _read_cursor_state_row(path, (key,)).get(key)
        if value is not None:
            return value
    return None


def _read_cursor_state_token(
    candidates: tuple[str, ...] | None = None,
) -> str | None:
    """Return the editor's session token from its ``state.vscdb`` store.

    ``candidates`` overrides the default search list — an extra account's
    ``--user-data-dir`` db is passed as the sole candidate so an empty
    account can never fall through to the default account's token.
    """
    return _read_cursor_state_value("cursorAuth/accessToken", candidates)


def _read_cursor_keychain_token() -> str | None:
    """Return the ``cursor-agent`` CLI's session token from the keychain."""
    if not shutil.which("security"):
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", "cursor-access-token", "-a", "cursor-user", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    token = (result.stdout or "").strip()
    return token or None


# Where ``cursor-agent`` keeps its session off macOS (on a Mac it uses the
# keychain): ``{"accessToken": <jwt>, "refreshToken": <jwt>}``.
_CURSOR_CLI_AUTH_FILE = "~/.config/cursor/auth.json"


def _read_cursor_cli_token(path: str | None = None) -> str | None:
    """Return the ``cursor-agent`` CLI's session token from its auth file."""
    try:
        with open(os.path.expanduser(path or _CURSOR_CLI_AUTH_FILE)) as f:
            auth = json.load(f)
    except (json.JSONDecodeError, OSError, RecursionError, UnicodeDecodeError):
        return None
    token = auth.get("accessToken") if isinstance(auth, dict) else None
    return token.strip() if isinstance(token, str) and token.strip() else None


# The editor keeps its session tokens Electron-safeStorage-encrypted as
# ``cursor-safe-storage:v1:<base64>``. On macOS the base64 is Chromium's
# OSCrypt blob: ``v10`` then AES-128-CBC (PKCS#7, an IV of 16 spaces)
# under PBKDF2-HMAC-SHA1(<"Cursor Safe Storage" keychain password>,
# "saltysalt", 1003 rounds, 16 bytes). OSCrypt on macOS writes only
# ``v10``; any other version is left undecrypted.
_CURSOR_SAFE_STORAGE_PREFIX = "cursor-safe-storage:v1:"
_CURSOR_SAFE_STORAGE_SERVICE = "Cursor Safe Storage"
# Reading the password puts up an "allow access" dialog until the user
# trusts ``security`` on the item, and ``security`` waits on it. Give up
# quickly (the whole fetch has a 2 s deadline), and after a read that may
# have shown that dialog, don't ask again for a while, so a background
# refresh never puts it up every few minutes.
_CURSOR_SAFE_STORAGE_TIMEOUT = 1.0
_CURSOR_SAFE_STORAGE_BACKOFF = 6 * 3600
# ``security`` exits with the low byte of the OSStatus:
# errSecItemNotFound (-25300) is 44, and no dialog was shown.
_SECURITY_ITEM_NOT_FOUND = 44
# The derived key, once read in this process, so the fetch, the usage
# events and the diagnosis share one keychain read.
_cursor_safe_storage_key_cache: bytes | None = None


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _cursor_safe_storage_backoff_file():
    return RATE_LIMITS_CACHE_FILE.with_name("cursor_safe_storage_backoff")


def _cursor_safe_storage_ciphertext(value) -> bytes | None:
    """The AES-128-CBC ciphertext inside a ``cursor-safe-storage:`` value,
    or None when it is not a ``v10`` blob this reader can decrypt."""
    if not isinstance(value, str) or not value.startswith(
            _CURSOR_SAFE_STORAGE_PREFIX):
        return None
    try:
        raw = base64.b64decode(value[len(_CURSOR_SAFE_STORAGE_PREFIX):],
                               validate=True)
    except ValueError:
        return None
    body = raw[3:]
    if raw[:3] != b"v10" or not body or len(body) % 16:
        return None
    return body


def _cursor_safe_storage_key() -> bytes | None:
    """The editor's safeStorage AES key, from the macOS keychain.

    Absent off macOS, when the item is missing, when the read times out
    or is refused, and for a while after any read that may have shown the
    access dialog (see ``_CURSOR_SAFE_STORAGE_BACKOFF``). The app reads
    several Cursor accounts at once, each in its own ``swe``, so the read
    runs under a lock shared by every process: only one of them can put
    the dialog up, and the others find its backoff once they get in.
    """
    global _cursor_safe_storage_key_cache
    if _cursor_safe_storage_key_cache is not None:
        return _cursor_safe_storage_key_cache
    if not _is_macos() or not shutil.which("security"):
        return None
    backoff = _cursor_safe_storage_backoff_file()
    import fcntl
    try:
        backoff.parent.mkdir(parents=True, exist_ok=True)
        lock = open(backoff.with_name("cursor_safe_storage.lock"), "a")
    except OSError:
        return None
    with lock:
        deadline = time.monotonic() + _CURSOR_SAFE_STORAGE_TIMEOUT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return None  # another process is still asking
                time.sleep(0.05)
            except OSError:
                return None
        key = _read_cursor_safe_storage_key(backoff)
    if key is not None:
        _cursor_safe_storage_key_cache = key
    return key


def _read_cursor_safe_storage_key(backoff) -> bytes | None:
    """``_cursor_safe_storage_key``'s keychain read, run under its lock."""
    try:
        if time.time() - os.path.getmtime(backoff) < _CURSOR_SAFE_STORAGE_BACKOFF:
            return None
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w",
             "-s", _CURSOR_SAFE_STORAGE_SERVICE],
            capture_output=True,
            text=True,
            timeout=_CURSOR_SAFE_STORAGE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        result = None
    except OSError:
        return None
    if result is None or result.returncode not in (0, _SECURITY_ITEM_NOT_FOUND):
        # Timed out on the dialog, or the user refused it.
        try:
            backoff.touch()
        except OSError:
            pass
        return None
    password = (result.stdout or "").strip()
    if result.returncode != 0 or not password:
        return None
    return hashlib.pbkdf2_hmac(
        "sha1", password.encode("utf-8"), b"saltysalt", 1003, 16)


def _aes_128_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes | None:
    """PKCS#7-unpadded plaintext, or None (no libcrypto, bad padding)."""
    lib = _load_libcrypto()
    if lib is None or not hasattr(lib, "EVP_aes_128_cbc"):
        return None
    import ctypes
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        return None
    try:
        if lib.EVP_CipherInit_ex(ctx, lib.EVP_aes_128_cbc(), None,
                                 key, iv, 0) != 1:
            return None
        out = ctypes.create_string_buffer(len(data) + 16)
        outl = ctypes.c_int(0)
        if lib.EVP_CipherUpdate(ctx, out, ctypes.byref(outl),
                                data, len(data)) != 1:
            return None
        fin = ctypes.create_string_buffer(32)
        finl = ctypes.c_int(0)
        if lib.EVP_CipherFinal_ex(ctx, fin, ctypes.byref(finl)) != 1:
            return None  # bad padding: the wrong key or a corrupt blob
        return out.raw[:outl.value] + fin.raw[:finl.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def _decrypt_cursor_safe_storage(value, cached_key_only: bool = False) -> str | None:
    """The session JWT inside a ``cursor-safe-storage:`` value, or None.

    The keychain is asked only once a decryptable blob is in hand (never
    with ``cached_key_only``), and only a result that decodes as a JWT is
    returned, so a blob (or what a wrong key makes of it) is never sent
    as a token.
    """
    global _cursor_safe_storage_key_cache
    body = _cursor_safe_storage_ciphertext(value)
    if body is None:
        return None
    key = (_cursor_safe_storage_key_cache if cached_key_only
           else _cursor_safe_storage_key())
    if key is None:
        return None
    plain = _aes_128_cbc_decrypt(key, b" " * 16, body)
    if plain is None:
        # A rotated key: read the keychain again next time.
        _cursor_safe_storage_key_cache = None
        return None
    try:
        token = plain.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return token if token and _cursor_token_claims(token) is not None else None


def _read_cursor_state_session(
    candidates: tuple[str, ...] | None = None,
) -> str | None:
    """The editor's session JWT from ``state.vscdb``, decrypted when the
    editor stored it safeStorage-encrypted."""
    value = _read_cursor_state_token(candidates)
    if value and _cursor_token_claims(value) is not None:
        return value
    return _decrypt_cursor_safe_storage(value)


def _cursor_token_expired(claims: dict) -> bool:
    expires_at = claims.get("exp")
    return (
        isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and expires_at <= time.time()
    )


def _get_cursor_access_token() -> str | None:
    """First usable session token: editor store, then the CLI's auth file
    (Linux), then its keychain entry (macOS), then the editor's
    safeStorage-encrypted copy. Decrypting comes last because it reads a
    keychain item the user may have to allow access to. A value that
    doesn't decode as a JWT is skipped, and an expired token only wins
    when no source has a live one, so a stale CLI login never hides the
    editor's current session."""
    expired = None
    for read in (_read_cursor_state_token, _read_cursor_cli_token,
                 _read_cursor_keychain_token,
                 lambda: _decrypt_cursor_safe_storage(_read_cursor_state_token())):
        token = read()
        claims = _cursor_token_claims(token) if token else None
        if claims is None:
            continue
        if not _cursor_token_expired(claims):
            return token
        expired = expired or token
    return expired


def _diagnose_cursor_credentials(
    candidates: tuple[str, ...] | None = None,
) -> str | None:
    """Cursor's credential verdict, as an issue status.

    Runs the same fall-through ``_get_cursor_access_token`` does, but
    names why it came back empty: no store at all (signed-out), a store
    macOS will not let us read (permission-required), or a store holding
    only an opaque, non-JWT blob: safeStorage-encrypted with a key we
    could not read, or worse (auth-required: a stored login this build
    cannot use). ``ok`` means a usable token was found and the fetch
    itself is what should be judged.

    ``candidates`` pins the check to an extra account's ``state.vscdb``;
    the CLI auth file and the keychain are the default account's, so a
    pinned check never falls through to them.
    """
    denied = False
    saw_opaque = False
    blobs = []
    for candidate in (candidates if candidates is not None
                      else _CURSOR_STATE_DB_CANDIDATES):
        path = os.path.expanduser(candidate)
        if not os.path.isfile(path):
            continue
        if not os.access(path, os.R_OK):
            denied = True
            continue
        value = _read_cursor_state_value("cursorAuth/accessToken", (candidate,))
        if value is None:
            continue
        if _cursor_token_claims(value) is not None:
            return remedies.OK
        blobs.append(value)
    if candidates is None:
        # ``_read_cursor_cli_token`` swallows PermissionError like the
        # other readers, so the denied file is probed before reading it —
        # a file macOS will not open is permission-required, not
        # signed-out.
        cli_auth = os.path.expanduser(_CURSOR_CLI_AUTH_FILE)
        if os.path.isfile(cli_auth) and not os.access(cli_auth, os.R_OK):
            denied = True
        token = _read_cursor_cli_token()
        if token and _cursor_token_claims(token) is not None:
            return remedies.OK
        if _keychain_item_exists("-s", "cursor-access-token",
                                 "-a", "cursor-user"):
            return remedies.OK
        if token:
            # The CLI's stored token is unusable and nothing else decoded.
            saw_opaque = True
    # Decrypting reads the keychain, so it is the last thing tried, as
    # in ``_get_cursor_access_token``.
    if any(_decrypt_cursor_safe_storage(blob) is not None for blob in blobs):
        return remedies.OK
    saw_opaque = saw_opaque or bool(blobs)
    if denied:
        # A file we cannot read may hold the real token: the permission
        # grant is the first fix, whatever the readable files hold.
        return remedies.PERMISSION_REQUIRED
    if saw_opaque:
        return remedies.AUTH_REQUIRED
    return remedies.SIGNED_OUT


def _jwt_payload(token) -> dict | None:
    """Decode a JWT payload without verifying the signature; display
    fields only (``sub``, ``exp``, ``email``), the server still checks."""
    if not isinstance(token, str):
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, RecursionError):
        return None
    return claims if isinstance(claims, dict) else None


def _cursor_token_claims(token: str) -> dict | None:
    """Decode the JWT payload without verifying it; only ``sub`` and
    ``exp`` are read, and the server still checks the signature."""
    return _jwt_payload(token)


def _cursor_session_cookie(token: str, claims: dict) -> str | None:
    """Cursor's dashboard authenticates with ``WorkosCursorSessionToken``,
    whose value is ``<sub>::<jwt>`` URL-encoded. A bearer header is
    rejected with 401, so this is the only way in."""
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    value = urllib.parse.quote(f"{sub}::{token}", safe="")
    return f"WorkosCursorSessionToken={value}"


# The last second of year 9999: no real Cursor timestamp lies past it, and
# datetime cannot hold one that does.
_CURSOR_MAX_EPOCH = 253_402_300_799.0
# A usage event stamped further ahead than this is junk, not clock skew.
_CURSOR_EVENT_MAX_AHEAD = 24 * 60 * 60


def _cursor_ms_to_epoch(value) -> float:
    """Cursor sends epoch milliseconds as decimal strings; accept a bare
    number too. ``bool`` is excluded because it subclasses ``int``. A value
    past year 9999 reads as 0.0 (unknown), like any other junk: the Mac
    app turns these into Int64 milliseconds, which traps far below the
    largest float."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number) or number <= 0:
        return 0.0
    seconds = number / 1000 if number > 100_000_000_000 else number
    return seconds if seconds <= _CURSOR_MAX_EPOCH else 0.0


def _parse_cursor_usage(data: dict) -> RateLimitInfo | None:
    """Surface the more exhausted of Cursor's two included-usage figures."""
    plan = data.get("planUsage")
    if not isinstance(plan, dict):
        return None
    reset_at = _cursor_ms_to_epoch(data.get("billingCycleEnd"))
    cycle_start = _cursor_ms_to_epoch(data.get("billingCycleStart"))
    window_seconds = (
        int(reset_at - cycle_start)
        if reset_at and cycle_start and reset_at > cycle_start
        else 0
    )
    best_label = ""
    best_percent = -1.0
    windows = []
    for name, label in _CURSOR_BUCKETS:
        try:
            percent = float(plan.get(name))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(percent):
            continue
        windows.append(_window(label, percent, reset_at, window_seconds))
        if percent > best_percent:
            best_percent = percent
            best_label = label
    if best_percent < 0:
        return None

    # A team seat's spend limit is the team's pool; the CLI's "Usage · Team".
    spend = data.get("spendLimitUsage")
    limit_type = spend.get("limitType") if isinstance(spend, dict) else None
    plan_type = limit_type.strip().lower() if isinstance(limit_type, str) and limit_type.strip() else "—"
    used_percent = int(round(min(100.0, max(0.0, best_percent))))
    return RateLimitInfo(
        tool_name="cursor",
        used_percent=used_percent,
        limit_reached=best_percent >= 100,
        reset_at=reset_at,
        plan_type=plan_type,
        window_seconds=window_seconds,
        window=best_label,
        windows=windows,
    )


def _cursor_grok_bot_window(data: dict | None) -> dict | None:
    """Grok Bot's weekly window, or None when there is no personal one.

    A seat drawing on a pooled enterprise allowance, or one with a zero
    included limit, has no meter of its own, so it gets no window.
    """
    if not isinstance(data, dict):
        return None
    if data.get("usesPooledEnterpriseAllowance") is True:
        return None
    if data.get("hasNonZeroIncludedLimit") is False or data.get("includedLimitZero") is True:
        return None
    percent = data.get("usagePercent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    if not math.isfinite(percent) or percent < 0:
        return None

    def epoch(value) -> float:
        return _parse_iso8601_to_epoch(value) if isinstance(value, str) and not value.strip().isdigit() \
            else _cursor_ms_to_epoch(value)

    reset_at = epoch(data.get("nextResetTimestampUtc"))
    start = epoch(data.get("currentPeriodStart"))
    seconds = int(reset_at - start) if reset_at and start and reset_at > start else _WEEK_SECONDS
    return _window("grok_bot", float(percent), reset_at, seconds)


# `cursor-agent` treats a hard limit at int32 max as "no monthly limit".
_CURSOR_UNLIMITED = 2147483647


def _cursor_on_demand(spend: dict | None, hard: dict | None) -> dict:
    """On-demand usage the way `cursor-agent usage` words it.

    ``spend`` is the usage reply's ``spendLimitUsage`` (cents; a zero field
    is left out of the reply), ``hard`` the ``get-hard-limit`` reply
    (dollars) or None when that call failed. Returns ``state``: "fixed" (a
    cap, ``limit_usd``), "unlimited", "disabled" or "unavailable"; ``scope``:
    "team-member" for a team seat with no personal cap ("No personal
    limit"), else "personal"; and ``used_usd``. Ported from the CLI's
    usage-data module so Quiver says what it says.
    """
    spend = spend if isinstance(spend, dict) else {}

    def number(value) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if math.isfinite(result) else None

    used = round((number(spend.get("individualUsed")) or 0.0) / 100, 2)

    def state(name: str, scope: str = "personal", limit: float | None = None) -> dict:
        return {"state": name, "scope": scope, "used_usd": used, "limit_usd": limit}

    personal = None
    individual = number(spend.get("individualLimit"))
    if individual is not None:
        personal = state("fixed", limit=round(individual / 100, 2)) if individual > 0 else state("disabled")
    hard_limit = number(hard.get("hardLimit")) if isinstance(hard, dict) else None
    no_usage = isinstance(hard, dict) and hard.get("noUsageBasedAllowed") is True

    if spend.get("limitType") == "team":
        if personal is not None:
            return personal
        if isinstance(hard, dict):
            if no_usage or (hard_limit or 0) <= 0:
                return state("disabled")
            return state("unlimited", "team-member")
        if (number(spend.get("pooledLimit")) or 0) > 0:
            return state("unlimited", "team-member")
        return state("unavailable")
    if not isinstance(hard, dict):
        return personal or state("unavailable")
    if no_usage:
        return state("disabled")
    if (hard_limit or 0) >= _CURSOR_UNLIMITED:
        return state("unlimited")
    if (hard_limit or 0) > 0:
        return state("fixed", limit=hard_limit)
    return state("disabled")


def _warn_cursor_401() -> None:
    import sys
    print(
        "Cursor usage endpoint returned 401. Sign in to the Cursor app or "
        "run `cursor-agent login` to renew the session.",
        file=sys.stderr,
    )


# The identity rows the editor writes beside the session token: the
# address itself and the profile it belongs to.
_CURSOR_IDENTITY_KEYS = (
    "cursorAuth/accessToken",
    "cursorAuth/cachedEmail",
    "cursorAuth/cachedScopedProfile",
)


def _cursor_email(state_db: str | None, token: str, claims: dict) -> str | None:
    """The address of the account whose token fetched usage, or None.

    ``cachedEmail`` is the only place the address exists — the JWT
    itself has no email claim. A token read from the same db row set is
    that account's by construction, but the CLI's auth-file or keychain
    token can belong to a different sign-in than the editor's. Then the
    email only counts once the ``cachedScopedProfile`` authId stored
    beside it matches the token's own ``sub``: an editor signed in as
    someone else must not lend its address to the CLI account's usage.
    """
    candidates = (
        (state_db,) if state_db is not None else _CURSOR_STATE_DB_CANDIDATES
    )
    rows = []
    for candidate in candidates:
        path = os.path.expanduser(candidate)
        if os.path.isfile(path):
            rows.append(_read_cursor_state_row(path, _CURSOR_IDENTITY_KEYS))
    for row in rows:
        stored = row.get("cursorAuth/accessToken")
        # A decrypted token came from a key already read, so matching it
        # against an encrypted row never asks the keychain.
        if stored == token or _decrypt_cursor_safe_storage(
                stored, cached_key_only=True) == token:
            return row.get("cursorAuth/cachedEmail")
    for row in rows:
        profile = row.get("cursorAuth/cachedScopedProfile")
        try:
            parsed = json.loads(profile) if profile else None
        except (ValueError, RecursionError):
            parsed = None
        # The row is the editor's to write: JSON that is not an object
        # (a list, a string) used to raise AttributeError here, which threw
        # away the whole Cursor reading over a display-only email.
        auth_id = parsed.get("authId") if isinstance(parsed, dict) else None
        if isinstance(auth_id, str) and auth_id and auth_id == claims.get("sub"):
            return row.get("cursorAuth/cachedEmail")
    return None


def _fetch_cursor(state_db: str | None = None) -> RateLimitInfo | None:
    """Read the current billing period's included usage for Cursor.

    ``state_db`` points at an extra account's ``state.vscdb``: the token
    then comes from that file alone, never the keychain, so a signed-out
    account reports nothing rather than the default account's usage.
    """
    token = (
        _read_cursor_state_session((state_db,))
        if state_db is not None
        else _get_cursor_access_token()
    )
    if not token:
        return None
    claims = _cursor_token_claims(token)
    if claims is None:
        return None
    email = _cursor_email(state_db, token, claims)

    expires_at = claims.get("exp")
    if (
        isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and expires_at <= time.time()
    ):
        # Neither token source can be refreshed from here; the app or the
        # CLI has to log in again. Say so instead of sending a dead token.
        return RateLimitInfo(
            tool_name="cursor",
            used_percent=0,
            limit_reached=False,
            reset_at=0.0,
            plan_type="auth-required",
            window_seconds=0,
            email=email,
        )

    cookie = _cursor_session_cookie(token, claims)
    if cookie is None:
        return None

    def post(url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            data=b"{}",
            method="POST",
            headers={
                "Cookie": cookie,
                # The dashboard API refuses any POST without a first-party
                # Origin ("Invalid origin for state-changing request", 403),
                # even though these calls only read.
                "Origin": "https://cursor.com",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"quiver/{__version__}",
            },
        )

    # The plan's name and the on-demand limit, as `cursor-agent usage` reads
    # them. Both are extras: a failure leaves the usage reading as it is.
    def optional(url: str) -> dict | None:
        try:
            reply = _fetch_json(post(url))
        except Exception:
            return None
        return reply if isinstance(reply, dict) else None

    def fetch_grok_bot() -> None:
        try:
            reply = _fetch_json(urllib.request.Request(
                _CURSOR_GROK_BOT_URL,
                data=b"{}",
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Connect-Protocol-Version": "1",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": f"quiver/{__version__}",
                },
            ))
        except Exception:
            reply = None
        extras[_CURSOR_GROK_BOT_URL] = reply if isinstance(reply, dict) else None

    # The extras run alongside the usage call, as the CLI's do, on daemon
    # threads with their own small budget: a slow one is given up on rather
    # than waited for, so it can't push the usage reading past the
    # aggregator's deadline, and it never holds up the process at exit.
    extras: dict[str, dict | None] = {}

    def fetch_extra(url: str) -> None:
        extras[url] = optional(url)

    started = time.monotonic()
    workers = [
        threading.Thread(target=fetch_extra, args=(url,), daemon=True)
        for url in (_CURSOR_HARD_LIMIT_URL, _CURSOR_PLAN_INFO_URL)
    ] + [threading.Thread(target=fetch_grok_bot, daemon=True)]
    for worker in workers:
        worker.start()
    data = _fetch_json(post(_CURSOR_USAGE_URL), on_401=_warn_cursor_401)
    extras_end = started + _CURSOR_EXTRAS_BUDGET
    cutoff = getattr(_fetch_cutoff, "at", None)
    if cutoff is not None:
        extras_end = min(extras_end, cutoff - _EXTRAS_CUTOFF_MARGIN)
    for worker in workers:
        worker.join(max(0.0, extras_end - time.monotonic()))
    hard = extras.get(_CURSOR_HARD_LIMIT_URL)
    plan_info = extras.get(_CURSOR_PLAN_INFO_URL)
    info = _parse_cursor_usage(data) if isinstance(data, dict) else None
    if info is None:
        return None
    info.email = email
    plan = plan_info.get("planInfo") if plan_info else None
    plan_name = plan.get("planName") if isinstance(plan, dict) else None
    if isinstance(plan_name, str) and plan_name.strip():
        info.plan_type = plan_name.strip().lower()
    info.on_demand = _cursor_on_demand(data.get("spendLimitUsage"), hard)
    # Listed with the rest but never the headline: running out of Grok Bot
    # leaves Cursor's own allowance as it is.
    grok_reply = extras.get(_CURSOR_GROK_BOT_URL)
    if grok_reply is None:
        # The worker missed its budget or the call failed: the meter exists
        # but went unread, so the window stays listed, marked unavailable,
        # rather than silently vanishing until the cache expires.
        info.windows.append(
            _window("grok_bot", 0.0, 0.0, _WEEK_SECONDS, unavailable=True))
    else:
        grok_bot = _cursor_grok_bot_window(grok_reply)
        if grok_bot is not None:
            info.windows.append(grok_bot)
    return info


# Every usage event the dashboard lists for the account: the editor,
# `cursor-agent`, Cursor cloud agents and Grok Bot (which signs in with the
# Cursor account). Paged, 1000 a page, oldest-first order is not promised,
# so a page is read whole and paging stops on a short page.
_CURSOR_USAGE_EVENTS_URL = (
    "https://cursor.com/api/dashboard/get-filtered-usage-events")
# The dashboard rejects windows beyond 180 days.
_CURSOR_USAGE_EVENTS_MAX_AGE = 180 * 24 * 60 * 60
_CURSOR_USAGE_EVENTS_PAGE_SIZE = 1000
_CURSOR_USAGE_EVENTS_MAX_PAGES = 50
# A window is walked oldest-first in slices this long: a runaway page
# count then pins down to the slice that overflowed instead of silently
# truncating the whole window.
_CURSOR_USAGE_EVENTS_CHUNK = 7 * 24 * 60 * 60


def _usage_event_fields(event: dict, now: float | None = None) -> dict | None:
    """One ``usageEventsDisplay`` entry mapped to the CLI's keys.

    ``tokenUsage`` is a per-request counter, so missing fields are zeros,
    not unknowns. ``totalCents`` is the charge Cursor itself computed, in
    US cents; absent means unpriced rather than free. An event stamped more
    than a day past ``now`` is dropped: the Mac app's feed takes the newest
    event time as where the next poll starts, so one would hide every real
    event after it.
    """
    timestamp = _cursor_ms_to_epoch(event.get("timestamp"))
    if not timestamp:
        return None
    if now is not None and timestamp > now + _CURSOR_EVENT_MAX_AHEAD:
        return None
    usage = event.get("tokenUsage")
    usage = usage if isinstance(usage, dict) else {}

    def tokens(key: str) -> int:
        value = usage.get(key)
        if isinstance(value, bool):
            return 0
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            # OverflowError: int() on a float("inf") parsed from 1e999.
            return 0
        return number if number > 0 else 0

    cents = usage.get("totalCents")
    cost = None
    if isinstance(cents, (int, float)) and not isinstance(cents, bool):
        try:
            # JSON carries a 400-digit integer that float() cannot
            # convert; isfinite() on the int would raise the same way.
            cents_f = float(cents)
        except OverflowError:
            cents_f = float("nan")
        if math.isfinite(cents_f):
            cost = cents_f / 100
    conversation = event.get("conversationId")
    return {
        "timestamp": timestamp,
        "model": str(event.get("model") or ""),
        "kind": str(event.get("kind") or ""),
        "conversation_id": conversation if isinstance(conversation, str) else None,
        "headless": bool(event.get("isHeadless")),
        "input_tokens": tokens("inputTokens"),
        "output_tokens": tokens("outputTokens"),
        "cache_read_tokens": tokens("cacheReadTokens"),
        "cache_write_tokens": tokens("cacheWriteTokens"),
        "cost_usd": cost,
    }


def fetch_cursor_usage_events(
    state_db: str | None = None,
    since: float | None = None,
    now: float | None = None,
) -> tuple[str, list[dict], str | None]:
    """Read one Cursor account's usage events, oldest requested first.

    ``state_db`` pins the read to an extra account's ``state.vscdb`` — its
    token only, never the default account's or the keychain's. ``since``
    is epoch seconds, clamped to the 180 days the dashboard serves.

    Returns ``(status, events, account_id)``: ``ok`` with the event list,
    ``incomplete`` when a 7-day slice of the window still filled all 50
    pages (the events read are kept, but the slice may have more),
    ``signed-out``/``auth-required`` with an empty list when there is no
    usable token, ``unavailable`` on a network, HTTP or malformed-page
    failure so the caller can keep its stored data and report the outage.
    ``account_id`` is the token's ``sub`` claim — Cursor's user id, not a
    credential — so callers can deduplicate profiles that share an
    account; it is None whenever there is no usable token.
    """
    token = (
        _read_cursor_state_session((state_db,))
        if state_db is not None
        else _get_cursor_access_token()
    )
    if not token:
        return "signed-out", [], None
    claims = _cursor_token_claims(token)
    if claims is None:
        return "signed-out", [], None
    sub = claims.get("sub")
    account_id = sub if isinstance(sub, str) and sub else None
    expires_at = claims.get("exp")
    if (
        isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and expires_at <= time.time()
    ):
        return "auth-required", [], account_id
    cookie = _cursor_session_cookie(token, claims)
    if cookie is None:
        return "signed-out", [], account_id

    now = time.time() if now is None else now
    start = now - _CURSOR_USAGE_EVENTS_MAX_AGE
    if since is not None:
        try:
            start = max(start, min(float(since), now))
        except (TypeError, ValueError, OverflowError):
            pass

    status = "ok"

    def post(chunk_start: float, chunk_end: float, page: int) -> urllib.request.Request:
        body = json.dumps({
            "teamId": 0,
            "startDate": str(int(chunk_start * 1000)),
            "endDate": str(int(chunk_end * 1000)),
            "page": page,
            "pageSize": _CURSOR_USAGE_EVENTS_PAGE_SIZE,
        }).encode()
        return urllib.request.Request(
            _CURSOR_USAGE_EVENTS_URL,
            data=body,
            method="POST",
            headers={
                "Cookie": cookie,
                "Origin": "https://cursor.com",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"quiver/{__version__}",
            },
        )

    def expired() -> None:
        nonlocal status
        status = "auth-required"

    def failed(code: int, retry_after: float | None) -> None:
        nonlocal status
        status = "unavailable"

    events: list[dict] = []
    chunk_start = start
    while chunk_start < now:
        # Half-open slices so an event on a boundary is never read twice.
        chunk_end = min(chunk_start + _CURSOR_USAGE_EVENTS_CHUNK, now)
        for page in range(1, _CURSOR_USAGE_EVENTS_MAX_PAGES + 1):
            reply = _fetch_json(
                post(chunk_start, chunk_end, page),
                on_401=expired, on_http_error=failed)
            # "incomplete" from a previous slice is not an error here.
            if status in ("auth-required", "unavailable"):
                return status, [], account_id
            if not isinstance(reply, dict):
                # Network errors report nothing at all through _fetch_json:
                # None here is an outage.
                return "unavailable", [], account_id
            rows = reply.get("usageEventsDisplay")
            if rows is None:
                # Cursor omits the list entirely on a window with no events.
                rows = []
            if not isinstance(rows, list):
                # A page with a different shape is an outage.
                return "unavailable", [], account_id
            for row in rows:
                if isinstance(row, dict):
                    mapped = _usage_event_fields(row, now)
                    if mapped is not None:
                        events.append(mapped)
            if len(rows) < _CURSOR_USAGE_EVENTS_PAGE_SIZE:
                break
        else:
            # A slice that filled every allowed page may still have more.
            status = "incomplete"
        chunk_start = chunk_end + 0.001
    return status, events, account_id


def _register_cursor() -> None:
    """Register the Cursor included-usage fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_cursor()

    def signed_in() -> bool:
        # A stored blob the JWT decoder rejects (the editor's
        # cursor-safe-storage value) is not a credential. The keychain
        # entry is probed without ``-w``: reading it back to decode would
        # read the secret and could prompt.
        for read in (_read_cursor_state_token, _read_cursor_cli_token):
            token = read()
            if token and _cursor_token_claims(token) is not None:
                return True
        if _keychain_item_exists("-s", "cursor-access-token",
                                 "-a", "cursor-user"):
            return True
        # An encrypted editor token counts once its key is there too.
        return (_cursor_safe_storage_ciphertext(_read_cursor_state_token())
                is not None
                and _keychain_item_exists("-s", _CURSOR_SAFE_STORAGE_SERVICE))

    register("cursor", fetch, signed_in=signed_in,
             diagnose=_diagnose_cursor_credentials)


# ---------------------------------------------------------------------------
# Devin fetcher
# ---------------------------------------------------------------------------

_DEVIN_CREDENTIALS_PATH = "~/.local/share/devin/credentials.toml"
_DEVIN_DEFAULT_API_SERVER = "https://server.codeium.com"
_DEVIN_USER_STATUS_RPC = (
    "/exa.seat_management_pb.SeatManagementService/GetUserStatus"
)

# Devin's quota windows as they appear under ``userStatus.planStatus``:
# (remaining-percent field, reset field, column label). Both are listed;
# the most-used window is surfaced, as for Claude.
_DEVIN_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("dailyQuotaRemainingPercent", "dailyQuotaResetAtUnix", "1d"),
    ("weeklyQuotaRemainingPercent", "weeklyQuotaResetAtUnix", "7d"),
)


def _read_devin_credentials(path: str | None = None) -> tuple[str, str] | None:
    """Return ``(api_key, api_server_url)`` from Devin's credentials file.

    The CLI is Cognition's Windsurf-backed agent, so the key is named
    ``windsurf_api_key`` and the server is Windsurf's. The file is TOML,
    read with stdlib ``tomllib``. ``path`` overrides the default
    ``~/.local/share/devin/credentials.toml`` for an extra account's
    ``XDG_DATA_HOME``.
    """
    path = os.path.expanduser(path or _DEVIN_CREDENTIALS_PATH)
    try:
        with open(path, "rb") as file:
            raw = file.read()
    except OSError:
        return None
    import tomllib
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError):
        # RecursionError: a credentials file nested past the parser's
        # depth limit is unreadable — same verdict as malformed TOML.
        return None
    key = data.get("windsurf_api_key")
    if not isinstance(key, str) or not key.strip():
        return None
    server = data.get("api_server_url")
    if not isinstance(server, str) or not server.strip():
        server = _DEVIN_DEFAULT_API_SERVER
    return key.strip(), server.strip().rstrip("/")


def _diagnose_devin_credentials(path: str | None = None) -> str | None:
    """Devin's credential verdict, as an issue status.

    Distinguishes a missing credentials file (signed-out) from one macOS
    will not let us read (permission-required); a readable file that still
    holds no usable key counts as signed-out — re-login is its fix either
    way. ``ok`` means a key was read and the fetch itself should be judged.
    """
    expanded = os.path.expanduser(path or _DEVIN_CREDENTIALS_PATH)
    try:
        with open(expanded, "rb") as file:
            file.read(1)
    except PermissionError:
        return remedies.PERMISSION_REQUIRED
    except OSError:
        # Missing, or a directory in the way: no credential either way.
        return remedies.SIGNED_OUT
    return (remedies.OK if _read_devin_credentials(path) is not None
            else remedies.SIGNED_OUT)


def _devin_unix_to_epoch(value) -> float:
    """Reset times arrive as decimal-string epoch seconds."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number) or number <= 0:
        return 0.0
    return number / 1000 if number > 100_000_000_000 else number


def _parse_devin_status(data: dict) -> RateLimitInfo | None:
    """Surface the more exhausted of Devin's daily and weekly quotas.

    Quota plans report each window as a remaining percentage. A plan on
    prompt credits instead reports ``availablePromptCredits`` against
    ``planInfo.monthlyPromptCredits`` (``-1`` means unlimited), which is
    shown as ``remaining/total`` with the plan end as the reset.
    """
    status = data.get("userStatus")
    plan = status.get("planStatus") if isinstance(status, dict) else None
    if not isinstance(plan, dict):
        return None
    # ``name`` sits beside it, but the card shows one line and the address
    # is what a sign-in is identified by.
    email = status.get("email") if isinstance(status, dict) else None
    email = email.strip() if isinstance(email, str) and email.strip() else None
    info = plan.get("planInfo") if isinstance(plan.get("planInfo"), dict) else {}
    plan_name = info.get("planName")
    plan_type = (
        plan_name.strip().lower() if isinstance(plan_name, str) and plan_name.strip()
        else "—"
    )

    # Prepaid usage beyond the quota, in millionths of a dollar: `devin
    # /usage`'s "Extra usage balance".
    try:
        micros = float(plan.get("overageBalanceMicros"))
        extra_usage_usd = round(micros / 1_000_000, 2) if math.isfinite(micros) and micros >= 0 else None
    except (TypeError, ValueError, OverflowError):
        extra_usage_usd = None

    best_label = ""
    best_used = -1.0
    best_reset = 0.0
    windows = []
    for remaining_field, reset_field, label in _DEVIN_WINDOWS:
        remaining = plan.get(remaining_field)
        reset_at = _devin_unix_to_epoch(plan.get(reset_field))
        if remaining is None:
            # proto3 JSON drops zero values, so a spent window arrives
            # with only its reset time: that reads as 100% used.
            if reset_at <= 0:
                continue
            remaining = 0.0
        elif isinstance(remaining, bool):
            continue
        else:
            try:
                remaining = float(remaining)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(remaining):
                continue
        used = 100.0 - remaining
        windows.append(_window(label, used, reset_at,
                               86400 if label == "1d" else 604800))
        if used > best_used:
            best_used = used
            best_label = label
            best_reset = reset_at
    if best_used >= 0:
        used_percent = int(round(min(100.0, max(0.0, best_used))))
        return RateLimitInfo(
            tool_name="devin",
            used_percent=used_percent,
            limit_reached=best_used >= 100,
            reset_at=best_reset,
            plan_type=plan_type,
            window_seconds=86400 if best_label == "1d" else 604800,
            window=best_label,
            windows=windows,
            extra_usage_usd=extra_usage_usd,
            email=email,
        )

    try:
        total = float(info.get("monthlyPromptCredits"))
        remaining = float(plan.get("availablePromptCredits"))
    except (TypeError, ValueError, OverflowError):
        return None
    if not (
        math.isfinite(total) and math.isfinite(remaining)
        and total > 0 and 0 <= remaining <= total
    ):
        return None
    used_percent = int(round((1.0 - remaining / total) * 100.0))
    return RateLimitInfo(
        tool_name="devin",
        used_percent=used_percent,
        limit_reached=remaining <= 0,
        reset_at=_parse_iso8601_to_epoch(plan.get("planEnd")),
        plan_type=plan_type,
        window_seconds=0,
        remaining_units=remaining,
        total_units=total,
        extra_usage_usd=extra_usage_usd,
        email=email,
    )


def _warn_devin_401() -> None:
    import sys
    print(
        "Devin usage endpoint rejected the API key. Run `devin auth login` "
        "to renew ~/.local/share/devin/credentials.toml.",
        file=sys.stderr,
    )


def _fetch_devin(credentials_path: str | None = None) -> RateLimitInfo | None:
    """Read Devin's quota through Windsurf's seat-management RPC.

    ``credentials_path`` points at an extra account's credentials.toml (its
    ``XDG_DATA_HOME``/devin dir); the default ``None`` reads the standard
    path exactly as before.
    """
    credentials = _read_devin_credentials(credentials_path)
    if credentials is None:
        return None
    api_key, server = credentials
    # The key travels in the body, not a header: this is a Connect RPC
    # and the server rejects a request without the client metadata block
    # ("invalid_argument"), so the fields the CLI itself sends are kept.
    body = json.dumps({
        "metadata": {
            "api_key": api_key,
            "ide_name": "devin",
            "ide_version": __version__,
            "extension_version": __version__,
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        server + _DEVIN_USER_STATUS_RPC,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"quiver/{__version__}",
        },
    )
    data = _fetch_json(request, on_401=_warn_devin_401)
    return _parse_devin_status(data) if isinstance(data, dict) else None


def _register_devin() -> None:
    """Register the Devin quota fetcher."""

    def fetch() -> RateLimitInfo | None:
        return _fetch_devin()

    register("devin", fetch,
             signed_in=lambda: _read_devin_credentials() is not None,
             diagnose=_diagnose_devin_credentials)


# Tools whose fetchers list every quota window in ``RateLimitInfo.windows``.
_MULTI_WINDOW_TOOLS = frozenset({"devin", "cursor"})


# Register built-in fetchers at import time
_register_codex()
_register_github_copilot()
_register_claude()
_register_droid()
_register_freebuff()
_register_cursor()
_register_devin()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cached(
    ttl: float | None = None,
) -> tuple[dict[str, dict], dict[str, float]] | None:
    """Load per-provider readings that are individually fresh.

    Freshness is judged on each provider's ``updated_at`` (the envelope
    ``cached_at`` for entries saved before it existed). The envelope is
    rewritten on every save, so gating on it would let a persistent
    refetch — e.g. a logged-out Claude re-deriving its ``auth-required``
    verdict — keep week-old values alive indefinitely. Returns
    ``(limits, updated_at)``; ``None`` when the file is missing, corrupt,
    or wholly past the envelope TTL.
    """
    try:
        if not RATE_LIMITS_CACHE_FILE.exists():
            return None
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        ttl = _CACHE_TTL if ttl is None else ttl
        try:
            cached_at = float(data.get("cached_at", 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(cached_at) or time.time() - cached_at > ttl:
            # inf lands here as "never stale" — an unparseable or
            # non-finite envelope timestamp is a miss, not a keep.
            return None
        limits = data.get("limits", {})
        updated_at = data.get("updated_at", {})
        if not isinstance(limits, dict):
            return None
        if not isinstance(updated_at, dict):
            updated_at = {}
        now = time.time()
        fresh_limits: dict[str, dict] = {}
        fresh_at: dict[str, float] = {}
        for name, raw in limits.items():
            try:
                ts = float(updated_at.get(name, cached_at))
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(ts) and now - ts <= ttl:
                fresh_limits[name] = raw
                fresh_at[name] = ts
        return fresh_limits, fresh_at
    except Exception:
        return None


def _load_cached_no_data(ttl: float | None = None) -> set[str]:
    """Names whose fetcher recently returned nothing."""
    return set(_load_cached_no_data_marks(ttl))


def _load_cached_no_data_marks(ttl: float | None = None) -> dict[str, float]:
    """Names whose fetcher recently returned nothing, with when it did.

    Without this a provider that never reports usage counts as a permanent
    cache miss: it is absent from ``limits``, so every call re-runs its
    fetcher. For providers that shell out, that cost lands on every
    ``swe list``. Remembering the
    negative answer for the same TTL keeps the miss to once per window.
    Newer saves keep a per-name answer time; the legacy list form is
    covered by the envelope freshness check above and is upgraded to the
    dict on the next save.
    """
    try:
        if not RATE_LIMITS_CACHE_FILE.exists():
            return {}
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        ttl = _CACHE_TTL if ttl is None else ttl
        cached_at = data.get("cached_at", 0)
        if not isinstance(cached_at, (int, float)) or not math.isfinite(cached_at):
            return {}
        if time.time() - cached_at > ttl:
            return {}
        names = data.get("no_data", [])
        if isinstance(names, dict):
            now = time.time()
            return {
                name: ts for name, ts in names.items()
                if isinstance(ts, (int, float)) and math.isfinite(ts)
                and now - ts <= ttl
            }
        # The legacy list carries no per-name time: it is as old as the file.
        return ({name: cached_at for name in names if isinstance(name, str)}
                if isinstance(names, list) else {})
    except Exception:
        return {}


def _load_cached_no_data_status() -> dict[str, str]:
    """Why each ``no_data`` name had nothing, from the cache's
    ``no_data_status`` map: ``SIGNED_OUT`` or ``FETCH_ERROR``. Freshness is
    ``_load_cached_no_data``'s call; a name absent here carries no reason
    (saved by an swe from before the map, or a fetcher that can't tell)."""
    try:
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        reasons = data.get("no_data_status") if isinstance(data, dict) else None
        if not isinstance(reasons, dict):
            return {}
        return {
            name: reason for name, reason in reasons.items()
            if reason in (SIGNED_OUT, FETCH_ERROR)
        }
    except Exception:
        return {}


def _load_cached_no_data_issues() -> dict[str, str]:
    """The saved per-provider no-data statuses in the remedies vocabulary,
    from the cache's ``no_data_issues`` map. Older swe files lack it; a
    caller falls back to deriving one from ``no_data_status``."""
    try:
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        issues = data.get("no_data_issues") if isinstance(data, dict) else None
        if not isinstance(issues, dict):
            return {}
        return {name: status for name, status in issues.items()
                if isinstance(status, str)}
    except Exception:
        return {}


def _save_cached(
    limits: dict[str, dict],
    updated_at: dict[str, float] | None = None,
    no_data: Iterable[str] | None = None,
    no_data_status: dict[str, str] | None = None,
    no_data_issues: dict[str, str] | None = None,
) -> None:
    """Persist rate limits to disk cache.

    ``no_data_status`` gives the reason for some ``no_data`` names. It is
    a map of its own beside ``no_data`` so an older swe, which reads
    ``no_data`` values as timestamps, still parses the file.
    ``no_data_issues`` carries the finer remedies-vocabulary status of the
    same names so a stored ``auth-required`` does not degrade into an
    outage on the next read.
    """
    try:
        RATE_LIMITS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        payload = {
            "cached_at": now,
            "limits": limits,
            "updated_at": updated_at or {name: now for name in limits},
            # A dict carries the marker's own timestamp (entries preserved
            # across a filtered run); an iterable gets stamped now.
            "no_data": {
                name: no_data[name] if isinstance(no_data, dict) else now
                for name in (no_data or ())
            },
        }
        reasons = {
            name: reason for name, reason in (no_data_status or {}).items()
            if name in payload["no_data"]
        }
        if reasons:
            payload["no_data_status"] = reasons
        issues = {
            name: status for name, status in (no_data_issues or {}).items()
            if name in payload["no_data"]
        }
        if issues:
            payload["no_data_issues"] = issues
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

# Interactive runs can afford to wait out a fetcher's whole designed
# budget — the slowest is Copilot's ``gh auth token`` subprocess (5s) plus
# a 10s HTTP read plus its once-only cert-bundle retry (10s) — so a slow
# provider resolves to a real figure instead of the ``…`` timeout marker.
# ``swe list`` passes this only when stdout is a terminal; piped output
# and library callers keep the shorter cap.
_RATE_LIMIT_FETCH_DEADLINE_TTY = 25.0


def _load_stale_cached() -> tuple[dict[str, dict], dict[str, float]]:
    """Load the last snapshot for outage fallback, up to 24 hours old."""
    try:
        with open(RATE_LIMITS_CACHE_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, {}
        cached_at = float(data.get("cached_at", 0))
        limits = data.get("limits", {})
        timestamps = data.get("updated_at", {})
        if not isinstance(limits, dict) or not isinstance(timestamps, dict):
            return {}, {}
        usable: dict[str, dict] = {}
        usable_timestamps: dict[str, float] = {}
        for name, raw in limits.items():
            if not isinstance(raw, dict):
                continue
            # Transient statuses (auth failure, timed-out fetch) are not
            # usage readings. A forced refresh must be able to replace or
            # remove these markers immediately.
            if raw.get("plan_type") in ("auth-required", "timeout"):
                continue
            try:
                provider_updated_at = float(timestamps.get(name, cached_at))
            except (TypeError, ValueError, OverflowError):
                continue
            if (math.isfinite(provider_updated_at)
                    and time.time() - provider_updated_at <= _STALE_CACHE_TTL):
                usable[name] = raw
                usable_timestamps[name] = provider_updated_at
        return usable, usable_timestamps
    except (OSError, ValueError, TypeError, json.JSONDecodeError,
            RecursionError):
        return {}, {}


def _fill_unavailable_windows(info: RateLimitInfo, previous: dict | None) -> None:
    """Give a window the provider left unread its last known figures.

    ``previous`` is this tool's saved entry (fresh cache or 24h snapshot).
    The matching window's figures carry over, stamped ``as_of``: the saved
    window's own ``as_of`` when it was already a carried copy, so a chain
    of failed refetches keeps the original reading's age, else the saved
    reading's ``fetched_at``. With nothing saved the window reads 0%.
    """
    for window in info.windows:
        if not (isinstance(window, dict) and window.get("unavailable")):
            continue
        match = None
        if isinstance(previous, dict):
            for saved in previous.get("windows") or []:
                if isinstance(saved, dict) and saved.get("window") == window.get("window"):
                    match = saved
                    break
        # An unavailable saved window carries a figure only with its own
        # age: one never successfully read (no ``as_of``) counts as no
        # previous value at all, so a chain of failures can't echo a 0 as
        # if it were a real reading.
        if match is not None and match.get("unavailable") and not match.get("as_of"):
            match = None
        if match is None:
            window["used_percent"] = 0
            window.pop("as_of", None)
            continue
        for key in ("used_percent", "limit_reached", "reset_at", "window_seconds"):
            if key in match:
                window[key] = match[key]
        # The reading's fetch date dates the carry only when the saved
        # window was itself a real reading; a carried copy keeps its own.
        as_of = match.get("as_of") or previous.get("fetched_at")
        if as_of:
            window["as_of"] = as_of


def get_all_rate_limits(
    use_cache: bool = True,
    tool_names: Iterable[str] | None = None,
    deadline: float | None = None,
    refresh_failed: bool = False,
    cache_ttl: float | None = None,
    no_reading: dict[str, str] | None = None,
    issues: dict[str, str] | None = None,
) -> dict[str, RateLimitInfo]:
    """Fetch rate limits for the selected registered tools.

    Returns a dict mapping ``tool_name`` → ``RateLimitInfo``.
    ``tool_names=None`` retains the low-level all-fetchers behavior for tests
    and diagnostics. CLI callers pass the starred harness set so unselected
    usage scripts never start. Tools without a fetcher or whose fetch fails
    are omitted. ``deadline`` overrides ``_RATE_LIMIT_FETCH_DEADLINE`` — how
    long the aggregator waits for the workers before marking stragglers
    timed out.

    ``cache_ttl`` replaces ``_CACHE_TTL`` for this call's freshness check —
    a shorter one lets a caller re-read only readings older than it.
    ``refresh_failed`` treats a cached failure — a ``timeout`` marker, an
    ``auth-required`` verdict (always refetched anyway), or a reading with
    a window marked ``unavailable`` — as a cache miss, so a Refresh press
    retries the providers that failed while healthy ones are still served
    from cache.

    ``no_reading``, when given, is filled with why each selected tool left
    out of the result has no reading: ``SIGNED_OUT`` (no credential to
    read with) or ``FETCH_ERROR`` (credentials, but the fetch failed).
    Tools whose reason can't be told are left out of it too.

    ``issues``, when given, is filled with the finer status the same
    failures carry on the issue boundary — ``signed-out``,
    ``auth-required``, ``permission-required`` or ``unavailable`` from the
    ``remedies`` module — for tools left out of the result, plus
    ``timeout``/``auth-required`` for the sentinel readings left in it.
    """
    result: dict[str, RateLimitInfo] = {}
    reasons: dict[str, str] = {}
    statuses: dict[str, str] = {}
    # no_data markers served from cache this call, kept as saved.
    kept_empty: dict[str, float] = {}

    def report() -> dict[str, RateLimitInfo]:
        if no_reading is not None:
            no_reading.update({
                name: reason for name, reason in reasons.items()
                if name not in result
            })
        if issues is not None:
            for name, info in result.items():
                if info.plan_type in ("auth-required", "timeout"):
                    issues[name] = info.plan_type
            for name, status in statuses.items():
                if name not in result:
                    issues[name] = status
        return result

    requested = None if tool_names is None else set(tool_names)
    fetchers = [
        (name, fetcher)
        for name, fetcher in _FETCHERS.items()
        if requested is None or name in requested
    ]
    fetcher_names = {name for name, _fetcher in fetchers}
    raw_cache: dict[str, dict] = {}
    cache_updated_at: dict[str, float] = {}
    # Older-format readings set aside for a refetch (see the upgrade check).
    # Each is the reading and its saved entry, which is written back as it
    # was (no ``windows`` key) so the next call still tries the upgrade.
    upgrade_fallback: dict[str, tuple[RateLimitInfo, dict]] = {}

    # What every provider last saved, for refetch fallbacks and for
    # filling an unavailable window's last known figures. The 24h stale
    # snapshot always contributes: a reading older than this call's TTL is
    # out of the fresh cache but still the best last-known value.
    previous_raw: dict[str, dict] = {}
    stale_limits, stale_updated_at = _load_stale_cached()
    for name, raw in stale_limits.items():
        previous_raw.setdefault(name, raw)

    if use_cache:
        loaded = _load_cached(cache_ttl)
        if loaded is not None:
            cached, fresh_at = loaded
            previous_raw.update(cached)
            for name, raw in cached.items():
                if name not in fetcher_names:
                    continue
                # auth-required is a local credential verdict, not a
                # network reading: re-deriving it is a file/Keychain
                # lookup, and serving it from cache would pin a stale
                # re-login (or a superseded rule) for the whole TTL.
                # timeout markers stay cached — re-running a hung fetcher
                # costs the deadline again.
                if (isinstance(raw, dict)
                        and raw.get("plan_type") == "auth-required"):
                    continue
                # A Refresh press retries what failed: a timed-out fetch
                # and a reading whose provider left a window unread are
                # treated as misses. The failed entry is kept as the
                # fallback should the refetch fail again.
                if (refresh_failed and isinstance(raw, dict)
                        and (raw.get("plan_type") == "timeout"
                             or any(isinstance(w, dict) and w.get("unavailable")
                                    for w in raw.get("windows", [])))):
                    try:
                        old = RateLimitInfo(**raw)
                        old.fetched_at = old.fetched_at or fresh_at[name]
                        upgrade_fallback[name] = (old, raw)
                    except (TypeError, ValueError):
                        pass
                    continue
                # Saved by an swe from before these tools listed every
                # window (or by an older swe still running): refetch rather
                # than serve one window as the whole reading for the TTL.
                # Kept as the fallback in case that refetch fails.
                if (name in _MULTI_WINDOW_TOOLS and isinstance(raw, dict)
                        and "windows" not in raw):
                    try:
                        old = RateLimitInfo(**raw)
                        old.fetched_at = old.fetched_at or fresh_at[name]
                        upgrade_fallback[name] = (old, raw)
                    except (TypeError, ValueError):
                        pass
                    continue
                try:
                    info = RateLimitInfo(**raw)
                    # Each hydrated value keeps its real fetch time — a
                    # partial refetch must not re-age the untouched
                    # providers or the stale-fallback bound leaks.
                    cache_updated_at[name] = fresh_at[name]
                    info.fetched_at = info.fetched_at or fresh_at[name]
                    result[name] = info
                except (TypeError, ValueError):
                    pass
            # A provider that answered "nothing to report" last time is
            # covered for this window too, otherwise it is refetched forever.
            empty_marks = _load_cached_no_data_marks(cache_ttl)
            known_empty = set(empty_marks) & fetcher_names
            cached_reasons = _load_cached_no_data_status()
            # Signed out is a local credential verdict, re-derived every
            # call like auth-required, so a fresh sign-in shows at once. A
            # Refresh press retries a failed fetch.
            known_empty = {
                name for name in known_empty
                if cached_reasons.get(name) != SIGNED_OUT
                and not (refresh_failed and cached_reasons.get(name) == FETCH_ERROR)
            }
            reasons.update({
                name: cached_reasons[name] for name in known_empty
                if name in cached_reasons
            })
            # Saved issues keep the fine status across reads; caches from
            # before the map fall back to the coarse-to-fine mapping —
            # "error" to "unavailable", "signed_out" to "signed-out".
            cached_issues = _load_cached_no_data_issues()
            statuses.update({
                name: cached_issues.get(
                    name,
                    remedies.SIGNED_OUT
                    if cached_reasons[name] == SIGNED_OUT
                    else remedies.UNAVAILABLE)
                for name in known_empty
                if name in cached_reasons
            })
            # An entry set aside for a refetch is not "nothing to report":
            # a leftover no_data marker must not stop its retry.
            served_empty = known_empty - set(upgrade_fallback) - result.keys()
            missing = fetcher_names - result.keys() - served_empty
            if not missing:
                return report()
            # Answered from cache this call: the save below must keep their
            # markers and reasons as they were, or the next call refetches.
            kept_empty = {name: empty_marks[name] for name in served_empty}
            fetchers = [item for item in fetchers if item[0] in missing]
            raw_cache = {name: asdict(info) for name, info in result.items()}

    if not raw_cache:
        for name, raw in stale_limits.items():
            if name not in fetcher_names:
                continue
            try:
                info = RateLimitInfo(**raw)
            except (TypeError, ValueError):
                continue
            info.fetched_at = info.fetched_at or stale_updated_at[name]
            result[name] = info
            raw_cache[name] = raw
            cache_updated_at[name] = stale_updated_at[name]

    fetched: dict[str, RateLimitInfo | None] = {}
    fetched_reasons: dict[str, str] = {}
    fetched_statuses: dict[str, str] = {}
    fetched_lock = threading.Lock()

    limit = _RATE_LIMIT_FETCH_DEADLINE if deadline is None else deadline
    cutoff = time.monotonic() + limit

    def fetch_one(tool_name: str, fetcher: Callable) -> None:
        _fetch_cutoff.at = cutoff
        _reset_json_failures()
        raised = False
        try:
            info = fetcher()
        except Exception:
            info, raised = None, True
        kinds = _json_failure_kinds()
        with fetched_lock:
            fetched[tool_name] = info
        # The reading is in; the reason is extra and only asked while the
        # deadline allows, so a probe can never turn a finished fetch into
        # a timeout.
        if info or time.monotonic() >= cutoff:
            return
        status = _failure_status(tool_name, fetcher, raised, kinds)
        reason = (SIGNED_OUT if status == remedies.SIGNED_OUT
                  else FETCH_ERROR if status is not None
                  else _no_reading_reason(tool_name, fetcher, raised))
        with fetched_lock:
            if reason:
                fetched_reasons[tool_name] = reason
            if status:
                fetched_statuses[tool_name] = status

    workers = [
        threading.Thread(target=fetch_one, args=item, daemon=True)
        for item in fetchers
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(max(0.0, cutoff - time.monotonic()))

    with fetched_lock:
        completed = fetched.copy()
        completed_reasons = fetched_reasons.copy()
        completed_statuses = fetched_statuses.copy()
    empty_fetchers: set[str] = set()
    # no_data entries carried over from a filtered run keep their own time.
    empty_fetchers_ts: dict[str, float] = {}
    for tool_name, _fetcher in fetchers:
        info = completed.get(tool_name)
        if not info and tool_name in upgrade_fallback and tool_name not in result:
            # The refetch of an older-format reading failed: keep that
            # reading, dated, rather than drop the provider.
            old, old_raw = upgrade_fallback[tool_name]
            result[tool_name] = old
            raw_cache[tool_name] = old_raw
            cache_updated_at[tool_name] = old.fetched_at
            continue
        if info:
            info.fetched_at = info.fetched_at or time.time()
            _fill_unavailable_windows(info, previous_raw.get(tool_name))
            result[tool_name] = info
            raw_cache[tool_name] = asdict(info)
            cache_updated_at[tool_name] = info.fetched_at
        elif tool_name in completed and tool_name not in result:
            # Ran to completion and produced nothing. Distinct from a worker
            # that hit the deadline, which is absent from ``completed`` and
            # gets a timeout marker below rather than an empty record. A kept
            # previous reading is data: it must never gain a no_data marker,
            # or the next call would serve "nothing to report" over it.
            empty_fetchers.add(tool_name)
            if tool_name in completed_reasons:
                reasons[tool_name] = completed_reasons[tool_name]
            if tool_name in completed_statuses:
                statuses[tool_name] = completed_statuses[tool_name]
        elif tool_name not in result:
            # Missed the deadline: remember a marker so the cell reads …
            # instead of looking like a provider with nothing to report.
            # Cached for the normal TTL but excluded from the 24h stale
            # fallback, so a forced refresh retries it for real. A stale
            # reading already in ``result`` stays authoritative.
            marker = RateLimitInfo(
                tool_name=tool_name,
                used_percent=0,
                limit_reached=False,
                reset_at=0.0,
                plan_type="timeout",
                window_seconds=0,
            )
            result[tool_name] = marker
            raw_cache[tool_name] = asdict(marker)
            cache_updated_at[tool_name] = time.time()

    carried_reasons: dict[str, str] = {}
    if requested is not None:
        # A filtered run (--exclude, starred-only) never asked the other
        # providers anything: rewriting the file without them would drop
        # their cached readings, so carry those entries over untouched —
        # limits, their own timestamps, and no_data markers alike.
        try:
            disk = json.loads(RATE_LIMITS_CACHE_FILE.read_text())
        except Exception:
            disk = {}
        if not isinstance(disk, dict):
            # A cache that parses but isn't the expected shape — a list,
            # or limits stored as one — merges as empty rather than
            # raising AttributeError and taking the listing down with it.
            disk = {}
        carried: set[str] = set()
        saved_limits = disk.get("limits")
        if isinstance(saved_limits, dict):
            for name, raw in saved_limits.items():
                if name not in fetcher_names and name not in raw_cache:
                    raw_cache[name] = raw
                    carried.add(name)
        saved_updated = disk.get("updated_at")
        if isinstance(saved_updated, dict):
            for name, ts in saved_updated.items():
                if (name not in fetcher_names and name not in cache_updated_at
                        and isinstance(ts, (int, float))):
                    cache_updated_at[name] = ts
        # A carried reading with no per-provider timestamp must keep the
        # age of the file it came from — re-stamping it now would let
        # repeated filtered runs keep a stale quota fresh forever.
        base_ts = disk.get("cached_at")
        if isinstance(base_ts, (int, float)):
            for name in carried - set(cache_updated_at):
                cache_updated_at[name] = base_ts
        saved_reasons = disk.get("no_data_status")
        if isinstance(saved_reasons, dict):
            for name, reason in saved_reasons.items():
                if name not in fetcher_names:
                    # Carried for the next save only; a filtered run must
                    # not report another tool's stored failure as its own.
                    carried_reasons[name] = reason
        saved_no_data = disk.get("no_data")
        if isinstance(saved_no_data, dict):
            for name, ts in saved_no_data.items():
                if name not in fetcher_names and isinstance(ts, (int, float)):
                    empty_fetchers_ts[name] = ts
        elif isinstance(saved_no_data, list):
            for name in saved_no_data:
                if name not in fetcher_names:
                    empty_fetchers_ts[name] = time.time()
    # A reading in the result (the 24h fallback) must never gain a marker.
    kept_empty = {n: ts for n, ts in kept_empty.items() if n not in result}
    no_data_marks = {**empty_fetchers_ts, **kept_empty}
    for name in empty_fetchers:
        no_data_marks[name] = time.time()

    # Cache an empty result only when no recent successful snapshot exists.
    # Partial outages retain each provider's last known value for up to 24h.
    no_data_reasons = {**carried_reasons, **{
        name: reasons[name] for name in empty_fetchers | set(kept_empty)
        if name in reasons
    }}
    no_data_issues = {**{
        name: statuses[name] for name in empty_fetchers | set(kept_empty)
        if name in statuses
    }}
    _save_cached(raw_cache, cache_updated_at, no_data=no_data_marks,
                 no_data_status=no_data_reasons,
                 no_data_issues=no_data_issues)

    return report()


def get_rate_limit(tool_name: str, use_cache: bool = True) -> RateLimitInfo | None:
    """Fetch one starred tool's rate limit without polling other providers."""
    from quiver.harness.stars import is_starred

    if not is_starred(tool_name):
        return None
    return get_all_rate_limits(
        use_cache=use_cache,
        tool_names={tool_name},
    ).get(tool_name)


# An extra account's credential file, relative to its data dir: for Devin
# the CLI's XDG_DATA_HOME (``<dir>/devin/credentials.toml``), for Cursor
# the desktop app's ``--user-data-dir`` (``<dir>/User/globalStorage``).
_ACCOUNT_CREDENTIALS = {
    "devin": ("devin", "credentials.toml"),
    "cursor": ("User", "globalStorage", "state.vscdb"),
}


class AccountUsageError(Exception):
    """An extra account's credentials exist but its usage could not be
    read: an outage, not a signed-out account (which returns None)."""


@dataclass(frozen=True)
class UsageOutcome:
    """One usage read's verdict, in the ``remedies`` status vocabulary:
    ``ok`` with a reading, or ``signed-out``, ``auth-required``,
    ``permission-required``, ``unavailable`` or ``timeout`` — each a
    different fix, where the old None/error collapse had none."""
    status: str
    limit: RateLimitInfo | None = None
    # Why, for the CLI's human output. The app's card and Doctor rows use
    # the REMEDIES table against ``status`` instead.
    detail: str | None = None


def get_account_usage(tool_name: str, account_dir: str) -> UsageOutcome:
    """Fetch one extra account's rate limit from its own files.

    ``account_dir`` is the account's data dir, never the shared one. The
    account's credentials are read from there only — an empty account must
    not fall back to the default account's token — and the shared
    rate-limit cache is untouched, since its keys name harnesses, not
    accounts. The fetch runs on a daemon thread under the same deadline
    the aggregator gives its workers; missing it yields the same
    ``timeout`` marker a straggler gets there.

    The credential verdict (``_diagnose_*``) comes first: it names a
    missing, denied or opaque credential before any network call. A
    credential that reads ``ok`` is followed by the fetch, whose own
    ``_fetch_json`` failures split into ``auth-required`` (a 401 — the
    stored login is stale) and ``unavailable`` (anything else).
    """
    if tool_name not in _ACCOUNT_CREDENTIALS:
        raise ValueError(
            f"No per-account usage for '{tool_name}'. "
            f"Known: {', '.join(sorted(_ACCOUNT_CREDENTIALS))}."
        )
    path = os.path.join(account_dir, *_ACCOUNT_CREDENTIALS[tool_name])
    if tool_name == "devin":
        credential = _diagnose_devin_credentials(path)

        def fetcher() -> RateLimitInfo | None:
            return _fetch_devin(path)
    else:
        credential = _diagnose_cursor_credentials((path,))

        def fetcher() -> RateLimitInfo | None:
            return _fetch_cursor(path)
    if credential != remedies.OK:
        return UsageOutcome(status=credential)

    cutoff = time.monotonic() + _RATE_LIMIT_FETCH_DEADLINE
    box: list[RateLimitInfo | None] = []
    failures: list[Exception] = []
    kinds: list[str] = []

    def work() -> None:
        _fetch_cutoff.at = cutoff
        _reset_json_failures()
        try:
            box.append(fetcher())
        except Exception as exc:
            failures.append(exc)
        finally:
            kinds.extend(_json_failure_kinds())

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(max(0.0, cutoff - time.monotonic()))
    if not box and not failures:
        return UsageOutcome(
            status=remedies.TIMEOUT,
            limit=RateLimitInfo(
                tool_name=tool_name,
                used_percent=0,
                limit_reached=False,
                reset_at=0.0,
                plan_type="timeout",
                window_seconds=0,
            ))
    info = box[0] if box else None
    if info is None:
        detail = f": {failures[0]}" if failures else ""
        return UsageOutcome(
            status=(remedies.AUTH_REQUIRED if "auth" in kinds
                    else remedies.UNAVAILABLE),
            detail=f"couldn't read {tool_name} usage for this account{detail}")
    # Stamp the read time so the JSON carries when it was taken; a reading
    # that arrives already dated (a carried snapshot) keeps its own.
    if info.fetched_at <= 0:
        info.fetched_at = time.time()
    return UsageOutcome(
        status=(info.plan_type
                if info.plan_type in ("no-sub", "auth-required", "timeout")
                else remedies.OK),
        limit=info)


def get_account_rate_limit(
    tool_name: str, account_dir: str,
) -> RateLimitInfo | None:
    """The pre-``UsageOutcome`` contract over ``get_account_usage``: a
    reading or None for signed-out, ``AccountUsageError`` for a failed
    read. Callers that want the status should use ``get_account_usage``.
    """
    outcome = get_account_usage(tool_name, account_dir)
    if outcome.limit is not None or outcome.status == remedies.SIGNED_OUT:
        return outcome.limit
    raise AccountUsageError(
        outcome.detail or f"couldn't read {tool_name} usage for this account")
