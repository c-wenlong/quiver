"""The Pricing page's data: per-model rows, sources, billing and brands."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.ledger.pricing import PricingCatalog, load_catalog, rates_from_entry
from quiver.ledger.store import LedgerStore
from quiver.webapp.api import WebApp
from quiver.webapp.pages import _brands, pricing
from quiver.webapp.static import STATIC_DIR

from tests.test_remote_hub import HubBase

SG = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=SG).timestamp()
AT = datetime(2026, 10, 20, 10, 0, tzinfo=SG).timestamp()


def _rates(inp, out, **extra):
    return rates_from_entry({"input_cost_per_token": inp, "output_cost_per_token": out, **extra})


def catalog():
    return PricingCatalog(
        {"claude-opus-5": _rates(5e-06, 25e-06, cache_read_input_token_cost=5e-07),
         "gpt-x": _rates(1e-06, 2e-06),
         "grok-mine": _rates(3e-06, 4e-06),
         "grok-bot": _rates(1e-06, 1e-06),
         "gpt-y": _rates(2e-06, 8e-06, input_cost_per_token_priority=3.5e-06)},
        {"tier_multipliers": {"claude-opus-5": {"fast": 2}},
         "default_tier_multipliers": {"priority": 2}},
        overrides={"gpt-x"}, quiver={"grok-mine"})


class _Store(unittest.TestCase):
    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)
        self.n = 0

    def add(self, model, inp, harness="claude", cost=None, tier=None, ts=AT):
        self.n += 1
        self.store.upsert(LedgerRecord(key=f"k{self.n}", event=UsageEvent(
            harness=harness, account="/h", session="s", cwd=None, model=model,
            ts=ts, input=inp, cost=cost, tier=tier)))

    def build(self, picked=(), period="month"):
        return pricing.build(self.store, catalog(), period, "Asia/Singapore", list(picked), now=NOW)


class TestCatalogSources(unittest.TestCase):
    def test_resolve_entry_names_the_key_and_its_source(self):
        cat = catalog()
        self.assertEqual(cat.resolve_entry("claude-opus-5-20261001", None), ("priced", "claude-opus-5", None))
        self.assertEqual(cat.resolve_entry("claude-opus-5-fast", None), ("priced", "claude-opus-5", "fast"))
        self.assertEqual(cat.resolve_entry("openrouter/free", None), ("free", None, None))
        self.assertEqual(cat.resolve_entry("mystery", None), ("unpriced", None, None))
        self.assertEqual([cat.source(k) for k in ("gpt-x", "grok-mine", "claude-opus-5")],
                         ["yours", "quiver", "litellm"])
        status, rates, tier = cat.resolve("claude-opus-5-fast", None)
        self.assertEqual((status, rates.input, tier), ("priced", 5e-06, "fast"))

    def test_load_catalog_marks_user_prices(self):
        home = Path(tempfile.mkdtemp())
        (home / ".quiver" / "config").mkdir(parents=True)
        (home / ".quiver" / "config" / "pricing.json").write_text(
            json.dumps({"models": {"my-model": {"input": 1, "output": 2}}}))
        cat = load_catalog(home, download=False)
        self.assertEqual(cat.resolve_entry("my-model", None)[1], "my-model")
        self.assertEqual(cat.source("my-model"), "yours")
        self.assertEqual(cat.source("claude-opus-5"), "litellm")


class TestRows(_Store):
    def test_rows_merge_harnesses_and_carry_rates_per_million(self):
        self.add("claude-opus-5", 100)
        self.add("claude-opus-5", 300, harness="devin")
        self.add("claude-opus-5", 50, tier="fast")
        body = self.build()
        (group,) = body["groups"]
        self.assertEqual((group["id"], group["name"], group["tokens"]), ("anthropic", "Anthropic", 450))
        (row,) = group["models"]
        self.assertEqual(row["harnesses"], ["devin", "claude"])
        self.assertEqual(row["spend_source"], "litellm")
        self.assertEqual(row["entry"], "claude-opus-5")
        self.assertAlmostEqual(row["rates"]["input"], 5.0)
        self.assertAlmostEqual(row["rates"]["cache_read"], 0.5)
        self.assertEqual(row["tiers"], [{"name": "fast", "multiplier": 2.0}])
        self.assertEqual(body["unpriced"], [])
        self.assertEqual(body["harness_options"], ["devin", "claude"])

    def test_a_tier_with_explicit_rates_shows_no_multiplier(self):
        # The ledger prices gpt-y's priority tier at its own rates, not ×2.
        self.add("gpt-y", 10, harness="codex", tier="priority")
        self.add("gpt-x", 10, harness="codex", tier="priority")
        rows = {r["model"]: r for g in self.build()["groups"] for r in g["models"]}
        self.assertEqual(rows["gpt-y"]["tiers"], [{"name": "priority", "multiplier": None}])
        self.assertEqual(rows["gpt-x"]["tiers"], [{"name": "priority", "multiplier": 2.0}])

    def test_sources_free_and_unpriced(self):
        self.add("gpt-x", 10, harness="codex")
        self.add("grok-mine", 20)
        self.add("opencode/big-pickle-free", 30, harness="opencode")
        self.add("mystery-1", 40)
        self.add("mystery-2", 40)
        rows = {r["model"]: r for g in self.build()["groups"] for r in g["models"]}
        self.assertEqual(rows["gpt-x"]["spend_source"], "yours")
        self.assertEqual(rows["grok-mine"]["spend_source"], "quiver")
        self.assertEqual(rows["opencode/big-pickle-free"]["spend_source"], "free")
        self.assertIsNone(rows["opencode/big-pickle-free"]["rates"])
        self.assertEqual(rows["mystery-1"]["spend_source"], "unpriced")
        unpriced = self.build()["unpriced"]
        # Largest first, then by name descending, as the app sorts.
        self.assertEqual([u["model"] for u in unpriced], ["mystery-2", "mystery-1"])
        self.assertEqual(unpriced[0]["unpriced_tokens"], 40)

    def test_harness_billing(self):
        # Every slice logged a cost: billed by the harness (Grok Bot's by Cursor).
        self.add("grok-bot", 10, harness="grok-bot", cost=0.1)
        # Mixed: billed_by names the biller but the rest goes by the list.
        self.add("claude-opus-5", 10, harness="cursor", cost=0.2)
        self.add("claude-opus-5", 10)
        rows = {r["model"]: r for g in self.build()["groups"] for r in g["models"]}
        bot = rows["grok-bot"]
        self.assertTrue(bot["harness_billed"])
        self.assertEqual((bot["spend_source"], bot["billed_by"]), ("billed", ["cursor"]))
        opus = rows["claude-opus-5"]
        self.assertFalse(opus["harness_billed"])
        self.assertEqual((opus["spend_source"], opus["billed_by"]), ("litellm", ["cursor"]))

    def test_groups_sort_by_tokens_and_filter_by_harness(self):
        self.add("claude-opus-5", 100)
        self.add("gpt-x", 500, harness="codex")
        self.add("mystery", 1)
        body = self.build()
        self.assertEqual([g["id"] for g in body["groups"]], ["openai", "anthropic", "other"])
        body = self.build(["claude", "gone"])
        self.assertEqual(body["harness_filter"], ["claude"])
        self.assertEqual([g["id"] for g in body["groups"]], ["anthropic", "other"])
        # The options still list every harness in the period.
        self.assertEqual(body["harness_options"], ["codex", "claude"])

    def test_period_bounds_the_rows(self):
        self.add("claude-opus-5", 100, ts=datetime(2026, 9, 1, tzinfo=SG).timestamp())
        self.assertEqual(self.build()["groups"], [])
        self.assertEqual(len(self.build(period="all")["groups"]), 1)


class TestBrands(unittest.TestCase):
    def test_brand_for_reads_the_name_after_any_provider(self):
        ids = {m: _brands.brand_for(m)["id"] for m in (
            "claude-opus-5", "openrouter/qwen3-coder", "gpt-5.6", "o3", "grok-4.20",
            "grok-bot", "swe-2-max", "glm-4.6", "MODEL_PRIVATE_11")}
        self.assertEqual(ids, {
            "claude-opus-5": "anthropic", "openrouter/qwen3-coder": "alibaba", "gpt-5.6": "openai",
            "o3": "openai", "grok-4.20": "xai", "grok-bot": "grok-bot", "swe-2-max": "cognition",
            "glm-4.6": "zai", "MODEL_PRIVATE_11": "other"})
        other = _brands.brand_for("mystery")
        self.assertEqual((other["color"], other["logo"]), ("#45454b", None))

    def test_every_lab_logo_ships(self):
        for bid, _name, _color, icon, _kw, models in _brands.BRANDS:
            if models:
                logo = _brands.logo_file(icon)
                self.assertIsNotNone(logo, bid)
                self.assertTrue((STATIC_DIR / logo).is_file(), bid)
        self.assertTrue(_brands.brand_for("gpt-5")["mono"])
        self.assertFalse(_brands.brand_for("claude-opus-5")["mono"])


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.srv.state.webapp = WebApp(self.srv.home, download_prices=False)
        self.addCleanup(self.srv.state.webapp.close)

    def test_needs_a_device_and_answers_with_groups(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/pricing")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/pricing?period=bogus&harness=claude",
                                        headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["period"], "month")
        self.assertEqual((body["groups"], body["harness_filter"], body["notes"]), ([], [], []))
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_bundles_carry_both_pages(self):
        status, body = self.srv.request("GET", "/app/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b'registerPage("pricing"', body)
        self.assertIn(b'registerPage("models"', body)
        for name in ("source-litellm.svg", "harness-openai.svg"):
            self.assertEqual(self.srv.request("GET", f"/app/{name}")[0], 200, name)


if __name__ == "__main__":
    unittest.main()
