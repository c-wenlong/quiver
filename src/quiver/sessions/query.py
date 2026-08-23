"""Composable filtering and calendar ranges for coding sessions."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Iterable

from quiver.sessions.models import Session


def _positive_int(value: int | None, name: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer")


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD format") from exc


def _local_date(now: datetime | None, zone: tzinfo | None) -> date:
    if now is None:
        return datetime.now(zone).date() if zone is not None else datetime.now().date()
    if zone is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=zone).date()
        return now.astimezone(zone).date()
    if now.tzinfo is not None:
        return now.astimezone().date()
    return now.date()


def _midnight_epoch_ms(value: date, zone: tzinfo | None) -> int:
    boundary = datetime.combine(value, time.min)
    if zone is not None:
        boundary = boundary.replace(tzinfo=zone)
    # For zone=None, timestamp() deliberately uses the host's local timezone,
    # including its historical DST rules for this particular date.
    return int(boundary.timestamp() * 1000)


def calendar_range_ms(
    *,
    days: int | None = None,
    weeks: int | None = None,
    start: str | None = None,
    end: str | None = None,
    now: datetime | None = None,
    zone: tzinfo | None = None,
) -> tuple[int, int]:
    """Return inclusive epoch-millisecond bounds for a local calendar range.

    Relative ranges include today: ``days=1`` is today, while ``weeks=1`` is
    today and the preceding six local calendar dates.
    """

    _positive_int(days, "days")
    _positive_int(weeks, "weeks")

    relative_count = int(days is not None) + int(weeks is not None)
    explicit_count = int(start is not None) + int(end is not None)
    if relative_count > 1:
        raise ValueError("days and weeks are mutually exclusive")
    if relative_count and explicit_count:
        raise ValueError("relative and explicit date ranges are mutually exclusive")
    if explicit_count == 1:
        raise ValueError("start and end must be provided together")
    if not relative_count and not explicit_count:
        raise ValueError("a relative or explicit date range is required")

    if start is not None and end is not None:
        first = _parse_date(start, "start")
        last = _parse_date(end, "end")
        if first > last:
            raise ValueError("start must be on or before end")
    else:
        last = _local_date(now, zone)
        if days is not None:
            count = days
        else:
            assert weeks is not None
            count = weeks * 7
        first = last - timedelta(days=count - 1)

    start_ms = _midnight_epoch_ms(first, zone)
    end_ms = _midnight_epoch_ms(last + timedelta(days=1), zone) - 1
    return start_ms, end_ms


@dataclass(frozen=True, slots=True)
class SessionQuery:
    """A validated, immutable set of filters for :class:`Session` objects."""

    start_ms: int | float | None = None
    end_ms: int | float | None = None
    agent: str | None = None
    cwd: str | None = None
    search: str | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("start_ms and end_ms must be provided together")
        if (
            self.start_ms is not None
            and self.end_ms is not None
            and self.start_ms > self.end_ms
        ):
            raise ValueError("start_ms must be less than or equal to end_ms")
        _positive_int(self.limit, "limit")

    @classmethod
    def from_calendar(
        cls,
        *,
        days: int | None = None,
        weeks: int | None = None,
        start: str | None = None,
        end: str | None = None,
        now: datetime | None = None,
        zone: tzinfo | None = None,
        agent: str | None = None,
        cwd: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> "SessionQuery":
        start_ms, end_ms = calendar_range_ms(
            days=days, weeks=weeks, start=start, end=end, now=now, zone=zone
        )
        return cls(
            start_ms=start_ms,
            end_ms=end_ms,
            agent=agent,
            cwd=cwd,
            search=search,
            limit=limit,
        )

    def apply(self, sessions: Iterable[Session]) -> list[Session]:
        """Apply all filters, sort newest first, and apply the limit last."""

        # ⚡ Bolt: Cache path within checks to avoid O(N) filesystem hits
        # when filtering many sessions that share the same base path.
        path_cache: dict[str, bool] = {}

        matches = [session for session in sessions if self._matches(session, path_cache)]
        matches.sort(key=lambda session: session.timestamp, reverse=True)
        return matches if self.limit is None else matches[: self.limit]

    def _matches(self, session: Session, path_cache: dict[str, bool] | None = None) -> bool:
        if self.start_ms is not None and self.end_ms is not None:
            if not self.start_ms <= session.timestamp <= self.end_ms:
                return False
        if self.agent and not _matches_agent(session, self.agent):
            return False
        if self.cwd:
            if path_cache is not None:
                if session.path not in path_cache:
                    path_cache[session.path] = _path_is_within(session.path, self.cwd)
                if not path_cache[session.path]:
                    return False
            else:
                if not _path_is_within(session.path, self.cwd):
                    return False
        if self.search and not _matches_search(session, self.search):
            return False
        return True


def filter_sessions(sessions: Iterable[Session], query: SessionQuery) -> list[Session]:
    """Functional wrapper for callers that do not want to call ``apply``."""

    return query.apply(sessions)


def _matches_agent(session: Session, agent: str) -> bool:
    needle = agent.casefold()
    if needle in {(session.agent or "").casefold(), (session.tool_name or "").casefold()}:
        return True

    # Keep aliases aligned with the existing aggregator without duplicating its
    # registry. The local import avoids a module cycle during aggregator import.
    try:
        from quiver.sessions.aggregator import PARSER_REGISTRY

        matching_tools = {
            name
            for name, _parser, keys in PARSER_REGISTRY
            if needle in {key.casefold() for key in keys}
        }
        return (session.tool_name or "").casefold() in matching_tools
    except ImportError:
        return False


def _path_is_within(session_path: str, cwd: str) -> bool:
    candidate = os.path.realpath(os.path.expanduser(session_path))
    parent = os.path.realpath(os.path.expanduser(cwd))
    try:
        return os.path.commonpath((candidate, parent)) == parent
    except ValueError:
        return False


def _matches_search(session: Session, search: str) -> bool:
    needle = search.casefold()
    metadata = " ".join(
        (
            session.agent or "",
            session.tool_name or "",
            session.path or "",
            session.title or "",
            session.session_id or "",
        )
    ).casefold()
    return needle in metadata


def resolve_project_root(path: str | os.PathLike[str]) -> str:
    """Resolve a session path to its Git root, falling back to that path."""

    recorded = Path(path).expanduser()
    try:
        recorded = recorded.resolve(strict=False)
    except OSError:
        recorded = Path(os.path.abspath(os.fspath(recorded)))

    working_dir = recorded.parent if recorded.is_file() else recorded
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(working_dir), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        root = result.stdout.strip()
        if root:
            return os.fspath(Path(root).resolve(strict=False))
    except (OSError, subprocess.SubprocessError):
        pass
    return os.fspath(working_dir)
