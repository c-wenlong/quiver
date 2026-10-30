"""The ``swe mcp list --json`` / ``swe mcp status --json`` server inventory."""

from __future__ import annotations

from quiver.mcp import gateway
from quiver.mcp.models import ServerRow
from quiver.mcp.secrets import (
    load_secrets,
    redact as redact_secrets,
    resolve as resolve_secrets,
    scrub_args,
    scrub_url,
    unresolved_names,
)


def transport_of(raw, canonical: dict) -> str:
    """How a server is reached: stdio, http, sse, wrapped or unknown.

    The canonical shape drops ``type``, so the one distinction it cannot
    carry, legacy SSE against streamable HTTP, is read from the raw entry.
    """
    from quiver.mcp.cli import server_type

    kind = server_type(canonical)
    if (kind == "http" and isinstance(raw, dict)
            and str(raw.get("type", "")).lower() == "sse"):
        return "sse"
    return kind


def _gateway_settings() -> dict:
    from quiver.configuration import CorruptConfigurationError
    from quiver.mcp.cli import MCP_SOURCE_FILE, load_json

    try:
        return gateway.settings_of(load_json(MCP_SOURCE_FILE))
    except CorruptConfigurationError:
        return gateway.settings_of(None)


def _is_url(raw) -> bool:
    """Whether a hub definition is reached at a URL."""
    from quiver.mcp.cli import get_mcp_handler

    return isinstance(raw, dict) and bool(get_mcp_handler("quiver").parse(raw).get("url"))


def identity_of(raw, canonical: dict) -> tuple:
    """What makes two definitions of one server the same server.

    Compared on canonical fields with absent and empty treated alike, so a
    harness that spells out ``args: []`` or ``type: stdio`` does not
    disagree with one that leaves them off. Only ever compared, never
    printed: it holds the env and header values.
    """
    return (
        # SSE and streamable HTTP at one URL are different servers to a
        # client, and only the raw entry still says which one it is.
        transport_of(raw, canonical),
        canonical.get("command") or "",
        tuple(canonical.get("args") or ()),
        tuple(sorted((canonical.get("env") or {}).items())),
        canonical.get("url") or "",
        tuple(sorted((canonical.get("headers") or {}).items())),
    )


def server_inventory(tool_data: dict, include_hub: bool = False) -> list[tuple[dict, object]]:
    """One ``(json_row, first_raw_config)`` per server name, sorted by name.

    ``tool_data`` maps each tool to its raw ``{name: config}``. The row is
    what ``--json`` prints: identity taken from the first tool that defines
    the server, every tool it appears in, whether those tools disagree, and
    ``variants``: the tools grouped by identical definition, in order of
    first appearance, so a caller can say which ones agree.
    Env and header values stay out; only their names go into the row. The
    command, arguments and URL are scrubbed, since a credential can sit in
    any of them (see ``quiver.mcp.secrets.scrub_url``).
    """
    from quiver.mcp.cli import get_hub_servers, get_mcp_handler

    secrets = load_secrets()
    # Read once for every server: ``secrets_missing`` is the hub's own
    # ``${NAME}`` references the store cannot satisfy.
    hub = get_hub_servers()
    gateway_settings = _gateway_settings()
    hub_handler = get_mcp_handler("quiver")
    seen: dict[str, dict] = {}
    for tool, servers in tool_data.items():
        handler = get_mcp_handler(tool)
        for name, raw in servers.items():
            canonical = handler.parse(raw) if isinstance(raw, dict) else {}
            ident_raw, ident_canonical = raw, canonical
            if (gateway.is_shared(hub.get(name))
                    and gateway.is_gateway_entry(name, canonical, gateway_settings)):
                # The gateway URL is how sync writes a shared hub server,
                # so it agrees with a harness holding the command itself.
                ident_raw = resolve_secrets(gateway.without_flags(hub[name]), secrets)
                ident_canonical = hub_handler.parse(ident_raw)
            entry = seen.setdefault(
                name, {"raw": raw, "canonical": canonical, "tools": [],
                       "identities": [], "remote": []})
            entry["tools"].append(tool)
            if canonical.get("url"):
                entry["remote"].append(tool)
            entry["identities"].append(identity_of(ident_raw, ident_canonical))
    inventory = []
    for name in sorted(seen):
        entry = seen[name]
        canonical = entry["canonical"]
        env = canonical.get("env") or {}
        headers = canonical.get("headers") or {}
        # Grouped by equality, not hashing: an identity can hold a list or a
        # dict straight from a hand-edited config.
        groups: list[tuple[tuple, list[str]]] = []
        for tool, ident in zip(entry["tools"], entry["identities"]):
            group = next((g for g in groups if g[0] == ident), None)
            if group is None:
                groups.append((ident, [tool]))
            else:
                group[1].append(tool)
        row = ServerRow(
            name=name,
            transport=transport_of(entry["raw"], canonical),
            command=(redact_secrets(str(canonical["command"]), secrets)
                     if canonical.get("command") else None),
            args=scrub_args(canonical.get("args") or [], secrets),
            url=(scrub_url(str(canonical["url"]), secrets)
                 if canonical.get("url") else None),
            env_keys=sorted(str(k) for k in env),
            header_keys=sorted(str(k) for k in headers),
            harnesses=entry["tools"],
            conflict=len(groups) > 1,
            variants=[tools for _, tools in groups],
            secrets_missing=(unresolved_names(hub[name], secrets)
                             if name in hub else []),
            in_hub=name in hub,
            remote_in=entry["remote"] + (["quiver"] if name in hub and _is_url(hub[name]) else []),
        )
        inventory.append((row.to_json(), entry["raw"]))

    # Servers only the hub holds: no harness defines them, so they get a
    # row with empty harnesses/variants, built from the hub's own entry.
    # Only when every harness was listed — with a target a server merely
    # absent from that one config is not hub-only.
    handler = get_mcp_handler("quiver")
    for name in sorted(set(hub) - set(seen)) if include_hub else ():
        raw = hub[name]
        canonical = handler.parse(raw) if isinstance(raw, dict) else {}
        env = canonical.get("env") or {}
        headers = canonical.get("headers") or {}
        row = ServerRow(
            name=name,
            transport=transport_of(raw, canonical),
            command=(redact_secrets(str(canonical["command"]), secrets)
                     if canonical.get("command") else None),
            args=scrub_args(canonical.get("args") or [], secrets),
            url=(scrub_url(str(canonical["url"]), secrets)
                 if canonical.get("url") else None),
            env_keys=sorted(str(k) for k in env),
            header_keys=sorted(str(k) for k in headers),
            harnesses=[],
            conflict=False,
            variants=[],
            secrets_missing=unresolved_names(raw, secrets),
            in_hub=True,
            remote_in=["quiver"] if canonical.get("url") else [],
        )
        inventory.append((row.to_json(), raw))
    inventory.sort(key=lambda pair: pair[0]["name"])
    return inventory
