/* Tasks: the work items the hub's agents are on — the task store over
 * the pinned repos' open issues and PRs (and Linear's, when a key is
 * configured). The list groups by repository under All / Running /
 * PR open / Done tabs; a task's detail (#/tasks?task=<id>) shows the
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
   * issue's ``quiver/issue-<n>`` PR, or a pull task itself; "Done" is
   * merged or closed. */
  const FILTERS = [
    { id: "all", title: "All", test: () => true },
    { id: "running", title: "Running", test: (t) => !!t.agent_running },
    {
      id: "pr_open", title: "PR open",
      test: (t) => t.state === "pr_open"
        || (t.kind === "pull" && t.pr && t.pr.state === "open"),
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

  const STATE_WORDS = {
    open: "Open", in_progress: "In progress", pr_open: "PR open",
    merged: "Merged", closed: "Closed",
  };
  const LAUNCH_POLLS = 60;

  const page = {
    data: null, status: 0, error: null, loading: false,
    filter: "all", busy: new Set(), actionError: null,
    seq: 0, forTask: null,
    // The detail's two panels; at most one is open at a time.
    action: null,      // "new" | "link"
    session: null,     // the New agent session panel's state
    link: null,        // the Link session panel's state
  };

  const tasks = () => (page.data && page.data.tasks) || [];
  const task = (id) => tasks().find((t) => t.id === id) || null;
  const selectedId = () => GhUI.hashParams().get("task");
  const onTasks = () => (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "tasks";

  /* A github task groups under its repo; another source's task under its
   * project or the source's name. */
  const groupName = (t) => t.repo || t.project
    || { linear: "Linear", notion: "Notion" }[t.source] || t.source;
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

  /* GhUI.post only POSTs; the unlink route is a DELETE with the same
   * JSON shape. Never throws: {status: 0} when the hub is unreachable. */
  async function del_(path, body) {
    let resp;
    try {
      resp = await fetch(path, {
        method: "DELETE",
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
      <span class="tk-step ${cls}" role="listitem"${i === at ? ' aria-current="step"' : ""}>${mark}<span>${STATE_WORDS[r]}</span></span>`;
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
        <span class="gh-row-sub">${esc(refLabel(t))} · ${esc(STATE_WORDS[t.state] || t.state)}${when}</span>
      </span>
      <span class="gh-row-side">${t.agent_running
        ? `<span class="gh-label gh-success tk-running">${ico("running", "gh-spin gh-small")}<span>Agent running</span></span>` : ""}
        ${sessions ? badge(sessions) : ""}</span>
    </a>`;
  }

  function groupHead(name, repo, count) {
    if (repo) {
      const [owner, short] = repo.includes("/") ? repo.split("/", 2) : [null, repo];
      return `<div class="gh-group-head">
        ${owner ? `<span class="gh-muted">${esc(owner)} /</span>` : ""}<span>${esc(short)}</span>
        <span class="caption num gh-muted">${count}</span><span class="gh-spacer"></span>
        <span class="gh-head-links">
          <a href="https://github.com/${esc(repo)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open ${esc(repo)} on GitHub">${ico("external")}</a>
          <a href="#/repositories?repo=${encodeURIComponent(repo)}" title="Open ${esc(repo)} in Repositories" aria-label="Open ${esc(repo)} in Repositories">${ico("expand")}</a>
        </span></div>`;
    }
    return `<div class="gh-group-head"><span class="gh-muted">${esc(name)}</span>
      <span class="caption num gh-muted">${count}</span></div>`;
  }

  /* Rows grouped by repository, in the list's own order (last activity
   * first); a repo whose read failed gets a group with its error. */
  function listGroups(rows, failed) {
    const seen = new Map();
    for (const t of rows) {
      const name = groupName(t);
      if (!seen.has(name)) seen.set(name, { name, repo: t.source === "github" ? t.repo : null, rows: [] });
      seen.get(name).rows.push(t);
    }
    const groups = [...seen.values()];
    for (const r of failed) groups.push({ name: r.name, repo: r.name, error: r.error || r.status });
    const last = groups.length - 1;
    return `<div class="gh-box">${groups.map((g, i) => `${groupHead(g.name, g.repo, g.rows ? g.rows.length : "")}
      <div class="gh-surface${i === last ? " last" : ""}">${g.error
        ? `<div class="gh-error-row"><span class="error">${esc(g.error)}</span></div>`
        : g.rows.map(taskRow).join("")}</div>`).join("")}</div>`;
  }

  /* The blocked-source offer: gh's setup card for GitHub, a plain offer
   * for a configured-but-failing Linear (``disabled`` means off, not
   * broken, so it earns no card). */
  function sourceCards(d) {
    const cards = [];
    const setup = GhUI.envSetup(d.github);
    if (setup) cards.push(GhUI.setupCard(setup, d.machine));
    const lin = d.linear;
    if (lin && lin.status !== "ok" && lin.status !== "disabled") {
      cards.push(`<div class="card offer"><div class="offer-text">
          <div class="card-title">Linear: ${esc(String(lin.status).replace(/-/g, " "))}</div>
          <p class="card-subtitle">${esc(lin.error || "Linear did not answer this refresh.")}</p></div>
        <button type="button" class="offer-button" id="tk-source-refresh">Refresh</button></div>`);
    }
    return cards;
  }

  function listContent() {
    const d = page.data;
    if (!d) return loadingCard("Reading the task list…");
    const cards = sourceCards(d);
    const blocked = GhUI.envSetup(d.github) || (d.linear && d.linear.status !== "ok" && d.linear.status !== "disabled");
    const stored = tasks().length;
    const rows = tasks().filter(filterOf().test);
    const failed = (d.repos || []).map((r) => ({ name: r.repo || r.name, error: r.error || r.status }));
    if (!stored && !failed.length) {
      // A blocked source with no store is the offer card alone; a fresh
      // hub is the empty state.
      if (blocked) return cards.join("");
      return [...cards, `<div class="card"><div class="gh-empty">${ico("stack", "gh-muted")}<span>No tasks yet. Tasks are the issues and pull requests your agents touch — resolve an issue or open a PR from Repositories, or link a running session to one.</span>
        <a class="gh-button" href="#/repositories">${ico("expand")}<span>Repositories</span></a></div></div>`].join("");
    }
    const stale = blocked && stored
      ? `<div class="caption gh-muted tk-stale">${ico("refresh", "gh-small")}<span>Last known state — a source isn't answering, so this is what the store remembers.</span></div>` : "";
    const error = page.error ? `<div class="card"><div class="gh-error-row"><span class="error">${esc(page.error)}</span></div></div>` : "";
    const body = rows.length || failed.length
      ? listGroups(rows, failed)
      : emptyCard(FILTER_EMPTY[page.filter] || "No tasks.");
    return [...cards, stale, error, body].join("");
  }

  function listHeader() {
    return pageHeader("Tasks", "The issues and pull requests your agents are on.",
      `<div class="tabs" role="tablist" aria-label="Which tasks to list">${FILTERS.map((f) => {
        const n = tasks().filter(f.test).length;
        return `<button class="tab${f.id === page.filter ? " selected" : ""}" role="tab" aria-selected="${f.id === page.filter}"
          data-filter="${f.id}">${f.title} <span class="num">${n}</span></button>`;
      }).join("")}</div>
      <button class="icon-button${page.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>`);
  }

  function paintList() {
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${listHeader()}<div class="stack">${listContent()}</div></div>`;
    bindRefresh(() => load(true));
    GhUI.bindSetup(() => load(true));
    for (const btn of document.querySelectorAll("#panel [data-filter]")) {
      btn.onclick = () => { page.filter = btn.dataset.filter; paintList(); };
    }
    const retry = document.getElementById("tk-source-refresh");
    if (retry) retry.onclick = () => load(true);
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
    const caption = [pr.draft ? "Draft" : null, pr.state ? STATE_WORDS[pr.state] || pr.state : null]
      .filter(Boolean).join(" · ");
    const row = `<span class="gh-row-lead">${iconHtml}</span><span class="gh-row-main">
      <span class="gh-row-title"><span class="gh-title">#${esc(pr.number)} ${esc(pr.title)}</span></span>
      ${caption ? `<span class="gh-row-sub">${esc(caption)}</span>` : ""}</span>`;
    const body = link
      ? `<a class="gh-note-link" href="${link}">${row}<span class="gh-row-side">${ico("chevronRight", "gh-muted")}</span></a>`
      : `<span class="gh-note-link">${row}${pr.url
          ? `<a class="icon-button gh-note-read" style="opacity:1" href="${esc(pr.url)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open on GitHub">${ico("external")}</a>` : ""}</span>`;
    return `<div class="card tk-card"><div class="card-title">${t.kind === "pull" ? "Pull request" : "Linked pull request"}</div>
      <div class="gh-note">${body}</div></div>`;
  }

  /* "New agent session" exists only where a launch route does: a github
   * issue resolves, a github pull opens. Both stamp the launch's
   * origin, so the session that lands links itself to this task. */
  const canLaunch = (t) => t.source === "github" && (t.kind === "issue" || t.kind === "pull") && t.repo;

  function newPanel(t) {
    const x = page.session;
    if (page.action !== "new" || !x) return "";
    const busy = !!x.phase;
    let picker;
    if (x.harnesses === null) {
      picker = `<div class="caption gh-muted">${ico("running", "gh-spin gh-small")} Reading what the hub can start…</div>`;
    } else if (!x.harnesses.length) {
      picker = `<div class="caption gh-muted">No harness can start on the hub: install one there, or turn it on in Settings › Provider visibility.</div>`;
    } else {
      picker = `<select class="field" id="tk-harness" aria-label="Harness"${busy || x.started ? " disabled" : ""}>${
        x.harnesses.map((h) => `<option value="${esc(h.name)}"${h.name === x.harness ? " selected" : ""}>${esc(h.display_name)}</option>`).join("")}</select>`;
    }
    const where = (page.data && page.data.machine) ? ` on ${esc(page.data.machine)}` : " on the hub";
    const what = `${t.kind === "pull" ? "PR" : "issue"} ${refLabel(t)}`;
    return `<div class="gh-confirm" role="group" aria-label="New agent session">
      <div class="gh-confirm-text">Start an agent on <strong>${esc(what)}</strong>${where} — the session links itself here when it lands.</div>
      ${picker}
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
      `${refLabel(t)} · ${where} · ${STATE_WORDS[t.state] || t.state}`,
      `<a class="gh-button" href="#/tasks" title="Back to the task list">${ico("chevronLeft")}<span>Tasks</span></a>
       <button class="icon-button${page.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>
       ${t.url ? `<a class="icon-button" href="${esc(t.url)}" target="_blank" rel="noopener" title="Open on ${t.source === "github" ? "GitHub" : esc(t.source)}" aria-label="Open on ${esc(t.source)}">${ico("external")}</a>` : ""}`);
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
    const x = { harnesses: null, harness: null, phase: null, error: null, note: null, started: false, prompt: "" };
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
    paint();
  }

  async function startNew(t) {
    const x = page.session;
    if (!x || x.phase || x.started || !x.harness) return;
    const alive = () => onTasks() && page.session === x;
    const say = (fields) => { Object.assign(x, fields); if (alive()) paint(); };
    say({ phase: "Starting…", error: null, note: null });
    const body = { harness: x.harness, prompt: x.prompt.trim() };
    const [owner, name] = (t.repo || "/").split("/", 2);
    const launched = t.kind === "pull"
      ? await GhUI.post(`/v1/pulls/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${encodeURIComponent(t.ref)}/open`, body)
      : await GhUI.post("/v1/issues/resolve", { ...body, repo: t.repo, number: Number(t.ref) });
    if (!alive()) return;
    const id = launched.data && launched.data.launch_id;
    if ((launched.status !== 202 && launched.status !== 200) || !id) {
      return say({ phase: null, error: launched.status === 401
        ? "The hub doesn't know this browser's credential. It may have been revoked."
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
        ? "The hub doesn't know this browser's credential. It may have been revoked."
        : GhUI.failMessage(status, data);
    }
    paint();
  }

  async function linkSession(t, key) {
    if (page.busy.has("link:" + key)) return;
    page.busy.add("link:" + key);
    if (page.link) page.link.error = null;
    paint();
    const { status, data } = await GhUI.post(
      `/v1/sessions/${encodeURIComponent(key)}/task`, { task_ref: refOf(t) });
    page.busy.delete("link:" + key);
    if (status !== 200 || !data) {
      if (page.link) {
        page.link.error = status === 401
          ? "The hub doesn't know this browser's credential. It may have been revoked."
          : GhUI.failMessage(status, data);
        if (onTasks()) paint();
      }
      return;
    }
    page.action = null;
    page.link = null;
    if (onTasks()) load(true);
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
      page.actionError = status === 401
        ? "The hub doesn't know this browser's credential. It may have been revoked."
        : GhUI.failMessage(status, data);
      if (onTasks()) paint();
      return;
    }
    if (onTasks()) load(true);
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
    if (harness && page.session) harness.onchange = () => { page.session.harness = harness.value; };
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
    if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    if (status !== 200 || !data) {
      // Keep the last good rows under the error, as the other pages do.
      if (!page.data) {
        return pageError(pageHeader("Tasks", "The issues and pull requests your agents are on."),
          status, data, () => load(true));
      }
      page.error = GhUI.failMessage(status, data);
      return paint();
    }
    page.data = data;
    page.status = status;
    page.error = null;
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
