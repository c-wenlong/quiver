import unittest

from quiver.harness.tools import extract_version_number


class ExtractVersionNumberTest(unittest.TestCase):
    def test_strips_tool_name_prefix(self):
        cases = {
            "codex-cli 0.144.1": "0.144.1",
            "forge 2.12.10": "2.12.10",
            "crush version v0.62.0": "0.62.0",
            "2.1.126 (Claude Code)": "2.1.126",
            "GitHub Copilot CLI 1.0.70.": "1.0.70",
            "Hermes Agent v0.12.0 (2026.4.30)": "0.12.0",
            "kimi, version 1.39.0": "1.39.0",
            "kiro-cli 2.13.0": "2.13.0",
            "vibe 2.8.1": "2.8.1",
            "tau 0.1.2": "0.1.2",
            "v0.1.42": "0.1.42",
            "Version: v0.1.42": "0.1.42",
            "grok 0.2.101 (5bc4b5dfadcf) [stable]": "0.2.101",
            "0.24.0 (commit b4381cdc)": "0.24.0",
            "0.0.1777026701-gd887d5 (released 2026-04-24)": "0.0.1777026701-gd887d5",
            "\x1b[36mCline CLI version: 2.17.0\x1b[0m": "2.17.0",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(extract_version_number(raw), expected)

    def test_rejects_errors_and_usage(self):
        bad = [
            "Error: unknown flag: --version",
            "Usage: mastracode --prompt <text> [options]",
            "Warning: could not connect to a running Ollama instance",
            "Failed to change directory to /Users/kai",
            "",
            "not a version line",
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertIsNone(extract_version_number(raw))


if __name__ == "__main__":
    unittest.main()
