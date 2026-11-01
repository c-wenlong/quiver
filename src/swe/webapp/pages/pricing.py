"""The Pricing page (``GET /v1/app/pricing``): every model the ledger saw
in the period, grouped by provider, with the price its name resolves to
and where that price came from. Ports ``PricingModel.swift``.

Writes: the Mac app's price form (``PricingEditSheet``), which saves a
price in ``~/.swe/config/pricing.json`` or removes the saved ones for a
model (``post``). The web ledger reads that file, so a saved price shows
here as "yours" and reprices past usage, as in the app.
"""

from __future__ import annotations

import time

import math

from swe.ledger import overview
from swe.ledger.pricing import PricingCatalog, base_name
from swe.ledger.store import LedgerStore
from swe.webapp.pages._brands import brand_for, resolved_name

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
        # The form's "Price applies to": the saved entry when it is yours,
        # else the base name, which covers every dated or tiered form.
        row["key"] = row["entry"] if row["source"] == "yours" else base_name(model)
        row["spend_source"] = _spend_source(row)
        # Most tokens first, as the Swift doc promises.
        row["harnesses"] = [h for h, _ in sorted(by_harness[model].items(),
                                                 key=lambda kv: (-kv[1], kv[0]))]
        row["brand"] = brand_for(model)
        # The real name an alias gives an opaque id, shown with the id under it.
        row["alias"] = resolved_name(model)
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
        "unpriced": [{"model": r["model"], "alias": r["alias"], "unpriced_tokens": r["unpriced_tokens"],
                      "harnesses": r["harnesses"], "brand": r["brand"]} for r in unpriced],
    }


def get(ctx, args: dict) -> dict:
    period, tz_name, picked, rescan = parse_args(args)
    with ctx.state.webapp.ledger(rescan=rescan) as (store, catalog, scan_ok):
        body = build(store, catalog, period, tz_name, picked)
    body["notes"] = [] if scan_ok else [SCAN_NOTE]
    body["machine"] = ctx.machine.get("name")
    return body


# ---------------------------------------------------------------- writes
# ``save`` {"model", "key", "input", "output", "cache_read"?, "cache_write"?,
# "replacing"?}: PricingModel.save, USD per million tokens; a blank cache
# price is 0. ``replacing`` is the saved key the form opened on, dropped in
# the same write when the key changed. ``remove`` {"model"}: every saved
# key that prices the model (PricingModel.remove(matching:)). Each answers
# the page read again with the query the page sends (period, tz, harness).

NAME_MAX = 200
RATE_MAX = 1_000_000.0


def post(ctx, action: str, body: dict) -> dict:
    from swe.ledger.pricing import remove_overrides, save_override
    from swe.webapp.pages import PageError

    if action not in ("save", "remove"):
        raise PageError(404, "unknown_action", f"unknown pricing action: {action}")
    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "expected a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > NAME_MAX:
        raise PageError(422, "bad_model", "model must be a model name the page lists")
    # The home the shared price list is read from, so it reads this write.
    home = ctx.state.webapp.home
    try:
        if action == "save":
            key = body.get("key")
            if not isinstance(key, str) or not key.strip() or len(key) > NAME_MAX or "\n" in key:
                raise PageError(422, "bad_key", "Price applies to must be one model name.")
            prices = {
                "input": _rate(body.get("input"), "input", required=True),
                "output": _rate(body.get("output"), "output", required=True),
                "cache_read": _rate(body.get("cache_read"), "cache read"),
                "cache_write": _rate(body.get("cache_write"), "cache write"),
            }
            replacing = body.get("replacing")
            with ctx.state.webapp.ledger() as (_, catalog, _ok):
                # Only a key that is yours now can be renamed away.
                replacing = replacing if isinstance(replacing, str) and replacing in catalog.overrides else None
            save_override(home, key.strip(), prices, replacing)
        else:
            with ctx.state.webapp.ledger() as (_, catalog, _ok):
                keys = catalog.user_keys(model)
            if not keys:
                raise PageError(404, "no_saved_price", f"You have no saved price for {model}. Refresh to see the current one.")
            remove_overrides(home, keys)
    except ValueError as exc:
        raise PageError(409, "pricing_unwritable", str(exc)) from exc
    ctx.state.webapp.prices_changed()
    args = {k: body.get(k) for k in ("period", "tz", "harness") if isinstance(body.get(k), str)}
    return {"action": action, "page": get(ctx, args)}


def _rate(value, label: str, required: bool = False) -> float:
    from swe.webapp.pages import PageError

    if value is None or value == "":
        if required:
            raise PageError(422, "bad_rate", f"Give an {label} price." if label[0] in "aeiou" else f"Give a {label} price.")
        return 0.0
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            value = None
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0 or value > RATE_MAX):
        raise PageError(422, "bad_rate", f"The {label} price must be a number of dollars per 1M tokens, 0 or more.")
    return float(value)
