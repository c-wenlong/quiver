"""Per-key FIFO send queue: message writes run off the HTTP handler.

A headless send blocks for a whole agent turn, and ``queue`` mode may
wait minutes for the session to go idle — neither can hold a request
thread. ``SendQueue`` runs one daemon worker per session key, started
lazily and retired the moment the key's backlog drains.
"""

from __future__ import annotations

import threading
from typing import Callable


class SendQueue:
    """``submit`` appends a job; workers drain their key in order."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, list[Callable[[], None]]] = {}
        self._working: set[str] = set()

    def submit(self, key: str, job: Callable[[], None]) -> None:
        with self._lock:
            self._queues.setdefault(key, []).append(job)
            if key in self._working:
                return
            self._working.add(key)
            threading.Thread(
                target=self._drain, args=(key,), daemon=True
            ).start()

    def pending(self, key: str) -> int:
        with self._lock:
            return len(self._queues.get(key, ()))

    def _drain(self, key: str) -> None:
        while True:
            with self._lock:
                jobs = self._queues.get(key)
                if not jobs:
                    self._queues.pop(key, None)
                    self._working.discard(key)
                    return
                job = jobs.pop(0)
            try:
                job()
            except Exception:
                pass  # a failed job never takes down the key's queue
