"""Where plugins are installed, and what each one brings with it.

Five harnesses run plugin systems and each records installs differently:
claude and factory keep an ``installed_plugins.json``, codex declares
``[plugins."name@marketplace"]`` blocks in ``config.toml``, grok keeps a
marketplace cache, cursor tracks nothing the user can read. So this reads
per-harness rather than globbing, the same way ``swe mcp`` does.

The scope words carry over from the rest of ``swe find``, but a plugin has no
"file sitting in a harness root", so they mean:

  global    installed AND enabled, so it loads into every session
  local     installed but disabled, or project-scoped
  all       every plugin manifest on disk, cached-but-uninstalled included
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from quiver.find.tree import count_skills

# What a plugin can ship. Counting these is the point: an installed plugin is
# not one thing, it is however many skills, hooks and agents it carries.
COMPONENT_DIRS = ("skills", "agents", "commands", "hooks")


@dataclass
class Plugin:
    harness: str
    name: str                     # "cloudflare"
    marketplace: str              # "dv"
    version: str = ""
    enabled: bool | None = None   # None when the harness does not record it
    path: Path | None = None
    components: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.marketplace}" if self.marketplace else self.name

    @property
    def scope(self) -> str:
        if self.enabled is True:
            return "global"
        return "local" if self.enabled is False else "all"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _split_ref(ref: str) -> tuple[str, str]:
    name, _, market = ref.partition("@")
    return name, market


def count_components(root: Path | None, fallback: Path | None = None) -> dict:
    """How many of each component a plugin ships.

    ``fallback`` is the marketplace source. A Directory-source marketplace
    installs by copying, and a copy of a symlinked skill lands as an empty
    directory: the installed lazyweb plugin has zero SKILL.md on disk while
    Claude reports seven, because Claude reads the source. Counting the cache
    alone would report a plugin as empty when it works fine.
    """
    counts = _count_at(root)
    if not counts and fallback is not None:
        counts = _count_at(fallback)
    return counts


def _count_at(root: Path | None) -> dict:
    if root is None or not root.is_dir():
        return {}
    out = {}
    for kind in COMPONENT_DIRS:
        d = root / kind
        if not d.is_dir():
            continue
        if kind == "skills":
            n = count_skills(d)          # deep: plugins nest skills too
        else:
            n = sum(1 for p in d.iterdir() if p.suffix == ".md" or p.is_file())
        if n:
            out[kind] = n
    return out


def _marketplace_sources(home: Path, harness: str) -> dict:
    """marketplace name -> its source directory, for local marketplaces."""
    data = _load_json(home / f".{harness}" / "plugins" / "known_marketplaces.json") or {}
    out = {}
    for name, entry in data.items():
        src = (entry or {}).get("source") or {}
        loc = src.get("path") or src.get("source") if isinstance(src, dict) else None
        if isinstance(src, dict) and src.get("source") == "directory":
            loc = src.get("path")
        if loc and Path(loc).is_dir():
            out[name] = Path(loc)
    return out


def _from_installed_json(harness: str, path: Path, enabled_map: dict,
                         sources: dict | None = None) -> list[Plugin]:
    """claude and factory share this shape."""
    data = _load_json(path) or {}
    sources = sources or {}
    found = []
    for ref, entries in (data.get("plugins") or {}).items():
        entry = entries[0] if isinstance(entries, list) and entries else {}
        name, market = _split_ref(ref)
        install = entry.get("installPath")
        root = Path(install) if install else None
        src = sources.get(market)
        found.append(Plugin(
            harness=harness, name=name, marketplace=market,
            version=str(entry.get("version", "")),
            enabled=enabled_map.get(ref) if enabled_map else None,
            path=root,
            components=count_components(root, (src / name) if src else None),
        ))
    return found


def _from_codex_toml(home: Path) -> list[Plugin]:
    """codex declares each plugin with an explicit enabled flag."""
    try:
        text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for ref, flag in re.findall(
        r'\[plugins\."([^"]+)"\]\s*\nenabled\s*=\s*(true|false)', text
    ):
        name, market = _split_ref(ref)
        cache = home / ".codex" / "plugins" / "cache" / market / name
        root = None
        if cache.is_dir():
            versions = sorted(p for p in cache.iterdir() if p.is_dir())
            root = versions[-1] if versions else None
        found.append(Plugin(
            harness="codex", name=name, marketplace=market,
            version=root.name if root else "",
            enabled=flag == "true", path=root,
            components=count_components(root),
        ))
    return found


def _from_cache_scan(harness: str, cache: Path) -> list[Plugin]:
    """Harnesses with no readable install record, so report what is cached.

    enabled stays None: cursor and grok give no way to tell installed from
    merely downloaded, and guessing would be worse than saying so.
    """
    if not cache.is_dir():
        return []

    # grok names its marketplace directories by content hash, so 783232b6...
    # tells you nothing. Each one carries a marketplace.json with the real
    # name, which is what you would recognise.
    named = {}
    for top in cache.iterdir():
        if not top.is_dir():
            continue
        for mf in sorted(top.rglob(".*-plugin/marketplace.json"))[:1] or \
                sorted(top.glob("**/marketplace.json"))[:1]:
            name = (_load_json(mf) or {}).get("name")
            if name:
                named[top.name] = name
            break

    found = []
    for manifest in sorted(cache.rglob("*-plugin/plugin.json")):
        root = manifest.parent.parent
        data = _load_json(manifest) or {}
        rel = root.relative_to(cache).parts
        market = rel[0] if len(rel) > 1 else ""
        found.append(Plugin(
            harness=harness, name=data.get("name") or root.name,
            marketplace=named.get(market, market),
            version=str(data.get("version", "")),
            enabled=None, path=root, components=count_components(root),
        ))
    return found


def discover_plugins(home: Path | None = None) -> list[Plugin]:
    """Every plugin the five plugin-capable harnesses know about."""
    home = home or Path.home()
    settings = _load_json(home / ".claude" / "settings.json") or {}
    found: list[Plugin] = []
    found += _from_installed_json(
        "claude", home / ".claude/plugins/installed_plugins.json",
        settings.get("enabledPlugins") or {},
        _marketplace_sources(home, "claude"))
    found += _from_installed_json(
        "factory", home / ".factory/plugins/installed_plugins.json", {})
    found += _from_codex_toml(home)
    found += _from_cache_scan("cursor", home / ".cursor/plugins/cache")
    found += _from_cache_scan("grok", home / ".grok/marketplace-cache")
    return found


def filter_plugins(plugins: list[Plugin], scope: str) -> tuple[list[Plugin], int]:
    """Return (shown, hidden_count) for the requested scope."""
    if scope == "all":
        return plugins, 0
    if scope == "global":
        # Only a confirmed enabled flag counts as loaded into every session.
        # cursor and grok report None, meaning "cached, cannot tell", and
        # treating that as enabled would overstate what is actually running.
        keep = [p for p in plugins if p.enabled is True]
    else:
        keep = [p for p in plugins if p.enabled is False]
    return keep, len(plugins) - len(keep)
