"""Persistent per-file parse cache for session listings.

A listing reads every transcript on disk, but between two listings almost
none of them change. This cache keeps what a parser extracted from one file,
keyed by the file's absolute path, and hands it back while the file's stat
signature is unchanged, so an unchanged file costs one ``stat`` instead of a
read and a JSON parse per line.

Rules that keep it from ever showing a stale session:

- **The signature is taken before the read.** A file written between the
  ``stat`` and the read is stored under the older signature, so the next run
  sees a mismatch and reads it again. The other order would pin old content
  to a new signature for good.
- **The signature is (size, mtime_ns, ctime_ns, inode).** ``ctime`` moves on
  every content change and cannot be set back from userland, so a write that
  restores the old mtime (``touch -m``, ``cp -p``) still invalidates; an
  atomic replace changes the inode.
- **Only what a file says is cached.** Anything a parser derives from other
  state (a sidecar index, a sqlite store, whether a directory exists, the
  clock) is applied after the lookup, on every run, or keyed by that
  sidecar's own path.
- **Any code change discards it.** The header holds a format version, the
  package version and a hash of the modules whose logic produced the
  values, so an upgrade or an edit cannot serve results from older code.
- **Any read problem is a miss.** A missing, corrupt or foreign cache file
  reads as empty; an entry with the wrong shape is ignored.

Caching only happens inside ``activate()``, which the aggregator opens around
a listing. Parsers called anywhere else compute every time, exactly as
before. When the block closes, entries for files a harness's parser no
longer visited are pruned, but only for harnesses whose parser ran to
completion, so an ``--agent`` filtered run leaves the others alone.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from quiver.paths import atomic_write_text, session_parse_cache_file_for

# Bump when the on-disk layout of an entry changes.
FORMAT_VERSION = 1

# Modules whose code decides what a cached value contains. Editing any of
# them changes the fingerprint and discards every entry.
_FINGERPRINT_MODULES = (
    "quiver.sessions.parse_cache",
    "quiver.sessions.parsers",
    "quiver.sessions.engines.jsonl_engine",
    "quiver.sessions.engines.common",
)

_local = threading.local()
_fingerprint: str | None = None


def _source_fingerprint() -> str:
    """Format version, package version and a hash of the deciding modules."""
    global _fingerprint
    if _fingerprint is not None:
        return _fingerprint
    import importlib

    from quiver import __version__

    digest = hashlib.sha256()
    for name in _FINGERPRINT_MODULES:
        try:
            module = importlib.import_module(name)
            with open(module.__file__, "rb") as fh:
                digest.update(fh.read())
        except Exception:
            # No readable source (a zipapp, a frozen build): the package
            # version alone still separates releases.
            digest.update(f"nosrc:{name}".encode())
    _fingerprint = f"{FORMAT_VERSION}:{__version__}:{digest.hexdigest()[:16]}"
    return _fingerprint


def _is_valid(valid, value) -> bool:
    try:
        return bool(valid(value))
    except Exception:
        return False


def _signature(st: os.stat_result) -> list[int]:
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]


class _Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fingerprint = _source_fingerprint()
        self.entries: dict[str, dict[str, list]] = {}
        self.touched: dict[str, set[str]] = {}
        # Harnesses whose parser ran to completion in this block. Only their
        # namespaces are pruned, so an --agent filtered listing, or a parser
        # that crashed, leaves everyone else's entries alone.
        self.ran: set[str] = set()
        self.dirty = False

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        if not isinstance(data, dict) or data.get("version") != self.fingerprint:
            return
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return
        self.entries = {
            ns: files
            for ns, files in entries.items()
            if isinstance(ns, str) and isinstance(files, dict)
        }

    def lookup(self, namespace: str, path: str, st: os.stat_result, compute, valid):
        sig = _signature(st)
        self.touched.setdefault(namespace, set()).add(path)
        files = self.entries.setdefault(namespace, {})
        entry = files.get(path)
        if (
            isinstance(entry, list)
            and len(entry) == 2
            and entry[0] == sig
            and (valid is None or _is_valid(valid, entry[1]))
        ):
            return entry[1]
        value = compute()
        # Hand back exactly what a later hit will load, so a cold and a
        # warm run can never differ by type (a tuple read back as a list).
        try:
            stored = json.loads(json.dumps(value))
        except (TypeError, ValueError):
            files.pop(path, None)
            return value
        files[path] = [sig, stored]
        self.dirty = True
        return stored

    def prune(self) -> None:
        """Drop entries the harness's parser ran past without visiting.

        A namespace is ``<harness>:<what>``. A file its parser no longer
        lists (deleted, moved, no longer the newest copy) is not visited,
        and goes, even when nothing at all was visited.
        """
        for namespace, files in self.entries.items():
            if namespace.split(":", 1)[0] not in self.ran or not files:
                continue
            seen = self.touched.get(namespace, set())
            stale = [p for p in files if p not in seen]
            for p in stale:
                del files[p]
            if stale:
                self.dirty = True

    def save(self) -> None:
        self.prune()
        if not self.dirty:
            return
        payload = {"version": self.fingerprint, "entries": self.entries}
        try:
            # Titles are first prompts; keep the file owner-only.
            atomic_write_text(
                self.path,
                json.dumps(payload, separators=(",", ":")),
                private=True,
            )
        except Exception:
            # A cache that cannot be written only costs the next run time.
            pass


def _active() -> _Store | None:
    return getattr(_local, "store", None)


@contextmanager
def activate(home: Path | None = None):
    """Use and persist the parse cache for the parsers run inside.

    Re-entrant: only the outermost block loads and saves. ``home`` defaults
    to the current ``$HOME``, read when the block opens.
    """
    outer = _active()
    if outer is not None:
        yield outer
        return
    try:
        store = _Store(session_parse_cache_file_for(home))
    except Exception:
        # No resolvable home: list without a cache rather than fail.
        yield None
        return
    store.load()
    _local.store = store
    try:
        yield store
    finally:
        _local.store = None
        store.save()


def mark_ran(harness: str) -> None:
    """Record that ``harness``'s parser finished, so its stale entries prune."""
    store = _active()
    if store is not None:
        store.ran.add(harness)


def lookup(
    namespace: str,
    path: str,
    compute: Callable[[], Any],
    st: os.stat_result | None = None,
    valid: Callable[[Any], bool] | None = None,
) -> Any:
    """``compute()`` for ``path``, reused while the file is unchanged.

    ``namespace`` is ``<harness>:<what>``, the harness being the parser's
    registry name, which is what ``mark_ran`` prunes by.

    ``compute`` must depend only on the file's own bytes and return plain
    JSON values. Both a hit and a miss come back through a JSON round
    trip, so a tuple always returns as a list; a value JSON cannot hold is
    returned as is and not stored. ``st`` is the caller's ``os.stat(path)`` when
    it already has one; it has to be taken before the file is read.
    ``valid`` checks a stored value's shape; one it rejects (a hand-edited
    or damaged entry) is a miss, never an exception in the parser.
    Outside ``activate()``, or when the file cannot be stat'ed, this is
    just ``compute()``.
    """
    store = _active()
    if store is None:
        return compute()
    if st is None:
        try:
            st = os.stat(path)
        except OSError:
            return compute()
    return store.lookup(namespace, os.path.abspath(path), st, compute, valid)
