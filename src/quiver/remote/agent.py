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
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from quiver.paths import remote_dir_for
from quiver.remote import attachments, catalog as catalog_mod
from quiver.remote import inject, protocol, supervise, tail

AGENT_FILE = "agent.json"          # satellite's stored hub credential
PUSH_BATCH = 50                    # events per /v1/agent/events POST
RECONNECT_S = 5.0
CATALOG_PUSH_S = 10.0


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
    path.write_text(json.dumps(conf, indent=1))
    os.chmod(path, 0o600)
    return path


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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def pair_agent(hub: str, token: str, name: str,
               home: Path | None = None) -> dict | None:
    """Exchange a one-time pair token for this satellite's credential."""
    status, payload = _post(
        hub, "/v1/pair", "",  # no auth needed for /v1/pair
        {"token": token, "name": name or "satellite"},
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
        self.outbox: "list[dict]" = []
        self._outbox_lock = threading.Lock()
        self._stop = threading.Event()
        self._last_cmd_seq = self._load_cmd_cursor()
        self.tail_manager = tail.TailManager(
            emit=self._capture, machine_id=self.machine_id, home=self.home,
        )

    def _cmd_cursor_file(self) -> Path:
        return agent_file_for(self.home).parent / "agent-cursor"

    def _load_cmd_cursor(self) -> int:
        """Last handled command seq, so a restart never re-executes."""
        try:
            return int(self._cmd_cursor_file().read_text().strip())
        except (OSError, ValueError):
            return 0

    def _save_cmd_cursor(self) -> None:
        path = self._cmd_cursor_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(self._last_cmd_seq))
        except OSError:
            pass

    # -- upstream ----------------------------------------------------------

    def _capture(self, key: str, event: dict) -> dict:
        """TailManager's emit: buffer for the uplink thread."""
        with self._outbox_lock:
            self.outbox.append(event)
        return event

    def _push_events(self) -> bool:
        with self._outbox_lock:
            batch, self.outbox = self.outbox[:PUSH_BATCH], \
                self.outbox[PUSH_BATCH:]
        if not batch:
            return True
        status, _payload = _post(
            self.hub, "/v1/agent/events", self.cred,
            {"machine_id": self.machine_id, "events": batch},
        )
        if status != 200:
            with self._outbox_lock:
                self.outbox = batch + self.outbox
            return False
        return True

    def _push_catalog(self) -> bool:
        rows = self.catalog.refresh()
        status, _ = _post(
            self.hub, "/v1/agent/catalog", self.cred,
            {"machine_id": self.machine_id, "rows": rows},
        )
        return status == 200

    def _register(self) -> bool:
        status, _ = _post(
            self.hub, "/v1/agent/register", self.cred,
            {"machine": self.machine},
        )
        return status == 200

    # -- downlink ----------------------------------------------------------

    def _command_stream(self) -> None:
        """GET /v1/agent/commands as an SSE stream; dispatch each command."""
        url = (
            f"{self.hub}/v1/agent/commands"
            f"?machine={self.machine_id}&last={self._last_cmd_seq}"
        )
        req = urllib.request.Request(url, headers=_headers(self.cred, None))
        try:
            resp = urllib.request.urlopen(req, timeout=90)
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
                            self._handle_frame(b"\n".join(data_lines))
                            data_lines = []
                        continue
                    if line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip())
        except Exception:
            pass
        if not self._stop.is_set():
            time.sleep(1)

    def _handle_frame(self, blob: bytes) -> None:
        try:
            ev = json.loads(blob.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(ev, dict) or ev.get("type") != "command":
            return
        seq = ev.get("seq")
        if isinstance(seq, int):
            if seq <= self._last_cmd_seq:
                return
            self._last_cmd_seq = seq
            self._save_cmd_cursor()
        self._dispatch(ev)

    def _dispatch(self, cmd: dict) -> None:
        """Execute one command envelope; report back as command.result."""
        data = cmd.get("data") if isinstance(cmd.get("data"), dict) else {}
        op = str(data.get("op") or "")
        key = str(cmd.get("session") or "")
        command_id = str(data.get("command_id") or "")
        ok, note = self._execute(key, op, data)
        result = protocol.event(
            "command.result", key, self.machine_id,
            {"command_id": command_id, "op": op, "ok": ok, "note": note},
        )
        with self._outbox_lock:
            self.outbox.append(result)

    def _execute(self, key: str, op: str, data: dict) -> tuple[bool, str]:
        session = self.catalog.session_for(key)
        try:
            if op == "launch":
                harness = str(data.get("harness") or "")
                cwd = str(data.get("cwd") or "")
                # Same pre-launch snapshot the hub takes: any session
                # the catalog already knows must not be adopted.
                real_cwd = os.path.realpath(cwd)
                known = [
                    s.session_id
                    for _k, s, _st in self.catalog.tail_rows()
                    if s.tool_name == harness
                    and os.path.realpath(s.path or "") == real_cwd
                ]
                ok, note, _ = supervise.launch_new(
                    self.home, harness, cwd,
                    str(data.get("prompt") or ""), known_ids=known,
                )
                return ok, note
            if op == "attachment":
                ref = str(data.get("ref") or "")
                # The hub staged the upload under the hub-scoped key —
                # that's what the pull URL carries, not our local key.
                hub_key = str(data.get("hub_key") or key)
                blob = _get_bytes(
                    self.hub,
                    f"/v1/agent/attachments/{hub_key}/{ref}", self.cred,
                )
                if blob is None:
                    return False, "attachment fetch failed"
                payload, err = attachments.stage(
                    self.home, key, ref, blob
                )
                return (payload is not None), (err or "staged")
            if session is None:
                return False, "unknown session on this machine"
            if op == "attach":
                return supervise.attach(
                    self.home, key, session,
                    last_append_at=self.tail_manager.last_append_at(key),
                    status=str(data.get("status") or ""),
                    mode=str(data.get("mode") or "tmux"),
                )
            injector = inject.injector_for(
                self.home, key, supervise.meta_for(self.home, key)
            )
            if op == "message":
                # Resolve attachment refs to the paths the attachment
                # op staged locally, same as the hub's send path.
                paths = []
                for ref in data.get("attachments") or []:
                    path = attachments.resolve(self.home, key, str(ref))
                    if path is None:
                        return False, f"unknown attachment {ref}"
                    paths.append(path)
                result = injector.send(
                    session,
                    {"text": data.get("text") or "",
                     "mode": data.get("mode") or "queue",
                     "attachments": paths},
                    str(data.get("status") or "done"),
                )
                return result.ok, result.note
            if op == "approval":
                result = injector.approval(
                    session,
                    str(data.get("request_id") or ""),
                    str(data.get("decision") or ""),
                )
                return result.ok, result.note
            if op == "control":
                result = injector.control(
                    session,
                    str(data.get("ctl") or ""),
                    str(data.get("value") or ""),
                )
                return result.ok, result.note
            return False, f"unknown op {op}"
        except Exception as exc:
            return False, str(exc)

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
                self.tail_manager.sync(self.catalog.tail_rows())
            except Exception:
                pass
            self._stop.wait(CATALOG_PUSH_S)

    def _tail_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tail_manager.tick()
            except Exception:
                pass
            self._stop.wait(0.25)

    def _downlink_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._command_stream()
            except Exception:
                self._stop.wait(RECONNECT_S)

    def run(self) -> int:
        self._register()
        self._push_catalog()
        threads = [
            threading.Thread(target=self._uplink_loop, daemon=True,
                             name="agent-uplink"),
            threading.Thread(target=self._catalog_loop, daemon=True,
                             name="agent-catalog"),
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
        for t in threads:
            t.join(timeout=2)
        return 0


def serve(hub: str, credential: str,
          home: Path | str | None = None) -> int:
    """Blocking entry point for ``swe remote agent``."""
    return RemoteAgent(hub, credential, home).run()
