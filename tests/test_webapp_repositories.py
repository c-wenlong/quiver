"""The Repositories page's data: dashboard boards, the focus page, PR
detail, the gh cache, and the route with auth. No real ``gh`` runs."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver import paths
from quiver.github import gh, repos
from quiver.webapp.pages import PageError
from quiver.webapp.pages import _github as G
from quiver.webapp.pages import repositories as R

from tests.test_remote_hub import HubBase

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octo")


def _board(name, failed=()):
    surfaces = {}
    for key in ("pull_requests", "issues", "runs", "releases", "tags", "branches"):
        surfaces[key] = ({"repo": name, "status": "upstream-error", key: [], "error": "boom"}
                         if key in failed else {"repo": name, "status": "ok", key: []})
    surfaces["pages"] = {"repo": name, "status": "ok", "pages": None}
    return {"repo": name, "surfaces": surfaces}


def _pin(home, *names):
    for name in names:
        repos.add_pinned(name, paths.github_file_for(home))


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Base(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp())
        self.clock = _Clock()
        cache = G.TtlCache(clock=self.clock)
        p = patch.object(G, "CACHE", cache)
        p.start()
        self.addCleanup(p.stop)
        self.ctx = SimpleNamespace(home=self.home, machine={"name": "hub-box"})


class TestCache(_Base):
    def test_ttl_refresh_floor_and_short_failures(self):
        calls = []

        def fetch():
            calls.append(1)
            return {"status": "ok"}
        ok = lambda v: v["status"] == "ok"
        G.CACHE.get("k", fetch, ok=ok)
        G.CACHE.get("k", fetch, ok=ok)
        self.assertEqual(len(calls), 1)
        # A forced refresh inside the floor reuses the reply...
        self.clock.t += G.REFRESH_FLOOR - 1
        G.CACHE.get("k", fetch, refresh=True, ok=ok)
        self.assertEqual(len(calls), 1)
        # ...and past it refetches.
        self.clock.t += 2
        G.CACHE.get("k", fetch, refresh=True, ok=ok)
        self.assertEqual(len(calls), 2)
        self.clock.t += G.TTL + 1
        G.CACHE.get("k", fetch, ok=ok)
        self.assertEqual(len(calls), 3)

        failed = []
        G.CACHE.get("f", lambda: failed.append(1) or {"status": "x"}, ok=ok)
        self.clock.t += G.FAIL_TTL + 1
        G.CACHE.get("f", lambda: failed.append(1) or {"status": "x"}, ok=ok)
        self.assertEqual(len(failed), 2)


class TestDashboard(_Base):
    def test_no_pins_reports_the_probe_and_fetches_nothing(self):
        with patch.object(gh, "probe_env", return_value=OK_ENV), \
                patch("quiver.github.cli._fetch_board") as board:
            body = R.get(self.ctx, {})
        board.assert_not_called()
        self.assertEqual(body["github"], {"status": "ok", "login": "octo", "error": None})
        self.assertEqual((body["repos"], body["boards"], body["machine"]), ([], {}, "hub-box"))

    def test_signed_out_gh_stops_before_any_read(self):
        _pin(self.home, "o/a")
        env = gh.GhEnv(installed=True, authenticated=False)
        with patch.object(gh, "probe_env", return_value=env), \
                patch("quiver.github.cli._fetch_board") as board:
            body = R.get(self.ctx, {})
        board.assert_not_called()
        self.assertEqual(body["github"]["status"], "auth-required")
        self.assertEqual([p["name"] for p in body["repos"]], ["o/a"])

    def test_boards_per_pin_are_cached(self):
        _pin(self.home, "o/a", "o/b")
        with patch.object(gh, "probe_env", return_value=OK_ENV) as probe, \
                patch("quiver.github.cli._fetch_board", side_effect=_board) as board:
            body = R.get(self.ctx, {})
            self.assertEqual(sorted(body["boards"]), ["o/a", "o/b"])
            self.assertEqual(body["boards"]["o/a"]["surfaces"]["pull_requests"]["status"], "ok")
            self.assertIn("fetched_at", body["boards"]["o/a"])
            R.get(self.ctx, {})
            self.assertEqual(board.call_count, 2)
            self.assertEqual(probe.call_count, 1)
            self.clock.t += G.REFRESH_FLOOR + 1
            R.get(self.ctx, {"refresh": "1"})
            self.assertEqual(board.call_count, 4)

    def test_a_failed_surface_is_kept_only_briefly(self):
        _pin(self.home, "o/a")
        with patch.object(gh, "probe_env", return_value=OK_ENV), \
                patch("quiver.github.cli._fetch_board",
                      side_effect=lambda n: _board(n, failed=("runs",))) as board:
            R.get(self.ctx, {})
            self.clock.t += G.FAIL_TTL + 1
            R.get(self.ctx, {})
        self.assertEqual(board.call_count, 2)


class TestFocus(_Base):
    def test_only_pinned_repos_are_read(self):
        _pin(self.home, "Owner/Repo")
        with self.assertRaises(PageError) as err:
            R.get(self.ctx, {"repo": "o/other"})
        self.assertEqual(err.exception.status, 404)
        with self.assertRaises(PageError) as err:
            R.get(self.ctx, {"repo": "not a repo"})
        self.assertEqual(err.exception.status, 400)

    def test_every_surface_in_every_state(self):
        _pin(self.home, "Owner/Repo")
        seen = {}

        def prs(name, state, limit):
            seen["prs"] = (name, state, limit)
            return {"repo": name, "status": "ok", "pull_requests": [{"number": 1}]}

        def issues(name, state, limit):
            seen["issues"] = (name, state, limit)
            return {"repo": name, "status": "ok", "issues": []}
        ok = lambda key: (lambda name, *_a: {"repo": name, "status": "ok", key: []})
        with patch("quiver.github.cli._fetch_prs", side_effect=prs), \
                patch("quiver.github.cli._fetch_issues", side_effect=issues), \
                patch("quiver.github.cli._fetch_runs", side_effect=ok("runs")), \
                patch("quiver.github.cli._fetch_pages", side_effect=lambda n: {"repo": n, "status": "ok", "pages": None}), \
                patch("quiver.github.cli._fetch_releases", side_effect=ok("releases")), \
                patch("quiver.github.cli._fetch_tags", side_effect=ok("tags")), \
                patch("quiver.github.cli._fetch_branches", side_effect=ok("branches")), \
                patch.object(gh, "probe_env") as probe:
            body = R.get(self.ctx, {"repo": "owner/repo"})
        probe.assert_not_called()  # every reply ok proves gh works
        self.assertEqual(body["repo"], "Owner/Repo")
        self.assertEqual(seen["prs"], ("Owner/Repo", "all", 50))
        self.assertEqual(seen["issues"], ("Owner/Repo", "all", 50))
        self.assertEqual(sorted(body["surfaces"]), sorted(R.FOCUS))
        self.assertEqual(body["limits"]["runs"], 30)
        self.assertEqual(body["github"]["status"], "ok")

    def test_one_surface_at_a_wider_window(self):
        _pin(self.home, "o/a")
        with patch("quiver.github.cli._fetch_tags",
                   side_effect=lambda n, lim: {"repo": n, "status": "ok", "tags": [{}] * 3}) as tags:
            body = R.get(self.ctx, {"repo": "o/a", "surface": "tags", "limit": "999"})
        tags.assert_called_once_with("o/a", R.MAX_LIMIT)
        self.assertEqual((body["surface"], body["limit"]), ("tags", R.MAX_LIMIT))
        for bad in ({"surface": "pages"}, {"surface": "nope"}, {"surface": "tags", "limit": "x"}):
            with self.assertRaises(PageError):
                R.get(self.ctx, {"repo": "o/a", **bad})


class TestPullRequest(_Base):
    PR = {"number": 7, "title": "Fix", "state": "OPEN", "isDraft": False,
          "author": {"login": "me"}, "headRefName": "fix", "baseRefName": "main",
          "body": "hi", "url": "https://github.com/o/a/pull/7", "mergeable": "MERGEABLE",
          "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
          "statusCheckRollup": [{"__typename": "CheckRun", "name": "ci",
                                 "status": "COMPLETED", "conclusion": "SUCCESS"}],
          "latestReviews": [], "reviewRequests": [], "labels": []}

    def test_detail_is_the_pr_view_shape(self):
        _pin(self.home, "o/a")
        with patch.object(gh, "probe_env", return_value=OK_ENV), \
                patch.object(gh, "run_json", return_value=gh.GhResult("ok", data=self.PR)) as run:
            body = R.get(self.ctx, {"repo": "o/a", "pr": "#7"})
            R.get(self.ctx, {"repo": "o/a", "pr": "7"})
        self.assertEqual(run.call_count, 1)
        args = run.call_args[0][0]
        self.assertEqual(args[:5], ["pr", "view", "7", "--repo", "o/a"])
        self.assertEqual(body["status"], "ok")
        pr = body["pr"]
        self.assertEqual((pr["state"], pr["checks"], pr["review_decision"]), ("open", "passing", "approved"))
        self.assertEqual(pr["check_runs"][0]["state"], "passing")
        self.assertEqual(pr["merge_state"], "clean")

    def test_failures_and_a_blocked_env(self):
        _pin(self.home, "o/a")
        with self.assertRaises(PageError):
            R.get(self.ctx, {"repo": "o/a", "pr": "abc"})
        with patch.object(gh, "probe_env", return_value=OK_ENV), \
                patch.object(gh, "run_json", return_value=gh.GhResult("not-found", error="no PR")):
            body = R.get(self.ctx, {"repo": "o/a", "pr": "9"})
        self.assertEqual((body["status"], body["pr"], body["error"]), ("not-found", None, "no PR"))
        G.CACHE.clear()
        missing = gh.GhEnv(installed=False)
        with patch.object(gh, "probe_env", return_value=missing), \
                patch.object(gh, "run_json") as run:
            body = R.get(self.ctx, {"repo": "o/a", "pr": "9"})
        run.assert_not_called()
        self.assertEqual((body["status"], body["github"]["status"]), ("unavailable", "unavailable"))


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)

    def test_needs_a_device_and_reads_the_hubs_pins(self):
        _pin(self.srv.home, "o/a")
        self.assertEqual(self.srv.request("GET", "/v1/app/repositories")[0], 401)
        with patch.object(gh, "probe_env", return_value=OK_ENV), \
                patch("quiver.github.cli._fetch_board", side_effect=_board):
            status, data = self.srv.request("GET", "/v1/app/repositories", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(list(body["boards"]), ["o/a"])
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        status, data = self.srv.request("GET", "/v1/app/repositories?repo=x/y", headers=self.auth())
        self.assertEqual(status, 404)
        self.assertIn("not pinned", data.decode())

    def test_page_scripts_are_bundled(self):
        _, js = self.srv.request("GET", "/app/pages.js")
        for name in (b'registerPage("repositories"', b'registerPage("inbox"', b"const GhUI"):
            self.assertIn(name, js)
        _, css = self.srv.request("GET", "/app/pages.css")
        self.assertIn(b".gh-row", css)


if __name__ == "__main__":
    unittest.main()
