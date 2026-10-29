"""The launch sheets' Model field, run under node with the DOM stubbed.

``tasks.js``'s New agent session panel, ``repositories.js``'s Open in
session panel and ``sessions.js``'s New session sheet share the same
contract: the field shows only while the picked harness can take a model
at launch, opens prefilled with its last-used model, re-prefills on a
harness (or machine) switch until the user types their own, and adds
``model`` to the POST body only when non-empty and supported. The stub
below is a DOM good enough to drive the real page scripts: ``#panel``
(and ``<dialog>``s for the PR and new-session sheets) re-parses its
``innerHTML`` into stub elements on each paint, and ``api``/``fetch``
answer from canned routes so the POST bodies can be inspected."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages"

# The shared prelude: DOM stub, the globals app.js/manage.js/format.js
# provide, and the fetch/api route tables each flow fills first.
STUB = r"""
"use strict";
const registry = new Map(); // ids parsed out of #panel's last innerHTML
const created = new Map();  // dialogs appended to body (the PR sheet)
let activeEl = null;
function unesc(s) {
  return String(s || "").replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}
function attrsOf(src) {
  const a = {};
  for (const m of src.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) a[m[1]] = m[2] || "";
  return a;
}
/* select/textarea own inner content; input/button/a are open-tag only
 * (an input has no close tag, so a lazy capture would swallow the rest). */
function parseEls(html) {
  const out = [];
  const re = /<(select|textarea)\b([^>]*)>([\s\S]*?)<\/\1>|<(input|button|a|div)\b([^>]*?)\/?>/g;
  let m;
  while ((m = re.exec(html))) out.push([attrsOf(m[2] || m[5] || ""), m[3] || "", m[1] || m[4]]);
  return out;
}
function makeEl(id, attrs, inner, tag) {
  const el = { id, attrs, dataset: {}, _html: inner || "", _kids: null,
    disabled: "disabled" in attrs, value: "", open: false, isConnected: true,
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    onclick: null, oninput: null, onchange: null, onkeydown: null,
    focus() { activeEl = el; }, blur() { if (activeEl === el) activeEl = null; },
    setSelectionRange() {}, addEventListener() {},
    setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return attrs[k] ?? null; },
    close() { el.open = false; }, showModal() { el.open = true; }, closest() { return null; } };
  Object.defineProperty(el, "innerHTML", { get: () => el._html,
    set(v) { el._html = v; el._kids = null; } });
  const kids = () => {
    if (!el._kids) {
      el._kids = { byId: new Map(), all: [] };
      for (const [a, inner2, t] of parseEls(el._html)) {
        const k = makeEl(a.id || "", a, inner2, t);
        el._kids.all.push(k);
        if (a.id) el._kids.byId.set(a.id, k);
      }
    }
    return el._kids;
  };
  el.querySelector = (sel) => {
    const m = sel.match(/^#([\w-]+)$/);
    if (m) return kids().byId.get(m[1]) || null;
    const d = sel.match(/\[([\w-]+)(?:="[^"]*")?\]/);
    return d ? kids().all.find((k) => d[1] in k.attrs) || null : null;
  };
  el.querySelectorAll = (sel) => {
    const d = sel.match(/\[([\w-]+)(?:="[^"]*")?\]/);
    return d ? kids().all.filter((k) => d[1] in k.attrs) : [];
  };
  for (const k of Object.keys(attrs)) if (k.startsWith("data-")) {
    el.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = unesc(attrs[k]);
  }
  if (attrs.value != null) el.value = unesc(attrs.value);
  if (tag === "textarea") el.value = unesc(el._html);
  if (tag === "select") {
    const s = el._html.match(/<option[^>]*value="([^"]*)"[^>]*selected/)
      || el._html.match(/<option[^>]*selected[^>]*value="([^"]*)"/)
      || el._html.match(/<option[^>]*value="([^"]*)"/);
    if (s) el.value = unesc(s[1]);
  }
  return el;
}
function rebuild(html) {
  registry.clear();
  for (const [a, inner, t] of parseEls(html)) if (a.id) registry.set(a.id, makeEl(a.id, a, inner, t));
}
function queryAllIn(html, sel) {
  const d = sel.match(/\[([\w-]+)(?:="[^"]*")?\]/);
  const c = sel.match(/\.([\w-]+)/);
  const out = [];
  for (const [a, inner, t] of parseEls(html)) {
    if (d && d[1] in a) out.push(makeEl(a.id || "", a, inner, t));
    else if (!d && c && (a.class || "").split(/\s+/).includes(c[1])) out.push(makeEl(a.id || "", a, inner, t));
  }
  return out;
}
const panel = { _html: "",
  get innerHTML() { return this._html; },
  set innerHTML(v) { this._html = v; rebuild(v); },
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
  querySelectorAll(sel) { return queryAllIn(this._html, sel); } };
globalThis.document = {
  getElementById: (id) => (id === "panel" ? panel : registry.get(id) || created.get(id)
    || [...created.values()].map((e) => e.querySelector("#" + id)).find(Boolean) || null),
  querySelectorAll: (sel) => queryAllIn(panel.innerHTML, sel),
  createElement: (tag) => makeEl("", { __tag: tag }, "", tag),
  addEventListener() {}, hidden: false,
  body: { appendChild(el) { if (el.id) created.set(el.id, el); } } };
Object.defineProperty(document, "activeElement", { get: () => activeEl });
globalThis.window = { addEventListener() {} };
globalThis.location = { hash: "" };
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
const PAGES = {};
globalThis.registerPage = (id, r) => { PAGES[id] = r; };
globalThis.esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
globalThis.icon = () => "";
globalThis.pageHeader = () => "";
globalThis.renderGate = () => {};
globalThis.pageError = () => {};
globalThis.bindRefresh = () => {};
globalThis.routeGen = 0;
globalThis.Fmt = { ago: () => "1h", logo: () => "", harnessName: (n) => n || "",
  tokens: () => "0", dollars: () => "$0" };
globalThis.authHeaders = (extra = {}) => ({ ...extra });
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.setTimeout = (fn) => { fn(); return 0; };
const fetchCalls = [];
const fetchRoutes = []; // [url prefix, {status, body}]
globalThis.fetch = async (path, opts) => {
  fetchCalls.push({ path, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const r = fetchRoutes.find(([p]) => path.startsWith(p));
  const a = r ? r[1] : { status: 404, body: { error: { message: "unstubbed " + path } } };
  return { status: a.status, json: async () => a.body };
};
const apiAnswers = []; // [url prefix, {status, data}]
globalThis.api = async (path) => {
  for (const [m, a] of apiAnswers) if (path.startsWith(m)) return a;
  return { status: 404, data: { error: { message: "unstubbed " + path } } };
};
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
"""

# The three harnesses every flow offers: claude and codex take a model
# pick (with ledger last-used), gemini does not.
HARNESSES = """
const harnesses = [
  { name: "claude", display_name: "Claude Code", tracked: true,
    launch_model: true, launch_effort: true, last_model: "opus" },
  { name: "gemini", display_name: "Gemini", tracked: true,
    launch_model: false, launch_effort: false, last_model: "" },
  { name: "codex", display_name: "Codex", tracked: true,
    launch_model: true, launch_effort: false, last_model: "gpt-5-codex" }];
"""

# tasks.js: detail of a github issue, open "New agent session", walk the
# pick, post, and watch the body and the sheet.
TASKS_FLOW = HARNESSES + r"""
const out = {};
const el = (id) => document.getElementById(id);
apiAnswers.push(
  ["/v1/app/tasks?harnesses=1", { status: 200, data: { machine: "hub", harnesses } }],
  ["/v1/app/tasks", { status: 200, data: { machine: "hub", github: { status: "ok" },
    tasks: [{ id: "github:issue:o/r:5", source: "github", kind: "issue", ref: "5",
      title: "Fix the bug", repo: "o/r", state: "open", status: "Open",
      sessions: [], launches: [], session_rows: [], launch_rows: [] }] } }],
  ["/v1/launches/", { status: 200, data: { state: "untracked" } }]);
fetchRoutes.push(["/v1/issues/resolve",
  { status: 409, body: { error: { code: "unsupported_launch_option",
    message: "codex cannot take a model pick at launch" } } }]);
(async () => {
  location.hash = "#/tasks?task=github:issue:o/r:5";
  PAGES.tasks();
  await tick();
  el("tk-new").onclick();
  await tick();
  out.shown = !!el("tk-model");
  out.prefill = el("tk-model").value;
  el("tk-harness").value = "gemini"; el("tk-harness").onchange();
  out.hides = !el("tk-model");
  el("tk-harness").value = "claude"; el("tk-harness").onchange();
  out.reprefill = el("tk-model").value;
  el("tk-model").value = "sonnet"; el("tk-model").oninput();
  el("tk-harness").value = "codex"; el("tk-harness").onchange();
  out.dirtyKept = el("tk-model").value;
  el("tk-new-start").onclick();
  await tick();
  const post = fetchCalls.find((c) => c.path === "/v1/issues/resolve");
  out.postModel = post && post.body.model;
  out.postHarness = post && post.body.harness;
  out.errorShown = panel.innerHTML.includes("cannot take a model pick");
  // A cleared field stays cleared across a switch and sends no key.
  el("tk-model").value = ""; el("tk-model").oninput();
  el("tk-harness").value = "claude"; el("tk-harness").onchange();
  out.clearedKept = el("tk-model").value;
  el("tk-new-start").onclick();
  await tick();
  const post2 = fetchCalls.filter((c) => c.path === "/v1/issues/resolve").pop();
  out.emptyHasNoModel = !("model" in (post2 && post2.body || {}));
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

# repositories.js: the PR sheet's Open in session panel, same walk, on
# /v1/sessions (after the worktree post).
REPOS_FLOW = HARNESSES + r"""
const out = {};
apiAnswers.push(
  ["/v1/app/repositories?harnesses=1", { status: 200, data: { machine: "hub", harnesses } }],
  ["/v1/app/repositories?repo=o%2Fr&pr=7", { status: 200, data: { machine: "hub",
    github: { status: "ok" }, repo: "o/r", status: "ok", fetched_at: 0,
    pr: { number: 7, title: "Fix the thing", repo: "o/r", state: "open", draft: false,
      branch: "feat", base: "main", author: "me", url: "https://github.com/o/r/pull/7",
      mergeable: "mergeable", merge_state: "clean", checks: "passing",
      review_decision: "approved", labels: [], additions: 3, deletions: 1,
      changed_files: 2, updated_at: 0, body: "" } } }],
  ["/v1/app/repositories?repo=o%2Fr", { status: 200, data: { machine: "hub",
    github: { status: "ok" }, repo: "o/r", surfaces: {}, limits: {}, fetched_at: 0 } }],
  ["/v1/launches/", { status: 200, data: { state: "untracked" } }]);
fetchRoutes.push(
  ["/v1/app/repositories/worktree", { status: 200,
    body: { repo: "o/r", number: 7, path: "/tmp/wt", head: "abc", created: true, machine: "hub" } }],
  ["/v1/sessions", { status: 202, body: { launch_id: "L1", state: "pending" } }]);
(async () => {
  location.hash = "#/repositories?repo=o/r&pr=7";
  PAGES.repositories();
  await tick();
  const d = created.get("gh-pr-sheet");
  out.sheetOpened = !!d;
  const q = (s) => d.querySelector(s);
  q("#gh-session-open").onclick();
  await tick();
  out.shown = !!q("#gh-session-model");
  out.prefill = q("#gh-session-model").value;
  q("#gh-session-harness").value = "gemini"; q("#gh-session-harness").onchange();
  out.hides = !q("#gh-session-model");
  q("#gh-session-harness").value = "claude"; q("#gh-session-harness").onchange();
  out.reprefill = q("#gh-session-model").value;
  q("#gh-session-model").value = "sonnet"; q("#gh-session-model").oninput();
  q("#gh-session-harness").value = "codex"; q("#gh-session-harness").onchange();
  out.dirtyKept = q("#gh-session-model").value;
  q("#gh-session-start").onclick();
  await tick();
  const sess = fetchCalls.find((c) => c.path === "/v1/sessions");
  out.postModel = sess && sess.body.model;
  out.postCwd = sess && sess.body.cwd;
  // A fresh sheet state prefills again; clearing it sends no key.
  q("#gh-session-cancel").onclick();
  await tick();
  d.querySelector("#gh-session-open").onclick();
  await tick();
  q("#gh-session-harness").value = "codex"; q("#gh-session-harness").onchange();
  out.freshPrefill = q("#gh-session-model").value;
  q("#gh-session-model").value = ""; q("#gh-session-model").oninput();
  q("#gh-session-start").onclick();
  await tick();
  const sess2 = fetchCalls.filter((c) => c.path === "/v1/sessions").pop();
  out.emptyHasNoModel = !("model" in (sess2 && sess2.body || {}));
  // A launch the harness can't honor shows the hub's refusal.
  q("#gh-session-cancel").onclick();
  await tick();
  fetchRoutes.length = 0;
  fetchRoutes.push(
    ["/v1/app/repositories/worktree", { status: 200, body: { path: "/tmp/wt" } }],
    ["/v1/sessions", { status: 409, body: { error: { code: "unsupported_launch_option",
      message: "claude cannot take a model pick at launch" } } }]);
  d.querySelector("#gh-session-open").onclick();
  await tick();
  q("#gh-session-start").onclick();
  await tick();
  out.errorShown = d._html.includes("cannot take a model pick");
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


# sessions.js's New session sheet: machine + harness chips over
# /v1/machines rows (``model_at_launch``/``last_models`` live there, not
# on a harnesses list), posting /v1/sessions.
SESSIONS_FLOW = r"""
globalThis.apiPost = async (path, body) => {
  fetchCalls.push({ path, body });
  const r = fetchRoutes.find(([p]) => path.startsWith(p));
  const a = r ? r[1] : { status: 404, body: { error: { message: "unstubbed " + path } } };
  return { status: a.status, data: a.body };
};
apiAnswers.push(
  ["/v1/machines", { status: 200, data: { machines: [
    { id: "m1", name: "hub", online: true, launchable: ["claude", "gemini", "codex"],
      model_at_launch: ["claude", "codex"], effort_at_launch: ["claude"],
      last_models: { claude: "opus", codex: "gpt-5-codex" } },
    { id: "m2", name: "sat", online: true, launchable: ["claude"],
      model_at_launch: ["claude"], effort_at_launch: [],
      last_models: { claude: "opus-m2" } }] } }],
  ["/v1/launches/", { status: 200, data: { state: "untracked" } }]);
fetchRoutes.push(
  ["/v1/sessions", { status: 202, body: { launch_id: "L1", state: "pending" } }]);
sessionsPage.data = { sessions: [
  { cwd: "/tmp/wt", harness: "claude", machine_id: "m1" },
  { cwd: "/tmp/gem", harness: "gemini", machine_id: "m1" }] };
const out = {};
(async () => {
  await openNewSession();
  await tick();
  const d = created.get("new-session-sheet");
  const q = (s) => d.querySelector(s);
  const chip = (h) => d.querySelectorAll("[data-harness]").find((b) => b.dataset.harness === h);
  out.sheetOpened = !!(d && d.open);
  out.shown = !!q("#ns-model");
  out.prefill = q("#ns-model") && q("#ns-model").value;
  q("#ns-folder").value = "/tmp/wt"; q("#ns-folder").oninput(); await tick();
  q("#ns-prompt").value = "say ok"; q("#ns-prompt").oninput();
  chip("gemini").onclick(); await tick();
  out.hides = !q("#ns-model");
  chip("claude").onclick(); await tick();
  out.reprefill = q("#ns-model").value;
  q("#ns-model").value = "sonnet"; q("#ns-model").oninput();
  chip("codex").onclick(); await tick();
  out.dirtyKept = q("#ns-model").value;
  q("#ns-start").onclick(); await tick();
  const post = fetchCalls.find((c) => c.path === "/v1/sessions");
  out.postModel = post && post.body.model;
  out.postMachine = post && post.body.machine;
  q("#ns-model").value = ""; q("#ns-model").oninput();
  chip("claude").onclick(); await tick();
  out.clearedKept = q("#ns-model").value;
  q("#ns-start").onclick(); await tick();
  const post2 = fetchCalls.filter((c) => c.path === "/v1/sessions").pop();
  out.emptyHasNoModel = !("model" in (post2 && post2.body || {}));
  // A fresh sheet is clean again: a machine switch re-prefills from
  // that machine's own ledger.
  await openNewSession();
  await tick();
  q("#ns-folder").value = "/tmp/wt"; q("#ns-folder").oninput(); await tick();
  q("#ns-prompt").value = "say ok"; q("#ns-prompt").oninput();
  d.querySelectorAll("[data-machine]").find((b) => b.dataset.machine === "m2").onclick();
  await tick();
  out.machineRefill = q("#ns-model").value;
  // An old satellite's row carries no model_at_launch: the field hides.
  apiAnswers.length = 0;
  apiAnswers.push(["/v1/machines", { status: 200, data: { machines: [
    { id: "m1", name: "hub", online: true, launchable: ["claude"] }] } }]);
  await openNewSession();
  await tick();
  out.hiddenOldSat = !q("#ns-model");
  // A typed folder that flips the model row must not rebuild the sheet:
  // the folder input keeps its node (and so its focus and caret).
  apiAnswers.length = 0;
  apiAnswers.push(["/v1/machines", { status: 200, data: { machines: [
    { id: "m1", name: "hub", online: true, launchable: ["claude", "gemini", "codex"],
      model_at_launch: ["claude", "codex"], effort_at_launch: ["claude"],
      last_models: { claude: "opus", codex: "gpt-5-codex" } }] } }]);
  await openNewSession();
  await tick();
  const folderEl = q("#ns-folder");
  const slot = q("#ns-model-slot");
  folderEl.value = "/tmp/gem"; folderEl.oninput(); await tick();
  out.flipHidesRow = slot.querySelector("#ns-model") == null;
  out.folderNodeKeptOff = q("#ns-folder") === folderEl;
  folderEl.value = "/tmp/wt"; folderEl.oninput(); await tick();
  const shown2 = slot.querySelector("#ns-model");
  out.flipShowsRow = shown2 && shown2.value;
  out.folderNodeKeptOn = q("#ns-folder") === folderEl;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLaunchModelField(unittest.TestCase):
    """All three sheets drive the same contract; each flow returns the
    observations the assertions below check."""

    def run_node(self, script: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def run_flow(self, page_file: str, flow: str) -> dict:
        return self.run_node(STUB
                             + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                             + (PAGES / page_file).read_text(encoding="utf-8") + "\n"
                             + flow)

    def assert_walk(self, out: dict):
        """The shared contract, whichever sheet drove it."""
        self.assertTrue(out["shown"], "Model field should show for a launch_model harness")
        self.assertEqual(out["prefill"], "opus")
        self.assertTrue(out["hides"], "Model field should hide without launch_model")
        self.assertEqual(out["reprefill"], "opus")
        self.assertEqual(out["dirtyKept"], "sonnet")
        self.assertEqual(out["postModel"], "sonnet")
        self.assertTrue(out["emptyHasNoModel"], "an empty field must send no model key")
        self.assertTrue(out["errorShown"], "the refusal should show in the sheet")

    def test_tasks_new_agent_session(self):
        out = self.run_flow("tasks.js", TASKS_FLOW)
        self.assert_walk(out)
        self.assertEqual(out["postHarness"], "codex")
        self.assertEqual(out["clearedKept"], "",
                         "a cleared pick stays cleared across a harness switch")

    def test_repositories_open_in_session(self):
        out = self.run_flow("repositories.js", REPOS_FLOW)
        self.assert_walk(out)
        self.assertTrue(out["sheetOpened"])
        self.assertEqual(out["postCwd"], "/tmp/wt")
        self.assertEqual(out["freshPrefill"], "gpt-5-codex")

    def test_sessions_new_session(self):
        out = self.run_node(STUB
                            + (PAGES.parent / "sessions.js").read_text(encoding="utf-8") + "\n"
                            + SESSIONS_FLOW)
        self.assertTrue(out["sheetOpened"])
        self.assertTrue(out["shown"], "Model field should show for a model_at_launch harness")
        self.assertEqual(out["prefill"], "opus")
        self.assertTrue(out["hides"], "Model field should hide off model_at_launch")
        self.assertEqual(out["reprefill"], "opus")
        self.assertEqual(out["dirtyKept"], "sonnet")
        self.assertEqual(out["postModel"], "sonnet")
        self.assertEqual(out["postMachine"], "m1")
        self.assertEqual(out["clearedKept"], "",
                         "a cleared pick stays cleared across a harness switch")
        self.assertTrue(out["emptyHasNoModel"], "an empty field must send no model key")
        self.assertEqual(out["machineRefill"], "opus-m2",
                         "a clean field re-prefills from the new machine's ledger")
        self.assertTrue(out["hiddenOldSat"],
                        "a machine row without model_at_launch shows no field")
        self.assertTrue(out["flipHidesRow"],
                        "a folder re-defaulting to a non-model harness drops the row")
        self.assertTrue(out["folderNodeKeptOff"],
                        "the folder input is rebuilt on a model-row flip — focus is lost")
        self.assertEqual(out["flipShowsRow"], "opus",
                         "a folder re-defaulting back re-fills the row")
        self.assertTrue(out["folderNodeKeptOn"],
                        "the folder input is rebuilt when the row returns — focus is lost")


if __name__ == "__main__":
    unittest.main()
