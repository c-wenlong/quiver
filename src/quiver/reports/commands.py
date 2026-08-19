"""CLI commands for coding-session reports and follow-ups."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from quiver.config_commands import cmd_config
from quiver.configuration import (
    get_value,
    load_resolved_config,
    report_setup_complete,
    validate_config,
)
from quiver.console import c
from quiver.reports.followups import FollowUpLedger
from quiver.reports.pipeline import (
    ApprovedReportPlan,
    ReportApprovalError,
    ReportPipeline,
    ReportWriterError,
)
from quiver.reports.runners import RunnerSpec
from quiver.reports.store import MalformedReportStateError, ReportStore
from quiver.reports.work import FollowUpWorkError, work_on_follow_up
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.query import SessionQuery, calendar_range_ms


@dataclass
class _GenerateArgs:
    days: int | None = None
    weeks: int | None = None
    start: str | None = None
    end: str | None = None
    here: bool = False
    agent: str | None = None
    search: str | None = None
    session_harness: str | None = None
    session_model: str | None = None
    session_args: list[str] = field(default_factory=list)
    writer_harness: str | None = None
    writer_model: str | None = None
    writer_args: list[str] = field(default_factory=list)


def _value(args: list[str], index: int, flag: str) -> str:
    if index + 1 >= len(args):
        raise ValueError(f"{flag} requires a value")
    return args[index + 1]


def _parse_generate_args(args: list[str]) -> _GenerateArgs:
    parsed = _GenerateArgs()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-d", "--days"):
            parsed.days = int(_value(args, i, arg))
            i += 2
        elif arg in ("-w", "--weeks"):
            parsed.weeks = int(_value(args, i, arg))
            i += 2
        elif arg in ("-s", "--start"):
            parsed.start = _value(args, i, arg)
            i += 2
        elif arg in ("-e", "--end"):
            parsed.end = _value(args, i, arg)
            i += 2
        elif arg == "--here":
            parsed.here = True
            i += 1
        elif arg == "--agent":
            parsed.agent = _value(args, i, arg)
            i += 2
        elif arg in ("--search", "-q"):
            parsed.search = _value(args, i, arg)
            i += 2
        elif arg == "--session-harness":
            parsed.session_harness = _value(args, i, arg)
            i += 2
        elif arg == "--session-model":
            parsed.session_model = _value(args, i, arg)
            i += 2
        elif arg == "--session-arg":
            parsed.session_args.append(_value(args, i, arg))
            i += 2
        elif arg == "--writer-harness":
            parsed.writer_harness = _value(args, i, arg)
            i += 2
        elif arg == "--writer-model":
            parsed.writer_model = _value(args, i, arg)
            i += 2
        elif arg == "--writer-arg":
            parsed.writer_args.append(_value(args, i, arg))
            i += 2
        else:
            raise ValueError(f"Unknown report argument: {arg}")
    if any(value is not None for value in (parsed.days, parsed.weeks, parsed.start, parsed.end)):
        calendar_range_ms(
            days=parsed.days,
            weeks=parsed.weeks,
            start=parsed.start,
            end=parsed.end,
        )
    return parsed


def _default_period(cadence: str, store: ReportStore) -> tuple[int, int, str, str]:
    now = datetime.now().astimezone()
    if cadence == "daily":
        completed_start = datetime.combine(
            now.date() - timedelta(days=1), time.min, tzinfo=now.tzinfo
        )
        end_dt = datetime.combine(
            now.date(), time.min, tzinfo=now.tzinfo
        ) - timedelta(milliseconds=1)
    else:
        current_monday = now.date() - timedelta(days=now.weekday())
        completed_start = datetime.combine(
            current_monday - timedelta(days=7), time.min, tzinfo=now.tzinfo
        )
        end_dt = datetime.combine(
            current_monday, time.min, tzinfo=now.tzinfo
        ) - timedelta(milliseconds=1)

    cursor = store.get_cursor(cadence)
    if cursor is not None:
        try:
            previous = datetime.fromisoformat(cursor.through)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=now.tzinfo)
            start_dt = previous + timedelta(milliseconds=1)
        except ValueError:
            start_dt = completed_start
    else:
        start_dt = completed_start
    return (
        int(start_dt.timestamp() * 1000),
        int(end_dt.timestamp() * 1000),
        start_dt.isoformat(),
        end_dt.isoformat(),
    )


def _source_period(cadence: str, parsed: _GenerateArgs, store: ReportStore):
    custom = any(
        value is not None for value in (parsed.days, parsed.weeks, parsed.start, parsed.end)
    )
    if not custom:
        start_ms, end_ms, start_label, end_label = _default_period(cadence, store)
        return start_ms, end_ms, start_label, end_label, True
    start_ms, end_ms = calendar_range_ms(
        days=parsed.days,
        weeks=parsed.weeks,
        start=parsed.start,
        end=parsed.end,
    )
    start_label = datetime.fromtimestamp(start_ms / 1000).astimezone().isoformat()
    end_label = datetime.fromtimestamp(end_ms / 1000).astimezone().isoformat()
    return start_ms, end_ms, start_label, end_label, False


def _runner_spec(config: dict, role: str, parsed: _GenerateArgs) -> RunnerSpec:
    harness_override = getattr(parsed, f"{role}_harness")
    model_override = getattr(parsed, f"{role}_model")
    args_override = getattr(parsed, f"{role}_args")
    harness = harness_override or get_value(config, f"report.{role}.harness")
    model = model_override or get_value(config, f"report.{role}.model")
    extra = args_override or list(get_value(config, f"report.{role}.args", []))
    timeout_key = "writer_timeout_seconds" if role == "writer" else "runner_timeout_seconds"
    return RunnerSpec(
        str(harness or ""),
        str(model or ""),
        tuple(extra),
        float(get_value(config, f"report.{timeout_key}")),
    )


def _pipeline(config: dict, store: ReportStore, parsed: _GenerateArgs) -> ReportPipeline:
    return ReportPipeline(
        _runner_spec(config, "session", parsed),
        _runner_spec(config, "writer", parsed),
        store=store,
        max_workers=int(get_value(config, "report.max_workers")),
        max_summary_calls=int(get_value(config, "report.max_summary_calls")),
        max_estimated_input_tokens=int(get_value(config, "report.max_estimated_input_tokens")),
        max_batch_sessions=int(get_value(config, "report.batch.max_sessions")),
        max_batch_chars=int(get_value(config, "report.batch.max_chars")),
        max_transcript_chars=int(get_value(config, "report.transcript.max_chars")),
    )


def _print_preview(preview) -> None:
    print()
    print(c("bold", f"  {preview.total} sessions found from {preview.period_start} to {preview.period_end}."))
    print(c("dim", f"  {preview.excluded} startup/noise excluded"))
    print(c("dim", f"  {preview.cached} cached summaries reused"))
    print(c("dim", f"  {preview.unreadable} unreadable sessions reported as warnings"))
    print(c("dim", f"  {preview.project_batched} sessions in {preview.project_batch_calls} project batch(es)"))
    print(c("dim", f"  {preview.dedicated} dedicated session summary call(s)"))
    print(c(
        "dim",
        f"  Estimated: {preview.estimated_summary_calls} summary call(s), "
        f"~{preview.estimated_summary_input_tokens:,} summary input tokens, "
        f"~{preview.estimated_writer_input_tokens:,} writer input tokens, "
        f"~{preview.estimated_input_tokens:,} total, 1 writer call",
    ))
    print()


def _warnings_command(manifest_path: Path | str) -> str:
    return shlex.join(["swe", "report", "warnings", str(manifest_path)])


def _print_report_warnings(path: str) -> int:
    manifest_path = Path(path).expanduser()
    if manifest_path.suffix == ".md":
        manifest_path = manifest_path.with_suffix(".json")
    if manifest_path.suffix != ".json":
        print(c("red", "  Report warnings require a .json manifest or its .md report path."))
        return 1
    try:
        manifest = ReportStore().load_manifest(manifest_path)
    except (MalformedReportStateError, OSError) as exc:
        print(c("red", f"  Could not read report warnings: {exc}"))
        return 1

    print()
    print(c("bold", f"  Warnings for {manifest.report_id}"))
    if not manifest.warnings:
        print(c("green", "  No warnings were recorded for this report."))
    else:
        for index, warning in enumerate(manifest.warnings, start=1):
            print(f"  {index}. {warning}")
    print(c("dim", f"\n  Manifest: {manifest_path}\n"))
    return 0


def _generate(cadence: str, args: list[str], input_fn: Callable[[str], str] = input) -> int:
    try:
        parsed = _parse_generate_args(args)
    except (ValueError, TypeError) as exc:
        print(c("red", f"  {exc}"))
        return 1

    config = load_resolved_config()
    config_issues = validate_config(config)
    if config_issues:
        print(c("red", "  Report configuration is invalid:"))
        for issue in config_issues:
            print(c("red", f"    {issue}"))
        print(c("dim", "  Run `swe config check` or update the listed values."))
        return 1
    overrides_complete = all(
        (
            parsed.session_harness or get_value(config, "report.session.harness"),
            parsed.session_model or get_value(config, "report.session.model"),
            parsed.writer_harness or get_value(config, "report.writer.harness"),
            parsed.writer_model or get_value(config, "report.writer.model"),
        )
    )
    if not report_setup_complete(config) and not overrides_complete:
        print(c("yellow", "  Report runners are not configured; starting setup."))
        if cmd_config(["setup", "report"]):
            return 1
        config = load_resolved_config()

    store = ReportStore()
    try:
        start_ms, end_ms, start_label, end_label, advance = _source_period(
            cadence, parsed, store
        )
        query = SessionQuery(
            start_ms=start_ms,
            end_ms=end_ms,
            agent=parsed.agent,
            cwd=os.getcwd() if parsed.here else None,
            search=parsed.search,
        )
        sessions = query.apply(get_all_sessions(limit=None))
        if not sessions:
            print(c("dim", f"  No sessions found from {start_label} to {end_label}."))
            return 0
        pipeline = _pipeline(config, store, parsed)
        preview = pipeline.preview(
            sessions,
            cadence=cadence,
            period_start=start_label,
            period_end=end_label,
            advance_cursor=advance,
        )
        _print_preview(preview)
        prompt = (
            '  This exceeds the configured approval limits. Type "process all" to continue: '
            if preview.over_budget
            else "  Continue? [y/N]: "
        )
        approval = input_fn(prompt)
        result = pipeline.run(ApprovedReportPlan(preview, approval))
    except ReportApprovalError:
        print(c("dim", "  Report cancelled; no model calls were started."))
        return 0
    except (MalformedReportStateError, ReportWriterError, OSError, TypeError, ValueError) as exc:
        print(c("red", f"  Report failed: {exc}"))
        return 1
    print(result.report.markdown.rstrip())
    print()
    print(c("dim", f"  Saved: {result.markdown_path}"))
    if result.warnings:
        print(c("yellow", f"  Completed with {len(result.warnings)} warning(s)."))
        print(c("dim", f"  Run `{_warnings_command(result.manifest_path)}` to see them."))
    return 0


def _print_followups(ledger: FollowUpLedger, status: str | None = None) -> int:
    items = ledger.list(status=status)
    if not items:
        print(c("dim", "  No follow-ups found."))
        return 0
    for item in items:
        marker = "[ ]" if item.status == "open" else "[x]"
        suggestion = " (possibly resolved)" if item.resolution_suggested else ""
        print(f"  {marker} {c('bold', item.id)}  {item.text}{suggestion}")
        print(c("dim", f"      {item.project_root}  ·  {item.status}"))
    print()
    return 0


def _followup(args: list[str]) -> int:
    ledger = FollowUpLedger()
    if not args:
        return _print_followups(ledger, "open")
    action = args[0]
    try:
        if action == "add":
            if len(args) < 2:
                raise ValueError("Usage: swe report followup add <text> [--project PATH]")
            project = os.getcwd()
            words = args[1:]
            if "--project" in words:
                index = words.index("--project")
                if index + 1 >= len(words):
                    raise ValueError("--project requires a path")
                project = words[index + 1]
                del words[index : index + 2]
            item = ledger.add(" ".join(words), project)
        elif action in {"done", "dismiss", "reopen"}:
            if len(args) != 2:
                raise ValueError(f"Usage: swe report followup {action} <id>")
            item = getattr(ledger, action)(args[1])
        elif action == "edit":
            if len(args) < 3:
                raise ValueError("Usage: swe report followup edit <id> <text>")
            item = ledger.edit(args[1], text=" ".join(args[2:]))
        elif action == "work":
            if len(args) < 2:
                raise ValueError("Usage: swe report followup work <id> [--resume|--new --harness NAME]")
            item = ledger.get(args[1])
            if item is None:
                raise ValueError(f"Unknown follow-up: {args[1]}")
            mode = "resume" if "--resume" in args[2:] else "new" if "--new" in args[2:] else None
            if "--resume" in args[2:] and "--new" in args[2:]:
                raise ValueError("Choose only one of --resume or --new")
            harness = None
            if "--harness" in args[2:]:
                index = args.index("--harness")
                harness = _value(args, index, "--harness")
            if mode is None:
                choice = input("Resume source session or start new? [r/n]: ").strip().lower()
                if choice in {"r", "resume"}:
                    mode = "resume"
                elif choice in {"n", "new"}:
                    mode = "new"
                else:
                    raise ValueError("Choose 'resume' or 'new'")
            if mode == "new" and not harness:
                harness = input("Harness for new session: ").strip()
            return int(work_on_follow_up(item, mode=mode, harness=harness) or 0)
        else:
            raise ValueError(f"Unknown follow-up action: {action}")
    except (KeyError, ValueError, FollowUpWorkError, MalformedReportStateError) as exc:
        print(c("red", f"  {exc}"))
        return 1
    print(c("green", f"  {item.id}: {item.status}"))
    return 0


def cmd_report(args: list[str]) -> int:
    if not args:
        print("Usage: swe report daily|weekly|warnings|followups|followup ...")
        return 1
    command = args[0]
    if command in {"daily", "weekly"}:
        return _generate(command, args[1:])
    if command == "warnings":
        if len(args) != 2:
            print(c("red", "  Usage: swe report warnings <report-manifest.json>"))
            return 1
        return _print_report_warnings(args[1])
    if command == "followups":
        status = None
        if args[1:]:
            if len(args) != 3 or args[1] != "--status":
                print(c("red", "  Usage: swe report followups [--status open|done|dismissed]"))
                return 1
            status = args[2]
        try:
            return _print_followups(FollowUpLedger(), status)
        except (ValueError, MalformedReportStateError) as exc:
            print(c("red", f"  {exc}"))
            return 1
    if command == "followup":
        return _followup(args[1:])
    print(c("red", f"  Unknown report command: {command}"))
    return 1
