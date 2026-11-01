"""What the hub's GitHub surfaces share: ``gh`` reads behind a short
cache, the write lock, the hub's pinned repos, and the env payload.

The web app's Repositories and Inbox pages, the ``/v1`` issue, pull and
task routes and the Linear reads all go through these. Every read uses
the builders behind ``swe gh … --json`` (``swe.github.cli``), in
process, with the hub machine's own ``gh`` login. The writes (merging a
PR, pinning a repo, marking notifications read) call the same functions
``swe gh pr merge``, ``swe gh repo add`` and ``swe gh inbox read`` do,
one at a time under ``WRITE_LOCK``, and drop what they changed from the
cache. Kept here rather than under ``swe.webapp`` so ``swe.remote``
doesn't import the web app.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from swe import paths
from swe.github import cli as ghcli
from swe.github import gh, repos

# The Mac app polls every surface on a minute timer, so a minute is the
# freshness both clients agree on. A failed read is kept only briefly,
# so a blip clears on the next look without a hammering retry loop.
TTL = 60.0
FAIL_TTL = 10.0
# A forced refresh still reuses a reply younger than this: two tabs or
# a double click must not cost two rounds of `gh` calls.
REFRESH_FLOOR = 5.0
# Old entries (PRs viewed once, repos unpinned) are dropped on write.
_PRUNE_AFTER = 600.0


class TtlCache:
    """A per-key cache with one fetch in flight per key: a second
    caller for the same key waits for the first one's reply instead of
    starting its own ``gh`` run."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Any, tuple[float, float, float, Any]] = {}
        self._key_locks: dict[Any, threading.Lock] = {}
        # Callers holding or waiting on each key's lock: a lock is only
        # dropped when nobody uses it, so two callers never end up on
        # different locks for one key.
        self._users: dict[Any, int] = {}
        # Bumped by every ``drop``: a fetch that began before a write
        # may have read the old state, so its reply is returned to its
        # caller but not kept.
        self._epoch = 0

    def get(self, key, fetch: Callable[[], Any], *, refresh: bool = False,
            ok: Callable[[Any], bool] = lambda _v: True) -> tuple[Any, float]:
        """``(value, fetched_at)``; ``fetched_at`` is wall-clock epoch."""
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
            self._users[key] = self._users.get(key, 0) + 1
        try:
            with key_lock:
                return self._get_locked(key, fetch, refresh, ok)
        finally:
            with self._lock:
                left = self._users.get(key, 1) - 1
                if left:
                    self._users[key] = left
                else:
                    self._users.pop(key, None)
                    # Nothing kept for the key (a dropped entry, or a
                    # reply a write made stale): nothing needs its lock.
                    if key not in self._entries:
                        self._key_locks.pop(key, None)

    def _get_locked(self, key, fetch, refresh, ok) -> tuple[Any, float]:
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None:
            at, ttl, wall, value = entry
            age = now - at
            if age < ttl and not (refresh and age >= REFRESH_FLOOR):
                return value, wall
        with self._lock:
            epoch = self._epoch
        value = fetch()
        wall = time.time()
        ttl = TTL if ok(value) else FAIL_TTL
        with self._lock:
            if self._epoch == epoch:
                self._entries[key] = (self._clock(), ttl, wall, value)
            self._prune()
        return value, wall

    def drop(self, match: Callable[[Any], bool]) -> None:
        """Forget every entry whose key ``match``es, after a write, and
        keep no reply from a fetch still in flight."""
        with self._lock:
            self._epoch += 1
            for key in [k for k in self._entries if match(k)]:
                self._entries.pop(key, None)
                if not self._users.get(key):
                    self._key_locks.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _prune(self) -> None:
        now = self._clock()
        stale = [k for k, (at, _t, _w, _v) in self._entries.items()
                 if now - at > _PRUNE_AFTER]
        for key in stale:
            self._entries.pop(key, None)
            if not self._users.get(key):
                self._key_locks.pop(key, None)


CACHE = TtlCache()

# The hub answers each request on its own thread. Writes to GitHub run
# one at a time, so a double click or two tabs can't merge twice or race
# a state check against another write; the Mac app keys its actionBusy
# set for the same reason.
WRITE_LOCK = threading.Lock()


def home_key(home: Path | None) -> str:
    return str(home) if home else ""


def pinned(home: Path | None) -> list[dict]:
    """The hub's pins (``swe gh repo list``), in stored order."""
    return repos.load_pinned(paths.github_file_for(home))


class PinError(ValueError):
    """A ``repo`` argument that isn't a pin: ``status`` 400 when it isn't
    owner/name at all, 404 when it is but the hub hasn't pinned it."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def pinned_name(home: Path | None, raw: str) -> str:
    """The pin a ``repo`` argument names, spelled as pinned; raises
    ``PinError``. Clients only read repos the hub has pinned, as the Mac
    app does."""
    name = repos.normalize_repo_input(raw or "")
    if not name:
        raise PinError(400, "bad_repo", "Not a GitHub repo: want owner/name.")
    for pin in pinned(home):
        if pin["name"].lower() == name.lower():
            return pin["name"]
    raise PinError(404, "not_pinned", f"{name} is not pinned on the hub.")


def env_payload(env: gh.GhEnv) -> dict:
    return ghcli._env_payload(env)


def probe(home: Path | None, refresh: bool = False) -> gh.GhEnv:
    """``gh auth status``, cached like the reads it classifies."""
    env, _at = CACHE.get(("env", home_key(home)), gh.probe_env,
                         refresh=refresh,
                         ok=lambda e: e.status == "ok")
    return env


def env_after(home: Path | None, entries: list[dict],
              refresh: bool = False) -> gh.GhEnv:
    """``swe``'s rule: replies that all came back ok prove ``gh`` works,
    so only a failure pays for the probe that names why."""
    if all(e.get("status") == "ok" for e in entries):
        return gh.GhEnv(installed=True, authenticated=True)
    return probe(home, refresh=refresh)


# One worker per repo, at most eight: ``swe gh``'s own fan-out.
fan = ghcli._fan
