/* Inbox: a port of the Mac app's InboxPage, with the iOS Inbox's Needs
 * you sections on top.
 *
 * Needs you is the hub's own inbox (GET /v1/inbox?done=1): sessions
 * waiting on an approval or an answer, errors, and what finished today.
 * A row opens its session's chat; Clear (one row, or a section) posts
 * POST /v1/inbox/dismiss, which keeps the row off this browser's inbox
 * until the session moves again. A pending approval can't be cleared.
 * The hub's GitHub rows are left out: the list below already shows them.
 *
 * Below it, the hub account's GitHub notifications across every repo,
 * unread first; a row opens its subject on github.com. Marking read (one
 * row, or every notification behind an inline confirm) posts to
 * /v1/app/inbox/read and repaints from the hub's answer. Uses GhUI from
 * github.js (same bundle). */
"use strict";

/* The iOS app's InboxGrouping and DismissalLedger (AppModel.swift), kept
 * out of the view so they can be tested on their own. */
const NeedsYou = (() => {
  /* Page order, the iOS titles, and the "Clear …" each section's button
   * names. */
  const SECTIONS = [
    { id: "waiting", title: "Waiting on you", clear: "waiting on you", tone: "gh-warning" },
    { id: "errors", title: "Errors", clear: "errors", tone: "gh-danger" },
    { id: "finished", title: "Finished today", clear: "finished", tone: "gh-secondary" },
  ];
  const LABEL = { approval: "Approval needed", followup: "Waiting", error: "Error",
    interrupted: "Interrupted", done: "Done" };

  const isGitHub = (item) => item.kind === "github" || item.status === "github";
  /* An unfamiliar status lands in Waiting on you rather than vanishing. */
  function section(item) {
    if (item.status === "error" || item.status === "interrupted") return "errors";
    if (item.status === "done") return "finished";
    return "waiting";
  }
  /* A pending approval blocks an agent until answered: the hub keeps it
   * whatever a device clears, so it never gets a Clear. */
  const clearable = (item) => item.status !== "approval";
  /* What a dismiss posts back as last_active_at. */
  const stamp = (item) => Number(item.last_active_at) || 0;
  const label = (status) => LABEL[status] || String(status || "");

  /* The session rows bucketed by section, sections in page order; empty
   * sections left out. */
  function groups(items) {
    const by = {};
    for (const item of items || []) {
      if (isGitHub(item)) continue;
      (by[section(item)] = by[section(item)] || []).push(item);
    }
    return SECTIONS.filter((s) => by[s.id]).map((s) => ({ ...s, items: by[s.id] }));
  }

  /* Dismissals posted but maybe not in a fetched list yet: ``ledger``
   * maps key -> the stamp posted. A fetched row at or before its stamp
   * stays hidden; one that moved past it has new activity, comes back,
   * and retires the entry (the hub's own rule). A pending approval
   * always shows, as the hub ignores dismissals for one: it can arrive
   * on a cleared session before that session's stamp moves. */
  function overlay(items, ledger) {
    const out = [];
    for (const item of items || []) {
      if (item.status === "approval" || !(item.key in ledger)) { out.push(item); continue; }
      if (stamp(item) > ledger[item.key]) {
        delete ledger[item.key];
        out.push(item);
      }
    }
    return out;
  }

  return { SECTIONS, groups, section, clearable, stamp, label, overlay, isGitHub };
})();

(() => {
  const inbox = {
    data: null, status: 0, unreadOnly: true, loading: false,
    busy: new Set(), confirmAll: false, actionError: null, writes: 0,
  };
  /* Needs you: the hub's rows (null until the first answer), the last
   * read's or clear's error, and the dismissals still in flight. */
  const needs = { items: null, error: null, actionError: null, ledger: {}, seq: 0 };

  const unreadCount = () => ((inbox.data && inbox.data.notifications) || []).filter((n) => n.unread).length;

  function header() {
    // GitHubModel.unreadCount > 0 shows the mark-all button, as in the app.
    const busyAll = inbox.busy.has("*");
    const markAll = !inbox.data || inbox.data.transport || !unreadCount() ? "" : `<button type="button"
        class="gh-button" id="gh-read-all"
        title="Mark every notification read"${busyAll || inbox.confirmAll ? " disabled" : ""}>${
        GhUI.ico(busyAll ? "running" : "envelopeOpen", busyAll ? "gh-spin" : "")}<span>Mark all read</span></button>`;
    // The app's Unread / All Tabs: both sides of the filter are labelled.
    const filter = `<div class="tabs" role="radiogroup" aria-label="Which GitHub notifications to list">${[["unread", "Unread"], ["all", "All"]].map(([id, label]) => {
      const on = (id === "unread") === inbox.unreadOnly;
      return `<button class="tab${on ? " selected" : ""}" role="radio" aria-checked="${on}" tabindex="${on ? 0 : -1}" data-inbox-show="${id}">${label}</button>`;
    }).join("")}</div>`;
    return pageHeader("Inbox",
      "What your agents are waiting on, then your GitHub account's live notifications across every repo.",
      `${filter}
       ${markAll}
       <button class="icon-button${inbox.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch the inbox again">${icon("rescan")}</button>`);
  }

  async function render(_id, refresh = false) {
    const gen = routeGen;
    const writes = inbox.writes;
    inbox.loading = true;
    paint();
    loadNeeds();
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
      if (onInbox()) renderGate();
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

  /* GET /v1/inbox?done=1 ("Finished today" ships only when asked). It
   * paints on its own, so a slow GitHub read never holds it back. A
   * failed read keeps the last rows and says so. */
  async function loadNeeds() {
    const gen = routeGen;
    // Only the newest read lands: an older one answering last would undo
    // what the newer one's overlay retired, and bring a cleared row back.
    const seq = ++needs.seq;
    const { status, data } = await api("/v1/inbox?done=1");
    if (gen !== routeGen || seq !== needs.seq || !onInbox()) return;
    // The GitHub read answers 401 too, and gates the page.
    if (status === 401) return;
    if (status === 200 && data && Array.isArray(data.items)) {
      needs.items = NeedsYou.overlay(data.items.filter((i) => !NeedsYou.isGitHub(i)), needs.ledger);
      needs.error = null;
    } else {
      needs.error = GhUI.failMessage(status, data);
    }
    paint();
  }

  /* Clear rows for this browser: gone at once, then one dismiss each.
   * A refused one drops its ledger entry and reads the inbox again,
   * which brings the row back. */
  async function clearNeeds(rows) {
    rows = rows.filter(NeedsYou.clearable);
    if (!rows.length) return;
    const keys = new Set(rows.map((r) => r.key));
    needs.items = (needs.items || []).filter((i) => !keys.has(i.key));
    for (const r of rows) needs.ledger[r.key] = NeedsYou.stamp(r);
    needs.actionError = null;
    paint();
    const next = document.querySelector("[data-need-clear]") || document.getElementById("page-refresh");
    if (next) next.focus();
    const answers = await Promise.all(rows.map((r) =>
      GhUI.post("/v1/inbox/dismiss", { key: r.key, last_active_at: NeedsYou.stamp(r) })));
    const failed = rows.filter((_r, i) => answers[i].status !== 200);
    if (!failed.length) return;
    for (const r of failed) {
      if (needs.ledger[r.key] === NeedsYou.stamp(r)) delete needs.ledger[r.key];
    }
    const first = answers.find((a) => a.status !== 200);
    if (first.status === 401) {
      if (onInbox()) GhUI.gate("inbox");
      return;
    }
    const what = failed.length === 1 ? `"${failed[0].title || "Untitled session"}"` : `${failed.length} rows`;
    needs.actionError = `Couldn't clear ${what}: ${GhUI.failMessage(first.status, first.data)}`;
    if (onInbox()) loadNeeds();
  }

  /* A session row, as the iOS row reads: status dot, title, the
   * harness's logo, what it is waiting on and when. */
  function needRow(item) {
    const title = item.title || "Untitled session";
    const mark = Fmt.logo(item.harness, "logo logo-sm");
    const who = mark.startsWith("<img") ? mark : esc(Fmt.harnessName(item.agent || item.harness));
    const when = item.last_active_at ? Fmt.ago(item.last_active_at) : "";
    const meta = [NeedsYou.label(item.status), when].filter(Boolean).map(esc).join(" · ");
    const review = item.status === "approval" ? '<span class="gh-need-review">Review</span>' : "";
    const clear = NeedsYou.clearable(item) ? `<button type="button" class="icon-button gh-note-read"
        data-need-clear="${esc(item.key)}" title="Clear" aria-label="Clear: ${esc(title)}">${GhUI.ico("xmark")}</button>` : "";
    return `<div class="gh-note gh-need">
      <a class="gh-note-link" href="#/sessions?chat=${esc(encodeURIComponent(item.key))}">
        <span class="gh-need-dot status-${esc(item.status)}" aria-hidden="true"></span>
        <span class="gh-row-main">
          <span class="gh-need-title">${esc(title)}</span>
          <span class="caption gh-muted gh-ellipsis gh-need-meta">${who}<span>${meta}</span></span>
        </span>${review}
      </a>${clear}
    </div>`;
  }

  /* The Needs you card: a head per section with its Clear, then rows. */
  function needsContent() {
    const errors = [needs.error, needs.actionError].filter(Boolean)
      .map((m) => `<div class="card" role="alert"><span class="error">${esc(m)}</span></div>`).join("");
    if (!needs.items) {
      return needs.error ? errors : Sk.cardRows(4);
    }
    const groups = NeedsYou.groups(needs.items);
    if (!groups.length) {
      return `${errors}<div class="card"><div class="gh-empty">${GhUI.ico("checkFill", "gh-muted")}<span>Nothing needs you. Approvals, questions and errors from your agents land here.</span></div></div>`;
    }
    return `${errors}<div class="card gh-inbox gh-needs">${groups.map((g) => {
      const n = g.items.filter(NeedsYou.clearable).length;
      const clear = n ? `<button type="button" class="gh-need-clear" data-need-section="${g.id}"
          title="Clear ${esc(g.clear)}" aria-label="Clear ${esc(g.clear)}">Clear</button>` : "";
      return `<div class="gh-need-head"><span class="gh-need-section ${g.tone}">${esc(g.title)}</span>
          <span class="gh-spacer"></span>${clear}</div>
        ${g.items.map(needRow).join("")}`;
    }).join("")}</div>`;
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
      return Sk.cardRows(5);
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
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${header()}<div class="stack">
      <h2 class="gh-inbox-heading">Needs you</h2>${needsContent()}
      <h2 class="gh-inbox-heading">GitHub</h2>${content()}</div></div>`;
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
    for (const btn of document.querySelectorAll("#panel [data-need-clear]")) {
      btn.onclick = (e) => {
        e.preventDefault();
        clearNeeds((needs.items || []).filter((i) => i.key === btn.dataset.needClear));
      };
    }
    for (const btn of document.querySelectorAll("#panel [data-need-section]")) {
      btn.onclick = () => clearNeeds((needs.items || [])
        .filter((i) => !NeedsYou.isGitHub(i) && NeedsYou.section(i) === btn.dataset.needSection));
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
    needs.actionError = null;
    render(id);
    GhUI.poll("inbox", () => {
      if (inbox.loading || inbox.busy.size || inbox.confirmAll) return false;
      render("inbox");
    });
  });
})();
