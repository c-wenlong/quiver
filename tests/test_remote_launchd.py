"""remote/launchd.py: the hub LaunchAgent, with launchctl faked."""

import plistlib
import subprocess
import unittest
from pathlib import Path

from swe.remote import launchd

from tests._tempdir import temp_dir


class FakeLaunchctl:
    def __init__(self, fail=(), stdout=""):
        self.calls = []
        self.fail = set(fail)
        self.stdout = stdout

    def __call__(self, argv, **_kw):
        self.calls.append(argv[1])
        rc = 1 if argv[1] in self.fail else 0
        return subprocess.CompletedProcess(argv, rc, stdout=self.stdout, stderr="boom" if rc else "")


class LaunchdTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.log = self.home / ".quiver" / "data" / "remote" / "serve.log"

    def test_install_writes_a_keepalive_agent_and_loads_it(self):
        run = FakeLaunchctl(fail={"bootout"})  # not loaded yet: normal
        path = launchd.install(["/x/swe", "remote", "serve", "--tailnet"], self.log,
                               "/usr/bin:/bin", home=self.home, run=run)
        self.assertEqual(path, self.home / "Library/LaunchAgents" / f"{launchd.LABEL}.plist")
        plist = plistlib.loads(path.read_bytes())
        self.assertEqual(plist["ProgramArguments"], ["/x/swe", "remote", "serve", "--tailnet"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["StandardOutPath"], str(self.log))
        self.assertEqual(plist["EnvironmentVariables"], {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"})
        self.assertEqual(run.calls, ["bootout", "bootstrap"])
        self.assertTrue(self.log.parent.is_dir())

    def test_install_reports_launchctl_failure(self):
        with self.assertRaisesRegex(RuntimeError, "boom"):
            launchd.install(["/x/swe"], self.log, "", home=self.home, run=FakeLaunchctl(fail={"bootstrap"}))

    def test_uninstall_removes_the_plist(self):
        launchd.install(["/x/swe"], self.log, "", home=self.home, run=FakeLaunchctl())
        self.assertTrue(launchd.uninstall(home=self.home, run=FakeLaunchctl()))
        self.assertFalse(launchd.plist_path(self.home).exists())
        # Absent job: bootout and print both fail, nothing to remove.
        self.assertFalse(launchd.uninstall(home=self.home, run=FakeLaunchctl(fail={"bootout", "print"})))

    def test_uninstall_keeps_the_plist_when_the_hub_will_not_stop(self):
        launchd.install(["/x/swe"], self.log, "", home=self.home, run=FakeLaunchctl())
        with self.assertRaisesRegex(RuntimeError, "boom"):
            launchd.uninstall(home=self.home, run=FakeLaunchctl(fail={"bootout"}))
        self.assertTrue(launchd.plist_path(self.home).exists())

    def test_bundled_swe_is_found_in_applications(self):
        shim = self.home / "Applications/Quiver.app/Contents/Resources/swe/bin/swe"
        shim.parent.mkdir(parents=True)
        shim.write_text("#!/bin/sh\n")
        shim.chmod(0o755)
        self.assertEqual(launchd.bundled_swe(self.home), shim)

    def test_protected_dir(self):
        self.assertEqual(launchd.protected_dir(self.home / "Desktop/p/.venv/bin/swe", self.home), "Desktop")
        self.assertEqual(launchd.protected_dir(self.home / "Library/Mobile Documents/x", self.home),
                         "Library/Mobile Documents")
        self.assertIsNone(launchd.protected_dir(self.home / "code/quiver/.venv/bin/swe", self.home))
        self.assertIsNone(launchd.protected_dir("/opt/homebrew/bin/swe", self.home))

    def test_protected_dir_follows_symlinks(self):
        real = self.home / "Desktop/quiver/.venv/bin/swe"
        real.parent.mkdir(parents=True)
        real.write_text("")
        link = self.home / "bin/swe"
        link.parent.mkdir()
        link.symlink_to(real)
        self.assertEqual(launchd.protected_dir(link, self.home), "Desktop")

    def test_loaded_pid_reads_launchctl_print(self):
        out = f"gui/501/{launchd.LABEL} = {{\n\tstate = running\n\tpid = 4807\n}}"
        self.assertEqual(launchd.loaded_pid(run=FakeLaunchctl(stdout=out)), (True, 4807))
        self.assertEqual(launchd.loaded_pid(run=FakeLaunchctl(stdout="state = waiting")), (True, None))
        self.assertEqual(launchd.loaded_pid(run=FakeLaunchctl(fail={"print"})), (False, None))



class BootstrapRetryTest(unittest.TestCase):
    """bootstrap right after bootout can fail with EIO while launchd
    still tears the old job down; it loads on a retry."""

    def setUp(self):
        self.home = temp_dir(self)
        self.log = self.home / "serve.log"

    def fake(self, eio_times):
        calls = []

        def run(argv, **_kw):
            calls.append(argv[1])
            if argv[1] == "bootstrap" and calls.count("bootstrap") <= eio_times:
                return subprocess.CompletedProcess(argv, 5, stdout="",
                                                   stderr="Bootstrap failed: 5: Input/output error")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return run, calls

    def test_an_io_error_is_retried(self):
        run, calls = self.fake(eio_times=2)
        sleeps = []
        launchd.install(["/x/swe"], self.log, "/bin", home=self.home, run=run, sleep=sleeps.append)
        self.assertEqual(calls, ["bootout", "bootstrap", "bootstrap", "bootstrap"])
        self.assertEqual(len(sleeps), 2)

    def test_it_gives_up_after_the_last_try(self):
        run, calls = self.fake(eio_times=99)
        with self.assertRaises(RuntimeError) as ctx:
            launchd.install(["/x/swe"], self.log, "/bin", home=self.home, run=run, sleep=lambda _s: None)
        self.assertIn("Input/output error", str(ctx.exception))
        self.assertEqual(calls.count("bootstrap"), launchd.BOOTSTRAP_TRIES)

    def test_other_errors_are_not_retried(self):
        def run(argv, **_kw):
            rc = 1 if argv[1] == "bootstrap" else 0
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr="no such file")
        with self.assertRaises(RuntimeError):
            launchd.install(["/x/swe"], self.log, "/bin", home=self.home, run=run,
                            sleep=lambda _s: self.fail("slept"))


if __name__ == "__main__":
    unittest.main()
