"""Tool registry load/save and alias resolution."""

import json

from quiver.harness.defaults import DEFAULT_TOOLS
from quiver.paths import CONFIG_DIR, REGISTRY_FILE


def load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        save_registry(DEFAULT_TOOLS)
        return dict(DEFAULT_TOOLS)
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def save_registry(tools: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(tools, f, indent=2)


def alias_map(tools: dict) -> dict[str, str]:
    """Return {alias_or_name: canonical_name} for every tool."""
    mapping: dict[str, str] = {}
    for name, info in tools.items():
        mapping[name] = name
        for alias in info.get("aliases", []):
            mapping[alias] = name
    return mapping


def resolve(tools: dict, key: str) -> str | None:
    """Resolve a name or alias to canonical name, or None."""
    return alias_map(tools).get(key)
