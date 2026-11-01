"""Command-level instruction compiler behavior, using a throwaway home."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.instructions.commands import cmd_instructions


class InstructionsCommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.home / ".swe" / "AGENTS_MD"
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
        self.assertFalse((self.home / ".swe" / "AGENTS.md").exists())
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
        for args in (["unknown"], ["watch", "extra"], ["watch", "--json"]):
            with self.subTest(args=args):
                code, _, errors = self.run_command(args)
                self.assertEqual(code, 1)
                self.assertTrue(errors)

    def test_check_json_reports_state(self):
        import json

        def document(args):
            code, output, errors = self.run_command(args)
            self.assertEqual(errors, "")
            return code, json.loads(output)["data"]

        code, doc = document(["check", "--json"])
        self.assertEqual(code, 1)
        self.assertTrue(doc["index_exists"])
        self.assertEqual([c["name"] for c in doc["chapters"]], ["rules.md"])
        self.assertTrue(doc["chapters"][0]["exists"])
        self.assertIsNone(doc["error"])
        self.assertTrue(doc["stale"])
        self.assertFalse(doc["output_exists"])

        self.run_command(["build", "--json"])
        code, doc = document(["check", "--json"])
        self.assertEqual((code, doc["stale"], doc["error"]), (0, False, None))
        self.assertTrue(doc["output_exists"] and doc["output_generated"])

        (self.source / "index.txt").write_text("missing.md\n", encoding="utf-8")
        code, doc = document(["check", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("missing.md", doc["error"])
        self.assertFalse(doc["chapters"][0]["exists"])

    def test_build_json_and_init(self):
        import json

        code, output, errors = self.run_command(["build", "--json"])
        self.assertEqual((code, errors), (0, ""))
        self.assertTrue(json.loads(output)["data"]["changed"])
        self.assertFalse(json.loads(self.run_command(["build", "--json"])[1])["data"]["changed"])

        self.assertEqual(self.run_command(["init"])[0], 1)
        (self.source / "index.txt").unlink()
        code, output, errors = self.run_command(["init", "--json"])
        self.assertEqual((code, errors), (0, ""))
        data = json.loads(output)["data"]
        self.assertTrue(data["created"])
        self.assertEqual(data["chapter"], "main.md")
        chapter = (self.source / "main.md").read_text(encoding="utf-8")
        # The earlier build left a generated AGENTS.md; init folds it back
        # into a chapter with the compiler comments stripped.
        self.assertIn("Be concise", chapter)
        self.assertNotIn("<!--", chapter)
        self.assertIn("main.md",
                      (self.source / "index.txt").read_text(encoding="utf-8"))

    def test_init_folds_existing_flat_file(self):
        output = self.home / ".swe" / "AGENTS.md"
        output.write_text("# My Rules\n\nBe brief.\n", encoding="utf-8")
        (self.source / "index.txt").unlink()
        (self.source / "rules.md").unlink()
        code, stdout, errors = self.run_command(["init"])
        self.assertEqual((code, errors), (0, ""))
        self.assertIn("AGENTS.md", stdout)
        self.assertIn("# My Rules", (self.source / "main.md").read_text(encoding="utf-8"))
        self.assertEqual(self.run_command(["check"])[0], 0)
        self.assertIn("Generated by swe instructions build",
                      output.read_text(encoding="utf-8"))

    def test_init_refuses_an_existing_index(self):
        code, output, errors = self.run_command(["init"])
        self.assertEqual((code, output), (1, ""))
        self.assertIn("index.txt", errors)

    def test_init_keeps_the_users_own_comments(self):
        output = self.home / ".swe" / "AGENTS.md"
        output.write_text(
            "# My Rules\n\n<!-- Keep this section in sync -->\n\nBe brief.\n",
            encoding="utf-8")
        (self.source / "index.txt").unlink()
        (self.source / "rules.md").unlink()
        code, _, _ = self.run_command(["init"])
        self.assertEqual(code, 0)
        chapter = (self.source / "main.md").read_text(encoding="utf-8")
        self.assertIn("<!-- Keep this section in sync -->", chapter)

    def test_init_refuses_a_symlinked_output_before_creating(self):
        output = self.home / ".swe" / "AGENTS.md"
        target = self.home / "elsewhere.md"
        target.write_text("# Elsewhere\n", encoding="utf-8")
        output.symlink_to(target)
        (self.source / "index.txt").unlink()
        (self.source / "rules.md").unlink()
        code, _, errors = self.run_command(["init"])
        self.assertEqual(code, 1)
        self.assertIn("symlink", errors.lower())
        self.assertFalse((self.source / "index.txt").exists())
        self.assertTrue(output.is_symlink())

    def test_init_refuses_to_overwrite_a_newer_save(self):
        output = self.home / ".swe" / "AGENTS.md"
        output.write_text("# Old\n", encoding="utf-8")
        (self.source / "index.txt").unlink()
        (self.source / "rules.md").unlink()

        def racing(build):
            def wrapped(home=None, *, check=False, expected=compiler._UNSET):
                if expected is not compiler._UNSET:
                    output.write_text("# Saved meanwhile\n", encoding="utf-8")
                return build(home, check=check, expected=expected)
            return wrapped

        from swe.instructions import commands, compiler
        with patch.object(commands.compiler, "build", racing(compiler.build)):
            code, _, errors = self.run_command(["init"])
        self.assertEqual(code, 1)
        self.assertIn("changed", errors)
        self.assertEqual(output.read_text(encoding="utf-8"), "# Saved meanwhile\n")

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

        with patch("swe.instructions.commands.watcher.watch", side_effect=fake_watch):
            code, output, errors = self.run_command(["watch"])
        self.assertEqual(code, 0)
        self.assertIn("missing chapter", errors)
        self.assertIn("Built", output)
        self.assertIn("Unchanged", output)
        self.assertIn("Stopped", output)

    def test_os_error_and_build_interrupt(self):
        with patch("swe.instructions.commands.compiler.build", side_effect=OSError("denied")):
            code, _, errors = self.run_command(["build"])
        self.assertEqual(code, 1)
        self.assertIn("denied", errors)
        with patch("swe.instructions.commands.compiler.build", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_command(["build"])[0], 130)

    def test_top_level_dispatch_and_help(self):
        from swe.cli import main

        for args in (["swe", "instructions", "build"], ["swe", "instructions", "--help"]):
            with patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
