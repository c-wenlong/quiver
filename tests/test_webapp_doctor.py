"""The Doctor page: the swe doctor report, and Run for a finding's fix."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver.harness.discover import HarnessFinding
from quiver.harness.models import DoctorFinding, DoctorReport
from quiver.webapp.pages import PageError
from quiver.webapp.pages import doctor as doctor_page

from tests.test_remote_hub import HubBase


class _TempHome:
    """A real temp home: ~/.quiver and harness.json live there, and every
    fix writes there for real. Scans of the real machine are stubbed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = self.home / ".quiver" / "config"
        self.registry = self.config / "harness.json"
        self.ctx = SimpleNamespace(home=self.home, machine={"name": "hub-box"})
        self._patches = [
            patch("quiver.harness.registry.CONFIG_DIR", self.config),
            patch("quiver.harness.registry.HARNESS_FILE", self.registry),
            patch("quiver.harness.registry.TOOLS_FILE", self.config / "tools.json"),
            patch("pathlib.Path.home", return_value=self.home),
            # No probing of this machine's PATH, npm, or drift in the repo.
            patch("quiver.harness.path_health.probe_node_env", return_value=SimpleNamespace(
                node="/usr/bin/node", npm="/usr/bin/npm", global_bin=None, global_bin_on_path=True)),
            patch("quiver.harness.path_health.nvm_bin_dirs", return_value=[]),
            patch("quiver.harness.path_health.preferred_npm_bin", return_value=None),
            patch("quiver.harness.path_health.find_off_path_tools", return_value=[]),
            patch("quiver.harness.drift.run_drift_checks", return_value=[]),
            patch("quiver.setup.commands.discover_harnesses", return_value=[]),
            patch("quiver.setup.commands.discover_mcp_servers", return_value=[]),
            patch("quiver.harness.discover.live_version", return_value=None),
        ]
        for p in self._patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def checks(self, report):
        return [f["check"] for f in report["findings"]]

    def finding(self, check):
        report = doctor_page.get(self.ctx, {})
        return next(f for f in report["findings"] if f["check"] == check)

    def fix(self, finding):
        return doctor_page.post(self.ctx, "run", {"check": finding["check"], "message": finding["message"]})

    def seed(self, registry=None):
        """A home with the hub and shared AGENTS.md, as after swe setup."""
        (self.home / ".quiver").mkdir(exist_ok=True)
        (self.home / ".quiver" / "AGENTS.md").write_text("# shared\n")
        if registry is not None:
            self.config.mkdir(parents=True, exist_ok=True)
            self.registry.write_text(json.dumps(registry))


class TestReport(_TempHome, unittest.TestCase):
    def test_the_report_is_swe_doctors(self):
        from quiver.harness.commands import doctor_report

        report = doctor_page.get(self.ctx, {})
        self.assertEqual(report["machine"], "hub-box")
        want = doctor_report(self.home).to_json()
        self.assertEqual({k: v for k, v in report.items() if k != "machine"}, want)
        self.assertEqual(report["findings"][0]["check"], "setup.hub")
        self.assertEqual(report["findings"][0]["action"], "setup")
        self.assertTrue(report["ok"])

    def test_a_finding_about_a_harness_names_it(self):
        self.seed({"claude": {"command": "claude"}})
        (self.home / ".claude").mkdir()
        finding = self.finding("setup.unlinked")
        self.assertEqual((finding["harness"], finding["action"], finding["fix"]), ("claude", "init", "swe init"))


class TestRun(_TempHome, unittest.TestCase):
    def test_setup_makes_the_hub_and_the_shared_agents_md(self):
        reply = self.fix(self.finding("setup.hub"))
        self.assertEqual((reply["ran"], reply["error"]), ("setup", None))
        self.assertTrue((self.home / ".quiver" / "AGENTS.md").is_file())
        self.assertNotIn("setup.hub", self.checks(reply["report"]))
        self.assertNotIn("setup.agents-md", self.checks(reply["report"]))

    def test_init_links_the_shared_agents_md(self):
        self.seed({"claude": {"command": "claude"}})
        (self.home / ".claude").mkdir()
        reply = self.fix(self.finding("setup.unlinked"))
        self.assertEqual((reply["ran"], reply["error"]), ("init", None))
        link = self.home / ".claude" / "CLAUDE.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.home / ".quiver" / "AGENTS.md").resolve())
        self.assertNotIn("setup.unlinked", self.checks(reply["report"]))

    def test_init_leaves_a_real_file_and_lists_it(self):
        self.seed({"claude": {"command": "claude"}, "codex": {"command": "codex"}})
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "AGENTS.md").write_text("mine\n")
        reply = self.fix(self.finding("setup.unlinked"))
        self.assertEqual((self.home / ".codex" / "AGENTS.md").read_text(), "mine\n")
        # Not a failure: the page offers Replace and back up for it.
        self.assertIsNone(reply["error"])
        self.assertIn("~/.codex/AGENTS.md", reply["blocked"])

    def test_replace_backs_up_and_links(self):
        self.seed({"codex": {"command": "codex"}})
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "AGENTS.md").write_text("mine\n")
        reply = doctor_page.post(self.ctx, "replace", {})
        self.assertEqual((reply["ran"], reply["error"]), ("replace", None))
        self.assertNotIn("~/.codex/AGENTS.md", reply["blocked"])
        link = self.home / ".codex" / "AGENTS.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.home / ".quiver" / "AGENTS.md").resolve())
        backups = list((self.home / ".quiver" / "backups").iterdir())
        self.assertEqual([b.read_text() for b in backups], ["mine\n"])
        self.assertIn("report", reply)

    def test_discover_lists_nothing_blocked(self):
        self.seed()
        with patch.dict(doctor_page.RUNNERS, {"discover": lambda home: None}):
            self.assertEqual(self.fix(self.finding("setup.registry"))["blocked"], [])

    def test_init_never_asks(self):
        self.seed({"claude": {"command": "claude"}})
        (self.home / ".claude").mkdir()
        with patch("quiver.init.commands._supported", return_value=True), \
                patch("quiver.init.commands.multiselect", side_effect=AssertionError("asked")):
            self.assertIsNone(self.fix(self.finding("setup.unlinked"))["error"])

    def test_discover_registers_the_sure_matches(self):
        self.seed()
        found = [HarnessFinding("codex", "codex", "/bin/codex", "high", "catalog", "new"),
                 HarnessFinding("maybe", "maybe", "/bin/maybe", "medium", "path_scan", "new")]
        with patch("quiver.harness.discover.discover_harnesses", return_value=found):
            reply = self.fix(self.finding("setup.registry"))
        self.assertEqual((reply["ran"], reply["error"]), ("discover", None))
        self.assertEqual(list(json.loads(self.registry.read_text())), ["codex"])
        self.assertNotIn("setup.registry", self.checks(reply["report"]))

    def test_a_fix_that_fails_reports_and_rereads(self):
        finding = self.finding("setup.hub")
        with patch("quiver.setup.commands.apply_setup", side_effect=OSError("disk full")):
            reply = self.fix(finding)
        self.assertEqual(reply["error"], "disk full")
        self.assertIn("setup.hub", self.checks(reply["report"]))

    def test_a_stopped_linking_pass_is_the_error(self):
        finding = self.finding("setup.hub")
        applied = {"harness": [], "links": {"error": "cannot read .linkignore"}}
        with patch("quiver.setup.commands.apply_setup", return_value=applied):
            self.assertEqual(self.fix(finding)["error"], "cannot read .linkignore")


class TestInstall(_TempHome, unittest.TestCase):
    """An off-PATH harness gets Run: swe install <name> --via=<method>."""

    def off_path(self, source="nvm",
                 path="/home/u/.nvm/versions/node/v22/lib/node_modules/@openai/codex/bin/codex.js"):
        from quiver.harness.path_health import OffPathHit

        self.seed({"codex": {"command": "codex"}})
        hit = OffPathHit("codex", path, source)
        return patch("quiver.harness.path_health.find_off_path_tools", return_value=[("codex", "codex", hit)]), \
            patch("quiver.harness.path_health.search_dirs_for_command", return_value=[hit])

    def test_the_finding_names_the_method_that_put_it_there(self):
        orphans, hits = self.off_path()
        with orphans, hits, patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}" if t in ("brew", "npm") else None):
            finding = self.finding("off-path")
        # Homebrew comes first in swe's order, but an nvm copy came from npm.
        self.assertEqual((finding["action"], finding["via"], finding["fix"]),
                         ("install", "npm", "swe install codex --via=npm"))

    def test_no_usable_method_means_no_run(self):
        orphans, hits = self.off_path()
        with orphans, hits, patch("shutil.which", return_value=None):
            finding = self.finding("off-path")
        self.assertNotIn("action", finding)
        self.assertEqual(finding["fix"], "swe install codex")

    def test_run_installs_with_that_method(self):
        from quiver.harness.commands import InstallResult

        orphans, hits = self.off_path()
        done = InstallResult(code=0, action="Updated", path="/usr/bin/codex", version="1", output="",
                             exited=True, error=None)
        with orphans, hits, patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}" if t == "npm" else None), \
                patch("quiver.harness.commands.install_harness", return_value=done) as install:
            reply = self.fix(self.finding("off-path"))
        install.assert_called_once_with("codex", "npm")
        self.assertEqual((reply["ran"], reply["error"]), ("install", None))

    def test_a_failed_install_shows_the_end_of_its_output(self):
        from quiver.harness.commands import InstallResult

        failed = InstallResult(code=1, action=None, path=None, version=None, exited=True,
                               output="\n".join(f"line {i}" for i in range(20)),
                               error="npm install exited with code 1")
        problem = doctor_page.install_problem(failed, "codex")
        self.assertTrue(problem.startswith("npm install exited with code 1\n"))
        self.assertIn("line 19", problem)
        self.assertNotIn("line 11", problem)
        stray = InstallResult(code=1, action="Added", path=None, version=None, exited=True, output="", error=None)
        self.assertIn("still not on PATH", doctor_page.install_problem(stray, "codex"))


class TestRefusals(_TempHome, unittest.TestCase):
    def refused(self, action, body) -> PageError:
        with patch.dict(doctor_page.RUNNERS, {k: self.fail for k in doctor_page.RUNNERS}):
            with self.assertRaises(PageError) as caught:
                doctor_page.post(self.ctx, action, body)
        return caught.exception

    def test_only_run_and_replace(self):
        for action in ("fix", "setup", "init", ""):
            error = self.refused(action, {"check": "setup.hub", "message": "x"})
            self.assertEqual((error.status, error.code), (404, "unknown_action"))

    def test_the_body_must_name_a_finding(self):
        for body in ({}, {"check": "setup.hub"}, {"check": 1, "message": "m"},
                     {"check": "c", "message": ""}, {"check": "c", "message": "x" * 5000}):
            error = self.refused("run", body)
            self.assertEqual((error.status, error.code), (400, "bad_finding"), body)

    def test_a_finding_not_in_the_report_is_refused(self):
        error = self.refused("run", {"check": "setup.hub", "message": "rm -rf ~"})
        self.assertEqual((error.status, error.code), (404, "unknown_finding"))

    def test_a_finding_without_an_action_is_refused(self):
        report = DoctorReport([DoctorFinding("npm", "warning", "npm not found on PATH",
                                             "Install Node/npm, or put npm on PATH.")])
        with patch("quiver.harness.commands.doctor_report", return_value=report):
            error = self.refused("run", {"check": "npm", "message": "npm not found on PATH"})
        self.assertEqual((error.status, error.code), (422, "no_action"))

    def test_an_action_this_page_does_not_know_is_refused(self):
        report = DoctorReport([DoctorFinding("x", "warning", "m", "swe x", action="uninstall")])
        with patch("quiver.harness.commands.doctor_report", return_value=report):
            error = self.refused("run", {"check": "x", "message": "m"})
        self.assertEqual((error.status, error.code), (422, "no_action"))

    def test_one_fix_at_a_time(self):
        finding = self.finding("setup.hub")
        started, release = threading.Event(), threading.Event()

        def slow(home):
            started.set()
            release.wait(5)

        with patch.dict(doctor_page.RUNNERS, {"setup": slow}):
            first = threading.Thread(target=self.fix, args=(finding,))
            first.start()
            started.wait(5)
            try:
                with self.assertRaises(PageError) as caught:
                    self.fix(finding)
                self.assertEqual((caught.exception.status, caught.exception.code), (409, "busy"))
                with self.assertRaises(PageError) as caught:
                    doctor_page.post(self.ctx, "replace", {})
                self.assertEqual(caught.exception.status, 409)
            finally:
                release.set()
                first.join(5)
        # The lock is free again.
        with patch.dict(doctor_page.RUNNERS, {"setup": lambda home: None}):
            self.assertEqual(self.fix(finding)["ran"], "setup")


class TestRoute(_TempHome, HubBase):
    def setUp(self):
        super().setUp()
        self.ctx = SimpleNamespace(home=self.srv.home, machine=self.srv.state.machine)
        self.home = self.srv.home

    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def test_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/doctor")[0], 401)
        status, _ = self.srv.request("POST", "/v1/app/doctor/run", body={"check": "setup.hub", "message": "m"})
        self.assertEqual(status, 401)

    def test_report_and_run_through_the_hub(self):
        auth = self.auth()
        status, data = self.srv.request("GET", "/v1/app/doctor", headers=auth)
        self.assertEqual(status, 200, data)
        report = json.loads(data)
        self.assertEqual(report["machine"], self.srv.state.machine["name"])
        # The hub's own ~/.quiver holds its pairing store, but no AGENTS.md.
        finding = report["findings"][0]
        self.assertEqual((finding["check"], finding["action"]), ("setup.agents-md", "setup"))
        ran = []
        with patch.dict(doctor_page.RUNNERS, {"setup": lambda home: ran.append(home)}):
            status, data = self.srv.request("POST", "/v1/app/doctor/run", headers={**auth, "Origin": self.origin()},
                                            body={"check": finding["check"], "message": finding["message"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(ran, [self.srv.home])
        self.assertEqual(json.loads(data)["ran"], "setup")

    def test_a_cross_site_origin_is_refused(self):
        finding = doctor_page.get(self.ctx, {})["findings"][0]
        with patch.dict(doctor_page.RUNNERS, {"setup": self.fail}):
            status, data = self.srv.request(
                "POST", "/v1/app/doctor/run", headers={**self.auth(), "Origin": "https://evil.example"},
                body={"check": finding["check"], "message": finding["message"]})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertFalse((self.srv.home / ".quiver" / "AGENTS.md").exists())

    def test_refusals_carry_their_status(self):
        auth = self.auth()
        for body, want in (({"check": "setup.hub"}, 400), ({"check": "nope", "message": "m"}, 404)):
            status, data = self.srv.request("POST", "/v1/app/doctor/run", body=body, headers=auth)
            self.assertEqual(status, want, data)

    def test_page_assets_are_served(self):
        _, js = self.srv.request("GET", "/pages.js")
        self.assertIn(b"/v1/app/doctor/run", js)
        _, css = self.srv.request("GET", "/pages.css")
        self.assertIn(b".doctor-fix", css)



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestFindingIds(unittest.TestCase):
    """pages/doctor.js's finding id keeps check and message apart, so
    two findings whose texts only join the same way stay two (one Run
    confirm or busy row would otherwise mark both)."""

    def test_check_and_message_do_not_run_together(self):
        page = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/doctor.js"
        body = ("process.stdout.write(JSON.stringify([doctorId({ check: 'a', message: 'bc' }),"
                " doctorId({ check: 'ab', message: 'c' })]));\n")
        done = subprocess.run(["node", "-e", "function registerPage() {}\n" + page.read_text(encoding="utf-8") + "\n" + body],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        first, second = json.loads(done.stdout)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
