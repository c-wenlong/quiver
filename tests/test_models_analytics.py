import unittest

from quiver.sessions.models_analytics import classify_provider


class ModelsAnalyticsTest(unittest.TestCase):
    def test_classify_openai_models(self):
        self.assertEqual(classify_provider("gpt-4.1"), "openai")
        self.assertEqual(classify_provider("openai/gpt-4o"), "openai")

    def test_classify_anthropic_models(self):
        self.assertEqual(classify_provider("claude-sonnet-4"), "anthropic")
        self.assertEqual(classify_provider("anthropic/claude-opus-4"), "anthropic")

    def test_classify_google_models(self):
        self.assertEqual(classify_provider("gemini-2.5-pro"), "google")

    def test_classify_unknown_returns_other(self):
        self.assertEqual(classify_provider("totally-custom-model"), "other")


if __name__ == "__main__":
    unittest.main()
