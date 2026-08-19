"""Deterministic, auditable transcript triage for report summarization."""

from __future__ import annotations

import re
from dataclasses import dataclass

from quiver.reports.transcripts import NormalizedTranscript


NOISE = "noise"
PROJECT_BATCH = "project_batch"
DEDICATED = "dedicated"


@dataclass(frozen=True)
class SessionActivity:
    human_turns: int
    substantive_human_turns: int
    assistant_turns: int
    tool_events: int
    normalized_chars: int
    has_substantive_response: bool


@dataclass(frozen=True)
class TriageDecision:
    classification: str
    reasons: tuple[str, ...]
    digest: str
    activity: SessionActivity

    @property
    def is_noise(self) -> bool:
        return self.classification == NOISE


_NO_OP_RE = re.compile(
    r"^(?:hi|hello|hey|yo|test|testing|version|--version|-v|/exit|exit|quit|/quit|bye)[!.?\s]*$",
    re.IGNORECASE,
)
_STARTUP_FAILURE_RE = re.compile(
    r"(?:login|log in|re-?login|authenticate|authentication|unauthorized|forbidden|"
    r"rate.?limit|quota|usage limit|weekly limit|monthly limit|dns|name resolution|"
    r"network unavailable|connection (?:failed|refused|timed out)|startup failed)",
    re.IGNORECASE,
)


def _substantive_human(text: str) -> bool:
    compact = " ".join(text.split()).strip()
    return bool(compact) and not _NO_OP_RE.fullmatch(compact)


def inspect_activity(transcript: NormalizedTranscript) -> SessionActivity:
    human = [m for m in transcript.messages if m.role == "human"]
    assistants = [m for m in transcript.messages if m.role == "assistant"]
    tools = [m for m in transcript.messages if m.role == "tool"]
    substantive_humans = [m for m in human if _substantive_human(m.text)]
    response_text = "\n".join(m.text for m in assistants + tools).strip()
    return SessionActivity(
        human_turns=len(human),
        substantive_human_turns=len(substantive_humans),
        assistant_turns=len(assistants),
        tool_events=len(tools),
        normalized_chars=len(transcript.normalized_text),
        has_substantive_response=bool(response_text),
    )


def classify_transcript(
    transcript: NormalizedTranscript,
    *,
    dedicated_chars: int = 60_000,
    dedicated_human_turns: int = 10,
    extensive_tool_events: int = 20,
) -> TriageDecision:
    activity = inspect_activity(transcript)

    if not transcript.readable:
        return TriageDecision(
            PROJECT_BATCH,
            (f"unreadable transcript: {transcript.error or 'unknown error'}",),
            transcript.digest,
            activity,
        )

    if not transcript.messages:
        return TriageDecision(NOISE, ("empty transcript",), transcript.digest, activity)

    if activity.substantive_human_turns == 0:
        response = "\n".join(m.text for m in transcript.messages if m.role != "human")
        reason = "startup-only failure without work" if _STARTUP_FAILURE_RE.search(response) else "no substantive human prompt"
        return TriageDecision(NOISE, (reason,), transcript.digest, activity)

    if not activity.has_substantive_response:
        return TriageDecision(
            NOISE,
            ("substantive prompt without assistant or tool outcome",),
            transcript.digest,
            activity,
        )

    dedicated_reasons: list[str] = []
    if activity.normalized_chars > dedicated_chars:
        dedicated_reasons.append(f"normalized transcript exceeds {dedicated_chars} characters")
    if activity.substantive_human_turns >= dedicated_human_turns:
        dedicated_reasons.append(f"at least {dedicated_human_turns} substantive human turns")
    if activity.tool_events >= extensive_tool_events:
        dedicated_reasons.append(f"at least {extensive_tool_events} tool events")
    if dedicated_reasons:
        return TriageDecision(DEDICATED, tuple(dedicated_reasons), transcript.digest, activity)

    return TriageDecision(
        PROJECT_BATCH,
        ("meaningful work suitable for project batching",),
        transcript.digest,
        activity,
    )

