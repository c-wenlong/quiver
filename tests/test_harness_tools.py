import subprocess
import unittest
from unittest.mock import patch

from swe.harness.tools import extract_version_number, live_version


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
            "Failed to change directory to /Users/dev",
            "",
            "not a version line",
        ]
        for raw in bad:
            with self.subTest(raw=raw):
                self.assertIsNone(extract_version_number(raw))


class LiveVersionTest(unittest.TestCase):
    def _run(self, outputs):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            out = outputs.get(argv[1], "")
            return subprocess.CompletedProcess(argv, 0 if out else 1, stdout=out, stderr="")

        with patch("swe.harness.tools.subprocess.run", side_effect=fake_run):
            version = live_version("claude")
        return version, calls

    def test_tries_version_flag_first(self):
        version, calls = self._run({"--version": "2.1.126 (Claude Code)\n"})
        self.assertEqual(version, "2.1.126")
        self.assertEqual(calls, [["claude", "--version"]])

    def test_never_sends_bare_version_subcommand(self):
        # `claude version` is a prompt to Claude Code and starts a session.
        version, calls = self._run({})
        self.assertIsNone(version)
        self.assertTrue(calls)
        self.assertNotIn("version", [argv[1] for argv in calls])

    def test_falls_back_to_short_flags(self):
        version, calls = self._run({"-V": "3.0.73\n"})
        self.assertEqual(version, "3.0.73")
        self.assertEqual([argv[1] for argv in calls], ["--version", "-v", "-V"])


if __name__ == "__main__":
    unittest.main()
