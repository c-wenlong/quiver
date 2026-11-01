"""plan_providers.py: coding plans as Claude Code launch providers, kept
in step with the Mac app's InferenceCatalog."""

import re
import unittest
from pathlib import Path

from swe.remote import launch_provider as lp
from swe.remote import plan_providers

SWIFT = (Path(__file__).resolve().parents[1] / "app" / "mac" / "Sources"
         / "QuiverCore" / "Providers" / "InferencePlans.swift")
SECRET = "plan-key-test-not-real"


def _swift_plans() -> tuple:
    src = SWIFT.read_text(encoding="utf-8")
    body = src[src.index("public static let all"):]
    body = body[:body.index("\n    ]\n")]
    out = []
    for block in re.split(r"\n\s*InferencePlan\(", body)[1:]:
        def get(k):
            return re.search(k + r':\s*"([^"]*)"', block).group(1)
        var = re.search(r'claudeCodeKeyVar:\s*(token|"[^"]*")', block).group(1)
        model = re.search(r'model:\s*(nil|"[^"]*")', block).group(1)
        out.append((get("id"), get("name"), get("keyName"),
                    "ANTHROPIC_AUTH_TOKEN" if var == "token" else var.strip('"'),
                    None if model == "nil" else model.strip('"'),
                    tuple(re.findall(r'endpoint\("([^"]*)",\s*"([^"]*)"', block))))
    return tuple(out)


class TestPlanProviders(unittest.TestCase):
    def test_table_matches_the_mac_catalog(self):
        self.assertEqual(plan_providers.PLANS, _swift_plans(),
                         "InferencePlans.swift changed: port it to "
                         "plan_providers.PLANS")

    def test_one_slug_per_plan_region(self):
        entries = plan_providers.claude_entries()
        self.assertEqual(len(entries),
                         sum(len(p[5]) for p in plan_providers.PLANS))
        self.assertIn("glm-coding-plan-china", entries)
        self.assertIn("ark-agent-plan", entries)
        caps = lp.capability("claude", {})
        for slug in entries:
            self.assertTrue(lp.SLUG_RE.fullmatch(slug), slug)
            self.assertIn(slug, caps)

    def test_plan_key_only_from_its_own_variable(self):
        env = lp.provider_env("claude", "glm-coding-plan-china",
                              {"GLM_CODING_PLAN_KEY": SECRET,
                               "ZAI_API_KEY": "payg"}, {})
        self.assertEqual(env, {
            "ANTHROPIC_AUTH_TOKEN": SECRET,
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_MODEL": "glm-5.3",
        })
        # A pay-as-you-go key never stands in for the plan's.
        self.assertIn("GLM_CODING_PLAN_KEY", lp.provider_error(
            "claude", "glm-coding-plan-china", {}, {"ZAI_API_KEY": "payg"}))

    def test_api_key_plans_blank_the_bearer(self):
        entry = plan_providers.claude_entries()["kimi-code-international"]
        self.assertEqual(entry["keys"], {"ANTHROPIC_API_KEY": ["KIMI_CODE_KEY"]})
        self.assertEqual(entry["set"]["ANTHROPIC_AUTH_TOKEN"], "")
        self.assertEqual(entry["label"], "Kimi Code (International)")

    def test_plan_with_no_model_sets_none(self):
        no_model = [p for p in plan_providers.PLANS if p[4] is None]
        self.assertTrue(no_model)
        pid, _n, _k, _v, _m, eps = no_model[0]
        slug = pid if len(eps) == 1 else next(
            s for s in plan_providers.claude_entries() if s.startswith(pid))
        self.assertNotIn("ANTHROPIC_MODEL",
                         plan_providers.claude_entries()[slug]["set"])


if __name__ == "__main__":
    unittest.main()
