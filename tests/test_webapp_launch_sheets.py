"""The launch sheets' Model menu, run under node with the DOM stubbed.

``tasks.js``'s New agent session panel, ``repositories.js``'s Open in
session panel and ``sessions.js``'s New session sheet share
``model-menu.js`` and so one contract: the menu shows only while the
picked harness can take a model at launch, lists Harness default, the
harness's recent models and Other…, opens on the newest recent model,
re-defaults on a harness (or machine) switch until the user picks, and
adds ``model`` to the POST body only when one is picked or typed. The stub
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

from tests.test_webapp import app_js_function

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
  querySelector(sel) {
    const m = sel.match(/^#([\w-]+)$/);
    return m ? registry.get(m[1]) || null : this.querySelectorAll(sel)[0] || null;
  },
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
globalThis.safeHref = (u) => (/^(https?:\/\/|mailto:)/i.test(String(u || "").trim()) ? esc(String(u).trim()) : "");
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
// The Model menu as a user sees it: option labels, the picked one, and
// picking one by its label.
const optionsOf = (sel) => [...sel._html.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)]
  .map((m) => [unesc(m[1]), unesc(m[2])]);
const labels = (sel) => sel ? optionsOf(sel).map((o) => o[1]) : null;
const picked = (sel) => { const o = sel && optionsOf(sel).find((x) => x[0] === sel.value); return o ? o[1] : null; };
const choose = (sel, label) => { sel.value = optionsOf(sel).find((x) => x[1] === label)[0]; sel.onchange(); };
"""

STUB += app_js_function("currentRoute")

MENU_JS = (Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/model-menu.js")

# Writes go through app.js's own send(), over the stubbed fetch.
STUB += app_js_function("apiSend") + "const apiPost = (path, body) => apiSend(\"POST\", path, body);\n"

# The four harnesses every flow offers: claude and codex take a model
# pick and have recent models, gemini takes none, and pi is what an old
# hub sends (``last_model`` only, no ``recent_models``).
HARNESSES = """
const harnesses = [
  { name: "claude", display_name: "Claude Code", tracked: true,
    launch_model: true, launch_effort: true, last_model: "opus", recent_models: ["opus", "sonnet"] },
  { name: "gemini", display_name: "Gemini", tracked: true,
    launch_model: false, launch_effort: false, last_model: "", recent_models: [] },
  { name: "codex", display_name: "Codex", tracked: true,
    launch_model: true, launch_effort: false, last_model: "gpt-5-codex", recent_models: ["gpt-5-codex", "gpt-5"] },
  { name: "pi", display_name: "Pi", tracked: true,
    launch_model: true, launch_effort: false, last_model: "pi-model" }];
"""

# tasks.js: detail of a github issue, open "New agent session", walk the
# menu, post, and watch the body and the sheet.
TASKS_FLOW = HARNESSES + r"""
const out = {};
const el = (id) => document.getElementById(id);
const harness = (h) => { el("tk-harness").value = h; el("tk-harness").onchange(); };
const start = async () => { el("tk-new-start").onclick(); await tick();
  return fetchCalls.filter((c) => c.path === "/v1/issues/resolve").pop(); };
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
  out.options = labels(el("tk-model"));
  out.prefill = picked(el("tk-model"));
  out.noBox = !el("tk-model-other");
  harness("gemini");
  out.hides = !el("tk-model");
  harness("claude");
  out.reprefill = picked(el("tk-model"));
  choose(el("tk-model"), "sonnet");
  out.recentKept = (harness("claude"), picked(el("tk-model")));
  harness("codex");
  out.foreignRedefault = picked(el("tk-model"));
  harness("pi");
  out.oldHubOptions = labels(el("tk-model"));
  harness("codex");
  choose(el("tk-model"), "Other…");
  out.boxFocused = !!activeEl && activeEl.id === "tk-model-other";
  el("tk-model-other").value = "  my-model "; el("tk-model-other").oninput();
  harness("claude"); harness("codex");
  out.dirtyPick = picked(el("tk-model"));
  out.dirtyKept = el("tk-model-other") && el("tk-model-other").value;
  const post = await start();
  out.postModel = post && post.body.model;
  out.postHarness = post && post.body.harness;
  out.errorShown = panel.innerHTML.includes("cannot take a model pick");
  el("tk-model-other").value = "   "; el("tk-model-other").oninput();
  const blank = await start();
  out.blankOtherNoModel = !("model" in (blank && blank.body || {}));
  // An explicit Harness default stays across a switch and sends no key.
  choose(el("tk-model"), "Harness default");
  out.boxGone = !el("tk-model-other");
  out.selectFocused = !!activeEl && activeEl.id === "tk-model";
  harness("claude");
  out.clearedKept = picked(el("tk-model"));
  const post2 = await start();
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
  const harness = (h) => { q("#gh-session-harness").value = h; q("#gh-session-harness").onchange(); };
  q("#gh-session-open").onclick();
  await tick();
  out.shown = !!q("#gh-session-model");
  out.options = labels(q("#gh-session-model"));
  out.prefill = picked(q("#gh-session-model"));
  out.noBox = !q("#gh-session-model-other");
  harness("gemini");
  out.hides = !q("#gh-session-model");
  harness("claude");
  out.reprefill = picked(q("#gh-session-model"));
  choose(q("#gh-session-model"), "sonnet");
  out.recentKept = (harness("claude"), picked(q("#gh-session-model")));
  harness("codex");
  out.foreignRedefault = picked(q("#gh-session-model"));
  harness("pi");
  out.oldHubOptions = labels(q("#gh-session-model"));
  harness("codex");
  choose(q("#gh-session-model"), "Other…");
  out.boxFocused = !!activeEl && activeEl.id === "gh-session-model-other";
  q("#gh-session-model-other").value = "  my-model "; q("#gh-session-model-other").oninput();
  harness("claude"); harness("codex");
  out.dirtyPick = picked(q("#gh-session-model"));
  out.dirtyKept = q("#gh-session-model-other") && q("#gh-session-model-other").value;
  q("#gh-session-start").onclick();
  await tick();
  const sess = fetchCalls.find((c) => c.path === "/v1/sessions");
  out.postModel = sess && sess.body.model;
  out.postCwd = sess && sess.body.cwd;
  // A fresh panel defaults again; an explicit Harness default survives
  // a switch and sends no key.
  q("#gh-session-cancel").onclick();
  await tick();
  d.querySelector("#gh-session-open").onclick();
  await tick();
  harness("codex");
  out.freshPrefill = picked(q("#gh-session-model"));
  choose(q("#gh-session-model"), "Harness default");
  out.boxGone = !q("#gh-session-model-other");
  harness("claude");
  out.clearedKept = picked(q("#gh-session-model"));
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


# repositories.js: an issue row's sheet (GET /v1/issues/<o>/<r>/<n>), its
# body, comments and comment box (POST /v1/issues/comment), then Resolve
# with agent's panel: the same walk, posting /v1/issues/resolve (the
# context posted as a comment first), and the chat opening once the
# launch lands.
ISSUE_SETUP = HARNESSES + r"""
const out = {};
const apiCalls = [];
const api0 = globalThis.api;
globalThis.api = async (path) => { apiCalls.push(path); return api0(path); };
const reads = () => apiCalls.filter((p) => p.startsWith("/v1/issues/o/r/5")).length;
const posts = (path) => fetchCalls.filter((c) => c.path === path);
const lastQ = {};
const qsa = panel.querySelectorAll.bind(panel);
panel.querySelectorAll = (sel) => (lastQ[sel] = qsa(sel));
const click = (attr, value) => lastQ[`[data-${attr}]`].find((e) => e.attrs[`data-${attr}`] === value).onclick();
const ISSUE = { number: 5, title: "Fix the bug", state: "open", author: "amy", labels: ["bug"],
  assignees: ["bob"], url: "https://github.com/o/r/issues/5", body: "It **breaks** on save.",
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-02T00:00:00Z",
  comments: [{ author: "cat", body: "Same here", created_at: "2026-10-02T00:00:00Z",
    url: "https://github.com/o/r/issues/5#issuecomment-1" }],
  branch: "quiver/issue-5", worktree: "/w/o-r-5", suggested_prompt: "Resolve issue #5 in o/r." };
apiAnswers.push(
  ["/v1/app/repositories?harnesses=1", { status: 200, data: { machine: "hub", harnesses } }],
  ["/v1/issues/o/r/5", { status: 200, data: { machine: "hub", github: { status: "ok", login: "octocat" },
    repo: "o/r", status: "ok", issue: ISSUE, fetched_at: 0 } }],
  ["/v1/app/repositories", { status: 200, data: { machine: "hub", github: { status: "ok" },
    repos: [{ name: "o/r" }], boards: { "o/r": { surfaces: {
      pull_requests: { status: "ok", pull_requests: [] },
      issues: { status: "ok", issues: [{ number: 5, title: "Fix the bug", state: "open", author: "amy",
        labels: [], assignees: [], comments: 1, updated_at: null, url: ISSUE.url }] },
      runs: { status: "ok", runs: [] } } } } } }],
  ["/v1/launches/", { status: 200, data: { state: "started", key: "k9" } }]);
fetchRoutes.push(["/v1/issues/resolve", { status: 409, body: { error: { code: "unsupported_launch_option",
  message: "codex cannot take a model pick at launch" } } }]);
"""

ISSUE_FLOW = ISSUE_SETUP + r"""
(async () => {
  location.hash = "#/repositories";
  PAGES.repositories();
  await tick();
  click("tab", "issues");
  out.rowIsButton = /<button type="button" class="gh-row" data-issue="5" data-repo="o\/r">/.test(panel.innerHTML);
  click("issue", "5");
  await tick();
  const d = created.get("gh-issue-sheet");
  out.sheetOpened = !!d && d.open;
  const q = (s) => d.querySelector(s);
  out.body = d._html.includes("It <strong>breaks</strong> on save.");
  out.comment = d._html.includes("Same here") && d._html.includes("Comments · 1");
  out.github = d._html.includes('href="https://github.com/o/r/issues/5"');
  // The comment box: off while blank, Cmd+Return posts, a refusal keeps
  // the draft, a post clears it and reads the issue again.
  out.composer = !!q("#gh-issue-comment") && q("#gh-issue-comment-post").disabled;
  out.composerCaption = d._html.includes("Posts to GitHub as octocat, through gh on hub.");
  fetchRoutes.unshift(["/v1/issues/comment", { status: 502, body: { error: { code: "comment_failed",
    message: "gh: API rate limit exceeded" } } }]);
  q("#gh-issue-comment").value = "  Looks fixed on main  "; q("#gh-issue-comment").oninput();
  out.postOn = !q("#gh-issue-comment-post").disabled;
  q("#gh-issue-comment").onkeydown({ key: "Enter", preventDefault() {} });
  await tick();
  out.plainEnterNoPost = posts("/v1/issues/comment").length === 0;
  q("#gh-issue-comment").onkeydown({ key: "Enter", metaKey: true, preventDefault() {} });
  await tick();
  out.commentPost = posts("/v1/issues/comment").pop().body;
  out.commentError = d._html.includes("gh: API rate limit exceeded");
  out.draftKept = q("#gh-issue-comment").value;
  fetchRoutes.shift();
  fetchRoutes.unshift(["/v1/issues/comment", { status: 200, body: { ok: true } }]);
  const before = reads();
  q("#gh-issue-comment-post").onclick();
  await tick();
  out.reread = reads() === before + 1;
  out.draftCleared = q("#gh-issue-comment").value === "" && !d._html.includes("rate limit");
  const harness = (h) => { q("#gh-resolve-harness").value = h; q("#gh-resolve-harness").onchange(); };
  q("#gh-resolve-open").onclick();
  await tick();
  out.composerHidden = !q("#gh-issue-comment");
  out.boxTicked = "checked" in q("#gh-resolve-post").attrs
    && d._html.includes("Also post as a comment on #5");
  out.promptPrefill = q("#gh-resolve-prompt").value;
  out.shown = !!q("#gh-resolve-model");
  out.options = labels(q("#gh-resolve-model"));
  out.prefill = picked(q("#gh-resolve-model"));
  out.noBox = !q("#gh-resolve-model-other");
  harness("gemini");
  out.hides = !q("#gh-resolve-model");
  harness("claude");
  out.reprefill = picked(q("#gh-resolve-model"));
  choose(q("#gh-resolve-model"), "sonnet");
  out.recentKept = (harness("claude"), picked(q("#gh-resolve-model")));
  harness("codex");
  out.foreignRedefault = picked(q("#gh-resolve-model"));
  harness("pi");
  out.oldHubOptions = labels(q("#gh-resolve-model"));
  harness("codex");
  choose(q("#gh-resolve-model"), "Other…");
  out.boxFocused = !!activeEl && activeEl.id === "gh-resolve-model-other";
  q("#gh-resolve-model-other").value = "  my-model "; q("#gh-resolve-model-other").oninput();
  harness("claude"); harness("codex");
  out.dirtyPick = picked(q("#gh-resolve-model"));
  out.dirtyKept = q("#gh-resolve-model-other") && q("#gh-resolve-model-other").value;
  q("#gh-resolve-context").value = " Use the new API. "; q("#gh-resolve-context").oninput();
  q("#gh-resolve-start").onclick();
  await tick();
  const post = fetchCalls.filter((c) => c.path === "/v1/issues/resolve").pop();
  out.post = post && post.body;
  out.postModel = post && post.body.model;
  out.errorShown = d._html.includes("cannot take a model pick");
  // The ticked context posted first, once: a retried Start doesn't post it again.
  out.contextComment = posts("/v1/issues/comment").pop().body;
  out.commentFirst = fetchCalls.indexOf(posts("/v1/issues/comment").pop()) < fetchCalls.indexOf(post);
  const comments = posts("/v1/issues/comment").length;
  q("#gh-resolve-start").onclick();
  await tick();
  out.noRepost = posts("/v1/issues/comment").length === comments
    && posts("/v1/issues/resolve").length === 2;
  // A fresh panel defaults again; an explicit Harness default survives
  // a switch and sends no key; a cleared prompt is left to the hub.
  q("#gh-resolve-cancel").onclick();
  await tick();
  q("#gh-resolve-open").onclick();
  await tick();
  harness("codex");
  out.freshPrefill = picked(q("#gh-resolve-model"));
  choose(q("#gh-resolve-model"), "Harness default");
  out.boxGone = !q("#gh-resolve-model-other");
  harness("claude");
  out.clearedKept = picked(q("#gh-resolve-model"));
  q("#gh-resolve-prompt").value = "  "; q("#gh-resolve-prompt").oninput();
  fetchRoutes.length = 0;
  fetchRoutes.push(["/v1/issues/resolve", { status: 202, body: { launch_id: "L1", state: "pending" } }]);
  q("#gh-resolve-start").onclick();
  await tick();
  const post2 = fetchCalls.filter((c) => c.path === "/v1/issues/resolve").pop();
  out.emptyHasNoModel = !("model" in (post2 && post2.body || {}));
  out.blankPrompt = post2 && post2.body.prompt;
  out.noContext = !("context" in (post2 && post2.body || {}));
  out.chat = location.hash;
  out.closed = !d.open;
  // Closed since the panel opened: Fetch again turns Start off and a
  // press posts nothing.
  location.hash = "#/repositories";
  PAGES.repositories();
  await tick();
  click("tab", "issues");
  click("issue", "5");
  await tick();
  q("#gh-resolve-open").onclick();
  await tick();
  apiAnswers.unshift(["/v1/issues/o/r/5", { status: 200, data: { machine: "hub", github: { status: "ok" },
    repo: "o/r", status: "ok", issue: { ...ISSUE, state: "closed" }, fetched_at: 0 } }]);
  q("[data-issue-refresh]").onclick();
  await tick();
  out.closedStartOff = q("#gh-resolve-start").disabled && d._html.includes("This issue is closed.");
  const closedBefore = fetchCalls.filter((c) => c.path === "/v1/issues/resolve").length;
  q("#gh-resolve-start").onclick();
  await tick();
  out.closedNoPost = fetchCalls.filter((c) => c.path === "/v1/issues/resolve").length === closedBefore;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

# The Resolve panel's "Also post as a comment": a refused comment is a
# warning and the launch still goes ahead; unticked, nothing posts.
ISSUE_COMMENT_FLOW = ISSUE_SETUP + r"""
(async () => {
  const openPanel = async () => {
    location.hash = "#/repositories";
    PAGES.repositories();
    await tick();
    click("tab", "issues");
    click("issue", "5");
    await tick();
    const d = created.get("gh-issue-sheet");
    d.querySelector("#gh-resolve-open").onclick();
    await tick();
    return d;
  };
  fetchRoutes.length = 0;
  fetchRoutes.push(["/v1/issues/comment", { status: 502, body: { error: { code: "comment_failed",
    message: "gh: API rate limit exceeded" } } }],
    ["/v1/issues/resolve", { status: 202, body: { launch_id: "L1", state: "pending" } }]);
  let d = await openPanel();
  d.querySelector("#gh-resolve-context").value = "  Tried a fix  ";
  d.querySelector("#gh-resolve-context").oninput();
  d.querySelector("#gh-resolve-start").onclick();
  await tick();
  out.failedComment = posts("/v1/issues/comment").pop().body;
  out.launchedAnyway = posts("/v1/issues/resolve").pop().body.context;
  out.warning = d._html.includes("Couldn&#39;t post your context to GitHub: gh: API rate limit exceeded. The agent still gets it in the prompt.");
  out.chat = location.hash;
  d = await openPanel();
  d.querySelector("#gh-resolve-context").value = "Not for GitHub";
  d.querySelector("#gh-resolve-context").oninput();
  d.querySelector("#gh-resolve-post").checked = false;
  d.querySelector("#gh-resolve-post").onchange();
  const comments = posts("/v1/issues/comment").length;
  d.querySelector("#gh-resolve-start").onclick();
  await tick();
  out.untickedNoComment = posts("/v1/issues/comment").length === comments;
  out.untickedContext = posts("/v1/issues/resolve").pop().body.context;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


# sessions.js's New session sheet: machine + harness chips over
# /v1/machines rows (``model_at_launch``/``recent_models`` live there,
# not on a harnesses list), posting /v1/sessions. The model row is drawn
# into ``#ns-model-slot`` on its own, so it is read through the slot.
SESSIONS_FLOW = r"""
globalThis.apiPost = async (path, body) => {
  fetchCalls.push({ path, body });
  const r = fetchRoutes.find(([p]) => path.startsWith(p));
  const a = r ? r[1] : { status: 404, body: { error: { message: "unstubbed " + path } } };
  return { status: a.status, data: a.body };
};
const HUB = { id: "m1", name: "hub", online: true, launchable: ["claude", "gemini", "codex"],
  model_at_launch: ["claude", "codex"], effort_at_launch: ["claude"],
  last_models: { claude: "opus", codex: "gpt-5-codex" },
  recent_models: { claude: ["opus", "sonnet"], codex: ["gpt-5-codex", "gpt-5"] } };
apiAnswers.push(
  ["/v1/machines", { status: 200, data: { machines: [HUB,
    // An old satellite's hub: last_models only.
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
  const slot = () => q("#ns-model-slot");
  const menu = () => slot().querySelector("#ns-model");
  const box = () => slot().querySelector("#ns-model-other");
  const chip = (h) => d.querySelectorAll("[data-harness]").find((b) => b.dataset.harness === h);
  out.sheetOpened = !!(d && d.open);
  out.shown = !!menu();
  out.options = labels(menu());
  out.prefill = picked(menu());
  out.noBox = !box();
  q("#ns-folder").value = "/tmp/wt"; q("#ns-folder").oninput(); await tick();
  q("#ns-prompt").value = "say ok"; q("#ns-prompt").oninput();
  chip("gemini").onclick(); await tick();
  out.hides = !menu();
  chip("claude").onclick(); await tick();
  out.reprefill = picked(menu());
  choose(menu(), "sonnet");
  chip("claude").onclick(); await tick();
  out.recentKept = picked(menu());
  chip("codex").onclick(); await tick();
  out.foreignRedefault = picked(menu());
  const folderEl = q("#ns-folder");
  choose(menu(), "Other…");
  out.boxFocused = !!activeEl && activeEl.id === "ns-model-other";
  out.folderNodeKeptOther = q("#ns-folder") === folderEl;
  box().value = "  my-model "; box().oninput();
  chip("claude").onclick(); await tick();
  chip("codex").onclick(); await tick();
  out.dirtyPick = picked(menu());
  out.dirtyKept = box() && box().value;
  q("#ns-start").onclick(); await tick();
  const post = fetchCalls.find((c) => c.path === "/v1/sessions");
  out.postModel = post && post.body.model;
  out.postMachine = post && post.body.machine;
  // A fresh sheet: an explicit Harness default survives a switch and
  // sends no key.
  await openNewSession();
  await tick();
  q("#ns-folder").value = "/tmp/wt"; q("#ns-folder").oninput(); await tick();
  q("#ns-prompt").value = "say ok"; q("#ns-prompt").oninput();
  choose(menu(), "Other…");
  choose(menu(), "Harness default");
  out.boxGone = !box();
  out.selectFocused = !!activeEl && activeEl.id === "ns-model";
  chip("codex").onclick(); await tick();
  out.clearedKept = picked(menu());
  q("#ns-start").onclick(); await tick();
  const post2 = fetchCalls.filter((c) => c.path === "/v1/sessions").pop();
  out.emptyHasNoModel = !("model" in (post2 && post2.body || {}));
  // A fresh sheet is clean again: a machine switch re-defaults from that
  // machine's own ledger, here an old hub's one-item last_models.
  await openNewSession();
  await tick();
  q("#ns-folder").value = "/tmp/wt"; q("#ns-folder").oninput(); await tick();
  q("#ns-prompt").value = "say ok"; q("#ns-prompt").oninput();
  d.querySelectorAll("[data-machine]").find((b) => b.dataset.machine === "m2").onclick();
  await tick();
  out.machineRefill = picked(menu());
  out.oldHubOptions = labels(menu());
  // An old satellite's row carries no model_at_launch: the menu hides.
  apiAnswers.length = 0;
  apiAnswers.push(["/v1/machines", { status: 200, data: { machines: [
    { id: "m1", name: "hub", online: true, launchable: ["claude"] }] } }]);
  await openNewSession();
  await tick();
  out.hiddenOldSat = !menu();
  // A typed folder that flips the model row must not rebuild the sheet:
  // the folder input keeps its node (and so its focus and caret).
  apiAnswers.length = 0;
  apiAnswers.push(["/v1/machines", { status: 200, data: { machines: [HUB] } }]);
  await openNewSession();
  await tick();
  const folder2 = q("#ns-folder");
  folder2.value = "/tmp/gem"; folder2.oninput(); await tick();
  out.flipHidesRow = menu() == null;
  out.folderNodeKeptOff = q("#ns-folder") === folder2;
  folder2.value = "/tmp/wt"; folder2.oninput(); await tick();
  out.flipShowsRow = picked(menu());
  out.folderNodeKeptOn = q("#ns-folder") === folder2;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

# model-menu.js on its own: what the sheets above lean on.
MENU_UNIT = r"""
const out = {};
const st = ModelMenu.fresh();
ModelMenu.redefault(st, ["a", "b"]);
out.untouched = [st.kind, ModelMenu.value(st)];
st.kind = "recent"; st.recent = "b"; st.dirty = true;
ModelMenu.redefault(st, ["c", "b"]);
out.sharedRecentKept = [st.kind, ModelMenu.value(st), st.dirty];
ModelMenu.redefault(st, ["c"]);
out.foreignRecent = [st.kind, ModelMenu.value(st), st.dirty];
ModelMenu.redefault(st, []);
out.noRecents = [st.kind, ModelMenu.value(st)];
out.recents = [ModelMenu.recents(undefined, "x"), ModelMenu.recents(null, ""),
  ModelMenu.recents(["a", "", "a", "b"], "a"), ModelMenu.recents([], "x")];
out.harnessRecents = [ModelMenu.harnessRecents({ launch_model: false, recent_models: ["a"] }),
  ModelMenu.harnessRecents({ launch_model: true, last_model: "z" }), ModelMenu.harnessRecents(null)];
const busy = ModelMenu.html({ kind: "other", recent: "", typed: "x", dirty: true }, ["a"], "m", true);
out.busy = (busy.match(/ disabled/g) || []).length;
const weird = ModelMenu.html({ kind: "recent", recent: 'a"<b', typed: "", dirty: false }, ['a"<b'], "m", false);
out.escaped = weird.includes("a&quot;&lt;b") && !weird.includes('a"<b');
out.selectedWeird = /value="r0" selected/.test(weird);
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLaunchModelMenu(unittest.TestCase):
    """All three sheets drive the same Model menu; each flow returns the
    observations the assertions below check."""

    def run_node(self, script: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def run_flow(self, page_file: str, flow: str) -> dict:
        return self.run_node(STUB + MENU_JS.read_text(encoding="utf-8") + "\n"
                             + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                             + (PAGES / page_file).read_text(encoding="utf-8") + "\n"
                             + flow)

    def assert_walk(self, out: dict, old_hub_model: str = "pi-model"):
        """The shared contract, whichever sheet drove it."""
        self.assertTrue(out["shown"], "the menu should show for a model-taking harness")
        self.assertEqual(out["options"], ["Harness default", "opus", "sonnet", "Other…"])
        self.assertEqual(out["prefill"], "opus", "the newest recent model is the default")
        self.assertTrue(out["noBox"], "no typed box until Other… is picked")
        self.assertTrue(out["hides"], "the menu should hide for a harness that takes no model")
        self.assertEqual(out["reprefill"], "opus")
        self.assertEqual(out["recentKept"], "sonnet", "a picked recent model survives a re-default")
        self.assertEqual(out["foreignRedefault"], "gpt-5-codex",
                         "a picked model the new harness never used re-defaults")
        self.assertEqual(out["oldHubOptions"], ["Harness default", old_hub_model, "Other…"],
                         "an old hub's last-used model stands in as a one-item list")
        self.assertTrue(out["boxFocused"], "picking Other… focuses its box")
        self.assertEqual(out["dirtyPick"], "Other…")
        self.assertEqual(out["dirtyKept"], "  my-model ", "a typed model survives switches")
        self.assertEqual(out["postModel"], "my-model", "the typed model posts trimmed")
        self.assertEqual(out["clearedKept"], "Harness default",
                         "an explicit Harness default stays across a switch")
        self.assertTrue(out["emptyHasNoModel"], "Harness default must send no model key")

    def test_tasks_new_agent_session(self):
        out = self.run_flow("tasks.js", TASKS_FLOW)
        self.assert_walk(out)
        self.assertEqual(out["postHarness"], "codex")
        self.assertTrue(out["errorShown"], "the refusal should show in the sheet")
        self.assertTrue(out["blankOtherNoModel"], "a blank Other… sends no model key")
        self.assertTrue(out["boxGone"])
        self.assertTrue(out["selectFocused"], "leaving Other… puts focus back on the menu")

    def test_repositories_open_in_session(self):
        out = self.run_flow("repositories.js", REPOS_FLOW)
        self.assert_walk(out)
        self.assertTrue(out["sheetOpened"])
        self.assertEqual(out["postCwd"], "/tmp/wt")
        self.assertEqual(out["freshPrefill"], "gpt-5-codex")
        self.assertTrue(out["boxGone"])
        self.assertTrue(out["errorShown"], "the refusal should show in the sheet")

    def test_repositories_resolve_issue(self):
        out = self.run_flow("repositories.js", ISSUE_FLOW)
        self.assert_walk(out)
        self.assertTrue(out["rowIsButton"], "an issue row opens the sheet, not github.com")
        self.assertTrue(out["sheetOpened"])
        self.assertTrue(out["body"], "the body renders as markdown")
        self.assertTrue(out["comment"])
        self.assertTrue(out["github"], "Open on GitHub stays")
        self.assertTrue(out["composer"], "the comment box shows, Comment off while blank")
        self.assertTrue(out["composerCaption"], "it says whose gh posts and where")
        self.assertTrue(out["postOn"])
        self.assertTrue(out["plainEnterNoPost"], "Return alone breaks the line")
        self.assertEqual(out["commentPost"], {"repo": "o/r", "number": 5,
                                              "body": "  Looks fixed on main  "})
        self.assertTrue(out["commentError"], "a refused comment says why")
        self.assertEqual(out["draftKept"], "  Looks fixed on main  ")
        self.assertTrue(out["reread"], "a posted comment reads the issue again")
        self.assertTrue(out["draftCleared"])
        self.assertTrue(out["composerHidden"], "the Resolve panel replaces the comment box")
        self.assertTrue(out["boxTicked"], "Also post as a comment starts ticked, as on the Mac")
        self.assertEqual(out["contextComment"], {"repo": "o/r", "number": 5,
                                                 "body": "Use the new API."})
        self.assertTrue(out["commentFirst"], "the comment posts before the launch")
        self.assertTrue(out["noRepost"], "a retried Start doesn't comment twice")
        self.assertEqual(out["promptPrefill"], "Resolve issue #5 in o/r.")
        self.assertEqual(out["post"], {"repo": "o/r", "number": 5, "harness": "codex",
                                       "prompt": "Resolve issue #5 in o/r.",
                                       "context": "Use the new API.", "model": "my-model"})
        self.assertTrue(out["errorShown"], "the refusal should show in the sheet")
        self.assertEqual(out["freshPrefill"], "gpt-5-codex")
        self.assertTrue(out["boxGone"])
        self.assertEqual(out["blankPrompt"], "")
        self.assertTrue(out["noContext"])
        self.assertEqual(out["chat"], "#/sessions?chat=k9")
        self.assertTrue(out["closed"])
        self.assertTrue(out["closedStartOff"], "a refetch finding the issue closed turns Start off")
        self.assertTrue(out["closedNoPost"])

    def test_repositories_resolve_posts_context_comment(self):
        out = self.run_flow("repositories.js", ISSUE_COMMENT_FLOW)
        self.assertEqual(out["failedComment"], {"repo": "o/r", "number": 5, "body": "Tried a fix"})
        self.assertEqual(out["launchedAnyway"], "Tried a fix")
        self.assertTrue(out["warning"], "a refused comment warns and the launch goes ahead")
        self.assertEqual(out["chat"], "#/sessions?chat=k9")
        self.assertTrue(out["untickedNoComment"])
        self.assertEqual(out["untickedContext"], "Not for GitHub")

    def test_sessions_new_session(self):
        out = self.run_node(STUB + app_js_function("bodySheet") + MENU_JS.read_text(encoding="utf-8") + "\n"
                            + (PAGES.parent / "sessions.js").read_text(encoding="utf-8") + "\n"
                            + SESSIONS_FLOW)
        self.assertTrue(out["sheetOpened"])
        self.assert_walk(out, old_hub_model="opus-m2")
        self.assertEqual(out["postMachine"], "m1")
        self.assertTrue(out["folderNodeKeptOther"], "Other… redraws the model row only")
        self.assertTrue(out["boxGone"])
        self.assertTrue(out["selectFocused"], "leaving Other… puts focus back on the menu")
        self.assertEqual(out["machineRefill"], "opus-m2",
                         "an untouched menu re-defaults from the new machine's ledger")
        self.assertTrue(out["hiddenOldSat"],
                        "a machine row without model_at_launch shows no menu")
        self.assertTrue(out["flipHidesRow"],
                        "a folder re-defaulting to a non-model harness drops the row")
        self.assertTrue(out["folderNodeKeptOff"],
                        "the folder input is rebuilt on a model-row flip, so focus is lost")
        self.assertEqual(out["flipShowsRow"], "opus",
                         "a folder re-defaulting back re-fills the row")
        self.assertTrue(out["folderNodeKeptOn"],
                        "the folder input is rebuilt when the row returns, so focus is lost")

    def test_sessions_no_machine_note(self):
        """A hub that answers ``launchable: []`` is current, so the sheet
        says to install or turn a harness on; only a row without the
        field (an older hub), or none, says to update swe."""
        out = self.run_node(STUB + MENU_JS.read_text(encoding="utf-8") + "\n"
                            + (PAGES.parent / "sessions.js").read_text(encoding="utf-8") + "\n"
                            + """
const hub = { id: "m1", harnesses: ["claude"], launchable: [] };
process.stdout.write(JSON.stringify({
  current: nsNoMachineNote([hub]),
  fleet: nsNoMachineNote([hub, { id: "s1", launchable: [] }]),
  old: nsNoMachineNote([{ id: "m1", harnesses: [] }]),
  none: nsNoMachineNote([]),
}));
""")
        self.assertEqual(out["current"], "No harness can start on the hub: install one there, "
                                         "or turn it on in Settings › Providers.")
        self.assertTrue(out["fleet"].startswith("No harness can start on any machine"))
        self.assertTrue(out["old"].startswith("No machine can start a session. Update swe on the hub"))
        self.assertTrue(out["none"].startswith("No machine can start a session. Update swe on the hub"))

    def test_no_harness_note_names_the_settings_section(self):
        """The web Settings section is "Providers"; "Provider visibility"
        is only a row inside it on the Mac."""
        for page in ("tasks.js", "repositories.js"):
            text = (PAGES / page).read_text(encoding="utf-8")
            self.assertNotIn("Settings › Provider visibility", text, page)
            self.assertIn("turn it on in Settings › Providers.", text, page)

    def test_menu_rules(self):
        out = self.run_node(STUB + MENU_JS.read_text(encoding="utf-8") + "\n" + MENU_UNIT)
        self.assertEqual(out["untouched"], ["recent", "a"])
        self.assertEqual(out["sharedRecentKept"], ["recent", "b", True])
        self.assertEqual(out["foreignRecent"], ["recent", "c", False])
        self.assertEqual(out["noRecents"], ["default", ""])
        self.assertEqual(out["recents"], [["x"], [], ["a", "b"], []])
        self.assertEqual(out["harnessRecents"], [[], ["z"], []])
        self.assertEqual(out["busy"], 2, "busy disables the select and the box")
        self.assertTrue(out["escaped"])
        self.assertTrue(out["selectedWeird"])


if __name__ == "__main__":
    unittest.main()
