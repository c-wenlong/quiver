/* AGENTS.md: a port of the Mac app's AgentsMdPage. Read only: saving and
 * "Link with swe init" stay in swe and the Mac app. Global shows the
 * shared ~/.quiver/AGENTS.md and where swe init --check says it is linked;
 * Project shows one folder's AGENTS.md and CLAUDE.md. Data:
 * /v1/app/agents (pages/agents.py). Uses skillsPath and skillsMark from
 * skills.js, which the bundle loads after this file but before any render. */
"use strict";

const agentsPage = {
  scope: "global", data: null, seq: 0,
  folder: null, project: null, projectError: null, file: "AGENTS.md",
};
const AGENTS_FOLDER_KEY = "quiver.agentsProjectFolder";

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
  const p = agentsPage, gen = routeGen, mine = ++p.seq;
  if (p.folder === null) {
    try { p.folder = localStorage.getItem(AGENTS_FOLDER_KEY) || ""; } catch { p.folder = ""; }
  }
  paintAgents(!p.data);
  const loads = [api("/v1/app/agents")];
  if (p.folder) loads.push(api(`/v1/app/agents?folder=${encodeURIComponent(p.folder)}`));
  const [global, project] = await Promise.all(loads);
  if (gen !== routeGen || mine !== p.seq) return;
  if (global.status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (global.status !== 200 || !global.data) return pageError(agentsHeader(false), global.status, global.data, renderAgents);
  p.data = global.data;
  if (project) agentsTakeProject(project);
  paintAgents(false);
}

function agentsTakeProject({ status, data }) {
  const p = agentsPage;
  if (status === 200 && data) { p.project = data; p.projectError = null; return; }
  p.project = null;
  p.projectError = (data && data.error && data.error.message)
    || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
}

function paintAgents(loading) {
  const p = agentsPage;
  let body;
  if (loading) body = `<div class="card"><p class="empty">Checking links…</p></div>`;
  else body = p.scope === "global" ? agentsGlobal(p.data) : agentsProject();
  document.getElementById("panel").innerHTML = `<div class="page">${agentsHeader(loading)}
    <div class="stack">${agentsTabs()}${body}</div></div>`;
  bindRefresh(renderAgents);
  for (const tab of document.querySelectorAll("[data-scope]")) {
    tab.onclick = () => { p.scope = tab.dataset.scope; paintAgents(!p.data); };
  }
  for (const tab of document.querySelectorAll("[data-file]")) {
    tab.onclick = () => { p.file = tab.dataset.file; paintAgents(false); };
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
  const rows = d.harnesses.filter((h) => h.instructions);
  const linked = rows.filter((h) => h.instructions.state === "linked").length;
  const strip = `<div class="ag-strip"><div class="ag-strip-row">
      <span class="card-title" title="From swe init --check">Harnesses</span>
      <div class="ag-links">${rows.map((h) => agentsLinkRow(h, d.home)).join("")}</div></div>
    ${rows.some((h) => agentBlocked(h.instructions.state))
      ? '<p class="caption">A real file is in the way for some harnesses. swe init leaves those alone; swe init --force in Terminal replaces them.</p>' : ""}
    ${rows.some((h) => agentInitFixes(h.instructions.state))
      ? '<p class="caption">Run <code>swe init</code> on the hub to link the rest. It never replaces a real file.</p>' : ""}
  </div>`;
  let inner;
  if (!d.agents_file_exists) inner = `<p class="empty">${esc(skillsPath(d.agents_file, d.home))} doesn't exist yet. <code>swe init</code> on the hub writes it.</p>`;
  else if (d.text == null) inner = '<p class="caption">Can\'t preview this file.</p>';
  else inner = `<pre class="sk-text ag-text">${esc(d.text)}</pre>`;
  return strip + agentsDoc(skillsPath(d.agents_file, d.home),
    linked === 1 ? "Linked into 1 harness" : `Linked into ${linked} harnesses`, inner);
}

function agentsLinkRow(h, home) {
  const s = h.instructions.state, title = agentStateTitle(s);
  const color = s === "linked" ? "linked" : agentBlocked(s) ? "danger" : agentInitFixes(s) ? "fixes" : "other";
  const name = Fmt.harnessName(h.name);
  return `<span class="ag-link" title="${esc(name)}, ${esc(title.toLowerCase())}: ${esc(skillsPath(h.instructions.path, home))}"
      aria-label="${esc(name)}: ${esc(title)}"><span class="ag-dot ${color}" aria-hidden="true"></span>${skillsMark(h.name)}${
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
  const p = agentsPage, folder = value.trim(), gen = routeGen, mine = ++p.seq;
  p.folder = folder;
  p.project = null;
  p.projectError = null;
  try {
    if (folder) localStorage.setItem(AGENTS_FOLDER_KEY, folder); else localStorage.removeItem(AGENTS_FOLDER_KEY);
  } catch { /* private window: the folder just isn't remembered */ }
  paintAgents(false);
  if (!folder) return;
  const reply = await api(`/v1/app/agents?folder=${encodeURIComponent(folder)}`);
  if (gen !== routeGen || mine !== p.seq) return;
  if (reply.status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  agentsTakeProject(reply);
  paintAgents(false);
}

registerPage("agents", renderAgents);
