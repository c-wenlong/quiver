"""Durable storage for coding-session reports and their intermediate state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from quiver.paths import REPORTS_DIR
from quiver.reports.models import (
    CadenceCursor,
    ExclusionRecord,
    Report,
    ReportManifest,
    SessionSummary,
    utc_now,
)


class MalformedReportStateError(ValueError):
    """Raised when persisted report state cannot be safely read or replaced."""

    def __init__(self, path: Path, message: str = "malformed report state") -> None:
        super().__init__(f"{message}: {path}")
        self.path = path


def _atomic_write(path: Path, content: str) -> None:
    """Write text durably without exposing a partially-written destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedReportStateError(path) from exc


def _safe_key(*parts: str) -> str:
    material = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _filename_component(value: str) -> str:
    """Keep persisted report names readable without trusting caller input."""
    readable = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    readable = readable.strip("._")[:80]
    return readable or _safe_key(value)[:16]


class ReportStore:
    """Store reports beneath ``REPORTS_DIR`` by default.

    The root is injectable so callers and tests can isolate state. Mutating a
    JSON state file first parses its current contents; malformed state is never
    overwritten implicitly.
    """

    def __init__(self, root: Path | str | None = None, clock: Callable[[], str] = utc_now) -> None:
        self.root = Path(root) if root is not None else REPORTS_DIR
        self.clock = clock

    @property
    def summaries_dir(self) -> Path:
        return self.root / "session-summaries"

    @property
    def exclusions_file(self) -> Path:
        return self.root / "exclusions.json"

    @property
    def cursors_file(self) -> Path:
        return self.root / "cursors.json"

    @property
    def followups_file(self) -> Path:
        return self.root / "followups.json"

    def _validate_cadence(self, cadence: str) -> str:
        normalized = cadence.strip().lower()
        if normalized not in {"daily", "weekly"}:
            raise ValueError("cadence must be 'daily' or 'weekly'")
        return normalized

    def _summary_path(self, session_id: str, source_tool: str) -> Path:
        return self.summaries_dir / f"{_safe_key(source_tool, session_id)}.json"

    def save_session_summary(self, summary: SessionSummary) -> Path:
        path = self._summary_path(summary.session_id, summary.source_tool)
        if path.exists():
            existing = _read_json(path, None)
            if not isinstance(existing, dict):
                raise MalformedReportStateError(path)
            try:
                SessionSummary.from_dict(existing)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise MalformedReportStateError(path) from exc
        _atomic_write_json(path, summary.to_dict())
        return path

    def get_session_summary(
        self, session_id: str, digest: str, source_tool: str | None = None
    ) -> SessionSummary | None:
        paths = (
            [self._summary_path(session_id, source_tool)]
            if source_tool is not None
            else sorted(self.summaries_dir.glob("*.json"))
        )
        for path in paths:
            data = _read_json(path, None)
            if data is None:
                continue
            if not isinstance(data, dict):
                raise MalformedReportStateError(path)
            try:
                summary = SessionSummary.from_dict(data)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise MalformedReportStateError(path) from exc
            if summary.session_id == session_id and summary.digest == digest:
                return summary
        return None

    def invalidate_session_summary(self, session_id: str, source_tool: str) -> bool:
        """Remove one valid cache entry, returning whether it existed."""
        path = self._summary_path(session_id, source_tool)
        if not path.exists():
            return False
        data = _read_json(path, None)
        if not isinstance(data, dict):
            raise MalformedReportStateError(path)
        try:
            summary = SessionSummary.from_dict(data)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise MalformedReportStateError(path) from exc
        if summary.session_id != session_id or summary.source_tool != source_tool:
            raise MalformedReportStateError(path)
        path.unlink()
        return True

    def record_exclusions(self, records: list[ExclusionRecord]) -> None:
        current = _read_json(self.exclusions_file, [])
        if not isinstance(current, list):
            raise MalformedReportStateError(self.exclusions_file)
        indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in current:
            if not isinstance(item, dict):
                raise MalformedReportStateError(self.exclusions_file)
            try:
                parsed = ExclusionRecord.from_dict(item)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise MalformedReportStateError(self.exclusions_file) from exc
            indexed[(parsed.source_tool, parsed.session_id, parsed.digest)] = parsed.to_dict()
        for record in records:
            indexed[(record.source_tool, record.session_id, record.digest)] = record.to_dict()
        ordered = sorted(indexed.values(), key=lambda item: (item["excluded_at"], item["session_id"]))
        _atomic_write_json(self.exclusions_file, ordered)

    def load_exclusions(self) -> list[ExclusionRecord]:
        data = _read_json(self.exclusions_file, [])
        if not isinstance(data, list):
            raise MalformedReportStateError(self.exclusions_file)
        try:
            return [ExclusionRecord.from_dict(item) for item in data]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise MalformedReportStateError(self.exclusions_file) from exc

    def write_report(
        self, markdown: str, manifest: ReportManifest
    ) -> tuple[Path, Path]:
        cadence = self._validate_cadence(manifest.cadence)
        generated = manifest.generated_at or self.clock()
        stamp = generated.replace(":", "").replace("-", "").replace("+", "_")
        basename = "_".join(
            _filename_component(item)
            for item in (manifest.period_start, manifest.period_end, stamp, manifest.report_id)
        )
        report_dir = self.root / cadence
        markdown_path = report_dir / f"{basename}.md"
        manifest_path = report_dir / f"{basename}.json"

        # Report IDs are immutable. Refuse to replace either half of an
        # existing report, even if a caller accidentally reuses its ID.
        if markdown_path.exists() or manifest_path.exists():
            raise FileExistsError(f"report already exists: {manifest.report_id}")

        persisted = replace(
            manifest,
            cadence=cadence,
            generated_at=generated,
            markdown_path=str(markdown_path),
        )
        _atomic_write(markdown_path, markdown)
        try:
            _atomic_write_json(manifest_path, persisted.to_dict())
        except BaseException:
            markdown_path.unlink(missing_ok=True)
            raise
        return markdown_path, manifest_path

    def save_report(self, report: Report) -> tuple[Path, Path]:
        """Persist a serializable report model."""
        return self.write_report(report.markdown, report.manifest)

    def load_manifest(self, path: Path | str) -> ReportManifest:
        manifest_path = Path(path)
        data = _read_json(manifest_path, None)
        if not isinstance(data, dict):
            raise MalformedReportStateError(manifest_path)
        try:
            return ReportManifest.from_dict(data)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise MalformedReportStateError(manifest_path) from exc

    def latest_manifest(self, cadence: str) -> ReportManifest | None:
        cadence_dir = self.root / self._validate_cadence(cadence)
        manifests = sorted(cadence_dir.glob("*.json"), reverse=True)
        return self.load_manifest(manifests[0]) if manifests else None

    def get_cursor(self, cadence: str) -> CadenceCursor | None:
        cadence = self._validate_cadence(cadence)
        data = _read_json(self.cursors_file, {})
        if not isinstance(data, dict):
            raise MalformedReportStateError(self.cursors_file)
        item = data.get(cadence)
        if item is None:
            return None
        if not isinstance(item, dict):
            raise MalformedReportStateError(self.cursors_file)
        try:
            return CadenceCursor.from_dict(item)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise MalformedReportStateError(self.cursors_file) from exc

    def advance_cursor(self, cadence: str, through: str, report_id: str) -> CadenceCursor:
        cadence = self._validate_cadence(cadence)
        data = _read_json(self.cursors_file, {})
        if not isinstance(data, dict):
            raise MalformedReportStateError(self.cursors_file)
        current = data.get(cadence)
        if current is not None:
            if not isinstance(current, dict):
                raise MalformedReportStateError(self.cursors_file)
            try:
                previous = CadenceCursor.from_dict(current)
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise MalformedReportStateError(self.cursors_file) from exc
            if through < previous.through:
                raise ValueError(
                    f"cannot move {cadence} cursor backwards "
                    f"from {previous.through} to {through}"
                )
        cursor = CadenceCursor(
            cadence=cadence,
            through=through,
            report_id=report_id,
            updated_at=self.clock(),
        )
        data[cadence] = cursor.to_dict()
        _atomic_write_json(self.cursors_file, data)
        return cursor
