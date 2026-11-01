"""``--json`` payload models for the setup domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessPreview:
    """One harness in the ``swe setup --json`` preview."""

    name: str
    command: str
    path: str

    @classmethod
    def of(cls, f) -> "HarnessPreview":
        return cls(name=f.name, command=f.command, path=f.path)

    def to_json(self) -> dict:
        return {"name": self.name, "command": self.command, "path": self.path}


@dataclass(frozen=True)
class McpPreview:
    """One MCP server in the ``swe setup --json`` preview."""

    name: str
    tools: list

    @classmethod
    def of(cls, f) -> "McpPreview":
        return cls(name=f.name, tools=list(f.tools))

    def to_json(self) -> dict:
        return {"name": self.name, "tools": self.tools}


@dataclass(frozen=True)
class SkillsPreview:
    """One skills hint in the ``swe setup --json`` preview."""

    label: str
    action: str
    command: str
    reason: str

    @classmethod
    def of(cls, h) -> "SkillsPreview":
        return cls(label=h.label, action=h.action,
                   command=h.command, reason=h.reason)

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "action": self.action,
            "command": self.command,
            "reason": self.reason,
        }
