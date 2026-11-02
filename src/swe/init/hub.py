"""`swe init --hub=<git url>` and `swe setup --hub=<git url>`: bring an
existing hub onto a new machine.

A hub is the user's own git repository (a fork of the demo hub, or one that
grew on their first machine). Cloning it as ``~/.swe`` gives a second
machine the same AGENTS.md, skills, plugins, hooks and MCP config, with the
user's URL as ``origin`` so they push to their own hub from day one (#696).

swe never clones over a hub or over a file: ``~/.swe`` has to be absent,
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

from swe.paths import GITIGNORE_BODY, swe_dir_for

# Long enough for a slow first contact over SSH, short enough that a host
# that never answers does not hang a terminal forever.
REACH_TIMEOUT_S = 30
CLONE_TIMEOUT_S = 600

# The public demo hub people fork (or use as a template) to start their own
# (#1446). Every surface that asks for a hub URL shows it as a link line, and
# hides that line when this is empty.
DEMO_HUB_URL = "https://github.com/c-wenlong/swe-demo-hub"

# What every surface says beside the hub URL field.
FORK_NOTE = ("Fork a hub repo first (or use it as a template for a private copy), "
             "then paste your fork's URL.")


class HubError(Exception):
    """Why the hub could not be cloned, worded for the person running init.
    ``keep_staging`` asks clone_hub to leave its staging folder, which the
    message names, because part of the clone could not be put back."""

    def __init__(self, message: str, keep_staging: bool = False):
        super().__init__(message)
        self.keep_staging = keep_staging


def public_url(url: str) -> str:
    """``url`` safe to print: userinfo (a token in the URL) is dropped.

    A pattern rather than urlsplit, so a URL too malformed to parse still
    loses its credentials. scp-style ``git@host:path`` has no ``://`` and
    is left as is: ``git`` there is a login name, not a secret.
    """
    return _USERINFO.sub("", url)


_USERINFO = re.compile(r"(?<=://)[^/?#]*@")


# Either one means ~/.swe already holds a hub: the seed or cloned
# AGENTS.md, or a repository of its own.
HUB_MARKERS = ("AGENTS.md", ".git")


def check_target(home: Path) -> None:
    """Raise unless ``~/.swe`` can take a clone: absent, or a folder with
    no hub in it yet. Machine-local state already there is fine."""
    root = swe_dir_for(home)
    if not os.path.lexists(root):
        return
    if root.is_symlink():
        raise HubError("~/.swe is a symlink; swe will not clone through it")
    if not root.is_dir():
        raise HubError("~/.swe is a file, not a folder")
    try:
        names = {entry.name for entry in root.iterdir()}
    except OSError as exc:
        raise HubError(f"can't read ~/.swe: {exc.strerror or exc}") from None
    found = [name for name in HUB_MARKERS if name in names]
    if found:
        raise HubError(
            f"~/.swe already has a hub (it has {' and '.join(found)}), so swe will "
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
                f"~/.swe/{shown} is on this machine and in the hub, so swe will "
                "not clone over it. Move it aside, then try again.")
    return moves


def _move_in(clone: Path, root: Path) -> None:
    """Move the finished clone into ``~/.swe``: a plain rename while it
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
    except OSError as exc:
        stuck = []
        for src, dest in reversed(done):
            try:
                dest.rename(src)
            except OSError:
                stuck.append(dest)
        reason = f"can't move the clone into ~/.swe: {exc.strerror or exc}"
        if not stuck:
            raise HubError(reason) from None
        # Some of the clone is in ~/.swe and won't come back out: say
        # exactly what, and keep the rest of it where the user can see it.
        names = ", ".join(f"~/.swe/{p.relative_to(root)}" for p in reversed(stuck))
        raise HubError(
            f"{reason}. These couldn't be moved back and are still there: {names}. "
            f"The rest of the clone is in {clone}; remove both before trying again.",
            keep_staging=True) from None


def _exclude_local_state(root: Path) -> None:
    """Keep this machine's own state out of the cloned hub's git.

    A hub's ``.gitignore`` may be older than ours and miss a path swe now
    writes (``data/web-ledger/``), and the clone lands beside that state.
    The missing patterns go into ``.git/info/exclude``, which is this
    clone's alone, so the hub's own files stay untouched."""
    git_dir = root / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        return
    patterns = [line.strip() for line in GITIGNORE_BODY.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    have: set[str] = set()
    for name in (root / ".gitignore", git_dir / "info" / "exclude"):
        try:
            have.update(line.strip() for line in name.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    missing = [p for p in patterns if p not in have]
    if not missing:
        return
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    try:
        before = exclude.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        before = ""
    lead = "" if not before or before.endswith("\n") else "\n"
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write(f"{lead}# Added by swe: this machine's own state, never versioned.\n")
        fh.write("".join(f"{p}\n" for p in missing))


def can_clone(home: Path) -> bool:
    """Whether a hub can be cloned as ``~/.swe`` now. Setup asks for a
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
    """Clone ``url`` as ``~/.swe`` and return the path; HubError otherwise."""
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
    # Clone beside ~/.swe and move it into place only once git is done,
    # so a failed or timed-out clone never leaves a half-written hub that
    # the next --hub run would refuse.
    root = swe_dir_for(home)
    try:
        staging = Path(tempfile.mkdtemp(prefix=".swe-clone-", dir=home))
    except OSError as exc:
        raise HubError(f"can't make a folder to clone into: {exc.strerror or exc}") from None
    target = staging / "hub"
    keep = False
    try:
        cloned = _git(["clone", url, str(target)], CLONE_TIMEOUT_S, run)
        if cloned.returncode != 0:
            detail = (cloned.stderr or "").strip().splitlines()
            reason = f": {detail[-1]}" if detail else ""
            raise HubError(public_url(f"git clone {shown} failed{reason}"))
        check_target(home)  # no hub appeared there while git ran
        try:
            _move_in(target, root)
        except HubError as exc:
            keep = exc.keep_staging
            raise
        except OSError as exc:
            raise HubError(f"can't move the clone into ~/.swe: {exc.strerror or exc}") from None
    finally:
        if not keep:
            shutil.rmtree(staging, ignore_errors=True)
    try:
        _exclude_local_state(root)
    except OSError:
        # The clone is in place and the hub's own .gitignore still applies;
        # failing now would report a finished clone as not done.
        pass
    return root


def clone_and_register(home: Path, url: str, run=subprocess.run) -> dict:
    """Clone ``url`` as ``~/.swe``, then register its marketplaces.

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
    from swe.find.plugin_drift import FixError, apply_fix, plugin_drift

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
