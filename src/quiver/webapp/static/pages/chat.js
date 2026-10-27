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
    // A detach mode=when_idle the hub still holds: it fires when the
    // turn ends, or an attach cancels it.
    detaching: false,
    // The session rail (the Mac app's SessionRail): its own load state,
    // and a copy of the list's rows for when sessions.js's page state
    // isn't there (a chat opened from a link, the list never visited).
    rail: { loading: false, error: "" }, railData: null,
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

  /* The phone's icon per message kind (app/ios's ToolCard.icon), as
   * 24px stroke svg in the bar chevron's style. */
  const KIND_ICONS = {
    terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
    pencil: '<path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/><path d="m15 5 4 4"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.4-4.4"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.3 4 5.4 4 9s-1.4 6.7-4 9c-2.6-2.3-4-5.4-4-9s1.4-6.7 4-9z"/>',
    people: '<circle cx="9" cy="7.5" r="3.5"/><path d="M3 20.5c.5-3.5 3-5.5 6-5.5s5.5 2 6 5.5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.7 15.3c2.7.6 4.6 2.6 4.8 5.2"/>',
    wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-8 8l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 8-8z"/>',
    thought: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5a6 6 0 0 0-12 0c0 1.3.5 2.6 1.5 3.5.8.8 1.3 1.5 1.5 2.5"/><path d="M9 18h6"/><path d="M10 22h4"/>',
    warn: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  };
  const TOOL_ICONS = {
    bash: "terminal", shell: "terminal", exec: "terminal", exec_command: "terminal", run_shell_command: "terminal",
    read: "doc", view: "doc", read_file: "doc",
    edit: "pencil", multiedit: "pencil", write: "pencil", apply_patch: "pencil", patch: "pencil",
    file_edit: "pencil", notebookedit: "pencil",
    grep: "search", glob: "search", search: "search", find: "search",
    webfetch: "globe", websearch: "globe", fetch: "globe",
    task: "people", agent: "people",
  };
  function iconSvg(kind) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${KIND_ICONS[kind]}</svg>`;
  }
  function toolIcon(name) {
    return iconSvg(TOOL_ICONS[String(name || "").toLowerCase()] || "wrench");
  }

  /* mcp__server__tool reads as "server · tool", the way the phone shows
   * an MCP tool; other names pass through. */
  function toolDisplayName(name) {
    name = String(name || "");
    if (!name.startsWith("mcp__")) return name;
    const parts = name.slice(5).split("__").filter(Boolean);
    return parts.length >= 2 ? `${parts[0]} · ${parts.slice(1).join("__")}` : (parts[0] || name);
  }

  /* The hub's summary leads with the tool's name ("Bash · make test"),
   * which the card already shows. */
  function toolArgsSummary(tool) {
    const s = String(tool.args_summary || "");
    const lead = `${tool.name || ""} · `;
    return s.startsWith(lead) ? s.slice(lead.length) : s;
  }

  /* Three or more tool cards in a row fold into one "Used N tools" card
   * (the phone's ToolRunCard rule); tools arriving later join the run at
   * the end. */
  function toolRunNames(items) {
    const counts = new Map(), order = [];
    for (const el of items) {
      const name = (el.querySelector(".tool-name") || {}).textContent || "Tool";
      if (!counts.has(name)) order.push(name);
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    return order.sort((a, b) => counts.get(b) - counts.get(a))
      .map((n) => (counts.get(n) > 1 ? `${n} ×${counts.get(n)}` : n)).join(", ");
  }

  function paintToolRun(run) {
    const items = run.querySelector(".tool-run-items").children;
    run.querySelector(".tool-run-count").textContent = `Used ${items.length} tools`;
    run.querySelector(".tool-run-names").textContent = toolRunNames(items);
  }

  function groupTool(el) {
    const prev = el.previousElementSibling;
    if (prev && prev.classList.contains("tool-run")) {
      prev.querySelector(".tool-run-items").appendChild(el);
      paintToolRun(prev);
      return;
    }
    const run = [el];
    for (let n = prev; n && n.matches("details.tool"); n = n.previousElementSibling) run.unshift(n);
    if (run.length < 3) return;
    const box = document.createElement("details");
    box.className = "tool-run";
    box.innerHTML = `<summary><span class="tool-ic">${iconSvg("wrench")}</span><span class="tool-run-count"></span><span class="tool-run-names"></span></summary><div class="tool-run-items"></div>`;
    el.parentNode.insertBefore(box, run[0]);
    const items = box.querySelector(".tool-run-items");
    for (const it of run) items.appendChild(it);
    paintToolRun(box);
  }

  /* An entry seq-dated inside a folded run (a restored approval asked
   * between two calls) splits it; each side keeps its fold only if it
   * still has three tools in a row. */
  function splitToolRun(run, beforeEl, el) {
    const items = [...run.querySelector(".tool-run-items").children];
    const cut = items.indexOf(beforeEl);
    const parent = run.parentNode, ref = run.nextSibling;
    if (cut === 0) { parent.insertBefore(el, run); return; }
    run.remove();
    for (const it of items.slice(0, cut)) parent.insertBefore(it, ref);
    parent.insertBefore(el, ref);
    for (const it of items.slice(cut)) parent.insertBefore(it, ref);
    for (const it of items) groupTool(it);
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
      <div class="chat-dash">
      <div class="chat-main">
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
      <aside class="chat-rail" id="chat-rail" aria-label="Sessions"></aside>
      </div>
    </div>
    <dialog class="sheet" id="session-sheet"></dialog>`;
    paintBar();
    paintRail();
    bindComposer();
  }

  function paintBar() {
    const bar = $("chat-bar");
    if (!bar) return;
    const r = chat.row || {};
    const status = STATUS[r.status] ? `<span class="badge status-${esc(r.status)}">${esc(STATUS[r.status])}</span>` : "";
    // Ownership needs no chip: an observed session says so in the banner
    // below, and an attached one shows an enabled composer.
    const sub = [harnessLabel(chat.row), r.cwd, r.remote ? r.machine : ""]
      .filter(Boolean).map(esc).join(" · ");
    // The tmux pane name rides the catalog row for an attached tmux-mode
    // session and for a kept one; the chip copies its attach command.
    const tmuxChip = r.tmux
      ? `<button type="button" class="tmux-chip" id="chat-tmux" title="Copy: tmux attach -t ${esc(r.tmux)}">${esc(r.tmux)}${Fmt.copyIcon()}</button>` : "";
    bar.innerHTML = `
      <a class="icon-button chat-back" href="#/sessions" aria-label="Back to Sessions" title="Back to Sessions">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m14.5 6-6 6 6 6"/></svg></a>
      ${r.harness ? `<span class="chat-logo" title="${esc(Fmt.harnessName(r.harness))}">${Fmt.logo(r.harness)}</span>` : ""}
      <div class="chat-titles">
        <h1 class="chat-title">${esc(r.title || r.id || chat.key || "Session")}</h1>
        <div class="chat-sub">${sub}</div>
      </div>
      <div class="chat-badges">${status}${tmuxChip}</div>
      <span class="chat-conn" id="chat-conn"><span class="dot"></span><span class="caption" id="chat-conn-label"></span></span>
      ${r.ownership === "attached" ? `<button type="button" class="icon-button" id="chat-detach" aria-label="Detach" title="Detach, or keep it running in tmux">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg></button>` : ""}
      ${chat.details ? `<button type="button" class="icon-button" id="chat-details" aria-label="Details" title="Details">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg></button>` : ""}`;
    setConn(chat.connState || "connecting");
    const chip = $("chat-tmux");
    if (chip) Fmt.copyTick(chip, () => `tmux attach -t ${(chat.row || {}).tmux}`);
    const detachBtn = $("chat-detach");
    if (detachBtn) detachBtn.onclick = openDetachSheet;
    const details = $("chat-details");
    if (details) {
      details.onclick = async () => {
        details.disabled = true;
        details.title = "Loading…";
        const ok = await chat.details();
        details.disabled = false;
        details.title = "Details";
        if (!ok) showError("The session's details could not be loaded.");
      };
    }
  }

  function setConn(state) {
    chat.connState = state;
    const conn = $("chat-conn");
    if (!conn) return;
    conn.className = `chat-conn ${state === true ? "on" : state === "err" ? "err" : "off"}`;
    conn.title = state === true ? "Streaming the session live"
      : state === "err" ? "Reconnecting to the session stream" : "Connecting to the session stream";
    $("chat-conn-label").textContent = state === true ? "" : state === "err" ? "Reconnecting…" : "Connecting…";
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
    // click bounce off the 409. A kept tmux pane is the exception: attach
    // adopts it as it is, so mid-turn is fine.
    const busy = !!row && row.status === "active";
    const kept = !!row && !!row.kept;
    const canAttach = observed && !!row && (!!row.attachable || kept) && (!busy || kept);
    if (!canAttach) chat.confirmAttach = false;
    paintBanner(chat.rowKnown && (observed || chat.detaching), canAttach, busy);
    updateWorking();
    paintBar();
    paintRailRows();
  }

  function paintBanner(show, canAttach, busy) {
    const banner = $("chat-banner");
    if (!banner) return;
    banner.classList.toggle("hidden", !show);
    if (!show) { banner.innerHTML = ""; return; }
    const r = chat.row || {};
    const kept = !!r.kept;
    // A detach that waits out the turn: say it's pending, with a way to
    // keep the session attached instead.
    if (chat.detaching && r.ownership === "attached") {
      banner.innerHTML = `<div class="chat-banner-text"><p>Detaches when this turn ends.</p></div>
        <div class="chat-banner-actions"><button type="button" class="tab" id="chat-detach-cancel">Stay attached</button></div>`;
      $("chat-detach-cancel").onclick = cancelDetach;
      return;
    }
    const text = kept ? `Still running in tmux${r.tmux ? ` as ${r.tmux}` : ""}: read-only here, but the pane is alive for a terminal.`
      : canAttach ? "Observed session: read-only until attached."
      : busy ? "Observed session: mid-turn. Attach once it is idle."
        : "Observed session: read-only (this harness can't be attached).";
    if (canAttach && chat.confirmAttach) {
      const where = r.remote && r.machine ? ` on ${r.machine}` : "";
      const [title, note, go] = kept
        ? ["Reattach to the running tmux session?",
          `The pane ${r.tmux || ""} is still alive${esc(where)}: attach takes it back as it is, mid-turn included. Nothing is stopped or relaunched.`,
          "Reattach"]
        : ["Attach this session?",
          `Attach stops ${esc(harnessLabel(r) || "the harness")} in the terminal running this session${esc(where)},
          then resumes the session under Quiver so you can send to it from here.`,
          "Stop it and attach"];
      banner.innerHTML = `<div class="chat-banner-text"><div class="card-title">${esc(title)}</div>
          <p class="caption">${note}</p></div>
        <div class="chat-banner-actions">
          <button type="button" class="tab" id="chat-attach-cancel">Cancel</button>
          <button type="button" class="chat-send" id="chat-attach-go"${chat.attaching ? " disabled" : ""}>${chat.attaching ? "Attaching…" : esc(go)}</button>
        </div>`;
      $("chat-attach-cancel").onclick = () => { chat.confirmAttach = false; updateOwnership(); };
      $("chat-attach-go").onclick = attach;
      return;
    }
    banner.innerHTML = `<div class="chat-banner-text"><p>${esc(text)}</p></div>${canAttach
      ? `<div class="chat-banner-actions"><button type="button" class="chat-send" id="chat-attach">${kept ? "Reattach" : "Attach"}</button></div>` : ""}`;
    const btn = $("chat-attach");
    if (btn) btn.onclick = () => { chat.confirmAttach = true; updateOwnership(); };
  }

  /* ------------------------------------------------------------- detach */

  /* The lifecycle the hub supports for an attached session: the phone's
   * detach sheet, with "keep" only where a tmux pane exists to keep. */
  function openDetachSheet() {
    const sheet = $("session-sheet");
    const r = chat.row || {};
    if (!sheet || r.ownership !== "attached") return;
    const tmux = r.mode === "tmux" && !!r.tmux;
    const busy = r.status === "active";
    const opt = (mode, name, desc, danger = false) =>
      `<button type="button" class="detach-opt${danger ? " danger" : ""}" data-detach="${mode}">
        <span class="opt-name">${name}</span><span class="opt-desc">${desc}</span></button>`;
    sheet.innerHTML = `<form method="dialog" class="sheet-body">
      <div class="sheet-title">Detach this session?</div>
      <p class="caption" style="margin:0">Detaching stops Quiver's control of it;
        the transcript stays readable here either way.</p>
      <div class="detach-options">
        ${opt("when_idle", "Detach when it finishes",
          busy ? "Waits out the current turn, then detaches." : "Detaches as soon as the hub next checks.")}
        ${tmux ? opt("keep", "Keep it running in tmux",
          `Leaves <code>${esc(r.tmux)}</code> alive for <code>tmux attach -t ${esc(r.tmux)}</code>. Auto-closed after 12h idle.`) : ""}
        ${opt("now", "Detach now",
          busy ? "Detaches immediately, stopping the turn." : "Detaches immediately.", true)}
      </div>
      <div class="sheet-actions"><button class="tab selected" value="cancel">Cancel</button></div>
    </form>`;
    for (const b of sheet.querySelectorAll("[data-detach]")) {
      b.onclick = () => { sheet.close(); detach(b.dataset.detach); };
    }
    sheet.showModal();
  }

  async function detach(mode) {
    if (!chat.key) return;
    const gen = chat.gen;
    const { status, data } = await post(sessionPath(chat.key, "control"), { op: "detach", mode });
    if (gen !== chat.gen) return;
    if (status === 200 || status === 202) {
      if (mode === "when_idle") {
        // The hub detaches when the turn ends (or at once when idle):
        // the ownership event, or a detach command.result, closes it.
        chat.detaching = true;
      } else if (status === 200 && chat.row) {
        chat.detaching = false;
        chat.row.ownership = "observed";
        if (mode === "keep") chat.row.kept = true;
        else { delete chat.row.kept; delete chat.row.tmux; }
        delete chat.row.mode;
      }
      updateOwnership();
      return;
    }
    appendMessage({ kind: "error", text: `Detach failed: ${errorText(data) || (status ? `the hub answered ${status}` : "connection lost")}` }, true);
    noteRefusal(data);
    scrollBottom();
  }

  /* A pending when_idle detach is cancelled by attaching again; one that
   * already fired re-attaches, which is what Stay attached means either
   * way. */
  async function cancelDetach() {
    if (!chat.key) return;
    const { status } = await post(sessionPath(chat.key, "control"), { op: "attach" });
    if (status === 200 || status === 202) {
      chat.detaching = false;
      updateOwnership();
    }
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
        // Newer hubs carry the supervised fields so the chip and banner
        // repaint right; absent keys mean an older hub — leave them.
        if ("tmux" in d) { if (d.tmux) chat.row.tmux = d.tmux; else delete chat.row.tmux; }
        if ("mode" in d) { if (d.mode) chat.row.mode = d.mode; else delete chat.row.mode; }
        if ("kept" in d) chat.row.kept = !!d.kept;
        if (chat.row.ownership !== "attached") chat.detaching = false;
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
    // Any reported detach outcome ends the pending banner: a fired one
    // (the when_idle firing as the turn ended, or another client's) flips
    // ownership; an expired or cancelled one leaves the session attached
    // with nothing pending.
    if (d.op === "detach") {
      chat.detaching = false;
      if (d.ok === true && chat.row && chat.row.ownership === "attached") {
        chat.row.ownership = "observed";
      }
      updateOwnership();
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
      if (!at) { log.appendChild(el); return; }
      for (const c of log.children) {
        if (Number(c.dataset.seq || 0) > at) { log.insertBefore(el, c); return; }
        // A folded run has no seq of its own: the point may fall inside
        // it, where the entry belongs between the run's tools.
        if (c.classList.contains("tool-run")) {
          for (const it of c.querySelector(".tool-run-items").children) {
            if (Number(it.dataset.seq || 0) > at) { splitToolRun(c, it, el); return; }
          }
        }
      }
      log.appendChild(el);
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
      card.innerHTML = `<summary><span class="tool-ic">${toolIcon(msg.tool.name)}</span><span class="tool-name">${esc(toolDisplayName(msg.tool.name))}</span><span class="tool-args">${esc(toolArgsSummary(msg.tool))}</span></summary><div class="tool-body"></div>`;
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
      groupTool(card);
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
      card.innerHTML = `<summary><span class="tool-ic">${toolIcon("output")}</span><span class="tool-name">Tool output</span></summary><div class="tool-body"><pre>${esc((msg.text || "").slice(0, 8000))}</pre></div>`;
      if (live) card.classList.add("arrive");
      put(card);
      groupTool(card);
      return;
    }
    const div = document.createElement("div");
    div.className = `msg ${kind}`;
    if (kind === "compaction") {
      div.innerHTML = '<span class="text">Context compacted</span>';
    } else if (kind === "system") {
      div.innerHTML = `<span class="text">${esc(msg.text || "")}</span>`;
    } else if (kind === "thinking") {
      div.innerHTML = `<details><summary><span class="tool-ic">${iconSvg("thought")}</span>Thought</summary><div class="text">${md(msg.text)}</div></details>`;
    } else if (kind === "approval") {
      approvalCard(div, msg);
    } else if (kind === "error") {
      div.innerHTML = `<div class="role"><span class="tool-ic">${iconSvg("warn")}</span>Error</div><div class="text">${md(msg.text)}</div>`;
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

  /* ----------------------------------------------------------------- rail
     The Mac app's SessionRail (SessionsPage.swift): the sessions the page
     lists, grouped by day the same way, so a chat can switch sessions
     without going back to the list. Rows come from the page's own data
     when it has loaded, else one fetch kept in the page's state; the
     open session stays listed even when that data predates it. */

  /* sessions.js's page state, guarded so the script stands alone. */
  const railPage = () => (typeof sessionsPage !== "undefined" ? sessionsPage : null);

  /* The page's sessions array, or the rail's own copy of an answer. */
  function railSessions() {
    const page = railPage();
    const d = (page && page.data) || chat.railData;
    return d && Array.isArray(d.sessions) ? d.sessions : null;
  }

  /* The list's day bucket (sessions.js's localDay): the local calendar
   * day an epoch falls on. */
  function railDay(epoch) {
    if (typeof localDay === "function") return localDay(epoch);
    const d = new Date(epoch * 1000);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  /* The list's badge colours as a rail dot (styles.css's .badge.status-*,
   * plus the chat bar's status-approval); nothing for a finished one. */
  const RAIL_DOT = { active: "status-active", error: "status-error",
    followup: "status-followup", interrupted: "status-interrupted", approval: "status-approval" };

  /* The rail's rows: every session the page lists, its own filters left
   * to the page. The open one merges in what the stream last said, and
   * joins when the data predates it or the filters would leave it out. */
  function railRows() {
    const rows = (railSessions() || []).slice()
      .sort((a, b) => (b.last_active_at || 0) - (a.last_active_at || 0));
    const i = chat.key ? rows.findIndex((r) => r.key === chat.key) : -1;
    if (i >= 0) {
      if (chat.row) {
        rows[i] = Object.assign({}, rows[i], {
          status: "status" in chat.row ? chat.row.status : rows[i].status,
          title: chat.row.title || rows[i].title,
        });
      }
    } else if (chat.key && chat.row) {
      rows.push(chat.row);
      rows.sort((a, b) => (b.last_active_at || 0) - (a.last_active_at || 0));
    }
    return rows;
  }

  /* The list's day grouping (sessionsTable) over the rail's rows. */
  function railGroups(rows) {
    const days = [];
    for (const r of rows) {
      const day = railDay(r.last_active_at || 0);
      if (!days.length || days[days.length - 1].day !== day) days.push({ day, rows: [] });
      days[days.length - 1].rows.push(r);
    }
    return days;
  }

  function railRowHtml(r) {
    const sel = r.key === chat.key;
    const dot = r.status && RAIL_DOT[r.status]
      ? `<span class="cr-dot ${RAIL_DOT[r.status]}" title="${esc(STATUS[r.status] || r.status)}"></span>` : "";
    return `<button type="button" class="cr-row${sel ? " selected" : ""}" data-key="${esc(r.key)}"
        ${sel ? 'aria-current="true" ' : ""}title="${esc(r.title || r.id || "Untitled")}">
      <span class="cr-logo">${Fmt.logo(r.harness)}</span><span class="cr-name">${esc(r.title || r.id || "Untitled")}</span>
      <span class="cr-spacer"></span>${dot}</button>`;
  }

  function paintRail() {
    const rail = $("chat-rail");
    if (!rail) return;
    rail.innerHTML = `<div class="cr-head"><span class="caption">Sessions</span><span class="cr-spacer"></span>
      <button type="button" class="cr-btn${chat.rail.loading ? " spinning" : ""}" id="cr-refresh"
        title="List sessions again" aria-label="List sessions again">${icon("rescan")}</button></div>
    <div class="cr-list" id="cr-list"></div>`;
    $("cr-refresh").onclick = () => loadRail(chat.gen, true);
    paintRailRows();
  }

  function paintRailRows() {
    const list = $("cr-list");
    if (!list) return;
    // A repaint for a status change rebuilds the rows; keep a focused
    // row's focus instead of dropping it to <body>.
    const focus = document.activeElement;
    const focusKey = focus && list.contains(focus) && focus.dataset ? focus.dataset.key : null;
    const sessions = railSessions();
    // The open session rides into the rows even when the list itself is
    // empty (a cached empty list and a chat opened from a link).
    const rows = sessions ? railRows() : null;
    let html;
    if (chat.rail.error) {
      html = `<div class="cr-note">${esc(chat.rail.error)}</div>`;
    } else if (!rows) {
      html = `<div class="cr-note">${chat.rail.loading ? "Listing sessions…" : "No sessions listed."}</div>`;
    } else if (!rows.length) {
      html = '<div class="cr-note">No sessions listed.</div>';
    } else {
      html = railGroups(rows).map(({ day, rows }) => {
        const [y, m, dd] = day.split("-").map(Number);
        const label = new Date(y, m - 1, dd).toLocaleDateString("en-US",
          { month: "short", day: "numeric", year: "numeric" });
        return `<div class="cr-day">${label}</div>${rows.map(railRowHtml).join("")}`;
      }).join("");
    }
    list.innerHTML = html;
    for (const btn of list.querySelectorAll(".cr-row")) {
      btn.onclick = () => {
        // A row opens its chat the way the list's do: the hash is the
        // chat, so Back returns to whichever session was open before.
        if (btn.dataset.key !== chat.key) {
          location.hash = `#/sessions?chat=${encodeURIComponent(btn.dataset.key)}`;
        }
      };
    }
    if (focusKey) {
      const again = list.querySelector(`.cr-row[data-key="${CSS.escape(focusKey)}"]`);
      if (again) again.focus();
    }
  }

  /* The rail's rows are the page's `/v1/app/sessions` answer: its own
   * copy when the list already loaded, else one fetch (a plain relist;
   * the refresh button adds rescan, like the page's own) that also fills
   * the page's state for when the chat is left. */
  async function loadRail(gen, rescan = false) {
    if (railSessions() && !rescan) { paintRail(); return; }
    if (chat.rail.loading) return;
    chat.rail.loading = true;
    chat.rail.error = "";
    paintRail();
    const { status, data } = await api(`/v1/app/sessions${rescan ? "?rescan=1" : ""}`);
    if (gen !== chat.gen) return;
    chat.rail.loading = false;
    if (status === 401) {
      renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
      return;
    }
    if (status !== 200 || !data) {
      chat.rail.error = errorText(data)
        || (status ? `The hub answered ${status}.` : "Couldn't list the sessions.");
      paintRail();
      return;
    }
    const page = railPage();
    if (page) page.data = data; else chat.railData = data;
    paintRail();
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
    chat.detaching = false;
    chat.connState = "connecting";
    chat.rail = { loading: false, error: "" };
    paintShell();
    updateOwnership();
    // The rail lists what the page already knows at once, and fills in
    // after its own fetch when the list never loaded (a chat from a link).
    loadRail(gen);
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
      // open the composer now. A kept pane is adopted under its old tmux
      // name, and the note names a fresh one ("attached under tmux qv-…"),
      // so the chip can show before the ownership event lands.
      chat.detaching = false;
      chat.row.ownership = "attached";
      if (chat.row.kept) delete chat.row.kept;
      const m = data && String(data.note || "").match(/\bqv-[a-z0-9]+\b/i);
      if (m) chat.row.tmux = m[0];
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
    _test: { md, diffLines, renderDiff, renderPatch, toolDiff, parseFrame, revealDelay,
      railDay, railGroups },
  };
})();

globalThis.SessionChat = SessionChat;
