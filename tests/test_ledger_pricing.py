"""ledger/pricing.py: LiteLLM entries, name resolution and cost."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.ledger import pricing
from swe.ledger.pricing import PricingCatalog, forms, load_catalog, rates_from_entry

REPO = Path(__file__).resolve().parents[1]

OPUS = {"input_cost_per_token": 5e-06, "output_cost_per_token": 2.5e-05,
        "cache_read_input_token_cost": 5e-07, "cache_creation_input_token_cost": 6.25e-06,
        "litellm_provider": "anthropic"}
LONG = {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06,
        "input_cost_per_token_above_200k_tokens": 3e-06,
        "output_cost_per_token_above_200k_tokens": 4e-06, "litellm_provider": "openai"}


def _catalog(entries, supplement=None):
    return PricingCatalog({k: rates_from_entry(v) for k, v in entries.items()}, supplement or {})


class TestEntries(unittest.TestCase):
    def test_anthropic_hour_writes_default_to_double_input(self):
        r = rates_from_entry(OPUS)
        self.assertEqual(r.cache_write_1h, 1e-05)
        self.assertEqual(r.reasoning, r.output)

    def test_non_chat_and_unpriced_entries_are_dropped(self):
        self.assertIsNone(rates_from_entry({**OPUS, "mode": "embedding"}))
        self.assertIsNone(rates_from_entry({"output_cost_per_token": 1}))

    def test_long_context(self):
        r = rates_from_entry(LONG)
        self.assertEqual(r.long_context.threshold, 200_000)
        self.assertAlmostEqual(r.cost(1, 1, 0, 0, 0, 0, long=True), 7e-06)
        self.assertAlmostEqual(r.cost(1, 1, 0, 0, 0, 0), 3e-06)

    def test_cost_splits_hour_writes(self):
        r = rates_from_entry(OPUS)
        got = r.cost(input=1, output=1, cache_read=1, cache_write=3, cache_write_1h=1, reasoning=0)
        self.assertAlmostEqual(got, 5e-06 + 2.5e-05 + 5e-07 + 2 * 6.25e-06 + 1e-05)


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.cat = _catalog(
            {"claude-opus-5": OPUS, "bedrock/us.anthropic.claude-haiku-4-5-v1:0": OPUS,
             "gpt-5.5": LONG},
            {"aliases": {"opus-latest": "claude-opus-5"},
             "tier_multipliers": {"claude-opus-5": {"fast": 2}},
             "default_tier_multipliers": {"priority": 2}},
        )

    def test_forms(self):
        self.assertEqual(forms("anthropic/claude-opus-4-6-20260205", True),
                         ["claude-opus-4-6-20260205", "claude-opus-4-6"])
        self.assertIn("gpt-5-5", forms("gpt-5.5", False))
        self.assertEqual(forms("swe-2-high", True, strip_effort=True)[-1], "swe-2")

    def test_dated_prefixed_dotted_and_aliased_names(self):
        for name in ("claude-opus-5", "anthropic/claude-opus-5-20261001", "Claude-Opus-5",
                     "claude-haiku-4-5", "gpt-5-5", "opus-latest", "claude-opus-5[1m]"):
            self.assertEqual(self.cat.resolve(name, None)[0], "priced", name)

    def test_fast_suffix_and_tier_multipliers(self):
        status, rates, tier = self.cat.resolve("claude-opus-5-fast", None)
        self.assertEqual((status, tier), ("priced", "fast"))
        self.assertAlmostEqual(rates.cost(1, 0, 0, 0, 0, 0, tier="fast"), 1e-05)
        _, gpt, _ = self.cat.resolve("gpt-5.5", "priority")
        self.assertAlmostEqual(gpt.cost(1, 0, 0, 0, 0, 0, tier="priority"), 2e-06)

    def test_provider_qualified_names_resolve_like_the_bare_ones(self):
        """OpenCode rows are logged ``provider/model``, with any slash of
        the model's own after the first one."""
        from swe.ledger.pricing import base_name, is_free
        from swe.webapp.pages import _brands

        for qualified, bare in (("github-copilot/gpt-5.5", "gpt-5.5"), ("anthropic/claude-opus-5", "claude-opus-5"),
                                ("zai-coding-plan/claude-opus-5", "claude-opus-5")):
            self.assertEqual(self.cat.resolve(qualified, None)[1], self.cat.resolve(bare, None)[1], qualified)
            self.assertEqual(base_name(qualified), base_name(bare))
        self.assertEqual(self.cat.resolve("xyz-co/unlisted", None)[0], "unpriced")
        for name in ("openrouter/nvidia/nemotron-3-ultra:free", "opencode/nemotron-3-ultra-free",
                     "openrouter/openrouter/free"):
            self.assertTrue(is_free(name), name)
        self.assertFalse(is_free("openai/gpt-5.5"))
        self.assertEqual(_brands.brand_for("zai-coding-plan/glm-5.1")["id"], "zai")
        self.assertEqual(_brands.brand_for("featherless/zai-org/GLM-5.2")["id"], "zai")
        self.assertEqual(base_name("groq/openai/gpt-oss-120b"), "gpt-oss-120b")

    def test_free_and_unpriced(self):
        self.assertEqual(self.cat.resolve("openrouter/some-model:free", None)[0], "free")
        self.assertEqual(self.cat.resolve("swe-2-max", None)[0], "unpriced")


class TestLoading(unittest.TestCase):
    def test_bundled_copies_match_the_mac_app(self):
        """The Python snapshot is a copy of the app's; refresh both together."""
        app = REPO / "app" / "mac" / "Sources" / "QuiverCore" / "Resources"
        if not app.is_dir():
            self.skipTest("no app/mac/ in this checkout")
        for name in ("pricing.json", "pricing-supplement.json"):
            self.assertEqual((app / name).read_bytes(),
                             (REPO / "src" / "swe" / "ledger" / name).read_bytes(), name)

    def test_offline_falls_back_to_the_snapshot_and_overrides_win(self):
        home = Path(tempfile.mkdtemp())
        cfg = home / ".swe" / "config"
        cfg.mkdir(parents=True)
        (cfg / "pricing.json").write_text(json.dumps(
            {"models": {"my-model": {"input": 1, "output": 2}, "bad": {"input": 1}}}))
        with patch.object(pricing.urllib.request, "urlopen", side_effect=OSError("offline")) as fetch:
            cat = load_catalog(home)
            load_catalog(home)
        # The failed attempt is remembered, so a reload doesn't retry it.
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(cat.resolve("claude-opus-5", None)[0], "priced")
        status, rates, _ = cat.resolve("my-model", None)
        self.assertEqual(status, "priced")
        self.assertAlmostEqual(rates.input, 1e-06)
        # A missing cache price is $0, as the app reads the same file.
        self.assertEqual((rates.cache_read, rates.cache_write), (0, 0))
        self.assertEqual(cat.resolve("bad", None)[0], "unpriced")

    def test_an_unsupported_override_shape_is_ignored(self):
        home = Path(tempfile.mkdtemp())
        cfg = home / ".swe" / "config"
        cfg.mkdir(parents=True)
        (cfg / "pricing.json").write_text(json.dumps({"models": [{"model": "x", "input": 1}]}))
        cat = load_catalog(home, download=False)
        self.assertEqual(cat.resolve("claude-opus-5", None)[0], "priced")

    def test_download_is_cached_for_a_day(self):
        home = Path(tempfile.mkdtemp())
        listing = {f"model-{i}": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}
                   for i in range(120)}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(listing).encode()

        with patch.object(pricing.urllib.request, "urlopen", return_value=Resp()) as fetch:
            cat = load_catalog(home)
            load_catalog(home)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(cat.resolve("model-7", None)[0], "priced")
        # The download replaces the snapshot rather than adding to it.
        self.assertEqual(cat.resolve("claude-opus-5", None)[0], "unpriced")

    def test_a_certificate_failure_retries_against_the_os_bundle(self):
        # python.org builds have no CA store of their own: the download
        # must retry against the OS bundle (still verified), not fall
        # back to the snapshot for good.
        import ssl
        import urllib.error

        from swe.remote import agent as agent_mod

        home = Path(tempfile.mkdtemp())
        listing = {f"model-{i}": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}
                   for i in range(120)}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(listing).encode()

        bundle = object()
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append(context)
            if context is None:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("no CA"))
            return Resp()

        with patch.object(agent_mod, "_fallback_ctx", None), \
             patch("swe.harness.rate_limits._verified_context", return_value=bundle), \
             patch.object(pricing.urllib.request, "urlopen", side_effect=urlopen):
            cat = load_catalog(home)
        self.assertEqual(calls, [None, bundle])
        self.assertEqual(cat.resolve("model-7", None)[0], "priced")

    def test_a_short_or_broken_download_keeps_the_snapshot(self):
        home = Path(tempfile.mkdtemp())

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"tiny": {}}'

        with patch.object(pricing.urllib.request, "urlopen", return_value=Resp()):
            cat = load_catalog(home)
        self.assertEqual(cat.resolve("claude-opus-5", None)[0], "priced")


if __name__ == "__main__":
    unittest.main()
