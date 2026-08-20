"""Credential indirection for the MCP hub.

``~/.quiver/mcp.json`` records which servers exist and is worth versioning.
Credentials are not, so the hub stores ``${NAME}`` references and the values
live in ``~/.quiver/secrets/.api_keys``, mode 600 and gitignored.

Resolution happens when a harness config is written, not by exporting to the
environment: Claude, Cursor and Antigravity launch from the Dock and never
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
        """Replace only a whole field value, or a scheme-prefixed one.

        Substring matching is unsafe here: TELEGRAM_API_ID is 8 digits, and a
        blind replace would rewrite any config text that happened to contain
        that number. A credential occupies its own field, so anchoring to the
        whole value (optionally after ``Bearer``/``Token``/``Basic``) is both
        sufficient and safe.
        """
        for name, secret in pairs:
            if not secret:
                continue
            if text == secret:
                return f"${{{name}}}"
            for scheme in ("Bearer ", "Token ", "Basic "):
                if text == scheme + secret:
                    return f"{scheme}${{{name}}}"
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


def store_env(home: Path | None = None) -> dict[str, str]:
    """The store merged over os.environ, for callers that want a full env."""
    return {**os.environ, **load_secrets(home)}
