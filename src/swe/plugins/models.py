"""``--json`` payload models for the plugins domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionResult:
    """The ``swe plugins install|enable|disable|uninstall --json`` reply."""

    action: str
    harness: str
    ref: str
    changed: bool
    detail: str | None
    ran: list
    edited: str | None
    output: str | None
    state: dict

    def to_json(self) -> dict:
        return {
            "action": self.action,
            "harness": self.harness,
            "ref": self.ref,
            "changed": self.changed,
            "detail": self.detail,
            "ran": self.ran,
            "edited": self.edited,
            "output": self.output,
            "state": self.state,
        }
