"""Tests for swe shell completion engine and script generation."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class CompletionEngineTest(unittest.TestCase):
    def test_top_level_completions(self):
        from quiver.completion import get_completions

        comps = get_completions([])
        names = [c for c, _ in comps]
        self.assertIn("list", names)
        self.assertIn("use", names)
        self.assertIn("session", names)
        self.assertIn("autocomplete", names)
        # __complete should NOT appear
        self.assertNotIn("__complete", names)

    def test_partial_subcommand_filter(self):
        from quiver.completion import get_completions

        comps = get_completions(["s"])
        names = [c for c, _ in comps]
        self.assertIn("session", names)
        self.assertIn("star", names)
        self.assertIn("skills", names)
        self.assertNotIn("list", names)

    def test_use_returns_tool_completions(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
            "codex": {"description": "Codex CLI", "aliases": ["cx"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            comps = get_completions(["use", ""])
        names = [c for c, _ in comps]
        self.assertIn("claude", names)
        self.assertIn("codex", names)
        self.assertIn("cc", names)
        self.assertIn("cx", names)

    def test_star_returns_tool_completions(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            comps = get_completions(["star", ""])
        names = [c for c, _ in comps]
        self.assertIn("claude", names)
        self.assertIn("cc", names)

    def test_partial_tool_filter(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
            "cline": {"description": "Cline", "aliases": ["cl"]},
            "codex": {"description": "Codex CLI", "aliases": ["cx"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            comps = get_completions(["use", "cl"])
        names = [c for c, _ in comps]
        self.assertIn("claude", names)
        self.assertIn("cline", names)
        self.assertIn("cl", names)
        self.assertNotIn("codex", names)

    def test_list_flag_completions(self):
        from quiver.completion import get_completions

        comps = get_completions(["list", "--"])
        names = [c for c, _ in comps]
        self.assertIn("--refresh", names)

    def test_list_tag_completions(self):
        fake_registry = {
            "claude": {"description": "Claude", "aliases": [], "tags": ["coding", "byok"]},
            "codex": {"description": "Codex", "aliases": [], "tags": ["coding"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            comps = get_completions(["list", ""])
        names = [c for c, _ in comps]
        self.assertIn("coding", names)
        self.assertIn("byok", names)

    def test_session_flag_completions(self):
        from quiver.completion import get_completions

        comps = get_completions(["session", "--"])
        names = [c for c, _ in comps]
        self.assertIn("--search", names)

    def test_no_completions_for_unknown_context(self):
        from quiver.completion import get_completions

        comps = get_completions(["unknown_cmd", "arg1", "arg2"])
        self.assertEqual(comps, [])

    def test_use_with_extra_args_returns_nothing(self):
        from quiver.completion import get_completions

        comps = get_completions(["use", "claude", "extra"])
        self.assertEqual(comps, [])


class CompleteCommandTest(unittest.TestCase):
    """Test the hidden __complete command output format."""

    def _capture_complete(self, args):
        import io
        from quiver.cli import cmd_complete

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            cmd_complete(args)
        return mock_stdout.getvalue()

    def test_directive_line(self):
        output = self._capture_complete([])
        lines = output.strip().split("\n")
        self.assertEqual(lines[-1], ":4")

    def test_tab_separated_format(self):
        output = self._capture_complete([])
        lines = output.strip().split("\n")
        # At least one line should have a tab separator (candidate\tdescription)
        has_tab = any("\t" in line for line in lines[:-1])  # exclude :4
        self.assertTrue(has_tab)

    def test_use_completions_output(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            output = self._capture_complete(["use", ""])
        lines = output.strip().split("\n")
        # Should have claude, cc, then :4
        candidates = [l.split("\t")[0] for l in lines[:-1]]
        self.assertIn("claude", candidates)
        self.assertIn("cc", candidates)


class AutocompleteCommandTest(unittest.TestCase):
    """Test the autocomplete command script generation and injection."""

    def test_zsh_generates_script(self):
        from quiver.cli import cmd_autocomplete

        with tempfile.TemporaryDirectory() as tmp:
            completion_dir = Path(tmp) / "completions"
            zshrc = Path(tmp) / ".zshrc"

            fake_configs = {
                "zsh": {
                    "script": "# test zsh script",
                    "filename": "swe.zsh",
                    "profile": str(zshrc),
                    "profile_instructions": f"source {zshrc}",
                },
            }

            with (
                patch("quiver.paths.COMPLETION_DIR", completion_dir),
                patch("quiver.completion_scripts.SHELL_CONFIGS", fake_configs),
            ):
                result = cmd_autocomplete(["zsh"])

            self.assertEqual(result, 0)
            script_file = completion_dir / "swe.zsh"
            self.assertTrue(script_file.exists())
            self.assertIn("# test zsh script", script_file.read_text())

    def test_injection_idempotent(self):
        from quiver.cli import cmd_autocomplete

        with tempfile.TemporaryDirectory() as tmp:
            completion_dir = Path(tmp) / "completions"
            zshrc = Path(tmp) / ".zshrc"
            zshrc.write_text("# existing config\n")

            fake_configs = {
                "zsh": {
                    "script": "# test zsh script",
                    "filename": "swe.zsh",
                    "profile": str(zshrc),
                    "profile_instructions": f"source {zshrc}",
                },
            }

            with (
                patch("quiver.paths.COMPLETION_DIR", completion_dir),
                patch("quiver.completion_scripts.SHELL_CONFIGS", fake_configs),
            ):
                cmd_autocomplete(["zsh"])
                content_after_first = zshrc.read_text()
                cmd_autocomplete(["zsh"])
                content_after_second = zshrc.read_text()

            self.assertEqual(content_after_first, content_after_second)

    def test_unsupported_shell_returns_error(self):
        import io
        from quiver.cli import cmd_autocomplete

        with patch("sys.stdout", new_callable=io.StringIO):
            result = cmd_autocomplete(["tcsh"])
        self.assertEqual(result, 1)

    def test_no_args_returns_usage(self):
        import io
        from quiver.cli import cmd_autocomplete

        with patch("sys.stdout", new_callable=io.StringIO):
            result = cmd_autocomplete([])
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
