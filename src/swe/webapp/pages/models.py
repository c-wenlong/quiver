"""The Models page (``GET /v1/app/models``): the models the ledger saw in
the period, with the lab behind each, the harnesses that ran it, its list
price and the tokens. A port of the usage side of ``ModelsModel.swift``.

The Mac page is built on Artificial Analysis benchmarks (intelligence,
coding, reasoning, speed, time to first token, blended cost), fetched
with the user's API key into a store only the app reads. None of that is
here: without benchmarks every logged name is its own row (the app
merges names that match one Artificial Analysis model), and search
covers your models and LiteLLM's price list, not the benchmark catalog.

``?q=`` searches LiteLLM's price list instead: the keys whose name holds
the query, best hits first, for the page's catalog search.
"""

from __future__ import annotations

import re
import time

from swe.ledger import overview
from swe.ledger.pricing import BUNDLED, SUPPLEMENT, PricingCatalog, load_catalog
from swe.ledger.store import LedgerStore
from swe.paths import cache_dir_for, swe_dir_for
from swe.webapp.pages import pricing as pricing_page
from swe.webapp.pages._brands import brand_for, resolved_name

# A search lists this many price-list keys; the rest count as overflow.
SEARCH_LIMIT = 60

_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
_EFFORT_TAIL = re.compile(r"\s*\((minimal|low|medium|high|xhigh|max)(,[^)]*)?\)\s*$", re.I)
_THINKING = {"thinking", "reasoning"}
_MODE_WORDS = _THINKING | {"adaptive", "auto", "latest"}
_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
_BRACKET = re.compile(r"\(([^()]*)\)")
_FAST = re.compile(r"[-\s]fast$")
_EFFORT = re.compile(r"[-\s](minimal|low|medium|high|xhigh|max)$")
_DATES = [re.compile(p) for p in (r"@\d{8}$", r"-\d{4}-\d{2}-\d{2}$", r"-\d{8}$")]


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-z]+", text.lower().replace("+", " plus ")) if w]


def qualifier(name: str) -> str | None:
    """How a logged name says the model ran: "High", "Fast", "Thinking ·
    High · Fast". The run-qualifier half of ``ModelMatcher.parse``."""
    text = name.strip().lower()
    effort = None
    thinking = False
    m = _EFFORT_TAIL.search(text)
    if m:
        effort = m.group(1).lower()
        text = text[:m.start()]
    text = text[text.rfind("/") + 1:]
    # Bracketed notes: a mode or a release note goes; any other note is
    # part of the name. Rewritten notes lose their brackets, so this ends.
    while True:
        m = _BRACKET.search(text)
        if not m:
            break
        words = _words(m.group(1))
        if any(w in _MONTHS for w in words):
            replacement = " "
        elif all(w in _MODE_WORDS or w in _EFFORTS or w == "non" for w in words):
            if any(w in _THINKING for w in words) and "non" not in words:
                thinking = True
            replacement = " "
        else:
            replacement = " " + " ".join(words) + " "
        text = text[:m.start()] + replacement + text[m.end():]
    text = text.strip()
    fast = False
    changed = True
    while changed:
        changed = False
        m = _FAST.search(text)
        if m:
            fast, text, changed = True, text[:m.start()], True
        if effort is None:
            m = _EFFORT.search(text)
            if m:
                effort, text, changed = m.group(1), text[:m.start()], True
        for pattern in _DATES:
            m = pattern.search(text)
            if m:
                text, changed = text[:m.start()], True
    words = _words(text)
    for i, w in enumerate(words):
        if w in _THINKING and (i == 0 or words[i - 1] != "non"):
            thinking = True
    parts = (["Thinking"] if thinking else []) + ([effort.capitalize()] if effort else []) \
        + (["Fast"] if fast else [])
    return " · ".join(parts) or None


def list_price(catalog: PricingCatalog, model: str) -> dict | None:
    """The price list entry the ledger charges ``model`` at, per 1M
    tokens, with its key and source; ``None`` when it has none."""
    status, key, _tier = catalog.resolve_entry(model, None)
    if status != "priced":
        return None
    rates = catalog.entries[key]
    return {"key": key, "source": catalog.source(key), "input": rates.input * 1e6,
            "output": rates.output * 1e6, "cache_read": rates.cache_read * 1e6,
            "cache_write": rates.cache_write * 1e6}


def rows(all_slices: list[dict], catalog: PricingCatalog, picked: list[str]) -> list[dict]:
    """One row per logged model the picked harnesses ran (every harness
    when none is picked): most tokens first, then by name."""
    by_model: dict[str, dict[str, int]] = {}
    for s in all_slices:
        if picked and s["harness"] not in picked:
            continue
        tokens = by_model.setdefault(s["model"], {})
        tokens[s["harness"]] = tokens.get(s["harness"], 0) + s["tokens"]
    out = []
    for model, by_harness in by_model.items():
        out.append({
            "model": model,
            "alias": resolved_name(model),
            "qualifier": qualifier(model),
            "brand": brand_for(model),
            "harnesses": [h for h, _ in sorted(by_harness.items(), key=lambda kv: (kv[1], kv[0]),
                                               reverse=True)],
            "tokens": sum(by_harness.values()),
            "list_price": list_price(catalog, model),
        })
    out.sort(key=lambda r: (r["tokens"], r["model"]), reverse=True)
    return out


def normalized(text: str) -> str:
    """The alphanumeric skeleton search compares: "kimi k3" hits
    "Kimi K3", "kimi-k3" and "us.moonshotai.kimi-k3" alike."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def search(catalog: PricingCatalog, query: str, limit: int = SEARCH_LIMIT,
           exclude: set[str] | frozenset = frozenset()) -> dict:
    """Price list keys whose name holds ``query``: a bare-name exact hit
    first, then a bare-name prefix, then anything the key contains,
    shorter keys first in each (``ModelsModel.liteRank``). ``exclude``
    holds the names the page already lists as your models; they neither
    show nor count toward ``total``."""
    needle = normalized(query)
    if not needle:
        return {"query": query, "matches": [], "total": 0}
    hits = []
    for key in catalog.entries:
        if key in exclude or needle not in normalized(key):
            continue
        bare = normalized(key[key.rfind("/") + 1:])
        group = 0 if bare == needle else 1 if bare.startswith(needle) else 2 if needle in bare else 3
        hits.append((group, len(key), key))
    hits.sort()
    matches = []
    for _g, _n, key in hits[:limit]:
        matches.append({"model": key, "alias": None, "qualifier": None, "brand": brand_for(key),
                        "harnesses": [], "tokens": 0, "list_price": list_price(catalog, key)})
    return {"query": query, "matches": matches, "total": len(hits)}


def build(store: LedgerStore, catalog: PricingCatalog, period: str, tz_name: str | None,
          picked: list[str], now: float | None = None) -> dict:
    now = time.time() if now is None else now
    start = overview.period_start(period, now, overview.zone(tz_name))
    all_slices = pricing_page.slices(store, catalog, start, now)
    options = pricing_page.harness_options(all_slices)
    live = [h for h in picked if h in options]
    return {
        "period": period,
        "harness_options": options,
        "harness_filter": live,
        "rows": rows(all_slices, catalog, live),
    }


_CATALOGS: dict[str, tuple[tuple, PricingCatalog]] = {}


def _stamp(path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def search_catalog(home) -> PricingCatalog:
    """The price list search reads: built from the files the ledger's is
    (the LiteLLM download or snapshot, the supplement, your prices),
    never downloading, and rebuilt whenever one of them changes, so a
    search prices a key as the ledger does. Outside the ledger's lock,
    so typing never waits behind a usage scan."""
    files = (cache_dir_for(home) / "web-ledger" / "litellm.json", BUNDLED, SUPPLEMENT,
             swe_dir_for(home) / "config" / "pricing.json")
    stamp = tuple(_stamp(f) for f in files)
    hit = _CATALOGS.get(str(home))
    if hit is None or hit[0] != stamp:
        hit = (stamp, load_catalog(home, download=False))
        _CATALOGS[str(home)] = hit
    return hit[1]


def get(ctx, args: dict) -> dict:
    period, tz_name, picked, rescan = pricing_page.parse_args(args)
    query = args.get("q")
    if query is not None:
        exclude = {name for name in (args.get("exclude") or "").split("\n") if name}
        return search(search_catalog(ctx.home), query, exclude=exclude)
    with ctx.state.webapp.ledger(rescan=rescan) as (store, catalog, scan_ok):
        body = build(store, catalog, period, tz_name, picked)
    body["notes"] = [] if scan_ok else [pricing_page.SCAN_NOTE]
    body["machine"] = ctx.machine.get("name")
    return body
