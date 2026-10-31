"""Live token streaming: ``message.delta`` events, coalesced, never journaled.

The API channels (Codex app-server, opencode serve, headless Claude) see
a reply's text arrive in small chunks. :class:`DeltaStream` gathers the
chunks per streamed message and hands a ``message.delta`` event to its
sink at most once per ``COALESCE_S`` for that message, plus a last one
with ``done: true`` when the channel says the message is finished.

Deltas are a preview. The transcript's ``message.appended`` stays the
durable record and replaces the streamed text on the client, so the
sink publishes to live subscribers only: the hub's ``publish_transient``
or a satellite's best-effort uplink. Nothing here touches a journal.

Text is cleaned and redacted like a transcript message, over the whole
text so far rather than per chunk, so a secret split across chunks is
still caught. The trailing word is held back until whitespace ends it,
and a line that looks like it carries a credential is held back until
the line ends; a private-key block is held from its BEGIN marker until
its END marker arrives. If redaction ever rewrites text already sent,
the message stops streaming and the client waits for the final record.

Payload (``data`` of a ``message.delta`` envelope, which has no ``seq``)::

    {"id": "<channel's id for the message>", "kind": "assistant",
     "text": "<cleaned text appended since the last delta>",
     "seq": 3, "done": false}
"""

from __future__ import annotations

import re
import threading
import time

from quiver.console import sanitize_document
from quiver.remote import protocol
from quiver.reports import transcripts as _tr

# At most one delta per streamed message this often.
COALESCE_S = 0.1

# A message whose text grows past this stops streaming; its final record
# still arrives. Keeps a runaway stream from holding memory.
MAX_STREAM_CHARS = 200_000

# A line holding any of these is held back whole until it ends: the
# redaction patterns that span a quoted value or a header need the full
# line to match.
_SENSITIVE_LINE_RE = re.compile(
    r"(?i)authorization|api_key|access_token|auth_token|bearer_token|"
    r"client_secret|private_key|secret_key|password|passwd|token\s*[=:\"']"
)
_BEGIN_KEY = "-----BEGIN "
# A whole BEGIN marker, label included (the redactor's label alphabet).
_BEGIN_LABEL_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]*)-----")


def _clean(text: str) -> str:
    return _tr._redact_secrets(sanitize_document(text))


def _open_key_block(raw: str) -> int:
    """Where the first private-key block the redactor can't match yet
    starts (its BEGIN marker has no complete matching END), else -1.

    A marker whose finished label is not a private key (a certificate,
    a public key) streams on; one whose label is still arriving is held,
    since it may yet turn out to be private.
    """
    pos = raw.find(_BEGIN_KEY)
    while pos >= 0:
        match = _tr._PRIVATE_KEY_RE.match(raw, pos)
        if match is not None:
            pos = raw.find(_BEGIN_KEY, match.end())
            continue
        label = _BEGIN_LABEL_RE.match(raw, pos)
        if label is not None and not label.group(1).endswith("PRIVATE KEY"):
            pos = raw.find(_BEGIN_KEY, label.end())
            continue
        return pos
    return -1


def safe_prefix(raw: str, final: bool) -> str:
    """The cleaned text of ``raw`` that is safe to show now.

    An unterminated private-key block never shows, not even when the
    message is ``final`` (a reader that died mid-block): only a complete
    block matches the redactor. Otherwise ``final`` shows everything,
    and before that the held-back tail (the unfinished word, a
    credential-looking line) waits for more text.
    """
    begin = _open_key_block(raw)
    if begin >= 0:
        raw = raw[:begin]
    if not final:
        line_start = raw.rfind("\n") + 1
        if _SENSITIVE_LINE_RE.search(raw[line_start:]):
            raw = raw[:line_start]
        else:
            cut = max(raw.rfind(" "), raw.rfind("\n"), raw.rfind("\t"))
            raw = raw[:cut + 1] if cut >= 0 else ""
    return _clean(raw)


class _Open:
    """One message mid-stream."""

    __slots__ = ("machine", "kind", "raw", "sent", "seq", "last",
                 "timer", "stalled")

    def __init__(self, machine: str, kind: str) -> None:
        self.machine = machine
        self.kind = kind
        self.raw: list[str] = []
        self.sent = ""
        self.seq = 0
        self.last = 0.0
        self.timer: threading.Timer | None = None
        self.stalled = False


class DeltaStream:
    """Per-message coalescing of token deltas into ``message.delta``.

    ``sink(key, event)`` must not block: it runs under this object's
    lock, which is what keeps one message's deltas in ``seq`` order.
    """

    def __init__(self, sink, interval: float = COALESCE_S,
                 clock=time.monotonic) -> None:
        self._sink = sink
        self._interval = interval
        self._clock = clock
        self._lock = threading.Lock()
        self._open: dict[tuple[str, str], _Open] = {}

    def push(self, key: str, machine: str, msg_id: str, kind: str,
             chunk: str) -> None:
        """Add ``chunk`` to message ``msg_id``'s text."""
        if not key or not msg_id or not chunk or \
                kind not in protocol.MESSAGE_KINDS:
            return
        with self._lock:
            st = self._open.get((key, msg_id))
            if st is None:
                st = _Open(machine, kind)
                self._open[(key, msg_id)] = st
            if st.stalled:
                return
            st.raw.append(chunk)
            if sum(len(c) for c in st.raw) > MAX_STREAM_CHARS:
                st.stalled = True
                self._cancel(st)
                return
            now = self._clock()
            if now - st.last >= self._interval:
                self._flush(key, msg_id, st, final=False)
            elif st.timer is None:
                st.timer = threading.Timer(
                    st.last + self._interval - now, self._due,
                    args=(key, msg_id))
                st.timer.daemon = True
                st.timer.start()

    def finish(self, key: str, msg_id: str) -> None:
        """Message ``msg_id`` is complete: send what is left, ``done``."""
        with self._lock:
            st = self._open.pop((key, msg_id), None)
            if st is None:
                return
            self._cancel(st)
            if not st.stalled:
                self._flush(key, msg_id, st, final=True)

    def finish_all(self, key: str) -> None:
        """The turn ended: finish every message still open for ``key``."""
        with self._lock:
            ids = [mid for (k, mid) in self._open if k == key]
        for mid in ids:
            self.finish(key, mid)

    # -- internals ---------------------------------------------------------

    def _due(self, key: str, msg_id: str) -> None:
        with self._lock:
            st = self._open.get((key, msg_id))
            if st is None:
                return
            st.timer = None
            if not st.stalled:
                self._flush(key, msg_id, st, final=False)

    @staticmethod
    def _cancel(st: _Open) -> None:
        if st.timer is not None:
            st.timer.cancel()
            st.timer = None

    def _flush(self, key: str, msg_id: str, st: _Open, *,
               final: bool) -> None:
        """Send the safe text past what was sent; caller holds the lock."""
        st.last = self._clock()
        safe = safe_prefix("".join(st.raw), final)
        if not safe.startswith(st.sent):
            # Redaction rewrote text already on the client: stop here;
            # the final record replaces the bubble.
            st.stalled = True
            self._cancel(st)
            return
        chunk = safe[len(st.sent):]
        if not chunk and not final:
            return
        st.sent = safe
        st.seq += 1
        try:
            self._sink(key, protocol.delta_event(
                key, st.machine, msg_id, st.kind, chunk, st.seq, done=final))
        except Exception:
            pass  # a preview never breaks the channel feeding it
