/* Inbox: a port of the Mac app's InboxPage. The hub account's GitHub
 * notifications across every repo, unread first; a row opens its subject
 * on github.com. Marking read (one row, or every notification behind an
 * inline confirm) posts to /v1/app/inbox/read and repaints from the hub's
 * answer. Uses GhUI from github.js (same bundle). */
"use strict";

(() => {
  const inbox = {
    data: null, status: 0, unreadOnly: true, loading: false,
    busy: new Set(), confirmAll: false, actionError: null, writes: 0,
  };

  const unreadCount = () => ((inbox.data && inbox.data.notifications) || []).filter((n) => n.unread).length;

  function header() {
    // GitHubModel.unreadCount > 0 shows the mark-all button, as in the app.
    const busyAll = inbox.busy.has("*");
    const markAll = !inbox.data || inbox.data.transport || !unreadCount() ? "" : `<button type="button"
        class="gh-button" id="gh-read-all"
        title="Mark every notification read"${busyAll || inbox.confirmAll ? " disabled" : ""}>${
        GhUI.ico(busyAll ? "running" : "envelopeOpen", busyAll ? "gh-spin" : "")}<span>Mark all read</span></button>`;
    // The app's Unread / All Tabs: both sides of the filter are labelled.
    const filter = `<div class="tabs" role="radiogroup" aria-label="Which notifications to list">${[["unread", "Unread"], ["all", "All"]].map(([id, label]) => {
      const on = (id === "unread") === inbox.unreadOnly;
      return `<button class="tab${on ? " selected" : ""}" role="radio" aria-checked="${on}" tabindex="${on ? 0 : -1}" data-inbox-show="${id}">${label}</button>`;
    }).join("")}</div>`;
    return pageHeader("Inbox",
      "Your GitHub account's live notifications across every repo, via swe gh inbox.",
      `${filter}
       ${markAll}
       <button class="icon-button${inbox.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch the inbox again">${icon("rescan")}</button>`);
  }

  async function render(_id, refresh = false) {
    const gen = routeGen;
    const writes = inbox.writes;
    inbox.loading = true;
    paint();
    const { status, data } = await api(`/v1/app/inbox${refresh ? "?refresh=1" : ""}`);
    if (gen !== routeGen) return;
    inbox.loading = false;
    if (status === 401) return GhUI.gate("inbox");
    // A mark-read landed while this read was in flight: its rows may be
    // older than the write's answer, so the answer stays on screen.
    if (writes !== inbox.writes && inbox.data && !inbox.data.transport) return paint();
    inbox.status = status;
    inbox.data = status === 200 && data ? data : { error: data && data.error, transport: true };
    paint();
  }

  /* POST one mark-read: ids, or every notification. Rows change only
   * from the hub's answer; until then the button spins and is off. */
  async function markRead(ids, all = false) {
    const key = all ? "*" : ids.join(",");
    if (inbox.busy.has(key)) return;
    inbox.busy.add(key);
    inbox.actionError = null;
    paint();
    const { status, data } = await GhUI.post("/v1/app/inbox/read", all ? { all: true } : { ids });
    inbox.busy.delete(key);
    if (all) inbox.confirmAll = false;
    if (status === 200 && data) {
      inbox.writes++;
      if (inbox.data && !inbox.data.transport && inbox.data.status === "ok") {
        // Flip only what this answer marked, on the rows as they are now:
        // two marks in flight can answer in either order, and a whole
        // listing from the earlier one would bring the later one's row back.
        // A mark-all covers the rows the hub listed when it marked; a row
        // a refresh brought in since may be newer than the write, so it
        // keeps its own state.
        const marked = (data.marked || []).map(String);
        const done = new Set(marked.includes("*")
          ? (data.notifications || []).map((n) => String(n.id)) : marked);
        inbox.data.notifications = (inbox.data.notifications || []).map((n) =>
          (done.has(String(n.id)) ? { ...n, unread: false } : n));
        inbox.data.github = data.github || inbox.data.github;
      } else {
        // The last read failed (or none landed): the answer carries the
        // whole listing, so it becomes the page's rows.
        inbox.status = 200;
        inbox.data = { github: data.github, status: "ok", notifications: data.notifications || [],
          fetched_at: Date.now() / 1000, machine: data.machine };
      }
      const failed = data.failed || [];
      if (failed.length) inbox.actionError = failed.map((f) => `${f.id}: ${f.error}`).join(" · ");
    } else if (status === 401) {
      if (onInbox()) renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
      return;
    } else {
      inbox.actionError = GhUI.failMessage(status, data);
    }
    if (!onInbox()) return;
    paint();
    // The clicked button is gone (its row went read) or was disabled:
    // put focus somewhere sensible instead of on <body>.
    const next = document.querySelector("[data-read]:not([disabled])") || document.getElementById("page-refresh");
    if (next && document.activeElement === document.body) next.focus();
  }

  const onInbox = () => currentRoute() === "inbox";

  /* The subject type's icon, tinted by why GitHub notified you. */
  function typeIcon(note) {
    const name = {
      PullRequest: "prOpen", Issue: "issueOpen", CheckSuite: "xFill", CheckRun: "xFill",
      Release: "tag", Discussion: "bubble", Commit: "commit",
    }[note.type] || "bell";
    const tone = note.reason === "ci_activity" ? "gh-danger"
      : note.reason === "review_requested" || note.reason === "mention" ? "gh-warning" : "gh-secondary";
    return GhUI.ico(name, tone, note.type || "Notification");
  }

  function row(note) {
    const when = GhUI.ago(note.updated_at);
    const href = safeHref(note.url);
    const busy = inbox.busy.has(String(note.id)) || inbox.busy.has("*");
    const read = note.unread ? `<button type="button" class="icon-button gh-note-read${busy ? " spinning" : ""}"
        data-read="${esc(note.id)}" title="Mark as read" aria-label="Mark as read: ${esc(note.title)}"${busy ? " disabled" : ""}>${
        GhUI.ico(busy ? "running" : "envelopeOpen")}</button>` : "";
    return `<div class="gh-note${note.unread ? " unread" : ""}">
      <a class="gh-note-link"${href ? ` href="${href}" target="_blank" rel="noopener"` : ""}>
        <span class="gh-note-dot" aria-label="${note.unread ? "Unread" : "Read"}"></span>
        ${typeIcon(note)}
        <span class="gh-row-main">
          <span class="gh-note-title">${esc(note.title)}</span>
          <span class="caption gh-muted gh-ellipsis">${esc(note.repo)} · ${esc(String(note.reason || "").replace(/_/g, " "))}${when ? ` · ${when}` : ""}</span>
        </span>
      </a>${read}
    </div>`;
  }

  const retry = '<button type="button" class="gh-button" id="gh-inbox-retry">Refresh</button>';
  const failCard = (msg) => `<div class="card"><div class="gh-error-row"><span class="error">${esc(msg)}</span>${retry}</div></div>`;

  /* Mark all read reaches past the rows on screen (every notification
   * GitHub has), so it asks once, inline, before it goes. */
  function confirmAll() {
    if (!inbox.confirmAll) return "";
    const n = unreadCount();
    const busy = inbox.busy.has("*");
    return `<div class="card gh-confirm" role="group" aria-label="Confirm mark all read">
      <div class="gh-confirm-text">Mark every GitHub notification read, including the ${n} unread here? This can't be undone from here.</div>
      <div class="gh-confirm-buttons">
        <button type="button" class="gh-button" id="gh-read-all-cancel"${busy ? " disabled" : ""}>Cancel</button>
        <button type="button" class="gh-button gh-primary" id="gh-read-all-go"${busy ? " disabled" : ""}>${busy ? "Marking…" : "Mark all read"}</button>
      </div></div>`;
  }

  function content() {
    const d = inbox.data;
    if (!d) {
      return `<div class="card"><div class="gh-empty">${GhUI.ico("running", "gh-muted gh-spin")}<span>Reading GitHub notifications…</span></div></div>`;
    }
    if (d.transport) return failCard(GhUI.failMessage(inbox.status, d));
    const setup = GhUI.envSetup(d.github);
    if (setup) return GhUI.setupCard(setup, d.machine);
    if (d.status !== "ok") return failCard(`GitHub returned ${d.status}`);
    const errorCard = inbox.actionError ? `<div class="card" role="alert"><span class="error">${esc(inbox.actionError)}</span></div>` : "";
    const notes = d.notifications || [];
    const rows = inbox.unreadOnly ? notes.filter((n) => n.unread) : notes;
    if (!rows.length) {
      return `${errorCard}<div class="card"><div class="gh-empty">${GhUI.ico("tray", "gh-muted")}<span>${
        inbox.unreadOnly ? "No unread notifications." : "The inbox is empty."}</span></div></div>`;
    }
    const updated = d.fetched_at ? `<div class="caption gh-muted">Updated ${Fmt.ago(d.fetched_at)}</div>` : "";
    return `${errorCard}${confirmAll()}<div class="card gh-inbox">${rows.map(row).join("")}${updated}</div>`;
  }

  function paint() {
    if (!unreadCount()) inbox.confirmAll = false;
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${header()}<div class="stack">${content()}</div></div>`;
    bindRefresh(() => render("inbox", true));
    GhUI.bindSetup(() => render("inbox", true));
    const again = document.getElementById("gh-inbox-retry");
    if (again) again.onclick = () => render("inbox", true);
    for (const btn of document.querySelectorAll("#panel [data-inbox-show]")) {
      const focusSelected = () => {
        const sel = document.querySelector('#panel [data-inbox-show][aria-checked="true"]');
        if (sel) sel.focus();
      };
      btn.onclick = () => {
        inbox.unreadOnly = btn.dataset.inboxShow === "unread";
        paint();
        focusSelected();
      };
      btn.onkeydown = (e) => {
        // radiogroup: arrows move to the other option and select it
        if (!e.key.startsWith("Arrow")) return;
        e.preventDefault();
        inbox.unreadOnly = btn.dataset.inboxShow !== "unread";
        paint();
        focusSelected();
      };
    }
    for (const btn of document.querySelectorAll("#panel [data-read]")) {
      btn.onclick = (e) => { e.preventDefault(); markRead([btn.dataset.read]); };
    }
    const all = document.getElementById("gh-read-all");
    if (all) all.onclick = () => {
      inbox.confirmAll = true;
      inbox.actionError = null;
      paint();
      const cancel = document.getElementById("gh-read-all-cancel");
      if (cancel) cancel.focus();
    };
    const cancel = document.getElementById("gh-read-all-cancel");
    if (cancel) cancel.onclick = () => {
      inbox.confirmAll = false;
      paint();
      const back = document.getElementById("gh-read-all");
      if (back) back.focus();
    };
    const go = document.getElementById("gh-read-all-go");
    if (go) go.onclick = () => markRead([], true);
  }

  registerPage("inbox", (id) => {
    inbox.confirmAll = false;
    inbox.actionError = null;
    render(id);
    GhUI.poll("inbox", () => {
      if (inbox.loading || inbox.busy.size || inbox.confirmAll) return false;
      render("inbox");
    });
  });
})();
