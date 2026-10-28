"""`swe init --hub=<url>`: clone an existing hub as ~/.quiver (#696)."""

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.init import commands as init_commands
from quiver.init.hub import HubError, check_target, clone_hub, public_url

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
    def test_absent_and_empty_are_fine_files_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            check_target(home)
            (home / ".quiver").mkdir()
            check_target(home)
            (home / ".quiver" / "AGENTS.md").write_text("x")
            with self.assertRaisesRegex(HubError, "already has files"):
                check_target(home)

    def test_a_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "elsewhere").mkdir()
            (home / ".quiver").symlink_to(home / "elsewhere")
            with self.assertRaisesRegex(HubError, "symlink"):
                check_target(home)


class PublicUrlTest(unittest.TestCase):
    def test_a_token_in_the_url_is_dropped(self):
        self.assertEqual(public_url("https://me:ghp_x@github.com/me/hub.git"),
                         "https://github.com/me/hub.git")
        self.assertEqual(public_url("git@github.com:me/hub.git"), "git@github.com:me/hub.git")


class CloneHubTest(unittest.TestCase):
    def test_unreachable_url_writes_nothing(self):
        fail = mock.Mock(return_value=subprocess.CompletedProcess(
            [], 128, "", "fatal: repository not found\n"))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaisesRegex(HubError, "can't reach .*repository not found"):
                clone_hub(home, "https://example.invalid/hub.git", run=fail)
            self.assertEqual(fail.call_count, 1)  # ls-remote only, never clone
            self.assertFalse((home / ".quiver").exists())

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

    @unittest.skipUnless(GIT, "git not installed")
    def test_clones_a_real_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _hub_repo(root)
            home = root / "home"
            home.mkdir()
            clone_hub(home, str(src))
            self.assertEqual((home / ".quiver" / "AGENTS.md").read_text(), "# from the hub\n")
            remote = subprocess.run(["git", "-C", str(home / ".quiver"), "remote", "get-url",
                                     "origin"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(remote, str(src))


@unittest.skipUnless(GIT, "git not installed")
class CmdInitHubTest(unittest.TestCase):
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
            quiver = home / ".quiver"
            self.assertTrue((quiver / ".git").is_dir())
            self.assertTrue((quiver / "plugins" / "mine").is_dir())
            # Folders the hub lacked are created by the scaffold repair.
            self.assertTrue((quiver / "skills").is_dir())
            link = home / ".claude" / "CLAUDE.md"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.read_text(), "# from the hub\n")

    def test_init_refuses_to_clone_over_an_existing_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _hub_repo(root)
            home = root / "home"
            (home / ".quiver").mkdir(parents=True)
            (home / ".quiver" / "AGENTS.md").write_text("mine\n")
            code, out = self._run(home, [f"--hub={src}"])
            self.assertEqual(code, 1)
            self.assertIn("already has files", out)
            self.assertEqual((home / ".quiver" / "AGENTS.md").read_text(), "mine\n")

    def test_hub_needs_the_equals_form_and_no_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            code, out = self._run(home, ["--hub"])
            self.assertEqual(code, 1)
            self.assertIn("--hub=<value>", out)
            code, out = self._run(home, ["--hub=x", "--check"])
            self.assertEqual(code, 1)
            self.assertFalse((home / ".quiver").exists())


if __name__ == "__main__":
    unittest.main()
