"""``--json`` payload models for the skills domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkReply:
    """The ``swe skills link --json`` reply."""

    harness: str
    source: str
    target: str

    def to_json(self) -> dict:
        return {
            "action": "link",
            "harness": self.harness,
            "source": self.source,
            "target": self.target,
        }


@dataclass(frozen=True)
class UnlinkReply:
    """The ``swe skills unlink --json`` reply."""

    harness: str
    path: str
    mkdir: bool

    def to_json(self) -> dict:
        return {
            "action": "unlink",
            "harness": self.harness,
            "path": self.path,
            "mkdir": self.mkdir,
        }


@dataclass(frozen=True)
class MoveReply:
    """The ``swe skills move --json`` reply."""

    name: str
    from_path: str
    to_path: str

    def to_json(self) -> dict:
        return {
            "action": "move",
            "name": self.name,
            "from": self.from_path,
            "to": self.to_path,
        }


@dataclass(frozen=True)
class CatalogRow:
    """One ``swe skills catalog discover --json`` row."""

    label: str
    path: str
    skill_count: int
    source: str
    status: str

    @classmethod
    def of(cls, f) -> "CatalogRow":
        return cls(
            label=f.label,
            path=str(f.path),
            skill_count=f.skill_count,
            source=f.source,
            status=f.status,
        )

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "path": self.path,
            "skill_count": self.skill_count,
            "source": self.source,
            "status": self.status,
        }
