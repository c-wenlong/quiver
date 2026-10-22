"""Supervision: sessions running under our tmux (or headless) control.

An ``observed`` session belongs to a human terminal; an ``attached`` one
runs under quiver's supervisor and the phone can write to it. Two modes:

- ``tmux``: ``tmux new-session -d`` relaunches the harness with its
  resume flags. The session keeps a real TUI — ``tmux attach -t`` still
  works locally — and writes go in through ``send-keys``.
- ``headless``: no persistent process. Each prompt spawns a one-shot
  harness invocation (``claude -p --resume``); ``inject.py`` owns it.

The registry is ``data/remote/supervised.json``: ``{key: {mode, tmux,
tool_name, session_id, cwd, attached_at}}``. Catalog reads the same file
to flag ownership; a dead tmux entry is pruned on read so a crashed
supervisor cleanly reverts a session to ``observed``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote.protocol import key_slug
from quiver.sessions.models import Session
from quiver.sessions.resume import resume_flags, launch_tool_name

# Attach is offered only for idle sessions; the hub re-checks at command
# time. "Idle" = no transcript append in this window.
ATTACH_IDLE_S = 5.0

# The supervised registry is read-change-save; the hub, satellite agents
# and `swe remote attach` CLI calls can all touch it. In-process lock
# plus an flock'd sidecar make the read-write atomic across both.
_REGISTRY_LOCK = threading.RLock()

# Keystrokes into a tmux pane are multi-call (text, then Enter): per-pane
# locks keep two concurrent writes from interleaving into one prompt.
_PANE_LOCKS: dict[str, threading.Lock] = {}
_PANE_LOCKS_LOCK = threading.Lock()


@contextlib.contextmanager
def _registry_lock(home: Path | None):
    """Exclusive section around supervised.json read-modify-write."""
    path = remote_dir_for(home)
    path.mkdir(parents=True, exist_ok=True)
    with _REGISTRY_LOCK:
        with open(path / "supervised.lock", "w") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _pane_lock(name: str) -> threading.Lock:
    with _PANE_LOCKS_LOCK:
        return _PANE_LOCKS.setdefault(name, threading.Lock())


def tmux_bin() -> str | None:
    return shutil.which("tmux")


def tmux_name(session_key: str) -> str:
    return f"qv-{key_slug(session_key)[:16]}"


def _registry_file(home: Path | None) -> Path:
    return remote_dir_for(home) / "supervised.json"


def _load_registry(home: Path | None) -> dict[str, dict]:
    try:
        data = json.loads(_registry_file(home).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _save_registry(home: Path | None, entries: dict[str, dict]) -> None:
    path = _registry_file(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(entries, indent=1))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [tmux_bin() or "tmux", *args],
        capture_output=True, text=True, timeout=10,
    )


def tmux_alive(name: str) -> bool:
    if not tmux_bin():
        return False
    try:
        return _tmux("has-session", "-t", name).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def supervised(home: Path | str | None) -> dict[str, dict]:
    """Live supervised entries; dead tmux rows pruned on read."""
    home_path = Path(home) if home is not None else None
    with _registry_lock(home_path):
        entries = _load_registry(home_path)
        dead = [
            key for key, meta in entries.items()
            if meta.get("mode") == "tmux"
            and not tmux_alive(str(meta.get("tmux") or ""))
        ]
        if dead:
            for key in dead:
                entries.pop(key, None)
            _save_registry(home_path, entries)
    return entries


def supervised_keys(home: Path | str | None) -> set[str]:
    return set(supervised(Path(home) if home is not None else None))


def mark(home: Path | None, session_key: str, meta: dict) -> dict:
    with _registry_lock(home):
        entries = _load_registry(home)
        entries[session_key] = meta
        _save_registry(home, entries)
    return meta


def unmark(home: Path | None, session_key: str) -> None:
    with _registry_lock(home):
        entries = _load_registry(home)
        if entries.pop(session_key, None) is not None:
            _save_registry(home, entries)


def meta_for(home: Path | None, session_key: str) -> dict | None:
    return _load_registry(home).get(session_key)


def launch_tmux(session_key: str, argv: list[str], cwd: str | None) -> str | None:
    """Start ``argv`` in a detached tmux session; returns the tmux name."""
    if not tmux_bin():
        return None
    name = tmux_name(session_key)
    if tmux_alive(name):
        return name
    cmd = " ".join(_shquote(a) for a in argv)
    args = ["new-session", "-d", "-s", name]
    if cwd:
        args += ["-c", cwd]
    args.append(cmd)
    try:
        if _tmux(*args).returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    return name


def _shquote(arg: str) -> str:
    if arg and all(c.isalnum() or c in "-_./:=@%+," for c in arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


def send_text(name: str, text: str) -> bool:
    """Type ``text`` into tmux session ``name`` and press Enter.

    Serialized per pane: the literal-text and Enter send-keys are two
    tmux calls, and an interleaved second writer would merge prompts.
    """
    with _pane_lock(name):
        try:
            if _tmux("send-keys", "-t", name, "-l", text).returncode != 0:
                return False
            return _tmux("send-keys", "-t", name, "Enter").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def send_key(name: str, key: str) -> bool:
    """One named tmux key (``Escape``, ``C-c``, digits for menus)."""
    try:
        return _tmux("send-keys", "-t", name, key).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def capture_pane(name: str, lines: int = 200) -> str:
    try:
        out = _tmux("capture-pane", "-p", "-t", name,
                    "-S", f"-{lines}")
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def kill(name: str) -> bool:
    try:
        return _tmux("kill-session", "-t", name).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def channel_name(home: Path | None, session_key: str) -> str:
    """The tmux session a key's writes go to (registry first, else derive)."""
    meta = _load_registry(home).get(session_key) or {}
    return str(meta.get("tmux") or "") or tmux_name(session_key)


def attach(
    home: Path | None,
    session_key: str,
    session: Session,
    *,
    last_append_at: float | None,
    status: str,
    mode: str = "tmux",
) -> tuple[bool, str]:
    """observed → attached: relaunch under the supervisor.

    The phone offers Attach only when the session looks idle; this
    server-side re-check is the one that actually prevents the race —
    a session can go active between list and tap.
    """
    if session_key in _load_registry(home):
        return True, "already attached"
    if status == "active":
        return False, "session is mid-turn; try again when it is idle"
    if last_append_at is not None and time.time() - last_append_at < ATTACH_IDLE_S:
        return False, "transcript is still moving; try again when it is idle"
    flags = resume_flags(session)
    if not flags:
        return False, f"{session.tool_name} cannot be resumed"
    if mode == "headless" and session.tool_name != "claude":
        return False, "headless attach only supports claude"

    # The original terminal still holds the harness process and would
    # keep writing the same transcript — split brain. Kill matching
    # writers before relaunching under the supervisor (best-effort: an
    # interactive `claude` run has no session id in argv, so a plain
    # foreground TUI can't always be found — the catalog row stays
    # authoritative for who may write).
    _stop_local_writers(session)

    if mode == "headless":
        meta = {
            "mode": "headless",
            "tool_name": session.tool_name,
            "session_id": session.session_id,
            "cwd": session.path or "",
            "attached_at": time.time(),
        }
        mark(home, session_key, meta)
        return True, "attached (headless)"

    binary = launch_tool_name(session.tool_name)
    name = launch_tmux(
        session_key, [binary, *flags], session.path or None
    )
    if name is None:
        return False, "tmux is not available or the launch failed"
    mark(home, session_key, {
        "mode": "tmux",
        "tmux": name,
        "tool_name": session.tool_name,
        "session_id": session.session_id,
        "cwd": session.path or "",
        "attached_at": time.time(),
    })
    return True, f"attached under tmux {name}"


def _stop_local_writers(session: Session) -> None:
    """SIGTERM harness processes that still own ``session``.

    Matches on the session id appearing in the command line — i.e. a
    process started via ``--resume <id>`` — and requires the harness
    binary name too, so a stray `grep sid` never qualifies. A session
    started bare (``claude`` with no id flag) is not identifiable this
    way; its terminal keeps write access and the doc's "don't type in
    both places" guidance applies.
    """
    sid = str(session.session_id or "")
    binary = os.path.basename(
        launch_tool_name(session.tool_name) or ""
    )
    if not sid or not binary:
        return
    try:
        out = subprocess.run(
            ["pgrep", "-f", sid],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return
    for pid_s in out.stdout.split():
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        try:
            cmdline = subprocess.run(
                ["ps", "-p", pid_s, "-o", "command="],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if binary not in cmdline:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue


def detach(home: Path | None, session_key: str) -> bool:
    """attached → observed: kill the tmux session, drop the registry row."""
    meta = meta_for(home, session_key)
    killed = True
    if meta and meta.get("mode") == "tmux":
        killed = kill(channel_name(home, session_key))
    unmark(home, session_key)
    return killed


def _pending_dir(home: Path | None) -> Path:
    return remote_dir_for(home) / "pending"


def launch_new(
    home: Path | None,
    tool_name: str,
    cwd: str,
    prompt: str,
) -> tuple[bool, str, str | None]:
    """``POST /v1/sessions``: start a fresh harness run under tmux.

    The harness picks its own session id, so the placeholder key cannot
    be marked supervised directly — a pending marker lets the catalog
    adopt the new transcript on its next refresh. Returns
    ``(ok, note, tmux_name)``.
    """
    binary = launch_tool_name(tool_name)
    if not binary or not shutil.which(binary):
        return False, f"unknown harness {tool_name}", None
    # The nonce matters: pid+second collide for two same-harness launches
    # inside one second, and the tmux name derives from the key.
    key = f"new:{tool_name}:{int(time.time())}-{os.getpid()}-{secrets.token_hex(3)}"
    argv = [binary]
    name = launch_tmux(key, argv, cwd or None)
    if name is None:
        return False, "tmux is not available or the launch failed", None
    try:
        folder = _pending_dir(home)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.json").write_text(json.dumps({
            "tmux": name,
            "tool_name": tool_name,
            "cwd": cwd or "",
            "launched_at": time.time(),
        }))
    except OSError:
        pass
    if prompt:
        prime_pane(name, prompt)
    return True, "launched under tmux; the session appears on refresh", name


def prime_pane(name: str, prompt: str) -> None:
    """Boot the TUI past its trust dialog, then type ``prompt``.

    Claude (and friends) show a folder-trust gate in a fresh directory;
    typing a prompt into that dialog loses it. Confirm first.
    """
    time.sleep(1.0)
    pane = capture_pane(name).lower()
    if "trust this folder" in pane:
        # Claude's trust menu defaults to "No, exit": move to "Yes, I
        # trust this folder" before confirming.
        send_key(name, "Down")
        send_key(name, "Enter")
        time.sleep(1.0)
    elif "enter to confirm" in pane:
        send_key(name, "Enter")
        time.sleep(1.0)
    send_text(name, prompt)


def adopt_pending(
    home: Path | None,
    rows: list[tuple[str, Session, str]],
) -> list[str]:
    """Mark newly discovered sessions that came from ``launch_new``.

    A row is adopted when a pending launch's harness and cwd match and
    the session's transcript was written after the launch went up (with
    slack for clock/mtime ordering). Returns the adopted keys.
    """
    folder = _pending_dir(home)
    try:
        markers = list(folder.glob("*.json"))
    except OSError:
        return []
    if not markers:
        return []
    adopted: list[str] = []
    now = time.time()
    for marker in markers:
        try:
            pending = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker.unlink(missing_ok=True)
            continue
        # Give up on markers older than a day: whatever launched either
        # never wrote a transcript or was missed by the parser.
        if now - float(pending.get("launched_at") or 0) > 86400:
            marker.unlink(missing_ok=True)
            continue
        hit = _pending_match(home, pending, rows)
        if hit is None:
            continue
        key = hit
        mark(home, key, {
            "mode": "tmux",
            "tmux": str(pending.get("tmux") or ""),
            "tool_name": str(pending.get("tool_name") or ""),
            "session_id": "",
            "cwd": str(pending.get("cwd") or ""),
            "attached_at": now,
        })
        marker.unlink(missing_ok=True)
        adopted.append(key)
    return adopted


def _pending_match(
    home: Path | None, pending: dict, rows: list[tuple[str, Session, str]]
) -> str | None:
    from quiver.remote import tail

    tool = str(pending.get("tool_name") or "")
    cwd = os.path.realpath(str(pending.get("cwd") or ""))
    launched = float(pending.get("launched_at") or 0)
    candidates: list[tuple[str, float]] = []
    for key, session, _status in rows:
        if key in _load_registry(home):
            continue
        if session.tool_name != tool:
            continue
        if os.path.realpath(session.path or "") != cwd:
            continue
        transcript = tail.resolve_jsonl_path(session)
        try:
            mtime = transcript.stat().st_mtime if transcript else 0
        except OSError:
            mtime = 0
        # -120s slack: mtime ordering against launch time is enough to
        # distinguish a transcript this launch created from older ones.
        if mtime >= launched - 120:
            candidates.append((key, mtime))
    if len(candidates) != 1:
        # Zero matches: nothing to adopt yet. Two or more: a human may
        # have started a same-cwd session in the window — adopting would
        # bind their transcript to our pane, so stay pending until the
        # marker ages out instead of guessing.
        return None
    return candidates[0][0]
