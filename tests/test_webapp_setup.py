"""The web app's first-run setup page: GET /v1/app/setup and its one
write, POST /v1/app/setup/apply (swe setup --apply --json in process).

Every test runs against a real temp home and a real harness.json: the
apply links files, adopts AGENTS.md and rewrites the stars for real.
Discovery is stubbed (as in test_setup_commands) so the developer's own
PATH and harness configs never leak in, and PATH lookups find nothing.
"""

import io
import json
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
        reply = self.post(starred=["claude", "gemini", "kimi"], adopt=str(self.claude_md))
        applied = reply["applied"]
        self.assertEqual(self.agents_md().read_text(), "my own rules\n")
        self.assertTrue(self.claude_md.is_symlink())
        self.assertEqual(self.claude_md.resolve(), self.agents_md().resolve())
        self.assertEqual(applied["links"]["adopted"], str(self.claude_md))
        self.assertEqual(applied["harness"], ["claude"])
        self.assertEqual(applied["starred"], {"starred": ["claude", "gemini"],
                                              "registered": ["gemini"], "unknown": ["kimi"]})
        self.assertFalse(reply["first_run"])
        self.assertEqual(reply["starred"], ["claude", "gemini"])
        reg = self.registry()
        self.assertEqual(reg["claude"]["state"], "starred")
        self.assertEqual(reg["gemini"]["state"], "starred")
        self.assertTrue(list((self.home / ".quiver" / "backups").rglob("*CLAUDE.md*")))

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

    def test_unknown_actions_are_refused(self):
        self.assertEqual(self.refused("run", starred=[]).status, 404)


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
        _, js = self.srv.request("GET", "/app/pages.js")
        self.assertIn(b'registerPage("setup"', js)
        self.assertIn(b"/v1/app/setup/apply", js)
        _, css = self.srv.request("GET", "/app/pages.css")
        self.assertIn(b".setup-sheet", css)


if __name__ == "__main__":
    unittest.main()
