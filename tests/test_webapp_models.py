"""The Models page's data: used models, qualifiers, list prices, search."""

import json
import unittest
from unittest.mock import patch

from swe.webapp.api import WebApp
from swe.webapp.pages import models

from tests.test_remote_hub import HubBase
from tests.test_webapp_pricing import NOW, _Store, catalog


class TestQualifier(unittest.TestCase):
    def test_effort_fast_and_thinking(self):
        cases = {
            "claude-opus-5": None,
            "swe-2-max": "Max",
            "claude-opus-5-thinking-high-fast": "Thinking · High · Fast",
            "cursor-grok-4.6-high-fast": "High · Fast",
            "openai/gpt-6.1-sol@20261003": None,
            "gpt-6.1-xhigh@20261003": "Xhigh",
            "Claude Opus 5.5 (max)": "Max",
            "Kimi K2 (Reasoning)": "Thinking",
            "model (Non-reasoning, High)": None,
        }
        for name, want in cases.items():
            self.assertEqual(models.qualifier(name), want, name)


class TestRows(_Store):
    def build(self, picked=()):
        return models.build(self.store, catalog(), "month", "Asia/Singapore", list(picked), now=NOW)

    def test_used_models_most_tokens_first_with_prices(self):
        self.add("claude-opus-5-high", 100)
        self.add("claude-opus-5-high", 300, harness="devin")
        self.add("gpt-x", 50, harness="codex")
        self.add("mystery", 50)
        body = self.build()
        rows = body["rows"]
        self.assertEqual([r["model"] for r in rows], ["claude-opus-5-high", "mystery", "gpt-x"])
        top = rows[0]
        self.assertEqual((top["tokens"], top["harnesses"], top["qualifier"]), (400, ["devin", "claude"], "High"))
        self.assertEqual(top["brand"]["name"], "Anthropic")
        self.assertEqual(top["list_price"]["key"], "claude-opus-5")
        self.assertAlmostEqual(top["list_price"]["output"], 25.0)
        self.assertEqual(rows[2]["list_price"]["source"], "yours")
        self.assertIsNone(rows[1]["list_price"])
        self.assertEqual([r["alias"] for r in rows], [None, None, None])
        filtered = self.build(["codex"])
        self.assertEqual([r["model"] for r in filtered["rows"]], ["gpt-x"])
        self.assertEqual(filtered["harness_options"], ["devin", "claude", "codex"])


    def test_an_aliased_name_shows_its_real_name_and_brand(self):
        self.add("MODEL_PRIVATE_11", 100, harness="devin")
        row = self.build()["rows"][0]
        self.assertEqual((row["model"], row["alias"]), ("MODEL_PRIVATE_11", "claude-haiku-4-5"))
        self.assertEqual(row["brand"]["id"], "anthropic")


class TestSearch(unittest.TestCase):
    def test_bare_exact_then_prefix_then_contains(self):
        from swe.ledger.pricing import PricingCatalog, rates_from_entry
        r = rates_from_entry({"input_cost_per_token": 1e-06, "output_cost_per_token": 1e-06})
        cat = PricingCatalog({k: r for k in (
            "vendor/kimi-k3-eu", "kimi-k3", "moonshot/kimi-k3", "kimi-k3-turbo", "x/notkimik3")})
        body = models.search(cat, "Kimi K3")
        self.assertEqual([m["model"] for m in body["matches"]],
                         ["kimi-k3", "moonshot/kimi-k3", "kimi-k3-turbo", "vendor/kimi-k3-eu", "x/notkimik3"])
        self.assertEqual(body["total"], 5)
        self.assertEqual(models.search(cat, "kimi", limit=2)["total"], 5)
        self.assertEqual(len(models.search(cat, "kimi", limit=2)["matches"]), 2)
        self.assertEqual(models.search(cat, " - ")["matches"], [])
        # Keys the page already lists as your models neither show nor count.
        body = models.search(cat, "kimi", limit=2, exclude={"kimi-k3", "moonshot/kimi-k3"})
        self.assertEqual([m["model"] for m in body["matches"]], ["kimi-k3-turbo", "vendor/kimi-k3-eu"])
        self.assertEqual(body["total"], 3)


class TestSearchCatalog(unittest.TestCase):
    def test_rebuilt_when_a_price_file_changes(self):
        import tempfile
        from pathlib import Path
        home = Path(tempfile.mkdtemp())
        first = models.search_catalog(home)
        self.assertIs(models.search_catalog(home), first)
        (home / ".quiver" / "config").mkdir(parents=True)
        (home / ".quiver" / "config" / "pricing.json").write_text(
            json.dumps({"models": {"my-model": {"input": 1, "output": 2}}}))
        second = models.search_catalog(home)
        self.assertIsNot(second, first)
        self.assertEqual(second.source("my-model"), "yours")


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.srv.state.webapp = WebApp(self.srv.home, download_prices=False)
        self.addCleanup(self.srv.state.webapp.close)

    def test_rows_and_search(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/models")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/models", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual((body["rows"], body["period"]), ([], "month"))
        # Search reads its own cached price list, never the ledger (no scan).
        with patch.object(self.srv.state.webapp, "ledger", side_effect=AssertionError("scanned")):
            status, data = self.srv.request("GET", "/v1/app/models?q=claude-opus&exclude=claude-opus-5%0Ax",
                                            headers=self.auth())
        self.assertEqual(status, 200, data)
        found = json.loads(data)
        self.assertGreater(found["total"], 0)
        names = [m["model"] for m in found["matches"]]
        self.assertTrue(all("claudeopus" in models.normalized(n) for n in names))
        self.assertNotIn("claude-opus-5", names)


if __name__ == "__main__":
    unittest.main()
