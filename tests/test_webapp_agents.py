"""The AGENTS.md page: the shared file with its links, and one project
folder's AGENTS.md and CLAUDE.md, never outside the hub's home."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from swe.webapp.pages import PageError
from swe.webapp.pages import agents as page

from tests.test_remote_hub import HubBase
from tests.test_webapp_doctor import _TempHome


class TestCheckDocument(unittest.TestCase):
    def test_matches_init_check_json_and_writes_nothing(self):
        from swe.init.commands import check_document
        home = Path(tempfile.mkdtemp())
        doc = check_document(home)
        self.assertEqual(doc["agents_file"], str(home / ".swe" / "AGENTS.md"))
        self.assertFalse(doc["agents_file_exists"])
        self.assertIsInstance(doc["harnesses"], list)
        self.assertEqual(list(home.iterdir()), [])


class TestProject(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.app = self.home / "dev" / "app"
        self.app.mkdir(parents=True)

    def test_files_links_and_text(self):
        (self.app / "AGENTS.md").write_text("# App <b>")
        os.symlink("AGENTS.md", self.app / "CLAUDE.md")
        d = page.project("~/dev/app", self.home)
        self.assertEqual(d["folder"], str(self.app))
        agents, claude = d["files"]
        self.assertEqual((agents["name"], agents["links_to"], agents["text"]), ("AGENTS.md", None, "# App <b>"))
        self.assertEqual((claude["links_to"], claude["text"]), ("AGENTS.md", "# App <b>"))

    def test_missing_file_reads_as_new(self):
        claude = page.project(str(self.app), self.home)["files"][1]
        self.assertEqual((claude["exists"], claude["text"], claude["links_to"]), (False, None, None))

    def test_refuses_folders_outside_home(self):
        cases = {"relative/dir": 400, "/": 403, str(self.home / "nope"): 404}
        outside = tempfile.mkdtemp()
        os.symlink(outside, self.home / "link")
        cases[str(self.home / "link")] = 403
        for folder, status in cases.items():
            with self.assertRaises(PageError, msg=folder) as refused:
                page.project(folder, self.home)
            self.assertEqual(refused.exception.status, status, folder)

    def test_a_file_linking_out_of_home_is_not_read(self):
        secret = Path(tempfile.mkdtemp()) / "AGENTS.md"
        secret.write_text("private")
        os.symlink(secret, self.app / "AGENTS.md")
        agents = page.project(str(self.app), self.home)["files"][0]
        self.assertTrue(agents["outside"])
        self.assertIsNone(agents["text"])


class TestSuggestions(unittest.TestCase):
    def test_recent_local_folders_under_home(self):
        home = Path(tempfile.mkdtemp())
        a, b = home / "a", home / "b"
        a.mkdir()
        b.mkdir()
        rows = [{"cwd": str(a), "machine_id": "me"}, {"cwd": str(a), "machine_id": "me"},
                {"cwd": str(b), "machine_id": "other"}, {"cwd": str(home), "machine_id": "me"},
                {"cwd": "/", "machine_id": "me"}, {"cwd": str(home / "gone"), "machine_id": "me"},
                {"cwd": None, "machine_id": "me"}]
        state = SimpleNamespace(catalog=SimpleNamespace(rows=lambda: rows), machine_id="me")
        self.assertEqual(page.suggestions(SimpleNamespace(state=state), home), [str(a)])


class TestLink(_TempHome, unittest.TestCase):
    """Link with swe init and Replace and back up, run for real in a temp home."""

    def setUp(self):
        super().setUp()
        self.seed({"claude": {"command": "claude"}, "codex": {"command": "codex"}})
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "AGENTS.md").write_text("mine\n")

    def states(self, view):
        return {h["name"]: h["instructions"]["state"] for h in view["harnesses"] if h["instructions"]}

    def test_the_global_tab_lists_what_replace_would_replace(self):
        view = page.get(self.ctx, {})
        self.assertEqual(self.states(view)["codex"], "conflict")
        self.assertIn("~/.codex/AGENTS.md", view["blocked"])

    def test_link_links_and_leaves_the_real_file(self):
        reply = page.post(self.ctx, "link", {})
        self.assertEqual((reply["ran"], reply["error"]), ("link", None))
        self.assertEqual(self.states(reply)["claude"], "linked")
        self.assertEqual((self.home / ".codex" / "AGENTS.md").read_text(), "mine\n")
        self.assertIn("~/.codex/AGENTS.md", reply["blocked"])

    def test_replace_backs_up_the_real_file_and_links(self):
        reply = page.post(self.ctx, "replace", {})
        self.assertEqual((reply["ran"], reply["error"]), ("replace", None))
        self.assertEqual(self.states(reply)["codex"], "linked")
        self.assertEqual(reply["blocked"], [])
        backups = list((self.home / ".swe" / "backups").iterdir())
        self.assertEqual([b.read_text() for b in backups], ["mine\n"])

    def test_unknown_actions_are_refused(self):
        for action in ("init", "force", ""):
            with self.assertRaises(PageError) as refused:
                page.post(self.ctx, action, {})
            self.assertEqual(refused.exception.status, 404, action)
        self.assertEqual((self.home / ".codex" / "AGENTS.md").read_text(), "mine\n")


class TestRoute(HubBase):
    def test_global_shows_the_file_and_its_links(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/agents")[0], 401)
        agents = self.srv.home / ".swe" / "AGENTS.md"
        agents.parent.mkdir(parents=True, exist_ok=True)
        agents.write_text("# Shared")
        report = {"agents_file": str(agents), "agents_file_exists": True, "harnesses": [
            {"name": "claude", "instructions": {"path": "/c/CLAUDE.md", "state": "linked"}, "skills": None}]}
        with patch("swe.init.commands.check_document", return_value=report):
            status, data = self.srv.request("GET", "/v1/app/agents", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["text"], "# Shared")
        self.assertEqual(body["harnesses"][0]["instructions"]["state"], "linked")
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_link_through_the_hub(self):
        origin = {"Origin": f"http://127.0.0.1:{self.srv.port}"}
        self.assertEqual(self.srv.request("POST", "/v1/app/agents/link", body={}, headers=origin)[0], 401)
        with patch("swe.webapp.pages.doctor._run_init", return_value=None) as ran:
            status, data = self.srv.request("POST", "/v1/app/agents/replace", body={},
                                            headers={**self.auth(), **origin})
        self.assertEqual(status, 200, data)
        self.assertEqual(ran.call_args.kwargs, {"force": True})
        self.assertEqual(json.loads(data)["ran"], "replace")

    def test_project_route_refuses_outside_home(self):
        status, data = self.srv.request("GET", "/v1/app/agents?folder=" + quote("/etc"), headers=self.auth())
        self.assertEqual(status, 403, data)


if __name__ == "__main__":
    unittest.main()
