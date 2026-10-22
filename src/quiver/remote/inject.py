"""Write path: the phone's commands into a supervised session.

Registry keyed by harness, same shape as the parser/reader registries.
Two channels ship in R2:

- ``ClaudeHeadlessInjector`` (tier A): one ``claude -p --resume <id>
  --output-format stream-json`` invocation per prompt. The transcript
  file gets the appended turn, so the tailer picks up the same
  ``message.appended`` events as a TUI run. Permissions are fixed per
  invocation (print mode has no mid-turn prompts), so approvals answer
  ``approval_not_supported``.
- ``TmuxInjector`` (tier B): ``send-keys`` for prompts, menu digits for
  approvals, Escape for interrupt — best-effort against whatever TUI is
  running under the supervisor.

``queue|steer``: queue refuses while a turn looks active; steer writes
immediately (the TUI buffers input). Headless invocations are atomic —
steer degrades to queue and one run is in flight per session at a time.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import NamedTuple

from quiver.remote import supervise
from quiver.sessions.models import Session

# A headless claude run gets this long before we kill it; a queued send
# waits on the in-flight run with half that patience.
HEADLESS_TIMEOUT_S = 300
_QUEUE_WAIT_S = 150

# claude TUI permission menus take a digit: 1 allow once, 2 always
# allow, 3 deny. Other TUIs ignore stray digits harmlessly.
_APPROVAL_KEYS = {"allow": "1", "always": "2", "deny": "3"}

_OPS_TEXT = {
    "compact": "/compact",
    "clear": "/clear",
    "model": "/model {value}",
    "effort": "/effort {value}",
}

# Markers a claude permission menu prints on screen; a pane without one
# is not waiting on an approval.
_APPROVAL_MARKERS = (
    "do you want to",
    "don't ask again",
    "esc to cancel",
    "allow",
)


def _pane_shows_approval(pane: str) -> bool:
    text = pane.lower()
    return any(marker in text for marker in _APPROVAL_MARKERS)


class InjectResult(NamedTuple):
    ok: bool
    note: str = ""


class TmuxInjector:
    """Tier B: everything is a keystroke into the supervised pane."""

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
        text = str(cmd.get("text") or "")
        for path in cmd.get("attachments") or []:
            text = f"@{path} {text}"
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
        key = _APPROVAL_KEYS.get(decision)
        if key is None:
            return InjectResult(False, f"unknown decision {decision}")
        # The tmux pane is shared state: a stale approval would answer
        # whatever menu happens to be on screen. Require a visible
        # permission prompt before sending a menu key.
        if not _pane_shows_approval(supervise.capture_pane(self.name)):
            return InjectResult(
                False, "no approval prompt is on screen"
            )
        if supervise.send_key(self.name, key):
            return InjectResult(True, f"sent menu key {key}")
        return InjectResult(False, "tmux session is not reachable")

    def control(self, session: Session, op: str, value: str) -> InjectResult:
        if op == "interrupt":
            ok = supervise.send_key(self.name, "Escape")
            return InjectResult(ok, "escape sent" if ok else "pane unreachable")
        if op in {"kill", "detach"}:
            return InjectResult(
                supervise.detach(self.home, self.key),
                "supervisor stopped; session is observed again",
            )
        template = _OPS_TEXT.get(op)
        if template is None:
            return InjectResult(False, f"unsupported control {op}")
        text = template.format(value=value).rstrip()
        ok = supervise.send_text(self.name, text)
        return InjectResult(ok, f"sent {text!r}" if ok else "pane unreachable")


class ClaudeHeadlessInjector:
    """Tier A: one print-mode invocation per prompt, serialized."""

    _locks: dict[str, threading.Lock] = {}
    _procs: dict[str, subprocess.Popen] = {}

    def __init__(self, home: Path | None, session_key: str) -> None:
        self.home = home
        self.key = session_key
        self._locks.setdefault(session_key, threading.Lock())

    @staticmethod
    def argv(session: Session, text: str) -> list[str]:
        return [
            "claude", "-p", "--resume", session.session_id,
            "--output-format", "stream-json",
            "--verbose", text,
        ]

    def _run(self, session: Session, argv: list[str]) -> tuple[bool, str]:
        try:
            proc = subprocess.Popen(
                argv, cwd=session.path or None,
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
        text = str(cmd.get("text") or "")
        for path in cmd.get("attachments") or []:
            text = f"@{path} {text}"
        if not text.strip():
            return InjectResult(False, "empty message")
        # steer degrades to queue: a print-mode run is atomic.
        lock = self._locks[self.key]
        if not lock.acquire(timeout=_QUEUE_WAIT_S):
            return InjectResult(False, "a headless turn is still in flight")
        try:
            # A detach could land between the route's ownership check and
            # this write; under the session lock the registry is the
            # last word.
            if supervise.meta_for(self.home, self.key) is None:
                return InjectResult(
                    False, "session detached; refusing the write"
                )
            ok, note = self._run(session, self.argv(session, text))
            return InjectResult(ok, note)
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
        return InjectResult(
            False, f"unsupported_control: {op} needs a live TUI"
        )


def injector_for(
    home: Path | str | None,
    session_key: str,
    meta: dict | None,
) -> TmuxInjector | ClaudeHeadlessInjector:
    """Pick the channel from the supervised registry row."""
    meta = meta or {}
    if meta.get("mode") == "headless" and meta.get("tool_name") == "claude":
        return ClaudeHeadlessInjector(
            Path(home) if home is not None else None, session_key
        )
    return TmuxInjector(
        Path(home) if home is not None else None, session_key
    )
