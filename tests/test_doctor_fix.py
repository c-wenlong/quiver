"""`swe doctor --fix`: the ordered fix run, its refusals, and the CLI."""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from quiver.harness import doctor_fix
from quiver.harness.doctor_fix import FixRefused, run_fixes

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from _tempdir import temp_dir
    from test_json_output import decode, run_swe
except ImportError:  # `python -m unittest tests.test_doctor_fix`
    from tests._tempdir import temp_dir
    from tests.test_json_output import decode, run_swe


def finding(check, message, action=None, **extra):
    out = {"check": check, "severity": "warning", "message": message,
           "fix": f"fix {check}"}
    if action:
        out["action"] = action
    out.update(extra)
    return out


class FakeMachine:
    """A report that changes as fixes run: each runner removes the
    findings it fixes, and may reveal new ones (a hub hides link checks)."""

    def __init__(self, findings, reveals=None, fail=()):
        self.findings = list(findings)
        self.reveals = reveals or {}
        self.fail = set(fail)
        self.calls = []

    def report(self):
        return [dict(f) for f in self.findings]

    def runner(self, name):
        def run(home, f, remedy):
            self.calls.append((name, f["check"], remedy))
            if name in self.fail:
                raise OSError(f"{name} broke")
            self.findings = [x for x in self.findings if name not in doctor_fix.actions_of(x)]
            self.findings += self.reveals.pop(name, [])
            return None
        return run

    def patch(self):
        return mock.patch.dict(doctor_fix.RUNNERS,
                               {n: self.runner(n) for n in doctor_fix.RUNNERS})


class OrderTest(unittest.TestCase):
    def test_batch_runs_in_order_and_each_global_fix_once(self):
        machine = FakeMachine([
            finding("setup.unlinked", "a", "init", harness="claude"),
            finding("setup.unlinked", "b", "init", harness="codex"),
            finding("setup.registry", "r", "discover"),
            finding("setup.agents-md", "h", "setup"),
        ])
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report)
        self.assertEqual([c[0] for c in machine.calls], ["setup", "discover", "init"])
        self.assertEqual([s["action"] for s in result["ran"]], ["setup", "discover", "init"])
        self.assertEqual(len(result["ran"][2]["findings"]), 2)
        self.assertEqual(len(result["fixed"]), 4)
        self.assertEqual(result["still_failing"], [])

    def test_a_later_step_sees_what_an_earlier_one_revealed(self):
        machine = FakeMachine(
            [finding("setup.hub", "no hub", "setup")],
            reveals={"setup": [finding("setup.unlinked", "x", "init")]})
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report)
        self.assertEqual([c[0] for c in machine.calls], ["setup", "init"])
        self.assertEqual(doctor_fix.fixable_left(result), [])

    def test_click_actions_never_run_in_a_batch(self):
        machine = FakeMachine([
            finding("off-path", "codex", "install", harness="codex", via="npm"),
            finding("drift.hooks", "gone.py", remedies=[
                {"kind": "action", "action": "undeclare",
                 "settings": ["/s"], "targets": ["gone.py"]}]),
            finding("setup.marketplace-missing", "m", remedies=[
                {"kind": "action", "action": "marketplace-drop",
                 "settings_entries": ["x"], "cli_entries": []}]),
        ])
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report)
        self.assertEqual(machine.calls, [])
        self.assertEqual([s["actions"] for s in result["skipped"]],
                         [["install"], ["undeclare"], ["marketplace-drop"]])
        self.assertEqual(len(result["still_failing"]), 3)
        self.assertEqual(doctor_fix.fixable_left(result), [])

    def test_a_failing_fix_reports_and_the_rest_still_run(self):
        machine = FakeMachine([
            finding("setup.agents-md", "h", "setup"),
            finding("setup.unlinked", "x", "init"),
        ], fail={"setup"})
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report)
        self.assertEqual([c[0] for c in machine.calls], ["setup", "init"])
        self.assertEqual(result["ran"][0]["error"], "setup broke")
        self.assertIsNone(result["ran"][1]["error"])
        self.assertEqual([f["check"] for f in doctor_fix.fixable_left(result)],
                         ["setup.agents-md"])

    def test_dry_run_runs_nothing(self):
        machine = FakeMachine([finding("setup.agents-md", "h", "setup"),
                               finding("setup.unlinked", "x", "init")])
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report, dry_run=True)
        self.assertEqual(machine.calls, [])
        self.assertEqual([s["action"] for s in result["ran"]], ["setup", "init"])
        self.assertEqual(result["fixed"], [])

    def test_a_check_scopes_the_run(self):
        machine = FakeMachine([finding("setup.agents-md", "h", "setup"),
                               finding("drift.hooks", "x", "init")])
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report, check="drift")
        self.assertEqual([c[0] for c in machine.calls], ["init"])
        self.assertEqual([f["check"] for f in result["still_failing"]], [])
        self.assertTrue(doctor_fix.in_scope({"check": "setup.unlinked"}, "setup"))
        self.assertFalse(doctor_fix.in_scope({"check": "setupx"}, "setup"))

    def test_a_fix_that_clears_nothing_needs_a_person_not_a_retry(self):
        # An empty discovery, or init leaving a real hook file in place:
        # the runner worked, the finding stays, and --fix must settle.
        stuck = [finding("setup.registry", "none", "discover"),
                 finding("drift.hooks", "real file", "init")]
        calls = []
        with mock.patch.dict(doctor_fix.RUNNERS, {
                "discover": lambda h, f, r: calls.append("discover"),
                "init": lambda h, f, r: calls.append("init")}):
            result = run_fixes(Path("/h"), lambda: [dict(f) for f in stuck])
        self.assertEqual(calls, ["discover", "init"])
        self.assertEqual(doctor_fix.fixable_left(result), [])
        self.assertEqual([f["check"] for f in result["not_cleared"]],
                         ["setup.registry", "drift.hooks"])

    def test_findings_revealed_mid_run_are_counted(self):
        click = finding("setup.marketplace-missing", "m", remedies=[
            {"kind": "action", "action": "marketplace-drop", "settings_entries": ["x"]}])
        machine = FakeMachine(
            [finding("setup.registry", "r", "discover")],
            reveals={"discover": [finding("setup.unlinked", "u", "init"), click]})
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report)
        self.assertEqual([f["check"] for f in result["fixed"]],
                         ["setup.registry", "setup.unlinked"])
        self.assertEqual([f["check"] for f in result["skipped"]],
                         ["setup.marketplace-missing"])

    def test_running_twice_runs_nothing_the_second_time(self):
        machine = FakeMachine([finding("setup.unlinked", "x", "init")])
        with machine.patch():
            run_fixes(Path("/h"), machine.report)
            again = run_fixes(Path("/h"), machine.report)
        self.assertEqual(again["ran"], [])
        self.assertEqual(len(machine.calls), 1)


class OneFindingTest(unittest.TestCase):
    def test_a_named_finding_runs_its_click_action(self):
        remedy = {"kind": "action", "action": "undeclare",
                  "settings": ["/s"], "targets": ["gone.py"]}
        machine = FakeMachine([finding("drift.hooks", "gone", remedies=[remedy])])
        with machine.patch():
            result = run_fixes(Path("/h"), machine.report, check="drift.hooks",
                               message="gone", action="undeclare")
        self.assertEqual(machine.calls, [("undeclare", "drift.hooks", remedy)])
        self.assertEqual(result["fixed"], [{"check": "drift.hooks", "message": "gone"}])

    def test_refusals(self):
        machine = FakeMachine([finding("x", "m", "init"), finding("y", "dup"),
                               finding("y", "dup"), finding("z", "plain")])
        with machine.patch():
            for check, message, action in (("x", "gone", None), ("y", "dup", None),
                                           ("x", "m", "undeclare"), ("z", "plain", None)):
                with self.assertRaises(FixRefused):
                    run_fixes(Path("/h"), machine.report, check=check,
                              message=message, action=action)
        self.assertEqual(machine.calls, [])

    def test_an_install_without_its_method_is_refused_not_run(self):
        f = finding("off-path", "codex", "install", harness="codex")
        with mock.patch("quiver.harness.commands.install_harness") as install:
            result = run_fixes(Path("/h"), lambda: [f], check="off-path", message="codex")
        install.assert_not_called()
        self.assertIn("names no harness or method", result["ran"][0]["error"])


def run_swe_on(home: Path, path: str, *args: str):
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")}
    env.update({"HOME": str(home), "PATH": path, "NO_COLOR": "1",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    return subprocess.run([sys.executable, "-m", "quiver.cli", *args],
                          capture_output=True, text=True, env=env, timeout=60)


class CliTest(unittest.TestCase):
    """The real CLI against a throwaway HOME."""

    def snapshot(self, root: Path) -> dict:
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            # doctor asks npm for its global prefix, and npm keeps its own
            # logs under ~/.npm: that write is npm's, not swe's.
            if Path(dirpath) == root and ".npm" in dirnames:
                dirnames.remove(".npm")
            for name in dirnames + filenames:
                path = Path(dirpath) / name
                out[str(path)] = path.lstat().st_mtime_ns
        return out

    def test_plain_doctor_writes_nothing(self):
        home = temp_dir(self)
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").write_text("{}\n")
        before = self.snapshot(home)
        run_swe(home, "doctor")
        run_swe(home, "doctor", "--json")
        run_swe(home, "doctor", "--fix", "--dry-run")
        self.assertEqual(self.snapshot(home), before)

    def test_fix_leaves_no_fixable_finding(self):
        home = temp_dir(self)
        (home / ".claude").mkdir()
        done = run_swe(home, "doctor", "--fix", "--json")
        data = decode(done)["data"]
        self.assertEqual([s["action"] for s in data["ran"]][:1], ["setup"])
        self.assertTrue(all(s["error"] is None for s in data["ran"]), data["ran"])
        self.assertEqual(doctor_fix.fixable_left(data), [])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((home / ".quiver" / "AGENTS.md").is_file())
        # Idempotent: a second run has nothing to do.
        again = decode(run_swe(home, "doctor", "--fix", "--json"))["data"]
        self.assertEqual(again["ran"], [])

    def test_no_harness_on_path_still_settles(self):
        home = temp_dir(self)
        (home / ".quiver").mkdir()
        (home / ".quiver" / "AGENTS.md").write_text("# shared\n")
        env_path = os.environ.get("PATH", "/usr/bin:/bin")
        done = run_swe_on(home, "/nonexistent-bin", "doctor", "--fix", "--json")
        data = decode(done)["data"]
        self.assertEqual(doctor_fix.fixable_left(data), [], env_path)
        self.assertEqual(done.returncode, 0, done.stderr)
        again = decode(run_swe_on(home, "/nonexistent-bin", "doctor", "--fix", "--json"))["data"]
        self.assertEqual(doctor_fix.fixable_left(again), [])

    def test_message_needs_a_check(self):
        home = temp_dir(self)
        done = run_swe(home, "doctor", "--fix", "--message=x")
        self.assertEqual(done.returncode, 1)
        self.assertIn("--fix=<check>", done.stdout + done.stderr)
        self.assertFalse((home / ".quiver").exists())

    def test_an_unknown_finding_is_refused(self):
        home = temp_dir(self)
        done = run_swe(home, "doctor", "--fix=setup.hub", "--message=rm -rf ~", "--json")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertFalse((home / ".quiver").exists())


class RunnerParityTest(unittest.TestCase):
    def test_the_web_page_runs_every_action(self):
        from quiver.webapp.pages import doctor as page

        self.assertEqual(set(page.RUNNERS), set(doctor_fix.RUNNERS))
        self.assertEqual(set(doctor_fix.ORDER), set(doctor_fix.RUNNERS))
        self.assertEqual(doctor_fix.BATCH | doctor_fix.CLICK, set(doctor_fix.RUNNERS))
        self.assertFalse(doctor_fix.BATCH & doctor_fix.CLICK)
        self.assertIn("install", doctor_fix.CLICK)


if __name__ == "__main__":
    unittest.main()
