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
from quiver.webapp.pages import _github as G
from quiver.webapp.pages import repositories as R

from tests.test_remote_hub import HubBase

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
        """The calls that change GitHub: merges and non-GET api calls."""
        return [c for c in self.calls()
                if c[:2] == ["pr", "merge"] or (c[:1] == ["api"] and "--method" in c)]


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
            R.post(self.ctx, "rerun", {"repo": "o/a", "number": 7})
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


@unittest.skipUnless(shutil.which("node"), "node is not installed")
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

    def test_page_scripts_are_bundled(self):
        _, js = self.srv.request("GET", "/app/pages.js")
        for name in (b'registerPage("repositories"', b'registerPage("inbox"', b"const GhUI"):
            self.assertIn(name, js)
        _, css = self.srv.request("GET", "/app/pages.css")
        self.assertIn(b".gh-row", css)


if __name__ == "__main__":
    unittest.main()
