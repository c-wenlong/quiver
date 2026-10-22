"""Per-key FIFO send queue: message writes run off the HTTP handler.

A headless send blocks for a whole agent turn, and ``queue`` mode may
wait minutes for the session to go idle — neither can hold a request
thread. ``SendQueue`` runs one daemon worker per session key, started
lazily and retired the moment the key's backlog drains.

Jobs carry a ``cancel`` callback: ``close()`` and post-close submits
fail the message honestly (a ``command.result`` event) instead of
dropping an accepted send on the floor.
"""

from __future__ import annotations

import threading
from typing import Callable

Job = tuple[Callable[[], None], Callable[[], None]]


class SendQueue:
    """``submit`` appends a job; workers drain their key in order."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, list[Job]] = {}
        self._running: dict[str, int] = {}
        self._working: set[str] = set()
        self._closed = False

    def submit(
        self,
        key: str,
        run: Callable[[], None],
        cancel: Callable[[], None],
    ) -> None:
        start = False
        with self._lock:
            if not self._closed:
                self._queues.setdefault(key, []).append((run, cancel))
                self._running[key] = self._running.get(key, 0) + 1
                if key not in self._working:
                    self._working.add(key)
                    start = True
            else:
                run = None
        if run is None:
            cancel()
            return
        if start:
            threading.Thread(
                target=self._drain, args=(key,), daemon=True
            ).start()

    def pending(self, key: str) -> int:
        """Queued plus in-flight jobs for ``key``."""
        with self._lock:
            return self._running.get(key, 0)

    def close(self) -> None:
        """Cancel every not-yet-started job; refuse later submits."""
        with self._lock:
            self._closed = True
            jobs = [j for jobs in self._queues.values() for j in jobs]
            for key, key_jobs in self._queues.items():
                # Never-run jobs stop counting as in-flight here.
                left = self._running.get(key, 0) - len(key_jobs)
                if left > 0:
                    self._running[key] = left
                else:
                    self._running.pop(key, None)
            self._queues.clear()
        for _run, cancel in jobs:
            cancel()

    def _drain(self, key: str) -> None:
        while True:
            with self._lock:
                jobs = self._queues.get(key)
                if not jobs:
                    self._queues.pop(key, None)
                    self._working.discard(key)
                    return
                run, _cancel = jobs.pop(0)
            try:
                run()
            except Exception:
                pass  # a failed job never takes down the key's queue
            finally:
                with self._lock:
                    left = self._running.get(key, 0) - 1
                    if left > 0:
                        self._running[key] = left
                    else:
                        self._running.pop(key, None)
