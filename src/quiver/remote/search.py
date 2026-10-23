"""Session search for ``GET /v1/search``.

Two sources, merged into one ranked list of catalog rows:

1. Transcript text, through the Quiver app's FTS5 index
   (``quiver.sessions.search_index``), when that index exists on this
   machine. Hits are BM25-ranked and carry a snippet of the best match.
2. Session metadata (title, harness, folder, id), always. The query is a
   case-insensitive regular expression; one that doesn't compile is
   matched as plain text instead.

Index hits come first in rank order, then metadata-only matches, newest
first. Only sessions the catalog lists are returned, so every result can
be opened with the usual ``/v1/sessions/{key}`` routes. The index covers
this machine only; satellite sessions match on metadata.
"""

from __future__ import annotations

import re
from pathlib import Path

from quiver.sessions import search_index

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# A pathological pattern on a huge title list is the only cost here; the
# metadata haystack per row is short, so a length cap is enough.
MAX_QUERY_LEN = 200

_META_FIELDS = ("title", "agent", "harness", "cwd", "id", "key")

# A quantified group that itself holds a quantifier (``(a+)+``, ``(x*)*``,
# ``(\w+){2,}``) can backtrack exponentially, and Python's ``re`` has no
# timeout. Such a pattern is matched as literal text instead.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*}][^()]*\)\s*[+*{]")


def compile_query(query: str) -> re.Pattern[str]:
    """``query`` as a case-insensitive regex, or as literal text when it
    isn't a valid pattern (``foo(``, a lone ``*``) or could backtrack
    without bound."""
    if not _NESTED_QUANTIFIER.search(query):
        try:
            return re.compile(query, re.IGNORECASE)
        except re.error:
            pass
    return re.compile(re.escape(query), re.IGNORECASE)


def metadata_text(row: dict) -> str:
    return " ".join(str(row.get(field) or "") for field in _META_FIELDS)


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
        hits = search_index.search(home, query, limit=max(limit, search_index.DEFAULT_LIMIT))
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

    pattern = compile_query(query)
    meta = [
        row for row in rows
        if row.get("key") and row["key"] not in taken and pattern.search(metadata_text(row))
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
