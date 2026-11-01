"""The Overview page's figures (ports ``OverviewModel.swift`` and
``Heatmap.swift``): totals, per-harness shares, daily rows, quick
figures, top models and the activity heatmap for one period."""

from __future__ import annotations

import bisect
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from swe.ledger.pricing import PricingCatalog
from swe.ledger.store import LedgerStore

PERIODS = ("today", "7d", "month", "30d", "90d", "365d", "all")
DEFAULT_PERIOD = "month"
HEATMAP_WEEKS = 26
TOP_MODELS = 5


def zone(name: str | None) -> tzinfo:
    """The browser's IANA zone, else the hub's own."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


def period_start(period: str, now: float, tz: tzinfo) -> float | None:
    local = datetime.fromtimestamp(now, tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight.timestamp()
    if period == "month":
        return midnight.replace(day=1).timestamp()
    days = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}.get(period)
    return None if days is None else now - days * 86400


class Priced:
    """Prices store groups and sums them by day, harness and model."""

    def __init__(self, store: LedgerStore, catalog: PricingCatalog, tz: tzinfo | None = None):
        self.store, self.catalog, self.tz = store, catalog, tz
        self._resolved: dict[tuple[str, str], tuple] = {}
        self.thresholds = {}
        for model in store.models():
            status, rates, _tier = catalog.resolve(model, None)
            if status == "priced" and rates.long_context is not None:
                self.thresholds[model] = rates.long_context.threshold

    def _resolve(self, model: str, tier: str):
        key = (model, tier)
        if key not in self._resolved:
            self._resolved[key] = self.catalog.resolve(model, tier or None)
        return self._resolved[key]

    def price(self, model: str, tier: str, long, logged, inp: int, out: int, cr: int,
              cw: int, cw1h: int, reas: int, cost_sum: float) -> tuple[float, int]:
        """``(cost, unpriced tokens)`` for one group of events. A logged
        cost wins; an unpriced model's tokens are counted, never as $0."""
        if logged:
            return cost_sum, 0
        status, rates, t = self._resolve(model, tier)
        if status == "priced":
            return rates.cost(inp, out, cr, cw, cw1h, reas, tier=t, long=bool(long)), 0
        return 0.0, (inp + out + cr + cw + reas if status == "unpriced" else 0)

    def rows(self, start: float | None, end: float):
        """``(day, harness, model, counts dict, cost, unpriced tokens)``."""
        day_of: dict[int, date] = {}
        for (harness, model, tier, bucket, long, logged, inp, out, cr, cw, cw1h,
             reas, cost_sum) in self.store.groups(start, end, self.thresholds):
            tokens = inp + out + cr + cw + reas
            cost, unpriced = self.price(model, tier, long, logged, inp, out, cr, cw,
                                        cw1h, reas, cost_sum)
            if bucket not in day_of:
                day_of[bucket] = datetime.fromtimestamp(bucket * 900, self.tz).date()
            counts = {"input": inp, "output": out, "cache_read": cr,
                      "cache_write": cw, "reasoning": reas, "tokens": tokens}
            yield day_of[bucket], harness, model, counts, cost, unpriced


def _blank() -> dict:
    return {"tokens": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
            "reasoning": 0, "cost_usd": 0.0, "unpriced_tokens": 0}


def _add(into: dict, counts: dict, cost: float, unpriced: int) -> None:
    for k, v in counts.items():
        into[k] += v
    into["cost_usd"] += cost
    into["unpriced_tokens"] += unpriced


COUNT_KEYS = ("input", "output", "cache_read", "cache_write", "reasoning", "tokens")
# How far back a satellite's daily totals go: the 365-day period plus slack.
DAILY_DAYS = 400


def daily_totals(store: LedgerStore, catalog: PricingCatalog, days: int = DAILY_DAYS,
                 now: float | None = None) -> dict:
    """This machine's tokens and cost per local day and harness, for the
    satellite usage report (#525): ``{"time_zone", "days": [{date,
    harness, input, output, cache_read, cache_write, reasoning, tokens,
    cost_usd, unpriced_tokens}]}``, oldest first, empty days left out."""
    now = time.time() if now is None else now
    tz = zone(None)
    priced = Priced(store, catalog, tz)
    sums: dict[tuple[date, str], dict] = defaultdict(_blank)
    for day, harness, _model, counts, cost, unpriced in priced.rows(now - days * 86400, now):
        _add(sums[(day, harness)], counts, cost, unpriced)
    return {
        "time_zone": str(getattr(tz, "key", "") or datetime.now(tz).tzname() or ""),
        "days": [{"date": d.isoformat(), "harness": h, **v}
                 for (d, h), v in sorted(sums.items()) if v["tokens"] > 0],
    }


def _extra_rows(rows, since: date | None, until: date):
    """Other machines' daily rows in the overview's row shape, between two
    local dates. Their dates are their own machine's days, which is the
    one approximation a daily report allows."""
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        if day > until or (since is not None and day < since):
            continue
        counts = {}
        for key in COUNT_KEYS:
            value = row.get(key)
            counts[key] = value if isinstance(value, int) and not isinstance(value, bool) else 0
        cost = row.get("cost_usd")
        unpriced = row.get("unpriced_tokens")
        yield (day, str(row.get("harness") or "unknown"), None, counts,
               float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
               unpriced if isinstance(unpriced, int) and not isinstance(unpriced, bool) else 0)


def period_totals(store: LedgerStore, catalog: PricingCatalog, period: str = DEFAULT_PERIOD,
                  tz_name: str | None = None, now: float | None = None,
                  extra: list[dict] | None = None, local: bool = True) -> dict:
    """``{"tokens", "cost_usd"}`` for one machine over the period: this
    one's ledger (``local``) or another's daily rows (``extra``)."""
    now = time.time() if now is None else now
    if period not in PERIODS:
        period = DEFAULT_PERIOD
    tz = zone(tz_name)
    start = period_start(period, now, tz)
    rows = Priced(store, catalog, tz).rows(start, now) if local else _extra_rows(
        extra or [], None if start is None else datetime.fromtimestamp(start, tz).date(),
        datetime.fromtimestamp(now, tz).date())
    out = {"tokens": 0, "cost_usd": 0.0}
    for _day, _h, _m, counts, cost, _u in rows:
        out["tokens"] += counts["tokens"]
        out["cost_usd"] += cost
    return out


def build(store: LedgerStore, catalog: PricingCatalog, period: str = DEFAULT_PERIOD,
          tz_name: str | None = None, now: float | None = None,
          extra: list[dict] | None = None, local: bool = True) -> dict:
    """The Overview for one period. ``extra`` adds other machines' daily
    rows (``daily_totals``'s shape) to the totals, harness shares, days,
    quick figures and heatmap; top models stay this machine's, since the
    daily rows carry no model. ``local=False`` leaves this machine out."""
    now = time.time() if now is None else now
    if period not in PERIODS:
        period = DEFAULT_PERIOD
    tz = zone(tz_name)
    priced = Priced(store, catalog, tz)
    start = period_start(period, now, tz)
    today_local = datetime.fromtimestamp(now, tz).date()
    since_local = None if start is None else datetime.fromtimestamp(start, tz).date()
    extra = extra or []

    def rows():
        if local:
            yield from priced.rows(start, now)
        yield from _extra_rows(extra, since_local, today_local)

    totals = _blank()
    unpriced_models: set[str] = set()
    by_day: dict[date, dict] = defaultdict(_blank)
    by_harness: dict[str, dict] = defaultdict(_blank)
    harness_models: dict[str, set] = defaultdict(set)
    model_tokens: dict[str, int] = defaultdict(int)
    model_harness: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for day, harness, model, counts, cost, unpriced in rows():
        _add(totals, counts, cost, unpriced)
        _add(by_day[day], counts, cost, unpriced)
        _add(by_harness[harness], counts, cost, unpriced)
        if model is None:  # another machine's daily row: no model to count
            continue
        if unpriced:
            unpriced_models.add(model)
        if counts["tokens"]:
            harness_models[harness].add(model)
            model_tokens[model] += counts["tokens"]
            model_harness[model][harness] += counts["tokens"]

    days = sorted((d for d in by_day.items() if d[1]["tokens"] > 0), key=lambda x: x[0], reverse=True)
    active = len(days)
    busiest = max(days, key=lambda x: (x[1]["tokens"], x[0]))[0] if days else None
    grand = max(sum(model_tokens.values()), 1)
    top = sorted(model_tokens.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_MODELS]

    today = datetime.fromtimestamp(now, tz).date()
    first_ts = store.first_ts() if local else None
    first_day = datetime.fromtimestamp(first_ts, tz).date() if first_ts is not None else None
    for row in extra:
        try:
            day = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        first_day = day if first_day is None else min(first_day, day)
    return {
        "period": period,
        "time_zone": str(getattr(tz, "key", "") or datetime.now(tz).tzname() or ""),
        "range": None if start is None else {
            "start_day": datetime.fromtimestamp(start, tz).date().isoformat(),
            "end_day": today.isoformat(),
        },
        "first_day": first_day.isoformat() if first_day else None,
        "totals": {**totals, "unpriced_models": sorted(unpriced_models)},
        "harnesses": sorted((
            {"harness": h, "tokens": v["tokens"], "cost_usd": v["cost_usd"],
             "share": v["tokens"] / max(totals["tokens"], 1),
             "models": len(harness_models[h])}
            for h, v in by_harness.items() if v["tokens"] > 0
        ), key=lambda x: (-x["tokens"], x["harness"])),
        "days": [{
            "date": d.isoformat(), "tokens": v["tokens"], "input": v["input"],
            "output": v["output"], "cached": v["cache_read"] + v["cache_write"],
            "cost_usd": v["cost_usd"],
        } for d, v in days],
        "quick": {
            "active_days": active,
            "tokens_per_day": totals["tokens"] // max(active, 1),
            "busiest_day": busiest.isoformat() if busiest else None,
        },
        "top_models": [{
            "model": m, "tokens": t, "share": t / grand,
            "harnesses": [h for h, _ in sorted(model_harness[m].items(), key=lambda kv: (-kv[1], kv[0]))],
        } for m, t in top],
        "heatmap": heatmap(priced, now, tz, extra=extra, local=local),
    }


def heatmap(priced: Priced, now: float, tz: tzinfo, weeks: int = HEATMAP_WEEKS,
            extra: list[dict] | None = None, local: bool = True) -> dict:
    """Daily totals from local midnight 182 days ago, as week columns
    starting on Sunday, oldest first. Levels are quartiles of the busy
    days, so one huge day doesn't wash out the rest."""
    local_now = datetime.fromtimestamp(now, tz)
    today = local_now.date()
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = (midnight - timedelta(days=182)).timestamp()
    tokens: dict[date, int] = defaultdict(int)
    costs: dict[date, float] = defaultdict(float)
    local_rows = priced.rows(since, now) if local else ()
    other_rows = _extra_rows(extra or [], datetime.fromtimestamp(since, tz).date(), today)
    for day, _h, _m, counts, cost, _u in [*local_rows, *other_rows]:
        tokens[day] += counts["tokens"]
        costs[day] += cost
    busy = sorted(t for t in tokens.values() if t > 0)

    def level(value: int) -> int:
        if value <= 0 or not busy:
            return 0
        return min(4, 1 + bisect.bisect_left(busy, value) * 4 // len(busy))

    week_start = today - timedelta(days=(today.weekday() + 1) % 7)  # Sunday
    first = week_start - timedelta(days=7 * (weeks - 1))
    cells = []
    for i in range(weeks * 7):
        day = first + timedelta(days=i)
        if day > today:
            cells.append({"date": day.isoformat(), "future": True})
            continue
        cells.append({"date": day.isoformat(), "tokens": tokens.get(day, 0),
                      "cost_usd": costs.get(day, 0.0), "level": level(tokens.get(day, 0))})
    return {"weeks": weeks, "days": cells}
