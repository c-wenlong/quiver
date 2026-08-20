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
from quiver.mcp.secrets import redact as redact_secrets
from quiver.paths import MCP_SOURCE_FILE

MCP_SOURCE_KEY = "mcpServers"


@dataclass(frozen=True)
class McpFinding:
    name: str
    tools: tuple[str, ...]
    # new       present in a harness, absent from the hub
    # changed   present in both, and the harness copy differs
    # in_source present in both and identical
    # orphaned  present in the hub, absent from every harness
    status: str
    source_tool: str
    server: dict


def _load_source_servers() -> dict:
    data = load_json(MCP_SOURCE_FILE)
    servers = data.get(MCP_SOURCE_KEY, data if MCP_SOURCE_KEY not in data and data else {})
    if not isinstance(servers, dict):
        return {}
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def discover_mcp_servers(*, include_in_source: bool = False) -> list[McpFinding]:
    """Compare every harness config against the hub at ~/.quiver/mcp.json."""
    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)
    source = _load_source_servers()

    by_name: dict[str, dict] = {}
    for tool in sorted(mcp_tools):
        # Harness configs hold real values. Swap them back to ${NAME} so
        # discover cannot quietly undo the indirection.
        for name, server in redact_secrets(
            get_tool_servers_canonical(tool)
        ).items():
            entry = by_name.setdefault(
                name,
                {"tools": set(), "server": server, "source_tool": tool},
            )
            entry["tools"].add(tool)

    findings: list[McpFinding] = []
    for name, meta in sorted(by_name.items()):
        tools = tuple(sorted(meta["tools"]))
        if name not in source:
            status = "new"
        elif source[name] != meta["server"]:
            # Both sides are canonical and redacted by this point, so a
            # difference is a real edit rather than a formatting artefact.
            status = "changed"
        else:
            status = "in_source"
        findings.append(
            McpFinding(
                name=name,
                tools=tools,
                status=status,
                source_tool=meta["source_tool"],
                server=meta["server"],
            )
        )

    # A server the hub still lists but no harness configures any more. Reported
    # rather than deleted: quiver cannot tell a deliberate removal from a
    # harness whose config file is temporarily unreadable.
    for name in sorted(set(source) - set(by_name)):
        findings.append(
            McpFinding(
                name=name,
                tools=(),
                status="orphaned",
                source_tool="",
                server=source[name],
            )
        )

    if not include_in_source:
        findings = [f for f in findings if f.status != "in_source"]
    return findings


@dataclass(frozen=True)
class MergeResult:
    """What a merge did, and what it deliberately left alone."""

    added: list[str]
    updated: list[str]
    orphaned: list[str]
    pruned: list[str]

    @property
    def wrote(self) -> bool:
        return bool(self.added or self.updated or self.pruned)


def apply_mcp_findings(findings: list[McpFinding], prune: bool = False) -> MergeResult:
    """Merge findings into the hub at ~/.quiver/mcp.json.

    Adds servers the hub has not seen and updates ones a harness has since
    edited. Orphans are reported, and only removed when ``prune`` is set: a
    harness config that failed to parse looks identical to a deliberate
    deletion, and silently dropping a server would lose its credentials.
    """
    source_data = load_json(MCP_SOURCE_FILE)
    servers = dict(_load_source_servers())
    added: list[str] = []
    updated: list[str] = []
    orphaned: list[str] = []
    pruned: list[str] = []

    for finding in findings:
        if finding.status == "new":
            if finding.name not in servers:
                servers[finding.name] = finding.server
                added.append(finding.name)
        elif finding.status == "changed":
            servers[finding.name] = finding.server
            updated.append(finding.name)
        elif finding.status == "orphaned":
            orphaned.append(finding.name)
            if prune:
                servers.pop(finding.name, None)
                pruned.append(finding.name)

    result = MergeResult(sorted(added), sorted(updated), sorted(orphaned), sorted(pruned))
    if not result.wrote:
        return result

    source_data[MCP_SOURCE_KEY] = servers
    # Assignment, not setdefault: the old code only ever stamped the first
    # write, so the file claimed to be hours older than it was.
    source_data["updated"] = datetime.now().isoformat()
    save_json(MCP_SOURCE_FILE, source_data)
    return result
