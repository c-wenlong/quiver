/* Tasks: the work items the hub's agents are on — the task store over
 * the pinned repos' open issues and PRs (and Linear's, when a key is
 * configured). The list groups by repository under All / Running /
 * PR open / Done tabs, five rows a group before a Show more fold, with
 * the Repositories page's rail beside it (every source's groups and
 * their counts; a tap narrows the list to one); a task's detail (#/tasks?task=<id>) shows the
 * state ladder, its linked sessions (each deep-linking into the chat),
 * its linked PR (deep link into the Repositories page's PR sheet) and
 * the two actions the phone has: start a new agent on it — the
 * issues/resolve or pulls/open route, whose launch origin links the
 * session on adoption — and link a session that is already running
 * (POST /v1/sessions/<key>/task; the row's unlink is DELETE). Uses GhUI
 * from github.js. */
"use strict";

(() => {
  const ico = GhUI.ico;
  const badge = (text) => `<span class="badge">${esc(text)}</span>`;

  /* The header's tabs. "Running" is a linked agent in flight
   * (``agent_running``); "PR open" is a task whose PR is open — an
   * issue's ``quiver/issue-<n>`` PR, or a pull task until it lands, as
   * the phone counts it; "Done" is merged or closed. */
  const FILTERS = [
    { id: "all", title: "All", test: () => true },
    { id: "running", title: "Running", test: (t) => !!t.agent_running },
    {
      id: "pr_open", title: "PR open",
      test: (t) => t.state === "pr_open"
        || (t.kind === "pull" && t.state !== "merged" && t.state !== "closed"),
    },
    {
      id: "done", title: "Done",
      test: (t) => t.state === "merged" || t.state === "closed",
    },
  ];
  const FILTER_EMPTY = {
    running: "Nothing is running right now.",
    pr_open: "No task has a pull request open.",
    done: "Nothing is merged or closed yet.",
  };
  const filterOf = () => FILTERS.find((f) => f.id === page.filter) || FILTERS[0];

  /* ``state`` is the ladder, a sort and group key (#875); what a row
   * says is the hub's ``status``, in the source's own words. These are
   * the stepper's rungs: GitHub's progress is the pipeline (an agent,
   * its PR, the merge), another source's is its own started and done. */
  const RUNG_WORDS = {
    open: "Open", in_progress: "Agent", pr_open: "PR open",
    merged: "Merged", closed: "Closed",
  };
  const OTHER_RUNG_WORDS = { ...RUNG_WORDS, in_progress: "Started", merged: "Done" };
  const statusOf = (t) => t.status || RUNG_WORDS[t.state] || t.state;
  const LAUNCH_POLLS = 60;

  const page = {
    data: null, error: null, loading: false,
    filter: "all", busy: new Set(), actionError: null,
    seq: 0, forTask: null,
    // The list: the rail's chosen group (null is every task), the groups
    // unfolded past their first rows, and the rail's open quiet folds.
    shown: null, expanded: new Set(), openQuiet: new Set(),
    // The detail's two panels; at most one is open at a time.
    action: null,      // "new" | "link"
    session: null,     // the New agent session panel's state
    link: null,        // the Link session panel's state
  };

  const tasks = () => (page.data && page.data.tasks) || [];
  const task = (id) => tasks().find((t) => t.id === id) || null;
  const selectedId = () => GhUI.hashParams().get("task");
  const onTasks = () => currentRoute() === "tasks";

  /* A github task groups under its repo; another source's task under its
   * project or the source's name, "Other" only for a row with no source,
   * as the Mac and iOS group them. */
  const SOURCE_NAMES = { github: "GitHub", linear: "Linear", notion: "Notion" };
  const sourceName = (s) => SOURCE_NAMES[s] || (s ? s[0].toUpperCase() + s.slice(1) : "Other");
  const groupName = (t) => t.repo || t.project || sourceName(t.source);
  const PREVIEW = 5; // rows a group shows before its Show more fold
  /* ``#42`` for a github issue/pull; the ref as it is anywhere else. */
  const refLabel = (t) => (t.source === "github" ? `#${t.ref}` : `${t.ref}`);

  /* ``POST /v1/sessions/<key>/task`` wants the task's ref fields back. */
  const refOf = (t) => {
    const ref = { source: t.source, kind: t.kind, ref: t.ref,
      title: t.title || "", url: t.url || "" };
    if (t.repo) ref.repo = t.repo;
    if (t.project) ref.project = t.project;
    return ref;
  };

  // The unlink route is a DELETE with the same JSON shape as a POST.
  const del_ = (path, body) => apiSend("DELETE", path, body);

  /* ----------------------------------------------------------- marks */

  function taskIcon(t) {
    if (t.kind === "pull") {
      if (t.pr && t.pr.draft && t.state !== "merged" && t.state !== "closed") {
        return ico("prDraft", "gh-muted", "Draft");
      }
      if (t.state === "merged") return ico("prMerged", "gh-merged", "Merged");
      if (t.state === "closed") return ico("prClosed", "gh-danger", "Closed");
      return ico("prOpen", "gh-success", "Open");
    }
    if (t.state === "merged") return ico("issueClosed", "gh-merged", "Merged");
    if (t.state === "closed") return ico("issueClosed", "gh-muted", "Closed");
    return ico("issueOpen", "gh-success", "Open");
  }

  /* The derived ladder as rungs: open → in progress → (PR open) →
   * merged|closed. Only a GitHub issue's own PR can stand between "an
   * agent is on it" and "done" — a pull IS its PR, and the other
   * sources' ladders end at started. */
  const rungs = (t) => {
    const list = ["open", "in_progress"];
    if (t.source === "github" && t.kind === "issue") list.push("pr_open");
    list.push(t.state === "closed" ? "closed" : "merged");
    return list;
  };

  function stepper(t) {
    const list = rungs(t);
    let at = list.indexOf(t.state);
    if (at < 0) at = t.state === "open" ? 0 : 1;
    return `<div class="tk-steps" role="list">${list.map((r, i) => {
      const cls = i < at ? "done" : i === at ? "at" : "";
      const mark = i < at ? ico("checkFill", "tk-step-check")
        : `<span class="tk-num num">${i + 1}</span>`;
      return `${i ? '<span class="tk-line" aria-hidden="true"></span>' : ""}
      <span class="tk-step ${cls}" role="listitem"${i === at ? ' aria-current="step"' : ""}>${mark}<span>${(t.source === "github" ? RUNG_WORDS : OTHER_RUNG_WORDS)[r]}</span></span>`;
    }).join("")}</div>`;
  }

  const emptyCard = (text, iconName = "issueClosed") =>
    `<div class="card"><div class="gh-empty">${ico(iconName, "gh-muted")}<span>${esc(text)}</span></div></div>`;
  const loadingCard = (text) =>
    `<div class="card"><div class="gh-empty">${ico("running", "gh-muted gh-spin")}<span>${esc(text)}</span></div></div>`;

  /* ------------------------------------------------------------- list */

  function taskRow(t) {
    const pr = t.pr;
    const prBadge = pr
      ? `<span class="badge${pr.state === "merged" ? " tk-merged" : pr.state === "open" ? "" : " tk-closed"}" title="PR #${esc(pr.number)}: ${esc(pr.title)}">PR #${esc(pr.number)}</span>`
      : "";
    const sessions = (t.sessions || []).length;
    const when = t.last_active_at ? ` · ${Fmt.ago(t.last_active_at)}` : "";
    return `<a class="gh-row gh-center" href="#/tasks?task=${encodeURIComponent(t.id)}">
      <span class="gh-row-lead">${taskIcon(t)}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(t.title || refLabel(t))}</span>${prBadge}</span>
        <span class="gh-row-sub">${esc(refLabel(t))} · ${esc(statusOf(t))}${when}</span>
      </span>
      <span class="gh-row-side">${t.agent_running
        ? `<span class="gh-label gh-success tk-running">${ico("running", "gh-spin gh-small")}<span>Agent running</span></span>` : ""}
        ${sessions ? badge(sessions) : ""}</span>
    </a>`;
  }

  /* Repositories' group header: a repo reads "owner / name" with its
   * links out; another source's project reads "Linear / project". */
  function groupHead(g) {
    if (g.repo) {
      const [owner, short] = g.repo.includes("/") ? g.repo.split("/", 2) : [null, g.repo];
      return `<div class="gh-group-head">
        ${owner ? `<span class="gh-muted">${esc(owner)} /</span>` : ""}<span>${esc(short)}</span>
        <span class="gh-spacer"></span>
        <span class="gh-head-links">
          <a href="https://github.com/${esc(g.repo)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open ${esc(g.repo)} on GitHub">${ico("external")}</a>
          <a href="#/repositories?repo=${encodeURIComponent(g.repo)}" title="Open ${esc(g.repo)} in Repositories" aria-label="Open ${esc(g.repo)} in Repositories">${ico("expand")}</a>
        </span></div>`;
    }
    const source = g.source && sourceName(g.source);
    return `<div class="gh-group-head">
      ${source && source !== g.name ? `<span class="gh-muted">${esc(source)} /</span>` : ""}<span>${esc(g.name)}</span></div>`;
  }

  /* A group's rows: the first five and a Show more fold, as the
   * Repositories dashboard; the fold stays open per group and tab
   * across repaints. A group the rail narrowed to shows every row. */
  function groupRows(g) {
    if (page.shown) return g.rows.map(taskRow).join("");
    const key = `${g.name}:${page.filter}`;
    const open = page.expanded.has(key);
    const visible = open ? g.rows : g.rows.slice(0, PREVIEW);
    const hidden = g.rows.length - PREVIEW;
    const more = hidden > 0 ? `<button type="button" class="gh-expand" data-expand="${esc(key)}"
        title="${open ? `Show only the first ${PREVIEW}` : "Show every row"}">${ico(open ? "chevronUp" : "chevronDown")}<span>${
        open ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</span></button>` : "";
    return visible.map(taskRow).join("") + more;
  }

  /* Rows grouped by repository, in the list's own order (last activity
   * first); a repo whose read failed gets a group with its error. */
  function listGroups(rows, failed) {
    const seen = new Map();
    for (const t of rows) {
      const name = groupName(t);
      if (!seen.has(name)) seen.set(name, { name, source: t.source, repo: t.source === "github" ? t.repo : null, rows: [] });
      seen.get(name).rows.push(t);
    }
    // A failed repo keeps its stored rows under its error, in its own
    // group: one header per repo, never a second one for the failure.
    for (const r of failed) {
      if (!seen.has(r.name)) seen.set(r.name, { name: r.name, source: "github", repo: r.name, rows: [] });
      seen.get(r.name).error = r.error;
    }
    const groups = [...seen.values()];
    const last = groups.length - 1;
    return `<div class="gh-box">${groups.map((g, i) => `${groupHead(g)}
      <div class="gh-surface${i === last ? " last" : ""}">${g.error
        ? `<div class="gh-state"><div class="gh-error-row"><span class="error">${esc(g.error)}</span></div></div>` : ""}${groupRows(g)}</div>`).join("")}</div>`;
  }

  /* ------------------------------------------------------------- rail */

  /* The repos whose read failed this refresh, from ``repos``. */
  const failedRepos = () => ((page.data && page.data.repos) || [])
    .map((r) => ({ name: r.repo || r.name, error: r.error || r.status }));

  /* The rail's sections: GitHub groups under their repo's owner, every
   * other source under its own name, each row one list group (keyed by
   * the group's name). Sections and rows sort by name, so the rail stays
   * put while the list reorders by activity. */
  function railSections() {
    const sections = new Map();
    const add = (id, caption, key, label) => {
      if (!sections.has(id)) sections.set(id, { id, caption, rows: new Map() });
      const rows = sections.get(id).rows;
      if (!rows.has(key)) rows.set(key, { key, label });
    };
    const addRepo = (repo) => {
      const [owner, short] = repo.includes("/") ? repo.split("/", 2) : [repo, repo];
      add(`gh:${owner}`, owner, repo, short);
    };
    for (const t of tasks()) {
      if (t.source === "github" && t.repo) addRepo(t.repo);
      else add(`src:${t.source}`, sourceName(t.source), groupName(t), groupName(t));
    }
    for (const r of failedRepos()) addRepo(r.name);
    const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base" });
    const rank = (sec) => (sec.id.startsWith("gh:") ? 0 : 1); // owners, then the other sources
    return [...sections.values()]
      .sort((a, b) => rank(a) - rank(b) || byName(a.caption, b.caption))
      .map((sec) => ({ ...sec, rows: [...sec.rows.values()].sort((a, b) => byName(a.label, b.label)) }));
  }

  function railHtml() {
    const rows = tasks().filter(filterOf().test);
    const count = (key) => rows.filter((t) => groupName(t) === key).length;
    const running = (key) => tasks().some((t) => t.agent_running && groupName(t) === key);
    // No CI here: the dot lights only while an agent is on a task there.
    const row = (r, quiet) => {
      const n = count(r.key);
      const on = running(r.key);
      return `<button type="button" class="gh-rail-row${page.shown === r.key ? " selected" : ""}${quiet ? " quiet" : ""}"
          data-shown="${esc(r.key)}" title="${esc(r.key)}${on ? ". An agent is running here." : ""}">
        <span class="tk-rail-mark"><span class="gh-dot${on ? " tk-dot-agent" : " gh-dot-none"}"></span></span>
        <span class="gh-rail-name">${esc(r.label)}</span>
        <span class="gh-spacer"></span>${n ? `<span class="caption num gh-muted">${n}</span>` : ""}</button>`;
    };
    const body = railSections().map((sec) => {
      const active = sec.rows.filter((r) => count(r.key) !== 0);
      const quiet = sec.rows.filter((r) => count(r.key) === 0);
      const open = page.openQuiet.has(sec.id);
      return `<div class="gh-owner">${esc(sec.caption)}</div>${active.map((r) => row(r, false)).join("")}
        ${quiet.length ? `<button type="button" class="gh-quiet-toggle${open ? " open" : ""}" data-quiet="${esc(sec.id)}"
          title="${open ? "Hide groups with nothing on this tab" : "Show groups with nothing on this tab"}">${ico("chevronRight")}<span>${quiet.length} quiet</span></button>
          <div class="gh-quiet${open ? " open" : ""}">${quiet.map((r) => row(r, true)).join("")}</div>` : ""}`;
    }).join("");
    return `<aside class="gh-rail" aria-label="Task sources">
      <div class="gh-rail-head"><span class="caption gh-muted">Sources</span><span class="gh-spacer"></span>
        <button type="button" class="gh-rail-btn${page.loading ? " spinning" : ""}" id="page-refresh" title="Fetch again" aria-label="Fetch again">${ico("refresh")}</button></div>
      <div class="gh-rail-list">
        <button type="button" class="gh-rail-row${page.shown ? "" : " selected"}" data-shown="">${ico("stack")}<span>All tasks</span>
          <span class="gh-spacer"></span><span class="caption num gh-muted">${rows.length}</span></button>
        ${body}
      </div>
    </aside>`;
  }

  /* ------------------------------------------------------------- body */

  /* ``gh`` ran but failed (a timeout, a crash): not a setup problem, so
   * no setup card, but the GitHub rows are only what the store kept. */
  const githubFailed = (d) => !!(d.github && d.github.status === "upstream-error");

  /* The blocked-source offer: gh's setup card for GitHub, a plain offer
   * for a configured-but-failing Linear (``disabled`` means off, not
   * broken, so it earns no card). */
  function sourceCards(d) {
    const cards = [];
    const setup = GhUI.envSetup(d.github);
    if (setup) cards.push(GhUI.setupCard(setup, d.machine));
    else if (githubFailed(d)) {
      cards.push(`<div class="card offer"><div class="offer-text">
          <div class="card-title">GitHub didn't answer</div>
          <p class="card-subtitle">${esc(d.github.error || "gh failed on the hub this refresh.")}</p></div>
        <button type="button" class="offer-button tk-source-refresh">Refresh</button></div>`);
    }
    const lin = d.linear;
    if (lin && lin.status !== "ok" && lin.status !== "disabled") {
      cards.push(`<div class="card offer"><div class="offer-text">
          <div class="card-title">Linear: ${esc(String(lin.status).replace(/-/g, " "))}</div>
          <p class="card-subtitle">${esc(lin.error || "Linear did not answer this refresh.")}</p></div>
        <button type="button" class="offer-button tk-source-refresh">Refresh</button></div>`);
    }
    return cards;
  }

  function listContent() {
    const d = page.data;
    if (!d) return loadingCard("Reading the task list…");
    const cards = sourceCards(d);
    const blocked = GhUI.envSetup(d.github) || githubFailed(d)
      || (d.linear && d.linear.status !== "ok" && d.linear.status !== "disabled");
    const stored = tasks().length;
    const inScope = (name) => !page.shown || name === page.shown;
    const rows = tasks().filter((t) => filterOf().test(t) && inScope(groupName(t)));
    const failed = failedRepos().filter((r) => inScope(r.name));
    // The refresh error outranks the empty states: an "empty" answer is
    // still an answer, and this is why the next read didn't land.
    const error = page.error ? `<div class="card"><div class="gh-error-row"><span class="error">${esc(page.error)}</span></div></div>` : "";
    if (!stored && !failedRepos().length) {
      // A blocked source with no store is the offer card alone; a fresh
      // hub is the empty state.
      if (blocked) return [...cards, error].join("");
      return [...cards, error, `<div class="card"><div class="gh-empty">${ico("stack", "gh-muted")}<span>No tasks yet. Tasks are the issues and pull requests your agents touch — resolve an issue or open a PR from Repositories, or link a running session to one.</span>
        <a class="gh-button" href="#/repositories">${ico("expand")}<span>Repositories</span></a></div></div>`].join("");
    }
    const failedRows = failedRepos().some((r) => tasks().some((t) => t.repo === r.name));
    const stale = (blocked || failedRows) && stored
      ? `<div class="caption gh-muted tk-stale">${ico("refresh", "gh-small")}<span>Last known state — a source isn't answering, so this is what the store remembers.</span></div>` : "";
    const body = rows.length || failed.length
      ? listGroups(rows, failed)
      : emptyCard(emptyText());
    return [...cards, stale, error, body].join("");
  }

  /* What an empty tab says, naming the rail's group when it narrowed. */
  function emptyText() {
    const place = page.shown;
    if (!place) return FILTER_EMPTY[page.filter] || "No tasks.";
    return {
      running: `Nothing is running in ${place}.`,
      pr_open: `No task in ${place} has a pull request open.`,
      done: `Nothing in ${place} is merged or closed yet.`,
    }[page.filter] || `No tasks in ${place}.`;
  }

  /* With the rail showing, Refresh rides in its head, as Repositories. */
  function listHeader(refresh) {
    return pageHeader("Tasks", "The issues and pull requests your agents are on.",
      `<div class="tabs" role="tablist" aria-label="Which tasks to list">${FILTERS.map((f) => {
        const n = tasks().filter(f.test).length;
        return `<button class="tab${f.id === page.filter ? " selected" : ""}" role="tab" aria-selected="${f.id === page.filter}"
          data-filter="${f.id}">${f.title} <span class="num">${n}</span></button>`;
      }).join("")}</div>
      ${refresh ? `<button class="icon-button${page.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>` : ""}`);
  }

  function paintList() {
    // No rail beside the setup card or the empty state: only rows earn one.
    const rail = !!page.data && (tasks().length > 0 || failedRepos().length > 0);
    document.getElementById("panel").innerHTML = `<div class="page gh-page">
      <div class="gh-dash${rail ? " with-rail" : ""}">
        <div class="gh-head">${listHeader(!rail)}</div>
        ${rail ? railHtml() : ""}
        <div class="gh-body stack">${listContent()}</div>
      </div></div>`;
    bindRefresh(() => load(true));
    GhUI.bindSetup(() => load(true));
    const panel = document.getElementById("panel");
    for (const btn of panel.querySelectorAll("[data-filter]")) {
      btn.onclick = () => { page.filter = btn.dataset.filter; paintList(); };
    }
    for (const row of panel.querySelectorAll("[data-shown]")) {
      row.onclick = () => { page.shown = row.dataset.shown || null; paintList(); };
    }
    for (const btn of panel.querySelectorAll("[data-quiet]")) {
      btn.onclick = () => {
        const id = btn.dataset.quiet;
        if (page.openQuiet.has(id)) page.openQuiet.delete(id); else page.openQuiet.add(id);
        paintList();
      };
    }
    for (const btn of panel.querySelectorAll("[data-expand]")) {
      btn.onclick = () => {
        const key = btn.dataset.expand;
        if (page.expanded.has(key)) page.expanded.delete(key); else page.expanded.add(key);
        paintList();
      };
    }
    // GitHub and Linear can each put up a Refresh card at once.
    for (const retry of document.querySelectorAll("#panel .tk-source-refresh")) {
      retry.onclick = () => load(true);
    }
  }

  /* ----------------------------------------------------------- detail */

  const sessionLogo = (s) => Fmt.logo(s.harness || s.agent || "", "logo tk-logo");
  const sessionName = (s) => Fmt.harnessName(s.harness || s.agent || "") || s.agent || "Session";

  /* One linked session, a chat link; a launch still on its way shows as
   * its own row so "Starting…" is visible before adoption lands. */
  function sessionRows(t) {
    const out = [];
    for (const l of t.launch_rows || []) {
      if (l.state === "pending") {
        out.push(`<div class="gh-note"><span class="gh-note-link tk-still">
          ${ico("running", "gh-warning gh-spin")}
          <span class="gh-row-main"><span class="gh-note-title">Starting ${esc(Fmt.harnessName(l.harness) || l.harness || "an agent")}…</span>
          <span class="caption gh-muted gh-ellipsis">${esc(l.note || "preparing")}</span></span></span></div>`);
      } else if (l.state === "failed") {
        out.push(`<div class="gh-note"><span class="gh-note-link tk-still">
          ${ico("xFill", "gh-danger")}
          <span class="gh-row-main"><span class="gh-note-title">The session didn't start</span>
          <span class="caption gh-muted gh-ellipsis">${esc(l.note || "launch failed")}</span></span></span></div>`);
      } else if (l.state === "untracked") {
        out.push(`<div class="gh-note"><span class="gh-note-link tk-still">
          ${ico("question", "gh-muted")}
          <span class="gh-row-main"><span class="gh-note-title">${esc(Fmt.harnessName(l.harness) || l.harness || "Harness")} started; Quiver can't read its sessions</span>
          <span class="caption gh-muted gh-ellipsis">It keeps running under tmux on the hub.</span></span></span></div>`);
      }
      // A "started" launch whose key is already a session row is that
      // row; one the catalog hasn't listed yet shows on the next read.
    }
    for (const s of t.session_rows || []) {
      if (s.gone) {
        out.push(`<div class="gh-note"><span class="gh-note-link tk-still">
          ${ico("terminal", "gh-muted")}
          <span class="gh-row-main"><span class="gh-note-title gh-muted">${esc(s.key)}</span>
          <span class="caption gh-muted">No longer in the session catalog.</span></span></span></div>`);
        continue;
      }
      const where = s.remote ? ` · ${esc(s.machine || "remote")}` : "";
      const when = s.last_active_at ? ` · ${Fmt.ago(s.last_active_at)}` : "";
      const busy = page.busy.has("unlink:" + s.key);
      out.push(`<div class="gh-note">
        <a class="gh-note-link" href="#/sessions?chat=${encodeURIComponent(s.key)}">${sessionLogo(s)}
          <span class="gh-row-main">
            <span class="gh-note-title">${esc(s.title || s.key)}</span>
            <span class="caption gh-muted gh-ellipsis">${esc(sessionName(s))} · ${esc(s.status || "")}${where}${when}</span>
          </span></a>
        <button type="button" class="icon-button gh-note-read${busy ? " spinning" : ""}" data-unlink="${esc(s.key)}"
          title="Unlink this session" aria-label="Unlink ${esc(s.title || s.key)}"${busy ? " disabled" : ""}>${ico(busy ? "running" : "minusCircle")}</button></div>`);
    }
    return out.length ? out.join("")
      : `<div class="gh-empty caption gh-muted">No session is linked to this task yet.</div>`;
  }

  /* The linked PR: an issue's ``quiver/issue-<n>`` PR, or a pull task
   * itself. The row deep-links into Repositories' PR sheet, the same
   * surface a PR row opens. */
  function prCard(t) {
    const pr = t.pr;
    if (!pr || pr.number == null) return "";
    const iconHtml = pr.draft ? ico("prDraft", "gh-muted")
      : pr.state === "merged" ? ico("prMerged", "gh-merged")
        : pr.state === "closed" ? ico("prClosed", "gh-danger")
          : ico("prOpen", "gh-success");
    const link = t.source === "github" && t.repo
      ? `#/repositories?repo=${encodeURIComponent(t.repo)}&pr=${encodeURIComponent(pr.number)}` : null;
    const caption = [pr.draft ? "Draft" : null, pr.state ? RUNG_WORDS[pr.state] || pr.state : null]
      .filter(Boolean).join(" · ");
    const row = `<span class="gh-row-lead">${iconHtml}</span><span class="gh-row-main">
      <span class="gh-row-title"><span class="gh-title">#${esc(pr.number)} ${esc(pr.title)}</span></span>
      ${caption ? `<span class="gh-row-sub">${esc(caption)}</span>` : ""}</span>`;
    const body = link
      ? `<a class="gh-note-link" href="${link}">${row}<span class="gh-row-side">${ico("chevronRight", "gh-muted")}</span></a>`
      : `<span class="gh-note-link">${row}${safeHref(pr.url)
          ? `<a class="icon-button gh-note-read" style="opacity:1" href="${safeHref(pr.url)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open on GitHub">${ico("external")}</a>` : ""}</span>`;
    return `<div class="card tk-card"><div class="card-title">${t.kind === "pull" ? "Pull request" : "Linked pull request"}</div>
      <div class="gh-note">${body}</div></div>`;
  }

  /* "New agent session" exists only where a launch route does: a github
   * issue resolves, a github pull opens. Both stamp the launch's
   * origin, so the session that lands links itself to this task. */
  const canLaunch = (t) => t.source === "github" && (t.kind === "issue" || t.kind === "pull") && t.repo;

  /* The launch routes take an optional ``model`` pick where the harness
   * reports ``launch_model``: the shared Model menu (model-menu.js)
   * offers its ``recent_models``, newest first by default, and Harness
   * default or an empty Other… sends no ``model`` key. */
  const pickedHarness = (x) => (x.harnesses || []).find((h) => h.name === x.harness) || null;
  const recentModels = (x) => ModelMenu.harnessRecents(pickedHarness(x));

  function newPanel(t) {
    const x = page.session;
    if (page.action !== "new" || !x) return "";
    const busy = !!x.phase;
    let picker;
    if (x.harnesses === null) {
      picker = `<div class="caption gh-muted">${ico("running", "gh-spin gh-small")} Reading what the hub can start…</div>`;
    } else if (!x.harnesses.length) {
      picker = `<div class="caption gh-muted">No harness can start on the hub: install one there, or turn it on in Settings › Providers.</div>`;
    } else {
      picker = `<select class="field" id="tk-harness" aria-label="Harness"${busy || x.started ? " disabled" : ""}>${
        x.harnesses.map((h) => `<option value="${esc(h.name)}"${h.name === x.harness ? " selected" : ""}>${esc(h.display_name)}</option>`).join("")}</select>`;
    }
    const picked = pickedHarness(x);
    const model = picked && picked.launch_model
      ? ModelMenu.html(x.model, recentModels(x), "tk-model", busy || x.started)
      : "";
    const where = (page.data && page.data.machine) ? ` on ${esc(page.data.machine)}` : " on the hub";
    const what = `${t.kind === "pull" ? "PR" : "issue"} ${refLabel(t)}`;
    return `<div class="gh-confirm" role="group" aria-label="New agent session">
      <div class="gh-confirm-text">Start an agent on <strong>${esc(what)}</strong>${where} — the session links itself here when it lands.</div>
      ${picker}
      ${model}
      <textarea class="field gh-session-prompt" id="tk-prompt" rows="3" aria-label="First message"
        placeholder="Its first message — leave blank for the hub's built prompt"${busy || x.started ? " disabled" : ""}>${esc(x.prompt)}</textarea>
      <div class="caption gh-muted">It runs under tmux in a worktree the hub prepares. The chat opens once the session shows up.</div>
      ${x.error ? `<div class="caption gh-danger-text" role="alert">${esc(x.error)}</div>` : ""}
      ${x.note ? `<div class="caption" role="status">${esc(x.note)}</div>` : ""}
      <div class="gh-confirm-buttons">
        <button type="button" class="gh-button" id="tk-new-cancel">${x.started ? "Close" : "Cancel"}</button>
        <button type="button" class="gh-button gh-primary" id="tk-new-start"${x.harnesses && x.harnesses.length && !busy && !x.started ? "" : " disabled"}>${esc(x.phase || "Start")}</button>
      </div></div>`;
  }

  function linkPanel(t) {
    const x = page.link;
    if (page.action !== "link" || !x) return "";
    const linked = new Set(t.sessions || []);
    const q = (x.filter || "").trim().toLowerCase();
    const found = (x.sessions || []).filter((s) => !linked.has(s.key)
      && (!q || [s.title, s.key, s.status, s.harness, s.agent, s.machine, s.cwd]
        .some((v) => v && String(v).toLowerCase().includes(q))));
    let list;
    if (x.sessions === null) {
      list = `<div class="caption gh-muted">${ico("running", "gh-spin gh-small")} Reading sessions…</div>`;
    } else if (!x.sessions.length) {
      list = `<div class="caption gh-muted">No session is running on the hub.</div>`;
    } else if (!found.length) {
      list = `<div class="caption gh-muted">${q ? `Nothing matches "${esc(q)}".` : "Every session is already linked here."}</div>`;
    } else {
      list = `<div class="tk-pick">${found.slice(0, 30).map((s) => {
        const busy = page.busy.has("link:" + s.key);
        const where = s.remote ? ` · ${esc(s.machine || "remote")}` : "";
        const when = s.last_active_at ? ` · ${Fmt.ago(s.last_active_at)}` : "";
        return `<div class="gh-note"><span class="gh-note-link tk-still">
          ${sessionLogo(s)}
          <span class="gh-row-main"><span class="gh-note-title">${esc(s.title || s.key)}</span>
          <span class="caption gh-muted gh-ellipsis">${esc(sessionName(s))} · ${esc(s.status || "")}${where}${when}</span></span></span>
          <button type="button" class="gh-button tk-link-go" data-link="${esc(s.key)}"${busy ? " disabled" : ""}>${busy ? "Linking…" : "Link"}</button></div>`;
      }).join("")}</div>`;
    }
    return `<div class="gh-confirm" role="group" aria-label="Link session">
      <div class="gh-confirm-text">Link a session to this task — it counts as work on it until you unlink it.</div>
      <input class="field" id="tk-link-filter" type="search" placeholder="Filter sessions" value="${esc(x.filter)}"
        autocomplete="off" spellcheck="false" aria-label="Filter sessions">
      ${list}
      ${x.error ? `<div class="caption gh-danger-text" role="alert">${esc(x.error)}</div>` : ""}
      <div class="gh-confirm-buttons"><button type="button" class="gh-button" id="tk-link-cancel">Cancel</button></div></div>`;
  }

  function detailHeader(t) {
    const where = t.repo || t.project || "";
    return pageHeader(t.title || refLabel(t),
      `${refLabel(t)} · ${where} · ${statusOf(t)}`,
      `<a class="gh-button" href="#/tasks" title="Back to the task list">${ico("chevronLeft")}<span>Tasks</span></a>
       <button class="icon-button${page.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>
       ${safeHref(t.url) ? `<a class="icon-button" href="${safeHref(t.url)}" target="_blank" rel="noopener" title="Open on ${t.source === "github" ? "GitHub" : esc(t.source)}" aria-label="Open on ${esc(t.source)}">${ico("external")}</a>` : ""}`);
  }

  function detailContent(t) {
    const err = page.actionError || page.error;
    return `${detailHeader(t)}<div class="stack">
      ${err ? `<div class="card"><div class="gh-error-row"><span class="error">${esc(err)}</span></div></div>` : ""}
      <div class="card tk-card">${stepper(t)}</div>
      <div class="card tk-card"><div class="card-title">Sessions</div>
        ${sessionRows(t)}
        ${newPanel(t)}${linkPanel(t)}
        <div class="tk-actions">
          ${canLaunch(t) ? `<button type="button" class="gh-button gh-primary" id="tk-new"${page.action ? " disabled" : ""}>${ico("terminal")}<span>New agent session</span></button>` : ""}
          <button type="button" class="gh-button" id="tk-link"${page.action ? " disabled" : ""}>${ico("plus")}<span>Link session</span></button>
        </div></div>
      ${prCard(t)}
    </div>`;
  }

  function paintDetail() {
    const id = selectedId();
    const t = id && task(id);
    let body;
    if (!page.data) {
      body = `${pageHeader("Tasks", "The issues and pull requests your agents are on.")}${loadingCard("Reading the task list…")}`;
    } else if (!t) {
      body = `${pageHeader("Task", "The issues and pull requests your agents are on.",
        `<a class="gh-button" href="#/tasks" title="Back to the task list">${ico("chevronLeft")}<span>Tasks</span></a>`)}
        <div class="card"><div class="gh-empty">${ico("question", "gh-muted")}<span>This task isn't listed any more — it may have closed or left the pinned repos.</span></div>
        <div class="gh-inline"><a class="gh-button" href="#/tasks">All tasks</a></div></div>`;
    } else {
      body = detailContent(t);
    }
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${body}</div>`;
    bindDetail(t);
  }

  function paint() {
    if (selectedId()) paintDetail(); else paintList();
  }

  /* ---------------------------------------------------------- actions */

  async function openNew(t) {
    const x = { harnesses: null, harness: null, model: ModelMenu.fresh(),
      phase: null, error: null, note: null, started: false, prompt: "" };
    page.action = "new";
    page.link = null;
    page.session = x;
    paint();
    const prompt = document.getElementById("tk-prompt");
    if (prompt) prompt.focus();
    const { status, data } = await api("/v1/app/tasks?harnesses=1");
    if (page.session !== x || !onTasks()) return;
    x.harnesses = status === 200 && data ? data.harnesses || [] : [];
    if (status !== 200) x.error = GhUI.failMessage(status, data);
    x.harness = x.harnesses.length ? x.harnesses[0].name : null;
    ModelMenu.redefault(x.model, recentModels(x));
    paint();
  }

  async function startNew(t) {
    const x = page.session;
    if (!x || x.phase || x.started || !x.harness) return;
    const alive = () => onTasks() && page.session === x;
    const say = (fields) => { Object.assign(x, fields); if (alive()) paint(); };
    say({ phase: "Starting…", error: null, note: null });
    const body = { harness: x.harness, prompt: x.prompt.trim() };
    const h = pickedHarness(x);
    const model = ModelMenu.value(x.model);
    if (h && h.launch_model && model) body.model = model;
    const [owner, name] = (t.repo || "/").split("/", 2);
    const launched = t.kind === "pull"
      ? await GhUI.post(`/v1/pulls/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${encodeURIComponent(t.ref)}/open`, body)
      : await GhUI.post("/v1/issues/resolve", { ...body, repo: t.repo, number: Number(t.ref) });
    if (!alive()) return;
    const id = launched.data && launched.data.launch_id;
    if ((launched.status !== 202 && launched.status !== 200) || !id) {
      return say({ phase: null, error: launched.status === 401
        ? CREDENTIAL_REFUSED
        : GhUI.failMessage(launched.status, launched.data) });
    }
    say({ phase: "Waiting for the session…" });
    let launch = launched.data;
    for (let polls = 0; ; polls++) {
      if (launch.state === "started" && launch.key) {
        location.hash = `#/sessions?chat=${encodeURIComponent(launch.key)}`;
        return;
      }
      if (launch.state === "untracked") {
        return say({ phase: null, started: true,
          note: "Started. Quiver can't read this harness's sessions yet, so there's no chat to open. It keeps running under tmux on the hub." });
      }
      if (launch.state === "failed") {
        return say({ phase: null, error: launch.note ? `The session didn't start: ${launch.note}` : "The session didn't start." });
      }
      if (polls === LAUNCH_POLLS) break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
      if (!alive()) return;
      const { status, data } = await api(`/v1/launches/${encodeURIComponent(id)}`);
      if (!alive()) return;
      if (status === 404) return say({ phase: null, error: GhUI.failMessage(status, data) });
      if (status === 200 && data) launch = data;
    }
    say({ phase: null, started: true,
      note: "It started but hasn't shown up yet. It'll appear in Sessions when it does." });
  }

  async function openLink() {
    const x = { sessions: null, filter: "", error: null };
    page.action = "link";
    page.session = null;
    page.link = x;
    paint();
    const { status, data } = await api("/v1/sessions");
    if (page.link !== x || !onTasks()) return;
    if (status === 200 && data) {
      const act = (s) => String(s.status || "") === "active";
      x.sessions = (data.sessions || []).slice()
        .sort((a, b) => (act(b) - act(a)) || (b.last_active_at || 0) - (a.last_active_at || 0));
    } else {
      x.sessions = [];
      x.error = status === 401
        ? CREDENTIAL_REFUSED
        : GhUI.failMessage(status, data);
    }
    paint();
  }

  async function linkSession(t, key) {
    if (page.busy.has("link:" + key)) return;
    const x = page.link;
    page.busy.add("link:" + key);
    if (x) x.error = null;
    paint();
    const { status, data } = await GhUI.post(
      `/v1/sessions/${encodeURIComponent(key)}/task`, { task_ref: refOf(t) });
    page.busy.delete("link:" + key);
    /* The reply may land after the panel was swapped for another (or the
     * user moved to another task): only the panel that asked is touched. */
    if (status !== 200 || !data) {
      if (x && page.link === x) {
        x.error = status === 401
          ? CREDENTIAL_REFUSED
          : GhUI.failMessage(status, data);
        if (onTasks()) paint();
      }
      return;
    }
    if (page.link === x) {
      page.action = null;
      page.link = null;
    }
    // The hub reads the store on every call; only gh's lists are cached,
    // and a link doesn't change them.
    if (onTasks()) load();
  }

  async function unlinkSession(t, key) {
    if (page.busy.has("unlink:" + key)) return;
    page.busy.add("unlink:" + key);
    page.actionError = null;
    paint();
    const { status, data } = await del_(
      `/v1/sessions/${encodeURIComponent(key)}/task`, { task_ref: refOf(t) });
    page.busy.delete("unlink:" + key);
    if (status !== 200 || !data) {
      /* The error belongs to this task's detail, not the one the user
       * may have moved to. */
      if (onTasks() && selectedId() === t.id) {
        page.actionError = status === 401
          ? CREDENTIAL_REFUSED
          : GhUI.failMessage(status, data);
        paint();
      }
      return;
    }
    if (onTasks()) load();
  }

  function bindDetail(t) {
    bindRefresh(() => load(true));
    for (const btn of document.querySelectorAll("#panel [data-unlink]")) {
      btn.onclick = (e) => { e.preventDefault(); if (t) unlinkSession(t, btn.dataset.unlink); };
    }
    const t2 = t || task(selectedId());
    const newer = document.getElementById("tk-new");
    if (newer && t2) newer.onclick = () => openNew(t2);
    const linker = document.getElementById("tk-link");
    if (linker && t2) linker.onclick = openLink;
    const cancelNew = document.getElementById("tk-new-cancel");
    if (cancelNew) cancelNew.onclick = () => { page.action = null; page.session = null; paint(); };
    const start = document.getElementById("tk-new-start");
    if (start && t2) start.onclick = () => startNew(t2);
    const harness = document.getElementById("tk-harness");
    if (harness && page.session) harness.onchange = () => {
      const x = page.session;
      if (!x) return;
      x.harness = harness.value;
      // Re-default the model menu to this harness's newest unless the
      // user picked; the menu may appear or go away.
      ModelMenu.redefault(x.model, recentModels(x));
      paint();
      const again = document.getElementById("tk-harness");
      if (again) again.focus();
    };
    if (page.session) {
      ModelMenu.bind(document.getElementById("panel"), page.session.model, recentModels(page.session), "tk-model", paint);
    }
    const prompt = document.getElementById("tk-prompt");
    if (prompt && page.session) prompt.oninput = () => { page.session.prompt = prompt.value; };
    const cancelLink = document.getElementById("tk-link-cancel");
    if (cancelLink) cancelLink.onclick = () => { page.action = null; page.link = null; paint(); };
    const filter = document.getElementById("tk-link-filter");
    if (filter && page.link) {
      filter.oninput = () => { page.link.filter = filter.value; GhUI.keepFocus("tk-link-filter", paint); };
    }
    for (const btn of document.querySelectorAll("#panel [data-link]")) {
      btn.onclick = () => { if (t2) linkSession(t2, btn.dataset.link); };
    }
  }

  /* ------------------------------------------------------------ load */

  async function load(refresh = false) {
    const gen = routeGen;
    const seq = ++page.seq;
    page.loading = true;
    paint();
    const { status, data } = await api(`/v1/app/tasks${refresh ? "?refresh=1" : ""}`);
    if (gen !== routeGen || seq !== page.seq) return;
    page.loading = false;
    if (status === 401) return GhUI.gate("tasks");
    if (status !== 200 || !data) {
      // Keep the last good rows under the error, as the other pages do.
      if (!page.data) {
        // The error says "then refresh", so the header carries the button.
        return pageError(pageHeader("Tasks", "The issues and pull requests your agents are on.",
          `<button class="icon-button" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>`),
          status, data, () => load(true));
      }
      page.error = GhUI.failMessage(status, data);
      return paint();
    }
    page.data = data;
    page.error = null;
    // The rail's group went away (all closed out, unpinned): back to All.
    if (page.shown && !tasks().some((t) => groupName(t) === page.shown)
      && !failedRepos().some((r) => r.name === page.shown)) page.shown = null;
    paint();
  }

  /* A read or write running while a panel is open must not yank the
   * controls mid-prompt; the poll skips then. */
  const panelBusy = () => page.action || page.busy.size;

  function render() {
    const id = selectedId();
    if (id !== page.forTask) {
      page.forTask = id;
      page.action = null;
      page.session = null;
      page.link = null;
      page.actionError = null;
    }
    load();
    GhUI.poll("tasks", () => {
      if (page.loading || panelBusy()) return false;
      load();
    });
  }

  window.addEventListener("hashchange", () => {
    if (!onTasks()) { page.action = null; page.session = null; page.link = null; }
  });

  registerPage("tasks", render);
})();
