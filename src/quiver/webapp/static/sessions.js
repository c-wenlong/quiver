/* Sessions page: the hub's sessions with tokens and spend, filtered,
 * grouped by day. Ports the Mac app's SessionsPage. Loaded before app.js;
 * it uses app.js's helpers (api, esc, icon, pageHeader) only when called.
 * A row opens the session's chat (pages/chat.js, #/sessions?chat=<key>),
 * whose Details button shows the sheet this page used to open.
 *
 * The search box filters the rows' details in the browser and also asks
 * the hub's /v1/search, which matches what was said in each transcript
 * when the hub has a search index. A transcript hit keeps its row and
 * shows the matching snippet under it.
 */
"use strict";

const WINDOWS = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["90d", "90 days"],
  ["365d", "365 days"], ["all", "All time"]];
const DAY_FOLD = 10;
const STATUS_BADGE = { active: "Active", error: "Error", interrupted: "Interrupted", followup: "Waiting" };
// Transcript search waits for a pause in typing and a query this long.
const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_MIN_CHARS = 2;
const SEARCH_LIMIT = 200;

const sessionsPage = {
  data: null, gen: 0,
  query: "", tool: "", window: "90d", project: "", model: "",
  showTrivial: false, openDays: new Set(),
  // /v1/search for `search.query`: key -> { snippet, marked }, whether the
  // hub has an index, and "pending" / "error" while it has no answer.
  search: { query: "", hits: new Map(), indexed: null, state: "" },
  searchTimer: null, searchSeq: 0,
  // Where the list was scrolled when a chat opened, restored on the way back.
  scrollY: null,
};

const sessionChat = () => globalThis.SessionChat || null;

async function renderSessions(_id, rescan = false) {
  const chat = sessionChat();
  const chatKey = chat ? chat.keyFromHash() : null;
  if (chatKey) {
    sessionsPage.gen++;
    return chat.open(chatKey, { row: sessionRowFor(chatKey), details: () => sessionDetails(chatKey) });
  }
  if (chat) chat.close();
  const panel = document.getElementById("panel");
  const gen = ++sessionsPage.gen;
  if (!sessionsPage.data || rescan) {
    panel.innerHTML = `<div class="page">${sessionsHeader(true)}<div class="card"><p class="empty">${
      sessionsPage.data ? "Refreshing…" : "Listing sessions…"}</p></div></div>`;
  } else {
    paintSessions();
    if (sessionsPage.scrollY != null) window.scrollTo(0, sessionsPage.scrollY);
    scheduleTranscriptSearch();
  }
  sessionsPage.scrollY = null;
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
  scheduleTranscriptSearch();
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

/* The transcript hit for a row, when the answer on hand is for the
 * query in the box now. */
function transcriptHit(key) {
  const s = sessionsPage;
  if (s.search.query !== s.query.trim()) return null;
  return s.search.hits.get(key) || null;
}

/* A snippet with its matched terms in <mark>. The hub wraps them in the
 * private-use pair U+E000/U+E001, which esc() leaves alone. */
function markedSnippet(hit) {
  const text = hit.marked || hit.snippet || "";
  return esc(text).replace(/\uE000/g, "<mark>").replace(/\uE001/g, "</mark>");
}

/* Ask /v1/search for the box's query once typing pauses. Only the newest
 * answer lands; an older one that returns late is dropped. */
function scheduleTranscriptSearch() {
  const s = sessionsPage;
  const q = s.query.trim();
  // The same trimmed query (a trailing space, a page coming back) keeps
  // the request or answer it already has.
  if (s.search.query === q && s.search.state !== "error") return;
  clearTimeout(s.searchTimer);
  const seq = ++s.searchSeq;
  if (q.length < SEARCH_MIN_CHARS) {
    s.search = { query: q, hits: new Map(), indexed: s.search.indexed, state: "" };
    return;
  }
  s.search = { query: q, hits: new Map(), indexed: s.search.indexed, state: "pending" };
  s.searchTimer = setTimeout(async () => {
    const { status, data } = await api(`/v1/search?q=${encodeURIComponent(q)}&limit=${SEARCH_LIMIT}`);
    if (seq !== s.searchSeq) return;
    if (!onSessionsList()) {
      // Left for a chat or another page: forget the request, so the list
      // asks again when it opens.
      s.search = { query: "", hits: new Map(), indexed: s.search.indexed, state: "" };
      return;
    }
    const hits = new Map();
    if (status === 200 && data) {
      for (const r of data.results || []) {
        if (r.source === "index" || r.snippet) hits.set(r.key, { snippet: r.snippet, marked: r.marked_snippet });
      }
    }
    s.search = status === 200 && data
      ? { query: q, hits, indexed: !!data.indexed, state: "" }
      : { query: q, hits, indexed: s.search.indexed, state: "error" };
    repaintKeepingFocus();
  }, SEARCH_DEBOUNCE_MS);
}

function onSessionsList() {
  return !!document.getElementById("session-search");
}

/* The line under the filters while a query is in the box. */
function searchNote(kept) {
  const s = sessionsPage, q = s.query.trim();
  if (q.length < SEARCH_MIN_CHARS || s.search.query !== q) return "";
  if (s.search.state === "pending") return `<p class="caption search-note">Searching what was said…</p>`;
  if (s.search.state === "error") {
    return `<p class="caption search-note">Couldn't reach transcript search, so this matches titles and details only.</p>`;
  }
  if (s.search.indexed === false) {
    return `<p class="caption search-note">Searching titles and details only. Turn on <a href="#/settings?section=experimental">Session search</a> in Settings › Experimental to search what was said.</p>`;
  }
  const said = kept.filter((r) => transcriptHit(r.key)).length;
  if (!said) return "";
  const n = kept.length.toLocaleString("en-US");
  const text = kept.length === 1 ? "1 session matches on what was said."
    : said === kept.length ? `${n} sessions match, all on what was said.`
      : `${n} sessions match, ${said.toLocaleString("en-US")} of them on what was said.`;
  return `<p class="caption search-note">${text}</p>`;
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
      if (!hay.includes(q) && !transcriptHit(r.key)) continue;
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
    <div class="stack">${notes}${filterBar()}${searchNote(kept)}${summaryStrip(kept)}${body}${footer}</div>
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
  const badge = (STATUS_BADGE[r.status] ? `<span class="badge status-${r.status}">${STATUS_BADGE[r.status]}</span>` : "")
    + (r.ownership === "attached" ? '<span class="badge ownership-attached" title="Runs under Quiver: you can send to it">Attached</span>' : "");
  const time = new Date(r.last_active_at * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const hit = sessionsPage.query.trim() ? transcriptHit(r.key) : null;
  const snippet = hit && (hit.marked || hit.snippet)
    ? `<span class="session-snippet">${markedSnippet(hit)}</span>` : "";
  const second = [r.project, Fmt.harnessName(r.harness), r.remote ? r.machine : r.location,
    r.usage ? r.usage.models.map((m) => m.model).join(", ") : "", time, duration(r.duration_s)]
    .filter(Boolean).map(esc).join(" · ");
  return `<button class="session-row" data-key="${esc(r.key)}">
    <span class="session-logo" title="${esc(Fmt.harnessName(r.harness))}">${Fmt.logo(r.harness)}</span>
    <span class="session-main"><span class="session-title"><span class="ellipsis">${esc(r.title || r.id || "Untitled")}</span>${badge}</span>
      <span class="session-sub">${second}</span>${snippet}</span>
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
  // The supervised channel: the attach mode, and the qv-… pane name for
  // tmux runs (a kept one stays alive detached until adopted or reaped).
  const supervised = (r.ownership === "attached" || r.kept) && (r.mode || r.tmux);
  const tmuxLine = r.tmux ? `tmux attach -t ${r.tmux}` : "";
  const canOpenTerminal = !!(tmuxLine && !r.remote &&
    sessionsPage.data && sessionsPage.data.open_terminal);
  sheet.innerHTML = `<form method="dialog" class="sheet-body">
    <div class="sheet-title">${Fmt.logo(r.harness)}<span>${esc(r.title || r.id || "Untitled")}</span>
      ${STATUS_BADGE[r.status] ? `<span class="badge status-${r.status}">${STATUS_BADGE[r.status]}</span>` : ""}</div>
    <div class="fields">
      ${field("Harness", esc(Fmt.harnessName(r.harness)))}
      ${field("Machine", esc(r.machine || ""))}
      ${field("Location", esc(r.location))}
      ${field("Folder", esc(r.cwd || "Unknown"))}
      ${field("Last active", esc(lastActive))}
      ${field("Session id", `<code>${esc(r.id || "")}</code><button type="button" class="icon-button copy-inline" id="copy-id" aria-label="Copy session ID" title="Copy session ID">${Fmt.copyIcon()}</button>`)}
      ${u ? field("Models", u.models.map((m) => esc(m.model)).join(", ")) : ""}
      ${supervised ? field("Supervised",
        `${esc(r.mode || "tmux")}${r.tmux ? ` · <code>${esc(r.tmux)}</code>` : ""}${r.kept ? " (kept)" : ""}`) : ""}
      ${field("Ran for", esc(duration(r.duration_s)))}
    </div>
    ${figures}
    ${line ? `<div class="caption">Resume</div><div class="command-wrap"><pre class="command" id="resume-line">${esc(line)}</pre>
      <button type="button" class="icon-button command-copy" id="copy-resume" aria-label="Copy resume command" title="Copy resume command">${Fmt.copyIcon()}</button></div>` : ""}
    ${tmuxLine ? `<div class="caption">Attach from a terminal</div><div class="command-wrap"><pre class="command" id="tmux-line">${esc(tmuxLine)}</pre>
      <button type="button" class="icon-button command-copy" id="copy-tmux" aria-label="Copy attach command" title="Copy attach command">${Fmt.copyIcon()}</button></div>` : ""}
    <div class="sheet-actions">
      ${canOpenTerminal ? '<button type="button" class="tab" id="run-terminal">Run in terminal</button>' : ""}
      <button class="tab selected" value="close">Close</button>
    </div>
  </form>`;
  // Plain http has no clipboard API: Fmt.copy's fallback still fails on
  // some browsers, so a failed copy selects the text to grab by hand.
  const byHand = (sel) => (btn) => {
    btn.title = "Couldn't copy — select it by hand";
    const el = sel ? sheet.querySelector(sel)
      : btn.parentElement && btn.parentElement.querySelector("code");
    if (el) window.getSelection().selectAllChildren(el);
  };
  Fmt.copyTick(sheet.querySelector("#copy-id"), () => r.id || "", byHand(null));
  Fmt.copyTick(sheet.querySelector("#copy-resume"), () => line, byHand("#resume-line"));
  Fmt.copyTick(sheet.querySelector("#copy-tmux"), () => tmuxLine, byHand("#tmux-line"));
  const run = sheet.querySelector("#run-terminal");
  if (run) {
    run.onclick = async () => {
      run.disabled = true;
      const { status, data } = await apiPost("/v1/app/sessions/open_terminal", { key: r.key });
      run.disabled = false;
      if (status === 200) {
        run.textContent = "Opened";
        setTimeout(() => { if (document.contains(run)) run.textContent = "Run in terminal"; }, 2000);
      } else {
        run.textContent = "Couldn't open";
        run.title = (data && data.error && data.error.message) ||
          (status ? `The hub answered ${status}.` : "Couldn't reach the hub.");
      }
    };
  }
  sheet.showModal();
}

function bindSessionControls() {
  const s = sessionsPage;
  const search = document.getElementById("session-search");
  if (search) {
    search.oninput = () => {
      s.query = search.value;
      scheduleTranscriptSearch();
      repaintKeepingFocus();
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
    scheduleTranscriptSearch();
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
    row.onclick = () => {
      const r = byKey.get(row.dataset.key);
      if (!r) return;
      // Without the chat script (it failed to load), a row keeps its sheet.
      if (!sessionChat()) return openSheet(r);
      s.scrollY = window.scrollY;
      location.hash = `#/sessions?chat=${encodeURIComponent(r.key)}`;
    };
  }
}

/* Repaint without losing the search box's focus or selection. */
function repaintKeepingFocus() {
  const box = document.getElementById("session-search");
  const focused = box && document.activeElement === box;
  const sel = box ? [box.selectionStart, box.selectionEnd, box.selectionDirection] : null;
  paintSessions();
  const again = document.getElementById("session-search");
  if (again && focused) {
    again.focus();
    if (sel && sel[0] != null) again.setSelectionRange(sel[0], sel[1], sel[2] || "none");
  }
}

function sessionRowFor(key) {
  return (sessionsPage.data && sessionsPage.data.sessions.find((r) => r.key === key)) || null;
}

/* The chat's Details button: the row's sheet, read from the list's data
 * (fetched now when the chat was opened from a link). */
async function sessionDetails(key) {
  let r = sessionRowFor(key);
  if (!r) {
    const { status, data } = await api("/v1/app/sessions");
    if (status === 200 && data) {
      sessionsPage.data = data;
      r = sessionRowFor(key);
    }
  }
  // The answer may land after the chat was left for another page.
  const chat = sessionChat();
  if (!r || (chat && chat.key !== key) || !document.getElementById("session-sheet")) return false;
  openSheet(r);
  return true;
}
