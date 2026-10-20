"""Pinned repositories and repo-name resolution for `swe gh`.

The dashboard works on a list of repos the user pins, stored as authored
state in ``~/.quiver/config/github.json``. Any command also resolves a
repo from ``--repo=owner/name`` or from the git remote of the working
directory it runs in.
"""

import json
import re
import subprocess
import time
from pathlib import Path

from quiver import paths

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
    """Pinned repos as ``[{name, added_at}]``, oldest first."""
    path = path or paths.GITHUB_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return []
    repos = data.get("repos")
    if not isinstance(repos, list):
        return []
    return [r for r in repos if isinstance(r, dict)
            and isinstance(r.get("name"), str)]


def save_pinned(repos: list[dict], path: Path | None = None) -> None:
    path = path or paths.GITHUB_FILE
    body = {"schema": 1, "repos": repos}
    paths.atomic_write_text(path, json.dumps(body, indent=2) + "\n")


def add_pinned(name: str, path: Path | None = None) -> list[dict]:
    repos = load_pinned(path)
    if not any(r["name"].lower() == name.lower() for r in repos):
        repos.append({"name": name, "added_at": int(time.time())})
        save_pinned(repos, path)
    return repos


def remove_pinned(name: str, path: Path | None = None) -> tuple[list[dict], bool]:
    repos = load_pinned(path)
    kept = [r for r in repos if r["name"].lower() != name.lower()]
    if len(kept) != len(repos):
        save_pinned(kept, path)
    return kept, len(kept) != len(repos)


def resolve(repo_arg: str | None, cwd: Path | None = None) -> list[str]:
    """Which repos a query runs against, in priority order.

    ``--repo=`` wins, then the working directory's remote, then every
    pinned repo — so ``swe gh pr list`` alone is a dashboard over pins.
    """
    if repo_arg:
        name = normalize_repo_input(repo_arg)
        return [name] if name else []
    local = repo_at(cwd or Path.cwd())
    if local:
        return [local]
    return [r["name"] for r in load_pinned()]
