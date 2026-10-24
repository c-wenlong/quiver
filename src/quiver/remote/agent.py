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
import queue as queue_mod
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from quiver.paths import remote_dir_for
from quiver.remote import attachments, catalog as catalog_mod, slash
from quiver.remote import inject, journal, protocol, supervise, tail
from quiver.remote import usage as usage_mod
from quiver.remote.detach import PendingDetaches

AGENT_FILE = "agent.json"          # satellite's stored hub credential
PUSH_BATCH = 50                    # events per /v1/agent/events POST
RECONNECT_S = 5.0
CATALOG_PUSH_S = 10.0
# The hub refuses JSON bodies over 4 MiB (hub._MAX_BODY); a catalog push
# stays under it with room to spare.
CATALOG_BODY_MAX = (4 << 20) - (64 << 10)
OUTBOX_KEY = "_outbox"             # journal key for unsent uplink events


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
        # Last "/" menu payload the hub accepted; see _push_catalog.
        self._slash_digest: str | None = None
        self._slash_skipped: str | None = None
        self.outbox: "list[dict]" = []
        self._outbox_lock = threading.Lock()
        self._stop = threading.Event()
        # Commands dispatch on the stream thread except ``message``,
        # which can run minutes (headless claude): those go through
        # ``_work_q`` so a queued interrupt/detach isn't stuck behind it.
        self._work_q: "queue_mod.Queue" = queue_mod.Queue()
        # ``detach`` with ``mode: when_idle``, waiting out a turn here.
        self.detaches = PendingDetaches(self._stop)
        # Fresh per-session status for those waits; tests stub it.
        from quiver.sessions.status import session_statuses
        self.status_of = session_statuses
        # Two cursors: ``_last_cmd_seq`` dedups frames seen on the wire;
        # ``_done_cmd_seq`` is the persisted *completed* floor the
        # ``?last=`` replay starts from — a crash mid-dispatch replays
        # the command instead of skipping it.
        self._last_cmd_seq = self._load_cmd_cursor()
        self._done_cmd_seq = self._last_cmd_seq
        self._done_set: set[int] = set()
        self.tail_manager = tail.TailManager(
            emit=self._capture, machine_id=self.machine_id, home=self.home,
        )
        # Requeue uplink events the hub never acknowledged — the
        # transcript cursor is saved on read, so an in-memory buffer
        # would lose them across a restart.
        self._outbox_ack = self._load_outbox_ack()
        self._outbox = journal.Journal(self.home)
        events, _truncated = self._outbox.read_since(
            OUTBOX_KEY, self._outbox_ack)
        self.outbox = list(events)

    def _cmd_cursor_file(self) -> Path:
        return agent_file_for(self.home).parent / "agent-cursor.json"

    def _load_cmd_cursor(self) -> int:
        """Last *completed* command seq for this hub; 0 for a new one."""
        path = self._cmd_cursor_file()
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get("hub") == self.hub:
                return int(data.get("seq") or 0)
            return 0  # paired to a different hub — replay its queue
        except (OSError, ValueError):
            pass
        # Pre-migration cursor was a bare int for whatever hub was
        # paired then; honor it once rather than replay the backlog.
        legacy = path.with_name("agent-cursor")
        try:
            return int(legacy.read_text().strip())
        except (OSError, ValueError):
            return 0

    def _save_cmd_cursor(self) -> None:
        path = self._cmd_cursor_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(
                {"hub": self.hub, "seq": self._done_cmd_seq}))
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
            self._outbox_ack_file().write_text(str(self._outbox_ack))
        except OSError:
            pass

    def _mark_done(self, seq: int | None) -> None:
        """Advance the completed cursor to the highest contiguous seq."""
        if not isinstance(seq, int):
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
        return stamped or event

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
        seq = batch[-1].get("seq")
        if isinstance(seq, int) and seq > self._outbox_ack:
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
            # Names the key a phone's launch became; the hub maps it to
            # the hub-scoped key and answers GET /v1/launches with it.
            lid = (supervise.meta_for(self.home, key) or {}).get("launch_id")
            if lid:
                self._capture(key, protocol.event(
                    "session.launched", key, self.machine_id,
                    {"launch_id": lid},
                ))
        body = {"machine_id": self.machine_id, "rows": rows}
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
        fields = {
            "launchable": [item["name"] for item in launchable],
            "harness_names": catalog_mod.harness_names(launchable),
            "untracked_harnesses": catalog_mod.untracked(launchable),
        }
        if all(self.machine.get(k) == v for k, v in fields.items()):
            return False
        self.machine.update(fields)
        return True

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
            f"?machine={self.machine_id}&last={self._done_cmd_seq}"
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
        if (ev.get("data") or {}).get("op") == "message":
            # Headless sends block for the whole turn — queue them so a
            # later interrupt/detach isn't stuck behind the run.
            self._work_q.put((seq, ev))
            return
        self._dispatch(ev)
        self._mark_done(seq)

    def _work_loop(self) -> None:
        while not self._stop.is_set():
            try:
                seq, cmd = self._work_q.get(timeout=0.5)
            except queue_mod.Empty:
                continue
            try:
                self._dispatch(cmd)
            finally:
                self._mark_done(seq)

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
        stamped = self._outbox.append(OUTBOX_KEY, event)
        with self._outbox_lock:
            self.outbox.append(stamped or event)

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
                )
                return ok, note, False
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
                    return False, "attachment fetch failed", False
                # Keep the hub's ref name — the message op that follows
                # resolves attachments by it.
                payload, err = attachments.place(
                    self.home, key, ref, blob
                )
                return payload is not None, (err or "staged"), False
            if session is None:
                return False, "unknown session on this machine", False
            if op == "attach":
                # Same as the hub: attaching again cancels a when_idle
                # detach (one already running finishes first).
                undone = self.detaches.cancel(key, "cancelled by attach")
                ok, note = supervise.attach(
                    self.home, key, session,
                    last_append_at=self.tail_manager.last_append_at(key),
                    status=str(data.get("status") or ""),
                    mode=str(data.get("mode") or "auto"),
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
                # The status in the queued command is stale — the
                # session may have gone idle or errored while we were
                # offline. Our own tail manager knows the current one.
                live = (self.tail_manager.statuses.get(key)
                        or str(data.get("status") or "done"))
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
                self.tail_manager.sync(self.catalog.tail_rows())
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
        while not self._stop.is_set():
            try:
                self.tail_manager.tick()
            except Exception:
                pass
            self.tail_manager.wait(0.25)

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
            threading.Thread(target=self._work_loop, daemon=True,
                             name="agent-worker"),
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
        self.tail_manager.interrupt()
        self.detaches.close()
        for t in threads:
            t.join(timeout=2)
        return 0


def serve(hub: str, credential: str,
          home: Path | str | None = None) -> int:
    """Blocking entry point for ``swe remote agent``."""
    return RemoteAgent(hub, credential, home).run()
