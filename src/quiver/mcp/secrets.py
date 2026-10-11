"""Credential indirection for the MCP hub.

``~/.quiver/mcp.json`` records which servers exist and is worth versioning.
Credentials are not, so the hub stores ``${NAME}`` references and the values
live in ``~/.quiver/secrets/.api_keys``, mode 600 and gitignored.

Resolution happens when a harness config is written, not by exporting to the
environment: Claude and Cursor launch from the Dock and never
read a shell profile, so an unresolved reference would reach the server as a
literal token string.

Only names present in the store are substituted. ``$HOME`` and
``$(gh auth token)`` appear in real configs and must survive untouched.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from quiver.paths import quiver_dir_for

SECRETS_DIRNAME = "secrets"
SECRETS_BASENAME = ".api_keys"

# export NAME=value, with optional quotes. Comments and blanks are skipped.
_EXPORT_RE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$""")
_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def secrets_dir_for(home: Path | None = None) -> Path:
    return quiver_dir_for(home) / SECRETS_DIRNAME


def secrets_file_for(home: Path | None = None) -> Path:
    return secrets_dir_for(home) / SECRETS_BASENAME


def load_secrets(home: Path | None = None) -> dict[str, str]:
    """Parse the store into {NAME: value}. Missing file reads as empty."""
    path = secrets_file_for(home)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}

    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _EXPORT_RE.match(line)
        if not m:
            continue
        name, raw = m.group(1), m.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        out[name] = raw
    return out


def resolve(value, secrets: dict[str, str] | None = None, home: Path | None = None):
    """Replace ``${NAME}`` with its value, recursing through dicts and lists.

    An unknown name is left as-is rather than blanked: a literal ``${FOO}``
    in a config is visible and debuggable, whereas an empty string silently
    authenticates as nobody.
    """
    if secrets is None:
        secrets = load_secrets(home)
    if isinstance(value, dict):
        return {k: resolve(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, secrets) for v in value]
    if isinstance(value, str):
        return _REF_RE.sub(
            lambda m: secrets.get(m.group(1), m.group(0)), value
        )
    return value


def unresolved_names(value, secrets: dict[str, str] | None = None,
                     home: Path | None = None) -> list[str]:
    """Reference names that the store cannot satisfy, for warning on."""
    if secrets is None:
        secrets = load_secrets(home)
    found: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for name in _REF_RE.findall(v):
                if name not in secrets:
                    found.add(name)

    walk(value)
    return sorted(found)


def redact(value, secrets: dict[str, str] | None = None, home: Path | None = None):
    """Inverse of resolve: swap known secret values back to ``${NAME}``.

    ``swe mcp discover`` reads harness configs, which hold real values. Without
    this every discover would write plaintext back into the hub and quietly
    undo the indirection.
    """
    if secrets is None:
        secrets = load_secrets(home)
    # Longest first, so a value that contains another is not half-replaced.
    pairs = sorted(secrets.items(), key=lambda kv: -len(kv[1]))

    def swap(text: str) -> str:
        """Replace whole-field values, and long embedded credentials.

        Blind substring matching is unsafe for short values:
        TELEGRAM_API_ID is 8 digits, and a blind replace would rewrite any
        config text that happened to contain that number. But resolve()
        substitutes ``${NAME}`` anywhere in a string, so a
        ``?key=TOKEN`` URL gets a literal credential written into the
        harness — and without embedded matching it is written back into
        the hub in plaintext, undoing the indirection. Values long enough
        to be unambiguous (16+ chars) are replaced wherever they appear;
        shorter ones keep the whole-field anchor.
        """
        for name, secret in pairs:
            if not secret:
                continue
            if text == secret:
                return f"${{{name}}}"
            for scheme in ("Bearer ", "Token ", "Basic "):
                if text == scheme + secret:
                    return f"{scheme}${{{name}}}"
            if len(secret) >= 16 and secret in text:
                text = text.replace(secret, f"${{{name}}}")
        return text

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            return swap(v)
        return v

    return walk(value)


# ── Display scrubbing ───────────────────────────────────────────────
#
# redact() only knows values that are in the store. A harness config can
# also hold a credential nobody ever put there, in a URL or an argument, and
# a machine-readable listing must not carry it. scrub_url / scrub_args hide
# anything credential-shaped, keeping ${NAME} references (they name a secret
# without being one) and the structure around the hidden part.

MASK = "***"

_REF_ONLY_RE = re.compile(r"(?:\$\{[A-Za-z_][A-Za-z0-9_]*\})+")
_SCHEME_VALUE_RE = re.compile(r"(?:Bearer|Basic|Token)\s+(.+)", re.IGNORECASE)
_SECRET_FLAG_RE = re.compile(
    r"key|token|secret|passw|bearer|credential|cookie|signature"
    r"|authori[sz]ation",
    re.IGNORECASE,
)
_HEADER_FLAGS = frozenset({"--header", "--headers", "-H"})
_OPAQUE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_\-=.%~+]{24,}")
_ENV_ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)


def _hide(value: str) -> str:
    """``***``, unless the value is only ``${NAME}`` references."""
    if _REF_ONLY_RE.fullmatch(value):
        return value
    scheme = _SCHEME_VALUE_RE.fullmatch(value)
    if scheme and _REF_ONLY_RE.fullmatch(scheme.group(1)):
        return value
    return MASK


def _hide_header(value: str) -> str:
    """Keep a header's name, hide what follows the colon."""
    name, colon, rest = value.partition(":")
    if not colon:
        return _hide(value)
    return f"{name}: {_hide(rest.strip())}"


def _looks_opaque(segment: str) -> bool:
    """A long run of token characters with letters and digits in it.

    Webhook-style MCP URLs carry their credential as a path segment
    (``/s/<token>/mcp``). Real path words ("mcp", "sse", "v1") are short
    or have no digits, so only credential-shaped segments are hidden.
    """
    return (
        _OPAQUE_SEGMENT_RE.fullmatch(segment) is not None
        and any(ch.isdigit() for ch in segment)
        and any(ch.isalpha() for ch in segment)
    )


def scrub_url(url: str, secrets: dict[str, str] | None = None) -> str:
    """A URL safe to print: no password, query value, fragment or token path."""
    from urllib.parse import urlsplit, urlunsplit

    url = redact(url, secrets)
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    netloc = parts.netloc
    userinfo, at, hostport = netloc.rpartition("@")
    if at:
        user, colon, password = userinfo.partition(":")
        userinfo = f"{user}:{_hide(password)}" if colon else _hide(userinfo)
        netloc = f"{userinfo}@{hostport}"
    path = "/".join(
        _hide(seg) if _looks_opaque(seg) else seg
        for seg in parts.path.split("/")
    )
    query = "&".join(
        f"{key}{eq}{_hide(value) if value else value}"
        for key, eq, value in (
            pair.partition("=") for pair in parts.query.split("&") if pair)
    )
    fragment = _hide(parts.fragment) if parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, path, query, fragment))


def scrub_args(args, secrets: dict[str, str] | None = None) -> list[str]:
    """Command-line arguments safe to print.

    Hides the value of a credential-named flag (``--token X``,
    ``--api-key=X``) or env assignment (``API_KEY=X``), the value of a
    header flag (``--header "Name: X"``), and the credential parts of any
    URL argument. Everything else stays.
    """
    out: list[str] = []
    pending = None  # "secret" or "header": the next bare arg is its value
    for arg in redact([str(a) for a in args], secrets):
        if pending and not arg.startswith("-"):
            out.append(_hide_header(arg) if pending == "header" else _hide(arg))
            pending = None
            continue
        pending = None
        if arg.startswith("-"):
            flag, eq, value = arg.partition("=")
            if flag in _HEADER_FLAGS:
                if eq:
                    arg = f"{flag}={_hide_header(value)}"
                else:
                    pending = "header"
            elif _SECRET_FLAG_RE.search(flag):
                if eq:
                    arg = f"{flag}={_hide(value)}"
                else:
                    pending = "secret"
        elif "://" in arg:
            arg = scrub_url(arg, secrets)
        elif (assign := _ENV_ASSIGN_RE.fullmatch(arg)) and _SECRET_FLAG_RE.search(
                assign.group(1)):
            # `env API_KEY=... npx server`, a common wrapper in configs.
            arg = f"{assign.group(1)}={_hide(assign.group(2))}"
        out.append(arg)
    return out


def store_env(home: Path | None = None) -> dict[str, str]:
    """The store merged over os.environ, for callers that want a full env."""
    return {**os.environ, **load_secrets(home)}
