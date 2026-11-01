"""`swe setup --hub=<url>` and the wizard's hub step (#696)."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from swe.init import hub as hub_repo
from swe.setup import commands as sc
from swe.setup.wizard import _stage_hub, run_setup_wizard
from tests.test_init_hub import _hub_repo
from tests.test_setup_commands import _SetupHarness

GIT = shutil.which("git")


class TakeHubTest(unittest.TestCase):
    def test_both_spellings_and_the_last_one_wins(self):
        self.assertEqual(sc._take_hub(["--hub=a", "--json"]), (["--json"], "a"))
        self.assertEqual(sc._take_hub(["--hub", "a", "--apply"]), (["--apply"], "a"))
        self.assertEqual(sc._take_hub(["--hub=a", "--hub=b"]), ([], "b"))
        self.assertEqual(sc._take_hub(["--json"]), (["--json"], None))

    def test_a_hub_with_no_url_after_it_is_refused(self):
        for args in (["--hub"], ["--hub", "--json"]):
            with self.assertRaisesRegex(ValueError, "--hub=<url>"):
                sc._take_hub(args)


class _HubHarness(_SetupHarness):
    def setUp(self):
        super().setUp()
        # Registering marketplaces runs each harness's own CLI.
        patcher = patch("swe.init.hub.register_marketplaces", return_value=[])
        self.register = patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = Path(tmp.name)

    def _run_json(self, args):
        err = io.StringIO()
        with redirect_stderr(err):
            code, out, _ = self._run(args + ["--json"])
        return code, out, err.getvalue()


@unittest.skipUnless(GIT, "git not installed")
class SetupHubCloneTest(_HubHarness):
    def test_json_preview_clones_first_then_previews_the_hub(self):
        src = _hub_repo(self.scratch)
        code, out, err = self._run_json([f"--hub={src}"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)["data"]  # stdout is the one document
        self.assertEqual((self.home / ".swe" / "AGENTS.md").read_text(), "# from the hub\n")
        self.assertTrue((self.home / ".swe" / ".git").is_dir())
        self.assertEqual(data["hub"]["cloned"]["url"], str(src))
        self.assertEqual(data["hub"]["cloned"]["marketplaces"], [])
        self.assertFalse(data["hub"]["can_clone"])
        # The preview is of the cloned hub: its AGENTS.md is already there.
        self.assertTrue(data["instructions"]["agents_file_exists"])
        self.register.assert_called_once_with(self.home)

    def test_space_form_with_apply_clones_then_links(self):
        src = _hub_repo(self.scratch)
        (self.home / ".claude").mkdir()
        code, out, err = self._run_json(["--apply", "--hub", str(src)])
        self.assertEqual(code, 0, err)
        data = json.loads(out)["data"]
        self.assertEqual(data["hub"]["cloned"]["path"], str(self.home / ".swe"))
        link = self.home / ".claude" / "CLAUDE.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_text(), "# from the hub\n")
        self.assertTrue(any(r["result"] == "linked" for r in data["applied"]["links"]["rows"]))

    def test_text_mode_says_what_it_cloned(self):
        src = _hub_repo(self.scratch)
        self.register.return_value = [("claude", "mine", None),
                                      ("codex", "mine", "codex is not on PATH")]
        code, out, _ = self._run([f"--hub={src}"])
        self.assertEqual(code, 0, out)
        self.assertIn(f"Cloning {src} into ~/.swe", out)
        self.assertIn("Cloned.", out)
        self.assertIn("mine registered in claude", out)
        self.assertIn("mine not registered in codex: codex is not on PATH", out)
        # ~/.swe has a hub now, so the dry run no longer points at --hub.
        self.assertNotIn("swe setup --hub=<git url>", out)

    def test_an_unreachable_url_writes_nothing_and_keeps_stdout_clean(self):
        code, out, err = self._run_json([f"--hub={self.scratch / 'missing'}"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("can't reach", err)
        self.assertFalse((self.home / ".swe").exists())
        self.register.assert_not_called()

    def test_a_token_in_the_url_never_reaches_the_output(self):
        def fail(args, timeout, run):
            import subprocess
            return subprocess.CompletedProcess(
                args, 128, "", "fatal: could not read from https://me:tok@h/r.git")

        out, err = io.StringIO(), io.StringIO()
        with patch.object(hub_repo, "_git", fail), \
                redirect_stdout(out), redirect_stderr(err), \
                patch("sys.stdin.isatty", return_value=False):
            code = sc.cmd_setup(["--hub=https://me:tok@h/r.git"])
        self.assertEqual(code, 1)
        self.assertNotIn("tok", out.getvalue() + err.getvalue())
        self.assertIn("https://h/r.git", out.getvalue())

    def test_an_existing_quiver_is_refused_and_left_alone(self):
        src = _hub_repo(self.scratch)
        (self.home / ".swe").mkdir()
        (self.home / ".swe" / "AGENTS.md").write_text("mine\n")
        code, out, err = self._run_json(["--apply", f"--hub={src}"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("already has a hub", err)
        self.assertEqual((self.home / ".swe" / "AGENTS.md").read_text(), "mine\n")


class SetupHubRefusalTest(_HubHarness):
    def test_an_empty_url_is_refused(self):
        code, out, err = self._run_json(["--hub="])
        self.assertEqual(code, 1)
        self.assertIn("--hub needs a git URL", err)

    def test_hub_and_adopt_are_refused_together(self):
        code, out, err = self._run_json(["--apply", "--hub=x", "--adopt=/tmp/x"])
        self.assertEqual(code, 1)
        self.assertIn("pick one", err)

    def test_a_section_without_a_terminal_refuses_before_cloning(self):
        with patch.object(sc, "_clone_hub") as clone:
            code, out, _ = self._run(["mcp", "--hub=x"])
        self.assertEqual(code, 1)
        self.assertIn("requires an interactive terminal", out)
        clone.assert_not_called()

    def test_preview_says_whether_a_hub_can_be_cloned(self):
        code, out, err = self._run_json([])
        self.assertEqual(code, 0, err)
        hub = json.loads(out)["data"]["hub"]
        self.assertEqual(hub, {"can_clone": True, "demo_url": None, "cloned": None})
        (self.home / ".swe").mkdir()
        (self.home / ".swe" / "AGENTS.md").write_text("x")
        hub = json.loads(self._run_json([])[1])["data"]["hub"]
        self.assertFalse(hub["can_clone"])

    def test_dry_run_points_at_hub_only_while_there_is_none(self):
        _, out, _ = self._run([])
        self.assertIn("swe setup --hub=<git url>", out)
        self.assertIn(hub_repo.FORK_NOTE, out)
        self.assertNotIn("Demo hub", out)
        with patch.object(hub_repo, "DEMO_HUB_URL", "https://github.com/x/demo-hub"):
            _, out, _ = self._run([])
        self.assertIn("Demo hub: https://github.com/x/demo-hub", out)
        (self.home / ".swe").mkdir()
        (self.home / ".swe" / "AGENTS.md").write_text("x")
        _, out, _ = self._run([])
        self.assertNotIn("--hub", out)

    def test_interactive_hub_clones_then_runs_the_wizard(self):
        calls = []
        with patch.object(sc, "_clone_hub", side_effect=lambda *a, **k: calls.append("clone") or {}), \
                patch.object(sc, "run_setup_wizard",
                             side_effect=lambda **k: calls.append("wizard") or 0), \
                patch("sys.stdin.isatty", return_value=True):
            self.assertEqual(sc.cmd_setup(["--hub=x"]), 0)
        self.assertEqual(calls, ["clone", "wizard"])


class WizardHubStageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def _stage(self, answers, clone):
        answers = iter(answers)
        out = io.StringIO()
        with patch.object(hub_repo, "clone_and_register", clone), redirect_stdout(out):
            outcome = _stage_hub(1, 7, input_fn=lambda _prompt: next(answers),
                                 home=self.home, quick=False)
        return outcome, out.getvalue()

    def test_blank_starts_an_empty_hub(self):
        outcome, out = self._stage([""], clone=None)
        self.assertEqual(outcome.status, "skipped")
        self.assertIn(hub_repo.FORK_NOTE, out)
        self.assertNotIn("Demo hub", out)

    def test_demo_link_shows_only_once_it_is_set(self):
        with patch.object(hub_repo, "DEMO_HUB_URL", "https://github.com/x/demo-hub"):
            _, out = self._stage([""], clone=None)
        self.assertIn("Demo hub: https://github.com/x/demo-hub", out)

    def test_a_failed_clone_asks_again(self):
        def clone(home, url):
            if url == "bad":
                raise hub_repo.HubError("can't reach bad: not found")
            return {"url": url, "path": str(home / ".swe"),
                    "marketplaces": [{"harness": "claude", "marketplace": "mine", "error": None}]}

        outcome, out = self._stage(["bad", "good"], clone)
        self.assertEqual(outcome.status, "changed")
        self.assertEqual(outcome.changed, ("good",))
        self.assertIn("can't reach bad", out)
        self.assertIn("Cloned good into ~/.swe", out)
        self.assertIn("mine registered in claude", out)

    def test_a_failure_that_left_a_hub_behind_stops_asking(self):
        def clone(home, url):
            (home / ".swe").mkdir()
            (home / ".swe" / "AGENTS.md").write_text("x")
            raise hub_repo.HubError("~/.swe already has a hub")

        outcome, _ = self._stage(["bad"], clone)
        self.assertEqual(outcome.status, "attention")

    def test_the_wizard_asks_first_only_while_quiver_can_take_a_clone(self):
        seen = []

        def stage(key):
            def run(number, total, **_kwargs):
                seen.append((key, number, total))
                from swe.setup.wizard import StageOutcome
                return StageOutcome(key, key, "ready", "ok")
            return run

        keys = ("hub", "harnesses", "providers", "mcp", "skills", "report", "check")
        with patch.dict("swe.setup.wizard._STAGES", {k: stage(k) for k in keys}, clear=True), \
                redirect_stdout(io.StringIO()):
            run_setup_wizard(input_fn=lambda _p: "", home=self.home)
            self.assertEqual(seen[0], ("hub", 1, 7))
            seen.clear()
            (self.home / ".swe").mkdir()
            (self.home / ".swe" / "AGENTS.md").write_text("x")
            run_setup_wizard(input_fn=lambda _p: "", home=self.home)
            self.assertEqual([k for k, *_ in seen], list(keys[1:]))


if __name__ == "__main__":
    unittest.main()
