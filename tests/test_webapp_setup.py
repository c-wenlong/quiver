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
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from swe.harness.discover import HarnessFinding
from swe.init import hub as hub_mod
from swe.webapp.pages import PageError
from swe.webapp.pages import setup as setup_page
from tests.test_init_hub import _hub_repo
from tests.test_remote_hub import HubBase
from tests.test_webapp import app_js_function

GIT = shutil.which("git")


def _finding(name):
    return HarnessFinding(name=name, command=name, path=f"/bin/{name}",
                          confidence="high", source="catalog",
                          status="new", description="", tags=(), aliases=())


class _Home:
    """A blank home with a real registry under it, discovery stubbed."""

    findings = ("claude",)

    def make_home(self, home: Path):
        self.home = home
        config = home / ".swe" / "config"
        config.mkdir(parents=True, exist_ok=True)
        self.registry_file = config / "harness.json"
        (home / ".claude").mkdir()
        self.claude_md = home / ".claude" / "CLAUDE.md"
        self.claude_md.write_text("my own rules\n")
        from swe.setup import commands as sc
        self._patches = [
            patch("swe.harness.registry.CONFIG_DIR", config),
            patch("swe.harness.registry.HARNESS_FILE", self.registry_file),
            patch("swe.harness.registry.TOOLS_FILE", config / "tools.json"),
            patch.object(Path, "home", staticmethod(lambda: home)),
            patch("swe.init.layout.shutil.which", return_value=None),
            patch("swe.harness.tools.is_installed", return_value=False),
            patch("swe.webapp.pages.setup.shutil.which", return_value=None),
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
        return self.home / ".swe" / "AGENTS.md"


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
        with patch("swe.harness.tools.is_installed", return_value=True):
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
        with patch("swe.harness.installers.installed_app",
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
        from swe.setup.commands import cmd_setup, setup_preview
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
        self.assertTrue(list((self.home / ".swe" / "backups").rglob("*CLAUDE.md*")))

    def test_apply_holds_the_shared_config_lock(self):
        # Setup writes mcp.json and relinks skills: it must wait for an
        # MCP or Skills page write, which hold this same lock.
        from swe.webapp import pages
        from swe.webapp.pages import mcp, skills

        self.assertIs(mcp._WRITE_LOCK, pages.CONFIG_LOCK)
        self.assertIs(skills._WRITE_LOCK, pages.CONFIG_LOCK)
        held = []
        with patch("swe.setup.commands.apply_setup",
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
        from swe.setup import commands as sc

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

    The first run creates ~/.swe/skills; the second used to symlink it
    into every harness skills root swe knows, making ~/.augment,
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
            if path.relative_to(self.home).parts[0] != ".swe"
        )

    def assert_rerun_is_a_no_op(self, apply):
        apply()
        self.assertTrue((self.home / ".swe" / "skills").is_dir())
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
        from swe.setup.commands import apply_setup
        registry = self.assert_rerun_is_a_no_op(lambda: apply_setup(self.home))
        self.assertEqual(registry, ["claude"])

    def test_the_cli_apply_twice_adds_no_folder_or_harness(self):
        from swe.setup.commands import cmd_setup

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

        with patch("swe.harness.commands.shutil.which", side_effect=where), \
                patch("swe.harness.commands.subprocess.run", side_effect=run or installer) as stub, \
                patch("swe.harness.tools.live_version", return_value="1.0.0"):
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


def _wait_for_clone(home: Path, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hub = setup_page.hub_status(home)
        if hub["job"]["state"] != "cloning":
            return hub
        time.sleep(0.02)
    raise AssertionError("the clone never finished")


class _HubClone(_Home):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.make_home(Path(tmp.name) / "home")
        self.src = _hub_repo(Path(tmp.name))
        setup_page._JOBS.clear()
        self.addCleanup(setup_page._JOBS.clear)
        register = patch("swe.init.hub.register_marketplaces", return_value=[])
        self.register = register.start()
        self.addCleanup(register.stop)

    def clone(self, url) -> dict:
        return setup_page.post(self.ctx(), "hub", {"url": url})

    def refused(self, **body) -> PageError:
        with self.assertRaises(PageError) as caught:
            setup_page.post(self.ctx(), "hub", body)
        return caught.exception


@unittest.skipUnless(GIT, "git not installed")
class TestHubClone(_HubClone, unittest.TestCase):
    def test_get_says_a_hub_can_be_cloned_beside_local_state(self):
        # make_home made ~/.swe/config/: local state, no hub.
        data = setup_page.get(self.ctx(), {})
        self.assertEqual(data["hub"], {"can_clone": True, "demo_url": hub_mod.DEMO_HUB_URL,
                                       "job": {"state": "idle", "url": None,
                                               "error": None, "cloned": None}})
        self.assertEqual(setup_page.get(self.ctx(), {"hub": "1"}), {"hub": data["hub"]})

    def test_a_clone_runs_on_its_own_thread_and_lands_in_quiver(self):
        reply = self.clone(str(self.src))
        self.assertIn(reply["hub"]["job"]["state"], ("cloning", "done"))
        hub = _wait_for_clone(self.home)
        self.assertEqual(hub["job"]["state"], "done", hub)
        self.assertEqual(hub["job"]["url"], str(self.src))
        self.assertEqual(hub["job"]["cloned"]["path"], str(self.home / ".swe"))
        self.assertFalse(hub["can_clone"])
        self.assertEqual(self.agents_md().read_text(), "# from the hub\n")
        self.assertTrue(self.registry_file.parent.is_dir())  # the local state stayed
        self.register.assert_called_once()
        self.assertEqual(self.register.call_args.args, (self.home,))
        # The setup lock is free again: apply runs on the cloned hub.
        self.assertTrue(setup_page._LOCK.acquire(blocking=False))
        setup_page._LOCK.release()

    def test_the_clone_syncs_mcp_servers_under_the_mcp_pages_lock(self):
        # An MCP page add or remove while the clone wrote the same harness
        # configs could be lost. Only the sync holds it: the plugin
        # installs before it take minutes.
        from swe.webapp import pages

        held = {}

        def step(name):
            def run(*_a, **_k):
                held[name] = pages.CONFIG_LOCK.locked()
                return []
            return run

        with patch("swe.init.hub.install_hub_plugins", side_effect=step("plugins")), \
                patch("swe.init.hub.sync_hub_mcp", side_effect=step("mcp")):
            self.clone(str(self.src))
            hub = _wait_for_clone(self.home)
        self.assertEqual(hub["job"]["state"], "done", hub)
        self.assertEqual(held, {"plugins": False, "mcp": True})
        self.assertFalse(pages.CONFIG_LOCK.locked())

    def test_an_unreachable_url_fails_with_why_and_no_token(self):
        def fail(args, timeout, run):
            return subprocess.CompletedProcess(
                args, 128, "", "fatal: unable to access 'https://me:tok@h/r.git/': 403")

        with patch("swe.init.hub._git", fail):
            reply = self.clone("https://me:tok@h/r.git")
            hub = _wait_for_clone(self.home)
        self.assertNotIn("tok", json.dumps(reply) + json.dumps(hub))
        self.assertEqual(hub["job"]["state"], "failed")
        self.assertEqual(hub["job"]["url"], "https://h/r.git")
        self.assertIn("can't reach https://h/r.git", hub["job"]["error"])
        self.assertTrue(hub["can_clone"])
        self.assertFalse(self.agents_md().exists())

    def test_a_timeout_is_reported(self):
        def slow(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 30)

        # _git's own timeout handling, with the run git would have timed out in.
        real_git = hub_mod._git
        with patch("swe.init.hub._git", lambda args, timeout, run: real_git(args, timeout, slow)):
            self.clone("https://h/r.git")
            hub = _wait_for_clone(self.home)
        self.assertEqual(hub["job"]["state"], "failed")
        self.assertIn("took longer than 30s", hub["job"]["error"])

    def test_an_existing_hub_is_a_409_and_starts_nothing(self):
        self.agents_md().write_text("mine\n")
        err = self.refused(url=str(self.src))
        self.assertEqual((err.status, err.code), (409, "has_hub"))
        self.assertIn("already has a hub", err.message)
        self.assertEqual(setup_page.hub_status(self.home)["job"]["state"], "idle")
        self.assertEqual(self.agents_md().read_text(), "mine\n")

    def test_bad_urls_are_422(self):
        for url in (None, 3, "", "  ", "a\nb", "x" * 3000, "--upload-pack=touch x"):
            err = self.refused(url=url)
            self.assertEqual((err.status, err.code), (422, "bad_url"), url)

    def test_one_at_a_time_with_setup_itself(self):
        with setup_page._LOCK:
            err = self.refused(url=str(self.src))
        self.assertEqual((err.status, err.code), (409, "busy"))

    def _apply_behind_a_clone(self, claim_after: float) -> tuple[PageError | None, float]:
        """Apply while a clone holds (or comes to hold) the setup lock."""
        setup_page._LOCK.acquire()
        self.addCleanup(setup_page._release_claim)
        timer = threading.Timer(claim_after, setup_page._claim_clone)
        timer.start()
        started = time.monotonic()
        try:
            setup_page.post(self.ctx(), "apply", {"starred": []})
        except PageError as exc:
            return exc, time.monotonic() - started
        finally:
            timer.cancel()
            setup_page._LOCK.release()
        return None, time.monotonic() - started

    def test_apply_is_a_409_not_a_wait_while_a_clone_holds_the_lock(self):
        err, waited = self._apply_behind_a_clone(claim_after=0)
        self.assertEqual((err.status, err.code), (409, "busy"))
        self.assertLess(waited, 2)

    def test_apply_already_waiting_gives_up_when_a_clone_claims_the_lock(self):
        # The race: apply found no clone, then a clone took the lock first.
        err, waited = self._apply_behind_a_clone(claim_after=0.3)
        self.assertEqual((err.status, err.code), (409, "busy"))
        self.assertLess(waited, 2)

    def test_a_clone_cant_start_while_another_has_claimed(self):
        self.assertTrue(setup_page._claim_clone())
        self.addCleanup(setup_page._release_claim)
        err = self.refused(url=str(self.src))
        self.assertEqual((err.status, err.code), (409, "busy"))


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
        with patch("swe.webapp.pages.doctor._run_install", return_value=None) as ran:
            status, data = self.srv.request(
                "POST", "/v1/app/setup/install", body={"id": "gemini", "via": "npm"},
                headers={**self.auth(), "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(ran.call_args.kwargs, {"name": "gemini", "via": "npm"})
        self.assertIsNone(json.loads(data)["error"])

    @unittest.skipUnless(GIT, "git not installed")
    def test_hub_clone_through_the_hub_needs_a_device(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = _hub_repo(Path(tmp.name))
        setup_page._JOBS.clear()
        self.addCleanup(setup_page._JOBS.clear)
        status, _ = self.srv.request("POST", "/v1/app/setup/hub", body={"url": str(src)})
        self.assertEqual(status, 401)
        # Pairing writes ~/.swe/data/remote: local state the clone lands beside.
        auth = self.auth()
        with patch("swe.init.hub.register_marketplaces", return_value=[]):
            status, data = self.srv.request("POST", "/v1/app/setup/hub", body={"url": str(src)},
                                            headers={**auth, "Origin": self.origin()})
            self.assertEqual(status, 200, data)
            hub = _wait_for_clone(self.srv.home)
        self.assertEqual(hub["job"]["state"], "done", hub)
        status, data = self.srv.request("GET", "/v1/app/setup?hub=1", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["hub"]["job"]["state"], "done")
        self.assertEqual(self.agents_md().read_text(), "# from the hub\n")
        self.assertTrue((self.srv.home / ".swe" / "data" / "remote").is_dir())

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
        page = Path(__file__).resolve().parents[1] / "src/swe/webapp/static/pages/setup.js"
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

    def test_clone_sends_one_request_however_often_it_is_asked(self):
        """Enter twice sent two clones; the second's 409 said "already
        running" under the clone the first had started."""
        out = self.run_js(
            "globalThis.location = { hash: '#/overview' }; const posts = [];"
            " globalThis.apiPost = (path, body) => new Promise((r) => posts.push(r));"
            " page.data = { hub: { can_clone: true, job: { state: 'idle' } } }; page.hubUrl = 'https://x/h.git';"
            " const first = cloneHub(); cloneHub();"
            " const during = { posts: posts.length, posting: page.hubPosting };"
            " posts[0]({ status: 200, data: { hub: { can_clone: true, job: { state: 'failed', error: 'no' } } } });"
            " await first;"
            " process.stdout.write(JSON.stringify({ during, posting: page.hubPosting }));")
        self.assertEqual(out, {"during": {"posts": 1, "posting": True}, "posting": False})

    def test_a_revoked_credential_stops_the_clone_poll_at_the_gate(self):
        """pollHub only skipped a non-200, so a 401 polled every 1.5 s for
        as long as the page stayed open and never showed the gate."""
        out = self.run_js(
            "globalThis.location = { hash: '#/setup' }; globalThis.setTimeout = (f) => f();"
            " const gates = []; globalThis.renderGate = (m) => gates.push(m === undefined);"
            " page.data = { hub: { can_clone: true, job: { state: 'cloning', url: 'u' } } };"
            " const polling = pollHub();"
            " while (!answers.length) await null;"
            " answers[0]({ status: 401, data: null }); await polling;"
            " process.stdout.write(JSON.stringify({ reads: answers.length, gates, polling: page.hubPolling }));")
        self.assertEqual(out, {"reads": 1, "gates": [True], "polling": False})

    _NAMES = ("globalThis.esc = (s) => String(s); globalThis.Fmt = { harnessName: (id) => id };"
              " page.data = { machine: 'mac' };")

    def test_a_clone_from_an_older_swe_keeps_the_old_done_wording(self):
        """With no plugins or mcp lists the card said both had run."""
        out = self.run_js(
            self._NAMES +
            " const old = hubClonedSummary({ marketplaces: [] });"
            " const now = hubClonedSummary({ marketplaces: [], plugins: [], mcp: [] });"
            " process.stdout.write(JSON.stringify({ old, now, notes: hubInstallNotes({}) }));")
        self.assertEqual(out["old"], "Its AGENTS.md, skills, plugins and MCP servers are on mac now. "
                                     "Set up links them into each agent.")
        self.assertEqual(out["now"], "Its plugins are installed and its MCP servers synced into each "
                                     "agent on mac. Set up links its AGENTS.md and skills.")
        self.assertEqual(out["notes"], [])

    def test_install_notes_name_what_was_left_out(self):
        rows = {
            "plugins": [
                {"harness": "claude", "name": "ok", "marketplace": "m", "skipped": None,
                 "error": None, "retry": None},
                {"harness": "codex", "name": "had", "marketplace": "m",
                 "skipped": "already installed", "error": None, "retry": None},
                {"harness": "codex", "name": "late", "marketplace": "m",
                 "skipped": "not attempted: setup ran out of time", "error": None,
                 "retry": "swe plugins install late@m --harness=codex"},
            ],
            "mcp": [
                {"harness": "", "name": "", "skipped": None, "needs": [],
                 "error": "Cannot parse JSON in /h/.swe/mcp.json: fix or remove the file"},
            ],
        }
        out = self.run_js(self._NAMES +
                          f" process.stdout.write(JSON.stringify(hubInstallNotes({json.dumps(rows)})));")
        self.assertEqual(out, [
            "late@m isn't installed in codex: not attempted: setup ran out of time; "
            "retry with: swe plugins install late@m --harness=codex",
            "Cannot parse JSON in /h/.swe/mcp.json: fix or remove the file",
        ])

    def test_leaving_still_ends_the_read(self):
        out = self.run_js("const a = load(1); routeGen = 2; answers[0]({ status: 200, data: { n: 1 } });"
                          " const r = await a; process.stdout.write(JSON.stringify({ r, data: page.data, loading: page.loading }));")
        self.assertEqual(out, {"r": False, "data": None, "loading": False})


if __name__ == "__main__":
    unittest.main()
