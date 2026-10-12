"""``--json`` payload models for the init domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LinkRow:
    """One ``swe init --check --json`` harness row.

    Keyed by the registry name, so a row joins ``swe list --json`` on
    ``name``. A harness with no instruction file (or no skills root) carries
    ``null`` for that half rather than an invented state.
    """

    name: str
    instructions: dict | None = None
    skills: dict | None = None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "instructions": self.instructions,
            "skills": self.skills,
        }
