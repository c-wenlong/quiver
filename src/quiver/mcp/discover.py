"""Discover MCP servers across tool configs vs quiver source-of-truth."""

from dataclasses import dataclass
from datetime import datetime

from quiver.mcp.cli import (
    get_mcp_tools,
    get_tool_servers_canonical,
    load_json,
    load_registry,
    save_json,
)
from quiver.paths import MCP_SOURCE_FILE

MCP_SOURCE_KEY = "mcpServers"


@dataclass(frozen=True)
class McpFinding:
    name: str
    tools: tuple[str, ...]
    status: str  # new | in_source | tool_only
    source_tool: str
    server: dict


def _load_source_servers() -> dict:
    data = load_json(MCP_SOURCE_FILE)
    servers = data.get(MCP_SOURCE_KEY, data if MCP_SOURCE_KEY not in data and data else {})
    if not isinstance(servers, dict):
        return {}
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def discover_mcp_servers(*, include_in_source: bool = False) -> list[McpFinding]:
    """Find MCP servers in tool configs not yet in ~/.config/swe/mcp.json."""
    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)
    source = _load_source_servers()

    by_name: dict[str, dict] = {}
    for tool in sorted(mcp_tools):
        for name, server in get_tool_servers_canonical(tool).items():
            entry = by_name.setdefault(
                name,
                {"tools": set(), "server": server, "source_tool": tool},
            )
            entry["tools"].add(tool)

    findings: list[McpFinding] = []
    for name, meta in sorted(by_name.items()):
        tools = tuple(sorted(meta["tools"]))
        if name in source:
            status = "in_source"
        else:
            status = "new"
        findings.append(
            McpFinding(
                name=name,
                tools=tools,
                status=status,
                source_tool=meta["source_tool"],
                server=meta["server"],
            )
        )

    if not include_in_source:
        findings = [f for f in findings if f.status != "in_source"]
    return findings


def apply_mcp_findings(findings: list[McpFinding]) -> list[str]:
    """Merge new MCP findings into ~/.config/swe/mcp.json."""
    source_data = load_json(MCP_SOURCE_FILE)
    servers = dict(_load_source_servers())
    added: list[str] = []

    for finding in findings:
        if finding.status != "new":
            continue
        if finding.name in servers:
            continue
        servers[finding.name] = finding.server
        added.append(finding.name)

    if not added:
        return []

    source_data[MCP_SOURCE_KEY] = servers
    source_data.setdefault("updated", datetime.now().isoformat())
    save_json(MCP_SOURCE_FILE, source_data)
    return added
