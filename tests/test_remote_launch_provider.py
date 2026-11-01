"""launch_provider.py: which provider variables a launch may inject."""

import os
import unittest

from swe.remote import launch_provider as lp

from tests._tempdir import temp_dir

SECRET = "sk-test-not-a-real-key-0123456789"


class TestCapability(unittest.TestCase):
    def test_fallback_for_known_harnesses(self):
        self.assertIn("groq", lp.capability("opencode", {}))
        self.assertIn("anthropic", lp.capability("claude", {}))
        self.assertIn("openrouter", lp.capability("pi", {}))
        self.assertIsNone(lp.capability("codex", {}))

    def test_every_fallback_entry_is_well_formed(self):
        for harness, table in lp.PROVIDER_FALLBACK.items():
            caps = lp.capability(harness, {})
            self.assertEqual(set(caps), set(table), harness)
            for slug in table:
                self.assertTrue(lp.SLUG_RE.fullmatch(slug), slug)

    def test_registry_merges_over_fallback(self):
        reg = {"opencode": {"capabilities": {"provider": {"providers": {
            "groq": None,
            "myproxy": {"keys": {"OPENAI_API_KEY": "MYPROXY_API_KEY"},
                        "label": "My proxy"},
        }}}}}
        caps = lp.capability("opencode", reg)
        self.assertNotIn("groq", caps)
        self.assertEqual(caps["myproxy"]["keys"],
                         {"OPENAI_API_KEY": ["MYPROXY_API_KEY"]})
        self.assertEqual(lp.label("myproxy", caps["myproxy"]), "My proxy")
        self.assertIn("openai", caps)

    def test_registry_declares_a_new_harness(self):
        reg = {"crush": {"capabilities": {"provider": {"providers": {
            "groq": {"keys": {"GROQ_API_KEY": ["GROQ_API_KEY"]}}}}}}}
        self.assertEqual(list(lp.capability("crush", reg)), ["groq"])

    def test_supported_false_switches_it_off(self):
        reg = {"claude": {"capabilities": {"provider": {"supported": False}}}}
        self.assertIsNone(lp.capability("claude", reg))

    def test_malformed_and_reserved_entries_are_ignored(self):
        reg = {"x": {"capabilities": {"provider": {"providers": {
            "bad1": "nope",
            "bad2": {"keys": {}},
            "bad3": {"keys": {"PATH": ["A_KEY"]}},
            "bad4": {"keys": {"A": ["A_KEY"]}, "set": {"DYLD_X": "1"}},
            "bad5": {"keys": {"A": ["A_KEY"]}, "set": {"B": "x\ny"}},
            "bad6": {"keys": {"A": ["not a name"]}},
            "Bad Slug": {"keys": {"A": ["A_KEY"]}},
            "good": {"keys": {"A": ["A_KEY"]}},
        }}}}}
        self.assertEqual(list(lp.capability("x", reg)), ["good"])

    def test_garbage_registry_never_raises(self):
        for reg in ({"claude": "x"}, {"claude": {"capabilities": []}},
                    {"claude": {"capabilities": {"provider": []}}}):
            self.assertIn("anthropic", lp.capability("claude", reg))


class TestProviderEnv(unittest.TestCase):
    def test_env_is_exactly_the_providers_variables(self):
        keys = {"GROQ_API_KEY": SECRET, "OPENAI_API_KEY": "other",
                "ANTHROPIC_API_KEY": "third"}
        self.assertEqual(lp.provider_env("opencode", "groq", keys, {}),
                         {"GROQ_API_KEY": SECRET})

    def test_renamed_source_and_first_set_alternative(self):
        env = lp.provider_env("opencode", "zai", {"ZAI_API_KEY": SECRET}, {})
        self.assertEqual(env, {"ZHIPU_API_KEY": SECRET})
        env = lp.provider_env("opencode", "kimi",
                              {"KIMI_API_KEY": "", "MOONSHOT_API_KEY": SECRET},
                              {})
        self.assertEqual(env, {"MOONSHOT_API_KEY": SECRET})

    def test_claude_base_url_entry_blanks_the_anthropic_key(self):
        env = lp.provider_env("claude", "deepseek",
                              {"DEEPSEEK_API_KEY": SECRET,
                               "ANTHROPIC_API_KEY": "must-not-leak"}, {})
        self.assertEqual(env, {
            "ANTHROPIC_AUTH_TOKEN": SECRET,
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_API_KEY": "",
        })

    def test_missing_key_is_refused_by_name_only(self):
        note = lp.provider_error("opencode", "groq", {},
                                 {"OPENAI_API_KEY": SECRET})
        self.assertIn("GROQ_API_KEY", note)
        self.assertNotIn(SECRET, note)
        with self.assertRaises(ValueError) as ctx:
            lp.provider_env("opencode", "groq", {"OPENAI_API_KEY": SECRET}, {})
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_undeclared_provider_and_harness_are_refused(self):
        self.assertIn("can't take the provider",
                      lp.provider_error("claude", "groq", {}))
        self.assertIn("no capabilities.provider",
                      lp.provider_error("codex", "groq", {}))
        self.assertEqual(lp.provider_error("codex", None, {}), "")

    def test_configured_lists_slugs_with_keys(self):
        keys = {"GROQ_API_KEY": SECRET, "ZAI_API_KEY": SECRET,
                "OPENAI_API_KEY": ""}
        self.assertEqual(lp.configured("opencode", keys, {}),
                         ["zai", "groq"])
        self.assertEqual(lp.configured("codex", keys, {}), [])


class TestReadKeys(unittest.TestCase):
    def test_reads_the_keys_file(self):
        path = temp_dir(self) / ".api_keys"
        path.write_text(f'# Groq\nexport GROQ_API_KEY="{SECRET}"\n')
        os.chmod(path, 0o600)
        self.assertEqual(lp.read_keys(path), {"GROQ_API_KEY": SECRET})

    def test_no_file_is_no_keys(self):
        self.assertEqual(lp.read_keys(temp_dir(self) / "none"), {})

    def test_folder_is_refused(self):
        with self.assertRaises(ValueError):
            lp.read_keys(temp_dir(self))


if __name__ == "__main__":
    unittest.main()
