"""Non-interactive harness runners used by coding-session reports."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


REPORT_ENV_MARKER = "QUIVER_REPORT_GENERATED"
REPORT_ENV_RUN_ID = "QUIVER_REPORT_RUN_ID"
REPORT_ENV_OUTPUT_KIND = "QUIVER_REPORT_OUTPUT_KIND"

_SUPPORTED_HARNESSES = {"claude", "codex"}
_TRANSIENT_RE = re.compile(
    r"(?i)(rate.?limit|too many requests|\b429\b|temporar(?:y|ily)|timed? out|"
    r"timeout|connection (?:reset|refused)|network|service unavailable|"
    r"bad gateway|gateway timeout|\b50[234]\b)"
)
_AUTH_FAILURE_RE = re.compile(
    r"(?i)(?:failed to authenticate|authentication_error|oauth|access token.*revoked|"
    r"\b(?:401|unauthorized)\b)"
)
_SECRET_RE = re.compile(
    r"(?i)(?:sk-ant-|sk-proj-|sk-|ghp_|github_pat_|xox[baprs]-|bearer\s+)"
    r"[A-Za-z0-9._~+/=-]{8,}"
)
_SENSITIVE_FLAGS = {
    "--api-key",
    "--apikey",
    "--auth-token",
    "--password",
    "--token",
}
_UNSAFE_FLAGS = {
    "--dangerously-skip-permissions",
    "--full-auto",
    "--permission-mode",
    "--sandbox",
    "--yolo",
}
_MANAGED_FLAGS = {
    "claude": {
        "--model",
        "--no-session-persistence",
        "--output-format",
        "--permission-mode",
        "--print",
    },
    "codex": {
        "--ephemeral",
        "--json",
        "--model",
        "--sandbox",
    },
}
_SESSION_STATUSES = {"completed", "partial", "blocked", "unclear"}
_FOLLOW_UP_ACTIONS = {"create", "suggest_resolved", "update_context"}


@dataclass(frozen=True)
class RunnerSpec:
    """Configuration for one report-model invocation."""

    harness: str
    model: str
    args: tuple[str, ...] = ()
    timeout_seconds: float = 120.0
    cwd: str | Path | None = None

    def __post_init__(self) -> None:
        normalized = self.harness.strip().lower()
        object.__setattr__(self, "harness", normalized)
        object.__setattr__(self, "args", tuple(self.args))
        if not self.model.strip():
            raise ValueError("Runner model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("Runner timeout must be greater than zero")


@dataclass(frozen=True)
class RunnerResult:
    """Validated structured output from a report-model invocation."""

    harness: str
    data: Mapping[str, Any]
    attempts: int
    returncode: int = 0
    stderr: str = ""


class RunnerError(RuntimeError):
    """Base class for report runner failures."""


class UnsupportedHarnessError(RunnerError):
    """Raised when a report runner has not been implemented for a harness."""


class RunnerTimeoutError(RunnerError):
    """Raised after the bounded timeout retry is exhausted."""


class RunnerOutputError(RunnerError):
    """Raised when a harness does not return valid structured output."""


def _validate_extra_args(harness: str, args: Sequence[str]) -> None:
    value_allowed = False
    for arg in args:
        if arg in {"-", "--"}:
            raise ValueError(
                "Report runner arguments cannot contain positional or option terminators"
            )

        if not arg.startswith("-"):
            if not value_allowed:
                raise ValueError(
                    f"Report runner positional argument is not allowed: {arg}"
                )
            value_allowed = False
            continue

        flag = arg.split("=", 1)[0].lower()
        if flag in _UNSAFE_FLAGS or flag in _MANAGED_FLAGS.get(harness, set()):
            raise ValueError(f"Report runner argument is managed by Quiver: {flag}")
        value_allowed = "=" not in arg


def build_argv(spec: RunnerSpec) -> list[str]:
    """Build a safety-constrained command line for a supported harness."""
    _validate_extra_args(spec.harness, spec.args)
    if spec.harness == "claude":
        return [
            "claude",
            "--print",
            "--output-format",
            "json",
            "--model",
            spec.model,
            "--no-session-persistence",
            "--permission-mode",
            "plan",
            *spec.args,
        ]
    if spec.harness == "codex":
        return [
            "codex",
            "exec",
            "--model",
            spec.model,
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--json",
            *spec.args,
            "-",
        ]
    raise UnsupportedHarnessError(
        f"Unsupported report harness: {spec.harness or '<empty>'}"
    )


def _runner_env(output_kind: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            REPORT_ENV_MARKER: "1",
            REPORT_ENV_RUN_ID: uuid.uuid4().hex,
            REPORT_ENV_OUTPUT_KIND: output_kind,
            "NO_COLOR": "1",
        }
    )
    return env


def _sensitive_values(args: Sequence[str]) -> list[str]:
    values: list[str] = []
    for index, arg in enumerate(args):
        flag, separator, inline_value = arg.partition("=")
        if flag.lower() not in _SENSITIVE_FLAGS:
            continue
        if separator and inline_value:
            values.append(inline_value)
        elif index + 1 < len(args):
            values.append(args[index + 1])
    return values


def _redact(text: str, args: Sequence[str] = ()) -> str:
    redacted = text
    for value in _sensitive_values(args):
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return _SECRET_RE.sub("[REDACTED]", redacted)


def _stdout_error_detail(stdout: str, args: Sequence[str] = ()) -> str:
    """Extract a bounded diagnostic from harness JSON written to stdout."""

    candidates = [stdout, *reversed(stdout.splitlines())]
    for candidate in candidates:
        try:
            record = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        is_error = bool(record.get("is_error")) or record.get("type") == "error"
        status = record.get("api_error_status")
        if not is_error and status is None:
            continue

        message: Any = record.get("result") or record.get("message")
        error = record.get("error")
        if not message and isinstance(error, dict):
            message = error.get("message") or error.get("type")
        elif not message and isinstance(error, str):
            message = error
        detail = str(message or "structured harness error").strip()
        if status is not None:
            detail = f"HTTP {status}: {detail}"
        return _redact(detail[:2_000], args)
    return ""


def _failure_detail(spec: RunnerSpec, stdout: str, stderr: str) -> str:
    detail = _redact(stderr or "", spec.args).strip()
    if not detail:
        detail = _stdout_error_detail(stdout or "", spec.args)
    if not detail:
        return "no diagnostic output"
    if (
        spec.harness == "claude"
        and _AUTH_FAILURE_RE.search(detail)
        and "claude auth login" not in detail
    ):
        detail += " Run `claude auth login` to refresh Claude Code authentication."
    return detail


def _decode_json(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, dict):
        structured = value.get("structured_output")
        if isinstance(structured, dict):
            return structured

        # Claude JSON responses place the model's text in ``result``. Codex
        # JSONL responses place it in a completed agent-message item's text.
        result = value.get("result")
        if isinstance(result, (dict, str)):
            decoded = _decode_json(result)
            if decoded is not None:
                return decoded
        item = value.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            decoded = _decode_json(item.get("text"))
            if decoded is not None:
                return decoded
        if "kind" in value:
            return value
        return None

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return _decode_json(json.loads(candidate))
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_output(stdout: str) -> Mapping[str, Any]:
    decoded = _decode_json(stdout)
    if decoded is not None:
        return decoded

    # Codex --json emits JSONL. Walk backwards so the final agent message wins.
    for line in reversed(stdout.splitlines()):
        decoded = _decode_json(line)
        if decoded is not None:
            return decoded
    raise RunnerOutputError("Harness returned malformed or missing JSON output")


def _require_string(item: Mapping[str, Any], key: str) -> None:
    if not isinstance(item.get(key), str):
        raise RunnerOutputError(f"Structured output field '{key}' must be a string")


def validate_output(data: Mapping[str, Any], output_kind: str) -> None:
    """Validate the stable report output contracts without third-party packages."""
    if data.get("kind") != output_kind:
        raise RunnerOutputError(
            f"Expected structured output kind '{output_kind}', got "
            f"'{data.get('kind', '<missing>')}'"
        )

    if output_kind == "session_summary_batch":
        _require_string(data, "project_path")
        _require_string(data, "project_summary")
        sessions = data.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise RunnerOutputError("Structured output field 'sessions' must be non-empty")
        for session in sessions:
            if not isinstance(session, dict):
                raise RunnerOutputError("Each session summary must be an object")
            for key in ("session_id", "objective", "outcome", "status", "context"):
                _require_string(session, key)
            if session["status"] not in _SESSION_STATUSES:
                raise RunnerOutputError(
                    "Session summary field 'status' must be completed, partial, "
                    "blocked, or unclear"
                )
            for key in ("changes", "decisions", "blockers", "follow_ups"):
                if not isinstance(session.get(key), list) or not all(
                    isinstance(value, str) for value in session[key]
                ):
                    raise RunnerOutputError(
                        f"Session summary field '{key}' must be a list of strings"
                    )
        return

    if output_kind == "final_report":
        _require_string(data, "markdown")
        if not data["markdown"].strip():
            raise RunnerOutputError("Final report markdown must not be empty")
        suggestions = data.get("follow_up_suggestions")
        if not isinstance(suggestions, list) or not all(
            isinstance(value, dict) for value in suggestions
        ):
            raise RunnerOutputError(
                "Final report field 'follow_up_suggestions' must be a list of objects"
            )
        for suggestion in suggestions:
            for key in ("action", "follow_up_id", "text", "project_path"):
                _require_string(suggestion, key)
            if suggestion["action"] not in _FOLLOW_UP_ACTIONS:
                raise RunnerOutputError(
                    "Follow-up action must be create, suggest_resolved, or update_context"
                )
            if "status" in suggestion:
                raise RunnerOutputError(
                    "Follow-up suggestions cannot change user-owned status"
                )
            evidence = suggestion.get("evidence")
            if not isinstance(evidence, list) or not all(
                isinstance(value, str) for value in evidence
            ):
                raise RunnerOutputError(
                    "Follow-up suggestion field 'evidence' must be a list of strings"
                )
        return

    raise RunnerOutputError(f"Unknown structured output kind: {output_kind}")


def _is_transient(returncode: int, stderr: str) -> bool:
    return returncode < 0 or bool(_TRANSIENT_RE.search(stderr))


def run_structured(
    spec: RunnerSpec,
    prompt: str,
    *,
    output_kind: str,
    sleep: Any = time.sleep,
) -> RunnerResult:
    """Run a report prompt with exactly one retry for transient failures."""
    argv = build_argv(spec)
    env = _runner_env(output_kind)
    safe_stderr = ""

    for attempt in (1, 2):
        try:
            completed = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                cwd=str(spec.cwd) if spec.cwd is not None else None,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if attempt == 1:
                sleep(0.1)
                continue
            raise RunnerTimeoutError(
                f"{spec.harness} report runner timed out after 2 attempts"
            ) from exc
        except FileNotFoundError as exc:
            raise RunnerError(f"Report harness executable not found: {spec.harness}") from exc
        except OSError as exc:
            raise RunnerError(
                f"Unable to start {spec.harness} report runner: {_redact(str(exc), spec.args)}"
            ) from exc

        safe_stderr = _redact(completed.stderr or "", spec.args).strip()
        if completed.returncode != 0:
            detail = _failure_detail(
                spec, completed.stdout or "", completed.stderr or ""
            )
            if attempt == 1 and _is_transient(completed.returncode, detail):
                sleep(0.1)
                continue
            raise RunnerError(
                f"{spec.harness} report runner failed with exit code "
                f"{completed.returncode}: {detail}"
            )

        data = _extract_output(completed.stdout or "")
        validate_output(data, output_kind)
        return RunnerResult(
            harness=spec.harness,
            data=data,
            attempts=attempt,
            returncode=completed.returncode,
            stderr=safe_stderr,
        )

    raise AssertionError("bounded report runner loop exited unexpectedly")
