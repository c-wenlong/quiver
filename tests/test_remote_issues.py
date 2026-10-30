"""remote/issues.py: GET /v1/issues, the issue detail route, and
POST /v1/issues/resolve on the hub.

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

from quiver.github import gh, manage, repos
from quiver.github import issues as gh_issues
from quiver.remote import issues as issues_mod
from quiver.remote import protocol, sessionmeta
from quiver.github import shared as G
from quiver.webapp.pages import repositories

from tests.test_github_issues import RAW_ISSUE
from tests.test_remote_hub import _Server, _session

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")


def _issue_row(number, author="octocat", title=None, **extra):
    """A ``cli._issue_row`` entry, the shaped list row."""
    return {"number": number, "title": title or f"Issue {number}",
            "state": "open", "author": author, "labels": [],
            "assignees": [], "comments": 0,
            "updated_at": "2026-01-02T00:00:00Z",
            "url": f"https://github.com/o/r/issues/{number}", **extra}


def _stub_issues(name, state, limit):
    rows = {"o/r": [_issue_row(5), _issue_row(7, author="other"),
                    _issue_row(9, agent_running=True)],
            "o/s": [_issue_row(3)]}.get(name, [])
    return {"repo": name, "status": "ok", "issues": rows}


class _Base(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        repos.add_pinned("o/r", manage.pins_file(self.srv.home))
        repos.add_pinned("o/s", manage.pins_file(self.srv.home))
        # The issues reads sit on the shared gh cache; tests stub the
        # fetches per case, so nothing may survive from a prior one.
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _get(self, path):
        status, data = self.srv.request("GET", path, headers=self.hdr)
        return status, json.loads(data)


class TestList(_Base):
    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, issues=None):
        with patch("quiver.github.gh.probe_env", return_value=env), \
             patch("quiver.github.cli._fetch_issues",
                   side_effect=issues or _stub_issues) as lp:
            yield lp

    def test_lists_every_pin_with_the_mine_flag(self):
        with self._stubs():
            status, body = self._get("/v1/issues")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["github"]["login"], "octocat")
        self.assertNotIn("env_blocked", body)
        by_name = {r["name"]: r for r in body["repos"]}
        self.assertEqual(set(by_name), {"o/r", "o/s"})
        issues = {i["number"]: i for i in by_name["o/r"]["issues"]}
        self.assertEqual(set(issues), {5, 7, 9})
        self.assertEqual((issues[5]["mine"], issues[7]["mine"]),
                         (True, False))
        # ``agent_running`` rides a row through when the hub sends it.
        self.assertIs(issues[9]["agent_running"], True)
        self.assertNotIn("agent_running", issues[5])
        self.assertIn("fetched_at", body)

    def test_no_pins(self):
        repos.remove_pinned("o/r", manage.pins_file(self.srv.home))
        repos.remove_pinned("o/s", manage.pins_file(self.srv.home))
        with self._stubs() as lp:
            status, body = self._get("/v1/issues")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["repos"], [])
        lp.assert_not_called()

    def test_env_blocked_stops_before_any_fetch(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env) as lp:
            status, body = self._get("/v1/issues")
        self.assertEqual(status, 200, body)
        self.assertIn("gh auth login", body["env_blocked"])
        self.assertEqual(body["repos"], [])
        lp.assert_not_called()

    def test_one_repo_failing_leaves_the_others(self):
        def issues(name, state, limit):
            if name == "o/s":
                return {"repo": name, "status": "upstream-error",
                        "issues": [], "error": "GitHub is down"}
            return _stub_issues(name, state, limit)

        with self._stubs(issues=issues):
            status, body = self._get("/v1/issues")
        self.assertEqual(status, 200, body)
        by_name = {r["name"]: r for r in body["repos"]}
        self.assertEqual(by_name["o/r"]["status"], "ok")
        self.assertEqual(len(by_name["o/r"]["issues"]), 3)
        self.assertEqual(by_name["o/s"]["status"], "upstream-error")
        self.assertEqual(by_name["o/s"]["issues"], [])
        self.assertIn("GitHub is down", by_name["o/s"]["error"])

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/issues")
        self.assertEqual(status, 401)

    def test_state_param_picks_the_gh_list(self):
        calls = []

        def issues(name, state, limit):
            calls.append((name, state))
            return _stub_issues(name, state, limit)

        with self._stubs(issues=issues):
            status, body = self._get("/v1/issues?state=closed")
        self.assertEqual(status, 200, body)
        self.assertEqual(set(calls), {("o/r", "closed"), ("o/s", "closed")})
        # ``all`` is the other word ``gh issue list --state`` takes.
        G.CACHE.clear()
        calls.clear()
        with self._stubs(issues=issues):
            status, _body = self._get("/v1/issues?state=all")
        self.assertEqual(status, 200)
        self.assertEqual(set(calls), {("o/r", "all"), ("o/s", "all")})

    def test_bad_state_is_a_422_before_any_fetch(self):
        with self._stubs() as lp:
            status, body = self._get("/v1/issues?state=bogus")
        self.assertEqual((status, body["error"]["code"]),
                         (422, "bad_state"))
        lp.assert_not_called()

    def test_a_closed_list_never_lands_in_the_open_slot(self):
        """tasks.py shares the ``("issues", hk, repo)`` slot with this
        route's default read, so a closed page must cache apart: the
        open read still answers open rows and is not fetched again."""
        calls = []

        def issues(name, state, limit):
            calls.append((name, state))
            return {"repo": name, "status": "ok",
                    "issues": [_issue_row(5, state=state)]}

        with self._stubs(issues=issues):
            s1, open_body = self._get("/v1/issues")
            s2, closed_body = self._get("/v1/issues?state=closed")
            s3, open_again = self._get("/v1/issues")
        self.assertEqual((s1, s2, s3), (200, 200, 200))
        by_name = {r["name"]: r for r in closed_body["repos"]}
        self.assertEqual(by_name["o/r"]["issues"][0]["state"], "closed")
        open_by_name = {r["name"]: r for r in open_again["repos"]}
        self.assertEqual(open_by_name["o/r"]["issues"][0]["state"], "open")
        # The third read answered from the cache: one fetch per repo
        # and state, nothing more.
        self.assertEqual(sorted(calls),
                         [("o/r", "closed"), ("o/r", "open"),
                          ("o/s", "closed"), ("o/s", "open")])


class TestDetail(_Base):
    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, fetched=None):
        fetched = fetched or gh.GhResult(
            "ok", data=gh_issues.shape(RAW_ISSUE, "o/r", self.srv.home))
        with patch("quiver.github.gh.probe_env", return_value=env), \
             patch("quiver.github.issues.fetch", return_value=fetched):
            yield

    def test_full_row_plus_detail_fields(self):
        with self._stubs():
            status, body = self._get("/v1/issues/o/r/5")
        self.assertEqual(status, 200, body)
        self.assertEqual((body["status"], body["repo"]), ("ok", "o/r"))
        issue = body["issue"]
        self.assertEqual(issue["number"], 5)
        self.assertEqual(issue["title"], "Fix the thing")
        self.assertEqual(issue["body"], "It breaks.\n\nSteps: run it.")
        self.assertEqual(issue["labels"], ["bug", "ui"])
        self.assertEqual(issue["assignees"], ["bob"])
        self.assertEqual(issue["comments"][0]["author"], "carol")
        self.assertEqual(issue["branch"], "quiver/issue-5")
        self.assertTrue(issue["worktree"].endswith("o__r-issue5"))
        self.assertTrue(issue["suggested_prompt"].startswith(
            "Resolve o/r#5 — Fix the thing"))
        self.assertIn(issue["worktree"], issue["suggested_prompt"])
        self.assertIn("fetched_at", body)

    def test_not_pinned_and_bad_path(self):
        with self._stubs():
            status, body = self._get("/v1/issues/other/repo/5")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_pinned"))
        with self._stubs():
            status, body = self._get("/v1/issues/o/r/x")
        self.assertEqual((status, body["error"]["code"]),
                         (422, "bad_issue"))
        status, body = self._get("/v1/issues/o/5")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_found"))
        # ``resolve`` is a POST route; a GET on it is no detail.
        status, body = self._get("/v1/issues/resolve")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_found"))

    def test_gh_blocked_reports_instead_of_fetching(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env):
            status, body = self._get("/v1/issues/o/r/5")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "auth-required")
        self.assertIsNone(body["issue"])
        self.assertIn("gh auth login", body["error"])

    def test_missing_issue_is_a_status_not_a_crash(self):
        missing = gh.GhResult("not-found", error="no such issue")
        with self._stubs(fetched=missing):
            status, body = self._get("/v1/issues/o/r/404")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "not-found")
        self.assertIsNone(body["issue"])
        self.assertIn("no such issue", body["error"])

    def test_a_bare_cached_row_still_carries_the_extra_fields(self):
        """An entry written under ``("issue", hk, repo, n)`` without the
        fields this route adds (``shape`` fills them on a real fetch)
        still answers with branch, worktree and suggested_prompt."""
        key = ("issue", G.home_key(self.srv.home), "o/r", "5")
        G.CACHE.get(key, lambda: {
            "repo": "o/r", "status": "ok",
            "issue": {"number": 5, "title": "Fix the thing",
                      "state": "open", "author": "alice",
                      "url": "https://github.com/o/r/issues/5"}})
        with self._stubs():
            status, body = self._get("/v1/issues/o/r/5")
        self.assertEqual(status, 200, body)
        issue = body["issue"]
        self.assertEqual(issue["branch"], "quiver/issue-5")
        self.assertTrue(issue["worktree"].endswith("o__r-issue5"))
        self.assertTrue(issue["suggested_prompt"].startswith(
            "Resolve o/r#5"))

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/issues/o/r/5")
        self.assertEqual(status, 401)


class TestDropRepo(_Base):
    def test_writes_invalidate_the_phone_reads(self):
        """``repositories._drop_repo`` runs after a merge, rerun or
        unpin; the phone's issue list and detail entries for the repo
        must go with it."""
        hk = G.home_key(self.srv.home)
        keys = [("issues", hk, "o/r"), ("issue", hk, "o/r", "5"),
                # The Closed chip's own slot goes with the repo too.
                ("issues", hk, "o/r", "closed")]
        for key in keys:
            G.CACHE.get(key, lambda: {"stale": True})
        repositories._drop_repo(self.srv.home, "o/r")
        fresh = {"fresh": True}
        for key in keys:
            value, _at = G.CACHE.get(key, lambda: fresh)
            self.assertIs(value, fresh, key)


class TestCommentRoute(_Base):
    """``POST /v1/issues/comment``: the web and iPhone composers'
    write, through the hub's own ``gh``."""

    POSTED = "https://github.com/o/r/issues/5#issuecomment-1\n"

    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, ran=None):
        ran = ran or gh.GhResult("ok", data=self.POSTED)
        with patch("quiver.github.gh.probe_env", return_value=env), \
             patch("quiver.github.gh.run_gh", return_value=ran) as run:
            yield run

    def _post(self, body, headers=None):
        status, data = self.srv.request(
            "POST", "/v1/issues/comment", body=body,
            headers=self.hdr if headers is None else headers)
        return status, json.loads(data)

    def test_posts_the_text_on_stdin_and_drops_the_issue_reads(self):
        hk = G.home_key(self.srv.home)
        dropped = [("issue", hk, "o/r", "5"), ("issues", hk, "o/r"),
                   ("issues", hk, "o/r", "closed"), ("board", hk, "o/r")]
        kept = [("issue", hk, "o/r", "6"), ("issues", hk, "o/s"),
                ("pr", hk, "o/r", "5")]
        for key in dropped + kept:
            G.CACHE.get(key, lambda: {"stale": True})
        text = "--repo evil/x\n\nLooks fixed on main; `$(rm -rf ~)` stays text."
        with self._stubs() as run:
            status, body = self._post(
                {"repo": "O/R", "number": "5", "body": f"  {text}\n"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual((body["repo"], body["number"]), ("o/r", 5))
        self.assertEqual(body["comment"]["url"], self.POSTED.strip())
        self.assertEqual(body["comment"]["author"], "octocat")
        self.assertEqual(body["comment"]["body"], text)
        self.assertEqual(body["github"]["login"], "octocat")
        args, kwargs = run.call_args
        # The text rides stdin, trimmed like the Mac's composer; argv
        # names only the pinned repo and the number.
        self.assertEqual(args[0], ["issue", "comment", "5", "--repo", "o/r",
                                   "--body-file", "-"])
        self.assertEqual(kwargs["stdin"], text)
        fresh = {"fresh": True}
        for key in dropped:
            self.assertIs(G.CACHE.get(key, lambda: fresh)[0], fresh, key)
        for key in kept:
            self.assertIsNot(G.CACHE.get(key, lambda: fresh)[0], fresh, key)

    def test_bad_input_is_refused_before_gh_runs(self):
        cases = [
            ({"number": 5, "body": "x"}, "bad_repo"),
            ({"repo": "not a repo", "number": 5, "body": "x"}, "bad_repo"),
            ({"repo": ["o/r"], "number": 5, "body": "x"}, "bad_repo"),
            ({"repo": "o/r", "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": 0, "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": -5, "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": True, "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": 5.0, "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": "5x", "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": 10 ** 9, "body": "x"}, "bad_issue"),
            ({"repo": "o/r", "number": 5}, "bad_comment"),
            ({"repo": "o/r", "number": 5, "body": 7}, "bad_comment"),
            ({"repo": "o/r", "number": 5, "body": " \n\t "}, "bad_comment"),
            ({"repo": "o/r", "number": 5,
              "body": "x" * (issues_mod.MAX_COMMENT + 1)},
             "comment_too_long"),
        ]
        with self._stubs() as run:
            for sent, code in cases:
                status, body = self._post(sent)
                self.assertEqual((status, body["error"]["code"]),
                                 (422, code), sent)
            # A repo the hub doesn't pin is never written to.
            status, body = self._post(
                {"repo": "other/repo", "number": 5, "body": "x"})
            self.assertEqual((status, body["error"]["code"]),
                             (404, "not_pinned"))
        run.assert_not_called()

    def test_the_cap_counts_the_trimmed_text(self):
        text = "x" * issues_mod.MAX_COMMENT
        with self._stubs() as run:
            status, body = self._post(
                {"repo": "o/r", "number": 5, "body": f"\n{text}\n"})
        self.assertEqual(status, 200, body)
        self.assertEqual(run.call_args.kwargs["stdin"], text)

    def test_gh_missing_or_signed_out_is_a_409(self):
        for env, code in (
                (gh.GhEnv(installed=True, authenticated=False),
                 "gh_auth_required"),
                (gh.GhEnv(installed=False), "gh_unavailable")):
            G.CACHE.clear()
            with self._stubs(env=env) as run:
                status, body = self._post(
                    {"repo": "o/r", "number": 5, "body": "x"})
            self.assertEqual((status, body["error"]["code"]), (409, code))
            run.assert_not_called()

    def test_gh_failures_answer_with_its_words(self):
        missing = gh.GhResult("not-found", error="HTTP 404: Not Found")
        with self._stubs(ran=missing):
            status, body = self._post({"repo": "o/r", "number": 5,
                                       "body": "x"})
        self.assertEqual((status, body["error"]["code"]),
                         (404, "unknown_issue"))
        self.assertIn("No issue #5 in o/r", body["error"]["message"])
        failed = gh.GhResult("upstream-error", error="gh timed out after 60s")
        with self._stubs(ran=failed):
            status, body = self._post({"repo": "o/r", "number": 5,
                                       "body": "x"})
        self.assertEqual((status, body["error"]["code"]),
                         (502, "comment_failed"))
        self.assertIn("timed out", body["error"]["message"])

    def test_unreadable_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", "/v1/issues/comment", body=b"{not json",
                     headers={**self.hdr,
                              "Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400, resp.read())

    def test_needs_a_phone_or_owner_device(self):
        sent = {"repo": "o/r", "number": 5, "body": "x"}
        with self._stubs() as run:
            status, _ = self.srv.request("POST", "/v1/issues/comment",
                                         body=sent)
            self.assertEqual(status, 401)
            # A satellite credential is a machine's, for /v1/agent/*.
            cred = self.srv.pair(role="satellite")
            status, _ = self.srv.request(
                "POST", "/v1/issues/comment", body=sent,
                headers={"Authorization": f"Bearer {cred['credential']}"})
            self.assertEqual(status, 401)
        run.assert_not_called()

    def test_a_get_is_no_route(self):
        status, body = self._get("/v1/issues/comment")
        self.assertEqual((status, body["error"]["code"]),
                         (404, "not_found"))


class TestResolveRoute(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        repos.add_pinned("o/r", manage.pins_file(self.srv.home))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = str(Path(tmp.name) / "o__r-issue5")
        Path(self.tree).mkdir()
        self.issue = gh_issues.shape(RAW_ISSUE, "o/r", self.srv.home)

    def _worktree_ok(self, repo, number, home=None, progress=None):
        progress("Creating worktree quiver/issue-5…")
        return gh.GhResult("ok", data={
            "path": self.tree, "branch": "quiver/issue-5",
            "base": "origin/main", "created": True})

    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, installed=True, tmux=True, fetched=None,
               worktree=None, launch=None, blocker=None, tracked=True):
        fetched = fetched or gh.GhResult("ok", data=self.issue)
        with patch("quiver.remote.catalog.launch_command",
                   return_value="claude" if installed else None), \
             patch("quiver.remote.catalog.launchable_harnesses",
                   return_value=[]), \
             patch("quiver.remote.catalog.known_session_ids",
                   return_value=["old"]), \
             patch("quiver.remote.supervise.tmux_bin",
                   return_value="/usr/bin/tmux" if tmux else None), \
             patch("quiver.remote.supervise.fork_blocker",
                   return_value=blocker), \
             patch("quiver.remote.catalog.untracked",
                   return_value=[] if tracked else ["claude"]), \
             patch("quiver.github.gh.probe_env", return_value=env), \
             patch("quiver.github.issues.fetch", return_value=fetched), \
             patch("quiver.github.manage.issue_worktree",
                   side_effect=worktree or self._worktree_ok) as wt, \
             patch("quiver.remote.supervise.launch_new",
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

    def _post(self, body):
        status, data = self.srv.request(
            "POST", "/v1/issues/resolve", body=body, headers=self.hdr)
        return status, json.loads(data)

    def _view(self, launch_id):
        status, data = self.srv.request(
            "GET", f"/v1/launches/{launch_id}", headers=self.hdr)
        self.assertEqual(status, 200, data)
        return json.loads(data)

    def test_happy_path_launches_in_the_worktree(self):
        with self._stubs() as (wt, ln):
            status, body = self._post(
                {"repo": "O/R", "number": 5, "harness": "claude"})
        self.assertEqual(status, 202, body)
        self.assertEqual((body["state"], body["note"]),
                         ("pending", "preparing worktree"))
        lid = body["launch_id"]
        self.assertEqual(wt.call_args[0][:2], ("o/r", 5))  # pinned spelling
        self.assertEqual(wt.call_args[0][2], self.srv.home)
        args, kw = ln.call_args
        self.assertEqual(args[1:3], ("claude", self.tree))
        self.assertTrue(args[3].startswith("Resolve o/r#5 — Fix the thing"))
        self.assertIn(f"at {self.tree}.", args[3])
        self.assertEqual(kw["launch_id"], lid)
        self.assertEqual(kw["binary"], "claude")
        self.assertEqual(kw["known_ids"], ["old"])
        view = self._view(lid)
        self.assertEqual(view["state"], "pending")
        self.assertEqual(view["cwd"], self.tree)
        self.assertEqual(view["note"], "Starting claude…")
        self.assertEqual(view["title"], "o/r#5 Fix the thing")

    def test_resolve_carries_the_pick(self):
        with self._stubs() as (_wt, ln):
            status, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude",
                 "model": "opus", "effort": "high"})
        self.assertEqual(status, 202, body)
        kw = ln.call_args[1]
        self.assertEqual((kw["model"], kw["effort"]), ("opus", "high"))
        view = self._view(body["launch_id"])
        self.assertEqual((view["model"], view["effort"]), ("opus", "high"))

    def test_resolve_refuses_a_pick_the_harness_cannot_take(self):
        # gemini declares no capabilities.model; nothing is prepared.
        with self._stubs() as (wt, ln):
            status, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "gemini",
                 "model": "opus"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"],
                         "unsupported_launch_option")
        wt.assert_not_called()
        ln.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})

    def test_resolve_rejects_a_bad_model_shape(self):
        with self._stubs() as (wt, ln):
            status, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude",
                 "model": "two words"})
        self.assertEqual(status, 422, body)
        self.assertEqual(body["error"]["code"], "bad_command")
        wt.assert_not_called()
        ln.assert_not_called()

    def test_title_applied_on_adoption_only_for_resolves(self):
        state = self.srv.state
        with self._stubs():
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
        q = state.bus.subscribe("_global")
        self.addCleanup(state.bus.unsubscribe, q)
        own_seq = state.journal.last_seq("claude:s1")
        state.emit("_global", protocol.event(
            "session.launched", "claude:s1", "m",
            {"launch_id": body["launch_id"]}))
        view = self._view(body["launch_id"])
        self.assertEqual((view["state"], view["key"]),
                         ("started", "claude:s1"))
        row = state.catalog.row_for("claude:s1")
        self.assertEqual(row["title"], "o/r#5 Fix the thing")
        self.assertEqual(sessionmeta.get(state.home, "claude:s1")["title"],
                         "o/r#5 Fix the thing")
        types = []
        while not q.empty():
            types.append(q.get_nowait()["type"])
        self.assertEqual(types, ["session.launched", "session.meta"])
        # Nothing went into the session's own journal: its tailer would
        # read that as history and skip the first turn.
        self.assertEqual(state.journal.last_seq("claude:s1"), own_seq)
        # A second launched event for the same launch renames nothing.
        sessionmeta.update(state.home, "claude:s1", "title", {"title": ""})
        state.emit("_global", protocol.event(
            "session.launched", "claude:s1", "m",
            {"launch_id": body["launch_id"]}))
        self.assertEqual(sessionmeta.get(state.home, "claude:s1")["title"],
                         "")

        # An ordinary launch never gets a title.
        lid = state.new_launch(state.machine_id, "claude", "/tmp")
        state.emit("_global", protocol.event(
            "session.launched", "claude:s1", "m", {"launch_id": lid}))
        self.assertEqual(sessionmeta.get(state.home, "claude:s1")["title"],
                         "")

    def test_progress_notes_while_pending(self):
        gate = threading.Event()
        seen = threading.Event()

        def slow(repo, number, home=None, progress=None):
            progress("Cloning mirror…")
            seen.set()
            gate.wait(5)
            return self._worktree_ok(repo, number, home, progress)

        with self._stubs(worktree=slow):
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
            self.assertTrue(seen.wait(5))
            self.assertEqual(self._view(body["launch_id"])["note"],
                             "Cloning mirror…")
            gate.set()

    def test_a_second_resolve_waits_for_the_first(self):
        gate = threading.Event()
        seen = threading.Event()

        def slow(repo, number, home=None, progress=None):
            seen.set()
            gate.wait(5)
            return self._worktree_ok(repo, number, home, progress)

        req = {"repo": "o/r", "number": 5, "harness": "claude"}
        with self._stubs(worktree=slow) as (_wt, ln):
            status, first = self._post(req)
            self.assertEqual(status, 202, first)
            self.assertTrue(seen.wait(5))
            status, body = self._post(req)
            self.assertEqual((status, body["error"]["code"]),
                             (409, "resolve_pending"))
            # Another issue in the same repo is not held up.
            status, other = self._post({**req, "number": 6})
            self.assertEqual(status, 202, other)
            gate.set()
        self.assertEqual(ln.call_count, 2)
        # Once launch_new has run, its pending marker is the guard.
        with self._stubs(blocker="qv-other") as (_wt, ln):
            status, body = self._post(req)
        self.assertEqual((status, body["error"]["code"]),
                         (409, "resolve_pending"))
        ln.assert_not_called()
        with self._stubs() as (_wt, ln):
            status, _body = self._post(req)
        self.assertEqual(status, 202)

    def test_an_untracked_harness_marker_blocks_nothing(self):
        # Its marker is never adopted, so it must not hold the issue.
        req = {"repo": "o/r", "number": 5, "harness": "claude"}
        with self._stubs(blocker="qv-old", tracked=False) as (_wt, ln):
            status, body = self._post(req)
        self.assertEqual(status, 202, body)
        self.assertEqual(self._view(body["launch_id"])["state"], "untracked")
        ln.assert_called_once()

    def test_prompt_and_context_must_fit_with_the_header(self):
        cap = protocol.MAX_LAUNCH_PROMPT
        base = {"repo": "o/r", "number": 5, "harness": "claude",
                "context": "x"}
        with self.assertRaises(ValueError):
            issues_mod.parse_body({**base, "prompt": "p" * (cap - 1)})
        with self.assertRaises(ValueError):
            issues_mod.parse_body({**base, "prompt": "p" * (cap - 80)})
        cmd = issues_mod.parse_body({**base, "prompt": "p" * (cap - 81)})
        text = issues_mod.final_prompt(cmd, None, "/w", "x" * 39)
        self.assertLessEqual(len(text), cap)
        # Without a prompt the built one is capped on its own.
        issues_mod.parse_body({**base, "context": "c" * 8000})

    def test_worktree_failure_fails_the_launch(self):
        fail = lambda *a, **k: gh.GhResult(  # noqa: E731
            "upstream-error", error="Clone failed: denied")
        with self._stubs(worktree=fail) as (_wt, ln):
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
        view = self._view(body["launch_id"])
        self.assertEqual((view["state"], view["note"]),
                         ("failed", "Clone failed: denied"))
        ln.assert_not_called()

    def test_launch_failure_and_crash_fail_the_launch(self):
        with self._stubs(launch=lambda *a, **k: (False, "tmux died", None)):
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
        self.assertEqual(self._view(body["launch_id"])["note"], "tmux died")

        def boom(*a, **k):
            raise RuntimeError("kaboom")

        with self._stubs(worktree=boom):
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
        view = self._view(body["launch_id"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("kaboom", view["note"])

    def test_unreadable_issue_without_a_prompt_fails(self):
        missing = gh.GhResult("not-found", error="no such issue")
        with self._stubs(fetched=missing) as (wt, ln):
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
        view = self._view(body["launch_id"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("Couldn't read o/r#5", view["note"])
        wt.assert_not_called()

    def test_client_prompt_and_context(self):
        missing = gh.GhResult("upstream-error", error="down")
        with self._stubs(fetched=missing) as (_wt, ln):
            _s, body = self._post({
                "repo": "o/r", "number": 5, "harness": "claude",
                "prompt": "Do the fix.", "context": "Keep it small."})
        prompt = ln.call_args[0][3]
        self.assertEqual(prompt, "Do the fix.\n\nContext for this run, "
                                 "from octocat:\nKeep it small.")
        # The issue couldn't be read, so the title is the ref alone.
        self.assertEqual(self._view(body["launch_id"])["title"], "o/r#5")

        with self._stubs() as (_wt, ln):
            self._post({
                "repo": "o/r", "number": 5, "harness": "claude",
                "prompt": "Do it. Keep it small. Thanks",
                "context": "Keep it small."})
        self.assertEqual(ln.call_args[0][3], "Do it. Keep it small. Thanks")

        with self._stubs() as (_wt, ln):
            self._post({"repo": "o/r", "number": 5, "harness": "claude",
                        "context": "Keep it small."})
        built = ln.call_args[0][3]
        self.assertIn("Context for this run, from octocat:\nKeep it small.",
                      built)
        self.assertTrue(built.startswith("Resolve o/r#5"))

    def test_refusals_answer_422_before_any_launch(self):
        cases = [
            ({"repo": "nope", "number": 5, "harness": "claude"}, {},
             "bad_command"),
            ({"repo": "o/r", "number": "x", "harness": "claude"}, {},
             "bad_command"),
            ({"repo": "o/r", "number": True, "harness": "claude"}, {},
             "bad_command"),
            ({"repo": "o/r", "number": 5, "harness": "Not A Name"}, {},
             "bad_command"),
            ({"repo": "o/r", "number": 5, "harness": "claude",
              "context": "x" * (issues_mod.MAX_CONTEXT + 1)}, {},
             "bad_command"),
            ({"repo": "other/repo", "number": 5, "harness": "claude"}, {},
             "not_pinned"),
            ({"repo": "o/r", "number": 5, "harness": "claude"},
             {"installed": False}, "not_launchable"),
            ({"repo": "o/r", "number": 5, "harness": "claude"},
             {"tmux": False}, "launch_failed"),
            ({"repo": "o/r", "number": 5, "harness": "claude"},
             {"env": gh.GhEnv(installed=True, authenticated=False)},
             "gh_unavailable"),
        ]
        for req, stubs, code in cases:
            with self.subTest(code=code, req=req):
                with self._stubs(**stubs) as (wt, ln):
                    status, body = self._post(req)
                self.assertEqual((status, body["error"]["code"]),
                                 (422, code), body)
                wt.assert_not_called()
                ln.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})
        with self._stubs(env=gh.GhEnv(installed=True, authenticated=False)):
            _s, body = self._post(
                {"repo": "o/r", "number": 5, "harness": "claude"})
        self.assertIn("gh auth login", body["error"]["message"])

    def test_unreadable_body(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port,
                                          timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", "/v1/issues/resolve", body=b"{not json",
                     headers={**self.hdr,
                              "Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400, resp.read())
        with self.assertRaises(ValueError):
            issues_mod.parse_body([1, 2])

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request(
            "POST", "/v1/issues/resolve",
            body={"repo": "o/r", "number": 5, "harness": "claude"})
        self.assertEqual(status, 401)


class TestFinalPrompt(unittest.TestCase):
    def test_context_added_once(self):
        cmd = {"repo": "o/r", "number": 5, "prompt": "P", "context": ""}
        self.assertEqual(issues_mod.final_prompt(cmd, None, "/w"), "P")
        cmd["context"] = "  C  "
        self.assertEqual(issues_mod.final_prompt(cmd, None, "/w", "me"),
                         "P\n\nContext for this run, from me:\nC")
        cmd["prompt"] = "P with C inside"
        self.assertEqual(issues_mod.final_prompt(cmd, None, "/w"),
                         "P with C inside")

    def test_no_issue_and_no_prompt_still_builds(self):
        cmd = {"repo": "o/r", "number": 5, "prompt": "", "context": ""}
        text = issues_mod.final_prompt(cmd, None, "/w")
        self.assertTrue(text.startswith("Resolve o/r#5"))
        self.assertIn("(no description)", text)


class TestRunLaunch(unittest.TestCase):
    """``run_launch``: the thread a resolve and a PR open share."""

    def _state(self):
        settled = []

        class State:
            def settle_launch(self, launch_id, **kw):
                settled.append(kw)
        return State(), settled

    def test_a_crash_settles_failed_with_the_verb_and_releases(self):
        state, settled = self._state()
        released = []

        def boom():
            raise RuntimeError("boom")

        issues_mod.run_launch(state, "l1", "claude", True, boom, "Open",
                              lambda: released.append(1))
        self.assertEqual(settled, [{"state": "failed",
                                    "note": "Open failed: boom"}])
        self.assertEqual(released, [1])

    def test_a_note_fails_and_an_untracked_harness_says_so(self):
        state, settled = self._state()
        issues_mod.run_launch(state, "l1", "claude", True,
                              lambda: "no worktree", "Resolve", lambda: None)
        issues_mod.run_launch(state, "l2", "aider", False,
                              lambda: "", "Resolve", lambda: None)
        self.assertEqual(settled[0], {"state": "failed", "note": "no worktree"})
        self.assertEqual(settled[1]["state"], "untracked")
        self.assertIn("aider", settled[1]["note"])


if __name__ == "__main__":
    unittest.main()
