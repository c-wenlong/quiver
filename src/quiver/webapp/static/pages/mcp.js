/* MCP servers: a port of the Mac app's McpServersPage. Every server the
 * hub machine's harnesses configure, as a server × harness grid split into
 * Local (run from a folder in ~/.quiver/mcp/servers) and Remote, with the
 * definitions that disagree lettered. A server's sheet syncs it, with a
 * Preview first, through POST /v1/app/mcp/preview and /sync; Add server,
 * Remove and secret values stay in swe and the Mac app. Data: /v1/app/mcp,
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
  // What the last Remove did, until the list is read again.
  notice: null,
};

function mcpLoadHidden() {
  try { return new Set(JSON.parse(localStorage.getItem(MCP_HIDDEN_KEY) || "[]")); } catch { return new Set(); }
}

function mcpSaveHidden() {
  try { localStorage.setItem(MCP_HIDDEN_KEY, JSON.stringify([...mcpPage.hidden].sort())); } catch { /* private window */ }
}

const mcpOnPage = () => (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "mcp";

const MCP_SVG = {
  health: '<path d="M2 12h3l2-5 3 10 3-12 3 9 2-2h4"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  plug: '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
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
    if (mcpOnPage()) renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
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

/* McpServerRepo.grouped: a server is Local when a folder in the servers
 * folder runs it, Remote otherwise; folders no server uses join Local as
 * their own rows. Local sorts by name, Remote keeps swe's order. */
function mcpGrouped(servers, folders) {
  const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
  const local = [], remote = [];
  for (const s of servers) {
    const backing = folders.filter((f) => f.used_by.includes(s.name)).sort((a, b) => byName(a.name, b.name));
    (backing.length ? local : remote).push({ server: s, folders: backing, name: s.name });
  }
  for (const f of folders) if (!f.used_by.length) local.push({ folder: f, name: f.name });
  local.sort((a, b) => byName(a.name, b.name));
  return { local, remote };
}

/* HarnessMark: the logo, and a word after it (or alone with no logo). */
function mcpMark(h) {
  const has = (id) => Fmt.logo(id).startsWith("<img");
  const special = { claude: ["claude", "CLI"], "claude-desktop": ["claude", "Desktop"], "devin-cloud": ["devin", "Cloud"] };
  let logoId = null, word = null, name = Fmt.harnessName(h);
  if (special[h]) [logoId, word] = special[h];
  else if (has(h)) logoId = h;
  else {
    const dash = h.lastIndexOf("-");
    const base = dash > 0 ? h.slice(0, dash) : "", suffix = dash > 0 ? h.slice(dash + 1) : "";
    if (suffix && has(base)) { logoId = base; word = suffix; name = `${Fmt.harnessName(base)} (${suffix})`; }
    else word = name;
  }
  return { logoId, word, name };
}

function mcpMarkHtml(h) {
  const m = mcpMark(h);
  return `<span class="mcp-mark" title="${esc(m.name)}">${m.logoId ? Fmt.logo(m.logoId) : ""}${
    m.word ? `<span>${esc(m.word)}</span>` : ""}</span>`;
}

const mcpNames = (list) => list.map((h) => mcpMark(h).name).join(", ");

/* ---------------------------------------------------------------- paint */

function mcpHeader() {
  const d = mcpPage.data;
  const where = d && d.machine ? ` on ${d.machine}` : "";
  const busy = mcpPage.loading;
  return pageHeader("MCP servers",
    `Every server your harnesses configure${where}, where each is missing, and where two disagree.`,
    `<button class="mcp-button" id="mcp-health" title="Run each server's command check and reach each URL"${
      mcpPage.checking ? " disabled" : ""}>${mcpSvg("health")}<span>${mcpPage.checking ? "Checking…" : "Check health"}</span></button>
     <button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Read the configs again">${icon("rescan")}</button>`);
}

function mcpShell(body) {
  document.getElementById("panel").innerHTML = `<div class="page mcp">${mcpHeader()}
    <div class="stack">${body}</div></div>
    <dialog class="sheet mcp-sheet" id="mcp-sheet"></dialog>`;
  const health = document.getElementById("mcp-health");
  if (health) health.onclick = () => mcpLoad(true);
  // Refresh keeps health on when the list on show has it, as in the app.
  bindRefresh(() => mcpLoad(mcpHasHealth()));
}

function paintMcp() {
  if (!mcpPage.data) {
    const { status, data } = mcpPage.failure || {};
    const msg = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
    return mcpShell(`<div class="card"><p class="error">${esc(msg)}</p></div>`);
  }
  const d = mcpPage.data, p = mcpPage;
  const folders = d.folders || { items: [] };
  const parts = [];
  if (p.failure) {
    const { status, data } = p.failure;
    const msg = (data && data.error && data.error.message) || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
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
  mcpShell(parts.join(""));
  mcpBind();
  const again = openName != null && d.servers.find((s) => s.name === openName);
  if (again) mcpOpenSheet(again);
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
    const on = !mcpPage.hidden.has(h), name = mcpMark(h).name;
    return `<button class="mcp-chip${on ? " on" : ""}" data-mcp-chip="${esc(h)}" aria-pressed="${on}"
      title="${esc(on ? `Leave ${name} out of the comparison` : `Compare ${name} too`)}">${on ? mcpSvg("check", "tick") : ""}${mcpMarkHtml(h)}</button>`;
  }).join("")}</div>`;
}

function mcpGrid(harnesses, grouped, showHealth, hubKnown) {
  const sections = [["Local", grouped.local], ["Remote", grouped.remote]].filter(([, rows]) => rows.length);
  const span = harnesses.length + 1;
  const head = `<thead><tr><th class="mcp-name-col">Server</th>${harnesses.map((h) =>
    `<th class="mcp-col">${mcpMarkHtml(h)}</th>`).join("")}</tr></thead>`;
  const body = sections.map(([title, rows]) => {
    const open = mcpPage.opened.has(title);
    const shown = open || rows.length <= MCP_FOLD ? rows : rows.slice(0, MCP_FOLD);
    const hidden = Math.max(0, rows.length - MCP_FOLD);
    let html = `<tr class="mcp-section"><td colspan="${span}">${esc(title)} · ${rows.length}</td></tr>`;
    html += shown.map((row) => (row.server
      ? mcpServerRow(row.server, row.folders, harnesses, showHealth)
      : mcpFolderRow(row.folder, harnesses, hubKnown))).join("");
    if (hidden) {
      html += `<tr class="mcp-expand-row"><td colspan="${span}"><button class="mcp-expand" data-mcp-fold="${esc(title)}"
        title="${esc(open ? `Show only the first ${MCP_FOLD}` : `Show every ${title.toLowerCase()} row`)}">${mcpSvg(open ? "up" : "down")}<span>${
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
    secondary += `<span>·</span><span class="mono" title="${esc(folders.map((f) => f.path).join("\n"))}">${esc(folders.map((f) => f.name).join(", "))}</span>`;
    if (folders.length === 1) secondary += `<span>·</span>${mcpGitPieces(folders[0].git)}`;
  }
  const cells = harnesses.map((h) => {
    const g = mcpGroupOf(s, h);
    if (g < 0) return `<td class="mcp-cell"><span class="mcp-absent" title="${esc(`Not in ${mcpMark(h).name}`)}">—</span></td>`;
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
function mcpSyncTargets(s, source, harnesses) {
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

const mcpSourceName = (src) => (src === "quiver" ? "Quiver hub" : mcpMark(src).name);

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
      preview: null, done: null, error: null, running: false, confirming: false, removing: false };
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

/* manage.js's apiPost: JSON with app.js's authHeaders (the bearer when one
 * is stored, and the pair header an owner's tailnet device signs in with). */
const mcpPost = (action, body) => apiPost(`/v1/app/mcp/${action}`, body);

const mcpFailure = (status, data) => (data && data.error && data.error.message)
  || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");

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
  if (status === 401) { renderGate("The hub doesn't know this browser's credential. It may have been revoked."); return; }
  if (status !== 200 || !data) {
    st.error = mcpFailure(status, data);
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
  if (status === 401) { renderGate("The hub doesn't know this browser's credential. It may have been revoked."); return; }
  if (status !== 200 || !data) {
    st.error = mcpFailure(status, data);
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

/* Where Remove takes the server out of: every harness that has it, and the
 * hub (McpServersPage.removeServer). The hub refuses if any has changed. */
const mcpRemovePlaces = (s) => [...s.harnesses, ...(s.in_hub ? ["quiver"] : [])];

function mcpRemoveConfirm(s) {
  const places = mcpRemovePlaces(s);
  const where = (mcpPage.data && mcpPage.data.machine) || "the hub";
  const names = places.map((p) => (p === "quiver" ? "Quiver hub" : mcpMark(p).name)).join(", ");
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
    if (mcpOnPage()) renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    return;
  }
  if (status !== 200 || !data) {
    st.error = mcpFailure(status, data);
    if (mcpPage.sheet === st) mcpRepaintSheet(s);
    // Most refusals mean the configs moved on: read them again.
    if (status) mcpLoad(mcpHasHealth());
    return;
  }
  const sheet = document.getElementById("mcp-sheet");
  if (sheet && sheet.open && sheet.dataset.server === s.name) sheet.close();
  if (mcpPage.sheet === st) mcpPage.sheet = null;
  mcpPage.notice = `Removed ${s.name} from ${data.removed.map((p) => (p === "quiver" ? "Quiver hub" : mcpMark(p).name)).join(", ")}.`;
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
    const mark = mcpMark(h.harness);
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
    <ul class="mcp-confirm-files">${writes.map((h) => `<li><code>${esc(h.path)}</code><span class="caption">${esc(mcpMark(h.harness).name)}: ${
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
    const m = mcpMark(h);
    return `<span class="mcp-mark">${m.logoId ? Fmt.logo(m.logoId) : ""}<span>${esc(m.name)}</span></span>`;
  }).join("")}</span>`;
  const health = s.health
    ? `${mcpHealthDot(s)}<span class="caption">${esc(s.detail || "Healthy")}</span>` : "";
  const missing = mcpMissing(s, active);
  const facts = [
    ...groups.map((g, i) => fact(groups.length > 1 ? `Definition ${mcpLetter(i)}` : "In", marks(g))),
    missing.length ? fact("Missing from", marks(missing)) : "",
  ].join("");
  const secrets = (s.secrets_missing || []).length ? `<hr class="mcp-rule">
    <div class="card-title">Needs values</div>
    <div class="mcp-secrets">${s.secrets_missing.map((n) => `<code>${esc(n)}</code>`).join("")}</div>
    <p class="caption">Set each on the hub with <code>swe mcp secret set NAME</code>. Values are saved to ~/.quiver/secrets/mcp/.api_keys, never versioned, and never shown here. Sync fills these in when it writes a harness config.</p>` : "";

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
      const m = mcpMark(h);
      const label = replaces ? `${m.name}, replaces its own definition` : m.name;
      return `<label class="mcp-target">${m.logoId ? Fmt.logo(m.logoId) : `<span class="mcp-logo-gap"></span>`}<span class="mcp-target-name">${esc(m.name)}</span>${
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
    <div class="sheet-title"><span class="mcp-sheet-name">${esc(s.name)}</span><span class="badge">${esc(s.transport)}</span>${health}</div>
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
    <p class="caption mcp-muted">Add server and secret values stay in swe and the Mac app.</p>
  </form>`;
  sheet.dataset.server = s.name;
  mcpBindSheet(sheet, s, st);
  if (!sheet.open) sheet.showModal();
}

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
