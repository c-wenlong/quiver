"""`swe init --hub=<git url>` and `swe setup --hub=<git url>`: bring an
existing hub onto a new machine.

A hub is the user's own git repository (a fork of the demo hub, or one that
grew on their first machine). Cloning it as ``~/.quiver`` gives a second
machine the same AGENTS.md, skills, plugins, hooks and MCP config, with the
user's URL as ``origin`` so they push to their own hub from day one (#696).

swe never clones over a hub or over a file: ``~/.quiver`` has to be absent,
or a folder with no hub in it yet (no ``AGENTS.md``, no ``.git``). What the
app, the remote hub and earlier swe runs write there on their own (caches,
the usage ledgers, pairing state) does not count, since a hub's
``.gitignore`` keeps all of it out of git. The clone is moved in around it,
and a path that exists on both sides stops the clone before anything moves.
The URL is checked with ``git ls-remote`` first, so a typo or a missing
credential fails before anything is written.
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

# The public demo hub people fork to start their own. It doesn't exist yet
# (#696), so this is empty, and every surface that asks for a hub URL shows
# its link line only when it is set: adding the repo is this one line.
DEMO_HUB_URL = ""

# What every surface says beside the hub URL field.
FORK_NOTE = ("Fork a hub repo first (or use it as a template for a private copy), "
             "then paste your fork's URL.")


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


# Either one means ~/.quiver already holds a hub: the seed or cloned
# AGENTS.md, or a repository of its own.
HUB_MARKERS = ("AGENTS.md", ".git")


def check_target(home: Path) -> None:
    """Raise unless ``~/.quiver`` can take a clone: absent, or a folder with
    no hub in it yet. Machine-local state already there is fine."""
    root = quiver_dir_for(home)
    if not os.path.lexists(root):
        return
    if root.is_symlink():
        raise HubError("~/.quiver is a symlink; swe will not clone through it")
    if not root.is_dir():
        raise HubError("~/.quiver is a file, not a folder")
    try:
        names = {entry.name for entry in root.iterdir()}
    except OSError as exc:
        raise HubError(f"can't read ~/.quiver: {exc.strerror or exc}") from None
    found = [name for name in HUB_MARKERS if name in names]
    if found:
        raise HubError(
            f"~/.quiver already has a hub (it has {' and '.join(found)}), so swe will "
            "not clone over it. Run swe init without --hub to repair it, or move it "
            "aside first.")


def _plan_merge(src: Path, dest: Path, rel: str = "") -> list[tuple[Path, Path]]:
    """The renames that move ``src``'s contents into ``dest`` without
    overwriting anything: a whole entry where ``dest`` has nothing by that
    name, a walk into a folder both sides have. Raises HubError naming the
    first path both sides have that isn't a folder on both."""
    moves = []
    for entry in sorted(src.iterdir(), key=lambda p: p.name):
        there = dest / entry.name
        shown = f"{rel}{entry.name}"
        if not os.path.lexists(there):
            moves.append((entry, there))
        elif (entry.is_dir() and not entry.is_symlink()
              and there.is_dir() and not there.is_symlink()):
            moves += _plan_merge(entry, there, f"{shown}/")
        else:
            raise HubError(
                f"~/.quiver/{shown} is on this machine and in the hub, so swe will "
                "not clone over it. Move it aside, then try again.")
    return moves


def _move_in(clone: Path, root: Path) -> None:
    """Move the finished clone into ``~/.quiver``: a plain rename while it
    is absent, else entry by entry around what is already there. Every
    collision is found before anything moves, and a rename that fails puts
    back the ones before it, so no half-merged hub is left behind."""
    if not os.path.lexists(root):
        clone.rename(root)
        return
    moves = _plan_merge(clone, root)
    done: list[tuple[Path, Path]] = []
    try:
        for src, dest in moves:
            src.rename(dest)
            done.append((src, dest))
    except OSError:
        for src, dest in reversed(done):
            try:
                dest.rename(src)
            except OSError:
                pass
        raise


def can_clone(home: Path) -> bool:
    """Whether a hub can be cloned as ``~/.quiver`` now. Setup asks for a
    hub URL only then."""
    try:
        check_target(home)
    except HubError:
        return False
    return True


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
        check_target(home)  # no hub appeared there while git ran
        try:
            _move_in(target, root)
        except OSError as exc:
            raise HubError(f"can't move the clone into ~/.quiver: {exc.strerror or exc}") from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return root


def clone_and_register(home: Path, url: str, run=subprocess.run) -> dict:
    """Clone ``url`` as ``~/.quiver``, then register its marketplaces.

    What ``swe setup --hub`` runs; ``swe init --hub`` does the same with
    its linking pass in between. Returns ``{"url", "path",
    "marketplaces": [{"harness", "marketplace", "error"}]}`` with ``url``
    already stripped of credentials. Raises HubError when nothing was
    cloned.
    """
    root = clone_hub(home, url, run=run)
    return {
        "url": public_url(url.strip()),
        "path": str(root),
        "marketplaces": [
            {"harness": harness, "marketplace": marketplace, "error": error}
            for harness, marketplace, error in register_marketplaces(home)
        ],
    }


def register_marketplaces(home: Path, run=None) -> list[tuple[str, str, str | None]]:
    """Register each hub ``plugins/<name>`` marketplace with every harness.

    The work is ``swe find plugins``'s own ``unregistered`` fixes, so the
    commands are the ones that module builds (``claude plugin marketplace
    add <dir>``, ``codex plugin marketplace add <dir>``) and a harness that
    is not installed is never asked. Returns ``(harness, marketplace,
    error)`` per attempt; ``error`` is None when it worked.
    """
    from quiver.find.plugin_drift import FixError, apply_fix, plugin_drift

    results = []
    for finding in plugin_drift(home):
        if finding.kind != "unregistered":
            continue
        try:
            apply_fix(home, finding.id, run=run)
        except FixError as exc:
            results.append((finding.harness, finding.marketplace, str(exc)))
        else:
            results.append((finding.harness, finding.marketplace, None))
    return results
