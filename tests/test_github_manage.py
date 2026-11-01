"""swe.github.manage: pin, repo search and the catalog sync, against a
throwaway home and a fake ``gh`` on PATH. Every store they touch lives
under that home's ~/.swe, never the real one."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from swe import paths
from swe.github import catalog, gh, manage, repos

from tests.test_webapp_repositories import FakeGh

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octo")


class _Base(unittest.TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def pins(self) -> list[str]:
        return [r["name"] for r in repos.load_pinned(paths.github_file_for(self.home))]


class TestAddRepo(_Base):
    def test_pins_the_name_github_spells(self):
        fake = FakeGh(self, repos={"o/quiver": "O/Quiver"})
        added = manage.add_repo("o/quiver", self.home)
        self.assertEqual(added.status, "ok")
        self.assertEqual(added.data, {"repo": "O/Quiver", "pinned": ["O/Quiver"]})
        self.assertEqual(self.pins(), ["O/Quiver"])
        self.assertEqual(fake.calls(), [["repo", "view", "o/quiver", "--json", "nameWithOwner"]])

    def test_pinning_twice_keeps_one_pin(self):
        FakeGh(self, repos={"o/a": "o/a", "o/b": "o/b"})
        manage.add_repo("o/a", self.home)
        manage.add_repo("o/b", self.home)
        again = manage.add_repo("o/a", self.home)
        self.assertEqual(again.data["pinned"], ["o/a", "o/b"])

    def test_an_unreachable_repo_is_not_pinned(self):
        FakeGh(self)
        added = manage.add_repo("o/gone", self.home)
        self.assertEqual(added.status, "not-found")
        self.assertIn("Cannot reach o/gone", added.error)
        self.assertEqual(self.pins(), [])


class TestSearch(_Base):
    def probe(self):
        self.probed += 1
        return OK_ENV

    def setUp(self):
        super().setUp()
        self.probed = 0

    def test_a_catalog_hit_never_runs_gh(self):
        fake = FakeGh(self)
        catalog.write([{"full_name": "o/quiver", "owner": "o", "description": "arrows",
                        "stars": 5, "updated_at": None}], ["o"],
                      path=paths.github_catalog_for(self.home))
        found = manage.search_repos("quiv", 15, self.probe, self.home)
        self.assertEqual(found["source"], "catalog")
        self.assertEqual([r["name"] for r in found["results"]], ["o/quiver"])
        self.assertEqual((self.probed, fake.calls()), (0, []))

    def test_a_catalog_miss_searches_github_scoped_to_the_account(self):
        fake = FakeGh(self, orgs=["acme"], found=["acme/new"])
        catalog.write([], ["o"], path=paths.github_catalog_for(self.home))
        found = manage.search_repos("new", 10, self.probe, self.home)
        self.assertEqual((found["status"], found["source"], found["scoped"]), ("ok", "github", True))
        self.assertEqual(found["results"][0]["name"], "acme/new")
        search = [c for c in fake.calls() if c[:2] == ["search", "repos"]][0]
        self.assertEqual(search[search.index("--limit") + 1], "10")
        owners = [search[i + 1] for i, a in enumerate(search) if a == "--owner"]
        self.assertEqual(owners, ["octo", "acme"])
        # The owners are cached in this home, so the next search skips the org call.
        cache = json.loads(paths.github_owners_cache_for(self.home).read_text())
        self.assertEqual(cache["logins"]["octo"]["owners"], ["octo", "acme"])
        manage.search_repos("new", 10, self.probe, self.home)
        self.assertEqual(sum(c[:2] == ["api", "user/orgs"] for c in fake.calls()), 1)

    def test_everywhere_skips_the_catalog_and_the_scope(self):
        fake = FakeGh(self, found=["x/y"])
        catalog.write([{"full_name": "o/y", "owner": "o"}], ["o"],
                      path=paths.github_catalog_for(self.home))
        found = manage.search_repos("y", 15, self.probe, self.home, everywhere=True)
        self.assertEqual((found["source"], found["scoped"]), ("github", False))
        self.assertNotIn("--owner", fake.calls()[0])

    def test_a_blocked_gh_and_a_refused_search(self):
        FakeGh(self, search_error="HTTP 422: Validation Failed")
        signed_out = manage.search_repos(
            "q", 5, lambda: gh.GhEnv(installed=True, authenticated=False), self.home)
        self.assertEqual((signed_out["status"], signed_out["env_blocked"]), ("auth-required", True))
        self.assertIn("gh auth login", signed_out["error"])
        refused = manage.search_repos("q", 5, self.probe, self.home)
        self.assertEqual((refused["status"], refused["env_blocked"]), ("upstream-error", False))
        self.assertIn("Validation Failed", refused["error"])


class TestSync(_Base):
    def test_lists_every_owner_into_this_homes_catalog(self):
        FakeGh(self, orgs=["acme"], lists={"octo": ["dots"], "acme": ["sdk", "api"]})
        summary = manage.sync_catalog(OK_ENV, self.home)
        self.assertIsNone(summary["error"])
        self.assertEqual(summary["repos"], 3)
        self.assertEqual(summary["failed"], [])
        self.assertEqual({e["owner"]: e["repos"] for e in summary["listed"]}, {"octo": 1, "acme": 2})
        status = catalog.status(paths.github_catalog_for(self.home))
        self.assertEqual((status["repos"], status["owners"]), (3, ["acme", "octo"]))
        self.assertGreater(summary["synced_at"], time.time() - 60)

    def test_a_failed_owner_and_a_failed_org_list_are_reported(self):
        FakeGh(self, orgs=None, lists={})
        summary = manage.sync_catalog(OK_ENV, self.home)
        self.assertEqual([f["owner"] for f in summary["failed"]], ["<orgs>", "octo"])
        self.assertIn("502", summary["failed"][1]["error"])

    def test_no_login_and_no_orgs_writes_nothing(self):
        fake = FakeGh(self, orgs=None)
        summary = manage.sync_catalog(gh.GhEnv(installed=True, authenticated=True), self.home)
        self.assertEqual(summary["error"], "Could not resolve the account or its orgs.")
        self.assertIsNone(catalog.status(paths.github_catalog_for(self.home)))
        self.assertEqual([c[:2] for c in fake.calls()], [["api", "user/orgs"]])


if __name__ == "__main__":
    unittest.main()
