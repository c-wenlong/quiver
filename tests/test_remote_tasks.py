"""remote/tasks.py: the task store, the link/unlink routes, the
``/v1/tasks`` aggregation and the launch ``origin`` plumbing.

Nothing real runs: ``gh``, the worktree and ``launch_new`` are stubbed,
so no network, no git remote and no harness process."""

import contextlib
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.github import cli as ghcli
from swe.github import gh, manage, repos
from swe.github import issues as gh_issues
from swe.github import pulls as gh_pulls
from swe.remote import protocol
from swe.remote import tasks as tasks_mod
from swe.github import shared as G

from tests.test_github_issues import RAW_ISSUE
from tests.test_remote_pulls import RAW_PR, _row, _pr_status
from tests.test_remote_hub import _Server, _session

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")


def _issue_row(number, title=None, branch=None):
    """A ``cli._issue_row`` entry."""
    return {"number": number, "title": title or f"Issue {number}",
            "state": "open", "author": "octocat", "labels": [],
            "assignees": [], "comments": 0,
            "updated_at": "2026-01-02T00:00:00Z",
            "url": f"https://github.com/o/r/issues/{number}",
            "branch": branch}


ISSUE_REF = {"source": "github", "kind": "issue", "repo": "o/r",
             "ref": "5", "title": "Fix the thing",
             "url": "https://github.com/o/r/issues/5"}
PULL_REF = {"source": "github", "kind": "pull", "repo": "o/r",
            "ref": "7", "title": "PR 7",
            "url": "https://github.com/o/r/pull/7"}


class TestTaskRef(unittest.TestCase):
    def test_github_issue_ref(self):
        ref = tasks_mod.parse_ref({"source": "github", "kind": "issue",
                                   "repo": "O/R", "ref": "#42",
                                   "title": "T"})
        self.assertEqual(ref["ref"], "42")
        self.assertEqual(ref["repo"], "O/R")  # pinned spelling kept
        self.assertEqual(ref["url"], "https://github.com/O/R/issues/42")
        self.assertEqual(tasks_mod.task_id(ref), "github:issue:o/r:42")

    def test_github_pull_ref(self):
        ref = tasks_mod.parse_ref({"source": "github", "kind": "pull",
                                   "repo": "o/r", "ref": 7,
                                   "title": "T", "url": ""})
        self.assertEqual(ref["url"], "https://github.com/o/r/pull/7")

    def test_linear_and_notion_refs(self):
        card = tasks_mod.parse_ref({"source": "linear", "kind": "card",
                                    "project": "QVR", "ref": "QVR-12",
                                    "title": "x",
                                    "url": "https://linear.app/a/issue/qvr-12"})
        self.assertEqual(tasks_mod.task_id(card), "linear:card:qvr:qvr-12")
        page = tasks_mod.parse_ref({"source": "notion", "kind": "page",
                                    "ref": "abc-123", "title": "x",
                                    "url": "https://notion.so/abc-123"})
        self.assertEqual(tasks_mod.task_id(page), "notion:page::abc-123")

    def test_bad_refs(self):
        bad = [
            "not a dict",
            {"kind": "issue", "repo": "o/r", "ref": "5"},     # no source
            {"source": "github", "kind": "card", "repo": "o/r",
             "ref": "5"},                                      # bad kind
            {"source": "jira", "kind": "issue", "ref": "5"},  # bad source
            {"source": "github", "kind": "issue", "ref": "5"},  # no repo
            {"source": "github", "kind": "issue", "repo": "r",
             "ref": "5"},                                      # bad repo
            {"source": "github", "kind": "issue", "repo": 7,
             "ref": "5"},                               # non-string repo
            {"source": "github", "kind": "issue", "repo": "o/r"},  # no ref
            {"source": "github", "kind": "issue", "repo": "o/r",
             "ref": "x"},                                      # bad ref
            {"source": "github", "kind": "issue", "repo": "o/r",
             "ref": "0"},                                      # zero
            {"source": "linear", "kind": "card", "ref": True},  # bool ref
            {"source": "linear", "kind": "card", "ref": "x" * 201},  # long
            {"source": "linear", "kind": "card", "ref": "Q-1",
             "project": "p" * 201},                        # long project
            {"source": "notion", "kind": "page", "ref": "a",
             "url": "javascript:alert(1)"},                 # bad scheme
            {"source": "notion", "kind": "page", "ref": "a",
             "url": "https:no-host"},                       # no host
            {"source": "notion", "kind": "page", "ref": "a",
             "url": "https://:80/path"},                    # port, no host
            {"source": "notion", "kind": "page", "ref": "a",
             "url": "http://[bad"},                         # unparsable
            {"source": "notion", "kind": "page", "ref": "a",
             "url": "https://x.io/" + "a" * 2048},          # long url
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                tasks_mod.parse_ref(body)

    def test_fields_at_their_bounds_pass(self):
        base = {"source": "linear", "kind": "card"}
        url = "https://x.io/"
        url += "a" * (tasks_mod.MAX_URL - len(url))
        ok = tasks_mod.parse_ref({**base, "ref": "r" * tasks_mod.MAX_REF,
                                  "project": "p" * tasks_mod.MAX_PROJECT,
                                  "url": url})
        self.assertEqual(len(ok["url"]), tasks_mod.MAX_URL)
        for field, value in (("ref", "r" * (tasks_mod.MAX_REF + 1)),
                             ("project", "p" * (tasks_mod.MAX_PROJECT + 1)),
                             ("url", url + "a")):
            with self.assertRaises(ValueError, msg=field):
                tasks_mod.parse_ref({**base, "ref": "Q-1", field: value})

    def test_a_long_title_is_cut_not_refused(self):
        ref = tasks_mod.parse_ref({"source": "linear", "kind": "card",
                                   "ref": "Q-1", "title": "t" * 5000})
        self.assertEqual(len(ref["title"]), tasks_mod.MAX_TITLE)

    def test_origin_refs(self):
        issue = gh_issues.shape(RAW_ISSUE, "o/r", Path("/tmp/h"))
        ref = tasks_mod.issue_ref("o/r", 5, issue)
        self.assertEqual((ref["kind"], ref["ref"], ref["title"]),
                         ("issue", "5", "Fix the thing"))
        self.assertEqual(tasks_mod.issue_ref("o/r", 5, None)["url"],
                         "https://github.com/o/r/issues/5")
        pr = gh_pulls.shape(RAW_PR, "o/r", Path("/tmp/h"))
        ref = tasks_mod.pull_ref("o/r", 5, pr)
        self.assertEqual((ref["kind"], ref["ref"]), ("pull", "5"))
        self.assertEqual(tasks_mod.pull_task_id("o/r", 5),
                         "github:pull:o/r:5")


class TestStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def _entries(self):
        return tasks_mod._load(self.home)

    def test_link_creates_the_file_private(self):
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1",
                       launch_id="l1")
        path = tasks_mod.tasks_file(self.home)
        self.assertEqual(path.name, "tasks.json")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        tid = tasks_mod.task_id(ISSUE_REF)
        entry = self._entries()[tid]
        self.assertEqual(entry["sessions"], ["claude:s1"])
        self.assertEqual(entry["launches"], ["l1"])
        self.assertEqual(entry["task"]["title"], "Fix the thing")
        self.assertLessEqual(entry["created_at"],
                             entry["last_active_at"])

    def test_link_dedupes_and_freshens_the_ref(self):
        tid = tasks_mod.task_id(ISSUE_REF)
        tasks_mod.link(self.home, {**ISSUE_REF, "title": ""},
                       session_key="claude:s1")
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1",
                       launch_id="l1")
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s2")
        entry = self._entries()[tid]
        self.assertEqual(entry["sessions"], ["claude:s1", "claude:s2"])
        self.assertEqual(entry["launches"], ["l1"])
        self.assertEqual(entry["task"]["title"], "Fix the thing")

    def test_unlink_one_and_all(self):
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1")
        tasks_mod.link(self.home, PULL_REF, session_key="claude:s1",
                       launch_id="l1")
        i_tid = tasks_mod.task_id(ISSUE_REF)
        p_tid = tasks_mod.task_id(PULL_REF)
        left = tasks_mod.unlink(self.home, "claude:s1", i_tid)
        self.assertEqual(left, [i_tid])
        entries = self._entries()
        self.assertNotIn(i_tid, entries)      # empty task is dropped
        self.assertIn(p_tid, entries)          # its launch keeps it
        left = tasks_mod.unlink(self.home, "claude:s1")
        self.assertEqual(left, [p_tid])
        # The launch link keeps the task; only its sessions emptied.
        kept = self._entries()[p_tid]
        self.assertEqual(kept["sessions"], [])
        self.assertEqual(kept["launches"], ["l1"])

    def test_corrupt_file_reads_empty_and_recovers(self):
        path = tasks_mod.tasks_file(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self._entries(), {})
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1")
        self.assertEqual(self._entries()[tasks_mod.task_id(ISSUE_REF)]
                         ["sessions"], ["claude:s1"])

    def test_malformed_link_fields_read_empty(self):
        """Valid JSON with ``"sessions": 7`` or a bare string must not
        raise — both read as no links, not a crash or character soup."""
        path = tasks_mod.tasks_file(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        tid = tasks_mod.task_id(ISSUE_REF)
        path.write_text(json.dumps({"tasks": {tid: {
            "task": ISSUE_REF, "sessions": 7,
            "launches": "abc", "created_at": 1,
            "last_active_at": 1}}}), encoding="utf-8")
        entry = self._entries()[tid]
        self.assertEqual(entry["sessions"], [])
        self.assertEqual(entry["launches"], [])

    def test_state_cache_reads_empty_when_corrupt(self):
        path = tasks_mod.states_file(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(tasks_mod._load_states(self.home), {})
        path.write_text(json.dumps({"states": {
            "a": {"state": 7}, "b": "x",
            "c": {"state": "closed", "pr": "junk"}}}), encoding="utf-8")
        self.assertEqual(tasks_mod._load_states(self.home),
                         {"c": {"state": "closed", "pr": None}})

    def test_remember_writes_changes_and_drops_unstored_tasks(self):
        i_tid = tasks_mod.task_id(ISSUE_REF)
        p_tid = tasks_mod.task_id(PULL_REF)
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1")
        tasks_mod.link(self.home, PULL_REF, session_key="claude:s2")
        tasks_mod._remember(self.home, {
            i_tid: {"state": "open", "pr": None},
            p_tid: {"state": "closed", "pr": None}})
        # The pull left the store; the next write forgets it.
        tasks_mod.unlink(self.home, "claude:s2")
        tasks_mod._remember(self.home, {
            i_tid: {"state": "closed", "pr": None}})
        self.assertEqual(tasks_mod._load_states(self.home),
                         {i_tid: {"state": "closed", "pr": None}})

    def test_remember_prunes_against_the_store_as_it_is_now(self):
        """A slow poll that started before a task was linked must not
        prune that task's entry, which a newer poll already wrote."""
        i_tid = tasks_mod.task_id(ISSUE_REF)
        p_tid = tasks_mod.task_id(PULL_REF)
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1")
        tasks_mod.link(self.home, PULL_REF, session_key="claude:s2")
        tasks_mod._remember(self.home, {
            p_tid: {"state": "merged", "pr": None}})
        # The older poll only knew the issue.
        tasks_mod._remember(self.home, {
            i_tid: {"state": "closed", "pr": None}})
        self.assertEqual(set(tasks_mod._load_states(self.home)),
                         {i_tid, p_tid})

    def test_remember_survives_an_unwritable_cache(self):
        tasks_mod.link(self.home, ISSUE_REF, session_key="claude:s1")
        with patch("swe.remote.tasks.atomic_write_text",
                   side_effect=OSError("read-only")):
            tasks_mod._remember(self.home, {
                tasks_mod.task_id(ISSUE_REF): {"state": "open",
                                               "pr": None}})
        self.assertEqual(tasks_mod._load_states(self.home), {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        repos.add_pinned("o/r", manage.pins_file(self.srv.home))
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _get(self, path):
        status, data = self.srv.request("GET", path, headers=self.hdr)
        return status, json.loads(data)

    def _post(self, path, body):
        status, data = self.srv.request(
            "POST", path, body=body, headers=self.hdr)
        return status, json.loads(data)

    def _delete(self, path, body=None):
        status, data = self.srv.request(
            "DELETE", path, body=body, headers=self.hdr)
        return status, json.loads(data)

    def _task(self, tid):
        _s, body = self._get("/v1/tasks")
        for row in body["tasks"]:
            if row["id"] == tid:
                return row
        return None


class TestTasksRoute(_Base):
    """``GET /v1/tasks`` aggregation."""

    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, pulls=None, issues=None, run_json=None):
        pulls = pulls if pulls is not None else (
            lambda name, login: {"name": name, "status": "ok",
                                 "pulls": [_row(7)]})
        issues = issues if issues is not None else (
            lambda name, state, limit: {
                "repo": name, "status": "ok",
                "issues": [_issue_row(5), _issue_row(6)]})
        with patch("swe.github.gh.probe_env", return_value=env), \
             patch("swe.remote.pulls._repo_pulls",
                   side_effect=pulls), \
             patch("swe.github.cli._fetch_issues",
                   side_effect=issues), \
             patch("swe.github.gh.run_json",
                   side_effect=run_json or (lambda *a, **k:
                                            gh.GhResult("not-found"))):
            yield

    def test_lists_open_work_with_derived_states(self):
        with self._stubs():
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["github"]["login"], "octocat")
        by_id = {t["id"]: t for t in body["tasks"]}
        issue = by_id["github:issue:o/r:5"]
        self.assertEqual((issue["source"], issue["kind"],
                          issue["repo"], issue["ref"]),
                         ("github", "issue", "o/r", "5"))
        self.assertEqual(issue["state"], "open")
        self.assertEqual(issue["status"], "Open")
        self.assertFalse(issue["agent_running"])
        self.assertFalse(issue["linked"])
        self.assertEqual(issue["sessions"], [])
        self.assertEqual(by_id["github:pull:o/r:7"]["state"], "open")
        self.assertIn("fetched_at", body)

    def test_issue_on_its_quiver_branch_reads_pr_open(self):
        pr = {**_row(9), "branch": "quiver/issue-5"}
        pulls = lambda name, login: {"name": name, "status": "ok",
                                     "pulls": [pr]}
        with self._stubs(pulls=pulls):
            _s, body = self._get("/v1/tasks")
        row = {t["id"]: t for t in body["tasks"]}["github:issue:o/r:5"]
        self.assertEqual(row["state"], "pr_open")
        self.assertEqual(row["status"], "PR open")
        self.assertEqual(row["pr"]["number"], 9)
        self.assertEqual(row["pr"]["state"], "open")

    def test_a_fork_branch_does_not_mark_the_issue(self):
        """Someone else's fork with a quiver/issue-<n> branch is not
        the PR the hub's worktree produced."""
        fork = {**_row(9), "branch": "quiver/issue-5",
                "head_owner": "someone-else"}
        pulls = lambda name, login: {"name": name, "status": "ok",
                                     "pulls": [fork]}
        with self._stubs(pulls=pulls):
            _s, body = self._get("/v1/tasks")
        row = {t["id"]: t for t in body["tasks"]}["github:issue:o/r:5"]
        self.assertEqual(row["state"], "open")
        self.assertIsNone(row["pr"])

    def test_linked_pr_skips_a_forks_pr_listed_first(self):
        # gh's --head matches the branch name alone: a fork's PR from a
        # same-named branch can come back first, and a limit of 1 then
        # hid the repo's own merged PR.
        fork = {"number": 11, "title": "fork", "state": "OPEN",
                "headRefName": "quiver/issue-5",
                "headRepositoryOwner": {"login": "someone-else"}}
        own = {"number": 9, "title": "mine", "state": "MERGED",
               "headRefName": "quiver/issue-5",
               "headRepositoryOwner": {"login": "o"}}
        with patch("swe.github.gh.run_json",
                   return_value=gh.GhResult("ok", data=[fork, own])) as run:
            answered, pr = tasks_mod.GitHubTasks(
                self.srv.home)._linked_pr("o/r", 5)
        args = run.call_args[0][0]
        self.assertGreater(int(args[args.index("--limit") + 1]), 1)
        self.assertTrue(answered)
        self.assertEqual(pr["number"], 9)

    def test_linked_session_runs_and_finishes(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertEqual(row["state"], "in_progress")
        self.assertEqual(row["status"], "Agent running")
        self.assertTrue(row["agent_running"])
        self.assertEqual(row["sessions"], ["claude:s1"])
        # The session finishing flips the flag off.
        self.srv.state.catalog._status_of = (
            lambda ss: ["done"] * len(ss))
        self.srv.state.catalog.refresh()
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertFalse(row["agent_running"])
        # The ladder still sorts it as worked on; what the row says is
        # GitHub's own word, not "in progress" for ever (#875).
        self.assertEqual(row["state"], "in_progress")
        self.assertEqual(row["status"], "Open")

    def test_stored_task_gets_fresh_state(self):
        """A closed issue leaves the open list; ``get`` still tells
        the stored task it is closed, and a merged PR reads merged."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        merged = {**_row(9), "state": "merged",
                  "branch": "quiver/issue-5"}

        def run_json(args, **kw):
            if args[:2] == ["issue", "view"]:
                return gh.GhResult("ok", data={
                    "number": 5, "title": "Fix the thing",
                    "state": "CLOSED",
                    "url": "https://github.com/o/r/issues/5"})
            if args[:2] == ["pr", "list"]:
                return gh.GhResult("ok", data=[{
                    "number": 9, "title": "PR 9", "state": "MERGED",
                    "isDraft": False, "headRefName": "quiver/issue-5",
                    "url": "https://github.com/o/r/pull/9"}])
            return gh.GhResult("not-found")

        with self._stubs(run_json=run_json):
            row = self._task("github:issue:o/r:5")
        self.assertEqual(row["state"], "merged")
        self.assertEqual(row["pr"]["state"], "merged")

    def test_env_blocked_still_serves_the_store(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env):
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertIn("gh auth login", body["env_blocked"])
        row = {t["id"]: t for t in body["tasks"]}[
            "github:issue:o/r:5"]
        self.assertTrue(row["agent_running"])
        self.assertEqual(row["state"], "in_progress")
        # Never read, nothing cached: the state is the links' guess.
        self.assertEqual(row["state_source"], "links")

    @staticmethod
    def _closed_with_merged_pr(args, **kw):
        if args[:2] == ["issue", "view"]:
            return gh.GhResult("ok", data={
                "number": 5, "title": "Fix the thing", "state": "CLOSED",
                "url": "https://github.com/o/r/issues/5"})
        if args[:2] == ["pr", "list"]:
            return gh.GhResult("ok", data=[{
                "number": 9, "title": "PR 9", "state": "MERGED",
                "isDraft": False, "headRefName": "quiver/issue-5",
                "url": "https://github.com/o/r/pull/9"}])
        return gh.GhResult("not-found")

    def _states(self):
        return tasks_mod._load_states(self.srv.home)

    def test_blocked_source_serves_the_last_known_state(self):
        """A merged issue stays merged while ``gh`` is signed out,
        rather than reading ``in_progress`` off its link again."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tid = tasks_mod.task_id(ISSUE_REF)
        with self._stubs(run_json=self._closed_with_merged_pr):
            row = self._task(tid)
        self.assertEqual((row["state"], row["state_source"]),
                         ("merged", "live"))
        path = tasks_mod.states_file(self.srv.home)
        self.assertEqual(path.name, "task_states.json")
        self.assertEqual(path.parent.name, "cache")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(self._states()[tid]["state"], "closed")
        # tasks.json stays links only.
        stored = json.loads(tasks_mod.tasks_file(
            self.srv.home).read_text(encoding="utf-8"))["tasks"][tid]
        self.assertNotIn("state", stored)
        self.assertNotIn("pr", stored)

        G.CACHE.clear()
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env):
            row = self._task(tid)
        self.assertEqual((row["state"], row["state_source"]),
                         ("merged", "cached"))
        self.assertEqual(row["pr"]["number"], 9)
        self.assertEqual(row["pr"]["state"], "merged")

    def test_a_quiet_poll_writes_nothing(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        writes = []
        real = tasks_mod.atomic_write_text

        def spy(path, text, **kw):
            if Path(path) == tasks_mod.states_file(self.srv.home):
                writes.append(text)
            return real(path, text, **kw)

        with patch("swe.remote.tasks.atomic_write_text",
                   side_effect=spy), \
                self._stubs(run_json=self._closed_with_merged_pr):
            self._get("/v1/tasks")
            self._get("/v1/tasks?refresh=1")
            self._get("/v1/tasks")
        self.assertEqual(len(writes), 1)

    def test_outside_the_refresh_window_reads_the_cache(self):
        """The source answers, but the task is off the open list and
        past ``MAX_REFRESH``: no read this round, so the cache speaks."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tid = tasks_mod.task_id(ISSUE_REF)
        no_issues = lambda name, state, limit: {
            "repo": name, "status": "ok", "issues": []}
        with self._stubs(issues=no_issues,
                         run_json=self._closed_with_merged_pr):
            self._task(tid)
        G.CACHE.clear()
        with patch.object(tasks_mod, "MAX_REFRESH", 0), \
                self._stubs(issues=no_issues):
            row = self._task(tid)
        self.assertEqual((row["state"], row["state_source"]),
                         ("merged", "cached"))

    def test_a_reopened_issue_updates_the_cache(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tid = tasks_mod.task_id(ISSUE_REF)
        closed = lambda args, **kw: (
            gh.GhResult("ok", data={
                "number": 5, "title": "Fix the thing", "state": "CLOSED",
                "url": "https://github.com/o/r/issues/5"})
            if args[:2] == ["issue", "view"] else gh.GhResult("ok", data=[]))
        no_issues = lambda name, state, limit: {
            "repo": name, "status": "ok", "issues": []}
        with self._stubs(issues=no_issues, run_json=closed):
            self.assertEqual(self._task(tid)["state"], "closed")
        self.assertEqual(self._states()[tid],
                         {"state": "closed", "pr": None})
        # Back on the open list; the per-task read fails this round.
        G.CACHE.clear()
        with self._stubs():
            row = self._task(tid)
        self.assertEqual((row["state"], row["state_source"]),
                         ("in_progress", "live"))
        self.assertEqual(self._states()[tid],
                         {"state": "open", "pr": None})

    def test_a_failed_pr_lookup_keeps_the_remembered_pr(self):
        """``issue view`` answering while the branch's ``pr list``
        fails says nothing about the PR: the merged one stays."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tid = tasks_mod.task_id(ISSUE_REF)
        with self._stubs(run_json=self._closed_with_merged_pr):
            self._task(tid)

        def pr_list_fails(args, **kw):
            if args[:2] == ["pr", "list"]:
                return gh.GhResult("upstream-error", error="502")
            return self._closed_with_merged_pr(args, **kw)

        G.CACHE.clear()
        with self._stubs(run_json=pr_list_fails):
            row = self._task(tid)
        self.assertEqual((row["state"], row["state_source"]),
                         ("merged", "live"))
        self.assertEqual(self._states()[tid]["pr"]["state"], "merged")
        G.CACHE.clear()
        with self._stubs(env=gh.GhEnv(installed=True,
                                      authenticated=False)):
            row = self._task(tid)
        self.assertEqual((row["state"], row["state_source"]),
                         ("merged", "cached"))

    def test_an_open_pr_gone_from_the_open_list_is_forgotten(self):
        """The open list rules out an open PR on the branch, so a
        remembered open one goes; it can't rule out a merged one."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tid = tasks_mod.task_id(ISSUE_REF)
        pr = {**_row(9), "branch": "quiver/issue-5"}
        with self._stubs(pulls=lambda name, login: {
                "name": name, "status": "ok", "pulls": [pr]}):
            self.assertEqual(self._task(tid)["state"], "pr_open")
        self.assertEqual(self._states()[tid]["pr"]["state"], "open")
        G.CACHE.clear()
        with patch.object(tasks_mod, "MAX_REFRESH", 0), self._stubs():
            row = self._task(tid)
        self.assertEqual(row["state"], "in_progress")
        self.assertIsNone(row["pr"])
        self.assertIsNone(self._states()[tid]["pr"])

    def test_listed_rows_read_live(self):
        with self._stubs():
            _s, body = self._get("/v1/tasks")
        self.assertTrue(body["tasks"])
        self.assertEqual({t["state_source"] for t in body["tasks"]},
                         {"live"})
        # Nothing is stored, so nothing is remembered.
        self.assertFalse(tasks_mod.states_file(self.srv.home).exists())

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/tasks")
        self.assertEqual(status, 401)


class TestLinkRoutes(_Base):
    """``POST`` and ``DELETE /v1/sessions/{key}/task``."""

    def _gh_ok(self):
        env = OK_ENV
        return patch("swe.github.gh.probe_env", return_value=env), \
            patch("swe.remote.pulls._repo_pulls",
                  return_value={"name": "o/r", "status": "ok",
                                "pulls": []}), \
            patch("swe.github.cli._fetch_issues",
                  return_value={"repo": "o/r", "status": "ok",
                                "issues": []}), \
            patch("swe.github.gh.run_json",
                  return_value=gh.GhResult("not-found"))

    def test_link_then_unlink_through_the_routes(self):
        status, body = self._post("/v1/sessions/claude:s1/task",
                                  {"task_ref": ISSUE_REF})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["task_id"], "github:issue:o/r:5")
        self.assertEqual(body["sessions"], ["claude:s1"])
        stored = tasks_mod._load(self.srv.home)
        self.assertIn("github:issue:o/r:5", stored)

        with contextlib.ExitStack() as stack:
            for p in self._gh_ok():
                stack.enter_context(p)
            row = self._task("github:issue:o/r:5")
        self.assertTrue(row["agent_running"])

        status, body = self._delete(
            "/v1/sessions/claude:s1/task", {"task_ref": ISSUE_REF})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["unlinked"], ["github:issue:o/r:5"])
        self.assertNotIn("github:issue:o/r:5",
                         tasks_mod._load(self.srv.home))

    def test_delete_without_a_ref_unlinks_everything(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tasks_mod.link(self.srv.home, PULL_REF, session_key="claude:s1")
        status, body = self._delete("/v1/sessions/claude:s1/task")
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body["unlinked"]),
                         {"github:issue:o/r:5", "github:pull:o/r:7"})

    def test_unlinking_a_gone_session_still_works(self):
        """A linked session key survives in the store after the catalog
        drops the session; DELETE must still remove the link."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:gone")
        self.assertIsNone(self.srv.state.catalog.row_for("claude:gone"))
        status, body = self._delete(
            "/v1/sessions/claude:gone/task", {"task_ref": ISSUE_REF})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["unlinked"], ["github:issue:o/r:5"])
        self.assertNotIn("github:issue:o/r:5",
                         tasks_mod._load(self.srv.home))

    def test_bad_ref_and_missing_session(self):
        status, body = self._post(
            "/v1/sessions/claude:s1/task", {"task_ref": {"source": "x"}})
        self.assertEqual((status, body["error"]["code"]),
                         (422, "bad_command"))
        status, _ = self._post("/v1/sessions/nope:1/task",
                               {"task_ref": ISSUE_REF})
        self.assertEqual(status, 404)
        # Linking still needs a catalogued session, but unlinking an
        # unknown key just confirms there is nothing left linked.
        status, body = self._delete("/v1/sessions/nope:1/task")
        self.assertEqual((status, body["unlinked"]), (200, []))
        status, body = self._post("/v1/sessions/claude:s1/task", {})
        self.assertEqual((status, body["error"]["code"]),
                         (422, "bad_command"))

    def test_delete_with_an_unreadable_body_is_400(self):
        """A malformed DELETE body must not fall through to unlink-all."""
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        tasks_mod.link(self.srv.home, PULL_REF, session_key="claude:s1")
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request("DELETE", "/v1/sessions/claude:s1/task",
                         body=b"{not json", headers=self.hdr)
            resp = conn.getresponse()
            data = json.loads(resp.read())
            status = resp.status
        finally:
            conn.close()
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "bad_body")
        self.assertEqual(
            set(tasks_mod._load(self.srv.home)),
            {"github:issue:o/r:5", "github:pull:o/r:7"})

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:s1/task",
            body={"task_ref": ISSUE_REF})
        self.assertEqual(status, 401)
        status, _ = self.srv.request(
            "DELETE", "/v1/sessions/claude:s1/task")
        self.assertEqual(status, 401)


class TestOrigin(_Base):
    """resolve → launch → adoption writes the link."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = str(Path(tmp.name) / "o__r-issue5")
        Path(self.tree).mkdir()
        self.issue = gh_issues.shape(RAW_ISSUE, "o/r", self.srv.home)
        self.pr = gh_pulls.shape(RAW_PR, "o/r", self.srv.home)

    def _wait(self):
        for t in threading.enumerate():
            if t.name.startswith("remote-launch-"):
                t.join(timeout=5)

    @contextlib.contextmanager
    def _resolve_stubs(self):
        with patch("swe.remote.catalog.launch_command",
                   return_value="claude"), \
             patch("swe.remote.catalog.launchable_harnesses",
                   return_value=[]), \
             patch("swe.remote.catalog.known_session_ids",
                   return_value=[]), \
             patch("swe.remote.supervise.tmux_bin",
                   return_value="/usr/bin/tmux"), \
             patch("swe.remote.supervise.fork_blocker",
                   return_value=None), \
             patch("swe.remote.catalog.untracked", return_value=[]), \
             patch("swe.github.gh.probe_env", return_value=OK_ENV), \
             patch("swe.github.issues.fetch",
                   return_value=gh.GhResult("ok", data=self.issue)), \
             patch("swe.github.manage.issue_worktree",
                   return_value=gh.GhResult("ok", data={
                       "path": self.tree, "branch": "quiver/issue-5",
                       "created": True})), \
             patch("swe.remote.supervise.launch_new",
                   return_value=(True, "launched", "qv-t")):
            try:
                yield
            finally:
                # Even when the test fails: the launch thread must end
                # while the patches still stand, or it runs for real.
                self._wait()

    def _resolve(self):
        status, data = self.srv.request(
            "POST", "/v1/issues/resolve",
            body={"repo": "o/r", "number": 5, "harness": "claude"},
            headers=self.hdr)
        return status, json.loads(data)

    def _adopt(self, launch_id, key="claude:s1"):
        self.srv.state.emit("_global", protocol.event(
            "session.launched", key, "m", {"launch_id": launch_id}))

    def test_resolve_origin_links_session_and_launch(self):
        with self._resolve_stubs():
            status, body = self._resolve()
        self.assertEqual(status, 202, body)
        lid = body["launch_id"]
        # The launch record carries its origin for the phone to see.
        view = self.srv.state.launch_view(lid)
        self.assertEqual(view["origin"]["ref"], "5")
        self.assertEqual(view["origin"]["kind"], "issue")
        # launch_linked marked the pending launch at settle time —
        # the task reads agent_running through the whole prepare
        # window — and adoption adds the session key on top.
        tid = tasks_mod.task_id(ISSUE_REF)
        entry = tasks_mod._load(self.srv.home)[tid]
        self.assertEqual(entry["sessions"], [])
        self.assertEqual(entry["launches"], [lid])
        self.assertIn(tid, tasks_mod.running_task_ids(self.srv.state))
        self._adopt(lid)
        entry = tasks_mod._load(self.srv.home)[tid]
        self.assertEqual(entry["sessions"], ["claude:s1"])
        self.assertEqual(entry["launches"], [lid])
        self.assertEqual(entry["task"]["title"], "Fix the thing")

    def test_open_origin_links_to_the_pull_task(self):
        tree = str(Path(self.tree).parent / "o__r-pr5")
        Path(tree).mkdir()
        with patch("swe.remote.catalog.launch_command",
                   return_value="claude"), \
             patch("swe.remote.catalog.launchable_harnesses",
                   return_value=[]), \
             patch("swe.remote.catalog.known_session_ids",
                   return_value=[]), \
             patch("swe.remote.supervise.tmux_bin",
                   return_value="/usr/bin/tmux"), \
             patch("swe.remote.supervise.fork_blocker",
                   return_value=None), \
             patch("swe.remote.catalog.untracked", return_value=[]), \
             patch("swe.github.gh.probe_env", return_value=OK_ENV), \
             patch("swe.github.pulls.fetch",
                   return_value=gh.GhResult("ok", data=self.pr)), \
             patch("swe.github.manage.pr_worktree",
                   return_value=gh.GhResult("ok", data={
                       "path": tree, "head": "abc", "created": True})), \
             patch("swe.remote.supervise.launch_new",
                   return_value=(True, "launched", "qv-t")):
            status, data = self.srv.request(
                "POST", "/v1/pulls/o/r/5/open",
                body={"harness": "claude"}, headers=self.hdr)
            self._wait()
        self.assertEqual(status, 202, data)
        body = json.loads(data)
        self._adopt(body["launch_id"])
        tid = tasks_mod.pull_task_id("o/r", 5)
        entry = tasks_mod._load(self.srv.home)[tid]
        self.assertEqual(entry["sessions"], ["claude:s1"])
        self.assertEqual(entry["task"]["kind"], "pull")

    def test_a_launch_without_origin_links_nothing(self):
        lid = self.srv.state.new_launch("m", "claude", "/tmp/x")
        self._adopt(lid)
        self.assertEqual(tasks_mod._load(self.srv.home), {})


class TestPullsAgentRunning(_Base):
    """``/v1/pulls`` rows carry ``agent_running`` from the store."""

    def test_linked_pull_runs(self):
        tasks_mod.link(self.srv.home, PULL_REF, session_key="claude:s1")
        with patch("swe.github.gh.probe_env", return_value=OK_ENV), \
             patch("swe.github.cli._fetch_prs",
                   return_value={"repo": "o/r", "status": "ok",
                                 "pull_requests": [_row(7), _row(8)]}), \
             patch("swe.github.cli._fetch_pr_status",
                   return_value=_pr_status()):
            status, body = self._get("/v1/pulls")
        self.assertEqual(status, 200, body)
        prs = {p["number"]: p for p in body["repos"][0]["pulls"]}
        self.assertTrue(prs[7]["agent_running"])
        self.assertFalse(prs[8]["agent_running"])

    def test_pending_launch_counts_as_running(self):
        lid = self.srv.state.new_launch("m", "claude", "/tmp/x")
        tasks_mod.link(self.srv.home, PULL_REF, launch_id=lid)
        with patch("swe.github.gh.probe_env", return_value=OK_ENV), \
             patch("swe.github.cli._fetch_prs",
                   return_value={"repo": "o/r", "status": "ok",
                                 "pull_requests": [_row(7)]}), \
             patch("swe.github.cli._fetch_pr_status",
                   return_value=_pr_status()):
            _s, body = self._get("/v1/pulls")
        self.assertTrue(body["repos"][0]["pulls"][0]["agent_running"])


class StatusWordsTest(unittest.TestCase):
    """``status``: the row's label in the source's words, never the ladder."""

    def status(self, source_name="", **row):
        return tasks_mod._status({"source": "github", "kind": "issue", **row},
                                 source_name)

    def test_github_reads_the_pipeline(self):
        self.assertEqual(self.status(state="open"), "Open")
        self.assertEqual(self.status(state="in_progress", agent_running=True),
                         "Agent running")
        self.assertEqual(self.status(state="in_progress", agent_running=False), "Open")
        self.assertEqual(self.status(state="pr_open", agent_running=True), "PR open")
        self.assertEqual(self.status(state="merged"), "Merged")
        self.assertEqual(self.status(state="closed"), "Closed")
        self.assertEqual(self.status(kind="pull", state="in_progress",
                                     pr={"draft": True}), "Draft")

    def test_linear_passes_its_workflow_state_through(self):
        row = {"source": "linear", "kind": "issue", "state": "in_progress"}
        self.assertEqual(tasks_mod._status(row, "In Review"), "In Review")
        # A stored row with no fresh read has no name: the ladder's word.
        self.assertEqual(tasks_mod._status(row, ""), "Started")
        self.assertEqual(tasks_mod._status({**row, "agent_running": True}, ""),
                         "Agent running")

