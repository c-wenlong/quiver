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
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import fork as fork_mod, protocol, sessionmeta
from quiver.sessions.models import Session, SessionRow
from quiver.sessions.resume import resume_json

# How fresh the sessions list stays. The aggregator's own 60s disk cache
# keeps each refresh cheap even with every parser in the registry.
REFRESH_INTERVAL_S = 10.0

# Cap on rows served at once: a well-used machine lists thousands, and the
# phone wants the live end.
MAX_ROWS = 500

# Follow-ups, errors and interrupts leave the inbox this long after the
# session's last activity; pending approvals never do.
INBOX_MAX_AGE_S = 3 * 86_400
INBOX_STATUSES = frozenset({"followup", "error", "interrupted"})


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


def detected_harnesses() -> list[str]:
    """Tool names whose launch binary is on this machine's PATH."""
    from quiver.sessions.resume import (
        LIMITED_RESUME,
        RESUME_FLAGS,
        launch_tool_name,
    )

    out = []
    for tool_name in sorted(set(RESUME_FLAGS) | set(LIMITED_RESUME)):
        binary = launch_tool_name(tool_name)
        if binary and shutil.which(binary):
            out.append(tool_name)
    return out


# The Mac app's preferences: Settings > Provider visibility keeps the
# provider order and the ones switched off here.
APP_PREFS = Path.home() / "Library/Preferences/com.cwenlong.quiver.plist"


def _provider_visibility() -> tuple[list[str], set[str]]:
    """``(order, disabled)`` from the Mac app's Provider visibility, or
    empty when the app has never run here (a Linux hub)."""
    import plistlib

    try:
        with open(APP_PREFS, "rb") as fh:
            prefs = plistlib.load(fh)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return [], set()
    order = prefs.get("providerOrder")
    disabled = prefs.get("providerDisabled")
    return (
        [str(x) for x in order] if isinstance(order, list) else [],
        {str(x) for x in disabled} if isinstance(disabled, list) else set(),
    )


def _session_parsers() -> dict:
    """``name -> parser`` for every harness swe can list sessions of."""
    from quiver.sessions.aggregator import PARSER_REGISTRY

    return {name: parser for name, parser, _keys in PARSER_REGISTRY}


def launchable_harnesses() -> list[dict]:
    """What ``POST /v1/sessions`` can start here, in Provider visibility
    order: every harness ``swe list`` shows (not archived) whose command
    is on PATH, minus the ones switched off in the Mac app's Settings.
    Each item is ``{name, command, display_name, tracked}``; ``tracked``
    is false for a harness swe has no session parser for (qwen-code): it
    starts, but no session ever shows up to open as a chat."""
    from quiver.harness import registry

    try:
        reg = registry.load_registry_readonly()
    except (OSError, ValueError):
        return []
    order, disabled = _provider_visibility()
    rank = {name: i for i, name in enumerate(order)}
    parsers = _session_parsers()
    out = []
    for position, (name, entry) in enumerate(reg.items()):
        if not isinstance(entry, dict) or not registry.is_active(entry):
            continue
        if name in disabled:
            continue
        command = str(entry.get("command") or name)
        if not shutil.which(command):
            continue
        out.append((rank.get(name, len(rank) + position), {
            "name": name,
            "command": command,
            "display_name": str(entry.get("display_name") or name),
            "tracked": name in parsers,
        }))
    out.sort(key=lambda pair: pair[0])
    return [item for _rank, item in out]


def known_session_ids(
    rows: list[tuple[str, Session, str]], harness: str, cwd: str
) -> list[str]:
    """Ids of ``harness`` sessions already in ``cwd``, for adoption to
    refuse. The catalog can be a minute old, so the harness's own parser
    runs too: a session started since then is known as well."""
    real_cwd = os.path.realpath(cwd)

    def here(session: Session) -> bool:
        return (session.tool_name == harness
                and os.path.realpath(session.path or "") == real_cwd)

    known = {s.session_id for _k, s, _st in rows if here(s)}
    parser = _session_parsers().get(harness)
    if parser is not None:
        try:
            known.update(s.session_id for s in parser() if here(s))
        except Exception:
            pass
    return sorted(known)


def harness_names(launchable: list[dict]) -> dict[str, str]:
    return {item["name"]: item["display_name"] for item in launchable}


def untracked(launchable: list[dict]) -> list[str]:
    return [item["name"] for item in launchable if not item["tracked"]]


def launch_command(harness: str) -> str | None:
    """The command that starts ``harness``, or None when it can't start here."""
    for item in launchable_harnesses():
        if item["name"] == harness:
            return item["command"]
    return None


def tailscale_ip() -> str:
    """This machine's tailnet IPv4, or '' when Tailscale is absent."""
    from quiver.remote.tsid import tailscale_exe

    exe = tailscale_exe()
    if not exe:
        return ""
    import subprocess

    try:
        out = subprocess.run(
            [exe, "ip", "-4"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    lines = out.stdout.strip().splitlines()
    return lines[0].strip() if lines else ""


def local_machine(home: Path | str | None = None) -> dict:
    """This machine's ``/v1/machines`` row.

    ``launchable`` is what the phone's new-session sheet offers, in order
    (``launchable_harnesses``); ``harness_names`` names each one.
    """
    harnesses = detected_harnesses()
    launchable = launchable_harnesses()
    return {
        "id": load_machine_id(home),
        "name": platform.node() or "localhost",
        "os": platform.system().lower() or os.name,
        "online": True,
        "last_seen_at": time.time(),
        "harnesses": harnesses,
        "launchable": [item["name"] for item in launchable],
        "harness_names": harness_names(launchable),
        "untracked_harnesses": untracked(launchable),
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


_machines_lock = threading.Lock()


def upsert_machine(home: Path | None, row: dict) -> dict[str, dict]:
    """Insert or update one machine row and persist the registry.

    The whole read-modify-write runs under a lock: satellite connect,
    disconnect and register calls hit this concurrently and would
    otherwise lose each other's rows.
    """
    with _machines_lock:
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


def _satellite_dir(home: Path | None) -> Path:
    return remote_dir_for(home) / "satellite"


def _safe_mid(machine_id: str) -> str:
    """``machine_id`` as a filename: alphanumeric run, else its slug."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", machine_id)
    # "..", ".", "" escape the satellite dir — collapse to a slug.
    if not safe or set(safe) <= {".", "_", "-"}:
        safe = protocol.key_slug(machine_id)
    return safe


def merge_remote_rows(
    home: Path | None, machine_id: str, rows: list[dict]
) -> list[dict]:
    """Persist a satellite's catalog push; rows get ``@mid`` keys.

    The file lets the hub keep showing a sleeping satellite's sessions
    (marked offline) instead of dropping them on restart.
    """
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        local = str(row.get("key") or "")
        row["key"] = protocol.remote_key(machine_id, local)
        row["machine_id"] = machine_id
        row["remote"] = True
        out.append(row)
    path = _satellite_dir(home)
    try:
        path.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path / f"{_safe_mid(machine_id)}.json",
            json.dumps({"rows": out}, indent=1),
        )
    except OSError:
        pass
    return out


def load_remote_rows(home: Path | None) -> list[dict]:
    """All persisted satellite rows (``(key, row)``-ready dicts)."""
    base = _satellite_dir(home)
    out: list[dict] = []
    try:
        files = list(base.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            rows = json.loads(f.read_text()).get("rows") or []
        except (OSError, ValueError):
            continue
        out.extend(r for r in rows if isinstance(r, dict))
    return out


def _supervised(home: Path | None) -> dict[str, dict]:
    """Supervised entries by key; dead tmux entries prune out on read."""
    from quiver.remote import supervise

    return supervise.supervised(home)


def _kept(home: Path | None) -> dict[str, dict]:
    """Kept tmux sessions by key (observed, pane still recorded)."""
    from quiver.remote import supervise

    return supervise.kept(home)


def session_to_row(
    session: Session,
    status: str,
    machine_id: str,
    supervised: dict[str, dict],
    kept: dict[str, dict] | None = None,
) -> tuple[str, dict]:
    """``Session`` → (key, ``/v1/sessions`` row).

    An attached row carries the supervised ``mode`` and, for tmux mode,
    the ``qv-…`` pane name. An observed row left running by a ``keep``
    detach carries ``kept`` and the pane name, so a client can offer the
    pane back: attach adopts it live instead of relaunching.
    """
    key = protocol.session_key(
        session.tool_name, session.session_id, session.path
    )
    meta = supervised.get(key)
    row = SessionRow.of(session, status, resume_json(session)).to_json()
    row["key"] = key
    row["machine_id"] = machine_id
    row["ownership"] = (
        protocol.OWNERSHIP_ATTACHED
        if meta is not None else protocol.OWNERSHIP_OBSERVED
    )
    if meta is not None:
        if meta.get("mode"):
            row["mode"] = meta["mode"]
        if meta.get("tmux"):
            row["tmux"] = meta["tmux"]
    elif kept and key in kept:
        row["kept"] = True
        if kept[key].get("mode"):
            row["mode"] = kept[key]["mode"]
        if kept[key].get("tmux"):
            row["tmux"] = kept[key]["tmux"]
    # Whether Attach is even possible: a resumable harness on this
    # machine, or a kept pane attach can adopt without resume flags.
    row["attachable"] = row["resume"] is not None or bool(row.get("kept"))
    # Whether Fork is: the harness's CLI has a fork command line.
    row["can_fork"] = fork_mod.can_fork(session)
    return key, row


class Catalog:
    """The hub-side session list: refresh on a timer, serve instantly."""

    def __init__(
        self,
        home: Path | str | None = None,
        machine_id: str | None = None,
        fetch_sessions: Callable[[], list[Session]] | None = None,
        status_of: Callable[[list[Session]], list[str]] | None = None,
        fetch_github: Callable[[], list[dict] | None] | None = None,
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
        # The GitHub notification feed is hub-only: serve() wires it, so
        # a bare catalog — tests, the satellite agent — never reaches for
        # ``gh``. ``fetch_github`` returns the unread ``github`` inbox
        # rows or None on a failed read; ``on_github_alert`` runs once
        # per thread that newly turned unread.
        self.fetch_github = fetch_github
        self.on_github_alert: Callable[[dict], None] | None = None
        self._github_rows: list[dict] = []
        # gh_thread_id → updated_at of the last-seen unread state; None
        # until the first successful poll, which is a silent baseline —
        # a hub start must not re-push the whole unread backlog.
        self._github_seen: dict[str, str] | None = None
        self.max_rows = max_rows
        self._rows: list[tuple[str, Session | None, dict, str]] = []
        self._refreshed_at = 0.0
        # One lock for every write to the rows after they are built:
        # refresh's overlay-and-swap, reapply_meta (pins and titles) and
        # note_status (tail probes). Each takes it for its whole
        # read-modify-write, so none is lost to another's older read.
        self._meta_lock = threading.Lock()
        self._lock = self._meta_lock
        # Test seam: runs between refresh's metadata read and row swap.
        self._after_meta_read: Callable[[], None] | None = None
        # Statuses a tailer probed after this catalog's own read, keyed
        # by session: ``(status, probed_at)``. A refresh that started
        # before ``probed_at`` keeps these instead of its own (older)
        # verdict; one that started after drops them. ``_status_as_of``
        # is when the current rows' statuses were read.
        self._fresh: dict[str, tuple[str, float]] = {}
        self._status_as_of = 0.0

    @property
    def refreshed_at(self) -> float:
        return self._refreshed_at

    def refresh(self) -> list[dict]:
        """Re-list sessions, reclassify statuses, rebuild rows."""
        started = time.time()
        try:
            sessions = self._fetch()
        except Exception:
            sessions = []
        try:
            statuses = self._status_of(sessions)
        except Exception:
            statuses = [""] * len(sessions)
        supervised = _supervised(self.home)
        kept = _kept(self.home)
        rows: list[tuple[str, Session | None, dict, str]] = []
        for session, status in zip(sessions, statuses):
            key, row = session_to_row(
                session, status, self.machine_id, supervised, kept
            )
            rows.append((key, session, row, status))
        # Satellite sessions travel as row dicts (no Session object — the
        # satellite owns execution); remote entries stay out of tail_rows.
        for row in load_remote_rows(self.home):
            rows.append((
                str(row.get("key") or ""), None, row,
                str(row.get("status") or ""),
            ))
        # Read the pins, overlay them, keep fresher probed statuses and
        # swap the rows under one lock: reapply_meta and note_status take
        # it too, so a pin saved or a status probed meanwhile is applied
        # to the new rows, never lost to this older read.
        with self._meta_lock:
            meta = sessionmeta.load(self.home)
            if self._after_meta_read is not None:
                self._after_meta_read()
            # Fresher-wins: a tailer probe newer than this read stays.
            self._fresh = {
                k: v for k, v in self._fresh.items() if v[1] > started
            }
            for i, (key, session, row, status) in enumerate(rows):
                sessionmeta.apply(row, meta.get(key))
                fresh = self._fresh.get(key)
                if fresh is not None and session is not None:
                    row["status"] = fresh[0] or None
                    rows[i] = (key, session, row, fresh[0])
            self._rows = rows
            self._status_as_of = started
            self._refreshed_at = time.time()
        return [row for _key, _s, row, _st in rows]

    def note_status(self, key: str, status: str, at: float) -> None:
        """A tailer probed ``key`` at ``at``; patch its row now.

        Ignored when the catalog's own read is newer. Otherwise the row
        changes in place and the next refresh keeps this status unless
        that refresh started after ``at``.
        """
        with self._lock:
            if at <= self._status_as_of:
                return
            self._fresh[key] = (status, at)
            for i, (k, session, row, _st) in enumerate(self._rows):
                if k == key and session is not None:
                    row["status"] = status or None
                    self._rows[i] = (k, session, row, status)
                    break

    def tail_snapshot(self) -> tuple[list[tuple[str, Session, str]], float]:
        """``tail_rows()`` plus when their statuses were read, together."""
        with self._lock:
            return self.tail_rows(), self._status_as_of

    def rows(self) -> list[dict]:
        return [row for _key, _s, row, _st in self._rows]

    def reapply_meta(self, key: str) -> dict | None:
        """Re-overlay one row's pin and title after a write; returns the row."""
        with self._meta_lock:
            row = self.row_for(key)
            if row is not None:
                sessionmeta.apply(row, sessionmeta.get(self.home, key))
            return row

    def tail_rows(self) -> list[tuple[str, Session, str]]:
        """``(key, Session, status)`` for the tail manager, newest first."""
        return [
            (key, session, status)
            for key, session, _row, status in self._rows
            if session is not None
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

    def poll_github(self) -> None:
        """Fetch the unread GitHub listing, store its rows, and fire
        ``on_github_alert`` once per thread that newly turned unread.

        Kept out of ``refresh()``: the session path — adoption, tail
        sync — never waits on ``gh``; the catalog loop calls this after
        its session work, on the same cadence. The fetch runs unlocked;
        the dedupe and the row swap land under ``_meta_lock`` so two
        overlapping callers can't compare against the same old map and
        repeat an alert. A failed read changes nothing — the last good
        rows and the dedupe map both stay. The first successful poll is
        a silent baseline: a hub start must not re-push the whole
        unread backlog.
        """
        if self.fetch_github is None:
            return
        try:
            rows = self.fetch_github()
        except Exception:
            return
        if not isinstance(rows, list):
            return
        rows = [
            r for r in rows
            if isinstance(r, dict) and str(r.get("key") or "")
        ]
        with self._meta_lock:
            for row in rows:
                row.setdefault("machine_id", self.machine_id)
            prev = self._github_seen
            seen: dict[str, str] = {}
            alerts = []
            for row in rows:
                tid = str(row.get("gh_thread_id") or "")
                if not tid:
                    continue
                stamp = str(row.get("updated_at") or "")
                seen[tid] = stamp
                # Dedupe on gh_thread_id + updated_at: a thread already
                # seen unread at this stamp can't alert again until
                # GitHub moves it.
                if prev is not None and prev.get(tid) != stamp:
                    alerts.append(row)
            self._github_seen = seen
            self._github_rows = rows
        if self.on_github_alert is not None:
            for row in alerts:
                try:
                    self.on_github_alert(row)
                except Exception:
                    pass  # a broken notifier must not stall the loop

    def inbox(
        self,
        pending: dict[str, str] | None = None,
        dismissed: dict[str, float] | None = None,
        include_done: bool = False,
        now: float | None = None,
    ) -> list[dict]:
        """Pending decisions: sessions asking for input or in trouble.

        ``pending`` maps session key -> outstanding approval request id.
        A session waiting on an approval belongs in the inbox even when
        its transcript status is still ``active``, and it stays there
        whatever its age or any dismissal: it blocks an agent until
        answered.

        Follow-ups, errors and interrupts older than ``INBOX_MAX_AGE_S``
        drop out. ``dismissed`` maps key -> the ``last_active_at`` a
        device cleared; the item stays hidden until it moves past that.
        ``include_done`` adds sessions that finished since local
        midnight as ``done`` items.

        Unread GitHub notifications follow as one group after the
        session rows, newest first; they are all unread by construction,
        so the age cutoff never applies to them. Their ``key`` is
        ``gh:<thread id>``, which can't collide with a session key, so
        the same per-device dismissal store hides them — until GitHub
        bumps the thread's ``updated_at``.
        """
        pending = pending or {}
        dismissed = dismissed or {}
        now = time.time() if now is None else now
        cutoff = now - INBOX_MAX_AGE_S
        midnight = _local_midnight(now)
        items = []
        for _key, _s, row, status in self._rows:
            rid = pending.get(row["key"])
            last = _num(row.get("last_active_at"))
            if rid is not None:
                status = "approval"
            elif status in INBOX_STATUSES:
                if last < cutoff:
                    continue
            elif not (include_done and status == "done" and last >= midnight):
                continue
            if rid is None and row["key"] in dismissed and (
                last <= dismissed[row["key"]]
            ):
                continue
            item = {
                "key": row["key"],
                "machine_id": row["machine_id"],
                "harness": row["harness"],
                "agent": row["agent"],
                "title": row["title"],
                "cwd": row["cwd"],
                "status": status,
                "last_active_at": row["last_active_at"],
            }
            if rid is not None:
                item["request_id"] = rid
            items.append(item)
        github = []
        for row in self._github_rows:
            last = _num(row.get("last_active_at"))
            if row["key"] in dismissed and last <= dismissed[row["key"]]:
                continue
            github.append(dict(row))
        github.sort(key=lambda r: _num(r.get("last_active_at")),
                    reverse=True)
        items.extend(github)
        return items


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _local_midnight(now: float) -> float:
    """Start of ``now``'s day in the hub's local time."""
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
