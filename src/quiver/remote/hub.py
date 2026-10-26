"""The hub: REST + SSE over ``http.server``, device auth, event fan-out.

Stdlib-only by design — SSE is a held HTTP GET, so no WebSocket
dependency enters the tree. ``HubState`` is the reusable core: the
catalog refreshes sessions, the tail manager turns transcripts into
events, and one ``emit`` funnel writes every event to the journal and
the subscriber bus. The satellite agent builds the same core minus the
listener.

Auth: every ``/v1/*`` route except ``/v1/status`` and ``/v1/pair`` wants
``Authorization: Bearer <device credential>`` from ``devices.json``.
The approval POST alone also accepts a scoped, single-use ntfy action
token (``notify.ApprovalTokens``) so a notification never carries a
full-power credential.
"""

from __future__ import annotations

import email.message
import json
import os
import queue
import secrets
import signal
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import (
    attachments,
    catalog as catalog_mod,
    injected as injected_mod,
    dismissals,
    files as files_mod,
    inject,
    notify,
    pairing,
    protocol,
    search as search_mod,
    sessionmeta,
    sendq,
    slash,
    sshkeys,
    supervise,
    tail,
    tsid,
    usage as usage_mod,
)
from quiver.remote import fork as fork_mod
from quiver.remote.detach import PendingDetaches
from quiver.remote.journal import Journal
from quiver.webapp import (
    api as webapp_api,
    harnesses as webapp_harnesses,
    pages as webapp_pages,
    static as webapp_static,
)

DEFAULT_PORT = 8477
_MAX_BODY = 4 << 20  # 4 MB JSON cap; attachments go through their own route
_SSE_HEARTBEAT_S = 15.0
# A request that stalls mid-read (declared Content-Length, no bytes)
# can't hold a handler thread forever; SSE writes get the same cap,
# which is harmless — a healthy client's socket never blocks for 30 s.
_REQ_TIMEOUT_S = 30.0
# A response sent before the handler read the request body (a 401, 404,
# 409, 413 ...) first discards that body, or the next request on the
# keep-alive connection would be parsed out of its bytes. Up to this
# much, within this long, is read and dropped so the connection stays
# open; anything bigger, slower or unframed closes the connection.
_DRAIN_MAX = 1 << 20
_DRAIN_S = 2.0
_MAX_WORKERS = 64
# How often an open SSE stream re-checks that its device wasn't revoked.
_REVOCATION_CHECK_S = 5.0

# Peers that count as "this machine". Behind ``tailscale serve`` every
# request arrives from one of these, with the real client in headers.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

# Behind serve the owner lookup needs this node's tailnet IP, which a hub
# started at login can ask for before Tailscale is up; it retries on use
# this often until it settles.
_OWNER_RETRY_S = 30.0

# The owner's own devices skip pairing, so every page load asks who the
# caller is. ``tailscale whois`` is a subprocess; its answer per IP is
# kept this long (a miss for less, so a device that just joined the
# tailnet isn't locked out for long).
_WHOIS_TTL_S = 60.0
_WHOIS_MISS_TTL_S = 10.0

# Journal bucket for non-session events (machine.online, hub-level notify).
GLOBAL_KEY = "_global"

# ``queue``-mode sends poll a fresh session status on this cadence and
# give up after the cap — a turn outliving it fails the send honestly.
QUEUE_POLL_S = 2.0
QUEUE_WAIT_MAX_S = 1800
# Per-key in-flight sends (waiting plus running) get an honest 429
# rather than an unbounded backlog nobody reads.
MAX_SEND_BACKLOG = 20

# Refuse new commands for a satellite whose unacked queue is deeper
# than this — the journal trims at ~4k records and silently dropped
# commands are worse than an honest 429.
MAX_SAT_PENDING = 2000

# /v1/folders lists at most this many subfolders of one folder.
FOLDER_LIST_CAP = 500

# The unauthenticated pair routes (/v1/pair, /v1/pair/request) allow
# this many attempts per source IP per window.
_PAIR_ATTEMPT_MAX = 5
_PAIR_ATTEMPT_S = 600

# A launch the phone started stays answerable at /v1/launches/<id> this
# long; past it the tmux pane has long since been adopted or abandoned.
LAUNCH_TTL_S = 86400


class EventBus:
    """Fan-out to SSE subscriber queues, keyed by session or global."""

    def __init__(self) -> None:
        self._subs: list[tuple[str | None, "queue.Queue"]] = []
        self._lock = threading.Lock()

    def subscribe(self, key: str | None) -> "queue.Queue":
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._subs.append((key, q))
        return q

    def unsubscribe(self, q: "queue.Queue") -> None:
        with self._lock:
            self._subs = [(k, s) for k, s in self._subs if s is not q]

    def publish(self, key: str, event: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for want, q in subs:
            if want is None or want == key:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass  # a wedged client misses events, never blocks the hub


class HubState:
    """Everything the HTTP handlers close over; threads start here."""

    def __init__(self, home: Path | str | None = None) -> None:
        self.home = Path(home) if home is not None else None
        self.machine = catalog_mod.local_machine(self.home)
        self.machine_id = self.machine["id"]
        self.journal = Journal(self.home)
        self.bus = EventBus()
        self.catalog = catalog_mod.Catalog(self.home, self.machine_id)
        self.tail_manager = tail.TailManager(
            emit=self.emit,
            machine_id=self.machine_id,
            home=self.home,
            journal_has=self._journal_has,
            # Hot or growing sessions get their status re-probed between
            # catalog refreshes; the catalog keeps the fresher of the two.
            status_of=lambda sessions: self.status_of(sessions),
            on_status=self._on_probed_status,
        )
        # The web app's page data (/v1/app/*): its ledger opens on the
        # first request, so a hub nobody browses never scans.
        self.webapp = webapp_api.WebApp(self.home)
        self.started_at = time.time()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        # Satellite presence: command-stream subscribers are the online
        # signal — a sleeping MacBook just drops offline until it dials in.
        self._sat_subs: dict[str, int] = {}
        self._cmd_seq = 0
        self._cmd_lock = threading.Lock()
        # Last command-stream seq each satellite connected with — the
        # floor of what it has executed. queue_command refuses to grow a
        # backlog it would silently trim.
        self._sat_acked: dict[str, int] = {}
        # Message sends run here — a headless turn or a queue-until-idle
        # wait must not hold the phone's POST open.
        self.sendq = sendq.SendQueue()
        # queue-mode sends not yet handed to an injector, by session
        # and client_id: GET/POST /v1/sessions/{key}/queue read it.
        self.waiting = sendq.Waiting()
        # ``detach`` with ``mode: when_idle``, waiting out a turn.
        self.detaches = PendingDetaches(self._stop)
        # Fresh per-session status for queued sends; tests stub it.
        from quiver.sessions.status import session_statuses
        self.status_of = session_statuses
        # Tailscale identity lookup for pair requests; tests stub it.
        self.whois = tsid.whois
        self._whois_cache: dict[str, tuple[float, dict | None]] = {}
        self._whois_lock = threading.Lock()
        # One lookup in flight per IP: a page firing several requests at
        # once waits on the first instead of forking its own whois.
        self._whois_ip_locks: dict[str, threading.Lock] = {}
        # The hub owner's tailnet login — set by serve() from whois of
        # the bind address; "" means owner-identity approval is off.
        self.owner_login = ""
        # Set by configure() when that lookup could not run yet; owner()
        # calls it again, at most every _OWNER_RETRY_S.
        self.owner_retry = None
        self._owner_tried_at = 0.0
        # Unauthenticated pair-route rate limits: ip → timestamps, one
        # bucket per route so token tries and request floods don't
        # share a budget. One lock guards both.
        self._pair_req_times: dict[str, list[float]] = {}
        self._pair_ex_times: dict[str, list[float]] = {}
        self._pair_req_lock = threading.Lock()
        # Host values this hub answers to — the owner-identity approval
        # path requires one, and the unauthenticated routes answer only
        # these (filled by serve() from the bind host + whois); empty
        # disables both checks.
        self.allowed_hosts: set[str] = set()
        # The hub's reachable base — set by serve() before start() so
        # ntfy action buttons can point back at this host.
        self.base_url = ""
        # Set when the hub runs behind ``tailscale serve`` (``--public-url``
        # on a loopback bind): serve overwrites X-Forwarded-For/-Proto
        # with the real tailnet client, so loopback requests carrying
        # them are read as that client. Off, the headers are ignored.
        self.trust_proxy = False
        # session key -> request_id for every approval.requested that has
        # not yet been answered — local panes and satellites both flow
        # through emit(), so the inbox sees them uniformly.
        self.pending_approvals: dict[str, str] = {}
        # One-shot, minutes-lived bearer tokens the ntfy action buttons
        # send instead of a device credential (notify.ApprovalTokens).
        self.approval_tokens = notify.ApprovalTokens()
        # launch_id -> {machine, harness, cwd, state, key, note,
        # created_at} for every POST /v1/sessions. ``state`` goes pending
        # -> started (a ``session.launched`` event names the key) or
        # failed (the launch, or the satellite's command, said no).
        self.launches: dict[str, dict] = {}
        self._launch_lock = threading.Lock()

    def new_launch(self, machine: str, harness: str, cwd: str) -> str:
        now = time.time()
        launch_id = "l" + secrets.token_hex(6)
        with self._launch_lock:
            for lid in [k for k, v in self.launches.items()
                        if now - v["created_at"] > LAUNCH_TTL_S]:
                del self.launches[lid]
            self.launches[launch_id] = {
                "launch_id": launch_id, "machine": machine,
                "harness": harness, "cwd": cwd, "state": "pending",
                "key": "", "note": "", "created_at": now,
            }
        return launch_id

    def settle_launch(self, launch_id: str, **fields) -> None:
        with self._launch_lock:
            entry = self.launches.get(launch_id)
            # A launch settles once: a late failure note for one that
            # already started must not flip it back.
            if entry is not None and entry["state"] == "pending":
                entry.update(fields)

    def _settle_untracked(self, launch_id: str) -> None:
        """A satellite started a harness swe can't read sessions of: no
        session will ever name this launch, so say so now."""
        with self._launch_lock:
            entry = dict(self.launches.get(launch_id) or {})
        row = catalog_mod.load_machines(self.home).get(entry.get("machine"))
        if entry and entry["harness"] in (row or {}).get(
                "untracked_harnesses", []):
            self.settle_launch(launch_id, state="untracked", note=(
                f"{entry['harness']} is running under tmux, but swe can't "
                f"read its sessions, so there's no chat to open"
            ))

    def launch_view(self, launch_id: str) -> dict | None:
        """``GET /v1/launches/<id>``: started only once the catalog has
        the row, so the phone can open the chat straight away."""
        with self._launch_lock:
            entry = self.launches.get(launch_id)
            view = dict(entry) if entry else None
        if view and view["state"] == "started" \
                and self.catalog.row_for(view["key"]) is None:
            view["state"] = "pending"
        return view

    def _whois_hit(self, ip: str) -> tuple[bool, dict | None]:
        with self._whois_lock:
            hit = self._whois_cache.get(ip)
            if hit is not None and hit[0] > time.time():
                return True, hit[1]
        return False, None

    def whois_cached(self, ip: str) -> dict | None:
        """``whois(ip)``, remembered for _WHOIS_TTL_S (a miss for
        _WHOIS_MISS_TTL_S). Concurrent callers for one IP share a single
        lookup; different IPs look up in parallel."""
        found, peer = self._whois_hit(ip)
        if found:
            return peer
        with self._whois_lock:
            if len(self._whois_ip_locks) > 256:
                self._whois_ip_locks.clear()
            ip_lock = self._whois_ip_locks.setdefault(ip, threading.Lock())
        with ip_lock:
            # Whoever held the lock may have just filled the cache.
            found, peer = self._whois_hit(ip)
            if found:
                return peer
            peer = self.whois(ip)
            ttl = _WHOIS_TTL_S if peer is not None else _WHOIS_MISS_TTL_S
            with self._whois_lock:
                if len(self._whois_cache) > 256:
                    self._whois_cache.clear()
                self._whois_cache[ip] = (time.time() + ttl, peer)
        return peer

    def owner(self) -> str:
        """The hub owner's tailnet login, retrying a lookup that failed
        at start-up (behind serve, before Tailscale was up)."""
        if not self.owner_login and self.owner_retry is not None:
            now = time.time()
            if now - self._owner_tried_at >= _OWNER_RETRY_S:
                self._owner_tried_at = now
                if self.owner_retry():
                    self.owner_retry = None
        return self.owner_login

    def _on_probed_status(self, key: str, status: str, at: float) -> None:
        """A tail probe read ``key``'s status: patch the catalog row, and
        let a pending ``when_idle`` detach act once the turn is over."""
        self.catalog.note_status(key, status, at)
        if status != "active":
            self.detaches.nudge(key)

    def _journal_has(self, key: str) -> bool:
        return self.journal.last_seq(key) > 0

    # The one funnel: every event is journaled, then fanned out.
    def emit(self, key: str, event: dict) -> dict:
        etype = event.get("type")
        if etype == "approval.requested":
            rid = str((event.get("data") or {}).get("request_id") or "")
            if rid:
                self.pending_approvals[key] = rid
        elif etype == "approval.resolved":
            self.pending_approvals.pop(key, None)
            # Retire only the resolved request's token — a different
            # still-pending request on the session (a Codex app-server
            # holds approvals independently) keeps its buttons live.
            rid = str((event.get("data") or {}).get("request_id") or "")
            self.approval_tokens.drop(key, rid)
        elif etype in {"session.launched", "command.result"}:
            data = event.get("data") or {}
            lid = str(data.get("launch_id") or "")
            if lid and etype == "session.launched":
                self.settle_launch(
                    lid, state="started",
                    key=str(event.get("session") or key),
                )
            elif lid and data.get("op") in {"launch", "fork"} \
                    and not data.get("ok"):
                self.settle_launch(
                    lid, state="failed", note=str(data.get("note") or "")
                )
            elif lid and data.get("op") == "launch":
                self._settle_untracked(lid)
        written = self.journal.append(key, event)
        self.bus.publish(key, written)
        if key != GLOBAL_KEY:
            # The global journal mirrors everything so /v1/events replays
            # a whole-machine timeline. Global subscribers get the
            # *global*-numbered copy so their ``?last=`` cursor lines up
            # with replay.
            mirrored = self.journal.append(GLOBAL_KEY, written)
            self.bus.publish(GLOBAL_KEY, mirrored)
        return written

    def start(self) -> None:
        self._register_machine()
        # A keep or adopt cut short by a crash leaves a key in both
        # the supervised and kept registries; settle it before serving.
        supervise.reconcile_kept(self.home)
        notify_cfg = notify.load_config(self.home)
        # Copies of the legacy ntfy action credential already sent to a
        # topic still authenticate as a paired device, so retire it at
        # boot whether or not notifications still post — a disabled or
        # incomplete config must not strand the row live.
        notify.retire_action_credential(self.home, notify_cfg)
        targets: list = [
            (self._catalog_loop, "remote-catalog"),
            (self._tail_loop, "remote-tail"),
            (self._search_index_loop, "remote-search-index"),
        ]
        if notify.ready(notify_cfg):
            notifier = notify.Notifier(
                self, self.base_url, subscribe_key=GLOBAL_KEY
            )
            # Subscribe before the producers start — an approval prompt
            # already visible at boot would otherwise be emitted into a
            # bus with no listener and never reach the phone.
            notifier.subscribe()
            targets.append((notifier.run, "remote-notify"))
        for target, name in targets:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self.tail_manager.interrupt()
        # Fail every accepted-but-not-yet-started send honestly; the
        # in-flight one sees _stop and reports the same note.
        self.waiting.wake_all()
        self.sendq.close()
        self.detaches.close()
        for t in self._threads:
            t.join(timeout=2)
        self.webapp.close()

    def _register_machine(self) -> None:
        catalog_mod.upsert_machine(
            self.home, {**self.machine, "online": True}
        )
        # Presence is this process's live streams — a restart opens with
        # zero satellite connections, so every persisted ``online`` flag
        # for another machine is stale until that machine dials back in.
        for mid in catalog_mod.load_machines(self.home):
            if mid != self.machine_id:
                catalog_mod.upsert_machine(
                    self.home, {"id": mid, "online": False,
                                "last_seen_at": time.time()}
                )

    # -- satellites ---------------------------------------------------------

    @staticmethod
    def sat_journal_key(machine_id: str) -> str:
        return f"_sat_{machine_id}"

    def machine_online(self, mid: str) -> bool:
        return self._sat_subs.get(mid, 0) > 0 or mid == self.machine_id

    def sat_connected(self, mid: str) -> None:
        first = self._sat_subs.get(mid, 0) == 0
        self._sat_subs[mid] = self._sat_subs.get(mid, 0) + 1
        if first:
            catalog_mod.upsert_machine(
                self.home, {"id": mid, "online": True,
                            "last_seen_at": time.time()}
            )
            self.emit(
                GLOBAL_KEY,
                protocol.event(
                    "machine.online", "", mid, {"machine_id": mid}
                ),
            )

    def sat_disconnected(self, mid: str) -> None:
        left = max(0, self._sat_subs.get(mid, 0) - 1)
        self._sat_subs[mid] = left
        if left:
            return
        catalog_mod.upsert_machine(
            self.home, {"id": mid, "online": False,
                        "last_seen_at": time.time()}
        )
        self.emit(
            GLOBAL_KEY,
            protocol.event(
                "machine.offline", "", mid, {"machine_id": mid}
            ),
        )

    def queue_command(
        self, mid: str, op: str, session_key: str, data: dict
    ) -> dict:
        """Append a command to the satellite's journal; returns the envelope.

        The satellite replays ``_sat_<mid>`` on reconnect, so this IS the
        offline queue — nothing else to build.

        Returns ``None`` when the machine's unacked backlog exceeds
        ``MAX_SAT_PENDING``: the journal trims and silently dropped
        commands are worse than an honest "machine too far behind".
        """
        pending = self.journal.last_seq(
            self.sat_journal_key(mid)
        ) - self._sat_acked.get(mid, 0)
        if pending >= MAX_SAT_PENDING:
            if session_key:
                self.emit(
                    session_key,
                    protocol.event(
                        "session.error", session_key, self.machine_id,
                        {"message": f"{mid} is {pending} commands behind; "
                                    f"not queueing more"},
                    ),
                )
            return None
        with self._cmd_lock:
            self._cmd_seq += 1
            command_id = f"c{self._cmd_seq}"
        local_key, _mid = protocol.split_remote_key(session_key)
        cmd = protocol.command(op, local_key, mid, command_id, data)
        written = self.journal.append(self.sat_journal_key(mid), cmd)
        self.bus.publish(self.sat_journal_key(mid), written)
        if session_key:
            self.emit(
                session_key,
                protocol.event(
                    "command.queued", session_key, self.machine_id,
                    {"command_id": command_id, "op": op},
                ),
            )
        return written

    def _catalog_loop(self) -> None:
        while not self._stop.is_set():
            try:
                prev_own = {
                    row["key"]: row.get("ownership")
                    for row in self.catalog.rows()
                }
                self.catalog.refresh()
                self._adopt_launches()
                self._refresh_launchable()
                # Ownership can change outside HTTP — `swe remote
                # attach`, pending adoption, dead-tmux pruning — and the
                # phone needs the event either way.
                for row in self.catalog.rows():
                    new_own = row.get("ownership")
                    if prev_own.get(row["key"]) not in (None, new_own):
                        if new_own == protocol.OWNERSHIP_ATTACHED:
                            # Attached outside HTTP (`swe remote attach`,
                            # a kept tmux session taken back): same hot
                            # window as an attach through the hub.
                            self.tail_manager.wake(row["key"])
                        self.emit(
                            row["key"],
                            protocol.event(
                                "session.ownership", row["key"],
                                self.machine_id,
                                {"ownership": new_own},
                            ),
                        )
                rows, as_of = self.catalog.tail_snapshot()
                self.tail_manager.sync(rows, as_of=as_of)
                supervise.reap_kept(self.home, self.catalog.session_for)
            except Exception:
                pass
            self._stop.wait(catalog_mod.REFRESH_INTERVAL_S)

    def _search_index_loop(self) -> None:
        """Keep this machine's transcript search index current while
        ``swe session index --on`` says so. The setting is read every
        cycle, so turning it on or off needs no restart."""
        from quiver.sessions import search_writer

        while not self._stop.is_set():
            if search_writer.is_enabled(self.home):
                try:
                    search_writer.run_pass(self.home, should_stop=self._stop.is_set)
                except search_writer.IndexBusy:
                    pass  # a `swe session index` run has it; next cycle
                except Exception:
                    # A newer schema or a locked file: run_pass saved it
                    # as last_error for `swe session index --status` and
                    # the Settings row, and the next cycle tries again.
                    pass
            self._stop.wait(search_writer.INDEX_INTERVAL_S)

    def _refresh_launchable(self) -> None:
        """Keep this machine's ``launchable`` current: Provider visibility
        and the registry can change while the hub runs."""
        launchable = catalog_mod.launchable_harnesses()
        fields = {
            "launchable": [item["name"] for item in launchable],
            "harness_names": catalog_mod.harness_names(launchable),
            "untracked_harnesses": catalog_mod.untracked(launchable),
        }
        if all(self.machine.get(k) == v for k, v in fields.items()):
            return
        self.machine.update(fields)
        catalog_mod.upsert_machine(self.home, {"id": self.machine_id, **fields})

    def _adopt_launches(self) -> None:
        adopted = supervise.adopt_pending(
            self.home, self.catalog.tail_rows()
        )
        if adopted:
            # Newly adopted keys need a second refresh pass so the
            # rows carry ownership: attached.
            self.catalog.refresh()
        for key in adopted:
            # A launch or fork starts with its first turn: its tailer
            # (created on the next sync) starts hot and status-probed.
            self.tail_manager.wake(key)
            lid = (supervise.meta_for(self.home, key) or {}).get("launch_id")
            if lid:
                # Global stream only: anything in the session's own
                # journal before its tailer starts reads as history
                # already sent, and the tailer would skip the first turn.
                self.emit(GLOBAL_KEY, protocol.event(
                    "session.launched", key, self.machine_id,
                    {"launch_id": lid},
                ))

    def _tail_loop(self) -> None:
        sweeps = 0
        while not self._stop.is_set():
            try:
                self.tail_manager.tick()
            except Exception:
                pass
            sweeps += 1
            if sweeps >= 1200:  # ~5 min at a 250 ms tick
                sweeps = 0
                try:
                    attachments.sweep(self.home)
                except Exception:
                    pass
            # A write to a session (tail_manager.wake) cuts this short.
            self.tail_manager.wait(0.25)


_UNSET = object()


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "quiver-remote"
    protocol_version = "HTTP/1.1"
    state: HubState  # stamped on the server, set in make_server

    def setup(self) -> None:
        super().setup()
        # Without a deadline a tailnet peer can park threads by never
        # finishing a request. Reads only happen while parsing headers
        # and body — SSE never reads again after dispatch.
        self.connection.settimeout(_REQ_TIMEOUT_S)

    # -- plumbing ---------------------------------------------------------

    @property
    def _state(self) -> HubState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # quiet: hub runs as a daemon
        return

    def _send_json(self, status: int, payload) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, protocol.error(code, message))

    # -- request body bookkeeping -------------------------------------
    #
    # ``_unread`` is how many body bytes of the current request are still
    # on the socket: the declared Content-Length until something reads
    # it, then 0. None means the body can't be framed (Transfer-Encoding,
    # which the hub doesn't decode, or a malformed Content-Length), so
    # the only safe end for the connection is a close. Every response
    # goes through ``send_response``, which settles what is left before
    # the status line, so no early-exit path can leave bytes behind.

    _unread: int | None = 0
    _must_close = False
    _body_cache: object = _UNSET

    def handle_one_request(self) -> None:
        self._unread = 0
        self._must_close = False
        self._body_cache = _UNSET
        super().handle_one_request()

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        self._unread = self._declared_length()
        return True

    def _declared_length(self) -> int | None:
        if self.headers.get("Transfer-Encoding") is not None:
            return None
        values = {v.strip() for v in
                  self.headers.get_all("Content-Length") or []}
        if not values:
            return 0  # a request with neither header has no body
        if len(values) > 1:
            return None
        raw = values.pop()
        # int() would also take "+5", "1_000" or non-ASCII digits.
        if not (raw.isascii() and raw.isdigit()):
            return None
        try:
            return int(raw)
        except ValueError:  # past sys.get_int_max_str_digits()
            return None

    def _take_body(self, limit: int) -> bytes | None:
        """The request body when it is at most ``limit`` bytes, else None
        (left unread for ``send_response`` to settle)."""
        left = self._unread
        if left is None or left > limit:
            return None
        self._unread = 0
        return self.rfile.read(left) if left else b""

    def _settle_body(self) -> None:
        left = self._unread
        self._unread = 0
        if left == 0:
            return
        if left is None or left > _DRAIN_MAX or not self._drain(left):
            self._must_close = True

    def _drain(self, left: int) -> bool:
        """Read and drop ``left`` body bytes within ``_DRAIN_S``. False
        when the peer is too slow or goes away first."""
        deadline = time.monotonic() + _DRAIN_S
        try:
            while left > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.connection.settimeout(remaining)
                # read1: at most one recv per call, so the deadline holds
                # against a peer trickling a byte at a time.
                chunk = self.rfile.read1(min(left, 65536))
                if not chunk:
                    return False
                left -= len(chunk)
            return True
        except OSError:  # TimeoutError included
            return False
        finally:
            try:
                self.connection.settimeout(_REQ_TIMEOUT_S)
            except OSError:
                pass

    def send_response(self, code, message=None) -> None:
        # 100 Continue goes out through send_response_only, so a client
        # waiting for it is never drained here.
        self._settle_body()
        super().send_response(code, message)
        if self._must_close:
            self.send_header("Connection", "close")

    def end_headers(self) -> None:
        # A handler's own "Connection: keep-alive" must not reopen a
        # connection whose request body was left on the socket.
        if self._must_close:
            self.close_connection = True
        super().end_headers()

    def _read_body(self) -> dict | None:
        # A second call returns the first answer: the bytes are gone.
        if self._body_cache is not _UNSET:
            return self._body_cache  # type: ignore[return-value]
        try:
            raw = self._take_body(_MAX_BODY)
            if raw is None:
                return None
            if not raw:
                data: object = {}
            else:
                data = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeError):
            data = None
        result = data if isinstance(data, dict) else None
        self._body_cache = result
        return result

    def _device(self) -> str | None:
        auth = self.headers.get("Authorization") or ""
        cred = auth[7:].strip() if auth.startswith("Bearer ") else ""
        hit = pairing.device_for_credential(self._state.home, cred)
        if hit is None:
            return None
        device_id, row = hit
        # Throttled last_seen update: a write per request would churn
        # devices.json; five-minute freshness is plenty for the list.
        seen = row.get("last_seen_at") or 0
        if time.time() - seen > 300:
            try:
                # Same write backfills kind/client/os on rows that
                # predate their capture — never overwrites pair-time
                # fields or a user rename.
                pairing.touch_device(
                    self._state.home, device_id,
                    meta=pairing.client_meta(
                        self.headers.get("User-Agent") or ""
                    ),
                )
            except Exception:
                pass
        return device_id

    @staticmethod
    def _device_revoked(home: Path | None, device_id: str) -> bool:
        row = pairing.load_devices(home).get(device_id) or {}
        return bool(row.get("revoked_at"))

    def _approver_node(self, peer: dict | None) -> str | None:
        """The peer's node id when it shares the hub owner's login —
        else None. Tagged nodes never qualify."""
        owner = self._state.owner()
        if (
            peer is None or peer.get("tagged")
            or not owner
            or peer.get("login") != owner
        ):
            return None
        return str(peer.get("node_id") or "") or None

    def _owner_device(self) -> str | None:
        """A device id for the hub owner's own tailnet device, which needs
        no pairing: tailnet login is the identity the Mac app's owner
        already has. Held to the approval path's gate (custom header,
        known Host, matching Origin), so a page on another site can't
        borrow the owner's network identity. Tagged nodes and other
        logins still pair."""
        if not self._identity_ok() or self._bad_origin():
            return None
        node = self._approver_node(self._state.whois_cached(self._client_ip()))
        return f"tailnet:{node}" if node else None

    def _caller(self, path: str) -> str | None:
        """The paired device, else the owner's own device. Satellite
        routes stay bearer-only: a satellite is a paired machine."""
        device_id = self._device()
        if device_id is None and not path.startswith("/v1/agent/"):
            device_id = self._owner_device()
        return device_id

    def _scoped_approval_device(self, path: str) -> str | None:
        """A minted ntfy action token as caller identity.

        Action buttons carry a scoped token, not a device credential:
        bound to ``(session key, request_id)``, single-use, minutes
        lived, and accepted only here. The token is burned on any
        presentation — a mismatched ``request_id`` is a failed spend,
        not a free probe — so a topic reader holding it can answer
        exactly the one prompt it was minted for and nothing else.
        """
        prefix = "/v1/sessions/"
        suffix = "/approval"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        key = unquote(path[len(prefix):-len(suffix)])
        auth = self.headers.get("Authorization") or ""
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        if not token:
            return None
        body = self._read_body() or {}
        rid = str(body.get("request_id") or "")
        if not self._state.approval_tokens.consume(token, key, rid):
            return None
        # The approval route never reads the device id back; non-None
        # is the whole grant.
        return "ntfy-action"

    def _proxied(self) -> bool:
        """True when ``tailscale serve`` relayed this request: the hub
        trusts its proxy and the TCP peer is this machine."""
        return (self._state.trust_proxy
                and self.client_address[0] in _LOOPBACK)

    def _client_ip(self) -> str:
        """The requester's address: the tailnet client serve forwarded
        for, else the TCP peer. Serve replaces any X-Forwarded-For the
        client sent, so the last hop is the one it added."""
        if self._proxied():
            forwarded = self.headers.get("X-Forwarded-For") or ""
            hop = forwarded.split(",")[-1].strip()
            if hop:
                return hop
        return self.client_address[0]

    def _scheme(self) -> str:
        """``https`` when serve terminated TLS in front of us."""
        if self._proxied():
            proto = (self.headers.get("X-Forwarded-Proto") or "").lower()
            if proto in {"http", "https"}:
                return proto
        return "http"

    def _bad_origin(self) -> bool:
        """Origin must name this hub when present — a cross-site page
        can't satisfy either form."""
        origin = self.headers.get("Origin")
        if origin is not None and (
            origin != f"{self._scheme()}://{self.headers.get('Host')}"
        ):
            return True
        return False

    def _identity_ok(self) -> bool:
        """The unauthenticated approval path's CSRF gate: a cross-site
        form can't send a custom header, and the Host must be a name
        this hub actually answers (DNS-rebinding check)."""
        if (self.headers.get("X-Quiver-Pair") or "") != "1":
            return False
        allowed = self._state.allowed_hosts
        if allowed and (self.headers.get("Host") or "") not in allowed:
            return False
        return True

    def _pair_meta(self, body: dict | None, node_name: str = "") -> dict:
        """kind/model/client/os for the pairing device: the body's own
        ``device`` map wins over whatever the User-Agent lets us infer,
        and the tailnet node name goes in when it resolves."""
        meta = pairing.client_meta(self.headers.get("User-Agent") or "")
        sent = (body or {}).get("device")
        meta.update(pairing.clean_device_meta(sent))
        if node_name:
            meta["node_name"] = node_name
        return meta

    def _peer_node_name(self) -> str:
        """The caller's tailnet node name, best-effort: a miss costs the
        row its node_name, nothing more."""
        try:
            peer = self._state.whois_cached(self._client_ip())
        except Exception:
            return ""
        return str((peer or {}).get("node_name") or "")

    def _local_host_ok(self) -> bool:
        """The DNS-rebinding gate on the unauthenticated routes: once
        ``allowed_hosts`` names the addresses this hub answers to, a
        request under any other Host came through a name that isn't
        this hub's — a page that rebound its domain to this address.
        Browsers can't forge Host, so the check is quiet for clients
        that dialed the hub's own address."""
        allowed = self._state.allowed_hosts
        return not allowed or (self.headers.get("Host") or "") in allowed

    def _attempt_ok(self, bucket: dict[str, list[float]]) -> bool:
        """The per-source attempt cap on the unauthenticated pair
        routes: ``_PAIR_ATTEMPT_MAX`` per ``_PAIR_ATTEMPT_S``. Locked:
        read-check-append must be atomic across handler threads."""
        ip = self._client_ip()
        now = time.time()
        with self._state._pair_req_lock:
            times = [t for t in bucket.get(ip, [])
                     if now - t < _PAIR_ATTEMPT_S]
            if len(times) >= _PAIR_ATTEMPT_MAX:
                return False
            times.append(now)
            bucket[ip] = times
        return True

    def _pair_request(self) -> None:
        if not self._local_host_ok():
            self._send_error(
                403, "bad_origin", "request blocked by origin policy"
            )
            return
        # Rate limit BEFORE the whois fork — a flood must not spawn a
        # subprocess per request.
        if not self._attempt_ok(self._state._pair_req_times):
            self._send_error(429, "rate_limited",
                             "too many pair requests")
            return
        ip = self._client_ip()
        peer = self._state.whois(ip)
        if peer is None:
            self._send_error(
                403, "not_tailnet",
                "can't identify this device on the tailnet; "
                "use a pair token",
            )
            return
        body = self._read_body() or {}
        try:
            result = pairing.request_pair(
                self._state.home, {**peer, "ip": ip},
                str(body.get("name") or ""),
                meta=self._pair_meta(body),
            )
        except pairing.PairBusy:
            self._send_error(429, "busy",
                             "too many pending pair requests")
            return
        self._send_json(201, result)

    def _pair_decide(self, approve: bool) -> None:
        ct = self.headers.get("Content-Type") or ""
        if not ct.startswith("application/json"):
            self._send_error(
                403, "bad_origin", "request blocked by origin policy"
            )
            return
        device_id = self._device()
        # The peer's node decides self-approval no matter how the
        # caller proved itself — bearer or owner identity alike.
        peer = self._state.whois(self._client_ip())
        node_id = (
            str(peer.get("node_id") or "") or None
        ) if peer else None
        if device_id is None and self._approver_node(peer) is None:
            self._send_error(401, "unauthorized",
                             "pair this device first")
            return
        if self._bad_origin() or (
            device_id is None and not self._identity_ok()
        ):
            self._send_error(
                403, "bad_origin", "request blocked by origin policy"
            )
            return
        body = self._read_body() or {}
        code = str(body.get("code") or "")
        rid = body.get("request_id")
        rid = str(rid) if rid else None
        if approve:
            try:
                result = pairing.approve_request(
                    self._state.home, code, rid, approver_node_id=node_id
                )
            except pairing.SelfApproval:
                self._send_error(
                    403, "self_approval",
                    "approve from a different device",
                )
                return
            if result is None:
                self._send_error(404, "no_match",
                                 "no pending request matches that code")
                return
            self._send_json(200, {
                "ok": True, "device_id": result["device_id"],
                "name": result["name"],
            })
            return
        if pairing.deny_request(self._state.home, code, rid):
            self._send_json(200, {"ok": True})
        else:
            self._send_error(404, "no_match",
                             "no pending request matches that code")

    # -- SSE --------------------------------------------------------------

    def _sse_stream(
        self,
        key: str | None,
        after_seq: int,
        device_id: str | None = None,
        subscribe_key: str | None = None,
    ) -> None:
        journal_key = key if key is not None else GLOBAL_KEY
        # Subscribe BEFORE replay: an event appended between read_since
        # and subscribe would be lost from both. Queued events are then
        # deduped by seq against what replay already sent.
        q = self._state.bus.subscribe(
            subscribe_key if subscribe_key is not None else journal_key
        )
        try:
            replay, truncated = self._state.journal.read_since(
                journal_key, after_seq
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            # An SSE response is the last thing this connection ever
            # does. Must come after end_headers — send_header parses the
            # Connection field and would flip this back off.
            self.close_connection = True
            replayed = after_seq
            try:
                if truncated:
                    self._sse_write(
                        {"type": "journal.truncated", "seq": after_seq})
                for ev in replay:
                    self._sse_write(ev)
                    if isinstance(ev.get("seq"), int):
                        replayed = max(replayed, ev["seq"])
                self._sse_write({
                    "type": "journal.caught-up",
                    "seq": replayed,
                })
            except (BrokenPipeError, ConnectionResetError,
                    TimeoutError, OSError):
                return
            last_revocation_check = 0.0
            while True:
                try:
                    ev = q.get(timeout=_SSE_HEARTBEAT_S)
                except queue.Empty:
                    ev = None
                now = time.time()
                if (
                    device_id
                    and now - last_revocation_check > _REVOCATION_CHECK_S
                ):
                    last_revocation_check = now
                    if self._device_revoked(self._state.home, device_id):
                        return
                if ev is None:
                    try:
                        self.wfile.write(b": ka\n\n")
                        self.wfile.flush()
                    except (
                        BrokenPipeError, ConnectionResetError,
                        TimeoutError, OSError,
                    ):
                        return
                    continue
                if (
                    isinstance(ev.get("seq"), int)
                    and ev["seq"] <= replayed
                ):
                    continue  # already delivered by the replay pass
                try:
                    self._sse_write(ev)
                    replayed = max(replayed, ev["seq"])
                except (
                    BrokenPipeError, ConnectionResetError,
                    TimeoutError, OSError,
                ):
                    return
        finally:
            self._state.bus.unsubscribe(q)

    def _agent_stream(
        self, mid: str, after_seq: int, device_id: str | None = None
    ) -> None:
        """Command SSE for one satellite; presence = this stream's lifetime."""
        state = self._state
        # The seq the satellite acknowledges: new queues past
        # MAX_SAT_PENDING over this floor are refused.
        state._sat_acked[mid] = after_seq
        state.sat_connected(mid)
        try:
            self._sse_stream(
                HubState.sat_journal_key(mid), after_seq,
                device_id=device_id,
                subscribe_key=HubState.sat_journal_key(mid),
            )
        finally:
            state.sat_disconnected(mid)

    def _pull_attachment(self, key: str, ref: str) -> None:
        target = attachments.resolve(self._state.home, key, ref)
        if target is None:
            self._send_error(404, "not_found", "no such attachment")
            return
        try:
            body = Path(target).read_bytes()
        except OSError:
            self._send_error(404, "not_found", "attachment unreadable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse_write(self, ev: dict) -> None:
        ev = injected_mod.clean_event(ev)
        if ev is None:
            return
        frame = (
            f"id: {ev.get('seq', 0)}\n"
            f"event: {ev.get('type', 'message')}\n"
            f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        )
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        args = parse_qs(parsed.query)

        if path == "/v1/status":
            if not self._local_host_ok():
                self._send_error(
                    403, "bad_origin", "request blocked by origin policy"
                )
                return
            # A reachability check only: machine id, version and uptime
            # tell the whole tailnet more than a caller needs.
            self._send_json(200, {
                "ok": True,
                "v": protocol.PROTOCOL_VERSION,
            })
            return

        # The requester's poll is unauthenticated — the request id +
        # poll secret it carries are the capability.
        if path.startswith("/v1/pair/request/"):
            if not self._local_host_ok():
                self._send_error(
                    403, "bad_origin", "request blocked by origin policy"
                )
                return
            rid = unquote(path[len("/v1/pair/request/"):])
            secret = str((args.get("secret") or [""])[0])
            result = pairing.poll_request(self._state.home, rid, secret)
            if result is None:
                self._send_error(
                    404, "unknown_request", "unknown or expired request"
                )
                return
            self._send_json(200, result)
            return

        # Approving devices get in two ways: a paired bearer, or the
        # tailnet login that owns this hub node.
        if path == "/v1/pair/requests":
            peer = self._state.whois(self._client_ip())
            device_id = self._device()
            if device_id is None and self._approver_node(peer) is None:
                self._send_error(401, "unauthorized",
                                 "pair this device first")
                return
            if self._bad_origin() or (
                device_id is None and not self._identity_ok()
            ):
                self._send_error(
                    403, "bad_origin", "request blocked by origin policy"
                )
                return
            self._send_json(
                200, {"requests": pairing.list_requests(self._state.home)}
            )
            return

        # Everything else under /v1 wants a paired device credential, or
        # the owner's own tailnet device.
        device_id = None
        if path.startswith("/v1/"):
            device_id = self._caller(path)
            if device_id is None:
                self._send_error(401, "unauthorized", "pair this device first")
                return

        if path == "/v1/sessions":
            self._send_json(200, {
                "sessions": self._state.catalog.rows(),
                "machines": list(
                    catalog_mod.load_machines(self._state.home).values()
                ),
                "server_time": time.time(),
            })
            return

        if path.startswith("/v1/launches/"):
            view = self._state.launch_view(
                unquote(path[len("/v1/launches/"):]))
            if view is None:
                self._send_error(404, "unknown_launch", "no such launch")
                return
            self._send_json(200, view)
            return

        if path == "/v1/folders":
            self._folders(args)
            return

        if path == "/v1/machines":
            self._send_json(200, {
                "machines": list(
                    catalog_mod.load_machines(self._state.home).values()
                )
            })
            return

        if path == "/v1/devices/me":
            self._device_me(device_id)
            return

        if path == "/v1/ssh":
            self._send_json(200, sshkeys.view(self._state.home, device_id))
            return

        if path == "/v1/search":
            query = (args.get("q") or [""])[0]
            self._send_json(200, search_mod.search(
                self._state.catalog.rows(),
                query,
                home=self._state.home,
                limit=_int_arg(args.get("limit"), search_mod.DEFAULT_LIMIT),
            ))
            return

        if path == "/v1/usage":
            refresh = str((args.get("refresh") or [""])[0]) in {"1", "true"}
            try:
                body = usage_mod.snapshot(refresh=refresh)
            except (OSError, ValueError) as exc:
                self._send_error(503, "usage_unavailable", str(exc))
                return
            body["machine"] = self._state.machine
            body["machines"] = self._usage_machines(body)
            self._send_json(200, body)
            return

        if path == "/v1/app/overview":
            try:
                body = self._state.webapp.overview(
                    period=str((args.get("period") or [""])[0]),
                    tz_name=str((args.get("tz") or [""])[0]) or None,
                    rescan=str((args.get("rescan") or [""])[0]) in {"1", "true"},
                )
            except (OSError, sqlite3.Error) as exc:
                self._send_error(503, "ledger_unavailable", str(exc))
                return
            body["machine"] = self._state.machine.get("name")
            self._send_json(200, body)
            return

        if path == "/v1/app/harnesses":
            try:
                harnesses = webapp_harnesses.rows()
            except (OSError, ValueError) as exc:
                self._send_error(503, "registry_unavailable", str(exc))
                return
            self._send_json(200, {
                "harnesses": harnesses,
                "window_days": usage_mod.WINDOW_DAYS,
                "machine": self._state.machine.get("name"),
            })
            return

        if path == "/v1/app/sessions":
            try:
                body = self._state.webapp.sessions(
                    self._state.catalog.rows(),
                    hub_machine_id=self._state.machine_id,
                    machines=catalog_mod.load_machines(self._state.home),
                    rescan=str((args.get("rescan") or [""])[0]) in {"1", "true"},
                )
            except (OSError, sqlite3.Error) as exc:
                self._send_error(503, "ledger_unavailable", str(exc))
                return
            body["machine"] = self._state.machine.get("name")
            self._send_json(200, body)
            return

        if path.startswith("/v1/app/"):
            self._page_get(path[len("/v1/app/"):], args, device_id)
            return

        if path == "/v1/inbox":
            done =str((args.get("done") or [""])[0])
            self._send_json(200, {
                "items": self._state.catalog.inbox(
                    pending=self._state.pending_approvals,
                    dismissed=dismissals.for_device(
                        self._state.home, device_id
                    ),
                    include_done=done in {"1", "true"},
                )
            })
            return

        if path == "/v1/events":
            after = _int_arg(args.get("last"), 0)
            self._sse_stream(None, after, device_id=device_id)
            return

        if path == "/v1/agent/commands":
            mid = str((args.get("machine") or [""])[0])
            if not mid:
                self._send_error(
                    422, "bad_command", "machine is required"
                )
                return
            after = _int_arg(args.get("last"), 0)
            self._agent_stream(mid, after, device_id=device_id)
            return

        if path.startswith("/v1/agent/attachments/"):
            stem = path[len("/v1/agent/attachments/"):]
            key_part, _sep, ref = stem.rpartition("/")
            self._pull_attachment(unquote(key_part), unquote(ref))
            return

        if path.startswith("/v1/sessions/"):
            stem = path[len("/v1/sessions/"):]
            key_part, sep, action = stem.rpartition("/")
            key = unquote(key_part if sep else stem)
            if sep and action == "commands":
                self._slash_commands(key)
                return
            if sep and action in {"events", "messages"}:
                if self._state.catalog.row_for(key) is None:
                    self._send_error(
                        404, "unknown_session", "no such session"
                    )
                    return
                # Sessions past the background tail cap get a tailer on
                # demand so an opened chat isn't an empty journal.
                session = self._state.catalog.session_for(key)
                row = self._state.catalog.row_for(key) or {}
                if session is not None:
                    self._state.tail_manager.ensure(
                        key, session, str(row.get("status") or "")
                    )
                elif row.get("remote") and row.get("machine_id"):
                    # A satellite session has no local Session to tail —
                    # ask its agent to start one instead.
                    self._state.queue_command(
                        str(row["machine_id"]), "tail", key,
                        {"status": str(row.get("status") or "")},
                    )
            if sep and action == "events":
                after = _int_arg(args.get("last"), 0)
                self._sse_stream(key, after, device_id=device_id)
                return
            if sep and action == "messages":
                self._messages(key, args)
                return
            if sep and action == "files":
                self._files(key, args)
                return
            if sep and action == "queue":
                if self._state.catalog.row_for(key) is None:
                    self._send_error(
                        404, "unknown_session", "no such session"
                    )
                    return
                # A satellite session's sends never wait on the hub, so
                # its list is honestly empty.
                self._send_json(200, {
                    "sends": self._state.waiting.list(key),
                })
                return
            self._send_error(404, "not_found", "unknown route")
            return

        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/v1/pair":
            if not self._local_host_ok():
                self._send_error(
                    403, "bad_origin", "request blocked by origin policy"
                )
                return
            # Same per-source cap as /v1/pair/request: the 192-bit
            # token isn't brute-forceable, but a flood shouldn't get
            # unlimited tries (or a whois fork each).
            if not self._attempt_ok(self._state._pair_ex_times):
                self._send_error(429, "rate_limited",
                                 "too many pair attempts")
                return
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            result = pairing.exchange(
                self._state.home,
                str(body.get("token") or ""),
                str(body.get("name") or ""),
                meta=self._pair_meta(
                    body, node_name=self._peer_node_name()
                ),
            )
            if result is None:
                self._send_error(
                    403, "pair_failed", "unknown or expired pair token"
                )
                return
            self._send_json(200, result)
            return

        if path == "/v1/pair/request":
            self._pair_request()
            return

        if path in {"/v1/pair/requests/approve", "/v1/pair/requests/deny"}:
            self._pair_decide(path.endswith("/approve"))
            return

        device_id = self._caller(path)
        if device_id is None:
            device_id = self._scoped_approval_device(path)
        if device_id is None:
            self._send_error(401, "unauthorized", "pair this device first")
            return

        if path.startswith("/v1/app/"):
            self._page_post(path[len("/v1/app/"):], device_id)
            return

        if path == "/v1/devices/rename":
            self._device_rename(device_id)
            return

        if path == "/v1/ssh/key":
            self._ssh_key(device_id)
            return

        if path == "/v1/inbox/dismiss":
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            key = body.get("key")
            last = body.get("last_active_at")
            if not dismissals.valid(key, last):
                self._send_error(
                    422, "bad_dismiss",
                    "key and a numeric last_active_at are required",
                )
                return
            try:
                dismissals.dismiss(self._state.home, device_id, key, last)
            except OSError:
                self._send_error(500, "store_failed",
                                 "could not save the dismissal")
                return
            self._send_json(200, {"ok": True})
            return

        if path == "/v1/agent/register":
            body = self._read_body()
            machine = (body or {}).get("machine") or {}
            mid = str(machine.get("id") or "")
            if not mid:
                self._send_error(
                    422, "bad_command", "machine.id is required"
                )
                return
            catalog_mod.upsert_machine(
                self._state.home,
                {**machine, "last_seen_at": time.time()},
            )
            self._send_json(200, {"ok": True, "machine_id": mid})
            return

        if path == "/v1/agent/catalog":
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            mid = str(body.get("machine_id") or "")
            rows = body.get("rows") or []
            if not mid or not isinstance(rows, list):
                self._send_error(
                    422, "bad_command",
                    "machine_id and rows are required",
                )
                return
            # Snapshot ownership before the merge so attach/detach done
            # on the satellite surfaces as an event the PWA reacts to.
            before = {
                protocol.remote_key(mid, str(r.get("key") or "")):
                    r.get("ownership")
                for r in rows if isinstance(r, dict)
            }
            old = {
                k: (self._state.catalog.row_for(k) or {}).get("ownership")
                for k in before
            }
            catalog_mod.merge_remote_rows(self._state.home, mid, rows)
            stored = "slash" in body and slash.save_satellite(
                self._state.home, mid, body["slash"])
            self._state.catalog.refresh()
            for hub_key, ownership in before.items():
                if ownership and ownership != old.get(hub_key):
                    self._state.emit(
                        hub_key,
                        protocol.event(
                            "session.ownership", hub_key,
                            self._state.machine_id,
                            {"ownership": ownership},
                        ),
                    )
            self._send_json(200, {
                "ok": True, "rows": len(rows), "slash": bool(stored),
            })
            return

        if path == "/v1/agent/usage":
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            mid = str(body.get("machine_id") or "")
            if not mid:
                self._send_error(
                    422, "bad_command", "machine_id is required"
                )
                return
            try:
                usage_mod.save_satellite(self._state.home, mid, body)
            except ValueError as exc:
                self._send_error(422, "bad_command", str(exc))
                return
            except OSError as exc:
                self._send_error(503, "usage_unavailable", str(exc))
                return
            self._send_json(200, {"ok": True})
            return

        if path == "/v1/agent/events":
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            events = body.get("events") or []
            mid = str(body.get("machine_id") or "")
            if not isinstance(events, list):
                self._send_error(
                    422, "bad_command", "events must be a list"
                )
                return
            accepted = self._accept_agent_events(mid, events)
            self._send_json(200, {"ok": True, "accepted": accepted})
            return

        if path == "/v1/sessions":
            self._launch()
            return

        if path.startswith("/v1/sessions/"):
            # Resolve "<key>/<action>" by splitting once on the last slash.
            stem = path[len("/v1/sessions/"):]
            key_part, _, action = stem.rpartition("/")
            key = unquote(key_part)
            row = self._state.catalog.row_for(key)
            if row is None:
                self._send_error(404, "unknown_session", "no such session")
                return
            if action == "fork":  # hub or satellite: fork.py routes it
                self._send_json(*fork_mod.request(
                    self._state, key, row, self._read_body()))
                return
            if action in sessionmeta.ACTIONS:
                self._session_meta(key, action)
                return
            remote_mid = row.get("machine_id")
            if remote_mid and remote_mid != self._state.machine_id:
                self._forward(key, remote_mid, action, row)
                return
            if action == "attachments":
                self._stage_attachment(key)
                return
            if action == "control":
                self._control(key, row)
                return
            if action == "queue":
                self._queue_action(key, row)
                return
            if action in {"message", "approval"}:
                if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
                    self._send_error(
                        409, "not_attached",
                        "session is observed: read-only until attached",
                    )
                    return
                if action == "message":
                    self._send_message(key, row)
                else:
                    self._send_approval(key)
                return
            self._send_error(404, "not_found", "unknown route")
            return

        self._send_error(404, "not_found", "unknown route")

    def _folders(self, args) -> None:
        """``GET /v1/folders?path=``: the subfolders of one folder on the
        hub machine, for the phone's folder browser. Names only, no files;
        dot-folders stay hidden. A satellite's disk is not readable from
        here, so another ``machine`` is refused."""
        machine = str((args.get("machine") or [""])[0])
        if machine and machine != self._state.machine_id:
            self._send_error(422, "browse_unavailable",
                             "only the hub machine's folders can be browsed")
            return
        raw = str((args.get("path") or [""])[0]) or os.path.expanduser("~")
        if not raw.startswith("/") or "\x00" in raw:
            self._send_error(422, "bad_cwd", "path must be absolute")
            return
        folder = os.path.realpath(raw)
        if not os.path.isdir(folder):
            self._send_error(422, "bad_cwd",
                             f"no such folder on this machine: {raw}")
            return
        try:
            with os.scandir(folder) as it:
                names = sorted(
                    (e.name for e in it
                     if not e.name.startswith(".") and _is_dir(e)),
                    key=str.casefold,
                )
        except OSError:
            self._send_error(403, "not_readable",
                             f"can't read {folder} on this machine")
            return
        parent = os.path.dirname(folder)
        self._send_json(200, {
            "path": folder,
            "parent": parent if parent != folder else None,
            "folders": [
                {"name": n, "path": os.path.join(folder, n)}
                for n in names[:FOLDER_LIST_CAP]
            ],
            "truncated": len(names) > FOLDER_LIST_CAP,
        })

    def _files(self, key: str, args) -> None:
        """``GET /v1/sessions/{id}/files?q=&limit=``: paths under the
        session's working folder for the prompt box's ``@`` menu
        (``files.py``)."""
        row = self._state.catalog.row_for(key)
        if row is None:
            self._send_error(404, "unknown_session", "no such session")
            return
        try:
            body = files_mod.for_session(
                row,
                hub_machine_id=self._state.machine_id,
                machines=catalog_mod.load_machines(self._state.home),
                query=str((args.get("q") or [""])[0]),
                limit=_int_arg(args.get("limit"), files_mod.DEFAULT_LIMIT),
            )
        except files_mod.FilesError as exc:
            self._send_error(exc.status, exc.code, exc.message)
            return
        self._send_json(200, body)

    def _launch(self) -> None:
        """``POST /v1/sessions``: start a harness on the hub or a satellite.

        Answers 202 with a ``launch_id``; ``GET /v1/launches/<id>`` turns
        it into the session key once the new transcript is adopted.
        """
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        try:
            cmd = protocol.parse_launch_command(body)
        except ValueError as exc:
            self._send_error(422, "bad_command", str(exc))
            return
        state = self._state
        machine = cmd["machine"] or state.machine_id
        harness, cwd = cmd["harness"], cmd["cwd"]
        if machine != state.machine_id:
            row = catalog_mod.load_machines(state.home).get(machine) or {}
            # A satellite older than the launchable field reports none;
            # it checks its own registry when the command arrives.
            offered = row.get("launchable")
            if isinstance(offered, list) and harness not in offered:
                self._send_error(422, "not_launchable",
                                 f"{harness} can't be launched on {machine}")
                return
            launch_id = state.new_launch(machine, harness, cwd)
            queued = state.queue_command(machine, "launch", "", {
                "harness": harness, "cwd": cwd, "prompt": cmd["prompt"],
                "launch_id": launch_id,
            })
            if queued is None:
                state.settle_launch(launch_id, state="failed",
                                    note="machine is too far behind")
                self._send_error(429, "backlog_full",
                                 "machine is too far behind")
                return
            self._send_json(202, {
                "ok": True, "queued": True, "launch_id": launch_id,
                "state": "pending", "note": "queued for satellite",
            })
            return
        if not os.path.isdir(cwd):
            self._send_error(422, "bad_cwd",
                             f"no such folder on this machine: {cwd}")
            return
        rows = state.catalog.tail_rows()
        # Failures a phone can act on answer now; launch_new itself
        # runs after the reply, since priming the pane can take 30 s and
        # the phone needs the launch id to follow it.
        binary = catalog_mod.launch_command(harness)
        if binary is None:
            self._send_error(
                422, "not_launchable",
                f"{harness} can't start here: archived, switched off in "
                f"Provider visibility, or not installed",
            )
            return
        if not supervise.tmux_bin():
            self._send_error(422, "launch_failed", "tmux is not installed")
            return
        tracked = harness not in catalog_mod.untracked(
            catalog_mod.launchable_harnesses())
        launch_id = state.new_launch(machine, harness, cwd)

        def run() -> None:
            # Every same-cwd session that exists before launch: adoption
            # later refuses these ids, so a pre-existing session can never
            # be claimed as the one we just started.
            known = catalog_mod.known_session_ids(rows, harness, cwd)
            ok, note, _name = supervise.launch_new(
                state.home, harness, cwd, cmd["prompt"],
                known_ids=known, launch_id=launch_id, binary=binary,
            )
            if not ok:
                state.settle_launch(launch_id, state="failed", note=note)
            elif not tracked:
                state.settle_launch(launch_id, state="untracked", note=(
                    f"{harness} is running under tmux, but swe can't read "
                    f"its sessions, so there's no chat to open"
                ))

        threading.Thread(
            target=run, name=f"remote-launch-{launch_id}", daemon=True
        ).start()
        self._send_json(202, {
            "ok": True, "launch_id": launch_id, "state": "pending",
            "note": "starting under tmux",
        })

    def _injector(self, key: str):
        meta = supervise.meta_for(self._state.home, key)
        # emit + machine let tier-A channels raise approval events from
        # their server streams, same path the tmux watcher uses. Every
        # write through it wakes the session's tailer.
        return self._state.tail_manager.waking(key, inject.injector_for(
            self._state.home, key, meta,
            emit=self._state.emit, machine=self._state.machine_id,
        ))

    def _forward(
        self, key: str, mid: str, action: str, row: dict
    ) -> None:
        """Route a write to the satellite owning ``key``.

        Same ownership rules as local writes: observed sessions only
        accept ``attach``. Everything lands in the machine's command
        journal, so the phone gets 202 whether the satellite is live or
        asleep — results arrive later as ``command.result`` events.
        """
        if action == "queue":
            # A satellite never parks a queue-mode send: it delivers on
            # arrival or refuses a busy session, so there is nothing
            # waiting here to steer or remove.
            self._send_error(
                409, "queue_unsupported",
                "sends to another machine's session don't wait on the "
                "hub; there is nothing queued to steer or remove",
            )
            return
        if action != "attach" and action != "control":
            if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
                self._send_error(
                    409, "not_attached",
                    "session is observed: read-only until attached",
                )
                return
        if action == "attachments":
            upload = self._read_upload()
            if upload is None:
                return
            name, data = upload
            payload, err = attachments.stage(
                self._state.home, key, name, data
            )
            if payload is None:
                self._send_error(
                    422, "bad_attachment", err or "stage failed"
                )
                return
            cmd = self._state.queue_command(mid, "attachment", key, {
                "ref": payload["ref"],
                "hub_key": key,
                "machine": mid,
            })
            if cmd is None:
                self._send_error(429, "backlog_full",
                                 "machine is too far behind")
                return
            self._send_json(202, {**payload, "queued": True})
            return
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        if action == "message":
            try:
                cmd = protocol.parse_message_command(body)
            except ValueError as exc:
                self._send_error(422, "bad_command", str(exc))
                return
            queued = self._state.queue_command(mid, "message", key, {
                "text": cmd["text"], "mode": cmd["mode"],
                "attachments": cmd["attachments"],
                "client_id": cmd["client_id"],
                "status": row.get("status") or "",
            })
            if queued is None:
                self._send_error(429, "backlog_full",
                                 "machine is too far behind")
                return
            # Same shape as the local 202; waiting is always false —
            # the satellite re-reads status and decides for itself.
            self._send_json(202, {
                "ok": True, "queued": True,
                "client_id": cmd["client_id"], "waiting": False,
            })
            return
        elif action == "approval":
            try:
                cmd = protocol.parse_approval_command(body)
            except ValueError as exc:
                self._send_error(422, "bad_command", str(exc))
                return
            queued = self._state.queue_command(mid, "approval", key, {
                "request_id": cmd["request_id"],
                "decision": cmd["decision"],
            })
            if queued is None:
                self._send_error(429, "backlog_full",
                                 "machine is too far behind")
                return
        elif action == "control":
            try:
                cmd = protocol.parse_control_command(body)
            except ValueError as exc:
                self._send_error(422, "bad_command", str(exc))
                return
            op = cmd["op"]
            if op == "attach":
                queued = self._state.queue_command(mid, "attach", key, {
                    "mode": body.get("mode") or "auto",
                    "status": row.get("status") or "",
                })
            else:
                if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
                    self._send_error(
                        409, "not_attached",
                        "session is observed: read-only until attached",
                    )
                    return
                fields = {"ctl": op, "value": cmd.get("value") or ""}
                if "mode" in cmd:
                    fields["mode"] = cmd["mode"]
                queued = self._state.queue_command(
                    mid, "control", key, fields)
            if queued is None:
                self._send_error(429, "backlog_full",
                                 "machine is too far behind")
                return
        else:
            self._send_error(404, "not_found", "unknown route")
            return
        self._send_json(202, {"ok": True, "queued": True})

    def _accept_agent_events(
        self, mid: str, events: list
    ) -> int:
        """Journal upstream events under the hub-scoped ``key@mid``."""
        accepted = 0
        for ev in events:
            if not isinstance(ev, dict) or "type" not in ev:
                continue
            local_key = str(ev.get("session") or "")
            if local_key:
                hub_key = protocol.remote_key(mid, local_key)
                ev = dict(ev, session=hub_key, machine=mid)
                self._state.emit(hub_key, ev)
            else:
                self._state.emit(GLOBAL_KEY, dict(ev, machine=mid))
            accepted += 1
        return accepted

    def _send_message(self, key: str, row: dict) -> None:
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        try:
            cmd = protocol.parse_message_command(body)
        except ValueError as exc:
            self._send_error(422, "bad_command", str(exc))
            return
        # Resolve attachment refs to staged paths before injection.
        refs = list(cmd["attachments"])
        paths = []
        for ref in refs:
            path = attachments.resolve(self._state.home, key, ref)
            if path is None:
                self._send_error(
                    422, "bad_attachment", f"unknown attachment {ref}"
                )
                return
            paths.append(path)
        cmd["attachments"] = paths
        session = self._state.catalog.session_for(key)
        # The row's status can lag a just-finished turn — reclassify now
        # so ``waiting`` and the first poll don't trust a stale read.
        status = self._fresh_status(session, str(row.get("status") or ""))
        client_id = cmd["client_id"]
        state = self._state
        waiting = cmd["mode"] == "queue" and status == "active"
        qkey = self._send_key(key, cmd["mode"])
        if state.sendq.pending(qkey) >= MAX_SEND_BACKLOG:
            self._send_error(
                429, "backlog_full",
                "too many messages waiting for this session",
            )
            return
        alive = None
        if cmd["mode"] == "queue":
            send = sendq.QueuedSend(
                client_id, cmd["text"], refs,
                {"cmd": cmd, "session": session, "status": status},
            )
            added = state.waiting.add(key, send)
            if added:
                job, cancel = self._queue_job(key, send)
                # A steered or removed send frees its backlog slot now,
                # not when its parked job finally reaches the front.
                alive = send.is_waiting
        else:
            added = not state.waiting.has(key, client_id)
            if added:
                job, cancel = self._steer_job(key, cmd, session, status)
        if not added:
            # Two sends under one client_id would make the tray's
            # actions and the command.result ambiguous.
            self._send_error(
                409, "duplicate_client_id",
                "a send with this client_id is still queued",
            )
            return
        state.sendq.submit(qkey, job, cancel, alive)
        self._send_json(202, {
            "ok": True, "queued": True, "client_id": client_id,
            "waiting": waiting,
        })

    @staticmethod
    def _send_key(key: str, mode: str) -> str:
        # steer jobs get their own key: ordered among themselves but
        # never stuck behind a queue-mode job waiting out a turn.
        return key if mode == "queue" else f"{key}\x00steer"

    def _queue_job(self, key: str, send: "sendq.QueuedSend"):
        """A queue-mode send's (job, cancel): wait for idle, then inject.

        Every exit that reports goes through ``waiting.claim`` first; a
        send the phone already steered or removed exits silently, since
        that action reported it.
        """
        state = self._state
        cmd = send.payload["cmd"]
        session = send.payload["session"]
        status = send.payload["status"]
        client_id = send.client_id

        def fail(note: str) -> None:
            if state.waiting.claim(key, send, "failed"):
                self._send_result(key, client_id, False, note, False)

        def job() -> None:
            if send.state != "waiting":
                return
            # Re-read at run time: the submit-time status predates any
            # wait behind earlier jobs on this key — a send that started
            # a turn since then must still be waited out. The Session
            # object itself can go stale mid-wait (the status classifier
            # times out against session.timestamp), so re-fetch it too.
            current = state.catalog.session_for(key) or session
            live = self._fresh_status(current, status)
            deadline = time.monotonic() + QUEUE_WAIT_MAX_S
            while live == "active" and time.monotonic() < deadline:
                send.wake.wait(QUEUE_POLL_S)
                if send.state != "waiting":
                    return
                if state._stop.is_set():
                    fail("hub restarted; message not sent")
                    return
                current = state.catalog.session_for(key) or session
                live = self._fresh_status(current, status)
            if live == "active":
                fail("session stayed busy for 30 min; message not sent")
                return
            if not state.waiting.claim(key, send, "sending"):
                return
            self._deliver(key, client_id, current, cmd, live)

        def cancel() -> None:
            fail("hub restarted; message not sent")

        return job, cancel

    def _steer_job(self, key: str, cmd: dict, session, status: str):
        """A steer-mode send's (job, cancel): inject as soon as it runs."""
        state = self._state

        def job() -> None:
            current = state.catalog.session_for(key) or session
            live = self._fresh_status(current, status)
            self._deliver(key, cmd["client_id"], current, cmd, live)

        def cancel() -> None:
            self._send_result(
                key, cmd["client_id"], False,
                "hub restarted; message not sent", False,
            )

        return job, cancel

    def _deliver(
        self, key: str, client_id: str, session, cmd: dict, live: str
    ) -> None:
        try:
            result = self._injector(key).send(session, cmd, live)
            ok, note, degraded = result.ok, result.note, result.degraded
        except Exception as exc:
            ok, note, degraded = False, str(exc), False
        self._send_result(key, client_id, ok, note, degraded)

    # Why a client_id is no longer waiting, for the 409 on a late action.
    _GONE_NOTES = {
        "sending": "that message already went out",
        "steered": "that message was already sent now",
        "removed": "that message was already removed",
        "failed": "that message already failed",
    }

    def _queue_action(self, key: str, row: dict) -> None:
        """``POST /v1/sessions/{key}/queue {client_id, op, text?}``.

        ``steer`` takes one waiting queue-mode send off the idle wait and
        submits it as a steer (after the steers already queued);
        ``cancel`` drops it and reports ``command.result`` with
        ``cancelled: true``; ``edit`` replaces its text in place. All
        three act under the ``Waiting`` lock, so they race the send's own
        job safely: the job injects only after claiming it there.
        """
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        try:
            act = protocol.parse_queue_action(body)
        except ValueError as exc:
            self._send_error(422, "bad_command", str(exc))
            return
        client_id, op = act["client_id"], act["op"]
        state = self._state
        steer_key = self._send_key(key, "steer")
        if op == "edit":
            text = act["text"]

            def change(send) -> None:
                # The job's command is the same dict, read only after
                # its claim, so it injects the new text.
                send.text = text
                send.payload["cmd"]["text"] = text

            send, gone = state.waiting.update(key, client_id, change)
            if send is None:
                self._queue_gone(gone)
                return
            self._send_json(200, {
                "ok": True, "client_id": client_id, "op": op,
                "text": text,
            })
            return
        if op == "steer":
            # Sending now is a write: the same rules as a steer message.
            if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
                self._send_error(
                    409, "not_attached",
                    "session is observed: read-only until attached",
                )
                return
            if state.sendq.pending(steer_key) >= MAX_SEND_BACKLOG:
                self._send_error(
                    429, "backlog_full",
                    "too many messages waiting for this session",
                )
                return
        send, gone = state.waiting.take(
            key, client_id, "steered" if op == "steer" else "removed"
        )
        if send is None:
            self._queue_gone(gone)
            return
        if op == "cancel":
            self._send_result(
                key, client_id, False, "removed before sending", False,
                cancelled=True,
            )
        else:
            payload = send.payload
            job, cancel = self._steer_job(
                key, dict(payload["cmd"], mode="steer"),
                payload["session"], payload["status"],
            )
            state.sendq.submit(steer_key, job, cancel)
        self._send_json(200, {"ok": True, "client_id": client_id, "op": op})

    def _queue_gone(self, gone: str) -> None:
        """Answer an action on a send that is not waiting."""
        if gone == "unknown":
            self._send_error(
                404, "unknown_send", "no queued message with that client_id",
            )
        else:
            self._send_error(409, "not_waiting", self._GONE_NOTES.get(
                gone, "that message is no longer queued"))

    def _fresh_status(self, session, fallback: str) -> str:
        """Reclassify one session now; the catalog row can be stale."""
        try:
            got = self._state.status_of([session])
        except Exception:
            return fallback
        return str(got[0]) if got else fallback

    def _send_result(
        self,
        key: str,
        client_id: str,
        ok: bool,
        note: str,
        degraded: bool,
        cancelled: bool = False,
    ) -> None:
        """Report a queued send the way a satellite would.

        ``cancelled`` is set (and true) only on a send the phone removed
        from the queue, so a client can tell it from a failure.
        """
        fields = {
            "command_id": "", "op": "message", "ok": ok,
            "note": note, "degraded": degraded,
            "client_id": client_id,
        }
        if cancelled:
            fields["cancelled"] = True
        self._state.emit(
            key,
            protocol.event(
                "command.result", key, self._state.machine_id, fields,
            ),
        )

    def _send_approval(self, key: str) -> None:
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        try:
            cmd = protocol.parse_approval_command(body)
        except ValueError as exc:
            self._send_error(422, "bad_command", str(exc))
            return
        session = self._state.catalog.session_for(key)
        result = self._injector(key).approval(
            session, cmd["request_id"], cmd["decision"]
        )
        if not result.ok:
            code = (
                "approval_not_supported"
                if result.note.startswith("approval_not_supported")
                else "approval_failed"
            )
            self._send_error(409, code, result.note)
            return
        self._state.emit(
            key,
            protocol.event(
                "approval.resolved", key, self._state.machine_id,
                {
                    "request_id": cmd["request_id"],
                    "decision": cmd["decision"],
                },
            ),
        )
        self._send_json(200, {"ok": True, "note": result.note})

    def _control(self, key: str, row: dict) -> None:
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        try:
            cmd = protocol.parse_control_command(body)
        except ValueError as exc:
            self._send_error(422, "bad_command", str(exc))
            return
        op = cmd["op"]
        mode = cmd.get("mode") or "now"
        if op == "attach":
            self._attach(key, row, body)
            return
        if op == "kill" or (op == "detach" and mode != "when_idle"):
            # A later detach or kill replaces a pending when_idle one.
            self._state.detaches.cancel(key, f"replaced by {op} {mode}")
        if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
            self._send_error(
                409, "not_attached",
                "session is observed: read-only until attached",
            )
            return
        if op == "detach" and mode == "when_idle":
            self._detach_when_idle(key)
            return
        session = self._state.catalog.session_for(key)
        if op == "detach" and mode == "keep":
            result = inject.InjectResult(*supervise.keep(
                self._state.home, key))
        else:
            result = self._injector(key).control(
                session, op, str(cmd.get("value") or "")
            )
        if not result.ok:
            self._send_error(409, "control_failed", result.note)
            return
        if op in {"kill", "detach"}:
            self._detached(key, drop_files=mode != "keep")
        self._send_json(200, {"ok": True, "note": result.note})

    def _detached(self, key: str, drop_files: bool = True) -> None:
        # A kept harness still runs and may read its staged files.
        if drop_files:
            attachments.drop_session(self._state.home, key)
        row = self._state.catalog.row_for(key)
        if row is not None:
            row["ownership"] = protocol.OWNERSHIP_OBSERVED
        self._emit_ownership(key)

    def _detach_when_idle(self, key: str) -> None:
        """``detach`` with ``mode: when_idle``: 202 now, detach later.

        The wait, its 30 min cap and its cancel live in ``detach.py``;
        the outcome lands as a ``command.result`` (``op: detach``).
        """
        state = self._state

        def status() -> str:
            row = state.catalog.row_for(key) or {}
            session = state.catalog.session_for(key)
            fallback = str(row.get("status") or "")
            return self._fresh_status(session, fallback) \
                if session is not None else fallback

        def fire() -> tuple[bool, str]:
            if supervise.meta_for(state.home, key) is None:
                return False, "session was already detached"
            result = self._injector(key).control(
                state.catalog.session_for(key), "detach", "")
            if result.ok:
                self._detached(key)
            return result.ok, result.note

        def report(ok: bool, note: str, cancelled: bool) -> None:
            fields = {"command_id": "", "op": "detach",
                      "mode": "when_idle", "ok": ok, "note": note}
            if cancelled:
                fields["cancelled"] = True
            state.emit(key, protocol.event(
                "command.result", key, state.machine_id, fields))

        fresh = state.detaches.schedule(
            key, status=status, fire=fire, report=report)
        self._send_json(202, {
            "ok": True, "queued": True, "mode": "when_idle",
            "note": "detaches when the current turn ends" if fresh
            else "a detach is already waiting for this turn to end",
        })

    def _attach(self, key: str, row: dict, body: dict) -> None:
        # Attaching again cancels a when_idle detach; one already
        # running finishes first, and this attach then starts over.
        undone = self._state.detaches.cancel(key, "cancelled by attach")
        if undone == "fired":
            # The detach ran while we waited, and a catalog refresh may
            # have replaced the row: judge from the current one and the
            # registry, never the request's copy.
            row = self._state.catalog.row_for(key) or row
            if supervise.meta_for(self._state.home, key) is None:
                row["ownership"] = protocol.OWNERSHIP_OBSERVED
        if row.get("ownership") == protocol.OWNERSHIP_ATTACHED:
            self._send_json(200, {"ok": True, "note": (
                "already attached; the pending detach is cancelled"
                if undone == "cancelled" else "already attached")})
            return
        if not row.get("attachable"):
            self._send_error(
                422, "not_attachable",
                "this harness cannot be resumed under supervision",
            )
            return
        session = self._state.catalog.session_for(key)
        if session is None:
            self._send_error(404, "unknown_session", "no such session")
            return
        mode = str(body.get("mode") or "auto")
        if mode not in supervise.ATTACH_MODES:
            self._send_error(
                422, "bad_mode",
                f"mode must be one of {sorted(supervise.ATTACH_MODES)}",
            )
            return
        if mode == "headless" and session.tool_name != "claude":
            self._send_error(
                422, "bad_mode", "headless attach only exists for claude"
            )
            return
        if mode == "serve" and session.tool_name != "opencode":
            self._send_error(
                422, "bad_mode", "serve attach only exists for opencode"
            )
            return
        if mode == "appserver" and session.tool_name != "codex":
            self._send_error(
                422, "bad_mode", "appserver attach only exists for codex"
            )
            return
        if str(row.get("status") or "") == "active" and \
                key not in supervise.kept(self._state.home):
            # Busy beats environment: on a box with no tmux the truest
            # answer for a mid-turn session is still not_idle. A kept
            # tmux session is adopted as it is, so mid-turn is fine.
            self._send_error(
                409, "not_idle",
                "session is mid-turn; try again when it is idle",
            )
            return
        resolved = supervise.resolve_mode(session, mode)
        if resolved == "tmux" and supervise.tmux_bin() is None:
            # Environment failure, not a state conflict: 409/not_idle
            # would tell the client to retry, which never helps.
            self._send_error(
                422, "not_attachable", "tmux is not available"
            )
            return
        ok, note = supervise.attach(
            self._state.home, key, session,
            last_append_at=self._state.tail_manager.last_append_at(key),
            status=str(row.get("status") or ""),
            mode=mode,
        )
        if not ok:
            self._send_error(409, "not_idle", note)
            return
        self._state.tail_manager.wake(key)
        row["ownership"] = protocol.OWNERSHIP_ATTACHED
        self._emit_ownership(key)
        self._send_json(200, {"ok": True, "note": note})

    def _session_meta(self, key: str, action: str) -> None:
        """``POST /v1/sessions/{key}/pin|title``: kept on the hub for
        every session, satellite ones too (``sessionmeta.py``)."""
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        try:
            sessionmeta.update(self._state.home, key, action, body)
        except ValueError as exc:
            self._send_error(422, "bad_command", str(exc))
            return
        except sessionmeta.TooManyEntries as exc:
            self._send_error(409, "too_many_pins", str(exc))
            return
        except OSError:
            self._send_error(500, "store_failed", "could not save the change")
            return
        row = self._state.catalog.reapply_meta(key) or {}
        data = sessionmeta.fields(row)
        self._state.emit(
            key,
            protocol.event("session.meta", key, self._state.machine_id, data),
        )
        self._send_json(200, {"ok": True, **data})

    def _emit_ownership(self, key: str) -> None:
        row = self._state.catalog.row_for(key) or {}
        self._state.emit(
            key,
            protocol.event(
                "session.ownership", key, self._state.machine_id,
                {"ownership": row.get("ownership")},
            ),
        )

    def _read_upload(self):
        """Returns ``(name, data)`` or sends the error and returns None."""
        if not self._unread:  # None: unframed, which the hub doesn't decode
            self._send_error(400, "bad_body", "empty upload")
            return None
        data = self._take_body(attachments.MAX_BYTES)
        if data is None:
            self._send_error(413, "too_large", "attachment exceeds 20 MB")
            return None
        ctype = self.headers.get("Content-Type") or ""
        if ctype.startswith("multipart/form-data"):
            name, data = _parse_multipart(ctype, data)
            if data is None:
                self._send_error(
                    400, "bad_body", "unreadable multipart upload"
                )
                return None
            return name, data
        from urllib.parse import parse_qs, urlparse

        args = parse_qs(urlparse(self.path).query)
        name = (args.get("name") or [self.headers.get("X-Filename") or ""])[0]
        return name, data

    def _stage_attachment(self, key: str) -> None:
        upload = self._read_upload()
        if upload is None:
            return
        name, data = upload
        payload, err = attachments.stage(self._state.home, key, name, data)
        if payload is None:
            self._send_error(422, "bad_attachment", err or "stage failed")
            return
        self._send_json(200, payload)

    def _slash_commands(self, key: str) -> None:
        """``GET /v1/sessions/{key}/commands``: the "/" menu (``slash.py``)."""
        row = self._state.catalog.row_for(key)
        if row is None:
            self._send_error(404, "unknown_session", "no such session")
            return
        if row.get("remote"):
            body = slash.for_remote_row(self._state.home, key, row)
        else:
            body = slash.for_local_session(
                self._state.home, key, row,
                self._state.catalog.session_for(key),
            )
        self._send_json(200, body)

    def _messages(self, key: str, args) -> None:
        before = _int_arg(args.get("before"), 0)
        limit = _int_arg(args.get("limit"), 500)
        limit = max(1, min(limit, 2000))
        events, _trunc = self._state.journal.read_since(key, 0)
        messages = [
            cleaned for ev in events
            if ev.get("type") == "message.appended"
            and (cleaned := injected_mod.clean_event(ev)) is not None
        ]
        if before:
            messages = [m for m in messages if m.get("seq", 0) < before]
        tail_events = messages[-limit:]
        self._send_json(200, {
            "session": key,
            "messages": tail_events,
            "next_before": tail_events[0]["seq"]
            if len(messages) > len(tail_events) and tail_events
            else None,
            "has_more": len(messages) > len(tail_events),
        })

    def _usage_machines(self, own: dict) -> list[dict]:
        """``/v1/usage``'s per-machine list: the hub, then each satellite.

        Every entry carries ``reported_at`` (the reporting machine's
        clock when it took the snapshot) and ``received_at`` (the hub's
        clock when it got it); for the hub both are its ``server_time``.
        """
        now = own.get("server_time") or time.time()
        machines = catalog_mod.load_machines(self._state.home)
        out = [{
            "machine": self._state.machine,
            "harnesses": own.get("harnesses") or [],
            "window_days": own.get("window_days"),
            "reported_at": now,
            "received_at": now,
            "hub": True,
        }]
        for record in usage_mod.load_satellites(self._state.home):
            mid = record["machine_id"]
            if mid == self._state.machine_id:
                continue
            out.append({
                "machine": machines.get(mid) or {"id": mid, "name": mid},
                "harnesses": record["harnesses"],
                "window_days": record.get("window_days"),
                "reported_at": record.get("reported_at"),
                "received_at": record.get("received_at"),
                "hub": False,
            })
        return out

    # -- static PWA --------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        if path == "/app" or path.startswith("/app/"):
            self._serve_webapp(path)
            return
        web = Path(__file__).resolve().parent / "web"
        name = {
            "/": "index.html",
            "/pair": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
            "/manifest.webmanifest": "manifest.webmanifest",
            "/icon-192.png": "icon-192.png",
            "/icon-512.png": "icon-512.png",
        }.get(path)
        if name is None:
            self._send_error(404, "not_found", "unknown route")
            return
        self._send_asset(web / name)

    def _serve_webapp(self, path: str) -> None:
        """The web app (``quiver/webapp/static``) under ``/app/``.

        Only a flat file name of a known type is served, so no request
        can climb out of the folder. The pages fetch ``/v1/app/*`` with
        the same device credential the PWA pairs, which this origin
        shares.
        """
        if path == "/app":
            self.send_response(301)
            self.send_header("Location", "/app/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        name = path[len("/app/"):] or "index.html"
        if name in {"pages.js", "pages.css"}:
            self._send_body(
                webapp_pages.bundle(name[len("pages"):]),
                "text/javascript; charset=utf-8" if name.endswith(".js")
                else "text/css; charset=utf-8",
            )
            return
        if not webapp_static.is_asset_name(name):
            self._send_error(404, "not_found", "unknown route")
            return
        self._send_asset(webapp_static.STATIC_DIR / name)

    # -- paired device self-service --------------------------------------

    def _device_me(self, device_id: str) -> None:
        """``GET /v1/devices/me``: the caller's own pairing record, so a
        client can show (and rename) itself. The owner's unpaired tailnet
        device has no devices.json row — a stub with its node name."""
        if device_id.startswith("tailnet:"):
            self._send_json(200, {
                "id": device_id,
                "name": "This device",
                "node_name": self._peer_node_name(),
                "owner": True,
            })
            return
        row = pairing.load_devices(self._state.home).get(device_id)
        if row is None:
            self._send_error(404, "unknown_device", "no such device")
            return
        self._send_json(200, pairing.device_view(device_id, row))

    def _device_rename(self, device_id: str) -> None:
        """``POST /v1/devices/rename`` ``{name}``: the caller sets its own
        ``display_name``; an empty name clears it back to the paired one.
        Owner tailnet devices have no row to rename — same answer as the
        SSH key route."""
        if device_id.startswith("tailnet:"):
            self._send_error(
                409, "needs_pairing",
                "pair this device to rename it",
            )
            return
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        name = str(body.get("name") or "").strip()[:80]
        if not pairing.update_device(
            self._state.home, device_id, display_name=name or None
        ):
            self._send_error(404, "unknown_device", "no such device")
            return
        row = pairing.load_devices(self._state.home).get(device_id) or {}
        self._send_json(200, {
            "ok": True,
            "device_id": device_id,
            "name": str(row.get("display_name") or row.get("name") or ""),
        })

    def _ssh_key(self, device_id: str) -> None:
        """``POST /v1/ssh/key`` ``{public_key}``: store this device's SSH
        key ("" clears it) and bring ``authorized_keys`` in line."""
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        raw = body.get("public_key")
        key = None
        if raw != "":
            key = sshkeys.normalize_public_key(raw)
            if key is None:
                self._send_error(
                    422, "bad_key",
                    "public_key must be one ed25519 or ecdsa OpenSSH key",
                )
                return
        try:
            sshkeys.set_device_key(self._state.home, device_id, key)
        except KeyError:
            # The owner's own tailnet devices have no devices.json row
            # to keep a key on.
            self._send_error(
                409, "needs_pairing",
                "pair this device to give it an SSH key",
            )
            return
        except OSError as exc:
            self._send_error(500, "store_failed",
                             f"could not update authorized_keys: {exc}")
            return
        self._send_json(200, sshkeys.view(self._state.home, device_id))

    # -- web app page plugins (quiver/webapp/pages) ------------------------

    def _page_context(self, device_id: str | None):
        return webapp_pages.PageContext(
            home=self._state.home, state=self._state, device_id=device_id
        )

    def _page_call(self, fn, *call_args) -> None:
        try:
            body = fn(*call_args)
        except webapp_pages.PageError as exc:
            self._send_error(exc.status, exc.code, exc.message)
            return
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._send_error(503, "page_unavailable", str(exc))
            return
        self._send_json(200, body)

    def _page_get(self, name: str, args, device_id: str | None) -> None:
        page = webapp_pages.load(name)
        if page is None or not hasattr(page, "get"):
            self._send_error(404, "not_found", "unknown page")
            return
        flat = {k: str(v[0]) for k, v in args.items() if v}
        self._page_call(page.get, self._page_context(device_id), flat)

    def _page_post(self, rest: str, device_id: str | None) -> None:
        name, _sep, action = rest.partition("/")
        page = webapp_pages.load(name)
        if page is None or not hasattr(page, "post") or not action:
            self._send_error(404, "not_found", "unknown page action")
            return
        if self._bad_origin():
            self._send_error(403, "bad_origin", "request blocked by origin policy")
            return
        body = self._read_body()
        if body is None:
            self._send_error(400, "bad_body", "unreadable JSON body")
            return
        self._page_call(page.post, self._page_context(device_id), action, body)

    def _send_body(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_asset(self, target: Path) -> None:
        try:
            body = target.read_bytes()
        except OSError:
            self._send_error(404, "not_found", "missing asset")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".webmanifest": "application/manifest+json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
        }.get(target.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _parse_multipart(ctype: str, body: bytes) -> tuple[str, bytes | None]:
    """Minimal single-file multipart reader: returns (filename, bytes)."""
    try:
        # Parse the Content-Type header properly: `boundary=` may be
        # followed by more params (`; charset=utf-8`) or quoted — taking
        # everything after it would corrupt the delimiter marker.
        msg = email.message.Message()
        msg["content-type"] = ctype
        boundary = msg.get_param("boundary", header="content-type")
        # RFC 2231 continuation params come back as tuples — not a
        # boundary we can build a marker from.
        if not isinstance(boundary, str) or not boundary:
            return "", None
        marker = ("--" + boundary).encode()
        for part in body.split(marker):
            if b"Content-Disposition" not in part:
                continue
            head, _, blob = part.partition(b"\r\n\r\n")
            if not blob:
                continue
            # The CRLF before the closing boundary is separator, not
            # content — strip exactly that pair, never content bytes.
            if blob.endswith(b"\r\n"):
                blob = blob[:-2]
            name = ""
            for line in head.decode("utf-8", "replace").splitlines():
                if "content-disposition" not in line.lower():
                    continue
                for field in line.split(";"):
                    field = field.strip()
                    if field.startswith("filename="):
                        name = field.split("=", 1)[1].strip().strip('"')
            return name, blob
    except Exception:
        pass
    return "", None


def _int_arg(values, default: int) -> int:
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


class _BoundedHTTPServer(ThreadingHTTPServer):
    """Thread-per-connection, but capped — slowloris peers get refused
    at the socket instead of spawning unbounded handler threads."""

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(_MAX_WORKERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            # All workers busy: refuse the connection outright.
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def make_server(
    state: HubState, host: str, port: int
) -> ThreadingHTTPServer:
    server = _BoundedHTTPServer((host, port), _Handler)
    server.state = state  # type: ignore[attr-defined]
    return server


def _advertise_url(host: str, port: int) -> str:
    """The base URL handed to phones/ntfy — never a wildcard bind."""
    advertised = host
    if host in ("0.0.0.0", "::", ""):
        advertised = (
            catalog_mod.tailscale_ip()
            or socket.gethostname()
            or "127.0.0.1"
        )
    return f"http://{advertised}:{port}"


def check_public_url(url: str) -> str:
    """``url`` reduced to scheme and host[:port], no trailing slash.

    Raises ``ValueError`` unless it is an absolute http(s) URL.
    """
    parts = urlsplit((url or "").strip())
    try:
        # Reading .port validates it: non-numeric or out of range raises.
        bad = (parts.scheme not in {"http", "https"} or not parts.hostname
               or parts.port == 0)
    except ValueError:
        bad = True
    if bad:
        raise ValueError(
            "--public-url must be an http(s) URL, e.g. "
            "https://box.tailnet.ts.net:8477"
        )
    return f"{parts.scheme}://{parts.netloc}"


def _public_hosts(public_url: str) -> set[str]:
    """Host header values that name ``public_url``. A browser drops the
    scheme's default port, so 443/80 are allowed with and without it."""
    parts = urlsplit(public_url)
    default = 443 if parts.scheme == "https" else 80
    port = parts.port or default
    hosts = {f"{parts.hostname}:{port}"}
    if port == default:
        hosts.add(parts.hostname or "")
    return hosts


def _loopback_hosts(port: int) -> set[str]:
    """Host values a client on this machine uses for a loopback bind.
    A browser drops the http default port, so 80 is allowed bare."""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if port == 80:
        hosts |= {"127.0.0.1", "localhost", "[::1]"}
    return hosts


def _hostport(name: str, port: int) -> str:
    """``name:port`` spelled the way a Host header carries it — a v6
    literal needs brackets to be told apart from the port."""
    return f"[{name}]:{port}" if ":" in name else f"{name}:{port}"


def _hub_file(home: Path | None) -> Path:
    return remote_dir_for(home) / "hub.json"


def save_hub_config(home: Path | None, host: str, port: int,
                    public_url: str) -> None:
    """Record where the running hub listens and how phones reach it, so
    ``swe remote pair`` and ``status`` find it without flags."""
    path = _hub_file(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({
            "host": host, "port": port, "public_url": public_url,
        }, indent=1))
    except OSError:
        pass


def load_hub_config(home: Path | None = None) -> dict:
    """``{"host", "port", "public_url"}`` from the last ``serve``, or {}."""
    try:
        data = json.loads(_hub_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def configure(state: HubState, host: str, port: int,
              public_url: str = "") -> None:
    """Set the advertised URL, proxy trust and owner identity on
    ``state`` for a hub listening on ``host:port``.

    ``public_url`` is the address phones use when something fronts the
    hub; ``tailscale serve`` on a loopback bind is the supported case
    (``https://box.tailnet.ts.net:8477``), and only then are its
    forwarded-client headers trusted.
    """
    # The listen address and the advertised URL are different things:
    # ntfy action buttons need a dialable address — the public URL,
    # else the tailnet IP, then the machine hostname. (The CLI refuses
    # a wildcard bind without --unsafe-bind-any; _advertise_url still
    # turns one into something reachable.)
    state.base_url = public_url or _advertise_url(host, port)
    # Owner identity for pair-request approval: the tailnet login that
    # owns this node. A plain loopback bind and tagged nodes get none;
    # behind serve the node is looked up by its own tailnet IP.
    if public_url:
        state.allowed_hosts = _public_hosts(public_url)
        if host in {"127.0.0.1", "::1", "localhost"}:
            # The loopback bind still answers local clients directly
            # (swe remote status, a browser on this machine), so its
            # own Host names stay allowed next to the public URL's.
            state.allowed_hosts |= _loopback_hosts(port)
            state.trust_proxy = True

            def lookup() -> bool:
                """True once settled: an owner found, or a tagged node
                that never has one. False means ask again later."""
                ip = catalog_mod.tailscale_ip()
                peer = state.whois(ip) if ip else None
                if peer is None:
                    return False
                if not peer.get("tagged"):
                    state.owner_login = str(peer.get("login") or "")
                return True

            if not lookup():
                state.owner_retry = lookup
            return
    elif host in {"127.0.0.1", "::1", "localhost"}:
        # A loopback bind is where a DNS-rebinding page lands, so the
        # unauthenticated routes answer only the loopback names a
        # local client would use.
        state.allowed_hosts = _loopback_hosts(port)
        return
    if host not in {"127.0.0.1", "::1", "localhost", "0.0.0.0"}:
        peer = state.whois(host)
        own_ip = catalog_mod.tailscale_ip()
        if peer is None and own_ip and own_ip != host:
            # whois wants an IP; a bind by MagicDNS name is looked up
            # through the node's own address.
            peer = state.whois(own_ip)
        if not public_url:
            # Every spelling this node answers to — the bind value,
            # the tailnet IP and the MagicDNS names — so a pairing
            # link reaches the hub whichever one it names (`swe
            # remote pair` defaults the phone address to the tailnet
            # IP). The names belong to the node even when it is
            # tagged; a tag only means there is no owner login.
            state.allowed_hosts = {_hostport(host, port)}
            if own_ip:
                state.allowed_hosts.add(_hostport(own_ip, port))
            if peer:
                for name in (peer.get("node_name"),
                             peer.get("node_short")):
                    if name:
                        state.allowed_hosts.add(_hostport(name, port))
        if peer and not peer.get("tagged"):
            state.owner_login = str(peer.get("login") or "")


def serve(state: HubState, host: str, port: int,
          public_url: str = "") -> int:
    """Blocking entry point for ``swe remote serve``."""
    configure(state, host, port, public_url)
    state.start()
    server = make_server(state, host, port)
    # Only a hub that owns the port may say where it is: a second copy
    # that fails to bind must not overwrite the running one's address.
    save_hub_config(state.home, host, port, public_url)
    # SIGTERM (launchd/systemd stop) must take the same shutdown path as
    # ^C — without it the process dies mid-write and queued sends drop
    # silently. shutdown() can't run on the serve_forever thread.
    def _on_sigterm(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    prev_sigterm = None
    if threading.current_thread() is threading.main_thread():
        prev_sigterm = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)
        server.server_close()
        state.stop()
    return 0
