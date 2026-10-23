/* Harnesses and Accounts: ports of the Mac app's HarnessesPage and the
 * starred-harness cards on AccountsPage. Harnesses can star, unstar,
 * archive (with a reason) and restore, through POST /v1/app/harness/<action>;
 * editing, Detect and Add stay in swe and the Mac app. Loaded before app.js.
 */
"use strict";

/* ---------------------------------------------------------------- Harnesses */

const harnessesPage = {
  data: null, gen: 0, scope: "in-use", query: "", expanded: false,
  // Writes: rows waiting on the hub, the row whose archive form is open
  // (and what is typed in it), and the last action's error.
  busy: new Set(), archiving: null, reason: "", actionError: null,
};
const REASON_MAX = 500;

async function renderHarnesses() {
  const panel = document.getElementById("panel");
  const gen = ++harnessesPage.gen;
  // Paint before awaiting, cached or loading, so the previous page's
  // controls never stay on screen while the request runs.
  if (!harnessesPage.data) {
    panel.innerHTML = `<div class="page">${harnessesHeader()}<div class="card"><p class="empty">Reading the registry…</p></div></div>`;
    bindRefresh(renderHarnesses, () => { harnessesPage.data = null; });
  } else {
    paintHarnesses();
  }
  const { status, data } = await api("/v1/app/harnesses");
  if (gen !== harnessesPage.gen) return;
  if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (status !== 200 || !data) return pageError(harnessesHeader(), status, data, renderHarnesses);
  harnessesPage.data = data;
  paintHarnesses();
}

function harnessesHeader() {
  const where = harnessesPage.data && harnessesPage.data.machine ? ` on ${harnessesPage.data.machine}` : "";
  return pageHeader("Harnesses", `The AI coding CLIs swe knows about${where}. Star the ones you use; archive the ones you've ruled out.`,
    `<button class="icon-button" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

/* Starred first, then the busiest, then by name. */
function harnessOrder(a, b) {
  const star = (h) => (h.state === "starred" ? 0 : 1);
  return star(a) - star(b) || (b.sessions_100d || 0) - (a.sessions_100d || 0) || a.name.localeCompare(b.name);
}

function harnessTitle(h) {
  return (h.display_name || "").trim() || Fmt.harnessName(h.name);
}

function paintHarnesses() {
  const p = harnessesPage, all = p.data.harnesses;
  const archived = all.filter((h) => h.state === "archived");
  const inUse = all.filter((h) => h.state !== "archived");
  let list = (p.scope === "archived" ? archived : inUse).slice().sort(harnessOrder);
  const q = p.query.trim().toLowerCase();
  if (q) {
    list = list.filter((h) => [h.name, harnessTitle(h), h.description, ...(h.tags || [])]
      .join(" ").toLowerCase().includes(q));
  }
  // In use, unsearched: starred rows show and the rest fold away.
  let hidden = 0;
  if (p.scope === "in-use" && !q && !p.expanded) {
    const starred = list.filter((h) => h.state === "starred");
    if (starred.length) { hidden = list.length - starred.length; list = starred; }
  }
  const count = list.length + hidden;
  let body;
  if (!all.length) body = `<p class="empty">No harnesses registered yet. Run <code>swe harness discover --apply</code> on the hub.</p>`;
  else if (!list.length) {
    body = `<p class="empty">${q ? "No harnesses match." : p.scope === "archived" ? "Nothing archived." : "Every harness is archived."}</p>`;
  } else {
    body = list.map(harnessRow).join("");
    if (hidden || (p.expanded && p.scope === "in-use" && !q)) {
      body += `<button class="fold" id="harness-fold">${p.expanded
        ? "Hide the harnesses you haven't starred" : `Show ${hidden} more you haven't starred`}</button>`;
    }
  }
  document.getElementById("panel").innerHTML = `<div class="page">${harnessesHeader()}
    <div class="stack">
      <div class="filters">
        <select class="field" id="harness-scope" aria-label="Scope">${options(
          [["in-use", `In use (${inUse.length})`], ["archived", `Archived (${archived.length})`]], p.scope)}</select>
        <input class="field search" id="harness-search" type="search" placeholder="Filter harnesses"
          value="${esc(p.query)}" autocomplete="off" spellcheck="false">
        <span class="caption count">${count} harness${count === 1 ? "" : "es"}</span>
      </div>
      ${p.actionError ? `<div class="card harness-error"><p class="error" role="alert">${esc(p.actionError)}</p></div>` : ""}
      <div class="card list">${body}</div>
    </div></div>`;
  bindRefresh(renderHarnesses, () => { harnessesPage.data = null; });
  const scope = document.getElementById("harness-scope");
  scope.onchange = () => { p.scope = scope.value; paintHarnesses(); };
  const search = document.getElementById("harness-search");
  search.oninput = () => {
    p.query = search.value;
    const at = search.selectionStart;
    paintHarnesses();
    const again = document.getElementById("harness-search");
    again.focus();
    again.setSelectionRange(at, at);
  };
  const fold = document.getElementById("harness-fold");
  if (fold) fold.onclick = () => { p.expanded = !p.expanded; paintHarnesses(); };
  bindHarnessActions();
}

function archivedLine(a) {
  // An ISO string (naive local time) in the archive record; epoch seconds tolerated.
  const when = a.archived_at == null ? null
    : new Date(typeof a.archived_at === "number" ? a.archived_at * 1000 : a.archived_at);
  const date = when && !isNaN(when) ? ` ${when.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}` : "";
  const usage = a.usage && a.usage !== "unknown" ? `, usage: ${a.usage}` : "";
  const reason = (a.reason || "").trim() || "no reason given";
  return `Archived${date}${usage}. ${reason[0].toUpperCase()}${reason.slice(1)}`;
}

function harnessRow(h) {
  const aliases = (h.aliases || []).map((a) => `<span class="badge">${esc(a)}</span>`).join("");
  const busy = harnessesPage.busy.has(h.name);
  const installed = h.installed == null ? ""
    : `<span class="badge">${h.installed ? "Installed" : "Not on PATH"}</span>`;
  const sessions = h.sessions_100d == null ? ""
    : `<span class="caption">${h.sessions_100d} session${h.sessions_100d === 1 ? "" : "s"} in 100 days</span>`;
  return `<div class="list-row harness-row${busy ? " busy" : ""}" data-harness="${esc(h.name)}"${busy ? ' aria-busy="true"' : ""}>
    <span class="session-logo">${Fmt.logo(h.name)}</span>
    <div class="list-main">
      <div class="list-title"><span class="name">${esc(harnessTitle(h))}</span>
        ${h.version ? `<code class="muted">${esc(h.version)}</code>` : ""}
        <code>${esc(h.command)}</code>${aliases}</div>
      ${h.description ? `<div class="caption">${esc(h.description)}</div>` : ""}
      ${h.archived ? `<div class="caption warn">${esc(archivedLine(h.archived))}</div>` : ""}
      ${(h.tags || []).length ? `<div class="caption muted">${h.tags.map(esc).join(" · ")}</div>` : ""}
    </div>
    <div class="list-side">${installed}${sessions}</div>
    ${harnessActions(h, busy)}
    ${harnessesPage.archiving === h.name && h.state !== "archived" ? archiveForm(h, busy) : ""}
  </div>`;
}

/* ------------------------------------------------------- Harnesses: writes */

const HARNESS_ICON = {
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
};

function harnessIcon(name, filled = false) {
  return `<svg viewBox="0 0 24 24" width="15" height="15" fill="${filled ? "currentColor" : "none"}" stroke="currentColor"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${HARNESS_ICON[name]}</svg>`;
}

/* Archived rows get Restore; the rest get Archive over Star, as in the app. */
function harnessActions(h, busy) {
  const off = busy ? " disabled" : "";
  const name = esc(harnessTitle(h));
  if (h.state === "archived") {
    return `<div class="harness-actions">
      <button class="harness-button" data-act="restore"${off}>Restore</button></div>`;
  }
  const starred = h.state === "starred";
  const open = harnessesPage.archiving === h.name;
  return `<div class="harness-actions">
    <button class="icon-button harness-icon${open ? " pressed" : ""}" data-act="archive-open" aria-expanded="${open}"
      aria-label="Archive ${name}" title="Archive: hide it from swe list and record why"${off}>${harnessIcon("archive")}</button>
    <button class="icon-button harness-icon${starred ? " starred" : ""}" data-act="${starred ? "unstar" : "star"}"
      aria-pressed="${starred}" aria-label="${starred ? "Unstar" : "Star"} ${name}"
      title="${starred ? "Unstar: stop pinning it to the top" : "Star: pin it to the top and track its limits"}"${off}>${harnessIcon("star", starred)}</button>
  </div>`;
}

/* The app's archive popover, inline under the row: a reason is required. */
function archiveForm(h, busy) {
  const p = harnessesPage;
  const empty = !p.reason.trim();
  return `<form class="harness-archive" novalidate>
    <div class="card-title">Archive ${esc(harnessTitle(h))}</div>
    <p class="caption">It drops out of swe list. Nothing is uninstalled, and the reason is kept so you remember why.</p>
    <input class="field" id="harness-reason" type="text" maxlength="${REASON_MAX}" autocomplete="off"
      placeholder="Why? Like: too slow, or no MCP support" aria-label="Why are you archiving it?"
      value="${esc(p.reason)}"${busy ? " disabled" : ""}>
    <div class="harness-form-buttons">
      <button type="button" class="harness-button" data-act="archive-cancel">Cancel</button>
      <button type="submit" class="harness-button primary" id="harness-archive-go"${empty || busy ? " disabled" : ""}>${busy ? "Archiving…" : "Archive"}</button>
    </div>
  </form>`;
}

function bindHarnessActions() {
  const p = harnessesPage;
  for (const btn of document.querySelectorAll(".harness-row [data-act]")) {
    const name = btn.closest(".harness-row").dataset.harness;
    btn.onclick = (event) => {
      event.stopPropagation();
      const act = btn.dataset.act;
      if (act === "archive-open") {
        p.archiving = p.archiving === name ? null : name;
        p.reason = "";
        paintHarnesses();
        const field = document.getElementById("harness-reason");
        if (field) field.focus();
      } else if (act === "archive-cancel") {
        closeArchiveForm();
      } else {
        harnessWrite(act, name);
      }
    };
  }
  const form = document.querySelector(".harness-archive");
  if (!form) return;
  const name = form.closest(".harness-row").dataset.harness;
  const field = document.getElementById("harness-reason");
  const go = document.getElementById("harness-archive-go");
  field.oninput = () => {
    p.reason = field.value;
    go.disabled = !field.value.trim() || p.busy.has(name);
  };
  field.onkeydown = (event) => {
    if (event.key === "Escape") { event.preventDefault(); closeArchiveForm(); }
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    const why = p.reason.trim();
    if (why && !p.busy.has(name)) harnessWrite("archive", name, { reason: why });
  };
}

function closeArchiveForm() {
  const p = harnessesPage, name = p.archiving;
  p.archiving = null;
  p.reason = "";
  paintHarnesses();
  const back = name && document.querySelector(`.harness-row[data-harness="${CSS.escape(name)}"] [data-act="archive-open"]`);
  if (back) back.focus();
}

/* POST one write. The row changes only once the hub answers, from the row
 * it sends back; until then it is dimmed and its buttons are off. */
async function harnessWrite(action, name, extra = {}) {
  const p = harnessesPage;
  if (p.busy.has(name)) return;
  p.busy.add(name);
  p.actionError = null;
  if (onHarnessesPage()) paintHarnesses();
  const { status, data } = await apiPost(`/v1/app/harness/${action}`, { name, ...extra });
  p.busy.delete(name);
  if (status === 401) {
    if (onHarnessesPage()) renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    return;
  }
  if (status !== 200 || !data) {
    p.actionError = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");
  } else if (p.data) {
    const rows = p.data.harnesses;
    const at = rows.findIndex((h) => h.name === data.name);
    if (data.harness && at >= 0) rows[at] = data.harness;
    else if (data.harness) rows.push(data.harness);
    else if (at >= 0) rows.splice(at, 1);
    if (action === "archive" && p.archiving === name) { p.archiving = null; p.reason = ""; }
  } else {
    // A refresh is under way; let it read the registry after this write.
    if (action === "archive" && p.archiving === name) { p.archiving = null; p.reason = ""; }
    if (onHarnessesPage()) renderHarnesses();
    return;
  }
  if (!onHarnessesPage() || !p.data) return;
  paintHarnesses();
  // Keep keyboard focus on the row's star where it still shows: a repaint
  // replaces every button, so focus would otherwise drop to the page.
  const ok = status === 200;
  const next = { star: ok ? "unstar" : "star", unstar: ok ? "star" : "unstar" }[action];
  const row = document.querySelector(`.harness-row[data-harness="${CSS.escape(name)}"]`);
  const target = row && (next ? row.querySelector(`[data-act="${next}"]`)
    : action === "archive" && !ok ? row.querySelector("#harness-reason") : null);
  if (target) target.focus();
}

function onHarnessesPage() {
  return (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "harnesses";
}

/* app.js's api() only reads; writes send JSON with the same credential. */
async function apiPost(path, body) {
  let resp;
  try {
    resp = await fetch(path, {
      method: "POST",
      headers: { Authorization: `Bearer ${credential()}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return { status: 0, data: null };
  }
  let data = null;
  try { data = await resp.json(); } catch { /* empty body */ }
  return { status: resp.status, data };
}

/* ---------------------------------------------------------------- Accounts */

const accountsPage = { data: null, gen: 0, machine: null };
// Harnesses whose limits swe reads (HarnessAccess); others have none to show.
const LIMIT_HARNESSES = new Set(["claude", "codex", "copilot", "cursor", "devin", "droid", "freebuff"]);
const STALE_S = 20 * 60;

async function renderAccounts(_id, refresh = false) {
  const panel = document.getElementById("panel");
  const gen = ++accountsPage.gen;
  if (!accountsPage.data || refresh) {
    panel.innerHTML = `<div class="page">${accountsHeader(true)}<div class="card"><p class="empty">${
      accountsPage.data ? "Refreshing…" : "Reading limits…"}</p></div></div>`;
  } else {
    paintAccounts();
  }
  startAccountsClock();
  accountsPage.loading = true;
  const { status, data } = await api(`/v1/usage${refresh ? "?refresh=1" : ""}`);
  if (gen !== accountsPage.gen) return;
  accountsPage.loading = false;
  // A failed request pauses the clock's repaints, which would otherwise
  // put the old readings back over the error or the pairing steps.
  accountsPage.failed = status !== 200 || !data;
  if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (accountsPage.failed) return pageError(accountsHeader(false), status, data, () => renderAccounts("accounts", true));
  accountsPage.data = data;
  paintAccounts();
}

function accountsHeader(busy) {
  return pageHeader("Accounts", "Usage limits for the harnesses you starred, on the hub and each paired machine.",
    `<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

function paintAccounts() {
  const a = accountsPage, d = a.data;
  const machines = d.machines && d.machines.length ? d.machines
    : [{ machine: d.machine, harnesses: d.harnesses, hub: true, reported_at: d.server_time, received_at: d.server_time }];
  const ids = machines.map((m) => (m.machine && m.machine.id) || "hub");
  if (!a.machine || !ids.includes(a.machine)) a.machine = ids[0];
  const current = machines[ids.indexOf(a.machine)];
  const chips = machines.length > 1 ? `<div class="tabs" role="tablist">${machines.map((m, i) =>
    `<button class="tab${ids[i] === a.machine ? " selected" : ""}" data-machine="${esc(ids[i])}">${
      esc((m.machine && m.machine.name) || ids[i])}${m.hub ? " (hub)" : ""}</button>`).join("")}</div>` : "";
  // A satellite's figures are as old as its last report.
  const age = current.hub ? 0 : Math.max(0, (Date.now() / 1000) - (current.received_at || 0));
  const lateNote = !current.hub && age > 2 * 300
    ? `<div class="card"><p class="note">${esc((current.machine && current.machine.name) || "This machine")} last reported ${Fmt.ago(current.received_at)}; its figures may be out of date.</p></div>` : "";
  const starred = (current.harnesses || []).filter((h) => h.state === "starred");
  const body = starred.length
    ? `<div class="account-grid">${starred.map((h) => accountCard(h, current)).join("")}</div>
       <p class="caption footer">Only starred harnesses show here. Star one on the <a href="#/harnesses">Harnesses</a> page, or with <code>swe harness star &lt;name&gt;</code>.</p>`
    : `<div class="card"><p class="empty">Nothing starred. Star a harness on the <a href="#/harnesses">Harnesses</a> page to see its limits here.</p></div>`;
  document.getElementById("panel").innerHTML = `<div class="page">${accountsHeader(false)}
    <div class="stack">${chips}${lateNote}${body}</div></div>`;
  bindRefresh(() => renderAccounts("accounts", true));
  for (const chip of document.querySelectorAll("[data-machine]")) {
    chip.onclick = () => { a.machine = chip.dataset.machine; paintAccounts(); };
  }
}

/* Ages, countdowns, staleness and the late-satellite note move with the
 * clock, so the open page repaints them every 30 s (no provider call). */
function startAccountsClock() {
  if (accountsPage.timer) return;
  accountsPage.timer = setInterval(() => {
    const onPage = (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "accounts";
    if (!onPage) { clearInterval(accountsPage.timer); accountsPage.timer = null; return; }
    if (accountsPage.data && !accountsPage.loading && !accountsPage.failed && !document.hidden) paintAccounts();
  }, 30000);
}

/* A reading's windows, or one built from its top-level fields. */
function readingWindows(rl) {
  if (rl.windows && rl.windows.length) return rl.windows;
  return [{ window: rl.window, used_percent: rl.used_percent, reset_at: rl.reset_at }];
}

function barColor(pct) {
  return pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warning)" : "var(--accent-fill)";
}

/* ``now`` ages the machine's own stamps (as_of); a reset time is the
 * provider's absolute clock, so it counts down on this browser's. */
function windowRow(w, now) {
  const pct = Math.max(0, Math.min(100, Math.round(w.used_percent || 0)));
  const failed = !!w.unavailable;
  const real = Date.now() / 1000;
  const right = failed
    ? (w.as_of ? `as of ${Fmt.ago(w.as_of, now)}` : "")
    : (w.reset_at && w.reset_at > real ? `Resets ${Fmt.resetIn(w.reset_at, real)}` : "");
  return `<div class="limit${failed ? " failed" : ""}">
    <div class="limit-head"><span class="limit-label">${esc(Fmt.windowLabel(w.window))}</span>
      <span class="num">${failed && !w.as_of ? "—" : `${pct}%`}</span></div>
    <div class="track"><div class="fill" style="width:${pct}%; background:${barColor(pct)}"></div></div>
    ${right ? `<div class="caption">${esc(right)}</div>` : ""}
  </div>`;
}

function accountCard(h, machine) {
  const rl = h.rate_limit;
  // A satellite's clock may differ: age its reading on the hub's clock.
  const skew = machine.hub ? 0 : (machine.received_at || 0) - (machine.reported_at || machine.received_at || 0);
  const now = Date.now() / 1000 - skew;
  const plan = rl ? Fmt.planTitle(rl.plan_type) : "";
  let body = "", status;
  if (!rl) {
    status = LIMIT_HARNESSES.has(h.name) ? "No reading yet" : "No usage limits to read";
  } else if (rl.status === "ok") {
    body = readingWindows(rl).map((w) => windowRow(w, now)).join("");
    if (rl.on_demand) {
      const [value, detail] = Fmt.onDemand(rl.on_demand);
      body += `<div class="limit-extra"><span class="caption">On-demand</span><span>${esc(value)} · <span class="caption">${esc(detail)}</span></span></div>`;
    }
    if (rl.extra_usage_usd != null) {
      body += `<div class="limit-extra"><span class="caption">Extra usage balance</span><span class="num">${Fmt.balance(rl.extra_usage_usd)}</span></div>`;
    }
    status = rl.fetched_at ? `Fetched ${Fmt.ago(rl.fetched_at, now)}` : "No reading yet";
  } else {
    status = { "auth-required": `Signed out. Sign in again in ${Fmt.harnessName(h.name)}.`,
      "no-sub": "No subscription" }[rl.status] || "Didn't answer in time";
  }
  // Older than 20 minutes reads as stale, as in the menu bar.
  const stale = rl && rl.status === "ok" && (!rl.fetched_at || now - rl.fetched_at > STALE_S);
  return `<div class="card account${stale ? " stale" : ""}">
    <div class="account-head">${Fmt.logo(h.name)}<span class="card-title">${esc(harnessTitle(h))}</span>
      ${plan ? `<span class="badge">${esc(plan)}</span>` : ""}</div>
    ${body}
    <div class="caption">${esc(status)}</div>
  </div>`;
}

/* ---------------------------------------------------------------- shared */

function bindRefresh(render, before) {
  const btn = document.getElementById("page-refresh");
  if (btn) btn.onclick = () => { if (before) before(); render(); };
}

function pageError(header, status, data, retry) {
  const msg = (data && data.error && data.error.message)
    || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
  document.getElementById("panel").innerHTML = `<div class="page">${header}<div class="card"><p class="error">${esc(msg)}</p></div></div>`;
  bindRefresh(retry);
}
