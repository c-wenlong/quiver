"""Run the hub as a macOS LaunchAgent, so it outlives a restart.

A hub started from a shell (``nohup swe remote serve &``) dies with the
login session and nothing brings it back: the phone then shows "Can't
reach the hub" until someone notices. ``swe remote serve --install``
writes a per-user LaunchAgent that starts the hub at login and restarts
it whenever it exits. The agent runs ``serve --tailnet``, which exits
instead of falling back to localhost while Tailscale is still coming up,
so launchd retries until the tailnet address exists.

Logic only: callers print. ``run`` is injectable so tests never touch
the real launchctl.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Callable

LABEL = "com.cwenlong.quiver.remote-hub"
# Seconds launchd waits before restarting a hub that exited, so a hub
# waiting on Tailscale retries a few times a minute, not in a hot loop.
THROTTLE_S = 15

Runner = Callable[..., subprocess.CompletedProcess]


def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def build_plist(program: list[str], log_path: Path, path_env: str) -> dict:
    """The LaunchAgent: run ``program`` at login, restart it on exit.

    launchd starts agents with a bare PATH, and the hub shells out to
    tailscale, tmux and the harness CLIs, so the installing shell's PATH
    goes in. Output is unbuffered so the log has the start line even
    when the process is killed.
    """
    return {
        "Label": LABEL,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_S,
        "ProcessType": "Background",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {"PATH": path_env, "PYTHONUNBUFFERED": "1"},
    }


def install(program: list[str], log_path: Path, path_env: str,
            home: Path | None = None,
            run: Runner = subprocess.run) -> Path:
    """Write the plist and (re)load it. Reinstalling restarts the hub.

    Raises RuntimeError with launchctl's message when loading fails.
    """
    path = plist_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(build_plist(program, log_path, path_env)))
    # bootstrap refuses a label that is already loaded, so unload first;
    # a "not loaded" failure here is the normal first-install case.
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    res = run(["launchctl", "bootstrap", _domain(), str(path)],
              capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(msg or f"launchctl bootstrap exited {res.returncode}")
    return path


def uninstall(home: Path | None = None, run: Runner = subprocess.run) -> bool:
    """Stop the hub and remove the plist. False when none was installed."""
    path = plist_path(home)
    res = run(["launchctl", "bootout", f"{_domain()}/{LABEL}"],
              capture_output=True, text=True, stdin=subprocess.DEVNULL)
    existed = path.exists()
    if existed:
        path.unlink()
    return existed or res.returncode == 0


def loaded_pid(run: Runner = subprocess.run) -> tuple[bool, int | None]:
    """(loaded, pid): whether launchd holds the agent, and its running pid."""
    res = run(["launchctl", "print", f"{_domain()}/{LABEL}"],
              capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        return False, None
    m = re.search(r"^\s*pid = (\d+)", res.stdout or "", re.M)
    return True, int(m.group(1)) if m else None
