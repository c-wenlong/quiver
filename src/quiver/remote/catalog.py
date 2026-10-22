"""Session registry: local + satellite rows, machine-scoped.

The catalog turns ``swe session`` rows into the phone's ``/v1/sessions``
payload: every ``SessionRow.to_json`` field verbatim, plus ``key`` (the
journal/stream name), ``machine_id`` and ``ownership``. Machines register
in ``data/remote/machines.json`` — the hub writes its own row at serve
time, satellites upsert theirs over the agent channel.
"""

from __future__ import annotations

import json
import os
import platform
import time
import uuid
from pathlib import Path
from typing import Callable

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import protocol
from quiver.sessions.models import Session, SessionRow
from quiver.sessions.resume import resume_json

# How fresh the sessions list stays. The aggregator's own 60s disk cache
# keeps each refresh cheap even with every parser in the registry.
REFRESH_INTERVAL_S = 10.0

# Cap on rows served at once: a well-used machine lists thousands, and the
# phone wants the live end.
MAX_ROWS = 500


def load_machine_id(home: Path | str | None = None) -> str:
    """This machine's stable remote id, minted on first use.

    Lives in ``data/remote/machine.json`` — independent of hostname so a
    rename does not orphan journals or registry rows.
    """
    path = remote_dir_for(home) / "machine.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
    except Exception:
        pass
    machine_id = uuid.uuid4().hex[:16]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"id": machine_id}))
    except OSError:
        pass
    return machine_id


def local_machine(home: Path | str | None = None) -> dict:
    """This machine's ``/v1/machines`` row."""
    return {
        "id": load_machine_id(home),
        "name": platform.node() or "localhost",
        "os": platform.system().lower() or os.name,
        "online": True,
        "last_seen_at": time.time(),
        "harnesses": [],
    }


def _machines_file(home: Path | None) -> Path:
    return remote_dir_for(home) / "machines.json"


def load_machines(home: Path | str | None = None) -> dict[str, dict]:
    """``machine_id → row`` for every machine the hub has heard from."""
    path = _machines_file(Path(home) if home is not None else None)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        machines = data.get("machines") if isinstance(data, dict) else None
        if isinstance(machines, dict):
            return machines
    except Exception:
        pass
    return {}


def upsert_machine(home: Path | None, row: dict) -> dict[str, dict]:
    """Insert or update one machine row and persist the registry."""
    machines = load_machines(home)
    mid = str(row.get("id") or "")
    if not mid:
        return machines
    existing = machines.get(mid, {})
    machines[mid] = {**existing, **row}
    path = _machines_file(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path, json.dumps({"machines": machines}, indent=1)
        )
    except OSError:
        pass
    return machines


def _supervised_keys(home: Path | None) -> set[str]:
    """Session keys under our supervisor; dead tmux entries prune out."""
    from quiver.remote import supervise

    return supervise.supervised_keys(home)


def session_to_row(
    session: Session,
    status: str,
    machine_id: str,
    supervised: set[str],
) -> tuple[str, dict]:
    """``Session`` → (key, ``/v1/sessions`` row)."""
    key = protocol.session_key(
        session.tool_name, session.session_id, session.path
    )
    attached = key in supervised
    row = SessionRow.of(session, status, resume_json(session)).to_json()
    row["key"] = key
    row["machine_id"] = machine_id
    row["ownership"] = (
        protocol.OWNERSHIP_ATTACHED if attached else protocol.OWNERSHIP_OBSERVED
    )
    # Whether Attach is even possible: a resumable harness on this machine.
    row["attachable"] = row["resume"] is not None
    return key, row


class Catalog:
    """The hub-side session list: refresh on a timer, serve instantly."""

    def __init__(
        self,
        home: Path | str | None = None,
        machine_id: str | None = None,
        fetch_sessions: Callable[[], list[Session]] | None = None,
        status_of: Callable[[list[Session]], list[str]] | None = None,
        max_rows: int = MAX_ROWS,
    ) -> None:
        self.home = Path(home) if home is not None else None
        self.machine_id = machine_id or load_machine_id(self.home)
        if fetch_sessions is None:
            from quiver.sessions.aggregator import get_all_sessions

            fetch_sessions = lambda: get_all_sessions(limit=None, use_cache=True)
        if status_of is None:
            from quiver.sessions.status import session_statuses

            status_of = session_statuses
        self._fetch = fetch_sessions
        self._status_of = status_of
        self.max_rows = max_rows
        self._rows: list[tuple[str, Session, dict, str]] = []
        self._refreshed_at = 0.0

    @property
    def refreshed_at(self) -> float:
        return self._refreshed_at

    def refresh(self) -> list[dict]:
        """Re-list sessions, reclassify statuses, rebuild rows."""
        try:
            sessions = self._fetch()
        except Exception:
            sessions = []
        try:
            statuses = self._status_of(sessions)
        except Exception:
            statuses = [""] * len(sessions)
        supervised = _supervised_keys(self.home)
        rows: list[tuple[str, Session, dict, str]] = []
        for session, status in zip(sessions, statuses):
            key, row = session_to_row(
                session, status, self.machine_id, supervised
            )
            rows.append((key, session, row, status))
        self._rows = rows
        self._refreshed_at = time.time()
        return [row for _key, _s, row, _st in rows]

    def rows(self) -> list[dict]:
        return [row for _key, _s, row, _st in self._rows]

    def tail_rows(self) -> list[tuple[str, Session, str]]:
        """``(key, Session, status)`` for the tail manager, newest first."""
        return [
            (key, session, status)
            for key, session, _row, status in self._rows
        ]

    def row_for(self, key: str) -> dict | None:
        for k, _s, row, _st in self._rows:
            if k == key:
                return row
        return None

    def session_for(self, key: str) -> Session | None:
        for k, session, _row, _st in self._rows:
            if k == key:
                return session
        return None

    def due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self._refreshed_at >= REFRESH_INTERVAL_S

    def inbox(self) -> list[dict]:
        """Pending decisions: sessions asking for input or in trouble."""
        items = []
        for _key, _s, row, status in self._rows:
            if status in {"followup", "error", "interrupted"}:
                items.append(
                    {
                        "key": row["key"],
                        "machine_id": row["machine_id"],
                        "harness": row["harness"],
                        "agent": row["agent"],
                        "title": row["title"],
                        "cwd": row["cwd"],
                        "status": status,
                        "last_active_at": row["last_active_at"],
                    }
                )
        return items
