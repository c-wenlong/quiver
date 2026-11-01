"""Pinned repositories and repo-name resolution for `swe gh`.

The dashboard works on a list of repos the user pins, stored as authored
state in ``~/.quiver/config/github.json``. Any command also resolves a
repo from ``--repo=owner/name`` or from the git remote of the working
directory it runs in.
"""

import contextlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from swe import paths

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SSH_RE = re.compile(r"^git@([^:]+):(.+?)(?:\.git)?$")
_HTTPS_RE = re.compile(r"^https?://([^/]+)/(.+?)(?:\.git)?/?$")


def repo_from_remote(url: str) -> str | None:
    """``git@github.com:o/r.git`` or ``https://github.com/o/r`` -> ``o/r``."""
    url = (url or "").strip()
    if not url:
        return None
    match = _SSH_RE.match(url) or _HTTPS_RE.match(url)
    if not match:
        return None
    host, rest = match.group(1), match.group(2)
    if host.lower() not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in rest.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    name = f"{parts[0]}/{parts[1]}"
    return name if _NAME_RE.match(name) else None


def normalize_repo_input(text: str) -> str | None:
    """``owner/name``, a GitHub URL, or an SSH remote -> ``owner/name``.

    URLs pointing at a subpage (``…/pull/12``) keep only the repo pair.
    """
    text = (text or "").strip()
    if not text:
        return None
    if _NAME_RE.match(text):
        return text
    if text.startswith("git@") or "github.com" in text:
        if "://" not in text and not text.startswith("git@"):
            text = f"https://{text}"
        return repo_from_remote(text)
    return None


def repo_at(path: Path | None) -> str | None:
    """The GitHub repo a directory belongs to, from its git remotes.

    ``origin`` wins; a fork's PRs still belong to it. When origin is not a
    GitHub remote (a mirror, a local path) ``upstream`` gets a turn, which
    is where fork-based checkouts keep the canonical repo.
    """
    if path is None:
        return None
    for remote in ("origin", "upstream"):
        try:
            done = subprocess.run(
                ["git", "-C", str(path), "remote", "get-url", remote],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            name = repo_from_remote(done.stdout)
            if name:
                return name
    return None


def load_pinned(path: Path | None = None) -> list[dict]:
    """Pinned repos as ``[{name, added_at}]``, in stored order.

    A hand-edited or half-written file degrades to ``[]`` instead of
    crashing a command — entries need a string ``name``, and ``added_at``
    is coerced to an int so ``repo list`` never blows up on a bad value.
    """
    path = path or paths.GITHUB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    pinned = data.get("repos")
    if not isinstance(pinned, list):
        return []
    out = []
    for entry in pinned:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        try:
            added_at = int(entry.get("added_at") or 0)
        except (TypeError, ValueError):
            added_at = 0
        out.append({"name": entry["name"], "added_at": added_at})
    return out


def save_pinned(repos: list[dict], path: Path | None = None) -> None:
    path = path or paths.GITHUB_FILE
    body = {"schema": 1, "repos": repos}
    paths.atomic_write_text(path, json.dumps(body, indent=2) + "\n")


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive lock beside the store so overlapping `repo add`/`remove`
    calls can't drop each other's change."""
    try:
        import fcntl
    except ImportError:  # non-POSIX: best effort without a lock
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.parent / (path.name + ".lock"),
                 os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def add_pinned(name: str, path: Path | None = None) -> list[dict]:
    path = path or paths.GITHUB_FILE
    with _locked(path):
        pinned = load_pinned(path)
        if not any(r["name"].lower() == name.lower() for r in pinned):
            pinned.append({"name": name, "added_at": int(time.time())})
            save_pinned(pinned, path)
        return pinned


def remove_pinned(name: str, path: Path | None = None) -> tuple[list[dict], bool]:
    path = path or paths.GITHUB_FILE
    with _locked(path):
        pinned = load_pinned(path)
        kept = [r for r in pinned if r["name"].lower() != name.lower()]
        if len(kept) != len(pinned):
            save_pinned(kept, path)
        return kept, len(kept) != len(pinned)


def reorder_pinned(order: list[str], path: Path | None = None) -> list[dict]:
    """Rewrite pin order: names in ``order`` take their positions first,
    any pinned repo not named keeps its relative place at the end — a
    caller moving one entry to the front needn't list the rest.
    """
    path = path or paths.GITHUB_FILE
    wanted = [n.lower() for n in order]
    with _locked(path):
        pinned = load_pinned(path)
        by_name = {r["name"].lower(): r for r in pinned}
        head, seen = [], set()
        for n in wanted:
            if n in by_name and n not in seen:
                seen.add(n)
                head.append(by_name[n])
        tail = [r for r in pinned if r["name"].lower() not in seen]
        save_pinned(head + tail, path)
        return head + tail


def resolve(repo_arg: str | None, cwd: Path | None = None) -> list[str]:
    """Which repos a query runs against, in priority order.

    ``--repo=`` wins — a comma list (``--repo=a/b,c/d``) resolves every
    part — then the working directory's remote, then every pinned repo,
    so ``swe gh pr list`` alone is a dashboard over pins.
    """
    if repo_arg:
        names = [normalize_repo_input(part)
                 for part in repo_arg.split(",")]
        return [n for n in names if n]
    local = repo_at(cwd or Path.cwd())
    if local:
        return [local]
    return [r["name"] for r in load_pinned()]
