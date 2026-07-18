"""Provider registry load/save and alias resolution.

Mirrors ``quiver.harness.registry`` so the on-disk shape and ergonomics
match the rest of quiver. The registry lives at
``~/.config/swe/providers.json`` and is seeded with the built-in
``DEFAULT_PROVIDERS`` catalog on first run.

The ``name`` and ``aliases`` fields on every provider are *derived*
from ``env_vars[0]`` (see :func:`quiver.providers.defaults.display_name`
and :func:`quiver.providers.defaults.derived_alias`) at hydration time
inside :func:`load_registry`. The persisted ``providers.json`` may
carry either derived or omitted values — the loader recomputes them so
that the API_KEY env var stays the single source of truth.

When ``DEFAULT_PROVIDERS`` gains a new built-in (e.g. we ship
``minimax`` in 0.2.7), a user who already has a ``providers.json``
will see the new entry merged in automatically on the next ``swe``
invocation. User-added entries, edits to description / url /
key_filename, and removals are preserved.

A user who runs ``swe providers remove <name>`` is recorded in the
``_removed`` list inside ``providers.json`` so future default merges
won't re-add it. Pass ``include_removed=False`` to load the user-facing
view of providers without the bookkeeping key.
"""

import json
from datetime import datetime

from quiver.paths import CONFIG_DIR, PROVIDERS_REGISTRY_FILE
from quiver.providers.defaults import (
    DEFAULT_PROVIDERS,
    derived_alias,
    display_name,
)

_REMOVED_KEY = "_removed"


def _hydrate(entry: dict) -> dict:
    """Populate the derived ``name`` and ``aliases`` fields on ``entry``.

    The API_KEY env var is the source of truth — these fields are
    always recomputed, regardless of what ``entry`` already carries.
    Returns a shallow copy so the caller's dict is untouched.
    """
    out = dict(entry)
    out["name"] = display_name(out)
    derived = derived_alias(out)
    out["aliases"] = [derived] if derived else []
    return out


def load_registry(include_removed: bool = False) -> dict:
    """Load provider metadata, merging the latest defaults in.

    First run seeds ``providers.json`` with a hydrated copy of the
    defaults. Subsequent runs merge: user-registry entries win for
    user-editable fields (``description``, ``url``, ``key_filename``,
    ``env_vars``), missing defaults are auto-added with a fresh
    ``added`` timestamp, and entries the user explicitly removed are
    filtered out via the ``_removed`` allow-list. ``name`` and
    ``aliases`` are always recomputed from ``env_vars[0]``.
    """
    user_reg: dict = {}
    if PROVIDERS_REGISTRY_FILE.exists():
        try:
            with open(PROVIDERS_REGISTRY_FILE) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    user_reg = data
        except FileNotFoundError:
            user_reg = {}
        except json.JSONDecodeError:
            # Do not overwrite a corrupt user registry with defaults. Returning
            # an empty registry keeps the command read-only and preserves the
            # broken file for manual recovery.
            return {_REMOVED_KEY: []} if include_removed else {}

    explicitly_removed: set[str] = set(user_reg.get(_REMOVED_KEY) or [])

    if not user_reg:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fresh = {
            name: _hydrate(dict(info))
            for name, info in DEFAULT_PROVIDERS.items()
        }
        save_registry(fresh)
        return fresh

    removed_list = sorted(explicitly_removed)
    merged = {
        name: _hydrate(dict(info))
        for name, info in user_reg.items()
        if name != _REMOVED_KEY and name not in explicitly_removed
    }

    added_now: list[str] = []
    for name, info in DEFAULT_PROVIDERS.items():
        if name in explicitly_removed:
            continue
        if name not in merged:
            merged[name] = _hydrate(dict(info))
            merged[name]["added"] = datetime.now().isoformat()
            added_now.append(name)

    if added_now:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        to_save = dict(merged)
        if removed_list:
            to_save[_REMOVED_KEY] = removed_list
        save_registry(to_save)

    if include_removed:
        out = dict(merged)
        out[_REMOVED_KEY] = removed_list
        return out
    return dict(merged)


def save_registry(providers: dict) -> None:
    """Persist provider metadata to disk (no key strings)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROVIDERS_REGISTRY_FILE, "w") as f:
        json.dump(providers, f, indent=2)


def alias_map(providers: dict) -> dict[str, str]:
    """Return ``{alias_or_name: canonical_name}`` for every provider.

    The ``_removed`` bookkeeping key is filtered out if present.
    """
    mapping: dict[str, str] = {
        name: name
        for name in providers
        if name != _REMOVED_KEY
    }
    for name, info in providers.items():
        if name == _REMOVED_KEY:
            continue
        for alias in info.get("aliases") or []:
            mapping[alias] = name
    return mapping


def resolve(providers: dict, key: str) -> str | None:
    """Resolve a name or alias to the canonical provider slug, or ``None``."""
    return alias_map(providers).get(key)
