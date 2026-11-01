"""The Pricing page's data: per-model rows, sources, billing and brands."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from swe.ledger.models import LedgerRecord, UsageEvent
from swe.ledger.pricing import (PricingCatalog, alias_target, base_name, load_catalog, rates_from_entry,
                                   remove_overrides, save_override)
from swe.ledger.store import LedgerStore
from swe.webapp.api import WebApp
from swe.webapp.pages import _brands, pricing
from swe.webapp.static import STATIC_DIR

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
        overrides={"gpt-x"}, swe={"grok-mine"})


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
        (home / ".swe" / "config").mkdir(parents=True)
        (home / ".swe" / "config" / "pricing.json").write_text(
            json.dumps({"models": {"my-model": {"input": 1, "output": 2}}}))
        cat = load_catalog(home, download=False)
        self.assertEqual(cat.resolve_entry("my-model", None)[1], "my-model")
        self.assertEqual(cat.source("my-model"), "yours")
        self.assertEqual(cat.source("claude-opus-5"), "litellm")


class TestOverrideWriter(unittest.TestCase):
    """config/pricing.json as the Mac app's PricingOverrides writes it."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.file = self.home / ".swe" / "config" / "pricing.json"

    def doc(self):
        return json.loads(self.file.read_text())

    def test_save_writes_the_apps_shape_and_keeps_the_rest(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"note": "mine", "models": {"old": {"input": 1, "output": 2},
                                                                    "odd": {"input": "x"}}}))
        save_override(self.home, "my-model", {"input": 0.5, "output": 1.5})
        doc = self.doc()
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["note"], "mine")
        self.assertEqual(doc["models"]["my-model"],
                         {"input": 0.5, "output": 1.5, "cache_read": 0.0, "cache_write": 0.0})
        self.assertEqual(set(doc["models"]), {"old", "odd", "my-model"})
        cat = load_catalog(self.home, download=False)
        self.assertEqual(cat.source("my-model"), "yours")
        self.assertAlmostEqual(cat.entries["my-model"].input, 0.5e-6)

    def test_a_renamed_key_leaves_in_the_same_write(self):
        save_override(self.home, "a", {"input": 1, "output": 1})
        save_override(self.home, "b", {"input": 2, "output": 2}, replacing="a")
        self.assertEqual(list(self.doc()["models"]), ["b"])

    def test_remove_drops_every_key_given(self):
        for key in ("gpt-4.1", "gpt-4-1", "keep"):
            save_override(self.home, key, {"input": 1, "output": 1})
        remove_overrides(self.home, ["gpt-4.1", "gpt-4-1"])
        self.assertEqual(list(self.doc()["models"]), ["keep"])

    def test_a_file_that_isnt_json_is_never_overwritten(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("{ hand edited")
        with self.assertRaises(ValueError):
            save_override(self.home, "x", {"input": 1, "output": 1})
        self.assertEqual(self.file.read_text(), "{ hand edited")

    def test_user_keys_and_base_name(self):
        cat = PricingCatalog({k: _rates(1e-6, 1e-6) for k in ("gpt-4.1", "gpt-4-1", "claude-x", "swe-2-high")},
                             overrides={"gpt-4.1", "gpt-4-1", "claude-x", "swe-2-high"})
        self.assertEqual(cat.user_keys("openai/gpt-4.1-2025-04-14"), ["gpt-4-1", "gpt-4.1"])
        self.assertEqual(cat.user_keys("swe-2-medium"), [])
        self.assertEqual(cat.user_keys("swe-2-high"), ["swe-2-high"])
        self.assertEqual(cat.user_keys("nothing"), [])
        # An alias the row resolves through is a saved price it shows.
        aliased = PricingCatalog({"claude-haiku-4-5": _rates(1e-6, 1e-6)}, {"aliases": {"model_private_11": "claude-haiku-4-5"}},
                                 overrides={"claude-haiku-4-5"})
        self.assertEqual(aliased.user_keys("MODEL_PRIVATE_11"), ["claude-haiku-4-5"])
        self.assertEqual(base_name("anthropic/claude-opus-5-20261001"), "claude-opus-5")
        self.assertEqual(base_name("gpt-5-high"), "gpt-5")


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

    def test_an_aliased_row_carries_its_real_name(self):
        self.add("MODEL_PRIVATE_11", 100, harness="devin")
        self.add("claude-opus-5", 10)
        body = self.build()
        self.assertEqual([g["id"] for g in body["groups"]], ["anthropic"])
        rows = {r["model"]: r for r in body["groups"][0]["models"]}
        self.assertEqual(rows["MODEL_PRIVATE_11"]["alias"], "claude-haiku-4-5")
        self.assertIsNone(rows["claude-opus-5"]["alias"])

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
            "glm-4.6": "zai", "MODEL_PRIVATE_11": "anthropic"})
        other = _brands.brand_for("mystery")
        self.assertEqual((other["color"], other["logo"]), ("#45454b", None))

    def test_the_table_mirrors_the_swift_one_row_for_row(self):
        import re
        path = (Path(__file__).resolve().parents[1] / "app" / "mac" / "Sources"
                / "SWECore" / "Models" / "ModelBrands.swift")
        if not path.exists():
            self.skipTest("no app/mac/ in this checkout")
        swift = path.read_text()
        ids = re.findall(r'\.init\(id: "([^"]+)"', swift)
        self.assertEqual([b[0] for b in _brands.BRANDS], ids)

    def test_an_alias_names_and_brands_an_opaque_id(self):
        # Devin logs Claude Haiku 4.5 as MODEL_PRIVATE_11; the bundled
        # supplement aliases it, so the row takes Anthropic's brand.
        self.assertEqual(_brands.resolved_name("MODEL_PRIVATE_11"), "claude-haiku-4-5")
        self.assertEqual(_brands.resolved_name("model_private_11"), "claude-haiku-4-5")
        self.assertIsNone(_brands.resolved_name("claude-opus-5"))
        self.assertIsNone(_brands.resolved_name("MODEL_PRIVATE_99"))
        self.assertEqual(_brands.brand_for("MODEL_PRIVATE_99")["id"], "other")
        self.assertEqual(alias_target("a", {"a": "b", "b": "c"}), "c")
        self.assertEqual(alias_target("openrouter/a-20260101", {"a": "b"}), "b")
        self.assertIsNone(alias_target("a", {"a": "A"}))

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

    def usage(self, model):
        """One event in the hub's ledger, with the log scan stubbed out so
        it is the only usage there is."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from swe.ledger.store import ledger_path_for

        scan = patch("swe.webapp.api.scan", return_value=SimpleNamespace(errors=[]))
        scan.start()
        self.addCleanup(scan.stop)
        store = LedgerStore(ledger_path_for(self.srv.home))
        store.upsert(LedgerRecord(key="k1", event=UsageEvent(
            harness="claude", account="/h", session="s", cwd=None, model=model,
            ts=datetime.now().timestamp() - 60, input=1_000_000)))
        store.db.commit()
        store.close()

    def row(self, body, model):
        return next(m for g in body["groups"] for m in g["models"] if m["model"] == model)

    def test_save_and_remove_a_price(self):
        self.usage("my-local-model")
        auth = {**self.auth(), "Origin": f"http://127.0.0.1:{self.srv.port}"}
        _, data = self.srv.request("GET", "/v1/app/pricing", headers=auth)
        row = self.row(json.loads(data), "my-local-model")
        self.assertEqual((row["spend_source"], row["key"]), ("unpriced", "my-local-model"))
        status, data = self.srv.request("POST", "/v1/app/pricing/save", headers=auth, body={
            "model": "my-local-model", "key": "my-local-model", "input": 0.5, "output": "1.5",
            "cache_read": "", "period": "month"})
        self.assertEqual(status, 200, data)
        row = self.row(json.loads(data)["page"], "my-local-model")
        self.assertEqual((row["source"], row["rates"]["input"], row["rates"]["output"]), ("yours", 0.5, 1.5))
        status, data = self.srv.request("POST", "/v1/app/pricing/remove", headers=auth,
                                        body={"model": "my-local-model"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.row(json.loads(data)["page"], "my-local-model")["spend_source"], "unpriced")
        status, _ = self.srv.request("POST", "/v1/app/pricing/remove", headers=auth, body={"model": "my-local-model"})
        self.assertEqual(status, 404)

    def test_bad_prices_are_refused_and_write_nothing(self):
        auth = {**self.auth(), "Origin": f"http://127.0.0.1:{self.srv.port}"}
        base = {"model": "m", "key": "m", "input": 1, "output": 1}
        for change, code in (({"input": -1}, "bad_rate"), ({"output": None}, "bad_rate"),
                             ({"input": "1e400"}, "bad_rate"), ({"cache_write": True}, "bad_rate"),
                             ({"key": " "}, "bad_key"), ({"key": "a\nb"}, "bad_key"), ({"model": ""}, "bad_model")):
            status, data = self.srv.request("POST", "/v1/app/pricing/save", headers=auth, body={**base, **change})
            self.assertEqual((status, json.loads(data)["error"]["code"]), (422, code), change)
        status, _ = self.srv.request("POST", "/v1/app/pricing/save", body=base,
                                     headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertFalse((self.srv.home / ".swe" / "config" / "pricing.json").exists())

    def test_bundles_carry_both_pages(self):
        status, body = self.srv.request("GET", "/pages.js")
        self.assertEqual(status, 200)
        for name in ("source-litellm.svg", "harness-openai.svg"):
            self.assertEqual(self.srv.request("GET", f"/{name}")[0], 200, name)


if __name__ == "__main__":
    unittest.main()
