/* Settings: a port of the Mac app's SettingsPage. The same grouped
 * sub-nav (underline tabs when the page is narrow), section titles and
 * cards of rows. What belongs to the hub machine works: Integrations ›
 * Remote (hub status, paired devices with Revoke, Pair a device with a QR
 * code), the GitHub CLI, the API keys (read only, masked), the hub's
 * paths. Appearance and Sidebar are kept per browser, in localStorage.
 * Rows that configure the Mac app itself are greyed out and say
 * "Mac app only". Writes: POST /v1/app/settings/pair and /revoke.
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
    ["services", "Services", "Keys for the services Quiver itself calls, kept in the Keychain."],
  ]],
  ["Advanced", [
    ["integrations", "Integrations", "The GitHub CLI behind the GitHub pages, and the phones paired to watch your sessions."],
    ["paths", "Paths", "Where Quiver finds swe, your API keys file and your MCP server folders."],
    ["doctor", "Doctor", "Checks on PATH, Node and drift between quiver and your harnesses."],
    ["experimental", "Experimental", "Features being tried out. They may change or go away."],
  ]],
];
const SETTINGS_ORDER = SETTINGS_GROUPS.flatMap(([, sections]) => sections);

const SETTINGS_ICONS = {
  general: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  notifications: '<path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>',
  providers: '<path d="m12 3 9 4.5-9 4.5-9-4.5zM3 12l9 4.5 9-4.5M3 16.5 12 21l9-4.5"/>',
  services: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M15 8l2 2"/>',
  integrations: '<path d="M10 4a2 2 0 1 1 4 0v2h4v4h-2a2 2 0 1 0 0 4h2v4h-4v-2a2 2 0 1 0-4 0v2H6v-4H4a2 2 0 1 1 0-4h2V6h4z"/>',
  paths: '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
  doctor: '<path d="M6 3v6a4 4 0 0 0 8 0V3M10 13v2a5 5 0 0 0 10 0v-2"/><circle cx="20" cy="11" r="2"/>',
  experimental: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M7 15h10"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h4v-3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  warn: '<path d="M12 4 2.5 20h19zM12 10v4M12 17.5v.01"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
};
const settingsIcon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${SETTINGS_ICONS[name] || ICON[name] || ""}</svg>`;

const settingsPage = {
  data: null, gen: 0, github: null, githubBusy: false,
  // `swe remote setup`'s steps for this machine, and a load in flight.
  // Stale once Settings is opened again: a step can come undone (the
  // last phone revoked, the hub stopped), so each visit checks again.
  setup: null, setupBusy: false, setupCopied: null, setupStale: false,
  query: "", status: "all",
  // Remote writes: the minted ticket and which code it shows, the device
  // whose revoke is being confirmed, rows waiting on the hub, errors.
  ticket: null, target: "web", minting: false, pairError: null,
  confirming: null, revoking: new Set(), revokeErrors: {}, timer: null,
};

function settingsSection() {
  const fromHash = new URLSearchParams(location.hash.split("?")[1] || "").get("section");
  const saved = fromHash || settingsRead(SETTINGS_KEYS.section);
  // GitHub and Remote were their own sections before Integrations.
  if (saved === "github" || saved === "remote") return "integrations";
  return SETTINGS_ORDER.some(([id]) => id === saved) ? saved : "general";
}

function onSettingsPage() {
  return (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "settings";
}

async function renderSettings() {
  const gen = routeGen;
  const p = settingsPage;
  const mine = ++p.gen;
  settingsStopClock();
  p.ticket = null;
  p.confirming = null;
  p.setupStale = true;
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
    return renderGate(signedOut ? "This browser was signed out of the hub. Pair it again to come back."
      : "The hub doesn't know this browser's credential. It may have been revoked.");
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
    case "services": return servicesSection();
    case "integrations": return integrationsSection();
    case "paths": return pathsSection();
    case "doctor": return doctorSection();
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
  const rows = prov.rows || [];
  const withKey = rows.filter((r) => r.has_key).length;
  const p = settingsPage;
  const q = p.query.trim().toLowerCase();
  const visible = rows.filter((r) => (p.status === "all" || (p.status === "set") === r.has_key)
    && (!q || [r.name, r.id, ...(r.env_vars || [])].some((s) => String(s || "").toLowerCase().includes(q))));
  const subtitle = prov.exists ? `From ${prov.path}.` : `No key store at ${prov.path} yet.`;
  const filters = `<div class="filters set-filters">
    <select class="field" id="set-key-status" aria-label="Status">
      <option value="all"${p.status === "all" ? " selected" : ""}>Any status (${rows.length})</option>
      <option value="set"${p.status === "set" ? " selected" : ""}>Key set (${withKey})</option>
      <option value="none"${p.status === "none" ? " selected" : ""}>No key (${rows.length - withKey})</option>
    </select>
    <input class="field search" id="set-key-search" type="search" placeholder="Search providers or variables" value="${esc(p.query)}" aria-label="Search providers or variables">
    <span class="caption count num">${visible.length === 1 ? "1 row" : `${visible.length} rows`}</span>
  </div>`;
  const body = prov.error ? `<p class="error set-pad">${esc(prov.error)}</p>`
    : visible.length ? visible.map((r) => `<div class="set-key-row">
        <div class="set-key-name"><span class="name">${esc(r.name)}</span>
          ${r.url ? `<a class="caption" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Get a key</a>` : ""}</div>
        <div class="set-key-value"><code class="muted">${esc(r.env_var || "")}</code>${r.has_key ? `<span class="set-masked num">${esc(r.masked)}</span>` : ""}</div>
        <div class="set-key-type caption">API key</div>
        <div class="set-key-status ${r.has_key ? "ok" : "muted"}">${r.has_key ? "Key set" : "No key"}</div>
      </div>`).join("")
    : `<p class="empty set-pad">${rows.length ? "Nothing matches these filters." : "No providers known."}</p>`;
  return `<div class="set-stack">
    <div class="set-keys-head"><div class="set-group-title">Keys and plans</div><div class="caption">${esc(subtitle)}</div></div>
    ${filters}
    <div class="set-table">
      <div class="set-key-row set-key-header"><div>Provider</div><div>Value</div><div class="set-key-type">Type</div><div>Status</div></div>
      ${body}
    </div>
    <div class="caption muted">${withKey} / ${rows.length} providers with keys. Values stay masked here; adding, editing and checking keys stays in the Mac app and <code>swe providers</code>. Coding plans are listed in the Mac app.</div>
  </div>`;
}

/* ------------------------------------------------------------- Services */

function servicesSection() {
  return setCard([
    macRow("Service keys", "Keys for the services Quiver itself calls, kept in the Mac's Keychain, with Test and Replace."),
  ]);
}

/* --------------------------------------------------------- Integrations */

function integrationsSection() {
  return `<div class="set-stack wide">
    ${setGroup("GitHub", "The gh install behind the GitHub pages, and the synced repo catalog the Add-repo search reads.", githubCard())}
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
  return setCard([
    setRow("GitHub CLI", detail, refresh, { warning }),
    setRow("Repository catalog", catalog, `<code class="muted">swe gh sync</code>`),
  ]);
}

function settingsUptime(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

/* Foundation's .relative(presentation: .named): "now", "5 minutes ago". */
function settingsRelative(epoch) {
  const diff = Math.min(0, epoch - Date.now() / 1000);
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
  for (const [unit, size] of [["year", 31536000], ["month", 2592000], ["week", 604800],
    ["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (abs >= size) return rtf.format(Math.trunc(diff / size), unit);
  }
  return abs < 1 ? "now" : rtf.format(Math.trunc(diff), "second");
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
  // has no row here and nothing of its own to revoke.
  const viaTailnet = r.caller && r.caller.kind === "tailnet"
    ? `<p class="caption set-caller">This browser is signed in through Tailscale as the hub's owner, so it isn't a paired device: it isn't listed here and has nothing to revoke.</p>`
    : "";
  const devices = live.length
    ? `<div class="set-card"><div class="set-card-title">Paired devices</div>${viaTailnet}${live.map(deviceRow).join('<div class="set-divider"></div>')}<div class="set-divider"></div>${pair}</div>`
    : `<div class="set-card"><div class="set-card-title">Paired devices</div>${viaTailnet}<p class="caption set-empty">No devices paired yet.</p>${pair}</div>`;
  const requests = (r.requests || []).length ? setCard([...r.requests.map((q) => setRow(q.name || "device",
      [q.node_name, q.login, q.peer_ip, `expires in ${Math.max(0, Math.round(q.expires_in_s / 60))} min`].filter(Boolean).join(" · "))),
    setRow("Approve or deny", "Phones that asked to pair through Tailscale wait here. Decide on the hub's remote page, where you type the code the phone shows.",
      '<a class="set-button" href="/#approve">Open approvals</a>')], "Waiting for approval") : "";
  return `<div class="set-stack">${setupCard()}${hub}${devices}${requests}</div>${pairSheet()}`;
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
  if (live && a.kind === "open" && a.url) {
    control += ` <a class="set-button" href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.label || "Open")}</a>`;
  } else if (live && a.kind === "command" && a.command) {
    const copied = settingsPage.setupCopied === step.id;
    control += ` <button class="set-button" data-setup-copy="${esc(step.id)}">${copied ? "Copied" : esc(a.label || "Copy command")}</button>`;
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
    : { error: (data && data.error && data.error.message) || (status ? `The hub answered ${status}.` : "Couldn't reach the hub.") };
  if (gen === routeGen && onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote();
}

async function settingsCopySetup(id) {
  const p = settingsPage;
  const step = p.setup && p.setup.steps && p.setup.steps.find((x) => x.id === id);
  const cmd = step && step.action && step.action.command;
  if (!cmd) return;
  try {
    await navigator.clipboard.writeText(cmd);
    p.setupCopied = id;
  } catch {
    // Plain http has no clipboard API; the command is in the row to copy by hand.
    p.setupCopied = null;
  }
  settingsRepaintRemote(`[data-setup-copy="${CSS.escape(id)}"]`);
}

function deviceRow(dev) {
  const p = settingsPage;
  const name = dev.name || "Device";
  const bits = [dev.id];
  if (dev.last_seen_at) bits.push(`seen ${settingsRelative(dev.last_seen_at)}`);
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

function pairSheet() {
  const t = settingsPage.ticket;
  if (!t) return "";
  const target = settingsPage.target;
  const payload = target === "web" ? t.url : t.deep_link;
  const instructions = target === "web"
    ? "On your phone, scan the code to open the remote in a browser, then Add to Home Screen to keep it."
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
            <button class="set-button" data-act="copy">${settingsIcon("copy")}<span id="set-copy-label">Copy link</span></button>
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

/* ----------------------------------------------------------------- Paths */

function pathsSection() {
  const paths = settingsPage.data && settingsPage.data.paths;
  if (!paths) return `<div class="card"><p class="empty">Reading…</p></div>`;
  return `<div class="set-stack">${setCard([
    setRow("swe executable", paths.swe ? `In use: ${paths.swe}.` : "swe isn't on the hub's PATH."),
    setRow("API keys file", `In use: ${paths.api_keys}.`),
    setRow("MCP servers folder", `In use: ${paths.mcp_servers}.`),
    macRow("Editor", "Opens a server folder from the MCP servers page."),
  ])}<p class="caption set-note">These are the hub's paths. Pointing Quiver somewhere else is a Mac app setting.</p></div>`;
}

/* ---------------------------------------------------------------- Doctor */

function doctorSection() {
  return setCard([
    setRow("Doctor", "Checks on PATH, Node and drift between quiver and your harnesses, on the hub.",
      `<a class="set-button" href="#/doctor">Open Doctor</a>`),
  ]);
}

/* ---------------------------------------------------------- Experimental */

function experimentalSection() {
  return setCard([
    macRow("Session search", "Index the text of every session transcript, so search can match what was said, not only titles and paths.",
      '<span class="badge">Experimental</span>'),
  ]);
}

/* --------------------------------------------------------------- binding */

function bindSettings(section) {
  const panel = document.getElementById("panel");
  for (const btn of panel.querySelectorAll("[data-section]")) {
    btn.onclick = () => {
      settingsWrite(SETTINGS_KEYS.section, btn.dataset.section);
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
  for (const btn of panel.querySelectorAll("[data-sidebar-all]")) {
    btn.onclick = () => {
      const all = (typeof SIDEBAR !== "undefined" ? SIDEBAR : []).flatMap(([, items]) => items.map(([id]) => id));
      settingsSetHidden(new Set(btn.dataset.sidebarAll === "on" ? [] : all.filter((id) => !SETTINGS_CORE.has(id))));
      paintSettings();
    };
  }
  for (const btn of panel.querySelectorAll("[data-providers-tab]")) {
    btn.onclick = () => { settingsWrite(SETTINGS_KEYS.providersTab, btn.dataset.providersTab); paintSettings(); };
  }
  const status = document.getElementById("set-key-status");
  if (status) status.onchange = () => { settingsPage.status = status.value; paintSettings(); };
  const search = document.getElementById("set-key-search");
  if (search) {
    search.oninput = () => {
      settingsPage.query = search.value;
      const caret = search.selectionStart;
      paintSettings();
      const again = document.getElementById("set-key-search");
      if (again) { again.focus(); try { again.setSelectionRange(caret, caret); } catch { /* not text */ } }
    };
  }
  if (section === "integrations") bindRemote();
}

function bindRemote() {
  const p = settingsPage;
  if (!p.github && !p.githubBusy) settingsLoadGithub(false);
  if ((!p.setup || p.setupStale) && !p.setupBusy) settingsLoadSetup();
  const recheck = document.getElementById("set-setup-recheck");
  if (recheck) recheck.onclick = () => settingsLoadSetup();
  for (const btn of document.querySelectorAll("[data-setup-copy]")) btn.onclick = () => settingsCopySetup(btn.dataset.setupCopy);
  for (const btn of document.querySelectorAll("[data-setup-pair]")) btn.onclick = () => settingsMint();
  const gh = document.getElementById("set-gh-refresh");
  if (gh) gh.onclick = () => settingsLoadGithub(true);
  const pair = document.getElementById("set-pair");
  if (pair) pair.onclick = () => settingsMint();
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
  const sheet = document.getElementById("set-pair-sheet");
  if (sheet) {
    if (!sheet.open) sheet.showModal();
    sheet.oncancel = (e) => { e.preventDefault(); settingsDismissTicket(); };
    for (const btn of sheet.querySelectorAll("[data-target]")) {
      btn.onclick = () => { p.target = btn.dataset.target; settingsRepaintRemote(`[data-target="${p.target}"]`); };
    }
    sheet.querySelector('[data-act="done"]').onclick = settingsDismissTicket;
    sheet.querySelector('[data-act="copy"]').onclick = settingsCopyLink;
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
 * still there, else on ``fallback``. */
function settingsRepaintRemote(fallback) {
  if (!onSettingsPage()) return;
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
  else p.github = { error: (data && data.error && data.error.message) || (status ? `The hub answered ${status}.` : "Couldn't reach the hub.") };
  if (gen === routeGen && onSettingsPage() && settingsSection() === "integrations") settingsRepaintRemote();
}

async function settingsMint() {
  const p = settingsPage;
  if (p.minting) return;
  p.minting = true;
  p.pairError = null;
  settingsRepaintRemote();
  const { status, data } = await apiPost("/v1/app/settings/pair", {});
  p.minting = false;
  if (status === 401) { if (onSettingsPage()) renderGate("The hub doesn't know this browser's credential. It may have been revoked."); return; }
  if (status !== 200 || !data || !data.ticket) {
    p.pairError = (data && data.error && data.error.message) || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");
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

async function settingsRefreshRemote(focus) {
  const gen = routeGen;
  const mine = ++settingsPage.gen;
  settingsRepaintRemote(focus);
  const { status, data } = await api("/v1/app/settings");
  if (gen !== routeGen || mine !== settingsPage.gen) return;
  if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (status === 200 && data) { settingsPage.data = data; settingsRepaintRemote(focus); }
}

async function settingsCopyLink() {
  const p = settingsPage;
  if (!p.ticket) return;
  const text = p.target === "web" ? p.ticket.url : p.ticket.deep_link;
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch { /* plain http: no async clipboard */ }
  if (!ok) {
    // Over plain http the async clipboard is off; a selected textarea
    // and the old copy command still work there.
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.getElementById("set-pair-sheet").appendChild(area);
    area.select();
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
  }
  const label = document.getElementById("set-copy-label");
  if (label) label.textContent = ok ? "Copied" : "Select the link to copy it";
  if (ok) setTimeout(() => { const l = document.getElementById("set-copy-label"); if (l) l.textContent = "Copy link"; }, 1500);
  if (!ok) {
    const code = document.getElementById("set-payload");
    if (code) window.getSelection().selectAllChildren(code);
  }
}

async function settingsRevoke(id) {
  const p = settingsPage;
  const dev = p.data && p.data.remote.devices.find((d) => d.id === id);
  if (!dev || p.revoking.has(id)) return;
  p.revoking.add(id);
  delete p.revokeErrors[id];
  settingsRepaintRemote();
  const { status, data } = await apiPost("/v1/app/settings/revoke",
    { device_id: id, confirm_this_device: dev.this_device === true });
  p.revoking.delete(id);
  if (status === 200 && data && data.this_device) {
    // This browser's credential is gone. The owner's own tailnet device
    // still gets in without it; anything else now answers 401.
    p.data = null;
    p.confirming = null;
    p.signedOut = true;
    if (onSettingsPage()) renderSettings();
    return;
  }
  if (status === 401) { if (onSettingsPage()) renderGate("The hub doesn't know this browser's credential. It may have been revoked."); return; }
  if (status === 200 && data) {
    p.confirming = null;
    p.data.remote.devices = data.devices;
    settingsRepaintRemote("#set-pair");
    // Revoking the last phone undoes "Pair a phone".
    if (!p.setupBusy) settingsLoadSetup();
    return;
  }
  p.confirming = null;
  p.revokeErrors[id] = (data && data.error && data.error.message)
    || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");
  settingsRepaintRemote(`[data-device="${CSS.escape(id)}"] [data-act="revoke"]`);
}

registerPage("settings", renderSettings);
