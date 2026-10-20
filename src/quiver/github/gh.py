"""The one module that spawns ``gh``.

Quiver never stores GitHub tokens: every call inherits the user's own
``gh auth login`` the same way the Copilot quota fetcher in
``harness/rate_limits.py`` does. Reads are outcome-typed — a caller can
tell "no such PR" (``not-found``) apart from "GitHub is down"
(``upstream-error``) instead of both collapsing to an empty list.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

GH_TIMEOUT = 20


@dataclass
class GhResult:
    """One ``gh`` invocation, typed so "down" never reads as "empty"."""

    status: str          # ok | not-found | upstream-error | unavailable
    data: Any = None
    error: str = ""


@dataclass
class GhEnv:
    """What the ambient ``gh`` install looks like, probed once per command."""

    installed: bool = field(default_factory=lambda: shutil.which("gh") is not None)
    authenticated: bool = False
    login: str = ""

    @property
    def status(self) -> str:
        if not self.installed:
            return "unavailable"
        if not self.authenticated:
            return "auth-required"
        return "ok"


def probe_env() -> GhEnv:
    """Check ``gh`` presence and ``gh auth status`` once."""
    env = GhEnv()
    if not env.installed:
        return env
    try:
        done = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=GH_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return env
    env.authenticated = done.returncode == 0
    if env.authenticated:
        who = run_gh(["api", "user", "--jq", ".login"])
        if who.status == "ok":
            env.login = str(who.data or "").strip()
    return env


def run_gh(args: list[str], timeout: int = GH_TIMEOUT) -> GhResult:
    """Run ``gh <args>`` and return the raw stdout, never raising."""
    try:
        done = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return GhResult("unavailable", error="gh is not installed")
    except subprocess.TimeoutExpired:
        return GhResult("upstream-error",
                        error=f"gh timed out after {timeout}s")
    except OSError as exc:
        return GhResult("unavailable", error=str(exc))
    if done.returncode != 0:
        stderr = (done.stderr or done.stdout or "").strip()
        if "404" in stderr or "Not Found" in stderr or "Could not resolve" in stderr:
            return GhResult("not-found", error=stderr)
        return GhResult("upstream-error", error=stderr)
    return GhResult("ok", data=done.stdout)


def run_json(args: list[str], timeout: int = GH_TIMEOUT) -> GhResult:
    """Run ``gh`` and parse stdout as JSON."""
    result = run_gh(args, timeout=timeout)
    if result.status != "ok":
        return result
    text = (result.data or "").strip()
    if not text:
        return GhResult("ok", data=None)
    try:
        return GhResult("ok", data=json.loads(text))
    except json.JSONDecodeError as exc:
        return GhResult("upstream-error", error=f"gh returned non-JSON: {exc}")
