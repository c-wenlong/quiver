"""The MCP gateway: one copy of each shared stdio server, served over HTTP.

``swe mcp gateway serve`` listens on 127.0.0.1 and serves each shared
hub server at ``/mcp/<name>`` as MCP streamable HTTP. The first request
for a server starts it and runs the MCP handshake with it once; every
harness session after that talks to the same process.

One stdio process is one MCP session, so the gateway multiplexes:

- A client's ``initialize`` is answered from the result the server gave
  the gateway's own handshake, and its ``notifications/initialized`` is
  dropped. ``ping`` is answered here, and only the spec's client-to-
  server requests are forwarded (``FORWARDED_REQUESTS``).
- Every forwarded request gets a gateway-wide id, mapped back to the
  client's id on the way out, so two sessions both sending id 1 never
  collide. ``notifications/cancelled`` is mapped the same way.
- Answers go back as one JSON body per POST. The gateway opens no SSE
  stream (GET is 405, which the spec allows), so a server's
  notifications (progress, list_changed, log messages) are dropped, and
  the capabilities it advertises lose ``listChanged`` and ``subscribe``.
- A server's own requests to the client (sampling, roots, elicitation)
  are refused with "method not found": there is no single client to ask.

That is right for servers without per-session state, which is the only
kind ``swe mcp gateway share`` lets in. A server that exits is started
again on the next request, with a growing delay when it keeps dying.

Requests carrying an ``Origin`` header or a ``Host`` other than the
loopback address are refused: harnesses send neither, and a web page
that reaches 127.0.0.1 through the browser always sends Origin. Stdlib
only, like the rest of the core CLI.
"""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from quiver import __version__
from quiver.mcp.gateway import HOST, PATH_PREFIX

# The handshake can include a build (`go run`) or a dependency sync
# (`uv run`) on a cold start.
INIT_TIMEOUT_S = 180.0
# A tool call can run for minutes; the harness's own timeout is usually
# shorter, so this only frees a request nobody is waiting for any more.
REQUEST_TIMEOUT_S = 1800.0
MAX_BODY = 32 * 1024 * 1024
# A server's stderr log starts over once it would pass this size.
MAX_LOG = 5 * 1024 * 1024
# Restart backoff for a server that keeps exiting soon after it starts.
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 60.0
HEALTHY_AFTER_S = 60.0
# How long a stopped server's group gets between SIGTERM and SIGKILL.
KILL_GRACE_S = 5.0
# How often a request may re-read mcp.json and the secrets file.
RELOAD_EVERY_S = 2.0

DEFAULT_PROTOCOL = "2025-06-18"

# The requests a client may send a server under the MCP spec. Anything
# else is answered "method not found" here, not forwarded: Claude Code
# probes an HTTP server with `server/discover` before `initialize`, and an
# older Python SDK server exits on a method it doesn't know, which would
# take the one shared copy down for every session.
FORWARDED_REQUESTS = frozenset({
    "tools/list", "tools/call",
    "resources/list", "resources/read", "resources/templates/list",
    "prompts/list", "prompts/get",
    "completion/complete", "logging/setLevel",
})
# Client notifications worth passing on; the rest (initialized, roots and
# progress) describe a client the server never met.
FORWARDED_NOTIFICATIONS = frozenset({"notifications/cancelled"})

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal a process group; False when it has no members left."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # a member we can't signal is still a member
    return True


def _group_alive(pgid: int) -> bool:
    return _signal_group(pgid, 0)


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _plain_capabilities(result: dict) -> dict:
    """The server's initialize result, minus what the gateway can't relay.

    ``listChanged`` and ``subscribe`` promise notifications, and the
    gateway drops every notification.
    """
    out = dict(result)
    caps = out.get("capabilities")
    if isinstance(caps, dict):
        caps = dict(caps)
        for key in ("tools", "prompts", "resources"):
            if isinstance(caps.get(key), dict):
                caps[key] = {k: v for k, v in caps[key].items()
                             if k not in ("listChanged", "subscribe")}
        out["capabilities"] = caps
    return out


class _Waiter:
    __slots__ = ("event", "response")

    def __init__(self):
        self.event = threading.Event()
        self.response: dict | None = None


class _Child:
    """One running server process and the requests waiting on it."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.started = time.monotonic()
        self.write_lock = threading.Lock()
        self.pending: dict[int, _Waiter] = {}
        self.pending_lock = threading.Lock()
        self.alive = True

    def send(self, message: dict) -> None:
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self.write_lock:
            if self.proc.stdin.closed:
                raise BrokenPipeError("server exited")
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def close(self) -> None:
        """Close the pipes once the process is gone."""
        with self.write_lock:
            for pipe in (self.proc.stdin, self.proc.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass

    def expect(self, gid: int) -> _Waiter:
        waiter = _Waiter()
        with self.pending_lock:
            if not self.alive:
                raise BrokenPipeError("server exited")
            self.pending[gid] = waiter
        return waiter

    def forget(self, gid: int) -> None:
        with self.pending_lock:
            self.pending.pop(gid, None)

    def deliver(self, gid, response: dict) -> None:
        with self.pending_lock:
            waiter = self.pending.pop(gid, None)
        if waiter is not None:
            waiter.response = response
            waiter.event.set()

    def fail_all(self, message: str) -> None:
        with self.pending_lock:
            self.alive = False
            waiting = list(self.pending.items())
            self.pending.clear()
        for gid, waiter in waiting:
            waiter.response = _error(gid, INTERNAL_ERROR, message)
            waiter.event.set()


class Backend:
    """One shared server: started on first use, restarted after it exits."""

    def __init__(self, name: str, spec: dict, log_dir: Path,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen):
        self.name = name
        self.spec = spec
        self.log_dir = log_dir
        self._popen = popen
        self._ids = itertools.count(1)
        self._ids_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._child: _Child | None = None
        self._init_result: dict | None = None
        self.starts = 0
        self.last_error = ""
        self._failures = 0
        self._retry_at = 0.0
        self._stopped = False

    # -- lifecycle -------------------------------------------------------

    def _next_id(self) -> int:
        with self._ids_lock:
            return next(self._ids)

    def _spawn(self) -> _Child:
        argv = [self.spec["command"], *[str(a) for a in self.spec.get("args") or []]]
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.spec.get("env") or {}).items()})
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Its own process group, so stopping it also stops whatever a
        # wrapper (uv run, go run, sh -c) started under it.
        proc = self._popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, cwd=str(Path.home()),
                           start_new_session=True)
        child = _Child(proc)
        threading.Thread(target=self._read, args=(child,), daemon=True,
                         name=f"mcp-gateway-{self.name}").start()
        threading.Thread(target=self._log, args=(proc.stderr,), daemon=True,
                         name=f"mcp-gateway-{self.name}-log").start()
        return child

    def _log(self, stderr) -> None:
        """Copy the server's stderr to its log, starting over past MAX_LOG.

        Checked on every write, not only at start: a noisy server that stays
        up for months would otherwise fill the disk.
        """
        path = self.log_dir / f"{self.name}.log"
        try:
            log = open(path, "ab")
        except OSError:
            log = None
        try:
            if log is not None:
                log.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} "
                          f"starting {self.name}\n".encode())
                log.flush()
            for line in stderr:
                if log is None:
                    continue  # still drain it, or the server blocks on a full pipe
                if log.tell() + len(line) > MAX_LOG:
                    log.seek(0)
                    log.truncate()
                    log.write(b"--- log started over: it reached its size limit\n")
                log.write(line)
                log.flush()
        except (OSError, ValueError):
            pass
        finally:
            if log is not None:
                log.close()
            try:
                stderr.close()
            except OSError:
                pass

    def _read(self, child: _Child) -> None:
        for line in child.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue  # a server printing a banner on stdout
            if not isinstance(message, dict):
                continue
            if "method" in message:
                if "id" in message:
                    # A request to "the client": there are many, so nobody
                    # can answer it.
                    try:
                        child.send(_error(message["id"], METHOD_NOT_FOUND,
                                          "the Quiver MCP gateway does not relay "
                                          "server-to-client requests"))
                    except OSError:
                        break
                continue
            if "id" in message:
                child.deliver(message["id"], message)
        code = child.proc.wait()
        lived = time.monotonic() - child.started
        child.fail_all(f"{self.name} exited (status {code})")
        child.close()
        with self._start_lock:
            if self._child is child:
                self._child = None
                self._init_result = None
                if not self._stopped:
                    self.last_error = f"exited with status {code} after {lived:.0f}s"
                    self._note_failure(lived)

    def _note_failure(self, lived: float) -> None:
        if lived >= HEALTHY_AFTER_S:
            self._failures = 0
        self._failures += 1
        delay = min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** (self._failures - 1))
        self._retry_at = time.monotonic() + delay

    def ready(self, protocol: str) -> tuple[_Child, dict]:
        """The running child and its initialize result, starting it if needed.

        Raises RuntimeError saying why it can't run.
        """
        with self._start_lock:
            if self._stopped:
                raise RuntimeError(f"{self.name} is no longer shared")
            if self._child is not None and self._init_result is not None:
                return self._child, self._init_result
            wait = self._retry_at - time.monotonic()
            if wait > 0:
                raise RuntimeError(f"{self.name} keeps exiting ({self.last_error}); "
                                   f"next start in {wait:.0f}s")
            try:
                child = self._spawn()
            except OSError as exc:
                self.last_error = f"cannot start: {exc}"
                self._note_failure(0)
                raise RuntimeError(f"{self.name}: {self.last_error}") from exc
            self.starts += 1
            self._child = child
            try:
                result = self._handshake(child, protocol)
            except (RuntimeError, OSError) as exc:
                self._kill(child)
                self._child = None
                self._note_failure(0)
                if isinstance(exc, RuntimeError):
                    raise
                # The server exited before it could read the handshake.
                self.last_error = f"exited before initialize ({exc})"
                raise RuntimeError(f"{self.name}: {self.last_error}") from exc
            self._init_result = result
            return child, result

    def _handshake(self, child: _Child, protocol: str) -> dict:
        gid = self._next_id()
        waiter = child.expect(gid)
        child.send({"jsonrpc": "2.0", "id": gid, "method": "initialize", "params": {
            "protocolVersion": protocol,
            "capabilities": {},
            "clientInfo": {"name": "quiver-mcp-gateway", "version": __version__},
        }})
        if not waiter.event.wait(INIT_TIMEOUT_S):
            child.forget(gid)
            self.last_error = f"no answer to initialize in {INIT_TIMEOUT_S:.0f}s"
            raise RuntimeError(f"{self.name}: {self.last_error}")
        response = waiter.response or {}
        if "result" not in response:
            detail = (response.get("error") or {}).get("message", "no result")
            self.last_error = f"initialize failed: {detail}"
            raise RuntimeError(f"{self.name}: {self.last_error}")
        child.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.last_error = ""
        return _plain_capabilities(response["result"])

    def _kill(self, child: _Child) -> None:
        """Stop the server's whole process group, not just its leader.

        A wrapper (uv run, go run) can exit while the real server it
        started lives on, so the group is signalled even when the leader
        is gone, and waited on until no member is left.
        """
        proc = child.proc
        if not _signal_group(proc.pid, signal.SIGTERM) and proc.poll() is None:
            proc.terminate()
        deadline = time.monotonic() + KILL_GRACE_S
        while time.monotonic() < deadline:
            proc.poll()  # reap the leader, or the group never empties
            if not _group_alive(proc.pid):
                return
            time.sleep(0.05)
        _signal_group(proc.pid, signal.SIGKILL)
        if proc.poll() is None:
            proc.kill()
        # Killed members linger until their parent (or launchd) reaps them.
        deadline = time.monotonic() + KILL_GRACE_S
        while time.monotonic() < deadline:
            proc.poll()
            if not _group_alive(proc.pid):
                return
            time.sleep(0.05)

    def stop(self) -> None:
        with self._start_lock:
            self._stopped = True
            child, self._child, self._init_result = self._child, None, None
        if child is not None:
            child.fail_all(f"{self.name} stopped")
            self._kill(child)

    # -- traffic ---------------------------------------------------------

    def call(self, message: dict, protocol: str, on_sent: Callable[[int], None] | None = None,
             timeout: float = REQUEST_TIMEOUT_S) -> dict:
        """Forward one request; the response comes back under the caller's id."""
        try:
            child, _ = self.ready(protocol)
        except RuntimeError as exc:
            return _error(message.get("id"), INTERNAL_ERROR, str(exc))
        gid = self._next_id()
        try:
            waiter = child.expect(gid)
            child.send({**message, "id": gid})
        except OSError:
            child.forget(gid)
            return _error(message.get("id"), INTERNAL_ERROR, f"{self.name} exited")
        if on_sent is not None:
            on_sent(gid)
        if not waiter.event.wait(timeout):
            child.forget(gid)
            try:
                child.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                            "params": {"requestId": gid, "reason": "gateway timeout"}})
            except OSError:
                pass
            return _error(message.get("id"), INTERNAL_ERROR,
                          f"{self.name} did not answer in {timeout:.0f}s")
        response = dict(waiter.response or {})
        response["id"] = message.get("id")
        return response

    def notify(self, message: dict) -> None:
        child = self._child
        if child is None:
            return  # nothing is running to tell
        try:
            child.send(message)
        except OSError:
            pass

    def status(self) -> dict:
        child = self._child
        running = child is not None and child.proc.poll() is None
        return {
            "name": self.name,
            "state": "running" if running else ("error" if self.last_error else "idle"),
            "pid": child.proc.pid if running else None,
            "uptime_s": round(time.monotonic() - child.started) if running else None,
            "starts": self.starts,
            "error": self.last_error or None,
        }


SpecLoader = Callable[[], tuple[dict, dict]]


def load_hub_specs() -> tuple[dict, dict]:
    """``({name: resolved spec}, {name: why it can't run})`` for shared servers."""
    from quiver.mcp.cli import MCP_SOURCE_FILE, get_mcp_handler, load_json
    from quiver.mcp.gateway import is_shared
    from quiver.mcp.secrets import load_secrets, resolve, unresolved_names

    secrets = load_secrets()
    handler = get_mcp_handler("quiver")
    specs: dict[str, dict] = {}
    problems: dict[str, str] = {}
    # load_json raises on a file it can't parse: the caller keeps the
    # servers it has rather than reading that as "nothing is shared".
    servers = (load_json(MCP_SOURCE_FILE) or {}).get("mcpServers") or {}
    if not isinstance(servers, dict):
        raise ValueError("mcp.json: mcpServers is not an object")
    for name, raw in servers.items():
        if not is_shared(raw):
            continue
        canonical = handler.parse(raw)
        if not canonical.get("command") or canonical.get("url"):
            problems[name] = "not a stdio server"
            continue
        missing = unresolved_names(raw, secrets)
        if missing:
            problems[name] = "no value for " + ", ".join(missing)
            continue
        specs[name] = resolve(canonical, secrets)
    return specs, problems


def _watched_files() -> list[Path]:
    from quiver.paths import mcp_secrets_path_for, mcp_source_file_for

    return [mcp_source_file_for(), mcp_secrets_path_for()]


class Gateway:
    """The shared servers, kept in step with mcp.json."""

    def __init__(self, log_dir: Path, load: SpecLoader = load_hub_specs,
                 watched: Callable[[], list[Path]] = _watched_files,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen):
        self.log_dir = log_dir
        self._load = load
        self._watched = watched
        self._popen = popen
        self._lock = threading.Lock()
        self.backends: dict[str, Backend] = {}
        self.problems: dict[str, str] = {}
        self.load_error: str | None = None
        self._stamp: tuple | None = None
        self._checked = 0.0
        # (session, client id) -> gateway id, for notifications/cancelled.
        self._inflight: dict[tuple[str, object], tuple[Backend, int]] = {}
        self._inflight_lock = threading.Lock()
        self.reload(force=True)

    def _fingerprint(self) -> tuple:
        out = []
        for path in self._watched():
            try:
                st = path.stat()
                out.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((str(path), None, None))
        return tuple(out)

    def reload(self, force: bool = False) -> None:
        """Re-read the hub when it changed; stop servers that changed or left."""
        now = time.monotonic()
        with self._lock:
            if not force and now - self._checked < RELOAD_EVERY_S:
                return
            self._checked = now
            stamp = self._fingerprint()
            if not force and stamp == self._stamp:
                return
            self._stamp = stamp
            try:
                specs, problems = self._load()
            except Exception as exc:  # noqa: BLE001 - any bad read keeps the last good set
                # A half-written or broken mcp.json must not stop every
                # running server. Keep them, say why, and read again once
                # the file changes.
                self.load_error = f"cannot read the hub, keeping the last good servers: {exc}"
                return
            self.load_error = None
            self.problems = problems
            stale = []
            for name, backend in list(self.backends.items()):
                if specs.get(name) != backend.spec:
                    stale.append(self.backends.pop(name))
            for name, spec in specs.items():
                if name not in self.backends:
                    self.backends[name] = Backend(name, spec, self.log_dir, popen=self._popen)
        for backend in stale:
            backend.stop()

    def backend(self, name: str) -> Backend | None:
        self.reload()
        with self._lock:
            return self.backends.get(name)

    def stop(self) -> None:
        with self._lock:
            backends = list(self.backends.values())
        for backend in backends:
            backend.stop()

    def status(self) -> dict:
        with self._lock:
            backends = sorted(self.backends.values(), key=lambda b: b.name)
            problems = dict(self.problems)
        rows = [b.status() for b in backends]
        rows += [{"name": n, "state": "error", "pid": None, "uptime_s": None,
                  "starts": 0, "error": why} for n, why in sorted(problems.items())]
        return {"version": __version__, "pid": os.getpid(), "servers": rows,
                "error": self.load_error}

    # -- one message ---------------------------------------------------------

    def handle(self, backend: Backend, message, session: str, protocol: str):
        """The response to one JSON-RPC message, or None for none."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(message.get("id") if isinstance(message, dict) else None,
                          INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method = message.get("method")
        if not isinstance(method, str):
            return None  # a client's answer to a request the gateway never sent
        if "id" not in message:
            self._notification(backend, message, session)
            return None
        msg_id = message["id"]
        if method == "initialize":
            params = message.get("params") or {}
            wanted = params.get("protocolVersion") if isinstance(params, dict) else None
            try:
                _, result = backend.ready(wanted if isinstance(wanted, str) else DEFAULT_PROTOCOL)
            except RuntimeError as exc:
                return _error(msg_id, INTERNAL_ERROR, str(exc))
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method not in FORWARDED_REQUESTS:
            return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")
        key = (session, json.dumps(msg_id))

        def sent(gid: int) -> None:
            with self._inflight_lock:
                self._inflight[key] = (backend, gid)

        try:
            return backend.call(message, protocol, on_sent=sent)
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)

    def _notification(self, backend: Backend, message: dict, session: str) -> None:
        method = message["method"]
        if method not in FORWARDED_NOTIFICATIONS:
            return  # e.g. initialized: the gateway's own handshake sent it
        if method == "notifications/cancelled":
            params = message.get("params") or {}
            key = (session, json.dumps(params.get("requestId")))
            with self._inflight_lock:
                hit = self._inflight.get(key)
            if hit is None or hit[0] is not backend:
                return
            message = {**message, "params": {**params, "requestId": hit[1]}}
        backend.notify(message)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "quiver-mcp-gateway"
    sys_version = ""

    gateway: Gateway  # set on the subclass by make_server
    port: int

    def log_message(self, fmt, *args):  # noqa: D401 - quiet by design
        pass

    # -- plumbing --------------------------------------------------------

    def _send(self, status: int, body: bytes = b"", content_type: str = "application/json",
              headers: dict | None = None) -> None:
        if status >= 400:
            # An error can come before the body was read. Left on a kept-
            # alive connection, those bytes would be parsed as the next
            # request, so a refused page could smuggle a tool call in.
            self.close_connection = True
            headers = {**(headers or {}), "Connection": "close"}
        self.send_response(status)
        if body or status != HTTPStatus.ACCEPTED:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload, headers: dict | None = None) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), headers=headers)

    def _refused(self) -> bool:
        """Refuse what a browser could send: any Origin, or a foreign Host."""
        if self.headers.get("Origin"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin requests are refused"})
            return True
        host = (self.headers.get("Host") or "").lower()
        allowed = {f"{HOST}:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}
        if host not in allowed:
            self._json(HTTPStatus.FORBIDDEN, {"error": f"unexpected Host {host!r}"})
            return True
        return False

    def _backend(self) -> Backend | None:
        path = self.path.split("?", 1)[0]
        if not path.startswith(PATH_PREFIX):
            return None
        return self.gateway.backend(path[len(PATH_PREFIX):].rstrip("/"))

    # -- verbs -----------------------------------------------------------

    def do_GET(self):
        if self._refused():
            return
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
        elif path == "/status":
            self.gateway.reload()
            self._json(HTTPStatus.OK, self.gateway.status())
        elif self._backend() is not None:
            # No server-initiated stream: the spec lets a server say 405.
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "no SSE stream; POST only"},
                       headers={"Allow": "POST, DELETE"})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "no such shared server"})

    def do_HEAD(self):
        # `swe mcp status` probes a URL server with HEAD: say whether the
        # server is shared here, without starting it.
        if self._refused():
            return
        found = self._backend() is not None or self.path in ("/health", "/status")
        self._send(HTTPStatus.OK if found else HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        if self._refused():
            return
        # Sessions hold no state here, so ending one has nothing to free.
        status = HTTPStatus.OK if self._backend() is not None else HTTPStatus.NOT_FOUND
        self._send(status)

    def do_POST(self):
        if self._refused():
            return
        backend = self._backend()
        if backend is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "no such shared server"})
            return
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            # MCP clients always send JSON; a form or text/plain POST is
            # the one shape a web page can send without a preflight.
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            length = -1
        if length < 0:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
            return
        if length > MAX_BODY:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, _error(None, PARSE_ERROR, "invalid JSON"))
            return
        session = self.headers.get("Mcp-Session-Id") or ""
        protocol = self.headers.get("MCP-Protocol-Version") or DEFAULT_PROTOCOL
        headers = {}
        messages = body if isinstance(body, list) else [body]
        if any(isinstance(m, dict) and m.get("method") == "initialize" for m in messages):
            session = uuid.uuid4().hex
            headers["Mcp-Session-Id"] = session
        replies = [r for r in (self.gateway.handle(backend, m, session, protocol)
                               for m in messages) if r is not None]
        if not replies:
            self._send(HTTPStatus.ACCEPTED, headers=headers)
        elif isinstance(body, list):
            self._json(HTTPStatus.OK, replies, headers=headers)
        else:
            self._json(HTTPStatus.OK, replies[0], headers=headers)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # A harness closing its connection mid-request is routine, not
        # worth a traceback in the log.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(gateway: Gateway, port: int, host: str = HOST) -> ThreadingHTTPServer:
    """Bind the gateway's HTTP server (port 0 picks a free one, for tests)."""
    handler = type("GatewayHandler", (_Handler,), {"gateway": gateway, "port": port})
    server = _Server((host, port), handler)
    handler.port = server.server_address[1]
    return server


def serve(port: int, log_dir: Path, out=sys.stdout) -> int:
    """Run until SIGTERM or Ctrl-C, then stop every shared server."""
    gateway = Gateway(log_dir)
    server = make_server(gateway, port)

    def _terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    names = ", ".join(sorted(gateway.backends)) or "none yet"
    print(f"{datetime.now().isoformat(timespec='seconds')}  MCP gateway on "
          f"http://{HOST}:{port}{PATH_PREFIX}<name>  (shared: {names})", file=out, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        gateway.stop()
        print(f"{datetime.now().isoformat(timespec='seconds')}  MCP gateway stopped",
              file=out, flush=True)
    return 0
