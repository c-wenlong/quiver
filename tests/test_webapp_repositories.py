"""The Repositories page's data: dashboard boards, the focus page, PR
detail, the gh cache, the merge write, and the routes with auth. No real
``gh`` runs: reads patch ``gh``, writes run a fake ``gh`` on PATH."""

import json
import os
import shutil
import sys
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
from quiver.github import shared as G
from quiver.webapp.pages import repositories as R

from tests.test_remote_hub import HubBase
from tests.test_webapp import app_js_function

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octo")

_FAKE_GH = r"""#!PYTHON
import fcntl, json, os, sys
args = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
# One call at a time: mark_threads_read runs several at once, and each
# reads, changes and writes the whole state file. The lock is released
# when the process exits.
lock = open(path + ".lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX)
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
with open(path) as f:
    state = json.load(f)

def save():
    with open(path, "w") as f:
        json.dump(state, f)

def fail(msg, code=1):
    sys.stderr.write(msg)
    sys.exit(code)

if args[:2] == ["auth", "status"]:
    if state.get("signed_out"):
        fail("You are not logged into any GitHub hosts.")
    sys.stderr.write("Logged in to github.com account octo (keyring)\n")
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    pr = state["prs"].get(args[2])
    if pr is None:
        fail("GraphQL: Could not resolve to a PullRequest with the number of " + args[2])
    print(json.dumps(pr))
    sys.exit(0)
if args[:2] == ["pr", "merge"]:
    if state.get("merge_error"):
        fail(state["merge_error"])
    pr = state["prs"][args[2]]
    method = [a for a in args if a in ("--squash", "--merge", "--rebase")][0][2:]
    if "--auto" in args:
        pr["autoMergeRequest"] = {"mergeMethod": method.upper(), "enabledAt": "2026-10-24T00:00:00Z"}
    else:
        pr["state"], pr["mergedAt"] = "MERGED", "2026-10-24T00:00:00Z"
    save()
    sys.exit(0)
if args[:1] == ["api"] and "--method" in args:
    at = args.index("--method")
    verb, target = args[at + 1], args[at + 2]
    if state.get("api_error"):
        fail(state["api_error"])
    if verb == "PATCH":
        thread = target.rsplit("/", 1)[1]
        if thread in state.get("fail_ids", []):
            fail("HTTP 500: Server Error")
        for note in state["notes"]:
            if note["id"] == thread:
                note["unread"] = False
        save()
        sys.exit(0)
    if verb == "PUT" and target == "notifications":
        for note in state["notes"]:
            note["unread"] = False
        save()
        sys.exit(0)
if args[:1] == ["api"] and args[1].startswith("notifications"):
    print(json.dumps(state["notes"]))
    sys.exit(0)
if args[:2] == ["repo", "clone"]:
    # "clone_from" is a local repo standing in for GitHub.
    import subprocess
    if not state.get("clone_from"):
        fail("GraphQL: Could not resolve to a Repository with the name '" + args[2] + "'.")
    sys.exit(subprocess.run(["git", "clone", "-q", "--bare", state["clone_from"], args[3]]).returncode)
if args[:2] == ["run", "rerun"]:
    if args[2] not in state.get("runs", []):
        fail("HTTP 404: Not Found (https://api.github.com/repos/o/a/actions/runs/" + args[2] + ")")
    if state.get("rerun_error"):
        fail(state["rerun_error"])
    sys.exit(0)
# Pinning, repo search and the catalog sync. "repos" maps a name, as
# typed in lower case, to how GitHub spells it; "orgs" is the login's
# orgs (None fails the call); "lists" maps an owner to its repo names.
if args[:2] == ["repo", "view"]:
    name = state.get("repos", {}).get(args[2].lower())
    if name is None:
        fail("GraphQL: Could not resolve to a Repository with the name '" + args[2] + "'.")
    print(json.dumps({"nameWithOwner": name}))
    sys.exit(0)
if args[:2] == ["api", "user/orgs"]:
    if state.get("orgs") is None:
        fail("HTTP 403: Resource not accessible")
    print("\n".join(state["orgs"]))
    sys.exit(0)
if args[:2] == ["search", "repos"]:
    if state.get("search_error"):
        fail(state["search_error"])
    print(json.dumps([{"fullName": n, "description": n + " blurb", "stargazersCount": 3,
                       "updatedAt": "2026-10-20T00:00:00Z"} for n in state.get("found", [])]))
    sys.exit(0)
if args[:2] == ["repo", "list"]:
    names = state.get("lists", {}).get(args[2])
    if names is None:
        fail("HTTP 502: Bad Gateway")
    print(json.dumps([{"nameWithOwner": args[2] + "/" + n, "description": n, "stargazerCount": 1,
                       "updatedAt": "2026-10-20T00:00:00Z", "isPrivate": False} for n in names]))
    sys.exit(0)
fail("fake gh: unexpected " + " ".join(args), 2)
"""


class FakeGh:
    """A ``gh`` executable on PATH that logs each call and answers from
    a JSON state file: PRs by number, notifications, failure switches.
    The real ``gh.run_gh`` subprocess path runs against it, so a test
    sees the exact argv a write would send, and nothing leaves the box."""

    def __init__(self, case: unittest.TestCase, **state):
        tmp = Path(tempfile.mkdtemp())
        case.addCleanup(shutil.rmtree, tmp, True)
        self.bin = tmp / "bin"
        self.bin.mkdir()
        script = self.bin / "gh"
        script.write_text(_FAKE_GH.replace("PYTHON", sys.executable, 1))
        script.chmod(0o755)
        self.state_file, self.log_file = tmp / "state.json", tmp / "calls.log"
        self.log_file.write_text("")
        self.write({"prs": {}, "notes": [], **state})
        env = patch.dict(os.environ, {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_GH_STATE": str(self.state_file), "FAKE_GH_LOG": str(self.log_file)})
        env.start()
        case.addCleanup(env.stop)

    def read(self) -> dict:
        return json.loads(self.state_file.read_text())

    def write(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state))

    def update(self, **changes) -> None:
        self.write({**self.read(), **changes})

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log_file.read_text().splitlines()]

    def writes(self) -> list[list[str]]:
        """The calls that change GitHub: merges, re-runs and non-GET api calls."""
        return [c for c in self.calls()
                if c[:2] in (["pr", "merge"], ["run", "rerun"]) or (c[:1] == ["api"] and "--method" in c)]


def gh_pr(number=7, **over) -> dict:
    """A ``gh pr view --json`` reply, open and mergeable unless told."""
    return {"number": number, "title": "Fix the thing", "state": "OPEN", "isDraft": False,
            "author": {"login": "me"}, "headRefName": "fix", "baseRefName": "main",
            "body": "", "url": f"https://github.com/o/a/pull/{number}",
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
            "reviewDecision": "APPROVED", "statusCheckRollup": [], "latestReviews": [],
            "reviewRequests": [], "labels": [], "additions": 1, "deletions": 0,
            "changedFiles": 1, "createdAt": "2026-10-23T00:00:00Z",
            "updatedAt": "2026-10-23T00:00:00Z", "mergedAt": None,
            "autoMergeRequest": None, **over}


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
            R.get(self.ctx, {"repo": "o/a", "pr": "007"})  # same entry
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
        for bad in ("abc", "0", "9" * 5000, "\u0667", "1234567890"):
            with self.assertRaises(PageError) as caught:
                R.get(self.ctx, {"repo": "o/a", "pr": bad})
            self.assertEqual((caught.exception.status, caught.exception.code),
                             (400, "bad_pr"), bad)
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


class TestCacheDrop(_Base):
    def test_drop_forgets_matches_and_a_reply_in_flight(self):
        G.CACHE.get(("a", 1), lambda: "old")
        G.CACHE.get(("b", 1), lambda: "keep")
        G.CACHE.drop(lambda k: k[0] == "a")
        self.assertEqual(G.CACHE.get(("a", 1), lambda: "new")[0], "new")
        self.assertEqual(G.CACHE.get(("b", 1), lambda: "other")[0], "keep")

        # A fetch that began before the write returns its reply to its
        # own caller, but the next caller reads again.
        def stale():
            G.CACHE.drop(lambda k: k[0] == "c")
            return "stale"
        self.assertEqual(G.CACHE.get(("c", 1), stale)[0], "stale")
        self.assertEqual(G.CACHE.get(("c", 1), lambda: "fresh")[0], "fresh")

    def test_dropped_keys_leave_no_locks_behind(self):
        G.CACHE.get(("pr", 1), lambda: "x")
        G.CACHE.drop(lambda k: k[0] == "pr")
        self.assertNotIn(("pr", 1), G.CACHE._key_locks)

        def stale():
            G.CACHE.drop(lambda k: False)  # a write lands mid-fetch
            return "stale"
        G.CACHE.get(("pr", 2), stale)
        self.assertNotIn(("pr", 2), G.CACHE._key_locks)
        self.assertEqual(G.CACHE._users, {})


class TestMerge(_Base):
    """``post(ctx, "merge", ...)`` against a temp HOME's pins and a fake
    ``gh``: the success paths, then every refusal, none of which may
    reach ``gh pr merge``."""

    def setUp(self):
        super().setUp()
        _pin(self.home, "o/a")
        self.gh = FakeGh(self, prs={"7": gh_pr(7)})

    def merge(self, **body):
        return R.post(self.ctx, "merge", {"repo": "o/a", "number": 7, **body})

    def refused(self, status, code, **body):
        with self.assertRaises(PageError) as caught:
            self.merge(**body)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code),
                         caught.exception.message)
        self.assertEqual(self.gh.writes(), [])
        return caught.exception

    def test_squash_merges_and_answers_with_the_pr_read_again(self):
        body = self.merge()
        self.assertEqual(self.gh.writes(), [["pr", "merge", "7", "--repo", "o/a", "--squash"]])
        self.assertEqual((body["repo"], body["number"], body["method"], body["auto"], body["merged"]),
                         ("o/a", 7, "squash", False, True))
        self.assertEqual(body["pr"]["state"], "merged")
        self.assertEqual(body["github"]["status"], "ok")
        self.assertEqual(body["machine"], "hub-box")
        # Read fresh before the write, and again after it.
        views = [c for c in self.gh.calls() if c[:2] == ["pr", "view"]]
        self.assertEqual(len(views), 2)

    def test_auto_merge_arms_with_the_chosen_method(self):
        body = self.merge(method="rebase", auto=True)
        self.assertEqual(self.gh.writes(), [["pr", "merge", "7", "--repo", "o/a", "--rebase", "--auto"]])
        self.assertFalse(body["merged"])
        self.assertEqual(body["pr"]["state"], "open")
        self.assertEqual(body["pr"]["auto_merge"]["method"], "rebase")

    def test_the_pin_is_matched_as_pinned_and_number_as_text(self):
        body = R.post(self.ctx, "merge", {"repo": "O/A", "number": "7", "method": "merge"})
        self.assertEqual(body["repo"], "o/a")
        self.assertEqual(self.gh.writes(), [["pr", "merge", "7", "--repo", "o/a", "--merge"]])

    def test_a_merge_drops_the_repos_cached_views(self):
        hk = G.home_key(self.home)
        for key in (("board", hk, "o/a"), ("focus", hk, "o/a", "pull_requests", 50),
                    ("pr", hk, "o/a", "7"), ("board", hk, "o/b")):
            G.CACHE.get(key, lambda: {"status": "ok", "cached": True})
        self.merge()
        again = lambda: {"status": "ok", "cached": False}
        self.assertFalse(G.CACHE.get(("board", hk, "o/a"), again)[0]["cached"])
        self.assertFalse(G.CACHE.get(("focus", hk, "o/a", "pull_requests", 50), again)[0]["cached"])
        self.assertTrue(G.CACHE.get(("board", hk, "o/b"), again)[0]["cached"])

    def test_a_stale_open_view_cannot_merge_a_pr_merged_since(self):
        # The sheet's cached read says open; GitHub says merged now.
        R.get(self.ctx, {"repo": "o/a", "pr": "7"})
        self.gh.update(prs={"7": gh_pr(7, state="MERGED")})
        err = self.refused(409, "not_open")
        self.assertIn("merged", err.message)

    def test_the_apps_blocks(self):
        self.gh.update(prs={"7": gh_pr(7, state="CLOSED")})
        self.refused(409, "not_open")
        self.gh.update(prs={"7": gh_pr(7, isDraft=True)})
        self.assertEqual(self.refused(409, "draft").message, "Draft PRs can't merge")
        self.assertEqual(self.refused(409, "draft", auto=True).message, "Draft PRs can't merge")
        self.gh.update(prs={"7": gh_pr(7, mergeable="CONFLICTING")})
        self.assertEqual(self.refused(409, "conflicting").message, "Resolve the conflicts first")
        armed = {"mergeMethod": "SQUASH", "enabledAt": "2026-10-24T00:00:00Z"}
        self.gh.update(prs={"7": gh_pr(7, autoMergeRequest=armed)})
        self.assertEqual(self.refused(409, "auto_merge_armed", auto=True).message,
                         "Auto-merge is already armed")

    def test_conflicts_still_allow_arming_auto_merge_as_the_app_does(self):
        self.gh.update(prs={"7": gh_pr(7, mergeable="CONFLICTING")})
        self.assertFalse(self.merge(auto=True)["merged"])

    def test_failing_checks_are_left_to_github(self):
        failing = [{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}]
        self.gh.update(prs={"7": gh_pr(7, statusCheckRollup=failing)},
                       merge_error="GraphQL: Required status check \"ci\" is failing")
        err = self.refused_after_gh(502, "merge_failed")
        self.assertIn("Required status check", err.message)

    def refused_after_gh(self, status, code, **body):
        with self.assertRaises(PageError) as caught:
            self.merge(**body)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        return caught.exception

    def test_bad_input_is_refused_before_any_gh_call(self):
        for body, code in (({"repo": ""}, "bad_repo"), ({"repo": 5}, "bad_repo"),
                           ({"repo": "not a repo"}, "bad_repo"),
                           ({"number": "abc"}, "bad_pr"), ({"number": 0}, "bad_pr"),
                           ({"number": -3}, "bad_pr"), ({"number": True}, "bad_pr"),
                           ({"number": "\u0667"}, "bad_pr"), ({"number": None}, "bad_pr"),
                           ({"number": "9" * 5000}, "bad_pr"), ({"number": "1234567890"}, "bad_pr"),
                           ({"method": "yolo"}, "bad_method"), ({"method": None}, "bad_method"),
                           ({"auto": "yes"}, "bad_auto")):
            with self.subTest(body=body):
                self.refused(422, code, **body)
        self.assertEqual(self.gh.calls(), [])

    def test_unpinned_repos_unknown_prs_and_actions(self):
        self.refused(404, "not_pinned", repo="o/elsewhere")
        self.refused(404, "unknown_pr", number=99)
        with self.assertRaises(PageError) as caught:
            R.post(self.ctx, "approve", {"repo": "o/a", "number": 7})
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.gh.writes(), [])

    def test_a_signed_out_gh_is_refused_with_the_setup_words(self):
        self.gh.update(signed_out=True)
        err = self.refused(409, "gh_auth_required")
        self.assertIn("gh auth login", err.message)

    def test_two_merges_at_once_merge_once(self):
        results = []

        def one():
            try:
                results.append(self.merge()["merged"])
            except PageError as exc:
                results.append(exc.code)
        threads = [threading.Thread(target=one) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(map(str, results)), ["True", "not_open"])
        self.assertEqual(len(self.gh.writes()), 1)


STATIC = Path(R.__file__).resolve().parent.parent / "static" / "pages"


def _node(script: str, stdin: str = "") -> subprocess.CompletedProcess:
    """Run ``script`` from a file: the bundle is too big for ``node -e``."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run.js"
        path.write_text(script)
        return subprocess.run(["node", str(path)], input=stdin, capture_output=True,
                              text=True, timeout=30)


class TestPin(_Base):
    """The Add repository sheet: ``get(search=...)`` and ``post("add")``
    against a temp HOME and a fake ``gh``."""

    def pins(self):
        return [r["name"] for r in G.pinned(self.home)]

    def test_search_reads_the_hubs_catalog_without_gh(self):
        from quiver.github import catalog

        fake = FakeGh(self)
        catalog.write([{"full_name": "o/quiver", "owner": "o", "description": "arrows", "stars": 4}],
                      ["o"], path=paths.github_catalog_for(self.home))
        body = R.get(self.ctx, {"search": "  quiv "})
        self.assertEqual((body["status"], body["source"], body["query"]), ("ok", "catalog", "quiv"))
        self.assertEqual([r["name"] for r in body["results"]], ["o/quiver"])
        self.assertEqual(fake.calls(), [])

    def test_search_falls_through_to_github_and_is_cached(self):
        fake = FakeGh(self, orgs=[], found=["octo/new"])
        body = R.get(self.ctx, {"search": "new"})
        self.assertEqual((body["source"], body["github"]["status"]), ("github", "ok"))
        self.assertEqual(body["results"][0]["name"], "octo/new")
        R.get(self.ctx, {"search": "NEW"})
        self.assertEqual(sum(c[:2] == ["search", "repos"] for c in fake.calls()), 1)

    def test_search_failures_are_answered_not_raised(self):
        FakeGh(self, signed_out=True)
        body = R.get(self.ctx, {"search": "q"})
        self.assertEqual(body["status"], "auth-required")
        self.assertIn("gh auth login", body["error"])
        self.assertEqual(R.get(self.ctx, {"search": "   "})["results"], [])
        with self.assertRaises(PageError) as caught:
            R.get(self.ctx, {"search": "x" * 201})
        self.assertEqual(caught.exception.code, "bad_search")

    def test_add_pins_githubs_spelling_and_answers_the_pins(self):
        _pin(self.home, "o/a")
        FakeGh(self, repos={"o/quiver": "O/Quiver"})
        body = R.post(self.ctx, "add", {"repo": "https://github.com/o/quiver/pull/3"})
        self.assertEqual(body["repo"], "O/Quiver")
        self.assertEqual([r["name"] for r in body["repos"]], ["o/a", "O/Quiver"])
        self.assertEqual(self.pins(), ["o/a", "O/Quiver"])
        self.assertEqual(body["machine"], "hub-box")

    def test_add_drops_cached_searches(self):
        self._fake = FakeGh(self, orgs=[], found=["o/b"], repos={"o/b": "o/b"})
        R.get(self.ctx, {"search": "b"})
        R.post(self.ctx, "add", {"repo": "o/b"})
        R.get(self.ctx, {"search": "b"})
        # The second search ran again rather than answer from before the pin.
        self.assertEqual(sum(c[:2] == ["search", "repos"] for c in self._fake.calls()), 2)

    def test_add_refusals(self):
        fake = FakeGh(self, repos={})
        for body, status, code in (({}, 422, "bad_repo"), ({"repo": 7}, 422, "bad_repo"),
                                   ({"repo": "not a repo"}, 422, "bad_repo"),
                                   ({"repo": "o/gone"}, 404, "unknown_repo")):
            with self.assertRaises(PageError) as caught:
                R.post(self.ctx, "add", body)
            self.assertEqual((caught.exception.status, caught.exception.code), (status, code), body)
        self.assertEqual(self.pins(), [])
        self.assertEqual([c[:2] for c in fake.calls() if c[0] != "auth"], [["repo", "view"]])
        fake.update(signed_out=True)
        G.CACHE.clear()
        with self.assertRaises(PageError) as caught:
            R.post(self.ctx, "add", {"repo": "o/a"})
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "gh_auth_required"))

    def test_an_unpinned_repo_says_so_in_plain_words(self):
        with self.assertRaises(PageError) as caught:
            R.get(self.ctx, {"repo": "x/y"})
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "not_pinned"))
        self.assertEqual(caught.exception.message, "x/y is not pinned on the hub.")


class TestManagePins(_Base):
    """The gear popover's Unpin and the rail's drag: ``post("remove")``
    and ``post("reorder")`` on the hub's own pin file, with no ``gh``."""

    def setUp(self):
        super().setUp()
        _pin(self.home, "o/a", "o/b", "x/c")
        self.gh = FakeGh(self)

    def pins(self):
        return [r["name"] for r in G.pinned(self.home)]

    def refused(self, action, body, status, code):
        with self.assertRaises(PageError) as caught:
            R.post(self.ctx, action, body)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code), body)

    def test_unpin_answers_the_pins_and_drops_the_repos_views(self):
        G.CACHE.get(("board", G.home_key(self.home), "o/b"), lambda: "board")
        G.CACHE.get(("board", G.home_key(self.home), "o/a"), lambda: "kept")
        body = R.post(self.ctx, "remove", {"repo": "O/B"})
        self.assertEqual(body["repo"], "o/b")
        self.assertEqual([r["name"] for r in body["repos"]], ["o/a", "x/c"])
        self.assertEqual(self.pins(), ["o/a", "x/c"])
        self.assertEqual(G.CACHE.get(("board", G.home_key(self.home), "o/b"), lambda: "fresh")[0], "fresh")
        self.assertEqual(G.CACHE.get(("board", G.home_key(self.home), "o/a"), lambda: "fresh")[0], "kept")
        self.assertEqual(self.gh.calls(), [])

    def test_unpin_refusals(self):
        self.refused("remove", {"repo": "o/zzz"}, 404, "not_pinned")
        self.refused("remove", {"repo": "nope"}, 422, "bad_repo")
        self.refused("remove", {}, 422, "bad_repo")
        self.assertEqual(self.pins(), ["o/a", "o/b", "x/c"])

    def test_reorder_takes_the_named_order_and_keeps_the_rest(self):
        body = R.post(self.ctx, "reorder", {"order": ["x/c", "o/a", "o/b"]})
        self.assertEqual([r["name"] for r in body["repos"]], ["x/c", "o/a", "o/b"])
        # A partial list moves those to the front; a pin it doesn't name
        # (one added from another tab meanwhile) keeps its place after them.
        body = R.post(self.ctx, "reorder", {"order": ["o/b", "gone/repo"]})
        self.assertEqual(self.pins(), ["o/b", "x/c", "o/a"])
        self.assertEqual(self.gh.calls(), [])

    def test_reorder_refusals(self):
        for order in (None, "o/a", [1], [""], ["o/a"] * (R.REORDER_MAX + 1)):
            self.refused("reorder", {"order": order}, 422, "bad_order")
        self.assertEqual(self.pins(), ["o/a", "o/b", "x/c"])


class TestRerun(_Base):
    """An Actions row's Re-run: ``post("rerun")`` against a fake ``gh``."""

    def setUp(self):
        super().setUp()
        _pin(self.home, "o/a")
        self.gh = FakeGh(self, runs=["4242"])

    def refused(self, status, code, **body):
        with self.assertRaises(PageError) as caught:
            R.post(self.ctx, "rerun", {"repo": "o/a", "run": 4242, **body})
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code),
                         caught.exception.message)
        return caught.exception

    def test_failed_jobs_and_all_jobs(self):
        body = R.post(self.ctx, "rerun", {"repo": "O/A", "run": 4242, "failed": True})
        self.assertEqual(body, {"repo": "o/a", "run": 4242, "failed_only": True, "rerun": True,
                                "machine": "hub-box"})
        R.post(self.ctx, "rerun", {"repo": "o/a", "run": "4242"})
        self.assertEqual(self.gh.writes(), [["run", "rerun", "4242", "--repo", "o/a", "--failed"],
                                            ["run", "rerun", "4242", "--repo", "o/a"]])

    def test_a_rerun_drops_the_repos_cached_views(self):
        hk = G.home_key(self.home)
        G.CACHE.get(("board", hk, "o/a"), lambda: "old runs")
        R.post(self.ctx, "rerun", {"repo": "o/a", "run": 4242})
        self.assertEqual(G.CACHE.get(("board", hk, "o/a"), lambda: "fresh")[0], "fresh")

    def test_refusals(self):
        for body, code in (({"run": "12a"}, "bad_run"), ({"run": True}, "bad_run"), ({"run": 0}, "bad_run"),
                           ({"run": "9" * 21}, "bad_run"), ({"failed": "yes"}, "bad_failed"),
                           ({"repo": ""}, "bad_repo")):
            with self.subTest(body=body):
                self.refused(422, code, **body)
        self.refused(404, "not_pinned", repo="o/elsewhere")
        self.assertEqual(self.gh.calls(), [])
        self.refused(404, "unknown_run", run=99)
        self.gh.update(rerun_error="run 4242 cannot be rerun; This workflow is already running")
        err = self.refused(502, "rerun_failed")
        self.assertIn("already running", err.message)

    def test_a_signed_out_gh_is_refused_with_the_setup_words(self):
        self.gh.update(signed_out=True)
        err = self.refused(409, "gh_auth_required")
        self.assertIn("gh auth login", err.message)
        self.assertEqual(self.gh.writes(), [])

    def test_a_sign_out_since_the_cached_check_reads_as_one(self):
        G.probe(self.home)  # signed in, and cached
        self.gh.update(signed_out=True, rerun_error="HTTP 401: Bad credentials")
        self.clock.t += 10  # past the refresh floor
        self.refused(409, "gh_auth_required")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestPoll(unittest.TestCase):
    """GhUI.poll under node, with a fake clock, timer, route and tab."""

    SCRIPT = """
let now = 1000000, hidden = false, hash = "#/repositories", tick = null, onShow = null;
global.Date.now = () => now;
global.setInterval = (fn) => { tick = fn; return 1; };
global.clearInterval = () => { tick = null; };
global.location = { get hash() { return hash; } };
global.document = { get hidden() { return hidden; },
  addEventListener: (name, fn) => { if (name === "visibilitychange") onShow = fn; } };
global.esc = (s) => String(s); global.Fmt = { ago: () => "" };
GITHUB_JS
const runs = [];
let busy = false; // a sheet open: the page skips the read and says so
const read = () => { if (busy) return false; runs.push(now); };
GhUI.poll("repositories", read);
const step = (ms, h) => { now += ms; if (h !== undefined) hidden = h; };
step(60000); tick();                     // on screen: the minute tick reads
step(30000, true);                       // the tab goes to the background
step(30000); tick();                     // hidden: the tick is skipped
step(50000, false); onShow();            // back 2m10s after the last read: reads at once
step(20000); onShow();                   // back again 20s later: nothing yet
step(40000, false); busy = true; tick(); // a tick the page skips (a sheet open)...
step(5000, true); busy = false;
step(25000, false); onShow();            // ...doesn't count: back 90s after the read, it reads
step(5000); hash = "#/inbox"; tick();    // another page: the poll stops
process.stdout.write(JSON.stringify({ runs: runs.map((t) => t - 1000000), stopped: tick === null }));
"""

    def test_hidden_ticks_are_skipped_and_a_return_catches_up(self):
        script = self.SCRIPT.replace("GITHUB_JS", app_js_function("currentRoute") + (STATIC / "github.js").read_text())
        done = _node(script)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"runs": [60000, 170000, 260000], "stopped": True})

    def test_a_401_stops_the_poll_under_the_gate(self):
        """GhUI.gate: the gate, and no later tick paints the page's last
        rows over it (and its pair fields)."""
        script = self.SCRIPT.replace("GITHUB_JS", (STATIC / "github.js").read_text()).split("const step =")[0]
        script = ("const gates = []; global.renderGate = (m) => gates.push(m);\n" + script
                  + "GhUI.gate('repositories');\n"
                  "process.stdout.write(JSON.stringify({ gates: gates.length, stopped: tick === null }));\n")
        done = _node(script)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"gates": 1, "stopped": True})


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class TestPrWorktree(_Base):
    """The PR sheet's Open in session, its hub half: ``post("worktree")``
    with a fake ``gh`` whose clone copies a local repo that has GitHub's
    ``refs/pull/7/head``. The browser starts the harness itself through
    ``POST /v1/sessions``, which has its own tests."""

    def setUp(self):
        super().setUp()
        _pin(self.home, "o/a")
        src = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, src, True)
        git = ["git", "-C", str(src), "-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "the pr"], check=True)
        subprocess.run(git + ["update-ref", "refs/pull/7/head", "HEAD"], check=True)
        self.head = subprocess.run(git + ["rev-parse", "--short=12", "HEAD"], check=True,
                                   capture_output=True, text=True).stdout.strip()
        self.gh = FakeGh(self, clone_from=str(src))
        self.src = src

    def test_checks_the_head_out_in_the_hubs_home_then_reuses_it(self):
        body = R.post(self.ctx, "worktree", {"repo": "O/A", "number": "7"})
        path = Path(body["path"])
        self.assertEqual((body["repo"], body["number"], body["created"], body["head"]),
                         ("o/a", 7, True, self.head))
        self.assertEqual(path, paths.worktrees_dir_for(self.home) / "o__a-pr7")
        self.assertTrue((paths.github_mirrors_for(self.home) / "o__a.git").is_dir())
        out = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%s"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "the pr")
        again = R.post(self.ctx, "worktree", {"repo": "o/a", "number": 7})
        self.assertEqual((again["path"], again["created"]), (body["path"], False))
        self.assertEqual(sum(c[:2] == ["repo", "clone"] for c in self.gh.calls()), 1)

    def test_a_reused_checkout_reports_its_own_head(self):
        # The PR moved on after the first open: the checkout stays put,
        # and the answer says so instead of naming the new head.
        first = R.post(self.ctx, "worktree", {"repo": "o/a", "number": 7})
        src = self.src
        git = ["git", "-C", str(src), "-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "more"], check=True)
        subprocess.run(git + ["update-ref", "refs/pull/7/head", "HEAD"], check=True)
        new = subprocess.run(git + ["rev-parse", "--short=12", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        again = R.post(self.ctx, "worktree", {"repo": "o/a", "number": 7})
        self.assertFalse(again["created"])
        self.assertEqual(again["head"], first["head"])
        self.assertEqual(again["pr_head"], new)

    def test_failures_name_the_step(self):
        with self.assertRaises(PageError) as caught:
            R.post(self.ctx, "worktree", {"repo": "o/a", "number": 8})
        self.assertEqual((caught.exception.status, caught.exception.code), (502, "worktree_failed"))
        self.assertTrue(caught.exception.message.startswith("Fetch failed:"), caught.exception.message)
        for body, status, code in (({"repo": "o/a", "number": "x"}, 422, "bad_pr"),
                                   ({"repo": "o/zzz", "number": 7}, 404, "not_pinned")):
            with self.assertRaises(PageError) as caught:
                R.post(self.ctx, "worktree", body)
            self.assertEqual((caught.exception.status, caught.exception.code), (status, code))

    def test_checkouts_of_one_repo_wait_for_each_other(self):
        # Two PRs of one repo share its mirror's FETCH_HEAD: the second
        # checkout must not start until the first has finished.
        # The one mirror lock table lives in manage; a differently
        # cased name is the same mirror.
        from quiver.github import manage
        lock = manage._mirror_lock(manage.mirror_path("O/A", self.home))
        lock.acquire()
        done = []
        worker = threading.Thread(target=lambda: done.append(
            R.post(self.ctx, "worktree", {"repo": "o/a", "number": 7})))
        worker.start()
        worker.join(0.5)
        self.assertEqual(done, [])
        self.assertFalse(any(c[:2] == ["repo", "clone"] for c in self.gh.calls()))
        lock.release()
        worker.join(30)
        self.assertEqual(done[0]["head"], self.head)

    def test_a_checkout_holds_the_mirror_from_fetch_to_worktree(self):
        # Two PRs of one repo share the mirror's FETCH_HEAD: a second
        # call must not fetch while the first is between its fetch and
        # its checkout, whoever the caller is.
        from quiver.github import manage
        R.post(self.ctx, "worktree", {"repo": "o/a", "number": 7})  # mirror exists
        in_fetch, release = threading.Event(), threading.Event()
        fetches = []
        real = manage.run_git

        def run_git(args, *a, **k):
            if "fetch" in args:
                fetches.append(threading.current_thread().name)
                if len(fetches) == 1:
                    in_fetch.set()
                    release.wait(10)
            return real(args, *a, **k)

        with patch.object(manage, "run_git", side_effect=run_git):
            first = threading.Thread(name="first", target=manage.pr_worktree,
                                     args=("o/a", "7", self.home))
            second = threading.Thread(name="second", target=manage.pr_worktree,
                                      args=("o/a", "7", self.home))
            first.start()
            self.assertTrue(in_fetch.wait(10))
            second.start()
            second.join(0.5)
            self.assertEqual(fetches, ["first"])
            release.set()
            first.join(30)
            second.join(30)
        self.assertEqual(fetches, ["first", "second"])

    def test_harnesses_are_what_the_hub_can_launch(self):
        rows = [{"name": "claude", "command": "claude",
                 "display_name": "Claude Code", "tracked": True,
                 "launch_model": True, "launch_effort": True}]
        with patch("quiver.remote.catalog.launchable_harnesses",
                   return_value=rows), \
             patch("quiver.remote.catalog.recent_models",
                   return_value={"claude": ["opus", "sonnet"]}) as recent:
            body = R.get(self.ctx, {"harnesses": "1"})
        # Models come from the hub's own home, not the process's.
        self.assertEqual(recent.call_args[0][0], self.ctx.home)
        self.assertEqual(body["harnesses"], [{
            "name": "claude", "display_name": "Claude Code",
            "tracked": True, "launch_model": True,
            "launch_effort": True, "last_model": "opus",
            "recent_models": ["opus", "sonnet"]}])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestMarkdown(unittest.TestCase):
    """GhUI.markdown, run under node with the two globals it borrows."""

    def render(self, body: str) -> str:
        script = (
            "global.esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')"
            ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;')"
            ".replace(/'/g, '&#39;');\n"
            "global.safeHref = (u) => /^(https?:\\/\\/|mailto:)/i.test(String(u || '').trim()) ? esc(String(u).trim()) : '';\n"
            "global.Fmt = { ago: () => '' };\n"
            + (STATIC / "github.js").read_text()
            + "\nprocess.stdout.write(GhUI.markdown(require('fs').readFileSync(0, 'utf8')));"
            + "\nprocess.exit(0);"
        )
        done = _node(script, stdin=body)
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

    def test_bare_urls_keep_their_underscores_and_stars(self):
        url = "https://github.com/o/r/blob/main/__init__.py"
        html = self.render(f"see {url} and **bold**")
        self.assertIn(f'<a href="{url}" target="_blank" rel="noopener">{url}</a>', html)
        self.assertIn("<strong>bold</strong>", html)

    def test_bare_urls_stop_at_escaped_quotes_and_brackets(self):
        html = self.render('say "https://example.com/a" and https://x.io/p>q, https://x.io/?a=1&b=2')
        self.assertIn('href="https://example.com/a"', html)
        self.assertIn('>https://x.io/p</a>&gt;q', html)
        self.assertIn('href="https://x.io/?a=1&amp;b=2"', html)
        self.assertNotIn("&quot;\"", html)

    def test_autolinks_become_links(self):
        html = self.render("go <https://y.io/z> or <me_x@ex.io>, not `<https://c.io>`, <https://[2001:db8::1]/a>")
        self.assertIn('<a href="https://y.io/z" target="_blank" rel="noopener">https://y.io/z</a>', html)
        self.assertIn('<a href="https://[2001:db8::1]/a" target="_blank" rel="noopener">https://[2001:db8::1]/a</a>', html)
        self.assertIn('<a href="mailto:me_x@ex.io" target="_blank" rel="noopener">me_x@ex.io</a>', html)
        self.assertIn("<code>&lt;https://c.io&gt;</code>", html)

    def test_fenced_code_keeps_its_blank_lines(self):
        html = self.render("<p>x</p>\n\n```\na\n\n\n\nb\n```")
        self.assertIn("a\n\n\n\nb", html)

    def test_links_keep_balanced_parentheses(self):
        html = self.render("[wiki](https://en.wikipedia.org/wiki/Foo_(bar)) after")
        self.assertIn('<a href="https://en.wikipedia.org/wiki/Foo_(bar)" target="_blank" rel="noopener">wiki</a> after', html)

    def test_double_backtick_spans_hold_a_backtick(self):
        html = self.render("run ``a `b` c`` now")
        self.assertIn("<code>a `b` c</code> now", html)

    def test_code_inside_an_html_quote_survives(self):
        html = self.render("<blockquote>Use `foo` &amp; retry</blockquote>")
        self.assertIn("<code>foo</code> &amp; retry", html)
        self.assertNotIn("undefined", html)

    def test_code_spans_never_land_in_an_href(self):
        html = self.render("[file](https://example.com/a`b`c) and https://x.io/p`q`")
        self.assertNotIn("<code>b</code>c\"", html)
        self.assertNotRegex(html, r'href="[^"]*<code>')
        self.assertIn("<code>b</code>", html)

    def test_scripts_and_unsafe_links_never_render(self):
        html = self.render("<script>alert(1)</script>[x](javascript:alert(1)) <b onclick=x>hi</b>")
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html.replace("&quot;", ""))
        self.assertNotIn("onclick", html)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestBundle(unittest.TestCase):
    def test_both_pages_register_from_the_served_bundle(self):
        """Each script runs in its own scope in /pages.js: GhUI has to
        reach inbox.js and repositories.js as a global."""
        from quiver.webapp import pages
        stubs = (
            "const registered = [];\n"
            "global.registerPage = (id) => registered.push(id);\n"
            "global.window = { addEventListener() {} };\n"
            "global.document = { addEventListener() {} };\n"
            "global.location = { hash: '' };\n"
        )
        # Only this page group's scripts, bundled exactly as the hub does,
        # so another page's load-time needs never decide this test.
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("github.js", "inbox.js", "repositories.js"):
                shutil.copy(STATIC / name, Path(tmp) / name)
            with patch.object(pages, "STATIC", Path(tmp)):
                bundle = pages.bundle(".js").decode()
        script = (stubs + bundle
                  + "\nprocess.stdout.write(JSON.stringify(registered));\nprocess.exit(0);")
        done = _node(script)
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

    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def test_merge_needs_a_device_and_a_same_site_origin(self):
        _pin(self.srv.home, "o/a")
        fake = FakeGh(self, prs={"7": gh_pr(7)})
        body = {"repo": "o/a", "number": 7, "method": "squash"}
        self.assertEqual(self.srv.request("POST", "/v1/app/repositories/merge", body=body)[0], 401)
        auth = self.auth()
        status, data = self.srv.request("POST", "/v1/app/repositories/merge", body=body,
                                        headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertEqual(fake.writes(), [])
        status, data = self.srv.request("POST", "/v1/app/repositories/merge", body=body,
                                        headers={**auth, "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["pr"]["state"], "merged")
        self.assertEqual(fake.writes(), [["pr", "merge", "7", "--repo", "o/a", "--squash"]])
        # A refusal carries its status and the app's words.
        status, data = self.srv.request("POST", "/v1/app/repositories/merge", body=body, headers=auth)
        self.assertEqual(status, 409)
        self.assertIn("not_open", data.decode())
        status, _ = self.srv.request("POST", "/v1/app/repositories/merge",
                                     body={**body, "method": "octopus"}, headers=auth)
        self.assertEqual(status, 422)
        self.assertEqual(len(fake.writes()), 1)

    def test_add_through_the_hub_needs_a_same_site_origin(self):
        fake = FakeGh(self, repos={"o/b": "o/b"})
        auth = self.auth()
        status, _ = self.srv.request("POST", "/v1/app/repositories/add", body={"repo": "o/b"},
                                     headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(fake.calls(), [])
        status, data = self.srv.request("POST", "/v1/app/repositories/add", body={"repo": "o/b"},
                                        headers={**auth, "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual([r["name"] for r in G.pinned(self.srv.home)], ["o/b"])

    def test_unpin_and_reorder_through_the_hub(self):
        _pin(self.srv.home, "o/a", "o/b")
        auth = {**self.auth(), "Origin": self.origin()}
        status, _ = self.srv.request("POST", "/v1/app/repositories/reorder", body={"order": ["o/b"]},
                                     headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, data = self.srv.request("POST", "/v1/app/repositories/reorder", body={"order": ["o/b"]},
                                        headers=auth)
        self.assertEqual(status, 200, data)
        status, data = self.srv.request("POST", "/v1/app/repositories/remove", body={"repo": "o/a"},
                                        headers=auth)
        self.assertEqual(status, 200, data)
        self.assertEqual([r["name"] for r in G.pinned(self.srv.home)], ["o/b"])

    def test_page_scripts_are_bundled(self):
        _, js = self.srv.request("GET", "/pages.js")
        self.assertIn(b"const GhUI", js)
        _, css = self.srv.request("GET", "/pages.css")
        self.assertIn(b".gh-row", css)



PR_FILTER_FLOW = r"""
const out = {};
const lastQ = {};
const qsa = panel.querySelectorAll.bind(panel);
panel.querySelectorAll = (sel) => (lastQ[sel] = qsa(sel));
document.querySelector = (sel) => null;
const html = () => panel.innerHTML;
const prRows = () => [...html().matchAll(/data-pr="(\d+)" data-repo="([^"]*)"/g)].map((m) => `${m[2]}#${m[1]}`);
const chips = () => [...html().matchAll(/data-pr-filter="(\w+)">([^<]*)<span class="num">(\d+)/g)]
  .map((m) => [m[1], m[2].trim(), Number(m[3])]);
const selected = () => (html().match(/aria-checked="true"[^>]*\s+data-pr-filter="(\w+)"/) || [])[1] || null;
const click = (attr, value) => lastQ[`[data-${attr}]`].find((e) => e.attrs[`data-${attr}`] === value).onclick();
const pr = (number, author) => ({ number, title: "PR " + number, state: "open", draft: false, author,
  branch: "b" + number, base: "main", labels: [], mergeable: "mergeable", review_decision: "none",
  checks: "none", updated_at: null, url: "" });
const board = (prs) => ({ surfaces: { pull_requests: { status: "ok", pull_requests: prs },
  issues: { status: "ok", issues: [] }, runs: { status: "ok", runs: [] } } });
apiAnswers.push(["/v1/app/repositories", { status: 200, data: { machine: "hub", github: { status: "ok" },
  repos: [{ name: "o/a" }, { name: "o/b" }],
  boards: { "o/a": board([pr(1, "me"), pr(2, "bob"), pr(3, "bob")]), "o/b": board([pr(4, "me")]) } } }]);
let pulls = { status: 200, data: { github: { status: "ok" }, repos: [
  { name: "o/a", status: "ok", pulls: [
    { number: 1, mine: true, review_requested: false },
    { number: 2, mine: false, review_requested: true },
    { number: 3, mine: false, review_requested: false }] },
  { name: "O/B", status: "ok", pulls: [{ number: 4, mine: true, review_requested: false }] }] } };
apiAnswers.push(["/v1/pulls", { get status() { return pulls.status; }, get data() { return pulls.data; } }]);
(async () => {
  location.hash = "#/repositories";
  PAGES.repositories();
  await tick();
  out.chips = chips();
  out.allRows = prRows();
  click("pr-filter", "mine");
  out.mineSelected = selected();
  out.mineRows = prRows();
  click("pr-filter", "review");
  out.reviewRows = prRows();
  // o/b has nothing asking for review: it goes quiet in the rail.
  out.quiet = /1 quiet/.test(html());
  // Another tab: no filter bar, every row of that tab.
  click("tab", "issues");
  out.issuesBar = /data-pr-filter/.test(html());
  click("tab", "pulls");
  await tick();
  out.backSelected = selected();
  // gh pr status failed: review_requested is null, so that filter goes
  // and the pick falls back to All; Mine stays.
  pulls = { status: 200, data: { github: { status: "ok" }, repos: [
    { name: "o/a", status: "ok", pulls: [
      { number: 1, mine: true, review_requested: null },
      { number: 2, mine: false, review_requested: null },
      { number: 3, mine: false, review_requested: null }] },
    { name: "o/b", status: "ok", pulls: [{ number: 4, mine: true, review_requested: null }] }] } };
  PAGES.repositories();
  await tick();
  out.nullChips = chips().map((c) => c[0]);
  out.nullSelected = selected();
  out.nullRows = prRows().length;
  // o/b's review flags unknown, o/a's known: narrowing the list to o/a
  // still hides Review requested, or o/b would read as quiet in the rail.
  pulls = { status: 200, data: { github: { status: "ok" }, repos: [
    { name: "o/a", status: "ok", pulls: [
      { number: 1, mine: true, review_requested: false },
      { number: 2, mine: false, review_requested: true },
      { number: 3, mine: false, review_requested: false }] },
    { name: "o/b", status: "ok", pulls: [{ number: 4, mine: true, review_requested: null }] }] } };
  PAGES.repositories();
  await tick();
  click("shown", "o/a");
  out.scopedChips = chips().map((c) => c[0]);
  out.scopedMineCount = (chips().find((c) => c[0] === "mine") || [])[2];
  click("shown", "");
  // A PR the flags don't cover yet (newer than /v1/pulls' cache): no guess, no bar.
  pulls = { status: 200, data: { github: { status: "ok" }, repos: [
    { name: "o/a", status: "ok", pulls: [{ number: 1, mine: true, review_requested: false }] },
    { name: "o/b", status: "ok", pulls: [{ number: 4, mine: true, review_requested: false }] }] } };
  PAGES.repositories();
  await tick();
  out.partialBar = /data-pr-filter/.test(html());
  // gh blocked on the hub, or the read failing: no bar at all.
  pulls = { status: 200, data: { env_blocked: "gh is not signed in", repos: [] } };
  PAGES.repositories();
  await tick();
  out.blockedBar = /data-pr-filter/.test(html());
  pulls = { status: 503, data: null };
  PAGES.repositories();
  await tick();
  out.failedBar = /data-pr-filter/.test(html());
  out.failedRows = prRows().length;
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestPrFilter(unittest.TestCase):
    """The dashboard's Mine / Review requested filters under node, from
    the flags GET /v1/pulls carries; offered only while they're known."""

    def test_filters_and_null_flags(self):
        from tests.test_webapp_launch_sheets import PAGES, STUB
        script = (STUB + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                  + (PAGES / "repositories.js").read_text(encoding="utf-8") + "\n" + PR_FILTER_FLOW)
        done = _node(script)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["chips"], [["all", "All", 4], ["mine", "Mine", 2], ["review", "Review requested", 1]])
        self.assertEqual(out["allRows"], ["o/a#1", "o/a#2", "o/a#3", "o/b#4"])
        self.assertEqual(out["mineSelected"], "mine")
        self.assertEqual(out["mineRows"], ["o/a#1", "o/b#4"])
        self.assertEqual(out["reviewRows"], ["o/a#2"])
        self.assertTrue(out["quiet"])
        self.assertFalse(out["issuesBar"])
        self.assertEqual(out["backSelected"], "review")
        self.assertEqual(out["nullChips"], ["all", "mine"])
        self.assertEqual(out["nullSelected"], "all")
        self.assertEqual(out["nullRows"], 4)
        self.assertEqual(out["scopedChips"], ["all", "mine"])
        self.assertEqual(out["scopedMineCount"], 1)
        self.assertFalse(out["partialBar"])
        self.assertFalse(out["blockedBar"])
        self.assertFalse(out["failedBar"])
        self.assertEqual(out["failedRows"], 4)


# The focus page's filter under the minute poll and an IME: the poll's
# repaint puts the focus back in the (new) filter box, and keystrokes
# inside a composition update the filter without replacing the box.
FOCUS_FILTER_FLOW = r"""
let pollTick = null;
globalThis.setInterval = (fn) => { pollTick = fn; return 1; };
const pr = (number, title) => ({ number, title, state: "open", draft: false, author: "me",
  branch: "b" + number, base: "main", labels: [], mergeable: "mergeable", review_decision: "none",
  checks: "none", updated_at: null, url: "" });
apiAnswers.push(["/v1/app/repositories", { status: 200, data: { machine: "hub", github: { status: "ok" },
  limits: {}, surfaces: { pull_requests: { status: "ok", pull_requests: [pr(1, "alpha"), pr(2, "beta")] },
    issues: { status: "ok", issues: [] }, runs: { status: "ok", runs: [] } } } }]);
const out = {};
const box = () => document.getElementById("gh-search");
const rows = () => [...panel.innerHTML.matchAll(/>(alpha|beta)</g)].map((m) => m[1]);
(async () => {
  location.hash = "#/repositories?repo=o/a";
  PAGES.repositories();
  await tick();
  box().focus();
  pollTick(); // the minute poll: renderFocus paints its spinner first
  out.focusedWhileLoading = document.activeElement === box();
  await tick();
  out.focusedAfter = document.activeElement === box();
  const typing = box();
  typing.oncompositionstart();
  typing.value = "be";
  typing.oninput({ isComposing: true });
  pollTick(); // the poll starts, and its reply lands, mid-composition
  await tick();
  out.keptDuringComposition = box() === typing;
  out.rowsDuringComposition = rows();
  typing.value = "beta";
  typing.oncompositionend();
  out.repaintedAfter = box() !== typing && document.activeElement === box();
  out.rowsAfter = rows();
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestFocusFilter(unittest.TestCase):
    def test_the_poll_keeps_focus_and_a_composition_is_not_cut(self):
        from tests.test_webapp_launch_sheets import PAGES, STUB
        script = (STUB + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                  + (PAGES / "repositories.js").read_text(encoding="utf-8") + "\n" + FOCUS_FILTER_FLOW)
        done = _node(script)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {
            "focusedWhileLoading": True, "focusedAfter": True,
            "keptDuringComposition": True, "rowsDuringComposition": ["alpha", "beta"],
            "repaintedAfter": True, "rowsAfter": ["beta"]})


if __name__ == "__main__":
    unittest.main()
