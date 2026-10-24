/* Repositories: ports of the Mac app's RepositoriesPage (the dashboard of
 * pinned repos with its rail), RepoDetailPage (one repo, every state, at
 * #/repositories?repo=owner/name) and PrDetailSheet (a pull request's
 * checks, reviews and body, and its one write: merge with a strategy or
 * arm auto-merge, behind an inline confirm). Re-running, pinning,
 * reordering and PR worktrees stay in swe and the Mac app. */
"use strict";

(() => {
  const ico = GhUI.ico;

  const TABS = [
    { id: "pulls", title: "Pull requests", key: "pull_requests" },
    { id: "issues", title: "Issues", key: "issues" },
    { id: "actions", title: "Actions", key: "runs" },
    { id: "pages", title: "Pages", key: "pages" },
    { id: "releases", title: "Releases", key: "releases" },
    { id: "tags", title: "Tags", key: "tags" },
    { id: "branches", title: "Branches", key: "branches" },
  ];
  const tabOf = (id) => TABS.find((t) => t.id === id) || TABS[0];
  /* GitHubModel.tabs(oneRepo:): every surface for one repo; across all
   * repos only the three that read well merged into one list. */
  const tabsFor = (oneRepo) => (oneRepo ? TABS : TABS.slice(0, 3));

  const page = {
    data: null, error: null, loading: false,
    tab: "pulls", shown: null, filter: "",
    expanded: new Set(), openQuiet: new Set(),
    focus: null, sheet: null, observer: null,
  };

  const BATCH = 10; // rows each scroll fill reveals on the focus page
  const STEP = 50; // how much wider each further fetch asks for

  /* ---------------------------------------------------------- search */

  const BOTS = new Set(["dependabot", "dependabot-preview", "renovate", "github-actions", "pre-commit-ci", "snyk-bot"]);
  function isBot(login) {
    let name = String(login || "").toLowerCase();
    if (name.startsWith("app/")) name = name.slice(4);
    if (name.endsWith("[bot]")) name = name.slice(0, -5);
    return BOTS.has(name);
  }
  const FAILING = new Set(["failure", "timed_out", "startup_failure"]);
  const runFailed = (r) => FAILING.has(r.conclusion);
  const runRunning = (r) => r.status === "in_progress" || r.status === "queued";

  /* What each row's search matches (the app's searchText): the row's
   * fields plus words for its state, so "failing", "draft", "conflict",
   * "approved" or "bot" find what the icons mark. */
  const PARTS = {
    pull_requests: (pr) => {
      const w = [pr.title, `${pr.number}`, `#${pr.number}`, pr.branch, pr.base, pr.author, ...(pr.labels || [])];
      w.push(pr.draft ? "draft" : pr.state);
      w.push({ passing: "passing", failing: "failing failed", running: "running pending" }[pr.checks]);
      if (pr.mergeable === "conflicting") w.push("conflicts merge conflict");
      w.push({ approved: "approved", changes_requested: "changes requested",
        review_required: "review needed required" }[pr.review_decision]);
      if (isBot(pr.author)) w.push("bot");
      return w;
    },
    issues: (i) => [i.title, `${i.number}`, `#${i.number}`, i.author, i.state, ...(i.labels || []),
      ...(i.assignees || []), isBot(i.author) ? "bot" : null],
    runs: (r) => [r.title, r.workflow, r.branch, r.event, r.conclusion, r.status,
      runFailed(r) ? "failing failed" : null, runRunning(r) ? "running" : null],
    releases: (r) => [r.name, r.tag],
    tags: (t) => [t.name, t.sha],
    branches: (b) => [b.name, b.sha],
  };
  const contains = (text, q) => text != null && String(text).toLowerCase().includes(q.toLowerCase());
  const matches = (parts, q) => !q || parts.some((p) => contains(p, q));

  /* A surface's Loadable: loading, failed(message) or loaded(rows). */
  function surfaceState(surfaces, key) {
    const e = surfaces && surfaces[key];
    if (!e) return { kind: "loading" };
    if (e.status !== "ok") return { kind: "failed", message: e.error || e.status };
    return { kind: "loaded", rows: key === "pages" ? e.pages : (e[key] || []) };
  }

  function filtered(state, key, q, unfiltered) {
    if (state.kind !== "loaded" || unfiltered || !q || key === "pages") return state;
    return { kind: "loaded", rows: state.rows.filter((r) => matches(PARTS[key](r), q)) };
  }

  /* ---------------------------------------------------------- dashboard model */

  const query = () => page.filter.trim();
  const pins = () => (page.data && page.data.repos) || [];
  const boardOf = (repo) => ((page.data && page.data.boards[repo]) || {}).surfaces;

  /* A query matching repo names narrows to those; one matching none is
   * a row search across every pin. */
  function visibleRepos() {
    const q = query();
    if (!q) return pins();
    const named = pins().filter((p) => contains(p.name, q));
    return named.length ? named : pins();
  }
  const showsAllRows = (repo) => !query() || contains(repo, query());
  const listedRepos = () => (page.shown ? pins().filter((p) => p.name === page.shown) : visibleRepos());

  /* Rows the list shows for a repo on a tab; null while not loaded. */
  function rowCount(repo, tabId) {
    const key = tabOf(tabId).key;
    const state = surfaceState(boardOf(repo), key);
    if (state.kind !== "loaded") return null;
    if (key === "pages") return state.rows ? 1 : 0;
    return filtered(state, key, query(), showsAllRows(repo)).rows.length;
  }

  /* The rail's CI dot: the newest run on the default branch (or the
   * newest run when the default is unknown). */
  function ciState(repo) {
    const surfaces = boardOf(repo);
    const runs = surfaceState(surfaces, "runs");
    if (runs.kind !== "loaded" || !runs.rows.length) return null;
    const branches = surfaceState(surfaces, "branches");
    const main = branches.kind === "loaded" ? (branches.rows.find((b) => b.default) || {}).name : null;
    let run = runs.rows[0];
    if (main) {
      run = runs.rows.find((r) => r.branch === main);
      if (!run) return "unknown";
    }
    if (runRunning(run)) return "running";
    if (run.conclusion === "success") return "passing";
    return runFailed(run) ? "failing" : "unknown";
  }

  /* ---------------------------------------------------------- marks */

  function prIcon(pr) {
    if (pr.draft) return ico("prDraft", "gh-muted", "Draft");
    if (pr.state === "merged") return ico("prMerged", "gh-merged", "Merged");
    if (pr.state === "closed") return ico("prClosed", "gh-danger", "Closed");
    return ico("prOpen", "gh-success", "Open");
  }

  /* PrStateIcons: conflicts, review, then CI; the words on hover. */
  function prStateIcons(pr) {
    const out = [];
    if (pr.mergeable === "conflicting") out.push(ico("alert", "gh-warn-text gh-big", "Merge conflicts: this branch has merge conflicts to resolve."));
    const review = {
      approved: ["sealFill", "gh-success", "Approved: a reviewer approved this pull request."],
      changes_requested: ["pencilFill", "gh-danger", "Changes requested: a reviewer asked for changes."],
      review_required: ["eyeFill", "gh-warning", "Review needed: needs an approving review before it can merge."],
    }[pr.review_decision];
    if (review) out.push(ico(review[0], `${review[1]} gh-big`, review[2]));
    const ready = !pr.draft && pr.mergeable === "mergeable"
      && pr.review_decision !== "changes_requested" && pr.review_decision !== "review_required";
    const checks = {
      passing: ["checkFill", "gh-success", ready ? "Ready to merge: checks passed and nothing blocks the merge." : "Checks passed."],
      failing: ["xFill", "gh-danger", "Checks failing: at least one check failed."],
      running: ["running", "gh-warning gh-spin", "Running: checks are still running."],
      unknown: ["question", "gh-muted", "CI unknown: GitHub hasn't reported a CI state."],
    }[pr.checks];
    if (checks) out.push(ico(checks[0], `${checks[1]} gh-big`, checks[2]));
    return `<span class="gh-marks">${out.join("")}</span>`;
  }

  function runIcon(run) {
    if (runRunning(run)) return ico("running", "gh-warning gh-spin", run.status.replace("_", " "));
    const [name, cls] = {
      success: ["checkFill", "gh-success"], failure: ["xFill", "gh-danger"],
      cancelled: ["slash", "gh-muted"], skipped: ["minus", "gh-muted"],
    }[run.conclusion] || ["question", "gh-muted"];
    return ico(name, cls, run.conclusion || "");
  }

  function buildMark(status) {
    const [name, cls] = { built: ["checkFill", "gh-success"], building: ["running", "gh-warning"],
      errored: ["xFill", "gh-danger"] }[status] || ["question", "gh-muted"];
    return ico(name, cls);
  }

  const badge = (text) => `<span class="badge">${esc(text)}</span>`;
  const agoPart = (iso) => { const a = GhUI.ago(iso); return a ? ` · ${a}` : ""; };
  const capitalize = (s) => (s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, " ") : "");

  /* ---------------------------------------------------------- rows */

  function prRow(repo, pr) {
    return `<button type="button" class="gh-row gh-center" data-pr="${esc(pr.number)}" data-repo="${esc(repo)}">
      <span class="gh-row-lead">${prIcon(pr)}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(pr.title)}</span>${(pr.labels || []).slice(0, 3).map(badge).join("")}</span>
        <span class="gh-row-sub">#${esc(pr.number)} · ${esc(pr.author)} · ${esc(pr.branch)} → ${esc(pr.base)}${agoPart(pr.updated_at)}</span>
      </span>
      ${prStateIcons(pr)}
    </button>`;
  }

  const link = (url, body, cls = "") => `<a class="gh-row ${cls}" href="${esc(url)}" target="_blank" rel="noopener">${body}</a>`;

  function issueRow(issue) {
    const open = issue.state === "open";
    return link(issue.url, `<span class="gh-row-lead">${ico(open ? "issueOpen" : "issueClosed", open ? "gh-success" : "gh-muted")}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(issue.title)}</span>${(issue.labels || []).slice(0, 3).map(badge).join("")}</span>
        <span class="gh-row-sub">#${esc(issue.number)} · ${esc(issue.author)}${issue.comments > 0 ? ` · ${issue.comments} comments` : ""}${agoPart(issue.updated_at)}</span>
      </span>
      <span class="gh-row-side">${(issue.assignees || []).slice(0, 2).map((a) => badge(`@${a}`)).join("")}</span>`);
  }

  function runRow(run) {
    const done = run.conclusion ? badge(run.conclusion === "success" ? "Passed" : capitalize(run.conclusion)) : "";
    return link(run.url, `<span class="gh-row-lead">${runIcon(run)}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(run.title)}</span></span>
        <span class="gh-row-sub">${esc(run.workflow)} · ${esc(run.branch)} · ${esc(run.event)}${agoPart(run.created_at)}</span>
      </span>
      <span class="gh-row-side">${done}</span>`);
  }

  function releaseRow(rel) {
    return link(rel.url, `<span class="gh-row-lead">${ico("tag", "gh-accent")}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(rel.name || rel.tag)}</span>
          ${rel.latest ? badge("Latest") : ""}${rel.draft ? badge("Draft") : ""}${rel.prerelease ? badge("Pre-release") : ""}</span>
        <span class="gh-row-sub gh-mono">${esc(rel.tag)}${agoPart(rel.published_at)}</span>
      </span>`);
  }

  function tagRow(tag) {
    return link(tag.url, `<span class="gh-row-lead">${ico("tag", "gh-secondary")}</span>
      <span class="gh-row-main"><span class="gh-row-title"><span class="gh-title">${esc(tag.name)}</span></span></span>
      <span class="gh-row-side gh-mono gh-muted">${esc(tag.sha)}</span>`, "gh-center");
  }

  function branchRow(b) {
    return link(b.url, `<span class="gh-row-lead">${ico("branch", b.default ? "gh-accent" : "gh-secondary")}</span>
      <span class="gh-row-main"><span class="gh-row-title"><span class="gh-title">${esc(b.name)}</span>
        ${b.default ? badge("Default") : ""}${b.protected ? badge("Protected") : ""}</span></span>
      <span class="gh-row-side gh-mono gh-muted">${esc(b.sha)}</span>`, "gh-center");
  }

  const ROW = {
    pull_requests: (repo, r) => prRow(repo, r), issues: (_repo, r) => issueRow(r), runs: (_repo, r) => runRow(r),
    releases: (_repo, r) => releaseRow(r), tags: (_repo, r) => tagRow(r), branches: (_repo, r) => branchRow(r),
  };
  const EMPTY = {
    pull_requests: "No pull requests.", issues: "No issues.", runs: "No workflow runs.",
    releases: "No releases.", tags: "No tags.", branches: "No branches.",
  };

  function emptyCard(text, iconName, framed = true) {
    const body = `<div class="gh-empty">${ico(iconName, "gh-muted")}<span>${esc(text)}</span></div>`;
    return framed ? `<div class="card">${body}</div>` : `<div class="gh-state">${body}</div>`;
  }
  const loadingCard = (text, framed = true) => {
    const body = `<div class="gh-empty">${ico("running", "gh-muted gh-spin")}<span>${esc(text)}</span></div>`;
    return framed ? `<div class="card">${body}</div>` : `<div class="gh-state">${body}</div>`;
  };
  const errorCard = (text, framed = true, action = "") => {
    const body = `<div class="gh-error-row"><span class="error">${esc(text)}</span>${action}</div>`;
    return framed ? `<div class="card">${body}</div>` : `<div class="gh-state">${body}</div>`;
  };

  /* Pages is one facts panel, not a row list. */
  function pagesPanel(state) {
    if (state.kind === "loading") return loadingCard("Reading the Pages settings…", false);
    if (state.kind === "failed") return errorCard(state.message, false);
    const site = state.rows;
    if (!site) return emptyCard("This repo has no GitHub Pages site.", "globe", false);
    const build = site.latest_build;
    const commit = build && build.commit ? `<span class="gh-mono gh-muted">${esc(String(build.commit).slice(0, 7))}</span>` : "";
    const when = build && GhUI.ago(build.updated_at) ? `<span class="caption gh-muted">${GhUI.ago(build.updated_at)}</span>` : "";
    return `<div class="gh-pages">
      <div class="gh-pages-name">${esc(site.cname || site.url || "GitHub Pages")}</div>
      ${site.url ? `<a class="gh-pages-link" href="${esc(site.url)}" target="_blank" rel="noopener">${ico("external")}<span>${esc(site.url)}</span></a>` : ""}
      ${site.source_branch ? `<div class="caption gh-inline">${ico("branch")}<span>Built from ${esc(site.source_branch)}</span></div>` : ""}
      ${build ? `<div class="gh-inline">${buildMark(build.status)}<span class="caption">Last build ${esc(build.status)}</span>${commit}${when}</div>
        ${build.error ? `<div class="caption gh-danger-text gh-clamp3">${esc(build.error)}</div>` : ""}` : ""}
    </div>`;
  }

  /* One repo's tab surface (RepoSurfaces). opts: query, unfiltered,
   * preview + expandKey (the dashboard's fold) or window (the focus
   * page's scroll loader). */
  function surfaceHtml(repo, tabId, surfaces, opts) {
    const key = tabOf(tabId).key;
    const raw = surfaceState(surfaces, key);
    if (key === "pages") return pagesPanel(raw);
    const state = filtered(raw, key, opts.query, opts.unfiltered);
    if (state.kind === "loading") return loadingCard("Fetching…", false);
    if (state.kind === "failed") return errorCard(state.message, false);
    const rows = state.rows;
    if (opts.window) {
      const w = opts.window;
      const visible = rows.slice(0, w.shown);
      const pending = w.fetching || rows.length > w.shown || w.canGrow;
      const label = w.fetching ? "Fetching more…" : w.stalled ? "Couldn't fetch more. Click to try again" : "Keep scrolling for more";
      const sentinel = pending ? `<button type="button" class="gh-sentinel${w.fetching ? " fetching" : w.stalled ? " stalled" : ""}" data-sentinel="${key}">
        ${w.fetching ? ico("running", "gh-muted gh-spin") : w.stalled ? ico("refresh", "gh-muted") : '<span class="gh-ring"></span>'}
        <span class="caption gh-muted">${label}</span></button>` : "";
      // A filter with no match in the rows fetched so far keeps the loader,
      // so older rows can still be searched.
      if (!rows.length) {
        const text = !opts.query ? EMPTY[key] : `Nothing matches "${opts.query}".`;
        return emptyCard(text, "issueClosed", false) + sentinel;
      }
      return visible.map((r) => ROW[key](repo, r)).join("") + sentinel;
    }
    if (!rows.length) {
      const text = opts.unfiltered || !opts.query ? EMPTY[key] : `Nothing matches "${opts.query}".`;
      return emptyCard(text, "issueClosed", false);
    }
    const open = page.expanded.has(opts.expandKey);
    const visible = open ? rows : rows.slice(0, opts.preview);
    const hidden = rows.length - opts.preview;
    const more = hidden > 0 ? `<button type="button" class="gh-expand" data-expand="${esc(opts.expandKey)}"
        title="${open ? `Show only the first ${opts.preview}` : "Show every row"}">${ico(open ? "chevronUp" : "chevronDown")}<span>${
        open ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</span></button>` : "";
    return visible.map((r) => ROW[key](repo, r)).join("") + more;
  }

  /* ---------------------------------------------------------- dashboard */

  /* The app keeps Refresh in the rail; with no rail to show (gh not set
   * up, nothing pinned) it rides in the header instead. */
  const HEADER = (refresh = false) => pageHeader("Repositories", "Your pinned repos' pull requests, issues and CI, from swe gh.",
    refresh ? `<button class="icon-button${page.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>` : "");

  async function renderDashboard(refresh = false) {
    const gen = routeGen;
    page.loading = true;
    GhUI.keepFocus("gh-search", paintDashboard);
    const { status, data } = await api(`/v1/app/repositories${refresh ? "?refresh=1" : ""}`);
    if (gen !== routeGen) return;
    page.loading = false;
    if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    if (status !== 200 || !data) {
      // Keep the last good rows under the error, as the app does.
      if (!page.data) return pageError(HEADER(true), status, data, () => renderDashboard(true));
      page.error = GhUI.failMessage(status, data);
    } else {
      page.data = data;
      page.error = null;
      if (page.shown && !pins().some((p) => p.name === page.shown)) page.shown = null;
    }
    if (!tabsFor(!!page.shown).some((t) => t.id === page.tab)) page.tab = "pulls";
    GhUI.keepFocus("gh-search", paintDashboard);
  }

  function dashboardContent() {
    const d = page.data;
    if (!d) return loadingCard("Checking gh and the pinned repos…");
    const setup = GhUI.envSetup(d.github);
    if (setup) return GhUI.setupCard(setup, d.machine);
    if (!pins().length) {
      return `<div class="card"><div class="gh-empty">${ico("stack", "gh-muted")}<span>Pin a repository to see its pull requests, issues and CI here.</span></div>
        <p class="caption">Pin one on the hub with <code>swe gh repo add &lt;owner/name&gt;</code>, or in the Mac app.</p></div>`;
    }
    const tabs = tabsFor(!!page.shown);
    const toolbar = `<div class="gh-toolbar">
      <div class="gh-tabs" role="tablist">${tabs.map((t) => `<button type="button" class="gh-tab${t.id === page.tab ? " selected" : ""}"
        data-tab="${t.id}" role="tab" aria-selected="${t.id === page.tab}">${t.title}</button>`).join("")}</div>
      <input class="field gh-search" id="gh-search" type="search" placeholder="Search" value="${esc(page.filter)}" autocomplete="off"
        spellcheck="false" aria-label="Search" title="Search titles, branches, authors and states like failing or draft (⌘K)">
    </div>`;
    const error = page.error ? errorCard(page.error) : "";
    const repos = listedRepos().filter((p) => rowCount(p.name, page.tab) !== 0);
    let list;
    if (!repos.length) {
      const place = page.shown || "your pinned repos";
      const text = query() ? `Nothing matches "${query()}".` : {
        pulls: `No open pull requests in ${place}.`, issues: `No open issues in ${place}.`,
        actions: `No workflow runs in ${place}.`, pages: `${place} has no GitHub Pages site.`,
        releases: `No releases in ${place}.`, tags: `No tags in ${place}.`, branches: `No branches in ${place}.`,
      }[page.tab];
      list = emptyCard(text, "issueClosed");
    } else {
      list = `<div class="gh-box">${repos.map((pin, i) => {
        const [owner, name] = pin.name.includes("/") ? pin.name.split("/", 2) : [null, pin.name];
        const last = i === repos.length - 1;
        return `<div class="gh-group-head">
            ${owner ? `<span class="gh-muted">${esc(owner)} /</span>` : ""}<span>${esc(name)}</span>
            <span class="gh-spacer"></span>
            <span class="gh-head-links">
              <a href="https://github.com/${esc(pin.name)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open ${esc(pin.name)} on GitHub">${ico("external")}</a>
              <a href="#/repositories?repo=${encodeURIComponent(pin.name)}" title="Open ${esc(pin.name)} in full" aria-label="Open ${esc(pin.name)} in full">${ico("expand")}</a>
            </span>
          </div>
          <div class="gh-surface${last ? " last" : ""}">${surfaceHtml(pin.name, page.tab, boardOf(pin.name), {
            query: query(), unfiltered: showsAllRows(pin.name), preview: 5, expandKey: `${pin.name}:${page.tab}`,
          })}</div>`;
      }).join("")}</div>`;
    }
    return `${toolbar}${error}${list}`;
  }

  function railHtml() {
    const groups = [];
    const byOwner = new Map();
    for (const pin of visibleRepos()) {
      const owner = pin.name.split("/")[0];
      if (!byOwner.has(owner)) { byOwner.set(owner, []); groups.push(owner); }
      byOwner.get(owner).push(pin);
    }
    const total = visibleRepos().reduce((n, p) => n + (rowCount(p.name, page.tab) || 0), 0);
    const dot = (state) => `<span class="gh-dot gh-dot-${state || "none"}"></span>`;
    const row = (pin, quiet) => {
      const count = rowCount(pin.name, page.tab);
      return `<button type="button" class="gh-rail-row${page.shown === pin.name ? " selected" : ""}${quiet ? " quiet" : ""}"
          data-shown="${esc(pin.name)}" title="${esc(pin.name)}">
        ${dot(ciState(pin.name))}<span class="gh-rail-name">${esc(pin.name.split("/").pop())}</span>
        <span class="gh-spacer"></span>${count ? `<span class="caption num gh-muted">${count}</span>` : ""}</button>`;
    };
    const body = groups.map((owner) => {
      const list = byOwner.get(owner);
      const active = list.filter((p) => rowCount(p.name, page.tab) !== 0);
      const quiet = list.filter((p) => rowCount(p.name, page.tab) === 0);
      const open = page.openQuiet.has(owner);
      return `<div class="gh-owner">${esc(owner)}</div>${active.map((p) => row(p, false)).join("")}
        ${quiet.length ? `<button type="button" class="gh-quiet-toggle${open ? " open" : ""}" data-quiet="${esc(owner)}"
          title="${open ? "Hide repos with nothing on this tab" : "Show repos with nothing on this tab"}">${ico("chevronRight")}<span>${quiet.length} quiet</span></button>
          <div class="gh-quiet${open ? " open" : ""}">${quiet.map((p) => row(p, true)).join("")}</div>` : ""}`;
    }).join("");
    return `<aside class="gh-rail" aria-label="Pinned repos">
      <div class="gh-rail-head"><span class="caption gh-muted">Pinned repos</span><span class="gh-spacer"></span>
        <button type="button" class="gh-rail-btn${page.loading ? " spinning" : ""}" id="page-refresh" title="Fetch again" aria-label="Fetch again">${ico("refresh")}</button></div>
      <div class="gh-rail-list">
        <button type="button" class="gh-rail-row${page.shown ? "" : " selected"}" data-shown="">${ico("stack")}<span>All repos</span>
          <span class="gh-spacer"></span><span class="caption num gh-muted">${total}</span></button>
        ${body}
      </div>
    </aside>`;
  }

  function paintDashboard() {
    const d = page.data;
    const rail = d && !GhUI.envSetup(d.github) && pins().length;
    document.getElementById("panel").innerHTML = `<div class="page gh-page">
      <div class="gh-dash${rail ? " with-rail" : ""}">
        <div class="gh-head">${HEADER(!rail)}</div>
        ${rail ? railHtml() : ""}
        <div class="gh-body stack">${dashboardContent()}</div>
      </div></div>`;
    bindDashboard();
  }

  function bindDashboard() {
    const panel = document.getElementById("panel");
    bindRefresh(() => renderDashboard(true));
    GhUI.bindSetup(() => renderDashboard(true));
    for (const tab of panel.querySelectorAll("[data-tab]")) {
      tab.onclick = () => { page.tab = tab.dataset.tab; paintDashboard(); };
    }
    for (const row of panel.querySelectorAll("[data-shown]")) {
      row.onclick = () => {
        page.shown = row.dataset.shown || null;
        if (!tabsFor(!!page.shown).some((t) => t.id === page.tab)) page.tab = "pulls";
        paintDashboard();
      };
    }
    for (const btn of panel.querySelectorAll("[data-quiet]")) {
      btn.onclick = () => {
        const owner = btn.dataset.quiet;
        if (page.openQuiet.has(owner)) page.openQuiet.delete(owner); else page.openQuiet.add(owner);
        paintDashboard();
      };
    }
    bindRows(panel, paintDashboard);
    const search = document.getElementById("gh-search");
    if (search) {
      search.oninput = () => { page.filter = search.value; GhUI.keepFocus("gh-search", paintDashboard); };
    }
  }

  /* Shared by both views: fold rows and PR rows. */
  function bindRows(root, repaint) {
    for (const btn of root.querySelectorAll("[data-expand]")) {
      btn.onclick = () => {
        const key = btn.dataset.expand;
        if (page.expanded.has(key)) page.expanded.delete(key); else page.expanded.add(key);
        repaint();
      };
    }
    for (const row of root.querySelectorAll("[data-pr]")) {
      row.onclick = () => openPr(row.dataset.repo, row.dataset.pr);
    }
  }

  /* ---------------------------------------------------------- focus page */

  function newFocus(repo) {
    return { repo, data: null, error: null, loading: false, tab: "pulls", filter: "",
      shown: {}, limits: {}, defaults: {}, exhausted: new Set(), fetching: new Set(), stalled: new Set(), maxLimit: 200 };
  }

  const focusHeader = (f) => pageHeader(f.repo, "Pull requests, issues, CI and refs — every state, most recent first.",
    `<a class="gh-button" href="#/repositories" title="Back to all repositories">${ico("chevronLeft")}<span>Repositories</span></a>
     <button class="icon-button${f.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Fetch again">${icon("rescan")}</button>
     <a class="icon-button" href="https://github.com/${esc(f.repo)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open on GitHub">${ico("external")}</a>`);

  /* A short page means the listing ended; a full one at the hub's cap
   * means no wider window exists to ask for. */
  function noteLimits(f, key, entry, limit) {
    f.limits[key] = limit;
    const rows = entry && entry.status === "ok" ? entry[key] : null;
    if (rows && (rows.length < limit || limit >= f.maxLimit)) f.exhausted.add(key); else f.exhausted.delete(key);
  }

  async function renderFocus(repo, refresh = false) {
    const gen = routeGen;
    if (!page.focus || page.focus.repo !== repo) page.focus = newFocus(repo);
    const f = page.focus;
    f.loading = true;
    paintFocus();
    const q = `repo=${encodeURIComponent(repo)}`;
    const { status, data } = await api(`/v1/app/repositories?${q}${refresh ? "&refresh=1" : ""}`);
    if (gen !== routeGen || page.focus !== f) return;
    f.loading = false;
    if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    if (status !== 200 || !data) {
      if (!f.data) return pageError(focusHeader(f), status, data, () => renderFocus(repo, true));
      f.error = GhUI.failMessage(status, data);
      return GhUI.keepFocus("gh-search", paintFocus);
    }
    // A surface the user scrolled wider keeps its window across a refetch.
    const wider = Object.keys(f.limits).filter((k) => f.limits[k] > (data.limits || {})[k]);
    f.defaults = data.limits || {};
    f.maxLimit = data.max_limit || f.maxLimit;
    for (const key of Object.keys(data.surfaces || {})) {
      if (!wider.includes(key)) noteLimits(f, key, data.surfaces[key], f.defaults[key] || 0);
    }
    const keep = f.data ? f.data.surfaces : {};
    f.data = data;
    for (const key of wider) f.data.surfaces[key] = keep[key] || data.surfaces[key];
    f.error = null;
    GhUI.keepFocus("gh-search", paintFocus);
    for (const key of wider) loadMore(key, f.limits[key], refresh);
  }

  async function loadMore(key, limit, refresh = false) {
    const f = page.focus;
    if (!f || f.fetching.has(key)) return;
    const gen = routeGen;
    f.fetching.add(key);
    GhUI.keepFocus("gh-search", paintFocus);
    const q = new URLSearchParams({ repo: f.repo, surface: key, limit: String(limit) });
    if (refresh) q.set("refresh", "1");
    const { status, data } = await api(`/v1/app/repositories?${q}`);
    f.fetching.delete(key);
    if (gen !== routeGen || page.focus !== f) return;
    if (status === 200 && data && data.entry && data.entry.status === "ok") {
      f.data.surfaces[key] = data.entry;
      f.stalled.delete(key);
      f.error = null;
      noteLimits(f, key, data.entry, data.limit);
    } else {
      // Keep the rows already here and stop the automatic loading: the
      // loader waits for a click instead of retrying every 650 ms.
      f.stalled.add(key);
      f.error = status === 200 && data && data.entry
        ? (data.entry.error || data.entry.status) : GhUI.failMessage(status, data);
    }
    GhUI.keepFocus("gh-search", paintFocus);
  }

  /* One filled ring: show another batch of the rows already here, or
   * at the end of them ask the hub for a wider window. */
  function advance(key) {
    const f = page.focus;
    const state = filtered(surfaceState(f.data.surfaces, key), key, f.filter.trim(), false);
    const rows = state.kind === "loaded" ? state.rows.length : 0;
    const shown = f.shown[key] || BATCH;
    if (shown < rows) {
      f.shown[key] = shown + BATCH;
      GhUI.keepFocus("gh-search", paintFocus);
    } else if (!f.exhausted.has(key)) {
      loadMore(key, Math.min((f.limits[key] || 0) + STEP, f.maxLimit));
    }
  }

  function paintFocus() {
    const f = page.focus;
    if (page.observer) { page.observer.disconnect(); page.observer = null; }
    let content;
    if (!f.data) {
      content = loadingCard("Fetching…");
    } else if (GhUI.envSetup(f.data.github)) {
      content = GhUI.setupCard(GhUI.envSetup(f.data.github), f.data.machine);
    } else {
      const key = tabOf(f.tab).key;
      const loadingTab = f.loading || f.fetching.has(key);
      content = `<div class="gh-toolbar">
          <div class="gh-tabs" role="tablist">${TABS.map((t) => `<button type="button" class="gh-tab${t.id === f.tab ? " selected" : ""}"
            data-tab="${t.id}" role="tab" aria-selected="${t.id === f.tab}">${t.title}</button>`).join("")}</div>
          ${loadingTab ? ico("running", "gh-muted gh-spin gh-toolbar-spin") : ""}
          <input class="field gh-search" id="gh-search" type="search" placeholder="Filter by title, branch, tag…"
            value="${esc(f.filter)}" autocomplete="off" spellcheck="false" aria-label="Filter">
        </div>
        ${f.error ? errorCard(f.error) : ""}
        <div class="gh-box"><div class="gh-surface last">${surfaceHtml(f.repo, f.tab, f.data.surfaces, {
          query: f.filter.trim(), unfiltered: false,
          window: { shown: f.shown[key] || BATCH, fetching: f.fetching.has(key),
            canGrow: key !== "pages" && !f.exhausted.has(key), stalled: f.stalled.has(key) },
        })}</div></div>`;
    }
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${focusHeader(f)}<div class="stack">${content}</div></div>`;
    bindFocus();
  }

  function bindFocus() {
    const f = page.focus;
    const panel = document.getElementById("panel");
    bindRefresh(() => renderFocus(f.repo, true));
    GhUI.bindSetup(() => renderFocus(f.repo, true));
    for (const tab of panel.querySelectorAll("[data-tab]")) {
      tab.onclick = () => { f.tab = tab.dataset.tab; paintFocus(); };
    }
    bindRows(panel, paintFocus);
    const search = document.getElementById("gh-search");
    if (search) search.oninput = () => { f.filter = search.value; GhUI.keepFocus("gh-search", paintFocus); };
    const sentinel = panel.querySelector("[data-sentinel]");
    if (!sentinel) return;
    sentinel.onclick = () => { f.stalled.delete(sentinel.dataset.sentinel); advance(sentinel.dataset.sentinel); };
    if (sentinel.classList.contains("fetching") || sentinel.classList.contains("stalled")
        || !("IntersectionObserver" in window)) return;
    // The ring fills while half the row stays in view, then advances once;
    // scrolling away first resets it.
    let timer = null;
    page.observer = new IntersectionObserver((entries) => {
      const seen = entries.some((e) => e.isIntersecting);
      sentinel.classList.toggle("filling", seen);
      clearTimeout(timer);
      if (seen) timer = setTimeout(() => { if (sentinel.isConnected) advance(sentinel.dataset.sentinel); }, 650);
    }, { threshold: 0.5 });
    page.observer.observe(sentinel);
  }

  /* ---------------------------------------------------------- PR sheet */

  function sheetEl() {
    let dlg = document.getElementById("gh-pr-sheet");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.className = "sheet gh-sheet";
      dlg.id = "gh-pr-sheet";
      dlg.setAttribute("aria-label", "Pull request");
      // A click on the backdrop (the dialog box itself) closes it.
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
      dlg.addEventListener("close", () => { page.sheet = null; });
      document.body.appendChild(dlg);
    }
    return dlg;
  }

  function openPr(repo, number) {
    page.sheet = { repo, number: String(number), data: null, status: 0, checksOpen: false,
      method: "squash", confirm: null, merging: false, mergeError: null };
    const dlg = sheetEl();
    paintSheet();
    if (!dlg.open) dlg.showModal();
    loadPr(false);
  }

  async function loadPr(refresh) {
    const s = page.sheet;
    if (!s) return;
    s.busy = true;
    paintSheet();
    const q = new URLSearchParams({ repo: s.repo, pr: s.number });
    if (refresh) q.set("refresh", "1");
    const { status, data } = await api(`/v1/app/repositories?${q}`);
    if (page.sheet !== s) return;
    s.busy = false;
    s.status = status;
    s.data = data;
    paintSheet();
  }

  function checksMark(checks) {
    const m = { passing: ["checkFill", "gh-success", "Passing"], failing: ["xFill", "gh-danger", "Failing"],
      running: ["running", "gh-warning", "Running"], unknown: ["question", "gh-muted", "CI unknown"] }[checks];
    return m ? `<span class="gh-label ${m[1]}">${ico(m[0])}<span>${m[2]}</span></span>` : "";
  }

  function reviewMark(decision) {
    const m = { approved: ["sealFill", "gh-success", "Approved"], changes_requested: ["pencilFill", "gh-danger", "Changes"],
      review_required: ["eyeFill", "gh-warning", "Review needed"] }[decision];
    return m ? `<span class="gh-label ${m[1]}">${ico(m[0])}<span>${m[2]}</span></span>` : "";
  }

  function checkGlyph(state) {
    const [name, cls] = { passing: ["checkFill", "gh-success"], failing: ["xFill", "gh-danger"],
      running: ["running", "gh-warning"] }[state] || ["minus", "gh-muted"];
    return ico(name, cls);
  }

  function sheetBody(pr) {
    const facts = [`by ${esc(pr.author)}`];
    if (pr.additions != null) facts.push(`+${pr.additions}/−${pr.deletions || 0} in ${pr.changed_files || 0} files`);
    if (pr.mergeable) facts.push(esc(pr.mergeable));
    if (pr.merge_state && pr.merge_state !== "unknown") facts.push(esc(pr.merge_state));
    const header = `<div class="gh-pr-head"><span class="gh-pr-icon">${prIcon(pr)}</span><div class="gh-pr-headtext">
        <div class="gh-pr-title">${esc(pr.title)}</div>
        <div class="caption gh-ellipsis">#${esc(pr.number)} · ${esc(pr.repo)} · ${esc(pr.branch)} → ${esc(pr.base)}${agoPart(pr.updated_at)}</div>
        <div class="caption gh-facts">${facts.map((x) => `<span>${x}</span>`).join("")}</div>
        <div class="gh-pr-marks">${checksMark(pr.checks)}${reviewMark(pr.review_decision)}
          ${pr.auto_merge ? badge(`Auto-merge · ${pr.auto_merge.method}`) : ""}${(pr.labels || []).slice(0, 4).map(badge).join("")}</div>
      </div></div>`;
    const runs = pr.check_runs || [];
    let checks = "";
    if (runs.length) {
      const passed = runs.filter((c) => c.state === "passing").length;
      const failing = runs.some((c) => c.state === "failing");
      const tone = failing ? "gh-danger" : passed === runs.length ? "gh-success" : "gh-warning";
      const open = page.sheet.checksOpen;
      checks = `<div class="gh-section">
        <button type="button" class="gh-fold-btn${open ? " open" : ""}" id="gh-checks-toggle" title="${open ? "Hide the check list" : "Show every check"}">
          ${ico("chevronRight", "gh-fold-chevron")}<span class="caption gh-muted">Checks</span><span class="caption ${tone}">${passed}/${runs.length}</span></button>
        ${open ? `<div class="gh-settings-card">${runs.map((c) => `<div class="gh-check-row">${checkGlyph(c.state)}
            <span class="gh-check-name">${esc(c.name)}</span>${c.workflow ? `<span class="caption gh-muted gh-ellipsis">${esc(c.workflow)}</span>` : ""}
            <span class="gh-spacer"></span><span class="caption">${esc(c.conclusion || c.status)}</span>
            ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener" title="Open the check" class="gh-muted">${ico("external", "gh-small")}</a>` : ""}
          </div>`).join("")}</div>` : ""}
      </div>`;
    }
    const reviews = pr.reviews || [], requested = pr.review_requests || [];
    const reviewHtml = reviews.length || requested.length ? `<div class="gh-section"><div class="caption gh-muted">Reviews</div>
      <div class="gh-settings-card">${reviews.map((r) => `<div class="gh-check-row"><span class="gh-check-name">${esc(r.author)}</span>
          <span class="caption">${esc(String(r.state || "").replace(/_/g, " "))}</span><span class="gh-spacer"></span>
          <span class="caption gh-muted">${GhUI.ago(r.submitted_at)}</span></div>`).join("")}
        ${requested.length ? `<div class="gh-check-row caption gh-muted">requested: ${requested.map(esc).join(", ")}</div>` : ""}
      </div></div>` : "";
    const body = String(pr.body || "").trim();
    const description = body ? `<div class="gh-section"><div class="caption gh-muted">Description</div>
      <div class="gh-settings-card gh-md">${GhUI.markdown(body)}</div></div>` : "";
    return `${header}${checks}${reviewHtml}${description}`;
  }

  /* PrDetailSheet's strategy menu, in its order and words. */
  const METHODS = [
    { value: "squash", title: "Squash and merge" },
    { value: "rebase", title: "Rebase and merge" },
    { value: "merge", title: "Create a merge commit" },
  ];
  const methodTitle = (m) => (METHODS.find((x) => x.value === m) || METHODS[0]).title;

  /* Why the app's Merge / Auto-merge buttons are off, in its hover words;
   * null when the button is live. The hub refuses the same cases. */
  function mergeBlock(pr) {
    if (pr.draft) return "Draft PRs can't merge";
    if (pr.mergeable === "conflicting") return "Resolve the conflicts first";
    return null;
  }
  function autoBlock(pr) {
    if (pr.draft) return "Draft PRs can't merge";
    if (pr.auto_merge) return "Auto-merge is already armed";
    return null;
  }

  /* The merge controls for an open PR, or the confirm that names exactly
   * what will happen once one is pressed. */
  function mergeControls(s, pr) {
    if (!pr || pr.state !== "open") return "";
    const off = s.merging || !!s.confirm;
    const mb = mergeBlock(pr), ab = autoBlock(pr);
    return `<select class="field gh-method" id="gh-merge-method" aria-label="Merge strategy"${off ? " disabled" : ""}>${
        METHODS.map((m) => `<option value="${m.value}"${m.value === s.method ? " selected" : ""}>${m.title}</option>`).join("")}</select>
      <button type="button" class="gh-button gh-primary" id="gh-merge"${off || mb ? " disabled" : ""}
        title="${esc(mb || "Merge now with the selected strategy")}">Merge</button>
      <button type="button" class="gh-button" id="gh-auto-merge"${off || ab ? " disabled" : ""}
        title="${esc(ab || "Arm auto-merge — merges when checks pass")}">Auto-merge</button>`;
  }

  function confirmPanel(s, pr) {
    if (!s.confirm || !pr || pr.state !== "open") return "";
    const what = `<strong>#${esc(pr.number)} ${esc(pr.title)}</strong>`;
    const method = `<strong>${esc(methodTitle(s.method))}</strong>`;
    const text = s.confirm === "auto"
      ? `Arm auto-merge for ${what} in <strong>${esc(pr.repo)}</strong> with ${method}? GitHub merges it into <code>${esc(pr.base)}</code> once its checks pass.`
      : `Merge ${what} into <code>${esc(pr.base)}</code> of <strong>${esc(pr.repo)}</strong> with ${method}? This happens on GitHub now and can't be undone from here.`;
    const go = s.confirm === "auto" ? (s.merging ? "Arming…" : "Arm auto-merge") : (s.merging ? "Merging…" : `Merge #${esc(pr.number)}`);
    return `<div class="gh-confirm" role="group" aria-label="Confirm">
      <div class="gh-confirm-text">${text}</div>
      <div class="gh-confirm-buttons">
        <button type="button" class="gh-button" id="gh-confirm-cancel"${s.merging ? " disabled" : ""}>Cancel</button>
        <button type="button" class="gh-button gh-primary" id="gh-confirm-go"${s.merging ? " disabled" : ""}>${go}</button>
      </div></div>`;
  }

  /* POST the merge. The sheet repaints only from the hub's answer: the PR
   * as GitHub shows it after the write. */
  async function runMerge() {
    const s = page.sheet;
    const pr = s && s.data && s.data.pr;
    if (!s || !pr || s.merging || !s.confirm) return;
    const auto = s.confirm === "auto";
    s.merging = true;
    s.mergeError = null;
    paintSheet();
    const { status, data } = await GhUI.post("/v1/app/repositories/merge",
      { repo: s.repo, number: Number(s.number), method: s.method, auto });
    if (status === 200 && data) {
      // The page behind the sheet reads the repo again, even if the sheet
      // was closed meanwhile: the hub dropped its cached board, so a
      // merged row leaves the list.
      const here = (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0];
      const focusRepo = GhUI.hashParams().get("repo");
      if (here === "repositories" && !focusRepo) renderDashboard();
      else if (here === "repositories" && page.focus && page.focus.repo === focusRepo) renderFocus(focusRepo);
    }
    if (page.sheet !== s) return;
    s.merging = false;
    if (status === 401) {
      s.mergeError = "The hub doesn't know this browser's credential. It may have been revoked.";
    } else if (status !== 200 || !data) {
      s.mergeError = GhUI.failMessage(status, data);
      // A state refusal means the sheet is out of date: read the PR
      // again so it shows what the hub saw (the error card stays).
      if (status === 409 || status === 404) {
        paintSheet();
        return loadPr(true);
      }
    } else {
      s.confirm = null;
      if (data.pr) {
        s.data = { github: data.github, repo: data.repo, status: "ok", pr: data.pr,
          fetched_at: data.fetched_at, machine: data.machine };
        s.status = 200;
      }
      if (!data.pr) return loadPr(true);
    }
    paintSheet();
    const focus = document.getElementById(s.mergeError ? "gh-confirm-go" : "gh-sheet-done");
    if (focus && !focus.disabled) focus.focus();
  }

  function paintSheet() {
    const s = page.sheet;
    const dlg = document.getElementById("gh-pr-sheet");
    if (!s || !dlg) return;
    const d = s.data;
    let content, pr = null;
    if (!d && !s.status) {
      content = loadingCard("Fetching the pull request…", false);
    } else if (s.status === 401) {
      content = errorCard("The hub doesn't know this browser's credential. It may have been revoked.", false);
    } else if (s.status !== 200 || !d) {
      content = errorCard(GhUI.failMessage(s.status, d), false, '<button type="button" class="gh-button" data-sheet-refresh>Refresh</button>');
    } else if (GhUI.envSetup(d.github)) {
      content = GhUI.setupCard(GhUI.envSetup(d.github), d.machine);
    } else if (d.status !== "ok" || !d.pr) {
      content = errorCard(d.error || d.status, false, '<button type="button" class="gh-button" data-sheet-refresh>Refresh</button>');
    } else {
      pr = d.pr;
      content = `<div class="gh-sheet-scroll">${sheetBody(pr)}</div>`;
    }
    const url = pr ? pr.url : `https://github.com/${s.repo}/pull/${s.number}`;
    // A refetch that shows the PR no longer open (or blocked) drops a
    // pending confirm, so it never names a merge that can't happen.
    if (s.confirm && (!pr || pr.state !== "open" || (s.confirm === "auto" ? autoBlock(pr) : mergeBlock(pr)))) s.confirm = null;
    const mergeErr = s.mergeError ? `<div class="card gh-merge-error" role="alert"><span class="error">${esc(s.mergeError)}</span></div>` : "";
    dlg.innerHTML = `<div class="sheet-body">${content}${mergeErr}${confirmPanel(s, pr)}
      <div class="gh-sheet-actions">${mergeControls(s, pr)}<span class="gh-spacer"></span>
        <a class="icon-button" href="${esc(url)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open on GitHub">${ico("external")}</a>
        <button type="button" class="icon-button${s.busy && d ? " spinning" : ""}" data-sheet-refresh title="Fetch again" aria-label="Fetch again">${icon("rescan")}</button>
        <button type="button" class="gh-button" id="gh-sheet-done">Done</button>
      </div></div>`;
    for (const btn of dlg.querySelectorAll("[data-sheet-refresh]")) btn.onclick = () => loadPr(true);
    dlg.querySelector("#gh-sheet-done").onclick = () => dlg.close();
    const methodSel = dlg.querySelector("#gh-merge-method");
    if (methodSel) methodSel.onchange = () => { s.method = methodSel.value; };
    const ask = (which) => () => {
      s.confirm = which;
      s.mergeError = null;
      paintSheet();
      const cancel = dlg.querySelector("#gh-confirm-cancel");
      if (cancel) cancel.focus();
    };
    const mergeBtn = dlg.querySelector("#gh-merge");
    if (mergeBtn) mergeBtn.onclick = ask("merge");
    const autoBtn = dlg.querySelector("#gh-auto-merge");
    if (autoBtn) autoBtn.onclick = ask("auto");
    const cancelBtn = dlg.querySelector("#gh-confirm-cancel");
    if (cancelBtn) cancelBtn.onclick = () => {
      const was = s.confirm;
      s.confirm = null;
      paintSheet();
      const back = dlg.querySelector(was === "auto" ? "#gh-auto-merge" : "#gh-merge");
      if (back && !back.disabled) back.focus();
    };
    const goBtn = dlg.querySelector("#gh-confirm-go");
    if (goBtn) goBtn.onclick = runMerge;
    const toggle = dlg.querySelector("#gh-checks-toggle");
    if (toggle) toggle.onclick = () => { s.checksOpen = !s.checksOpen; paintSheet(); };
    GhUI.bindSetup(() => loadPr(true));
  }

  /* ---------------------------------------------------------- entry */

  function render() {
    const dlg = document.getElementById("gh-pr-sheet");
    if (dlg && dlg.open) dlg.close();
    const repo = GhUI.hashParams().get("repo");
    if (repo) {
      renderFocus(repo);
      GhUI.poll("repositories", () => {
        if (page.focus && !page.focus.loading && !(document.getElementById("gh-pr-sheet") || {}).open) renderFocus(page.focus.repo);
      });
    } else {
      renderDashboard();
      GhUI.poll("repositories", () => {
        if (!page.loading && !(document.getElementById("gh-pr-sheet") || {}).open) renderDashboard();
      });
    }
  }

  /* ⌘K / Ctrl+K focuses the search, as in the app. */
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      const search = document.getElementById("gh-search");
      if (search) { e.preventDefault(); search.focus(); search.select(); }
    }
  });

  /* The PR sheet lives on <body>, outside the panel a route repaints, so
   * leaving the page (Back, a sidebar link) has to close it here. */
  window.addEventListener("hashchange", () => {
    const here = (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0];
    const dlg = document.getElementById("gh-pr-sheet");
    if (here !== "repositories" && dlg && dlg.open) dlg.close();
  });

  registerPage("repositories", render);
})();
