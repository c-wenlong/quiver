/* MCP servers: a port of the Mac app's McpServersPage. Every server the
 * hub machine's harnesses configure, as a server × harness grid split into
 * Local (run from a folder in ~/.quiver/mcp/servers) and Remote, with the
 * definitions that disagree lettered. Read only: Add server, Sync, Remove
 * and secret values stay in swe and the Mac app. Data: /v1/app/mcp, and
 * /v1/app/mcp?health=1 only when "Check health" is pressed.
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
      if (s) mcpOpenSheet(s);
    };
  }
}

/* ---------------------------------------------------------------- sheet */

/* `--only` takes fnmatch globs: the name as a pattern matching only itself. */
function mcpOnlyPattern(name) {
  return name.replace(/[[*?]/g, (c) => (c === "[" ? "[[]" : `[${c}]`));
}

const mcpShellQuote = (s) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${String(s).replace(/'/g, "'\\''")}'`);

/* One server: what it is and who has which definition. The Mac sheet's
 * sync, secret fields and Remove are writes, so this shows the swe
 * command that does the sync instead. */
function mcpOpenSheet(s) {
  const sheet = document.getElementById("mcp-sheet");
  if (!sheet) return;
  const d = mcpPage.data;
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
  // McpModel.sources: each definition's first harness, then the hub.
  const sources = groups.map((g) => g[0]).filter(Boolean);
  if (s.in_hub && !sources.includes("quiver")) sources.push("quiver");
  const source = sources[0] || "";
  const canSync = !!s.name && !s.name.includes(",");
  const blocked = source === "quiver" && (s.secrets_missing || []).length;
  const targets = source === "quiver" ? active.filter((h) => !s.harnesses.includes(h)) : missing;
  let sync;
  if (!canSync) {
    sync = `<p class="caption">swe can't sync a server whose name has a comma on its own. Use swe mcp sync on the hub.</p>`;
  } else if (!targets.length) {
    sync = `<p class="caption">${source === "quiver" ? "Every harness already has this server." : "Every compared harness already has this server."}</p>`;
  } else if (blocked) {
    sync = `<p class="caption warn">Set the values above first, then sync with swe mcp sync on the hub.</p>`;
  } else {
    const line = ["swe", "mcp", "sync", source, ...targets, `--only=${mcpOnlyPattern(s.name)}`].map(mcpShellQuote).join(" ");
    sync = `<p class="caption">Copy ${esc(source === "quiver" ? "the Quiver hub" : mcpMark(source).name)}'s definition to ${esc(mcpNames(targets))} by running this on the hub:</p>
      <pre class="command">${esc(line)}</pre>`;
  }
  const secrets = (s.secrets_missing || []).length ? `<hr class="mcp-rule">
    <div class="card-title">Needs values</div>
    <div class="mcp-secrets">${s.secrets_missing.map((n) => `<code>${esc(n)}</code>`).join("")}</div>
    <p class="caption">Set each on the hub with <code>swe mcp secret set NAME</code>. Values are saved to ~/.quiver/secrets/mcp/.api_keys, never versioned, and never shown here.</p>` : "";
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
    ${sync}
    <p class="caption">Sync, Remove and Add server write config files, so they stay in swe and the Mac app for now.</p>
    <div class="sheet-actions"><button class="tab selected" value="close">Close</button></div>
  </form>`;
  sheet.dataset.server = s.name;
  sheet.showModal();
}

registerPage("mcp", renderMcp);
