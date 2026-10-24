"""Detach ``when_idle``: wait out the session's turn, then detach.

The hub and the satellite agent each hold one ``PendingDetaches``. A
pending detach is a daemon thread polling the session's status; when it
is no longer ``active`` the thread claims the entry and runs ``fire``.
A later attach, ``now``/``keep`` detach or kill cancels it; the wait is
bounded like a queue-mode send's, and every outcome is reported once.

The claim is the race guard: ``cancel`` and the waiter both move the
entry out of ``waiting`` under one lock, so exactly one of them acts.
A cancel that arrives while ``fire`` is running waits for it to finish,
so the caller then sees the session as already detached.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

# Same cadence and cap as a queue-mode send's wait for idle
# (``hub.QUEUE_POLL_S`` / ``hub.QUEUE_WAIT_MAX_S``).
WHEN_IDLE_POLL_S = 2.0
WHEN_IDLE_MAX_S = 1800

# How long a cancel waits for a detach that is already running.
_FIRING_WAIT_S = 15.0

# report(ok, note, cancelled): one call per pending detach.
Report = Callable[[bool, str, bool], None]


class _Pending:
    __slots__ = ("state", "wake", "done", "report")

    def __init__(self, report: Report) -> None:
        # waiting -> firing | cancelled | expired | stopped
        self.state = "waiting"
        self.wake = threading.Event()
        self.done = threading.Event()
        self.report = report


class PendingDetaches:
    """At most one pending ``when_idle`` detach per session key."""

    def __init__(self, stop: threading.Event | None = None) -> None:
        self._stop = stop or threading.Event()
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    def pending(self, key: str) -> bool:
        with self._lock:
            entry = self._pending.get(key)
            return entry is not None and entry.state == "waiting"

    def schedule(
        self,
        key: str,
        *,
        status: Callable[[], str],
        fire: Callable[[], tuple[bool, str]],
        report: Report,
    ) -> bool:
        """Start waiting; False when one is already pending for ``key``."""
        with self._lock:
            if key in self._pending:
                return False
            entry = _Pending(report)
            self._pending[key] = entry
        threading.Thread(
            target=self._run, args=(key, entry, status, fire),
            name=f"detach-when-idle-{key}", daemon=True,
        ).start()
        return True

    def cancel(self, key: str, note: str) -> str:
        """Drop ``key``'s pending detach.

        Returns ``none`` (nothing pending), ``cancelled`` (it was waiting;
        reported as cancelled with ``note``) or ``fired`` (it was already
        detaching; returns once that finished).
        """
        with self._lock:
            entry = self._pending.get(key)
            if entry is None:
                return "none"
            if entry.state == "waiting":
                entry.state = "cancelled"
                self._pending.pop(key, None)
                entry.wake.set()
                firing = False
            else:
                firing = True
        if firing:
            entry.done.wait(_FIRING_WAIT_S)
            return "fired"
        entry.report(False, note, True)
        return "cancelled"

    def nudge(self, key: str) -> None:
        """Re-check ``key``'s status now instead of at the next poll.

        The tail thread calls this when a status probe finds the session
        has left ``active``, so the detach fires within a second of the
        turn ending rather than up to WHEN_IDLE_POLL_S later.
        """
        with self._lock:
            entry = self._pending.get(key)
            if entry is not None and entry.state == "waiting":
                entry.wake.set()

    def close(self) -> None:
        """Wake every waiter; with ``stop`` set they report and exit."""
        with self._lock:
            entries = list(self._pending.values())
        for entry in entries:
            entry.wake.set()

    def _claim(self, key: str, entry: _Pending, state: str) -> bool:
        with self._lock:
            if entry.state != "waiting":
                return False
            entry.state = state
            if state != "firing" and self._pending.get(key) is entry:
                del self._pending[key]
            return True

    def _run(
        self,
        key: str,
        entry: _Pending,
        status: Callable[[], str],
        fire: Callable[[], tuple[bool, str]],
    ) -> None:
        try:
            deadline = time.monotonic() + WHEN_IDLE_MAX_S
            while True:
                if entry.state != "waiting":
                    return
                if self._stop.is_set():
                    if self._claim(key, entry, "stopped"):
                        entry.report(
                            False, "stopped before the turn ended; the "
                            "session is still attached", False,
                        )
                    return
                try:
                    live = status()
                except Exception:
                    live = "active"  # unknown: keep waiting, bounded
                if live != "active":
                    break
                if time.monotonic() >= deadline:
                    if self._claim(key, entry, "expired"):
                        entry.report(
                            False,
                            f"session stayed busy for "
                            f"{WHEN_IDLE_MAX_S // 60} min; it is still "
                            "attached",
                            False,
                        )
                    return
                entry.wake.wait(WHEN_IDLE_POLL_S)
                # A nudge only asks for one early check. A cancel or close
                # that set it is seen at the top of the loop (state, stop).
                entry.wake.clear()
            if not self._claim(key, entry, "firing"):
                return
            try:
                ok, note = fire()
            except Exception as exc:
                ok, note = False, str(exc)
            with self._lock:
                if self._pending.get(key) is entry:
                    del self._pending[key]
            entry.report(ok, note, False)
        finally:
            entry.done.set()
