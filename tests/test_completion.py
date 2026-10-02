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
        self.assertIn("report", names)
        self.assertIn("config", names)
        self.assertIn("autocomplete", names)
        # __complete should NOT appear
        self.assertNotIn("__complete", names)

    def test_partial_subcommand_filter(self):
        from quiver.completion import get_completions

        comps = get_completions(["s"])
        names = [c for c, _ in comps]
        self.assertIn("session", names)
        self.assertIn("skills", names)
        self.assertNotIn("list", names)
        # star moved under `swe harness` / `swe hs`.
        self.assertNotIn("star", names)

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

    def test_hs_star_returns_tool_completions(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            comps = get_completions(["hs", "star", ""])
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

        short_comps = get_completions(["list", "-"])
        short_names = [c for c, _ in short_comps]
        self.assertIn("-n", short_names)

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
        self.assertIn("--search=", names)
        self.assertIn("--days=", names)
        self.assertIn("--start=", names)

    def test_report_and_config_subcommands(self):
        from quiver.completion import get_completions

        report = [name for name, _ in get_completions(["report", ""])]
        config = [name for name, _ in get_completions(["config", ""])]
        self.assertIn("daily", report)
        self.assertIn("followups", report)
        self.assertIn("set", config)
        self.assertIn("check", config)

    def test_setup_sections_and_flags(self):
        from quiver.completion import get_completions

        sections = [name for name, _ in get_completions(["setup", ""])]
        flags = [name for name, _ in get_completions(["setup", "--"])]
        self.assertEqual(
            sections,
            ["harnesses", "providers", "mcp", "skills", "report", "check"],
        )
        self.assertIn("--quick", flags)
        self.assertIn("--non-interactive", flags)

    def test_no_completions_for_unknown_context(self):
        from quiver.completion import get_completions

        comps = get_completions(["unknown_cmd", "arg1", "arg2"])
        self.assertEqual(comps, [])

    def test_use_with_extra_args_returns_nothing(self):
        from quiver.completion import get_completions

        comps = get_completions(["use", "claude", "extra"])
        self.assertEqual(comps, [])

    def test_primary_commands_cover_dispatch(self):
        # `swe <TAB>` must offer every non-alias command — find and
        # discover were missing while `harness` was listed twice.
        from quiver.cli import COMMANDS
        from quiver.completion import _PRIMARY_COMMANDS
        from quiver.harness.drift import NO_TOPIC_WHITELIST

        names = [name for name, _ in _PRIMARY_COMMANDS]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertIn(name, COMMANDS, msg=name)
        for cmd in COMMANDS:
            if cmd in NO_TOPIC_WHITELIST:
                self.assertNotIn(cmd, names, msg=f"alias {cmd} in primary list")
            else:
                self.assertIn(cmd, names, msg=f"{cmd} not completable")

    def test_mcp_subcommands_and_sync_flags(self):
        from quiver.completion import get_completions

        subs = [c for c, _ in get_completions(["mcp", ""])]
        for sub in ("discover", "list", "status", "sync", "diff", "edit",
                    "validate", "doctor", "help"):
            self.assertIn(sub, subs)
        flags = [c for c, _ in get_completions(["mcp", "sync", "--"])]
        for flag in ("--all", "--only=", "--except=", "--prune", "--dry-run"):
            self.assertIn(flag, flags)

    def test_find_and_skills_subcommands(self):
        from quiver.completion import get_completions

        topics = [c for c, _ in get_completions(["find", ""])]
        self.assertIn("skills", topics)
        self.assertIn("mcp", topics)
        skills = [c for c, _ in get_completions(["skills", ""])]
        for sub in ("tree", "scope", "link", "unlink", "move", "catalog"):
            self.assertIn(sub, skills)

    def test_nested_flag_tables(self):
        from quiver.completion import get_completions

        # `swe report followups --<TAB>` offers --status, not report's range flags
        flags = [c for c, _ in get_completions(["report", "followups", "--"])]
        self.assertIn("--status=open", flags)
        self.assertNotIn("--days=", flags)
        # `swe list edit --<TAB>` offers --reset, not --scope
        flags = [c for c, _ in get_completions(["list", "edit", "--"])]
        self.assertEqual(flags, ["--reset"])
        # `swe find mcp --<TAB>` drops -i (not supported for mcps)
        flags = [c for c, _ in get_completions(["find", "mcp", "--"])]
        self.assertNotIn("-i", flags)
        self.assertNotIn("--interactive", flags)
        self.assertIn("--scope=global", flags)

    def test_nested_subcommands(self):
        from quiver.completion import get_completions

        actions = [c for c, _ in get_completions(["report", "followup", ""])]
        self.assertIn("work", actions)
        self.assertIn("done", actions)
        catalog = [c for c, _ in get_completions(["skills", "catalog", ""])]
        self.assertIn("add", catalog)

    def test_mcp_sync_multi_tool_completion(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
            "codex": {"description": "Codex CLI", "aliases": ["cx"]},
            "cursor": {"description": "Cursor", "aliases": []},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            names = [c for c, _ in get_completions(["mcp", "sync", "claude", ""])]
        self.assertIn("codex", names)
        self.assertIn("cursor", names)
        # Already-typed sources are not re-offered.
        self.assertNotIn("claude", names)
        self.assertNotIn("cc", names)

    def test_mcp_list_tool_completion(self):
        fake_registry = {
            "claude": {"description": "Claude Code", "aliases": ["cc"]},
        }
        with patch("quiver.completion.load_registry", return_value=fake_registry):
            from quiver.completion import get_completions

            names = [c for c, _ in get_completions(["mcp", "list", ""])]
        self.assertIn("claude", names)


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

    def test_bash_script_uses_bash_syntax(self):
        from quiver.completion_scripts import BASH_SCRIPT

        self.assertIn("while IFS= read -r line", BASH_SCRIPT)
        self.assertNotIn("${(f)out}", BASH_SCRIPT)
        self.assertNotIn("lines[-1]", BASH_SCRIPT)

    def test_no_args_returns_usage(self):
        import io
        from quiver.cli import cmd_autocomplete

        with patch("sys.stdout", new_callable=io.StringIO):
            result = cmd_autocomplete([])
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
