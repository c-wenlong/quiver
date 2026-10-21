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

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from quiver import __version__
from quiver.remote import catalog as catalog_mod
from quiver.remote import pairing, protocol, tail
from quiver.remote.journal import Journal

DEFAULT_PORT = 8477
_MAX_BODY = 4 << 20  # 4 MB JSON cap; attachments go through their own route
_SSE_HEARTBEAT_S = 15.0

# Journal bucket for non-session events (machine.online, hub-level notify).
GLOBAL_KEY = "_global"


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

    def _journal_has(self, key: str) -> bool:
        return self.journal.last_seq(key) > 0

    # The one funnel: every event is journaled, then fanned out.
    def emit(self, key: str, event: dict) -> dict:
        written = self.journal.append(key, event)
        self.bus.publish(key, written)
        if key != GLOBAL_KEY:
            # The global journal mirrors everything so /v1/events replays
            # a whole-machine timeline, not only machine.* events.
            self.journal.append(GLOBAL_KEY, written)
        return written

    def start(self) -> None:
        self._register_machine()
        for target, name in (
            (self._catalog_loop, "remote-catalog"),
            (self._tail_loop, "remote-tail"),
        ):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)

    def _register_machine(self) -> None:
        catalog_mod.upsert_machine(self.home, self.machine)

    def _catalog_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.catalog.refresh()
                self.tail_manager.sync(self.catalog.tail_rows())
            except Exception:
                pass
            self._stop.wait(catalog_mod.REFRESH_INTERVAL_S)

    def _tail_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tail_manager.tick()
            except Exception:
                pass
            self._stop.wait(0.25)


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "quiver-remote"
    protocol_version = "HTTP/1.1"
    state: HubState  # stamped on the server, set in make_server

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
        return hit[0] if hit else None

    # -- SSE --------------------------------------------------------------

    def _sse_stream(self, key: str | None, after_seq: int) -> None:
        journal_key = key if key is not None else GLOBAL_KEY
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        replay, truncated = self._state.journal.read_since(
            journal_key, after_seq
        )
        try:
            if truncated:
                self._sse_write({"type": "journal.truncated", "seq": after_seq})
            for ev in replay:
                self._sse_write(ev)
            self._sse_write({"type": "journal.caught-up", "seq": self._state.journal.last_seq(journal_key)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return
        q = self._state.bus.subscribe(key)
        try:
            while True:
                try:
                    ev = q.get(timeout=_SSE_HEARTBEAT_S)
                except queue.Empty:
                    try:
                        self.wfile.write(b": ka\n\n")
                        self.wfile.flush()
                    except (
                        BrokenPipeError, ConnectionResetError,
                        TimeoutError, OSError,
                    ):
                        return
                    continue
                try:
                    self._sse_write(ev)
                except (
                    BrokenPipeError, ConnectionResetError,
                    TimeoutError, OSError,
                ):
                    return
        finally:
            self._state.bus.unsubscribe(q)

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
            self._sse_stream(None, after)
            return

        if path.startswith("/v1/sessions/"):
            stem = path[len("/v1/sessions/"):]
            key_part, sep, action = stem.rpartition("/")
            key = unquote(key_part if sep else stem)
            if sep and action == "events":
                after = _int_arg(args.get("last"), 0)
                self._sse_stream(key, after)
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

        if path == "/v1/sessions":
            self._send_error(
                501, "not_implemented",
                "launching sessions lands with the write path (R2)",
            )
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
            if action in {"message", "approval", "control", "attachments"}:
                if row.get("ownership") != protocol.OWNERSHIP_ATTACHED:
                    self._send_error(
                        409, "not_attached",
                        "session is observed: read-only until attached",
                    )
                    return
                self._send_error(
                    501, "not_implemented",
                    "the write path lands in a later milestone",
                )
                return
            self._send_error(404, "not_found", "unknown route")
            return

        self._send_error(404, "not_found", "unknown route")

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


def _int_arg(values, default: int) -> int:
    if not values:
        return default
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return default


def make_server(
    state: HubState, host: str, port: int
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.state = state  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server


def serve(state: HubState, host: str, port: int) -> int:
    """Blocking entry point for ``swe remote serve``."""
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
