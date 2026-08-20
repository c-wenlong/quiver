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
