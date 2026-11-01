"""Discover MCP servers across tool configs vs quiver source-of-truth."""

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from swe.configuration import CorruptConfigurationError
from swe.console import c
from swe.mcp.cli import (
    get_mcp_tools,
    get_tool_config,
    get_tool_servers_canonical,
    load_json,
    load_registry,
    normalize_server,
    save_json,
)
from swe.mcp import gateway
from swe.mcp.secrets import redact as redact_secrets
from swe.mcp.secrets import shield_credentials
from swe.paths import MCP_SOURCE_FILE

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
    try:
        data = load_json(MCP_SOURCE_FILE)
    except CorruptConfigurationError as exc:
        # Read path: report the hub as empty rather than crashing discover.
        # apply_mcp_findings calls load_json itself, so writes still refuse
        # to overwrite the malformed file. stderr keeps --json output clean.
        print(c("yellow", f"  {exc}"), file=sys.stderr)
        return {}
    if MCP_SOURCE_KEY in data:
        servers = data[MCP_SOURCE_KEY]
    else:
        # A legacy flat hub keeps servers at the top level. The gateway's
        # settings live there too, and `swe mcp gateway install` writes
        # them into a hub that has no servers yet: read as a server, they
        # reached harness configs as an empty entry named "gateway".
        servers = {k: v for k, v in data.items() if k != gateway.GATEWAY_KEY}
    if not isinstance(servers, dict):
        return {}
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def _gateway_settings() -> dict:
    try:
        return gateway.settings_of(load_json(MCP_SOURCE_FILE))
    except CorruptConfigurationError:
        return gateway.settings_of(None)


def _unreadable_tools(mcp_tools) -> set[str]:
    """Tools whose MCP config exists on disk but cannot be parsed.

    A corrupt file reads as zero servers, so without this its servers look
    "orphaned" — and `discover --apply --prune` would delete live entries
    from the hub. Any unreadable config poisons orphan detection entirely.
    """
    from swe.mcp.codex_io import load_codex_servers

    out: set[str] = set()
    for tool in mcp_tools:
        cfg = get_tool_config(tool)
        if not cfg:
            continue
        path = cfg.get("path")
        if not path:
            continue
        try:
            p = Path(str(path)).expanduser()
        except (OSError, RuntimeError):
            continue
        if not p.exists():
            continue
        try:
            if cfg.get("io_type") == "toml_region":
                load_codex_servers(p)
            else:
                load_json(p)
        except Exception:
            out.add(tool)
    return out


def _registered_paths(mcp_tools) -> set:
    """Resolved config paths quiver already reads, to skip on the disk pass."""
    from pathlib import Path

    out = set()
    for tool in mcp_tools:
        cfg = get_tool_config(tool) or {}
        path = cfg.get("path")
        if not path:
            continue
        try:
            out.add(Path(str(path)).expanduser().resolve())
        except (OSError, RuntimeError):
            # RuntimeError: ``~missing-user`` has no home to expand into.
            continue
    return out


def _scanned_servers(mcp_tools) -> dict:
    """Servers in MCP configs on disk that no registered path covers.

    The registered list is what quiver was told about, so on its own it
    reports a clean bill of health for anything nobody registered. Here
    that was four config files holding three unknown servers, including a
    whole harness (LM Studio) quiver had no entry for.

    Vendored configs are excluded: an editor extension shipping its own
    server is not something the hub should adopt.
    """
    from swe.find.mcps import scan_configs

    known = _registered_paths(mcp_tools)
    out: dict[str, dict] = {}
    for cfg in scan_configs(scope="global"):
        try:
            if cfg.path.resolve() in known:
                continue
        except OSError:
            continue
        if cfg.harness in ("quiver",):
            continue
        # Scanned entries are raw, so they take the same normalise and
        # redact path as registered ones. Skipping it would write literal
        # credentials into the hub.
        for name, raw in cfg.servers.items():
            if not isinstance(raw, dict):
                continue
            out.setdefault(name, {"server": normalize_server(raw),
                                  "tool": cfg.harness})
    return out


def discover_mcp_servers(*, include_in_source: bool = False) -> list[McpFinding]:
    """Compare every MCP config against the hub at ~/.quiver/mcp.json.

    Reads both the config paths quiver has registered and the ones found by
    scanning the harness directories, because a server in a config nobody
    registered is exactly the one worth telling you about.
    """
    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)
    source = _load_source_servers()
    settings = _gateway_settings()
    unreadable = _unreadable_tools(mcp_tools)

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

    for name, meta in sorted(_scanned_servers(mcp_tools).items()):
        server = redact_secrets({name: meta["server"]})[name]
        entry = by_name.setdefault(
            name,
            {"tools": set(), "server": server, "source_tool": meta["tool"]},
        )
        entry["tools"].add(meta["tool"])

    findings: list[McpFinding] = []
    for name, meta in sorted(by_name.items()):
        tools = tuple(sorted(meta["tools"]))
        if name not in source:
            status = "new"
        elif not gateway.matches_hub(name, source[name], meta["server"], settings):
            # Both sides are canonical and redacted by this point, so a
            # difference is a real edit rather than a formatting artefact.
            # The gateway URL sync writes for a shared server is not one.
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
    # harness whose config file is temporarily unreadable. When any harness
    # config failed to parse, skip orphan detection entirely — those tools'
    # servers look absent but are not, so pruning would delete live entries.
    if unreadable:
        print(
            c("yellow",
              "  cannot read " + ", ".join(sorted(unreadable))
              + " — skipping orphan detection"),
            file=sys.stderr,
        )
    else:
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


def _shielded(server: dict) -> dict | None:
    """Move credential-shaped literals into the store before a hub write.

    redact() only swaps values the store already holds, so a literal
    ``env.API_KEY`` would land in the versioned hub file in plaintext.
    Every move is reported on stderr — the run stays honest and --json
    output clean. Returns None when a credential could not be stored:
    a half-shielded entry would still leak the failing literal, so the
    server is left out of the hub instead.
    """
    shielded, notes = shield_credentials(server)
    for note in notes:
        print(c("yellow", f"  {note}"), file=sys.stderr)
    return shielded


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
                shielded = _shielded(finding.server)
                if shielded is not None:
                    servers[finding.name] = shielded
                    added.append(finding.name)
        elif finding.status == "changed":
            shielded = _shielded(finding.server)
            if shielded is not None:
                if (gateway.is_shared(servers.get(finding.name))
                        and shielded.get("command") and not shielded.get("url")):
                    # An edit to the command keeps the server shared.
                    shielded = {**shielded, gateway.SHARED_KEY: True}
                servers[finding.name] = shielded
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
