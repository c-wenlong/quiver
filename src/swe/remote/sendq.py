"""Per-key FIFO send queue: message writes run off the HTTP handler.

A headless send blocks for a whole agent turn, and ``queue`` mode may
wait minutes for the session to go idle — neither can hold a request
thread. ``SendQueue`` runs one daemon worker per session key, started
lazily and retired the moment the key's backlog drains.

Jobs carry a ``cancel`` callback: ``close()`` and post-close submits
fail the message honestly (a ``command.result`` event) instead of
dropping an accepted send on the floor.

``Waiting`` tracks the ``queue``-mode sends that have not reached their
injector yet, so the phone can list them and steer or remove one.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable

# (run, cancel, alive): ``alive`` False means the job will do nothing
# when it runs (its send was steered or removed), so it no longer holds
# a backlog slot.
Job = tuple[Callable[[], None], Callable[[], None], Callable[[], bool]]


def _always() -> bool:
    return True


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
        alive: Callable[[], bool] | None = None,
    ) -> None:
        start = False
        with self._lock:
            if not self._closed:
                self._queues.setdefault(key, []).append(
                    (run, cancel, alive or _always))
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
        """Queued plus in-flight jobs for ``key``, minus dead queued ones."""
        with self._lock:
            dead = sum(
                1 for _run, _cancel, alive in self._queues.get(key, ())
                if not alive()
            )
            return self._running.get(key, 0) - dead

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
        for _run, cancel, _alive in jobs:
            cancel()

    def _drain(self, key: str) -> None:
        while True:
            with self._lock:
                jobs = self._queues.get(key)
                if not jobs:
                    self._queues.pop(key, None)
                    self._working.discard(key)
                    return
                run, _cancel, _alive = jobs.pop(0)
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


# How many finished client_ids ``Waiting`` remembers, so acting on one
# that already left answers "already sent" rather than "no such send".
GONE_MEMORY = 256


class QueuedSend:
    """One ``queue``-mode send, from accept until it leaves the queue.

    ``state`` starts ``waiting`` and moves out of it once, through
    ``Waiting.claim``: ``sending`` (its job is handing it to the
    injector), ``steered`` (sent now as a steer), ``removed`` (dropped
    from the phone) or ``failed`` (the wait timed out or the hub
    stopped). ``wake`` cuts the job's idle poll short when it moves.
    """

    def __init__(
        self,
        client_id: str,
        text: str,
        attachments: list[str],
        payload: dict,
    ) -> None:
        self.client_id = client_id
        self.text = text
        self.attachments = list(attachments)  # refs as the phone sent them
        self.queued_at = time.time()
        # What the job needs to send it (resolved command, session,
        # submit-time status); never shown to the phone.
        self.payload = payload
        self.state = "waiting"
        self.wake = threading.Event()

    def is_waiting(self) -> bool:
        return self.state == "waiting"

    def view(self) -> dict:
        return {
            "client_id": self.client_id,
            "text": self.text,
            "attachments": list(self.attachments),
            "queued_at": self.queued_at,
        }


class Waiting:
    """Per-session index of ``QueuedSend``s still waiting, by client_id.

    The race guard is ``claim``: a send leaves ``waiting`` exactly once,
    under the lock, and whoever moves it owns its ``command.result``.
    The job claims it to inject (or to fail it), the phone claims it to
    steer it now or remove it, so a cancel that lands as the job starts
    either wins (nothing is sent) or loses (the 409 says it went out).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[str, dict[str, QueuedSend]] = {}
        self._all: set[QueuedSend] = set()
        self._gone: OrderedDict[tuple[str, str], str] = OrderedDict()

    def has(self, key: str, client_id: str) -> bool:
        with self._lock:
            return bool(client_id) and client_id in self._by_key.get(key, {})

    def add(self, key: str, send: QueuedSend) -> bool:
        """Track ``send``; False when its client_id is already waiting.

        A send without a client_id is tracked (so stop can wake it) but
        not listed: the phone could not name it in an action.
        """
        with self._lock:
            cid = send.client_id
            if cid:
                sends = self._by_key.setdefault(key, {})
                if cid in sends:
                    return False
                sends[cid] = send
                self._gone.pop((key, cid), None)
            self._all.add(send)
            return True

    def list(self, key: str) -> list[dict]:
        """The waiting sends for ``key``, oldest first."""
        with self._lock:
            return [s.view() for s in self._by_key.get(key, {}).values()]

    def claim(self, key: str, send: QueuedSend, outcome: str) -> bool:
        """Move ``send`` out of ``waiting``; False if something else did."""
        with self._lock:
            return self._claim(key, send, outcome)

    def take(
        self, key: str, client_id: str, outcome: str
    ) -> tuple[QueuedSend | None, str]:
        """Claim the waiting send named ``client_id``.

        Returns ``(send, "")`` on success, else ``(None, why)`` where
        ``why`` is the outcome it already left with, or ``"unknown"``.
        """
        with self._lock:
            send = self._by_key.get(key, {}).get(client_id)
            if send is not None and self._claim(key, send, outcome):
                return send, ""
            return None, self._gone.get((key, client_id), "unknown")

    def update(
        self, key: str, client_id: str, change: Callable[[QueuedSend], None]
    ) -> tuple[QueuedSend | None, str]:
        """Run ``change`` on the waiting send named ``client_id``.

        It runs under the lock, so it lands before the send's job claims
        it to inject, or not at all. Returns ``(send, "")``, else
        ``(None, why)`` like ``take``.
        """
        with self._lock:
            send = self._by_key.get(key, {}).get(client_id)
            if send is not None and send.state == "waiting":
                change(send)
                return send, ""
            return None, self._gone.get((key, client_id), "unknown")

    def wake_all(self) -> None:
        """Cut every waiting job's poll short (the hub is stopping)."""
        with self._lock:
            sends = list(self._all)
        for send in sends:
            send.wake.set()

    def _claim(self, key: str, send: QueuedSend, outcome: str) -> bool:
        if send.state != "waiting":
            return False
        send.state = outcome
        self._all.discard(send)
        cid = send.client_id
        if cid:
            sends = self._by_key.get(key, {})
            if sends.get(cid) is send:
                del sends[cid]
                if not sends:
                    self._by_key.pop(key, None)
            self._gone[(key, cid)] = outcome
            self._gone.move_to_end((key, cid))
            while len(self._gone) > GONE_MEMORY:
                self._gone.popitem(last=False)
        send.wake.set()
        return True
