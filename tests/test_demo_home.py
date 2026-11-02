"""scripts/demo_home.sh: the stage for recording the primitives demo (#524).

Every run points --hub at a local fixture repository, so nothing here
touches the network. ``swe`` on the script's PATH is a shim that runs this
checkout's source, so the round trip tests the code under review, not
whatever swe happens to be installed.
"""

import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests._tempdir import temp_dir

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_home.sh"
SRC = ROOT / "src"
GIT = shutil.which("git")
# Prefer macOS's bash 3.2, the oldest one the script promises to run on.
BASH = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")
EXPORTS = ("DEMO_ROOT", "DEMO_FORK", "DEMO_A", "DEMO_B")


def _git(*args, cwd=None, env=None):
    base = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**base, **(env or {})})


def _hub_repo(root: Path) -> Path:
    """A local repository shaped like the demo hub: instructions and a skill."""
    src = root / "hub-src"
    (src / "skills" / "hello").mkdir(parents=True)
    (src / "skills" / "hello" / "SKILL.md").write_text(
        "---\nname: hello\ndescription: Say hello.\n---\n\nSay hello.\n")
    (src / "AGENTS.md").write_text("# demo hub\n")
    for args in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "hub")):
        _git(*args, cwd=src).check_returncode()
    return src


def _exports(stdout: str) -> dict:
    """The script's stdout is only `export NAME=value` lines; parse them the
    way a shell would read them."""
    lines = stdout.strip().splitlines()
    names = []
    found = {}
    for line in lines:
        words = shlex.split(line)
        assert words[0] == "export" and len(words) == 2, line
        name, value = words[1].split("=", 1)
        names.append(name)
        found[name] = value
    assert tuple(names) == EXPORTS, names
    return found


@unittest.skipUnless(SCRIPT.exists(), "scripts/ is not part of this source tree")
@unittest.skipUnless(GIT and BASH, "git or bash not installed")
class DemoHomeTest(unittest.TestCase):
    def setUp(self):
        self.scratch = temp_dir(self).resolve()
        self.hub = _hub_repo(self.scratch)
        # Only git and a swe that runs this checkout: no claude or codex, so
        # swe setup registers no marketplace in a real harness CLI.
        self.bin = self.scratch / "bin"
        self.bin.mkdir()
        (self.bin / "git").symlink_to(GIT)
        swe = self.bin / "swe"
        swe.write_text(f'#!/bin/sh\nPYTHONPATH={shlex.quote(str(SRC))} '
                       f'exec {shlex.quote(sys.executable)} -m swe.cli "$@"\n')
        swe.chmod(0o755)
        self.path = f"{self.bin}:/usr/bin:/bin"
        (self.scratch / "tmp").mkdir()
        (self.scratch / "presenter").mkdir()

    def _env(self, home: Path, path: str | None = None) -> dict:
        return {"PATH": path or self.path, "HOME": str(home), "NO_COLOR": "1",
                "TMPDIR": str(self.scratch / "tmp"), "GIT_CONFIG_NOSYSTEM": "1"}

    def _script(self, *args, path=None):
        return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True,
                              env=self._env(self.scratch / "presenter", path), timeout=120)

    def _swe(self, home: Path, *args):
        return subprocess.run(["swe", *args], capture_output=True, text=True,
                              env=self._env(home), timeout=180)

    def _stage(self, *extra) -> dict:
        run = self._script(f"--hub={self.hub}", *extra)
        self.assertEqual(run.returncode, 0, run.stderr)
        return _exports(run.stdout)

    def assert_fresh_machine(self, home: Path):
        for harness in (".claude", ".codex"):
            self.assertTrue((home / harness).is_dir())
            self.assertEqual(list((home / harness).iterdir()), [])
        config = str(home / ".gitconfig")
        self.assertEqual(_git("config", "--file", config, "user.name").stdout.strip(), "Demo User")
        self.assertEqual(_git("config", "--file", config, "user.email").stdout.strip(),
                         "demo@example.com")
        self.assertFalse((home / ".swe").exists())

    # --- the stage ---

    def test_builds_the_fork_and_two_machines_and_prints_exports_last(self):
        root = self.scratch / "stage"
        demo = self._stage(f"--root={root}")
        self.assertEqual(demo["DEMO_ROOT"], str(root))
        self.assertEqual(demo["DEMO_FORK"], f"file://{root}/fork.git")
        self.assertEqual(demo["DEMO_A"], str(root / "machine-a"))
        self.assertEqual(demo["DEMO_B"], str(root / "machine-b"))
        self.assertEqual(sorted(p.name for p in root.iterdir()),
                         ["fork.git", "machine-a", "machine-b"])
        fork = str(root / "fork.git")
        self.assertEqual(_git("--git-dir", fork, "rev-parse", "--is-bare-repository")
                         .stdout.strip(), "true")
        self.assertEqual(_git("--git-dir", fork, "log", "-1", "--format=%s", "main")
                         .stdout.strip(), "hub")
        # The stand-in fork keeps no URL back to the real hub.
        self.assertEqual(_git("--git-dir", fork, "remote").stdout.strip(), "")
        self.assert_fresh_machine(root / "machine-a")
        self.assert_fresh_machine(root / "machine-b")

    def test_with_no_root_it_builds_in_a_new_temp_folder(self):
        demo = self._stage()
        root = Path(demo["DEMO_ROOT"])
        self.assertEqual(root.parent, (self.scratch / "tmp").resolve())
        self.assertTrue((root / "fork.git").is_dir())
        self.assert_fresh_machine(Path(demo["DEMO_A"]))

    def test_help_goes_to_stderr_and_leaves_stdout_empty(self):
        run = self._script("--help")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")
        self.assertIn("--home=PATH", run.stderr)

    # --- refusals: each one leaves everything as it was ---

    def test_home_with_a_hub_is_refused_and_nothing_changes(self):
        for marker in ("AGENTS.md", ".git"):
            with self.subTest(marker=marker):
                home = self.scratch / f"home-{marker}"
                (home / ".swe" / marker).mkdir(parents=True)
                root = self.scratch / f"stage-{marker}"
                run = self._script(f"--hub={self.hub}", f"--root={root}", f"--home={home}")
                self.assertEqual(run.returncode, 1)
                self.assertEqual(run.stdout, "")
                self.assertIn("already has a hub", run.stderr)
                self.assertFalse(root.exists())
                self.assertEqual(sorted(p.name for p in home.iterdir()), [".swe"])

    def test_home_with_a_dangling_git_link_or_a_quiver_hub_is_refused(self):
        dangling = self.scratch / "home-link"
        (dangling / ".swe").mkdir(parents=True)
        (dangling / ".swe" / ".git").symlink_to(self.scratch / "gone")
        legacy = self.scratch / "home-quiver"
        (legacy / ".quiver").mkdir(parents=True)
        (legacy / ".quiver" / "AGENTS.md").write_text("x")
        for home, says in ((dangling, "already has a hub"), (legacy, ".quiver has a hub")):
            with self.subTest(home=home.name):
                before = sorted(p.name for p in home.iterdir())
                root = self.scratch / f"stage-{home.name}"
                run = self._script(f"--hub={self.hub}", f"--root={root}", f"--home={home}")
                self.assertEqual(run.returncode, 1)
                self.assertIn(says, run.stderr)
                self.assertFalse(root.exists())
                self.assertEqual(sorted(p.name for p in home.iterdir()), before)

    def test_a_failed_home_seed_removes_only_what_it_added(self):
        home = self.scratch / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "keep").write_text("mine")
        # A stale lock makes git config fail after .codex was created.
        (home / ".gitconfig.lock").write_text("")
        root = self.scratch / "stage"
        run = self._script(f"--hub={self.hub}", f"--root={root}", f"--home={home}")
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(root.exists())
        self.assertEqual(sorted(p.name for p in home.iterdir()), [".claude", ".gitconfig.lock"])
        self.assertEqual((home / ".claude" / "keep").read_text(), "mine")

    def test_home_must_be_an_existing_folder(self):
        root = self.scratch / "stage"
        run = self._script(f"--hub={self.hub}", f"--root={root}",
                           f"--home={self.scratch / 'nobody'}")
        self.assertEqual(run.returncode, 1)
        self.assertIn("not an existing folder", run.stderr)
        self.assertFalse(root.exists())
        self.assertFalse((self.scratch / "nobody").exists())

    def test_a_root_that_already_has_a_stage_is_refused(self):
        root = self.scratch / "stage"
        (root / "machine-b").mkdir(parents=True)
        run = self._script(f"--hub={self.hub}", f"--root={root}")
        self.assertEqual(run.returncode, 1)
        self.assertIn("machine-b already exists", run.stderr)
        self.assertEqual([p.name for p in root.iterdir()], ["machine-b"])

    def test_an_unreachable_hub_fails_before_writing_anything(self):
        root = self.scratch / "stage"
        run = self._script(f"--hub={self.scratch / 'missing'}", f"--root={root}")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stdout, "")
        self.assertIn("can't reach the hub", run.stderr)
        self.assertFalse(root.exists())
        self.assertEqual(list((self.scratch / "tmp").iterdir()), [])

    def test_missing_git_or_swe_is_named(self):
        empty = self.scratch / "empty"
        empty.mkdir()
        run = self._script(f"--hub={self.hub}", path=str(empty))
        self.assertEqual(run.returncode, 1)
        self.assertIn("git is not on PATH", run.stderr)
        only_git = self.scratch / "only-git"
        only_git.mkdir()
        (only_git / "git").symlink_to(GIT)
        run = self._script(f"--hub={self.hub}", path=str(only_git))
        self.assertEqual(run.returncode, 1)
        self.assertIn("swe is not on PATH", run.stderr)

    # --- the dedicated macOS demo account ---

    def test_home_mode_keeps_what_is_already_there(self):
        home = self.scratch / "demo-account"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}\n")
        (home / ".gitconfig").write_text("[user]\n\tname = Someone\n\temail = s@example.com\n")
        (home / ".swe").mkdir()  # machine-local state only, no hub: allowed
        root = self.scratch / "stage"
        demo = self._stage(f"--root={root}", f"--home={home}")
        self.assertEqual(demo["DEMO_A"], str(home))
        self.assertEqual(demo["DEMO_B"], str(root / "machine-b"))
        self.assertFalse((root / "machine-a").exists())
        self.assertEqual((home / ".claude" / "settings.json").read_text(), "{}\n")
        self.assertIn("name = Someone", (home / ".gitconfig").read_text())
        self.assertTrue((home / ".codex").is_dir())
        self.assert_fresh_machine(root / "machine-b")

    # --- the demo itself: one machine edits, the other sees it ---

    def test_an_edit_synced_from_machine_a_reaches_machine_b(self):
        demo = self._stage(f"--root={self.scratch / 'stage'}")
        a, b, fork = Path(demo["DEMO_A"]), Path(demo["DEMO_B"]), demo["DEMO_FORK"]

        setup = self._swe(a, "setup", f"--hub={fork}", "--json", "--non-interactive")
        self.assertEqual(setup.returncode, 0, setup.stderr)
        skill = a / ".swe" / "skills" / "hello" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nWave too.\n")

        sync = self._swe(a, "sync")
        self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
        self.assertIn("pushed main", sync.stdout)
        head = _git("--git-dir", fork[len("file://"):], "log", "-1",
                    "--format=%an <%ae>", "main").stdout.strip()
        self.assertEqual(head, "Demo User <demo@example.com>")

        setup = self._swe(b, "setup", f"--hub={fork}", "--json", "--non-interactive")
        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertIn("Wave too.", (b / ".swe" / "skills" / "hello" / "SKILL.md").read_text())
        init = self._swe(b, "init", "--yes")
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
        self.assertIn("Wave too.",
                      (b / ".claude" / "skills" / "hello" / "SKILL.md").read_text())


if __name__ == "__main__":
    unittest.main()
