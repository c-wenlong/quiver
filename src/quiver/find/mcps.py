"""Where MCP servers live, and how far the hub has actually reached.

`swe mcp list` answers "which tool has which server" as a matrix, which is
the right shape once you know what you are looking for. This answers the
prior question: what is in the hub, what is only in a harness, and which
harnesses are still empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HubView:
    servers: dict = field(default_factory=dict)
    by_prefix: dict = field(default_factory=dict)
    duplicates: list = field(default_factory=list)


@dataclass
class ToolView:
    name: str
    present: set = field(default_factory=set)
    only_here: set = field(default_factory=set)   # in this tool, not in the hub
    path: str = ""


def _kind(cfg: dict) -> str:
    """local (stdio) or remote (http/sse), the axis duplicates are judged on."""
    if not isinstance(cfg, dict):
        return "local"
    if cfg.get("url") or cfg.get("type") in ("http", "sse", "streamable-http"):
        return "remote"
    return "local"


def _signature(cfg: dict) -> str:
    """What makes two entries the same server rather than two servers."""
    import json

    if not isinstance(cfg, dict):
        return "?"
    return json.dumps(
        {k: cfg.get(k) for k in ("command", "args", "url", "type")},
        sort_keys=True,
    )


PREFIX_UNFILED = ""


def prefix_of(name: str) -> str:
    """The taxonomy prefix (`dv`, `pd`, ...), or "" for an unfiled server."""
    return name.split("__")[0] if "__" in name else ""


def hub_view() -> HubView:
    """The hub's contents, grouped by prefix, with duplicate names paired.

    A duplicate only counts when both copies share a transport. The rule is
    that one server may exist as both a local and a remote entry, because
    those are genuinely different ways to reach it, but two locals or two
    remotes under different names are the same thing filed twice.
    """
    from quiver.mcp.cli import get_hub_servers

    servers = get_hub_servers()

    by_prefix: dict[str, list[str]] = {}
    for name in sorted(servers):
        by_prefix.setdefault(prefix_of(name), []).append(name)

    seen: dict[tuple[str, str], list[str]] = {}
    for name, cfg in servers.items():
        seen.setdefault((_signature(cfg), _kind(cfg)), []).append(name)
    duplicates = [sorted(group) for group in seen.values() if len(group) > 1]

    return HubView(servers=servers, by_prefix=by_prefix,
                   duplicates=sorted(duplicates))


def tool_views(hub: dict) -> list[ToolView]:
    """Every MCP-capable harness, and how much of the hub reached it."""
    from quiver.mcp.cli import (
        get_mcp_tools,
        get_tool_config,
        get_tool_servers,
        load_registry,
    )

    out: list[ToolView] = []
    hub_names = set(hub)
    for name in sorted(get_mcp_tools(load_registry())):
        try:
            servers = get_tool_servers(name)
        except Exception:
            servers = {}
        if not isinstance(servers, dict):
            servers = {}
        cfg = get_tool_config(name) or {}
        path = cfg.get("path")
        # Every registry harness gets an optimistic ~/.<tool>/mcp.json path
        # so sync works without per-tool code. Most of those files do not
        # exist, and listing 20 harnesses as "missing 33 servers" when they
        # have no MCP config at all buries the ones that really are behind.
        on_disk = bool(path) and Path(str(path)).expanduser().exists()
        if not on_disk and not servers:
            continue
        path = str(path or "")
        names = set(servers)
        out.append(ToolView(
            name=name,
            present=names & hub_names,
            only_here=names - hub_names,
            path=path,
        ))
    return out


# ---------------------------------------------------------------------------
# Filesystem scan
#
# Everything above reads the nine config paths quiver already knows. That is
# fine for reporting how far a sync got, but it cannot tell you about a config
# nobody registered, which is exactly where an unmanaged server hides. This
# half looks at the disk instead, and it found five servers the hub had never
# seen while `swe mcp discover` was reporting none.
# ---------------------------------------------------------------------------

# Keys a config uses to hold its server table. "servers" and "mcp" are broad
# enough to match unrelated files, so a hit on those is only trusted when the
# values look like server definitions.
SERVER_KEYS = ("mcpServers", "mcp_servers", "contextServers", "servers", "mcp")

# Directories that never hold a live config. ~/.mcp-servers is a checkout of
# server source, so its example and fixture configs describe how to install a
# server rather than recording that one is installed.
SCAN_PRUNE = frozenset({
    "node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build",
    "site-packages", "cache", "logs", "tmp", "history", "sessions",
    "projects", "conversations", "checkpoints", "blobs", "target",
    "test", "tests", "fixtures", "examples", "docs",
})
SCAN_MAX_DEPTH = 3
SCAN_MAX_BYTES = 4_000_000


# A config one level deeper than a harness root belongs to something the
# harness vendored (an editor extension, a synced-state file) rather than to
# the harness itself. Same rule the rest of `swe find` uses for scope.
VENDORED_MARKERS = frozenset({"extensions", "agent-plugins", ".internal",
                              "plugins", "vendor", "vendor_imports"})


@dataclass
class FoundConfig:
    path: Path
    servers: dict = field(default_factory=dict)
    harness: str = ""

    @property
    def vendored(self) -> bool:
        return any(part in VENDORED_MARKERS for part in self.path.parts)

    @property
    def local(self) -> list:
        return sorted(n for n, cfg in self.servers.items() if _kind(cfg) == "local")

    @property
    def remote(self) -> list:
        return sorted(n for n, cfg in self.servers.items() if _kind(cfg) == "remote")


def _looks_like_servers(value) -> bool:
    """A dict of server definitions, rather than any dict under a broad key."""
    if not isinstance(value, dict) or not value:
        return False
    fields = {"command", "url", "args", "type", "env", "headers", "transport"}
    entries = [v for v in value.values() if isinstance(v, dict)]
    if not entries:
        return False
    return sum(1 for v in entries if fields & set(v)) >= max(1, len(entries) // 2)


def _servers_in(data) -> dict:
    if not isinstance(data, dict):
        return {}
    for key in SERVER_KEYS:
        value = data.get(key)
        if _looks_like_servers(value):
            return value
        # codex nests one level: [mcp.servers.<name>]
        if isinstance(value, dict):
            for nested in value.values():
                if _looks_like_servers(nested):
                    return nested
    return {}


def _parse(path: Path) -> dict:
    """Servers declared in one config file, or {} if it holds none."""
    import json

    try:
        if path.stat().st_size > SCAN_MAX_BYTES:
            return {}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not any(k in text for k in SERVER_KEYS):
        return {}
    if path.suffix == ".toml":
        try:
            import tomllib
        except ImportError:  # pragma: no cover - 3.10 only
            import tomli as tomllib
        try:
            return _servers_in(tomllib.loads(text))
        except Exception:
            return {}
    try:
        return _servers_in(json.loads(text))
    except Exception:
        return {}


def _harness_of(path: Path, home: Path) -> str:
    try:
        rel = path.relative_to(home)
    except ValueError:
        return ""
    first = rel.parts[0]
    if first == ".config" and len(rel.parts) > 1:
        return rel.parts[1]
    return first.lstrip(".").removesuffix(".json").removesuffix(".toml")


def scan_configs(home: Path | None = None, scope: str = "all") -> list[FoundConfig]:
    """Every MCP config on disk, not only the paths quiver has registered.

    ``scope="global"`` drops vendored configs, matching what the rest of
    ``swe find`` means by the word.
    """
    import os

    home = home or Path.home()
    found: list[FoundConfig] = []
    seen: set[Path] = set()

    def take(path: Path) -> None:
        try:
            real = path.resolve()
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        servers = _parse(path)
        if servers:
            found.append(FoundConfig(path=path, servers=servers,
                                     harness=_harness_of(path, home)))

    # Configs living directly in $HOME, like ~/.claude.json, are not inside
    # any harness directory and a directory walk alone would miss them.
    for entry in home.glob(".*"):
        if entry.is_file() and entry.suffix in (".json", ".toml"):
            take(entry)

    roots = [p for p in home.glob(".*") if p.is_dir()]
    roots += [p for p in (home / ".config").glob("*") if p.is_dir()]
    for root in roots:
        if root.name in SCAN_PRUNE or root.name == ".mcp-servers":
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath)
            if len(here.parts) - base_depth >= SCAN_MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SCAN_PRUNE]
            for name in filenames:
                if name.endswith((".json", ".toml")):
                    take(here / name)

    if scope == "global":
        found = [f for f in found if not f.vendored]
    return sorted(found, key=lambda f: str(f.path))


def unmanaged(home: Path | None = None, hub: dict | None = None,
              scope: str = "global") -> dict:
    """Servers that exist in some config on disk but not in the hub.

    Reported by name, mapped to the configs declaring them, because that is
    what you need to decide whether to adopt or delete one.
    """
    from quiver.mcp.cli import get_hub_servers

    hub_names = set(hub if hub is not None else get_hub_servers())
    out: dict[str, list[FoundConfig]] = {}
    for cfg in scan_configs(home, scope=scope):
        for name in cfg.servers:
            if name not in hub_names:
                out.setdefault(name, []).append(cfg)
    return out
