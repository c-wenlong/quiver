"""File mentions for ``GET /v1/sessions/{id}/files``.

The phone's prompt box opens a menu when the user types ``@``; this lists
the files and folders under the session's working folder so the menu can
insert ``@relative/path``. The harness resolves the path itself when the
message lands, so only paths leave this module, never file contents.

Listing, once per working folder and cached for ``CACHE_TTL_S``:

- In a git work tree, ``git ls-files --cached --others --exclude-standard``,
  so whatever ``.gitignore`` hides stays hidden, even when that leaves
  nothing. Folders are the parents of the files it lists.
- Elsewhere, a breadth-first walk that skips dot-folders and build or
  dependency folders (``SKIP_DIRS``) and never descends a symlink: each
  folder is opened one part at a time with ``O_NOFOLLOW``, and its
  children are read through that open folder.

Concurrent requests for a folder whose listing is being built wait for
that build instead of starting another.

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
    cwd = local_cwd(row, hub_machine_id=hub_machine_id, machines=machines,
                    code="files_unavailable", what="file search")
    return search(cwd, query, limit)


def local_cwd(
    row: dict,
    *,
    hub_machine_id: str,
    machines: dict[str, dict],
    code: str,
    what: str,
) -> str:
    """The working folder of a session on the hub's own disk. A
    satellite's session raises ``422 <code>``: the hub can't read that
    disk. No absolute folder raises ``422 bad_cwd``."""
    mid = str(row.get("machine_id") or "")
    if row.get("remote") or (mid and mid != hub_machine_id):
        name = str((machines.get(mid) or {}).get("name") or mid or "?")
        raise FilesError(
            422, code, f"{what} isn't available for sessions on {name} yet",
        )
    cwd = row.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/") or "\x00" in cwd:
        raise FilesError(422, "bad_cwd", "this session has no working folder")
    return cwd


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


def safe_rel(rel: str) -> bool:
    """Whether ``rel`` is a non-empty relative path with no ``..`` part
    (either slash counts) and no NUL. A syntax check only: ``confine``
    still has to resolve it."""
    if not rel or rel.startswith("/") or "\x00" in rel:
        return False
    return ".." not in rel.replace("\\", "/").split("/")


def confine(root: str, rel: str) -> str | None:
    """The real path of ``rel`` under ``root`` (a ``resolve_root``
    result), or None when ``rel`` is absolute, has a ``..`` part, or
    resolves outside ``root`` once symlinks are followed."""
    if not safe_rel(rel):
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
    order, or None. Each pass matches greedily forward to an end, walks
    back to the latest start for that end, then retries just past that
    start, so a tighter match later in the text is still found."""
    n, m = len(text), len(q)
    best = None
    i = 0
    while i < n:
        k = 0
        pos = i
        while pos < n:
            if text[pos] == q[k]:
                k += 1
                if k == m:
                    break
            pos += 1
        if k < m:
            break
        end = pos
        k = m - 1
        while True:
            if text[pos] == q[k]:
                k -= 1
                if k < 0:
                    break
            pos -= 1
        width = end - pos + 1
        if best is None or width < best:
            best = width
            if best == m:
                break
        i = pos + 1
    return best


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


class _Pending:
    """One in-progress build that later callers for the same root wait on."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.listing: _Listing | None = None


_building: dict[str, _Pending] = {}

# Test hook: called with each queued folder's relative path just before
# the walk opens it.
_before_open = None


def _listing(root: str) -> _Listing:
    with _cache_lock:
        hit = _cache.get(root)
        if hit is not None and time.monotonic() - hit.at < CACHE_TTL_S:
            _cache.move_to_end(root)
            return hit
        pending = _building.get(root)
        owner = pending is None
        if owner:
            pending = _building[root] = _Pending()
    if not owner:
        # Another keystroke is already listing this folder: share it.
        pending.done.wait(LIST_BUDGET_S + 5.0)
        if pending.listing is not None:
            return pending.listing
        return _build(root)  # that build failed or stalled
    try:
        listing = _build(root)
        pending.listing = listing
        with _cache_lock:
            _cache[root] = listing
            _cache.move_to_end(root)
            while len(_cache) > CACHE_ROOTS:
                _cache.popitem(last=False)
        return listing
    finally:
        with _cache_lock:
            _building.pop(root, None)
        pending.done.set()


def _build(root: str) -> _Listing:
    deadline = time.monotonic() + LIST_BUDGET_S
    got = _git_files(root, deadline)
    if got is not None:
        # An empty answer is still git's answer (a folder holding only
        # ignored files): walking instead would list what .gitignore hides.
        rels, truncated = got
        entries, cut = _from_git(root, rels, deadline)
        return _Listing(tuple(entries), "git", truncated or cut,
                        time.monotonic())
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
                try:
                    rel = raw.decode("utf-8")
                except UnicodeDecodeError:
                    # Can't go out as JSON, and couldn't be typed as @path.
                    continue
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


def _utf8(rel: str) -> bool:
    """False for a name that only decoded through surrogateescape: it
    can't be sent as JSON and couldn't be typed as ``@path`` anyway."""
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _make(rel: str, kind: str, mtime: float) -> _Entry:
    name = rel.rpartition("/")[2]
    return _Entry(rel, kind, mtime, rel.casefold(), name.casefold())


def _from_git(
    root: str, rels: list[str], deadline: float
) -> tuple[list[_Entry], bool]:
    out: list[_Entry] = []
    dirs: dict[str, bool] = {}  # rel dir -> stays inside root

    def dir_ok(d: str) -> bool:
        ok = dirs.get(d)
        if ok is None:
            ok = confine(root, d) is not None
            dirs[d] = ok
        return ok

    cut = False
    for n, rel in enumerate(rels):
        if n % 256 == 255 and time.monotonic() > deadline:
            cut = True
            break
        if not safe_rel(rel) or not _utf8(rel):
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
    return out, cut


def _entry(root: str, rel: str, kind: str | None = None) -> _Entry | None:
    if not _utf8(rel):
        return None
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
    return _make(rel, kind, st.st_mtime)


_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def open_dir(root: str, rel_dir: str) -> int | None:
    """An fd for ``root/rel_dir`` opened one part at a time, each relative
    to the folder before it and refusing a symlink, so a folder swapped
    for a link after it was queued can't lead the walk outside root."""
    try:
        fd = os.open(root, _DIR_FLAGS)
    except OSError:
        return None
    for part in rel_dir.split("/") if rel_dir else ():
        try:
            nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
        except OSError:
            os.close(fd)
            return None
        os.close(fd)
        fd = nxt
    return fd


def _read_dir(fd: int, deadline: float) -> tuple[list[tuple], bool]:
    """``(name, is_dir, is_link)`` for the opened folder, name-sorted, and
    whether the read was cut short: at most ``MAX_FILES`` names are read,
    and the deadline is checked while reading, before any sort."""
    items: list[tuple] = []
    cut = False
    try:
        with os.scandir(fd) as it:
            for n, d in enumerate(it):
                if n >= MAX_FILES or (
                    n % 256 == 255 and time.monotonic() > deadline
                ):
                    cut = True
                    break
                try:
                    items.append((d.name, d.is_dir(follow_symlinks=False),
                                  d.is_symlink()))
                except OSError:
                    continue
    except OSError:
        return [], False
    items.sort()
    return items, cut


def _link_entry(
    root: str, rel: str, fd: int, name: str
) -> _Entry | None:
    """A symlink in the opened folder, listed when it resolves inside root
    (never descended: a link back up would loop). The target reached from
    the opened folder must be the same file the resolved path names."""
    real = os.path.realpath(os.path.join(root, rel))
    if not inside(root, real):
        return None
    try:
        via = os.stat(name, dir_fd=fd)
        st = os.stat(real)
    except OSError:
        return None  # dangling
    if (via.st_dev, via.st_ino) != (st.st_dev, st.st_ino):
        return None
    return _make(rel, DIR if stat.S_ISDIR(st.st_mode) else FILE, st.st_mtime)


def _walk(root: str, deadline: float) -> tuple[list[_Entry], bool]:
    out: list[_Entry] = []
    files = 0
    opened = 0
    truncated = False
    queue: deque[str] = deque([""])
    while queue:
        if opened >= MAX_DIRS or time.monotonic() > deadline:
            return out, True
        rel_dir = queue.popleft()
        opened += 1
        if _before_open is not None:
            _before_open(rel_dir)
        fd = open_dir(root, rel_dir)
        if fd is None:
            continue
        try:
            items, cut = _read_dir(fd, deadline)
            truncated = truncated or cut
            for name, is_dir, is_link in items:
                rel = f"{rel_dir}/{name}" if rel_dir else name
                if not _utf8(rel):
                    continue
                if is_dir:
                    if name.startswith(".") or name in SKIP_DIRS:
                        continue
                    try:
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        continue
                    out.append(_make(rel, DIR, st.st_mtime))
                    queue.append(rel)
                    continue
                if is_link:
                    entry = _link_entry(root, rel, fd, name)
                    if entry is None:
                        continue
                else:
                    try:
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        continue
                    entry = _make(rel, FILE, st.st_mtime)
                out.append(entry)
                if entry.kind == FILE:
                    files += 1
                    if files >= MAX_FILES:
                        return out, True
        finally:
            os.close(fd)
    return out, truncated
