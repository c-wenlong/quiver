/* Plugins: a port of the Mac app's PluginsPage (Explore and Health tabs).
 * Data from /v1/app/plugins?view=health|explore (quiver/webapp/pages/
 * plugins.py). Health's Fix and Fix all POST /v1/app/plugins/fix|fix_all,
 * each after an inline confirm naming the commands. The plugin sheet's
 * Install, Enable, Disable and Uninstall per harness POST
 * /v1/app/plugins/plugin, after the same kind of confirm. Renames and Add
 * marketplace stay in swe and the Mac app.
 */
"use strict";
// The bundle runs this file in its own function scope.

const FOLD_LIMIT = 8;      // PluginsPage.foldLimit
const SHOWN_SKILLS = 8;    // PluginRow.shownSkills

const page = {
  tab: "explore",
  data: { health: null, explore: null },
  error: { health: null, explore: null },
  busy: { health: false, explore: false },
  seq: { health: 0, explore: 0 },
  opened: new Set(),          // Health cards unfolded, by harness
  harness: "", query: "",
  sheet: null,                // the plugin whose sheet is open, by ref
  expanded: new Set(),        // Explore marketplaces opened this session
  closedInSearch: new Set(),  // closed during the current search
  needle: "",
  exploreStale: false,        // a fix ran since Explore was read
  // Fix and Fix all (PluginsModel): the confirm on show (a finding id, or
  // "all" with the ids it listed), what is running, and the last outcome.
  confirming: null, confirmAll: [],
  fixing: null,               // a finding id, or "all"
  fixingCount: 0,
  fixError: null, lastFixed: null,
  // The sheet's actions (PluginAction): the one asking to confirm and the
  // one running ({verb, ref, harness}), and the last outcome per plugin.
  actConfirm: null, actRunning: null, actOutcome: null,
};

const SVG = {
  folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
  question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14M12 17h.01"/>',
  warn: '<path d="M12 4 2.5 20h19zM12 10v4M12 17h.01"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  seal: '<path d="m12 3 2.2 1.6 2.7-.2.8 2.6 2.3 1.4-1 2.6 1 2.6-2.3 1.4-.8 2.6-2.7-.2L12 21l-2.2-1.6-2.7.2-.8-2.6L4 15.6l1-2.6-1-2.6 2.3-1.4.8-2.6 2.7.2z"/><path d="m9 12 2 2 4-4"/>',
  puzzle: '<path d="M10 4a2 2 0 1 1 4 0v2h4v4h-2a2 2 0 1 0 0 4h2v4h-4v-2a2 2 0 1 0-4 0v2H6v-4H4a2 2 0 1 1 0-4h2V6h4z"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/>',
};
const svg = (name, cls = "") =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SVG[name]}</svg>`;

const STATUS_TITLE = { enabled: "Enabled", disabled: "Disabled", installed: "Installed",
  available: "Available", cached: "Cached", absent: "Not listed" };
const INSTALLED = new Set(["enabled", "disabled", "installed", "cached"]);
const COMPONENTS = [["skills", "skill", "skills"], ["agents", "agent", "agents"],
  ["commands", "command", "commands"], ["hooks", "hook", "hooks"], ["mcp_servers", "MCP server", "MCP servers"]];

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const onPage = () => currentRoute() === "plugins";

/* ------------------------------------------------------------ loading */

async function render() {
  paint();
  // Explore is the first tab. Once a tab is on show, only that tab is
  // read; the other reads when it opens.
  await load(page.tab);
}

async function load(view, refresh = false) {
  const gen = routeGen, seq = ++page.seq[view];
  page.busy[view] = true;
  if (onPage()) paintHeader();
  const q = new URLSearchParams({ view });
  if (refresh) q.set("refresh", "1");
  const { status, data } = await api(`/v1/app/plugins?${q}`);
  if (seq !== page.seq[view]) return;
  page.busy[view] = false;
  if (gen !== routeGen || !onPage()) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) {
    page.error[view] = failText(status, data, "then refresh");
  } else {
    page.error[view] = null;
    page.data[view] = data;
    if (view === "explore") page.exploreStale = false;
  }
  // A reply for the tab not on show only updates what that tab will show.
  if (view === page.tab) paint();
}

/* ------------------------------------------------------------- frame */

function header() {
  const d = page.data[page.tab];
  const busy = page.busy[page.tab];
  const updated = d && d.built_at ? `<span class="caption">Updated ${Fmt.ago(d.built_at)}</span>` : "";
  const help = page.tab === "health" ? "Check again" : "Read the marketplaces again";
  const fixing = page.fixing != null;
  let fixAll = "";
  if (page.tab === "health" && (fixAllIds().length || page.fixing === "all")) {
    const label = page.fixing === "all"
      ? `Fixing ${count(page.fixingCount, "finding", "findings")}…` : "Fix all";
    fixAll = `<button class="pl-button primary" id="pl-fix-all" title="Run every fix swe can run, one at a time"${
      fixing ? " disabled" : ""}${page.confirming === "all" ? ' aria-expanded="true"' : ""}>${label}</button>`;
  }
  const off = page.tab === "health" && fixing;
  return pageHeader("Plugins",
    "Every plugin in every marketplace, and where Claude Code and Codex no longer match ~/.quiver/plugins.",
    `${updated}${fixAll}<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="${help}"${
      off ? " disabled" : ""}>${icon("rescan")}</button>`);
}

function paintHeader() {
  const el = document.querySelector("#panel .page-header");
  if (!el) return;
  el.outerHTML = header();
  bindHeader();
}

function bindHeader() {
  const btn = document.getElementById("page-refresh");
  if (btn) {
    btn.onclick = () => {
      if (page.busy[page.tab] || (page.tab === "health" && page.fixing != null)) return;
      load(page.tab, true);
    };
  }
  const all = document.getElementById("pl-fix-all");
  if (all) {
    all.onclick = () => {
      if (page.fixing != null) return;
      // Only what this confirm lists runs, even if Health changes under it.
      page.confirming = "all";
      page.confirmAll = fixAllIds();
      paint();
      focusConfirm();
    };
  }
}

function paint() {
  const tabs = `<div class="pl-tabs" role="tablist">${[["explore", "Explore"], ["health", "Health"]].map(([id, label]) =>
    `<button class="pl-tab${page.tab === id ? " selected" : ""}" role="tab" aria-selected="${page.tab === id}" data-pl-tab="${id}">${label}</button>`).join("")}</div>`;
  const body = page.tab === "health" ? healthBody() : exploreBody();
  const active = document.activeElement && document.activeElement.id === "pl-search"
    ? document.activeElement.selectionStart : null;
  document.getElementById("panel").innerHTML = `<div class="page">${header()}
    <div class="stack">${tabs}${body}</div></div>`;
  bindHeader();
  for (const tab of document.querySelectorAll("[data-pl-tab]")) {
    tab.onclick = () => {
      const next = tab.dataset.plTab;
      if (next === page.tab) return;
      page.tab = next;
      paint();
      // A fresh read when the view has none or its cache has aged.
      const d = page.data[next];
      if (!d || Date.now() / 1000 - (d.built_at || 0) > 60 || (next === "explore" && page.exploreStale)) load(next);
    };
  }
  if (page.tab === "health") bindHealth(); else bindExplore();
  // A repaint (a reply landing, say) keeps the search being typed in and
  // the plugin sheet being read.
  const search = document.getElementById("pl-search");
  if (search && active != null) { search.focus(); search.setSelectionRange(active, active); }
  if (page.tab === "explore" && page.sheet) openPlugin(page.sheet);
}

const loadingCard = (text) => `<div class="card"><p class="empty">${esc(text)}</p></div>`;
const errorCard = (text) => `<div class="card"><p class="error">${esc(text)}</p></div>`;
const iconCard = (name, text, cls = "") =>
  `<div class="card"><p class="pl-label ${cls}">${svg(name)}<span>${text}</span></p></div>`;

/* ------------------------------------------------------------- Health */

function healthBody() {
  const d = page.data.health;
  const err = (page.error.health ? errorCard(page.error.health) : "") + fixOutcome() + confirmAllCard();
  if (!d) return err || loadingCard("Checking plugin drift…");
  if (d.sources === 0) {
    return err + iconCard("puzzle", "No plugins in ~/.quiver/plugins, so there is nothing to keep in step.", "muted-label");
  }
  if (!d.drift.length) return err + iconCard("seal", "Claude Code and Codex run what ~/.quiver/plugins holds.");
  const harnesses = [];
  for (const f of d.drift) if (!harnesses.includes(f.harness)) harnesses.push(f.harness);
  const cards = harnesses.map((h) => {
    const rows = d.drift.filter((f) => f.harness === h);
    const open = page.opened.has(h);
    const shown = open || rows.length <= FOLD_LIMIT ? rows : rows.slice(0, FOLD_LIMIT);
    const hidden = Math.max(0, rows.length - FOLD_LIMIT);
    const fold = hidden ? `<button class="pl-fold inset" data-pl-fold="${esc(h)}"
        title="${open ? `Show only the first ${FOLD_LIMIT}` : "Show every finding"}">${svg(open ? "up" : "down")}<span>${
        open ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</span></button>` : "";
    return `<div class="card pl-drift-card">
      <div class="pl-harness-title">${Fmt.logo(h)}<span class="card-title">${esc(Fmt.harnessName(h))}</span>
        <span class="caption">${count(rows.length, "finding", "findings")}</span></div>
      <div class="pl-drift-rows">${shown.map(driftRow).join("")}</div>${fold}
    </div>`;
  }).join("");
  return `${err}${cards}`;
}

/* The last fix's outcome, above the cards, as the app's banner. */
function fixOutcome() {
  if (page.fixError) return errorCard(page.fixError);
  if (page.lastFixed) {
    return `<div class="card"><p class="pl-label">${svg("check")}<span>Ran ${esc(page.lastFixed)}</span></p></div>`;
  }
  return "";
}

/* PluginsModel.fixAllIDs: the findings swe can fix itself, in report order. */
function fixAllIds() {
  const d = page.data.health;
  return d && d.drift ? d.drift.filter((f) => f.runnable).map((f) => f.id) : [];
}

const findingOf = (id) => (page.data.health && page.data.health.drift || []).find((f) => f.id === id);

/* Fix all's confirm: every finding it will fix, with its commands. */
function confirmAllCard() {
  if (page.confirming !== "all") return "";
  const rows = page.confirmAll.map(findingOf).filter(Boolean);
  const n = rows.length;
  const busy = page.fixing != null;
  return `<div class="card pl-confirm" role="group" aria-labelledby="pl-confirm-title" data-pl-confirm>
    <div class="card-title" id="pl-confirm-title">${n === 1 ? "Run 1 fix?" : `Run ${n} fixes?`}</div>
    <p class="caption">One at a time, stopping at the first that fails. swe checks each still applies before it runs:</p>
    <ol class="pl-confirm-list">${rows.map((f) => `<li>
      <div class="pl-line">${Fmt.logo(f.harness, "logo logo-sm")}<span class="pl-name">${esc(f.ref)}</span>
        <span class="badge">${esc(f.kind_title)}</span></div>
      <pre class="pl-code">${esc(f.fix)}</pre></li>`).join("")}</ol>
    <div class="pl-confirm-buttons">
      <button class="pl-button" data-pl-cancel${busy ? " disabled" : ""}>Cancel</button>
      <button class="pl-button primary" data-pl-run-all${busy || !n ? " disabled" : ""}>${
        page.fixing === "all" ? "Running…" : "Run all"}</button>
    </div>
  </div>`;
}

/* One finding's confirm, under its row: the exact command it runs. */
function confirmRow(f) {
  const busy = page.fixing != null;
  return `<div class="pl-confirm inline" role="group" aria-label="Run this fix?" data-pl-confirm>
    <div class="pl-confirm-title">Run this fix?</div>
    <p class="caption">swe will check the finding still applies, then run:</p>
    <pre class="pl-code">${esc(f.fix)}</pre>
    <div class="pl-confirm-buttons">
      <button class="pl-button" data-pl-cancel${busy ? " disabled" : ""}>Cancel</button>
      <button class="pl-button primary" data-pl-run="${esc(f.id)}"${busy ? " disabled" : ""}>${
        page.fixing === f.id ? "Running…" : "Run"}</button>
    </div>
  </div>`;
}

function driftRow(f) {
  const paths = f.paths || [];
  const pathsLine = paths.length
    ? paths.slice(0, 3).join(", ") + (paths.length > 3 ? ` and ${paths.length - 3} more` : "") : "";
  const running = page.fixing === f.id;
  const fixBtn = !f.runnable ? "" : running
    ? '<span class="pl-running" role="status" aria-label="Fixing"><span class="pl-spinner"></span></span>'
    : `<button class="pl-button" data-pl-fix="${esc(f.id)}" aria-label="Fix ${esc(f.ref)}"${
      page.confirming === f.id ? ' aria-expanded="true"' : ""}${page.fixing != null ? " disabled" : ""}>Fix</button>`;
  return `<div class="pl-drift${f.runnable ? " runnable" : ""}">
    <span class="pl-dot ${f.severe ? "danger" : "warning"}"></span>
    <div class="pl-drift-main">
      <div class="pl-line"><span class="pl-name">${esc(f.ref)}</span><span class="badge">${esc(f.kind_title)}</span></div>
      <div class="caption">${esc(f.detail)}</div>
      ${pathsLine ? `<div class="caption muted pl-clamp" title="${esc(paths.join("\n"))}">${esc(pathsLine)}</div>` : ""}
      <div class="pl-fix"><pre class="pl-code">${esc(f.fix)}</pre>
        <button class="pl-copy" data-pl-copy="${esc(f.fix)}" title="Copy" aria-label="Copy">${svg("copy")}</button></div>
      ${f.runnable ? "" : '<div class="caption muted">No command does this, so make the edit by hand.</div>'}
      ${page.confirming === f.id ? confirmRow(f) : ""}
    </div>
    ${fixBtn ? `<div class="pl-drift-action">${fixBtn}</div>` : ""}
  </div>`;
}

function bindHealth() {
  for (const btn of document.querySelectorAll("[data-pl-fold]")) {
    btn.onclick = () => {
      const h = btn.dataset.plFold;
      if (page.opened.has(h)) page.opened.delete(h); else page.opened.add(h);
      paint();
    };
  }
  for (const btn of document.querySelectorAll("[data-pl-copy]")) {
    Fmt.copyTick(btn, () => btn.dataset.plCopy,
      () => { btn.title = "Couldn't copy — select it by hand"; });
  }
  for (const btn of document.querySelectorAll("[data-pl-fix]")) {
    btn.onclick = () => {
      if (page.fixing != null) return;
      const id = btn.dataset.plFix;
      page.confirming = page.confirming === id ? null : id;
      paint();
      if (page.confirming) focusConfirm();
    };
  }
  for (const btn of document.querySelectorAll("[data-pl-cancel]")) btn.onclick = () => closeConfirm();
  for (const el of document.querySelectorAll("[data-pl-confirm]")) {
    el.onkeydown = (e) => { if (e.key === "Escape" && page.fixing == null) { e.preventDefault(); closeConfirm(); } };
  }
  for (const btn of document.querySelectorAll("[data-pl-run]")) {
    btn.onclick = () => runFix("fix", [btn.dataset.plRun]);
  }
  const all = document.querySelector("[data-pl-run-all]");
  if (all) all.onclick = () => runFix("fix_all", page.confirmAll.slice());
}

/* The confirm's Run button takes focus, so Enter runs and Escape cancels. */
function focusConfirm() {
  const btn = document.querySelector("[data-pl-run], [data-pl-run-all]");
  if (btn && !btn.disabled) btn.focus();
}

function closeConfirm() {
  const was = page.confirming;
  page.confirming = null;
  page.confirmAll = [];
  paint();
  const back = was === "all" ? document.getElementById("pl-fix-all")
    : was && document.querySelector(`[data-pl-fix="${CSS.escape(was)}"]`);
  if (back) back.focus();
}

/* POST a confirmed fix. The hub checks each id is a finding it lists now
 * and runs only the commands it builds itself; the cards repaint from
 * the fresh Health view it answers with. */
async function runFix(action, ids) {
  if (page.fixing != null || !ids.length) return;
  const one = action === "fix";
  page.fixing = one ? ids[0] : "all";
  page.fixingCount = ids.length;
  page.fixError = null;
  page.lastFixed = null;
  if (onPage()) paint();
  const { status, data } = await apiPost(`/v1/app/plugins/${action}`, one ? { id: ids[0] } : { ids });
  page.fixing = null;
  page.fixingCount = 0;
  page.confirming = null;
  page.confirmAll = [];
  if (status === 401) {
    if (onPage()) renderGate();
    return;
  }
  if (status === 200 && data && data.health) {
    // A Health read still in flight began before the fix: drop it.
    page.seq.health++;
    page.busy.health = false;
    page.data.health = data.health;
    page.error.health = null;
    // Explore changes with a fix too. A read of it in flight began before
    // the fix landed: drop it, and read again now if Explore is on show
    // (the hub dropped its cached copy), else when it next opens.
    page.seq.explore++;
    page.busy.explore = false;
    page.exploreStale = true;
    if (page.tab === "explore" && onPage()) load("explore");
    const failed = data.results.find((r) => r.status === "failed");
    if (failed) {
      page.fixError = data.ran ? `Ran ${count(data.ran, "fix", "fixes")}, then stopped. ${failed.error}` : failed.error;
    } else if (one) {
      page.lastFixed = data.results[0].fixed ? data.results[0].fixed.fix : null;
    } else {
      page.lastFixed = data.ran ? count(data.ran, "fix", "fixes") : null;
    }
    if (onPage()) {
      paint();
      // The clicked button was disabled while it ran; put focus back on
      // it, or on Fix all, so the keyboard keeps its place.
      const back = (one && document.querySelector(`[data-pl-fix="${CSS.escape(ids[0])}"]`))
        || document.getElementById("pl-fix-all") || document.getElementById("page-refresh");
      if (back) back.focus();
    }
    return;
  }
  page.fixError = failText(status, data);
  if (!onPage()) return;
  paint();
  // A refusal most likely means Health moved on (a finding gone, or
  // another fix running): read it again.
  if (status) load("health", true);
}

/* ------------------------------------------------------------ Explore */

const needleOf = (q) => q.trim().toLowerCase();

function haystack(p) {
  if (p._hay == null) {
    p._hay = [p.name, p.ref, p.marketplace, p.title || "", p.description || "",
      ...(p.skills || []).map((s) => s.name)].join("\n").toLowerCase();
  }
  return p._hay;
}

const statusOf = (p, h) => (p && p.harnesses[h] ? p.harnesses[h].status : "absent");

/* PluginExploreIndex.sources(harness:query:): only the rows showing that
 * harness's chip with something in it, and matching the search; empty
 * marketplaces and sources left out. Installed counts are of what is kept. */
function filtered(d) {
  const needle = needleOf(page.query), harness = page.harness;
  const out = [];
  for (const source of d.sources) {
    const markets = [];
    for (const m of source.marketplaces) {
      const kept = m.plugins.filter((e) => {
        const p = d.plugins[e.ref];
        if (!p) return false;
        if (harness && !(e.harnesses.includes(harness) && statusOf(p, harness) !== "absent")) return false;
        return !needle || haystack(p).includes(needle);
      });
      if (!kept.length && (harness || needle)) continue;
      const installed = kept.filter((e) => e.harnesses.some((h) => INSTALLED.has(statusOf(d.plugins[e.ref], h)))).length;
      markets.push({ ...m, plugins: kept, installed });
    }
    if (markets.length || (!harness && !needle)) out.push({ ...source, marketplaces: markets });
  }
  return out;
}

const distinct = (sources) => new Set(sources.flatMap((s) => s.marketplaces.flatMap((m) => m.plugins.map((e) => e.ref)))).size;

function sourceCount(s, searching) {
  const plugins = s.marketplaces.reduce((n, m) => n + m.plugins.length, 0);
  if (searching) return count(plugins, "match", "matches");
  return `${count(s.marketplaces.length, "marketplace", "marketplaces")} · ${count(plugins, "plugin", "plugins")}`;
}

function marketCount(m, searching) {
  if (searching) return count(m.plugins.length, "match", "matches");
  return `${count(m.plugins.length, "plugin", "plugins")} · ${m.installed} installed`;
}

function isOpen(id) {
  return needleOf(page.query) ? !page.closedInSearch.has(id) : page.expanded.has(id);
}

/* swe's words for a registered marketplace whose folder is not here
   (`_read_manifest` in src/quiver/plugins/catalog.py). A settings.json
   from another machine names several at once, so each harness gets one
   line naming them and where they were looked for, as the Mac app does. */
const MISSING_MANIFEST = "no marketplace manifest on disk";

function problemLines(errors) {
  const missing = new Map();
  const lines = [];
  for (const p of errors) {
    if (p.detail === MISSING_MANIFEST && p.marketplace) {
      if (!missing.has(p.harness)) missing.set(p.harness, []);
      missing.get(p.harness).push(p);
      continue;
    }
    lines.push(`${[Fmt.harnessName(p.harness), p.marketplace].filter(Boolean).join(", ")}: ${p.detail}`);
  }
  const folded = [...missing].map(([harness, rows]) => {
    const where = rows.map((p) => p.short_path || p.path || "");
    const parents = new Set(where.map((w) => w.replace(/\/[^/]*$/, "/")));
    // The shared parent says it all only when each folder is named after
    // its marketplace; Claude's installLocation and Codex's local source
    // can name any folder, and then each path has to be shown.
    const named = rows.every((p, i) => where[i].endsWith(`/${p.marketplace}`));
    const names = parents.size === 1 && named
      ? `${rows.map((p) => p.marketplace).join(", ")} (looked in ${[...parents][0]})`
      : rows.map((p, i) => where[i] ? `${p.marketplace} (${where[i]})` : p.marketplace).join(", ");
    const n = rows.length === 1 ? "1 registered marketplace is" : `${rows.length} registered marketplaces are`;
    return `${Fmt.harnessName(harness)}: ${n} missing on this machine: ${names}`;
  });
  return [...folded, ...lines];
}

function exploreBody() {
  const d = page.data.explore;
  const err = page.error.explore ? errorCard(page.error.explore) : "";
  if (!d) return err || loadingCard("Reading every marketplace…");
  const searching = !!needleOf(page.query);
  const sources = filtered(d);
  const harnessOpts = [["", "All harnesses"], ...d.harnesses.map((h) => [h, Fmt.harnessName(h)])];
  const filters = `<div class="filters">
    <select class="field" id="pl-harness" aria-label="Harness">${harnessOpts.map(([v, l]) =>
      `<option value="${esc(v)}"${v === page.harness ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>
    <input class="field search" id="pl-search" type="search" placeholder="Search plugins and their skills"
      value="${esc(page.query)}" autocomplete="off" spellcheck="false">
    <span class="caption count num">${distinct(sources)} of ${d.total} plugins</span>
  </div>`;
  const lines = problemLines(d.errors);
  const problems = lines.length ? `<div class="card pl-problems">${lines.map((text) =>
    `<div class="pl-label warn">${svg("warn")}<span>${esc(text)}</span></div>`).join("")}</div>` : "";
  let list;
  if (d.total === 0) {
    list = iconCard("puzzle", "No plugin marketplaces registered in Claude Code, Codex or Cursor.", "muted-label");
  } else if (!sources.length) {
    list = iconCard("puzzle", "No plugins match.", "muted-label");
  } else {
    list = `<div class="pl-sources">${sources.map((s) => sourceBlock(s, d, searching)).join("")}</div>`;
  }
  return `${err}${filters}${problems}${list}`;
}

function sourceBlock(s, d, searching) {
  const mark = s.kind === "local" ? svg("folder", "pl-source-icon")
    : s.kind === "unregistered" ? svg("question", "pl-source-icon") : Fmt.logo(s.harness);
  return `<section class="pl-source">
    <div class="pl-source-head" role="heading" aria-level="2">${mark}
      <span class="pl-source-title">${esc(s.title)}</span>
      <span class="caption pl-source-note">${esc(s.note)}</span>
      <span class="caption num pl-right">${esc(sourceCount(s, searching))}</span></div>
    <div class="pl-markets">${s.marketplaces.map((m) => marketCard(m, d, searching)).join("")}</div>
  </section>`;
}

function marketCard(m, d, searching) {
  const open = isOpen(m.id) && m.plugins.length;
  const lock = m.tag === "github"
    ? `<span class="pl-lock" title="Updates from GitHub would undo edits, so this marketplace is read-only.">${svg("lock")}</span>` : "";
  const rows = open ? `<div class="pl-rows">${m.plugins.map((e) => pluginRow(e, d)).join("")}</div>` : "";
  const expand = m.plugins.length ? `<button class="pl-fold" data-pl-market="${esc(m.id)}"
      title="${open ? "Show only the header" : "Show this marketplace's plugins"}">${svg(open ? "up" : "down")}<span>${
      open ? "Show less" : "Show plugins"}</span><span class="muted">· ${esc(marketCount(m, searching))}</span></button>` : "";
  return `<div class="pl-market">
    <div class="pl-market-head">
      <span class="pl-name pl-ellipsis">${esc(m.title)}</span>
      ${m.title !== m.name ? `<code class="pl-mono">${esc(m.name)}</code>` : ""}
      ${m.tag ? `<span class="pl-tag">${esc(m.tag)}</span>` : ""}${lock}
      <span class="caption num pl-right">${m.installed} installed</span>
    </div>${rows}${expand}
  </div>`;
}

function pluginRow(e, d) {
  const p = d.plugins[e.ref];
  const skills = p.skills || [];
  const badges = skills.slice(0, SHOWN_SKILLS).map((s) =>
    `<span class="badge" title="${esc(s.description || "No description.")}">${esc(s.name)}</span>`).join("");
  const rest = skills.slice(SHOWN_SKILLS);
  const more = rest.length ? `<span class="badge" title="${esc(rest.map((s) => s.name).join(", "))}">+${rest.length} more</span>` : "";
  const drift = p.drift ? `<button class="pl-drift-badge" data-pl-health
      title="${esc(`${p.drift.harnesses.map(Fmt.harnessName).join(", ")}: ${p.drift.title.toLowerCase()}. Open Health to see the fix.`)}">
      <span class="pl-dot small ${p.drift.state === "unregistered" ? "danger" : "warning"}"></span>${esc(p.drift.title)}</button>` : "";
  const chips = e.harnesses.filter((h) => p.harnesses[h]).map((h) => chip(p, h, d)).join("");
  return `<div class="pl-row">
    <div class="pl-row-main">
      <div class="pl-line">
        <button class="pl-open pl-name pl-ellipsis" data-pl-open="${esc(p.ref)}" title="Open ${esc(p.title)}">${esc(p.title)}</button>
        ${p.version ? `<span class="caption">v${esc(p.version)}</span>` : ""}${drift}
      </div>
      ${p.title !== p.name ? `<div class="pl-mono muted pl-ellipsis">${esc(p.ref)}</div>` : ""}
      ${p.description ? `<div class="pl-desc pl-ellipsis" title="${esc(p.description)}">${esc(p.description)}</div>` : ""}
      ${skills.length ? `<div class="pl-skills">${badges}${more}</div>` : ""}
    </div>
    <div class="pl-chips">${chips}</div>
  </div>`;
}

function chip(p, h, d) {
  const st = p.harnesses[h];
  const parts = [`${Fmt.harnessName(h)}: ${STATUS_TITLE[st.status].toLowerCase()}`];
  if (st.version) parts.push(`version ${st.version}`);
  if (st.scopes.length) parts.push(`scope ${st.scopes.join(", ")}`);
  if (!d.actions.includes(h)) parts.push("read only");
  const on = st.status === "enabled" || st.status === "installed";
  return `<span class="pl-chip${on ? " on" : ""}" title="${esc(parts.join(" · "))}">
    <span class="pl-status ${st.status}"></span>${Fmt.logo(h, "logo logo-sm")}${esc(STATUS_TITLE[st.status])}</span>`;
}

function bindExplore() {
  const select = document.getElementById("pl-harness");
  if (select) select.onchange = () => { page.harness = select.value; paint(); };
  const search = document.getElementById("pl-search");
  if (search) {
    search.oninput = () => {
      page.query = search.value;
      const needle = needleOf(page.query);
      // A new search forgets what was closed during the last one.
      if (needle !== page.needle) { page.needle = needle; page.closedInSearch.clear(); }
      paint();  // keeps the caret where it was
    };
  }
  for (const btn of document.querySelectorAll("[data-pl-market]")) {
    btn.onclick = () => {
      const id = btn.dataset.plMarket;
      const set = needleOf(page.query) ? page.closedInSearch : page.expanded;
      if (set.has(id)) set.delete(id); else set.add(id);
      paint();
    };
  }
  for (const btn of document.querySelectorAll("[data-pl-open]")) {
    btn.onclick = () => openPlugin(btn.dataset.plOpen);
  }
  for (const btn of document.querySelectorAll("[data-pl-health]")) {
    btn.onclick = () => { page.tab = "health"; paint(); load("health"); };
  }
}

/* ------------------------------------------------------------- detail */

/* The app's plugin detail page, read only: what the plugin is, where it
 * is installed, its folders and the skills it ships. Editing its files
 * and its display name stays in the Mac app. */
function openPlugin(ref) {
  const d = page.data.explore;
  const p = d && d.plugins[ref];
  if (!p) { page.sheet = null; return; }
  const sheet = bodySheet("plugin-sheet", "sheet");
  page.sheet = ref;
  sheet.onclose = () => { page.sheet = null; };
  let market = null;
  for (const s of d.sources) for (const m of s.marketplaces) if (!market && m.name === p.marketplace) market = m;
  const field = (label, value) => (value ? `<div class="field-row"><span class="caption">${label}</span><span>${value}</span></div>` : "");
  const comps = COMPONENTS.filter(([k]) => p.components[k]).map(([k, one, many]) => count(p.components[k], one, many)).join(" · ");
  const home = /^https?:\/\//i.test(p.homepage || "")
    ? `<a href="${safeHref(p.homepage)}" target="_blank" rel="noopener noreferrer">${esc(p.homepage)}</a>` : esc(p.homepage || "");
  const harnesses = Object.keys(p.harnesses).filter((h) => p.harnesses[h].status !== "absent");
  const pills = harnesses.map((h) => `<span class="badge pl-pill">${Fmt.logo(h, "logo logo-sm")}${esc(Fmt.harnessName(h))} · ${esc(STATUS_TITLE[p.harnesses[h].status])}</span>`).join("");
  const copies = harnesses.filter((h) => p.harnesses[h].path)
    .map((h) => field(`${esc(Fmt.harnessName(h))} copy`, `<code class="pl-path">${esc(p.harnesses[h].path)}</code>`)).join("");
  const skills = (p.skills || []).length ? `<div class="caption">Skills</div><div class="pl-skill-list">${p.skills.map((s) =>
    `<div class="pl-skill"><span class="pl-name">${esc(s.name)}</span><span class="caption">${esc(s.description || "No description.")}</span></div>`).join("")}</div>` : "";
  sheet.innerHTML = `<form method="dialog" class="sheet-body">
    <div class="sheet-title"><span>${esc(p.title)}</span>${p.version ? `<span class="caption">v${esc(p.version)}</span>` : ""}</div>
    <div class="pl-mono muted">${esc(p.ref)}</div>
    <div class="pl-line">${market ? `<span class="caption">${esc(market.title)}</span>` : ""}
      ${market && market.tag ? `<span class="badge">${esc(market.tag)}</span>` : ""}${pills}</div>
    ${p.description ? `<p class="pl-desc-full">${esc(p.description)}</p>` : ""}
    <div class="fields">
      ${field("Ships", esc(comps))}
      ${field("Category", esc(p.category || ""))}
      ${field("Author", esc(p.author || ""))}
      ${field("Homepage", home)}
      ${field("Tags", esc((p.tags || []).join(", ")))}
      ${field("Folder", p.source_path ? `<code class="pl-path">${esc(p.source_path)}</code>` : "")}
      ${copies}
    </div>
    ${skills}
    ${actionsSection(p, d)}
    <div class="sheet-actions"><button class="tab selected" value="close">Close</button></div>
  </form>`;
  bindActions(sheet);
  if (!sheet.open) sheet.showModal();
}

/* ------------------------------------------------- install, enable… */

const VERB = {
  install: ["Install", "Installed", "Installing…"], uninstall: ["Uninstall", "Uninstalled", "Uninstalling…"],
  enable: ["Enable", "Enabled", "Enabling…"], disable: ["Disable", "Disabled", "Disabling…"],
};
const sameAct = (a, b) => !!a && !!b && a.verb === b.verb && a.ref === b.ref && a.harness === b.harness;

/* PluginAction.command: what swe will run, or the edit it will make. */
function actionCommand(a) {
  if (a.harness === "codex" && a.verb === "install") return `codex plugin add ${a.ref}`;
  if (a.harness === "codex" && a.verb === "uninstall") return `codex plugin remove ${a.ref}`;
  if (a.harness === "codex") return `set [plugins."${a.ref}"] enabled = ${a.verb === "enable"} in ~/.codex/config.toml`;
  return `${a.harness} plugin ${a.verb} ${a.ref}`;
}

const actionWhere = (a) => `${a.verb === "uninstall" ? "from" : "in"} ${Fmt.harnessName(a.harness)}`;

/* One row per harness that has the plugin or could: its state, and the
 * buttons PluginCatalog.actions(for:in:) offers there. */
function actionsSection(p, d) {
  const where = (page.data.explore && page.data.explore.machine) || "the hub";
  const harnesses = Object.keys(p.harnesses).filter((h) => p.harnesses[h].status !== "absent" || p.harnesses[h].actions.length);
  if (!harnesses.length) return "";
  const busy = !!page.actRunning || page.fixing != null;
  const rows = harnesses.map((h) => {
    const st = p.harnesses[h], acts = st.actions || [];
    const running = page.actRunning && page.actRunning.ref === p.ref && page.actRunning.harness === h;
    let side;
    if (running) side = `<span class="pl-act-running" role="status"><span class="pl-spinner" aria-hidden="true"></span>${esc(VERB[page.actRunning.verb][2])}</span>`;
    else if (!(d.actions || []).includes(h)) side = `<span class="caption">swe can't change ${esc(Fmt.harnessName(h))} plugins</span>`;
    else side = acts.map((verb, i) => `<button type="button" class="pl-act${i === 0 && verb === "install" ? " primary" : ""}"
        data-pl-act="${esc(verb)}" data-pl-harness="${esc(h)}"${busy ? " disabled" : ""}>${esc(VERB[verb][0])}</button>`).join("");
    const a = page.actConfirm;
    const confirm = a && a.ref === p.ref && a.harness === h && !running ? `<div class="pl-act-confirm" role="group" aria-label="${esc(VERB[a.verb][0])} ${esc(a.ref)}?">
        <span>${esc(VERB[a.verb][0])} ${esc(a.ref)} ${esc(actionWhere(a))} on ${esc(where)}?</span>
        <code class="pl-path">${esc(actionCommand(a))}</code>
        <div class="pl-act-buttons"><button type="button" class="pl-act" data-pl-act-cancel>Cancel</button>
          <button type="button" class="pl-act primary${a.verb === "uninstall" ? " danger" : ""}" data-pl-act-go>${esc(VERB[a.verb][0])}</button></div></div>` : "";
    return `<div class="pl-act-row"><span class="pl-act-name">${Fmt.logo(h, "logo logo-sm")}${esc(Fmt.harnessName(h))}
      <span class="caption">· ${esc(STATUS_TITLE[st.status] || st.status)}</span></span>${side}</div>${confirm}`;
  }).join("");
  const o = page.actOutcome && page.actOutcome.ref === p.ref ? page.actOutcome : null;
  const outcome = o ? `<p class="${o.error ? "error pl-pre" : "caption pl-act-done"}" role="${o.error ? "alert" : "status"}">${esc(o.text)}</p>` : "";
  return `<div class="pl-acts"><div class="caption">In each harness</div>${rows}${outcome}</div>`;
}

function bindActions(sheet) {
  const ref = page.sheet;
  for (const btn of sheet.querySelectorAll("[data-pl-act]")) {
    btn.onclick = () => {
      const next = { verb: btn.dataset.plAct, ref, harness: btn.dataset.plHarness };
      page.actConfirm = sameAct(page.actConfirm, next) ? null : next;
      openPlugin(ref);
      const go = document.querySelector("[data-pl-act-go]");
      if (go) go.focus();
    };
  }
  const cancel = sheet.querySelector("[data-pl-act-cancel]");
  if (cancel) cancel.onclick = () => {
    const a = page.actConfirm;
    page.actConfirm = null;
    openPlugin(ref);
    const back = a && document.querySelector(`[data-pl-act="${CSS.escape(a.verb)}"][data-pl-harness="${CSS.escape(a.harness)}"]`);
    if (back) back.focus();
  };
  const go = sheet.querySelector("[data-pl-act-go]");
  if (go) go.onclick = () => runAction(page.actConfirm);
}

/* swe plugins <verb> <ref> --harness=<h> on the hub. Explore comes back in
 * the answer; Health may have changed too, so it reads again next time. */
async function runAction(a) {
  if (!a || page.actRunning) return;
  page.actRunning = a;
  page.actConfirm = null;
  page.actOutcome = null;
  if (onPage()) paint();
  const { status, data } = await apiPost("/v1/app/plugins/plugin", a);
  page.actRunning = null;
  if (status === 401) {
    if (onPage()) renderGate();
    return;
  }
  if (status === 200 && data && data.explore) {
    page.seq.explore++;
    page.busy.explore = false;
    page.data.explore = data.explore;
    page.error.explore = null;
    page.exploreStale = false;
    page.seq.health++;
    page.busy.health = false;
    page.data.health = null;
    if (page.tab === "health" && onPage()) load("health");
    const r = data.result;
    let text;
    if (data.error) text = data.error;
    else if (r.changed) text = `${VERB[a.verb][1]} ${a.ref} ${actionWhere(a)}`;
    else text = `${a.ref} in ${Fmt.harnessName(a.harness)}: ${r.detail || "nothing changed"}`;
    page.actOutcome = { ref: a.ref, text, error: !!data.error };
  } else {
    page.actOutcome = { ref: a.ref, error: true, text: failText(status, data) };
  }
  if (!onPage()) return;
  paint();
  const back = document.querySelector(`[data-pl-harness="${CSS.escape(a.harness)}"]`) || document.querySelector("#plugin-sheet .tab");
  if (back) back.focus();
}

registerPage("plugins", render);
