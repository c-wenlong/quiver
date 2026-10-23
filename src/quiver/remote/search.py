"""Session search for ``GET /v1/search``.

Two sources, merged into one ranked list of catalog rows:

1. Transcript text, through the Quiver app's FTS5 index
   (``quiver.sessions.search_index``), when that index exists on this
   machine. Hits are BM25-ranked and carry a snippet of the best match.
2. Session metadata (title, harness, folder, id), always, as a
   case-insensitive substring: the match the Mac app's Sessions page and
   ``swe session --search`` use. Plain text on purpose: a user-typed
   regex can backtrack exponentially and Python's ``re`` has no timeout.

Index hits come first in rank order, then metadata-only matches, newest
first. Only sessions the catalog lists are returned, so every result can
be opened with the usual ``/v1/sessions/{key}`` routes. The index covers
this machine only; satellite sessions match on metadata.
"""

from __future__ import annotations

from pathlib import Path

from quiver.sessions import search_index

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_QUERY_LEN = 200
# The index ranks across every transcript it holds, including sessions the
# catalog doesn't list (subagents, files past the catalog's scan). Asking
# for this many hits leaves room to drop those and still fill ``limit``.
INDEX_FETCH = 1000

_META_FIELDS = ("title", "agent", "harness", "cwd", "id", "key")

def metadata_text(row: dict) -> str:
    return " ".join(str(row.get(field) or "") for field in _META_FIELDS).casefold()


def search(
    rows: list[dict],
    query: str,
    home: Path | None = None,
    limit: int = DEFAULT_LIMIT,
    use_index: bool = True,
) -> dict:
    """``{"results": [...], "indexed": bool}`` for ``query`` over ``rows``.

    Each result is ``{"key", "source", "snippet", "marked_snippet",
    "role"}`` plus the catalog row itself under ``"session"``; ``source``
    is ``"index"`` for a transcript hit and ``"metadata"`` otherwise.
    """
    query = (query or "").strip()[:MAX_QUERY_LEN]
    limit = max(1, min(int(limit), MAX_LIMIT))
    if not query:
        return {"results": [], "indexed": False}

    results: list[dict] = []
    taken: set[str] = set()

    indexed = bool(use_index) and search_index.available(home)
    if indexed:
        by_id: dict[str, dict] = {}
        for row in rows:
            if row.get("remote"):
                continue  # the index holds this machine's transcripts only
            for k in search_index.match_keys(str(row.get("id") or "")):
                by_id.setdefault(k, row)
        # Snippets cost a query per hit, so fetch a little first and go
        # deeper only while hits outside the catalog leave ``limit`` unfilled.
        fetch = min(limit * 2, INDEX_FETCH)
        while True:
            hits = search_index.search(home, query, limit=fetch)
            results, taken = [], set()
            for hit in hits:
                row = next(
                    (by_id[k] for k in search_index.match_keys(hit.session_id) if k in by_id),
                    None,
                )
                if row is None or row["key"] in taken:
                    continue
                taken.add(row["key"])
                results.append({
                    "key": row["key"],
                    "source": "index",
                    "snippet": hit.snippet,
                    "marked_snippet": hit.marked_snippet,
                    "role": hit.role,
                    "session": row,
                })
                if len(results) >= limit:
                    return {"results": results, "indexed": True}
            if len(hits) < fetch or fetch >= INDEX_FETCH:
                break
            fetch = min(fetch * 4, INDEX_FETCH)

    needle = query.casefold()
    meta = [
        row for row in rows
        if row.get("key") and row["key"] not in taken and needle in metadata_text(row)
    ]
    meta.sort(key=lambda r: float(r.get("last_active_at") or 0), reverse=True)
    for row in meta[: limit - len(results)]:
        results.append({
            "key": row["key"],
            "source": "metadata",
            "snippet": None,
            "marked_snippet": None,
            "role": None,
            "session": row,
        })
    return {"results": results, "indexed": indexed}
