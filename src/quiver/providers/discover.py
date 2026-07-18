"""Walk provider metadata against the keys directory to build a status matrix.

Each row describes one provider:
    - ``name``         canonical slug
    - ``info``         full provider metadata dict (from registry)
    - ``key_file``     resolved path on disk (or ``None``)
    - ``raw_key``      read-if-possible key string (or ``None``); never persisted
    - ``matched_env``  the literal env-var name that was matched (shell-export only)
    - ``masked``       display-safe representation (``-`` if missing)
    - ``env_vars``     list of env-var names downstream tools should look at

Two on-disk layouts are supported:
  1. **Directory layout** (legacy): ``keys_dir`` is a directory with one
     file per provider, named after the provider's ``key_filename``.
  2. **Shell-export layout** (common in practice): ``keys_dir`` is a
     single shell-style file with ``export FOO_API_KEY=...`` lines.
"""

from __future__ import annotations

from pathlib import Path

from quiver.providers.keys import (
    find_key_file,
    mask_key,
    read_key,
    read_shell_export_keys,
)


def discover_provider_keys(
    providers: dict, keys_dir: Path
) -> list[dict]:
    """Return one row per provider, with the on-disk key status resolved.

    Detects whether ``keys_dir`` is a directory (file-per-provider) or a
    regular file (shell-export). The raw key string lives only in this
    row's ``raw_key`` field and returns from the function \u2014 it never
    gets written to providers.json.
    """
    use_shell_export = keys_dir.is_file()
    rows: list[dict] = []
    for name, info in providers.items():
        env_vars = info.get("env_vars") or []
        raw: str | None = None
        matched_env: str | None = None
        key_file = None
        if use_shell_export:
            result = read_shell_export_keys(keys_dir, env_vars)
            if result is not None:
                raw, matched_env = result
            key_file = str(keys_dir)
        else:
            key_file = find_key_file(info, keys_dir)
            raw = read_key(key_file) if key_file else None
        rows.append(
            {
                "name": name,
                "info": info,
                "key_file": key_file,
                "raw_key": raw,
                "matched_env": matched_env,
                "masked": mask_key(raw),
                "env_vars": env_vars,
            }
        )
    return rows
