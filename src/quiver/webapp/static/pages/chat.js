/* Sessions chat: remote control inside the Sessions page. Opening a
 * session row shows its transcript, live over the hub's SSE, with the
 * composer (Queue/Steer, Stop), approval cards and Attach. A port of the
 * hub PWA's chat (src/quiver/remote/web/app.js): the same session routes,
 * the same ownership rules and the same event handling, restyled.
 *
 * Not a page of its own: sessions.js calls SessionChat.open(key) for
 * #/sessions?chat=<key>, so the sidebar keeps Sessions selected. The
 * bundle runs this file in its own scope; SessionChat is its one global.
 * SSE is read with fetch streams, since EventSource can't send the
 * Authorization header.
 */
"use strict";

const SessionChat = (() => {
  const chat = {
    key: null, gen: 0, row: null, rowKnown: false, details: null,
    stream: null, retryTimer: null,
    // Live events not yet shown. The hub polls a busy transcript about
    // once a second, so several messages arrive together; they are shown
    // one at a time instead of as a block.
    pending: [], revealTimer: null, lastRevealAt: 0,
    // A send went through and nothing from the agent has come back yet.
    awaitingReply: false,
    // The last journal seq shown in this view; a dropped stream resumes
    // from here.
    seq: 0,
    toolCards: new Map(), // call_id -> tool card
    // While set, the stream is replaying events the backfill already
    // showed, looking for one waiting approval (restoreApproval).
    history: null,
    confirmAttach: false, attaching: false, stopping: false,
  };

  const STATUS = { active: "Active", done: "Done", followup: "Waiting", error: "Error",
    interrupted: "Interrupted", approval: "Approval needed" };
  const DECISIONS = { allow: "Allowed", always: "Always allowed", deny: "Denied" };

  const $ = (id) => document.getElementById(id);

  /* ------------------------------------------------------------ requests */

  /* Every request carries app.js's authHeaders: the paired bearer when
   * there is one, and X-Quiver-Pair, which lets the hub owner's own
   * tailnet devices in without pairing. status 0 means the hub never
   * answered (offline, connection lost). */
  async function post(path, body) {
    let resp;
    try {
      resp = await fetch(path, {
        method: "POST", credentials: "same-origin",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
    } catch {
      return { status: 0, data: null };
    }
    let data = null;
    try { data = await resp.json(); } catch { /* empty body */ }
    return { status: resp.status, data };
  }

  const sessionPath = (key, action) => `/v1/sessions/${encodeURIComponent(key)}/${action}`;
  const errorText = (data) => (data && data.error && data.error.message) || "";

  /* An answer saying the session is observed now: the hub's word wins. */
  function noteRefusal(data) {
    if (data && data.error && data.error.code === "not_attached" && chat.row) {
      chat.row.ownership = "observed";
      updateOwnership();
    }
  }

  /* ------------------------------------------------------------- helpers */

  function keyFromHash(hash = location.hash) {
    const m = String(hash || "").match(/^#\/?sessions\?(.*)$/);
    return m ? new URLSearchParams(m[1]).get("chat") : null;
  }

  /* Minimal markdown-ish rendering, as the PWA does: escape everything,
   * then restore fenced code blocks, inline code and bold. */
  function md(text) {
    // Code is set aside as it is found, so no later rule reaches into it:
    // a "**" or a backtick inside a code block stays as written.
    const code = [];
    const keep = (html) => `\u0000${code.push(html) - 1}\u0000`;
    let t = esc(String(text || "").replace(/\u0000/g, ""));
    // A fence swallows the line breaks around it (the text is pre-wrap) and
    // the language word after its opening backticks.
    t = t.replace(/\n?```[\w+-]*\n?([\s\S]*?)```\n?/g,
      (_m, body) => keep(`<pre class="code"><code>${body.replace(/^\n+|\n+$/g, "")}</code></pre>`));
    t = t.replace(/`([^`\n]+)`/g, (_m, body) => keep(`<code>${body}</code>`));
    t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    return t.replace(/\u0000(\d+)\u0000/g, (_m, i) => code[Number(i)]);
  }

  function newClientId() {
    // The hub accepts 1-64 of [A-Za-z0-9_-]; uuid dashes are already legal.
    if (crypto.randomUUID) return crypto.randomUUID();
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-";
    return [...crypto.getRandomValues(new Uint8Array(24))].map((b) => alphabet[b % 64]).join("");
  }

  function nearBottom() {
    const doc = document.documentElement;
    return window.innerHeight + window.scrollY >= doc.scrollHeight - 160;
  }

  function scrollBottom() {
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  /* ---------------------------------------------------------------- diffs
     Edit/Write tool calls carry parseable file args in args_full: they
     render as a unified diff instead of raw JSON. */

  const DIFF_TOOLS = new Set(["edit", "multiedit", "write", "notebookedit", "apply_patch", "patch", "file_edit"]);

  /* Common-affix diff: identical leading and trailing lines collapse to
     context; the differing middle renders removed, then added. */
  function diffLines(oldText, newText) {
    const a = (oldText || "").split("\n");
    const b = (newText || "").split("\n");
    let pre = 0;
    while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
    let suf = 0;
    while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
    return { ctxTop: a.slice(0, pre), del: a.slice(pre, a.length - suf), add: b.slice(pre, b.length - suf),
      ctxBot: a.slice(a.length - suf) };
  }

  const diffFile = (path) => (path ? `<div class="diff-file">${esc(path)}</div>` : "");
  const diffRow = (cls, text) => `<div class="diff-line ${cls}">${esc(text)}</div>`;

  function renderDiff(filePath, oldText, newText) {
    const d = diffLines(oldText, newText);
    const rows = [];
    const emit = (lines, cls, sign) => { for (const ln of lines) rows.push(diffRow(cls, `${sign} ${ln}`)); };
    if (d.ctxTop.length > 3) rows.push(diffRow("diff-meta", `${d.ctxTop.length - 3} unchanged lines`));
    emit(d.ctxTop.slice(-3), "diff-ctx", " ");
    emit(d.del, "diff-del", "-");
    emit(d.add, "diff-add", "+");
    if (d.ctxBot.length) {
      emit(d.ctxBot.slice(0, 2), "diff-ctx", " ");
      if (d.ctxBot.length > 2) rows.push(diffRow("diff-meta", `${d.ctxBot.length - 2} more lines`));
    }
    return `<div class="diff">${diffFile(filePath)}${rows.join("")}</div>`;
  }

  /* Raw patch text (codex apply_patch): colour by line sigil. */
  function renderPatch(filePath, patch) {
    const rows = String(patch || "").split("\n").slice(0, 400).map((ln) => diffRow(
      ln.startsWith("+") ? "diff-add" : ln.startsWith("-") ? "diff-del"
        : ln.startsWith("@@") || ln.startsWith("***") ? "diff-meta" : "diff-ctx", ln));
    return `<div class="diff">${diffFile(filePath)}${rows.join("")}</div>`;
  }

  /* The file a patch names first (codex's "*** Update File: x"), if any. */
  function patchFile(patch) {
    const m = String(patch || "").match(/^\*\*\* (?:Update|Add|Delete) File: (.+)$/m);
    return m ? m[1].trim() : "";
  }

  /* A diff's HTML for edit-ish tools, or null for anything else. */
  function toolDiff(tool) {
    const name = String(tool.name || "").toLowerCase();
    if (!DIFF_TOOLS.has(name) || !tool.args_full) return null;
    let args;
    try { args = JSON.parse(tool.args_full); } catch { return null; }
    const patching = name === "apply_patch" || name === "patch";
    // Codex's apply_patch is a custom tool whose input is the patch text
    // itself, so the hub's args_full is a JSON string, not an object.
    if (patching && typeof args === "string") return args ? renderPatch(patchFile(args), args) : null;
    if (!args || typeof args !== "object") return null;
    const file = args.file_path || args.path || args.filename || "";
    if (name === "write" || name === "notebookedit" || name === "file_edit") {
      const content = args.content || args.new_source || "";
      return content ? renderDiff(file, "", content) : null;
    }
    if (patching) {
      const patch = args.patch || args.diff || args.input || tool.args_full;
      return renderPatch(file || patchFile(patch), patch);
    }
    if (name === "multiedit" && Array.isArray(args.edits)) {
      const parts = args.edits.slice(0, 8)
        .filter((e) => e && (e.old_string != null || e.new_string != null))
        .map((e) => renderDiff(file, e.old_string, e.new_string));
      return parts.length ? parts.join("") : null;
    }
    // edit and friends: {old_string, new_string}
    if (args.old_string != null || args.new_string != null) {
      return renderDiff(file, args.old_string || "", args.new_string || "");
    }
    return null;
  }

  /* One SSE frame's event, or null for a comment, heartbeat or junk. */
  function parseFrame(frame) {
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("data:")) data += line.slice(5).replace(/^ /, "") + "\n";
    }
    if (!data.trim()) return null;
    try { return JSON.parse(data); } catch { return null; }
  }

  /* ---------------------------------------------------------------- shell */

  function harnessLabel(row) {
    return row ? (row.agent && row.agent !== row.harness ? row.agent : Fmt.harnessName(row.harness || "")) : "";
  }

  function paintShell() {
    $("panel").innerHTML = `<div class="page chat-page">
      <div class="chat-bar" id="chat-bar"></div>
      <div class="card error-card hidden" id="chat-error"></div>
      <div class="card chat-banner hidden" id="chat-banner"></div>
      <div class="chat-log" id="chat-log" aria-live="polite"><div class="chat-skel" id="chat-loading" role="status" aria-label="Loading the transcript">
        <div class="skel-lines"><div class="skel skel-line" style="--w:72%"></div><div class="skel skel-line" style="--w:88%"></div><div class="skel skel-line" style="--w:55%"></div></div>
        <div class="skel skel-bubble"></div>
        <div class="skel skel-tool"></div>
        <div class="skel-lines"><div class="skel skel-line" style="--w:80%"></div><div class="skel skel-line" style="--w:62%"></div></div>
        <div class="skel skel-bubble short"></div>
        <div class="skel-lines"><div class="skel skel-line" style="--w:78%"></div><div class="skel skel-line" style="--w:90%"></div><div class="skel skel-line" style="--w:48%"></div></div>
      </div></div>
      <div class="chat-working hidden" id="chat-working" role="status" aria-label="Agent is working"><span></span><span></span><span></span></div>
      <form class="chat-composer" id="chat-composer" autocomplete="off">
        <select class="field chat-mode" id="chat-mode" aria-label="Send mode"
          title="Queue waits for the agent to finish its turn; Steer writes now">
          <option value="queue">Queue</option><option value="steer">Steer</option>
        </select>
        <textarea class="field chat-text" id="chat-text" rows="1" placeholder="Loading…" disabled></textarea>
        <button type="button" class="icon-button chat-stop" id="chat-stop" aria-label="Stop" title="Stop the agent's turn" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/></svg></button>
        <button type="submit" class="chat-send" id="chat-send" disabled>Send</button>
      </form>
    </div>
    <dialog class="sheet" id="session-sheet"></dialog>`;
    paintBar();
    bindComposer();
  }

  function paintBar() {
    const bar = $("chat-bar");
    if (!bar) return;
    const r = chat.row || {};
    const status = STATUS[r.status] ? `<span class="badge status-${esc(r.status)}">${esc(STATUS[r.status])}</span>` : "";
    const own = chat.rowKnown
      ? `<span class="badge ownership-${r.ownership === "attached" ? "attached" : "observed"}"
          title="${r.ownership === "attached" ? "Runs under Quiver: you can send to it" : "Owned by its terminal: read-only here"}">${
          r.ownership === "attached" ? "Attached" : "Observed"}</span>` : "";
    const sub = [harnessLabel(chat.row), r.cwd, r.remote ? r.machine : ""]
      .filter(Boolean).map(esc).join(" · ");
    bar.innerHTML = `
      <a class="icon-button chat-back" href="#/sessions" aria-label="Back to Sessions" title="Back to Sessions">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14.5 6-6 6 6 6"/></svg></a>
      ${r.harness ? `<span class="chat-logo" title="${esc(Fmt.harnessName(r.harness))}">${Fmt.logo(r.harness)}</span>` : ""}
      <div class="chat-titles">
        <h1 class="chat-title">${esc(r.title || r.id || chat.key || "Session")}</h1>
        <div class="chat-sub">${sub}</div>
      </div>
      <div class="chat-badges">${status}${own}</div>
      <span class="chat-conn" id="chat-conn"><span class="dot"></span><span class="caption" id="chat-conn-label"></span></span>
      ${chat.details ? '<button type="button" class="tab" id="chat-details">Details</button>' : ""}`;
    setConn(chat.connState || "connecting");
    const details = $("chat-details");
    if (details) {
      details.onclick = async () => {
        details.disabled = true;
        details.textContent = "Loading…";
        const ok = await chat.details();
        details.disabled = false;
        details.textContent = ok ? "Details" : "No details";
      };
    }
  }

  function setConn(state) {
    chat.connState = state;
    const conn = $("chat-conn");
    if (!conn) return;
    conn.className = `chat-conn ${state === true ? "on" : state === "err" ? "err" : "off"}`;
    $("chat-conn-label").textContent = state === true ? "Live" : state === "err" ? "Reconnecting…" : "Connecting…";
  }

  function showError(message) {
    const card = $("chat-error");
    if (!card) return;
    card.innerHTML = `<p class="error">${esc(message)}</p>`;
    card.classList.remove("hidden");
  }

  /* ------------------------------------------------------------ ownership */

  function updateOwnership() {
    const row = chat.row;
    const input = $("chat-text");
    if (!input) return;
    const observed = !row || row.ownership !== "attached";
    // The composer always renders: on observed sessions it's disabled so
    // it still shows where typing will happen once attached.
    input.disabled = !chat.rowKnown || observed;
    input.placeholder = !chat.rowKnown ? "Loading…"
      : observed ? "Read-only until this session is attached" : "Message the session";
    $("chat-send").disabled = !chat.rowKnown || observed;
    $("chat-stop").disabled = !chat.rowKnown || observed || chat.stopping;
    $("chat-mode").classList.toggle("hidden", observed);
    // attachable means "has resume flags"; the hub also refuses while the
    // session is mid-turn, so the button hides then rather than have every
    // click bounce off the 409.
    const busy = !!row && row.status === "active";
    const canAttach = observed && !!row && !!row.attachable && !busy;
    if (!canAttach) chat.confirmAttach = false;
    paintBanner(chat.rowKnown && observed, canAttach, busy);
    updateWorking();
    paintBar();
  }

  function paintBanner(show, canAttach, busy) {
    const banner = $("chat-banner");
    if (!banner) return;
    banner.classList.toggle("hidden", !show);
    if (!show) { banner.innerHTML = ""; return; }
    const r = chat.row || {};
    const text = canAttach ? "Observed session: read-only until attached."
      : busy ? "Observed session: mid-turn. Attach once it is idle."
        : "Observed session: read-only (this harness can't be attached).";
    if (canAttach && chat.confirmAttach) {
      const where = r.remote && r.machine ? ` on ${r.machine}` : "";
      banner.innerHTML = `<div class="chat-banner-text"><div class="card-title">Attach this session?</div>
          <p class="caption">Attach stops ${esc(harnessLabel(r) || "the harness")} in the terminal running this session${esc(where)},
          then resumes the session under Quiver so you can send to it from here.</p></div>
        <div class="chat-banner-actions">
          <button type="button" class="tab" id="chat-attach-cancel">Cancel</button>
          <button type="button" class="chat-send" id="chat-attach-go"${chat.attaching ? " disabled" : ""}>${chat.attaching ? "Attaching…" : "Stop it and attach"}</button>
        </div>`;
      $("chat-attach-cancel").onclick = () => { chat.confirmAttach = false; updateOwnership(); };
      $("chat-attach-go").onclick = attach;
      return;
    }
    banner.innerHTML = `<div class="chat-banner-text"><p>${esc(text)}</p></div>${canAttach
      ? '<div class="chat-banner-actions"><button type="button" class="chat-send" id="chat-attach">Attach</button></div>' : ""}`;
    const btn = $("chat-attach");
    if (btn) btn.onclick = () => { chat.confirmAttach = true; updateOwnership(); };
  }

  /* --------------------------------------------------------------- events */

  function appendEvent(ev, live = true) {
    const stick = live && nearBottom();
    const d = ev.data || {};
    if (ev.type === "message.appended") {
      appendMessage(d, live, ev.seq);
    } else if (ev.type === "session.status") {
      const status = d.status || "";
      // The turn ended (or stopped to ask) without a message to show.
      if (ev.session === chat.key && status && status !== "active") chat.awaitingReply = false;
      if (chat.row && ev.session === chat.key) {
        chat.row.status = status;
        // An open chat polls nothing: a turn ending here should show the
        // Attach button and reword the banner on its own.
        updateOwnership();
      }
    } else if (ev.type === "session.ownership") {
      if (chat.row && ev.session === chat.key) {
        chat.row.ownership = d.ownership || chat.row.ownership;
        updateOwnership();
      }
    } else if (ev.type === "approval.requested") {
      appendMessage({ kind: "approval", text: d.prompt || "Approval requested", request_id: d.request_id || "" }, live, ev.seq);
    } else if (ev.type === "command.result") {
      commandResult(d);
    } else if (ev.type === "approval.resolved") {
      const rid = d.request_id || "";
      const said = DECISIONS[d.decision];
      for (const card of $("chat-log").querySelectorAll(".msg.approval")) {
        if (!rid || card.dataset.req === rid) {
          card.querySelectorAll(".approval-actions button").forEach((b) => { b.disabled = true; });
          card.classList.add("resolved");
          if (said && !card.querySelector(".approval-done")) {
            card.insertAdjacentHTML("beforeend", `<div class="approval-done caption">${said}</div>`);
          }
        }
      }
    }
    if (stick) scrollBottom();
  }

  /* Sends answer async: settle the optimistic bubble this result belongs
   * to, plus any queued approval card it answers. */
  function commandResult(d) {
    const log = $("chat-log");
    let settled = false;
    if (d.op === "message" && d.client_id) {
      const b = log.querySelector(`.msg.user.pending[data-client-id="${CSS.escape(d.client_id)}"]`);
      if (b) {
        settled = true;
        b.classList.remove("pending");
        const note = b.querySelector(".send-note");
        if (d.ok) {
          // A degraded send still landed: keep the why visible.
          if (d.degraded) {
            // Kept when the transcript echoes the message (appendMessage).
            b.classList.add("degraded");
            if (note) note.textContent = d.note || "Sent (queued: mid-turn input isn't supported here)";
          } else if (note) {
            note.remove();
          }
        } else {
          b.classList.add("failed");
          if (note) note.textContent = d.cancelled ? "Removed before sending" : (d.note || "Send failed");
          chat.awaitingReply = false;
          updateWorking();
        }
      }
    }
    if (d.op === "approval" && d.request_id) {
      for (const card of log.querySelectorAll(".msg.approval")) {
        if (card.dataset.req !== d.request_id || !card.dataset.queued) continue;
        delete card.dataset.queued;
        // The request's own answer may still be on its way: it must not
        // undo what this result settled.
        card.dataset.settled = "1";
        if (d.ok) {
          card.classList.add("resolved");
          const note = card.querySelector(".approval-note");
          if (note) note.remove();
        } else {
          card.querySelectorAll(".approval-actions button").forEach((b) => { b.disabled = false; });
          approvalNote(card).textContent = `Approval failed: ${d.note || "the satellite rejected it"}`;
        }
      }
    }
    if (!settled && (d.ok === false || d.degraded || d.note === "approval_not_supported")) {
      appendMessage({ kind: "system", text: d.ok === false
        ? `${d.op ? d.op[0].toUpperCase() + d.op.slice(1) : "Command"} failed: ${d.note || "unknown error"}`
        : (d.note || "Queued") }, true);
    }
  }

  /* ``seq`` stamps the entry with its journal seq; ``at`` places it among
   * the entries by that seq instead of at the end (a restored approval). */
  function appendMessage(msg, live = true, seq = 0, at = 0) {
    const log = $("chat-log");
    if (!log) return;
    const put = (el) => {
      if (seq) el.dataset.seq = String(seq);
      const next = at ? [...log.children].find((c) => Number(c.dataset.seq || 0) > at) : null;
      log.insertBefore(el, next || null);
    };
    const loading = $("chat-loading");
    if (loading) loading.remove();
    const kind = msg.kind || "assistant";
    if (kind === "tool_result" && msg.result_ref && chat.toolCards.has(msg.result_ref)) {
      const body = chat.toolCards.get(msg.result_ref).querySelector(".tool-body");
      if (body && msg.text) body.insertAdjacentHTML("beforeend", `<div class="caption">Output</div><pre>${esc(msg.text.slice(0, 8000))}</pre>`);
      return;
    }
    if (kind === "tool_use" && msg.tool) {
      const card = document.createElement("details");
      card.className = "tool";
      card.innerHTML = `<summary><span class="tool-name">${esc(msg.tool.name)}</span><span class="tool-args">${esc(msg.tool.args_summary || "")}</span></summary><div class="tool-body"></div>`;
      const diff = toolDiff(msg.tool);
      const body = card.querySelector(".tool-body");
      if (diff) {
        card.classList.add("has-diff");
        body.innerHTML = diff;
      } else if (msg.tool.args_full) {
        body.innerHTML = `<pre>${esc(msg.tool.args_full.slice(0, 8000))}</pre>`;
      }
      if (msg.tool.call_id) chat.toolCards.set(msg.tool.call_id, card);
      if (live) { card.classList.add("arrive"); chat.awaitingReply = false; }
      put(card);
      return;
    }
    // A degraded send's note survives its transcript echo.
    let carried = "";
    if (kind === "user" && live) {
      // A queued send's real bubble lands via message.appended: swap it for
      // the optimistic one already drawn (attachments prefix the stored text
      // with "@path ", hence the endsWith). Backfill never dedupes: history
      // replay must not erase a queued bubble.
      const hit = [...log.querySelectorAll(".msg.user[data-client-id]:not(.failed)")].find((b) => {
        const t = b.dataset.text;
        if (!t || (msg.text !== t && !(msg.text || "").endsWith(t))) return false;
        const sentAt = Number(b.dataset.sentAt || 0);
        return !msg.ts || !sentAt || msg.ts >= sentAt - 5;
      });
      if (hit && hit === log.lastElementChild) {
        // Still the newest entry: settle the bubble where it is, so it doesn't
        // leave and come back in. Dropping .pending also tells a late POST
        // reply or command.result it's already delivered.
        hit.classList.remove("pending");
        delete hit.dataset.clientId;
        const note = hit.querySelector(".send-note");
        if (note && !hit.classList.contains("degraded")) note.remove();
        hit.querySelector(".text").innerHTML = md(msg.text);
        return;
      }
      if (hit) {
        const note = hit.querySelector(".send-note");
        if (note && hit.classList.contains("degraded")) carried = note.textContent;
        hit.remove();
      }
    }
    if (live && kind !== "user" && kind !== "tool_result") chat.awaitingReply = false;
    if (kind === "tool_result") {
      // Output whose call isn't on screen: a closed card of its own.
      const card = document.createElement("details");
      card.className = "tool";
      card.innerHTML = `<summary><span class="tool-name">Tool output</span></summary><div class="tool-body"><pre>${esc((msg.text || "").slice(0, 8000))}</pre></div>`;
      if (live) card.classList.add("arrive");
      put(card);
      return;
    }
    const div = document.createElement("div");
    div.className = `msg ${kind}`;
    if (kind === "compaction") {
      div.innerHTML = '<span class="text">Context compacted</span>';
    } else if (kind === "system") {
      div.innerHTML = `<span class="text">${esc(msg.text || "")}</span>`;
    } else if (kind === "thinking") {
      div.innerHTML = `<details><summary>Thought</summary><div class="text">${md(msg.text)}</div></details>`;
    } else if (kind === "approval") {
      approvalCard(div, msg);
    } else if (kind === "error") {
      div.innerHTML = `<div class="role">Error</div><div class="text">${md(msg.text)}</div>`;
    } else if (kind === "user" || kind === "assistant") {
      div.innerHTML = `<div class="text">${md(msg.text)}</div>`;
      if (carried) {
        div.classList.add("degraded");
        div.insertAdjacentHTML("beforeend", `<div class="send-note">${esc(carried)}</div>`);
      }
    } else {
      div.innerHTML = `<div class="role">${esc(kind)}</div><div class="text">${md(msg.text)}</div>`;
    }
    if (live) div.classList.add("arrive");
    put(div);
  }

  function approvalCard(div, msg) {
    div.dataset.req = msg.request_id || "";
    div.innerHTML = `<div class="role">Approval needed</div><div class="text">${md(msg.text)}</div>${msg.request_id
      ? `<div class="approval-actions">
           <button type="button" class="tab" data-dec="allow">Allow</button>
           <button type="button" class="tab" data-dec="always">Always</button>
           <button type="button" class="tab deny" data-dec="deny">Deny</button>
         </div>` : ""}`;
    const buttons = () => div.querySelectorAll(".approval-actions button");
    for (const btn of buttons()) {
      btn.onclick = async () => {
        const gen = chat.gen;
        buttons().forEach((b) => { b.disabled = true; });
        // Waiting from before the request: a satellite's command.result
        // can land before the hub's 202 does, and must find the card.
        div.dataset.queued = "1";
        delete div.dataset.settled;
        const { status, data } = await post(sessionPath(chat.key, "approval"),
          { decision: btn.dataset.dec, request_id: msg.request_id });
        // The chat moved on, or an approval.resolved or command.result that
        // raced the answer already settled the card: theirs wins.
        if (gen !== chat.gen || div.classList.contains("resolved") || div.dataset.settled) return;
        const note = approvalNote(div);
        if (status === 202) {
          // The hub queued it for a satellite; the card stays pending until
          // a command.result or approval.resolved event closes it.
          note.classList.add("queued");
          note.textContent = "Queued: waiting for the satellite";
          return;
        }
        delete div.dataset.queued;
        if (status >= 400 || status === 0) {
          buttons().forEach((b) => { b.disabled = false; });
          note.textContent = status === 0 ? "Request failed: connection lost"
            : `Approval failed: ${errorText(data) || "the prompt may have closed"}`;
          noteRefusal(data);
        } else {
          note.remove();
          div.classList.add("resolved");
        }
      };
    }
  }

  function approvalNote(card) {
    let note = card.querySelector(".approval-note");
    if (!note) {
      note = document.createElement("div");
      card.appendChild(note);
    }
    note.className = "approval-note";
    return note;
  }

  /* ---------------------------------------------------------- live pacing
     Live events reveal one at a time with a short gap that shrinks as more
     wait; a replay after reconnecting (more than 24) lands at once. */

  function revealDelay(waiting) {
    if (waiting <= 0 || waiting > 24) return null;
    return Math.min(180, 1200 / waiting);
  }

  function enqueue(ev) {
    chat.pending.push(ev);
    updateWorking();
    if (chat.revealTimer !== null) return;
    // Frames often arrive in separate reads a few ms apart: the queue
    // empties between them, so keep the gap from the last entry shown.
    const gap = (revealDelay(chat.pending.length) || 0) - (performance.now() - chat.lastRevealAt);
    if (gap > 0) chat.revealTimer = setTimeout(revealNext, gap);
    else revealNext();
  }

  function revealNext() {
    chat.revealTimer = null;
    if (!chat.pending.length) { updateWorking(); return; }
    const delay = revealDelay(chat.pending.length);
    if (delay === null) { flushPending(); return; }
    const log = $("chat-log");
    const before = log ? log.childElementCount : 0;
    showLive(chat.pending.shift());
    updateWorking();
    // Only a new entry earns a pause; a status change or a result landing
    // on its card goes straight through.
    const added = log ? log.childElementCount > before : false;
    if (added) chat.lastRevealAt = performance.now();
    if (chat.pending.length) chat.revealTimer = setTimeout(revealNext, added ? delay : 0);
  }

  function flushPending() {
    clearTimeout(chat.revealTimer);
    chat.revealTimer = null;
    const all = chat.pending;
    chat.pending = [];
    for (const ev of all) showLive(ev);
    updateWorking();
  }

  function showLive(ev) {
    appendEvent(ev);
    remember(ev.seq);
  }

  function dropPending() {
    clearTimeout(chat.revealTimer);
    chat.revealTimer = null;
    chat.pending = [];
  }

  function remember(seq) {
    if (typeof seq === "number" && seq > chat.seq) chat.seq = seq;
  }

  /* Three pulsing dots under the last message while the agent is mid-turn,
   * a send waits for its reply, or messages are still being revealed. */
  function updateWorking() {
    const dots = $("chat-working");
    if (!dots) return;
    const on = !!chat.key && ((chat.row && chat.row.status === "active") || chat.awaitingReply || chat.pending.length > 0);
    if (dots.classList.contains("hidden") === !on) return;
    const stick = nearBottom();
    dots.classList.toggle("hidden", !on);
    if (on && stick) scrollBottom();
  }

  /* --------------------------------------------------------------- stream */

  function openStream(gen, after) {
    closeStream();
    const key = chat.key;
    const ctrl = new AbortController();
    const mine = { ctrl };
    chat.stream = mine;
    setConn("connecting");
    fetch(`${sessionPath(key, "events")}?last=${after}`, {
      headers: authHeaders(), credentials: "same-origin", cache: "no-store", signal: ctrl.signal,
    }).then(async (resp) => {
      if (chat.stream !== mine) return;
      if (resp.status === 401) {
        chat.stream = null;
        close();
        renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
        return;
      }
      if (resp.status === 404) {
        chat.stream = null;
        setConn("err");
        showError("This session isn't in the hub's catalog any more.");
        return;
      }
      if (!resp.ok || !resp.body) throw new Error(`sse ${resp.status}`);
      setConn(true);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (chat.stream !== mine) return;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          handleFrame(key, frame);
        }
      }
      throw new Error("stream closed");
    }).catch(() => {
      // Only the live stream reconnects: a closed or replaced one is done.
      if (chat.stream !== mine) return;
      chat.stream = null;
      setConn("err");
      // Reconnect with replay from the last seq shown.
      clearTimeout(chat.retryTimer);
      chat.retryTimer = setTimeout(() => {
        if (gen === chat.gen && !chat.stream) openStream(gen, resumeSeq());
      }, 2000);
    });
  }

  function handleFrame(key, frame) {
    const ev = parseFrame(frame);
    if (!ev || key !== chat.key) return;
    const h = chat.history;
    if (h) {
      // Replaying what came before the backfill's last message, only to
      // find the approval the hub says is still waiting.
      if (ev.type === "journal.caught-up" || (typeof ev.seq === "number" && ev.seq > h.until)) {
        restoreApproval();
      } else {
        const d = ev.data || {};
        if (ev.type === "approval.requested" && d.request_id === h.rid) Object.assign(h, { prompt: d.prompt, seq: ev.seq, resolved: false });
        if (ev.type === "approval.resolved" && (!d.request_id || d.request_id === h.rid)) h.resolved = true;
        return;
      }
    }
    if (ev.type === "journal.truncated" && chat.seq) {
      // The hub's ring dropped events past where this view stopped (a long
      // disconnect on a busy session): say so rather than look complete.
      const stick = nearBottom();
      appendMessage({ kind: "system", text: "Some events were missed while the connection was down, and the hub's journal no longer has them." }, true);
      if (stick) scrollBottom();
    }
    if (ev.type === "journal.truncated" || ev.type === "journal.caught-up") {
      if (ev.seq && !chat.pending.length) remember(ev.seq);
      return;
    }
    // The cursor advances as each event is shown (showLive), so events
    // still waiting when the stream drops are replayed on reconnect.
    enqueue(ev);
  }

  function closeStream() {
    dropPending();
    clearTimeout(chat.retryTimer);
    chat.retryTimer = null;
    const s = chat.stream;
    chat.stream = null;
    if (s) { try { s.ctrl.abort(); } catch { /* already done */ } }
  }

  /* An approval the hub still waits on, asked before the last message the
   * backfill showed (codex's app-server journals the ask before the tool
   * call it belongs to): its card goes back where it was asked. */
  function restoreApproval() {
    const h = chat.history;
    chat.history = null;
    const log = $("chat-log");
    // Not found before the backfill's last message: asked after it, so the
    // stream's live part brings it like any other.
    if (!h || !h.seq || h.resolved || !log || log.querySelector(`.msg.approval[data-req="${CSS.escape(h.rid)}"]`)) return;
    const stick = nearBottom();
    appendMessage({ kind: "approval", text: h.prompt || "Approval requested", request_id: h.rid }, false, h.seq, h.seq);
    if (stick) scrollBottom();
  }

  /* Where a reconnect picks up: the last seq shown, or the start again
   * while the search for a waiting approval hasn't finished. */
  function resumeSeq() {
    return chat.history ? 0 : chat.seq;
  }

  /* The request_id of this session's approval the hub still waits on
   * (the Inbox lists every pending one), or "". */
  async function pendingApproval(gen) {
    const key = chat.key;
    const { status, data } = await api("/v1/inbox");
    if (gen !== chat.gen || status !== 200 || !data) return "";
    const item = (data.items || []).find((i) => i.key === key && i.status === "approval" && i.request_id);
    return item ? String(item.request_id) : "";
  }

  /* -------------------------------------------------------------- opening */

  /* The catalog row: ownership, attachable and status as the hub has them
   * now, not as the list had them when it loaded. */
  async function loadRow(gen) {
    const { status, data } = await api("/v1/sessions");
    if (gen !== chat.gen) return;
    if (status === 401) return status;
    if (status !== 200 || !data) {
      showError(errorText(data) || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then reload."));
      return status;
    }
    const row = (data.sessions || []).find((r) => r.key === chat.key);
    if (!row) {
      showError("This session isn't in the hub's catalog. It may have ended, or its machine may be offline.");
      return 404;
    }
    const machine = (data.machines || []).find((m) => m.id === row.machine_id);
    chat.row = Object.assign({}, chat.row || {}, row, machine && machine.name ? { machine: machine.name } : {});
    chat.rowKnown = true;
    updateOwnership();
    return 200;
  }

  async function backfill(gen) {
    const { status, data } = await api(`${sessionPath(chat.key, "messages")}?limit=500`);
    if (gen !== chat.gen) return status;
    const loading = $("chat-loading");
    if (status !== 200 || !data) {
      if (loading) loading.outerHTML = `<p class="empty" id="chat-loading">${status === 404 ? "No transcript for this session." : "Couldn't load the transcript."}</p>`;
      return status;
    }
    const messages = data.messages || [];
    for (const ev of messages) appendEvent(ev, false);
    if (messages.length) remember(messages[messages.length - 1].seq);
    else if (loading) loading.outerHTML = '<p class="empty" id="chat-loading">No messages yet.</p>';
    scrollBottom();
    return status;
  }

  async function open(key, opts = {}) {
    close();
    const gen = ++chat.gen;
    chat.key = key;
    chat.details = opts.details || null;
    chat.row = opts.row ? Object.assign({}, opts.row) : null;
    chat.rowKnown = false;
    chat.awaitingReply = false;
    chat.seq = 0;
    chat.history = null;
    // Set once the opening reads are done and the stream is started; a
    // reconnect before that would replay what the backfill is drawing.
    chat.opened = false;
    chat.toolCards.clear();
    chat.confirmAttach = false;
    chat.attaching = false;
    chat.stopping = false;
    chat.connState = "connecting";
    paintShell();
    updateOwnership();
    // The catalog is read after the backfill, never before: an attach or a
    // status change journaled before the backfill's last message is then
    // already in the row, and one after it comes with the stream.
    const logStatus = await backfill(gen);
    if (gen !== chat.gen) return;
    const [rowStatus, waiting] = logStatus === 401 ? [401, ""]
      : await Promise.all([loadRow(gen), pendingApproval(gen)]);
    if (gen !== chat.gen) return;
    if (rowStatus === 401 || logStatus === 401) {
      close();
      renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
      return;
    }
    if (rowStatus === 404) { setConn("err"); return; }
    // The stream starts after the last message backfill showed, so an
    // approval asked or a result reported since is replayed into view. A
    // waiting approval may be older than that message: then the stream
    // replays from the start, and only that ask is taken from the part
    // the backfill already showed.
    chat.opened = true;
    const shown = waiting && $("chat-log").querySelector(`.msg.approval[data-req="${CSS.escape(waiting)}"]`);
    if (waiting && !shown && chat.seq) {
      chat.history = { until: chat.seq, rid: waiting, prompt: "", seq: 0, resolved: false };
      openStream(gen, 0);
    } else {
      openStream(gen, chat.seq);
    }
  }

  function close() {
    chat.gen++;
    closeStream();
    chat.key = null;
    chat.row = null;
    chat.toolCards.clear();
  }

  /* ------------------------------------------------------------- composer */

  function bindComposer() {
    const form = $("chat-composer");
    const input = $("chat-text");
    const grow = () => {
      input.style.height = "auto";
      input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
    };
    input.oninput = grow;
    input.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        form.requestSubmit();
      }
    };
    form.onsubmit = (e) => { e.preventDefault(); send(grow); };
    $("chat-stop").onclick = interrupt;
  }

  function appendOptimistic(text, clientId) {
    const loading = $("chat-loading");
    if (loading) loading.remove();
    const div = document.createElement("div");
    div.className = "msg user pending arrive";
    div.dataset.clientId = clientId;
    div.dataset.text = text;
    div.dataset.sentAt = String(Math.floor(Date.now() / 1000));
    div.innerHTML = `<div class="text">${md(text)}</div><div class="send-note">Sending…</div>`;
    $("chat-log").appendChild(div);
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

  async function send(grow) {
    const input = $("chat-text");
    const text = input.value.trim();
    if (!text || !chat.key || input.disabled) return;
    input.value = "";
    grow();
    const mode = $("chat-mode").value;
    const clientId = newClientId();
    const bubble = appendOptimistic(text, clientId);
    const gen = chat.gen, key = chat.key;
    chat.awaitingReply = true;
    updateWorking();
    const { status, data } = await post(sessionPath(key, "message"), { text, mode, client_id: clientId });
    // A fast command.result may already have settled this bubble: .pending
    // is gone then, and the response must not clobber it.
    if (!bubble.classList.contains("pending")) return;
    if (status === 202) {
      // The send itself resolves later as command.result; the note says
      // where it stands until then.
      const note = bubble.querySelector(".send-note");
      if (note) note.textContent = data && data.waiting ? "Queued until the agent is idle" : "Sent";
      return;
    }
    // Another chat may be open by now; its dots aren't ours to clear.
    if (gen === chat.gen) {
      chat.awaitingReply = false;
      updateWorking();
      noteRefusal(data);
    }
    failBubble(bubble, errorText(data) || (status ? `Send failed (${status})` : "Request failed: connection lost"));
  }

  async function interrupt() {
    if (!chat.key || chat.stopping) return;
    const gen = chat.gen;
    chat.stopping = true;
    updateOwnership();
    const { status, data } = await post(sessionPath(chat.key, "control"), { op: "interrupt" });
    if (gen !== chat.gen) return;
    chat.stopping = false;
    updateOwnership();
    if (status !== 200 && status !== 202) {
      appendMessage({ kind: "error", text: `Stop failed: ${errorText(data) || (status ? `the hub answered ${status}` : "connection lost")}` }, true);
      noteRefusal(data);
      scrollBottom();
    }
  }

  async function attach() {
    if (!chat.key || chat.attaching) return;
    const gen = chat.gen;
    chat.attaching = true;
    updateOwnership();
    const { status, data } = await post(sessionPath(chat.key, "control"), { op: "attach" });
    if (gen !== chat.gen) return;
    chat.attaching = false;
    chat.confirmAttach = false;
    if (status === 200) {
      // The hub also emits session.ownership; its answer is enough to
      // open the composer now.
      chat.row.ownership = "attached";
    } else if (status === 202) {
      // The attach went to a satellite's command journal: ownership
      // changes when its command.result lands.
      appendMessage({ kind: "system", text: "Attach queued: waiting for the satellite" }, true);
    } else {
      appendMessage({ kind: "error", text: `Attach failed: ${errorText(data) || (status ? `the hub answered ${status}` : "connection lost")}` }, true);
    }
    updateOwnership();
    scrollBottom();
  }

  /* Leaving the chat by any route closes its stream, so it never holds
   * one of the browser's few connections to the hub. */
  window.addEventListener("hashchange", () => {
    if (chat.key && keyFromHash() !== chat.key) close();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && chat.key && chat.opened && !chat.stream) {
      openStream(chat.gen, resumeSeq());
    }
  });

  return {
    open, close, keyFromHash,
    get key() { return chat.key; },
    // Pure helpers, for tests.
    _test: { md, diffLines, renderDiff, renderPatch, toolDiff, parseFrame, revealDelay },
  };
})();

globalThis.SessionChat = SessionChat;
