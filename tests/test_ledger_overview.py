"""ledger/overview.py: periods, local days, quick figures, heatmap."""

import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from swe.ledger import overview
from swe.ledger.models import LedgerRecord, UsageEvent
from swe.ledger.pricing import PricingCatalog, rates_from_entry
from swe.ledger.store import LedgerStore

SG = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=SG).timestamp()
CATALOG = PricingCatalog({"claude-opus-5": rates_from_entry(
    {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06})})


def _at(y, m, d, h=10):
    return datetime(y, m, d, h, tzinfo=SG).timestamp()


class TestOverview(unittest.TestCase):
    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)
        self.n = 0

    def add(self, ts, inp, harness="claude", model="claude-opus-5", cost=None):
        self.n += 1
        self.store.upsert(LedgerRecord(key=f"k{self.n}", event=UsageEvent(
            harness=harness, account="/h", session="s", cwd=None, model=model,
            ts=ts, input=inp, cost=cost)))

    def build(self, period):
        return overview.build(self.store, CATALOG, period, "Asia/Singapore", now=NOW)

    def test_periods_and_totals(self):
        self.add(_at(2026, 10, 23), 100)
        self.add(_at(2026, 10, 1, 1), 200)
        self.add(_at(2026, 9, 30, 23), 400)
        self.assertEqual(self.build("today")["totals"]["tokens"], 100)
        self.assertEqual(self.build("month")["totals"]["tokens"], 300)
        self.assertEqual(self.build("all")["totals"]["tokens"], 700)
        body = self.build("month")
        self.assertAlmostEqual(body["totals"]["cost_usd"], 300e-06)
        self.assertEqual(body["range"], {"start_day": "2026-10-01", "end_day": "2026-10-23"})
        self.assertEqual(self.build("all")["first_day"], "2026-09-30")

    def test_days_are_local(self):
        # 23:30 in Singapore is 15:30 UTC: still the 22nd, locally.
        self.add(datetime(2026, 10, 22, 23, 30, tzinfo=SG).timestamp(), 10)
        self.assertEqual(self.build("7d")["days"][0]["date"], "2026-10-22")

    def test_quick_figures_harness_shares_and_models(self):
        self.add(_at(2026, 10, 20), 100)
        self.add(_at(2026, 10, 21), 300)
        self.add(_at(2026, 10, 22), 300, harness="codex", model="gpt-x")
        body = self.build("7d")
        self.assertEqual(body["quick"], {"active_days": 3, "tokens_per_day": 233,
                                         "busiest_day": "2026-10-22"})
        self.assertEqual([h["harness"] for h in body["harnesses"]], ["claude", "codex"])
        self.assertEqual(body["top_models"][0]["model"], "claude-opus-5")
        self.assertEqual(body["totals"]["unpriced_tokens"], 300)
        self.assertEqual(body["totals"]["unpriced_models"], ["gpt-x"])

    def test_logged_cost_beats_the_catalog(self):
        self.add(_at(2026, 10, 23), 100, cost=2.5)
        self.assertAlmostEqual(self.build("today")["totals"]["cost_usd"], 2.5)

    def test_heatmap(self):
        self.add(_at(2026, 10, 23), 10)
        self.add(_at(2026, 10, 22), 1000)
        hm = self.build("today")["heatmap"]
        self.assertEqual(len(hm["days"]), 26 * 7)
        self.assertEqual(hm["days"][0]["date"], "2026-04-26")  # a Sunday
        by_date = {d["date"]: d for d in hm["days"]}
        self.assertEqual(by_date["2026-10-23"]["level"], 1)
        self.assertEqual(by_date["2026-10-22"]["level"], 3)
        self.assertTrue(by_date["2026-10-24"]["future"])

    def test_unknown_period_falls_back(self):
        self.assertEqual(self.build("nonsense")["period"], "month")


def _row(day, tokens, harness="codex", cost=0.5):
    return {"date": day, "harness": harness, "input": tokens, "output": 0, "cache_read": 0,
            "cache_write": 0, "reasoning": 0, "tokens": tokens, "cost_usd": cost,
            "unpriced_tokens": 0}


class TestOtherMachines(TestOverview):
    """Satellites' daily rows in the Overview (#525)."""

    def test_daily_totals_group_by_day_and_harness(self):
        self.add(_at(2026, 10, 22), 100)
        self.add(_at(2026, 10, 22, 11), 50)
        self.add(_at(2026, 10, 23), 7, harness="codex", model="gpt-x")
        with unittest.mock.patch.object(overview, "zone", return_value=SG):
            out = overview.daily_totals(self.store, CATALOG, now=NOW)
        self.assertEqual([(r["date"], r["harness"], r["tokens"]) for r in out["days"]],
                         [("2026-10-22", "claude", 150), ("2026-10-23", "codex", 7)])
        self.assertAlmostEqual(out["days"][0]["cost_usd"], 150e-06)
        self.assertEqual(out["days"][1]["unpriced_tokens"], 7)

    def test_all_machines_adds_the_rows_up(self):
        self.add(_at(2026, 10, 22), 100)
        extra = [_row("2026-10-22", 40), _row("2026-10-23", 60, harness="claude"),
                 _row("2026-09-01", 999), {"date": "garbage"}]
        body = overview.build(self.store, CATALOG, "month", "Asia/Singapore", now=NOW,
                              extra=extra)
        self.assertEqual(body["totals"]["tokens"], 200)  # September is outside the month
        self.assertEqual({h["harness"]: h["tokens"] for h in body["harnesses"]},
                         {"claude": 160, "codex": 40})
        self.assertEqual([(d["date"], d["tokens"]) for d in body["days"]],
                         [("2026-10-23", 60), ("2026-10-22", 140)])
        self.assertEqual(body["top_models"][0]["tokens"], 100)  # this machine's only
        cells = {c["date"]: c for c in body["heatmap"]["days"]}
        self.assertEqual(cells["2026-10-22"]["tokens"], 140)
        self.assertEqual(overview.build(self.store, CATALOG, "all", "Asia/Singapore",
                                        now=NOW, extra=extra)["first_day"], "2026-09-01")

    def test_one_other_machine_leaves_this_one_out(self):
        self.add(_at(2026, 10, 22), 100)
        body = overview.build(self.store, CATALOG, "month", "Asia/Singapore", now=NOW,
                              extra=[_row("2026-10-21", 30)], local=False)
        self.assertEqual(body["totals"]["tokens"], 30)
        self.assertEqual(body["top_models"], [])
        self.assertEqual(body["first_day"], "2026-10-21")
        self.assertEqual(overview.period_totals(
            self.store, CATALOG, "month", "Asia/Singapore", now=NOW,
            extra=[_row("2026-10-21", 30)], local=False), {"tokens": 30, "cost_usd": 0.5})
        self.assertEqual(overview.period_totals(
            self.store, CATALOG, "month", "Asia/Singapore", now=NOW)["tokens"], 100)


if __name__ == "__main__":
    unittest.main()
