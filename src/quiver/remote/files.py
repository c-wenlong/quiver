"""File mentions for ``GET /v1/sessions/{id}/files``.

The phone's prompt box opens a menu when the user types ``@``; this lists
the files and folders under the session's working folder so the menu can
insert ``@relative/path``. The harness resolves the path itself when the
message lands, so only paths leave this module, never file contents.

Listing, once per working folder and cached for ``CACHE_TTL_S``:

- In a git work tree, ``git ls-files --cached --others --exclude-standard``,
  so whatever ``.gitignore`` hides stays hidden. Folders are the parents of
  the files it lists.
- Elsewhere, a breadth-first walk that skips dot-folders and build or
  dependency folders (``SKIP_DIRS``) and never descends a symlink.

Either way at most ``MAX_FILES`` files are kept, and every path must stay
inside the working folder once symlinks are resolved: a symlink (or a
folder reached through one) pointing outside is dropped.

Ranking for a query, case-insensitive, in tiers (``_score``): the file name
equals the query, starts with it, holds it; the whole path starts with it,
holds it; the name, then the path, holds its letters in order. Within a tier
a tighter letter match leads, then the shorter path, then the more recently
modified one. An empty query lists the most recently modified files.

A query is only ever a filter over that listing: an absolute one, ``~``, or
one with a ``..`` part matches nothing rather than reaching outside.
"""

from __future__ import annotations

import os
import stat
import subprocess
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_QUERY_LEN = 200
# Files kept per listing; a working folder bigger than this is cut short
# and the answer says ``truncated``.
MAX_FILES = 20000
# Folders the walk opens before it stops, so a deep tree with few files
# can't keep it busy.
MAX_DIRS = 5000
# Wall-clock budget for one listing, git or walk.
LIST_BUDGET_S = 3.0
# A listing is reused this long, so each keystroke only re-ranks.
CACHE_TTL_S = 10.0
CACHE_ROOTS = 16

SKIP_DIRS = frozenset({
    "node_modules", "bower_components", "__pycache__", "venv",
    "site-packages", "build", "dist", "target", "DerivedData",
    "Pods", "Carthage", "coverage", "htmlcov",
})

FILE = "file"
DIR = "dir"


class FilesError(Exception):
    """An answer the route turns into ``protocol.error``."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Entry:
    path: str  # relative, "/"-separated, no trailing slash
    kind: str
    mtime: float
    fold: str  # path, casefolded
    name: str  # basename, casefolded


@dataclass(frozen=True)
class _Listing:
    entries: tuple[_Entry, ...]
    source: str  # "git" | "walk"
    truncated: bool
    at: float


_cache: "OrderedDict[str, _Listing]" = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# -- route ------------------------------------------------------------------


def for_session(
    row: dict,
    *,
    hub_machine_id: str,
    machines: dict[str, dict],
    query: str,
    limit: int,
) -> dict:
    """The route's answer for one catalog row; raises ``FilesError``."""
    mid = str(row.get("machine_id") or "")
    if row.get("remote") or (mid and mid != hub_machine_id):
        name = str((machines.get(mid) or {}).get("name") or mid or "?")
        raise FilesError(
            422, "files_unavailable",
            f"file search isn't available for sessions on {name} yet",
        )
    cwd = row.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/") or "\x00" in cwd:
        raise FilesError(422, "bad_cwd", "this session has no working folder")
    return search(cwd, query, limit)


def search(cwd: str, query: str, limit: int = DEFAULT_LIMIT) -> dict:
    root = resolve_root(cwd)
    listing = _listing(root)
    limit = max(1, min(int(limit), MAX_LIMIT))
    q = _clean_query(query)
    if q is None:
        hits: list[_Entry] = []
    elif not q:
        files = [e for e in listing.entries if e.kind == FILE]
        files.sort(key=lambda e: (-e.mtime, len(e.path), e.path))
        hits = files[:limit]
    else:
        scored = []
        for e in listing.entries:
            s = _score(e, q)
            if s is not None:
                scored.append((s, len(e.path), -e.mtime, e.path, e))
        scored.sort(key=lambda t: t[:4])
        hits = [t[-1] for t in scored[:limit]]
    return {
        "cwd": cwd,
        "query": str(query or "")[:MAX_QUERY_LEN],
        "source": listing.source,
        "truncated": listing.truncated,
        "entries": [
            {"path": e.path, "kind": e.kind, "mtime": e.mtime} for e in hits
        ],
    }


# -- confinement (shared with any route that touches a session's folder) ----


def resolve_root(cwd: str) -> str:
    """The session folder ``cwd`` with symlinks resolved, the root every
    later ``confine`` check measures against. Raises ``FilesError`` when
    it is relative, missing, not a folder, or unreadable."""
    if not cwd.startswith("/") or "\x00" in cwd:
        raise FilesError(422, "bad_cwd", "working folder must be absolute")
    root = os.path.realpath(cwd)
    if not os.path.exists(root):
        raise FilesError(
            422, "cwd_missing", f"working folder no longer exists: {cwd}"
        )
    if not os.path.isdir(root):
        raise FilesError(
            422, "cwd_missing", f"working folder is not a folder: {cwd}"
        )
    if not os.access(root, os.R_OK | os.X_OK):
        raise FilesError(403, "not_readable", f"can't read {cwd}")
    return root


def inside(root: str, real: str) -> bool:
    """Whether the resolved path ``real`` is ``root`` or under it. Both
    must already be ``realpath``-ed."""
    return real == root or real.startswith(root.rstrip("/") + "/")


def _safe_rel(rel: str) -> bool:
    if not rel or rel.startswith("/") or "\x00" in rel:
        return False
    return ".." not in rel.replace("\\", "/").split("/")


def confine(root: str, rel: str) -> str | None:
    """The real path of ``rel`` under ``root`` (a ``resolve_root``
    result), or None when ``rel`` is absolute, has a ``..`` part, or
    resolves outside ``root`` once symlinks are followed."""
    if not _safe_rel(rel):
        return None
    real = os.path.realpath(os.path.join(root, rel))
    return real if inside(root, real) else None


# -- ranking ----------------------------------------------------------------


def _clean_query(query: str) -> str | None:
    """Casefolded filter text, ``""`` for none, ``None`` when it tries to
    leave the working folder."""
    q = str(query or "")[:MAX_QUERY_LEN].strip()
    if q.startswith("@"):
        q = q[1:]
    while q.startswith("./"):
        q = q[2:]
    if q.startswith(("/", "~")) or "\x00" in q:
        return None
    if ".." in q.replace("\\", "/").split("/"):
        return None
    return q.casefold()


def _span(text: str, q: str) -> int | None:
    """Width of the tightest window of ``text`` holding ``q``'s letters in
    order, or None. Greedy forward to the end, then backward to tighten."""
    i = 0
    end = -1
    for pos, ch in enumerate(text):
        if ch == q[i]:
            i += 1
            if i == len(q):
                end = pos
                break
    if end < 0:
        return None
    j = len(q) - 1
    start = end
    for pos in range(end, -1, -1):
        if text[pos] == q[j]:
            j -= 1
            if j < 0:
                start = pos
                break
    return end - start + 1


def _score(e: _Entry, q: str) -> tuple[int, int] | None:
    name, fold = e.name, e.fold
    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    if name == q or stem == q:
        return (0, 0)
    if name.startswith(q):
        return (1, 0)
    at = name.find(q)
    if at >= 0:
        return (2, at)
    if fold.startswith(q):
        return (3, 0)
    at = fold.find(q)
    if at >= 0:
        return (4, at)
    if "/" not in q:
        span = _span(name, q)
        if span is not None:
            return (5, span)
    span = _span(fold, q)
    if span is not None:
        return (6, span)
    return None


# -- listing ----------------------------------------------------------------


def _listing(root: str) -> _Listing:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(root)
        if hit is not None and now - hit.at < CACHE_TTL_S:
            _cache.move_to_end(root)
            return hit
    listing = _build(root)
    with _cache_lock:
        _cache[root] = listing
        _cache.move_to_end(root)
        while len(_cache) > CACHE_ROOTS:
            _cache.popitem(last=False)
    return listing


def _build(root: str) -> _Listing:
    deadline = time.monotonic() + LIST_BUDGET_S
    got = _git_files(root, deadline)
    if got is not None and got[0]:
        rels, truncated = got
        entries = _from_git(root, rels)
        return _Listing(tuple(entries), "git", truncated, time.monotonic())
    entries, truncated = _walk(root, deadline)
    return _Listing(tuple(entries), "walk", truncated, time.monotonic())




def _git_files(root: str, deadline: float) -> tuple[list[str], bool] | None:
    """Relative paths git would show, or None outside a work tree.

    Streams ``-z`` output and stops at ``MAX_FILES`` or the deadline, so a
    huge untracked tree can't stall the request. The repo's fsmonitor hook
    is switched off and no optional index lock is taken: listing must not
    run repo-configured programs or write to the repo.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.Popen(
            ["git", "-c", "core.fsmonitor=false", "-C", root, "ls-files",
             "--cached", "--others", "--exclude-standard", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env,
        )
    except OSError:
        return None
    timer = threading.Timer(max(0.1, deadline - time.monotonic()), proc.kill)
    timer.start()
    seen: set[str] = set()
    rels: list[str] = []
    truncated = False
    buf = b""
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            *parts, buf = buf.split(b"\0")
            for raw in parts:
                rel = raw.decode("utf-8", "surrogateescape")
                if rel in seen:
                    continue  # unmerged entries repeat once per stage
                seen.add(rel)
                rels.append(rel)
            if len(rels) >= MAX_FILES:
                truncated = True
                break
    finally:
        if truncated:
            proc.kill()
        if proc.stdout is not None:
            proc.stdout.close()
        rc = proc.wait()
        timer.cancel()
    if truncated:
        return rels[:MAX_FILES], True
    if rc != 0:
        # Not a repo, git missing a feature, or killed at the deadline
        # with nothing usable: a killed run that did list files is kept.
        if rc < 0 and rels:
            return rels, True
        return None
    return rels, False


def _from_git(root: str, rels: list[str]) -> list[_Entry]:
    out: list[_Entry] = []
    dirs: dict[str, bool] = {}  # rel dir -> stays inside root

    def dir_ok(d: str) -> bool:
        ok = dirs.get(d)
        if ok is None:
            ok = confine(root, d) is not None
            dirs[d] = ok
        return ok

    for rel in rels:
        if not _safe_rel(rel):
            continue
        parent = rel.rpartition("/")[0]
        # Every folder on the way must resolve inside root; walk up so
        # each folder is resolved once however many files it holds.
        ok = True
        d = parent
        while d:
            if not dir_ok(d):
                ok = False
                break
            d = d.rpartition("/")[0]
        if not ok:
            continue
        entry = _entry(root, rel)
        if entry is not None:
            out.append(entry)
    for d, ok in dirs.items():
        if ok:
            entry = _entry(root, d, kind=DIR)
            if entry is not None:
                out.append(entry)
    return out


def _entry(root: str, rel: str, kind: str | None = None) -> _Entry | None:
    full = os.path.join(root, rel)
    try:
        st = os.lstat(full)
    except OSError:
        return None  # deleted since git indexed it
    if stat.S_ISLNK(st.st_mode):
        real = os.path.realpath(full)
        if not inside(root, real):
            return None
        try:
            st = os.stat(real)
        except OSError:
            return None  # dangling
    if kind is None:
        kind = DIR if stat.S_ISDIR(st.st_mode) else FILE
    name = rel.rpartition("/")[2]
    return _Entry(rel, kind, st.st_mtime, rel.casefold(), name.casefold())


def _walk(root: str, deadline: float) -> tuple[list[_Entry], bool]:
    out: list[_Entry] = []
    files = 0
    opened = 0
    queue: deque[str] = deque([""])
    while queue:
        if opened >= MAX_DIRS or time.monotonic() > deadline:
            return out, True
        rel_dir = queue.popleft()
        opened += 1
        try:
            with os.scandir(os.path.join(root, rel_dir)) as it:
                items = sorted(it, key=lambda d: d.name)
        except OSError:
            continue
        for item in items:
            rel = f"{rel_dir}/{item.name}" if rel_dir else item.name
            try:
                is_dir = item.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if item.name.startswith(".") or item.name in SKIP_DIRS:
                    continue
                entry = _entry(root, rel, kind=DIR)
                if entry is not None:
                    out.append(entry)
                    queue.append(rel)
                continue
            # A symlink is listed when it resolves inside root, never
            # descended (a link back up would loop).
            entry = _entry(root, rel)
            if entry is None:
                continue
            out.append(entry)
            if entry.kind == FILE:
                files += 1
                if files >= MAX_FILES:
                    return out, True
    return out, False
