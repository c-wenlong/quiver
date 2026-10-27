"""remote/issues.py: POST /v1/issues/resolve on the hub.

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

from tests.test_github_issues import RAW_ISSUE
from tests.test_remote_hub import _Server, _session

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")


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
            yield wt, ln
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


if __name__ == "__main__":
    unittest.main()
