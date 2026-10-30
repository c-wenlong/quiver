"""The Calendar's file activity sources: harnesses the ledger does not
draw from, read from their own session files (ports
``FileActivitySources.swift`` and ``ActivityFileIndex.swift``).

Each source lists its files with a stamp, re-parses only new or changed
ones, and hands back one entry per session: ``{"session", "account",
"cwd", "timestamps"}`` with the response times as epoch seconds, sorted.
``calendar.file_pieces`` turns them into blocks with the same rule as the
ledger's. The index lives in memory for the hub's lifetime; the Mac's
on-disk copy under ``~/.quiver/cache/calendar`` is the app's own and
stays unread.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

from quiver.ledger.models import parse_timestamp
from quiver.ledger.parsers import iter_lines

# Sessions the ledger also logs, so the Calendar drops the accidental
# ones the way the block query judges the rest
# (``Ledger.filterTrivialActivitySessions``).
TRIVIAL_FILTERED = ("droid", "pi")


class FileIndex:
    """``ActivityFileIndex``: path to its stamp and the sessions parsed
    out of it. Files whose stamp is unchanged keep their sessions; a
    file that fails to parse stays out, so the next read tries again."""

    def __init__(self):
        self._entries: dict[str, tuple[tuple, list[dict]]] = {}
        self._lock = threading.Lock()

    def sessions(self, files: dict[str, tuple], parse) -> list[dict]:
        with self._lock:
            for path in [p for p in self._entries if p not in files]:
                del self._entries[path]
            for path, stamp in files.items():
                entry = self._entries.get(path)
                if entry is not None and entry[0] == stamp:
                    continue
                # One bad file must not sink the rest of the source.
                try:
                    self._entries[path] = (stamp, parse(path))
                except Exception:  # noqa: BLE001 - any parse failure drops the file
                    self._entries.pop(path, None)
            return [s for path in sorted(files) for s in self._entries.get(path, ((), []))[1]]


_INDEXES: dict[tuple[str, str], FileIndex] = {}
_INDEXES_LOCK = threading.Lock()


def _index(harness: str, root: str) -> FileIndex:
    with _INDEXES_LOCK:
        return _INDEXES.setdefault((harness, root), FileIndex())


def _hidden(entry: os.DirEntry) -> bool:
    """``.skipsHiddenFiles``: a dot name, or the BSD hidden flag macOS
    also counts as hidden."""
    if entry.name.startswith("."):
        return True
    try:
        flags = getattr(entry.stat(follow_symlinks=False), "st_flags", 0)
    except OSError:
        return False
    return bool(flags & getattr(stat, "UF_HIDDEN", 0))


def walk(root: str):
    """Every non-directory under ``root``, recursively, as
    ``FileManager``'s enumerator lists it with ``.skipsHiddenFiles``:
    hidden names are neither listed nor descended into, symlinked folders
    are not followed, and unreadable folders are skipped."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for entry in entries:
        if _hidden(entry):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            yield from walk(entry.path)
        else:
            yield entry.path


def stamp(path: str) -> tuple | None:
    """``ActivityFileIndex.stamp``: mtime and size, None when unstattable."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def jsonl_files(root: str) -> dict[str, tuple]:
    """``ActivityParsers.jsonlFiles``: every ``*.jsonl`` under the root
    with its stamp; files that fail to stat are skipped."""
    files = {}
    for path in walk(root):
        if os.path.splitext(os.path.basename(path))[1] != ".jsonl":
            continue
        found = stamp(path)
        if found is not None:
            files[path] = found
    return files


def _no_constants(name: str):
    # JSONSerialization has no NaN or Infinity; a line holding one is broken.
    raise ValueError(name)


def json_object(raw) -> dict | None:
    """One JSON object out of ``raw`` (a line or a file's bytes), None
    when it is broken or not an object."""
    try:
        obj = json.loads(raw, parse_constant=_no_constants)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def jsonl(path: str):
    """``ActivityParsers.jsonl``: every object line of a JSONL file through
    the ledger's line reader (newline-terminated lines only, 64 MiB cap);
    broken or non-object lines are skipped. Raises ``OSError`` when the
    file can't be opened, which drops the file from the index."""
    for line in iter_lines(Path(path)):
        obj = json_object(line)
        if obj is not None:
            yield obj


def _session_log(path: str, account: str, session: str, cwd_type: str) -> list[dict]:
    """Droid and Pi share a shape: the first ``cwd_type`` line names the
    folder and every ``message`` line's ISO ``timestamp`` is a response."""
    cwd = None
    timestamps: list[float] = []
    for line in jsonl(path):
        kind = line.get("type")
        if kind == cwd_type:
            if cwd is None and isinstance(line.get("cwd"), str):
                cwd = line["cwd"]
        elif kind == "message":
            ts = parse_timestamp(line.get("timestamp"))
            if ts is not None:
                timestamps.append(ts)
    if not timestamps:
        return []
    return [{"session": session, "account": account, "cwd": cwd,
             "timestamps": sorted(timestamps)}]


def parse_droid(path: str, account: str) -> list[dict]:
    """``DroidActivity.parse``: ``<sessions>/<project>/<uuid>.jsonl``, the
    file stem is swe's session id and ``session_start`` carries the cwd."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return _session_log(path, account, stem, "session_start")


def parse_pi(path: str, account: str) -> list[dict]:
    """``PiActivity.parse``: ``<sessions>/<project>/<ts>_<uuid>.jsonl``,
    swe's session id is the full file path and a ``session`` line carries
    the cwd."""
    return _session_log(path, account, path, "session")


def freebuff_files(root: str) -> dict[str, tuple]:
    """``FreebuffActivity.files``: every ``<project>/chats/<chat>/run-state.json``
    under the root, at any depth, with its stamp."""
    files = {}
    for path in walk(root):
        p = Path(path)
        if p.name != "run-state.json" or p.parent.parent.name != "chats":
            continue
        found = stamp(path)
        if found is not None:
            files[path] = found
    return files


def parse_freebuff(path: str, account: str) -> list[dict]:
    """``FreebuffActivity.parse``: ``sessionState.mainAgentState.messageHistory``
    carries ``sentAt`` epoch milliseconds (any JSON number, a bool too, as
    ``as? NSNumber`` takes it), the chat folder's name is swe's session id,
    and the cwd is the agent's ``cwd`` or ``initialCwd``, else the
    state's, else the file's ``initialCwd``. An unreadable or malformed
    file, or a history holding anything but objects, is no session."""
    try:
        root = json_object(Path(path).read_bytes())
    except OSError:
        return []
    state = root.get("sessionState") if root is not None else None
    agent = state.get("mainAgentState") if isinstance(state, dict) else None
    history = agent.get("messageHistory") if isinstance(agent, dict) else None
    if not isinstance(history, list) or not all(isinstance(m, dict) for m in history):
        return []
    timestamps = sorted(m["sentAt"] / 1000 for m in history
                        if isinstance(m.get("sentAt"), (int, float)))
    if not timestamps:
        return []
    cwd = next((c for c in (agent.get("cwd"), agent.get("initialCwd"),
                            state.get("initialCwd"), root.get("initialCwd"))
                if isinstance(c, str)), None)
    return [{"session": Path(path).parent.name, "account": account, "cwd": cwd,
             "timestamps": timestamps}]


class Source:
    """One file source: its harness, the account home it reports, where
    its files live under that home, and how to list and parse them."""

    def __init__(self, harness: str, account: str, root: str, files, parse):
        self.harness, self.account, self.root = harness, account, root
        self._files, self._parse = files, parse

    def sessions(self, home: Path) -> list[dict]:
        account = str(home / self.account)
        root = str(home / self.root)
        return _index(self.harness, root).sessions(
            self._files(root), lambda path: self._parse(path, account))


# AppModel's file providers, with the same homes and roots.
SOURCES = (
    Source("droid", ".factory", ".factory/sessions", jsonl_files, parse_droid),
    Source("pi", ".pi", ".pi/agent/sessions", jsonl_files, parse_pi),
    Source("freebuff", ".config/manicode", ".config/manicode/projects",
           freebuff_files, parse_freebuff),
)


def read(home: Path | None, stats: dict[str, dict], trivial) -> list[tuple[str, dict]]:
    """``(harness, session)`` for every file source's sessions, the
    accidental Pi and Droid ones dropped. ``stats`` maps a filtered
    harness to ``LedgerStore.activity_session_stats``, keyed by account
    and session; ``trivial`` is ``sessions.is_trivial``. A session the
    ledger has no rows for stays: absent rows prove nothing about a
    file's responses."""
    user = Path(home) if home is not None else Path.home()
    out = []
    for source in SOURCES:
        usage = stats.get(source.harness)
        for session in source.sessions(user):
            totals = (usage or {}).get((session["account"], session["session"]))
            if totals is not None and trivial(source.harness, totals, True):
                continue
            out.append((source.harness, session))
    return out
