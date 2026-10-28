"""The Tasks page: ``GET /v1/app/tasks`` — the ``/v1/tasks`` payload
with the page's session and launch joins, and ``?harnesses=1``.

Nothing real runs: ``gh`` and Linear are stubbed, the launch ids the
store links live in the test hub's in-memory launch table."""

import contextlib
import json
import unittest
from unittest.mock import patch

from quiver.github import gh, manage, repos
from quiver.remote import tasks as tasks_mod
from quiver.webapp.pages import _github as G
from quiver.webapp.pages import tasks as T

from tests.test_remote_hub import _Server, _session
from tests.test_remote_pulls import _row
from tests.test_remote_tasks import _issue_row, ISSUE_REF, OK_ENV


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

    @contextlib.contextmanager
    def _stubs(self, env=OK_ENV, pulls=None, issues=None, run_json=None):
        pulls = pulls if pulls is not None else (
            lambda name, login: {"name": name, "status": "ok",
                                 "pulls": [_row(7)]})
        issues = issues if issues is not None else (
            lambda name, state, limit: {
                "repo": name, "status": "ok",
                "issues": [_issue_row(5), _issue_row(6)]})
        with patch("quiver.github.gh.probe_env", return_value=env), \
             patch("quiver.remote.pulls._repo_pulls",
                   side_effect=pulls), \
             patch("quiver.github.cli._fetch_issues",
                   side_effect=issues), \
             patch("quiver.github.gh.run_json",
                   side_effect=run_json or (lambda *a, **k:
                                            gh.GhResult("not-found"))):
            yield

    def _task(self, tid):
        _s, body = self._get("/v1/app/tasks")
        for row in body["tasks"]:
            if row["id"] == tid:
                return row
        return None


class TestPageRoute(_Base):
    def test_payload_shape_and_source_blocks(self):
        with self._stubs():
            status, body = self._get("/v1/app/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["github"], {"status": "ok",
                                          "login": "octocat",
                                          "error": None})
        # No linear section in config.json: the source is off, not failed.
        self.assertEqual(body["linear"]["status"], "disabled")
        self.assertIn("fetched_at", body)
        by_id = {t["id"]: t for t in body["tasks"]}
        self.assertEqual(sorted(by_id), [
            "github:issue:o/r:5", "github:issue:o/r:6",
            "github:pull:o/r:7"])
        issue = by_id["github:issue:o/r:5"]
        self.assertEqual((issue["source"], issue["kind"],
                          issue["repo"], issue["ref"], issue["state"]),
                         ("github", "issue", "o/r", "5", "open"))
        # The joins are always present, empty while nothing is linked.
        self.assertEqual((issue["sessions"], issue["session_rows"],
                          issue["launches"], issue["launch_rows"]),
                         ([], [], [], []))

    def test_args_reach_list_view_and_a_failure_becomes_the_status(self):
        seen = {}

        def view(state, args):
            seen["args"] = args
            return 200, {"tasks": []}
        with patch.object(tasks_mod, "list_view", side_effect=view):
            status, _ = self._get("/v1/app/tasks?refresh=1&x=a&x=b")
        self.assertEqual(status, 200)
        # The page's flat args become the hub's one-list-per-key dict.
        self.assertEqual(seen["args"], {"refresh": ["1"], "x": ["a"]})

        with patch.object(tasks_mod, "list_view", return_value=(
                503, {"error": {"code": "tasks_down",
                                "message": "the store won't read"}})):
            status, body = self._get("/v1/app/tasks")
        self.assertEqual((status, body["error"]["code"]),
                         (503, "tasks_down"))
        self.assertIn("the store won't read", body["error"]["message"])

    def test_needs_a_paired_device(self):
        status, _ = self.srv.request("GET", "/v1/app/tasks")
        self.assertEqual(status, 401)
        status, _ = self.srv.request(
            "GET", "/v1/app/tasks?harnesses=1")
        self.assertEqual(status, 401)

    def test_harnesses_answer(self):
        launchable = [{"name": "claude", "command": "claude",
                       "display_name": "Claude Code", "tracked": True}]
        with patch("quiver.remote.catalog.launchable_harnesses",
                   return_value=launchable):
            status, body = self._get("/v1/app/tasks?harnesses=1")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["harnesses"],
                         [{"name": "claude",
                           "display_name": "Claude Code",
                           "tracked": True}])
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        # The flag doesn't pull the task list at all.
        with patch("quiver.remote.catalog.launchable_harnesses",
                   return_value=[]), \
             patch.object(tasks_mod, "list_view") as view:
            status, body = self._get("/v1/app/tasks?harnesses=1")
        view.assert_not_called()
        self.assertEqual(body["harnesses"], [])


class TestSessionJoin(_Base):
    def test_a_linked_session_joins_its_catalog_row(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertEqual(row["state"], "in_progress")
        self.assertTrue(row["agent_running"])
        self.assertEqual(row["sessions"], ["claude:s1"])
        brief = row["session_rows"]
        self.assertEqual(len(brief), 1)
        s = brief[0]
        self.assertEqual((s["key"], s["harness"], s["title"],
                          s["status"], s["cwd"]),
                         ("claude:s1", "claude", "test", "active",
                          "/tmp/proj"))
        # A row the page doesn't draw stays off the wire.
        self.assertNotIn("resume", s)
        self.assertNotIn("raw", s)

    def test_a_session_the_catalog_lost_reads_gone(self):
        tasks_mod.link(self.srv.home, ISSUE_REF,
                       session_key="claude:elsewhere")
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertFalse(row["agent_running"])
        self.assertEqual(row["session_rows"],
                         [{"key": "claude:elsewhere", "gone": True}])
        # A gone session still counts as linked.
        self.assertTrue(row["linked"])
        self.assertEqual(row["state"], "in_progress")

    def test_sessions_stay_in_link_order(self):
        tasks_mod.link(self.srv.home, ISSUE_REF,
                       session_key="claude:elsewhere")
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertEqual(
            [s["key"] for s in row["session_rows"]],
            ["claude:elsewhere", "claude:s1"])


class TestLaunchJoin(_Base):
    def test_pending_and_started_launches(self):
        lid = self.srv.state.new_launch("m1", "claude", "/tmp/o__r")
        tasks_mod.link(self.srv.home, ISSUE_REF, launch_id=lid)
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        (launch,) = row["launch_rows"]
        self.assertEqual((launch["id"], launch["state"],
                          launch["harness"], launch["machine"],
                          launch["cwd"], launch["key"]),
                         (lid, "pending", "claude", "m1",
                          "/tmp/o__r", ""))
        self.assertTrue(row["agent_running"])

        self.srv.state.settle_launch(lid, state="started",
                                     key="claude:s1")
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertEqual(row["launch_rows"][0]["state"], "started")
        self.assertEqual(row["launch_rows"][0]["key"], "claude:s1")

    def test_failed_and_untracked_launches(self):
        failed = self.srv.state.new_launch("m1", "claude", "/tmp/x")
        self.srv.state.settle_launch(failed, state="failed",
                                     note="no worktree")
        untracked = self.srv.state.new_launch("m1", "qwen", "/tmp/y")
        self.srv.state.settle_launch(untracked, state="untracked",
                                     note="can't read sessions")
        tasks_mod.link(self.srv.home, ISSUE_REF, launch_id=failed)
        tasks_mod.link(self.srv.home, ISSUE_REF, launch_id=untracked)
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertEqual(
            [(l["state"], l["note"]) for l in row["launch_rows"]],
            [("failed", "no worktree"),
             ("untracked", "can't read sessions")])

    def test_a_launch_the_hub_forgot_is_left_off(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, launch_id="l-ancient")
        with self._stubs():
            row = self._task("github:issue:o/r:5")
        self.assertEqual(row["launch_rows"], [])
        # …but the store's launch list still says it was linked.
        self.assertEqual(row["launches"], ["l-ancient"])
        self.assertTrue(row["linked"])


class TestStoreOverlay(_Base):
    def test_env_blocked_still_serves_the_store(self):
        tasks_mod.link(self.srv.home, ISSUE_REF, session_key="claude:s1")
        env = gh.GhEnv(installed=True, authenticated=False)
        with self._stubs(env=env):
            status, body = self._get("/v1/app/tasks")
        self.assertEqual(status, 200, body)
        self.assertIn("gh auth login", body["env_blocked"])
        row = {t["id"]: t for t in body["tasks"]}["github:issue:o/r:5"]
        self.assertTrue(row["agent_running"])
        self.assertEqual(row["session_rows"][0]["title"], "test")


if __name__ == "__main__":
    unittest.main()
