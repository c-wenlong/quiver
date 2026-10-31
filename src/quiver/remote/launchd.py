"""Run the hub as a macOS LaunchAgent, so it outlives a restart.

A hub started from a shell (``nohup swe remote serve &``) dies with the
login session and nothing brings it back: the phone then shows "Can't
reach the hub" until someone notices. ``swe remote serve --install``
writes a per-user LaunchAgent that starts the hub at login and restarts
it whenever it exits. The agent runs ``serve --tailnet``, which exits
instead of falling back to localhost while Tailscale is still coming up,
so launchd retries until the tailnet address exists.

The agent runs Quiver.app's bundled ``swe`` when the app is installed.
macOS privacy protection blocks a launchd agent from reading ~/Desktop,
~/Documents and ~/Downloads, so a repo checkout's ``.venv/bin/swe`` there
dies at startup ("Operation not permitted" on ``pyvenv.cfg``). The
bundle lives in Applications, and the app's own folder access covers it.

Logic only: callers print. ``run`` is injectable so tests never touch
the real launchctl. ``label`` lets the MCP gateway (``swe mcp gateway
install``) run as its own agent through the same code.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

LABEL = "com.cwenlong.quiver.remote-hub"
# Seconds launchd waits before restarting a hub that exited, so a hub
# waiting on Tailscale retries a few times a minute, not in a hot loop.
THROTTLE_S = 15

# Home folders a launchd agent can't read without a privacy grant.
PROTECTED_DIRS = ("Desktop", "Documents", "Downloads", "Library/Mobile Documents")

Runner = Callable[..., subprocess.CompletedProcess]


def plist_path(home: Path | None = None, label: str = LABEL) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{label}.plist"


def bundled_swe(home: Path | None = None) -> Path | None:
    """Quiver.app's ``swe`` shim, from ~/Applications or /Applications."""
    for apps in ((home or Path.home()) / "Applications", Path("/Applications")):
        shim = apps / "Quiver.app" / "Contents" / "Resources" / "swe" / "bin" / "swe"
        if os.access(shim, os.X_OK):
            return shim
    return None


def protected_dir(path: str | Path, home: Path | None = None) -> str | None:
    """The protected home folder ``path`` sits in ("Desktop"), or None.

    Symlinks are followed: what launchd must read is where the link
    points, so ``~/bin/swe`` linking into ~/Desktop counts as Desktop.
    """
    p = Path(path).resolve()
    base = (home or Path.home()).resolve()
    for name in PROTECTED_DIRS:
        try:
            p.relative_to(base / name)
        except ValueError:
            continue
        return name
    return None


def _domain() -> str:
    return f"gui/{os.getuid()}"


def build_plist(program: list[str], log_path: Path, path_env: str,
                label: str = LABEL, process_type: str = "Background") -> dict:
    """The LaunchAgent: run ``program`` at login, restart it on exit.

    launchd starts agents with a bare PATH, and the hub shells out to
    tailscale, tmux and the harness CLIs, so the installing shell's PATH
    goes in. Output is unbuffered so the log has the start line even
    when the process is killed.

    ``Background`` lets macOS throttle the job's CPU and I/O, which suits
    the hub. The MCP gateway answers a session's tool calls while someone
    waits, so it asks for ``Interactive``: under ``Background`` one
    server's 6 MB tool list took 6.9 s instead of 1.3 s.
    """
    return {
        "Label": label,
        "ProgramArguments": program,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_S,
        "ProcessType": process_type,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {"PATH": path_env, "PYTHONUNBUFFERED": "1"},
    }


# bootstrap right after bootout can fail with "5: Input/output error"
# while launchd is still tearing the old job down; it loads on a retry.
BOOTSTRAP_TRIES = 4
BOOTSTRAP_RETRY_S = 1.5


def install(program: list[str], log_path: Path, path_env: str,
            home: Path | None = None,
            run: Runner = subprocess.run,
            sleep: Callable[[float], None] = time.sleep,
            label: str = LABEL, process_type: str = "Background") -> Path:
    """Write the plist and (re)load it. Reinstalling restarts the hub.

    Raises RuntimeError with launchctl's message when loading fails.
    """
    path = plist_path(home, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(build_plist(program, log_path, path_env, label,
                                                process_type)))
    # bootstrap refuses a label that is already loaded, so unload first;
    # a "not loaded" failure here is the normal first-install case.
    run(["launchctl", "bootout", f"{_domain()}/{label}"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    for attempt in range(BOOTSTRAP_TRIES):
        res = run(["launchctl", "bootstrap", _domain(), str(path)],
                  capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if res.returncode == 0:
            return path
        msg = (res.stderr or res.stdout or "").strip()
        if "input/output error" not in msg.lower() or attempt == BOOTSTRAP_TRIES - 1:
            break
        sleep(BOOTSTRAP_RETRY_S)
    raise RuntimeError(msg or f"launchctl bootstrap exited {res.returncode}")


def uninstall(home: Path | None = None, run: Runner = subprocess.run,
              label: str = LABEL) -> bool:
    """Stop the hub and remove the plist. False when none was installed.

    Raises RuntimeError, keeping the plist, when launchctl fails to stop
    a job that is still loaded; a failed bootout of an absent job is
    the normal "nothing to stop" case.
    """
    path = plist_path(home, label)
    res = run(["launchctl", "bootout", f"{_domain()}/{label}"],
              capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if res.returncode != 0 and loaded_pid(run, label)[0]:
        msg = (res.stderr or res.stdout or "").strip()
        raise RuntimeError(msg or f"launchctl bootout exited {res.returncode}")
    existed = path.exists()
    if existed:
        path.unlink()
    return existed or res.returncode == 0


def loaded_pid(run: Runner = subprocess.run, label: str = LABEL) -> tuple[bool, int | None]:
    """(loaded, pid): whether launchd holds the agent, and its running pid."""
    res = run(["launchctl", "print", f"{_domain()}/{label}"],
              capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        return False, None
    m = re.search(r"^\s*pid = (\d+)", res.stdout or "", re.M)
    return True, int(m.group(1)) if m else None
