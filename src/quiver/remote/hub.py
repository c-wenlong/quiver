"""The hub: REST + SSE over ``http.server``, device auth, event fan-out.

Stdlib-only by design — SSE is a held HTTP GET, so no WebSocket
dependency enters the tree. ``HubState`` is the reusable core: the
catalog refreshes sessions, the tail manager turns transcripts into
events, and one ``emit`` funnel writes every event to the journal and
the subscriber bus. The satellite agent builds the same core minus the
listener.

Auth: every ``/v1/*`` route except ``/v1/status`` and ``/v1/pair`` wants
``Authorization: Bearer <device credential>`` from ``devices.json``.
"""

from __future__ import annotations

import email.message
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from quiver import __version__
from quiver.remote import (
    attachments,
    catalog as catalog_mod,
    inject,
    notify,
    pairing,
    protocol,
    supervise,
    tail,
)
from quiver.remote.journal import Journal

DEFAULT_PORT = 8477
_MAX_BODY = 4 << 20  # 4 MB JSON cap; attachments go through their own route
_SSE_HEARTBEAT_S = 15.0
# A request that stalls mid-read (declared Content-Length, no bytes)
# can't hold a handler thread forever; SSE writes get the same cap,
# which is harmless — a healthy client's socket never blocks for 30 s.
_REQ_TIMEOUT_S = 30.0
_MAX_WORKERS = 64
# How often an open SSE stream re-checks that its device wasn't revoked.
_REVOCATION_CHECK_S = 5.0

# Journal bucket for non-session events (machine.online, hub-level notify).
GLOBAL_KEY = "_global"

# Refuse new commands for a satellite whose unacked queue is deeper
# than this — the journal trims at ~4k records and silently dropped
# commands are worse than an honest 429.
MAX_SAT_PENDING = 2000


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
        )
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
        # The hub's reachable base — set by serve() before start() so
        # ntfy action buttons can point back at this host.
        self.base_url = ""

    def _journal_has(self, key: str) -> bool:
        return self.journal.last_seq(key) > 0

    # The one funnel: every event is journaled, then fanned out.
    def emit(self, key: str, event: dict) -> dict:
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
        targets: list = [
            (self._catalog_loop, "remote-catalog"),
            (self._tail_loop, "remote-tail"),
        ]
        if notify.load_config(self.home).get("enabled"):
            notifier = notify.Notifier(
                self, self.base_url, subscribe_key=GLOBAL_KEY
            )
            targets.append((notifier.run, "remote-notify"))
        for target, name in targets:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)

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
                adopted = supervise.adopt_pending(
                    self.home, self.catalog.tail_rows()
                )
                if adopted:
                    # Newly adopted keys need a second refresh pass so the
                    # rows carry ownership: attached.
                    self.catalog.refresh()
                # Ownership can change outside HTTP — `swe remote
                # attach`, pending adoption, dead-tmux pruning — and the
                # phone needs the event either way.
                for row in self.catalog.rows():
                    new_own = row.get("ownership")
                    if prev_own.get(row["key"]) not in (None, new_own):
                        self.emit(
                            row["key"],
                            protocol.event(
                                "session.ownership", row["key"],
                                self.machine_id,
                                {"ownership": new_own},
                            ),
                        )
                self.tail_manager.sync(self.catalog.tail_rows())
            except Exception:
                pass
            self._stop.wait(catalog_mod.REFRESH_INTERVAL_S)

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
            self._stop.wait(0.25)


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

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > _MAX_BODY:
            return None
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeError):
            return None
        return data if isinstance(data, dict) else None

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
                pairing.touch_device(self._state.home, device_id)
            except Exception:
                pass
        return device_id

    @staticmethod
    def _device_revoked(home: Path | None, device_id: str) -> bool:
        row = pairing.load_devices(home).get(device_id) or {}
        return bool(row.get("revoked_at"))

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
            self._send_json(200, {
                "ok": True,
                "v": protocol.PROTOCOL_VERSION,
                "version": __version__,
                "machine": self._state.machine,
                "uptime_s": round(time.time() - self._state.started_at),
            })
            return

        # Everything else under /v1 wants a paired device credential.
        device_id = None
        if path.startswith("/v1/"):
            device_id = self._device()
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

        if path == "/v1/machines":
            self._send_json(200, {
                "machines": list(
                    catalog_mod.load_machines(self._state.home).values()
                )
            })
            return

        if path == "/v1/inbox":
            self._send_json(200, {"items": self._state.catalog.inbox()})
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
            self._send_error(404, "not_found", "unknown route")
            return

        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/v1/pair":
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            result = pairing.exchange(
                self._state.home,
                str(body.get("token") or ""),
                str(body.get("name") or ""),
            )
            if result is None:
                self._send_error(
                    403, "pair_failed", "unknown or expired pair token"
                )
                return
            self._send_json(200, result)
            return

        device_id = self._device()
        if device_id is None:
            self._send_error(401, "unauthorized", "pair this device first")
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
            self._send_json(200, {"ok": True, "rows": len(rows)})
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
            body = self._read_body()
            if body is None:
                self._send_error(400, "bad_body", "unreadable JSON body")
                return
            machine = str(body.get("machine") or "")
            harness = str(body.get("harness") or body.get("tool") or "")
            cwd = str(body.get("cwd") or "")
            prompt = str(body.get("prompt") or "")
            if machine and machine != self._state.machine_id:
                queued = self._state.queue_command(
                    machine, "launch", "",
                    {"harness": harness, "cwd": cwd, "prompt": prompt},
                )
                if queued is None:
                    self._send_error(429, "backlog_full",
                                     "machine is too far behind")
                    return
                self._send_json(
                    202, {"ok": True, "queued": True,
                          "note": "queued for satellite"}
                )
                return
            # Snapshot the catalog before launch: adoption later refuses
            # any of these ids, so a pre-existing same-cwd session can
            # never be claimed as the one we just started.
            real_cwd = os.path.realpath(cwd)
            known = [
                s.session_id
                for _k, s, _st in self._state.catalog.tail_rows()
                if s.tool_name == harness
                and os.path.realpath(s.path or "") == real_cwd
            ]
            ok, note, _name = supervise.launch_new(
                self._state.home, harness, cwd, prompt, known_ids=known
            )
            if not ok:
                self._send_error(422, "launch_failed", note)
                return
            self._send_json(202, {"ok": True, "note": note})
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

    def _injector(self, key: str):
        meta = supervise.meta_for(self._state.home, key)
        return inject.injector_for(self._state.home, key, meta)

    def _forward(
        self, key: str, mid: str, action: str, row: dict
    ) -> None:
        """Route a write to the satellite owning ``key``.

        Same ownership rules as local writes: observed sessions only
        accept ``attach``. Everything lands in the machine's command
        journal, so the phone gets 202 whether the satellite is live or
        asleep — results arrive later as ``command.result`` events.
        """
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
                "status": row.get("status") or "",
            })
            if queued is None:
                self._send_error(429, "backlog_full",
                                 "machine is too far behind")
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
                    "mode": body.get("mode") or "tmux",
                    "status": row.get("status") or "",
                })
            else:
                if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
                    self._send_error(
                        409, "not_attached",
                        "session is observed: read-only until attached",
                    )
                    return
                queued = self._state.queue_command(
                    mid, "control", key,
                    {"ctl": op, "value": cmd.get("value") or ""},
                )
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
        paths = []
        for ref in cmd["attachments"]:
            path = attachments.resolve(self._state.home, key, ref)
            if path is None:
                self._send_error(
                    422, "bad_attachment", f"unknown attachment {ref}"
                )
                return
            paths.append(path)
        cmd["attachments"] = paths
        session = self._state.catalog.session_for(key)
        status = str(row.get("status") or "")
        injector = self._injector(key)
        result = injector.send(session, cmd, status)
        if not result.ok:
            self._send_error(409, "send_failed", result.note)
            return
        self._send_json(200, {"ok": True, "note": result.note})

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
        if op == "attach":
            self._attach(key, row, body)
            return
        if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
            self._send_error(
                409, "not_attached",
                "session is observed: read-only until attached",
            )
            return
        session = self._state.catalog.session_for(key)
        result = self._injector(key).control(
            session, op, str(cmd.get("value") or "")
        )
        if not result.ok:
            self._send_error(409, "control_failed", result.note)
            return
        if op in {"kill", "detach"}:
            attachments.drop_session(self._state.home, key)
            row["ownership"] = protocol.OWNERSHIP_OBSERVED
            self._emit_ownership(key)
        self._send_json(200, {"ok": True, "note": result.note})

    def _attach(self, key: str, row: dict, body: dict) -> None:
        if row.get("ownership") == protocol.OWNERSHIP_ATTACHED:
            self._send_json(200, {"ok": True, "note": "already attached"})
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
        mode = str(body.get("mode") or "tmux")
        if mode == "headless" and session.tool_name != "claude":
            self._send_error(
                422, "bad_mode", "headless attach only exists for claude"
            )
            return
        if str(row.get("status") or "") == "active":
            # Busy beats environment: on a box with no tmux the truest
            # answer for a mid-turn session is still not_idle.
            self._send_error(
                409, "not_idle",
                "session is mid-turn; try again when it is idle",
            )
            return
        if mode == "tmux" and supervise.tmux_bin() is None:
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
        row["ownership"] = protocol.OWNERSHIP_ATTACHED
        self._emit_ownership(key)
        self._send_json(200, {"ok": True, "note": note})

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
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_error(400, "bad_body", "empty upload")
            return None
        if length > attachments.MAX_BYTES:
            self._send_error(413, "too_large", "attachment exceeds 20 MB")
            return None
        data = self.rfile.read(length)
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

    def _messages(self, key: str, args) -> None:
        before = _int_arg(args.get("before"), 0)
        limit = _int_arg(args.get("limit"), 500)
        limit = max(1, min(limit, 2000))
        events, _trunc = self._state.journal.read_since(key, 0)
        messages = [
            ev for ev in events if ev.get("type") == "message.appended"
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

    # -- static PWA --------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        web = Path(__file__).resolve().parent / "web"
        name = {
            "/": "index.html",
            "/pair": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
            "/manifest.webmanifest": "manifest.webmanifest",
            "/icon.svg": "icon.svg",
            "/icon-192.png": "icon-192.png",
            "/icon-512.png": "icon-512.png",
        }.get(path)
        if name is None:
            self._send_error(404, "not_found", "unknown route")
            return
        target = web / name
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


def serve(state: HubState, host: str, port: int) -> int:
    """Blocking entry point for ``swe remote serve``."""
    state.base_url = f"http://{host}:{port}"
    state.start()
    server = make_server(state, host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.stop()
    return 0
