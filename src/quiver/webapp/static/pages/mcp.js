/* MCP servers: a port of the Mac app's McpServersPage. Every server the
 * hub machine's harnesses configure, as a server × harness grid split into
 * Local (launched with a command on the hub; a folder in
 * ~/.quiver/mcp/servers it runs from shows as a checkout tag) and Remote
 * (reached at a URL), with the
 * definitions that disagree lettered. A server's sheet syncs it, with a
 * Preview first, through POST /v1/app/mcp/preview and /sync, removes it
 * through /remove, and sets its ${NAME} values through /secret_set (shown
 * masked; /secret_value reveals one). Add server is the Mac app's
 * McpAddServerSheet, through /add. Data: /v1/app/mcp,
 * and /v1/app/mcp?health=1 only when "Check health" is pressed.
 */
"use strict";

const MCP_HIDDEN_KEY = "quiver.mcpHiddenHarnesses";
const MCP_FILTERS = [["all", "All"], ["drift", "Out of sync"], ["conflicts", "Conflicts"]];
// McpModel's maxAge: a list younger than this is shown without a reread.
const MCP_MAX_AGE_MS = 60 * 1000;
// Rows a section shows before folding behind a Show more row.
const MCP_FOLD = 10;

const mcpPage = {
  data: null, at: 0, seq: 0, loading: false, checking: false, failure: null,
  filter: "all", opened: new Set(), hidden: mcpLoadHidden(),
  // The open sheet's sync state (mcpSheetState), kept across a repaint.
  sheet: null,
  // What the last Remove or Add did, until the list is read again.
  notice: null,
  // The Add server sheet's draft while it is open (mcpOpenAdd).
  add: null,
};

function mcpLoadHidden() {
  try { return new Set(JSON.parse(localStorage.getItem(MCP_HIDDEN_KEY) || "[]")); } catch { return new Set(); }
}

function mcpSaveHidden() {
  try { localStorage.setItem(MCP_HIDDEN_KEY, JSON.stringify([...mcpPage.hidden].sort())); } catch { /* private window */ }
}

const mcpOnPage = () => currentRoute() === "mcp";

const MCP_SVG = {
  health: '<path d="M2 12h3l2-5 3 10 3-12 3 9 2-2h4"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  plug: '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18M10.6 5.1A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7a9.8 9.8 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
};
const mcpSvg = (name, cls = "") =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${MCP_SVG[name]}</svg>`;

/* --------------------------------------------------------------- loading */

/* McpModel.loadIfStale: a list read in the last minute shows as it is,
 * health included; an older one is read again, without health. */
async function renderMcp() {
  if (mcpPage.data || mcpPage.failure) paintMcp();
  else mcpShell(`<div class="card"><p class="empty">Reading the configs…</p></div>`);
  if (mcpPage.loading) return;
  if (mcpPage.data && Date.now() - mcpPage.at < MCP_MAX_AGE_MS) return;
  await mcpLoad(false);
}

/* The list, or with `health` the list plus every server's probe. The reply
 * is kept even when the user has moved on (the Mac app's model is app-wide)
 * but painted only while this page is showing. */
async function mcpLoad(health) {
  const seq = ++mcpPage.seq;
  mcpPage.loading = true;
  mcpPage.notice = null;
  mcpPage.checking = health;
  if (mcpOnPage() && (mcpPage.data || mcpPage.failure)) paintMcp();
  const { status, data } = await api(`/v1/app/mcp${health ? "?health=1" : ""}`);
  if (seq !== mcpPage.seq) return;
  mcpPage.loading = false;
  mcpPage.checking = false;
  if (status === 401) {
    if (mcpOnPage()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    mcpPage.failure = { status, data };
  } else {
    mcpPage.data = data;
    mcpPage.at = Date.now();
    mcpPage.failure = null;
  }
  if (mcpOnPage()) paintMcp();
}

function mcpHasHealth() {
  return !!(mcpPage.data && mcpPage.data.servers.some((s) => s.health != null));
}

/* ---------------------------------------------------------------- model */

/* McpServer.groups: definitions grouped by who agrees. */
function mcpGroups(s) {
  if (s.variants) return s.variants;
  return s.conflict ? s.harnesses.map((h) => [h]) : [s.harnesses];
}
const mcpGroupOf = (s, h) => mcpGroups(s).findIndex((g) => g.includes(h));
const mcpMissing = (s, all) => all.filter((h) => !s.harnesses.includes(h));
function mcpConflicts(s, among) {
  return new Set(among.map((h) => mcpGroupOf(s, h)).filter((i) => i >= 0)).size > 1;
}
const mcpLetter = (i) => (i < 26 ? String.fromCharCode(65 + i) : String(i + 1));

/* Harnesses that configure at least one server get a column; one with
 * none is not using MCP and would make every server look out of sync. */
function mcpActive(d) {
  return d.harnesses.filter((h) => d.servers.some((s) => s.harnesses.includes(h)));
}

function mcpFiltered(d, filter, compared) {
  if (filter === "drift") {
    return d.servers.filter((s) => mcpMissing(s, compared).length || mcpConflicts(s, compared));
  }
  if (filter === "conflicts") return d.servers.filter((s) => mcpConflicts(s, compared));
  return d.servers;
}

/* Reached at a URL (http, sse), not launched with a command on the hub. */
const mcpIsRemote = (s) => s.transport === "http" || s.transport === "sse";

/* "Local: runs on box": the machine whose harnesses launch a command
 * server, which for the web app is the hub. */
const mcpLocalTitle = () => `Local: runs on ${(mcpPage.data && mcpPage.data.machine) || "the hub"}`;

/* "Local · stdio", "Remote · http": a server's type, for the sheet. */
const mcpTypeLabel = (s) => `${mcpIsRemote(s) ? "Remote" : "Local"} · ${s.transport}`;

/* McpServerRepo.grouped: Local holds every server a harness launches with
 * a command, Remote the ones at a URL. A Local server run from a folder in
 * the servers folder carries it (the row's checkout tag); folders no
 * server uses join Local as their own rows. Local sorts by name, Remote
 * keeps swe's order. */
function mcpGrouped(servers, folders) {
  const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
  const local = [], remote = [];
  for (const s of servers) {
    const backing = folders.filter((f) => f.used_by.includes(s.name)).sort((a, b) => byName(a.name, b.name));
    (mcpIsRemote(s) ? remote : local).push({ server: s, folders: backing, name: s.name });
  }
  for (const f of folders) if (!f.used_by.length) local.push({ folder: f, name: f.name });
  local.sort((a, b) => byName(a.name, b.name));
  return { local, remote };
}

/* HarnessMark (Fmt.mark): the logo, and a word after it (or alone with
 * no logo). */
function mcpMarkHtml(h) {
  const m = Fmt.mark(h);
  return `<span class="mcp-mark" title="${esc(m.name)}">${m.icon ? Fmt.logo(m.icon) : ""}${
    m.word ? `<span>${esc(m.word)}</span>` : ""}</span>`;
}

const mcpNames = (list) => list.map((h) => Fmt.mark(h).name).join(", ");

/* ---------------------------------------------------------------- paint */

function mcpHeader() {
  const d = mcpPage.data;
  const where = d && d.machine ? ` on ${d.machine}` : "";
  const busy = mcpPage.loading;
  return pageHeader("MCP servers",
    `Every server your harnesses configure${where}, where each is missing, and where two disagree.`,
    `<button class="mcp-button" id="mcp-add"${mcpPage.data ? "" : " disabled"}>${mcpSvg("plus")}<span>Add server</span></button>
     <button class="mcp-button" id="mcp-health" title="Run each server's command check and reach each URL"${
      mcpPage.checking ? " disabled" : ""}>${mcpSvg("health")}<span>${mcpPage.checking ? "Checking…" : "Check health"}</span></button>
     <button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Read the configs again">${icon("rescan")}</button>`);
}

function mcpShell(body) {
  document.getElementById("panel").innerHTML = `<div class="page mcp">${mcpHeader()}
    <div class="stack">${body}</div></div>
    <dialog class="sheet mcp-sheet" id="mcp-sheet"></dialog>
    <dialog class="sheet mcp-sheet mcp-add-sheet" id="mcp-add-sheet" aria-labelledby="mcp-add-title"></dialog>`;
  const add = document.getElementById("mcp-add");
  if (add) add.onclick = () => mcpStartAdd();
  const health = document.getElementById("mcp-health");
  if (health) health.onclick = () => mcpLoad(true);
  // Refresh keeps health on when the list on show has it, as in the app.
  bindRefresh(() => mcpLoad(mcpHasHealth()));
}

function paintMcp() {
  if (!mcpPage.data) {
    const { status, data } = mcpPage.failure || {};
    const msg = failText(status, data, "then refresh");
    return mcpShell(`<div class="card"><p class="error">${esc(msg)}</p></div>`);
  }
  const d = mcpPage.data, p = mcpPage;
  const folders = d.folders || { items: [] };
  const parts = [];
  if (p.failure) {
    const { status, data } = p.failure;
    const msg = failText(status, data, "then refresh");
    parts.push(`<div class="card"><p class="error">${esc(msg)}</p></div>`);
  }
  if (p.notice) parts.push(`<div class="card mcp-notice"><p role="status">${mcpSvg("check")}<span>${esc(p.notice)}</span></p></div>`);
  if (d.error) {
    parts.push(`<div class="card"><p class="error">${esc(d.error)}</p></div>`);
    parts.push(mcpFolderOnly(folders, false));
  } else if (!d.servers.length) {
    parts.push(`<div class="card"><p class="empty mcp-label">${mcpSvg("plug")}No MCP servers in any harness config.</p></div>`);
    parts.push(mcpFolderOnly(folders, true));
  } else {
    const active = mcpActive(d);
    const compared = active.filter((h) => !p.hidden.has(h));
    parts.push(`<div class="tabs" role="tablist">${MCP_FILTERS.map(([id, title]) => {
      const n = mcpFiltered(d, id, compared).length;
      return `<button class="tab${id === p.filter ? " selected" : ""}" data-mcp-filter="${id}" role="tab" aria-selected="${id === p.filter}">${title} ${n}</button>`;
    }).join("")}</div>`);
    parts.push(mcpChips(active));
    const grouped = mcpGrouped(mcpFiltered(d, p.filter, compared), folders.items);
    parts.push(grouped.local.length || grouped.remote.length
      ? `<div class="card mcp-grid-card">${mcpGrid(compared, grouped, mcpHasHealth(), true)}</div>`
      : `<div class="card"><p class="caption mcp-body-small">Every server is in every compared harness, defined the same way.</p></div>`);
    const idle = d.harnesses.filter((h) => !active.includes(h));
    parts.push(`<div class="mcp-notes caption">
      ${idle.length ? `<p>No servers in ${esc(mcpNames(idle))}, so they have no column. A sync can still copy servers there.</p>` : ""}
      <p>Letters mark different definitions of one server. Env and header values never leave swe; only their names show here.</p>
    </div>`);
  }
  parts.push(mcpRepoSection(folders));
  // A repaint (a check or refresh landing) rebuilds the sheet too, so an
  // open one is opened again on the same server with its new state.
  const sheet = document.getElementById("mcp-sheet");
  const openName = sheet && sheet.open ? sheet.dataset.server : null;
  // The Add sheet is drawn again too, with the cursor back where it was.
  const addSheet = document.getElementById("mcp-add-sheet");
  const addFocus = addSheet && addSheet.contains(document.activeElement) ? mcpAddFocusKey(document.activeElement) : null;
  mcpShell(parts.join(""));
  mcpBind();
  const again = openName != null && d.servers.find((s) => s.name === openName);
  if (again) mcpOpenSheet(again);
  if (p.add) mcpOpenAdd(addFocus);
}

/* The Local folder rows alone, when there is no server grid to put them
 * in. With the inventory unknown a folder can't claim "Not in the hub". */
function mcpFolderOnly(folders, hubKnown) {
  const local = mcpGrouped([], folders.items || []).local;
  return local.length ? `<div class="card mcp-grid-card">${mcpGrid([], { local, remote: [] }, false, hubKnown)}</div>` : "";
}

/* What the folder scan could not put in the list: an error, a missing
 * folder, or an empty one. A good scan's folders are the Local rows. */
function mcpRepoSection(folders) {
  if (folders.error) return `<div class="card"><p class="error">${esc(folders.error)}</p></div>`;
  if (!folders.exists) return `<div class="card"><p class="caption mcp-body-small">${esc(folders.root)} doesn't exist on the hub machine.</p></div>`;
  if (!folders.items.length) return `<div class="card"><p class="caption mcp-body-small">No server folders in ${esc(folders.root)}.</p></div>`;
  return "";
}

function mcpChips(harnesses) {
  return `<div class="mcp-chips"><span class="caption">Compare</span>${harnesses.map((h) => {
    const on = !mcpPage.hidden.has(h), name = Fmt.mark(h).name;
    return `<button class="mcp-chip${on ? " on" : ""}" data-mcp-chip="${esc(h)}" aria-pressed="${on}"
      title="${esc(on ? `Leave ${name} out of the comparison` : `Compare ${name} too`)}">${on ? mcpSvg("check", "tick") : ""}${mcpMarkHtml(h)}</button>`;
  }).join("")}</div>`;
}

function mcpGrid(harnesses, grouped, showHealth, hubKnown) {
  const sections = [["local", mcpLocalTitle(), grouped.local], ["remote", "Remote", grouped.remote]].filter(([, , rows]) => rows.length);
  const span = harnesses.length + 1;
  const head = `<thead><tr><th class="mcp-name-col">Server</th>${harnesses.map((h) =>
    `<th class="mcp-col">${mcpMarkHtml(h)}</th>`).join("")}</tr></thead>`;
  const body = sections.map(([key, title, rows]) => {
    const open = mcpPage.opened.has(key);
    const shown = open || rows.length <= MCP_FOLD ? rows : rows.slice(0, MCP_FOLD);
    const hidden = Math.max(0, rows.length - MCP_FOLD);
    let html = `<tr class="mcp-section"><td colspan="${span}">${esc(title)} · ${rows.length}</td></tr>`;
    html += shown.map((row) => (row.server
      ? mcpServerRow(row.server, row.folders, harnesses, showHealth)
      : mcpFolderRow(row.folder, harnesses, hubKnown))).join("");
    if (hidden) {
      html += `<tr class="mcp-expand-row"><td colspan="${span}"><button class="mcp-expand" data-mcp-fold="${key}"
        title="${esc(open ? `Show only the first ${MCP_FOLD}` : `Show every ${key} row`)}">${mcpSvg(open ? "up" : "down")}<span>${
        open ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</span></button></td></tr>`;
    }
    return html;
  }).join("");
  return `<div class="table-wrap"><table class="mcp-grid">${head}<tbody>${body}</tbody></table></div>`;
}

function mcpHealthDot(s) {
  const cls = s.health === "ok" ? "ok" : s.health === "error" ? "error" : "unknown";
  return `<span class="mcp-dot ${cls}" title="${esc(s.detail || "Healthy")}"></span>`;
}

/* A folder's git state as caption pieces: branch, ahead/behind, a Local
 * changes badge; "Not a git repo" when it isn't one. */
function mcpGitPieces(git) {
  if (!git) return "";
  if (!git.is_repo) return `<span>Not a git repo</span>`;
  const counts = [git.ahead > 0 ? `↑${git.ahead}` : "", git.behind > 0 ? `↓${git.behind}` : ""].filter(Boolean).join(" ");
  return `<span class="mcp-secondary-text">${esc(git.branch || "detached HEAD")}</span>${
    counts ? `<span class="mcp-secondary-text num">${counts}</span>` : ""}${
    git.has_changes ? `<span>·</span><span class="badge">Local changes</span>` : ""}`;
}

function mcpServerRow(s, folders, harnesses, showHealth) {
  const differs = mcpConflicts(s, harnesses);
  const warn = s.secrets_missing && s.secrets_missing.length
    ? `<span class="mcp-warn" title="${esc(`Needs a value for ${s.secrets_missing.join(", ")}`)}">${mcpSvg("warn")}</span>` : "";
  let secondary = `<span>${esc(s.transport)}</span>`;
  if (folders.length) {
    secondary += `<span>·</span><span class="badge mcp-checkout" title="Runs from a folder in your MCP servers folder">checkout</span>`;
    secondary += `<span>·</span><span class="mono" title="${esc(folders.map((f) => f.path).join("\n"))}">${esc(folders.map((f) => f.name).join(", "))}</span>`;
    if (folders.length === 1) secondary += `<span>·</span>${mcpGitPieces(folders[0].git)}`;
  }
  const cells = harnesses.map((h) => {
    const g = mcpGroupOf(s, h);
    if (g < 0) return `<td class="mcp-cell"><span class="mcp-absent" title="${esc(`Not in ${Fmt.mark(h).name}`)}">—</span></td>`;
    if (differs) {
      return `<td class="mcp-cell"><span class="mcp-letter${g === 0 ? "" : " other"}" title="Definition ${mcpLetter(g)}">${mcpLetter(g)}</span></td>`;
    }
    return `<td class="mcp-cell">${mcpSvg("check", "mcp-tick")}</td>`;
  }).join("");
  return `<tr class="mcp-row" data-mcp-server="${esc(s.name)}">
    <td class="mcp-name-col"><div class="mcp-name-cell">
      ${showHealth && s.health ? mcpHealthDot(s) : ""}
      <div class="mcp-name-main">
        <button class="mcp-open" aria-label="${esc(`${s.name}, in ${mcpNames(s.harnesses)}`)}"><span class="mcp-title">${esc(s.name)}</span>${warn}</button>
        <div class="mcp-secondary caption">${secondary}</div>
      </div></div></td>${cells}</tr>`;
}

function mcpFolderRow(f, harnesses, hubKnown) {
  return `<tr class="mcp-folder-row"><td class="mcp-name-col"><div class="mcp-name-main">
      <span class="mcp-folder-name" title="${esc(f.path)}">${esc(f.name)}</span>
      <div class="mcp-secondary caption">${hubKnown ? "<span>Not in the hub</span><span>·</span>" : ""}${mcpGitPieces(f.git)}</div>
    </div></td>${harnesses.map(() => "<td></td>").join("")}</tr>`;
}

function mcpBind() {
  const p = mcpPage;
  for (const tab of document.querySelectorAll("[data-mcp-filter]")) {
    tab.onclick = () => { p.filter = tab.dataset.mcpFilter; paintMcp(); };
  }
  for (const chip of document.querySelectorAll("[data-mcp-chip]")) {
    chip.onclick = () => {
      const h = chip.dataset.mcpChip;
      if (p.hidden.has(h)) p.hidden.delete(h); else p.hidden.add(h);
      mcpSaveHidden();
      paintMcp();
    };
  }
  for (const btn of document.querySelectorAll("[data-mcp-fold]")) {
    btn.onclick = () => {
      const t = btn.dataset.mcpFold;
      if (p.opened.has(t)) p.opened.delete(t); else p.opened.add(t);
      paintMcp();
    };
  }
  for (const row of document.querySelectorAll("[data-mcp-server]")) {
    row.onclick = () => {
      const s = p.data.servers.find((x) => x.name === row.dataset.mcpServer);
      if (!s) return;
      p.sheet = null; // a row opens a fresh sheet, as the Mac app's does
      mcpOpenSheet(s);
    };
  }
}

/* ---------------------------------------------------------------- sheet */

/* McpModel.sources: each definition's first harness, then the hub. */
function mcpSources(s) {
  const out = mcpGroups(s).map((g) => g[0]).filter(Boolean);
  if (s.in_hub && !out.includes("quiver")) out.push("quiver");
  return out;
}

/* McpModel.targets: harnesses without the server (picked by default) and
 * ones with a different definition (opt-in, since that replaces theirs).
 * From the hub every harness with the server counts as different. */
/* Whether the definition copied from ``source`` is reached at a URL. */
const mcpSourceIsRemote = (s, source) => (s.remote_in ? s.remote_in.includes(source) : mcpIsRemote(s));

function mcpSyncTargets(s, source, harnesses) {
  // A config that launches local servers only can't take a URL one.
  const caps = (mcpPage.data && mcpPage.data.caps) || {};
  if (mcpSourceIsRemote(s, source)) harnesses = harnesses.filter((h) => !caps[h] || caps[h].remote);
  if (source === "quiver") {
    return { missing: harnesses.filter((h) => !s.harnesses.includes(h)),
             differing: harnesses.filter((h) => s.harnesses.includes(h)) };
  }
  const mine = mcpGroupOf(s, source);
  const missing = [], differing = [];
  for (const h of harnesses) {
    if (h === source) continue;
    const g = mcpGroupOf(s, h);
    if (g < 0) missing.push(h);
    else if (g !== mine) differing.push(h);
  }
  return { missing, differing };
}

const mcpSourceName = (src) => (src === "quiver" ? "Quiver hub" : Fmt.mark(src).name);

/* McpSyncReport.sentence: "Added to cursor and cline. Updated codex." */
function mcpSentence(report) {
  const names = (list) => (list.length < 2 ? list.join("") : `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`);
  const with_ = (key) => report.targets.filter((t) => t[key].length).map((t) => t.name);
  const added = with_("added"), updated = with_("updated"), skipped = with_("skipped");
  const dry = report.dry_run, parts = [];
  if (added.length) parts.push(`${dry ? "Would add to " : "Added to "}${names(added)}.`);
  if (updated.length) parts.push(`${dry ? "Would update " : "Updated "}${names(updated)}.`);
  if (skipped.length) parts.push(`${dry ? "Would leave " : "Left "}${names(skipped)} as it was.`);
  return parts.length ? parts.join(" ") : "Nothing to change.";
}

/* The sheet's own state, kept across a repaint of the page (a check, a
 * refresh, or a sync landing) and dropped when another row opens it. */
function mcpSheetState(s) {
  let st = mcpPage.sheet;
  if (!st || st.name !== s.name) {
    st = mcpPage.sheet = { name: s.name, source: null, chosen: new Set(), replace: new Set(),
      preview: null, done: null, error: null, running: false, confirming: false, removing: false,
      // Secrets: values fetched for Reveal (cleared when the sheet closes,
      // and closing bumps revealGen so an answer still on its way is
      // dropped), names being replaced, drafts as typed, the one saving,
      // its error. Maps, since ${constructor} is a valid name.
      revealed: new Map(), revealGen: 0, replacing: new Set(), drafts: new Map(),
      savingSecret: null, secretError: null };
  }
  const sources = mcpSources(s);
  if (!sources.includes(st.source)) {
    st.source = sources[0] || "";
    mcpResetChoice(st, s);
  }
  return st;
}

/* ServerSheet.resetChoice: harnesses that use MCP and lack the server;
 * idle ones and different definitions stay opt-in. */
function mcpResetChoice(st, s) {
  const d = mcpPage.data;
  const compared = mcpActive(d).filter((h) => !mcpPage.hidden.has(h));
  st.chosen = new Set(mcpSyncTargets(s, st.source, d.harnesses).missing.filter((h) => compared.includes(h)));
  st.replace = new Set();
  st.preview = null;
  st.confirming = false;
}

/* ServerSheet.run's picks, recomputed from the latest list: a harness
 * that gained its own definition since it was ticked is neither added nor
 * replaced. Inventory order, as McpModel.sync passes them. */
function mcpRequest(s, st) {
  const t = mcpSyncTargets(s, st.source, mcpPage.data.harnesses);
  const adding = t.missing.filter((h) => st.chosen.has(h));
  const replacing = t.differing.filter((h) => st.chosen.has(h) && st.replace.has(h));
  const order = mcpPage.data.harnesses;
  const targets = [...adding, ...replacing].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return { server: s.name, source: st.source, targets, replace: replacing };
}

/* app.js's apiPost: JSON with its authHeaders (the bearer when one
 * is stored, and the pair header an owner's tailnet device signs in with). */
const mcpPost = (action, body) => apiPost(`/v1/app/mcp/${action}`, body);

/* The list a sync answered with becomes the page's, keeping the health
 * the old list had (the write does not probe). */
function mcpAdopt(page) {
  const old = mcpPage.data;
  if (old && mcpHasHealth()) {
    const by = new Map(old.servers.map((x) => [x.name, x]));
    for (const x of page.servers) {
      const o = by.get(x.name);
      if (o && o.health != null) { x.health = o.health; x.detail = o.detail; }
    }
    page.health = true;
  }
  mcpPage.seq++; // a read still in flight is older than this
  mcpPage.loading = false;
  mcpPage.checking = false;
  mcpPage.data = page;
  mcpPage.at = Date.now();
  mcpPage.failure = null;
}

/* Preview: the dry run. With `thenConfirm`, Sync's first step: the plan
 * is read fresh and the inline confirm shows the files it would write. */
async function mcpPreview(s, thenConfirm) {
  const st = mcpPage.sheet;
  const request = mcpRequest(s, st);
  st.running = true;
  st.error = null;
  st.confirming = false;
  // One confirm at a time: a sync on show closes Remove's.
  st.removing = false;
  mcpOpenSheet(s);
  const { status, data } = await mcpPost("preview", request);
  if (mcpPage.sheet !== st) return;
  st.running = false;
  if (status === 401) { renderGate(); return; }
  if (status !== 200 || !data) {
    st.error = failText(status, data);
    st.preview = null;
    if (data && data.error && data.error.code === "target_changed") mcpLoad(mcpHasHealth());
  } else {
    st.preview = { ...data, request: JSON.stringify(request) };
    st.error = data.error || null;
    st.confirming = !!thenConfirm && data.files.length > 0;
  }
  mcpRepaintSheet(s);
}

/* Sync: writes exactly the previewed plan; the hub refuses (409) when any
 * config it read for the preview has changed since. */
async function mcpSync(s) {
  const st = mcpPage.sheet;
  const plan = st.preview;
  if (!plan) return;
  const request = { ...JSON.parse(plan.request), fingerprint: plan.fingerprint };
  st.running = true;
  st.error = null;
  mcpOpenSheet(s);
  const { status, data } = await mcpPost("sync", request);
  st.running = false;
  st.confirming = false;
  if (status === 401) { renderGate(); return; }
  if (status !== 200 || !data) {
    st.error = failText(status, data);
    st.preview = null;
    // Something changed on disk, or a write failed part way: read again.
    if (mcpPage.sheet === st) mcpRepaintSheet(s);
    mcpLoad(mcpHasHealth());
    return;
  }
  st.done = data;
  st.preview = null;
  st.error = data.error || null;
  mcpAdopt(data.page);
  if (mcpOnPage()) paintMcp();
}

/* A word as a shell would need it typed: bare when safe, else single
 * quotes, so a copied "my server" stays one argument. */
const mcpShellQuote = (word) => (/^[A-Za-z0-9_@%+=:,.\/-]+$/.test(word) ? word : `'${word.replace(/'/g, "'\\''")}'`);

/* Settings › Providers › Inference's row, for each ${NAME} the hub's
 * definition refers to: a set one shows masked with Reveal, Copy and
 * Replace; a missing one, or one being replaced, a password field and
 * Save. A value reaches the page only when Reveal or Copy asks for it. */
function mcpSecretsHtml(s, st) {
  const list = s.secrets || [];
  if (!list.length) return "";
  const missing = list.filter((x) => !x.set).length;
  const rows = list.map((x) => {
    const name = esc(x.name);
    if (!x.set || st.replacing.has(x.name)) {
      const saving = st.savingSecret === x.name;
      return `<div class="mcp-secret-row"><code class="mcp-secret-name">${name}</code>
        <input class="field mcp-secret-input" type="password" autocomplete="new-password" spellcheck="false"
          data-mcp-secret-draft="${name}" placeholder="${x.set ? "New value" : "Paste the value"}" aria-label="Value for ${name}"
          value="${esc(st.drafts.get(x.name) || "")}"${saving ? " disabled" : ""}>
        ${x.set ? `<button type="button" class="mcp-sheet-button" data-mcp-secret-cancel="${name}"${saving ? " disabled" : ""}>Cancel</button>` : ""}
        <button type="button" class="mcp-sheet-button" data-mcp-secret-save="${name}"${
          saving || !(st.drafts.get(x.name) || "").trim() || st.savingSecret ? " disabled" : ""}>${saving ? "Saving…" : "Save"}</button></div>`;
    }
    const shown = st.revealed.get(x.name);
    return `<div class="mcp-secret-row"><code class="mcp-secret-name">${name}</code>
      <span class="mcp-secret-value${shown != null ? " revealed" : ""}">${esc(shown != null ? shown : x.masked || "")}</span>
      <button type="button" class="icon-button mcp-secret-icon" data-mcp-secret-reveal="${name}"
        aria-label="${shown != null ? "Hide" : "Show"} the value" title="${shown != null ? "Hide" : "Show"} the value">${mcpSvg(shown != null ? "eyeOff" : "eye")}</button>
      <button type="button" class="icon-button mcp-secret-icon" data-mcp-secret-copy="${name}" aria-label="Copy the value" title="Copy the value">${mcpSvg("copy")}</button>
      <button type="button" class="mcp-sheet-button" data-mcp-secret-replace="${name}">Replace</button></div>`;
  }).join("");
  return `<hr class="mcp-rule">
    <div class="card-title">${missing ? (missing === 1 ? "Needs a value" : "Needs values") : "Secrets"}</div>
    <div class="mcp-secret-rows">${rows}</div>
    ${st.secretError ? `<p class="error" role="alert">${esc(st.secretError)}</p>` : ""}
    <p class="caption">Saved to ~/.quiver/secrets/mcp/.api_keys on ${esc((mcpPage.data && mcpPage.data.machine) || "the hub")}, never versioned. Sync fills these in when it writes a harness config.</p>`;
}

/* One value, fetched only when asked for. */
async function mcpSecretValue(name, st) {
  const { status, data } = await mcpPost("secret_value", { name });
  if (status === 200 && data && typeof data.value === "string") return data.value;
  st.secretError = failText(status, data);
  return null;
}

function mcpBindSecrets(sheet, s, st) {
  for (const input of sheet.querySelectorAll("[data-mcp-secret-draft]")) {
    input.oninput = () => {
      st.drafts.set(input.dataset.mcpSecretDraft, input.value);
      const save = sheet.querySelector(`[data-mcp-secret-save="${CSS.escape(input.dataset.mcpSecretDraft)}"]`);
      if (save) save.disabled = !input.value.trim() || !!st.savingSecret;
    };
    input.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); mcpSaveSecret(s, input.dataset.mcpSecretDraft); }
    };
  }
  for (const btn of sheet.querySelectorAll("[data-mcp-secret-save]")) btn.onclick = () => mcpSaveSecret(s, btn.dataset.mcpSecretSave);
  for (const btn of sheet.querySelectorAll("[data-mcp-secret-replace]")) {
    btn.onclick = () => {
      const name = btn.dataset.mcpSecretReplace;
      st.replacing.add(name);
      st.revealed.delete(name);
      st.secretError = null;
      mcpOpenSheet(s);
      const input = sheet.querySelector(`[data-mcp-secret-draft="${CSS.escape(name)}"]`);
      if (input) input.focus();
    };
  }
  for (const btn of sheet.querySelectorAll("[data-mcp-secret-cancel]")) {
    btn.onclick = () => { st.replacing.delete(btn.dataset.mcpSecretCancel); st.drafts.delete(btn.dataset.mcpSecretCancel); mcpOpenSheet(s); };
  }
  for (const btn of sheet.querySelectorAll("[data-mcp-secret-reveal]")) {
    btn.onclick = async () => {
      const name = btn.dataset.mcpSecretReveal;
      st.secretError = null;
      if (st.revealed.has(name)) st.revealed.delete(name);
      else {
        const gen = st.revealGen;
        const value = await mcpSecretValue(name, st);
        // Closed since the click: the sheet forgot its values, so stay hidden.
        if (gen !== st.revealGen) return;
        if (value != null) st.revealed.set(name, value);
      }
      if (mcpPage.sheet === st) mcpRepaintSheet(s);
    };
  }
  for (const btn of sheet.querySelectorAll("[data-mcp-secret-copy]")) {
    const name = btn.dataset.mcpSecretCopy;
    Fmt.copyTick(btn, async () => {
      st.secretError = null;
      const value = st.revealed.get(name) ?? await mcpSecretValue(name, st);
      // The fetch failed and set secretError: repaint so it shows.
      if (value == null && mcpPage.sheet === st) mcpRepaintSheet(s);
      return value;
    }, () => {
      st.secretError = "The browser didn't allow copying. Use Show and copy it by hand.";
      if (mcpPage.sheet === st) mcpRepaintSheet(s);
    });
  }
}

/* set_secret on the hub, as swe mcp secret set NAME does. The field clears either way; the
 * page repaints from the answer. */
async function mcpSaveSecret(s, name) {
  const st = mcpPage.sheet;
  const value = (st.drafts.get(name) || "").trim();
  if (!value || st.savingSecret) return;
  st.savingSecret = name;
  st.secretError = null;
  mcpOpenSheet(s);
  const { status, data } = await mcpPost("secret_set", { name, value });
  st.savingSecret = null;
  st.drafts.delete(name);
  if (status === 401) {
    if (mcpOnPage()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    st.secretError = failText(status, data);
    if (mcpPage.sheet === st) mcpRepaintSheet(s);
    return;
  }
  st.replacing.delete(name);
  st.revealed.delete(name);
  // A new value can change what Preview would write: show it again, and
  // the Sync controls an earlier sync's report replaced.
  st.preview = null;
  st.done = null;
  st.confirming = false;
  mcpAdopt(data.page);
  if (mcpOnPage()) paintMcp();
}

/* Where Remove takes the server out of: every harness that has it, and the
 * hub (McpServersPage.removeServer). The hub refuses if any has changed. */
const mcpRemovePlaces = (s) => [...s.harnesses, ...(s.in_hub ? ["quiver"] : [])];

function mcpRemoveConfirm(s) {
  const places = mcpRemovePlaces(s);
  const where = (mcpPage.data && mcpPage.data.machine) || "the hub";
  const names = places.map((p) => (p === "quiver" ? "Quiver hub" : Fmt.mark(p).name)).join(", ");
  return `<div class="mcp-confirm mcp-remove-confirm" role="group" aria-label="Remove ${esc(s.name)}?">
    <p>Remove ${esc(s.name)}? It goes from ${esc(names)} on ${esc(where)}.</p>
    <pre class="command">swe mcp remove ${esc(mcpShellQuote(s.name))} --from=${esc(mcpShellQuote(places.join(",")))}</pre>
    <div class="sheet-actions"><button type="button" class="mcp-sheet-button" id="mcp-remove-cancel">Cancel</button>
      <button type="button" class="mcp-sheet-button primary danger" id="mcp-remove-go">Remove</button></div></div>`;
}

/* swe mcp remove on the hub; the sheet closes on success, as the app's
 * does, and the page repaints from the answer. */
async function mcpRemove(s) {
  const st = mcpPage.sheet;
  st.running = true;
  st.error = null;
  mcpOpenSheet(s);
  const { status, data } = await mcpPost("remove", { server: s.name, from: mcpRemovePlaces(s) });
  st.running = false;
  st.removing = false;
  if (status === 401) {
    if (mcpOnPage()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    st.error = failText(status, data);
    if (mcpPage.sheet === st) mcpRepaintSheet(s);
    // Most refusals mean the configs moved on: read them again.
    if (status) mcpLoad(mcpHasHealth());
    return;
  }
  const sheet = document.getElementById("mcp-sheet");
  if (sheet && sheet.open && sheet.dataset.server === s.name) sheet.close();
  if (mcpPage.sheet === st) mcpPage.sheet = null;
  mcpPage.notice = `Removed ${s.name} from ${data.removed.map((p) => (p === "quiver" ? "Quiver hub" : Fmt.mark(p).name)).join(", ")}.`;
  mcpAdopt(data.page);
  if (mcpOnPage()) paintMcp();
}

function mcpRepaintSheet(s) {
  const sheet = document.getElementById("mcp-sheet");
  if (!sheet || !sheet.open || sheet.dataset.server !== s.name) return;
  const fresh = mcpPage.data.servers.find((x) => x.name === s.name) || s;
  mcpOpenSheet(fresh);
}

/* A preview's or a sync's plan, harness by harness: the file, and what
 * that config gains, changes or loses. Only names: no value is sent. */
function mcpPlanHtml(plan, done) {
  const keyList = (diff, word) => {
    const bits = [
      ...diff.added.map((k) => `<span class="mcp-key gain" title="${esc(`Gains ${k}`)}">+ ${esc(k)}</span>`),
      ...diff.removed.map((k) => `<span class="mcp-key lose" title="${esc(`Loses ${k}`)}">− ${esc(k)}</span>`),
      ...diff.changed.map((k) => `<span class="mcp-key change" title="${esc(`${k} gets a new value`)}">~ ${esc(k)}</span>`),
    ];
    return bits.length ? `<div class="mcp-plan-line"><span class="caption">${word}</span><span class="mcp-keys">${bits.join("")}</span></div>` : "";
  };
  const rows = plan.harnesses.map((h) => {
    const mark = Fmt.mark(h.harness);
    const label = {
      add: done ? "Added" : "Gains it",
      replace: done ? "Replaced" : "Replaces its own definition",
      skip: done ? "Left as it was" : "Would be left as it was",
      not_run: "Not run",
    }[h.outcome] || h.outcome;
    let lines = "";
    if (h.outcome === "replace" && h.command_changes) {
      lines += `<div class="mcp-plan-line"><span class="caption">Was</span><code class="mcp-plan-cmd lose">${esc(h.before.summary)}</code></div>`;
      lines += `<div class="mcp-plan-line"><span class="caption">Now</span><code class="mcp-plan-cmd">${esc(h.after.summary)}</code></div>`;
    } else if (h.outcome === "replace") {
      lines += `<div class="mcp-plan-line"><span class="caption">Command</span><span class="caption">Same command line</span></div>`;
    } else if (h.outcome === "add" && h.after) {
      lines += `<div class="mcp-plan-line"><span class="caption">Runs</span><code class="mcp-plan-cmd">${esc(h.after.summary)}</code></div>`;
    }
    if (h.outcome === "add" || h.outcome === "replace") {
      lines += keyList(h.env, "Env vars") + keyList(h.headers, "Headers");
      if (h.kept.length) lines += `<div class="mcp-plan-line"><span class="caption">Keeps</span><span class="caption">its own ${esc(h.kept.join(", "))}</span></div>`;
    }
    return `<li class="mcp-plan-row">
      <div class="mcp-plan-head">${mcpMarkHtml(h.harness)}<span class="mcp-plan-name">${esc(mark.name)}</span>
        <span class="badge${h.outcome === "replace" ? " warn" : ""}">${esc(label)}</span></div>
      <div class="mcp-plan-line"><span class="caption">File</span><code class="mcp-plan-path">${esc(h.path)}</code>${
        h.creates ? `<span class="caption">(created)</span>` : ""}${
        h.unverified ? `<span class="caption warn" title="swe has no verified MCP config path for this harness">optimistic default path</span>` : ""}</div>
      ${lines}</li>`;
  }).join("");
  return `<ul class="mcp-plan">${rows}</ul>`;
}

/* Sync's inline confirm: the files it will write, by name, and whose
 * own definition it replaces (the Mac app's "Replace their definitions?"). */
function mcpConfirmHtml(s, st) {
  const plan = st.preview;
  const writes = plan.harnesses.filter((h) => h.outcome === "add" || h.outcome === "replace");
  const replacing = writes.filter((h) => h.outcome === "replace").map((h) => h.harness);
  const n = writes.length;
  return `<div class="mcp-confirm" role="group" aria-label="Confirm sync">
    <p class="mcp-confirm-title">${replacing.length ? "Replace their definitions?" : `Write ${n === 1 ? "this file" : `these ${n} files`}?`}</p>
    <p class="caption">Sync writes ${n === 1 ? "this file" : `these ${n} files`}${mcpPage.data.machine ? ` on ${esc(mcpPage.data.machine)}` : ""}:</p>
    <ul class="mcp-confirm-files">${writes.map((h) => `<li><code>${esc(h.path)}</code><span class="caption">${esc(Fmt.mark(h.harness).name)}: ${
      h.outcome === "replace" ? `replaces its own ${esc(s.name)}` : `adds ${esc(s.name)}`}${h.creates ? ", creates the file" : ""}</span></li>`).join("")}</ul>
    ${replacing.length ? `<p class="caption">${esc(mcpNames(replacing))} will get ${esc(mcpSourceName(st.source))}'s definition of ${esc(s.name)} instead of their own.</p>` : ""}
    <div class="sheet-actions">
      <button type="button" class="mcp-sheet-button" id="mcp-confirm-cancel"${st.running ? " disabled" : ""}>Cancel</button>
      <button type="button" class="mcp-sheet-button primary${replacing.length ? " danger" : ""}" id="mcp-confirm-go"${st.running ? " disabled" : ""}>${
        replacing.length ? "Replace" : `Write ${n === 1 ? "1 file" : `${n} files`}`}</button>
    </div></div>`;
}

/* One server: what it is, who has which definition, and a sync. */
function mcpOpenSheet(s) {
  const sheet = document.getElementById("mcp-sheet");
  if (!sheet) return;
  const d = mcpPage.data;
  const st = mcpSheetState(s);
  const active = mcpActive(d).filter((h) => !mcpPage.hidden.has(h));
  const groups = mcpGroups(s);
  const fact = (label, value) => `<div class="field-row"><span class="caption">${esc(label)}</span><span>${value}</span></div>`;
  const marks = (list) => `<span class="mcp-marks">${list.map((h) => {
    const m = Fmt.mark(h);
    return `<span class="mcp-mark">${m.icon ? Fmt.logo(m.icon) : ""}<span>${esc(m.name)}</span></span>`;
  }).join("")}</span>`;
  const health = s.health
    ? `${mcpHealthDot(s)}<span class="caption">${esc(s.detail || "Healthy")}</span>` : "";
  const missing = mcpMissing(s, active);
  const facts = [
    ...groups.map((g, i) => fact(groups.length > 1 ? `Definition ${mcpLetter(i)}` : "In", marks(g))),
    missing.length ? fact("Missing from", marks(missing)) : "",
  ].join("");
  const secrets = mcpSecretsHtml(s, st);

  const canSync = !!s.name && !s.name.includes(",");
  // Sync from the hub is refused by swe while a ${NAME} has no value.
  const blocked = st.source === "quiver" && (s.secrets_missing || []).length > 0;
  const targets = mcpSyncTargets(s, st.source, d.harnesses);
  const request = mcpRequest(s, st);
  const chosenAny = request.targets.length > 0;
  let form = "";
  if (!canSync) {
    form = `<p class="caption">swe can't sync a server whose name has a comma on its own. Use swe mcp sync on the hub.</p>`;
  } else {
    const sources = mcpSources(s);
    if (sources.length > 1) {
      form += `<div class="field-row mcp-source-row"><label class="caption" for="mcp-source">Copy from</label><span><select class="field" id="mcp-source"${
        st.running || st.done ? " disabled" : ""}>${sources.map((src) => `<option value="${esc(src)}"${src === st.source ? " selected" : ""}>${esc(mcpSourceName(src))}</option>`).join("")}</select></span></div>`;
    } else if (sources.length) {
      form += `<div class="fields">${fact("Copy from", st.source === "quiver" ? `<span>Quiver hub</span>` : marks([st.source]))}</div>`;
    }
    if (!targets.missing.length && !targets.differing.length) {
      form += `<p class="caption">${st.source === "quiver" ? "Every harness already has this server." : "Every harness already has this definition."}</p>`;
    }
    const toggle = (h, replaces) => {
      const m = Fmt.mark(h);
      const label = replaces ? `${m.name}, replaces its own definition` : m.name;
      return `<label class="mcp-target">${m.icon ? Fmt.logo(m.icon) : `<span class="mcp-logo-gap"></span>`}<span class="mcp-target-name">${esc(m.name)}</span>${
        replaces ? `<span class="caption">replaces its own definition</span>` : ""}<input type="checkbox" class="mcp-switch" role="switch" data-mcp-target="${esc(h)}"${
        replaces ? ` data-replaces="1"` : ""} aria-label="${esc(label)}"${st.chosen.has(h) ? " checked" : ""}${st.running || st.done ? " disabled" : ""}></label>`;
    };
    const rows = [...targets.missing.map((h) => toggle(h, false)), ...targets.differing.map((h) => toggle(h, true))];
    if (rows.length) form += `<div class="mcp-targets">${rows.join("")}</div>`;
  }
  const report = st.done || st.preview;
  let result = "";
  if (report) {
    result = `<div class="mcp-result"><p class="mcp-sentence">${mcpSvg(st.done ? "check" : "eye")}<span>${esc(mcpSentence(report.report))}</span></p>
      ${mcpPlanHtml(report, !!st.done)}</div>`;
  }
  const error = st.error ? `<p class="error">${esc(st.error)}</p>` : "";
  const confirm = st.confirming && st.preview ? mcpConfirmHtml(s, st) : "";
  const disabled = !chosenAny || st.running || blocked;
  const removeConfirm = st.removing && !st.running ? mcpRemoveConfirm(s) : "";
  const actions = `${removeConfirm}<div class="sheet-actions mcp-sync-actions">
      ${st.done ? "" : `<button type="button" class="mcp-sheet-button mcp-remove" id="mcp-remove" aria-expanded="${st.removing}"${st.running ? " disabled" : ""}>Remove…</button>`}
      ${st.running ? `<span class="caption">Working…</span>` : ""}
      ${blocked ? `<span class="caption warn">Set the values above first.</span>` : ""}
      <span class="mcp-spacer"></span>
      <button class="mcp-sheet-button" value="close">${st.done ? "Close" : "Cancel"}</button>
      ${!st.done && canSync ? `<button type="button" class="mcp-sheet-button" id="mcp-preview"${disabled ? " disabled" : ""}>Preview</button>
      <button type="button" class="mcp-sheet-button primary" id="mcp-sync"${disabled || st.confirming ? " disabled" : ""}>Sync</button>` : ""}
    </div>`;

  sheet.innerHTML = `<form method="dialog" class="sheet-body">
    <div class="sheet-title"><span class="mcp-sheet-name">${esc(s.name)}</span><span class="badge">${esc(mcpTypeLabel(s))}</span>${health}</div>
    ${s.summary ? `<pre class="command mcp-summary">${esc(s.summary)}</pre>` : ""}
    ${groups.length > 1 ? `<p class="caption">Command, URL and key names are definition A's.</p>` : ""}
    ${s.env_keys.length || s.header_keys.length ? `<div class="fields">
      ${s.env_keys.length ? fact("Env vars", esc(s.env_keys.join(", "))) : ""}
      ${s.header_keys.length ? fact("Headers", esc(s.header_keys.join(", "))) : ""}</div>` : ""}
    <hr class="mcp-rule">
    <div class="fields">${facts || fact("In", `<span class="caption">Only the Quiver hub</span>`)}</div>
    ${secrets}
    <hr class="mcp-rule">
    <div class="card-title">Sync</div>
    ${form}${result}${error}${confirm}
    ${actions}
  </form>`;
  sheet.dataset.server = s.name;
  // A closed sheet forgets every value it showed and every draft.
  sheet.onclose = () => {
    mcpForgetSecrets(st);
    // The closed dialog's markup still holds revealed values and pasted
    // drafts; empty it so the page keeps none of them.
    sheet.replaceChildren();
  };
  mcpBindSheet(sheet, s, st);
  if (!sheet.open) sheet.showModal();
}

/* Drops the secret values a server's sheet revealed or had typed into it;
 * bumping revealGen drops a Reveal answer still on its way. */
function mcpForgetSecrets(st) {
  st.revealed = new Map(); st.revealGen++; st.drafts = new Map(); st.replacing.clear(); st.secretError = null;
}

/* Leaving the page takes both sheets away with #panel, but no close event
 * fires, so their state would outlive them: the Add sheet would open again
 * on the next visit with its token and secret values filled in, and a
 * server's sheet would show its revealed values again. Forget them as a
 * close does. */
window.addEventListener("hashchange", () => {
  if (mcpOnPage()) return;
  mcpPage.add = null;
  if (mcpPage.sheet) mcpForgetSecrets(mcpPage.sheet);
});

function mcpBindSheet(sheet, s, st) {
  const source = sheet.querySelector("#mcp-source");
  if (source) {
    source.onchange = () => { st.source = source.value; st.error = null; mcpResetChoice(st, s); mcpOpenSheet(s); };
  }
  for (const box of sheet.querySelectorAll("[data-mcp-target]")) {
    box.onchange = () => {
      const h = box.dataset.mcpTarget;
      if (box.checked) st.chosen.add(h); else st.chosen.delete(h);
      // Only a harness ticked while shown as having its own definition
      // may have it replaced.
      if (box.checked && box.dataset.replaces) st.replace.add(h); else if (!box.checked) st.replace.delete(h);
      st.preview = null;
      st.confirming = false;
      st.error = null;
      mcpOpenSheet(s);
    };
  }
  const preview = sheet.querySelector("#mcp-preview");
  if (preview) preview.onclick = () => mcpPreview(s, false);
  const sync = sheet.querySelector("#mcp-sync");
  if (sync) {
    sync.onclick = () => {
      // A preview of exactly these picks can be confirmed as it is; any
      // other is read again first, so the confirm names what is on disk.
      const current = st.preview && st.preview.request === JSON.stringify(mcpRequest(s, st));
      if (current && st.preview.files.length) { st.confirming = true; st.removing = false; mcpOpenSheet(s); }
      else mcpPreview(s, true);
    };
  }
  mcpBindSecrets(sheet, s, st);
  const remove = sheet.querySelector("#mcp-remove");
  if (remove) remove.onclick = () => {
    st.removing = !st.removing;
    st.confirming = false;
    mcpOpenSheet(s);
    const next = sheet.querySelector(st.removing ? "#mcp-remove-go" : "#mcp-remove");
    if (next) next.focus();
  };
  const removeCancel = sheet.querySelector("#mcp-remove-cancel");
  if (removeCancel) removeCancel.onclick = () => { st.removing = false; mcpOpenSheet(s); sheet.querySelector("#mcp-remove").focus(); };
  const removeGo = sheet.querySelector("#mcp-remove-go");
  if (removeGo) removeGo.onclick = () => mcpRemove(s);
  const cancel = sheet.querySelector("#mcp-confirm-cancel");
  if (cancel) cancel.onclick = () => { st.confirming = false; mcpOpenSheet(s); };
  const go = sheet.querySelector("#mcp-confirm-go");
  if (go) { go.onclick = () => mcpSync(s); go.focus(); }
}

registerPage("mcp", renderMcp);

/* ----------------------------------------------------------- add server */

// McpRedaction.secretFlagEndings: a key ending in one is a secret by default.
const MCP_SECRET_ENDINGS = ["token", "key", "secret", "password", "passwd", "credential", "credentials",
  "auth", "authorization", "header"];

/* mcpCommandWords: the command line split as a shell would, quotes
 * grouping and unquoting; null for an unclosed quote. */
function mcpCommandWords(line) {
  const words = [];
  let word = "", open = false, quote = " ";
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\") {
      if (quote === "'") { word += "\\"; open = true; continue; }
      if (i + 1 >= line.length) { word += "\\"; continue; }
      const next = line[++i];
      word += quote === '"' && !'"\\$`'.includes(next) ? "\\" + next : next;
      open = true;
    } else if (ch === "'") {
      if (quote === '"') { word += "'"; open = true; continue; }
      if (quote === "'") quote = " "; else if (!open || quote === " ") quote = "'";
      open = true;
    } else if (ch === '"') {
      if (quote === "'") { word += '"'; open = true; continue; }
      if (quote === '"') quote = " "; else if (quote === " ") quote = '"';
      open = true;
    } else if (ch === " " || ch === "\t" || ch === "\n") {
      if (quote === " ") { if (open) { words.push(word); word = ""; open = false; } }
      else { word += ch; open = true; }
    } else {
      word += ch;
      open = true;
    }
  }
  if (quote !== " ") return null;
  if (open) words.push(word);
  return words;
}

/* mcpIsSecretName: whether a key looks like it holds a credential. */
function mcpIsSecretName(key) {
  const bare = key.replace(/[-_]/g, "").toLowerCase();
  return !!bare && MCP_SECRET_ENDINGS.some((end) => bare.endsWith(end));
}

/* mcpSecretName: the ${NAME} a secret is stored under, as the hub names it. */
function mcpSecretName(server, key) {
  const name = `${server}_${key}`.replace(/[^A-Za-z0-9]/g, "_").toUpperCase().replace(/_+/g, "_").replace(/^_|_$/g, "");
  return /^[0-9]/.test(name) ? "MCP_" + name : name;
}

const MCP_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.@-]*$/;
let mcpVarSeq = 0;
const mcpNewVar = () => ({ id: ++mcpVarSeq, key: "", value: "", secret: false, touched: false, show: false });

function mcpStartAdd() {
  const d = mcpPage.data;
  if (!d) return;
  const active = mcpActive(d).filter((h) => !mcpPage.hidden.has(h));
  mcpPage.add = { name: "", kind: "command", line: "", url: "", sse: false, vars: [mcpNewVar()],
    signIn: "none", token: "", showToken: false,
    targets: new Set(active), force: false, working: false, error: null, picking: false };
  mcpOpenAdd("#mcp-add-name");
}

function mcpAddHint(a) {
  const words = mcpCommandWords(a.line);
  if (words == null) return { text: "Unclosed quote", problem: true };
  if (!words.length) return { text: "", problem: false };
  return { text: `command ${words[0]}${words.length > 1 ? ` · args ${words.slice(1).join(" ")}` : ""}`, problem: false };
}

function mcpAddReady(a) {
  if (a.working || !MCP_NAME_RE.test(a.name)) return false;
  if (a.kind === "command") { const w = mcpCommandWords(a.line); return !!(w && w.length); }
  return !!a.url.trim() && (a.signIn !== "bearer" || !!a.token.trim());
}

/* Remote's Sign-in: none, a bearer token (a password field, stored as a
 * secret and sent as Authorization: Bearer ${SERVER_TOKEN}), or OAuth,
 * which writes nothing: each harness signs in when it first connects. */
function mcpSignInHtml(a, dis) {
  const options = [["none", "None"], ["bearer", "Bearer token"], ["oauth", "OAuth"]];
  const ref = mcpSecretName(a.name || "SERVER", "TOKEN");
  let detail = "";
  if (a.signIn === "bearer") {
    detail = `<span class="mcp-add-row"><input class="field mono mcp-add-value" id="mcp-add-token" type="${a.showToken ? "text" : "password"}"
        autocomplete="new-password" spellcheck="false" placeholder="token" aria-label="Bearer token" value="${esc(a.token)}"${dis}>
      <button type="button" class="icon-button mcp-secret-icon" id="mcp-add-token-show" aria-label="${a.showToken ? "Hide" : "Show"} the token" title="${a.showToken ? "Hide" : "Show"} the token">${mcpSvg(a.showToken ? "eyeOff" : "eye")}</button></span>
      <span class="caption mcp-muted mono" id="mcp-add-token-ref">Sent as Authorization: Bearer \${${esc(ref)}}</span>`;
  } else if (a.signIn === "oauth") {
    detail = `<span class="caption mcp-muted">Nothing is saved: each harness signs in the first time it connects, if it supports OAuth.</span>`;
  }
  return `<div class="mcp-add-field"><span class="caption">Sign-in</span>
    <div class="tabs mcp-add-kind" role="tablist">${options.map(([id, title]) =>
      `<button type="button" class="tab${a.signIn === id ? " selected" : ""}" role="tab" aria-selected="${a.signIn === id}" data-mcp-add-signin="${id}"${dis}>${title}</button>`).join("")}</div>
    ${detail}</div>`;
}

/* Why a harness can't take this server, or "" when it can: a local-only
 * config can't hold a URL server, and some formats have no SSE marker. */
function mcpAddWhyNot(a, h) {
  if (a.kind !== "url") return "";
  const caps = (mcpPage.data.caps || {})[h];
  if (!caps) return "";
  if (!caps.remote) return "takes local servers only (add remote ones in the app)";
  if (a.sse && !caps.sse) return "can't use an SSE server";
  return "";
}

const mcpAddFits = (a, h) => !mcpAddWhyNot(a, h);

function mcpAddHtml() {
  const a = mcpPage.add, d = mcpPage.data;
  const dis = a.working ? " disabled" : "";
  const hint = mcpAddHint(a);
  const command = a.kind === "command";
  // The grid's words: Local runs on the machine whose harnesses launch it.
  const kinds = [["command", mcpLocalTitle(), "A command its harnesses launch"],
    ["url", "Remote", "A URL, reached from any machine"]];
  const server = `<div class="mcp-add-kinds" role="radiogroup" aria-label="Where the server runs">${kinds.map(([id, title, sub]) =>
      `<button type="button" class="mcp-add-kind-card${a.kind === id ? " selected" : ""}" role="radio" aria-checked="${a.kind === id}" data-mcp-add-kind="${id}"${dis}>
        <span class="mcp-add-kind-title">${esc(title)}</span><span class="caption">${esc(sub)}</span></button>`).join("")}</div>
    <label class="mcp-add-field"><span class="caption">Name</span>
      <input class="field" id="mcp-add-name" placeholder="${command ? "github" : "linear"}" autocomplete="off" spellcheck="false" value="${esc(a.name)}"${dis}></label>
    ${command ? `<label class="mcp-add-field"><span class="caption">Command</span>
        <input class="field mono" id="mcp-add-line" placeholder="npx -y @modelcontextprotocol/server-github" autocomplete="off" spellcheck="false" value="${esc(a.line)}"${dis}>
        <span class="caption mono${hint.problem ? " warn" : ""}" id="mcp-add-hint">${esc(hint.text)}</span></label>`
      : `<div class="mcp-add-field"><label class="caption" for="mcp-add-url">URL</label>
        <div class="mcp-add-row"><input class="field mono" id="mcp-add-url" placeholder="https://mcp.example.com/mcp" autocomplete="off" spellcheck="false" value="${esc(a.url)}"${dis}>
        <div class="tabs mcp-add-kind" role="tablist">${[[false, "HTTP"], [true, "SSE"]].map(([v, title]) =>
          `<button type="button" class="tab${a.sse === v ? " selected" : ""}" role="tab" aria-selected="${a.sse === v}" data-mcp-add-sse="${v}"${dis}>${title}</button>`).join("")}</div></div></div>
      ${mcpSignInHtml(a, dis)}`}`;
  const vars = a.vars.map((v) => {
    const hidden = v.secret && !v.show;
    const saved = v.secret && v.key.trim() && !v.value.startsWith("${")
      ? `<span class="caption mcp-muted mono" data-mcp-add-saved="${v.id}">saved as \${${esc(mcpSecretName(a.name || "SERVER", v.key.trim()))}}</span>` : "";
    return `<div class="mcp-add-var" data-mcp-add-var="${v.id}">
      <div class="mcp-add-var-row">
        <input class="field mono mcp-add-key" data-mcp-add-key="${v.id}" placeholder="${command ? "NAME" : "Header"}" aria-label="${command ? "Variable name" : "Header name"}" autocomplete="off" spellcheck="false" value="${esc(v.key)}"${dis}>
        <input class="field mono mcp-add-value" data-mcp-add-value="${v.id}" type="${hidden ? "password" : "text"}" autocomplete="${v.secret ? "new-password" : "off"}" spellcheck="false"
          placeholder="${v.secret ? "value" : "value or ${NAME}"}" aria-label="Value for ${esc(v.key || "this variable")}" value="${esc(v.value)}"${dis}>
        ${v.secret ? `<button type="button" class="icon-button mcp-secret-icon" data-mcp-add-show="${v.id}" aria-label="${v.show ? "Hide" : "Show"} the value" title="${v.show ? "Hide" : "Show"} the value">${mcpSvg(v.show ? "eyeOff" : "eye")}</button>` : ""}
        <label class="mcp-add-secret caption">Secret<input type="checkbox" class="mcp-switch" role="switch" data-mcp-add-secret="${v.id}" aria-label="Secret"${v.secret ? " checked" : ""}${dis}></label>
        <button type="button" class="icon-button mcp-secret-icon" data-mcp-add-drop="${v.id}" aria-label="Remove" title="Remove"${dis}>${mcpSvg("minus")}</button>
      </div>${saved}</div>`;
  }).join("");
  const where = d.machine ? ` on ${esc(d.machine)}` : "";
  const variables = `<hr class="mcp-rule"><div class="card-title">${command ? "Env vars" : "Other headers"}</div>
    <div class="mcp-add-vars">${vars}</div>
    <div><button type="button" class="mcp-sheet-button" id="mcp-add-var"${dis}>${mcpSvg("plus")}<span>${command ? "Add variable" : "Add header"}</span></button></div>
    <p class="caption mcp-muted">Secret values go to ~/.quiver/secrets/mcp/.api_keys${where}. The Quiver hub keeps a \${NAME} reference; the harnesses you add it to get the value, as a sync gives them.</p>`;
  const fits = (h) => mcpAddFits(a, h);
  const picked = d.harnesses.filter((h) => a.targets.has(h) && fits(h)).length;
  const face = `${picked} of ${d.harnesses.length} ${d.harnesses.length === 1 ? "harness" : "harnesses"}`;
  const checklist = a.picking ? `<div class="mcp-add-picker" role="group" aria-label="Add to">
      <div class="mcp-add-picker-top"><button type="button" class="mcp-sheet-button" data-mcp-add-all="active">All active</button>
        <button type="button" class="mcp-sheet-button" data-mcp-add-all="none">None</button></div>
      ${d.harnesses.map((h) => {
        const m = Fmt.mark(h);
        const why = mcpAddWhyNot(a, h);
        return `<label class="mcp-target">${m.icon ? Fmt.logo(m.icon) : `<span class="mcp-logo-gap"></span>`}<span class="mcp-target-name">${esc(m.name)}</span>
          ${why ? `<span class="caption">${esc(why)}</span>` : ""}
          <span class="mcp-spacer"></span><input type="checkbox" class="mcp-switch" role="switch" data-mcp-add-target="${esc(h)}" aria-label="${esc(m.name)}"${
            a.targets.has(h) && !why ? " checked" : ""}${dis || why ? " disabled" : ""}></label>`;
      }).join("")}</div>` : "";
  const left = d.harnesses.filter((h) => mcpAddWhyNot(a, h));
  const leftNote = left.length && !a.picking
    ? `<p class="caption warn">${esc(left.map((h) => `${Fmt.mark(h).name}: ${mcpAddWhyNot(a, h)}`).join(" · "))}</p>` : "";
  const placesHtml = `<hr class="mcp-rule"><div class="card-title">Where</div>
    <div class="mcp-add-where"><div><div>Add to</div><div class="caption">Always saved to the Quiver hub too</div></div>
      <button type="button" class="field mcp-add-face" id="mcp-add-pick" aria-expanded="${a.picking}"${dis}>${esc(face)} ${mcpSvg(a.picking ? "up" : "down")}</button></div>
    ${checklist}${leftNote}
    <label class="mcp-add-where"><span>Replace if it already exists</span>
      <input type="checkbox" class="mcp-switch" role="switch" id="mcp-add-force" aria-label="Replace if it already exists"${a.force ? " checked" : ""}${dis}></label>`;
  return `<form method="dialog" class="sheet-body">
    <div class="sheet-title" id="mcp-add-title">Add a server</div>
    ${server}${variables}${placesHtml}
    ${a.error ? `<p class="error" role="alert">${esc(a.error)}</p>` : ""}
    <div class="sheet-actions mcp-sync-actions"><span class="mcp-spacer"></span>
      <button type="button" class="mcp-sheet-button" id="mcp-add-cancel"${dis}>Cancel</button>
      <button type="submit" class="mcp-sheet-button primary" id="mcp-add-go"${mcpAddReady(a) ? "" : " disabled"}>${a.working ? "Adding…" : "Add server"}</button></div>
  </form>`;
}

/* Draws the sheet from mcpPage.add and binds it, keeping focus on the
 * element that had it, or on ``focus``. */
function mcpOpenAdd(focus) {
  const sheet = document.getElementById("mcp-add-sheet");
  const a = mcpPage.add;
  if (!sheet || !a) return;
  const el = document.activeElement;
  const keep = el && sheet.contains(el) ? mcpAddFocusKey(el) : null;
  sheet.innerHTML = mcpAddHtml();
  // A closed sheet forgets its draft, secret values included.
  sheet.onclose = () => { mcpPage.add = null; sheet.replaceChildren(); };
  // While the hub is adding, Escape waits: the answer has to land.
  sheet.oncancel = (e) => { if (a.working) e.preventDefault(); };
  mcpBindAdd(sheet, a);
  if (!sheet.open) sheet.showModal();
  const target = (keep && sheet.querySelector(keep)) || (focus && sheet.querySelector(focus));
  if (target && !target.disabled) {
    target.focus();
    if (target.setSelectionRange && typeof target.value === "string") {
      try { target.setSelectionRange(target.value.length, target.value.length); } catch { /* not text */ }
    }
  }
}

function mcpAddFocusKey(el) {
  if (el.id) return `#${CSS.escape(el.id)}`;
  for (const attr of el.getAttributeNames()) {
    if (attr.startsWith("data-mcp-add-")) return `[${attr}="${CSS.escape(el.getAttribute(attr))}"]`;
  }
  return null;
}

function mcpBindAdd(sheet, a) {
  const go = sheet.querySelector("#mcp-add-go");
  const ready = () => { go.disabled = !mcpAddReady(a); };
  const name = sheet.querySelector("#mcp-add-name");
  name.oninput = () => {
    a.name = name.value;
    for (const v of a.vars) {
      const el = sheet.querySelector(`[data-mcp-add-saved="${v.id}"]`);
      if (el) el.textContent = `saved as \${${mcpSecretName(a.name || "SERVER", v.key.trim())}}`;
    }
    const ref = sheet.querySelector("#mcp-add-token-ref");
    if (ref) ref.textContent = `Sent as Authorization: Bearer \${${mcpSecretName(a.name || "SERVER", "TOKEN")}}`;
    ready();
  };
  const line = sheet.querySelector("#mcp-add-line");
  if (line) {
    line.oninput = () => {
      a.line = line.value;
      const hint = mcpAddHint(a), el = sheet.querySelector("#mcp-add-hint");
      el.textContent = hint.text;
      el.classList.toggle("warn", hint.problem);
      ready();
    };
  }
  const url = sheet.querySelector("#mcp-add-url");
  if (url) url.oninput = () => { a.url = url.value; ready(); };
  for (const tab of sheet.querySelectorAll("[data-mcp-add-kind]")) {
    tab.onclick = () => { a.kind = tab.dataset.mcpAddKind; mcpOpenAdd(a.kind === "command" ? "#mcp-add-line" : "#mcp-add-url"); };
  }
  for (const tab of sheet.querySelectorAll("[data-mcp-add-signin]")) {
    tab.onclick = () => {
      if (a.signIn === tab.dataset.mcpAddSignin) return;
      a.signIn = tab.dataset.mcpAddSignin; a.token = ""; a.showToken = false;
      mcpOpenAdd(a.signIn === "bearer" ? "#mcp-add-token" : null);
    };
  }
  const token = sheet.querySelector("#mcp-add-token");
  if (token) token.oninput = () => { a.token = token.value; ready(); };
  const tokenShow = sheet.querySelector("#mcp-add-token-show");
  if (tokenShow) tokenShow.onclick = () => { a.showToken = !a.showToken; mcpOpenAdd("#mcp-add-token"); };
  for (const tab of sheet.querySelectorAll("[data-mcp-add-sse]")) {
    tab.onclick = () => { a.sse = tab.dataset.mcpAddSse === "true"; mcpOpenAdd(); };
  }
  const find = (el, attr) => a.vars.find((v) => String(v.id) === el.dataset[attr]);
  for (const input of sheet.querySelectorAll("[data-mcp-add-key]")) {
    input.oninput = () => {
      const v = find(input, "mcpAddKey");
      v.key = input.value;
      // Until the switch is flipped by hand, the key decides it.
      if (!v.touched && v.secret !== mcpIsSecretName(v.key)) { v.secret = mcpIsSecretName(v.key); mcpOpenAdd(); return; }
      const saved = sheet.querySelector(`[data-mcp-add-saved="${v.id}"]`);
      if (saved) saved.textContent = `saved as \${${mcpSecretName(a.name || "SERVER", v.key.trim())}}`;
      else if (v.secret && v.key.trim()) mcpOpenAdd();
    };
  }
  for (const input of sheet.querySelectorAll("[data-mcp-add-value]")) {
    input.oninput = () => { find(input, "mcpAddValue").value = input.value; };
  }
  for (const box of sheet.querySelectorAll("[data-mcp-add-secret]")) {
    box.onchange = () => { const v = find(box, "mcpAddSecret"); v.secret = box.checked; v.touched = true; v.show = false; mcpOpenAdd(); };
  }
  for (const btn of sheet.querySelectorAll("[data-mcp-add-show]")) {
    btn.onclick = () => { const v = find(btn, "mcpAddShow"); v.show = !v.show; mcpOpenAdd(); };
  }
  for (const btn of sheet.querySelectorAll("[data-mcp-add-drop]")) {
    btn.onclick = () => {
      const v = find(btn, "mcpAddDrop");
      a.vars = a.vars.filter((x) => x !== v);
      mcpOpenAdd("#mcp-add-var");
    };
  }
  sheet.querySelector("#mcp-add-var").onclick = () => {
    const v = mcpNewVar();
    a.vars.push(v);
    mcpOpenAdd(`[data-mcp-add-key="${v.id}"]`);
  };
  sheet.querySelector("#mcp-add-pick").onclick = () => { a.picking = !a.picking; mcpOpenAdd("#mcp-add-pick"); };
  for (const btn of sheet.querySelectorAll("[data-mcp-add-all]")) {
    btn.onclick = () => {
      const d = mcpPage.data;
      a.targets = new Set(btn.dataset.mcpAddAll === "active" ? mcpActive(d).filter((h) => !mcpPage.hidden.has(h)) : []);
      mcpOpenAdd();
    };
  }
  for (const box of sheet.querySelectorAll("[data-mcp-add-target]")) {
    box.onchange = () => {
      const h = box.dataset.mcpAddTarget;
      if (box.checked) a.targets.add(h); else a.targets.delete(h);
      mcpOpenAdd();
    };
  }
  sheet.querySelector("#mcp-add-force").onchange = (e) => { a.force = e.target.checked; };
  sheet.querySelector("#mcp-add-cancel").onclick = () => sheet.close();
  // Enter in a field adds, as the app's default button does.
  sheet.querySelector("form").onsubmit = (e) => {
    e.preventDefault();
    if (mcpAddReady(a)) mcpAdd();
  };
}

/* swe mcp add on the hub. Secret values are cleared from the sheet once
 * sent, whether or not the hub took them. */
async function mcpAdd() {
  const a = mcpPage.add;
  if (!a || !mcpAddReady(a)) return;
  const d = mcpPage.data;
  const command = a.kind === "command";
  const body = {
    name: a.name, kind: a.kind, force: a.force,
    targets: d.harnesses.filter((h) => a.targets.has(h)),
    vars: a.vars.filter((v) => v.key.trim()).map((v) => ({ key: v.key.trim(), value: v.value, secret: v.secret })),
  };
  body.targets = body.targets.filter((h) => mcpAddFits(a, h));
  if (command) body.command = mcpCommandWords(a.line);
  else {
    body.url = a.url.trim();
    body.transport = a.sse ? "sse" : "http";
    body.sign_in = a.signIn;
    if (a.signIn === "bearer") body.token = a.token;
  }
  a.token = "";
  a.showToken = false;
  a.working = true;
  a.error = null;
  for (const v of a.vars) if (v.secret) { v.value = ""; v.show = false; }
  mcpOpenAdd();
  const { status, data } = await mcpPost("add", body);
  body.vars = [];
  a.working = false;
  if (status === 401) {
    if (mcpOnPage()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    if (mcpPage.add !== a) return;
    a.error = failText(status, data);
    return mcpOpenAdd();
  }
  // A server that was added is shown even if its sheet went away meanwhile.
  if (mcpPage.add === a) {
    const sheet = document.getElementById("mcp-add-sheet");
    mcpPage.add = null;
    if (sheet && sheet.open) sheet.close();
  }
  const places = ["Quiver hub", ...data.targets.map((t) => Fmt.mark(t.name).name)];
  mcpPage.notice = `${data.hub === "replaced" ? "Replaced" : "Added"} ${data.name} in ${places.join(", ")}.`;
  mcpAdopt(data.page);
  if (mcpOnPage()) paintMcp();
}
