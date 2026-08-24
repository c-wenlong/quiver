"""Where plugins are installed, and what each one brings with it.

Harnesses that run plugin systems each record installs differently: claude
and droid keep an ``installed_plugins.json`` (droid's lives under its home
directory, ``~/.factory``, even though the registry calls it "droid" — see
``PLUGIN_FALLBACK``), codex declares ``[plugins."name@marketplace"]`` blocks
in ``config.toml``, grok and cursor keep an unreadable marketplace cache, and
opencode writes no install record this module recognises at all. So this
reads per-harness rather than globbing, the same way ``swe mcp`` does.

Which harnesses get walked, and where each one's store lives, comes from
``harness.json``'s ``capabilities.plugins`` first: a harness the registry
marks unsupported (grok, on a real machine) is skipped even though it used
to be scanned unconditionally, and one it marks supported with a root this
module has never hardcoded (opencode) is picked up automatically.
``PLUGIN_FALLBACK`` only fires for a harness the registry has never heard of
— a fresh install before ``swe harness discover`` has run — so plugin
discovery still finds the same five it always found with no harness.json
on disk at all.

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
    """claude and droid share this shape."""
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


def _from_dir_presence(harness: str, root: Path) -> list[Plugin]:
    """A plugin root whose install format this module does not parse.

    opencode keeps ``~/.opencode/plugins`` but writes no install record in
    a shape any reader above recognises — no known_marketplaces.json, no
    installed_plugins.json, no ``[plugins.*]`` table, just loose plugin
    scripts. Guessing a schema from one file on one machine would be worse
    than saying so, so this reports that the root exists, with whatever
    components ``count_components`` can find in it, and lets the row's own
    directory be what the reader inspects.
    """
    if not root.is_dir():
        return []
    if not any(not p.name.startswith(".") for p in root.iterdir()):
        return []          # created but never populated
    return [Plugin(harness=harness, name=root.name, marketplace="",
                   enabled=None, path=root, components=count_components(root))]


# Where each plugin-capable harness keeps its store, relative to home, for a
# harness the registry has never heard of. This is the pre-capabilities
# behaviour, kept only as the floor a fresh install lands on: a harness.json
# with real capabilities.plugins data always overrides it (see
# ``_plugin_root`` and ``_plugin_capable``). droid is keyed by its registry
# name even though the directory underneath is ``~/.factory``, the same
# drift ``capabilities.plugins.root`` exists to record for everyone else.
PLUGIN_FALLBACK: dict[str, Path] = {
    "claude": Path(".claude/plugins"),
    "droid": Path(".factory/plugins"),
    "codex": Path(".codex/plugins"),
    "cursor": Path(".cursor/plugins"),
    "grok": Path(".grok/marketplace-cache"),   # no ~/.grok/plugins on disk
}


def _expand_root(root: str, home: Path) -> Path:
    """``~/.foo/plugins`` -> ``home / ".foo/plugins"``.

    Not ``Path.expanduser()``: that resolves against the real machine's
    ``$HOME`` regardless of what ``home`` a caller passed in, which breaks
    the moment a test walks a throwaway directory standing in for it.
    """
    raw = str(root)
    if raw.startswith("~/"):
        return home / raw[2:]
    if raw == "~":
        return home
    return Path(raw)


def _plugin_root(reg: dict, name: str, home: Path) -> Path | None:
    """Where a harness keeps its plugins: capabilities.json first, the
    hardcoded fallback table second, None when neither has an answer."""
    root = ((reg.get(name, {}).get("capabilities") or {}).get("plugins") or {}).get("root")
    if root:
        return _expand_root(root, home)
    fallback = PLUGIN_FALLBACK.get(name)
    return (home / fallback) if fallback else None


def _plugin_capable(reg: dict, name: str) -> bool:
    """Whether to walk this harness's plugin store at all.

    A harness present in the registry is judged purely on
    ``capabilities.plugins.supported`` — that is the whole point of the
    grok/opencode correction. A harness absent from the registry entirely
    falls back to membership in ``PLUGIN_FALLBACK``, the old hardcoded set,
    so discovery keeps working before any harness.json exists.
    """
    entry = reg.get(name)
    if entry is None:
        return name in PLUGIN_FALLBACK
    caps = (entry.get("capabilities") or {}).get("plugins") or {}
    if "supported" in caps:
        return bool(caps["supported"])
    return name in PLUGIN_FALLBACK


def discover_plugins(home: Path | None = None) -> list[Plugin]:
    """Every plugin every plugin-capable harness knows about.

    Reads ``harness.json`` read-only (never seeds or migrates it — that
    would make a browse-only command write to disk) and treats an absent
    registry exactly like an empty one, which is what makes
    ``PLUGIN_FALLBACK`` the answer on a fresh machine.
    """
    from quiver.harness.registry import load_registry_if_present

    home = home or Path.home()
    reg = load_registry_if_present()
    found: list[Plugin] = []

    if _plugin_capable(reg, "claude"):
        root = _plugin_root(reg, "claude", home) or (home / PLUGIN_FALLBACK["claude"])
        settings = _load_json(home / ".claude" / "settings.json") or {}
        found += _from_installed_json(
            "claude", root / "installed_plugins.json",
            settings.get("enabledPlugins") or {},
            _marketplace_sources(home, "claude"))

    if _plugin_capable(reg, "droid"):
        root = _plugin_root(reg, "droid", home) or (home / PLUGIN_FALLBACK["droid"])
        found += _from_installed_json("droid", root / "installed_plugins.json", {})

    if _plugin_capable(reg, "codex"):
        found += _from_codex_toml(home)

    if _plugin_capable(reg, "cursor"):
        root = _plugin_root(reg, "cursor", home) or (home / PLUGIN_FALLBACK["cursor"])
        found += _from_cache_scan("cursor", root / "cache")

    if _plugin_capable(reg, "grok"):
        # grok's root *is* the cache — there is no ~/.grok/plugins directory
        # to nest one under, unlike claude/droid/cursor.
        root = _plugin_root(reg, "grok", home) or (home / PLUGIN_FALLBACK["grok"])
        found += _from_cache_scan("grok", root)

    if _plugin_capable(reg, "opencode"):
        root = _plugin_root(reg, "opencode", home) or (home / ".opencode" / "plugins")
        found += _from_dir_presence("opencode", root)

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
