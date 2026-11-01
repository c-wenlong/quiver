"""The models each harness ran most recently, for the launch pickers.

The web ledger (``ledger/``) already records the model of every past
reply per harness, so "recently used" is a read of it with no new
persistence. The answer is asked once per catalog refresh (~10 s per
machine) while the ledger moves only when it scans, so it is cached:

- behind the ledger db+WAL file signature, so an idle machine pays two
  stats;
- incrementally past that: each change reads only the rows past the
  highest ``rowid`` already folded in (the rowid b-tree, no index
  needed), and a full re-read happens only when the store was replaced
  or shrank, or every ``_FULL_EVERY_S`` to pick up in-place relabels.

Names a launch could never take back are dropped: anything that fails
the launch ``model`` shape (``protocol._launch_value``, which also drops
Claude's ``<synthetic>`` and Cursor's "(Auto …)" labels), ``unknown``,
and per-harness internal names (``JUNK_MODELS``). OpenCode replies whose
row kept no provider are named ``provider/model`` from ``opencode.db``'s
``session_v2.model`` JSON (``{id, providerID}``), the form its launch
needs (a bare id can carry a slash of its own, so every OpenCode name
is looked up); a slash-less one with no provider anywhere is dropped.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# How many models each harness offers, newest first.
MAX_RECENT = 5

# Internal model names a harness logs for its own housekeeping, never a
# model a user picked or could launch on.
JUNK_MODELS: dict[str, frozenset[str]] = {
    "devin": frozenset({"compactor", "summarizer"}),
    "cursor": frozenset({"default"}),
}
_JUNK_EVERYWHERE = frozenset({"unknown"})

_FULL_EVERY_S = 600.0

_lock = threading.Lock()
# ledger path -> state dict (see ``_fold``).
_state: dict[str, dict] = {}
# opencode.db path -> (signature, {model id: provider id}).
_providers: dict[str, tuple[tuple, dict[str, str]]] = {}


def _signature(path: Path) -> tuple:
    """``(mtime_ns, size)`` of a SQLite db and its WAL, or None each."""
    sig = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            st = candidate.stat()
        except OSError:
            sig.append(None)
        else:
            sig.append((st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _inode(path: Path) -> int | None:
    try:
        return path.stat().st_ino
    except OSError:
        return None


def _open_ro(path: Path) -> sqlite3.Connection | None:
    # Read-only: this path must never create a store itself.
    try:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                               timeout=2)
    except (sqlite3.Error, ValueError):
        return None


def _fold(path: Path) -> dict[str, dict[str, float]]:
    """``harness -> {model: last ts}`` for the ledger at ``path``, read
    incrementally past what this process already folded in. Called
    under ``_lock``."""
    key = str(path)
    now = time.time()
    entry = _state.get(key)
    sig = _signature(path)
    # An unchanged ledger is served from memory, but only until the
    # periodic full re-read is due: an in-place relabel keeps its rowid,
    # so only that re-read ever sees it.
    if entry is not None and entry["sig"] == sig \
            and now - entry["full_at"] < _FULL_EVERY_S:
        return entry["seen"]
    db = _open_ro(path)
    if db is None:
        _state.pop(key, None)
        return {}
    try:
        top = db.execute("SELECT MAX(rowid) FROM events").fetchone()[0] or 0
        inode = _inode(path)
        full = (entry is None or entry["inode"] != inode
                or top < entry["rowid"]
                or now - entry["full_at"] >= _FULL_EVERY_S)
        seen: dict[str, dict[str, float]] = {} if full else entry["seen"]
        since = 0 if full else entry["rowid"]
        rows = db.execute(
            "SELECT harness, model, MAX(ts) FROM events"
            " WHERE rowid > ? AND model != '' GROUP BY harness, model",
            (since,),
        ).fetchall()
    except sqlite3.Error:
        _state.pop(key, None)
        return {}
    finally:
        db.close()
    for harness, model, ts in rows:
        if not harness or not model:
            continue
        per = seen.setdefault(harness, {})
        ts = float(ts or 0)
        if ts >= per.get(model, float("-inf")):
            per[model] = ts
    if len(_state) >= 64 and key not in _state:
        # Throwaway HOMEs in tests would grow the map forever otherwise.
        _state.clear()
    _state[key] = {
        # Taken before the reads, so a write during them re-reads next
        # call rather than being skipped until the next change.
        "sig": sig, "inode": inode, "rowid": top, "seen": seen,
        "full_at": now if full else entry["full_at"],
    }
    return seen


def _opencode_providers(db_path: Path) -> dict[str, str]:
    """``model id -> providerID`` from ``opencode.db``'s ``session_v2``
    (the newest session naming each id wins). Called under ``_lock``."""
    key = str(db_path)
    sig = _signature(db_path)
    hit = _providers.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    out: dict[str, str] = {}
    db = _open_ro(db_path) if sig[0] is not None else None
    if db is not None:
        try:
            rows = db.execute(
                "SELECT json_extract(model, '$.id'),"
                " json_extract(model, '$.providerID')"
                " FROM session_v2 WHERE model IS NOT NULL"
                " ORDER BY time_updated"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            db.close()
        for model_id, provider in rows:
            if isinstance(model_id, str) and model_id \
                    and isinstance(provider, str) and provider:
                out[model_id] = provider
    if len(_providers) >= 64 and key not in _providers:
        _providers.clear()
    _providers[key] = (sig, out)
    return out


def usable(harness: str, model: str) -> bool:
    """Whether ``model`` is a name ``harness`` could launch on: not
    junk (see the module docstring). The session rows' ``model``
    (``session_model``) drops the same names."""
    from swe.remote import protocol

    if model in _JUNK_EVERYWHERE or model in JUNK_MODELS.get(harness, ()):
        return False
    try:
        return protocol._launch_value({"model": model}, "model") == model
    except ValueError:
        return False


def recent_models(home: Path | str | None = None,
                  harnesses: list[str] | None = None,
                  ) -> dict[str, list[str]]:
    """``harness -> [model, ...]``, newest first, at most ``MAX_RECENT``
    each, junk dropped (see the module docstring). ``harnesses`` limits
    the answer to those names; a harness with nothing usable is left
    out. A machine with no ledger (or a locked one) has no history."""
    from swe.ledger.store import ledger_path_for

    root = Path(home) if home is not None else None
    path = ledger_path_for(root)
    wanted = set(harnesses) if harnesses is not None else None
    out: dict[str, list[str]] = {}
    with _lock:
        seen = _fold(path)
        providers: dict[str, str] | None = None
        for harness, models in seen.items():
            if wanted is not None and harness not in wanted:
                continue
            picks: list[str] = []
            for model, _ts in sorted(models.items(),
                                     key=lambda kv: kv[1], reverse=True):
                if harness == "opencode":
                    if providers is None:
                        base = root if root is not None else Path.home()
                        providers = _opencode_providers(
                            base / ".local/share/opencode/opencode.db")
                    # A slash doesn't prove a provider: the ledger keeps
                    # a bare id (``nvidia/nemotron:free``) when the row
                    # had none. An id opencode.db knows is bare, so it
                    # takes its provider; a name with no slash and no
                    # known provider can't launch.
                    provider = providers.get(model)
                    if provider:
                        model = f"{provider}/{model}"
                    elif "/" not in model:
                        continue
                if model in picks or not usable(harness, model):
                    continue
                picks.append(model)
                if len(picks) >= MAX_RECENT:
                    break
            if picks:
                out[harness] = picks
    return out
