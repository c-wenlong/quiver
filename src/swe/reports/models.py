"""Serializable domain models for coding-session reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp suitable for persisted state."""
    return datetime.now(timezone.utc).isoformat()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


@dataclass
class SessionSummary:
    session_id: str
    digest: str
    project_root: str
    source_tool: str
    started_at: str = ""
    ended_at: str = ""
    title: str = ""
    objective: str = ""
    outcome: str = ""
    summary: str = ""
    changes: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    context: str = ""
    generated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionSummary":
        return cls(
            session_id=str(data["session_id"]),
            digest=str(data["digest"]),
            project_root=str(data.get("project_root", "")),
            source_tool=str(data.get("source_tool", "")),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            title=str(data.get("title", "")),
            objective=str(data.get("objective", "")),
            outcome=str(data.get("outcome", "")),
            summary=str(data.get("summary", "")),
            changes=_string_list(data.get("changes")),
            decisions=_string_list(data.get("decisions")),
            blockers=_string_list(data.get("blockers")),
            follow_ups=_string_list(data.get("follow_ups")),
            context=str(data.get("context", "")),
            generated_at=str(data.get("generated_at", "")) or utc_now(),
        )


@dataclass
class ExclusionRecord:
    session_id: str
    digest: str
    reason: str
    source_tool: str = ""
    project_root: str = ""
    excluded_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExclusionRecord":
        return cls(
            session_id=str(data["session_id"]),
            digest=str(data["digest"]),
            reason=str(data["reason"]),
            source_tool=str(data.get("source_tool", "")),
            project_root=str(data.get("project_root", "")),
            excluded_at=str(data.get("excluded_at", "")) or utc_now(),
        )


@dataclass
class ReportManifest:
    report_id: str
    cadence: str
    period_start: str
    period_end: str
    generated_at: str
    markdown_path: str = ""
    source_session_ids: list[str] = field(default_factory=list)
    summary_digests: dict[str, str] = field(default_factory=dict)
    exclusion_ids: list[str] = field(default_factory=list)
    follow_up_ids: list[str] = field(default_factory=list)
    partial: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReportManifest":
        digests = data.get("summary_digests", {})
        if not isinstance(digests, dict):
            digests = {}
        return cls(
            report_id=str(data["report_id"]),
            cadence=str(data["cadence"]),
            period_start=str(data["period_start"]),
            period_end=str(data["period_end"]),
            generated_at=str(data["generated_at"]),
            markdown_path=str(data.get("markdown_path", "")),
            source_session_ids=_string_list(data.get("source_session_ids")),
            summary_digests={str(k): str(v) for k, v in digests.items()},
            exclusion_ids=_string_list(data.get("exclusion_ids")),
            follow_up_ids=_string_list(data.get("follow_up_ids")),
            partial=bool(data.get("partial", False)),
            warnings=_string_list(data.get("warnings")),
        )


@dataclass
class Report:
    """A rendered report paired with the manifest that describes its inputs."""

    markdown: str
    manifest: ReportManifest

    def to_dict(self) -> dict[str, Any]:
        return {"markdown": self.markdown, "manifest": self.manifest.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Report":
        manifest = data["manifest"]
        if not isinstance(manifest, dict):
            raise TypeError("report manifest must be an object")
        return cls(
            markdown=str(data.get("markdown", "")),
            manifest=ReportManifest.from_dict(manifest),
        )


@dataclass
class CadenceCursor:
    cadence: str
    through: str
    report_id: str
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CadenceCursor":
        return cls(
            cadence=str(data["cadence"]),
            through=str(data["through"]),
            report_id=str(data["report_id"]),
            updated_at=str(data.get("updated_at", "")) or utc_now(),
        )


FOLLOW_UP_STATUSES = frozenset({"open", "done", "dismissed"})


@dataclass
class FollowUp:
    id: str
    text: str
    project_root: str
    status: str = "open"
    source_session_ids: list[str] = field(default_factory=list)
    source_report_ids: list[str] = field(default_factory=list)
    context: str = ""
    blockers: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    dismissed_at: str | None = None
    resolution_suggested: bool = False
    resolution_suggestion: str = ""
    resolution_suggested_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in FOLLOW_UP_STATUSES:
            raise ValueError(f"invalid follow-up status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FollowUp":
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            project_root=str(data.get("project_root", "")),
            status=str(data.get("status", "open")),
            source_session_ids=_string_list(data.get("source_session_ids")),
            source_report_ids=_string_list(data.get("source_report_ids")),
            context=str(data.get("context", "")),
            blockers=_string_list(data.get("blockers")),
            completion_criteria=_string_list(data.get("completion_criteria")),
            created_at=str(data.get("created_at", "")) or utc_now(),
            updated_at=str(data.get("updated_at", "")) or utc_now(),
            completed_at=data.get("completed_at"),
            dismissed_at=data.get("dismissed_at"),
            resolution_suggested=bool(data.get("resolution_suggested", False)),
            resolution_suggestion=str(data.get("resolution_suggestion", "")),
            resolution_suggested_at=data.get("resolution_suggested_at"),
        )
