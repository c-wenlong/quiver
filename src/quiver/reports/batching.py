"""Deterministic, cost-aware batching for coding-session reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from quiver.reports.transcripts import NormalizedMessage, NormalizedTranscript
from quiver.sessions.models import Session
from quiver.sessions.query import resolve_project_root


DEFAULT_MAX_BATCH_SESSIONS = 25
DEFAULT_MAX_BATCH_CHARS = 60_000
DEFAULT_PROMPT_OVERHEAD_TOKENS = 1_500
DEFAULT_MAX_TRANSCRIPT_CHARS = 240_000


@dataclass(frozen=True)
class SummaryInput:
    """One immutable session input captured during report preview."""

    session: Session
    transcript: NormalizedTranscript
    digest: str
    project_root: str

    @property
    def normalized_chars(self) -> int:
        return len(self.transcript.normalized_text)


@dataclass(frozen=True)
class SummaryBatch:
    """One exact worker invocation in an approved report plan."""

    batch_id: str
    project_root: str
    inputs: tuple[SummaryInput, ...]
    dedicated: bool = False

    @property
    def normalized_chars(self) -> int:
        return sum(item.normalized_chars for item in self.inputs)

    @property
    def estimated_input_tokens(self) -> int:
        # Four characters per token is optimistic for code. Three plus fixed
        # schema/prompt overhead intentionally errs toward over-estimation.
        return (self.normalized_chars + 2) // 3 + DEFAULT_PROMPT_OVERHEAD_TOKENS


def make_summary_input(
    session: Session,
    transcript: NormalizedTranscript,
    *,
    digest: str | None = None,
) -> SummaryInput:
    return SummaryInput(
        session=session,
        transcript=transcript,
        digest=digest or transcript.digest,
        project_root=resolve_project_root(session.path),
    )


def _message_cost(message: NormalizedMessage) -> int:
    return len(message.role) + len(message.text) + 4


def _trim_text(text: str, budget: int, *, keep_end: bool = False) -> str:
    marker = "\n[...compacted...]\n"
    if len(text) <= budget:
        return text
    if budget <= len(marker):
        return text[-budget:] if keep_end else text[:budget]
    remaining = budget - len(marker)
    if keep_end:
        return marker + text[-remaining:]
    return text[:remaining] + marker


def _trim_both(text: str, budget: int) -> str:
    marker = "\n[...compacted...]\n"
    if len(text) <= budget:
        return text
    if budget <= len(marker):
        return text[:budget]
    remaining = budget - len(marker)
    beginning = (remaining + 1) // 2
    ending = remaining - beginning
    return text[:beginning] + marker + (text[-ending:] if ending else "")


def compact_transcript(
    transcript: NormalizedTranscript,
    max_chars: int = DEFAULT_MAX_TRANSCRIPT_CHARS,
) -> NormalizedTranscript:
    """Compact a transcript while retaining both context edges and tool evidence."""

    if max_chars <= 0:
        raise ValueError("max_transcript_chars must be positive")
    if len(transcript.normalized_text) <= max_chars:
        return transcript

    context = [
        (index, message)
        for index, message in enumerate(transcript.messages)
        if message.role in {"human", "assistant"}
    ]
    tools = [
        (index, message)
        for index, message in enumerate(transcript.messages)
        if message.role == "tool"
    ]
    # Context receives most of the budget. Tool outcomes keep a fixed share so
    # a long conversation cannot crowd out the evidence of what actually ran.
    tool_budget = max_chars // 5 if tools else 0
    context_budget = max_chars - tool_budget
    selected: dict[int, NormalizedMessage] = {}

    def add(index: int, message: NormalizedMessage, budget: int, keep_end: bool) -> int:
        overhead = len(message.role) + 4
        available = budget - overhead
        if available <= 0:
            return budget
        text = _trim_text(message.text, available, keep_end=keep_end)
        selected[index] = NormalizedMessage(
            message.role, text, kind=message.kind, timestamp=message.timestamp
        )
        return budget - _message_cost(selected[index])

    if len(context) == 1:
        index, message = context[0]
        overhead = len(message.role) + 4
        available = max(0, context_budget - overhead)
        selected[index] = NormalizedMessage(
            message.role,
            _trim_both(message.text, available),
            kind=message.kind,
            timestamp=message.timestamp,
        )
    elif context:
        beginning_budget = context_budget // 2
        ending_budget = context_budget - beginning_budget
        for index, message in context:
            if beginning_budget <= 0:
                break
            cost = _message_cost(message)
            if cost <= beginning_budget:
                selected[index] = message
                beginning_budget -= cost
            else:
                beginning_budget = add(index, message, beginning_budget, keep_end=False)
        for index, message in reversed(context):
            if ending_budget <= 0:
                break
            if index in selected:
                continue
            cost = _message_cost(message)
            if cost <= ending_budget:
                selected[index] = message
                ending_budget -= cost
            else:
                ending_budget = add(index, message, ending_budget, keep_end=True)

    for index, message in tools:
        if tool_budget <= 0:
            break
        cost = _message_cost(message)
        if cost <= tool_budget:
            selected[index] = message
            tool_budget -= cost
        else:
            tool_budget = add(index, message, tool_budget, keep_end=False)

    compacted = NormalizedTranscript(
        tool_name=transcript.tool_name,
        session_id=transcript.session_id,
        project_path=transcript.project_path,
        messages=[selected[index] for index in sorted(selected)],
        source_paths=list(transcript.source_paths),
        readable=transcript.readable,
        error=transcript.error,
    )
    # Separator accounting can exceed the approximate per-message budget by a
    # few characters. Deterministically trim the final selected message.
    overflow = len(compacted.normalized_text) - max_chars
    if overflow > 0 and compacted.messages:
        last = compacted.messages[-1]
        shortened = last.text[: max(0, len(last.text) - overflow)]
        compacted.messages[-1] = NormalizedMessage(
            last.role, shortened, kind=last.kind, timestamp=last.timestamp
        )
    return compacted


def build_summary_batches(
    project_inputs: Iterable[SummaryInput],
    dedicated_inputs: Iterable[SummaryInput] = (),
    *,
    max_sessions: int = DEFAULT_MAX_BATCH_SESSIONS,
    max_chars: int = DEFAULT_MAX_BATCH_CHARS,
) -> tuple[SummaryBatch, ...]:
    """Pack project inputs chronologically without crossing project roots."""

    if max_sessions <= 0 or max_chars <= 0:
        raise ValueError("batch limits must be positive")

    grouped: dict[str, list[SummaryInput]] = {}
    for item in project_inputs:
        grouped.setdefault(item.project_root, []).append(item)

    batches: list[SummaryBatch] = []
    for project_root in sorted(grouped):
        ordered = sorted(
            grouped[project_root],
            key=lambda item: (item.session.timestamp, item.session.session_id),
        )
        current: list[SummaryInput] = []
        current_chars = 0
        project_index = 0
        for item in ordered:
            would_overflow = current and (
                len(current) >= max_sessions
                or current_chars + item.normalized_chars > max_chars
            )
            if would_overflow:
                project_index += 1
                batches.append(
                    SummaryBatch(
                        batch_id=f"project:{project_root}:{project_index}",
                        project_root=project_root,
                        inputs=tuple(current),
                    )
                )
                current = []
                current_chars = 0
            current.append(item)
            current_chars += item.normalized_chars
        if current:
            project_index += 1
            batches.append(
                SummaryBatch(
                    batch_id=f"project:{project_root}:{project_index}",
                    project_root=project_root,
                    inputs=tuple(current),
                )
            )

    for index, item in enumerate(
        sorted(
            dedicated_inputs,
            key=lambda value: (
                value.project_root,
                value.session.timestamp,
                value.session.session_id,
            ),
        ),
        start=1,
    ):
        batches.append(
            SummaryBatch(
                batch_id=f"dedicated:{item.session.tool_name}:{item.session.session_id}:{index}",
                project_root=item.project_root,
                inputs=(item,),
                dedicated=True,
            )
        )
    return tuple(batches)
