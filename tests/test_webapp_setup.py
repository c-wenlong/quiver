"""The web app's first-run setup page: GET /v1/app/setup and its one
write, POST /v1/app/setup/apply (swe setup --apply --json in process).

Every test runs against a real temp home and a real harness.json: the
apply links files, adopts AGENTS.md and rewrites the stars for real.
Discovery is stubbed (as in test_setup_commands) so the developer's own
PATH and harness configs never leak in, and PATH lookups find nothing.
"""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver.harness.discover import HarnessFinding
from quiver.webapp.pages import PageError
from quiver.webapp.pages import setup as setup_page
from tests.test_remote_hub import HubBase
from tests.test_webapp import app_js_function


def _finding(name):
    return HarnessFinding(name=name, command=name, path=f"/bin/{name}",
                          confidence="high", source="catalog",
                          status="new", description="", tags=(), aliases=())


class _Home:
    """A blank home with a real registry under it, discovery stubbed."""

    findings = ("claude",)

    def make_home(self, home: Path):
        self.home = home
        config = home / ".quiver" / "config"
        config.mkdir(parents=True, exist_ok=True)
        self.registry_file = config / "harness.json"
        (home / ".claude").mkdir()
        self.claude_md = home / ".claude" / "CLAUDE.md"
        self.claude_md.write_text("my own rules\n")
        from quiver.setup import commands as sc
        self._patches = [
            patch("quiver.harness.registry.CONFIG_DIR", config),
            patch("quiver.harness.registry.HARNESS_FILE", self.registry_file),
            patch("quiver.harness.registry.TOOLS_FILE", config / "tools.json"),
            patch.object(Path, "home", staticmethod(lambda: home)),
            patch("quiver.init.layout.shutil.which", return_value=None),
            patch("quiver.harness.tools.is_installed", return_value=False),
            patch("quiver.webapp.pages.setup.shutil.which", return_value=None),
            patch.object(sc, "discover_harnesses", lambda: [_finding(n) for n in self.findings]),
            patch.object(sc, "discover_mcp_servers", lambda **k: []),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def ctx(self):
        return SimpleNamespace(home=self.home, machine={"name": "box"})

    def registry(self) -> dict:
        return json.loads(self.registry_file.read_text()) if self.registry_file.exists() else {}

    def agents_md(self) -> Path:
        return self.home / ".quiver" / "AGENTS.md"


class TestGet(_Home, unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.make_home(Path(tmp.name))

    def test_a_blank_home_is_a_first_run_and_nothing_is_written(self):
        (self.home / ".kimi").mkdir()
        data = setup_page.get(self.ctx(), {})
        self.assertTrue(data["first_run"])
        self.assertEqual(data["agents_file"], str(self.agents_md()))
        self.assertEqual(data["machine"], "box")
        providers = data["agents"]["providers"]
        self.assertEqual([p["id"] for p in providers], list(setup_page.PROVIDER_IDS))
        found = [p["id"] for p in providers if p["installed"]]
        # claude from setup's own discovery, kimi from its own sign (~/.kimi).
        self.assertEqual(found, ["claude", "kimi"])
        rows = {p["id"]: p for p in providers}
        self.assertNotIn("install", rows["claude"])
        self.assertIn("npm install -g @google/gemini-cli",
                      [m["command"] for m in rows["gemini"]["install"]])
        self.assertEqual(data["agents"]["starred"], [])
        self.assertIsNone(data["agents"]["error"])
        ins = data["preview"]["instructions"]
        self.assertEqual(ins["adopt_candidates"], [{"harness": "claude", "path": str(self.claude_md)}])
        self.assertEqual([h["name"] for h in data["preview"]["harness"]], ["claude"])
        self.assertFalse(self.agents_md().exists(), "GET must not write")
        self.assertFalse(self.registry_file.exists(), "GET must not write")
        self.assertFalse(self.claude_md.is_symlink())

    def test_registered_installed_harnesses_and_stars(self):
        self.registry_file.write_text(json.dumps({
            "codex": {"command": "codex", "state": "starred", "pin": 1},
            "gemini": {"command": "gemini", "state": "archived"},
            "ollama": {"command": "ollama", "state": "starred", "pin": 2},
        }))
        with patch("quiver.harness.tools.is_installed", return_value=True):
            data = setup_page.get(self.ctx(), {})
        found = [p["id"] for p in data["agents"]["providers"] if p["installed"]]
        # An archived harness is left out, as swe list leaves it out.
        self.assertEqual(found, ["claude", "codex"])
        self.assertEqual(data["agents"]["starred"], ["codex", "ollama"])

    def test_a_missing_agent_links_its_site_and_any_installed_app(self):
        # The Mac setup sheet's vendor link and "already installed as an
        # app" note ride the same fields.
        data = setup_page.get(self.ctx(), {})
        rows = {p["id"]: p for p in data["agents"]["providers"]}
        self.assertEqual(rows["gemini"]["website"], "https://geminicli.com")
        self.assertTrue(rows["gemini"]["website"].startswith("https://"))
        # An installed agent has no install block, so no website either.
        self.assertNotIn("website", rows["claude"])
        self.assertNotIn("app", rows["gemini"])
        with patch("quiver.harness.installers.installed_app",
                   return_value="/Applications/Gemini.app"):
            rows = {p["id"]: p for p in setup_page.get(self.ctx(), {})["agents"]["providers"]}
        self.assertEqual(rows["gemini"]["app"], "/Applications/Gemini.app")

    def test_a_corrupt_registry_is_an_error_not_a_crash(self):
        self.registry_file.write_text('{"claude": "oops"}')
        data = setup_page.get(self.ctx(), {})
        self.assertTrue(data["agents"]["error"])
        self.assertEqual(data["agents"]["starred"], [])

    def test_an_agents_md_means_set_up(self):
        self.agents_md().write_text("mine\n")
        self.assertFalse(setup_page.get(self.ctx(), {})["first_run"])

    def test_preview_is_swe_setup_json(self):
        from quiver.setup.commands import cmd_setup, setup_preview
        out = io.StringIO()
        with redirect_stdout(out), patch("sys.stdin.isatty", return_value=False):
            cmd_setup(["--json"])
        self.assertEqual(setup_preview(self.home), json.loads(out.getvalue())["data"])


class TestApply(_Home, unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.make_home(Path(tmp.name))

    def post(self, **body):
        return setup_page.post(self.ctx(), "apply", body)

    def refused(self, action="apply", **body) -> PageError:
        with self.assertRaises(PageError) as caught:
            setup_page.post(self.ctx(), action, body)
        self.assertFalse(self.agents_md().exists(), "a refusal must not write")
        self.assertFalse(self.registry_file.exists(), "a refusal must not write")
        return caught.exception

    def test_apply_adopts_links_registers_and_stars(self):
        # zcode stands in for a provider swe can't register: it is on the
        # app's list but a GUI app, so the catalog has no entry for it.
        reply = self.post(starred=["claude", "gemini", "zcode"], adopt=str(self.claude_md))
        applied = reply["applied"]
        self.assertEqual(self.agents_md().read_text(), "my own rules\n")
        self.assertTrue(self.claude_md.is_symlink())
        self.assertEqual(self.claude_md.resolve(), self.agents_md().resolve())
        self.assertEqual(applied["links"]["adopted"], str(self.claude_md))
        self.assertEqual(applied["harness"], ["claude"])
        self.assertEqual(applied["starred"], {"starred": ["claude", "gemini"],
                                              "registered": ["gemini"], "unknown": ["zcode"]})
        self.assertFalse(reply["first_run"])
        self.assertEqual(reply["starred"], ["claude", "gemini"])
        reg = self.registry()
        self.assertEqual(reg["claude"]["state"], "starred")
        self.assertEqual(reg["gemini"]["state"], "starred")
        self.assertTrue(list((self.home / ".quiver" / "backups").rglob("*CLAUDE.md*")))

    def test_apply_holds_the_shared_config_lock(self):
        # Setup writes mcp.json and relinks skills: it must wait for an
        # MCP or Skills page write, which hold this same lock.
        from quiver.webapp import pages
        from quiver.webapp.pages import mcp, skills

        self.assertIs(mcp._WRITE_LOCK, pages.CONFIG_LOCK)
        self.assertIs(skills._WRITE_LOCK, pages.CONFIG_LOCK)
        held = []
        with patch("quiver.setup.commands.apply_setup",
                   side_effect=lambda *a, **k: held.append(
                       pages.CONFIG_LOCK.locked()) or {}):
            self.post(starred=[], adopt=None)
        self.assertEqual(held, [True])

    def test_apply_with_the_starter_file_leaves_your_file_alone(self):
        applied = self.post(starred=[], adopt=None)["applied"]
        self.assertFalse(self.claude_md.is_symlink())
        self.assertEqual(self.claude_md.read_text(), "my own rules\n")
        results = {r["path"]: r["result"] for r in applied["links"]["rows"]}
        self.assertEqual(results[str(self.claude_md)], "blocked")
        self.assertTrue(self.agents_md().is_file())

    def test_a_second_run_changes_the_stars_without_adopting(self):
        self.post(starred=["claude"], adopt=str(self.claude_md))
        reply = self.post(starred=["codex"], adopt=None)
        self.assertEqual(reply["starred"], ["codex"])
        self.assertEqual(self.agents_md().read_text(), "my own rules\n")

    def test_adopt_once_agents_md_has_your_text_is_a_conflict(self):
        self.post(starred=[], adopt=str(self.claude_md))
        with self.assertRaises(PageError) as caught:
            self.post(starred=[], adopt=str(self.claude_md))
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("already has your own text", caught.exception.message)

    def test_adopt_must_be_a_file_the_preview_offers(self):
        other = self.home / "rules.md"
        other.write_text("other\n")
        for path in (str(other), "/etc/passwd", str(self.home / "missing.md")):
            self.assertEqual(self.refused(starred=[], adopt=path).status, 404, path)

    def test_bad_input_is_refused(self):
        for body in ({}, {"starred": "claude"}, {"starred": [1]}, {"starred": [""]},
                     {"starred": ["x" * 300]}, {"starred": [], "adopt": 3},
                     {"starred": [], "adopt": ""}):
            self.assertEqual(self.refused(**body).status, 422, body)

    def test_unknown_agents_are_refused(self):
        error = self.refused(starred=["claude", "nonesuch"])
        self.assertEqual(error.status, 404)
        self.assertIn("nonesuch", error.message)

    def test_a_registered_harness_outside_the_catalog_may_stay_starred(self):
        self.registry_file.write_text(json.dumps({"ollama": {"command": "ollama", "state": "starred", "pin": 1}}))
        reply = self.post(starred=["ollama", "claude"], adopt=None)
        self.assertEqual(reply["starred"], ["ollama", "claude"])

    def test_a_failed_discovery_is_a_503_and_writes_nothing(self):
        from quiver.setup import commands as sc

        def broken():
            raise OSError("PATH unreadable")
        with patch.object(sc, "discover_harnesses", broken):
            error = self.refused(starred=["claude"])
        self.assertEqual((error.status, error.code), (503, "setup_failed"))
        self.assertIn("PATH unreadable", error.message)

    def test_unknown_actions_are_refused(self):
        self.assertEqual(self.refused("run", starred=[]).status, 404)


class TestRerun(_Home, unittest.TestCase):
    """A second apply must not make uninstalled harnesses look installed.

    The first run creates ~/.quiver/skills; the second used to symlink it
    into every harness skills root quiver knows, making ~/.augment,
    ~/.pi, ~/.kiro and the rest, which swe init then registered and
    linked AGENTS.md into. Only claude and codex are installed here.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.make_home(Path(tmp.name))
        (self.home / ".codex").mkdir()

    def outside_quiver(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.home)) for path in self.home.rglob("*")
            if path.relative_to(self.home).parts[0] != ".quiver"
        )

    def assert_rerun_is_a_no_op(self, apply):
        apply()
        self.assertTrue((self.home / ".quiver" / "skills").is_dir())
        tree, registry = self.outside_quiver(), sorted(self.registry())
        for _ in range(2):
            apply()
            self.assertEqual(self.outside_quiver(), tree)
            self.assertEqual(sorted(self.registry()), registry)
        for name in (".augment", ".pi", ".kiro", ".cursor", ".config"):
            self.assertFalse((self.home / name).exists(), name)
        self.assertTrue((self.home / ".codex" / "skills").is_symlink())
        return registry

    def test_doctors_setup_fix_twice_adds_no_folder_or_harness(self):
        from quiver.setup.commands import apply_setup
        registry = self.assert_rerun_is_a_no_op(lambda: apply_setup(self.home))
        self.assertEqual(registry, ["claude"])

    def test_the_cli_apply_twice_adds_no_folder_or_harness(self):
        from quiver.setup.commands import cmd_setup

        def run():
            with redirect_stdout(io.StringIO()), \
                    patch("sys.stdin.isatty", return_value=False):
                self.assertEqual(cmd_setup(["--apply", "--json"]), 0)
        self.assertEqual(self.assert_rerun_is_a_no_op(run), ["claude"])

    def test_uninstalled_stars_stay_registered_without_folders(self):
        # Ticking a catalog harness on the Setup page registers it on
        # purpose, before it is installed; that must not make its home.
        registry = self.assert_rerun_is_a_no_op(
            lambda: self.post(starred=["claude", "augment", "pi"], adopt=None))
        self.assertEqual(registry, ["augment", "claude", "pi"])
        self.assertEqual(self.registry()["augment"]["state"], "starred")

    def post(self, **body):
        return setup_page.post(self.ctx(), "apply", body)


class TestInstall(_Home, unittest.TestCase):
    """Install on a missing agent's sheet: swe install --via, output captured."""

    def setUp(self):
        self.make_home(Path(tempfile.mkdtemp()))

    def install(self, body, *, returncode=0, which=None, run=None):
        """POST install with the installer and PATH stubbed; returns (reply, run)."""
        import subprocess

        def where(cmd):
            return which.get(cmd) if which else ("/opt/homebrew/bin/brew" if cmd == "brew" else None)

        def installer(argv, **kwargs):
            kwargs["stdout"].write(b"==> Pouring kilo\n")
            return subprocess.CompletedProcess(argv, returncode)

        with patch("quiver.harness.commands.shutil.which", side_effect=where), \
                patch("quiver.harness.commands.subprocess.run", side_effect=run or installer) as stub, \
                patch("quiver.harness.tools.live_version", return_value="1.0.0"):
            reply = setup_page.post(self.ctx(), "install", body)
        return reply, stub

    def test_runs_the_agents_own_installer_and_registers_it(self):
        import subprocess

        reply, run = self.install({"id": "kilo", "via": "brew"},
                                  which={"brew": "/opt/homebrew/bin/brew", "kilo": "/opt/homebrew/bin/kilo"})
        self.assertIsNone(reply["error"])
        self.assertEqual(run.call_args.args[0], ["brew", "install", "kilo-org/tap/kilo"])
        # No terminal: nothing on stdin, the output into a file, not memory.
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(self.registry()["kilo"]["command"], "kilo")
        self.assertIn("agents", reply["setup"])

    def test_an_alias_never_picks_another_agents_installer(self):
        self.registry_file.write_text(json.dumps({"codex": {"command": "codex", "aliases": ["kilo"]}}))
        _, run = self.install({"id": "kilo", "via": "brew"})
        self.assertEqual(run.call_args.args[0], ["brew", "install", "kilo-org/tap/kilo"])

    def test_a_timeout_keeps_what_the_installer_printed(self):
        import subprocess

        def slow(argv, **kwargs):
            kwargs["stdout"].write(b"==> Downloading kilo\n")
            raise subprocess.TimeoutExpired(argv, 600)

        reply, _ = self.install({"id": "kilo", "via": "brew"}, run=slow)
        self.assertIn("brew install timed out.", reply["error"])
        self.assertIn("Downloading kilo", reply["error"])

    def test_a_failed_install_says_why_with_its_output(self):
        reply, _ = self.install({"id": "kilo", "via": "brew"}, returncode=1)
        self.assertIn("brew install exited with code 1", reply["error"])
        self.assertIn("Pouring kilo", reply["error"])
        self.assertNotIn("kilo", self.registry())

    def test_a_tool_off_path_is_the_error(self):
        reply, run = self.install({"id": "gemini", "via": "nix"}, which={})
        self.assertIn("'nix' is needed", reply["error"])
        run.assert_not_called()

    def test_only_a_listed_agent_and_its_own_methods(self):
        cases = [("nosuch", "brew", 404), (["gemini"], "brew", 404),
                 ("gemini", "cask", 422), ("gemini", "rm -rf /", 422), ("gemini", None, 422)]
        for pid, via, status in cases:
            with self.assertRaises(PageError) as refused:
                self.install({"id": pid, "via": via})
            self.assertEqual(refused.exception.status, status, (pid, via))


class TestRoute(_Home, HubBase):
    def setUp(self):
        super().setUp()
        self.make_home(self.srv.home)

    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def test_get_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/setup")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/setup", headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertTrue(json.loads(data)["first_run"])

    def test_apply_needs_a_device(self):
        status, _ = self.srv.request("POST", "/v1/app/setup/apply", body={"starred": []})
        self.assertEqual(status, 401)
        self.assertFalse(self.agents_md().exists())

    def test_apply_through_the_hub(self):
        status, data = self.srv.request(
            "POST", "/v1/app/setup/apply",
            body={"starred": ["claude"], "adopt": str(self.claude_md)},
            headers={**self.auth(), "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["starred"], ["claude"])
        self.assertEqual(self.agents_md().read_text(), "my own rules\n")
        _, data = self.srv.request("GET", "/v1/app/setup", headers=self.auth())
        self.assertFalse(json.loads(data)["first_run"])

    def test_install_through_the_hub(self):
        with patch("quiver.webapp.pages.doctor._run_install", return_value=None) as ran:
            status, data = self.srv.request(
                "POST", "/v1/app/setup/install", body={"id": "gemini", "via": "npm"},
                headers={**self.auth(), "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(ran.call_args.kwargs, {"name": "gemini", "via": "npm"})
        self.assertIsNone(json.loads(data)["error"])

    def test_a_cross_site_origin_is_refused(self):
        status, data = self.srv.request(
            "POST", "/v1/app/setup/apply", body={"starred": ["claude"]},
            headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertFalse(self.agents_md().exists())
        self.assertFalse(self.registry_file.exists())

    def test_refusals_carry_their_status(self):
        auth = self.auth()
        for action, body, want in (
            ("apply", {"starred": "claude"}, 422),
            ("apply", {"starred": ["nonesuch"]}, 404),
            ("apply", {"starred": [], "adopt": "/etc/passwd"}, 404),
            ("toggle", {"starred": []}, 404),
        ):
            status, data = self.srv.request("POST", f"/v1/app/setup/{action}", body=body, headers=auth)
            self.assertEqual(status, want, (action, data))
            self.assertIn("message", json.loads(data)["error"])

    def test_page_assets_are_served(self):
        _, js = self.srv.request("GET", "/pages.js")
        self.assertIn(b"/v1/app/setup/apply", js)
        # The look step saves where Settings › General reads it.
        self.assertIn(b'"quiver.appearance"', js)
        _, css = self.srv.request("GET", "/pages.css")
        self.assertIn(b".setup-sheet", css)



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLoadOrder(unittest.TestCase):
    """pages/setup.js's load() under node: of two reads in flight only
    the newer one lands, whichever answers last, and a read that finishes
    after the page was left still clears the loading flag."""

    def run_js(self, body):
        page = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/setup.js"
        prelude = ("let routeGen = 1; const answers = [];\n"
                   "const api = () => new Promise((resolve) => answers.push(resolve));\n"
                   "function registerPage() {}\n" + app_js_function("currentRoute"))
        done = subprocess.run(["node", "-e", prelude + page.read_text(encoding="utf-8") + "\n(async () => {" + body + "})();"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_check_again_ignores_clicks_while_a_read_runs(self):
        out = self.run_js(
            "const btn = { classList: { add() {}, remove() {} } };"
            " globalThis.document = { getElementById: (id) => (id === 'page-refresh' ? btn : null) };"
            " globalThis.location = { hash: '#/overview' };"
            " bindRefreshButton();"
            " const first = btn.onclick(); btn.onclick();"
            " const during = answers.length;"
            " answers[0]({ status: 200, data: { n: 1 } }); await first;"
            " btn.onclick();"
            " process.stdout.write(JSON.stringify({ during, after: answers.length }));")
        self.assertEqual(out, {"during": 1, "after": 2})

    def test_the_newer_read_wins(self):
        out = self.run_js("const a = load(1), b = load(1);"
                          " answers[1]({ status: 200, data: { n: 2 } }); answers[0]({ status: 200, data: { n: 1 } });"
                          " const r = [await a, await b];"
                          " process.stdout.write(JSON.stringify({ r, n: page.data.n, loading: page.loading }));")
        self.assertEqual(out, {"r": [False, True], "n": 2, "loading": False})

    def test_leaving_still_ends_the_read(self):
        out = self.run_js("const a = load(1); routeGen = 2; answers[0]({ status: 200, data: { n: 1 } });"
                          " const r = await a; process.stdout.write(JSON.stringify({ r, data: page.data, loading: page.loading }));")
        self.assertEqual(out, {"r": False, "data": None, "loading": False})


if __name__ == "__main__":
    unittest.main()
