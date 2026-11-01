"""The one module that spawns ``gh``.

SWE never stores GitHub tokens: every call inherits the user's own
``gh auth login`` the same way the Copilot quota fetcher in
``harness/rate_limits.py`` does. Reads are outcome-typed — a caller can
tell "no such PR" (``not-found``) apart from "GitHub is down"
(``upstream-error``) instead of both collapsing to an empty list.
"""

import json
import re
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
    error: str = ""

    @property
    def status(self) -> str:
        if not self.installed:
            return "unavailable"
        if self.error:
            return "upstream-error"  # the probe itself failed — not proof auth is missing
        if not self.authenticated:
            return "auth-required"
        return "ok"


def env_error(env: GhEnv) -> str:
    """Why ``env`` blocks a ``gh`` call, in the words every surface shows."""
    if not env.installed:
        return "gh is not installed: brew install gh"
    if env.error:
        return f"gh is not responding ({env.error})"
    return "gh is not authenticated: run `gh auth login`"


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
    except (OSError, subprocess.TimeoutExpired) as exc:
        env.error = str(exc)
        return env
    env.authenticated = done.returncode == 0
    if env.authenticated:
        # `auth status` already names the account ("Logged in to github.com
        # account NAME"), so a second `api user` call is pure latency.
        match = re.search(r"account (\S+)", done.stderr + done.stdout)
        if match:
            env.login = match.group(1)
    return env


def run_gh(args: list[str], timeout: int = GH_TIMEOUT,
           stdin: str | None = None) -> GhResult:
    """Run ``gh <args>`` and return the raw stdout, never raising.

    ``stdin`` is written to the process's standard input: free text such
    as a comment body goes there (``--body-file -``), never into argv.
    """
    try:
        done = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, timeout=timeout,
            input=stdin,
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
        # "Could not resolve host" is a DNS outage, not a missing resource —
        # only the GraphQL "Could not resolve to a Repository" means gone.
        if ("404" in stderr or "Not Found" in stderr
                or "not found" in stderr
                or "Could not resolve to a" in stderr):
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
        # Every JSON endpoint this is used for prints a body on success;
        # a clean exit with nothing on stdout means the reply got eaten,
        # and "ok + no data" would otherwise surface as an empty list.
        return GhResult("upstream-error",
                        error="gh exited cleanly but printed nothing")
    try:
        return GhResult("ok", data=json.loads(text))
    except json.JSONDecodeError as exc:
        return GhResult("upstream-error", error=f"gh returned non-JSON: {exc}")
