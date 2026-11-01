"""remote/launchd.py: the hub LaunchAgent, with launchctl faked."""

import plistlib
import subprocess
import unittest
from pathlib import Path

from swe.remote import launchd

from tests._tempdir import temp_dir


class FakeLaunchctl:
    def __init__(self, fail=(), stdout="", loaded=()):
        self.calls = []
        self.fail = set(fail)
        self.stdout = stdout
        # Labels launchd still holds. Non-empty makes print/bootout
        # answer per label instead of per verb.
        self.loaded = set(loaded)

    def __call__(self, argv, **_kw):
        self.calls.append(argv[1])
        verb = argv[1]
        rc = 1 if verb in self.fail else 0
        out = self.stdout
        if self.loaded:
            label = argv[-1].rsplit("/", 1)[-1]
            if verb == "print":
                rc, out = ((0, "state = running\n\tpid = 4807\n")
                           if label in self.loaded else (1, ""))
            elif verb == "bootout" and rc == 0:
                self.loaded.discard(label)
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="boom" if rc else "")


class LaunchdTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.log = self.home / ".swe" / "data" / "remote" / "serve.log"

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
        shim = self.home / "Applications/SWE.app/Contents/Resources/swe/bin/swe"
        shim.parent.mkdir(parents=True)
        shim.write_text("#!/bin/sh\n")
        shim.chmod(0o755)
        self.assertEqual(launchd.bundled_swe(self.home), shim)

    def test_bundled_swe_falls_back_to_quiver_app(self):
        old = self.home / "Applications/Quiver.app/Contents/Resources/swe/bin/swe"
        old.parent.mkdir(parents=True)
        old.write_text("#!/bin/sh\n")
        old.chmod(0o755)
        self.assertEqual(launchd.bundled_swe(self.home), old)
        # Both installed: SWE.app wins.
        new = self.home / "Applications/SWE.app/Contents/Resources/swe/bin/swe"
        new.parent.mkdir(parents=True)
        new.write_text("#!/bin/sh\n")
        new.chmod(0o755)
        self.assertEqual(launchd.bundled_swe(self.home), new)

    def test_bundled_swe_prefers_swe_app_across_locations(self):
        # A stale Quiver.app in ~/Applications must not hide the
        # /Applications SWE.app.
        old = self.home / "Applications/Quiver.app/Contents/Resources/swe/bin/swe"
        old.parent.mkdir(parents=True)
        old.write_text("#!/bin/sh\n")
        old.chmod(0o755)
        system = self.home / "system-apps"
        new = system / "SWE.app/Contents/Resources/swe/bin/swe"
        new.parent.mkdir(parents=True)
        new.write_text("#!/bin/sh\n")
        new.chmod(0o755)
        self.assertEqual(launchd.bundled_swe(self.home, system), new)

    def test_install_clears_the_legacy_label(self):
        old_plist = self.home / "Library/LaunchAgents" / f"{launchd.OLD_LABEL}.plist"
        old_plist.parent.mkdir(parents=True)
        old_plist.write_bytes(plistlib.dumps({"Label": launchd.OLD_LABEL}))
        # The old job is not loaded, so print fails and its plist goes.
        run = FakeLaunchctl(fail={"print"})
        launchd.install(["/x/swe"], self.log, "", home=self.home, run=run,
                        old_label=launchd.OLD_LABEL)
        self.assertFalse(old_plist.exists())
        self.assertEqual(run.calls, ["bootout", "bootstrap", "bootout", "print"])

    def test_uninstall_removes_the_legacy_plist(self):
        launchd.install(["/x/swe"], self.log, "", home=self.home, run=FakeLaunchctl())
        old_plist = self.home / "Library/LaunchAgents" / f"{launchd.OLD_LABEL}.plist"
        old_plist.write_bytes(plistlib.dumps({"Label": launchd.OLD_LABEL}))
        self.assertTrue(launchd.uninstall(home=self.home, run=FakeLaunchctl(),
                                    old_label=launchd.OLD_LABEL))
        self.assertFalse(old_plist.exists())

    def test_install_keeps_the_legacy_plist_while_its_job_is_loaded(self):
        # A bootout that cannot stop the old job keeps its file: deleting
        # it would leave a running job with no saved configuration.
        old_plist = self.home / "Library/LaunchAgents" / f"{launchd.OLD_LABEL}.plist"
        old_plist.parent.mkdir(parents=True)
        old_plist.write_bytes(plistlib.dumps({"Label": launchd.OLD_LABEL}))
        launchd.install(["/x/swe"], self.log, "", home=self.home,
                        run=FakeLaunchctl(fail={"bootout"}, loaded={launchd.OLD_LABEL}),
                        old_label=launchd.OLD_LABEL)
        self.assertTrue(old_plist.exists())

    def test_uninstall_fails_while_the_legacy_job_is_loaded(self):
        old_plist = self.home / "Library/LaunchAgents" / f"{launchd.OLD_LABEL}.plist"
        old_plist.parent.mkdir(parents=True)
        old_plist.write_bytes(plistlib.dumps({"Label": launchd.OLD_LABEL}))
        run = FakeLaunchctl(fail={"bootout"}, loaded={launchd.OLD_LABEL})
        with self.assertRaisesRegex(RuntimeError, "boom"):
            launchd.uninstall(home=self.home, run=run, old_label=launchd.OLD_LABEL)
        self.assertTrue(old_plist.exists())

    def test_status_falls_back_to_the_legacy_label(self):
        args, running = launchd.status(home=self.home, run=FakeLaunchctl(),
                                       old_label=launchd.OLD_LABEL)
        self.assertIsNone(args)
        old_plist = self.home / "Library/LaunchAgents" / f"{launchd.OLD_LABEL}.plist"
        old_plist.parent.mkdir(parents=True)
        old_plist.write_bytes(plistlib.dumps(
            {"Label": launchd.OLD_LABEL, "ProgramArguments": ["/old/swe", "remote", "serve"]}))
        args, running = launchd.status(home=self.home, run=FakeLaunchctl(),
                                       old_label=launchd.OLD_LABEL)
        self.assertEqual(args, ["/old/swe", "remote", "serve"])
        # The new label wins when both are installed.
        launchd.install(["/x/swe"], self.log, "", home=self.home, run=FakeLaunchctl())
        args, _ = launchd.status(home=self.home, run=FakeLaunchctl(),
                                 old_label=launchd.OLD_LABEL)
        self.assertEqual(args, ["/x/swe"])

    def test_status_prefers_a_running_legacy_job(self):
        # A failed install leaves the new plist behind; the old job that
        # is still serving must report as running, not as stopped.
        agents = self.home / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        (agents / f"{launchd.LABEL}.plist").write_bytes(plistlib.dumps(
            {"Label": launchd.LABEL, "ProgramArguments": ["/new/swe"]}))
        (agents / f"{launchd.OLD_LABEL}.plist").write_bytes(plistlib.dumps(
            {"Label": launchd.OLD_LABEL, "ProgramArguments": ["/old/swe"]}))
        run = FakeLaunchctl(loaded={launchd.OLD_LABEL})
        args, running = launchd.status(home=self.home, run=run,
                                       old_label=launchd.OLD_LABEL)
        self.assertEqual((args, running), (["/old/swe"], True))
        # Nothing loaded anywhere: the newest plist's args still answer.
        args, running = launchd.status(home=self.home, run=FakeLaunchctl(fail={"print"}),
                                       old_label=launchd.OLD_LABEL)
        self.assertEqual((args, running), (["/new/swe"], False))

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
