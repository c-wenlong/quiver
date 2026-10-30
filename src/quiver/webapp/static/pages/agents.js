/* AGENTS.md: a port of the Mac app's AgentsMdPage. Saving stays in swe and
 * the Mac app. Global shows the shared ~/.quiver/AGENTS.md and where
 * swe init --check says it is linked, with Link with swe init and, for
 * real files in the way, Replace and back up (swe init --force, after an
 * inline confirm); Project shows one folder's AGENTS.md and CLAUDE.md.
 * Data: /v1/app/agents, POST /v1/app/agents/link|replace
 * (pages/agents.py). The bundle runs each page script in its own scope, so
 * nothing here comes from skills.js (its CSS classes do). */
"use strict";

const agentsPage = {
  // Global and Project reads count separately, so finishing one never
  // drops the other's answer.
  scope: "global", data: null, globalError: null, globalSeq: 0, projectSeq: 0,
  folder: null, project: null, projectError: null, file: "AGENTS.md",
  // The init in flight ("link" or "replace"), whether Replace is asking
  // to confirm, and the last run's failure (AgentsModel.linking/linkError).
  running: null, confirming: false, runError: null,
};
const AGENTS_FOLDER_KEY = "quiver.agentsProjectFolder";
// The app's "link" SF Symbol.
const AGENTS_LINK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/></svg>';

/* LinkState.title */
const AGENT_STATE_TITLE = {
  linked: "Linked", relink: "Points elsewhere", create: "Not linked", absorb: "Copy to absorb",
  keep: "Only copy, kept", conflict: "File in the way", skipped: "Not installed", ignored: "Archived",
};
const agentStateTitle = (s) => AGENT_STATE_TITLE[s] || (s ? s[0].toUpperCase() + s.slice(1) : "");
const agentInitFixes = (s) => ["create", "relink", "absorb"].includes(s);
const agentBlocked = (s) => ["conflict", "keep"].includes(s);

function agentsHeader(busy) {
  return pageHeader("AGENTS.md", "The instructions every harness reads, and each project's own.",
    `<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Check links and reread the file">${icon("rescan")}</button>`);
}

function agentsTabs() {
  const p = agentsPage;
  return `<div class="ag-tabs" role="tablist">${[["global", "Global"], ["project", "Project"]].map(([id, label]) =>
    `<button class="ag-tab${p.scope === id ? " selected" : ""}" data-scope="${id}" role="tab" aria-selected="${p.scope === id}">${label}</button>`).join("")}</div>`;
}

async function renderAgents() {
  const p = agentsPage, gen = routeGen;
  if (p.folder === null) {
    try { p.folder = localStorage.getItem(AGENTS_FOLDER_KEY) || ""; } catch { p.folder = ""; }
  }
  paintAgents(!p.data && !p.globalError);
  const mineGlobal = ++p.globalSeq, mineProject = p.folder ? ++p.projectSeq : null;
  const loads = [api("/v1/app/agents")];
  if (p.folder) loads.push(api(`/v1/app/agents?folder=${encodeURIComponent(p.folder)}`));
  const [global, project] = await Promise.all(loads);
  if (gen !== routeGen) return;
  if (global.status === 401 || (project && project.status === 401)) {
    return renderGate();
  }
  // A failed check (an unreadable .linkignore, say) stays on the Global
  // tab; the Project tab reads its folder all the same.
  if (mineGlobal === p.globalSeq) {
    if (global.status === 200 && global.data) { p.data = global.data; p.globalError = null; }
    else p.globalError = agentsErrorText(global);
  }
  if (project && mineProject === p.projectSeq) agentsTakeProject(project);
  paintAgents(false);
}

function agentsErrorText({ status, data }) {
  return failText(status, data, "then refresh");
}

function agentsTakeProject({ status, data }) {
  const p = agentsPage;
  if (status === 200 && data) { p.project = data; p.projectError = null; return; }
  p.project = null;
  p.projectError = agentsErrorText({ status, data });
}

function paintAgents(loading) {
  const p = agentsPage;
  let body;
  if (p.scope === "project" && !loading) body = agentsProject();
  else if (p.scope === "global" && p.globalError && !loading) body = `<div class="card"><p class="error">${esc(p.globalError)}</p></div>`;
  else if (loading || !p.data) body = `<div class="card"><p class="empty">Checking links…</p></div>`;
  else body = agentsGlobal(p.data);
  document.getElementById("panel").innerHTML = `<div class="page">${agentsHeader(loading)}
    <div class="stack">${agentsTabs()}${body}</div></div>`;
  bindRefresh(renderAgents);
  for (const tab of document.querySelectorAll("[data-scope]")) {
    tab.onclick = () => { p.scope = tab.dataset.scope; paintAgents(!p.data && !p.globalError); };
  }
  for (const tab of document.querySelectorAll("[data-file]")) {
    tab.onclick = () => { p.file = tab.dataset.file; paintAgents(false); };
  }
  for (const btn of document.querySelectorAll("[data-agents]")) {
    btn.onclick = () => {
      const act = btn.dataset.agents;
      if (act === "link" || act === "replace") agentsRun(act);
      else if (act === "ask" || act === "cancel") {
        p.confirming = act === "ask" ? !p.confirming : false;
        paintAgents(false);
        const next = document.querySelector(`[data-agents="${p.confirming ? "replace" : "ask"}"]`);
        if (next) next.focus();
      }
    };
  }
  const confirm = document.querySelector(".ag-confirm");
  if (confirm) {
    confirm.onkeydown = (event) => {
      if (event.key !== "Escape") return;
      p.confirming = false;
      paintAgents(false);
      const ask = document.querySelector('[data-agents="ask"]');
      if (ask) ask.focus();
    };
  }
  const form = document.getElementById("ag-folder-form");
  if (form) form.onsubmit = (e) => { e.preventDefault(); agentsOpenFolder(document.getElementById("ag-folder").value); };
}

/* InstructionEditor, read only: a card with the file's path and its text. */
function agentsDoc(title, subtitle, inner) {
  return `<div class="card ag-doc"><div><div class="card-title sk-mono-title">${esc(title)}</div>
    <div class="card-subtitle">${esc(subtitle)}</div></div>${inner}</div>`;
}

function agentsGlobal(d) {
  const p = agentsPage;
  const rows = d.harnesses.filter((h) => h.instructions);
  const linked = rows.filter((h) => h.instructions.state === "linked").length;
  const blocked = d.blocked || [];
  const needsInit = rows.some((h) => agentInitFixes(h.instructions.state)) || !d.agents_file_exists;
  const link = needsInit || p.running === "link"
    ? `<button class="sk-button primary ag-run" data-agents="link"${p.running ? " disabled" : ""}
        title="Runs swe init on the hub, which links what is missing and never replaces a real file.">${
        p.running === "link" ? '<span class="ag-spinner" aria-hidden="true"></span>Linking…' : `${AGENTS_LINK_ICON}Link with swe init`}</button>` : "";
  const replace = blocked.length || p.running === "replace" ? `<div class="ag-blocked">
      <p class="caption">A real file or folder is in the way for some harnesses. swe init leaves those alone.</p>
      <button class="sk-button ag-run" data-agents="ask" aria-expanded="${p.confirming}"${p.running ? " disabled" : ""}>${
        p.running === "replace" ? '<span class="ag-spinner" aria-hidden="true"></span>Replacing…' : "Replace and back up…"}</button></div>
    ${p.confirming && !p.running ? agentsConfirm(d, blocked) : ""}` : "";
  const strip = `<div class="ag-strip"><div class="ag-strip-row">
      <span class="card-title" title="From swe init --check">Harnesses</span>
      <div class="ag-links">${rows.map((h) => agentsLinkRow(h, d.home)).join("")}</div>${link}</div>
    ${replace}
    ${p.runError ? `<p class="error ag-run-error" role="alert">${esc(p.runError)}</p>` : ""}
  </div>`;
  let inner;
  if (!d.agents_file_exists) inner = `<p class="empty">${esc(Fmt.tilde(d.agents_file, d.home))} doesn't exist yet. Link with swe init writes it.</p>`;
  else if (d.text == null) inner = '<p class="caption">Can\'t preview this file.</p>';
  else inner = `<pre class="sk-text ag-text">${esc(d.text)}</pre>`;
  return strip + agentsDoc(Fmt.tilde(d.agents_file, d.home),
    linked === 1 ? "Linked into 1 harness" : `Linked into ${linked} harnesses`, inner);
}

/* Replace and back up asks first: it deletes real files after copying them. */
function agentsConfirm(d, blocked) {
  const machine = d.machine || "the hub";
  return `<div class="ag-confirm" role="group" aria-label="Run swe init --force?">
    <p class="caption">This runs swe init --force on ${esc(machine)}. Each of these is copied to ~/.quiver/backups, then replaced with a link to the shared copy:</p>
    <ul class="ag-confirm-paths">${blocked.map((path) => `<li><code>${esc(path)}</code></li>`).join("")}</ul>
    <div class="ag-confirm-buttons">
      <button class="sk-button" data-agents="cancel">Cancel</button>
      <button class="sk-button danger" data-agents="replace">Replace and back up</button>
    </div></div>`;
}

/* AgentsModel.link: one init at a time; the hub answers with the Global
 * tab read again, whether the run worked or not. */
async function agentsRun(action) {
  const p = agentsPage;
  if (p.running) return;
  p.running = action;
  p.confirming = false;
  p.runError = null;
  paintAgents(false);
  const { status, data } = await apiPost(`/v1/app/agents/${action}`, {});
  p.running = null;
  // The answer is kept even if the page was left meanwhile, so a return
  // to it shows the result rather than a button still spinning.
  const here = onAgentsPage();
  if (status === 401) {
    if (here) renderGate();
    return;
  }
  if (status === 200 && data) {
    // A Global load still in flight read the links before this run: drop it.
    p.globalSeq++;
    p.data = data;
    p.globalError = null;
    p.runError = data.error || null;
  } else {
    p.runError = agentsErrorText({ status, data });
  }
  if (!here) return;
  paintAgents(false);
  const again = document.querySelector(`[data-agents="${action === "replace" ? "ask" : "link"}"]`);
  if (again) again.focus();
}

function onAgentsPage() {
  return currentRoute() === "agents";
}

function agentsLinkRow(h, home) {
  const s = h.instructions.state, title = agentStateTitle(s);
  const color = s === "linked" ? "linked" : agentBlocked(s) ? "danger" : agentInitFixes(s) ? "fixes" : "other";
  const name = Fmt.harnessName(h.name);
  return `<span class="ag-link" title="${esc(name)}, ${esc(title.toLowerCase())}: ${esc(Fmt.tilde(h.instructions.path, home))}"
      aria-label="${esc(name)}: ${esc(title)}"><span class="ag-dot ${color}" aria-hidden="true"></span>${Fmt.markView(h.name, "sk-mark")}${
    s === "linked" ? "" : `<span class="caption">${esc(title.toLowerCase())}</span>`}</span>`;
}

function agentsProject() {
  const p = agentsPage, d = p.data || {}, pr = p.project;
  const suggestions = (d.suggestions || []).map((f) => `<option value="${esc(f)}"></option>`).join("");
  const strip = `<form class="ag-strip ag-folder" id="ag-folder-form">
    <span class="ag-folder-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></svg></span>
    <input class="field search" id="ag-folder" list="ag-folders" type="text" spellcheck="false" autocomplete="off"
      placeholder="A project folder on the hub, like ~/dev/app" aria-label="Project folder" value="${esc(p.folder || "")}">
    <datalist id="ag-folders">${suggestions}</datalist>
    <button class="sk-button" type="submit">Open</button>
  </form>`;
  if (!p.folder) {
    return `${strip}<p class="caption">Choose a project folder to read its AGENTS.md and CLAUDE.md.${
      suggestions ? " Recent session folders are suggested as you type." : ""}</p>`;
  }
  if (p.projectError) return `${strip}<div class="card"><p class="error">${esc(p.projectError)}</p></div>`;
  if (!pr) return `${strip}<div class="card"><p class="empty">Reading the folder…</p></div>`;
  const files = pr.files;
  if (!files.some((f) => f.name === p.file)) p.file = files[0].name;
  const tabTitle = (f) => (f.links_to ? `${f.name} → ${f.links_to}` : f.exists ? f.name : `${f.name} (new)`);
  const tabs = `<div class="tabs table" role="tablist">${files.map((f) =>
    `<button class="tab${f.name === p.file ? " selected" : ""}" data-file="${esc(f.name)}" role="tab" aria-selected="${f.name === p.file}">${esc(tabTitle(f))}</button>`).join("")}</div>`;
  const f = files.find((x) => x.name === p.file);
  const subtitle = f.links_to ? `Links to ${f.links_to}.` : !f.exists ? "Doesn't exist yet." : "In the folder above";
  let inner;
  if (!f.exists) inner = `<p class="empty">No ${esc(f.name)} in this folder.</p>`;
  else if (f.outside) inner = '<p class="caption">This file links outside the hub\'s home, so the web app doesn\'t open it.</p>';
  else if (f.text == null) inner = '<p class="caption">Can\'t preview this file.</p>';
  else inner = `<pre class="sk-text ag-text">${esc(f.text)}</pre>`;
  return `${strip}${tabs}${agentsDoc(f.name, subtitle, inner)}`;
}

async function agentsOpenFolder(value) {
  const p = agentsPage, folder = value.trim(), gen = routeGen, mine = ++p.projectSeq;
  p.folder = folder;
  p.project = null;
  p.projectError = null;
  try {
    if (folder) localStorage.setItem(AGENTS_FOLDER_KEY, folder); else localStorage.removeItem(AGENTS_FOLDER_KEY);
  } catch { /* private window: the folder just isn't remembered */ }
  paintAgents(false);
  if (!folder) return;
  const reply = await api(`/v1/app/agents?folder=${encodeURIComponent(folder)}`);
  if (gen !== routeGen || mine !== p.projectSeq) return;
  if (reply.status === 401) return renderGate();
  agentsTakeProject(reply);
  paintAgents(false);
}

registerPage("agents", renderAgents);
