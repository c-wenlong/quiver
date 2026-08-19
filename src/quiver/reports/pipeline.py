"""Previewable, cost-bounded orchestration for coding-session reports."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from quiver.reports.batching import (
    DEFAULT_MAX_BATCH_CHARS,
    DEFAULT_MAX_BATCH_SESSIONS,
    DEFAULT_MAX_TRANSCRIPT_CHARS,
    SummaryBatch,
    SummaryInput,
    build_summary_batches,
    compact_transcript,
    make_summary_input,
)
from quiver.reports.followups import FollowUpLedger, stable_follow_up_id
from quiver.reports.models import (
    ExclusionRecord,
    FollowUp,
    Report,
    ReportManifest,
    SessionSummary,
    utc_now,
)
from quiver.reports.prompts import build_final_report_prompt, build_session_summary_prompt
from quiver.reports.runners import RunnerResult, RunnerSpec, run_structured
from quiver.reports.store import ReportStore
from quiver.reports.transcripts import NormalizedTranscript, read_transcript
from quiver.reports.triage import DEDICATED, NOISE, classify_transcript
from quiver.sessions.models import Session


DEFAULT_MAX_WORKERS = 3
DEFAULT_MAX_SUMMARY_CALLS = 20
DEFAULT_MAX_ESTIMATED_INPUT_TOKENS = 200_000
DEFAULT_MAX_WRITER_INPUT_CHARS = 240_000
WRITER_PROMPT_OVERHEAD_TOKENS = 1_500
WRITER_COMPACTION_MARKER = "\n[...compacted for writer input limit...]\n"


@dataclass(frozen=True)
class ReportPreview:
    cadence: str
    period_start: str
    period_end: str
    total: int
    excluded: int
    unreadable: int
    cached: int
    project_batched: int
    dedicated: int
    estimated_summary_calls: int
    estimated_summary_input_tokens: int
    estimated_writer_input_tokens: int
    estimated_input_tokens: int
    writer_calls: int
    warnings: tuple[str, ...]
    over_budget: bool
    batches: tuple[SummaryBatch, ...]
    cached_summaries: tuple[SessionSummary, ...]
    exclusions: tuple[ExclusionRecord, ...]
    source_session_ids: tuple[str, ...]
    previous_report: str
    open_follow_ups: tuple[FollowUp, ...]
    writer_prompt_char_limit: int
    advance_cursor: bool = True

    @property
    def project_batch_calls(self) -> int:
        return sum(not batch.dedicated for batch in self.batches)


@dataclass(frozen=True)
class ApprovedReportPlan:
    """A frozen preview paired with the user's explicit approval token."""

    preview: ReportPreview
    approval: bool | str


@dataclass(frozen=True)
class ReportRunResult:
    report: Report
    markdown_path: Path
    manifest_path: Path
    summaries: tuple[SessionSummary, ...]
    warnings: tuple[str, ...]


class ReportApprovalError(PermissionError):
    pass


class ReportWriterError(RuntimeError):
    pass


def approval_granted(preview: ReportPreview, approval: bool | str) -> bool:
    """Return whether an approval token authorizes this exact preview."""

    if preview.over_budget:
        return isinstance(approval, str) and approval == "process all"
    if approval is True:
        return True
    return isinstance(approval, str) and approval.strip().casefold() in {"y", "yes"}


def require_approval(plan: ApprovedReportPlan) -> None:
    if not approval_granted(plan.preview, plan.approval):
        if plan.preview.over_budget:
            raise ReportApprovalError('over-budget reports require the exact phrase "process all"')
        raise ReportApprovalError("report generation was not approved")


def _session_payload(item: SummaryInput) -> dict[str, Any]:
    return {
        "session_id": item.session.session_id,
        "source_tool": item.session.tool_name,
        "title": item.session.title,
        "timestamp": item.session.timestamp,
        "messages": [asdict(message) for message in item.transcript.messages],
    }


def _summary_from_output(item: SummaryInput, data: Mapping[str, Any]) -> SessionSummary:
    return SessionSummary(
        session_id=item.session.session_id,
        digest=item.digest,
        project_root=item.project_root,
        source_tool=item.session.tool_name,
        ended_at=str(item.session.timestamp),
        title=item.session.title,
        objective=str(data.get("objective", "")),
        outcome=str(data.get("outcome", "")),
        summary=str(data.get("outcome", "")),
        changes=list(data.get("changes", [])),
        decisions=list(data.get("decisions", [])),
        blockers=list(data.get("blockers", [])),
        follow_ups=list(data.get("follow_ups", [])),
        context=str(data.get("context", "")),
    )


def _estimated_tokens(characters: int) -> int:
    return (characters + 2) // 3 + WRITER_PROMPT_OVERHEAD_TOKENS


def _compact_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= len(WRITER_COMPACTION_MARKER):
        return value[:limit]
    remaining = limit - len(WRITER_COMPACTION_MARKER)
    beginning = (remaining + 1) // 2
    ending = remaining - beginning
    return value[:beginning] + WRITER_COMPACTION_MARKER + value[-ending:]


def _compact_mapping(value: Mapping[str, Any], limit: int = 4_000) -> dict[str, Any]:
    """Bound one writer record while retaining identifiers and both text edges."""

    result: dict[str, Any] = {}
    string_keys = [key for key, item in value.items() if isinstance(item, str)]
    per_string = max(80, limit // max(1, len(string_keys)))
    for key, item in value.items():
        if isinstance(item, str):
            result[str(key)] = _compact_text(item, per_string)
        elif isinstance(item, list):
            kept = [_compact_text(str(entry), 400) for entry in item[:20]]
            if len(item) > len(kept):
                kept.append(f"[...{len(item) - len(kept)} items omitted...]")
            result[str(key)] = kept
        elif isinstance(item, Mapping):
            result[str(key)] = _compact_mapping(item, max(400, limit // 2))
        else:
            result[str(key)] = item
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True)
    if len(encoded) <= limit:
        return result
    # Fall back to a bounded serialized record while keeping routing identifiers
    # as first-class fields for citations and follow-up evidence.
    identifiers = {
        key: _compact_text(str(result[key]), 300)
        for key in ("session_id", "source_tool", "project_root", "project_path")
        if key in result
    }
    available = max(40, limit - len(json.dumps(identifiers, ensure_ascii=True)) - 80)
    bounded = {**identifiers, "compacted_record": _compact_text(encoded, available)}
    while len(json.dumps(bounded, ensure_ascii=True, sort_keys=True)) > limit and available > 1:
        excess = len(json.dumps(bounded, ensure_ascii=True, sort_keys=True)) - limit
        available = max(1, available - excess - 4)
        bounded["compacted_record"] = _compact_text(encoded, available)
    return bounded


def _compact_records(
    records: list[Mapping[str, Any]], limit: int, label: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Select records deterministically from both ends within a character budget."""

    if not records or limit <= 2:
        warning = f"writer input omitted {len(records)} {label}" if records else None
        return [], warning
    compacted = [_compact_mapping(record) for record in records]
    selected: list[tuple[int, dict[str, Any]]] = []
    used = 2
    left, right = 0, len(compacted) - 1
    take_left = True
    while left <= right:
        index = left if take_left else right
        item = compacted[index]
        cost = len(json.dumps(item, ensure_ascii=True, sort_keys=True)) + 2
        if used + cost > limit:
            break
        selected.append((index, item))
        used += cost
        if take_left:
            left += 1
        else:
            right -= 1
        take_left = not take_left
    selected.sort(key=lambda pair: pair[0])
    omitted = len(records) - len(selected)
    warning = f"writer input omitted {omitted} of {len(records)} {label}" if omitted else None
    return [item for _, item in selected], warning


def _bounded_writer_material(
    summaries: list[Mapping[str, Any]],
    project_outputs: list[Mapping[str, Any]],
    warnings: list[str],
    previous_report: str,
    open_follow_ups: list[Mapping[str, Any]],
    *,
    max_chars: int,
) -> tuple[list[Mapping[str, Any]], str, list[Mapping[str, Any]], list[str]]:
    """Compact every writer input category under deterministic fixed shares."""

    summary_budget = max_chars * 50 // 100
    project_budget = max_chars * 15 // 100
    follow_up_budget = max_chars * 15 // 100
    previous_budget = max_chars * 15 // 100
    warning_budget = max_chars - sum(
        (summary_budget, project_budget, follow_up_budget, previous_budget)
    )
    compacted_summaries, summary_warning = _compact_records(
        summaries, summary_budget, "session summaries"
    )
    compacted_projects, project_warning = _compact_records(
        project_outputs, project_budget, "project summaries"
    )
    compacted_follow_ups, follow_up_warning = _compact_records(
        open_follow_ups, follow_up_budget, "open follow-ups"
    )
    compacted_previous = _compact_text(previous_report, previous_budget)
    compaction_warnings = [
        item for item in (summary_warning, project_warning, follow_up_warning) if item
    ]
    if len(previous_report) > len(compacted_previous):
        compaction_warnings.append(
            f"writer input compacted previous report from {len(previous_report)} "
            f"to {len(compacted_previous)} characters"
        )
    warning_records = [{"pipeline_warning": item} for item in [*warnings, *compaction_warnings]]
    compacted_warning_records, warning_warning = _compact_records(
        warning_records, warning_budget, "pipeline warnings"
    )
    if warning_warning:
        compaction_warnings.append(warning_warning)
    writer_inputs: list[Mapping[str, Any]] = [*compacted_summaries, *compacted_projects]
    writer_inputs.extend(compacted_warning_records)
    return writer_inputs, compacted_previous, compacted_follow_ups, compaction_warnings


def _projected_summary(item: SummaryInput) -> dict[str, Any]:
    """Conservatively reserve writer space for a not-yet-generated summary."""

    placeholder = "x" * min(2_000, max(500, item.normalized_chars // 6))
    return {
        "session_id": item.session.session_id,
        "source_tool": item.session.tool_name,
        "project_root": item.project_root,
        "title": item.session.title,
        "objective": placeholder,
        "outcome": placeholder,
        "changes": [placeholder],
        "decisions": [placeholder],
        "blockers": [placeholder],
        "follow_ups": [placeholder],
        "context": placeholder,
    }


class ReportPipeline:
    def __init__(
        self,
        session_runner: RunnerSpec,
        writer_runner: RunnerSpec,
        *,
        store: ReportStore | None = None,
        ledger: FollowUpLedger | None = None,
        transcript_reader: Callable[[Session], NormalizedTranscript] = read_transcript,
        runner: Callable[..., RunnerResult] = run_structured,
        clock: Callable[[], str] = utc_now,
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_batch_sessions: int = DEFAULT_MAX_BATCH_SESSIONS,
        max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS,
        max_transcript_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS,
        max_summary_calls: int = DEFAULT_MAX_SUMMARY_CALLS,
        max_estimated_input_tokens: int = DEFAULT_MAX_ESTIMATED_INPUT_TOKENS,
    ) -> None:
        if any(
            value <= 0
            for value in (
                max_workers,
                max_batch_sessions,
                max_batch_chars,
                max_transcript_chars,
                max_summary_calls,
                max_estimated_input_tokens,
            )
        ):
            raise ValueError("report pipeline limits must be positive")
        self.session_runner = session_runner
        self.writer_runner = writer_runner
        self.store = store or ReportStore()
        self.ledger = ledger or FollowUpLedger(store=self.store)
        self.transcript_reader = transcript_reader
        self.runner = runner
        self.clock = clock
        self.max_workers = min(max_workers, DEFAULT_MAX_WORKERS)
        self.max_batch_sessions = max_batch_sessions
        self.max_batch_chars = max_batch_chars
        self.max_transcript_chars = max_transcript_chars
        self.max_summary_calls = max_summary_calls
        self.max_estimated_input_tokens = max_estimated_input_tokens

    def preview(
        self,
        sessions: Iterable[Session],
        *,
        cadence: str,
        period_start: str,
        period_end: str,
        advance_cursor: bool = True,
    ) -> ReportPreview:
        """Freeze local report inputs without invoking either configured runner."""

        if cadence not in {"daily", "weekly"}:
            raise ValueError("cadence must be 'daily' or 'weekly'")
        session_list = tuple(sessions)
        exclusions: list[ExclusionRecord] = []
        warnings: list[str] = []
        cached: list[SessionSummary] = []
        project_inputs: list[SummaryInput] = []
        dedicated_inputs: list[SummaryInput] = []
        unreadable = 0

        for session in session_list:
            transcript = self.transcript_reader(session)
            if not transcript.readable:
                unreadable += 1
                warnings.append(
                    f"{session.tool_name}/{session.session_id}: "
                    f"{transcript.error or 'transcript unreadable'}"
                )
                continue
            decision = classify_transcript(transcript)
            original_digest = decision.digest
            original_chars = len(transcript.normalized_text)
            compacted = compact_transcript(transcript, self.max_transcript_chars)
            compacted_chars = len(compacted.normalized_text)
            if compacted is not transcript:
                warnings.append(
                    f"{session.tool_name}/{session.session_id}: compacted transcript "
                    f"from {original_chars} to {compacted_chars} characters"
                )
            project_root = make_summary_input(
                session, compacted, digest=original_digest
            ).project_root
            if decision.classification == NOISE:
                exclusions.append(
                    ExclusionRecord(
                        session_id=session.session_id,
                        digest=original_digest,
                        reason="; ".join(decision.reasons),
                        source_tool=session.tool_name,
                        project_root=project_root,
                    )
                )
                continue
            cached_summary = self.store.get_session_summary(
                session.session_id, original_digest, session.tool_name
            )
            if cached_summary is not None:
                cached.append(cached_summary)
                continue
            item = SummaryInput(session, compacted, original_digest, project_root)
            if decision.classification == DEDICATED:
                dedicated_inputs.append(item)
            else:
                project_inputs.append(item)

        batches = build_summary_batches(
            project_inputs,
            dedicated_inputs,
            max_sessions=self.max_batch_sessions,
            max_chars=self.max_batch_chars,
        )
        estimated_summary_tokens = sum(batch.estimated_input_tokens for batch in batches)
        projected_summaries = [summary.to_dict() for summary in cached]
        projected_summaries.extend(
            _projected_summary(item) for batch in batches for item in batch.inputs
        )
        projected_projects = [
            {
                "project_path": batch.project_root,
                "project_summary": "x" * min(4_000, max(800, batch.normalized_chars // 8)),
                "session_ids": [item.session.session_id for item in batch.inputs],
            }
            for batch in batches
        ]
        previous_report = self._previous_report(cadence)
        open_follow_ups = tuple(self.ledger.list(status="open"))
        (
            writer_inputs,
            writer_previous,
            writer_follow_ups,
            writer_compaction_warnings,
        ) = _bounded_writer_material(
            projected_summaries,
            projected_projects,
            warnings,
            previous_report,
            [item.to_dict() for item in open_follow_ups],
            max_chars=DEFAULT_MAX_WRITER_INPUT_CHARS,
        )
        warnings.extend(
            warning.replace("writer input", "writer preview projects", 1)
            for warning in writer_compaction_warnings
        )
        projected_writer_prompt = build_final_report_prompt(
            writer_inputs,
            period_label=f"{period_start} to {period_end}",
            previous_report=writer_previous,
            open_follow_ups=writer_follow_ups,
        )
        estimated_writer_tokens = _estimated_tokens(len(projected_writer_prompt))
        estimated_tokens = estimated_summary_tokens + estimated_writer_tokens
        over_budget = (
            len(batches) > self.max_summary_calls
            or estimated_tokens > self.max_estimated_input_tokens
        )
        return ReportPreview(
            cadence=cadence,
            period_start=period_start,
            period_end=period_end,
            total=len(session_list),
            excluded=len(exclusions),
            unreadable=unreadable,
            cached=len(cached),
            project_batched=len(project_inputs),
            dedicated=len(dedicated_inputs),
            estimated_summary_calls=len(batches),
            estimated_summary_input_tokens=estimated_summary_tokens,
            estimated_writer_input_tokens=estimated_writer_tokens,
            estimated_input_tokens=estimated_tokens,
            writer_calls=1,
            warnings=tuple(warnings),
            over_budget=over_budget,
            batches=batches,
            cached_summaries=tuple(cached),
            exclusions=tuple(exclusions),
            source_session_ids=tuple(session.session_id for session in session_list),
            previous_report=previous_report,
            open_follow_ups=open_follow_ups,
            writer_prompt_char_limit=len(projected_writer_prompt),
            advance_cursor=advance_cursor,
        )

    def _run_batch(self, batch: SummaryBatch) -> tuple[list[SessionSummary], str]:
        prompt = build_session_summary_prompt(
            [_session_payload(item) for item in batch.inputs], project_path=batch.project_root
        )
        spec = replace(self.session_runner, cwd=batch.project_root)
        result = self.runner(spec, prompt, output_kind="session_summary_batch")
        raw_sessions = result.data.get("sessions", [])
        by_id = {str(item.get("session_id")): item for item in raw_sessions}
        expected = {item.session.session_id for item in batch.inputs}
        if len(raw_sessions) != len(expected) or set(by_id) != expected:
            raise ValueError(
                f"batch {batch.batch_id} returned session IDs {sorted(by_id)}; "
                f"expected {sorted(expected)}"
            )
        summaries = [_summary_from_output(item, by_id[item.session.session_id]) for item in batch.inputs]
        return summaries, str(result.data.get("project_summary", ""))

    def _previous_report(self, cadence: str) -> str:
        manifest = self.store.latest_manifest(cadence)
        if manifest is None or not manifest.markdown_path:
            return ""
        try:
            return Path(manifest.markdown_path).read_text(encoding="utf-8")
        except OSError:
            return ""

    def run(self, plan: ApprovedReportPlan) -> ReportRunResult:
        """Execute exactly the batches and cache hits frozen in ``plan``."""

        require_approval(plan)
        preview = plan.preview
        warnings = list(preview.warnings)
        summaries = list(preview.cached_summaries)
        project_outputs: list[dict[str, Any]] = []
        failed_batches: list[str] = []

        if preview.batches:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self._run_batch, batch): batch for batch in preview.batches}
                for future in as_completed(futures):
                    batch = futures[future]
                    try:
                        generated, project_summary = future.result()
                    except Exception as exc:
                        failed_batches.append(batch.batch_id)
                        warnings.append(f"{batch.batch_id} failed: {type(exc).__name__}: {exc}")
                        continue
                    for summary in generated:
                        self.store.save_session_summary(summary)
                    summaries.extend(generated)
                    project_outputs.append(
                        {
                            "project_path": batch.project_root,
                            "project_summary": project_summary,
                            "session_ids": [item.session_id for item in generated],
                        }
                    )

        if (
            preview.batches
            and len(failed_batches) == len(preview.batches)
            and not summaries
        ):
            failure = next(
                (warning for warning in reversed(warnings) if " failed: " in warning),
                "no session summaries were produced",
            )
            raise ReportWriterError(
                "all summary batches failed; final writer was not started. " + failure
            )

        ordered_summaries = sorted(
            summaries, key=lambda item: (item.project_root, item.ended_at, item.session_id)
        )
        sorted_projects = sorted(
            project_outputs, key=lambda item: (item["project_path"], item["session_ids"])
        )
        writer_inputs, writer_previous, open_follow_ups, compaction_warnings = (
            _bounded_writer_material(
                [summary.to_dict() for summary in ordered_summaries],
                sorted_projects,
                warnings,
                preview.previous_report,
                [item.to_dict() for item in preview.open_follow_ups],
                max_chars=DEFAULT_MAX_WRITER_INPUT_CHARS,
            )
        )
        for warning in compaction_warnings:
            if warning not in warnings:
                warnings.append(warning)
        writer_prompt = build_final_report_prompt(
            writer_inputs,
            period_label=f"{preview.period_start} to {preview.period_end}",
            previous_report=writer_previous,
            open_follow_ups=open_follow_ups,
        )
        if len(writer_prompt) > preview.writer_prompt_char_limit:
            # Generated summaries can be larger than their preview placeholders.
            # Reduce all category budgets together until execution fits the exact
            # writer ceiling the user approved.
            ratio = max(1, preview.writer_prompt_char_limit) / len(writer_prompt)
            reduced_budget = max(1_000, int(DEFAULT_MAX_WRITER_INPUT_CHARS * ratio * 0.95))
            writer_inputs, writer_previous, open_follow_ups, runtime_warnings = (
                _bounded_writer_material(
                    [summary.to_dict() for summary in ordered_summaries],
                    sorted_projects,
                    warnings,
                    preview.previous_report,
                    [item.to_dict() for item in preview.open_follow_ups],
                    max_chars=reduced_budget,
                )
            )
            for warning in runtime_warnings:
                if warning not in warnings:
                    warnings.append(warning)
            writer_prompt = build_final_report_prompt(
                writer_inputs,
                period_label=f"{preview.period_start} to {preview.period_end}",
                previous_report=writer_previous,
                open_follow_ups=open_follow_ups,
            )
            if len(writer_prompt) > preview.writer_prompt_char_limit:
                raise ReportWriterError(
                    "writer input could not be compacted to the approved preview limit"
                )
        try:
            writer_result = self.runner(
                self.writer_runner, writer_prompt, output_kind="final_report"
            )
        except Exception as exc:
            raise ReportWriterError(f"final report writer failed: {exc}") from exc

        report_id = f"{preview.cadence}-{preview.period_start}-{uuid.uuid4().hex[:12]}"
        prepared_suggestions, follow_up_ids = self._prepare_follow_up_suggestions(
            writer_result.data.get("follow_up_suggestions", []),
            warnings,
            valid_source_session_ids=set(preview.source_session_ids),
        )
        if preview.exclusions:
            self.store.record_exclusions(list(preview.exclusions))
        manifest = ReportManifest(
            report_id=report_id,
            cadence=preview.cadence,
            period_start=preview.period_start,
            period_end=preview.period_end,
            generated_at=self.clock(),
            source_session_ids=list(preview.source_session_ids),
            summary_digests={
                f"{summary.source_tool}:{summary.session_id}": summary.digest
                for summary in ordered_summaries
            },
            exclusion_ids=[record.session_id for record in preview.exclusions],
            follow_up_ids=follow_up_ids,
            partial=bool(warnings),
            warnings=warnings,
        )
        report = Report(markdown=str(writer_result.data["markdown"]), manifest=manifest)
        markdown_path, manifest_path = self.store.save_report(report)
        self._apply_follow_up_suggestions(prepared_suggestions, report_id)
        if preview.advance_cursor and not failed_batches and preview.unreadable == 0:
            self.store.advance_cursor(preview.cadence, preview.period_end, report_id)
        return ReportRunResult(
            report=report,
            markdown_path=markdown_path,
            manifest_path=manifest_path,
            summaries=tuple(ordered_summaries),
            warnings=tuple(warnings),
        )

    def _prepare_follow_up_suggestions(
        self,
        suggestions: Any,
        warnings: list[str],
        *,
        valid_source_session_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        prepared: list[dict[str, Any]] = []
        ids: list[str] = []
        for suggestion in suggestions if isinstance(suggestions, list) else []:
            action = suggestion.get("action")
            try:
                if action == "create":
                    text = str(suggestion.get("text", "")).strip()
                    project_path = str(suggestion.get("project_path", ""))
                    if not text:
                        raise ValueError("follow-up text must not be empty")
                    ids.append(stable_follow_up_id(text, project_path))
                    item = dict(suggestion)
                    evidence = suggestion.get("evidence", [])
                    if not isinstance(evidence, list):
                        raise ValueError("follow-up evidence must be a list")
                    item["_source_session_ids"] = list(
                        dict.fromkeys(
                            str(reference)
                            for reference in evidence
                            if str(reference) in valid_source_session_ids
                        )
                    )
                    prepared.append(item)
                elif action in {"suggest_resolved", "update_context"}:
                    follow_up_id = str(suggestion.get("follow_up_id", ""))
                    if self.ledger.get(follow_up_id) is None:
                        warnings.append(f"unknown follow-up suggestion target: {follow_up_id}")
                        continue
                    ids.append(follow_up_id)
                    prepared.append(dict(suggestion))
                else:
                    warnings.append(f"ignored advisory follow-up action: {action}")
            except (KeyError, TypeError, ValueError) as exc:
                warnings.append(f"ignored invalid follow-up suggestion: {exc}")
        return prepared, list(dict.fromkeys(ids))

    def _apply_follow_up_suggestions(
        self, suggestions: list[dict[str, Any]], report_id: str
    ) -> None:
        for suggestion in suggestions:
            action = suggestion["action"]
            if action == "create":
                self.ledger.add(
                    str(suggestion["text"]),
                    str(suggestion.get("project_path", "")),
                    source_session_ids=list(suggestion.get("_source_session_ids", [])),
                    source_report_ids=[report_id],
                    context="; ".join(suggestion.get("evidence", [])),
                )
            elif action == "suggest_resolved":
                evidence = "; ".join(suggestion.get("evidence", []))
                self.ledger.suggest_resolution(
                    str(suggestion["follow_up_id"]),
                    evidence or str(suggestion.get("text", "")),
                )
            elif action == "update_context":
                self.ledger.edit(
                    str(suggestion["follow_up_id"]),
                    context=str(suggestion.get("text", "")),
                )
