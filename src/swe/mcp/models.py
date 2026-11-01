"""``--json`` payload models for the mcp domain."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerRow:
    """One ``swe mcp list --json`` / ``swe mcp status --json`` server row.

    Env and header values stay out; only their names go into the row. The
    command, arguments and URL are already scrubbed by the caller, since a
    credential can sit in any of them (see ``swe.mcp.secrets.scrub_url``).
    """

    name: str
    transport: str
    command: str | None
    args: list
    url: str | None
    env_keys: list
    header_keys: list
    harnesses: list
    conflict: bool
    variants: list
    # ``${NAME}`` references in the hub's definition the secrets file can't
    # satisfy; [] for a server the hub doesn't hold. Swe fills them from
    # `swe mcp secret set`, and sync refuses until then.
    secrets_missing: list
    # Whether the hub (mcp.json) defines this server. A hub-only row has
    # empty harnesses and variants.
    in_hub: bool
    # The places (harnesses, and "quiver" for the hub) whose definition is
    # reached at a URL. ``transport`` is the first definition's; a sync
    # copies one place's, so it asks this.
    remote_in: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": self.args,
            "url": self.url,
            "env_keys": self.env_keys,
            "header_keys": self.header_keys,
            "harnesses": self.harnesses,
            "conflict": self.conflict,
            "variants": self.variants,
            "secrets_missing": self.secrets_missing,
            "in_hub": self.in_hub,
            "remote_in": self.remote_in,
        }


@dataclass(frozen=True)
class DiscoveryRow:
    """One ``swe mcp discover --json`` row (a bare list, no envelope)."""

    name: str
    tools: list
    status: str
    source_tool: str
    summary: str

    @classmethod
    def of(cls, f, summary: str) -> "DiscoveryRow":
        return cls(
            name=f.name,
            tools=list(f.tools),
            status=f.status,
            source_tool=f.source_tool,
            summary=summary,
        )

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "tools": self.tools,
            "status": self.status,
            "source_tool": self.source_tool,
            "summary": self.summary,
        }
