"""Per-session seq-numbered event journal — the reconnect story.

Every event the hub emits lands in ``data/remote/journal/<slug>.jsonl``
with a per-session ``seq``. A client that was offline for half an hour
rejoins its SSE stream with ``?last=<seq>`` and replays the gap instead of
refetching a transcript — the same thing Claude Remote Control's
server-side transcript buys them, except ours never leaves the tailnet.

The journal is a ring: appends past ``RING_MAX`` rewrite the file keeping
the newest ``RING_KEEP`` events. Clients that fell off the back of the
ring get ``truncated: true`` in the replay header and refetch history
through ``/v1/sessions/{id}/messages``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from quiver.paths import remote_dir_for
from quiver.remote.protocol import key_slug

# Ring bounds: keep journals small enough that replay stays a disk read,
# not a transcript-length scan.
RING_MAX = 4000
RING_KEEP = 2000

_LOCK = threading.RLock()  # append -> last_seq re-enters the same lock


class Journal:
    """Append/read event envelopes per session key, seq-numbered."""

    def __init__(self, home: Path | str | None = None) -> None:
        root = remote_dir_for(Path(home) if home is not None else None)
        self.dir = root / "journal"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seqs: dict[str, int] = {}

    def _file(self, session_key: str) -> Path:
        return self.dir / f"{key_slug(session_key)}.jsonl"

    def _load_seq(self, session_key: str) -> int:
        """Recover the last seq written for ``session_key``.

        Reads a tail window and walks it backward. A single event can
        exceed the window (full tool args run large), so the window
        doubles until a complete record parses or the file is spent —
        a >64 KiB final event must not reset numbering to 1.
        """
        path = self._file(session_key)
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        window = 65536
        while True:
            try:
                with open(path, "rb") as fh:
                    if size > window:
                        fh.seek(-window, os.SEEK_END)
                    tail = fh.read().decode("utf-8", errors="replace")
            except OSError:
                return 0
            for line in reversed(tail.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                seq = rec.get("seq")
                if isinstance(seq, int) and seq > 0:
                    return seq
            if window >= size:
                return 0
            window = min(window * 4, size)

    def last_seq(self, session_key: str) -> int:
        with _LOCK:
            if session_key not in self._seqs:
                self._seqs[session_key] = self._load_seq(session_key)
            return self._seqs[session_key]

    def append(self, session_key: str, event: dict) -> dict:
        """Stamp ``seq`` on ``event`` and append it; returns the event."""
        with _LOCK:
            seq = self.last_seq(session_key) + 1
            event = dict(event, seq=seq)
            self._seqs[session_key] = seq
            path = self._file(session_key)
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                return event
            self._maybe_trim(path)
            return event

    def _maybe_trim(self, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        # Byte check first: a line is ~200-400 B, so a 2 MB file is
        # already well past RING_MAX records and counting lines is cheap.
        if size < 1024 * 1024:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return
        if len(lines) <= RING_MAX:
            return
        kept = lines[-RING_KEEP:]
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def read_since(
        self,
        session_key: str,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> tuple[list[dict], bool]:
        """(events with seq > after_seq, truncated).

        ``truncated`` is True when the client asked for events the ring
        already dropped: the oldest seq on disk is past ``after_seq``.
        """
        path = self._file(session_key)
        events: list[dict] = []
        oldest = 0
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    seq = rec.get("seq")
                    if not isinstance(seq, int):
                        continue
                    if not oldest:
                        oldest = seq
                    if seq > after_seq:
                        events.append(rec)
                        if limit is not None and len(events) >= limit:
                            break
        except OSError:
            return [], False
        truncated = bool(oldest and after_seq and after_seq < oldest - 1)
        return events, truncated
