/* Quiver remote PWA: sessions list + chat over the hub's REST + SSE.
 * No build step, no deps. SSE uses fetch streams, not EventSource,
 * because EventSource cannot send the Authorization header.
 */
"use strict";

const store = {
  get cred() { try { return JSON.parse(localStorage.getItem("quiver.remote") || "null"); } catch { return null; } },
  set cred(v) { localStorage.setItem("quiver.remote", JSON.stringify(v)); },
  get lastSeq() { try { return JSON.parse(localStorage.getItem("quiver.seq") || "{}"); } catch { return {}; } },
  set lastSeq(v) { localStorage.setItem("quiver.seq", JSON.stringify(v)); },
};

const el = (id) => document.getElementById(id);
const state = {
  sessions: [],
  machines: [],
  view: "sessions",
  currentKey: null,
  stream: null,
  inboxTimer: null,
  sessionTimer: null,
  inboxCount: 0,
  openGen: 0,
};

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

/* Minimal markdown-ish rendering: escape everything, then restore
 * fenced code blocks, inline code and bold. */
function md(text) {
  let t = esc(text || "");
  t = t.replace(/```([\s\S]*?)```/g, (_m, code) => `<pre class="code"><code>${code.replace(/^\n+|\n+$/g, "")}</code></pre>`);
  t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  return t;
}

async function api(path, opts = {}) {
  const cred = store.cred;
  const headers = Object.assign({}, opts.headers || {});
  if (cred && cred.credential) headers["Authorization"] = `Bearer ${cred.credential}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, { ...opts, headers });
  let data = null;
  try { data = await resp.json(); } catch { /* SSE or empty */ }
  return { status: resp.status, data };
}

function setConn(on) {
  el("conn").className = "dot " + (on === true ? "on" : on === "err" ? "err" : "off");
}

function show(view) {
  state.view = view;
  for (const v of ["pair-view", "sessions-view", "chat-view", "inbox-view"]) {
    el(v).classList.add("hidden");
  }
  el(view + "-view").classList.remove("hidden");
  el("back").classList.toggle("hidden", view !== "chat");
  el("tabs").classList.toggle("hidden", view === "chat" || view === "pair");
  el("title").textContent = view === "chat" ? chatTitle() : "Quiver";
}

function chatTitle() {
  const s = state.sessions.find((r) => r.key === state.currentKey);
  return s ? (s.title || s.agent || s.key) : "Session";
}

/* ── pairing ─────────────────────────────────────────────── */

async function doPair(token, name) {
  const { status, data } = await api("/v1/pair", {
    method: "POST",
    body: JSON.stringify({ token, name: name || "phone" }),
  });
  if (status === 200 && data && data.credential) {
    store.cred = data;
    location.hash = "";
    boot();
    return;
  }
  el("pair-error").textContent =
    (data && data.error && data.error.message) || `pairing failed (${status})`;
}

/* ── sessions list ───────────────────────────────────────── */

const STATUS_LABEL = {
  active: "active", done: "done", followup: "needs you",
  error: "error", interrupted: "interrupted",
  approval: "approval needed", "": "idle",
};

function renderSessions() {
  const root = el("session-groups");
  const byMachine = new Map();
  for (const row of state.sessions) {
    const m = row.machine_id || "local";
    if (!byMachine.has(m)) byMachine.set(m, []);
    byMachine.get(m).push(row);
  }
  const machineName = (id) => {
    const m = state.machines.find((x) => x.id === id);
    return m ? (m.name || id) : id;
  };
  let html = "";
  for (const [mid, rows] of byMachine) {
    const byCwd = new Map();
    for (const r of rows) {
      const dir = r.cwd || "";
      if (!byCwd.has(dir)) byCwd.set(dir, []);
      byCwd.get(dir).push(r);
    }
    for (const [cwd, list] of byCwd) {
      html += `<div class="group"><h2><span class="mach">${esc(machineName(mid))}</span> · ${esc(shortDir(cwd))}</h2>`;
      for (const r of list) {
        const st = r.status || "";
        const when = r.last_active_at ? relTime(r.last_active_at) : "";
        html += `<div class="srow" data-key="${esc(r.key)}">
          <div class="dot st ${esc(st)}"></div>
          <div class="body">
            <div class="t">${esc(r.title || "(untitled)")}</div>
            <div class="sub">${esc(r.agent || r.harness)} · ${esc(STATUS_LABEL[st] || st || "idle")} · ${esc(when)}</div>
          </div>
          <div class="own">${esc(r.ownership || "observed")}</div>
        </div>`;
      }
      html += "</div>";
    }
  }
  root.innerHTML = html || `<p class="dim">No sessions found on this machine yet.</p>`;
  root.querySelectorAll(".srow").forEach((row) => {
    row.addEventListener("click", () => openChat(row.dataset.key));
  });
}

function shortDir(p) {
  if (!p) return "(no cwd)";
  const parts = p.replace(/\/+$/, "").split("/");
  return parts.slice(-2).join("/") || p;
}

function relTime(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "now";
  if (d < 3600) return Math.floor(d / 60) + "m";
  if (d < 86400) return Math.floor(d / 3600) + "h";
  return Math.floor(d / 86400) + "d";
}

async function loadSessions() {
  const { status, data } = await api("/v1/sessions");
  if (status === 401) { store.cred = null; boot(); return; }
  if (status !== 200 || !data) { setConn("err"); return; }
  state.sessions = data.sessions || [];
  state.machines = data.machines || [];
  setConn(true);
  if (state.view === "sessions") renderSessions();
}

/* ── chat ────────────────────────────────────────────────── */

const toolCards = new Map(); // call_id → card element

async function openChat(key) {
  state.currentKey = key;
  const gen = ++state.openGen; // a second openChat supersedes this one
  toolCards.clear();
  el("chat-log").innerHTML = "";
  show("chat");
  const row = state.sessions.find((r) => r.key === key);
  el("chat-meta").textContent = row
    ? `${row.agent || row.harness} · ${row.cwd || ""}`
    : key;
  updateOwnership(row);
  // Always backfill — the DOM was just cleared, and the stream must
  // start after the seq backfill actually reached, not a stale save.
  await backfill(key, gen);
  if (gen !== state.openGen || state.currentKey !== key) return;
  openStream(key, store.lastSeq[key] || 0);
}

function updateOwnership(row) {
  const observed = !row || row.ownership !== "attached";
  el("composer").classList.toggle("hidden", observed);
  el("chat-banner").classList.toggle("hidden", !observed);
  // attachable means "has resume flags"; the server also refuses when
  // the session is mid-turn, so hide the button then rather than have
  // every tap bounce off the 409.
  const busy = row && row.status === "active";
  const canAttach = observed && row && row.attachable && !busy;
  el("attach-btn").classList.toggle("hidden", !canAttach);
  el("chat-banner-text").textContent = !observed
    ? ""
    : canAttach
      ? "Observed session: read-only until attached."
      : busy
        ? "Observed session: mid-turn — attach once it is idle."
        : "Observed session: read-only (this harness cannot be attached).";
}

async function backfill(key, gen) {
  const { status, data } = await api(
    `/v1/sessions/${encodeURIComponent(key)}/messages?limit=500`
  );
  if (status !== 200 || !data) return;
  if (gen !== undefined && gen !== state.openGen) return; // user moved on
  for (const ev of data.messages || []) appendEvent(ev, false);
  if (data.messages && data.messages.length) {
    rememberSeq(key, data.messages[data.messages.length - 1].seq);
  }
  scrollBottom();
}

function appendEvent(ev, live = true) {
  if (ev.type === "message.appended") {
    appendMessage(ev.data || {}, ev.seq);
  } else if (ev.type === "session.status") {
    const row = state.sessions.find((r) => r.key === ev.session);
    if (row) {
      row.status = (ev.data || {}).status || "";
      // An open chat polls nothing — a turn ending here should un-hide
      // the Attach button / reword the banner on its own.
      if (row.key === state.currentKey) updateOwnership(row);
    }
  } else if (ev.type === "session.ownership") {
    const row = state.sessions.find((r) => r.key === ev.session);
    if (row) {
      row.ownership = (ev.data || {}).ownership || row.ownership;
      updateOwnership(row);
    }
  } else if (ev.type === "approval.requested") {
    appendMessage({
      kind: "approval",
      text: (ev.data || {}).prompt || "approval requested",
      request_id: (ev.data || {}).request_id || "",
    }, ev.seq);
  } else if (ev.type === "command.result") {
    // Satellite sends answer async — surface failures and degrades,
    // and settle any queued approval card this result answers.
    const d = ev.data || {};
    if (d.op === "approval" && d.request_id) {
      el("chat-log").querySelectorAll(".msg.approval").forEach((card) => {
        if (card.dataset.req !== d.request_id || !card.dataset.queued) return;
        delete card.dataset.queued;
        const note = card.querySelector(".approval-note");
        if (d.ok) {
          card.classList.add("resolved");
          if (note) note.remove();
        } else {
          card.querySelectorAll(".approval-actions button").forEach((b) => { b.disabled = false; });
          if (note) {
            note.classList.remove("queued");
            note.textContent = `approval failed: ${d.note || "satellite rejected it"}`;
          }
        }
      });
    }
    if (d.ok === false || d.degraded || d.note === "approval_not_supported") {
      appendMessage({
        kind: "system",
        text: d.ok === false
          ? `${d.op || "command"} failed: ${d.note || "unknown error"}`
          : (d.note || "queued"),
      }, ev.seq);
    }
  } else if (ev.type === "approval.resolved") {
    const rid = (ev.data || {}).request_id || "";
    el("chat-log").querySelectorAll(".msg.approval").forEach((card) => {
      if (!rid || card.dataset.req === rid) {
        card.querySelectorAll(".approval-actions button").forEach((b) => { b.disabled = true; });
        card.classList.add("resolved");
      }
    });
  }
  if (live) scrollBottom();
}

/* ── diff pane ──────────────────────────────────────────────
   Edit/Write tool calls carry parseable file args in args_full —
   render them as a unified diff instead of raw JSON. */

const DIFF_TOOLS = new Set([
  "edit", "multiedit", "write", "notebookedit",
  "apply_patch", "patch", "file_edit",
]);

function diffLines(oldText, newText) {
  /* Common-affix diff: identical leading/trailing lines collapse to
     context; the differing middle renders removed then added. */
  const a = (oldText || "").split("\n");
  const b = (newText || "").split("\n");
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre &&
         a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  return {
    ctxTop: a.slice(0, pre),
    del: a.slice(pre, a.length - suf),
    add: b.slice(pre, b.length - suf),
    ctxBot: a.slice(a.length - suf),
  };
}

function renderDiff(filePath, oldText, newText) {
  const d = diffLines(oldText, newText);
  const frag = document.createElement("div");
  frag.className = "diff";
  if (filePath) {
    const f = document.createElement("div");
    f.className = "diff-file";
    f.textContent = filePath;
    frag.appendChild(f);
  }
  const emit = (lines, cls, sign) => {
    for (const ln of lines) {
      const row = document.createElement("div");
      row.className = `diff-line ${cls}`;
      row.textContent = `${sign} ${ln}`;
      frag.appendChild(row);
    }
  };
  const top = d.ctxTop.slice(-3);
  if (d.ctxTop.length > 3) emit([`+ ${d.ctxTop.length - 3} unchanged`], "diff-meta", "");
  emit(top, "diff-ctx", " ");
  emit(d.del, "diff-del", "-");
  emit(d.add, "diff-add", "+");
  if (d.ctxBot.length) {
    emit(d.ctxBot.slice(0, 2), "diff-ctx", " ");
    if (d.ctxBot.length > 2) emit([`+ ${d.ctxBot.length - 2} more`], "diff-meta", "");
  }
  return frag;
}

function renderPatch(filePath, patch) {
  /* Raw patch text (codex apply_patch): color by line sigil. */
  const frag = document.createElement("div");
  frag.className = "diff";
  if (filePath) {
    const f = document.createElement("div");
    f.className = "diff-file";
    f.textContent = filePath;
    frag.appendChild(f);
  }
  for (const ln of String(patch || "").split("\n").slice(0, 400)) {
    const row = document.createElement("div");
    row.className =
      "diff-line " +
      (ln.startsWith("+") ? "diff-add"
        : ln.startsWith("-") ? "diff-del"
        : ln.startsWith("@@") ? "diff-meta"
        : "diff-ctx");
    row.textContent = ln;
    frag.appendChild(row);
  }
  return frag;
}

function toolDiff(tool) {
  /* A diff DOM node for edit-ish tools, or null for anything else. */
  const name = String(tool.name || "").toLowerCase();
  if (!DIFF_TOOLS.has(name) || !tool.args_full) return null;
  let args;
  try { args = JSON.parse(tool.args_full); } catch { return null; }
  if (!args || typeof args !== "object") return null;
  const file = args.file_path || args.path || args.filename || "";
  if (name === "write" || name === "notebookedit" || name === "file_edit") {
    const content = args.content || args.new_source || "";
    if (!content) return null;
    return renderDiff(file, "", content);
  }
  if (name === "apply_patch" || name === "patch") {
    const patch = args.patch || args.diff || tool.args_full;
    return renderPatch(file, patch);
  }
  if (name === "multiedit" && Array.isArray(args.edits)) {
    const frag = document.createElement("div");
    for (const e of args.edits.slice(0, 8)) {
      if (e && (e.old_string != null || e.new_string != null)) {
        frag.appendChild(renderDiff(file, e.old_string, e.new_string));
      }
    }
    return frag.childNodes.length ? frag : null;
  }
  // edit + friends: {old_string, new_string}
  if (args.old_string != null || args.new_string != null) {
    return renderDiff(file, args.old_string || "", args.new_string || "");
  }
  return null;
}

function appendMessage(msg, seq) {
  const log = el("chat-log");
  const kind = msg.kind || "assistant";
  if (kind === "tool_result" && msg.result_ref && toolCards.has(msg.result_ref)) {
    const card = toolCards.get(msg.result_ref);
    const body = card.querySelector(".tool-body");
    if (body && msg.text) {
      const pre = document.createElement("pre");
      pre.textContent = msg.text.slice(0, 8000);
      body.appendChild(pre);
    }
    return;
  }
  if (kind === "tool_use" && msg.tool) {
    const d = document.createElement("details");
    d.className = "tool";
    d.innerHTML = `<summary>${esc(msg.tool.name)} · ${esc(msg.tool.args_summary || "")}</summary><div class="tool-body"></div>`;
    const body = d.querySelector(".tool-body");
    const diff = toolDiff(msg.tool);
    if (diff) {
      d.classList.add("has-diff");
      body.appendChild(diff);
    } else if (msg.tool.args_full) {
      const pre = document.createElement("pre");
      pre.textContent = msg.tool.args_full.slice(0, 8000);
      body.appendChild(pre);
    }
    if (msg.tool.call_id) toolCards.set(msg.tool.call_id, d);
    log.appendChild(d);
    return;
  }
  const div = document.createElement("div");
  div.className = `msg ${kind}`;
  const role = { user: "you", assistant: "agent", thinking: "thought", error: "error" }[kind];
  if (kind === "compaction") {
    div.innerHTML = `<div class="text">— context compacted —</div>`;
  } else if (kind === "system") {
    div.innerHTML = `<div class="text">${esc(msg.text || "")}</div>`;
  } else if (kind === "thinking") {
    div.innerHTML = `<details><summary class="dim">${role}</summary><div class="text">${md(msg.text)}</div></details>`;
  } else if (kind === "approval") {
    div.dataset.req = msg.request_id || "";
    div.innerHTML = `<div class="role">approval</div><div class="card">${md(msg.text)}</div>` +
      (msg.request_id
        ? `<div class="approval-actions">
             <button data-dec="allow">Allow</button>
             <button data-dec="always">Always</button>
             <button data-dec="deny" class="deny">Deny</button>
           </div>`
        : "");
    div.querySelectorAll(".approval-actions button").forEach((btn) => {
      btn.addEventListener("click", async () => {
        div.querySelectorAll(".approval-actions button").forEach((b) => { b.disabled = true; });
        const note = div.querySelector(".approval-note") || document.createElement("div");
        note.className = "approval-note";
        let status = 0;
        try {
          ({ status } = await api(
            `/v1/sessions/${encodeURIComponent(state.currentKey)}/approval`,
            { method: "POST", body: JSON.stringify({ decision: btn.dataset.dec, request_id: msg.request_id }) },
          ));
        } catch {
          status = 0; // network failure — the hub never saw it
        }
        // An approval.resolved that raced us wins — don't reopen.
        if (div.classList.contains("resolved")) return;
        if (status === 202) {
          // The hub queued it for a satellite; the card stays pending
          // until a command.result / approval.resolved event closes it.
          div.dataset.queued = "1";
          note.classList.add("queued");
          note.textContent = "queued — waiting for the satellite";
          div.appendChild(note);
        } else if (status >= 400 || status === 0) {
          div.querySelectorAll(".approval-actions button").forEach((b) => { b.disabled = false; });
          note.textContent = status === 0
            ? "request failed — connection lost"
            : "approval failed — the prompt may have closed";
          div.appendChild(note);
        } else {
          div.classList.add("resolved");
        }
      });
    });
  } else {
    div.innerHTML = `<div class="role">${role || kind}</div><div class="text">${md(msg.text)}</div>`;
  }
  log.appendChild(div);
}

function scrollBottom() {
  const main = document.querySelector("main");
  main.scrollTop = main.scrollHeight;
}

function rememberSeq(key, seq) {
  if (!seq) return;
  const seqs = store.lastSeq;
  seqs[key] = Math.max(seqs[key] || 0, seq);
  store.lastSeq = seqs;
}

function openStream(key, lastSeq) {
  closeStream();
  const url = `/v1/sessions/${encodeURIComponent(key)}/events?last=${lastSeq}`;
  const cred = store.cred || {};
  const mine = {};
  fetch(url, { headers: { Authorization: `Bearer ${cred.credential || ""}` } })
    .then(async (resp) => {
      if (resp.status === 401) { store.cred = null; boot(); return; }
      if (!resp.ok || !resp.body) throw new Error(`sse ${resp.status}`);
      setConn(true);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      mine.reader = reader;
      state.stream = mine;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          handleFrame(key, frame);
        }
      }
      throw new Error("stream closed");
    })
    .catch(() => {
      // Only clear if this stream is still the live one — a newer
      // openChat may have already replaced it.
      if (state.stream === mine) state.stream = null;
      setConn("err");
      // Reconnect with replay from the last seq we saw.
      if (state.currentKey === key) {
        setTimeout(() => {
          if (state.currentKey === key && !state.stream) {
            openStream(key, store.lastSeq[key] || 0);
          }
        }, 2000);
      }
    });
}

function handleFrame(key, frame) {
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("data:")) data += line.slice(5).replace(/^ /, "") + "\n";
  }
  if (!data.trim()) return;
  let ev;
  try { ev = JSON.parse(data); } catch { return; }
  if (ev.seq) rememberSeq(key, ev.seq);
  if (state.currentKey === key) appendEvent(ev);
}

function closeStream() {
  if (state.stream) {
    try { state.stream.reader.cancel(); } catch {}
    state.stream = null;
  }
}

/* ── inbox ───────────────────────────────────────────────── */

async function loadInbox() {
  const { status, data } = await api("/v1/inbox");
  if (status !== 200 || !data) return;
  const items = data.items || [];
  state.inboxCount = items.length;
  const badge = el("inbox-badge");
  badge.classList.toggle("hidden", !items.length);
  badge.textContent = items.length || "";
  if (state.view !== "inbox") return;
  el("inbox-list").innerHTML = items.length
    ? items.map((it) => `
      <div class="srow" data-key="${esc(it.key)}">
        <div class="dot st ${esc(it.status)}"></div>
        <div class="body">
          <div class="t">${esc(it.title || "(untitled)")}</div>
          <div class="sub">${esc(it.agent || it.harness)} · ${esc(STATUS_LABEL[it.status] || it.status)} · ${esc(relTime(it.last_active_at || 0))}</div>
        </div>
      </div>`).join("")
    : `<p class="dim">Nothing needs you.</p>`;
  el("inbox-list").querySelectorAll(".srow").forEach((row) => {
    row.addEventListener("click", () => openChat(row.dataset.key));
  });
}

/* ── composer + controls ─────────────────────────────────── */

el("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = el("composer-text");
  const text = input.value.trim();
  if (!text || !state.currentKey) return;
  input.value = "";
  const mode = el("composer-mode").value;
  const { status, data } = await api(
    `/v1/sessions/${encodeURIComponent(state.currentKey)}/message`,
    { method: "POST", body: JSON.stringify({ text, mode }) }
  );
  if (status === 200 && data && data.degraded) {
    // steer fell back to queue on this channel — say so, don't pretend.
    appendMessage({
      kind: "system",
      text: data.note || "sent (queued: mid-turn input isn't supported here)",
    }, 0);
    scrollBottom();
  } else if (status !== 200 && data && data.error) {
    appendMessage({ kind: "error", text: data.error.message || "send failed" }, 0);
    scrollBottom();
  }
});

el("interrupt-btn").addEventListener("click", async () => {
  if (!state.currentKey) return;
  await api(
    `/v1/sessions/${encodeURIComponent(state.currentKey)}/control`,
    { method: "POST", body: JSON.stringify({ op: "interrupt" }) }
  );
});

el("attach-btn").addEventListener("click", async () => {
  if (!state.currentKey) return;
  const { status, data } = await api(
    `/v1/sessions/${encodeURIComponent(state.currentKey)}/control`,
    { method: "POST", body: JSON.stringify({ op: "attach" }) }
  );
  if (status !== 200) {
    appendMessage({
      kind: "error",
      text: (data && data.error && data.error.message) || "attach failed",
    }, 0);
    scrollBottom();
  }
});

/* ── wiring ──────────────────────────────────────────────── */

el("back").addEventListener("click", () => {
  closeStream();
  state.currentKey = null;
  show("sessions");
  loadSessions();
});

el("pair-go").addEventListener("click", () => {
  const token = el("pair-token").value.trim();
  if (token) doPair(token, el("pair-name").value.trim());
});
el("pair-token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") el("pair-go").click();
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    closeStream();
    state.currentKey = null;
    show(tab.dataset.tab);
    if (tab.dataset.tab === "inbox") loadInbox();
    if (tab.dataset.tab === "sessions") loadSessions();
  });
});

function boot() {
  const hash = location.hash || "";
  const m = hash.match(/#pair=([^&]+)/);
  if (m) {
    show("pair");
    const nm = hash.match(/[?&]name=([^&]+)/);
    const fromUrl = nm ? decodeURIComponent(nm[1]) : "";
    doPair(
      decodeURIComponent(m[1]),
      el("pair-name").value.trim() || fromUrl || "phone"
    );
    return;
  }
  if (!store.cred || !store.cred.credential) {
    show("pair");
    return;
  }
  show("sessions");
  loadSessions();
  loadInbox();
  clearInterval(state.sessionTimer);
  state.sessionTimer = setInterval(() => {
    if (state.view === "sessions") loadSessions();
    loadInbox();
  }, 15000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && store.cred) {
    if (state.view === "chat" && state.currentKey && !state.stream) {
      openStream(state.currentKey, store.lastSeq[state.currentKey] || 0);
    }
    loadSessions();
    loadInbox();
  }
});

boot();
