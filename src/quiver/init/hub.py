"""`swe init --hub=<git url>`: bring an existing hub onto a new machine.

A hub is the user's own git repository (a fork of the demo hub, or one that
grew on their first machine). Cloning it as ``~/.quiver`` gives a second
machine the same AGENTS.md, skills, plugins, hooks and MCP config, with the
user's URL as ``origin`` so they push to their own hub from day one (#696).

Init never clones over something: ``~/.quiver`` has to be absent or an empty
directory. The URL is checked with ``git ls-remote`` first, so a typo or a
missing credential fails before anything is written.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from quiver.paths import quiver_dir_for

# Long enough for a slow first contact over SSH, short enough that a host
# that never answers does not hang a terminal forever.
REACH_TIMEOUT_S = 30
CLONE_TIMEOUT_S = 600


class HubError(Exception):
    """Why the hub could not be cloned, worded for the person running init."""


def public_url(url: str) -> str:
    """``url`` safe to print: userinfo (a token in the URL) is dropped.

    A pattern rather than urlsplit, so a URL too malformed to parse still
    loses its credentials. scp-style ``git@host:path`` has no ``://`` and
    is left as is: ``git`` there is a login name, not a secret.
    """
    return _USERINFO.sub("", url)


_USERINFO = re.compile(r"(?<=://)[^/?#]*@")


def check_target(home: Path) -> None:
    """Raise unless ``~/.quiver`` can be cloned into: absent or an empty dir."""
    root = quiver_dir_for(home)
    if not os.path.lexists(root):
        return
    if root.is_symlink():
        raise HubError("~/.quiver is a symlink; init will not clone through it")
    if not root.is_dir():
        raise HubError("~/.quiver is a file, not a folder")
    try:
        busy = any(root.iterdir())
    except OSError as exc:
        raise HubError(f"can't read ~/.quiver: {exc.strerror or exc}") from None
    if busy:
        raise HubError(
            "~/.quiver already has files, so init will not clone over it. "
            "Run swe init without --hub to repair it, or move it aside first.")


def _git(args: list[str], timeout: int, run) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        return run(["git", *args], capture_output=True, text=True,
                   timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise HubError(f"git {args[0]} took longer than {timeout}s") from None
    except OSError as exc:
        raise HubError(f"could not run git: {exc}") from None


def clone_hub(home: Path, url: str, run=subprocess.run) -> Path:
    """Clone ``url`` as ``~/.quiver`` and return the path; HubError otherwise."""
    url = url.strip()
    if not url:
        raise HubError("--hub needs a git URL: swe init --hub=<url>")
    if url.startswith("-"):
        raise HubError(public_url(f"not a git URL: {url}"))
    check_target(home)
    shown = public_url(url)
    reach = _git(["ls-remote", "--exit-code", url, "HEAD"], REACH_TIMEOUT_S, run)
    # 2 is --exit-code's "reachable, but no HEAD": an empty repository.
    if reach.returncode not in (0, 2):
        detail = (reach.stderr or "").strip().splitlines()
        reason = f": {detail[-1]}" if detail else ""
        raise HubError(public_url(f"can't reach {shown}{reason}"))
    # Clone beside ~/.quiver and move it into place only once git is done,
    # so a failed or timed-out clone never leaves a half-written hub that
    # the next --hub run would refuse.
    root = quiver_dir_for(home)
    try:
        staging = Path(tempfile.mkdtemp(prefix=".quiver-clone-", dir=home))
    except OSError as exc:
        raise HubError(f"can't make a folder to clone into: {exc.strerror or exc}") from None
    target = staging / "hub"
    try:
        cloned = _git(["clone", url, str(target)], CLONE_TIMEOUT_S, run)
        if cloned.returncode != 0:
            detail = (cloned.stderr or "").strip().splitlines()
            reason = f": {detail[-1]}" if detail else ""
            raise HubError(public_url(f"git clone {shown} failed{reason}"))
        check_target(home)  # nothing appeared there while git ran
        try:
            if root.is_dir():
                root.rmdir()  # the empty folder check_target allowed
            target.rename(root)
        except OSError as exc:
            raise HubError(f"can't move the clone into ~/.quiver: {exc.strerror or exc}") from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return root
