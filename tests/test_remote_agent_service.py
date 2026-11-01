"""swe remote agent --install/--uninstall/--status: the satellite as its
own LaunchAgent or systemd user unit, with launchctl and systemctl faked
and HOME pointed at a temp folder."""

import io
import json
import os
import plistlib
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from swe.remote import agent as agent_mod
from swe.remote import commands, launchd, systemd

from tests._tempdir import temp_dir
from tests.test_remote_systemd import FakeCtl

SWE = "/Users/k/Applications/Quiver.app/Contents/Resources/swe/bin/swe"


class FakeLaunchctl:
    """Records launchctl verbs and which labels are loaded; a loaded job
    answers print with a pid."""

    def __init__(self, loaded=()):
        self.calls = []
        self.loaded = set(loaded)

    def __call__(self, argv, **_kw):
        self.calls.append(argv[1:])
        verb = argv[1]
        if verb == "bootstrap":
            self.loaded.add(argv[3].rsplit("/", 1)[-1].removesuffix(".plist"))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        label = argv[2].rsplit("/", 1)[-1]
        if label not in self.loaded:
            return subprocess.CompletedProcess(argv, 113, stdout="", stderr="not found")
        if verb == "bootout":
            self.loaded.discard(label)
        out = "\tpid = 4242\n" if verb == "print" else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


def _fake(real, run):
    """``real`` (launchd/systemd install, uninstall, status) with the
    service manager swapped for ``run``, whatever the caller passed."""
    return lambda *a, **k: real(*a, **{**k, "run": run})


class AgentServiceTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        env = patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def _run(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = commands.cmd_remote(list(args))
        return rc, out.getvalue(), err.getvalue()

    def _pair(self, hub="https://hub.tail.ts.net"):
        agent_mod.save_agent(self.home, {"hub": hub, "credential": "secret-cred"})

    def _mac(self, ctl):
        """Patches for a Mac with Quiver.app installed and launchctl faked."""
        return [
            patch.object(commands.sys, "platform", "darwin"),
            patch.object(commands.launchd, "bundled_swe", return_value=SWE),
            patch.object(commands.launchd, "install", _fake(launchd.install, ctl)),
            patch.object(commands.launchd, "uninstall", _fake(launchd.uninstall, ctl)),
            patch.object(commands.launchd, "status", _fake(launchd.status, ctl)),
        ]

    def _linux(self, ctl):
        return [
            patch.object(commands.sys, "platform", "linux"),
            patch.object(commands.sys, "argv", ["/home/dev/.local/bin/swe"]),
            patch("swe.remote.commands.os.access", return_value=True),
            patch.object(commands.systemd, "available", return_value=True),
            patch.object(commands.systemd, "install", _fake(systemd.install, ctl)),
            patch.object(commands.systemd, "uninstall", _fake(systemd.uninstall, ctl)),
            patch.object(commands.systemd, "status", _fake(systemd.status, ctl)),
        ]

    def _with(self, patches, args):
        for p in patches:
            p.start()
        try:
            return self._run(args)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_mac_install_writes_its_own_agent_beside_the_hubs_label(self):
        self._pair()
        ctl = FakeLaunchctl()
        rc, out, err = self._with(self._mac(ctl), ["agent", "--install"])
        self.assertEqual(rc, 0, err)
        path = self.home / "Library/LaunchAgents/com.cwenlong.quiver.remote-agent.plist"
        plist = plistlib.loads(path.read_bytes())
        self.assertEqual(plist["Label"], launchd.AGENT_LABEL)
        self.assertNotEqual(launchd.AGENT_LABEL, launchd.LABEL)
        self.assertEqual(plist["ProgramArguments"], [SWE, "remote", "agent"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["StandardOutPath"],
                         str(self.home / ".swe/data/remote/agent.log"))
        self.assertIn("PATH", plist["EnvironmentVariables"])
        self.assertNotIn("secret-cred", path.read_text(errors="ignore"))
        self.assertFalse(launchd.plist_path(self.home).exists(), "the hub's plist stays absent")
        self.assertEqual([c[0] for c in ctl.calls], ["bootout", "bootstrap"])
        self.assertTrue(all(launchd.AGENT_LABEL in c[-1] or c[-1] == str(path)
                            for c in ctl.calls))
        self.assertIn("installed", out)

    def test_install_is_refused_next_to_this_machines_installed_hub(self):
        self._pair()
        hub = launchd.plist_path(self.home)
        hub.parent.mkdir(parents=True)
        hub.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
        ctl = FakeLaunchctl()
        rc, _, err = self._with(self._mac(ctl), ["agent", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("this machine's hub is installed", err)
        self.assertIn("swe remote serve --uninstall", err)
        self.assertEqual(ctl.calls, [])
        self.assertFalse(launchd.plist_path(self.home, launchd.AGENT_LABEL).exists())

    def test_the_hub_install_is_refused_next_to_an_installed_agent(self):
        self._pair()
        ctl = FakeLaunchctl()
        self._with(self._mac(ctl), ["agent", "--install"])
        before = list(ctl.calls)
        patches = self._mac(ctl) + [
            patch.object(commands, "_probe", return_value=None),
            patch.object(commands.launchd, "loaded_pid", return_value=(False, None)),
            patch("swe.remote.commands._tailscale_ip", return_value="100.1.2.3"),
        ]
        rc, _, err = self._with(patches, ["serve", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("this machine's satellite agent is installed", err)
        self.assertIn("swe remote agent --uninstall", err)
        self.assertEqual(ctl.calls, before, "the refused hub never reached launchctl")
        self.assertFalse(launchd.plist_path(self.home).exists())
        # setup --apply installs through the same path.
        with patch.object(commands.sys, "platform", "darwin"), \
             patch.object(commands.launchd, "bundled_swe", return_value=SWE):
            from swe.remote import setup
            err = setup.default_actions(self.home, run=ctl).install_hub("https://m.ts.net", 8477)
        self.assertIn("satellite agent is installed", err)

    def test_install_is_refused_while_a_shell_agent_runs(self):
        self._pair()
        lock, _ = agent_mod.take_lock(self.home)
        self.addCleanup(lock.close)
        ctl = FakeLaunchctl()
        rc, _, err = self._with(self._mac(ctl), ["agent", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn(f"already runs here (pid {os.getpid()})", err)
        self.assertEqual([c[0] for c in ctl.calls], [], "no plist: nothing to ask launchd")

    def test_reinstall_restarts_the_services_own_agent(self):
        self._pair()
        ctl = FakeLaunchctl()
        self._with(self._mac(ctl), ["agent", "--install"])
        lock, _ = agent_mod.take_lock(self.home)  # the service's agent
        self.addCleanup(lock.close)
        rc, _, err = self._with(self._mac(ctl), ["agent", "--install"])
        self.assertEqual(rc, 0, err)

    def test_a_second_foreground_agent_exits(self):
        self._pair()
        lock, _ = agent_mod.take_lock(self.home)
        self.addCleanup(lock.close)
        with patch.object(commands.agent_mod, "serve") as serve:
            rc, _, err = self._run(["agent"])
        self.assertEqual(rc, 1)
        self.assertIn("another swe remote agent already runs here", err)
        serve.assert_not_called()
        # A refused start pairs nothing, so the saved hub stays put.
        with patch.object(commands.agent_mod, "pair_agent") as pair:
            rc, _, _ = self._run(["agent", "--hub=https://new.ts.net", "--token=T"])
        self.assertEqual(rc, 1)
        pair.assert_not_called()
        self.assertEqual(agent_mod.load_agent(self.home)["hub"], "https://hub.tail.ts.net")
        lock.close()
        self.assertIsNone(agent_mod.lock_holder(self.home))
        with patch.object(commands.agent_mod, "serve", return_value=0) as serve, \
             patch.object(commands.catalog_mod, "local_machine",
                          return_value={"name": "m", "id": "i"}):
            rc, _, _ = self._run(["agent"])
        self.assertEqual(rc, 0)
        serve.assert_called_once()

    def test_install_needs_a_paired_agent(self):
        ctl = FakeLaunchctl()
        rc, _, err = self._with(self._mac(ctl), ["agent", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("no hub credential", err)
        self.assertEqual(ctl.calls, [])

    def test_install_carries_a_hub_override_but_never_the_token(self):
        self._pair()
        ctl = FakeLaunchctl()
        rc, _, err = self._with(self._mac(ctl), ["agent", "--install", "--hub=https://other.ts.net/"])
        self.assertEqual(rc, 0, err)
        plist = plistlib.loads(launchd.plist_path(self.home, launchd.AGENT_LABEL).read_bytes())
        self.assertEqual(plist["ProgramArguments"], [SWE, "remote", "agent", "--hub=https://other.ts.net"])

    def test_install_pairs_first_when_given_a_token(self):
        def pair(hub, token, name):
            conf = {"hub": hub, "credential": "minted"}
            agent_mod.save_agent(self.home, conf)
            return conf

        ctl = FakeLaunchctl()
        with patch.object(commands.agent_mod, "pair_agent", pair):
            rc, out, err = self._with(self._mac(ctl),
                                      ["agent", "--install", "--hub=https://hub.ts.net", "--token=T"])
        self.assertEqual(rc, 0, err)
        self.assertIn("paired with https://hub.ts.net", out)
        plist = plistlib.loads(launchd.plist_path(self.home, launchd.AGENT_LABEL).read_bytes())
        self.assertEqual(plist["ProgramArguments"], [SWE, "remote", "agent"])

    def test_mac_uninstall_removes_only_the_agent(self):
        self._pair()
        hub = launchd.plist_path(self.home)
        ctl = FakeLaunchctl()
        self._with(self._mac(ctl), ["agent", "--install"])
        hub.write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
        rc, out, err = self._with(self._mac(ctl), ["agent", "--uninstall"])
        self.assertEqual(rc, 0, err)
        self.assertIn("stopped the agent", out)
        self.assertFalse(launchd.plist_path(self.home, launchd.AGENT_LABEL).exists())
        self.assertTrue(hub.exists())
        self.assertNotIn(launchd.LABEL, " ".join(" ".join(c) for c in ctl.calls).replace(
            launchd.AGENT_LABEL, ""))
        rc, out, _ = self._with(self._mac(FakeLaunchctl()), ["agent", "--uninstall"])
        self.assertIn("no agent LaunchAgent was installed", out)

    def test_status_json_reports_the_service_and_the_refusal(self):
        self._pair()
        ctl = FakeLaunchctl()
        rc, out, _ = self._with(self._mac(ctl), ["agent", "--status", "--json"])
        self.assertEqual(rc, 0)
        report = json.loads(out)["data"]
        self.assertEqual(report["configured"], True)
        self.assertEqual(report["hub"], "https://hub.tail.ts.net")
        self.assertEqual(report["installed"], False)
        self.assertEqual(report["running"], False)
        self.assertEqual(report["hub_installed"], False)
        self.assertEqual(report["supported"], True)
        self._with(self._mac(ctl), ["agent", "--install"])
        launchd.plist_path(self.home).write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
        rc, out, _ = self._with(self._mac(ctl), ["agent", "--status", "--json"])
        report = json.loads(out)["data"]
        self.assertEqual((report["installed"], report["running"], report["hub_installed"]),
                         (True, True, True))
        self.assertEqual(report["program"], [SWE, "remote", "agent"])
        self.assertTrue(report["service"].endswith("com.cwenlong.quiver.remote-agent.plist"))
        self.assertNotIn("secret-cred", out)

    def test_install_and_uninstall_json_print_only_the_status_on_stdout(self):
        self._pair()
        ctl = FakeLaunchctl()
        rc, out, err = self._with(self._mac(ctl), ["agent", "--install", "--json"])
        self.assertEqual(rc, 0, err)
        report = json.loads(out)["data"]
        self.assertEqual((report["installed"], report["running"]), (True, True))
        self.assertIn("installed", err)
        rc, out, err = self._with(self._mac(ctl), ["agent", "--uninstall", "--json"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["data"]["installed"], False)
        launchd.plist_path(self.home).write_bytes(plistlib.dumps({"Label": launchd.LABEL}))
        rc, out, err = self._with(self._mac(ctl), ["agent", "--install", "--json"])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("this machine's hub is installed", err)

    def test_status_prose(self):
        ctl = FakeLaunchctl()
        rc, out, _ = self._with(self._mac(ctl), ["agent", "--status"])
        self.assertEqual(rc, 0)
        self.assertIn("not installed", out)
        self.assertIn("not paired", out)

    def test_linux_install_writes_its_own_unit_and_says_how_to_linger(self):
        self._pair()
        ctl = FakeCtl(linger="no", can_linger=False)
        rc, out, err = self._with(self._linux(ctl), ["agent", "--install"])
        self.assertEqual(rc, 0, err)
        path = self.home / ".config/systemd/user/quiver-remote-agent.service"
        text = path.read_text()
        self.assertIn("Description=SWE remote agent (swe remote agent)", text)
        self.assertIn("ExecStart=/home/dev/.local/bin/swe remote agent", text)
        self.assertIn("agent.log", text)
        self.assertFalse(systemd.unit_path(self.home).exists())
        started = [a for a in ctl.calls if a[0] == "systemctl" and a[2] in {"enable", "restart"}]
        self.assertTrue(started and all(a[3] == systemd.AGENT_UNIT for a in started))
        self.assertIn("starts at boot", out)
        self.assertIn("sudo loginctl enable-linger $USER", err)

    def test_linux_install_is_refused_next_to_an_installed_hub_unit(self):
        self._pair()
        hub = systemd.unit_path(self.home)
        hub.parent.mkdir(parents=True)
        hub.write_text("[Service]\nExecStart=/bin/swe remote serve\n")
        ctl = FakeCtl()
        rc, _, err = self._with(self._linux(ctl), ["agent", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn(str(hub), err)
        self.assertEqual(ctl.calls, [])

    def test_linux_uninstall_and_status(self):
        self._pair()
        ctl = FakeCtl()
        self._with(self._linux(ctl), ["agent", "--install"])
        rc, out, _ = self._with(self._linux(ctl), ["agent", "--status"])
        self.assertIn("running", out)
        rc, out, err = self._with(self._linux(ctl), ["agent", "--uninstall"])
        self.assertEqual(rc, 0, err)
        self.assertFalse(systemd.unit_path(self.home, systemd.AGENT_UNIT).exists())
        disabled = [a for a in ctl.calls if a[0] == "systemctl" and a[2] == "disable"]
        self.assertEqual(disabled[-1][-1], systemd.AGENT_UNIT)

    def test_linux_without_systemd_names_the_agent_command(self):
        with patch.object(commands.sys, "platform", "linux"), \
             patch.object(commands.systemd, "available", return_value=False):
            rc, _, err = self._run(["agent", "--install"])
        self.assertEqual(rc, 1)
        self.assertIn("run `swe remote agent` under this machine's init system", err)


class SystemdUnitNameTest(unittest.TestCase):
    def test_status_and_unit_path_follow_the_unit(self):
        home = temp_dir(self)
        log = home / "agent.log"
        systemd.install(["/bin/swe", "remote", "agent"], log, "/bin", home=home,
                        run=FakeCtl(), unit=systemd.AGENT_UNIT, description="agent")
        self.assertEqual(systemd.status(home, FakeCtl(), systemd.AGENT_UNIT),
                         (["/bin/swe", "remote", "agent"], True))
        self.assertEqual(systemd.status(home, FakeCtl()), (None, False))


if __name__ == "__main__":
    unittest.main()
