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
  pairTimer: null,
  pairGen: 0,
  approveTimer: null,
  usageGen: 0,
  usageData: null,
  usageMachine: null,
  inboxCount: 0,
  openGen: 0,
  // Live events not yet shown. The hub polls a busy transcript about
  // once a second, so several messages arrive together; they are shown
  // one at a time instead of as a block.
  pending: [],
  revealTimer: null,
  lastRevealAt: 0,
  // A send went through and nothing from the agent has come back yet.
  awaitingReply: false,
  // The Queued tray: sends the hub holds until the agent goes idle,
  // oldest first, as {cid, text, phase, note}. phase is posting (the
  // POST is out), waiting, sendingNow, removing (hidden while the hub
  // confirms) or left (the hub says it already went).
  queued: [],
  // Sends that left the hub's queue while the chat was closed; hidden,
  // kept so a replayed echo or result still lands in the transcript.
  departed: [],
  // Client ids that left the tray: a queue list fetched before that
  // can't bring them back.
  settledSends: new Set(),
  // Live echoes of user messages that matched no row: a queue list
  // fetched before them can't bring those sends back.
  unmatchedEchoes: [],
  // The queued send whose text is in the prompt box to be edited.
  editingId: null,
  draftBeforeEdit: "",
  savingEdit: false,
  editNote: "",
  trayNote: "",
  confirmRemoveAll: null,
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
  for (const v of ["pair-view", "approve-view", "sessions-view", "chat-view", "inbox-view", "usage-view"]) {
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

async function startTailscalePair(name) {
  el("pair-error").textContent = "";
  let status = 0, data = null;
  try {
    ({ status, data } = await api("/v1/pair/request", {
      method: "POST", body: JSON.stringify({ name: name || "" }),
    }));
  } catch { status = 0; }
  if (status === 201 && data) {
    showPairCode(data);
    pollPairRequest(data.request_id, data.poll_secret);
    return;
  }
  el("pair-error").textContent = (status === 403)
    ? "This device isn't identifiable on the tailnet — use a pair token below."
    : (data && data.error && data.error.message) || `pair request failed (${status})`;
}

function showPairCode(req) {
  el("pair-start").classList.add("hidden");
  el("pair-code-card").classList.remove("hidden");
  const code = String(req.code || "");
  el("pair-code").textContent = `${code.slice(0, 3)} ${code.slice(3)}`;
  el("pair-code-flat").textContent = code;
  el("pair-node").textContent =
    `as ${(req.node && req.node.name) || "this device"}` +
    (req.node && req.node.login ? ` (${req.node.login})` : "");
  el("pair-code-status").textContent = "waiting for approval…";
  el("pair-retry").classList.add("hidden");
}

function pollPairRequest(requestId, secret) {
  // One in-flight request at a time; a new request or Try again bumps
  // the generation and stale responses drop.
  const gen = ++state.pairGen;
  const tick = async () => {
    if (gen !== state.pairGen) return;
    let status = 0, data = null;
    try {
      ({ status, data } = await api(
        `/v1/pair/request/${encodeURIComponent(requestId)}?secret=${encodeURIComponent(secret)}`
      ));
    } catch { /* transient — poll again */ }
    if (gen !== state.pairGen) return;
    if (data && data.status === "approved") {
      state.pairTimer = null;
      store.cred = {
        device_id: data.device_id, credential: data.credential,
        name: data.name,
      };
      location.hash = "";
      boot();
      return;
    }
    if (data && data.status === "denied") {
      pairFailed("Pairing was denied.");
      return;
    }
    if (status === 404) {
      pairFailed("Request expired.");
      return;
    }
    state.pairTimer = setTimeout(tick, 2000);
  };
  state.pairTimer = setTimeout(tick, 2000);
}

function pairFailed(msg) {
  ++state.pairGen; // stop the poll chain
  clearTimeout(state.pairTimer);
  state.pairTimer = null;
  el("pair-code-status").textContent = msg;
  el("pair-retry").classList.remove("hidden");
}

/* ── #approve: decide pending requests ───────────────────── */

async function loadApprovals() {
  const { status, data } = await api(
    "/v1/pair/requests", { headers: { "X-Quiver-Pair": "1" } }
  );
  if (status !== 200 || !data) {
    el("approve-error").textContent =
      (data && data.error && data.error.message) ||
      `can't list requests (${status})`;
    return;
  }
  el("approve-error").textContent = "";
  const list = el("approve-list");
  const rows = data.requests || [];
  // Reconcile by request_id — a 3s refresh must not wipe a code the
  // user is halfway through typing.
  const wanted = new Set(rows.map((r) => r.request_id));
  list.querySelectorAll(".approve-row").forEach((row) => {
    if (!wanted.has(row.dataset.rid)) row.remove();
  });
  const have = new Set(
    [...list.querySelectorAll(".approve-row")].map((r) => r.dataset.rid)
  );
  const empty = list.querySelector(".dim");
  if (rows.length) { if (empty) empty.remove(); }
  else if (!empty) {
    list.insertAdjacentHTML("beforeend", `<p class="dim">None pending.</p>`);
  }
  for (const r of rows) {
    if (have.has(r.request_id)) continue;
    const row = document.createElement("div");
    row.className = "approve-row";
    row.dataset.rid = r.request_id;
    row.innerHTML =
      `<div class="body"><div class="t">${esc(r.name)}</div>` +
      `<div class="sub">${esc(r.node_name || "?")} · ${esc(r.login || "")} · ${esc(relTime(r.created_at || 0))} ago</div></div>` +
      `<input class="code" inputmode="numeric" maxlength="7" placeholder="000 000">` +
      `<button class="primary act" data-act="approve">Approve</button>` +
      `<button class="ghost act" data-act="deny">Deny</button>`;
    const code = () => row.querySelector(".code").value.replace(/\D/g, "");
    row.querySelectorAll(".act").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        let s = 0, d = null;
        try {
          ({ status: s, data: d } = await api(
            `/v1/pair/requests/${btn.dataset.act}`,
            { method: "POST",
              headers: { "X-Quiver-Pair": "1" },
              body: JSON.stringify({ code: code(), request_id: r.request_id }) }
          ));
        } catch {
          el("approve-error").textContent = "request failed: connection lost";
        } finally {
          if (s !== 200) btn.disabled = false;
        }
        if (s === 200) { loadApprovals(); return; }
        if (d) {
          el("approve-error").textContent =
            (d.error && d.error.message) || `${btn.dataset.act} failed (${s})`;
        }
      });
    });
    list.appendChild(row);
  }
}

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
  dropPending();
  state.awaitingReply = false;
  resetTray();
  closeMenu();
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
  // The tray survives the app closing: the hub holds the sends.
  loadQueue(key, gen);
}

function updateOwnership(row) {
  const observed = !row || row.ownership !== "attached";
  // The composer always renders: on observed sessions it's disabled so
  // the phone still shows where typing will happen once attached.
  el("composer-text").disabled = observed;
  el("composer-text").placeholder = observed
    ? "Read-only: tap Attach to send"
    : "Message the session";
  el("chat-banner").classList.toggle("hidden", !observed);
  // attachable means "has resume flags"; the server also refuses when
  // the session is mid-turn, so hide the button then rather than have
  // every tap bounce off the 409.
  const busy = row && row.status === "active";
  const canAttach = observed && row && row.attachable && !busy;
  el("attach-btn").classList.toggle("hidden", !canAttach);
  updateWorking();
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
    appendMessage(ev.data || {}, ev.seq, live);
  } else if (ev.type === "session.status") {
    const row = state.sessions.find((r) => r.key === ev.session);
    const status = (ev.data || {}).status || "";
    // The turn ended (or stopped to ask) without a message to show.
    if (ev.session === state.currentKey && status && status !== "active") {
      state.awaitingReply = false;
    }
    if (row) {
      row.status = status;
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
    }, ev.seq, live);
  } else if (ev.type === "command.result") {
    // Sends answer async — settle the optimistic bubble this result
    // belongs to, plus any queued approval card it answers.
    const d = ev.data || {};
    let settled = false;
    if (d.op === "message" && d.client_id) {
      // Remembered even when no row shows it yet: a queue list fetched
      // before this result can't bring the send back.
      state.settledSends.add(d.client_id);
      const entry = takeQueued(
        d.client_id, d.cancelled ? "That message was removed" : undefined,
      );
      if (entry) {
        // A queued send went out (or failed): into the transcript as a
        // send, settled below. One that was removed is just gone.
        settled = true;
        if (!d.cancelled) appendOptimistic(entry.text, d.client_id);
      } else if (d.cancelled) {
        // Removed from another device, or after this one let it go:
        // never a failure.
        settled = true;
        state.settledSends.add(d.client_id);
        const gone = el("chat-log").querySelector(
          `.msg.user[data-client-id="${CSS.escape(d.client_id)}"]`
        );
        if (gone) gone.remove();
      }
      const b = el("chat-log").querySelector(
        `.msg.user.pending[data-client-id="${CSS.escape(d.client_id)}"]`
      );
      if (b) {
        settled = true;
        b.classList.remove("pending");
        const note = b.querySelector(".send-note");
        if (d.ok) {
          // A degraded send still landed — keep the why visible.
          if (note) {
            if (d.degraded) {
              note.textContent = d.note ||
                "sent (queued: mid-turn input isn't supported here)";
            } else {
              note.remove();
            }
          }
        } else {
          b.classList.add("failed");
          if (note) note.textContent = d.note || "send failed";
          state.awaitingReply = false;
          updateWorking();
        }
      }
    }
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
    if (!settled &&
        (d.ok === false || d.degraded || d.note === "approval_not_supported")) {
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

/* ── live pacing ────────────────────────────────────────────
   Live events reveal one at a time with a short gap that shrinks as
   more wait; a replay after reconnecting (more than 24) lands at once. */

function revealDelay(waiting) {
  if (waiting <= 0 || waiting > 24) return null;
  return Math.min(180, 1200 / waiting);
}

function enqueue(ev) {
  state.pending.push(ev);
  updateWorking();
  if (state.revealTimer !== null) return;
  // Frames often arrive in separate reads a few ms apart: the queue
  // empties between them, so keep the gap from the last entry shown.
  const gap = (revealDelay(state.pending.length) || 0) - (performance.now() - state.lastRevealAt);
  if (gap > 0) state.revealTimer = setTimeout(revealNext, gap);
  else revealNext();
}

function revealNext() {
  state.revealTimer = null;
  if (!state.pending.length) { updateWorking(); return; }
  const delay = revealDelay(state.pending.length);
  if (delay === null) { flushPending(); return; }
  const log = el("chat-log");
  const before = log.childElementCount;
  showLive(state.pending.shift());
  updateWorking();
  // Only a new entry earns a pause; a status change or a result landing
  // on its card goes straight through.
  const added = log.childElementCount > before;
  if (added) state.lastRevealAt = performance.now();
  if (state.pending.length) state.revealTimer = setTimeout(revealNext, added ? delay : 0);
}

function flushPending() {
  clearTimeout(state.revealTimer);
  state.revealTimer = null;
  const all = state.pending;
  state.pending = [];
  for (const ev of all) showLive(ev);
  updateWorking();
}

function showLive(ev) {
  appendEvent(ev);
  if (ev.seq && state.currentKey) rememberSeq(state.currentKey, ev.seq);
}

function dropPending() {
  clearTimeout(state.revealTimer);
  state.revealTimer = null;
  state.pending = [];
}

// Three pulsing dots under the last message while the agent is
// mid-turn, a send waits for its reply, or messages are still being
// revealed.
function updateWorking() {
  const row = state.sessions.find((r) => r.key === state.currentKey);
  const on = !!state.currentKey &&
    ((row && row.status === "active") || state.awaitingReply || state.pending.length > 0);
  updateSendButton();
  const dots = el("chat-working");
  if (dots.classList.contains("hidden") === !on) return;
  dots.classList.toggle("hidden", !on);
  if (on) scrollBottom();
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

function appendMessage(msg, seq, live = true) {
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
    if (live) d.classList.add("arrive");
    log.appendChild(d);
    if (live) state.awaitingReply = false;
    return;
  }
  if (kind === "user" && live) {
    // A queued send's real bubble lands via message.appended — swap it
    // for the optimistic one we already drew (attachments prefix the
    // stored text with "@path ", hence the endsWith). Backfill never
    // dedupes: history replay must not erase a queued bubble.
    const hit = [...log.querySelectorAll(".msg.user[data-client-id]:not(.failed)")].find(
      (b) => {
        if (!echoes(msg.text, b.dataset.text)) return false;
        const sentAt = Number(b.dataset.sentAt || 0);
        return !msg.ts || !sentAt || msg.ts >= sentAt - 5;
      }
    );
    if (hit && hit === log.lastElementChild) {
      // Still the newest entry: settle the bubble where it is, so it
      // doesn't leave and come back in. Dropping .pending also tells a
      // late POST reply or command.result it's already delivered.
      hit.classList.remove("pending");
      delete hit.dataset.clientId;
      const note = hit.querySelector(".send-note");
      if (note) note.remove();
      hit.querySelector(".text").innerHTML = md(msg.text);
      return;
    }
    if (hit) hit.remove();
    // A queued send went out: it leaves the tray and the echo lands in
    // the transcript where it really is.
    else echoLeavesTray(msg.text);
  }
  if (live && kind !== "user" && kind !== "tool_result") state.awaitingReply = false;
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
  if (live) div.classList.add("arrive");
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
  if (ev.type === "journal.truncated" || ev.type === "journal.caught-up") {
    if (ev.seq && !state.pending.length) rememberSeq(key, ev.seq);
    return;
  }
  // The cursor advances as each event is shown (showLive), so events
  // still waiting when the stream drops are replayed on reconnect.
  if (state.currentKey === key) enqueue(ev);
}

function closeStream() {
  dropPending();
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

/* ── usage ───────────────────────────────────────────────── */

/* ``where`` names the machine the reading came from: a satellite's
 * credentials live on the satellite, so logging in on the hub fixes
 * nothing there. */
function limitStatus(status, where) {
  switch (status) {
    case "no-sub": return "No subscription on this account.";
    case "auth-required": return `Signed out. Log in to it again on ${where}.`;
    case "timeout": return "The provider didn't answer in time.";
    default: return status;
  }
}

function resetIn(ts) {
  if (!ts) return "";
  const d = ts - Date.now() / 1000;
  if (d <= 0) return "resets now";
  if (d < 3600) return `resets in ${Math.ceil(d / 60)}m`;
  if (d < 86400) return `resets in ${Math.floor(d / 3600)}h ${Math.floor((d % 3600) / 60)}m`;
  return `resets in ${Math.floor(d / 86400)}d ${Math.floor((d % 86400) / 3600)}h`;
}

/* swe leaves display_name null when the registry names none; names
 * always start with a capital, as in the Mac app. */
function harnessName(h) {
  const n = h.display_name || h.name || "";
  return n.charAt(0).toUpperCase() + n.slice(1);
}

/* A provider's own window label ("5h", "7d", "grok_bot"), else one made
 * from its length: Codex reports a 7-day window with no label. */
function windowLabel(w) {
  if (w.window) return String(w.window).replace(/_/g, " ");
  const s = w.window_seconds;
  if (!s) return "Quota";
  return s % 86400 === 0 ? `${s / 86400}d` : `${Math.round(s / 3600)}h`;
}

/* Rate limits are cached for 300 s; a reading older than that (plus
 * slack) is the outage fallback, which can be up to a day old. */
const STALE_READING_S = 360;

function ago(seconds) {
  const d = Math.max(0, seconds);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function usd(n) {
  return "$" + Number(n).toFixed(2);
}

function limitBar(label, w, now) {
  const used = Math.max(0, Math.min(100, w.used_percent || 0));
  const tone = w.limit_reached || used >= 90 ? "hot" : used >= 70 ? "warm" : "";
  const right = w.unavailable ? "unavailable" : `${used}% used`;
  return `<div class="limit">
    <div class="limit-row"><span>${esc(label)}</span><span class="dim">${esc(right)}</span></div>
    <div class="bar"><div class="fill ${tone}" style="width:${w.unavailable ? 0 : used}%"></div></div>
    <div class="limit-sub dim">${esc([
      resetIn(w.reset_at),
      w.as_of ? `as of ${ago(now - w.as_of)}` : "",
    ].filter(Boolean).join(" · "))}</div>
  </div>`;
}

/* ``now`` and ``snapAt`` are on the reporting machine's clock: how old a
 * reading is today, and how old it already was when the machine took the
 * snapshot. Only the second says the provider stopped answering; a
 * satellite's report just sits on the hub for up to five minutes. */
function limitCard(h, now, snapAt, where) {
  const rl = h.rate_limit;
  const plan = rl.plan_type ? `<span class="own">${esc(rl.plan_type)}</span>` : "";
  let body;
  if (rl.status !== "ok") {
    body = `<p class="dim">${esc(limitStatus(rl.status, where))}</p>`;
  } else {
    const windows = rl.windows && rl.windows.length ? rl.windows : [rl];
    body = windows.map((w) => limitBar(windowLabel(w), w, now)).join("");
    if (rl.total_units != null && rl.remaining_units != null) {
      body += `<p class="dim">${esc(rl.total_units < 0
        ? "Unlimited credits"
        : `${rl.remaining_units} of ${rl.total_units} credits left`)}</p>`;
    }
    if (rl.extra_usage_usd != null) {
      body += `<p class="dim">${esc(usd(rl.extra_usage_usd))} extra usage left</p>`;
    }
    const od = rl.on_demand;
    if (od && od.state === "fixed" && od.limit_usd != null) {
      body += `<p class="dim">On-demand: ${esc(usd(od.used_usd || 0))} of ${esc(usd(od.limit_usd))}</p>`;
    } else if (od && od.state === "unlimited") {
      body += `<p class="dim">On-demand: ${esc(usd(od.used_usd || 0))}, no limit</p>`;
    }
    if (rl.fetched_at) {
      const age = now - rl.fetched_at;
      body += snapAt - rl.fetched_at > STALE_READING_S
        ? `<p class="stale">Stale: last read ${esc(ago(age))}, newer reads failed.</p>`
        : `<p class="dim">Read ${esc(ago(age))}</p>`;
    }
  }
  return `<div class="card usage-card">
    <div class="usage-title"><span class="t">${esc(harnessName(h))}</span>${plan}</div>
    ${body}
  </div>`;
}

/* A satellite reports every 300 s; two missed reports means it is
 * asleep or cut off, and its figures are that old. */
const STALE_REPORT_S = 660;

function machineName(m) {
  return (m.machine && (m.machine.name || m.machine.id)) || "unknown";
}

function usageMachines(data) {
  if (Array.isArray(data.machines) && data.machines.length) return data.machines;
  const now = data.server_time || Date.now() / 1000;
  return [{ machine: data.machine, harnesses: data.harnesses || [],
            window_days: data.window_days, reported_at: now,
            received_at: now, hub: true }];
}

function renderMachineUsage(m, serverTime) {
  const rows = m.harnesses || [];
  const limited = rows.filter((h) => h.rate_limit);
  const days = m.window_days || 100;
  const used = rows.filter((h) => h.sessions_100d > 0)
    .sort((a, b) => b.sessions_100d - a.sessions_100d);
  const most = used.length ? used[0].sessions_100d : 1;
  // Ages are measured on the reporting machine's own clock, which also
  // stamped fetched_at: the hub's clock moved back by however far the
  // two disagreed when the report arrived.
  const received = m.received_at || serverTime;
  const skew = m.reported_at != null ? received - m.reported_at : 0;
  const now = serverTime - skew;
  const snapAt = received - skew;
  let html = "";
  if (!m.hub) {
    const age = serverTime - received;
    const offline = m.machine && m.machine.online === false;
    const late = age > STALE_REPORT_S;
    html += `<p class="${late || offline ? "stale" : "dim"} usage-report">${esc(
      (offline ? "Offline. " : "") + `Last report ${ago(age)}` +
      (late ? ", so these figures may be out of date." : "."))}</p>`;
  }
  html += `<div class="usage-group"><h2>Limits</h2>`;
  html += limited.length
    ? limited.map((h) => limitCard(h, now, snapAt, machineName(m))).join("")
    : `<p class="dim">No limits to show. Star a harness on this machine with
       <code>swe hs star &lt;name&gt;</code> to track its quota.</p>`;
  html += `</div><div class="usage-group"><h2>Sessions in the last ${esc(days)} days</h2>`;
  html += used.length
    ? `<div class="card">${used.map((h) => `
        <div class="count-row">
          <span class="name">${esc(harnessName(h))}</span>
          <div class="bar"><div class="fill" style="width:${(h.sessions_100d / most) * 100}%"></div></div>
          <span class="n">${esc(h.sessions_100d)}</span>
        </div>`).join("")}</div>`
    : `<p class="dim">No sessions on this machine in that window.</p>`;
  return html + "</div>";
}

function renderUsage(data) {
  state.usageData = data;
  const machines = usageMachines(data);
  const ids = machines.map((m) => (m.machine && m.machine.id) || "");
  let pick = state.usageMachine;
  if (pick == null) {
    try { pick = localStorage.getItem("quiver.usageMachine"); } catch { pick = null; }
  }
  if (!ids.includes(pick)) pick = ids[0];
  state.usageMachine = pick;
  const current = machines[ids.indexOf(pick)];
  let html = "";
  if (machines.length > 1) {
    html += `<div class="chips" role="tablist">${machines.map((m, i) => `
      <button type="button" class="chip${ids[i] === pick ? " on" : ""}" role="tab"
        aria-selected="${ids[i] === pick}" data-mid="${esc(ids[i])}">${esc(machineName(m))}${
        m.hub ? ` <span class="dim">hub</span>` : ""}</button>`).join("")}</div>`;
  }
  html += renderMachineUsage(current, data.server_time || Date.now() / 1000);
  el("usage-body").innerHTML = html;
  el("usage-body").querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.usageMachine = chip.dataset.mid;
      try { localStorage.setItem("quiver.usageMachine", chip.dataset.mid); } catch { /* private mode */ }
      renderUsage(state.usageData);
    });
  });
  el("usage-meta").textContent = machines.length > 1
    ? `${machines.length} machines` : `On ${machineName(current)}`;
}

/* The timer, tab clicks and Refresh can overlap; only the newest
 * request may draw or re-enable the button, so a slow older answer
 * never replaces a newer one. */
async function loadUsage(refresh = false) {
  const gen = ++state.usageGen;
  const btn = el("usage-refresh");
  btn.disabled = true;
  if (!el("usage-body").innerHTML) {
    el("usage-body").innerHTML = `<p class="dim">Loading usage…</p>`;
  }
  try {
    const { status, data } = await api("/v1/usage" + (refresh ? "?refresh=1" : ""));
    if (gen !== state.usageGen) return;
    if (status === 401) { store.cred = null; boot(); return; }
    if (status !== 200 || !data) {
      const msg = data && data.error ? data.error.message : `hub answered ${status}`;
      el("usage-body").innerHTML = `<p class="error">Couldn't load usage: ${esc(msg)}</p>`;
      return;
    }
    if (state.view === "usage") renderUsage(data);
  } catch {
    if (gen === state.usageGen) {
      el("usage-body").innerHTML = `<p class="error">Couldn't reach the hub.</p>`;
    }
  } finally {
    if (gen === state.usageGen) btn.disabled = false;
  }
}

el("usage-refresh").addEventListener("click", () => loadUsage(true));

/* ── composer + controls ─────────────────────────────────── */

function newClientId() {
  // Server accepts 1-64 of [A-Za-z0-9_-]; uuid dashes are already legal.
  if (crypto.randomUUID) return crypto.randomUUID();
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
  return [...crypto.getRandomValues(new Uint8Array(24))]
    .map((b) => alphabet[b % 64]).join("");
}

function appendOptimistic(text, clientId) {
  const div = document.createElement("div");
  div.className = "msg user pending";
  div.dataset.clientId = clientId;
  div.dataset.text = text;
  div.dataset.sentAt = String(Math.floor(Date.now() / 1000));
  div.innerHTML =
    `<div class="role">you</div><div class="text">${md(text)}</div>` +
    `<div class="send-note">sending…</div>`;
  div.classList.add("arrive");
  el("chat-log").appendChild(div);
  scrollBottom();
  return div;
}

function failBubble(bubble, text) {
  bubble.classList.remove("pending");
  bubble.classList.add("failed");
  const note = bubble.querySelector(".send-note") || document.createElement("div");
  note.className = "send-note";
  note.textContent = text;
  bubble.appendChild(note);
}

/* An echoed user message is the stored text, which attachments prefix
 * with "@path ", hence the endsWith. */
function echoes(stored, sent) {
  if (!sent) return false;
  return stored === sent || (stored || "").endsWith(sent);
}

function hubError(status, data) {
  const e = (data && data.error) || {};
  return { status, code: e.code || (status ? "" : "network"), message: e.message || "" };
}

/* What the button beside the prompt box does: send whatever is typed,
 * stop the agent when nothing is typed and it's working, else nothing.
 * An emptied edit has nothing to save, so stop stays out of its way. */
function composerAction() {
  if (el("composer-text").value.trim()) return "send";
  if (state.editingId) return "disabled";
  return agentWorking() ? "stop" : "disabled";
}

// Mid-turn, or about to start on a send: worth a stop. Messages still
// being revealed after the turn ended don't count.
function agentWorking() {
  const row = state.sessions.find((r) => r.key === state.currentKey);
  return (row && row.status === "active") || state.awaitingReply;
}

function updateSendButton() {
  const btn = el("composer-send");
  const action = composerAction();
  const observed = el("composer-text").disabled;
  btn.classList.toggle("stop", action === "stop");
  btn.disabled = observed || action === "disabled" || state.savingEdit;
  btn.setAttribute("aria-label", action === "stop" ? "Stop the agent" : state.editingId ? "Save" : "Send");
  btn.title = action === "stop" ? "Stop the agent" : state.editingId ? "Save" : "Send";
}

el("composer-text").addEventListener("input", updateSendButton);

el("composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = el("composer-text");
  if (!state.currentKey || input.disabled) return;
  const action = composerAction();
  if (action === "stop") { interrupt(); return; }
  if (action !== "send") return;
  closeMenu();
  if (state.editingId) { saveEdit(); return; }
  const text = input.value.trim();
  input.value = "";
  closeMenu();
  state.editNote = "";
  state.trayNote = "";
  sendText(text);
});

/* Queue mode: it goes out at once when the agent is idle, else the hub
 * holds it and the tray shows it (Send now on a tray row is the steer).
 * A satellite's session can't be held, and a queue-mode send to it
 * mid-turn is refused, so mid-turn it steers straight in, no tray. */
function sendRoute(row) {
  const active = !!row && row.status === "active";
  if (!active) return { toTray: false, mode: "queue" };
  return row.remote ? { toTray: false, mode: "steer" } : { toTray: true, mode: "queue" };
}

async function sendText(text) {
  const key = state.currentKey;
  const gen = state.openGen;
  const row = state.sessions.find((r) => r.key === key);
  const route = sendRoute(row);
  const clientId = newClientId();
  let bubble = null;
  if (route.toTray) {
    state.queued.push({ cid: clientId, text, phase: "posting", note: "" });
    renderTray();
  } else {
    bubble = appendOptimistic(text, clientId);
    state.awaitingReply = true;
  }
  updateWorking();
  let status = 0, data = null;
  try {
    ({ status, data } = await api(
      `/v1/sessions/${encodeURIComponent(key)}/message`,
      { method: "POST", body: JSON.stringify({ text, mode: route.mode, client_id: clientId }) }
    ));
  } catch {
    status = 0; // network failure — the hub never saw it
  }
  if (gen !== state.openGen) return; // another chat is open now
  if (status === 202) posted(clientId, bubble, !!(data && data.waiting));
  else postFailed(clientId, bubble, hubError(status, data));
}

/* The hub took the send; `waiting` says whether it holds it for idle.
 * Where it shows follows the hub, not the status the page last saw. */
function posted(cid, bubble, waiting) {
  const entry = state.queued.find((q) => q.cid === cid);
  if (entry) {
    if (entry.phase !== "posting") return;
    if (waiting) {
      entry.phase = "waiting";
    } else {
      // The agent was idle after all: it goes out now.
      state.queued = state.queued.filter((q) => q !== entry);
      const b = appendOptimistic(entry.text, cid);
      b.querySelector(".send-note").textContent = "sent";
      state.awaitingReply = true;
      updateWorking();
    }
    renderTray();
    return;
  }
  // A fast command.result may already have settled this bubble —
  // .pending is gone then, and the response must not clobber it.
  if (!bubble || !bubble.classList.contains("pending")) return;
  if (waiting && !state.settledSends.has(cid)) {
    // The agent was busy after all: it waits in the tray.
    bubble.remove();
    state.queued.push({ cid, text: bubble.dataset.text, phase: "waiting", note: "" });
    state.awaitingReply = false;
    updateWorking();
    renderTray();
    return;
  }
  const note = bubble.querySelector(".send-note");
  if (note) note.textContent = "sent";
}

/* The POST failed: a failed bubble in the transcript. */
function postFailed(cid, bubble, err) {
  const why = err.message || (err.status ? `send failed (${err.status})` : "request failed — connection lost");
  const entry = state.queued.find((q) => q.cid === cid);
  if (entry) {
    state.queued = state.queued.filter((q) => q !== entry);
    state.settledSends.add(cid);
    renderTray();
    failBubble(appendOptimistic(entry.text, cid), why);
    return;
  }
  if (!bubble || !bubble.classList.contains("pending")) return;
  state.awaitingReply = false;
  updateWorking();
  failBubble(bubble, why);
}

async function interrupt() {
  if (!state.currentKey) return;
  let status = 0, data = null;
  try {
    ({ status, data } = await api(
      `/v1/sessions/${encodeURIComponent(state.currentKey)}/control`,
      { method: "POST", body: JSON.stringify({ op: "interrupt" }) }
    ));
  } catch { status = 0; }
  if (status >= 400 || status === 0) {
    appendMessage({
      kind: "system",
      text: (data && data.error && data.error.message) || "interrupt failed",
    }, 0);
    scrollBottom();
  }
}

/* ── queued tray ─────────────────────────────────────────────
   Sends made while the agent works, pinned above the prompt box until
   they go out. Each can be edited, removed, or sent now (written into
   the running turn); the header's button removes them all. */

function resetTray() {
  if (state.editingId) el("composer-text").value = state.draftBeforeEdit;
  state.queued = [];
  state.departed = [];
  state.settledSends = new Set();
  state.unmatchedEchoes = [];
  state.editingId = null;
  state.draftBeforeEdit = "";
  state.savingEdit = false;
  state.editNote = "";
  state.trayNote = "";
  clearTimeout(state.confirmRemoveAll);
  state.confirmRemoveAll = null;
  renderTray();
}

/* A send that left the hub's queue: out of the tray (or the hidden
 * departed list), remembered as settled. */
function takeQueued(cid, gone = "That message already went out") {
  for (const list of ["queued", "departed"]) {
    const entry = state[list].find((q) => q.cid === cid);
    if (entry) {
      state[list] = state[list].filter((q) => q !== entry);
      state.settledSends.add(cid);
      if (state.editingId === cid) {
        // Too late to edit: the unfinished text stays in the box to send
        // as a new message.
        state.editingId = null;
        state.draftBeforeEdit = "";
        state.editNote = gone;
      }
      renderTray();
      return entry;
    }
  }
  return null;
}

function echoLeavesTray(text) {
  for (const list of ["departed", "queued"]) {
    const entry = state[list].find((q) => echoes(text, q.text));
    if (entry) { takeQueued(entry.cid); return; }
  }
  // No row for it yet: a queue list fetched before this echo may still
  // name the send, so mergeQueue checks against it.
  state.unmatchedEchoes.push(text);
}

async function loadQueue(key, gen) {
  // Only echoes that arrive while this list is on its way can be
  // missing from it.
  state.unmatchedEchoes = [];
  let status = 0, data = null;
  try {
    ({ status, data } = await api(`/v1/sessions/${encodeURIComponent(key)}/queue`));
  } catch { return; }
  if (gen !== state.openGen || state.currentKey !== key) return;
  // An older hub has no queue route: the tray only shows this page's sends.
  if (status === 200 && data) mergeQueue(data.sends || []);
  state.unmatchedEchoes = [];
}

/* The hub's list wins: what it holds shows in its order (keeping what
 * this page knows about each), and what it no longer holds leaves the
 * tray for `departed`, except a send whose POST hasn't been answered
 * yet. Sends already shown in the transcript or gone can't come back. */
function mergeQueue(sends) {
  const known = new Set(state.settledSends);
  el("chat-log").querySelectorAll(".msg.user[data-client-id]").forEach((b) => known.add(b.dataset.clientId));
  const out = [];
  for (const send of sends) {
    const cid = send.client_id;
    if (!cid || known.has(cid) || out.some((q) => q.cid === cid)) continue;
    const mine = state.queued.find((q) => q.cid === cid);
    if (!mine) {
      const echo = state.unmatchedEchoes.findIndex((t) => echoes(t, send.text));
      if (echo >= 0) {
        // It went out between the hub's answer and now.
        state.unmatchedEchoes.splice(echo, 1);
        state.settledSends.add(cid);
        continue;
      }
    }
    // The hub's text wins: another device may have edited it. The local
    // row keeps its action state.
    const text = send.text == null ? (mine ? mine.text : "") : send.text;
    if (mine) out.push({ ...mine, text, phase: mine.phase === "posting" ? "waiting" : mine.phase });
    else out.push({ cid, text, phase: "waiting", note: "" });
  }
  for (const mine of state.queued) {
    if (out.some((q) => q.cid === mine.cid)) continue;
    if (mine.phase === "posting") out.push(mine);
    else if (!known.has(mine.cid) && !state.departed.some((d) => d.cid === mine.cid)) {
      state.departed.push(mine);
    }
  }
  state.queued = out;
  if (state.editingId && !out.some((q) => q.cid === state.editingId && q.phase === "waiting")) {
    cancelEdit();
  }
  renderTray();
}

/* The short note a tray row shows when the hub refuses Send now or
 * Remove. */
function queueNote(err) {
  switch (err.code) {
    case "not_waiting": {
      const why = err.message.toLowerCase();
      if (why.includes("removed")) return "Already removed";
      if (why.includes("failed")) return "Already failed";
      return "Already sent";
    }
    case "not_attached": return "Attach to send now";
    case "queue_unsupported": return "Not available on this machine";
    case "backlog_full": return "Too many waiting. Try again shortly";
    case "unknown_send": return "The hub lost this message";
    case "network": return "Can't reach the hub";
    default: return err.message || "Something went wrong";
  }
}

async function queueAction(cid, op, text) {
  const body = { client_id: cid, op };
  if (text !== undefined) body.text = text;
  let status = 0, data = null;
  try {
    ({ status, data } = await api(
      `/v1/sessions/${encodeURIComponent(state.currentKey)}/queue`,
      { method: "POST", body: JSON.stringify(body) }
    ));
  } catch { status = 0; }
  return status === 200 ? null : hubError(status, data);
}

/* Send now: the hub stops holding it and writes it into the running
 * turn. It shows "Sending now" until its result or echo arrives. */
async function sendNow(cid) {
  const entry = state.queued.find((q) => q.cid === cid);
  if (!entry || entry.phase !== "waiting") return;
  const gen = state.openGen;
  if (state.editingId === cid) cancelEdit();
  entry.phase = "sendingNow";
  entry.note = "";
  state.trayNote = "";
  renderTray();
  const err = await queueAction(cid, "steer");
  if (!err || gen !== state.openGen) return;
  if (entry.phase !== "sendingNow" || !state.queued.includes(entry)) return;
  entry.phase = err.code === "not_waiting" ? "left" : "waiting";
  entry.note = queueNote(err);
  renderTray();
}

/* Remove: gone from the tray at once, back with a note if the hub
 * refuses. Its command.result says cancelled, never a failure. */
async function removeQueued(cid) {
  const entry = state.queued.find((q) => q.cid === cid);
  if (!entry) return;
  if (entry.phase === "left") { dropQueued(cid); return; } // nothing to ask
  if (entry.phase !== "waiting") return;
  const gen = state.openGen;
  if (state.editingId === cid) cancelEdit();
  entry.phase = "removing";
  entry.note = "";
  state.trayNote = "";
  renderTray();
  const err = await queueAction(cid, "cancel");
  if (gen !== state.openGen) return;
  if (!err) { dropQueued(cid); return; }
  if (entry.phase !== "removing" || !state.queued.includes(entry)) return;
  // The hub never held it, or forgot it: there's nothing to remove.
  if (err.code === "unknown_send") { dropQueued(cid); return; }
  entry.phase = err.code === "not_waiting" ? "left" : "waiting";
  entry.note = queueNote(err);
  renderTray();
}

function dropQueued(cid) {
  state.settledSends.add(cid);
  state.queued = state.queued.filter((q) => q.cid !== cid);
  renderTray();
}

/* Edit: the message's text goes into the prompt box, and the send
 * button saves it back. Only a send the hub still holds can be edited. */
function beginEdit(cid) {
  const entry = state.queued.find((q) => q.cid === cid);
  if (!entry || entry.phase !== "waiting") return;
  const input = el("composer-text");
  if (!state.editingId) state.draftBeforeEdit = input.value;
  state.editingId = cid;
  state.editNote = "";
  input.value = entry.text;
  closeMenu();
  input.focus();
  renderTray();
}

function cancelEdit() {
  if (!state.editingId) return;
  state.editingId = null;
  el("composer-text").value = state.draftBeforeEdit;
  state.draftBeforeEdit = "";
  state.editNote = "";
  // The box's text changed under any open menu: its token is stale.
  closeMenu();
  renderTray();
}

async function saveEdit() {
  const cid = state.editingId;
  const input = el("composer-text");
  const text = input.value.trim();
  if (!cid || state.savingEdit || !text) return;
  const entry = state.queued.find((q) => q.cid === cid);
  if (!entry || entry.phase !== "waiting") { editWentOut(cid, text); return; }
  const gen = state.openGen;
  state.savingEdit = true;
  renderTray();
  const err = await queueAction(cid, "edit", text);
  if (gen !== state.openGen) return;
  state.savingEdit = false;
  if (!err) {
    // The row keeps its place with the new text.
    entry.text = text;
    entry.note = "";
    if (state.editingId === cid) cancelEdit();
    renderTray();
    return;
  }
  if (err.code === "not_waiting") { editWentOut(cid, text); return; }
  // An older hub can't edit in place: remove the message, then queue
  // the new text, which lands at the end.
  if (err.status === 400 || err.status === 422) { requeueEdit(cid, text, gen); return; }
  state.editNote = queueNote(err);
  renderTray();
}

/* Too late to edit: the new text stays in the prompt box to send as a
 * new message. */
function editWentOut(cid, text) {
  const entry = state.queued.find((q) => q.cid === cid);
  if (entry && entry.phase === "waiting") {
    entry.phase = "left";
    entry.note = "Already sent";
  }
  state.editingId = null;
  state.draftBeforeEdit = "";
  el("composer-text").value = text;
  closeMenu();
  state.editNote = "That message already went out";
  renderTray();
}

async function requeueEdit(cid, text, gen) {
  const err = await queueAction(cid, "cancel");
  if (gen !== state.openGen) return;
  if (err) {
    if (err.code === "not_waiting") { editWentOut(cid, text); return; }
    state.editNote = queueNote(err);
    renderTray();
    return;
  }
  dropQueued(cid);
  if (state.editingId === cid) {
    state.editingId = null;
    el("composer-text").value = state.draftBeforeEdit;
    state.draftBeforeEdit = "";
    closeMenu();
  }
  state.trayNote = "Edited. It moved to the end of the queue";
  sendText(text);
}

function removeAllQueued() {
  if (state.confirmRemoveAll === null) {
    // First tap arms it; the label asks for a second.
    state.confirmRemoveAll = setTimeout(() => {
      state.confirmRemoveAll = null;
      renderTray();
    }, 3000);
    renderTray();
    return;
  }
  clearTimeout(state.confirmRemoveAll);
  state.confirmRemoveAll = null;
  for (const q of [...state.queued]) {
    if (q.phase === "waiting" || q.phase === "left") removeQueued(q.cid);
  }
  renderTray();
}

function renderTray() {
  const tray = el("queued-tray");
  const shown = state.queued.filter((q) => q.phase !== "removing");
  tray.classList.toggle("hidden", !shown.length);
  if (!shown.length && state.confirmRemoveAll !== null) {
    clearTimeout(state.confirmRemoveAll);
    state.confirmRemoveAll = null;
  }
  const n = shown.length;
  const arming = state.confirmRemoveAll !== null;
  let html = `<div class="tray-head"><span>${n === 1 ? "1 message queued" : `${n} messages queued`}</span>` +
    `<button type="button" class="tray-btn${arming ? " arming" : ""}" data-act="remove-all"` +
    ` aria-label="Remove all">${arming ? (n === 1 ? "Remove it?" : `Remove all ${n}?`) : "✕"}</button></div>`;
  if (state.trayNote) html += `<div class="tray-note">${esc(state.trayNote)}</div>`;
  html += `<div class="tray-rows">`;
  for (const q of shown) {
    const editing = q.cid === state.editingId;
    let right;
    const actions = (off) =>
      `<button type="button" class="tray-btn" data-act="edit" aria-label="Edit" title="Edit"${off}>✎</button>` +
      `<button type="button" class="tray-btn" data-act="remove" aria-label="Remove" title="Remove"${off}>✕</button>` +
      `<button type="button" class="tray-btn" data-act="send-now" aria-label="Send now" title="Send now"${off}>↵</button>`;
    // A refused action can be tried again: the buttons stay, and the
    // note goes under the text.
    const retry = !!q.note && q.phase === "waiting";
    if (retry) {
      right = actions("");
    } else if (q.note) {
      right = `<span class="tray-row-note${q.phase === "left" ? " left" : ""}">${esc(q.note)}</span>` +
        (q.phase === "left"
          ? `<button type="button" class="tray-btn" data-act="remove" aria-label="Remove">✕</button>`
          : "");
    } else if (q.phase === "sendingNow") {
      right = `<span class="tray-row-note left">Sending now</span>`;
    } else {
      right = actions(q.phase === "posting" ? " disabled" : "");
    }
    html += `<div class="tray-row${editing ? " editing" : ""}${q.phase === "posting" ? " posting" : ""}" data-cid="${esc(q.cid)}">` +
      `<span class="tray-mark${q.phase === "sendingNow" ? " spin" : ""}">${editing ? "✎" : ""}</span>` +
      `<span class="tray-col"><span class="tray-text">${esc(q.text.replace(/\n/g, " "))}</span>` +
      (retry ? `<span class="tray-row-note sub">${esc(q.note)}</span>` : "") + `</span>${right}</div>`;
  }
  tray.innerHTML = html + `</div>`;
  const bar = el("edit-bar");
  bar.classList.toggle("hidden", !state.editingId && !state.editNote);
  bar.innerHTML = state.editingId
    ? `<span class="edit-label">Editing queued message${
        state.editNote ? `<span class="edit-note">${esc(state.editNote)}</span>` : ""}</span>` +
      `<button type="button" class="tray-btn text" data-act="cancel-edit"${state.savingEdit ? " disabled" : ""}>Cancel</button>`
    : state.editNote ? `<span class="edit-note">${esc(state.editNote)}</span>` : "";
  el("composer-text").placeholder = el("composer-text").disabled
    ? el("composer-text").placeholder
    : state.editingId ? "Edit the queued message" : "Message the session";
  updateSendButton();
}

el("queued-tray").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || btn.disabled) return;
  const cid = (btn.closest(".tray-row") || {}).dataset?.cid;
  const act = btn.dataset.act;
  if (act === "remove-all") removeAllQueued();
  else if (act === "remove") removeQueued(cid);
  else if (act === "send-now") sendNow(cid);
  else if (act === "edit") beginEdit(cid);
});

el("edit-bar").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act=cancel-edit]");
  if (btn && !btn.disabled) cancelEdit();
});

el("attach-btn").addEventListener("click", async () => {
  if (!state.currentKey) return;
  const { status, data } = await api(
    `/v1/sessions/${encodeURIComponent(state.currentKey)}/control`,
    { method: "POST", body: JSON.stringify({ op: "attach" }) }
  );
  if (status === 202) {
    // The attach went to a satellite's command journal — ownership
    // changes when the command.result lands.
    appendMessage({
      kind: "system",
      text: "attach queued: waiting for the satellite",
    }, 0);
    scrollBottom();
  } else if (status !== 200) {
    appendMessage({
      kind: "error",
      text: (data && data.error && data.error.message) || "attach failed",
    }, 0);
    scrollBottom();
  }
});

/* ── prompt menus ────────────────────────────────────────────
   Typing @ opens files and folders under the session's folder; the
   hub only lists names, and the harness reads the file when the
   message arrives. Positions are offsets into the box's value; the
   caret is the input's selectionEnd. */

/* The `@token` the caret is in, if any: the @ sits at the start of the
 * text or after whitespace and runs to the caret with no whitespace in
 * between. A path with spaces is quoted, @"My Documents/notes.txt", as
 * Claude Code writes it; while a folder is browsed the quote stays open.
 * An @ inside a word (a@b.com) never starts one. */
function mentionToken(text, caret) {
  const quoted = quotedMention(text, caret);
  if (quoted) return quoted;
  let start = caret;
  while (start > 0 && !/\s/.test(text[start - 1])) start--;
  if (start >= caret || text[start] !== "@") return null;
  let end = caret;
  while (end < text.length && !/\s/.test(text[end])) end++;
  return { start, end, query: text.slice(start + 1, caret) };
}

function quotedMention(text, caret) {
  let q = caret - 1;
  while (q >= 0 && text[q] !== '"' && text[q] !== "\n") q--;
  if (q < 1 || text[q] !== '"' || text[q - 1] !== "@") return null;
  const start = q - 1;
  if (start > 0 && !/\s/.test(text[start - 1])) return null;
  let end = caret;
  while (end < text.length && text[end] !== '"' && text[end] !== "\n") end++;
  if (end < text.length && text[end] === '"') {
    end++;
  } else {
    end = caret;
    while (end < text.length && !/\s/.test(text[end])) end++;
  }
  return { start, end, query: text.slice(q + 1, caret) };
}

/* The box after picking `path`, and where the caret goes. A file becomes
 * `@path ` (an existing space is reused); a folder becomes `@path/` so
 * the menu stays open to pick inside it. A path with whitespace is
 * quoted, and a folder's quote is left open until a file closes it. */
function insertMention(text, token, path, isDir) {
  const start = Math.min(token.start, text.length);
  const end = Math.min(Math.max(token.end, start), text.length);
  const clean = trimSlash(path);
  const quoted = /\s/.test(clean);
  let inserted = (quoted ? '@"' : "@") + clean;
  if (isDir) {
    inserted += "/";
  } else {
    if (quoted) inserted += '"';
    if (end >= text.length || !/\s/.test(text[end])) inserted += " ";
  }
  const out = text.slice(0, start) + inserted + text.slice(end);
  let caret = start + inserted.length;
  // A file before existing whitespace: step over it, as if typed.
  if (!isDir && !inserted.endsWith(" ") && caret < out.length) caret++;
  return { text: out, caret };
}

function trimSlash(path) {
  let p = String(path || "");
  while (p.endsWith("/") && p.length > 1) p = p.slice(0, -1);
  return p;
}

/* "src/app/main.py" as its name and the folder it's in. */
function splitPath(path) {
  const clean = trimSlash(path);
  const slash = clean.lastIndexOf("/");
  return slash < 0 ? { name: clean, parent: "" } : { name: clean.slice(slash + 1), parent: clean.slice(0, slash) };
}

const MENTION_DEBOUNCE_MS = 120;
const MENTION_LIMIT = 50;

// The open menu: kind "mention", the token, the rows, which row is
// highlighted, and the search's state. `dismissed` is the token the
// menu was closed on (Escape); it stays closed until the token changes.
const menu = {
  kind: null, token: null, rows: [], active: 0,
  answered: null, loading: false, error: "",
  gen: 0, timer: null, dismissed: null,
};

function sameToken(a, b) {
  return !!a && !!b && a.start === b.start && a.end === b.end && a.query === b.query;
}

function closeMenu() {
  menu.gen++;
  clearTimeout(menu.timer);
  menu.timer = null;
  menu.kind = null;
  menu.token = null;
  menu.rows = [];
  menu.active = 0;
  menu.answered = null;
  menu.loading = false;
  menu.error = "";
  renderMenu();
}

/* Called whenever the box's text, caret or focus changes. */
function trackMenus() {
  const input = el("composer-text");
  const focused = document.activeElement === input;
  const token = focused && !input.disabled && input.selectionStart === input.selectionEnd
    ? mentionToken(input.value, input.selectionEnd) : null;
  if (!token || !sameToken(token, menu.dismissed)) menu.dismissed = null;
  if (!token || menu.dismissed) {
    if (menu.kind) closeMenu();
    return;
  }
  const before = menu.kind === "mention" ? menu.token.query : null;
  menu.kind = "mention";
  menu.token = token;
  if (token.query === before && (menu.answered !== null || menu.loading || menu.error)) return;
  searchFiles(token.query);
}

/* Debounced, one request at a time; the last good rows stay up while
 * the next query loads. */
function searchFiles(query) {
  const gen = ++menu.gen;
  const key = state.currentKey;
  clearTimeout(menu.timer);
  menu.loading = true;
  renderMenu();
  menu.timer = setTimeout(async () => {
    menu.timer = null;
    let status = 0, data = null;
    try {
      ({ status, data } = await api(
        `/v1/sessions/${encodeURIComponent(key)}/files?q=${encodeURIComponent(query)}&limit=${MENTION_LIMIT}`
      ));
    } catch { status = 0; }
    if (gen !== menu.gen || key !== state.currentKey) return;
    menu.loading = false;
    menu.answered = query;
    if (status === 200 && data) {
      menu.rows = data.entries || [];
      menu.error = "";
    } else {
      menu.rows = [];
      menu.error = filesError(hubError(status, data));
    }
    menu.active = 0;
    renderMenu();
  }, MENTION_DEBOUNCE_MS);
}

/* The one muted line the menu shows instead of rows. */
function filesError(err) {
  switch (err.code) {
    case "files_unavailable": {
      const row = state.sessions.find((r) => r.key === state.currentKey);
      const m = row && state.machines.find((x) => x.id === row.machine_id);
      return `File search isn't available for sessions on ${(m && m.name) || "that machine"}`;
    }
    case "cwd_missing": return "This session's folder is gone";
    case "bad_cwd": return "This session has no working folder";
    case "not_readable": return "The hub can't read this session's folder";
    case "unknown_session": return "The hub doesn't know this session any more";
    case "network": return "Can't reach the hub";
    // A hub from before the route answers any unknown path 404.
    default: return err.status === 404 ? "Update the hub to search files from here" : "Couldn't search files";
  }
}

function renderMenu() {
  const box = el("prompt-menu");
  const input = el("composer-text");
  const open = menu.kind === "mention";
  box.classList.toggle("hidden", !open);
  input.setAttribute("aria-expanded", String(open));
  if (!open) { box.innerHTML = ""; input.removeAttribute("aria-activedescendant"); return; }
  let html;
  if (menu.error) {
    html = `<div class="menu-note error-note">${esc(menu.error)}</div>`;
  } else if (menu.answered === null && !menu.rows.length) {
    html = `<div class="menu-note">Searching files</div>`;
  } else if (!menu.rows.length) {
    html = `<div class="menu-note">No files match</div>`;
  } else {
    html = menu.rows.map((entry, i) => {
      const { name, parent } = splitPath(entry.path);
      const dir = entry.kind === "dir";
      return `<div class="menu-row${i === menu.active ? " active" : ""}" role="option" id="menu-opt-${i}"` +
        ` aria-selected="${i === menu.active}" data-i="${i}"` +
        ` aria-label="${esc(parent ? `${name}, in ${parent}` : name)}">` +
        `<span class="menu-icon ${dir ? "dir" : "file"}" aria-hidden="true"></span>` +
        `<span class="menu-name">${esc(name)}</span>` +
        (parent ? `<span class="menu-sub">${esc(parent)}</span>` : "") + `</div>`;
    }).join("");
  }
  box.innerHTML = html;
  if (menu.rows.length && !menu.error) {
    input.setAttribute("aria-activedescendant", `menu-opt-${menu.active}`);
    const row = box.querySelector(".menu-row.active");
    if (row) row.scrollIntoView({ block: "nearest" });
  } else {
    input.removeAttribute("aria-activedescendant");
  }
}

function pickMenu(i) {
  const entry = menu.rows[i];
  if (!entry || menu.kind !== "mention") return;
  const input = el("composer-text");
  const next = insertMention(input.value, menu.token, entry.path, entry.kind === "dir");
  input.value = next.text;
  input.focus();
  input.setSelectionRange(next.caret, next.caret);
  updateSendButton();
  trackMenus();
}

el("composer-text").addEventListener("input", trackMenus);
el("composer-text").addEventListener("focus", trackMenus);
// The caret moved without typing: an arrow key or a tap in the text.
el("composer-text").addEventListener("click", trackMenus);
el("composer-text").addEventListener("keyup", (e) => {
  if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End") trackMenus();
});
// Closing on blur waits a beat, so a tap on a row still lands.
el("composer-text").addEventListener("blur", () => {
  setTimeout(() => { if (document.activeElement !== el("composer-text")) closeMenu(); }, 150);
});

el("composer-text").addEventListener("keydown", (e) => {
  if (!menu.kind) return;
  const rows = menu.error ? 0 : menu.rows.length;
  if (e.key === "Escape") {
    e.preventDefault();
    menu.dismissed = menu.token;
    closeMenu();
  } else if (rows && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    menu.active = (menu.active + (e.key === "ArrowDown" ? 1 : rows - 1)) % rows;
    renderMenu();
  } else if ((e.key === "Enter" || e.key === "Tab") && !e.isComposing) {
    // While the menu is open Enter picks, or waits for rows; it sends
    // once the menu is closed (Escape sends the text as typed).
    if (!rows && e.key === "Tab") return;
    e.preventDefault();
    if (rows) pickMenu(menu.active);
  }
});

// A row picks on a completed tap: pointerdown only keeps the box's
// focus (so the phone keyboard stays up), and a drag that scrolls the
// list, or a touch the browser cancels for scrolling, picks nothing.
let menuTap = null;
el("prompt-menu").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const row = e.target.closest(".menu-row");
  menuTap = row ? { i: Number(row.dataset.i), x: e.clientX, y: e.clientY, id: e.pointerId } : null;
});
el("prompt-menu").addEventListener("pointermove", (e) => {
  if (menuTap && e.pointerId === menuTap.id &&
      Math.hypot(e.clientX - menuTap.x, e.clientY - menuTap.y) > 8) menuTap = null;
});
el("prompt-menu").addEventListener("pointercancel", () => { menuTap = null; });
el("prompt-menu").addEventListener("pointerup", (e) => {
  const tap = menuTap;
  menuTap = null;
  if (!tap || e.pointerId !== tap.id) return;
  const row = e.target.closest(".menu-row");
  if (row && Number(row.dataset.i) === tap.i) pickMenu(tap.i);
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
el("pair-ts").addEventListener("click", () => {
  startTailscalePair(el("pair-name").value.trim());
});
el("pair-retry").addEventListener("click", () => {
  ++state.pairGen;
  clearTimeout(state.pairTimer);
  state.pairTimer = null;
  el("pair-code-card").classList.add("hidden");
  el("pair-start").classList.remove("hidden");
});

window.addEventListener("hashchange", boot);

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    closeStream();
    state.currentKey = null;
    show(tab.dataset.tab);
    if (tab.dataset.tab === "inbox") loadInbox();
    if (tab.dataset.tab === "sessions") loadSessions();
    if (tab.dataset.tab === "usage") loadUsage();
  });
});

function boot() {
  const hash = location.hash || "";
  if (hash === "#approve") {
    show("approve");
    loadApprovals();
    clearInterval(state.approveTimer);
    state.approveTimer = setInterval(() => {
      if (state.view === "approve") loadApprovals();
    }, 3000);
    return;
  }
  clearInterval(state.approveTimer);
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
    if (state.view === "usage") loadUsage();
    loadInbox();
  }, 15000);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && store.cred) {
    if (state.view === "chat" && state.currentKey && !state.stream) {
      openStream(state.currentKey, store.lastSeq[state.currentKey] || 0);
    }
    if (state.view === "chat" && state.currentKey) loadQueue(state.currentKey, state.openGen);
    loadSessions();
    loadInbox();
    if (state.view === "usage") loadUsage();
  }
});

boot();
