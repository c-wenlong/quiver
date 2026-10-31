"""app/mac/script/setup-machine.sh: the checkout step and --dry-run.

Each test builds a throwaway origin and clone under a temp HOME and runs
only the checkout step (`--only=checkout`, which also runs off macOS)
against it. The step must never lose a commit or an uncommitted change.
"""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests._tempdir import temp_dir

SCRIPT = Path(__file__).resolve().parents[1] / "app" / "mac" / "script" / "setup-machine.sh"
INSTALL = SCRIPT.with_name("install-app.sh")
STALE = r"-stale-\d{4}-\d{2}-\d{2}"


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "needs git and bash")
class SetupMachineCheckoutTests(unittest.TestCase):
    def setUp(self):
        root = temp_dir(self)
        self.home = root / "home"
        self.home.mkdir()
        (self.home / ".gitconfig").write_text("")
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        # origin has main at A-B; the clone starts on main at B.
        self.origin = root / "origin.git"
        self.git(root, "init", "--quiet", "--bare", "-b", "main", str(self.origin))
        self.repo = root / "quiver"
        self.git(root, "clone", "--quiet", str(self.origin), str(self.repo))
        self.commit("A")
        self.commit("B")
        self.git(self.repo, "push", "--quiet", "origin", "main")

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, name, cwd=None):
        cwd = cwd or self.repo
        (Path(cwd) / f"{name}.txt").write_text(name)
        self.git(cwd, "add", f"{name}.txt")
        self.git(cwd, "commit", "--quiet", "-m", name)
        return self.git(cwd, "rev-parse", "HEAD")

    def advance_origin(self, name):
        """Push one more commit to origin/main from a second clone."""
        other = self.repo.parent / "other"
        if not other.exists():
            self.git(self.repo.parent, "clone", "--quiet", str(self.origin), str(other))
        else:
            self.git(other, "pull", "--quiet")
        sha = self.commit(name, other)
        self.git(other, "push", "--quiet", "origin", "main")
        return sha

    def run_step(self, *args):
        env = dict(self.env, QUIVER_SETUP_REPO=str(self.repo))
        return subprocess.run(["bash", str(SCRIPT), "--only=checkout", *args],
                              env=env, capture_output=True, text=True)

    def refs(self):
        out = self.git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")
        return dict(line.split() for line in out.splitlines())

    def branch_at(self, pattern):
        """Local branches whose name matches ``pattern``, with their sha."""
        return {ref[len("refs/heads/"):]: sha for ref, sha in self.refs().items()
                if ref.startswith("refs/heads/")
                and re.fullmatch(pattern, ref[len("refs/heads/"):])}

    def assert_on_origin_main(self):
        self.assertEqual(self.git(self.repo, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(self.git(self.repo, "rev-parse", "main"),
                         self.git(self.repo, "rev-parse", "origin/main"))
        self.assertEqual(self.git(self.repo, "rev-parse", "--abbrev-ref", "main@{upstream}"),
                         "origin/main")

    def test_behind_main_fast_forwards(self):
        tip = self.advance_origin("C")
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_on_origin_main()
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), tip)
        self.assertEqual(self.branch_at(r".*-stale-.*"), {})

    def test_second_run_changes_nothing(self):
        self.advance_origin("C")
        self.assertEqual(self.run_step().returncode, 0)
        before = self.refs()
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("main is already at origin/main", r.stdout)
        self.assertEqual(self.refs(), before)

    def test_unrelated_snapshot_and_main_are_renamed_aside(self):
        # The case from the issue: the clone sits on a pre-rewrite branch,
        # and local main has an unrelated history.
        self.git(self.repo, "switch", "--quiet", "--orphan", "snapshot")
        old = self.commit("X")
        self.git(self.repo, "branch", "--force", "main", old)
        self.advance_origin("C")
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_on_origin_main()
        self.assertEqual(list(self.branch_at("snapshot" + STALE).values()), [old])
        self.assertEqual(list(self.branch_at("main" + STALE).values()), [old])
        self.assertNotIn("refs/heads/snapshot", self.refs())

    def test_main_ahead_keeps_its_commits_on_a_dated_branch(self):
        mine = self.commit("local-only")
        self.advance_origin("C")
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_on_origin_main()
        self.assertEqual(list(self.branch_at("main" + STALE).values()), [mine])

    def test_stale_name_taken_gets_a_suffix(self):
        self.commit("first")
        self.advance_origin("C")
        self.assertEqual(self.run_step().returncode, 0)
        second = self.commit("second")
        self.advance_origin("D")
        self.git(self.repo, "reset", "--quiet", "--hard", second)
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list(self.branch_at("main" + STALE + "-2").values()), [second])
        self.assertEqual(len(self.branch_at("main" + STALE + "(-2)?")), 2)

    def test_feature_branch_sharing_history_keeps_its_name(self):
        self.git(self.repo, "switch", "--quiet", "-c", "feature")
        work = self.commit("F")
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_on_origin_main()
        self.assertEqual(self.git(self.repo, "rev-parse", "feature"), work)
        self.assertIn("leaving feature as it is", r.stdout)

    def test_detached_commits_get_a_branch(self):
        self.git(self.repo, "switch", "--quiet", "--detach")
        loose = self.commit("loose")
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_on_origin_main()
        self.assertEqual(list(self.branch_at("detached" + STALE).values()), [loose])

    def test_missing_local_main_is_created(self):
        self.git(self.repo, "switch", "--quiet", "-c", "feature")
        self.git(self.repo, "branch", "-D", "main")
        r = self.run_step()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_on_origin_main()

    def assert_refused_unchanged(self, needle):
        before = self.refs()
        head = self.git(self.repo, "symbolic-ref", "--short", "HEAD")
        r = self.run_step()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(needle, r.stdout)
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.git(self.repo, "symbolic-ref", "--short", "HEAD"), head)

    def test_uncommitted_change_refuses_and_keeps_it(self):
        self.git(self.repo, "switch", "--quiet", "--orphan", "snapshot")
        self.commit("X")
        (self.repo / "X.txt").write_text("edited, not committed")
        self.advance_origin("C")
        self.assert_refused_unchanged("uncommitted changes")
        self.assertEqual((self.repo / "X.txt").read_text(), "edited, not committed")

    def test_untracked_file_refuses(self):
        self.commit("local-only")
        (self.repo / "C.txt").write_text("mine")
        self.advance_origin("C")  # origin now tracks C.txt
        self.assert_refused_unchanged("uncommitted changes")
        self.assertEqual((self.repo / "C.txt").read_text(), "mine")

    def test_merge_in_progress_refuses(self):
        (self.repo / ".git" / "MERGE_HEAD").write_text(self.git(self.repo, "rev-parse", "HEAD"))
        self.assert_refused_unchanged("in progress")

    def test_linked_worktree_refuses(self):
        wt = self.repo.parent / "wt"
        self.git(self.repo, "worktree", "add", "--quiet", "-b", "side", str(wt))
        env = dict(self.env, QUIVER_SETUP_REPO=str(wt))
        r = subprocess.run(["bash", str(SCRIPT), "--only=checkout"], env=env,
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("linked worktree", r.stdout)

    def test_dry_run_prints_the_plan_and_changes_nothing(self):
        self.git(self.repo, "switch", "--quiet", "--orphan", "snapshot")
        old = self.commit("X")
        self.git(self.repo, "branch", "--force", "main", old)
        self.advance_origin("C")
        self.git(self.repo, "fetch", "--quiet")
        before = self.refs()
        r = self.run_step("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run, nothing will change", r.stdout)
        self.assertIn("would run: git fetch origin", r.stdout)
        self.assertRegex(r.stdout, r"would run: git -C \S+ branch -m snapshot snapshot" + STALE)
        self.assertRegex(r.stdout, r"would run: git -C \S+ branch -m main main" + STALE)
        self.assertIn("switch --quiet main", r.stdout)
        self.assertEqual(self.refs(), before)
        self.assertEqual(self.git(self.repo, "symbolic-ref", "--short", "HEAD"), "snapshot")

    def test_unknown_step_is_rejected(self):
        r = subprocess.run(["bash", str(SCRIPT), "--only=chekout"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown step chekout", r.stderr)


class InstallAppCaffeinateTests(unittest.TestCase):
    def test_scripts_are_executable(self):
        # The README runs both directly, not through bash.
        for script in (SCRIPT, INSTALL):
            self.assertTrue(os.access(script, os.X_OK), f"{script.name} is not executable")

    def test_scripts_parse(self):
        for script in (SCRIPT, INSTALL):
            r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{script.name}: {r.stderr}")

    def test_install_app_reexecs_under_caffeinate_once(self):
        # A stand-in uname says Darwin and a stand-in caffeinate logs its
        # call and runs the command. A held cache lock then stops the
        # install before it fetches or builds anything.
        root = temp_dir(self)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        log = root / "caffeinate.log"
        (bin_dir / "uname").write_text("#!/bin/sh\necho Darwin\n")
        (bin_dir / "caffeinate").write_text(
            f'#!/bin/sh\necho "$1 $QUIVER_INSTALL_CAFFEINATED" >> "{log}"\nshift\nexec "$@"\n')
        for tool in ("uname", "caffeinate"):
            (bin_dir / tool).chmod(0o755)
        cache = root / "cache"
        (cache / "lock").mkdir(parents=True)
        env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               "HOME": str(root), "QUIVER_INSTALL_CACHE": str(cache),
               "QUIVER_INSTALL_DIR": str(root / "Applications")}
        r = subprocess.run(["bash", str(INSTALL), "debug"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("another install is running", r.stderr)
        self.assertEqual(log.read_text().splitlines(), ["-i 1"])
        self.assertTrue((cache / "lock").is_dir())  # someone else's lock stays


if __name__ == "__main__":
    unittest.main()
