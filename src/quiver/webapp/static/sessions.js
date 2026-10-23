/* Sessions page: the hub's sessions with tokens and spend, filtered,
 * grouped by day. Ports the Mac app's SessionsPage. Loaded before app.js;
 * it uses app.js's helpers (api, esc, icon, pageHeader) only when called.
 */
"use strict";

const WINDOWS = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"],
  ["365d", "365 days"], ["all", "All time"]];
const DAY_FOLD = 10;
const STATUS_BADGE = { active: "Active", error: "Error", interrupted: "Interrupted", followup: "Waiting" };

const sessionsPage = {
  data: null, gen: 0,
  query: "", tool: "", window: "90d", project: "", model: "",
  showTrivial: false, openDays: new Set(),
};

async function renderSessions(_id, rescan = false) {
  const panel = document.getElementById("panel");
  const gen = ++sessionsPage.gen;
  if (!sessionsPage.data || rescan) {
    panel.innerHTML = `<div class="page">${sessionsHeader(true)}<div class="card"><p class="empty">${
      sessionsPage.data ? "Refreshing…" : "Listing sessions…"}</p></div></div>`;
  }
  const { status, data } = await api(`/v1/app/sessions${rescan ? "?rescan=1" : ""}`);
  if (gen !== sessionsPage.gen) return;
  if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (status !== 200 || !data) {
    const msg = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
    panel.innerHTML = `<div class="page">${sessionsHeader(false)}<div class="card"><p class="error">${esc(msg)}</p></div></div>`;
    bindSessionsHeader();
    return;
  }
  sessionsPage.data = data;
  paintSessions();
}

function sessionsHeader(busy) {
  const d = sessionsPage.data;
  const updated = d && d.scanned_at ? `<span class="caption">Updated ${Fmt.ago(d.scanned_at)}</span>` : "";
  const where = d && d.machine ? ` on ${d.machine} and its satellites` : "";
  return pageHeader("Sessions", `Sessions${where}, usage and estimated cost.`,
    `${updated}<button class="icon-button${busy ? " spinning" : ""}" id="sessions-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

function bindSessionsHeader() {
  const btn = document.getElementById("sessions-refresh");
  if (btn) btn.onclick = () => renderSessions("sessions", true);
}

function windowStart(win, now = Date.now() / 1000) {
  if (win === "today") { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; }
  const days = { "7d": 7, "30d": 30, "90d": 90, "365d": 365 }[win];
  return days ? now - days * 86400 : null;
}

/* The rows the filters keep, and how many of them are accidental. */
function filteredSessions() {
  const s = sessionsPage;
  const start = windowStart(s.window);
  const q = s.query.trim().toLowerCase();
  const kept = [];
  let trivial = 0;
  for (const r of s.data.sessions) {
    if (start != null && r.last_active_at < start) continue;
    if (s.tool && r.harness !== s.tool) continue;
    if (s.project && r.project_key !== s.project) continue;
    if (s.model && !(r.usage && r.usage.models.some((m) => m.model === s.model))) continue;
    if (q) {
      const hay = [r.title, r.cwd, r.project, r.id, r.agent, r.location, r.machine,
        ...(r.usage ? r.usage.models.map((m) => m.model) : [])].join(" ").toLowerCase();
      if (!hay.includes(q)) continue;
    }
    if (r.trivial && !s.showTrivial) { trivial++; continue; }
    kept.push(r);
  }
  return { kept, trivial };
}

function paintSessions() {
  const s = sessionsPage, d = s.data;
  const { kept, trivial } = filteredSessions();
  const notes = (d.notes || []).map((n) => `<div class="card"><p class="note">${esc(n)}</p></div>`).join("");
  const body = !d.sessions.length
    ? `<div class="card"><p class="empty">No sessions listed. Sessions from every harness on the hub machine, and on paired satellites, show here.</p></div>`
    : !kept.length
      ? `<div class="card"><p class="empty">No sessions match these filters.</p>
          <div><button class="tab selected" id="clear-filters">Clear filters</button></div></div>`
      : sessionsTable(kept);
  const footer = trivial || s.showTrivial ? trivialFooter(trivial) : "";
  document.getElementById("panel").innerHTML = `<div class="page">
    ${sessionsHeader(false)}
    <div class="stack">${notes}${filterBar()}${summaryStrip(kept)}${body}${footer}</div>
  </div>
  <dialog class="sheet" id="session-sheet"></dialog>`;
  bindSessionsHeader();
  bindSessionControls();
}

function options(list, selected) {
  return list.map(([value, label]) =>
    `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(label)}</option>`).join("");
}

function filterBar() {
  const s = sessionsPage, rows = s.data.sessions;
  const tools = [...new Set(rows.map((r) => r.harness))].sort();
  // Projects: counts over every row, largest first; hash-like names fold
  // into one "Other" entry at the end.
  const projects = new Map();
  let other = 0;
  for (const r of rows) {
    if (!r.project_key) continue;
    if (r.project_key === "other") { other++; continue; }
    const p = projects.get(r.project_key) || { name: r.project, n: 0 };
    p.n++;
    projects.set(r.project_key, p);
  }
  const projectList = [...projects.entries()]
    .sort((a, b) => b[1].n - a[1].n || a[1].name.localeCompare(b[1].name, undefined, { sensitivity: "base" }))
    .map(([key, p]) => [key, `${p.name} (${p.n})`]);
  if (other) projectList.push(["other", `Other (${other})`]);
  // Models: tokens inside the chosen window, largest first.
  const start = windowStart(s.window);
  const models = new Map();
  for (const r of rows) {
    if (!r.usage || (start != null && r.last_active_at < start)) continue;
    for (const m of r.usage.models) models.set(m.model, (models.get(m.model) || 0) + m.tokens);
  }
  const modelList = [...models.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([m, t]) => [m, `${m} (${Fmt.tokens(t)})`]);
  // A chosen model stays listed when the window has none of it, so the
  // menu always shows the filter that is on.
  if (s.model && !models.has(s.model)) modelList.unshift([s.model, `${s.model} (none in this window)`]);
  return `<div class="filters">
    <input class="field search" id="session-search" type="search" placeholder="Search sessions"
      value="${esc(s.query)}" autocomplete="off" spellcheck="false">
    <select class="field" id="filter-tool" aria-label="Tool">${options([["", "All tools"],
      ...tools.map((t) => [t, Fmt.harnessName(t)])], s.tool)}</select>
    <select class="field" id="filter-window" aria-label="Time">${options(WINDOWS, s.window)}</select>
    <select class="field" id="filter-project" aria-label="Project">${options([["", "All projects"], ...projectList], s.project)}</select>
    <select class="field" id="filter-model" aria-label="Model">${options([["", "All models"], ...modelList], s.model)}</select>
  </div>`;
}

function summaryStrip(rows) {
  let tokens = 0, cost = 0, unpriced = 0;
  for (const r of rows) {
    if (!r.usage) continue;
    tokens += r.usage.tokens; cost += r.usage.cost_usd; unpriced += r.usage.unpriced_tokens;
  }
  const note = unpriced ? ` <span class="caption" title="Plus ${Fmt.tokens(unpriced)} tokens with no list price">ⓘ</span>` : "";
  return `<div class="card summary num">
    <div><div class="caption">Matching sessions</div><div class="figure">${rows.length.toLocaleString("en-US")}</div></div>
    <div><div class="caption">Tokens</div><div class="figure">${Fmt.tokens(tokens)}</div></div>
    <div><div class="caption">Spend</div><div class="figure spend">${Fmt.dollars(cost)}${note}</div></div>
  </div>`;
}

function localDay(epoch) {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function sessionsTable(rows) {
  const days = [];
  for (const r of rows) {
    const day = localDay(r.last_active_at);
    if (!days.length || days[days.length - 1].day !== day) days.push({ day, rows: [] });
    days[days.length - 1].rows.push(r);
  }
  const groups = days.map(({ day, rows: list }) => {
    const open = sessionsPage.openDays.has(day);
    const shown = open ? list : list.slice(0, DAY_FOLD);
    const hidden = list.length - DAY_FOLD;
    const fold = hidden > 0
      ? `<button class="fold" data-day="${day}">${open ? `Show only the first ${DAY_FOLD}` : `Show every session from this day (${hidden} more)`}</button>`
      : "";
    const [y, m, dd] = day.split("-").map(Number);
    const label = new Date(y, m - 1, dd).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    return `<div class="day"><div class="day-label">${label}</div>${shown.map(sessionRow).join("")}${fold}</div>`;
  }).join("");
  return `<div class="card sessions">
    <div class="session-head"><span>Session / project</span><span class="num-col">Tokens</span><span class="num-col">Spend</span></div>
    ${groups}</div>`;
}

function duration(seconds) {
  if (seconds == null || seconds < 60) return "";
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

function sessionRow(r) {
  const badge = STATUS_BADGE[r.status] ? `<span class="badge status-${r.status}">${STATUS_BADGE[r.status]}</span>` : "";
  const time = new Date(r.last_active_at * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const second = [r.project, Fmt.harnessName(r.harness), r.remote ? r.machine : r.location,
    r.usage ? r.usage.models.map((m) => m.model).join(", ") : "", time, duration(r.duration_s)]
    .filter(Boolean).map(esc).join(" · ");
  return `<button class="session-row" data-key="${esc(r.key)}">
    <span class="session-logo" title="${esc(Fmt.harnessName(r.harness))}">${Fmt.logo(r.harness)}</span>
    <span class="session-main"><span class="session-title"><span class="ellipsis">${esc(r.title || r.id || "Untitled")}</span>${badge}</span>
      <span class="session-sub">${second}</span></span>
    <span class="num-col num">${r.usage ? Fmt.tokens(r.usage.tokens) : "–"}</span>
    <span class="num-col num spend">${r.usage ? Fmt.dollars(r.usage.cost_usd) : "–"}</span>
  </button>`;
}

function trivialFooter(count) {
  return sessionsPage.showTrivial
    ? `<p class="caption footer">Showing short or empty sessions. <button class="link" id="toggle-trivial">Hide</button></p>`
    : `<p class="caption footer">${count} short or empty session${count === 1 ? "" : "s"} hidden. <button class="link" id="toggle-trivial">Show</button></p>`;
}

/* The shell line that resumes a session where it ran. */
function resumeLine(r) {
  if (!r.resume || !r.resume.argv) return "";
  const quote = (s) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${String(s).replace(/'/g, "'\\''")}'`);
  const cmd = r.resume.argv.map(quote).join(" ");
  const cwd = r.resume.cwd || r.cwd;
  return cwd ? `cd ${quote(cwd)} && ${cmd}` : cmd;
}

function openSheet(r) {
  const sheet = document.getElementById("session-sheet");
  const u = r.usage;
  const field = (label, value) => (value ? `<div class="field-row"><span class="caption">${label}</span><span>${value}</span></div>` : "");
  const lastActive = new Date(r.last_active_at * 1000).toLocaleString("en-US",
    { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  const figures = u ? `<div class="chips five num">
      ${[["Input", u.input], ["Cache read", u.cache_read], ["Cache write", u.cache_write], ["Output", u.output + u.reasoning]]
        .map(([l, v]) => `<div class="chip"><div class="value">${Fmt.tokens(v)}</div><div class="label">${l}</div></div>`).join("")}
      <div class="chip"><div class="value spend">${Fmt.dollars(u.cost_usd)}</div><div class="label">Spend</div></div>
    </div>${u.unpriced_tokens ? `<p class="caption">${Fmt.tokens(u.unpriced_tokens)} tokens from models with no list price.</p>` : ""}`
    : `<p class="caption">${r.remote ? "Usage is read on the hub machine only; this session ran on " + esc(r.machine) + "."
      : "No token usage recorded for this session in the ledger."}</p>`;
  const line = resumeLine(r);
  sheet.innerHTML = `<form method="dialog" class="sheet-body">
    <div class="sheet-title">${Fmt.logo(r.harness)}<span>${esc(r.title || r.id || "Untitled")}</span>
      ${STATUS_BADGE[r.status] ? `<span class="badge status-${r.status}">${STATUS_BADGE[r.status]}</span>` : ""}</div>
    <div class="fields">
      ${field("Harness", esc(Fmt.harnessName(r.harness)))}
      ${field("Machine", esc(r.machine || ""))}
      ${field("Location", esc(r.location))}
      ${field("Folder", esc(r.cwd || "Unknown"))}
      ${field("Last active", esc(lastActive))}
      ${field("Session id", `<code>${esc(r.id || "")}</code>`)}
      ${u ? field("Models", u.models.map((m) => esc(m.model)).join(", ")) : ""}
      ${field("Ran for", esc(duration(r.duration_s)))}
    </div>
    ${figures}
    ${line ? `<div class="caption">Resume</div><pre class="command" id="resume-line">${esc(line)}</pre>` : ""}
    <div class="sheet-actions">
      <button type="button" class="tab" id="copy-id">Copy session ID</button>
      ${line ? '<button type="button" class="tab" id="copy-resume">Copy resume command</button>' : ""}
      <button class="tab selected" value="close">Close</button>
    </div>
  </form>`;
  // navigator.clipboard exists only on https or localhost; a hub reached
  // over plain http gets the older copy command, then a manual hint.
  const copy = (text, btn) => {
    const done = () => { btn.textContent = "Copied"; };
    const fallback = () => {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "absolute";
      area.style.left = "-9999px";
      sheet.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
      if (ok) return done();
      btn.textContent = "Select and copy it by hand";
      const pre = sheet.querySelector("#resume-line");
      if (pre && text === line) window.getSelection().selectAllChildren(pre);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else {
      fallback();
    }
  };
  sheet.querySelector("#copy-id").onclick = (e) => copy(r.id || "", e.target);
  const cr = sheet.querySelector("#copy-resume");
  if (cr) cr.onclick = (e) => copy(line, e.target);
  sheet.showModal();
}

function bindSessionControls() {
  const s = sessionsPage;
  const search = document.getElementById("session-search");
  if (search) {
    search.oninput = () => {
      s.query = search.value;
      const at = search.selectionStart;
      paintSessions();
      const again = document.getElementById("session-search");
      again.focus();
      again.setSelectionRange(at, at);
    };
  }
  for (const [id, key] of [["filter-tool", "tool"], ["filter-window", "window"],
    ["filter-project", "project"], ["filter-model", "model"]]) {
    const el = document.getElementById(id);
    if (el) el.onchange = () => { s[key] = el.value; paintSessions(); };
  }
  const clear = document.getElementById("clear-filters");
  if (clear) clear.onclick = () => {
    Object.assign(s, { query: "", tool: "", project: "", model: "", window: "all" });
    paintSessions();
  };
  const toggle = document.getElementById("toggle-trivial");
  if (toggle) toggle.onclick = () => { s.showTrivial = !s.showTrivial; paintSessions(); };
  for (const btn of document.querySelectorAll(".fold[data-day]")) {
    btn.onclick = () => {
      const day = btn.dataset.day;
      if (s.openDays.has(day)) s.openDays.delete(day); else s.openDays.add(day);
      paintSessions();
    };
  }
  const byKey = new Map(s.data.sessions.map((r) => [r.key, r]));
  for (const row of document.querySelectorAll(".session-row")) {
    row.onclick = () => { const r = byKey.get(row.dataset.key); if (r) openSheet(r); };
  }
}
