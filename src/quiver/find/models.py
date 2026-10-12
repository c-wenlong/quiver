"""``--json`` payload models for the find domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriftRow:
    """One ``swe find plugins --json`` drift row, with the fix it names."""

    id: str
    harness: str
    marketplace: str | None
    plugin: str | None
    ref: str | None
    kind: str
    detail: str
    fix: str
    runnable: bool
    paths: list

    @classmethod
    def of(cls, f) -> "DriftRow":
        return cls(
            id=f.id,
            harness=f.harness,
            marketplace=f.marketplace,
            plugin=f.plugin or None,
            ref=f.ref,
            kind=f.kind,
            detail=f.detail,
            fix=f.fix,
            runnable=bool(f.argv),
            paths=list(f.paths),
        )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "harness": self.harness,
            "marketplace": self.marketplace,
            "plugin": self.plugin,
            "ref": self.ref,
            "kind": self.kind,
            "detail": self.detail,
            "fix": self.fix,
            "runnable": self.runnable,
            "paths": self.paths,
        }
