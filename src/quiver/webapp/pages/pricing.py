"""The Pricing page (``GET /v1/app/pricing``): every model the ledger saw
in the period, grouped by provider, with the price its name resolves to
and where that price came from. Ports ``PricingModel.swift``.

Read only. The Mac app's price form, which saves or resets a price in
``~/.quiver/config/pricing.json``, is a write and is not ported; the web
ledger reads that file, so a price saved there shows here as "yours".
"""

from __future__ import annotations

import time

from quiver.ledger import overview
from quiver.ledger.pricing import PricingCatalog
from quiver.ledger.store import LedgerStore
from quiver.webapp.pages._brands import brand_for

SCAN_NOTE = "Some usage logs couldn't be read, so these figures may be incomplete."


def biller(harness: str) -> str:
    """``PricingModel.biller(of:)``: Grok Bot signs in with the Cursor
    account and its charges arrive through Cursor's feed; every other
    harness bills its own usage."""
    return "cursor" if harness == "grok-bot" else harness


def parse_args(args: dict) -> tuple[str, str | None, list[str], bool]:
    """``(period, tz, harness filter, rescan)`` from the query string."""
    period = args.get("period") or overview.DEFAULT_PERIOD
    if period not in overview.PERIODS:
        period = overview.DEFAULT_PERIOD
    picked = [h for h in (args.get("harness") or "").split(",") if h]
    return period, args.get("tz") or None, picked, args.get("rescan") in {"1", "true"}


def slices(store: LedgerStore, catalog: PricingCatalog, start: float | None,
           end: float) -> list[dict]:
    """``Ledger.byModelHarness``: the period's usage per model, harness,
    tier and whether the log carried its own cost, priced as the Overview
    prices it. Sorted, so the first slice of a model is always the same."""
    priced = overview.Priced(store, catalog)
    merged: dict[tuple, dict] = {}
    for (harness, model, tier, _bucket, long, logged, inp, out, cr, cw, cw1h, reas,
         cost_sum) in store.groups(start, end, priced.thresholds):
        cost, unpriced = priced.price(model, tier, long, logged, inp, out, cr, cw, cw1h,
                                      reas, cost_sum)
        key = (model, harness, tier, bool(logged))
        row = merged.setdefault(key, {
            "model": model, "harness": harness, "tier": tier or None,
            "harness_billed": bool(logged), "tokens": 0, "cost_usd": 0.0,
            "unpriced_tokens": 0,
        })
        row["tokens"] += inp + out + cr + cw + reas
        row["cost_usd"] += cost
        row["unpriced_tokens"] += unpriced
    return [merged[k] for k in sorted(merged)]


def _per_million(rates) -> dict | None:
    if rates is None:
        return None
    return {"input": rates.input * 1e6, "output": rates.output * 1e6,
            "cache_read": rates.cache_read * 1e6, "cache_write": rates.cache_write * 1e6}


def _spend_source(row: dict) -> str:
    """``PricedModel.spendSource``: a harness-billed row is billed by its
    harness whatever price is saved, since no rate applies to it."""
    if row["harness_billed"]:
        return "billed"
    if row["is_free"]:
        return "free"
    return row["source"] or "unpriced"


def harness_options(all_slices: list[dict]) -> list[str]:
    """Harnesses with usage in the period, most tokens first."""
    by_harness: dict[str, int] = {}
    for s in all_slices:
        by_harness[s["harness"]] = by_harness.get(s["harness"], 0) + s["tokens"]
    return [h for h, _ in sorted(by_harness.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)]


def rows(all_slices: list[dict], catalog: PricingCatalog, picked: list[str]) -> list[dict]:
    """One row per model in the picked harnesses' usage (every harness
    when none is picked), largest first: ``PricingModel.load``'s merge."""
    shown = [s for s in all_slices if not picked or s["harness"] in picked]
    merged: dict[str, dict] = {}
    any_logged: dict[str, bool] = {}
    by_harness: dict[str, dict[str, int]] = {}
    for s in shown:
        model = s["model"]
        billed = s["harness_billed"] or s["tokens"] == 0
        any_logged[model] = any_logged.get(model, False) or s["harness_billed"]
        who = biller(s["harness"]) if s["harness_billed"] else None
        tokens = by_harness.setdefault(model, {})
        tokens[s["harness"]] = tokens.get(s["harness"], 0) + s["tokens"]
        row = merged.get(model)
        if row is not None:
            row["tokens"] += s["tokens"]
            row["harness_billed"] = row["harness_billed"] and billed
            if who and who not in row["billed_by"]:
                row["billed_by"].append(who)
            row["unpriced_tokens"] += s["unpriced_tokens"]
            if s["tier"] and s["tier"] not in row["tier_names"]:
                row["tier_names"].append(s["tier"])
            continue
        status, key, resolved_tier = catalog.resolve_entry(model, s["tier"])
        tiers = [s["tier"]] if s["tier"] else []
        rates = None
        if status == "priced":
            rates = catalog.entries[key]
            if resolved_tier and resolved_tier not in tiers:
                tiers.append(resolved_tier)
        merged[model] = {
            "model": model, "tokens": s["tokens"], "harness_billed": billed,
            "billed_by": [who] if who else [],
            "entry": key, "source": catalog.source(key) if key else None,
            "rates": rates, "tier_names": tiers, "is_free": status == "free",
            "unpriced_tokens": s["unpriced_tokens"],
        }
    out = []
    for model, row in merged.items():
        if not any_logged[model]:
            row["harness_billed"] = False
        rates = row.pop("rates")
        # A multiplier only where the ledger applies one: a tier with
        # explicit rates is priced at them (ModelRates.cost), not scaled.
        row["tiers"] = [{"name": t, "multiplier": (rates.tier_multipliers.get(t)
                                                   if rates and t not in rates.tiers else None)}
                        for t in sorted(row.pop("tier_names"))]
        row["rates"] = _per_million(rates)
        row["spend_source"] = _spend_source(row)
        # Most tokens first, as the Swift doc promises.
        row["harnesses"] = [h for h, _ in sorted(by_harness[model].items(),
                                                 key=lambda kv: (-kv[1], kv[0]))]
        row["brand"] = brand_for(model)
        out.append(row)
    out.sort(key=lambda r: (r["tokens"], r["model"]), reverse=True)
    return out


def groups(model_rows: list[dict]) -> list[dict]:
    """Provider cards: each brand's models, largest first; the cards by
    their tokens."""
    grouped: dict[str, dict] = {}
    for row in model_rows:
        brand = row["brand"]
        group = grouped.setdefault(brand["id"], {
            "id": brand["id"], "name": brand["name"], "brand": brand, "models": [], "tokens": 0,
        })
        group["models"].append(row)
        group["tokens"] += row["tokens"]
    return sorted(grouped.values(), key=lambda g: (g["tokens"], g["id"]), reverse=True)


def build(store: LedgerStore, catalog: PricingCatalog, period: str, tz_name: str | None,
          picked: list[str], now: float | None = None) -> dict:
    now = time.time() if now is None else now
    start = overview.period_start(period, now, overview.zone(tz_name))
    all_slices = slices(store, catalog, start, now)
    options = harness_options(all_slices)
    # A pick whose usage left the period drops out; with none left,
    # everything shows again.
    live = [h for h in picked if h in options]
    model_rows = rows(all_slices, catalog, live)
    unpriced = sorted((r for r in model_rows if r["unpriced_tokens"] > 0),
                      key=lambda r: (r["unpriced_tokens"], r["model"]), reverse=True)
    return {
        "period": period,
        "harness_options": options,
        "harness_filter": live,
        "groups": groups(model_rows),
        "unpriced": [{"model": r["model"], "unpriced_tokens": r["unpriced_tokens"],
                      "harnesses": r["harnesses"], "brand": r["brand"]} for r in unpriced],
    }


def get(ctx, args: dict) -> dict:
    period, tz_name, picked, rescan = parse_args(args)
    with ctx.state.webapp.ledger(rescan=rescan) as (store, catalog, scan_ok):
        body = build(store, catalog, period, tz_name, picked)
    body["notes"] = [] if scan_ok else [SCAN_NOTE]
    body["machine"] = ctx.machine.get("name")
    return body
