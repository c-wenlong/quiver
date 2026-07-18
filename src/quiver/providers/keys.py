"""Key discovery and masking helpers.

The key directory is, by convention, ``~/.api_keys/`` (override via the
``--api-keys-dir`` CLI flag). Quiver never writes a key string to disk;
keys are read into memory only for the duration of a single command and
must always pass through ``mask_key()`` before being printed.

Two on-disk layouts are supported:

* **Directory layout** \u2014 ``~/.api_keys/<slug>``, one file per provider
  holding just the key string.
* **Shell-export layout** \u2014 ``~/.api_keys`` is a single shell-style
  file holding ``export FOO_API_KEY=...`` lines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from quiver.paths import DEFAULT_API_KEYS_DIR


def default_keys_dir(home: Path | None = None) -> Path:
    """Return the default keys directory (``~/.api_keys``).

    Honors an optional override ``home`` so tests can patch the location
    without touching the real filesystem.
    """
    if home is None:
        return DEFAULT_API_KEYS_DIR
    return home / DEFAULT_API_KEYS_DIR.name


def find_key_file(provider_info: dict, keys_dir: Path) -> Path | None:
    """Return the key file path inside ``keys_dir`` for ``provider_info``.

    Returns ``None`` if the provider has no ``key_filename`` configured.
    """
    filename = provider_info.get("key_filename") or provider_info.get("file")
    if not filename:
        return None
    return keys_dir / filename


def _safe_read_text(path: Path | None) -> str | None:
    """Read a file's text, returning ``None`` on any I/O error.

    Centralised so both ``read_key`` and ``read_shell_export_keys`` share
    the same exception swallowing semantics across the providers module.
    """
    if path is None:
        return None
    try:
        return path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def read_key(path: Path | None) -> str | None:
    """Read a key string from disk (one key per file, raw string content).

    Returns the stripped content if the file exists and is readable,
    otherwise ``None``.
    """
    text = _safe_read_text(path)
    if text is None:
        return None
    text = text.strip()
    return text or None


def read_shell_export_keys(
    path: Path | None, env_vars: list[str]
) -> Optional[Tuple[str, str]]:
    """Parse a shell-style export file and return ``(value, env_var)``
    for the first matching env-var name.

    Accepts lines like::

        export OPENAI_API_KEY=sk-proj-...
        OPENAI_API_KEY="sk-proj-..."
        # comment lines are ignored
        ANTHROPIC_API_KEY='sk-ant-...'

    Surrounding single or double quotes are stripped from values.
    Returns ``None`` if the file cannot be read or no env_var matches;
    otherwise returns a ``(value, matched_env_var)`` tuple so callers
    can surface which env-var name was actually resolved.
    """
    if not env_vars:
        return None
    text = _safe_read_text(path)
    if text is None or path is None or not path.is_file():
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        for env_var in env_vars:
            prefix = f"{env_var}="
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in ('"', "'")
                ):
                    value = value[1:-1]
                if value:
                    return (value, env_var)
    return None


def mask_key(raw: str | None) -> str:
    """Return a display-safe masked version of ``raw``.

    Long keys (>12 chars) render as ``first8***last4 (len=N)``.
    Short keys (<=12 chars) render as ``first3*** (len=N)``.
    Empty/None renders as ``-``.
    """
    if raw is None:
        return "-"
    raw = raw.strip()
    if not raw:
        return "-"
    n = len(raw)
    if n <= 12:
        return f"{raw[:3]}*** ({n})"
    return f"{raw[:8]}***{raw[-4:]} ({n})"
