"""The Tasks page: ``GET /v1/app/tasks`` — the ``/v1/tasks`` payload
with the page's session and launch joins, and ``?harnesses=1``.

Nothing real runs: ``gh`` and Linear are stubbed, the launch ids the
store links live in the test hub's in-memory launch table."""

import contextlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.github import gh, manage, repos
from quiver.remote import tasks as tasks_mod
from quiver.github import shared as G
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
        # The rows are the Repositories page's harnesses(), tested there.
        with patch("quiver.webapp.pages.tasks.harnesses",
                   return_value=[{"name": "claude"}]) as rows:
            status, body = self._get("/v1/app/tasks?harnesses=1")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["harnesses"], [{"name": "claude"}])
        rows.assert_called_once_with(self.srv.home)
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


# The list as tasks.js paints it: seven tasks in o/a (one running), one
# merged in o/b, two in Linear's Core project and one with no project,
# and x/y's read failing. ``click`` presses a control the last paint
# bound, found by its selector and data attribute.
LIST_FLOW = r"""
const out = {};
const lastQ = {};
const qsa = panel.querySelectorAll.bind(panel);
panel.querySelectorAll = (sel) => (lastQ[sel] = qsa(sel));
const click = (attr, value) => lastQ[`[data-${attr}]`]
  .find((e) => e.attrs[`data-${attr}`] === value).onclick();
// The header's actions hold the chips (and Refresh when there's no rail).
globalThis.pageHeader = (title, subtitle, actions = "") => `<header>${actions}</header>`;
const row = (id, repo, extra = {}) => ({ id, source: "github", kind: "issue", ref: id,
  title: "T" + id, repo, state: "open", status: "Open", sessions: [], ...extra });
const lin = (id, project) => ({ id, source: "linear", kind: "issue", ref: id,
  title: "L" + id, project, state: "open", status: "Todo", sessions: [] });
const all = [
  ...[1, 2, 3, 4, 5, 6, 7].map((n) => row(String(n), "o/a", n === 1 ? { agent_running: true } : {})),
  row("8", "o/b", { state: "merged" }), lin("ENG-1", "Core"), lin("ENG-2", "Core"), lin("ENG-3", null)];
let data = { machine: "hub", github: { status: "ok" }, tasks: all,
  repos: [{ repo: "x/y", status: "upstream-error", error: "x/y timed out" }] };
apiAnswers.push(["/v1/app/tasks", { status: 200, get data() { return data; } }]);
const html = () => panel.innerHTML;
const rows = () => (html().match(/class="gh-row gh-center"/g) || []).length;
const rail = () => html().slice(html().indexOf('<aside class="gh-rail"'), html().indexOf("</aside>"));
const railNames = () => [...rail().matchAll(/class="gh-owner">([^<]*)<|class="gh-rail-name">([^<]*)</g)]
  .map((m) => m[1] != null ? "#" + m[1] : m[2]);
(async () => {
  location.hash = "#/tasks";
  PAGES.tasks();
  await tick();
  out.withRail = html().includes("gh-dash with-rail");
  out.refreshInRail = rail().includes(`id="page-refresh"`) && html().split(`id="page-refresh"`).length === 2;
  out.allCount = (rail().match(/All tasks<\/span>\s*<span class="gh-spacer"><\/span><span class="caption num gh-muted">(\d+)/) || [])[1];
  out.railNames = railNames();
  out.running = /data-shown="o\/a"[^>]*>\s*<span class="tk-rail-mark"><span class="gh-dot tk-dot-agent"/.test(rail());
  out.folded = rows();
  out.more = (html().match(/Show (\d+) more/) || [])[1];
  out.failedGroup = html().includes("x/y timed out");
  click("expand", "o/a:all");
  out.expanded = rows();
  out.less = html().includes("Show less");
  PAGES.tasks();
  await tick();
  out.expandedAfterReload = rows();
  // Running: only o/a has a row; the rest fold under each section's quiet.
  click("filter", "running");
  out.runningQuiet = [...rail().matchAll(/<span>(\d+) quiet<\/span>/g)].map((m) => +m[1]);
  out.runningAll = (rail().match(/All tasks<\/span>\s*<span class="gh-spacer"><\/span><span class="caption num gh-muted">(\d+)/) || [])[1];
  click("quiet", "gh:o");
  out.quietOpen = rail().includes('class="gh-quiet open"');
  click("filter", "all");
  click("shown", "Core");
  out.scoped = rows();
  out.scopedHead = html().includes('<span class="gh-muted">Linear /</span><span>Core</span>');
  out.selected = /class="gh-rail-row selected"\s*data-shown="Core"/.test(rail());
  click("filter", "running");
  out.scopedEmpty = html().includes("Nothing is running in Core.");
  click("filter", "all");
  click("shown", "o/a");
  out.scopedUnfolded = rows();
  // o/a's tasks leave the list: the scope falls back to every task.
  data = { ...data, tasks: all.filter((t) => t.repo !== "o/a") };
  PAGES.tasks();
  await tick();
  out.fellBack = /class="gh-rail-row selected" data-shown=""/.test(rail()) && rows() === 4;
  // No tasks and no failure: the empty card, and no rail.
  data = { machine: "hub", github: { status: "ok" }, tasks: [] };
  PAGES.tasks();
  await tick();
  out.emptyNoRail = !html().includes("gh-rail") && html().includes("No tasks yet");
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestListRailAndFold(unittest.TestCase):
    """tasks.js's list under node: five rows a group with Repositories'
    Show more fold, and its rail of sources that narrows the list."""

    def test_fold_and_rail(self):
        from tests.test_webapp_launch_sheets import MENU_JS, PAGES, STUB
        script = (STUB + MENU_JS.read_text(encoding="utf-8") + "\n"
                  + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                  + (PAGES / "tasks.js").read_text(encoding="utf-8") + "\n" + LIST_FLOW)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.js"
            path.write_text(script)
            done = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertTrue(out["withRail"])
        self.assertTrue(out["refreshInRail"])
        self.assertEqual(out["allCount"], "11")
        # Owners A to Z, then the other sources; rows by name within.
        self.assertEqual(out["railNames"], ["#o", "a", "b", "#x", "y", "#Linear", "Core", "Linear"])
        self.assertTrue(out["running"])
        self.assertEqual(out["folded"], 5 + 1 + 3)
        self.assertEqual(out["more"], "2")
        self.assertTrue(out["failedGroup"])
        self.assertEqual(out["expanded"], 11)
        self.assertTrue(out["less"])
        self.assertEqual(out["expandedAfterReload"], 11)
        self.assertEqual(out["runningQuiet"], [1, 1, 2])
        self.assertEqual(out["runningAll"], "1")
        self.assertTrue(out["quietOpen"])
        self.assertEqual(out["scoped"], 2)
        self.assertTrue(out["scopedHead"])
        self.assertTrue(out["selected"])
        self.assertTrue(out["scopedEmpty"])
        self.assertEqual(out["scopedUnfolded"], 7)
        self.assertTrue(out["fellBack"])
        self.assertTrue(out["emptyNoRail"])



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestFirstLoadError(unittest.TestCase):
    """pages/tasks.js under the launch-sheet DOM stub: when the first read
    fails, the error's header carries the Refresh button its "then
    refresh" points at, and pageError gets a retry to bind to it."""

    def test_the_error_header_has_refresh(self):
        from tests.test_webapp_launch_sheets import PAGES, STUB
        flow = r"""
let header = null, retry = null;
globalThis.pageHeader = (t, s, actions = "") => `<h1>${t}</h1>${actions}`;
globalThis.pageError = (h, status, data, r) => { header = h; retry = r; };
apiAnswers.push(["/v1/app/tasks", { status: 503, data: null }]);
(async () => {
  location.hash = "#/tasks";
  PAGES.tasks();
  await tick();
  process.stdout.write(JSON.stringify({ refresh: /id="page-refresh"/.test(header || ""), retry: typeof retry }));
  process.exit(0);
})();
"""
        script = (STUB + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                  + (PAGES / "tasks.js").read_text(encoding="utf-8") + "\n" + flow)
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"refresh": True, "retry": "function"})


# A failing Notion source beside a working Linear: Notion's offer card
# with its reason, the last-known caption over the stored rows, the
# rail's Notion section, and the detail's link out naming Notion.
NOTION_FLOW = r"""
const out = {};
globalThis.pageHeader = (title, subtitle, actions = "") => `<header>${actions}</header>`;
const page = { id: "notion:page:roadmap:8f1c-1", source: "notion", kind: "page",
  ref: "8f1c-1", title: "Plan Q3", project: "Roadmap", state: "in_progress",
  status: "In progress", url: "https://www.notion.so/Plan-Q3-8f1c1", sessions: [] };
let data = { machine: "hub", github: { status: "ok" },
  linear: { status: "ok", login: "Kai", error: null },
  notion: { status: "bad-config", login: null,
            error: "notion.databases[0].properties.status is required" },
  tasks: [page] };
apiAnswers.push(["/v1/app/tasks", { status: 200, get data() { return data; } }]);
const html = () => panel.innerHTML;
(async () => {
  location.hash = "#/tasks";
  PAGES.tasks();
  await tick();
  out.card = html().includes('<div class="card-title">Notion: bad config</div>');
  out.reason = html().includes("properties.status is required");
  out.noLinearCard = !html().includes("Linear: ");
  out.stale = html().includes("tk-stale");
  out.rail = html().includes('<span class="gh-muted">Notion /</span><span>Roadmap</span>');
  // Disabled is off, not broken: no card and no last-known caption.
  data = { ...data, notion: { status: "disabled", login: null, error: null } };
  PAGES.tasks();
  await tick();
  out.disabledQuiet = !html().includes("Notion: ") && !html().includes("tk-stale");
  location.hash = "#/tasks?task=" + encodeURIComponent(page.id);
  PAGES.tasks();
  await tick();
  out.openLabel = html().includes('title="Open in Notion"');
  out.noLaunch = !html().includes("New agent session");
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestNotionSource(unittest.TestCase):
    """tasks.js treats Notion as it treats Linear: a failure card when
    configured but failing, nothing when disabled, a Notion link out."""

    def test_notion_card_rail_and_link(self):
        from tests.test_webapp_launch_sheets import MENU_JS, PAGES, STUB
        script = (STUB + MENU_JS.read_text(encoding="utf-8") + "\n"
                  + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                  + (PAGES / "tasks.js").read_text(encoding="utf-8") + "\n" + NOTION_FLOW)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.js"
            path.write_text(script)
            done = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        for key in ("card", "reason", "noLinearCard", "stale", "rail",
                    "disabledQuiet", "openLabel", "noLaunch"):
            self.assertTrue(out[key], key)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestTaskGroupNames(unittest.TestCase):
    """tasks.js groups a row with no repo or project under its source's
    name, spelled as the Mac and iOS spell it."""

    def test_scopeless_rows_group_under_their_source_name(self):
        import re

        from tests.test_webapp_launch_sheets import PAGES
        src = (PAGES / "tasks.js").read_text(encoding="utf-8")
        helpers = "\n".join(re.search(r"^  const %s = .*?;$" % name, src, re.M | re.S).group(0)
                            for name in ("SOURCE_NAMES", "sourceName", "groupName"))
        rows = [{"source": "linear"}, {"source": "github", "repo": ""}, {"source": "jira"},
                {}, {"source": "linear", "repo": "", "project": "QUI"}, {"source": "github", "repo": "o/a"}]
        done = subprocess.run(["node", "-e", helpers + "\nprocess.stdout.write(JSON.stringify("
                               + json.dumps(rows) + ".map(groupName)));"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), ["Linear", "GitHub", "Jira", "Other", "QUI", "o/a"])

if __name__ == "__main__":
    unittest.main()
