"""remote/pulls.py: GET /v1/pulls, the PR detail route, and
POST /v1/pulls/<o>/<r>/<n>/open on the hub.

Nothing real runs: ``gh``, the worktree and ``launch_new`` are stubbed,
so no network, no git remote and no harness process."""

import contextlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.github import cli as ghcli
from swe.github import gh, manage, repos
from swe.github import pulls as gh_pulls
from swe.remote import pulls as pulls_mod
from swe.remote import protocol
from swe.github import shared as G
from swe.webapp.pages import repositories

from tests.test_remote_hub import _Server, _session

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")

# A `gh pr view --json` reply, camelCase as gh prints it.
RAW_PR = {
    "number": 5,
    "title": "Fix the thing",
    "state": "OPEN",
    "isDraft": False,
    "author": {"login": "octocat"},
    "headRefName": "fix-thing",
    "baseRefName": "main",
    "body": "What broke and why this fixes it.",
    "url": "https://github.com/o/r/pull/5",
    "mergeable": "MERGEABLE",
    "mergeStateStatus": "CLEAN",
    "reviewDecision": "REVIEW_REQUIRED",
    "statusCheckRollup": [
        {"__typename": "CheckRun", "name": "tests",
         "workflow": {"name": "CI"}, "status": "COMPLETED",
         "conclusion": "FAILURE",
         "detailsUrl": "https://github.com/o/r/actions/runs/9"},
        {"__typename": "CheckRun", "name": "lint",
         "workflow": {"name": "CI"}, "status": "COMPLETED",
         "conclusion": "SUCCESS", "detailsUrl": ""},
    ],
    "latestReviews": [
        {"author": {"login": "rev"}, "state": "COMMENTED",
         "submittedAt": "2026-01-02T00:00:00Z"},
    ],
    "reviewRequests": [{"login": "octocat"}],
    "additions": 10, "deletions": 2, "changedFiles": 3,
    "labels": [{"name": "bug"}],
    "createdAt": "2026-01-01T00:00:00Z",
    "updatedAt": "2026-01-02T00:00:00Z",
    "mergedAt": None,
    "autoMergeRequest": None,
}


def _row(number, author="octocat", title=None):
    """A ``cli._pr_row`` entry, the shaped list row."""
    return {"number": number, "title": title or f"PR {number}",
            "state": "open", "draft": False, "author": author,
            "branch": "b", "base": "main", "labels": [],
            "mergeable": "mergeable", "review_decision": "none",
            "checks": "passing", "updated_at": "2026-01-02T00:00:00Z",
            "url": f"https://github.com/o/r/pull/{number}"}


def _pr_status(authored=(), requested=()):
    return {"repo": "o/r", "status": "ok", "current_branch": None,
            "authored": [{"number": n} for n in authored],
            "review_requested": [{"number": n} for n in requested]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        repos.add_pinned("o/r", manage.pins_file(self.srv.home))
        repos.add_pinned("o/s", manage.pins_file(self.srv.home))
        # The pulls reads sit on the shared gh cache; tests stub the
        # fetches per case, so nothing may survive from a prior one.
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _get(self, path):
        status, data = self.srv.request("GET", path, headers=self.hdr)
        return status, json.loads(data)

    def _view(self, launch_id):
        status, data = self._get(f"/v1/launches/{launch_id}")
        self.assertEqual(status, 200, data)
        return data


def _stub_prs(name, state, limit):
    rows = {"o/r": [_row(5), _row(7, author="other")],
            "o/s": [_row(3)]}.get(name, [])
    return {"repo": name, "status": "ok", "pull_requests": rows}


class TestList(_Base):
    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, prs=None, status=None):
        with patch("swe.github.gh.probe_env", return_value=env), \
             patch("swe.github.cli._fetch_prs",
                   side_effect=prs or _stub_prs) as lp, \
             patch("swe.github.cli._fetch_pr_status",
                   side_effect=status or (lambda _n: _pr_status())) as ls:
            yield lp, ls

    def test_lists_every_pin_with_viewer_flags(self):
        with self._stubs(status=lambda _n: _pr_status(
                authored=[5], requested=[7])):
            status, body = self._get("/v1/pulls")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["github"]["login"], "octocat")
        self.assertNotIn("env_blocked", body)
        by_name = {r["name"]: r for r in body["repos"]}
        self.assertEqual(set(by_name), {"o/r", "o/s"})
        prs = {p["number"]: p for p in by_name["o/r"]["pulls"]}
        self.assertEqual(set(prs), {5, 7})
        self.assertEqual((prs[5]["mine"], prs[5]["review_requested"]),
                         (True, False))
        self.assertEqual((prs[7]["mine"], prs[7]["review_requested"]),
                         (False, True))
        self.assertEqual(prs[5]["checks"], "passing")
        self.assertIn("fetched_at", body)

    def test_mine_falls_back_to_the_login_when_status_fails(self):
        with self._stubs(status=lambda _n: {
                "repo": "o/r", "status": "upstream-error",
                "current_branch": None, "authored": [],
                "review_requested": [], "error": "down"}):
            _s, body = self._get("/v1/pulls")
        prs = {p["number"]: p
               for p in body["repos"][0]["pulls"]}
        self.assertIs(prs[5]["mine"], True)      # author == login
        self.assertIs(prs[7]["mine"], False)
        self.assertIsNone(prs[5]["review_requested"])

    def test_no_pins(self):
        repos.remove_pinned("o/r", manage.pins_file(self.srv.home))
        repos.remove_pinned("o/s", manage.pins_file(self.srv.home))
        with self._stubs() as (lp, _ls):
            status, body = self._get("/v1/pulls")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["repos"], [])
        lp.assert_not_called()

    def test_env_blocked_stops_before_any_fetch(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env) as (lp, _ls):
            status, body = self._get("/v1/pulls")
        self.assertEqual(status, 200, body)
        self.assertIn("gh auth login", body["env_blocked"])
        self.assertEqual(body["repos"], [])
        lp.assert_not_called()

    def test_one_repo_failing_leaves_the_others(self):
        def prs(name, state, limit):
            if name == "o/s":
                return {"repo": name, "status": "upstream-error",
                        "pull_requests": [], "error": "GitHub is down"}
            return _stub_prs(name, state, limit)

        with self._stubs(prs=prs):
            status, body = self._get("/v1/pulls")
        self.assertEqual(status, 200, body)
        by_name = {r["name"]: r for r in body["repos"]}
        self.assertEqual(by_name["o/r"]["status"], "ok")
        self.assertEqual(len(by_name["o/r"]["pulls"]), 2)
        self.assertEqual(by_name["o/s"]["status"], "upstream-error")
        self.assertEqual(by_name["o/s"]["pulls"], [])
        self.assertIn("GitHub is down", by_name["o/s"]["error"])

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/pulls")
        self.assertEqual(status, 401)


class TestDetail(_Base):
    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, view=None):
        with patch("swe.github.gh.probe_env", return_value=env), \
             patch("swe.github.gh.run_json",
                   return_value=view or gh.GhResult("ok", data=RAW_PR)):
            yield

    def test_full_row_plus_detail_fields(self):
        with self._stubs():
            status, body = self._get("/v1/pulls/o/r/5")
        self.assertEqual(status, 200, body)
        self.assertEqual((body["status"], body["repo"]), ("ok", "o/r"))
        pr = body["pr"]
        self.assertEqual(pr["number"], 5)
        self.assertEqual(pr["title"], "Fix the thing")
        self.assertEqual(pr["body"], "What broke and why this fixes it.")
        self.assertEqual(pr["checks"], "failing")
        self.assertEqual([r["name"] for r in pr["check_runs"]],
                         ["tests", "lint"])
        self.assertEqual(pr["check_runs"][0]["state"], "failing")
        self.assertEqual(pr["reviews"][0]["author"], "rev")
        self.assertEqual(pr["review_requests"], ["octocat"])
        self.assertTrue(pr["worktree"].endswith("o__r-pr5"))
        self.assertTrue(pr["suggested_prompt"].startswith(
            "Review https://github.com/o/r/pull/5"))
        self.assertIn("Failing checks: tests", pr["suggested_prompt"])
        self.assertIn(pr["worktree"], pr["suggested_prompt"])
        self.assertIn("fetched_at", body)

    def test_not_pinned_and_bad_path(self):
        with self._stubs():
            status, body = self._get("/v1/pulls/other/repo/5")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_pinned"))
        with self._stubs():
            status, body = self._get("/v1/pulls/o/r/x")
        self.assertEqual((status, body["error"]["code"]), (422, "bad_pr"))
        status, body = self._get("/v1/pulls/o/5")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_found"))

    def test_gh_blocked_reports_instead_of_fetching(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env):
            status, body = self._get("/v1/pulls/o/r/5")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "auth-required")
        self.assertIsNone(body["pr"])
        self.assertIn("gh auth login", body["error"])

    def test_missing_pr_is_a_status_not_a_crash(self):
        with self._stubs(view=gh.GhResult("not-found", error="no such PR")):
            status, body = self._get("/v1/pulls/o/r/404")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "not-found")
        self.assertIsNone(body["pr"])
        self.assertIn("no such PR", body["error"])

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/pulls/o/r/5")
        self.assertEqual(status, 401)

    def test_a_page_populated_entry_still_carries_the_extra_fields(self):
        """The Repositories page caches the same ``("pr", hk, repo, n)``
        key with a plain ``_pr_detail`` row — a phone read of that
        entry must still carry ``worktree`` and ``suggested_prompt``,
        and must not pay for a second ``gh pr view``."""
        key = ("pr", G.home_key(self.srv.home), "o/r", "5")
        G.CACHE.get(key, lambda: {
            "repo": "o/r", "status": "ok",
            "pr": ghcli._pr_detail(RAW_PR, "o/r")})
        with self._stubs(view=gh.GhResult("upstream-error",
                                          error="must not be read")):
            status, body = self._get("/v1/pulls/o/r/5")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["pr"]["title"], "Fix the thing")
        self.assertTrue(body["pr"]["worktree"].endswith("o__r-pr5"))
        self.assertTrue(body["pr"]["suggested_prompt"].startswith(
            "Review https://github.com/o/r/pull/5"))


class TestDropRepo(_Base):
    def test_merge_invalidation_covers_the_phone_reads(self):
        """``repositories._drop_repo`` runs after a merge or check
        rerun; the phone's list and detail entries for the repo must
        go with it."""
        hk = G.home_key(self.srv.home)
        list_key = ("pulls", hk, "o/r")
        detail_key = ("pr", hk, "o/r", "5")
        for key in (list_key, detail_key):
            G.CACHE.get(key, lambda: {"stale": True})
        repositories._drop_repo(self.srv.home, "o/r")
        fresh = {"fresh": True}
        for key in (list_key, detail_key):
            value, _at = G.CACHE.get(key, lambda: fresh)
            self.assertIs(value, fresh, key)


class TestOpen(_Base):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = str(Path(tmp.name) / "o__r-pr5")
        Path(self.tree).mkdir()
        self.pr = gh_pulls.shape(RAW_PR, "o/r", self.srv.home)

    def _worktree_ok(self, repo, number, home=None):
        return gh.GhResult("ok", data={
            "path": self.tree, "head": "abc123def456", "created": True})

    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, installed=True, tmux=True, fetched=None,
               worktree=None, launch=None, blocker=None, tracked=True):
        fetched = fetched or gh.GhResult("ok", data=self.pr)
        with patch("swe.remote.catalog.launch_command",
                   return_value="claude" if installed else None), \
             patch("swe.remote.catalog.launchable_harnesses",
                   return_value=[]), \
             patch("swe.remote.catalog.known_session_ids",
                   return_value=["old"]), \
             patch("swe.remote.supervise.tmux_bin",
                   return_value="/usr/bin/tmux" if tmux else None), \
             patch("swe.remote.supervise.fork_blocker",
                   return_value=blocker), \
             patch("swe.remote.catalog.untracked",
                   return_value=[] if tracked else ["claude"]), \
             patch("swe.github.gh.probe_env", return_value=env), \
             patch("swe.github.pulls.fetch", return_value=fetched), \
             patch("swe.github.manage.pr_worktree",
                   side_effect=worktree or self._worktree_ok) as wt, \
             patch("swe.remote.supervise.launch_new",
                   side_effect=launch,
                   return_value=(True, "launched", "qv-t")) as ln:
            try:
                yield wt, ln
            finally:
                # Even when the test fails: the launch thread must end
                # while the patches still stand, or it runs for real.
                self._wait()

    def _wait(self):
        for t in threading.enumerate():
            if t.name.startswith("remote-launch-"):
                t.join(timeout=5)

    def _post(self, path, body):
        status, data = self.srv.request(
            "POST", path, body=body, headers=self.hdr)
        return status, json.loads(data)

    def _open(self, body, path="/v1/pulls/o/r/5/open"):
        return self._post(path, body)

    def test_happy_path_launches_in_the_pr_worktree(self):
        with self._stubs() as (wt, ln):
            status, body = self._open({"harness": "claude"})
        self.assertEqual(status, 202, body)
        self.assertEqual((body["state"], body["note"]),
                         ("pending", "preparing worktree"))
        lid = body["launch_id"]
        self.assertEqual(wt.call_args[0][:2], ("o/r", "5"))
        args, kw = ln.call_args
        self.assertEqual(args[1:3], ("claude", self.tree))
        self.assertTrue(args[3].startswith(
            "Review https://github.com/o/r/pull/5"))
        self.assertIn("refs/pull/5/head", args[3])
        self.assertIn("Failing checks: tests", args[3])
        self.assertIn(f"at {self.tree}.", args[3])
        self.assertEqual(kw["launch_id"], lid)
        self.assertEqual(kw["binary"], "claude")
        self.assertEqual(kw["known_ids"], ["old"])
        view = self._view(lid)
        self.assertEqual(view["state"], "pending")
        self.assertEqual(view["cwd"], self.tree)
        self.assertEqual(view["note"], "Starting claude…")
        self.assertEqual(view["title"], "o/r#5 Fix the thing")

    def test_open_carries_the_pick(self):
        with self._stubs() as (_wt, ln):
            status, body = self._open(
                {"harness": "claude",
                 "model": "opus", "effort": "high"})
        self.assertEqual(status, 202, body)
        kw = ln.call_args[1]
        self.assertEqual((kw["model"], kw["effort"]), ("opus", "high"))
        view = self._view(body["launch_id"])
        self.assertEqual((view["model"], view["effort"]), ("opus", "high"))

    def test_open_refuses_a_pick_the_harness_cannot_take(self):
        # gemini declares no capabilities.model; nothing is prepared.
        with self._stubs() as (wt, ln):
            status, body = self._open(
                {"harness": "gemini", "model": "opus"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"],
                         "unsupported_launch_option")
        wt.assert_not_called()
        ln.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})

    def test_title_applies_on_adoption(self):
        state = self.srv.state
        with self._stubs():
            _s, body = self._open({"harness": "claude"})
        state.emit("_global", protocol.event(
            "session.launched", "claude:s1", "m",
            {"launch_id": body["launch_id"]}))
        view = self._view(body["launch_id"])
        self.assertEqual((view["state"], view["key"]),
                         ("started", "claude:s1"))
        self.assertEqual(state.catalog.row_for("claude:s1")["title"],
                         "o/r#5 Fix the thing")

    def test_a_second_open_waits_for_the_first(self):
        gate = threading.Event()
        seen = threading.Event()

        def slow(repo, number, home=None):
            seen.set()
            gate.wait(5)
            return self._worktree_ok(repo, number, home)

        req = {"harness": "claude"}
        with self._stubs(worktree=slow) as (_wt, ln):
            status, first = self._open(req)
            self.assertEqual(status, 202, first)
            self.assertTrue(seen.wait(5))
            status, body = self._open(req)
            self.assertEqual((status, body["error"]["code"]),
                             (409, "open_pending"))
            # Another PR in the same repo is not held up.
            status, other = self._open(req, "/v1/pulls/o/r/6/open")
            self.assertEqual(status, 202, other)
            gate.set()
        self.assertEqual(ln.call_count, 2)
        # Once launch_new has run, its pending marker is the guard.
        with self._stubs(blocker="qv-other") as (_wt, ln):
            status, body = self._open(req)
        self.assertEqual((status, body["error"]["code"]),
                         (409, "open_pending"))
        ln.assert_not_called()
        with self._stubs() as (_wt, ln):
            status, _body = self._open(req)
        self.assertEqual(status, 202)

    def test_an_untracked_harness_marker_blocks_nothing(self):
        with self._stubs(blocker="qv-old", tracked=False) as (_wt, ln):
            status, body = self._open({"harness": "claude"})
        self.assertEqual(status, 202, body)
        self.assertEqual(self._view(body["launch_id"])["state"],
                         "untracked")
        ln.assert_called_once()

    def test_client_prompt_wins_and_the_pr_title_still_names(self):
        missing = gh.GhResult("upstream-error", error="down")
        with self._stubs(fetched=missing) as (_wt, ln):
            _s, body = self._open({"harness": "claude",
                                   "prompt": "Review it."})
        self.assertEqual(ln.call_args[0][3], "Review it.")
        self.assertEqual(self._view(body["launch_id"])["title"], "o/r#5")

    def test_unreadable_pr_without_a_prompt_fails(self):
        missing = gh.GhResult("not-found", error="no such PR")
        with self._stubs(fetched=missing) as (wt, ln):
            _s, body = self._open({"harness": "claude"})
        view = self._view(body["launch_id"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("Couldn't read o/r#5", view["note"])
        wt.assert_not_called()
        ln.assert_not_called()

    def test_worktree_failure_fails_the_launch(self):
        fail = lambda *a, **k: gh.GhResult(  # noqa: E731
            "upstream-error", error="Fetch failed: denied")
        with self._stubs(worktree=fail) as (_wt, ln):
            _s, body = self._open({"harness": "claude"})
        view = self._view(body["launch_id"])
        self.assertEqual((view["state"], view["note"]),
                         ("failed", "Fetch failed: denied"))
        ln.assert_not_called()

    def test_launch_failure_and_crash_fail_the_launch(self):
        with self._stubs(launch=lambda *a, **k: (False, "tmux died", None)):
            _s, body = self._open({"harness": "claude"})
        self.assertEqual(self._view(body["launch_id"])["note"],
                         "tmux died")

        def boom(*a, **k):
            raise RuntimeError("kaboom")

        with self._stubs(worktree=boom):
            _s, body = self._open({"harness": "claude"})
        view = self._view(body["launch_id"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("kaboom", view["note"])

    def test_refusals_answer_422_before_any_launch(self):
        cases = [
            ("/v1/pulls/o/r/x/open", {"harness": "claude"}, {},
             "bad_pr"),
            ("/v1/pulls/o/r/5/open", {"harness": "Not A Name"}, {},
             "bad_command"),
            ("/v1/pulls/o/r/5/open",
             {"harness": "claude",
              "prompt": "p" * (protocol.MAX_LAUNCH_PROMPT + 1)}, {},
             "bad_command"),
            ("/v1/pulls/other/repo/5/open", {"harness": "claude"}, {},
             "not_pinned"),
            ("/v1/pulls/o/r/5/open", {"harness": "claude"},
             {"installed": False}, "not_launchable"),
            ("/v1/pulls/o/r/5/open", {"harness": "claude"},
             {"tmux": False}, "launch_failed"),
            ("/v1/pulls/o/r/5/open", {"harness": "claude"},
             {"env": gh.GhEnv(installed=True, authenticated=False)},
             "gh_unavailable"),
        ]
        for path, req, stubs, code in cases:
            with self.subTest(code=code, req=req):
                with self._stubs(**stubs) as (wt, ln):
                    status, body = self._open(req, path)
                self.assertEqual((status, body["error"]["code"]),
                                 (422, code), body)
                wt.assert_not_called()
                ln.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})
        status, body = self._open({"harness": "claude"},
                                  "/v1/pulls/o/5/open")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_found"))

    def test_unreadable_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                        timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", "/v1/pulls/o/r/5/open", body=b"{not json",
                     headers={**self.hdr,
                              "Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400, resp.read())
        with self.assertRaises(ValueError):
            pulls_mod.parse_body([1, 2])

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request(
            "POST", "/v1/pulls/o/r/5/open", body={"harness": "claude"})
        self.assertEqual(status, 401)


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_names_review_url_body_and_checks(self):
        pr = gh_pulls.shape(RAW_PR, "o/r")
        text = gh_pulls.build_prompt("o/r", pr, "/w")
        self.assertTrue(text.startswith(
            "Review https://github.com/o/r/pull/5"))
        self.assertIn("Fix the thing", text)
        self.assertIn("What broke and why this fixes it.", text)
        self.assertIn("Failing checks: tests", text)
        self.assertIn("refs/pull/5/head", text)
        self.assertIn("at /w.", text)

    def test_prompt_survives_a_bare_ref(self):
        text = gh_pulls.build_prompt("o/r", {"number": 9}, "/w")
        self.assertTrue(text.startswith(
            "Review https://github.com/o/r/pull/9"))
        self.assertIn("(no description)", text)
        self.assertNotIn("checks", text.lower())

    def test_limit_budgets_the_whole_prompt(self):
        """``suggested_prompt`` goes back to the open route verbatim,
        so the whole assembled prompt — body, the checks line and the
        closing — must fit the cap it will be validated against."""
        pr = {"number": 5, "title": "t", "body": "x" * 60_000,
              "checks": "failing",
              "check_runs": [{"name": f"ci-check-{i}-" + "n" * 120,
                              "state": "failing"} for i in range(200)],
              "url": "", "base": "main", "branch": "b"}
        text = gh_pulls.build_prompt("o/r", pr, "/w",
                                     limit=protocol.MAX_LAUNCH_PROMPT)
        self.assertLessEqual(len(text), protocol.MAX_LAUNCH_PROMPT)
        self.assertTrue(text.startswith("Review https://"))
        self.assertIn("Failing checks: ", text)
        tighter = gh_pulls.build_prompt("o/r", pr, "/w", limit=8_000)
        self.assertLessEqual(len(tighter), 8_000)


if __name__ == "__main__":
    unittest.main()
