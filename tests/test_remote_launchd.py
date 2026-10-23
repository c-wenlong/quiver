"""remote/launchd.py: the hub LaunchAgent, with launchctl faked."""

import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from quiver.remote import launchd


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
        self.home = Path(tempfile.mkdtemp())
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
        self.assertFalse(launchd.uninstall(home=self.home, run=FakeLaunchctl(fail={"bootout"})))

    def test_loaded_pid_reads_launchctl_print(self):
        out = f"gui/501/{launchd.LABEL} = {{\n\tstate = running\n\tpid = 4807\n}}"
        self.assertEqual(launchd.loaded_pid(run=FakeLaunchctl(stdout=out)), (True, 4807))
        self.assertEqual(launchd.loaded_pid(run=FakeLaunchctl(stdout="state = waiting")), (True, None))
        self.assertEqual(launchd.loaded_pid(run=FakeLaunchctl(fail={"print"})), (False, None))


if __name__ == "__main__":
    unittest.main()
