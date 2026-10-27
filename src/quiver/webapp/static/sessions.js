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
  // Keys with a control write in flight (their action button reads
  // "Attaching…"/"Detaching…"), and the one-line result an action left.
  acting: new Set(), actionNote: "",
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
    `${updated}<button class="tab" id="new-session">New session</button>
    <button class="icon-button${busy ? " spinning" : ""}" id="sessions-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

function bindSessionsHeader() {
  const btn = document.getElementById("sessions-refresh");
  if (btn) btn.onclick = () => renderSessions("sessions", true);
  const add = document.getElementById("new-session");
  if (add) add.onclick = () => openNewSession();
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
  const actNote = s.actionNote ? `<div class="card"><p class="note">${esc(s.actionNote)}</p></div>` : "";
  const body = !d.sessions.length
    ? `<div class="card"><p class="empty">No sessions listed. Sessions from every harness on the hub machine, and on paired satellites, show here.</p></div>`
    : !kept.length
      ? `<div class="card"><p class="empty">No sessions match these filters.</p>
          <div><button class="tab selected" id="clear-filters">Clear filters</button></div></div>`
      : sessionsTable(kept);
  const footer = trivial || s.showTrivial ? trivialFooter(trivial) : "";
  document.getElementById("panel").innerHTML = `<div class="page">
    ${sessionsHeader(false)}
    <div class="stack">${notes}${actNote}${filterBar()}${searchNote(kept)}${summaryStrip(kept)}${body}${footer}</div>
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

/* The lifecycle action a row offers, the phone's leading-swipe rule:
 * detach an attached one, attach one the hub can take over (a kept pane
 * attaches even mid-turn: it is adopted, nothing restarts). */
function rowAction(r) {
  if (r.ownership === "attached") return "detach";
  const busy = r.status === "active" || r.status === "approval";
  if ((r.attachable || r.kept) && (r.kept || !busy)) return "attach";
  return "";
}

function sessionRow(r) {
  const badge = (STATUS_BADGE[r.status] ? `<span class="badge status-${r.status}">${STATUS_BADGE[r.status]}</span>` : "")
    + (r.ownership === "attached" ? '<span class="badge ownership-attached" title="Runs under Quiver: you can send to it">Attached</span>' : "")
    + (r.kept ? '<span class="badge" title="Detached, still running in tmux">Kept</span>' : "");
  const time = new Date(r.last_active_at * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const hit = sessionsPage.query.trim() ? transcriptHit(r.key) : null;
  const snippet = hit && (hit.marked || hit.snippet)
    ? `<span class="session-snippet">${markedSnippet(hit)}</span>` : "";
  const second = [r.project, Fmt.harnessName(r.harness), r.remote ? r.machine : r.location,
    r.usage ? r.usage.models.map((m) => m.model).join(", ") : "", time, duration(r.duration_s)]
    .filter(Boolean).map(esc).join(" · ");
  const act = rowAction(r);
  const acting = sessionsPage.acting.has(r.key);
  const actHtml = act
    ? `<button type="button" class="row-act" data-act="${act}" data-key="${esc(r.key)}"${acting ? " disabled" : ""}>${
        acting ? (act === "attach" ? "Attaching…" : "Detaching…") : (act === "attach" ? "Attach" : "Detach")}</button>`
    : "";
  // Not a <button>: the row carries its own Attach/Detach button inside,
  // and a button can't hold one. Click and Enter/Space act the same.
  return `<div class="session-row" role="button" tabindex="0" data-key="${esc(r.key)}">
    <span class="session-logo" title="${esc(Fmt.harnessName(r.harness))}">${Fmt.logo(r.harness)}</span>
    <span class="session-main"><span class="session-title"><span class="ellipsis">${esc(r.title || r.id || "Untitled")}</span>${badge}</span>
      <span class="session-sub">${second}</span>${snippet}</span>
    <span class="session-acts">${actHtml}</span>
    <span class="num-col num">${r.usage ? Fmt.tokens(r.usage.tokens) : "–"}</span>
    <span class="num-col num spend">${r.usage ? Fmt.dollars(r.usage.cost_usd) : "–"}</span>
  </div>`;
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
      ${(() => { const act = rowAction(r); return act
        ? `<button type="button" class="tab" id="sheet-act">${act === "attach" ? "Attach" : "Detach"}</button>` : ""; })()}
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
  const act = sheet.querySelector("#sheet-act");
  if (act) {
    act.onclick = (e) => {
      e.preventDefault();
      sheet.close();
      if (rowAction(r) === "attach") attachRow(r); else detachClick(r);
    };
  }
  sheet.showModal();
}

/* --------------------------------------------------------- lifecycle */

/* The same session control routes the chat uses, one hop per write. */
function sessionControl(key, op, mode) {
  const body = mode ? { op, mode } : { op };
  return apiPost(`/v1/sessions/${encodeURIComponent(key)}/control`, body);
}

function controlErrorText(status, data, fallback) {
  if (data && data.error && data.error.message) return data.error.message;
  return status ? `The hub answered ${status}.` : fallback;
}

/* A finished write leaves the row's ownership stale until the next list:
 * refetch rather than patch it, so badges and actions always agree. */
async function runControl(r, op, mode, doneNote) {
  const s = sessionsPage;
  if (s.acting.has(r.key)) return;
  s.acting.add(r.key);
  paintSessions();
  const { status, data } = await sessionControl(r.key, op, mode);
  s.acting.delete(r.key);
  if (status === 401) {
    return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  }
  if (status === 200 || status === 202) {
    s.actionNote = doneNote || "";
    renderSessions("sessions");
    return;
  }
  s.actionNote = controlErrorText(status, data,
    `Couldn't ${op === "attach" ? "attach it" : "detach it"}. Check the connection, then try again.`);
  paintSessions();
}

function attachRow(r) {
  const where = r.remote && r.machine ? ` on ${r.machine}` : "";
  return runControl(r, "attach", null, `Attaching${where}…`);
}

function detachRow(r, mode) {
  const where = r.remote && r.machine ? ` on ${r.machine}` : "";
  const note = mode === "keep" ? `Detached — it keeps running in tmux${where}.`
    : mode === "when_idle" ? "It detaches when the turn ends." : "";
  return runControl(r, "detach", mode, note);
}

/* The chat's same three-way choice, stacked like the phone's: idle rows
 * just detach when_idle, busy ones ask first since `now` cuts the turn. */
function detachClick(r) {
  const busy = r.status === "active" || r.status === "approval";
  if (!busy) return detachRow(r, "when_idle");
  const sheet = detachSheetEl();
  const canKeep = r.tmux || r.mode === "tmux";
  sheet.innerHTML = `<div class="sheet-body">
    <div class="sheet-title">${Fmt.logo(r.harness)}<span>Detach ${esc(r.title || r.id || "session")}?</span></div>
    <p class="note">It's working right now. Detaching now stops the turn; keeping it lets you attach to it from a terminal with tmux.</p>
    <div class="sheet-actions detach-choices">
      <button type="button" class="tab" data-mode="when_idle">Detach when it finishes</button>
      ${canKeep ? '<button type="button" class="tab" data-mode="keep">Keep it running in tmux</button>' : ""}
      <button type="button" class="tab detach-danger" data-mode="now">Detach now</button>
      <button type="button" class="tab" data-mode="">Cancel</button>
    </div></div>`;
  for (const b of sheet.querySelectorAll("[data-mode]")) {
    b.onclick = () => {
      const mode = b.dataset.mode;
      sheet.close();
      if (mode) detachRow(r, mode);
    };
  }
  if (!sheet.open) sheet.showModal();
}

function detachSheetEl() {
  let sheet = document.getElementById("detach-sheet");
  if (sheet) return sheet;
  sheet = document.createElement("dialog");
  sheet.id = "detach-sheet";
  sheet.className = "sheet";
  document.body.appendChild(sheet);
  return sheet;
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
    const open = () => {
      const r = byKey.get(row.dataset.key);
      if (!r) return;
      // Without the chat script (it failed to load), a row keeps its sheet.
      if (!sessionChat()) return openSheet(r);
      s.scrollY = window.scrollY;
      location.hash = `#/sessions?chat=${encodeURIComponent(r.key)}`;
    };
    row.onclick = open;
    row.onkeydown = (e) => {
      if (e.target !== row || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      open();
    };
  }
  for (const btn of document.querySelectorAll(".row-act")) {
    btn.onclick = (e) => {
      e.stopPropagation();
      const r = byKey.get(btn.dataset.key);
      if (!r) return;
      if (btn.dataset.act === "attach") attachRow(r); else detachClick(r);
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
  // The open chat's row is freshest on supervised fields (attach/detach
  // land there over SSE long before the list's data is refetched).
  if (chat && chat.row) {
    r = Object.assign({}, r, {
      ownership: chat.row.ownership, mode: chat.row.mode,
      tmux: chat.row.tmux, kept: chat.row.kept, status: chat.row.status,
    });
  }
  openSheet(r);
  return true;
}

/* ------------------------------------------------------- new session */

/* The iPhone app's NewSessionView: machine, harness, folder and a first
 * prompt, then POST /v1/sessions followed by /v1/launches/<id> polls
 * until the hub names the session key and the chat can open. */
const ns = {
  machines: null,            // /v1/machines answer, cached per sheet open
  machineId: null, harness: null, folder: "", prompt: "", query: "",
  browse: null,              // {path, parent, folders, error, loading}
  phase: "idle", message: "", seq: 0,  // idle|starting|waiting|started|failed|unconfirmed
};

function nsSheetEl() {
  let sheet = document.getElementById("new-session-sheet");
  if (sheet) return sheet;
  sheet = document.createElement("dialog");
  sheet.id = "new-session-sheet";
  sheet.className = "sheet";
  // Esc counts as Close: a poll in flight stops trusting its replies.
  sheet.oncancel = () => { ns.seq++; ns.phase = "idle"; ns.message = ""; };
  document.body.appendChild(sheet);
  return sheet;
}

function nsBusy() {
  return ns.phase === "starting" || ns.phase === "waiting" || ns.phase === "started";
}

/* The iPhone's NewSessionOptions: a machine offers `launchable`, falling
 * back to every harness it detected on older hub versions. Machines
 * that can launch nothing are left out, online ones first. */
function nsMachines() {
  return (ns.machines || [])
    .filter((m) => (m.launchable || m.harnesses || []).length)
    .sort((a, b) => ((b.online === true) - (a.online === true))
      || String(a.name || a.id).localeCompare(String(b.name || b.id)));
}

function nsMachine() {
  return nsMachines().find((m) => m.id === ns.machineId) || null;
}

function nsHarnesses() {
  const m = nsMachine();
  return m ? (m.launchable || m.harnesses || []) : [];
}

function nsHarnessName(m, h) {
  const names = (m && m.harness_names) || {};
  const raw = names[h] || h;
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/* Recent folders on the chosen machine, most recent first, the session
 * rows' cwd deduped; the box's words all have to match, any case. */
function nsFolders(m) {
  const mid = m ? m.id : "";
  const seen = new Set(), out = [];
  for (const r of (sessionsPage.data && sessionsPage.data.sessions) || []) {
    const cwd = r.cwd;
    if (!cwd || !cwd.startsWith("/")) continue;
    const onMachine = mid ? (r.machine_id || "") === mid || (!r.machine_id && !r.remote) : true;
    if (!onMachine || seen.has(cwd)) continue;
    seen.add(cwd);
    out.push(cwd);
  }
  const words = ns.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return words.length ? out.filter((p) => words.every((w) => p.toLowerCase().includes(w))) : out;
}

function nsReady() {
  return !nsBusy() && ns.machineId != null && ns.harness != null
    && ns.folder.trim().startsWith("/") && !!ns.prompt.trim();
}

function nsPaint() {
  const sheet = nsSheetEl();
  const machines = nsMachines();
  const machine = nsMachine();
  const busy = nsBusy();
  const browse = ns.browse;
  const untracked = machine && ns.harness
    && (machine.untracked_harnesses || []).includes(ns.harness);
  const machineChips = machines.length > 1 ? `<div class="ns-chips">${
    machines.map((m) => `<button type="button" class="tab ns-chip${m.id === ns.machineId ? " selected" : ""}"
      data-machine="${esc(m.id)}"${busy ? " disabled" : ""}>${esc(m.name || m.id)}${m.online === false ? " · offline" : ""}</button>`).join("")
  }</div>` : "";
  const harnessChips = nsHarnesses().map((h) => `<button type="button"
      class="tab ns-chip${h === ns.harness ? " selected" : ""}" data-harness="${esc(h)}"${busy ? " disabled" : ""}>
      ${Fmt.logo(h)}<span>${esc(nsHarnessName(machine, h))}</span></button>`).join("");
  const folders = nsFolders(machine).slice(0, 6);
  const browseUi = browse ? `<div class="ns-browse">
      <div class="ns-path">${esc(browse.path || "…")}</div>
      ${browse.loading ? '<p class="caption">Listing folders…</p>' : ""}
      ${browse.error ? `<p class="error">${esc(browse.error)}</p>` : ""}
      ${browse.parent ? `<button type="button" class="ns-folder" data-path="${esc(browse.parent)}">..</button>` : ""}
      ${(browse.folders || []).map((f) => `<button type="button" class="ns-folder" data-path="${esc(f.path)}">${esc(f.name)}/</button>`).join("")}
      ${!browse.loading && !browse.error ? `<div class="sheet-actions"><button type="button" class="tab selected" id="ns-browse-use">Use this folder</button></div>` : ""}
    </div>` : "";
  const note = ns.message ? `<p class="${ns.phase === "failed" ? "error" : "note"}">${esc(ns.message)}</p>` : "";
  const startLabel = { starting: "Starting…", waiting: "Waiting for the session…", started: "Opening the chat…" }[ns.phase] || "Start";
  sheet.innerHTML = `<div class="sheet-body">
    <div class="sheet-title">New session</div>
    ${machineChips}
    ${machines.length && !machine ? '<p class="note">Pick a machine.</p>' : ""}
    ${!machines.length ? `<p class="note">No machine can start a session. Update swe on the hub: a machine
      offers the harnesses swe lists there, minus the ones switched off in Provider visibility.</p>` : ""}
    <div class="caption">Harness</div>
    <div class="ns-chips">${harnessChips}</div>
    ${untracked ? `<p class="note">Quiver can't read ${esc(nsHarnessName(machine, ns.harness))} sessions yet: it starts, but no chat opens.</p>` : ""}
    <div class="caption">Folder</div>
    <input class="field ns-path-input" id="ns-folder" type="text" autocomplete="off" spellcheck="false"
      placeholder="/absolute/path" value="${esc(ns.folder)}"${busy ? " disabled" : ""}>
    <input class="field search" id="ns-query" type="search" placeholder="Recent folders on this machine — type to filter"
      value="${esc(ns.query)}"${busy ? " disabled" : ""}>
    <div class="ns-folders" id="ns-folders">
      ${folders.map((p) => `<button type="button" class="ns-folder" data-path="${esc(p)}"${busy ? " disabled" : ""}>${esc(p)}</button>`).join("")
        || '<p class="caption" style="padding:8px 10px">No recent folders match.</p>'}
    </div>
    <button type="button" class="link" id="ns-browse"${busy ? " disabled" : ""}>Browse this machine's folders…</button>
    ${browseUi}
    <div class="caption">First prompt</div>
    <textarea class="field ns-prompt" id="ns-prompt" rows="3" placeholder="Describe the first task"${busy ? " disabled" : ""}>${esc(ns.prompt)}</textarea>
    ${note}
    <div class="sheet-actions">
      <button type="button" class="tab" id="ns-cancel"${ns.phase === "started" ? " disabled" : ""}>${nsBusy() ? "Close" : "Cancel"}</button>
      <button type="button" class="tab selected" id="ns-start"${nsReady() ? "" : " disabled"}>${esc(startLabel)}</button>
    </div></div>`;
  for (const b of sheet.querySelectorAll("[data-machine]")) {
    b.onclick = () => {
      if (b.dataset.machine === ns.machineId) return;
      ns.machineId = b.dataset.machine;
      ns.harness = null;
      ns.browse = null;
      nsPickHarness();
      nsPaint();
    };
  }
  for (const b of sheet.querySelectorAll("[data-harness]")) {
    b.onclick = () => { ns.harness = b.dataset.harness; nsPaint(); };
  }
  for (const b of sheet.querySelectorAll(".ns-folder")) {
    b.onclick = () => {
      if (ns.browse) nsBrowse(b.dataset.path);   // inside the browser: descend
      else { ns.folder = b.dataset.path; nsPaint(); }
    };
  }
  const folderIn = sheet.querySelector("#ns-folder");
  if (folderIn) folderIn.oninput = () => { ns.folder = folderIn.value; nsPaintStart(); };
  const queryIn = sheet.querySelector("#ns-query");
  if (queryIn) queryIn.oninput = () => { ns.query = queryIn.value; nsPaintLists(); };
  const promptIn = sheet.querySelector("#ns-prompt");
  if (promptIn) promptIn.oninput = () => { ns.prompt = promptIn.value; nsPaintStart(); };
  const browseBtn = sheet.querySelector("#ns-browse");
  if (browseBtn) browseBtn.onclick = () => nsBrowse(ns.folder.trim());
  const use = sheet.querySelector("#ns-browse-use");
  if (use) use.onclick = () => { ns.folder = ns.browse.path; ns.browse = null; nsPaint(); };
  sheet.querySelector("#ns-cancel").onclick = () => {
    ns.seq++;              // in-flight polls drop their replies
    ns.phase = "idle"; ns.message = "";
    sheet.close();
  };
  sheet.querySelector("#ns-start").onclick = nsStart;
}

/* Light repaints for keystrokes: only the folder list or the Start
 * button's enabled state, so the focused field keeps its caret. */
function nsPaintStart() {
  const start = document.getElementById("ns-start");
  if (start) start.disabled = !nsReady();
}
function nsPaintLists() {
  const box = document.getElementById("ns-folders");
  if (!box) return nsPaint();
  const busy = nsBusy();
  const folders = nsFolders(nsMachine()).slice(0, 6);
  box.innerHTML = folders.map((p) => `<button type="button" class="ns-folder"
      data-path="${esc(p)}"${busy ? " disabled" : ""}>${esc(p)}</button>`).join("")
    || '<p class="caption" style="padding:8px 10px">No recent folders match.</p>';
  for (const b of box.querySelectorAll(".ns-folder")) {
    b.onclick = () => { ns.folder = b.dataset.path; nsPaint(); };
  }
}

/* The iPhone's defaultHarness: the harness last used in that folder on
 * that machine when it is on offer, else the first offer. */
function nsPickHarness() {
  const offered = nsHarnesses();
  const mid = ns.machineId;
  const last = ((sessionsPage.data && sessionsPage.data.sessions) || [])
    .find((r) => (r.machine_id || "") === mid && r.cwd === ns.folder);
  ns.harness = last && offered.includes(last.harness) ? last.harness : (offered[0] || null);
}

async function nsBrowse(path) {
  ns.browse = ns.browse || {};
  ns.browse.loading = true;
  ns.browse.error = "";
  nsPaint();
  const m = ns.machineId || "";
  // An empty path is the hub machine's home; only absolute ones go over.
  const qp = path && path.startsWith("/") ? `path=${encodeURIComponent(path)}&` : "";
  const { status, data } = await api(`/v1/folders?${qp}machine=${encodeURIComponent(m)}`);
  if (!ns.browse) return;
  ns.browse.loading = false;
  if (status === 200 && data) {
    ns.browse.path = data.path;
    ns.browse.parent = data.parent || null;
    ns.browse.folders = data.folders || [];
    if (data.truncated) ns.browse.error = "This folder has too many entries to list fully.";
    else ns.browse.error = "";
  } else {
    ns.browse.error = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub.");
    if (status === 401) {
      ns.seq++;
      nsSheetEl().close();
      return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    }
  }
  nsPaint();
}

async function openNewSession() {
  ns.machines = null;
  ns.machineId = null;
  ns.harness = null;
  ns.folder = ns.folder || "";   // the last folder is a good next guess
  ns.prompt = "";
  ns.query = "";
  ns.browse = null;
  ns.phase = "idle";
  ns.message = "";
  ns.seq++;
  const sheet = nsSheetEl();
  nsPaint();
  if (!sheet.open) sheet.showModal();
  const { status, data } = await api("/v1/machines");
  if (status === 401) {
    sheet.close();
    return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  }
  if (status !== 200 || !data) {
    ns.message = "Couldn't list the machines — the harness and folder pickers need it.";
    nsPaint();
    return;
  }
  ns.machines = data.machines || [];
  const first = nsMachines()[0];
  if (first && !ns.machineId) ns.machineId = first.id;
  nsPickHarness();
  nsPaint();
}

async function nsStart() {
  if (!nsReady()) return;
  const mine = ++ns.seq;
  ns.phase = "starting";
  ns.message = "";
  nsPaint();
  const body = { harness: ns.harness, cwd: ns.folder.trim(), prompt: ns.prompt.trim() };
  if (ns.machineId) body.machine = ns.machineId;
  const { status, data } = await apiPost("/v1/sessions", body);
  if (mine !== ns.seq) return;
  if (status === 401) {
    nsSheetEl().close();
    return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  }
  if (!((status === 200 || status === 202) && data && data.launch_id)) {
    ns.phase = "failed";
    ns.message = controlErrorText(status, data, "Couldn't reach the hub. Check the connection, then try again.");
    return nsPaint();
  }
  ns.phase = "waiting";
  nsPaint();
  const queued = data.queued === true;   // a satellite carries the launch
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let launch = data;
  for (let i = 0; i < 90; i++) {
    if (launch.state === "started" && launch.key) {
      ns.phase = "started";
      nsPaint();
      const key = launch.key;
      const sheet = nsSheetEl();
      ns.phase = "idle";
      sheet.close();
      // The list refetches behind the chat that is about to open.
      renderSessions("sessions");
      location.hash = `#/sessions?chat=${encodeURIComponent(key)}`;
      return;
    }
    if (launch.state === "untracked") {
      ns.phase = "unconfirmed";
      ns.message = "Started. Quiver can't read this harness's sessions yet, so there's no chat to open. It keeps running under tmux on the machine.";
      return nsPaint();
    }
    if (launch.state === "failed") {
      ns.phase = "failed";
      ns.message = launch.note ? `The session didn't start: ${launch.note}` : "The session didn't start.";
      return nsPaint();
    }
    await sleep(1000);
    if (mine !== ns.seq) return;
    const { status: s, data: d } = await api(`/v1/launches/${encodeURIComponent(data.launch_id)}`);
    if (mine !== ns.seq) return;
    if (s === 200 && d) launch = d;
    else if (s === 404) {
      ns.phase = "failed";
      ns.message = (d && d.error && d.error.message) || "The hub lost track of this launch.";
      return nsPaint();
    }
    // A dropped poll is retried until the attempts run out.
  }
  ns.phase = "unconfirmed";
  ns.message = queued
    ? "Queued. It starts when that machine is online, then shows up in Sessions."
    : "It started but hasn't shown up yet. It'll appear in Sessions when it does.";
  nsPaint();
}
