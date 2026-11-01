"""`swe add --json` and `--no-replace`: registered by the app's form."""

import contextlib
import io
import json
import os
import pathlib
import shutil
import stat
import tempfile
import unittest
from unittest import mock


class AddJsonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        (self.home / "harness.json").write_text("{}")
        # A PATH carrying only a stub `mytool`, so is_installed answers
        # from a directory the test owns.
        self.bin = self.home / "bin"
        self.bin.mkdir()
        stub = self.bin / "mytool"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def run_add(self, *argv):
        from swe.harness.commands import cmd_add
        from swe.harness import registry as reg

        harness_file = self.home / "harness.json"
        out, err = io.StringIO(), io.StringIO()
        env = {"HOME": str(self.home), "PATH": str(self.bin)}
        with mock.patch.object(reg, "HARNESS_FILE", harness_file), \
             mock.patch.dict(os.environ, env), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_add(list(argv))
        return code, out.getvalue(), err.getvalue()

    def registry(self):
        file = self.home / "harness.json"
        return json.loads(file.read_text()) if file.exists() else {}

    def test_json_added(self):
        code, out, _ = self.run_add(
            "mytool", "mytool", "--description=x", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["data"]["action"], "added")
        self.assertTrue(doc["data"]["installed"])
        self.assertEqual(doc["data"]["name"], "mytool")
        # Stub not on PATH case: command absent from the bin dir.
        code, out, _ = self.run_add("other", "missingcmd", "--json")
        self.assertEqual(json.loads(out)["data"]["installed"], False)

    def test_blank_and_repeated_list_items_are_dropped(self):
        """`--aliases=` stored [""] and `--tags=a,,a` stored a blank and a
        repeat; `swe edit` and `swe add -i` already cleaned both."""
        code, _, _ = self.run_add("mytool", "mytool", "--aliases=", "--tags=a,,a, b")
        self.assertFalse(code)          # the text path returns None on success
        row = self.registry()["mytool"]
        self.assertEqual(row["aliases"], [])
        self.assertEqual(row["tags"], ["a", "b"])

    def test_add_harness_cleans_lists_handed_in(self):
        from swe.harness import registry as reg
        from swe.harness.commands import add_harness

        with mock.patch.object(reg, "HARNESS_FILE", self.home / "harness.json"), \
             mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": str(self.bin)}):
            reply = add_harness("mytool", "mytool", aliases=["mt", " ", "mt"],
                                tags=["x", ""])
        self.assertEqual(reply["aliases"], ["mt"])
        self.assertEqual(reply["tags"], ["x"])

    def test_json_updated_then_no_replace_refused(self):
        self.run_add("s", "s-cmd")
        code, out, _ = self.run_add("s", "s-cmd2", "--json")
        self.assertEqual(json.loads(out)["data"]["action"], "updated")
        code, out, err = self.run_add("s", "s-cmd3", "--no-replace", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")  # refusals go to stderr under --json
        self.assertIn("already registered", err)
        self.assertEqual(self.registry()["s"]["command"], "s-cmd2")

    def test_bad_names_refused(self):
        for name in ("-x", "a b", "two/name", "under_score?"):
            with self.subTest(name=name):
                code, _, _ = self.run_add(name, "cmd")
                self.assertEqual(code, 1)
                self.assertNotIn(name, self.registry())


    def test_alias_owned_by_another_harness_refused(self):
        self.run_add("claude", "claude")
        code, out, err = self.run_add("mycli", "mycli", "--aliases=claude", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("already used by 'claude'", err)
        self.assertNotIn("mycli", self.registry())

    def test_interactive_refuses_the_app_flags(self):
        for flags in (["--json"], ["--no-replace"]):
            with self.subTest(flags=flags):
                code, out, err = self.run_add("x", "-i", *flags)
                self.assertEqual(code, 1)
                self.assertIn("can't be combined", out + err)

if __name__ == "__main__":
    unittest.main()
