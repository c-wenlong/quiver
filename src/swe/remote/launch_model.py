"""How a fresh launch starts a harness on a picked model.

A new session (``POST /v1/sessions``, an issue resolve, a PR open) is a
fresh harness process under tmux, so the model goes on its command line
rather than being typed into the TUI afterwards. How each harness takes
it is declared in ``harness.json`` as ``capabilities.model``:

  supported      false: the harness takes no model at launch, and the
                 launch routes refuse a ``model`` for it
  flag           the argv flag that takes the model (``--model``, ``-m``)
  subcommand     argv tokens put right after the binary, only when a
                 model is picked (opencode's full TUI has no model flag,
                 so a pick launches ``opencode mini -m <provider/model>``)
  settings_flag  with ``settings_key``: the model goes into a per-launch
                 JSON settings file passed as ``<settings_flag> <file>``
                 (droid has no interactive ``-m``; ``--settings`` takes
                 ``{"sessionDefaultSettings": {"model": ...}}``)
  settings_key   the dotted path the model is written under in that file
  provider       true: the model must be ``provider/model`` (opencode)

``MODEL_FALLBACK`` is the read-only floor for a harness whose registry
entry declares nothing, the same role ``HARNESS_ROOTS`` and
``PLUGIN_FALLBACK`` play: a registry ``capabilities.model`` entry's
fields override it, and ``supported: false`` switches it off. Every flag
here was tried on the owner's Mac on 2026-10-27 (claude, codex,
cursor-agent, pi, cline, devin, droid 0.219, opencode v2).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

MODEL_FALLBACK: dict[str, dict] = {
    "claude": {"flag": "--model"},
    "codex": {"flag": "-m"},
    "cursor": {"flag": "--model"},
    "pi": {"flag": "--model"},
    "cline": {"flag": "-m"},
    "devin": {"flag": "--model"},
    "droid": {"settings_flag": "--settings",
              "settings_key": "sessionDefaultSettings.model"},
    "opencode": {"subcommand": ["mini"], "flag": "-m", "provider": True},
}

# Per-launch settings files (droid) older than this are pruned on the
# next write: the harness reads its file at startup.
_SETTINGS_MAX_AGE_S = 7 * 86_400


def _usable(cap: dict) -> bool:
    flag = cap.get("flag")
    if isinstance(flag, str) and flag.startswith("-"):
        return True
    settings_flag = cap.get("settings_flag")
    return (isinstance(settings_flag, str) and settings_flag.startswith("-")
            and isinstance(cap.get("settings_key"), str)
            and bool(cap["settings_key"]))


def capability(name: str, reg: dict | None = None) -> dict | None:
    """``name``'s launch-model capability: the registry's
    ``capabilities.model`` over ``MODEL_FALLBACK``, or None when the
    harness can't take a model at launch. ``reg`` is the loaded registry;
    None reads it (read-only)."""
    if reg is None:
        from swe.harness import registry

        try:
            reg = registry.load_registry_readonly()
        except (OSError, ValueError):
            reg = {}
    entry = reg.get(name) if isinstance(reg, dict) else None
    # A hand-edited registry can hold anything here; a malformed
    # declaration is ignored rather than taking the hub down with it.
    caps = (entry if isinstance(entry, dict) else {}).get("capabilities")
    caps = caps.get("model") if isinstance(caps, dict) else None
    base = dict(MODEL_FALLBACK.get(name) or {})
    if isinstance(caps, dict):
        if "supported" in caps and not caps["supported"]:
            return None
        base.update(caps)
    return base if _usable(base) else None


def provider_split(model: str) -> tuple[str, str] | None:
    """A ``provider/model`` value as ``(provider, model id)``, split at
    the first slash (the id may hold more: ``openrouter/acme/m``); None
    when either side is empty. The one rule for opencode's launch flag,
    its serve channel's ``{providerID, modelID}`` and the switch to it."""
    provider, sep, model_id = model.partition("/")
    return (provider, model_id) if sep and provider and model_id else None


def model_error(name: str, model: str | None,
                reg: dict | None = None) -> str:
    """The refusal note when ``name``'s launch can't take ``model``,
    else "" (always "" for no pick)."""
    if not model:
        return ""
    cap = capability(name, reg)
    if cap is None:
        return (f"{name} launches can't take a model: harness.json "
                f"declares no capabilities.model for it")
    if cap.get("provider") and provider_split(model) is None:
        return (f"{name} launches need the model as provider/model, "
                f"not {model!r}")
    return ""


def _settings_file(folder: Path, stem: str, key: str, model: str) -> Path:
    """Write ``{key path: model}`` to ``<folder>/<stem>.json``, pruning
    files an earlier launch left behind long ago."""
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _SETTINGS_MAX_AGE_S
    for old in folder.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    data: dict = {}
    node = data
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = model
    path = folder / f"{stem}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def model_argv(name: str, model: str, *, settings_dir: Path, stem: str,
               reg: dict | None = None) -> list[str]:
    """The argv tokens that follow the binary to start ``name`` on
    ``model``. Raises ValueError when the harness can't take it (see
    ``model_error``) and OSError when a settings file can't be written.
    ``model`` is already a validated single token
    (``protocol._launch_value``: no leading ``-``, no whitespace)."""
    note = model_error(name, model, reg)
    if note:
        raise ValueError(note)
    cap = capability(name, reg) or {}
    sub = cap.get("subcommand") or []
    if isinstance(sub, str):
        sub = [sub]
    out = [str(token) for token in sub]
    flag = cap.get("flag")
    if isinstance(flag, str) and flag.startswith("-"):
        return out + [flag, model]
    path = _settings_file(settings_dir, stem, str(cap["settings_key"]),
                          model)
    return out + [str(cap["settings_flag"]), str(path)]
