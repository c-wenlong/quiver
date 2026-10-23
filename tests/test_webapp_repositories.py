"""The Repositories page's data: dashboard boards, the focus page, PR
detail, the gh cache, and the route with auth. No real ``gh`` runs."""

import json
import shutil
import subprocess
import tempfile
import threading
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


    def test_old_entries_never_split_a_key_across_two_locks(self):
        """A pruned key keeps its lock while a caller waits on it, so the
        waiter and a newcomer share one fetch."""
        started, release = threading.Event(), threading.Event()
        calls = []

        def slow():
            calls.append(1)
            started.set()
            release.wait(5)
            return {"status": "ok"}
        first = threading.Thread(target=lambda: G.CACHE.get("k", slow))
        first.start()
        started.wait(5)
        waiter = threading.Thread(target=lambda: G.CACHE.get("k", slow))
        waiter.start()
        # Another key's write prunes "k"'s (absent) entry meanwhile.
        self.clock.t += 10_000
        G.CACHE.get("other", lambda: {"status": "ok"})
        self.assertIn("k", G.CACHE._key_locks)
        release.set()
        first.join(5)
        waiter.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(G.CACHE._users, {})


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
        self.assertEqual((body["surface"], body["limit"], body["max_limit"]),
                         ("tags", R.MAX_LIMIT, R.MAX_LIMIT))
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


STATIC = Path(R.__file__).resolve().parent.parent / "static" / "pages"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestMarkdown(unittest.TestCase):
    """GhUI.markdown, run under node with the two globals it borrows."""

    def render(self, body: str) -> str:
        script = (
            "global.esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')"
            ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;')"
            ".replace(/'/g, '&#39;');\n"
            "global.Fmt = { ago: () => '' };\n"
            + (STATIC / "github.js").read_text()
            + "\nprocess.stdout.write(GhUI.markdown(require('fs').readFileSync(0, 'utf8')));"
        )
        done = subprocess.run(["node", "-e", script], input=body, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_a_details_fold_becomes_one(self):
        html = self.render("<details><summary>More</summary>\n\nhidden **text**\n</details>")
        self.assertIn('<details class="gh-md-fold">', html)
        self.assertIn("<span>More</span>", html)
        self.assertIn("<strong>text</strong>", html)

    def test_details_inside_code_stays_an_example(self):
        body = "```html\n<details><summary>Example</summary>hidden</details>\n```\n\nand `<details>` inline"
        html = self.render(body)
        self.assertNotIn("gh-md-fold", html)
        self.assertIn("&lt;details&gt;&lt;summary&gt;Example", html)
        self.assertIn("<code>&lt;details&gt;</code>", html)

    def test_scripts_and_unsafe_links_never_render(self):
        html = self.render("<script>alert(1)</script>[x](javascript:alert(1)) <b onclick=x>hi</b>")
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html.replace("&quot;", ""))
        self.assertNotIn("onclick", html)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestBundle(unittest.TestCase):
    def test_both_pages_register_from_the_served_bundle(self):
        """Each script runs in its own scope in /app/pages.js: GhUI has to
        reach inbox.js and repositories.js as a global."""
        from quiver.webapp import pages
        stubs = (
            "const registered = [];\n"
            "global.registerPage = (id) => registered.push(id);\n"
            "global.window = { addEventListener() {} };\n"
            "global.document = { addEventListener() {} };\n"
            "global.location = { hash: '' };\n"
        )
        script = (stubs + pages.bundle(".js").decode()
                  + "\nprocess.stdout.write(JSON.stringify(registered));")
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue({"repositories", "inbox"} <= set(json.loads(done.stdout)))


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
