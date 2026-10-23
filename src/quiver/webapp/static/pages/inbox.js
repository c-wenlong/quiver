/* Inbox: a port of the Mac app's InboxPage. The hub account's GitHub
 * notifications across every repo, unread first; a row opens its subject
 * on github.com. Read only: marking read stays in swe and the Mac app.
 * Uses GhUI from github.js (same bundle). */
"use strict";

(() => {
  const inbox = { data: null, status: 0, unreadOnly: true, loading: false };

  const header = () => pageHeader("Inbox",
    "Your GitHub account's live notifications across every repo, via swe gh inbox.",
    `<label class="gh-switch" title="Show only unread notifications"><input type="checkbox" id="gh-unread"${inbox.unreadOnly ? " checked" : ""}>
       <span class="gh-switch-track" aria-hidden="true"></span><span>Unread only</span></label>
     <button class="icon-button${inbox.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch the inbox again">${icon("rescan")}</button>`);

  async function render(_id, refresh = false) {
    const gen = routeGen;
    inbox.loading = true;
    paint();
    const { status, data } = await api(`/v1/app/inbox${refresh ? "?refresh=1" : ""}`);
    if (gen !== routeGen) return;
    inbox.loading = false;
    if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    inbox.status = status;
    inbox.data = status === 200 ? data : { error: data && data.error, transport: true };
    paint();
  }

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
    return `<a class="gh-note${note.unread ? " unread" : ""}" href="${esc(note.url)}" target="_blank" rel="noopener">
      <span class="gh-note-dot" aria-label="${note.unread ? "Unread" : "Read"}"></span>
      ${typeIcon(note)}
      <span class="gh-row-main">
        <span class="gh-note-title">${esc(note.title)}</span>
        <span class="caption gh-muted gh-ellipsis">${esc(note.repo)} · ${esc(String(note.reason || "").replace(/_/g, " "))}${when ? ` · ${when}` : ""}</span>
      </span>
    </a>`;
  }

  const retry = '<button type="button" class="gh-button" id="gh-inbox-retry">Refresh</button>';
  const failCard = (msg) => `<div class="card"><div class="gh-error-row"><span class="error">${esc(msg)}</span>${retry}</div></div>`;

  function content() {
    const d = inbox.data;
    if (!d) {
      return `<div class="card"><div class="gh-empty">${GhUI.ico("running", "gh-muted gh-spin")}<span>Reading GitHub notifications…</span></div></div>`;
    }
    if (d.transport) return failCard(GhUI.failMessage(inbox.status, d));
    const setup = GhUI.envSetup(d.github);
    if (setup) return GhUI.setupCard(setup, d.machine);
    if (d.status !== "ok") return failCard(`GitHub returned ${d.status}`);
    const notes = d.notifications || [];
    const rows = inbox.unreadOnly ? notes.filter((n) => n.unread) : notes;
    if (!rows.length) {
      return `<div class="card"><div class="gh-empty">${GhUI.ico("tray", "gh-muted")}<span>${
        inbox.unreadOnly ? "No unread notifications." : "The inbox is empty."}</span></div></div>`;
    }
    const updated = d.fetched_at ? `<div class="caption gh-muted">Updated ${Fmt.ago(d.fetched_at)}</div>` : "";
    return `<div class="card gh-inbox">${rows.map(row).join("")}${updated}</div>`;
  }

  function paint() {
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${header()}<div class="stack">${content()}</div></div>`;
    bindRefresh(() => render("inbox", true));
    GhUI.bindSetup(() => render("inbox", true));
    const again = document.getElementById("gh-inbox-retry");
    if (again) again.onclick = () => render("inbox", true);
    const toggle = document.getElementById("gh-unread");
    toggle.onchange = () => { inbox.unreadOnly = toggle.checked; paint(); };
  }

  registerPage("inbox", (id) => {
    render(id);
    GhUI.poll("inbox", () => { if (!inbox.loading) render("inbox"); });
  });
})();
