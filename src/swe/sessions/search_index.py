"""Read side of the app's FTS5 session-search index.

The Quiver app writes ``~/.quiver/data/search/search.sqlite`` (WAL) as it
scans transcripts, and :mod:`swe.sessions.search_writer` writes the same
file where the app doesn't run; this module queries it read-only and never
creates or writes the file. The schema, shared with the Swift writer:

::

    meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)
        -- schema_version = 1, index_incarnation = uuid
    sessions(id PK, agent, session_id, file_path, title DEFAULT '',
             cwd, cwd_key, created_at, updated_at, message_count DEFAULT 0)
    files(path PK, dev, ino, byte_offset, mtime_ms, size_bytes,
          session_row_id, state DEFAULT 'current', fail_count DEFAULT 0,
          failed_mtime_ms)
    messages(id PK, session_row_id, role, ts)
    messages_fts USING fts5(user_text, assistant_text, tool_text,
          identifiers, tokenize="unicode61 tokenchars '_.-/+''",
          detail=full)

``messages_fts.rowid`` equals ``messages.id``. The writer may leave
orphaned ``messages`` rows whose ``session_row_id`` has no ``sessions``
row (a rewritten file's old rows wait for a background drain), so every
query joins ``sessions`` to keep them invisible.

Each call opens its own short read-only connection: a read snapshot held
open across calls pins the WAL (Orca watched one grow 10 MB -> 266 MB).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from swe.paths import expand_user, search_index_for
from swe.sessions.engines.common import (
    clean_title,
    get_mtime,
    open_sqlite_ro,
    parse_iso_ts,
)
from swe.sessions.engines.jsonl_engine import event_cwd, first_user_title
from swe.sessions.models import Session
from swe.sessions.parsers import codex_thread_id

# The only meta.schema_version this reader understands. An index at any
# other version is ignored rather than misread.
SCHEMA_VERSION = 1

DEFAULT_LIMIT = 50
SNIPPET_MAX = 512
# Snippet() is called per returned row; a huge page means hundreds of tiny
# queries on the same connection. 512 bounds the work and the payload.

# unicode61 with tokenchars '_.-/+' keeps runs of word characters plus
# ``_ . - / +`` as one token. ``\w`` already covers ``_``.
_TERM_RE = re.compile(r"[\w./+-]+")

# One row per session, best matching message kept. ``max(score)`` is the
# query's only aggregate, so SQLite's bare-column rule makes rowid, role
# and ts come from the highest-scoring row of the group, not an arbitrary
# one. ``bm25`` and ``snippet`` take the table name, never an alias.
_MATCH_SQL = """\
WITH matched AS MATERIALIZED (
    SELECT messages_fts.rowid AS rowid,
           -bm25(messages_fts, 3.0, 2.0, 1.0, 1.0) AS score,
           m.session_row_id AS session_row_id,
           m.role AS role,
           m.ts AS ts,
           s.session_id AS session_id,
           s.agent AS agent,
           s.title AS title,
           s.cwd AS cwd,
           s.file_path AS file_path,
           s.updated_at AS updated_at
    FROM messages_fts
    JOIN messages m ON m.id = messages_fts.rowid
    JOIN sessions s ON s.id = m.session_row_id
    WHERE messages_fts MATCH ?{agent_clause}
)
SELECT rowid, max(score) AS score, session_row_id, role, ts,
       session_id, agent, title, cwd, file_path, updated_at
FROM matched
GROUP BY session_row_id
ORDER BY score DESC
LIMIT ?
"""

# ``rowid IN (SELECT ?)`` is deliberate: next to MATCH the FTS5 planner
# silently ignored a bound ``rowid = ?`` and snippet() ran against the
# wrong row (an Orca gotcha we keep). The second snippet wraps matched
# terms in private-use marks so a caller that wants to highlight can diff
# it against the plain one.
_SNIPPET_SQL = """\
SELECT snippet(messages_fts, -1, '', '', '…', 12),
       snippet(messages_fts, -1, char(0xE000), char(0xE001), '…', 12)
FROM messages_fts
WHERE messages_fts MATCH ? AND messages_fts.rowid IN (SELECT ?)
"""

# Scan budget for resolving a hit's transcript file: enough lines to find
# the recorded cwd and the first real user prompt, never a full pass.
_HEAD_LINES = 200


@dataclass(frozen=True)
class SearchHit:
    """One session's best matching message, plus the snippet to show."""

    session_id: str
    agent: str
    title: str
    cwd: str
    updated_at: str
    score: float
    role: str
    ts: str
    snippet: str
    file_path: str = ""
    marked_snippet: str = ""
    # Set by ``search_passes``, which leaves the snippet for
    # ``fill_snippets`` to fetch once the caller knows which hits it keeps.
    rowid: int = 0
    fts: str = ""


def index_path(home: Path | str | None = None) -> Path:
    """``~/.quiver/data/search/search.sqlite`` for ``home`` (real home by default)."""
    return search_index_for(None if home is None else Path(home))


def available(home: Path | str | None = None) -> bool:
    """Whether a usable index exists at ``index_path(home)``.

    The file must open read-only, pin ``meta.schema_version`` at this
    module's :data:`SCHEMA_VERSION` and answer a trivial FTS query. Any
    failure — missing file, unreadable, wrong version, no ``messages_fts``
    — is False; nothing is created or written here.
    """
    conn = open_sqlite_ro(str(index_path(home)))
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if not row or str(row[0]).strip() != str(SCHEMA_VERSION):
            return False
        conn.execute("SELECT rowid FROM messages_fts LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _terms(query: str) -> list[str]:
    """Search text split the way the tokenizer does, empties dropped."""
    return _TERM_RE.findall(query or "")


def _ladder(terms: list[str]) -> list[str]:
    """FTS match expressions, strictest first.

    Every term is double-quoted: unquoted, ``foo-bar`` reads as
    ``foo`` minus ``bar`` to FTS5 — silently wrong or a syntax error.
    The first variant is one quoted string holding every term — that is
    the phrase query. ``"t1" "t2"`` would only be an implicit AND.
    """
    escaped = [term.replace('"', '""') for term in terms]
    variants = ['"' + " ".join(escaped) + '"']
    if len(escaped) > 1:
        quoted = ['"' + term + '"' for term in escaped]
        variants.append(" AND ".join(quoted))
        variants.append(" OR ".join(quoted))
    return variants


def _run_match(
    conn: sqlite3.Connection, fts: str, limit: int, agents: list[str] | None
) -> list[tuple]:
    clause = ""
    params: list = [fts]
    if agents:
        names = [a for a in agents if a]
        if names:
            clause = " AND s.agent IN (" + ",".join("?" for _ in names) + ")"
            params.extend(names)
    params.append(limit)
    return conn.execute(
        _MATCH_SQL.format(agent_clause=clause), params
    ).fetchall()


def _snippet(conn: sqlite3.Connection, fts: str, rowid: int) -> tuple[str, str]:
    row = conn.execute(_SNIPPET_SQL, (fts, rowid)).fetchone()
    plain, marked = row if row else ("", "")
    return (plain or "")[:SNIPPET_MAX], (marked or "")[:SNIPPET_MAX]


def _hits(conn: sqlite3.Connection, fts: str, rows: list[tuple], snippets: bool) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for row in rows:
        (rowid, score, _row, role, ts, session_id,
         agent, title, cwd, file_path, updated_at) = row
        if session_id in seen:
            continue  # duplicate sessions rows for one id
        seen.add(session_id)
        plain, marked = _snippet(conn, fts, rowid) if snippets else ("", "")
        hits.append(SearchHit(
            session_id=session_id or "",
            agent=agent or "",
            title=title or "",
            cwd=cwd or "",
            updated_at=updated_at or "",
            score=score or 0.0,
            role=role or "",
            ts=ts or "",
            snippet=plain,
            file_path=file_path or "",
            marked_snippet=marked,
            rowid=rowid or 0,
            fts=fts,
        ))
    return hits


def _limit(limit) -> int:
    try:
        return max(1, int(limit))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def search(
    home: Path | str | None,
    query: str,
    limit: int = DEFAULT_LIMIT,
    agents: "list[str] | tuple[str, ...] | None" = None,
) -> list[SearchHit]:
    """BM25-ranked sessions whose transcript text matches ``query``.

    The ladder tries an exact phrase first, then all terms ANDed, then
    ORed, and stops at the first run that yields hits — so ``"ledger
    cursor"`` prefers sessions where the words sit together but still
    answers when they only appear separately. ``agents`` filters
    ``sessions.agent`` (the harness id, ``Session.tool_name``).
    """
    conn = open_sqlite_ro(str(index_path(home)))
    if conn is None:
        return []
    try:
        terms = _terms(query)
        if not terms:
            return []
        limit = _limit(limit)
        agent_names = list(agents) if agents else None
        for fts in _ladder(terms):
            try:
                rows = _run_match(conn, fts, limit, agent_names)
            except sqlite3.Error:
                return []
            if rows:
                return _hits(conn, fts, rows, snippets=True)
        return []
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def search_passes(
    home: Path | str | None,
    query: str,
    limit: int = DEFAULT_LIMIT,
) -> list[list[SearchHit]]:
    """Every rung of the ladder's hits, strictest first, without snippets.

    ``search`` stops at the first rung with hits, so one old session that
    holds the exact phrase hides every session that has the words apart.
    A caller ranking across rungs gets them all here (a one-word query has
    one rung), then asks ``fill_snippets`` for just the hits it keeps.
    """
    conn = open_sqlite_ro(str(index_path(home)))
    if conn is None:
        return []
    try:
        terms = _terms(query)
        if not terms:
            return []
        limit = _limit(limit)
        return [_hits(conn, fts, _run_match(conn, fts, limit, None), snippets=False)
                for fts in _ladder(terms)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def fill_snippets(home: Path | str | None, hits: list[SearchHit]) -> list[SearchHit]:
    """``hits`` from ``search_passes``, each with its snippet fetched."""
    if not hits:
        return []
    conn = open_sqlite_ro(str(index_path(home)))
    if conn is None:
        return list(hits)
    try:
        out = []
        for hit in hits:
            if hit.fts and not hit.snippet:
                plain, marked = _snippet(conn, hit.fts, hit.rowid)
                hit = replace(hit, snippet=plain, marked_snippet=marked)
            out.append(hit)
        return out
    except sqlite3.Error:
        return list(hits)
    finally:
        conn.close()


def match_keys(session_id: str) -> frozenset[str]:
    """The id forms one session can be known by, for hit-to-session joining.

    ``sessions.session_id`` in the index is whatever the writer recorded;
    parsers key on the transcript path. A claude id is already a uuid; a
    codex id is the rollout stem while thread stores use the uuid inside
    it; a pi id is the whole file path, so the stem counts too.
    """
    sid = session_id or ""
    keys = {sid, Path(sid).stem}
    keys.discard("")
    try:
        tid = codex_thread_id(sid)
    except Exception:
        tid = ""
    if tid:
        keys.add(tid)
    return frozenset(keys)


def tool_names_for(agent_filter: str | None) -> list[str] | None:
    """Registry tool names matching an ``--agent`` filter, or None for all.

    ``sessions.agent`` holds the harness id (``Session.tool_name``), so an
    alias like ``--agent=cc`` has to widen to ``claude`` before SQL can
    filter on it. An unknown name passes through verbatim rather than
    silently dropping every hit.
    """
    if not agent_filter:
        return None
    needle = agent_filter.casefold()
    try:
        from swe.sessions.aggregator import PARSER_REGISTRY

        names = [
            name
            for name, _parser, keys in PARSER_REGISTRY
            if needle in {key.casefold() for key in keys}
        ]
    except ImportError:
        names = []
    return names or [agent_filter]


def merge_hits(
    home: Path | str | None,
    query: str,
    sessions: list[Session],
    *,
    limit: int = DEFAULT_LIMIT,
    agent_filter: str | None = None,
) -> tuple[set[str] | None, dict[str, SearchHit]]:
    """Union the index's hits for ``query`` into a parsed listing.

    ``sessions`` gains a ``Session`` for each hit the parsers did not list
    (``resolve_hit_sessions``), in place. Returns ``(search_ids, best)``:
    every session id the index matched, for ``SessionQuery.search_ids``
    (None when there is no index or no hit), and each matched id's hit,
    the one a parsed session joined to first. ``swe session --search``
    and the MCP session tool both search this way.
    """
    if not available(home):
        return None, {}
    hits = search(home, query, limit=max(limit, DEFAULT_LIMIT),
                  agents=tool_names_for(agent_filter))
    if not hits:
        return None, {}
    search_ids = {h.session_id for h in hits if h.session_id}
    hit_by_key = {key: h for h in hits for key in match_keys(h.session_id)}
    best: dict[str, SearchHit] = {}
    for session in sessions:
        if not session.session_id:
            continue
        hit = next((hit_by_key[k] for k in match_keys(session.session_id)
                    if k in hit_by_key), None)
        if hit is not None:
            search_ids.add(session.session_id)
            best[session.session_id] = hit
    sessions.extend(resolve_hit_sessions(hits, sessions))
    for h in hits:
        if h.session_id:
            best.setdefault(h.session_id, h)
    return search_ids, best


def resolve_hit_sessions(hits: list[SearchHit], parsed: list[Session]) -> list[Session]:
    """``Session`` objects for the hits the parsers did not already list.

    A hit already in ``parsed`` needs no new object — the union adds it by
    id. For the rest (a subagent transcript, a file a parser filter skips,
    a stale listing), the transcript the index recorded is re-parsed
    lightly for cwd and title, with the index row's own fields as the
    fallback. The display agent name is borrowed from another session of
    the same tool when one is listed, else the harness id stands.
    """
    parsed_keys = {key for sess in parsed for key in match_keys(sess.session_id)}
    labels: dict[str, str] = {}
    for sess in parsed:
        if sess.tool_name and sess.agent:
            labels.setdefault(sess.tool_name, sess.agent)
    out: list[Session] = []
    for hit in hits:
        if match_keys(hit.session_id) & parsed_keys:
            continue
        out.append(_session_from_hit(hit, labels.get(hit.agent, hit.agent)))
    return out


def _session_from_hit(hit: SearchHit, agent_label: str) -> Session:
    path = hit.cwd or ""
    title = hit.title or ""
    ts = parse_iso_ts(hit.updated_at)
    fp = expand_user(hit.file_path) if hit.file_path else Path("")
    # A per-session transcript's mtime *is* the session's last activity,
    # matching what the engines put in Session.timestamp. For a shared
    # store (one .db or .json holding every session) the file's mtime says
    # nothing about this session, so only .jsonl paths feed it.
    if fp.suffix == ".jsonl" and fp.is_file():
        mtime = get_mtime(str(fp))
        if mtime:
            ts = mtime
        head_cwd, head_title = _scan_jsonl_head(str(fp))
        path = head_cwd or path
        title = head_title or title
    return Session(
        timestamp=ts,
        agent=agent_label,
        path=path,
        title=title,
        session_id=hit.session_id,
        tool_name=hit.agent,
    )


def _scan_jsonl_head(path: str, max_lines: int = _HEAD_LINES) -> tuple[str, str]:
    """``(cwd, first user prompt title)`` from the head of a JSONL transcript."""
    cwd = ""
    title = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines or (cwd and title):
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if not cwd:
                    cwd = event_cwd(data)
                if not title:
                    title = clean_title(first_user_title(data), 80)
    except OSError:
        pass
    return cwd, title
