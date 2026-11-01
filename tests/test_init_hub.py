"""`swe init --hub=<url>`: clone an existing hub as ~/.swe (#696)."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from swe.init import commands as init_commands
from swe.init.hub import (HubError, check_target, clone_hub, public_url,
                              register_marketplaces)

GIT = shutil.which("git")


def _git(*args, cwd=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _hub_repo(root: Path) -> Path:
    """A local repository shaped like a hub, usable as a clone URL."""
    src = root / "hub-src"
    (src / "plugins" / "mine" / ".claude-plugin").mkdir(parents=True)
    (src / "plugins" / "mine" / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "mine", "plugins": []}\n')
    (src / "AGENTS.md").write_text("# from the hub\n")
    _git("init", "-q", "-b", "main", cwd=src)
    _git("add", "-A", cwd=src)
    _git("commit", "-q", "-m", "hub", cwd=src)
    return src


class CheckTargetTest(unittest.TestCase):
    def test_absent_empty_and_local_state_are_fine_a_hub_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            check_target(home)
            (home / ".swe").mkdir()
            check_target(home)
            # What the app, the remote hub and swe write on their own.
            (home / ".swe" / "data" / "remote").mkdir(parents=True)
            (home / ".swe" / "data" / "remote" / "devices.json").write_text("{}")
            (home / ".swe" / "cache").mkdir()
            check_target(home)
            (home / ".swe" / "AGENTS.md").write_text("x")
            with self.assertRaisesRegex(HubError, "already has a hub .*AGENTS.md"):
                check_target(home)

    def test_a_git_repository_is_a_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe" / ".git").mkdir(parents=True)
            with self.assertRaisesRegex(HubError, "it has .git"):
                check_target(home)

    def test_a_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe").write_text("x")
            with self.assertRaisesRegex(HubError, "a file, not a folder"):
                check_target(home)

    def test_a_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "elsewhere").mkdir()
            (home / ".swe").symlink_to(home / "elsewhere")
            with self.assertRaisesRegex(HubError, "symlink"):
                check_target(home)


class PublicUrlTest(unittest.TestCase):
    def test_a_token_in_the_url_is_dropped(self):
        self.assertEqual(public_url("https://me:ghp_x@github.com/me/hub.git"),
                         "https://github.com/me/hub.git")
        self.assertEqual(public_url("git@github.com:me/hub.git"), "git@github.com:me/hub.git")

    def test_a_malformed_url_still_loses_its_token(self):
        self.assertNotIn("secret", public_url("https://me:secret@[broken/repo.git"))


class CloneHubTest(unittest.TestCase):
    def test_unreachable_url_writes_nothing(self):
        fail = mock.Mock(return_value=subprocess.CompletedProcess(
            [], 128, "", "fatal: repository not found\n"))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaisesRegex(HubError, "can't reach .*repository not found"):
                clone_hub(home, "https://example.invalid/hub.git", run=fail)
            self.assertEqual(fail.call_count, 1)  # ls-remote only, never clone
            self.assertFalse((home / ".swe").exists())

    def test_option_shaped_url_is_refused(self):
        run = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HubError):
                clone_hub(Path(tmp), "--upload-pack=touch x", run=run)
        run.assert_not_called()

    def test_errors_never_print_the_token(self):
        fail = mock.Mock(return_value=subprocess.CompletedProcess(
            [], 128, "", "fatal: could not read https://me:tok@h/r.git\n"))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HubError) as caught:
                clone_hub(Path(tmp), "https://me:tok@h/r.git", run=fail)
        self.assertNotIn("tok", str(caught.exception))

    def test_a_timed_out_clone_leaves_nothing_behind(self):
        def run(argv, **kw):
            if argv[1] == "ls-remote":
                return subprocess.CompletedProcess(argv, 0, "", "")
            Path(argv[-1]).mkdir(parents=True)
            (Path(argv[-1]) / "partial").write_text("x")
            raise subprocess.TimeoutExpired(argv, 1)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaisesRegex(HubError, "took longer"):
                clone_hub(home, "https://h/r.git", run=run)
            self.assertEqual(list(home.iterdir()), [])

    def test_an_unreadable_hub_is_a_refusal_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".swe").mkdir()
            with mock.patch.object(Path, "iterdir", side_effect=PermissionError(13, "Permission denied")):
                with self.assertRaisesRegex(HubError, "can't read ~/.swe"):
                    check_target(home)

    @unittest.skipUnless(GIT, "git not installed")
    def test_clones_a_real_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _hub_repo(root)
            home = root / "home"
            home.mkdir()
            clone_hub(home, str(src))
            self.assertEqual((home / ".swe" / "AGENTS.md").read_text(), "# from the hub\n")
            remote = subprocess.run(["git", "-C", str(home / ".swe"), "remote", "get-url",
                                     "origin"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(remote, str(src))


@unittest.skipUnless(GIT, "git not installed")
class CloneAroundLocalStateTest(unittest.TestCase):
    """A ~/.swe holding only what the app, the hub and swe wrote on their
    own takes the clone around that state, and never overwrites any of it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.src = _hub_repo(self.root)
        self.home = self.root / "home"
        self.hub_dir = self.home / ".swe"
        (self.hub_dir / "data" / "remote").mkdir(parents=True)
        (self.hub_dir / "data" / "remote" / "devices.json").write_text('{"paired": 1}')
        (self.hub_dir / "cache").mkdir()
        (self.hub_dir / "cache" / "session_cache.json").write_text("{}")
        # A folder the hub has too: the clone goes inside it.
        (self.hub_dir / "plugins").mkdir()
        (self.hub_dir / "plugins" / "local-only.txt").write_text("mine")

    def _listing(self) -> dict:
        return {str(p.relative_to(self.home)): (p.read_text() if p.is_file() else None)
                for p in sorted(self.home.rglob("*")) if ".git" not in p.parts}

    def test_the_clone_lands_beside_local_state(self):
        clone_hub(self.home, str(self.src))
        self.assertEqual((self.hub_dir / "AGENTS.md").read_text(), "# from the hub\n")
        self.assertTrue((self.hub_dir / ".git").is_dir())
        self.assertTrue((self.hub_dir / "plugins" / "mine" / ".claude-plugin"
                         / "marketplace.json").is_file())
        self.assertEqual((self.hub_dir / "data" / "remote" / "devices.json").read_text(),
                         '{"paired": 1}')
        self.assertEqual((self.hub_dir / "plugins" / "local-only.txt").read_text(), "mine")
        self.assertEqual((self.hub_dir / "cache" / "session_cache.json").read_text(), "{}")
        # git sees the hub's files as checked out, not modified.
        status = subprocess.run(["git", "-C", str(self.hub_dir), "status", "--porcelain",
                                 "--untracked-files=no"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")
        self.assertEqual([p.name for p in self.home.iterdir()], [".swe"])  # no staging left

    def test_a_path_on_both_sides_stops_before_anything_moves(self):
        clash = self.hub_dir / "plugins" / "mine" / ".claude-plugin"
        clash.mkdir(parents=True)
        (clash / "marketplace.json").write_text("local copy")
        before = self._listing()
        with self.assertRaisesRegex(
                HubError, r"~/.swe/plugins/mine/.claude-plugin/marketplace.json is on "
                          r"this machine and in the hub"):
            clone_hub(self.home, str(self.src))
        self.assertEqual(self._listing(), before)
        self.assertFalse((self.hub_dir / ".git").exists())

    def test_a_folder_against_a_file_is_a_collision_too(self):
        (self.hub_dir / "plugins" / "mine").write_text("a file where the hub has a folder")
        before = self._listing()
        with self.assertRaisesRegex(HubError, r"~/.swe/plugins/mine is on this machine"):
            clone_hub(self.home, str(self.src))
        self.assertEqual(self._listing(), before)

    def test_a_failed_move_puts_back_what_it_moved(self):
        real_rename = Path.rename
        calls = []

        def flaky(path, target):
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError(18, "Cross-device link")
            return real_rename(path, target)

        before = self._listing()
        with mock.patch.object(Path, "rename", flaky):
            with self.assertRaisesRegex(HubError, "can't move the clone into ~/.swe"):
                clone_hub(self.home, str(self.src))
        self.assertEqual(self._listing(), before)
        self.assertFalse((self.hub_dir / ".git").exists())
        self.assertEqual([p.name for p in self.home.iterdir()], [".swe"])

    def test_a_move_that_cant_be_undone_names_what_is_left_and_keeps_the_rest(self):
        real_rename = Path.rename
        calls = []

        def flaky(path, target):
            calls.append(path.name)
            # The first move (.git) lands, the second fails, and putting
            # .git back fails too.
            if len(calls) >= 2:
                raise OSError(18, "Cross-device link")
            return real_rename(path, target)

        with mock.patch.object(Path, "rename", flaky):
            with self.assertRaises(HubError) as caught:
                clone_hub(self.home, str(self.src))
        message = str(caught.exception)
        self.assertIn("couldn't be moved back and are still there: ~/.swe/.git", message)
        staging = [p for p in self.home.iterdir() if p.name.startswith(".swe-clone-")]
        self.assertEqual(len(staging), 1)
        self.assertIn(str(staging[0] / "hub"), message)
        self.assertTrue((staging[0] / "hub" / "AGENTS.md").is_file())

    def test_this_machines_state_stays_out_of_an_older_hubs_git(self):
        # A hub whose .gitignore predates data/web-ledger/.
        (self.src / ".gitignore").write_text("cache/\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-q", "-m", "ignore", cwd=self.src)
        (self.hub_dir / "data" / "web-ledger").mkdir(parents=True)
        (self.hub_dir / "data" / "web-ledger" / "ledger.sqlite").write_text("personal")
        clone_hub(self.home, str(self.src))
        self.assertEqual((self.hub_dir / ".gitignore").read_text(), "cache/\n")
        exclude = (self.hub_dir / ".git" / "info" / "exclude").read_text()
        self.assertIn("data/web-ledger/\n", exclude)
        self.assertNotIn("\ncache/\n", exclude)  # the hub already ignores it
        status = subprocess.run(["git", "-C", str(self.hub_dir), "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        self.assertNotIn("data", status)
        ignored = subprocess.run(["git", "-C", str(self.hub_dir), "check-ignore", "-q",
                                  "data/web-ledger/ledger.sqlite"])
        self.assertEqual(ignored.returncode, 0)


class RegisterMarketplacesTest(unittest.TestCase):
    def _hub_with_plugin(self, home: Path) -> Path:
        market = home / ".swe" / "plugins" / "mine"
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(
            '{"name": "mine", "plugins": [{"name": "p", "source": "./p"}]}\n')
        (market / "p" / ".claude-plugin").mkdir(parents=True)
        (market / "p" / ".claude-plugin" / "plugin.json").write_text('{"name": "p"}\n')
        return market

    def test_each_hub_marketplace_is_added_through_the_harness_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            market = self._hub_with_plugin(home)
            ran = []

            def run(argv, **kw):
                ran.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")
            got = register_marketplaces(home, run=run)
        self.assertEqual(got, [("claude", "mine", None)])
        self.assertEqual(ran, [["claude", "plugin", "marketplace", "add", str(market)]])

    def test_a_remote_only_marketplace_is_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            market = home / ".swe" / "plugins" / "far"
            (market / ".claude-plugin").mkdir(parents=True)
            (market / ".claude-plugin" / "marketplace.json").write_text(
                '{"name": "far", "plugins": [{"name": "r", "source": '
                '{"source": "github", "repo": "o/r"}}]}\n')
            ran = []

            def run(argv, **kw):
                ran.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")
            got = register_marketplaces(home, run=run)
        self.assertEqual(got, [("claude", "far", None)])
        self.assertEqual(ran, [["claude", "plugin", "marketplace", "add", str(market)]])

    def test_a_missing_cli_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            self._hub_with_plugin(home)
            got = register_marketplaces(home, run=mock.Mock(side_effect=FileNotFoundError))
        self.assertEqual(got, [("claude", "mine", "claude is not on PATH")])

    def test_no_harness_installed_means_nothing_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._hub_with_plugin(home)
            run = mock.Mock()
            self.assertEqual(register_marketplaces(home, run=run), [])
        run.assert_not_called()


class RegisterOutputTest(unittest.TestCase):
    def _print(self, results):
        buf = io.StringIO()
        with mock.patch("swe.init.hub.register_marketplaces", return_value=results), \
             redirect_stdout(buf):
            init_commands._register_hub_marketplaces(Path("/nowhere"))
        return buf.getvalue()

    def test_each_result_gets_a_line_and_failures_a_retry_hint(self):
        out = self._print([("claude", "mine", None),
                           ("codex", "mine", "codex is not on PATH")])
        self.assertIn("mine registered in claude", out)
        self.assertIn("mine not registered in codex: codex is not on PATH", out)
        self.assertIn("swe find plugins", out)

    def test_nothing_to_register_prints_nothing(self):
        self.assertEqual(self._print([]), "")
        self.assertNotIn("--fix", self._print([("claude", "mine", None)]))


@unittest.skipUnless(GIT, "git not installed")
class CmdInitHubTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("swe.init.hub.register_marketplaces", return_value=[])
        self.register = patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, home: Path, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
            return code, buf.getvalue()

    def test_init_clones_then_repairs_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _hub_repo(root)
            home = root / "home"
            (home / ".claude").mkdir(parents=True)
            code, out = self._run(home, [f"--hub={src}", "--yes"])
            self.assertEqual(code, 0, out)
            swe = home / ".swe"
            self.assertTrue((swe / ".git").is_dir())
            self.assertTrue((swe / "plugins" / "mine").is_dir())
            # Folders the hub lacked are created by the scaffold repair.
            for name in ("skills", "hooks", "mcp"):
                self.assertTrue((swe / name).is_dir(), name)
            link = home / ".claude" / "CLAUDE.md"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.read_text(), "# from the hub\n")
            self.register.assert_called_once_with(home)

    def test_init_refuses_to_clone_over_an_existing_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _hub_repo(root)
            home = root / "home"
            (home / ".swe").mkdir(parents=True)
            (home / ".swe" / "AGENTS.md").write_text("mine\n")
            code, out = self._run(home, [f"--hub={src}"])
            self.assertEqual(code, 1)
            self.assertIn("already has a hub", out)
            self.register.assert_not_called()
            self.assertEqual((home / ".swe" / "AGENTS.md").read_text(), "mine\n")

    def test_hub_needs_the_equals_form_and_no_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, out = self._run(home, ["--hub"])
            self.assertEqual(code, 1)
            self.assertIn("--hub=<value>", out)
            code, out = self._run(home, ["--hub=x", "--check"])
            self.assertEqual(code, 1)
            self.assertFalse((home / ".swe").exists())
            err = io.StringIO()
            with redirect_stderr(err):
                code, out = self._run(home, ["--hub=x", "--check", "--json"])
            self.assertEqual((code, out), (1, ""))
            self.assertIn("--hub clones", err.getvalue())


if __name__ == "__main__":
    unittest.main()
