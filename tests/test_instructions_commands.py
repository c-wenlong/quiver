"""Command-level instruction compiler behavior, using a throwaway home."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.instructions.commands import cmd_instructions


class InstructionsCommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.home / ".quiver" / "AGENTS_MD"
        self.source.mkdir(parents=True)
        (self.source / "index.txt").write_text("rules.md\n", encoding="utf-8")
        (self.source / "rules.md").write_text("# Rules\n\nBe concise.\n", encoding="utf-8")

    def run_command(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cmd_instructions(args)
        return code, output.getvalue(), errors.getvalue()

    def test_build_then_unchanged_and_check(self):
        self.assertEqual(self.run_command(["check"])[0], 1)
        self.assertFalse((self.home / ".quiver" / "AGENTS.md").exists())
        code, output, errors = self.run_command(["build"])
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("Built", output)
        self.assertIn("Unchanged", self.run_command(["build"])[1])
        self.assertEqual(self.run_command(["check"])[0], 0)
        (self.source / "rules.md").write_text("# Changed\n", encoding="utf-8")
        self.assertEqual(self.run_command(["check"])[0], 1)

    def test_source_error_returns_one_without_traceback(self):
        (self.source / "index.txt").write_text("missing.md\n", encoding="utf-8")
        for sub in ("build", "check"):
            with self.subTest(sub=sub):
                code, output, errors = self.run_command([sub])
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertIn("missing.md", errors)

    def test_unknown_command_and_extra_args_fail(self):
        for args in (["unknown"], ["build", "--json"], ["watch", "extra"]):
            with self.subTest(args=args):
                code, _, errors = self.run_command(args)
                self.assertEqual(code, 1)
                self.assertTrue(errors)

    def test_help_forms(self):
        for args in ([], ["help"], ["--help"], ["-h"], ["build", "--help"]):
            with self.subTest(args=args):
                code, output, errors = self.run_command(args)
                self.assertEqual((code, errors), (0, ""))
                self.assertIn("swe instructions watch", output)

    def test_watch_reports_failure_recovery_and_interrupt(self):
        def fake_watch(*, on_result, on_error):
            on_error(ValueError("missing chapter"))
            on_result(True)
            on_result(False)
            raise KeyboardInterrupt

        with patch("quiver.instructions.commands.watcher.watch", side_effect=fake_watch):
            code, output, errors = self.run_command(["watch"])
        self.assertEqual(code, 0)
        self.assertIn("missing chapter", errors)
        self.assertIn("Built", output)
        self.assertIn("Unchanged", output)
        self.assertIn("Stopped", output)

    def test_os_error_and_build_interrupt(self):
        with patch("quiver.instructions.commands.compiler.build", side_effect=OSError("denied")):
            code, _, errors = self.run_command(["build"])
        self.assertEqual(code, 1)
        self.assertIn("denied", errors)
        with patch("quiver.instructions.commands.compiler.build", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_command(["build"])[0], 130)

    def test_top_level_dispatch_and_help(self):
        from quiver.cli import main

        for args in (["swe", "instructions", "build"], ["swe", "instructions", "--help"]):
            with patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
