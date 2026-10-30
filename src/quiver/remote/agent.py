"""Satellite agent — ``swe remote agent``.

Same tail/inject machinery as the hub, no listener: a persistent
outbound connection to the hub over Tailscale. The agent registers its
machine metadata, pushes catalog snapshots and tailed transcript events
upstream, and long-polls a per-machine command journal downstream — the
command SSE stream doubles as the presence signal, so a sleeping MacBook
simply drops offline on the hub until it wakes and replays the queue.
"""

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import attachments, catalog as catalog_mod, slash
from quiver.remote import inject, journal, protocol, sendq, supervise, tail
from quiver.remote import fork as fork_mod, usage as usage_mod
from quiver.remote.detach import PendingDetaches

AGENT_FILE = "agent.json"          # satellite's stored hub credential
PUSH_BATCH = 50                    # most events per /v1/agent/events POST
RECONNECT_S = 5.0
CATALOG_PUSH_S = 10.0
# The hub refuses JSON bodies over 4 MiB (hub._MAX_BODY); a catalog push
# stays under it with room to spare.
CATALOG_BODY_MAX = (4 << 20) - (64 << 10)
# An events POST is held to the same size: a batch is cut by encoded
# bytes as well as count, and an event too big to go alone is clipped.
EVENTS_BODY_MAX = CATALOG_BODY_MAX
# Commands that run on the session's queue, in order, off the stream.
_SESSION_QUEUED_OPS = frozenset({"message", "attachment"})
OUTBOX_KEY = "_outbox"             # journal key for unsent uplink events
# Same as the hub's: a ``queue``-mode send polls a fresh status on this
# cadence and fails honestly once a turn outlives the cap.
QUEUE_POLL_S = 2.0
QUEUE_WAIT_MAX_S = 1800
# How long a queue-mode send that reached idle waits for an attachment
# pull it names to finish downloading.
PULL_WAIT_MAX_S = 300
# The in-memory uplink buffer keeps no more than the outbox journal's
# ring does across a restart; while the hub is down the oldest go first.
OUTBOX_MAX = journal.RING_MAX


def agent_file_for(home: Path | None) -> Path:
    return remote_dir_for(home) / AGENT_FILE


def load_agent(home: Path | None = None) -> dict | None:
    try:
        return json.loads(agent_file_for(home).read_text())
    except (OSError, ValueError):
        return None


def save_agent(home: Path | None, conf: dict) -> Path:
    path = agent_file_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(conf, indent=1), private=True)
    # private=True floors a new file at 0600 but keeps an existing mode,
    # so a restored or hand-written file must be tightened on every save.
    os.chmod(path, 0o600)
    return path


# Set once an https hub's certificate fails against Python's default
# store: python.org builds on macOS ship no CA certificates, so a hub
# behind ``tailscale serve`` (a real Let's Encrypt cert) is unverifiable
# until the OS bundle is loaded. Verification is never turned off.
_fallback_ctx: "ssl.SSLContext | None" = None


def _urlopen(req: urllib.request.Request, timeout: float | None):
    """``urlopen`` that retries a certificate failure once against the
    OS CA bundle, then keeps using that bundle for every later call."""
    global _fallback_ctx
    if _fallback_ctx is not None:
        return urllib.request.urlopen(req, timeout=timeout,
                                      context=_fallback_ctx)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        if not isinstance(getattr(exc, "reason", None),
                          ssl.SSLCertVerificationError):
            raise
        from quiver.harness.rate_limits import _verified_context

        ctx = _verified_context()
        if ctx is None:
            raise
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        _fallback_ctx = ctx
        return resp


def _encoded_len(event: dict) -> int:
    return len(json.dumps(event, ensure_ascii=False).encode("utf-8"))


def _clip_strings(value, cap: int):
    if isinstance(value, str):
        return value if len(value) <= cap else value[:cap] + "… (truncated)"
    if isinstance(value, dict):
        return {k: _clip_strings(v, cap) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_strings(v, cap) for v in value]
    return value


def _clip_event(event: dict, limit: int) -> dict | None:
    """``event`` with its long strings cut until it encodes within
    ``limit`` bytes, or None when even 1 KiB strings don't fit it."""
    cap = limit // 2
    while cap >= 1024:
        clipped = _clip_strings(event, cap)
        if _encoded_len(clipped) <= limit:
            return clipped
        cap //= 4
    return None


def _headers(credential: str, ctype: str | None = "application/json") -> dict:
    h = {"Authorization": f"Bearer {credential}"}
    if ctype:
        h["Content-Type"] = ctype
    return h


def _post(hub: str, path: str, credential: str, payload: dict,
          timeout: float = 10.0) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        hub.rstrip("/") + path, data=body,
        headers=_headers(credential), method="POST",
    )
    try:
        with _urlopen(req, timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception:
        return 0, {}


def _get_bytes(hub: str, path: str, credential: str,
               timeout: float = 30.0) -> bytes | None:
    req = urllib.request.Request(
        hub.rstrip("/") + path, headers=_headers(credential, None),
    )
    try:
        with _urlopen(req, timeout) as resp:
            return resp.read()
    except Exception:
        return None


def pair_agent(hub: str, token: str, name: str,
               home: Path | None = None) -> dict | None:
    """Exchange a one-time pair token for this satellite's credential.

    The claimed ``role`` lands on the device row; the hub binds it to
    this machine at first ``/v1/agent/register`` and refuses the agent
    routes to anything else.
    """
    status, payload = _post(
        hub, "/v1/pair", "",  # no auth needed for /v1/pair
        {"token": token, "name": name or "satellite",
         "role": "satellite"},
    )
    if status != 200 or not payload.get("credential"):
        return None
    conf = {
        "hub": hub.rstrip("/"),
        "credential": payload["credential"],
        "device_id": payload.get("device_id"),
    }
    save_agent(home, conf)
    return conf


class RemoteAgent:
    """The satellite daemon: tails local sessions, executes hub commands."""

    def __init__(
        self,
        hub: str,
        credential: str,
        home: Path | str | None = None,
    ) -> None:
        self.home = Path(home) if home is not None else None
        self.hub = hub.rstrip("/")
        self.cred = credential
        self.machine = catalog_mod.local_machine(self.home)
        self.machine_id = self.machine["id"]
        self.catalog = catalog_mod.Catalog(self.home, self.machine_id)
        # Last "/" menu payload the hub accepted; see _push_catalog.
        self._slash_digest: str | None = None
        self._slash_skipped: str | None = None
        # Row count last trimmed to fit a push, so the note prints once.
        self._rows_trimmed: int | None = None
        # Set after a printed 403 register refusal, so the loop retries
        # without re-printing; cleared on the next accepted register.
        self._register_denied: str | None = None
        self.outbox: "list[dict]" = []
        self._outbox_lock = threading.Lock()
        self._stop = threading.Event()
        # Commands dispatch on the stream thread except ``message``,
        # which can run minutes (headless claude) or wait out a turn
        # (``queue`` mode), and attachment pulls: those go through
        # ``_sendq``, one worker per session like the hub's, so a queued
        # interrupt/detach isn't stuck behind a run and one session's
        # turn doesn't hold every other session's messages.
        self._sendq = sendq.SendQueue()
        # Attachment pulls not finished yet, by (session, ref): a
        # queue-mode send waits on its own lane, so it can't count on
        # the session lane's order to find its file staged.
        self._pulls: dict[tuple[str, str], threading.Event] = {}
        self._pulls_lock = threading.Lock()
        # ``detach`` with ``mode: when_idle``, waiting out a turn here.
        self.detaches = PendingDetaches(self._stop)
        # Fresh per-session status for those waits; tests stub it.
        from quiver.sessions.status import session_statuses
        self.status_of = session_statuses
        # Two cursors: ``_last_cmd_seq`` dedups frames seen on the wire;
        # ``_done_cmd_seq`` is the persisted *completed* floor the
        # ``?last=`` replay starts from — a crash mid-dispatch replays
        # the command instead of skipping it. ``_done_set`` is the
        # persisted out-of-order completions above that floor, so a
        # replayed command that already finished is skipped, not run
        # twice.
        self._last_cmd_seq, self._done_set = self._load_cmd_cursor()
        self._done_cmd_seq = self._last_cmd_seq
        # The stream thread, the work loop and fork threads all finish
        # commands; the cursor advance must not interleave.
        self._done_lock = threading.Lock()
        # Bumped when the hub's command journal starts over under our
        # cursor (``_reset_cmd_cursor``); see ``_mark_done``.
        self._cmd_epoch = 0
        # The ``?last=`` the current command stream sent.
        self._stream_cursor = self._done_cmd_seq
        from quiver.sessions.status import session_statuses
        # Same status re-probe as the hub: the satellite's tailer emits
        # session.status itself, and its catalog push carries the
        # fresher of the two.
        self.tail_manager = tail.TailManager(
            emit=self._capture, machine_id=self.machine_id, home=self.home,
            status_of=session_statuses,
            on_status=self._on_probed_status,
        )
        # Requeue uplink events the hub never acknowledged — the
        # transcript cursor is saved on read, so an in-memory buffer
        # would lose them across a restart.
        self._outbox_ack = self._load_outbox_ack()
        self._outbox = journal.Journal(self.home)
        events, _truncated = self._outbox.read_since(
            OUTBOX_KEY, self._outbox_ack)
        self.outbox = list(events)[-OUTBOX_MAX:]

    def _on_probed_status(self, key: str, status: str, at: float) -> None:
        """Same as the hub's: patch the catalog row, and let a pending
        ``when_idle`` detach act once the turn is over."""
        self.catalog.note_status(key, status, at)
        if status != "active":
            self.detaches.nudge(key)

    def _cmd_cursor_file(self) -> Path:
        return agent_file_for(self.home).parent / "agent-cursor.json"

    def _load_cmd_cursor(self) -> tuple[int, set[int]]:
        """(Last *completed* seq, out-of-order done seqs); zeroed for a
        new or re-paired hub."""
        path = self._cmd_cursor_file()
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get("hub") == self.hub:
                done = {
                    s for s in (data.get("done") or [])
                    if isinstance(s, int) and not isinstance(s, bool)
                }
                return int(data.get("seq") or 0), done
            return 0, set()  # paired to a different hub — replay its queue
        except (OSError, ValueError):
            pass
        # Pre-migration cursor was a bare int for whatever hub was
        # paired then; honor it once rather than replay the backlog.
        legacy = path.with_name("agent-cursor")
        try:
            return int(legacy.read_text().strip()), set()
        except (OSError, ValueError):
            return 0, set()

    def _save_cmd_cursor(self) -> None:
        path = self._cmd_cursor_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic: a crash mid-write left a torn file that loaded as
            # no cursor, so the whole backlog replayed and re-ran.
            atomic_write_text(path, json.dumps({
                "hub": self.hub,
                "seq": self._done_cmd_seq,
                # Done seqs above the contiguous floor — without them a
                # restart replays and re-runs a command that already
                # finished. Every entry sits above the floor by
                # construction (_mark_done drops them as it climbs),
                # and the hub's backlog cap bounds how many can pile
                # up while one seq below them still runs — evicting
                # any would re-run that command, so the whole set
                # persists.
                "done": sorted(self._done_set),
            }))
        except OSError:
            pass

    def _outbox_ack_file(self) -> Path:
        return agent_file_for(self.home).parent / "agent-outbox-ack"

    def _load_outbox_ack(self) -> int:
        try:
            return int(self._outbox_ack_file().read_text().strip())
        except (OSError, ValueError):
            return 0

    def _save_outbox_ack(self) -> None:
        try:
            # Atomic: a torn file reads as ack 0 and resends the outbox.
            atomic_write_text(self._outbox_ack_file(), str(self._outbox_ack))
        except OSError:
            pass

    def _mark_done(self, seq: int | None, epoch: int | None = None) -> None:
        """Advance the completed cursor to the highest contiguous seq.

        ``epoch`` is ``_cmd_epoch`` when the command arrived: a command
        from before a journal reset finishing late must not mark the
        new journal's seq of the same number done.
        """
        if not isinstance(seq, int):
            return
        with self._done_lock:
            if epoch is not None and epoch != self._cmd_epoch:
                return
            self._done_set.add(seq)
            while self._done_cmd_seq + 1 in self._done_set:
                self._done_cmd_seq += 1
                self._done_set.discard(self._done_cmd_seq)
            self._save_cmd_cursor()

    # -- upstream ----------------------------------------------------------

    def _capture(self, key: str, event: dict) -> dict:
        """TailManager's emit: buffer for the uplink thread.

        Events are journaled before they're buffered — the transcript
        cursor is committed on read, so anything unacked must survive a
        restart or transcript output is lost while the hub is down.
        """
        stamped = self._outbox.append(OUTBOX_KEY, event)
        with self._outbox_lock:
            # Buffer the stamped copy: the seq is what the ack file
            # records once the hub accepts the batch.
            self.outbox.append(stamped or event)
            self._trim_outbox()
        return stamped or event

    def _trim_outbox(self) -> None:
        """Drop the oldest buffered events past ``OUTBOX_MAX``; the
        caller holds ``_outbox_lock``. The next accepted batch acks past
        them, so they are gone for good, as the ring would drop them."""
        over = len(self.outbox) - OUTBOX_MAX
        if over > 0:
            del self.outbox[:over]

    def _take_batch(self) -> tuple[list[dict], list[dict]]:
        """Pop the next POST off the outbox: (events taken, events to
        send). At most ``PUSH_BATCH`` events and ``EVENTS_BODY_MAX``
        encoded bytes go at once. An event over that alone goes
        clipped, or not at all when clipping can't fit it, so one huge
        tool result can't get a 400 on every retry and wedge the
        uplink."""
        taken: list[dict] = []
        batch: list[dict] = []
        size = 0
        with self._outbox_lock:
            for orig in self.outbox:
                if len(batch) >= PUSH_BATCH:
                    break
                ev, n = orig, _encoded_len(orig) + 1
                if n > EVENTS_BODY_MAX:
                    ev = _clip_event(orig, EVENTS_BODY_MAX - 1)
                    if ev is None:
                        taken.append(orig)  # dropped, acked with the rest
                        continue
                    n = _encoded_len(ev) + 1
                if batch and size + n > EVENTS_BODY_MAX:
                    break
                taken.append(orig)
                batch.append(ev)
                size += n
            del self.outbox[:len(taken)]
        return taken, batch

    def _push_events(self) -> bool:
        taken, batch = self._take_batch()
        if not taken:
            return True
        if batch:
            status, _payload = _post(
                self.hub, "/v1/agent/events", self.cred,
                {"machine_id": self.machine_id, "events": batch},
            )
            if status != 200:
                with self._outbox_lock:
                    self.outbox[:0] = taken
                    self._trim_outbox()
                return False
        seqs = [ev.get("seq") for ev in taken]
        seq = max((s for s in seqs if isinstance(s, int)), default=None)
        if seq is not None and seq > self._outbox_ack:
            self._outbox_ack = seq
            self._save_outbox_ack()
        return True

    def _push_catalog(self) -> bool:
        # Launch adoption runs here too: `launch` marks a pending file
        # and the new session must claim its tmux pane before the push
        # reports it, or it reaches the hub still `observed` — writable
        # nowhere.
        rows = self.catalog.refresh()
        adopted = supervise.adopt_pending(self.home, self.catalog.tail_rows())
        if adopted:
            rows = self.catalog.refresh()
        for key in adopted:
            # A launch or fork starts with its first turn: its tailer
            # (created on the next sync) starts hot and status-probed.
            self.tail_manager.wake(key)
            # Names the key a phone's launch became; the hub maps it to
            # the hub-scoped key and answers GET /v1/launches with it.
            lid = (supervise.meta_for(self.home, key) or {}).get("launch_id")
            if lid:
                self._capture(key, protocol.event(
                    "session.launched", key, self.machine_id,
                    {"launch_id": lid},
                ))
        body = {"machine_id": self.machine_id,
                "rows": self._fit_rows(rows)}
        # The "/" menu's custom commands live on this machine's disk; send
        # them only when they changed, so the 10 s push stays small.
        try:
            commands = slash.satellite_payload(rows, self.home)
        except Exception:
            commands = None
        digest = slash.payload_digest(commands) if commands else None
        if digest and digest != self._slash_digest:
            body["slash"] = commands
            # Never let the menu cost the rows: a body the hub would
            # refuse goes without it.
            if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) \
                    > CATALOG_BODY_MAX:
                del body["slash"]
                if digest != self._slash_skipped:
                    self._slash_skipped = digest
                    print("swe remote agent: slash command list too large "
                          "for the catalog push; sending rows without it",
                          file=sys.stderr)
        status, reply = _post(self.hub, "/v1/agent/catalog", self.cred, body)
        # An older hub ignores the field and doesn't echo it: keep sending
        # until one says it stored the list.
        if status == 200 and "slash" in body and \
                isinstance(reply, dict) and reply.get("slash"):
            self._slash_digest = digest
        if self._refresh_launchable():
            self._register()
        return status == 200

    def _fit_rows(self, rows: list[dict]) -> list[dict]:
        """The newest rows whose push fits under ``CATALOG_BODY_MAX``.

        A machine with thousands of sessions can list more than the hub
        takes in one body; refused whole, the push failed every 10 s
        and the hub never heard of a new session. Rows come newest
        first, so the oldest are the ones left out.
        """
        base = len(json.dumps({"machine_id": self.machine_id, "rows": []},
                              ensure_ascii=False).encode("utf-8"))
        total = base
        for n, row in enumerate(rows):
            # _post's json.dumps puts ", " between rows.
            total += len(json.dumps(row, ensure_ascii=False)
                         .encode("utf-8")) + (2 if n else 0)
            if total > CATALOG_BODY_MAX:
                if self._rows_trimmed != len(rows):
                    self._rows_trimmed = len(rows)
                    print(f"swe remote agent: {len(rows)} sessions are too "
                          f"many for one catalog push; sending the newest "
                          f"{n}", file=sys.stderr)
                return rows[:n]
        self._rows_trimmed = None
        return rows

    def _push_usage(self) -> bool:
        body = usage_mod.snapshot()
        status, _ = _post(
            self.hub, "/v1/agent/usage", self.cred,
            {"machine_id": self.machine_id, **body},
        )
        return status == 200

    def _refresh_launchable(self) -> bool:
        """Re-read what this machine can launch; True when it changed, so
        a Provider visibility change reaches the hub without a restart."""
        launchable = catalog_mod.launchable_harnesses()
        fields = catalog_mod.launch_fields(launchable, self.home)
        if all(self.machine.get(k) == v for k, v in fields.items()):
            return False
        self.machine.update(fields)
        return True

    def _register(self) -> bool:
        status, reply = _post(
            self.hub, "/v1/agent/register", self.cred,
            {"machine": self.machine},
        )
        if status == 403 and self._register_denied is None:
            # A permanent refusal — pre-roles satellites pair as phones
            # and the binding gate rejects them forever, so explain
            # once instead of letting the retry loop look flaky.
            # The hub answers {"error": {"code", "message"}}.
            err = reply.get("error") if isinstance(reply, dict) else None
            detail = str(err.get("message") or "") \
                if isinstance(err, dict) else ""
            self._register_denied = detail or "forbidden"
            print(
                "swe remote agent: the hub refused this credential "
                f"(403 {self._register_denied}); re-pair this machine: "
                "run `swe remote pair` on the hub, then "
                "`swe remote agent --hub=<url> --token=<token>` here",
                file=sys.stderr,
            )
        if status == 200:
            self._register_denied = None
        return status == 200

    # -- downlink ----------------------------------------------------------

    def _command_stream(self) -> None:
        """GET /v1/agent/commands as an SSE stream; dispatch each command."""
        # The cursor this connection asks from: a journal.truncated
        # reply is judged against it, not against a floor that commands
        # finishing meanwhile moved on.
        self._stream_cursor = self._done_cmd_seq
        url = (
            f"{self.hub}/v1/agent/commands"
            f"?machine={self.machine_id}&last={self._stream_cursor}"
        )
        req = urllib.request.Request(url, headers=_headers(self.cred, None))
        try:
            resp = _urlopen(req, 90)
        except Exception:
            time.sleep(RECONNECT_S)
            return
        data_lines: list[bytes] = []
        try:
            with resp:
                for raw in resp:
                    if self._stop.is_set():
                        break
                    line = raw.rstrip(b"\n")
                    if line == b"":
                        if data_lines:
                            frame = b"\n".join(data_lines)
                            data_lines = []
                            if self._handle_frame(frame) == "reset":
                                # Reconnect from the new cursor, so the
                                # hub's backlog floor follows it down.
                                break
                        continue
                    if line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip())
        except Exception:
            pass
        if not self._stop.is_set():
            time.sleep(1)

    def _handle_frame(self, blob: bytes) -> str | None:
        """Act on one SSE frame; "reset" when it moved the command
        cursor back (the stream should reconnect from there)."""
        try:
            ev = json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(ev, dict):
            return None
        if ev.get("type") == "journal.truncated":
            return self._reset_cmd_cursor(ev.get("seq"))
        if ev.get("type") != "command":
            return None
        epoch = self._cmd_epoch
        seq = ev.get("seq")
        if isinstance(seq, int):
            if seq in self._done_set:
                # Finished before a restart, above the contiguous
                # floor the replay started from: seen it, don't run
                # it again. The wire cursor still moves.
                self._last_cmd_seq = max(self._last_cmd_seq, seq)
                return None
            if seq <= self._last_cmd_seq:
                return None
            self._last_cmd_seq = seq
        op = (ev.get("data") or {}).get("op")
        if op in _SESSION_QUEUED_OPS:
            # Headless sends block for the whole turn and an attachment
            # pull is a download: queue them so a later interrupt/detach
            # isn't stuck behind them. A session's pulls and steers stay
            # in order; a queue-mode send waits for idle on its own lane
            # and then for the pulls it names (``_submit_queued``).
            # attach stays on the stream: queued behind a turn it could
            # land after a later detach and undo it. One still queued at
            # shutdown is left undone, so the replay runs it.
            self._submit_queued(seq, ev, epoch)
            return None
        if op in ("fork", "launch"):
            # Booting a new session's pane can take 30 s: run it beside
            # the stream so an interrupt or detach behind it goes at
            # once. The cursor still waits for it, so a crash replays it.
            threading.Thread(
                target=self._dispatch_then_done, args=(seq, ev, epoch),
                name=f"agent-{op}-{seq}", daemon=True,
            ).start()
            return None
        self._dispatch(ev)
        self._mark_done(seq, epoch)
        return None

    def _dispatch_then_done(self, seq, ev: dict,
                            epoch: int | None = None) -> None:
        try:
            self._dispatch(ev)
        finally:
            self._mark_done(seq, epoch)

    def _submit_queued(self, seq, ev: dict, epoch: int) -> None:
        """Put a ``message`` or ``attachment`` command on its lane.

        Attachment pulls and steer sends share the session's lane, in
        order. A ``queue``-mode send gets a lane of its own, as on the
        hub, and waits there until the session is idle, so it never
        holds a steer or the pull a steer needs. It first waits for the
        pulls it names, then for idle. A command the agent stops before
        running is
        never marked done, so the hub replays it after the restart.
        """
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        key = str(ev.get("session") or "")
        op = str(data.get("op") or "")
        if op == "attachment":
            pull = (key, str(data.get("ref") or ""))
            done = threading.Event()
            with self._pulls_lock:
                self._pulls[pull] = done

            def pull_job() -> None:
                try:
                    self._dispatch_then_done(seq, ev, epoch)
                finally:
                    with self._pulls_lock:
                        if self._pulls.get(pull) is done:
                            del self._pulls[pull]
                    done.set()

            self._sendq.submit(key, pull_job, lambda: None)
            return
        if str(data.get("mode") or "queue") != "queue":
            self._sendq.submit(
                key, lambda: self._dispatch_then_done(seq, ev, epoch),
                lambda: None)
            return

        def job() -> None:
            # Pulls first: they run on the session lane, which never
            # waits on a turn, so they end soon. The idle check then
            # sits right before the send; a steer that started a turn
            # during a download is waited out, not typed into.
            if not self._await_pulls(key, data):
                return
            waited = self._wait_idle(key, data)
            if waited == "stopped":
                return
            if waited == "busy":
                self._report_message(
                    key, data, False,
                    "session stayed busy for 30 min; message not sent")
                self._mark_done(seq, epoch)
                return
            self._dispatch_then_done(seq, ev, epoch)

        self._sendq.submit(f"{key}\x00queue", job, lambda: None)

    def _live_status(self, key: str, data: dict) -> str:
        """The session's status now: a fresh probe, else the tailer's.

        The status in the queued command is stale: the session may have
        gone idle or errored while we were offline.
        """
        fallback = (self.tail_manager.statuses.get(key)
                    or str(data.get("status") or "done"))
        session = self.catalog.session_for(key)
        if session is None:
            return fallback
        try:
            # The catalog's activity time can be minutes old for a turn
            # that just started; the probe would call it "interrupted".
            session = self.tail_manager.fresh_session(key, session)
            got = self.status_of([session])
        except Exception:
            return fallback
        return str(got[0]) if got and got[0] else fallback

    def _wait_idle(self, key: str, data: dict) -> str:
        """Wait for ``key``'s turn to end: ``idle``, ``busy`` or ``stopped``."""
        deadline = time.monotonic() + QUEUE_WAIT_MAX_S
        while self._live_status(key, data) == "active":
            if self.catalog.session_for(key) is None:
                # Gone from this machine: stop waiting, and let the
                # dispatch report the unknown session.
                return "idle"
            if time.monotonic() >= deadline:
                return "busy"
            if self._stop.wait(QUEUE_POLL_S):
                return "stopped"
        return "stopped" if self._stop.is_set() else "idle"

    def _await_pulls(self, key: str, data: dict) -> bool:
        """Wait for the attachment pulls ``data`` names; False if the
        agent stopped meanwhile. A pull that never finishes leaves the
        send to report its attachment unknown."""
        deadline = time.monotonic() + PULL_WAIT_MAX_S
        for ref in data.get("attachments") or []:
            with self._pulls_lock:
                done = self._pulls.get((key, str(ref)))
            while done is not None and not done.is_set():
                if self._stop.is_set():
                    return False
                if time.monotonic() >= deadline:
                    break
                done.wait(min(QUEUE_POLL_S, 0.5))
        return not self._stop.is_set()

    def _report_message(
        self, key: str, data: dict, ok: bool, note: str
    ) -> None:
        fields = {"command_id": str(data.get("command_id") or ""),
                  "op": "message", "ok": ok, "note": note,
                  "degraded": False}
        if data.get("client_id"):
            fields["client_id"] = str(data["client_id"])
        self._buffer_result(protocol.event(
            "command.result", key, self.machine_id, fields))

    def _reset_cmd_cursor(self, seq) -> str | None:
        """The hub answered ``journal.truncated``: start over if it says
        our cursor is past its command journal (wiped, or a hub that
        lost its data). Its seqs restart below us, and every new
        command would be dropped as already seen.

        The cursor goes to 0, not to the head the hub names: every
        command in the new journal was queued after ours ended, so the
        reconnect replays them all rather than skipping those queued
        while we were away. Behind the ring the hub echoes the cursor
        this connection sent, and nothing changes: compared with that,
        not the live floor, which commands finishing since have moved."""
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return None
        with self._done_lock:
            if seq >= self._stream_cursor:
                return None
            self._cmd_epoch += 1
            self._done_cmd_seq = 0
            self._last_cmd_seq = 0
            # Done seqs above the old floor name the old journal's
            # commands, not the new one's.
            self._done_set.clear()
            self._save_cmd_cursor()
        return "reset"

    def _dispatch(self, cmd: dict) -> None:
        """Execute one command envelope; report back as command.result."""
        data = cmd.get("data") if isinstance(cmd.get("data"), dict) else {}
        op = str(data.get("op") or "")
        key = str(cmd.get("session") or "")
        command_id = str(data.get("command_id") or "")
        ok, note, degraded = self._execute(key, op, data)
        fields = {"command_id": command_id, "op": op, "ok": ok,
                  "note": note, "degraded": degraded}
        # Echo request_id back so a queued approval card on the phone
        # can tell which prompt this result belongs to.
        if data.get("request_id"):
            fields["request_id"] = str(data["request_id"])
        if data.get("client_id"):
            fields["client_id"] = str(data["client_id"])
        if data.get("launch_id"):
            fields["launch_id"] = str(data["launch_id"])
        result = protocol.event(
            "command.result", key, self.machine_id, fields,
        )
        self._buffer_result(result)

    def _buffer_result(self, event: dict) -> None:
        """Journal + buffer a command.result the same way _capture does."""
        self._capture(str(event.get("session") or ""), event)

    def _execute(
        self, key: str, op: str, data: dict
    ) -> tuple[bool, str, bool]:
        session = self.catalog.session_for(key)
        try:
            if op == "launch":
                harness = str(data.get("harness") or "")
                cwd = str(data.get("cwd") or "")
                # Same pre-launch snapshot the hub takes: any session
                # that exists must not be adopted.
                known = catalog_mod.known_session_ids(
                    self.catalog.tail_rows(), harness, cwd)
                binary = catalog_mod.launch_command(harness)
                if binary is None:
                    return False, (
                        f"{harness} can't start here: archived, switched "
                        f"off in Provider visibility, or not installed"
                    ), False
                ok, note, _ = supervise.launch_new(
                    self.home, harness, cwd,
                    str(data.get("prompt") or ""), known_ids=known,
                    launch_id=str(data.get("launch_id") or ""),
                    binary=binary,
                    model=str(data.get("model") or "") or None,
                    effort=str(data.get("effort") or "") or None,
                )
                return ok, note, False
            if op == "attachment":
                ref = str(data.get("ref") or "")
                # The hub staged the upload under the hub-scoped key —
                # that's what the pull URL carries, not our local key.
                hub_key = str(data.get("hub_key") or key)
                blob = _get_bytes(
                    self.hub,
                    # Both quoted whole: a key can hold "/" or "#",
                    # and the hub unquotes each part.
                    f"/v1/agent/attachments/{quote(hub_key, safe='')}/"
                    f"{quote(ref, safe='')}", self.cred,
                )
                if blob is None:
                    return False, "attachment fetch failed", False
                # Keep the hub's ref name — the message op that follows
                # resolves attachments by it.
                payload, err = attachments.place(
                    self.home, key, ref, blob
                )
                return payload is not None, (err or "staged"), False
            if session is None:
                return False, "unknown session on this machine", False
            if op == "fork":
                # Tracked like launch: the result and the adopted
                # session both carry the hub's launch id.
                ok, note = fork_mod.start(
                    self.home, session, self.catalog.tail_rows(),
                    str(data.get("prompt") or ""),
                    str(data.get("launch_id") or ""),
                    model=str(data.get("model") or "") or None,
                    effort=str(data.get("effort") or "") or None,
                )
                return ok, note, False
            if op == "attach":
                # Same as the hub: attaching again cancels a when_idle
                # detach (one already running finishes first).
                undone = self.detaches.cancel(key, "cancelled by attach")
                ok, note = supervise.attach(
                    self.home, key, session,
                    last_append_at=self.tail_manager.last_append_at(key),
                    status=str(data.get("status") or ""),
                    mode=str(data.get("mode") or "auto"),
                    model=str(data.get("model") or "") or None,
                    effort=str(data.get("effort") or "") or None,
                )
                if ok:
                    self.tail_manager.wake(key)
                if ok and undone == "cancelled":
                    note += "; the pending detach is cancelled"
                return ok, note, False
            if op == "tail":
                # The hub is asking us to stream an older session a
                # phone just opened (past the background tail cap).
                self.tail_manager.ensure(
                    key, session, str(data.get("status") or ""))
                return True, "tailing", False
            # Same wake-on-write as the hub: the satellite tails its
            # own sessions, so a command it runs here starts the hot
            # window here.
            injector = self.tail_manager.waking(key, inject.injector_for(
                self.home, key, supervise.meta_for(self.home, key),
                emit=self._capture, machine=self.machine_id,
            ))
            if op == "message":
                # Resolve attachment refs to the paths the attachment
                # op staged locally, same as the hub's send path.
                paths = []
                for ref in data.get("attachments") or []:
                    path = attachments.resolve(self.home, key, str(ref))
                    if path is None:
                        return False, f"unknown attachment {ref}", False
                    paths.append(path)
                live = self._live_status(key, data)
                result = injector.send(
                    session,
                    {"text": data.get("text") or "",
                     "mode": data.get("mode") or "queue",
                     "attachments": paths},
                    live,
                )
                return result.ok, result.note, result.degraded
            if op == "approval":
                result = injector.approval(
                    session,
                    str(data.get("request_id") or ""),
                    str(data.get("decision") or ""),
                )
                return result.ok, result.note, result.degraded
            if op == "control":
                ctl = str(data.get("ctl") or "")
                mode = str(data.get("mode") or "now")
                if ctl == "kill" or (ctl == "detach" and mode != "when_idle"):
                    self.detaches.cancel(key, f"replaced by {ctl} {mode}")
                if ctl == "detach" and mode == "when_idle":
                    return self._detach_when_idle(key, session)
                if ctl == "detach" and mode == "keep":
                    ok, note = supervise.keep(self.home, key)
                    return ok, note, False
                result = injector.control(
                    session, ctl, str(data.get("value") or ""),
                )
                if result.ok and ctl in {"kill", "detach"}:
                    # As on the hub: the session is no longer ours, so
                    # its staged files go (a kept one returned above).
                    attachments.drop_session(self.home, key)
                return result.ok, result.note, result.degraded
            return False, f"unknown op {op}", False
        except Exception as exc:
            return False, str(exc), False

    def _detach_when_idle(
        self, key: str, session
    ) -> tuple[bool, str, bool]:
        """Queue a detach for when this machine's session goes idle.

        The command's own result says it is queued; the detach itself
        reports a second ``command.result`` (``op: detach``), and the
        catalog push carries the ownership change to the hub.
        """
        if supervise.meta_for(self.home, key) is None:
            return False, "session is not attached on this machine", False

        def status() -> str:
            current = self.catalog.session_for(key) or session
            got = self.status_of([current])
            return str(got[0]) if got else ""

        def fire() -> tuple[bool, str]:
            if supervise.meta_for(self.home, key) is None:
                return False, "session was already detached"
            current = self.catalog.session_for(key) or session
            result = inject.injector_for(
                self.home, key, supervise.meta_for(self.home, key),
                emit=self._capture, machine=self.machine_id,
            ).control(current, "detach", "")
            if result.ok:
                attachments.drop_session(self.home, key)
            return result.ok, result.note

        def report(ok: bool, note: str, cancelled: bool) -> None:
            fields = {"command_id": "", "op": "detach",
                      "mode": "when_idle", "ok": ok, "note": note}
            if cancelled:
                fields["cancelled"] = True
            self._buffer_result(protocol.event(
                "command.result", key, self.machine_id, fields))

        if not self.detaches.schedule(
                key, status=status, fire=fire, report=report):
            return True, "a detach is already waiting for this turn to end", False
        return True, "detaches when the current turn ends", False

    # -- loops -------------------------------------------------------------

    def _uplink_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._push_events()
            except Exception:
                pass
            self._stop.wait(0.5)

    def _catalog_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._push_catalog()
                rows, as_of = self.catalog.tail_snapshot()
                self.tail_manager.sync(rows, as_of=as_of)
                supervise.reap_kept(self.home, self.catalog.session_for)
            except Exception:
                pass
            self._stop.wait(CATALOG_PUSH_S)

    def _usage_loop(self) -> None:
        # A failed push (hub down, registry unreadable) retries on the
        # reconnect cadence rather than waiting out the whole interval.
        while not self._stop.is_set():
            try:
                ok = self._push_usage()
            except Exception:
                ok = False
            self._stop.wait(
                usage_mod.SATELLITE_PUSH_S if ok else RECONNECT_S * 12
            )

    def _tail_loop(self) -> None:
        sweeps = 0
        while not self._stop.is_set():
            try:
                self.tail_manager.tick()
            except Exception:
                pass
            sweeps += 1
            if sweeps >= 1200:  # ~5 min at a 250 ms tick, as on the hub
                sweeps = 0
                self._sweep()
            self.tail_manager.wait(0.25)

    def _sweep(self) -> None:
        """Delete files this satellite stopped needing: cursors of
        sessions idle past ``tail.STALE_FILE_AGE_S`` (the uplink outbox
        is its own and stays), and staged attachments past
        ``attachments.MAX_AGE_S``, as the hub's loop does."""
        try:
            self.tail_manager.sweep_stale(self._outbox, keep=(OUTBOX_KEY,))
        except Exception:
            pass
        try:
            attachments.sweep(self.home)
        except Exception:
            pass

    def _downlink_loop(self) -> None:
        while not self._stop.is_set():
            # Re-register on every reconnect: the first attempt can run
            # while the hub is down, and the stream itself only tells
            # the hub {id, online} — name/os/harnesses come from here.
            try:
                self._register()
            except Exception:
                pass
            try:
                self._command_stream()
            except Exception:
                self._stop.wait(RECONNECT_S)

    def run(self) -> int:
        supervise.reconcile_kept(self.home)
        self._register()
        self._push_catalog()
        threads = [
            threading.Thread(target=self._uplink_loop, daemon=True,
                             name="agent-uplink"),
            threading.Thread(target=self._catalog_loop, daemon=True,
                             name="agent-catalog"),
            threading.Thread(target=self._usage_loop, daemon=True,
                             name="agent-usage"),
            threading.Thread(target=self._tail_loop, daemon=True,
                             name="agent-tail"),
            threading.Thread(target=self._downlink_loop, daemon=True,
                             name="agent-downlink"),
        ]
        for t in threads:
            t.start()
        try:
            while True:
                time.sleep(0.5)
                if self._stop.is_set():
                    break
        except KeyboardInterrupt:
            pass
        self._stop.set()
        self._sendq.close()
        self.tail_manager.interrupt()
        self.detaches.close()
        for t in threads:
            t.join(timeout=2)
        return 0


def serve(hub: str, credential: str,
          home: Path | str | None = None) -> int:
    """Blocking entry point for ``swe remote agent``."""
    return RemoteAgent(hub, credential, home).run()
