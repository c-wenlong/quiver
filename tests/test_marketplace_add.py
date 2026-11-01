"""`swe plugins marketplace add`: validation, per-harness argv, reports."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from swe.plugins.actions import (ActionError, marketplace_add,
                                    marketplace_source)


class MarketplaceAddTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, results):
        """A `run` stub replaying (returncode, out, err) or exceptions."""
        calls = []
        queue = list(results)

        def fake(argv, **kw):
            calls.append(argv)
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            code, out, err = item
            return subprocess.CompletedProcess(argv, code, out, err)

        return fake, calls

    def test_argv_per_harness(self):
        run, calls = self.run_with([(0, "added\n", ""), (0, "done\n", "")])
        result = marketplace_add(self.home, "owner/repo",
                                 ["claude", "codex"], run=run)
        self.assertEqual(calls[0], ["claude", "plugin", "marketplace", "add",
                                    "--scope", "user", "owner/repo"])
        self.assertEqual(calls[1], ["codex", "plugin", "marketplace", "add",
                                    "owner/repo"])
        self.assertTrue(all(r["ok"] for r in result["results"]))
        self.assertEqual(result["source"], "owner/repo")

    def test_repo_at_ref_and_https_and_ssh_accepted(self):
        for source in ("owner/repo@v1.2", "https://git.example.com/x/m.git",
                       "git@git.example.com:owner/repo.git"):
            with self.subTest(source=source):
                self.assertEqual(marketplace_source(source, self.home), source)

    def test_local_dir_expands_to_absolute(self):
        folder = self.home / "market"
        folder.mkdir()
        self.assertEqual(marketplace_source(str(folder), self.home),
                         str(folder.resolve()))

    def test_bad_sources_refused(self):
        for source in ("-x", "foo", "a/b/c", str(self.home / "nope"), "", ):
            with self.subTest(source=source):
                with self.assertRaises(ActionError):
                    marketplace_source(source, self.home)

    def test_one_fails_the_other_succeeds(self):
        run, _ = self.run_with([(0, "ok\n", ""), (2, "", "auth failed\n")])
        result = marketplace_add(self.home, "owner/repo",
                                 ["claude", "codex"], run=run)
        self.assertEqual([r["ok"] for r in result["results"]], [True, False])
        self.assertEqual(result["results"][1]["detail"], "auth failed")

    def test_missing_cli_reports(self):
        run, _ = self.run_with([FileNotFoundError("no claude")])
        result = marketplace_add(self.home, "owner/repo", ["claude"], run=run)
        self.assertFalse(result["results"][0]["ok"])
        self.assertIn("not on PATH", result["results"][0]["detail"])

    def test_url_credentials_never_reach_detail(self):
        run, _ = self.run_with([(1, "",
                                 "failed https://u:secret@host/repo\n")])
        result = marketplace_add(self.home, "owner/repo", ["claude"], run=run)
        self.assertNotIn("secret", result["results"][0]["detail"])
        self.assertIn("host/repo", result["results"][0]["detail"])

    def test_path_looking_sources_must_be_folders(self):
        for source in ("./plugins", "~/nope/market", "/no/such/dir"):
            with self.subTest(source=source):
                with self.assertRaises(ActionError):
                    marketplace_source(source, self.home)

    def test_a_bad_harness_runs_nothing(self):
        run, calls = self.run_with([(0, "ok\n", "")])
        with self.assertRaises(ActionError):
            marketplace_add(self.home, "owner/repo", ["claude", "gemini"], run=run)
        self.assertEqual(calls, [])

    def test_https_source_token_never_printed(self):
        run, calls = self.run_with([(0, "ok\n", "")])
        result = marketplace_add(self.home, "https://u:tok@host/x/m.git", ["claude"], run=run)
        self.assertIn("https://u:tok@host/x/m.git", calls[0])
        self.assertNotIn("tok", result["source"])

    def test_unknown_harness_refused(self):
        with self.assertRaises(ActionError):
            marketplace_add(self.home, "owner/repo", ["gemini"])


class MarketplaceCliTest(unittest.TestCase):
    """cmd_plugins dispatch: usage, --json shape, exit codes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, patch_run=None):
        import contextlib
        import io
        import os
        from unittest import mock
        from swe.plugins.commands import cmd_plugins

        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.dict(os.environ, {"HOME": str(self.home)}),
                   mock.patch.object(Path, "home", return_value=self.home)]
        if patch_run is not None:
            patches.append(mock.patch("subprocess.run", patch_run))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = cmd_plugins(["marketplace", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_json_shape_and_exit(self):
        code, out, _ = self.run_cli(
            "add", "owner/repo", "--harness=claude", "--json",
            patch_run=lambda *a, **k: subprocess.CompletedProcess(
                a[0], 0, "ok\n", ""))
        self.assertEqual(code, 0)
        import json
        doc = json.loads(out)
        self.assertEqual(doc["data"]["source"], "owner/repo")
        self.assertEqual(doc["data"]["results"][0]["harness"], "claude")

    def test_refused_source_never_runs(self):
        code, _, err = self.run_cli("add", "--bogus", "--harness=claude")
        self.assertEqual(code, 1)
        code, out, err = self.run_cli("add", "owner/repo", "--json")
        self.assertEqual(code, 1)
        self.assertIn("--harness", err)

    def test_partial_failure_exits_one(self):
        seq = iter([(0, "ok\n", ""), (1, "", "bad\n")])

        def fake(argv, **kw):
            code, out, err = next(seq)
            return subprocess.CompletedProcess(argv, code, out, err)

        code, out, _ = self.run_cli(
            "add", "owner/repo", "--harness=claude,codex", patch_run=fake)
        self.assertEqual(code, 1)
        self.assertIn("✗", out)


if __name__ == "__main__":
    unittest.main()
