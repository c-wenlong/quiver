"""Session search for ``GET /v1/search``.

Every catalog row that matches goes into the first tier it qualifies for:

1. Title: holds the whole query, or every word of it.
2. Session details (title, harness, folder, id together): the same test.
   Plain case-insensitive text, as the Mac app's Sessions page and
   ``swe session --search`` match. Never a regex: a user-typed pattern can
   backtrack exponentially and Python's ``re`` has no timeout.
3. to 5. Transcript text, through the SWE app's FTS5 index
   (``swe.sessions.search_index``) when it exists on this machine: the
   exact phrase, then all the words, then any word. A one-word query has
   only the first of these.

Within a tier, a result's relevance is multiplied by how recent its
session is (``recency_weight``), so of two equal matches the newer one
leads, and a strong old match still beats a weak new one. Transcript
relevance is the BM25 score as a share of the best in that tier; a
details match counts 1 for the whole query and 0.7 for the words apart.
A result in any tier carries the snippet of its best transcript match.

Only sessions the catalog lists are returned, so every result opens
through the usual ``/v1/sessions/{key}`` routes. The index covers this
machine only; satellite sessions match on their details.
"""

from __future__ import annotations

import time
from pathlib import Path

from swe.sessions import search_index

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_QUERY_LEN = 200
# Hits per transcript pass. The index ranks across every transcript it
# holds, including sessions the catalog doesn't list (subagents, files past
# the catalog's scan), and recency can lift a hit BM25 put far down, so
# take plenty. Cheap: the match query groups every matching message before
# LIMIT applies, and snippets are fetched only for the results returned.
INDEX_FETCH = 1000

# A session last active this many days ago counts half as much as one
# active now, on the part of the weight that decays.
HALF_LIFE_DAYS = 14.0
# The part that never decays, so an old session that matches well isn't
# buried under every recent passing mention.
RECENCY_FLOOR = 0.3

# A details match with the words apart, against 1.0 for the whole query.
_WORDS_APART = 0.7

TIER_TITLE = 0
TIER_DETAILS = 1
TIER_TRANSCRIPT = 2  # + the pass: phrase, all words, any word

_META_FIELDS = ("title", "agent", "harness", "cwd", "id", "key")


def metadata_text(row: dict) -> str:
    return " ".join(str(row.get(field) or "") for field in _META_FIELDS).casefold()


def recency_weight(last_active_at, now: float) -> float:
    """1.0 for a session active now, falling to ``RECENCY_FLOOR``."""
    try:
        age = max(0.0, now - float(last_active_at or 0))
    except (TypeError, ValueError):
        age = float("inf")
    decay = 0.5 ** (age / 86400 / HALF_LIFE_DAYS)
    return RECENCY_FLOOR + (1 - RECENCY_FLOOR) * decay


def _text_match(text: str, needle: str, words: list[str]) -> float:
    """1.0 for the whole query, ``_WORDS_APART`` for every word apart, else 0."""
    if needle in text:
        return 1.0
    if len(words) > 1 and all(w in text for w in words):
        return _WORDS_APART
    return 0.0


def search(
    rows: list[dict],
    query: str,
    home: Path | None = None,
    limit: int = DEFAULT_LIMIT,
    use_index: bool = True,
    now: float | None = None,
) -> dict:
    """``{"results": [...], "indexed": bool}`` for ``query`` over ``rows``.

    Each result is ``{"key", "source", "snippet", "marked_snippet",
    "role"}`` plus the catalog row itself under ``"session"``; ``source``
    is ``"index"`` when the transcript is what matched and ``"metadata"``
    when the title or details did.
    """
    query = (query or "").strip()[:MAX_QUERY_LEN]
    limit = max(1, min(int(limit), MAX_LIMIT))
    if not query:
        return {"results": [], "indexed": False}
    now = time.time() if now is None else now
    needle = query.casefold()
    words = needle.split()

    # key -> (tier, relevance)
    best: dict[str, tuple[int, float]] = {}
    by_key: dict[str, dict] = {}
    for row in rows:
        key = row.get("key")
        if not key:
            continue
        by_key[key] = row
        title = _text_match(str(row.get("title") or "").casefold(), needle, words)
        if title:
            best[key] = (TIER_TITLE, title)
            continue
        details = _text_match(metadata_text(row), needle, words)
        if details:
            best[key] = (TIER_DETAILS, details)

    # key -> the session's hit from the strictest pass that has it
    hit_for: dict[str, search_index.SearchHit] = {}
    indexed = bool(use_index) and search_index.available(home)
    if indexed:
        by_id: dict[str, str] = {}
        for row in rows:
            if row.get("remote") or not row.get("key"):
                continue  # the index holds this machine's transcripts only
            for k in search_index.match_keys(str(row.get("id") or "")):
                by_id.setdefault(k, row["key"])
        for rung, hits in enumerate(search_index.search_passes(home, query, limit=INDEX_FETCH)):
            top = max((h.score for h in hits), default=0.0)
            for hit in hits:
                key = next((by_id[k] for k in search_index.match_keys(hit.session_id) if k in by_id), None)
                if key is None or key in hit_for:
                    continue
                hit_for[key] = hit
                if key not in best:
                    best[key] = (TIER_TRANSCRIPT + rung, hit.score / top if top > 0 else 1.0)

    ranked = sorted(
        best,
        key=lambda k: (best[k][0], -best[k][1] * recency_weight(by_key[k].get("last_active_at"), now)),
    )[:limit]
    snippets = {}
    kept = [hit_for[k] for k in ranked if k in hit_for]
    for hit in search_index.fill_snippets(home, kept) if kept else []:
        snippets[hit.session_id] = hit

    results = []
    for key in ranked:
        hit = hit_for.get(key)
        hit = snippets.get(hit.session_id, hit) if hit else None
        results.append({
            "key": key,
            "source": "index" if best[key][0] >= TIER_TRANSCRIPT else "metadata",
            "snippet": hit.snippet if hit else None,
            "marked_snippet": hit.marked_snippet if hit else None,
            "role": hit.role if hit else None,
            "session": by_key[key],
        })
    return {"results": results, "indexed": indexed}
