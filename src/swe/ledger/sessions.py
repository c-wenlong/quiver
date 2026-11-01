"""Usage per session (ports ``Ledger.bySession`` and ``SessionFolders``).

Read over the whole history, not a period: a session resumed today that
started months ago keeps its full cost. The key is ``(harness, session)``,
which is exactly the id ``swe session --json`` gives each row (Claude's
transcript uuid, Codex's rollout stem, Devin's session name).
"""

from __future__ import annotations

from collections import defaultdict

from swe.ledger.overview import Priced
from swe.ledger.pricing import PricingCatalog
from swe.ledger.store import LedgerStore


def by_session(store: LedgerStore, catalog: PricingCatalog) -> dict[tuple[str, str], dict]:
    priced = Priced(store, catalog)
    out: dict[tuple[str, str], dict] = {}
    model_tokens: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (harness, session, model, tier, long, logged, inp, outp, cr, cw, cw1h, reas,
         cost_sum, events, first, last) in store.session_groups(priced.thresholds):
        cost, unpriced = priced.price(model, tier, long, logged, inp, outp, cr, cw,
                                      cw1h, reas, cost_sum)
        key = (harness, session)
        u = out.get(key)
        if u is None:
            u = out[key] = {
                "tokens": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "reasoning": 0, "cost_usd": 0.0, "unpriced_tokens": 0, "events": 0,
                "first_ts": first, "last_ts": last, "cwd": None,
            }
        tokens = inp + outp + cr + cw + reas
        for k, v in (("tokens", tokens), ("input", inp), ("output", outp), ("cache_read", cr),
                     ("cache_write", cw), ("reasoning", reas), ("unpriced_tokens", unpriced),
                     ("events", events)):
            u[k] += v
        u["cost_usd"] += cost
        u["first_ts"] = min(u["first_ts"], first)
        u["last_ts"] = max(u["last_ts"], last)
        model_tokens[key][model] += tokens
    for key, models in model_tokens.items():
        out[key]["models"] = [
            {"model": m, "tokens": t}
            for m, t in sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    # The folder most of a session's events used; a Claude side-session
    # notes folder only when nothing else is known; ties go to the later.
    best: dict[tuple[str, str], tuple] = {}
    for harness, session, cwd, count, last in store.session_folders():
        notes = "/side-session-notes/" in cwd
        rank = (not notes, count, last)
        key = (harness, session)
        if key in out and (key not in best or rank > best[key][0]):
            best[key] = (rank, cwd)
    for key, (_rank, cwd) in best.items():
        out[key]["cwd"] = cwd
    return out


_SUMS = ("tokens", "input", "output", "cache_read", "cache_write", "reasoning",
         "cost_usd", "unpriced_tokens", "events")


def merge(parts: list[dict]) -> dict:
    """Several sessions' usage as one: sums, every model, the widest span,
    and the folder of the part with the most events."""
    out = {k: 0 for k in _SUMS}
    out["cost_usd"] = 0.0
    models: dict[str, int] = {}
    for u in parts:
        for k in _SUMS:
            out[k] += u[k]
        for m in u.get("models") or []:
            models[m["model"]] = models.get(m["model"], 0) + m["tokens"]
    out["first_ts"] = min(u["first_ts"] for u in parts)
    out["last_ts"] = max(u["last_ts"] for u in parts)
    out["cwd"] = max(parts, key=lambda u: u["events"]).get("cwd")
    out["models"] = [{"model": m, "tokens": t}
                     for m, t in sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out
