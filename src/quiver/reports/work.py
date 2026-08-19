"""Prepare and launch user-controlled work for a report follow-up."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from quiver.harness.registry import load_registry, resolve
from quiver.reports.models import FollowUp
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.commands import _RESUME_FLAGS, _resume_cmd_args
from quiver.sessions.models import Session


class FollowUpWorkError(ValueError):
    """Raised when a follow-up cannot be prepared safely for launch."""


@dataclass(frozen=True)
class FollowUpWorkPlan:
    """A validated launch plan that does not mutate the follow-up ledger."""

    follow_up_id: str
    action: str
    cwd: str
    launch_args: tuple[str, ...]
    prompt: str = ""
    source_session_id: str = ""


InputFunction = Callable[[str], str]
LaunchFunction = Callable[[Sequence[str], str], object]
SessionLoader = Callable[[], list[Session]]
RegistryLoader = Callable[[], dict]


def choose_work_action(mode: str | None, input_fn: InputFunction = input) -> str:
    """Resolve an explicit mode or prompt for resume/new selection."""
    if mode is not None:
        normalized = mode.strip().lower()
        if normalized not in {"resume", "new"}:
            raise FollowUpWorkError("work mode must be 'resume' or 'new'")
        return normalized

    answer = input_fn("Resume source session or start new? [r/n]: ").strip().lower()
    if answer in {"r", "resume"}:
        return "resume"
    if answer in {"n", "new"}:
        return "new"
    raise FollowUpWorkError("choose 'resume' or 'new'")


def validate_project_root(project_root: str) -> Path:
    """Return a real, existing directory suitable for a child process cwd."""
    if not project_root or not project_root.strip():
        raise FollowUpWorkError("follow-up has no project path")
    path = Path(project_root).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FollowUpWorkError(f"project path is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise FollowUpWorkError(f"project path is not a directory: {resolved}")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def find_source_session(
    follow_up: FollowUp,
    sessions: Iterable[Session],
    project_root: Path,
) -> Session:
    """Find the newest referenced session that belongs to the project."""
    wanted = set(follow_up.source_session_ids)
    if not wanted:
        raise FollowUpWorkError("follow-up has no source sessions to resume")

    candidates = [session for session in sessions if session.session_id in wanted]
    if not candidates:
        raise FollowUpWorkError("no available referenced source session found")
    newest = max(candidates, key=lambda item: item.timestamp)
    if not newest.path:
        raise FollowUpWorkError("newest referenced source session has no project path")
    try:
        session_path = Path(newest.path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FollowUpWorkError("newest referenced source session is unavailable") from exc
    if not session_path.is_dir() or not _is_within(session_path, project_root):
        raise FollowUpWorkError(
            "newest referenced source session is outside the follow-up project"
        )
    return newest


def build_follow_up_prompt(follow_up: FollowUp) -> str:
    """Build the initial context for a new coding session."""
    sections = [
        "Work on this user-owned follow-up:",
        follow_up.text.strip(),
    ]
    if follow_up.context.strip():
        sections.extend(["Context:", follow_up.context.strip()])
    if follow_up.blockers:
        sections.extend(["Blockers:", *[f"- {item}" for item in follow_up.blockers]])
    if follow_up.completion_criteria:
        sections.extend(
            ["Completion criteria:", *[f"- {item}" for item in follow_up.completion_criteria]]
        )
    if follow_up.source_session_ids:
        sections.extend(["Source session IDs:", *[f"- {item}" for item in follow_up.source_session_ids]])
    if follow_up.source_report_ids:
        sections.extend(["Source report IDs:", *[f"- {item}" for item in follow_up.source_report_ids]])
    sections.append(
        "Do not change the follow-up status automatically. The user must mark it done manually."
    )
    return "\n\n".join(sections)


def prepare_follow_up_work(
    follow_up: FollowUp,
    *,
    mode: str | None = None,
    harness: str | None = None,
    input_fn: InputFunction = input,
    session_loader: SessionLoader | None = None,
    registry_loader: RegistryLoader = load_registry,
) -> FollowUpWorkPlan:
    """Create a launch plan without starting a process or changing state."""
    action = choose_work_action(mode, input_fn)
    project_root = validate_project_root(follow_up.project_root)

    if action == "resume":
        if harness is not None:
            raise FollowUpWorkError("--harness is only valid when starting a new session")
        sessions = (session_loader or (lambda: get_all_sessions(limit=None)))()
        source = find_source_session(follow_up, sessions, project_root)
        if source.tool_name not in _RESUME_FLAGS or not source.session_id:
            raise FollowUpWorkError(
                f"{source.tool_name or source.agent} does not support CLI session resume"
            )
        return FollowUpWorkPlan(
            follow_up_id=follow_up.id,
            action="resume",
            cwd=str(Path(source.path).expanduser().resolve(strict=True)),
            launch_args=tuple(_resume_cmd_args(source)),
            source_session_id=source.session_id,
        )

    if not harness or not harness.strip():
        raise FollowUpWorkError("starting a new session requires a harness name")
    tools = registry_loader()
    canonical = resolve(tools, harness.strip())
    if canonical is None:
        raise FollowUpWorkError(f"unknown harness: {harness.strip()}")
    prompt = build_follow_up_prompt(follow_up)
    return FollowUpWorkPlan(
        follow_up_id=follow_up.id,
        action="new",
        cwd=str(project_root),
        launch_args=(canonical, prompt),
        prompt=prompt,
    )


def _default_launch(args: Sequence[str], cwd: str) -> object:
    from quiver.harness.commands import cmd_use

    previous = os.getcwd()
    try:
        os.chdir(cwd)
        return cmd_use(list(args))
    finally:
        # os.execvp normally replaces the process; injected/test launchers return.
        os.chdir(previous)


def launch_follow_up_work(
    plan: FollowUpWorkPlan,
    launch_fn: LaunchFunction = _default_launch,
) -> object:
    """Execute an already validated plan without touching follow-up state."""
    return launch_fn(plan.launch_args, plan.cwd)


def work_on_follow_up(
    follow_up: FollowUp,
    *,
    mode: str | None = None,
    harness: str | None = None,
    input_fn: InputFunction = input,
    launch_fn: LaunchFunction = _default_launch,
    session_loader: SessionLoader | None = None,
    registry_loader: RegistryLoader = load_registry,
) -> object:
    """Prepare and launch work while leaving status under user control."""
    plan = prepare_follow_up_work(
        follow_up,
        mode=mode,
        harness=harness,
        input_fn=input_fn,
        session_loader=session_loader,
        registry_loader=registry_loader,
    )
    return launch_follow_up_work(plan, launch_fn)
