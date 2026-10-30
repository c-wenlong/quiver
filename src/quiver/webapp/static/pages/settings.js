/* Settings: a port of the Mac app's SettingsPage. The same grouped
 * sub-nav (underline tabs when the page is narrow), section titles and
 * cards of rows. What belongs to the hub machine works: Integrations ›
 * Remote (hub status, paired devices with Revoke, Pair a device with a QR
 * code), the GitHub CLI, the API keys (masked, with reveal, copy, Add
 * key, edit and delete, as the Mac app's Inference tab), the hub's paths. Appearance and Sidebar are kept per browser, in localStorage.
 * Rows that configure the Mac app itself are greyed out and say
 * "Mac app only". Experimental › Session search turns the hub's transcript
 * index on and off and shows what it holds. Writes: POST
 * /v1/app/settings/pair, /revoke, /session_search, /key_set and
 * /key_remove; /key_value answers one key's value for reveal and copy.
 *
 * This script also runs when the app loads, before any page is opened,
 * to apply the saved appearance and hide the pages switched off in
 * Sidebar: both are settings of the whole app.
 */
"use strict";

const SETTINGS_KEYS = {
  section: "quiver.settings.section",
  providersTab: "quiver.settings.providersTab",
  appearance: "quiver.appearance",
  hidden: "quiver.sidebar.hidden",
};

function settingsRead(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function settingsWrite(key, value) {
  try {
    if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch { /* storage off: the choice lasts until reload */ }
}

/* ------------------------------------------------- app-wide, on load */

/* System follows the browser (prefers-color-scheme); Light and Dark set
 * data-theme on <html>, which styles.css already honours. */
function settingsApplyAppearance(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.dataset.theme = mode;
  else delete root.dataset.theme;
}

// SidebarSection.isCore: Settings must stay reachable, and Overview and
// Accounts are the app's home.
const SETTINGS_CORE = new Set(["overview", "accounts", "settings"]);

function settingsHidden() {
  try {
    const list = JSON.parse(settingsRead(SETTINGS_KEYS.hidden) || "[]");
    return new Set((Array.isArray(list) ? list : []).filter((id) => typeof id === "string" && !SETTINGS_CORE.has(id)));
  } catch { return new Set(); }
}

/* app.js draws the sidebar on every route; a stylesheet hides the pages
 * switched off, and a group's heading once none of its pages show, so
 * nothing in app.js has to know about this setting. */
function settingsApplySidebar(hidden) {
  let style = document.getElementById("settings-sidebar-style");
  if (!style) {
    style = document.createElement("style");
    style.id = "settings-sidebar-style";
    document.head.appendChild(style);
  }
  const ids = [...hidden].filter((id) => /^[a-z]+$/.test(id));
  if (!ids.length) { style.textContent = ""; return; }
  const sel = (id) => `[data-page="${id}"]`;
  const items = ids.map((id) => `#sidebar .nav-item${sel(id)}`).join(",\n");
  const shown = `.nav-item${ids.map((id) => `:not(${sel(id)})`).join("")}`;
  style.textContent = `${items} { display: none !important; }\n`
    + `#sidebar .nav-group:not(:has(${shown})) { display: none !important; }`;
}

settingsApplyAppearance(settingsRead(SETTINGS_KEYS.appearance));
settingsApplySidebar(settingsHidden());

/* ---------------------------------------------------------------- page */

// SettingsGroup / SettingsSection: titles, one-line details and symbols.
const SETTINGS_GROUPS = [
  ["App", [
    ["general", "General", "When Quiver starts, how it looks, and which version you're running."],
    ["sidebar", "Sidebar", "Which pages the sidebar lists, and their order inside each group."],
    ["notifications", "Notifications", "Alerts when a usage limit runs low."],
  ]],
  ["Usage", [
    ["providers", "Providers", "Which harnesses show up across the app, and the inference plans and API keys behind them."],
  ]],
  ["Advanced", [
    ["integrations", "Integrations", "The GitHub CLI behind the GitHub pages, the keys for the services Quiver calls, and the phones paired to watch your sessions."],
    ["files", "Files", "Where Quiver finds swe, your API keys file and your MCP server folders, and the editor that opens them."],
    ["experimental", "Experimental", "Features being tried out. They may change or go away."],
  ]],
];
const SETTINGS_ORDER = SETTINGS_GROUPS.flatMap(([, sections]) => sections);

const SETTINGS_ICONS = {
  general: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  notifications: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>',
  providers: '<path d="m12 3 9 4.5-9 4.5-9-4.5zM3 12l9 4.5 9-4.5M3 16.5 12 21l9-4.5"/>',
  integrations: '<path d="M10 4a2 2 0 1 1 4 0v2h4v4h-2a2 2 0 1 0 0 4h2v4h-4v-2a2 2 0 1 0-4 0v2H6v-4H4a2 2 0 1 1 0-4h2V6h4z"/>',
  files: '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
  doctor: '<path d="M6 3v6a4 4 0 0 0 8 0V3M10 13v2a5 5 0 0 0 10 0v-2"/><circle cx="20" cy="11" r="2"/>',
  experimental: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M7 15h10"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h4v-3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  warn: '<path d="M12 4 2.5 20h19zM12 10v4M12 17.5v.01"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M3 3l18 18M10.6 5.1A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7a9.8 9.8 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
};
const settingsIcon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SETTINGS_ICONS[name] || ICON[name] || ""}</svg>`;

const settingsPage = {
  data: null, gen: 0, github: null, githubBusy: false,
  // Sync now: a sync in flight, and its failure or the owners it skipped.
  syncing: false, syncError: null,
  // `swe remote setup`'s steps for this machine, and a load in flight.
  // Stale once Settings is opened again: a step can come undone (the
  // last phone revoked, the hub stopped), so each visit checks again.
  setup: null, setupBusy: false, setupStale: false,
  query: "", status: "set", catalogQuery: "", catalog: false,
  // Remote writes: the minted ticket and which code it shows, the device
  // whose revoke is being confirmed, rows waiting on the hub, errors.
  ticket: null, target: "web", minting: false, pairError: null,
  showsAllDevices: false,
  confirming: null, revoking: new Set(), revokeErrors: {}, timer: null,
  // Tailnet pair requests: the row mid-decision, its last error, the
  // digits typed into each row (kept across repaints), and the poll
  // that relists while the section is open.
  deciding: null, decideErrors: {}, codeDrafts: {}, reqPoll: null,
  // Session search: a switch write in flight, its error, the poll that
  // runs while a pass does.
  searchBusy: false, searchError: null, searchPoll: null,
  // Bumped by every switch write and page visit: a status read that
  // started before one is stale and never lands.
  searchGen: 0,
  // Inference keys: values fetched for the eye (a Map, since a variable
  // can be named constructor; forgotten on the next visit or reload, and
  // revealGen drops an answer that lands after either), the Add key /
  // Edit sheet's draft, a reload in flight.
  revealed: new Map(), revealGen: 0, keyEditor: null, keyError: null, keysBusy: false,
};

function settingsSection() {
  const fromHash = new URLSearchParams(location.hash.split("?")[1] || "").get("section");
  const saved = fromHash || settingsRead(SETTINGS_KEYS.section);
  // GitHub, Remote and Services were their own sections before Integrations.
  if (saved === "github" || saved === "remote" || saved === "services") return "integrations";
  // Doctor moved to a card group on General; Paths was renamed Files.
  if (saved === "doctor") return "general";
  if (saved === "paths") return "files";
  return SETTINGS_ORDER.some(([id]) => id === saved) ? saved : "general";
}

function onSettingsPage() {
  return currentRoute() === "settings";
}

/* Masks every key value the eye revealed; bumping revealGen drops a
 * reveal still on its way. A visit, a reload of the keys, and leaving
 * the Providers keys view for another section or tab all do this, so a
 * value never comes back in plain text on the way back. */
function settingsForgetRevealed() {
  settingsPage.revealed = new Map();
  settingsPage.revealGen++;
}

async function renderSettings() {
  const gen = routeGen;
  const p = settingsPage;
  const mine = ++p.gen;
  p.searchGen++;
  settingsStopClock();
  p.ticket = null;
  p.confirming = null;
  p.setupStale = true;
  settingsForgetRevealed();
  p.keyEditor = null;
  if (p.data) paintSettings();
  else {
    document.getElementById("panel").innerHTML = `<div class="page">${settingsHeader()}
      <div class="card"><p class="empty">Reading the hub's settings…</p></div></div>`;
  }
  const { status, data } = await api("/v1/app/settings");
  if (gen !== routeGen || mine !== p.gen) return;
  const signedOut = p.signedOut;
  p.signedOut = false;
  if (status === 401) {
    return signedOut ? renderGate("This browser was signed out of the hub. Pair it again to come back.", true)
      : renderGate();
  }
  if (status !== 200 || !data) {
    if (!p.data) return pageError(settingsHeader(), status, data, renderSettings);
  } else {
    p.data = data;
  }
  paintSettings();
}

function settingsHeader() {
  return pageHeader("Settings", "How Quiver starts, what it shows, and where it finds things.");
}

function paintSettings() {
  const section = settingsSection();
  const meta = SETTINGS_ORDER.find(([id]) => id === section);
  const nav = SETTINGS_GROUPS.map(([group, sections]) => `<div class="set-nav-group">
      <div class="set-nav-heading">${esc(group)}</div>
      ${sections.map(([id, title]) => `<button class="set-nav-item${id === section ? " selected" : ""}" data-section="${id}"
        ${id === section ? 'aria-current="page"' : ""}>${settingsIcon(id)}<span>${esc(title)}</span></button>`).join("")}
    </div>`).join("");
  const tabs = SETTINGS_ORDER.map(([id, title]) =>
    `<button class="set-tab${id === section ? " selected" : ""}" data-section="${id}">${esc(title)}</button>`).join("");
  const version = settingsPage.data ? settingsPage.data.version : "";
  document.getElementById("panel").innerHTML = `<div class="page settings-page">
    ${settingsHeader()}
    <div class="set-layout">
      <nav class="set-nav" aria-label="Settings sections">${nav}</nav>
      <div class="set-tabs" role="tablist">${tabs}</div>
      <section class="set-section" aria-labelledby="set-section-title">
        <div class="set-section-head">
          <h2 class="set-section-title" id="set-section-title">${esc(meta[1])}</h2>
          <p class="set-section-detail">${esc(meta[2])}</p>
        </div>
        <div class="set-hairline"></div>
        <div class="set-body" id="set-body">${settingsBody(section)}</div>
      </section>
    </div>
    <footer class="set-footer caption">
      <span>Quiver ${esc(version)}</span><span>·</span>
      <a href="#/doctor">${settingsIcon("doctor")}Doctor</a><span>·</span>
      <a href="#/status">${icon("status")}Service status</a>
    </footer>
  </div>`;
  bindSettings(section);
  // Narrow: keep the current section's tab in view in the scrolling row.
  const tab = document.querySelector(".set-tab.selected");
  if (tab && tab.offsetParent) {
    const bar = tab.parentElement;
    bar.scrollLeft = Math.max(0, tab.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2);
  }
}

/* ------------------------------------------------------------ building */

function setRow(title, detail, control = "", opts = {}) {
  const warning = opts.warning
    ? `<div class="set-warning${opts.danger ? " danger" : ""}">${settingsIcon("warn")}<span>${esc(opts.warning)}</span></div>`
    : `<div class="set-detail">${opts.html ? detail : esc(detail)}</div>`;
  return `<div class="set-row${opts.cls ? " " + opts.cls : ""}"${opts.attrs || ""}>
    <div class="set-text"><div class="set-title">${opts.titleHtml || esc(title)}</div>${warning}</div>
    ${control ? `<div class="set-control">${control}</div>` : ""}</div>`;
}

/* A row that configures the Mac app itself: the app's wording, greyed,
 * with app.js's MAC_ONLY_BADGE (the sidebar's Mac-only items wear it too). */
function macRow(title, detail, extra = "") {
  return setRow(title, detail, `${extra}${MAC_ONLY_BADGE}`, { cls: "set-mac-only" });
}

/* Card(title:) puts its title inside the card, above the rows. */
function setCard(rows, title = "") {
  const head = title ? `<div class="set-card-title">${esc(title)}</div>` : "";
  return `<div class="set-card">${head}${rows.filter(Boolean).join('<div class="set-divider"></div>')}</div>`;
}

function setGroup(title, detail, content) {
  return `<div class="set-group"><div class="set-group-head"><div class="set-group-title">${esc(title)}</div>
    ${detail ? `<div class="caption">${esc(detail)}</div>` : ""}</div>${content}</div>`;
}

function setSwitch(id, on, opts = {}) {
  return `<button class="set-switch" role="switch" aria-checked="${on}" data-switch="${esc(id)}"
    aria-label="${esc(opts.label || id)}"${opts.disabled ? " disabled" : ""}${opts.title ? ` title="${esc(opts.title)}"` : ""}><span></span></button>`;
}

function settingsBody(section) {
  switch (section) {
    case "general": return generalSection();
    case "sidebar": return sidebarSection();
    case "notifications": return notificationsSection();
    case "providers": return providersSection();
    case "integrations": return integrationsSection();
    case "files": return filesSection();
    case "experimental": return experimentalSection();
    default: return "";
  }
}

/* ------------------------------------------------------------- General */

function generalSection() {
  const d = settingsPage.data;
  const mode = settingsRead(SETTINGS_KEYS.appearance) || "system";
  const appearance = `<div class="tabs set-segment" role="radiogroup" aria-label="Appearance">${
    [["system", "System"], ["light", "Light"], ["dark", "Dark"]].map(([id, label]) =>
      `<button class="tab${id === mode ? " selected" : ""}" role="radio" aria-checked="${id === mode}" data-appearance="${id}">${label}</button>`).join("")}</div>`;
  const where = d && d.machine ? ` on ${d.machine}` : "";
  return `<div class="set-stack">
    ${setCard([
      macRow("Open at login", "Start Quiver in the menu bar when you log in."),
      macRow("Terminal", "Used by Terminal, Sign in and Resume."),
      macRow("Show in Dock", "Off keeps Quiver out of the Dock and the app switcher. Open it from the menu bar."),
      setRow("First-run setup", "Pick the agents you use, link AGENTS.md and skills into each one, and choose the look and notifications.",
        `<a class="set-button" href="#/setup">Set up this machine</a>`),
    ])}
    ${setGroup("Display", "", setCard([
      setRow("Appearance", "System follows this browser's setting. Saved in this browser.", appearance),
      macRow("App icon", "The Dock shows this one. Finder keeps the light icon."),
      macRow("Hide accidental sessions", "Sessions with no responses, no tokens, or a one-shot probe drop out of Sessions and the calendar."),
    ]))}
    ${setGroup("Updates", "", setCard([
      setRow("Version", d ? `swe ${d.version}${where}` : "Reading…"),
      macRow("Latest release", "Check for Updates… installs a new release of the app."),
      macRow("Automatic checks", "Sparkle checks the appcast once a day."),
    ]))}
    ${setGroup("Doctor", "", setCard([
      setRow("Doctor", "Checks on PATH, Node and drift between quiver and your harnesses, on the hub.",
        `<a class="set-button" href="#/doctor">Open Doctor</a>`),
    ]))}
  </div>`;
}

/* ------------------------------------------------------------- Sidebar */

function sidebarSection() {
  const hidden = settingsHidden();
  const groups = typeof SIDEBAR !== "undefined" ? SIDEBAR : [];
  const all = groups.flatMap(([, items]) => items);
  const shown = all.filter(([id]) => SETTINGS_CORE.has(id) || !hidden.has(id)).length;
  const head = setRow("Sidebar pages", `${shown} / ${all.length} pages shown. Saved in this browser.`,
    `<div class="set-actions-segment"><button data-sidebar-all="on">Enable all</button><button data-sidebar-all="off">Disable all</button></div>`);
  const lists = groups.map(([group, items]) => `<div class="set-sidebar-group">
      <div class="set-sidebar-heading">${esc(group)}</div>
      ${items.map(([id, label]) => {
        const core = SETTINGS_CORE.has(id);
        const on = core || !hidden.has(id);
        return `<div class="set-sidebar-row">
          <span class="set-sidebar-icon">${icon(id)}</span>
          <span class="set-sidebar-label">${esc(label)}</span>
          ${core ? '<span class="caption muted">Always on</span>' : ""}
          ${setSwitch(`page:${id}`, on, { label, disabled: core, title: core ? "Core pages always show" : "" })}
        </div>`;
      }).join("")}
    </div>`).join("");
  return `<div class="set-card">${head}<div class="set-divider"></div><div class="set-sidebar">${lists}</div></div>
    <p class="caption set-note">Dragging pages into your own order stays in the Mac app.</p>`;
}

function settingsSetHidden(hidden) {
  settingsWrite(SETTINGS_KEYS.hidden, JSON.stringify([...hidden].sort()));
  settingsApplySidebar(hidden);
}

/* -------------------------------------------------------- Notifications */

function notificationsSection() {
  return setCard([
    macRow("Notify about limits", "When a limit reaches 90%, and again when it resets after that. Once per window."),
    macRow("Notify about GitHub", "New inbox notifications banner while the app runs; a click opens the repo's board. Hiding Inbox in Sidebar turns this off."),
    macRow("Refresh only what failed for", "Within this many minutes of a good reading, Refresh re-checks only the usage that failed."),
  ]);
}

/* ------------------------------------------------------------ Providers */

function providersSection() {
  const tab = settingsRead(SETTINGS_KEYS.providersTab) === "harnesses" ? "harnesses" : "inference";
  const tabs = `<div class="tabs set-segment" role="tablist">${[["harnesses", "Harnesses"], ["inference", "Inference"]].map(([id, label]) =>
    `<button class="tab${id === tab ? " selected" : ""}" role="tab" aria-selected="${id === tab}" data-providers-tab="${id}">${label}</button>`).join("")}</div>`;
  const body = tab === "harnesses"
    ? setCard([
      macRow("Provider visibility", "Which harnesses show up across the Mac app, in your own order."),
      setRow("Harnesses on the hub", "Star the ones you use and archive the ones you've ruled out.",
        `<a class="set-button" href="#/harnesses">Open Harnesses</a>`),
    ])
    : inferenceTab();
  return `<div class="set-stack">${tabs}${body}</div>`;
}

function inferenceTab() {
  const prov = settingsPage.data && settingsPage.data.providers;
  if (!prov) return `<div class="card"><p class="empty">Reading…</p></div>`;
  const rows = prov.keys || [];
  const withValue = rows.filter((r) => r.has_value).length;
  const p = settingsPage;
  const q = p.query.trim().toLowerCase();
  const visible = rows.filter((r) => (p.status === "all" || (p.status === "set") === r.has_value)
    && (!q || [r.name, r.provider || "Other"].some((s) => String(s).toLowerCase().includes(q))));
  const providers = new Set(rows.map((r) => r.provider));
  const subtitle = prov.exists ? `From ${prov.path}.` : `No keys file at ${prov.path} yet; Add key creates it.`;
  const filters = `<div class="filters set-filters">
    <select class="field" id="set-key-status" aria-label="Status">
      <option value="all"${p.status === "all" ? " selected" : ""}>Any status (${rows.length})</option>
      <option value="set"${p.status === "set" ? " selected" : ""}>Key set (${withValue})</option>
      <option value="none"${p.status === "none" ? " selected" : ""}>No key (${rows.length - withValue})</option>
    </select>
    <input class="field search" id="set-key-search" type="search" placeholder="Search providers or variables" value="${esc(p.query)}" aria-label="Search providers or variables">
    <span class="caption count num">${visible.length === 1 ? "1 row" : `${visible.length} rows`}</span>
  </div>`;
  const body = visible.length ? visible.map(settingsKeyRow).join("")
    : `<p class="empty set-pad">${rows.length ? "Nothing matches these filters." : `No keys yet. Add one and Quiver writes it to ${esc(prov.path)}.`}</p>`;
  return `<div class="set-stack">
    <div class="set-keys-top">
      <div class="set-keys-head"><div class="set-group-title">Keys and plans</div><div class="caption">${esc(subtitle)}</div></div>
      <div class="set-keys-actions">
        <button class="set-button" id="set-keys-reload"${p.keysBusy ? " disabled" : ""} title="Read the file again">${settingsIcon("rescan")}<span>Reload</span></button>
        <button class="set-button" id="set-key-catalog"${prov.stamp == null ? " disabled" : ""}><span>Catalogue</span></button>
        <button class="set-button" id="set-key-add"${prov.stamp == null ? " disabled" : ""}>${settingsIcon("plus")}<span>Add key</span></button>
      </div>
    </div>
    ${prov.error ? `<p class="error" role="alert">${esc(prov.error)}</p>` : ""}
    ${p.keyError ? `<p class="error" role="alert">${esc(p.keyError)}</p>` : ""}
    ${filters}
    <div class="set-table">
      <div class="set-key-row set-key-header"><div>Provider</div><div>Value</div><div class="set-key-type">Type</div><div>Status</div></div>
      ${body}
    </div>
    <div class="caption muted">${rows.length} ${rows.length === 1 ? "key" : "keys"} from ${providers.size} ${providers.size === 1 ? "provider" : "providers"}. Terminals already open keep the old values until you run <code>source ${esc(prov.path)}</code>. Checking keys, renaming, moving keys between providers, custom providers and coding plans stay in the Mac app.</div>
    ${settingsKeySheet()}
    ${settingsCatalogSheet()}
  </div>`;
}

/* The Mac app's ProviderCatalogueSheet, API-key providers only (coding
 * plans stay in the Mac app): search + a grid where a pick opens the
 * Add-key editor with that provider chosen. */
const PROVIDER_LOGOS = {
  openai: "harness-openai.svg", anthropic: "harness-claude-color.svg",
  gemini: "harness-gemini-color.svg", deepseek: "harness-deepseek-color.svg",
  zai: "harness-zai.svg", minimax: "harness-minimax-color.svg",
  kimi: "harness-kimi.svg", qwen: "harness-qwen-code-color.svg",
  mimo: "harness-xiaomimimo.svg", xai: "harness-grok.svg",
  stepfun: "harness-stepfun-color.svg", mistral: "harness-mistral-vibe-color.svg",
  upstage: "harness-upstage-color.svg", vercel_gateway: "harness-vercel.svg",
  opencode_zen: "harness-opencode-color.svg",
};

function settingsCatalogSheet() {
  const p = settingsPage;
  if (!p.catalog) return "";
  const prov = p.data.providers;
  const q = p.catalogQuery.trim().toLowerCase();
  const rows = [...(prov.catalog || [])].sort((a, b) => a.name.localeCompare(b.name))
    .filter((c) => !q || c.name.toLowerCase().includes(q) || c.env_vars.some((v) => v.toLowerCase().includes(q)));
  const cell = (c) => {
    const file = PROVIDER_LOGOS[c.id];
    const logo = file
      ? `<img class="set-cat-logo${file.endsWith("-color.svg") ? "" : " mono"}" src="/${file}" alt="">`
      : `<span class="set-cat-logo set-cat-mono" aria-hidden="true">${esc(c.name.slice(0, 1))}</span>`;
    return `<button class="set-cat-cell" data-cat-pick="${esc(c.id)}" title="Add ${esc(c.name)}'s key">
      ${logo}<span class="set-cat-name">${esc(c.name)}${c.has_key ? ' <span class="badge">Set</span>' : ""}</span>
      <span class="caption">${esc(c.env_vars.join(" · "))}</span></button>`;
  };
  return `<dialog class="sheet set-cat-sheet" id="set-cat-sheet" aria-labelledby="set-cat-title">
    <div class="sheet-body">
      <div class="sheet-title" id="set-cat-title">Provider catalogue</div>
      <input class="field search" id="set-cat-search" type="search" placeholder="Search providers" value="${esc(p.catalogQuery)}" aria-label="Search providers">
      <div class="set-cat-grid">${rows.length ? rows.map(cell).join("") : '<p class="empty">Nothing matches.</p>'}</div>
      <div class="set-key-foot"><span class="caption count num">${rows.length} of ${(prov.catalog || []).length}</span><span class="set-key-spacer"></span>
        <button class="set-button" data-cat-act="close">Close</button></div>
    </div>
  </dialog>`;
}

/* One row: the heading, the variable with its value masked (or revealed)
 * and the eye and copy buttons, the type and the status. Anywhere but
 * those buttons opens the key, as the app's row does. */
function settingsKeyRow(r) {
  const shown = settingsPage.revealed.get(r.name);
  const name = esc(r.name);
  const value = shown != null ? shown : r.masked;
  return `<div class="set-key-row set-key-open" data-key-open="${name}" role="button" tabindex="0" aria-label="Edit ${name}" title="Click to edit ${name}">
    <div class="set-key-name"><span class="name">${esc(r.provider || "Other")}</span></div>
    <div class="set-key-value"><code class="muted">${name}</code>${r.has_value ? `<span class="set-masked num${shown != null ? " revealed" : ""}">${esc(value)}</span>
      <span class="set-key-tools">
        <button class="icon-button set-key-icon" data-key-reveal="${name}" aria-label="${shown != null ? "Hide" : "Show"} the value" title="${shown != null ? "Hide" : "Show"} the value">${settingsIcon(shown != null ? "eyeOff" : "eye")}</button>
        <button class="icon-button set-key-icon" data-key-copy="${name}" aria-label="Copy the value" title="Copy the value">${settingsIcon("copy")}</button>
      </span>` : ""}</div>
    <div class="set-key-type caption">API key</div>
    <div class="set-key-status ${r.has_value ? "ok" : "muted"}">${r.has_value ? "Key set" : "No key"}</div>
  </div>`;
}

/* Add a key / Edit NAME, the app's KeyEditor without Test, rename and
 * move. A new key picks a catalog provider and is saved as its variable;
 * an edit replaces the value where it sits. The value field is a
 * password field the browser won't fill, emptied after every save. */
function settingsKeySheet() {
  const e = settingsPage.keyEditor;
  if (!e) return "";
  const prov = settingsPage.data.providers;
  const catalog = [...(prov.catalog || [])].sort((a, b) => a.name.localeCompare(b.name));
  const picked = e.isNew ? catalog.find((c) => c.id === e.provider) : null;
  const name = settingsKeyName(e);
  const existing = (prov.keys || []).find((k) => k.name === name);
  const row = e.isNew ? null : existing;
  const link = picked ? picked.url : row && (catalog.find((c) => c.env_vars.includes(row.name)) || {}).url;
  let fields = "";
  if (e.isNew) {
    fields += `<label class="set-form-field"><span class="caption">Provider</span>
      <select class="field" id="set-key-provider"${e.working ? " disabled" : ""}>
        <option value=""${picked ? "" : " selected"}>Pick a provider</option>
        ${catalog.map((c) => `<option value="${esc(c.id)}"${c.id === e.provider ? " selected" : ""}>${esc(c.name)}</option>`).join("")}
      </select></label>`;
    if (picked) {
      const savedAs = picked.env_vars.length > 1
        ? `<span class="caption">saved as</span><select class="field set-key-var" id="set-key-var" aria-label="Variable"${e.working ? " disabled" : ""}>${picked.env_vars.map((v) =>
            `<option value="${esc(v)}"${v === name ? " selected" : ""}>${esc(v)}</option>`).join("")}</select>`
        : `<code class="caption">saved as ${esc(name)}</code>`;
      fields += `<div class="set-key-saved">${savedAs}<span class="set-key-spacer"></span>${safeHref(link) ? `<a class="caption" href="${safeHref(link)}" target="_blank" rel="noopener noreferrer">Get a key</a>` : ""}</div>`;
      if (existing) fields += `<p class="caption set-warn">${esc(name)} is already set; saving replaces it.</p>`;
    }
  } else {
    fields += `<div class="set-key-saved"><span class="caption">Under</span><span>${esc(row ? row.provider || "Other" : "")}</span>
      <span class="set-key-spacer"></span>${safeHref(link) ? `<a class="caption" href="${safeHref(link)}" target="_blank" rel="noopener noreferrer">Get a key</a>` : ""}</div>`;
  }
  fields += `<label class="set-form-field"><span class="caption">Value</span>
    <span class="set-key-input-row"><input class="field set-key-input" id="set-key-value" type="${e.show ? "text" : "password"}"
      autocomplete="new-password" spellcheck="false" autocapitalize="off" placeholder="${e.isNew ? "sk-…" : "Paste a new value"}"
      value="${esc(e.value)}"${e.working ? " disabled" : ""}>
    <button type="button" class="icon-button set-key-icon" id="set-key-show" aria-label="${e.show ? "Hide" : "Show"} the value" title="${e.show ? "Hide" : "Show"} the value">${settingsIcon(e.show ? "eyeOff" : "eye")}</button></span></label>`;
  const canSave = !e.working && name && e.value.trim() && (!e.isNew || picked);
  const left = e.isNew ? "" : e.confirmingDelete
    ? `<span class="caption">Delete ${esc(e.name)}?</span>
       <button class="set-button danger" data-key-act="delete-confirm"${e.working ? " disabled" : ""}>Delete</button>
       <button class="set-button" data-key-act="keep">Keep</button>`
    : `<button class="set-button set-danger" data-key-act="delete" title="Delete ${esc(e.name)} from the keys file"${e.working ? " disabled" : ""}>Delete</button>`;
  return `<dialog class="sheet set-key-sheet" id="set-key-sheet" aria-labelledby="set-key-title">
    <div class="sheet-body set-key-form">
      <div class="sheet-title" id="set-key-title">${e.isNew ? "Add a key" : `Edit ${esc(e.name)}`}</div>
      ${fields}
      ${e.error ? `<p class="error" role="alert">${esc(e.error)}</p>` : ""}
      <div class="set-key-foot">${left}<span class="set-key-spacer"></span>
        <button class="set-button" data-key-act="cancel">Cancel</button>
        <button class="set-button primary" data-key-act="save"${canSave ? "" : " disabled"}>${e.working === "save" ? "Saving…" : e.isNew ? "Add key" : "Save"}</button></div>
    </div>
  </dialog>`;
}

function settingsKeyName(e) {
  if (!e.isNew) return e.name;
  const picked = (settingsPage.data.providers.catalog || []).find((c) => c.id === e.provider);
  if (!picked) return "";
  return picked.env_vars.includes(e.envVar) ? e.envVar : picked.env_vars[0] || "";
}

/* --------------------------------------------------------- Integrations */

function integrationsSection() {
  return `<div class="set-stack wide">
    ${setGroup("GitHub", "The gh install behind the GitHub pages, and the synced repo catalog the Add-repo search reads.", githubCard())}
    ${setGroup("Services", "", setCard([
      macRow("Service keys", "Keys for the services Quiver itself calls, kept in the Mac's Keychain, with Test and Replace."),
    ]))}
    ${setGroup("Remote", "Pair a phone to watch and steer sessions, and see which devices are paired.", remoteCards())}
  </div>`;
}

function githubCard() {
  const g = settingsPage.github;
  const refresh = `<button class="icon-button${settingsPage.githubBusy ? " spinning" : ""}" id="set-gh-refresh" aria-label="Check gh again" title="Check gh again"${settingsPage.githubBusy ? " disabled" : ""}>${icon("rescan")}</button>`;
  let detail = "Not checked yet.", warning = null;
  if (g && g.error) detail = g.error;
  else if (g && g.env) {
    const env = g.env;
    detail = {
      ok: env.login ? `Signed in as ${env.login}.` : "Signed in.",
      unavailable: "Not installed.",
      "auth-required": "Not signed in.",
    }[env.status] || env.error || "The gh probe failed.";
    if (env.status === "unavailable") warning = "Download gh cli: brew install gh";
    if (env.status === "auth-required") warning = "Run gh auth login";
  }
  let catalog = "Not synced yet — the catalog is the repo list the Add-repository search reads.";
  if (g && g.catalog) {
    const ago = g.catalog.synced_at ? ` · synced ${Fmt.ago(g.catalog.synced_at)}` : "";
    catalog = `${g.catalog.repos} repos${ago} — the Add-repository search reads this list.`;
  }
  // The app's Sync now: off while one runs or while gh can't run.
  const blocked = !g || !g.env || g.env.status !== "ok";
  const p = settingsPage;
  const sync = `<button class="set-button primary" id="set-gh-sync"${p.syncing || blocked ? " disabled" : ""}
      title="${blocked ? "Sign gh in on the hub first" : "List every repo your account and its orgs can see"}">${p.syncing ? "Syncing…" : "Sync now"}</button>`;
  return setCard([
    setRow("GitHub CLI", detail, refresh, { warning }),
    setRow("Repository catalog", catalog, sync, { warning: p.syncError }),
  ]);
}

function settingsUptime(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}


function remoteCards() {
  const d = settingsPage.data;
  if (!d) return `<div class="set-card">${setRow("Checking hub…", "swe remote status")}</div>`;
  const r = d.remote;
  const bits = [r.hub.machine && r.hub.machine.name, r.public_url || r.hub_addr,
    r.hub.uptime_s != null ? `up ${settingsUptime(r.hub.uptime_s)}` : null].filter(Boolean);
  const hub = setCard([setRow("Hub running", bits.join(" · ") || "Connected", '<span class="badge set-online">Online</span>')], "Hub");
  const live = r.devices.filter((x) => !x.revoked);
  const p = settingsPage;
  const pair = `<div class="set-pair-row">
      <button class="set-button primary" id="set-pair"${p.minting ? " disabled" : ""}>${settingsIcon("qr")}${p.minting ? "Pairing…" : "Pair a device"}</button>
      ${p.pairError ? `<span class="caption set-danger">${esc(p.pairError)}</span>` : ""}
    </div>`;
  // The hub owner's own tailnet device gets in without pairing, so it
  // has no row here: it is listed under "Signed in through Tailscale".
  const viaTailnet = r.caller && r.caller.kind === "tailnet"
    ? `<p class="caption set-caller">This browser is signed in through Tailscale as the hub's owner, so it isn't a paired device. It's listed under Signed in through Tailscale.</p>`
    : "";
  // The Mac folds the list after two rows; the Pair row sits under it.
  const shown = p.showsAllDevices ? live : live.slice(0, 2);
  const fold = live.length > 2
    ? `<button class="set-button set-fold" id="set-devices-fold" title="${p.showsAllDevices ? "Show only the first 2" : "Show every paired device"}">${p.showsAllDevices ? "Show less" : `Show ${live.length - 2} more`}</button><div class="set-divider"></div>`
    : "";
  const devices = live.length
    ? `<div class="set-card"><div class="set-card-title">Paired devices</div>${viaTailnet}${shown.map(deviceRow).join('<div class="set-divider"></div>')}<div class="set-divider"></div>${fold}${pair}</div>`
    : `<div class="set-card"><div class="set-card-title">Paired devices</div>${viaTailnet}<p class="caption set-empty">No devices paired yet.</p>${pair}</div>`;
  // Devices that asked to pair through Tailscale wait for the code they
  // show: the old /#approve page's rows, now inline here.
  const requests = (r.requests || []).length ? setCard(
    r.requests.map(requestRow), "Waiting for approval") : "";
  // The owner's own tailnet devices, let in without pairing: revoking a
  // lost one here is the only way to stop it short of the Tailscale
  // admin console.
  const signins = (r.signins || []).filter((x) => !x.revoked);
  const tailnet = signins.length ? setCard(signins.map(signinRow), "Signed in through Tailscale") : "";
  return `<div class="set-stack">${setupCard()}${hub}${devices}${tailnet}${requests}</div>${pairSheet()}`;
}

/* `swe remote setup`'s steps, while any is left: each with its state and
 * what to do. Steps only a person can do link to the page; the ones Quiver
 * does itself are run from the Mac app or the CLI, never from here, since
 * moving the hub could cut off this very page. */
const SETUP_BADGE = {
  ok: '<span class="badge set-online">Done</span>',
  todo: '<span class="badge">Quiver does this</span>',
  needs_you: '<span class="badge set-needs">Needs you</span>',
  failed: '<span class="badge set-failed">Failed</span>',
  blocked: '<span class="badge set-waiting">Waiting</span>',
};

function setupCard() {
  const p = settingsPage;
  const s = p.setup;
  if (!s) return p.setupBusy ? `<div class="set-card">${setRow("Checking setup…", "Tailscale, the certificate, the hub and tailscale serve")}</div>` : "";
  if (s.error) {
    return setCard([setRow("Couldn't check setup", s.error, '<button class="set-button" id="set-setup-recheck">Check again</button>', { warning: s.error, danger: true })], "Set up remote control");
  }
  if (s.ready) return "";
  const rows = s.steps.map(setupRow);
  // A failed step Quiver retries (a hub that stopped) is fixed the same
  // way as one it hasn't done yet.
  const quiverSteps = s.steps.some((x) => x.state === "todo"
    || (x.state === "failed" && x.action && x.action.kind === "apply"));
  const foot = quiverSteps
    ? setRow("Steps Quiver does itself", "Open Settings › Remote › Set up remote control in the Quiver Mac app, or run swe remote setup --apply on the hub machine.",
        '<button class="set-button" id="set-setup-recheck">Check again</button>')
    : setRow("Done one of these?", "Steps you finish in a browser show here when you check again.",
        '<button class="set-button" id="set-setup-recheck">Check again</button>');
  return setCard([...rows, foot], "Set up remote control");
}

function setupRow(step) {
  const a = step.action || {};
  let control = SETUP_BADGE[step.state] || "";
  const live = step.state === "needs_you" || step.state === "failed";
  if (live && a.kind === "open" && safeHref(a.url)) {
    control += ` <a class="set-button" href="${safeHref(a.url)}" target="_blank" rel="noopener">${esc(a.label || "Open")}</a>`;
  } else if (live && a.kind === "command" && a.command) {
    const label = a.label || "Copy command";
    control += ` <button class="icon-button" data-setup-copy="${esc(step.id)}" aria-label="${esc(label)}" title="${esc(label)}">${Fmt.copyIcon()}</button>`;
  } else if (live && a.kind === "pair") {
    control += ` <button class="set-button primary" data-setup-pair>${esc(a.label || "Pair a phone")}</button>`;
  }
  let detail = step.detail || "";
  if (live && a.kind === "command" && a.command) detail = detail ? `${detail} (${a.command})` : a.command;
  if (live && a.kind === "launch") detail = detail || `Open ${a.app || "the app"} on the hub machine.`;
  return setRow(step.title, detail, control,
    step.state === "failed" ? { warning: detail, danger: true } : { cls: step.state === "blocked" ? "set-step-waiting" : "" });
}

async function settingsLoadSetup() {
  const p = settingsPage;
  const gen = routeGen;
  p.setupBusy = true;
  p.setupStale = false;
  if (onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote();
  let status = 0, data = null;
  try { ({ status, data } = await api("/v1/app/settings?part=remote_setup")); } catch { status = 0; }
  p.setupBusy = false;
  p.setup = status === 200 && data
    ? data.remote_setup
    : { error: failText(status, data, null) };
  if (gen === routeGen && onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote();
}

/* One pending pair request: name and node, the code field the asking
 * device shows, and the two decisions. ``request_id`` pins the answer so
 * a code meant for another request can't land here. The field's value is
 * kept in ``codeDrafts`` and its id lets a repaint hand focus back, so a
 * poll or a sibling card's refresh can't eat digits mid-typing. */
function requestRow(q) {
  const p = settingsPage;
  const detail = [q.node_name, q.login, q.peer_ip,
    `expires in ${Math.max(0, Math.round(q.expires_in_s / 60))} min`].filter(Boolean).join(" · ");
  const busy = p.deciding === q.request_id;
  const control = `<span class="set-decide" data-request="${esc(q.request_id)}">
    <input class="field set-code" id="set-code-${esc(q.request_id)}" inputmode="numeric" maxlength="7"
      placeholder="000 000" aria-label="Code the device shows" data-code
      value="${esc(p.codeDrafts[q.request_id] || "")}"${busy ? " disabled" : ""}>
    <button class="set-button primary" data-act="approve"${busy ? " disabled" : ""}>Approve</button>
    <button class="set-button" data-act="deny"${busy ? " disabled" : ""}>Deny</button></span>`;
  return setRow(q.name || "device", detail, control,
    { warning: p.decideErrors[q.request_id] || null });
}

function deviceRow(dev) {
  const p = settingsPage;
  // The name it renamed itself to, then what it paired as — the fields
  // the hub recorded, ahead of the id this row always showed.
  const name = dev.display_name || dev.name || "Device";
  const bits = [];
  for (const f of [dev.kind, dev.model, dev.client, dev.os, dev.node_name]) {
    if (f) bits.push(f);
  }
  bits.push(dev.id);
  if (dev.last_seen_at) bits.push(`seen ${Fmt.relative(dev.last_seen_at)}`);
  if (dev.paired_via === "tailscale") bits.push("paired with Tailscale");
  const title = `${esc(name)}${dev.this_device ? ' <span class="badge set-this">This device</span>' : ""}`;
  const busy = p.revoking.has(dev.id);
  const attrs = ` data-device="${esc(dev.id)}"`;
  if (p.confirming === dev.id) {
    const what = dev.this_device
      ? "This is the browser you're using. Revoking signs its pairing out of the hub now. Unless this device also signs in through Tailscale as the hub's owner, every page here stops working until you pair it again with a new code."
      : `Sign ${name} out of the hub? It stops working at once and needs a new pair code to come back.`;
    return `<div class="set-row set-confirm"${attrs}>
      <div class="set-text"><div class="set-title">${title}</div><div class="set-detail">${esc(what)}</div></div>
      <div class="set-control">
        <button class="set-button" data-act="cancel"${busy ? " disabled" : ""}>Cancel</button>
        <button class="set-button danger" data-act="revoke-confirm"${busy ? " disabled" : ""}>${busy ? "Revoking…" : dev.this_device ? "Revoke this browser" : "Revoke"}</button>
      </div></div>`;
  }
  return setRow(name, bits.join(" · "),
    `<button class="set-button" data-act="revoke" title="Sign this device out of the hub"${busy ? " disabled" : ""}>Revoke</button>`,
    { titleHtml: title, warning: p.revokeErrors[dev.id] || null, danger: true, attrs });
}

function signinRow(s) {
  const p = settingsPage;
  const name = s.node_name || "Tailscale device";
  const bits = ["your Tailscale login"];
  if (s.last_seen_at) bits.push(`seen ${Fmt.relative(s.last_seen_at)}`);
  bits.push(s.id);
  const title = `${esc(name)}${s.this_device ? ' <span class="badge set-this">This device</span>' : ""}`;
  const busy = p.revoking.has(s.id);
  const attrs = ` data-device="${esc(s.id)}"`;
  if (p.confirming === s.id) {
    const what = s.this_device
      ? "This is the browser you're using. Revoking stops this device getting in through Tailscale, so every page here stops working until you pair it with a new code."
      : `Sign ${name} out of the hub? It stops getting in through Tailscale at once, and any device paired from it is signed out too. Pairing it again brings it back.`;
    return `<div class="set-row set-confirm"${attrs}>
      <div class="set-text"><div class="set-title">${title}</div><div class="set-detail">${esc(what)}</div></div>
      <div class="set-control">
        <button class="set-button" data-act="cancel"${busy ? " disabled" : ""}>Cancel</button>
        <button class="set-button danger" data-act="revoke-confirm"${busy ? " disabled" : ""}>${busy ? "Revoking…" : s.this_device ? "Revoke this browser" : "Revoke"}</button>
      </div></div>`;
  }
  return setRow(name, bits.join(" · "),
    `<button class="set-button" data-act="revoke" title="Stop this device getting in through Tailscale"${busy ? " disabled" : ""}>Revoke</button>`,
    { titleHtml: title, warning: p.revokeErrors[s.id] || null, danger: true, attrs });
}

function pairSheet() {
  const t = settingsPage.ticket;
  if (!t) return "";
  const target = settingsPage.target;
  const payload = target === "web" ? t.url : t.deep_link;
  const instructions = target === "web"
    ? "On your phone, scan the code to pair the browser and open the web app, then Add to Home Screen to keep it."
    : "On your phone, scan the code to open the pairing link in the Quiver app.";
  return `<dialog class="sheet set-pair-sheet" id="set-pair-sheet" aria-labelledby="set-pair-title">
    <div class="sheet-body">
      <div class="set-sheet-head"><div class="sheet-title" id="set-pair-title">Pair a device</div>
        <button class="set-button" data-act="done">Done</button></div>
      <div class="set-pair">
        <div class="set-qr">${target === "web" ? t.qr.web : t.qr.app}</div>
        <div class="set-pair-side">
          <div class="tabs set-segment" role="tablist">${[["web", "Browser"], ["app", "Quiver app"]].map(([id, label]) =>
            `<button class="tab${id === target ? " selected" : ""}" role="tab" aria-selected="${id === target}" data-target="${id}">${label}</button>`).join("")}</div>
          <p class="caption">${esc(instructions)}</p>
          <code class="set-payload" id="set-payload">${esc(payload)}</code>
          <div class="set-pair-actions">
            <button class="set-button" data-act="copy">${settingsIcon("copy")}<span>Copy link</span></button>
            <span id="set-countdown">${settingsCountdown()}</span>
          </div>
        </div>
      </div>
    </div>
  </dialog>`;
}

function settingsCountdown() {
  const t = settingsPage.ticket;
  if (!t) return "";
  const left = Math.max(0, Math.round(t.expires_at_local - Date.now() / 1000));
  if (left > 0) {
    return `<span class="caption set-clock">${settingsIcon("clock")}Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}</span>`;
  }
  return `<button class="set-button" data-act="remint">Expired — mint a new code</button>`;
}

function settingsStopClock() {
  if (settingsPage.timer) clearInterval(settingsPage.timer);
  settingsPage.timer = null;
}

/* ----------------------------------------------------------------- Files */

function filesSection() {
  const paths = settingsPage.data && settingsPage.data.paths;
  if (!paths) return `<div class="card"><p class="empty">Reading…</p></div>`;
  return `<div class="set-stack">${setCard([
    setRow("swe executable", paths.swe ? `In use: ${paths.swe}.` : "swe isn't on the hub's PATH."),
    setRow("API keys file", `In use: ${paths.api_keys}.`),
    setRow("MCP servers folder", `In use: ${paths.mcp_servers}.`),
    macRow("Editor", "Opens a server folder from the MCP servers page."),
  ])}<p class="caption set-note">These are the hub's paths. Pointing Quiver somewhere else is a Mac app setting.</p></div>`;
}

/* ---------------------------------------------------------- Experimental */

const SEARCH_POLL_MS = 3000;

function experimentalSection() {
  const p = settingsPage;
  const ss = p.data && p.data.session_search;
  // A hub from before the Python indexer sends no status: the row stays
  // the Mac app's.
  if (!ss) {
    return setCard([
      macRow("Session search", "Index the text of every session transcript, so search can match what was said, not only titles and paths.",
        '<span class="badge">Experimental</span>'),
    ]);
  }
  const detail = "Index the text of agent transcripts on the hub, so search can match what was said, not only titles and paths.";
  const problem = p.searchError || (ss.error ? `Couldn't read the index: ${ss.error}`
    : ss.last_error ? `The last pass failed: ${ss.last_error.message}` : "");
  const status = `<div class="set-search-status">${esc(searchStatusLine(ss))}</div>`;
  const warn = problem
    ? `<div class="set-warning">${settingsIcon("warn")}<span>${esc(problem)}</span></div>` : "";
  // The Mac's SessionSearchRow: resync and Clear search data ride the
  // status line while the index is on, and stop answering mid-pass.
  const actions = ss.enabled ? `<div class="set-search-actions">
      <button class="icon-button set-key-icon" id="set-search-resync" title="Read every transcript again now" aria-label="Re-index every transcript"${ss.running || p.searchBusy ? " disabled" : ""}>${settingsIcon("rescan")}</button>
      <button class="set-button" id="set-search-clear"${ss.running || p.searchBusy ? " disabled" : ""}>Clear search data</button>
    </div>` : "";
  return setGroup("Session index", "How Quiver finds what a session said, not only its titles and paths.",
    setCard([
      setRow("Session search", `${esc(detail)}${status}${warn}${actions}`,
        `<span class="badge">Experimental</span>${setSwitch("session-search", !!ss.enabled,
          { label: "Session search", disabled: p.searchBusy })}`,
        { html: true }),
    ]));
}

/* The line under the switch: Mac SearchIndexModel.statusLine's wording. */
function searchStatusLine(ss) {
  const n = (count, noun) => `${Number(count || 0).toLocaleString("en-US")} ${noun}${count === 1 ? "" : "s"}`;
  const files = ss.files || {};
  const totals = `${n(ss.sessions, "session")}, ${n(ss.messages, "message")}`;
  let line;
  if (ss.running) {
    line = ss.last_pass ? `Indexing now · ${totals} so far`
      : "Indexing: the first pass reads every transcript and can take a few minutes.";
  } else if (!ss.enabled) {
    line = ss.exists && ss.sessions ? `Off. The index still holds ${totals}.` : "Off.";
  } else if (!ss.exists) {
    line = "The index hasn't run yet.";
  } else {
    line = `Synced: ${totals}`;
    if (ss.last_pass && ss.last_pass.finished_at) line += ` · last pass ${Fmt.ago(ss.last_pass.finished_at)}`;
  }
  if (files.due) line += ` · ${n(files.due, "file")} behind`;
  if (files.failed) line += ` · ${n(files.failed, "file")} failed`;
  return line;
}

async function settingsSetSearch(on) {
  const p = settingsPage;
  p.searchBusy = true;
  p.searchError = null;
  const gen = ++p.searchGen;
  paintSettings();
  const { status, data } = await apiPost("/v1/app/settings/session_search", { enabled: on });
  p.searchBusy = false;
  if (gen !== p.searchGen) return;
  if (status === 200 && data && data.session_search) {
    if (p.data) p.data.session_search = data.session_search;
  } else {
    p.searchError = failText(status, data, null);
  }
  if (!onSettingsPage()) return;
  paintSettings();
  document.querySelector('[data-switch="session-search"]')?.focus();
  settingsPollSearch();
}

/* Resync and Clear search data, the two buttons on the index row. */
async function settingsSearchAction(body, focus) {
  const p = settingsPage;
  p.searchBusy = true;
  p.searchError = null;
  const gen = ++p.searchGen;
  paintSettings();
  const { status, data } = await apiPost("/v1/app/settings/session_search", body);
  p.searchBusy = false;
  if (gen !== p.searchGen) return;
  if (status === 200 && data && data.session_search) {
    if (p.data) p.data.session_search = data.session_search;
  } else {
    p.searchError = failText(status, data, null);
  }
  if (!onSettingsPage()) return;
  paintSettings();
  if (focus) document.querySelector(focus)?.focus();
  settingsPollSearch();
}

/* While a pass runs, re-read the status every few seconds; stop once it
 * settles or the section is left. */
function settingsPollSearch() {
  const p = settingsPage;
  clearTimeout(p.searchPoll);
  const ss = p.data && p.data.session_search;
  if (!ss || !ss.running) return;
  p.searchPoll = setTimeout(async () => {
    if (!onSettingsPage() || settingsSection() !== "experimental") return;
    const gen = p.searchGen;
    const { status, data } = await api("/v1/app/settings");
    if (gen !== p.searchGen) return;
    if (status === 200 && data && data.session_search && p.data) {
      p.data.session_search = data.session_search;
      if (onSettingsPage() && settingsSection() === "experimental") {
        const sw = document.activeElement && document.activeElement.dataset
          ? document.activeElement.dataset.switch : null;
        paintSettings();
        if (sw) document.querySelector(`[data-switch="${sw}"]`)?.focus();
      }
    }
    settingsPollSearch();
  }, SEARCH_POLL_MS);
}

/* --------------------------------------------------------------- binding */

function bindSettings(section) {
  const panel = document.getElementById("panel");
  for (const btn of panel.querySelectorAll("[data-section]")) {
    btn.onclick = () => {
      settingsWrite(SETTINGS_KEYS.section, btn.dataset.section);
      settingsForgetRevealed();
      // A ?section= link picked the first one; a click wins from here on.
      if (location.hash.includes("?")) history.replaceState(null, "", "#/settings");
      paintSettings();
      const again = document.querySelector(`.set-nav-item[data-section="${btn.dataset.section}"]`);
      const tab = document.querySelector(`.set-tab[data-section="${btn.dataset.section}"]`);
      (again && again.offsetParent ? again : tab || again)?.focus();
    };
  }
  const nav = panel.querySelector(".set-nav");
  if (nav) {
    nav.onkeydown = (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      const at = SETTINGS_ORDER.findIndex(([id]) => id === settingsSection());
      const next = SETTINGS_ORDER[Math.min(Math.max(at + (e.key === "ArrowDown" ? 1 : -1), 0), SETTINGS_ORDER.length - 1)][0];
      nav.querySelector(`[data-section="${next}"]`).click();
    };
  }
  for (const btn of panel.querySelectorAll("[data-appearance]")) {
    btn.onclick = () => {
      const mode = btn.dataset.appearance;
      settingsWrite(SETTINGS_KEYS.appearance, mode === "system" ? null : mode);
      settingsApplyAppearance(mode);
      paintSettings();
    };
  }
  for (const sw of panel.querySelectorAll("[data-switch^='page:']")) {
    sw.onclick = () => {
      const id = sw.dataset.switch.slice(5);
      const hidden = settingsHidden();
      if (hidden.has(id)) hidden.delete(id); else hidden.add(id);
      settingsSetHidden(hidden);
      paintSettings();
      document.querySelector(`[data-switch="page:${id}"]`)?.focus();
    };
  }
  const searchSwitch = panel.querySelector('[data-switch="session-search"]');
  if (searchSwitch) {
    searchSwitch.onclick = () => settingsSetSearch(searchSwitch.getAttribute("aria-checked") !== "true");
    settingsPollSearch();
  }
  const resync = panel.querySelector("#set-search-resync");
  if (resync) resync.onclick = () => settingsSearchAction({ resync: true }, "#set-search-resync");
  const clear = panel.querySelector("#set-search-clear");
  if (clear) clear.onclick = () => settingsSearchAction({ clear: true }, "#set-search-clear");
  for (const btn of panel.querySelectorAll("[data-sidebar-all]")) {
    btn.onclick = () => {
      const all = (typeof SIDEBAR !== "undefined" ? SIDEBAR : []).flatMap(([, items]) => items.map(([id]) => id));
      settingsSetHidden(new Set(btn.dataset.sidebarAll === "on" ? [] : all.filter((id) => !SETTINGS_CORE.has(id))));
      paintSettings();
    };
  }
  for (const btn of panel.querySelectorAll("[data-providers-tab]")) {
    btn.onclick = () => {
      settingsWrite(SETTINGS_KEYS.providersTab, btn.dataset.providersTab);
      settingsForgetRevealed();
      paintSettings();
    };
  }
  const status = document.getElementById("set-key-status");
  if (status) status.onchange = () => { settingsPage.status = status.value; paintSettings(); };
  const search = document.getElementById("set-key-search");
  if (search) {
    bindSearch(search, (text) => { settingsPage.query = text; }, () => {
      const caret = search.selectionStart;
      paintSettings();
      const again = document.getElementById("set-key-search");
      if (again) { again.focus(); try { again.setSelectionRange(caret, caret); } catch { /* not text */ } }
    });
  }
  if (section === "integrations") bindRemote();
  bindKeys();
}

/* ------------------------------------------------- Inference keys */

function bindKeys() {
  const p = settingsPage;
  const reload = document.getElementById("set-keys-reload");
  if (!reload) return;
  reload.onclick = () => settingsReloadKeys("#set-keys-reload");
  document.getElementById("set-key-add").onclick = () => {
    p.keyEditor = { isNew: true, provider: "", envVar: "", value: "", show: false };
    settingsKeyRepaint("#set-key-provider");
  };
  const cat = document.getElementById("set-key-catalog");
  if (cat) cat.onclick = () => { p.catalog = true; p.catalogQuery = ""; settingsKeyRepaint("#set-cat-search"); };
  const catSheet = document.getElementById("set-cat-sheet");
  if (catSheet) {
    if (!catSheet.open) catSheet.showModal();
    catSheet.oncancel = (ev) => { ev.preventDefault(); p.catalog = false; settingsKeyRepaint("#set-key-catalog"); };
    const catSearch = document.getElementById("set-cat-search");
    bindSearch(catSearch, (text) => { p.catalogQuery = text; }, () => {
      const caret = catSearch.selectionStart;
      settingsKeyRepaint("#set-cat-search");
      const again = document.getElementById("set-cat-search");
      if (again) { again.focus(); try { again.setSelectionRange(caret, caret); } catch { /* not text */ } }
    });
    for (const btn of catSheet.querySelectorAll("[data-cat-pick]")) {
      btn.onclick = () => {
        p.catalog = false;
        p.keyEditor = { isNew: true, provider: btn.dataset.catPick, envVar: "", value: "", show: false };
        settingsKeyRepaint("#set-key-value");
      };
    }
    catSheet.querySelector('[data-cat-act="close"]').onclick = () => { p.catalog = false; settingsKeyRepaint("#set-key-catalog"); };
  }
  for (const row of document.querySelectorAll("[data-key-open]")) {
    const open = (ev) => {
      if (ev.target.closest("button, a")) return;
      p.keyEditor = { isNew: false, name: row.dataset.keyOpen, value: "", show: false };
      settingsKeyRepaint("#set-key-value");
    };
    row.onclick = open;
    row.onkeydown = (ev) => { if (ev.target === row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); open(ev); } };
  }
  for (const btn of document.querySelectorAll("[data-key-reveal]")) btn.onclick = () => settingsRevealKey(btn.dataset.keyReveal);
  for (const btn of document.querySelectorAll("[data-key-copy]")) {
    const name = btn.dataset.keyCopy;
    Fmt.copyTick(btn, async () => {
      const value = p.revealed.get(name) ?? await settingsKeyValue(name);
      if (value == null) settingsKeyRepaint(`[data-key-copy="${CSS.escape(name)}"]`);
      else p.keyError = null;
      return value;
    }, () => {
      p.keyError = "Couldn't copy here. Reveal the value and copy it by hand.";
      settingsKeyRepaint(`[data-key-copy="${CSS.escape(name)}"]`);
    });
  }
  const sheet = document.getElementById("set-key-sheet");
  if (!sheet) return;
  const e = p.keyEditor;
  if (!sheet.open) sheet.showModal();
  sheet.oncancel = (ev) => { ev.preventDefault(); settingsCloseKey(); };
  const provider = document.getElementById("set-key-provider");
  if (provider) provider.onchange = () => { e.provider = provider.value; e.envVar = ""; e.error = null; settingsKeyRepaint("#set-key-provider"); };
  const variable = document.getElementById("set-key-var");
  if (variable) variable.onchange = () => { e.envVar = variable.value; settingsKeyRepaint("#set-key-var"); };
  const value = document.getElementById("set-key-value");
  const save = sheet.querySelector('[data-key-act="save"]');
  value.oninput = () => {
    e.value = value.value;
    save.disabled = !!e.working || !settingsKeyName(e) || !e.value.trim();
  };
  value.onkeydown = (ev) => { if (ev.key === "Enter" && !save.disabled) { ev.preventDefault(); settingsSaveKey(); } };
  document.getElementById("set-key-show").onclick = () => { e.show = !e.show; settingsKeyRepaint("#set-key-value"); };
  for (const btn of sheet.querySelectorAll("[data-key-act]")) {
    btn.onclick = () => {
      const act = btn.dataset.keyAct;
      if (act === "cancel") settingsCloseKey();
      if (act === "save") settingsSaveKey();
      if (act === "delete") { e.confirmingDelete = true; settingsKeyRepaint('[data-key-act="keep"]'); }
      if (act === "keep") { e.confirmingDelete = false; settingsKeyRepaint('[data-key-act="delete"]'); }
      if (act === "delete-confirm") settingsRemoveKey();
    };
  }
}

function settingsKeyRepaint(focus) {
  if (onSettingsPage()) settingsRepaintRemote(focus);
}

function settingsCloseKey() {
  const p = settingsPage;
  const name = p.keyEditor && !p.keyEditor.isNew ? p.keyEditor.name : null;
  p.keyEditor = null;
  const sheet = document.getElementById("set-key-sheet");
  if (sheet && sheet.open) sheet.close();
  settingsKeyRepaint(name ? `[data-key-open="${CSS.escape(name)}"]` : "#set-key-add");
}

function settingsFailure(status, data) {
  return failText(status, data);
}

function settingsSignedOut(status) {
  if (status !== 401) return false;
  if (onSettingsPage()) renderGate();
  return true;
}

/* Reads the page again for a fresh file and stamp; revealed values go. */
async function settingsReloadKeys(focus) {
  const p = settingsPage;
  const gen = routeGen;
  const mine = ++p.gen;
  p.keysBusy = true;
  settingsForgetRevealed();
  settingsKeyRepaint(focus);
  const { status, data } = await api("/v1/app/settings");
  p.keysBusy = false;
  if (gen !== routeGen || mine !== p.gen) return;
  if (settingsSignedOut(status)) return;
  if (status === 200 && data) { p.data = data; p.keyError = null; } else p.keyError = settingsFailure(status, data);
  settingsKeyRepaint(focus);
}

/* One key's value, asked for only by the eye and copy buttons. */
async function settingsKeyValue(name) {
  const { status, data } = await apiPost("/v1/app/settings/key_value", { name });
  if (status === 200 && data && typeof data.value === "string") return data.value;
  if (!settingsSignedOut(status)) settingsPage.keyError = settingsFailure(status, data);
  return null;
}

async function settingsRevealKey(name) {
  const p = settingsPage;
  const focus = `[data-key-reveal="${CSS.escape(name)}"]`;
  if (p.revealed.has(name)) { p.revealed.delete(name); return settingsKeyRepaint(focus); }
  const gen = p.revealGen;
  const value = await settingsKeyValue(name);
  // A reload or a new visit since the click hid everything: stay hidden.
  if (gen !== p.revealGen || !onSettingsPage()) return;
  if (value != null) { p.revealed.set(name, value); p.keyError = null; }
  settingsKeyRepaint(focus);
}

/* key_set, then the page from the answer. The value field is emptied
 * whether or not the hub took it. */
async function settingsSaveKey() {
  const p = settingsPage;
  const e = p.keyEditor;
  const name = e && settingsKeyName(e);
  if (!e || e.working || !name || !e.value.trim()) return;
  const body = { name, value: e.value, stamp: p.data.providers.stamp };
  if (e.isNew) body.provider = e.provider;
  e.value = "";
  e.working = "save";
  e.error = null;
  settingsKeyRepaint();
  const { status, data } = await apiPost("/v1/app/settings/key_set", body);
  body.value = "";
  settingsKeyDone(e, name, status, data, "#set-key-value");
}

async function settingsRemoveKey() {
  const p = settingsPage;
  const e = p.keyEditor;
  if (!e || e.working) return;
  e.working = "delete";
  e.error = null;
  settingsKeyRepaint();
  const { status, data } = await apiPost("/v1/app/settings/key_remove", { name: e.name, stamp: p.data.providers.stamp });
  settingsKeyDone(e, e.name, status, data, '[data-key-act="cancel"]');
}

function settingsKeyDone(e, name, status, data, focus) {
  const p = settingsPage;
  e.working = false;
  if (settingsSignedOut(status)) return;
  if (status === 200 && data && data.providers) {
    p.data.providers = data.providers;
    p.revealed.delete(name);
    p.keyError = null;
    if (p.keyEditor === e) settingsCloseKey();
    else settingsKeyRepaint();
    return;
  }
  e.error = settingsFailure(status, data);
  e.confirmingDelete = false;
  if (p.keyEditor !== e) return;
  // The file moved under the page: read it again so a retry carries the
  // new stamp, keeping the sheet open.
  if (status === 409 && data && data.error && data.error.code === "changed_on_disk") {
    e.error = "The keys file changed since this page read it. It has been read again; make the change again.";
    return settingsReloadKeys(focus);
  }
  settingsKeyRepaint(focus);
}

function bindRemote() {
  const p = settingsPage;
  if (!p.github && !p.githubBusy) settingsLoadGithub(false);
  if ((!p.setup || p.setupStale) && !p.setupBusy) settingsLoadSetup();
  if (p.data) settingsPollRequests();
  const recheck = document.getElementById("set-setup-recheck");
  if (recheck) recheck.onclick = () => settingsLoadSetup();
  for (const btn of document.querySelectorAll("[data-setup-copy]")) {
    // Plain http has no clipboard API; the command is in the row to copy by hand.
    Fmt.copyTick(btn, () => {
      const step = p.setup && p.setup.steps && p.setup.steps.find((x) => x.id === btn.dataset.setupCopy);
      return step && step.action && step.action.command;
    }, (b) => { b.title = "Couldn't copy — select it by hand"; });
  }
  for (const btn of document.querySelectorAll("[data-setup-pair]")) btn.onclick = () => settingsMint();
  const gh = document.getElementById("set-gh-refresh");
  if (gh) gh.onclick = () => settingsLoadGithub(true);
  const sync = document.getElementById("set-gh-sync");
  if (sync) sync.onclick = () => settingsSyncGithub();
  const pair = document.getElementById("set-pair");
  if (pair) pair.onclick = () => settingsMint();
  const devFold = document.getElementById("set-devices-fold");
  if (devFold) devFold.onclick = () => { p.showsAllDevices = !p.showsAllDevices; settingsRepaintRemote("#set-devices-fold"); };
  for (const row of document.querySelectorAll("[data-device]")) {
    const id = row.dataset.device;
    for (const btn of row.querySelectorAll("[data-act]")) {
      btn.onclick = () => {
        if (btn.dataset.act === "revoke") { p.confirming = id; delete p.revokeErrors[id]; settingsRepaintRemote(`[data-device="${CSS.escape(id)}"] [data-act="cancel"]`); }
        if (btn.dataset.act === "cancel") { p.confirming = null; settingsRepaintRemote(`[data-device="${CSS.escape(id)}"] [data-act="revoke"]`); }
        if (btn.dataset.act === "revoke-confirm") settingsRevoke(id);
      };
    }
  }
  for (const row of document.querySelectorAll("[data-request]")) {
    const rid = row.dataset.request;
    const code = () => (row.querySelector("[data-code]").value || "").replace(/\D/g, "");
    for (const btn of row.querySelectorAll("[data-act]")) {
      btn.onclick = () => settingsDecide(rid, btn.dataset.act, code());
    }
  }
  const sheet = document.getElementById("set-pair-sheet");
  if (sheet) {
    if (!sheet.open) sheet.showModal();
    sheet.oncancel = (e) => { e.preventDefault(); settingsDismissTicket(); };
    for (const btn of sheet.querySelectorAll("[data-target]")) {
      btn.onclick = () => { p.target = btn.dataset.target; settingsRepaintRemote(`[data-target="${p.target}"]`); };
    }
    sheet.querySelector('[data-act="done"]').onclick = settingsDismissTicket;
    Fmt.copyTick(sheet.querySelector('[data-act="copy"]'),
      () => p.ticket && (p.target === "web" ? p.ticket.url : p.ticket.deep_link),
      () => {
        // Couldn't write the clipboard: put the link under the selection.
        const code = document.getElementById("set-payload");
        if (code) window.getSelection().selectAllChildren(code);
      });
    bindRemint(sheet);
    if (!p.timer) {
      // Looks the sheet up on every tick: a repaint (the Browser / Quiver
      // app switch, a new code) draws a new one. The countdown is only
      // rewritten when its text changes, and the button it may turn into
      // is bound each time it is drawn.
      p.timer = setInterval(() => {
        const el = document.getElementById("set-countdown");
        if (!el || !p.ticket) return settingsStopClock();
        const html = settingsCountdown();
        if (el.dataset.drawn === html) return;
        el.innerHTML = html;
        el.dataset.drawn = html;
        bindRemint(document.getElementById("set-pair-sheet"));
      }, 1000);
    }
  }
}

function bindRemint(sheet) {
  const btn = sheet && sheet.querySelector('[data-act="remint"]');
  if (btn) btn.onclick = () => settingsMint();
}

/* Repaint the section, keeping focus where it was when that element is
 * still there, else on ``fallback``. Code fields being typed in bank
 * their digits into ``codeDrafts`` first, so a repaint mid-typing —
 * this poll's, or a sibling card's — loses nothing. */
function settingsRepaintRemote(fallback) {
  if (!onSettingsPage()) return;
  for (const row of document.querySelectorAll("[data-request]")) {
    const input = row.querySelector("[data-code]");
    if (input) settingsPage.codeDrafts[row.dataset.request] = input.value;
  }
  const el = document.activeElement;
  const key = el && el.id ? `#${CSS.escape(el.id)}` : null;
  paintSettings();
  const again = (key && document.querySelector(key)) || (fallback && document.querySelector(fallback));
  if (again && !again.disabled) again.focus();
}

async function settingsLoadGithub(refresh) {
  const p = settingsPage;
  const gen = routeGen;
  p.githubBusy = true;
  if (onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote();
  const { status, data } = await api(`/v1/app/settings?part=github${refresh ? "&refresh=1" : ""}`);
  p.githubBusy = false;
  if (status === 200 && data) p.github = data.github;
  else p.github = { error: failText(status, data, null) };
  if (gen === routeGen && onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote();
}

/* Sync now: the hub runs swe gh sync and answers with the card read
 * again. Owners that failed keep their old rows, as the app says. */
async function settingsSyncGithub() {
  const p = settingsPage;
  if (p.syncing) return;
  const gen = routeGen;
  p.syncing = true;
  p.syncError = null;
  settingsRepaintRemote("#set-gh-sync");
  const { status, data } = await apiPost("/v1/app/settings/sync", {});
  p.syncing = false;
  if (status === 401) { if (gen === routeGen && onSettingsPage()) renderGate(); return; }
  if (status === 200 && data) {
    p.github = data.github;
    const skipped = (data.failed || []).map((f) => (f.owner === "<orgs>" ? "your organizations" : f.owner));
    p.syncError = skipped.length ? `Skipped ${skipped.join(", ")} — their old repos stayed.` : null;
  } else {
    p.syncError = failText(status, data);
    // A 409 for gh means the card's own line is out of date: check again.
    if (status === 409 && data && data.error && String(data.error.code || "").startsWith("gh_")) settingsLoadGithub(true);
  }
  // Repainted even when the sync began on an earlier visit: the page shown
  // now still draws the shared state, "Syncing…" included.
  if (onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote("#set-gh-sync");
}

async function settingsMint() {
  const p = settingsPage;
  if (p.minting) return;
  p.minting = true;
  p.pairError = null;
  settingsRepaintRemote();
  const { status, data } = await apiPost("/v1/app/settings/pair", {});
  p.minting = false;
  if (status === 401) { if (onSettingsPage()) renderGate(); return; }
  if (status !== 200 || !data || !data.ticket) {
    p.pairError = failText(status, data);
    p.ticket = null;
    settingsStopClock();
  } else {
    // Count down on this browser's clock from the token's lifetime, so a
    // hub clock that differs from this one can't end it early.
    p.ticket = { ...data.ticket, expires_at_local: Date.now() / 1000 + data.ticket.expires_in_s };
  }
  settingsRepaintRemote(p.ticket ? '[data-act="copy"]' : "#set-pair");
}

function settingsDismissTicket() {
  const p = settingsPage;
  p.ticket = null;
  settingsStopClock();
  const sheet = document.getElementById("set-pair-sheet");
  if (sheet && sheet.open) sheet.close();
  // A phone may have paired while the code was up: list it.
  settingsRefreshRemote("#set-pair");
  if (p.setup && !p.setup.ready && !p.setupBusy) settingsLoadSetup();
}

/* Approve or deny a pending pair request, on the protocol's own routes
 * (the same ones a paired phone or the owner's tailnet identity may
 * call): the web app's bearer or X-Quiver-Pair header satisfies both. */
async function settingsDecide(rid, act, code) {
  const p = settingsPage;
  if (p.deciding) return;
  p.deciding = rid;
  delete p.decideErrors[rid];
  settingsRepaintRemote();
  const { status, data } = await apiPost(`/v1/pair/requests/${act}`, { code, request_id: rid });
  p.deciding = null;
  if (status === 401) { if (onSettingsPage()) renderGate(); return; }
  if (status === 200) {
    // The request is decided: its draft dies with it, and the device
    // and request lists are read again.
    delete p.codeDrafts[rid];
    settingsRefreshRemote();
    return;
  }
  p.decideErrors[rid] = failText(status, data);
  settingsRepaintRemote(`[data-request="${CSS.escape(rid)}"] [data-act="${act}"]`);
}

async function settingsRefreshRemote(focus) {
  const gen = routeGen;
  const mine = ++settingsPage.gen;
  settingsRepaintRemote(focus);
  const { status, data } = await api("/v1/app/settings");
  if (gen !== routeGen || mine !== settingsPage.gen) return;
  if (status === 401) return renderGate();
  if (status === 200 && data) { settingsPage.data = data; settingsRepaintRemote(focus); }
  settingsPollRequests();
}

/* What about the remote card could change between reads: the request
 * and device ids. Names and expiries don't tick the signature, so a
 * quiet poll costs no repaint — and a typed code field is never
 * redrawn under the user's fingers. */
function remoteSig(remote) {
  const r = remote || {};
  return JSON.stringify([
    (r.requests || []).map((q) => q.request_id),
    (r.devices || []).map((d) => d.id),
    // A node seen or revoked from another browser or the CLI.
    (r.signins || []).map((x) => `${x.id}:${x.revoked ? 1 : 0}`),
  ]);
}

/* The remote PWA's approvals page polled every few seconds; the inline
 * rows do the same while Integrations is open, so a phone's new request
 * (or an expired one leaving) shows without a reload. */
function settingsPollRequests() {
  const p = settingsPage;
  if (p.reqPoll) return;
  const poll = p.reqPoll = setInterval(async () => {
    if (!onSettingsPage() || settingsSection() !== "integrations") {
      settingsStopPoll();
      return;
    }
    const gen = routeGen;
    const { status, data } = await api("/v1/app/settings?part=remote");
    // Left the page, or the poll was stopped (a 401, this browser
    // revoked) while the read was out.
    if (gen !== routeGen || p.reqPoll !== poll) return;
    if (status === 401) {
      // The gate's inputs would be wiped by the next tick's gate.
      settingsStopPoll();
      return renderGate();
    }
    if (status === 200 && data && p.data
        && remoteSig(data.remote) !== remoteSig(p.data.remote)) {
      p.data.remote = data.remote;
      settingsRepaintRemote();
    }
  }, 3000);
}

function settingsStopPoll() {
  clearInterval(settingsPage.reqPoll);
  settingsPage.reqPoll = null;
}

async function settingsRevoke(id) {
  const p = settingsPage;
  const r = p.data && p.data.remote;
  const dev = r && (r.devices.find((d) => d.id === id) || (r.signins || []).find((d) => d.id === id));
  if (!dev || p.revoking.has(id)) return;
  p.revoking.add(id);
  delete p.revokeErrors[id];
  settingsRepaintRemote();
  const { status, data } = await apiPost("/v1/app/settings/revoke",
    { device_id: id, confirm_this_device: dev.this_device === true });
  p.revoking.delete(id);
  if (status === 200 && data && data.this_device) {
    // This browser's credential is gone, so it leaves localStorage too.
    // The owner's own tailnet device still gets in without it; anything
    // else now answers 401.
    pairForget();
    // The remote poll would answer 401 now and replace the signed-out
    // gate with the generic one.
    settingsStopPoll();
    p.data = null;
    p.confirming = null;
    p.signedOut = true;
    if (onSettingsPage()) renderSettings();
    return;
  }
  if (status === 401) { if (onSettingsPage()) renderGate(); return; }
  if (status === 200 && data) {
    p.confirming = null;
    p.data.remote.devices = data.devices;
    if (data.signins) p.data.remote.signins = data.signins;
    settingsRepaintRemote("#set-pair");
    // Revoking the last phone undoes "Pair a phone".
    if (!p.setupBusy) settingsLoadSetup();
    return;
  }
  p.confirming = null;
  p.revokeErrors[id] = failText(status, data);
  settingsRepaintRemote(`[data-device="${CSS.escape(id)}"] [data-act="revoke"]`);
}

registerPage("settings", renderSettings);
