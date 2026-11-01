"""Write path: the phone's commands into a supervised session.

Registry keyed by harness, same shape as the parser/reader registries.
Two channels ship in R2:

- ``ClaudeHeadlessInjector`` (tier A): one ``claude -p --resume <id>
  --output-format stream-json --include-partial-messages`` invocation
  per prompt. The transcript file gets the appended turn, so the tailer
  picks up the same ``message.appended`` events as a TUI run; the
  partial-message lines on stdout feed ``message.delta`` only.
  Permissions are fixed per invocation (print mode has no mid-turn
  prompts), so approvals answer ``approval_not_supported``.
- ``TmuxInjector`` (tier B): ``send-keys`` for prompts, menu digits for
  approvals, Escape for interrupt — best-effort against whatever TUI is
  running under the supervisor.
- ``OpencodeServeInjector`` (tier A): REST against a managed ``opencode
  serve`` bound to the session's project — ``prompt_async`` for
  messages, permission replies for approvals, ``abort`` for interrupt.
  A background ``GET /event`` SSE subscription translates permission
  asks into ``approval.requested`` events.
- ``CodexAppServerInjector`` (tier A): a persistent ``codex app-server``
  JSON-RPC process per session — ``thread/resume`` once, ``turn/start``
  per message, ``turn/interrupt`` for interrupt; server-initiated
  approval requests become ``approval.requested`` and decisions are
  written back as JSON-RPC responses.

``queue|steer``: queue refuses while a turn looks active; steer writes
immediately where the channel accepts mid-turn input (tmux buffers
keystrokes, app-server queues server-side). Headless invocations are
atomic — steer degrades to queue and one run is in flight per session
at a time; a degraded send reports ``degraded=True`` so the client can
say so.

Live text: the three tier-A channels push each text chunk they read
into a ``stream.DeltaStream`` (``deltas``), which coalesces them into
``message.delta`` events that are never journaled. The transcript's
``message.appended`` stays the record; tmux panes never stream.
"""

from __future__ import annotations

import collections
import json
import os
import select
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

from swe.remote import attachments, supervise
from swe.remote.launch_model import provider_split
from swe.reports import transcripts as _tr
from swe.sessions.models import Session
from swe.sessions.resume import account_env

# A headless claude run gets this long before we kill it; a queued send
# waits on the in-flight run with half that patience.
HEADLESS_TIMEOUT_S = 300
_QUEUE_WAIT_S = 150

# Fallback when the pane's option rows don't parse: claude's order —
# 1 allow once, 2 always allow, 3 deny. Numbered menus are answered by
# label via ``supervise.menu_key_for`` instead, because positions vary
# (devin puts "No" at 8). Other TUIs ignore stray digits harmlessly.
_APPROVAL_KEYS = {"allow": "1", "always": "2", "deny": "3"}

_OPS_TEXT = {
    "compact": "/compact",
    "clear": "/clear",
    "model": "/model {value}",
    "effort": "/effort {value}",
}


def control_text(op: str, value: str) -> str:
    """The pane text a control op types (``/model sonnet``); "" for an
    op the TUI has no line for."""
    template = _OPS_TEXT.get(op)
    if template is None:
        return ""
    return template.format(value=value).rstrip()


def switch_model(home: Path | None, session_key: str, mode: str,
                 tool: str, value: str) -> InjectResult:
    """The ``model`` control op on an API channel: record the pick on
    the session's supervised row, which the channel's next turn reads
    (the same field an attach-time pick lands in). Nothing is sent now,
    so the switch applies from the next turn, mid-turn or not. The
    value is held to the launch rule and to the channel's own shape
    rule (``supervise.channel_tuning_error``)."""
    from swe.remote import protocol

    try:
        value = protocol._launch_value({"value": value}, "value") or ""
    except ValueError as exc:
        return InjectResult(False, str(exc))
    if not value:
        return InjectResult(False, "model needs a value")
    blocked = supervise.channel_tuning_error(mode, tool, value, None)
    if blocked:
        return InjectResult(False, blocked)
    if not supervise.set_meta(home, session_key, "model", value):
        return InjectResult(
            False, "session detached or its row could not be saved; "
                   "model unchanged")
    return InjectResult(True, f"model set to {value} from the next turn")


def _prompt(session: Session, cmd: dict, harness: str = "") -> str:
    """The message text with its attachments referenced the way the
    harness takes them (``@path`` or a bracketed path). A channel tied
    to one harness names it; a tmux pane runs whatever the session's
    harness is."""
    return attachments.prompt_text(
        str(cmd.get("text") or ""),
        [str(p) for p in cmd.get("attachments") or []],
        harness or getattr(session, "tool_name", "") or "",
    )


class InjectResult(NamedTuple):
    ok: bool
    note: str = ""
    # steer asked for mid-turn delivery but the channel queued it —
    # clients surface this as "sent (queued)" instead of pretending.
    degraded: bool = False


class TmuxInjector:
    """Tier B: everything is a keystroke into the supervised pane."""

    # request_ids already answered — a retried approval must not answer
    # whichever menu happens to be on screen now. Class-level because an
    # injector is built per request; bounded so a long-lived hub does
    # not grow it forever.
    _answered: dict[str, "collections.deque[str]"] = {}
    # One lock per pane serializes a whole approval: the hub serves
    # each client on its own thread, and two answers racing the same
    # menu would otherwise interleave their arrow keys mid-walk.
    _pane_locks: dict[str, threading.Lock] = {}

    def __init__(self, home: Path | None, session_key: str) -> None:
        self.home = home
        self.key = session_key
        # Adopted sessions live under a launch-time tmux name, not the
        # derived one — channel_name resolves either.
        self.name = supervise.channel_name(home, session_key)

    def send(self, session: Session, cmd: dict, status: str) -> InjectResult:
        if cmd.get("mode") == "queue" and status == "active":
            return InjectResult(
                False, "session is mid-turn; steer to write anyway"
            )
        text = _prompt(session, cmd)
        if not text.strip():
            return InjectResult(False, "empty message")
        # A detach could land between the route's ownership check and
        # this write; re-check under the pane lock's read side.
        if supervise.meta_for(self.home, self.key) is None:
            return InjectResult(
                False, "session detached; refusing the write"
            )
        if supervise.send_text(self.name, text):
            return InjectResult(True, "sent to tmux pane")
        return InjectResult(False, "tmux session is not reachable")

    def approval(self, session: Session, request_id: str, decision: str) -> InjectResult:
        # A semantic name maps to a menu row; a bare digit is the client
        # answering the exact numbered option the event listed.
        if decision not in _APPROVAL_KEYS and not (
            decision.isdigit() and len(decision) <= 2
        ):
            return InjectResult(False, f"unknown decision {decision}")
        # Checks, pane capture, the key walk and the answered mark are
        # one critical section per pane — a concurrent reply must see
        # either the request still open or the menu already consumed.
        lock = self._pane_locks.setdefault(self.key, threading.Lock())
        with lock:
            return self._approval(session, request_id, decision)

    def _approval(self, session: Session, request_id: str, decision: str) -> InjectResult:
        # The TUI menu does not display request ids, so we cannot bind a
        # reply to *the* on-screen prompt. What we can bind: reject a
        # request_id we already answered, and require a visible
        # permission prompt before sending a menu key — a stale retry
        # then can't consume a different pending menu under its old id.
        answered = self._answered.setdefault(
            self.key, collections.deque(maxlen=512)
        )
        if request_id in answered:
            return InjectResult(False, "approval already answered")
        # When the tailer issued a request_id for the prompt currently
        # on screen, only that id may answer it — a stale or superseded
        # id cannot consume the new menu.
        outstanding = supervise.pending_approval(self.key)
        if outstanding and request_id != outstanding:
            return InjectResult(False, "stale approval request id")
        pane = supervise.capture_pane(self.name)
        if not supervise.pane_shows_approval(pane):
            return InjectResult(
                False, "no approval prompt is on screen"
            )
        # The id was minted for the prompt that was on screen then —
        # re-fingerprint now so a menu swapped in between polls can't
        # be answered by the old id.
        fp = supervise.pending_approval_fp(self.key)
        if fp and fp != supervise.approval_fingerprint(pane):
            return InjectResult(
                False, "approval prompt changed since the request"
            )
        if decision.isdigit():
            # Only a row the menu actually shows may be sent — a digit
            # that isn't listed can't reach into an unrelated prompt.
            rows, kind, cursor = supervise.option_menu(pane)
            offered = {n for n, _ in rows}
            if decision not in offered:
                return InjectResult(
                    False, "that menu option is not on screen"
                )
            if kind == "choices":
                # An unnumbered arrow-select list takes no digit key —
                # walk the cursor onto the row and confirm.
                if self._send_choice_keys(
                        rows, int(decision) - 1, cursor):
                    answered.append(request_id)
                    return InjectResult(
                        True, f"selected menu option {decision}"
                    )
                return InjectResult(
                    False, "tmux session is not reachable"
                )
            key = decision
        else:
            rows, kind, _cursor = supervise.option_menu(pane)
            if kind == "choices" and rows:
                # An arrow-select list has no allow/always mapping —
                # only "deny" carries over, as the menu's own cancel.
                if decision != "deny":
                    return InjectResult(
                        False,
                        "this prompt takes a menu option, not a decision",
                    )
                if supervise.send_key(self.name, "Escape"):
                    answered.append(request_id)
                    return InjectResult(True, "escape sent")
                return InjectResult(
                    False, "tmux session is not reachable"
                )
            key = supervise.menu_key_for(pane, decision) \
                or _APPROVAL_KEYS[decision]
        if supervise.send_key(self.name, key):
            answered.append(request_id)
            return InjectResult(True, f"sent menu key {key}")
        return InjectResult(False, "tmux session is not reachable")

    def _send_choice_keys(
        self, rows: list, target: int, cursor: int | None
    ) -> bool:
        """Arrow to the 0-based ``target`` row and confirm.

        The cursor-marked row anchors the walk; without one, saturating
        Up lands on row 1 from wherever the list sits.
        """
        if cursor is not None:
            delta = target - cursor
            keys = ["Down" if delta > 0 else "Up"] * abs(delta)
        else:
            keys = ["Up"] * len(rows) + ["Down"] * target
        for key in keys + ["Enter"]:
            if not supervise.send_key(self.name, key):
                return False
        return True

    def control(self, session: Session, op: str, value: str) -> InjectResult:
        if op == "interrupt":
            ok = supervise.send_key(self.name, "Escape")
            return InjectResult(ok, "escape sent" if ok else "pane unreachable")
        if op in {"kill", "detach"}:
            return InjectResult(
                supervise.detach(self.home, self.key),
                "supervisor stopped; session is observed again",
            )
        text = control_text(op, value)
        if not text:
            return InjectResult(False, f"unsupported control {op}")
        ok = supervise.send_text(self.name, text)
        return InjectResult(ok, f"sent {text!r}" if ok else "pane unreachable")


class ClaudeHeadlessInjector:
    """Tier A: one print-mode invocation per prompt, serialized."""

    _locks: dict[str, threading.Lock] = {}
    _procs: dict[str, subprocess.Popen] = {}
    # Cleared once a claude refuses --include-partial-messages (a build
    # from before the flag): runs go on without live text.
    _partial = True

    def __init__(self, home: Path | None, session_key: str,
                 deltas=None, machine: str = "") -> None:
        self.home = home
        self.key = session_key
        self.deltas = deltas
        self.machine = machine
        # Set when the last run's claude refused --include-partial-messages
        # at argv parsing, before any JSON record (so before any work).
        self._flag_refused = False
        self._locks.setdefault(session_key, threading.Lock())

    @staticmethod
    def argv(session: Session, text: str, model: str = "",
             partial: bool = True) -> list[str]:
        args = [
            "claude", "-p", "--resume", session.session_id,
            "--output-format", "stream-json",
            "--verbose",
        ]
        # Token-level ``stream_event`` lines on stdout, for live text.
        # They change nothing on disk: the session's JSONL transcript
        # and --resume behave as without the flag.
        if partial:
            args.append("--include-partial-messages")
        # A launch-time pick recorded on the session's meta rides every
        # print-mode invocation as ``--model`` — the headless channel's
        # own mechanism for the launch ``model`` field.
        if model:
            args += ["--model", model]
        # "--" ends the flags: a message that starts with "-" (a pasted
        # list, "--help") is the prompt, not an option.
        return [*args, "--", text]

    def _run(self, session: Session, argv: list[str]) -> tuple[bool, str]:
        env = account_env(session)
        try:
            proc = subprocess.Popen(
                argv, cwd=session.path or None,
                env={**os.environ, **env} if env else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            return False, f"claude failed to launch: {exc}"
        self._procs[self.key] = proc
        # A detach that landed between Popen() and registration missed
        # this child — kill it ourselves rather than run detached.
        if supervise.meta_for(self.home, self.key) is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            self._procs.pop(self.key, None)
            return False, "session detached; refusing the write"
        # The stdout loop can block mid-read forever on a stalled run, so
        # the deadline lives in a watchdog: kill() closes the pipe and
        # ends the iteration.
        timed_out = {"hit": False}

        def _expire() -> None:
            timed_out["hit"] = True
            try:
                proc.kill()
            except OSError:
                pass

        watchdog = threading.Timer(HEADLESS_TIMEOUT_S, _expire)
        watchdog.daemon = True
        watchdog.start()
        self._flag_refused = False
        saw_json = False
        live = _ClaudeLive(self.deltas, self.key, self.machine)
        try:
            result_text = ""
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    if not saw_json and _refuses_partial_flag(line):
                        self._flag_refused = True
                    continue
                saw_json = True
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") == "stream_event":
                    live.feed(rec)
                    continue
                if rec.get("type") == "result":
                    result_text = str(rec.get("result") or "")
                    if rec.get("is_error"):
                        return False, result_text or "claude reported an error"
            if timed_out["hit"]:
                return False, "headless run timed out"
            rc = proc.wait(timeout=10)
            if rc != 0:
                return False, result_text or f"claude exited {rc}"
            return True, result_text or "turn complete"
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, "headless run timed out"
        finally:
            watchdog.cancel()
            live.close()
            # Reap whatever the run left: an errored or killed child
            # otherwise stays a zombie with an open pipe.
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            self._procs.pop(self.key, None)

    def send(self, session: Session, cmd: dict, status: str) -> InjectResult:
        text = _prompt(session, cmd, "claude")
        if not text.strip():
            return InjectResult(False, "empty message")
        # steer degrades to queue: a print-mode run is atomic.
        degraded = cmd.get("mode") == "steer"
        lock = self._locks[self.key]
        if not lock.acquire(timeout=_QUEUE_WAIT_S):
            return InjectResult(False, "a headless turn is still in flight")
        try:
            # A detach could land between the route's ownership check and
            # this write; under the session lock the registry is the
            # last word.
            meta = supervise.meta_for(self.home, self.key)
            if meta is None:
                return InjectResult(
                    False, "session detached; refusing the write"
                )
            model = str(meta.get("model") or "")
            partial = ClaudeHeadlessInjector._partial
            ok, note = self._run(session, self.argv(
                session, text, model, partial=partial))
            if not ok and partial and self._flag_refused:
                # This claude predates the flag and stopped at argv
                # parsing, before it printed a record or reached the
                # model: run the turn again without live text.
                ClaudeHeadlessInjector._partial = False
                ok, note = self._run(session, self.argv(
                    session, text, model, partial=False))
            if ok and degraded:
                note = (note or "turn complete") + \
                    " (steer queued: headless runs are atomic)"
            return InjectResult(ok, note, degraded=ok and degraded)
        finally:
            lock.release()

    def approval(self, session: Session, request_id: str, decision: str) -> InjectResult:
        return InjectResult(
            False, "approval_not_supported: headless permissions are "
                   "fixed per invocation"
        )

    def control(self, session: Session, op: str, value: str) -> InjectResult:
        if op == "interrupt":
            proc = self._procs.pop(self.key, None)
            if proc is None:
                return InjectResult(False, "no headless run in flight")
            proc.kill()
            try:
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            return InjectResult(True, "killed the in-flight run")
        if op in {"kill", "detach"}:
            proc = self._procs.pop(self.key, None)
            if proc is not None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            supervise.unmark(self.home, self.key)
            return InjectResult(True, "detached; session is observed again")
        if op == "model":
            # The next print-mode run reads it off the row as --model
            # (send(), under the session lock); a run in flight keeps
            # the model it started with.
            return switch_model(self.home, self.key, "headless", "claude",
                                value)
        return InjectResult(
            False, f"unsupported_control: {op} needs a live TUI"
        )


def _refuses_partial_flag(line: str) -> bool:
    """claude's argv error for a flag it doesn't know (``error: unknown
    option '--include-partial-messages'``)."""
    return "unknown option" in line.lower() and \
        "--include-partial-messages" in line


class _ClaudeLive:
    """One headless run's ``stream_event`` lines into ``message.delta``.

    Each text content block streams as its own message, id
    ``<API message id>:<block index>``. A subagent's lines
    (``parent_tool_use_id`` set) are skipped: they never reach the
    session's own transcript, so their bubble would never be replaced.
    """

    def __init__(self, deltas, key: str, machine: str) -> None:
        self.deltas = deltas
        self.key = key
        self.machine = machine
        self.msg = ""
        self.text_blocks: set = set()

    def _id(self, index) -> str:
        return f"{self.msg}:{index}"

    def feed(self, rec: dict) -> None:
        if self.deltas is None or rec.get("parent_tool_use_id"):
            return
        ev = rec.get("event")
        if not isinstance(ev, dict):
            return
        etype = ev.get("type")
        index = ev.get("index")
        if etype == "message_start":
            message = ev.get("message")
            mid = message.get("id") if isinstance(message, dict) else None
            self.msg = str(mid or f"claude-{time.time_ns()}")
            self.text_blocks = set()
        elif etype == "content_block_start":
            block = ev.get("content_block")
            if isinstance(block, dict) and block.get("type") == "text" \
                    and self.msg:
                self.text_blocks.add(index)
        elif etype == "content_block_delta":
            delta = ev.get("delta")
            if index in self.text_blocks and isinstance(delta, dict) \
                    and delta.get("type") == "text_delta":
                self.deltas.push(self.key, self.machine, self._id(index),
                                 "assistant", str(delta.get("text") or ""))
        elif etype == "content_block_stop":
            if index in self.text_blocks:
                self.text_blocks.discard(index)
                self.deltas.finish(self.key, self._id(index))

    def close(self) -> None:
        """The run ended (or died): nothing more will stream."""
        if self.deltas is not None:
            self.deltas.finish_all(self.key)


# ---------------------------------------------------------------------
# Tier A: opencode serve
#
# `opencode serve` exposes the project over HTTP + one SSE event stream.
# The hub owns a serve proc per project directory (refcounted across
# attached sessions) and translates `permission.*` events into the same
# `approval.requested`/`approval.resolved` protocol events the tmux
# watcher emits, so phone and ntfy paths need no second channel.

# How long a spawned serve gets to answer /session before we give up.
_SERVE_READY_S = 15.0
_SERVE_REQ_TIMEOUT = 20.0

# opencode decisions differ from ours: allow once / always / reject.
_OPENCODE_DECISIONS = {
    "allow": "once", "always": "always", "deny": "reject",
}


def _free_port() -> int:
    """An ephemeral loopback port for a spawned serve."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve_call(
    base: str, method: str, path: str, payload: dict | None,
    timeout: float = _SERVE_REQ_TIMEOUT,
) -> tuple[int, bytes]:
    """One HTTP call against a serve proc; ``(status, body)``."""
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read()
        except Exception:
            return exc.code, b""
    except Exception:
        return 0, b""


class _ServeProc:
    """One ``opencode serve`` child plus its SSE reader state."""

    def __init__(self, proc: subprocess.Popen, base: str) -> None:
        self.proc = proc
        self.base = base
        self.refs = 0
        self.lock = threading.Lock()
        # opencode session id -> (session_key, emit, machine)
        self.subs: dict[str, tuple[str, object, str]] = {}
        # permission id -> session id (for reply routing)
        self.permissions: dict[str, str] = {}
        # opencode session id -> the subscriber's DeltaStream
        self.deltas: dict[str, object] = {}
        # part id -> part type ("text", "reasoning", "tool", ...), so a
        # bare ``message.part.delta`` knows what it is growing
        self.part_types: dict[str, str] = {}
        self.reader_started = False
        self.dead = False


def _serve_event_loop(serve: _ServeProc) -> None:
    """Read ``GET /event``; permission asks become approval events."""
    from swe.remote import protocol

    try:
        req = urllib.request.Request(
            serve.base + "/event", headers={"Accept": "text/event-stream"}
        )
        resp = urllib.request.urlopen(req, timeout=None)
    except Exception:
        return
    data_lines: list[bytes] = []
    try:
        with resp:
            for raw in resp:
                line = raw.rstrip(b"\n")
                if line == b"":
                    if data_lines:
                        blob = b"\n".join(data_lines)
                        data_lines = []
                        _serve_event(serve, blob)
                    continue
                if line.startswith(b"data:"):
                    data_lines.append(line[5:].lstrip())
    except Exception:
        pass
    # The serve died or its stream dropped: no session.idle will close
    # what was mid-stream, so close it here.
    for sid, deltas in list(serve.deltas.items()):
        sub = serve.subs.get(sid)
        if sub is not None:
            deltas.finish_all(sub[0])


# Bound on remembered part types; a long-lived serve forgets the oldest.
_PART_TYPES_MAX = 2000


def _serve_live(serve: _ServeProc, etype: str, props: dict) -> None:
    """Text parts growing into ``message.delta``.

    opencode streams a part two ways depending on its version:
    ``message.part.updated`` with a ``delta`` beside the part, or a
    bare ``message.part.delta`` (``{sessionID, messageID, partID,
    field, delta}``) after the part was announced. Only ``text`` parts
    stream (an assistant reply; a prompt arrives whole, without a
    delta). The part's id is the stream id; a part with ``time.end``,
    or the session going idle, finishes it.
    """
    if etype == "message.part.updated":
        part = props.get("part")
        if not isinstance(part, dict):
            return
        part_id = str(part.get("id") or "")
        sid = str(part.get("sessionID") or props.get("sessionID") or "")
        ptype = str(part.get("type") or "")
        if not part_id:
            return
        if len(serve.part_types) >= _PART_TYPES_MAX:
            serve.part_types.pop(next(iter(serve.part_types)))
        serve.part_types[part_id] = ptype
        if ptype != "text":
            return
        target = _serve_target(serve, sid)
        if target is None:
            return
        deltas, key, machine = target
        delta = props.get("delta")
        if isinstance(delta, str) and delta:
            deltas.push(key, machine, part_id, "assistant", delta)
        timing = part.get("time")
        if isinstance(timing, dict) and timing.get("end"):
            deltas.finish(key, part_id)
            serve.part_types.pop(part_id, None)
    elif etype == "message.part.delta":
        part_id = str(props.get("partID") or "")
        if serve.part_types.get(part_id) != "text" or \
                props.get("field", "text") != "text":
            return
        target = _serve_target(serve, str(props.get("sessionID") or ""))
        delta = props.get("delta")
        if target is not None and isinstance(delta, str) and delta:
            deltas, key, machine = target
            deltas.push(key, machine, part_id, "assistant", delta)
    elif etype == "session.idle":
        target = _serve_target(serve, str(props.get("sessionID") or ""))
        if target is not None:
            deltas, key, _machine = target
            deltas.finish_all(key)


def _serve_target(serve: _ServeProc, sid: str):
    """``(deltas, key, machine)`` for an opencode session someone
    streams, else None."""
    deltas = serve.deltas.get(sid)
    sub = serve.subs.get(sid)
    if deltas is None or sub is None:
        return None
    return deltas, sub[0], sub[2]


def _serve_event(serve: _ServeProc, blob: bytes) -> None:
    """One SSE payload: route permission asks/replies to subscribers,
    and text deltas to their ``DeltaStream``."""
    from swe.remote import protocol

    try:
        ev = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(ev, dict):
        return
    etype = str(ev.get("type") or "")
    props = ev.get("properties") if isinstance(
        ev.get("properties"), dict) else ev.get("payload")
    if not isinstance(props, dict):
        return
    if etype.startswith("message.part.") or etype == "session.idle":
        _serve_live(serve, etype, props)
        return
    if "permission" not in etype.lower():
        return
    perm_id = str(
        props.get("id") or props.get("permissionID") or "")
    session_id = str(
        props.get("sessionID") or props.get("sessionId")
        or serve.permissions.get(perm_id) or "")
    sub = serve.subs.get(session_id)
    if sub is None:
        return
    key, emit, machine = sub
    if emit is None:
        return
    # asked/updated/replied naming has drifted; treat anything that is
    # not a reply/resolve as a fresh ask.
    resolved = any(w in etype.lower() for w in ("replied", "resolved"))
    if resolved:
        serve.permissions.pop(perm_id, None)
        emit(key, protocol.event(
            "approval.resolved", key, machine,
            {"request_id": perm_id},
        ))
        return
    if not perm_id:
        return
    serve.permissions[perm_id] = session_id
    prompt = str(
        props.get("title") or props.get("permission")
        or props.get("message") or "permission requested"
    )
    patterns = props.get("patterns") or props.get("metadata") or {}
    if isinstance(patterns, (list, tuple)):
        prompt += "\n\n" + "\n".join(f"`{p}`" for p in patterns[:8])
    emit(key, protocol.event(
        "approval.requested", key, machine,
        {"request_id": perm_id, "prompt": _tr._redact_secrets(prompt)},
    ))


class OpencodeServeInjector:
    """Tier A over a managed ``opencode serve`` for the session's cwd."""

    _serves: "dict[str, _ServeProc]" = {}
    _serves_lock = threading.Lock()

    def __init__(
        self,
        home: Path | None,
        session_key: str,
        emit=None,
        machine: str = "",
        deltas=None,
    ) -> None:
        self.home = home
        self.key = session_key
        self.emit = emit
        self.machine = machine
        self.deltas = deltas

    # -- serve proc lifecycle -------------------------------------------

    @staticmethod
    def _spawn(cwd: str, port: int) -> subprocess.Popen:
        return subprocess.Popen(
            ["opencode", "serve",
             "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=cwd or None,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _serve_for(self, session: Session) -> _ServeProc | None:
        """The shared serve proc for this session's project, spawning
        it (and waiting for readiness) on first use."""
        cwd = os.path.realpath(session.path or ".")
        sid = session.session_id
        with self._serves_lock:
            serve = self._serves.get(cwd)
            if serve is not None and serve.proc.poll() is not None:
                serve.dead = True
                self._serves.pop(cwd, None)
                serve = None
            if serve is None:
                port = _free_port()
                try:
                    proc = self._spawn(cwd, port)
                except OSError:
                    return None
                serve = _ServeProc(proc, f"http://127.0.0.1:{port}")
                self._serves[cwd] = serve
                if not serve.reader_started:
                    serve.reader_started = True
                    threading.Thread(
                        target=_serve_event_loop, args=(serve,),
                        daemon=True, name=f"opencode-events-{sid}",
                    ).start()
        # Wait for readiness outside the lock — slow starts shouldn't
        # serialize unrelated sessions.
        deadline = time.monotonic() + _SERVE_READY_S
        while time.monotonic() < deadline:
            if serve.proc.poll() is not None:
                with self._serves_lock:
                    serve.dead = True
                    if self._serves.get(cwd) is serve:
                        self._serves.pop(cwd, None)
                return None
            status, _ = _serve_call(serve.base, "GET", "/session", None,
                                    timeout=2.0)
            if status != 0:
                with self._serves_lock:
                    # Another waiter may have given up on this proc and
                    # killed it while our probe was in flight.
                    if serve.dead or self._serves.get(cwd) is not serve:
                        return None
                    if sid not in serve.subs:
                        serve.refs += 1
                    serve.subs[sid] = (self.key, self.emit, self.machine)
                    if self.deltas is not None:
                        serve.deltas[sid] = self.deltas
                return serve
            time.sleep(0.25)
        # Never answered. With no other session holding it, nothing
        # would ever release it: kill it rather than leak the process.
        with self._serves_lock:
            if serve.refs:
                return None
            serve.dead = True
            if self._serves.get(cwd) is serve:
                self._serves.pop(cwd, None)
        try:
            serve.proc.kill()
            serve.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _release(self, session: Session) -> None:
        """Drop this session's hold; kill the serve when unused."""
        cwd = os.path.realpath(session.path or ".")
        with self._serves_lock:
            serve = self._serves.get(cwd)
            if serve is None:
                return
            # Only a hold on THIS proc counts: after a respawn the
            # sessions subscribed to the dead one hold nothing here, and
            # dropping a ref they never took would kill the new serve
            # under the sessions using it.
            deltas = serve.deltas.pop(session.session_id, None)
            sub = serve.subs.pop(session.session_id, None)
            if deltas is not None and sub is not None:
                # A reply cut off by the detach: close its bubbles now,
                # no session.idle will come for it.
                deltas.finish_all(sub[0])
            if sub is None:
                return
            serve.refs = max(0, serve.refs - 1)
            if serve.refs:
                return
            self._serves.pop(cwd, None)
            serve.dead = True
        try:
            serve.proc.kill()
            serve.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    # -- injector interface ----------------------------------------------

    def send(self, session: Session, cmd: dict, status: str) -> InjectResult:
        text = _prompt(session, cmd, "opencode")
        if not text.strip():
            return InjectResult(False, "empty message")
        meta = supervise.meta_for(self.home, self.key)
        if meta is None:
            return InjectResult(False, "session detached; refusing the write")
        serve = self._serve_for(session)
        if serve is None:
            return InjectResult(False, "opencode serve failed to start")
        # prompt_async queues server-side — steer and queue are the same
        # call; opencode decides whether mid-turn input lands or waits.
        payload: dict = {"parts": [{"type": "text", "text": text}]}
        # The attach-time pick rides the channel's own fields: model as
        # {providerID, modelID} (provider/model is the only form the
        # server parses — channel_tuning_error enforces it; a stale or
        # hand-edited meta without the slash is left to the server's
        # default), effort as the model's variant.
        split = provider_split(str(meta.get("model") or ""))
        if split is not None:
            payload["model"] = {"providerID": split[0],
                                "modelID": split[1]}
        effort = str(meta.get("effort") or "")
        if effort:
            payload["variant"] = effort
        status_code, body = _serve_call(
            serve.base, "POST",
            f"/session/{session.session_id}/prompt_async",
            payload,
        )
        if status_code not in (200, 204):
            return InjectResult(
                False, f"opencode refused the prompt ({status_code})"
            )
        queued = cmd.get("mode") == "steer" and status == "active"
        note = "queued with opencode" if queued else "sent to opencode"
        return InjectResult(True, note, degraded=queued)

    def approval(
        self, session: Session, request_id: str, decision: str
    ) -> InjectResult:
        response = _OPENCODE_DECISIONS.get(decision)
        if response is None:
            return InjectResult(False, f"unknown decision {decision}")
        serve = self._serve_for(session)
        if serve is None:
            return InjectResult(False, "opencode serve failed to start")
        session_id = serve.permissions.get(request_id) or session.session_id
        status_code, _ = _serve_call(
            serve.base, "POST",
            f"/session/{session_id}/permissions/{request_id}",
            {"response": response},
        )
        if status_code in (404, 405):
            # Older opencode: /permission/{id}/reply {reply}.
            status_code, _ = _serve_call(
                serve.base, "POST",
                f"/permission/{request_id}/reply",
                {"reply": response},
            )
        if status_code not in (200, 204):
            return InjectResult(
                False, f"opencode refused the reply ({status_code})"
            )
        serve.permissions.pop(request_id, None)
        return InjectResult(True, f"replied {response}")

    def control(
        self, session: Session, op: str, value: str
    ) -> InjectResult:
        if op == "interrupt":
            serve = self._serve_for(session)
            if serve is None:
                return InjectResult(False, "opencode serve failed to start")
            status_code, _ = _serve_call(
                serve.base, "POST",
                f"/session/{session.session_id}/abort", {},
            )
            if status_code not in (200, 204):
                return InjectResult(
                    False, f"opencode refused abort ({status_code})"
                )
            return InjectResult(True, "abort sent")
        if op in {"kill", "detach"}:
            self._release(session)
            supervise.unmark(self.home, self.key)
            return InjectResult(True, "detached; session is observed again")
        if op == "model":
            # The next prompt_async carries it as its {providerID,
            # modelID}; a bare name is refused (channel_tuning_error),
            # since the server would drop it for its own default.
            return switch_model(self.home, self.key, "serve", "opencode",
                                value)
        return InjectResult(
            False, f"unsupported_control: {op} needs a live TUI"
        )


# ---------------------------------------------------------------------
# Tier A: codex app-server
#
# `codex app-server` speaks newline-delimited JSON-RPC 2.0 on stdio. One
# proc per attached session: initialize + initialized, thread/resume
# once, then turn/start per prompt. Server-initiated approval requests
# arrive as JSON-RPC *requests* on our read side; a decision is the
# matching response.

_APPSERVER_CALL_S = 60.0
_CODEX_DECISIONS = {
    "allow": "accept", "always": "acceptForSession", "deny": "decline",
}


class _AppServer:
    """One ``codex app-server`` child: writer lock + reader fan-out."""

    def __init__(self, proc: subprocess.Popen, thread_id: str) -> None:
        self.proc = proc
        self.thread_id = thread_id
        self.turn_id = ""
        self.wlock = threading.Lock()
        self.next_id = 1
        # rpc id -> Queue for the matching response
        self.replies: dict = {}
        # request_id (str rpc id) -> raw id, for approval() replies
        self.approvals: dict[str, object] = {}
        self.dead = False
        # Set once initialize+thread/resume finished (or failed) — a
        # second caller must not turn/start mid-handshake.
        self.ready = threading.Event()
        self.handshake_ok = False


def _rpc_line(proc: subprocess.Popen, payload: dict) -> bool:
    try:
        proc.stdin.write(json.dumps(payload).encode() + b"\n")
        proc.stdin.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def _appserver_live(method: str, params: dict, key: str, machine: str,
                    deltas) -> None:
    """Agent-message text into ``message.delta``.

    ``item/agentMessage/delta`` (``{itemId, delta}``) grows the item's
    text; ``item/completed`` for that item, or the turn ending,
    finishes it. Reasoning deltas are left out: a reasoning item often
    never reaches the rollout as text, so its bubble would never be
    replaced.
    """
    if deltas is None:
        return
    if method == "item/agentMessage/delta":
        item_id = str(params.get("itemId") or "")
        delta = params.get("delta")
        if item_id and isinstance(delta, str) and delta:
            deltas.push(key, machine, item_id, "assistant", delta)
    elif method == "item/completed":
        item = params.get("item")
        if isinstance(item, dict) and item.get("id"):
            deltas.finish(key, str(item["id"]))
    elif method in ("turn/completed", "turn/failed", "codex/turnCompleted"):
        deltas.finish_all(key)


def _appserver_read_loop(server: _AppServer, key: str, emit, machine: str,
                         deltas=None) -> None:
    """Demux stdout: responses go to waiting calls; server-initiated
    approval requests become protocol events; message text streams
    into ``deltas``."""
    from swe.remote import protocol

    proc = server.proc
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(msg, dict):
                continue
            method = str(msg.get("method") or "")
            if method:
                # Server -> client request or notification.
                params = msg.get("params")
                if isinstance(params, dict):
                    _appserver_live(method, params, key, machine, deltas)
                if "requestApproval" in method or "Approval" in method:
                    req_id = msg.get("id")
                    if req_id is not None:
                        request_id = str(req_id)
                        server.approvals[request_id] = req_id
                        params = msg.get("params") or {}
                        prompt = str(
                            params.get("reason")
                            or params.get("command")
                            or params.get("itemId")
                            or method
                        )
                        if emit is not None:
                            emit(key, protocol.event(
                                "approval.requested", key, machine,
                                {"request_id": request_id,
                                 "prompt": _tr._redact_secrets(prompt)},
                            ))
                elif method in ("turn/started", "codex/turnStarted"):
                    params = msg.get("params") or {}
                    turn = params.get("turn") or {}
                    if isinstance(turn, dict) and turn.get("id"):
                        server.turn_id = str(turn["id"])
                elif method in ("turn/completed", "turn/failed",
                                "codex/turnCompleted"):
                    server.turn_id = ""
                continue
            if "id" in msg:
                q = server.replies.pop(msg["id"], None)
                if q is not None:
                    q.put(msg)
    except Exception:
        pass
    server.dead = True
    if deltas is not None:
        deltas.finish_all(key)


class CodexAppServerInjector:
    """Tier A over a persistent ``codex app-server`` per session."""

    _servers: "dict[str, _AppServer]" = {}
    _servers_lock = threading.Lock()

    def __init__(
        self,
        home: Path | None,
        session_key: str,
        emit=None,
        machine: str = "",
        deltas=None,
    ) -> None:
        self.home = home
        self.key = session_key
        self.emit = emit
        self.machine = machine
        self.deltas = deltas

    # -- proc + rpc plumbing --------------------------------------------

    @staticmethod
    def _spawn(cwd: str, env: dict | None = None) -> subprocess.Popen:
        return subprocess.Popen(
            ["codex", "app-server"],
            cwd=cwd or None,
            env={**os.environ, **env} if env else None,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def _server_for(self, session: Session) -> _AppServer | None:
        spawned = False
        with self._servers_lock:
            server = self._servers.get(self.key)
            if server is not None and server.proc.poll() is None:
                spawned = False
            else:
                if server is not None:
                    self._servers.pop(self.key, None)
                thread_id = self._thread_id(session)
                if not thread_id:
                    return None
                env = account_env(session)
                try:
                    proc = (
                        self._spawn(session.path or ".", env=env)
                        if env else self._spawn(session.path or ".")
                    )
                except OSError:
                    return None
                server = _AppServer(proc, thread_id)
                self._servers[self.key] = server
                spawned = True
                threading.Thread(
                    target=_appserver_read_loop,
                    args=(server, self.key, self.emit, self.machine,
                          self.deltas),
                    daemon=True, name=f"codex-rpc-{self.key}",
                ).start()
        if not spawned:
            # Another caller (or an earlier call) owns the handshake —
            # wait for it rather than interleave writes mid-handshake.
            server.ready.wait(timeout=_SERVE_READY_S)
            return server if server.handshake_ok else None
        # Handshake outside the registry lock.
        from swe import __version__

        try:
            hello = self._call(server, "initialize", {
                "clientInfo": {"name": "quiver", "version": __version__},
            })
            if hello is None or "error" in hello:
                return None
            _rpc_line(proc, {"jsonrpc": "2.0", "method": "initialized"})
            resumed = self._call(server, "thread/resume",
                                 {"threadId": server.thread_id})
            if resumed is None or "error" in resumed:
                return None
            server.handshake_ok = True
            return server
        finally:
            server.ready.set()
            if not server.handshake_ok:
                self._kill(server)

    @staticmethod
    def _thread_id(session: Session) -> str:
        from swe.sessions.parsers import codex_thread_id

        return str(codex_thread_id(session.session_id) or "")

    def _call(
        self, server: _AppServer, method: str, params: dict | None,
        timeout: float | None = None,
    ):
        """One request/response; returns the reply dict or None."""
        import queue as queue_mod

        if timeout is None:
            timeout = _APPSERVER_CALL_S

        q: queue_mod.Queue = queue_mod.Queue()
        with server.wlock:
            rpc_id = server.next_id
            server.next_id += 1
            server.replies[rpc_id] = q
            if not _rpc_line(server.proc, {
                "jsonrpc": "2.0", "id": rpc_id,
                "method": method, "params": params,
            }):
                server.replies.pop(rpc_id, None)
                return None
        try:
            return q.get(timeout=timeout)
        except queue_mod.Empty:
            server.replies.pop(rpc_id, None)
            return None

    def _kill(self, server: _AppServer) -> None:
        with self._servers_lock:
            if self._servers.get(self.key) is server:
                self._servers.pop(self.key, None)
        server.dead = True
        try:
            server.proc.kill()
            server.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    # -- injector interface ----------------------------------------------

    def send(self, session: Session, cmd: dict, status: str) -> InjectResult:
        text = _prompt(session, cmd, "codex")
        if not text.strip():
            return InjectResult(False, "empty message")
        # queue waits for idle; steer writes mid-turn (the server queues
        # or interleaves it — either way the write is attempted).
        if cmd.get("mode") == "queue" and status == "active":
            return InjectResult(
                False, "session is mid-turn; steer to write anyway"
            )
        meta = supervise.meta_for(self.home, self.key)
        if meta is None:
            return InjectResult(False, "session detached; refusing the write")
        server = self._server_for(session)
        if server is None:
            return InjectResult(False, "codex app-server failed to start")
        params: dict = {
            "threadId": server.thread_id,
            "input": [{"type": "text", "text": text}],
        }
        # turn/start's own overrides carry the attach-time pick (each
        # holds for the turn and the ones after, matching headless's
        # per-call --model).
        for field in ("model", "effort"):
            value = str(meta.get(field) or "")
            if value:
                params[field] = value
        resp = self._call(server, "turn/start", params)
        if resp is None:
            return InjectResult(False, "codex app-server timed out")
        if "error" in resp:
            msg = str((resp.get("error") or {}).get("message") or "error")
            return InjectResult(False, f"codex refused: {msg}")
        turn = ((resp.get("result") or {}).get("turn") or {})
        if isinstance(turn, dict) and turn.get("id"):
            server.turn_id = str(turn["id"])
        return InjectResult(True, "turn started")

    def approval(
        self, session: Session, request_id: str, decision: str
    ) -> InjectResult:
        mapped = _CODEX_DECISIONS.get(decision)
        if mapped is None:
            return InjectResult(False, f"unknown decision {decision}")
        server = self._servers.get(self.key)
        if server is None or server.proc.poll() is not None:
            return InjectResult(False, "codex app-server is not running")
        rpc_id = server.approvals.pop(request_id, None)
        if rpc_id is None:
            return InjectResult(
                False, "unknown or already-answered approval request"
            )
        ok = _rpc_line(server.proc, {
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {"decision": mapped},
        })
        if not ok:
            server.approvals[request_id] = rpc_id  # don't lose it
            return InjectResult(False, "codex app-server is unreachable")
        return InjectResult(True, f"replied {mapped}")

    def control(
        self, session: Session, op: str, value: str
    ) -> InjectResult:
        if op == "interrupt":
            server = self._servers.get(self.key)
            if server is None or server.proc.poll() is not None:
                return InjectResult(False, "codex app-server is not running")
            params = {"threadId": server.thread_id}
            if server.turn_id:
                params["turnId"] = server.turn_id
            resp = self._call(server, "turn/interrupt", params)
            if resp is None or "error" in resp:
                return InjectResult(False, "interrupt failed")
            return InjectResult(True, "interrupted")
        if op in {"kill", "detach"}:
            server = self._servers.pop(self.key, None)
            if server is not None:
                server.dead = True
                try:
                    server.proc.kill()
                    server.proc.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            supervise.unmark(self.home, self.key)
            return InjectResult(True, "detached; session is observed again")
        if op == "model":
            # The next turn/start carries it as its model override,
            # which codex keeps for the turns after too; a turn already
            # running keeps the model it started on.
            return switch_model(self.home, self.key, "appserver", "codex",
                                value)
        return InjectResult(
            False, f"unsupported_control: {op} needs a live TUI"
        )


def injector_for(
    home: Path | str | None,
    session_key: str,
    meta: dict | None,
    emit=None,
    machine: str = "",
    deltas=None,
) -> TmuxInjector | ClaudeHeadlessInjector | OpencodeServeInjector | CodexAppServerInjector:
    """Pick the channel from the supervised registry row. ``deltas`` (a
    ``stream.DeltaStream``) takes the tier-A channels' live text; tmux
    has none."""
    meta = meta or {}
    home_path = Path(home) if home is not None else None
    mode = meta.get("mode")
    tool = meta.get("tool_name")
    if mode == "headless" and tool == "claude":
        return ClaudeHeadlessInjector(
            home_path, session_key, deltas=deltas, machine=machine
        )
    if mode == "serve" and tool == "opencode":
        return OpencodeServeInjector(
            home_path, session_key, emit=emit, machine=machine,
            deltas=deltas,
        )
    if mode == "appserver" and tool == "codex":
        return CodexAppServerInjector(
            home_path, session_key, emit=emit, machine=machine,
            deltas=deltas,
        )
    return TmuxInjector(home_path, session_key)
