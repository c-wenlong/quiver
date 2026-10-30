/* Repositories: ports of the Mac app's RepositoriesPage (the dashboard of
 * pinned repos with its rail), RepoDetailPage (one repo, every state, at
 * #/repositories?repo=owner/name) and PrDetailSheet (a pull request's
 * checks, reviews and body, and its one write: merge with a strategy or
 * arm auto-merge, behind an inline confirm) and AddRepoSheet (search the
 * hub's synced catalog or GitHub, then pin), with the rail's gear
 * popover (RepoManager: open or unpin each pin) and drag to reorder.
 * Actions rows re-run failed or all jobs from their ⋯ menu (the app's
 * context menu), and the PR sheet opens a PR in a session: the hub
 * checks its head out (swe gh pr worktree) and starts a harness there
 * (POST /v1/sessions), then the chat opens. An issue row opens the
 * Mac app's IssueDetailSheet: the issue's body and comments from the
 * hub's GET /v1/issues/<o>/<r>/<n>, a comment box (POST
 * /v1/issues/comment), Open on GitHub, and its ResolveIssueSheet as a
 * panel (POST /v1/issues/resolve). */
"use strict";

/* The iOS Pull requests page's Mine and Review requested filters
 * (PullsModel.swift's PullFilter) over the dashboard's open PRs. The
 * flags come from GET /v1/pulls (remote/pulls.py): ``mine`` and
 * ``review_requested`` on each open PR, null when the hub couldn't read
 * them. A filter is offered only while its flag is known for every PR
 * the list shows, so a failed ``gh pr status`` hides it rather than
 * hiding PRs on a guess. */
const PrFilter = (() => {
  const FILTERS = [
    { id: "all", title: "All", flag: null },
    { id: "mine", title: "Mine", flag: "mine" },
    { id: "review", title: "Review requested", flag: "review_requested" },
  ];
  const flagName = (id) => (FILTERS.find((f) => f.id === id) || FILTERS[0]).flag;

  /* GET /v1/pulls's answer as repo (lower case) -> number -> row, or
   * null when it has no flags to give (gh blocked, a bad answer). A repo
   * whose read failed maps to null. */
  function index(data) {
    if (!data || data.env_blocked || !Array.isArray(data.repos)) return null;
    const out = {};
    for (const r of data.repos) {
      if (!r || typeof r.name !== "string") continue;
      let rows = null;
      if (r.status === "ok") {
        rows = {};
        for (const pr of r.pulls || []) if (pr && pr.number != null) rows[pr.number] = pr;
      }
      out[r.name.toLowerCase()] = rows;
    }
    return out;
  }

  /* One PR's flag: true, false, or null when unknown (no answer, the
   * repo's read failed, the PR newer than the answer, or a null flag). */
  function flagOf(flags, repo, number, flag) {
    const rows = flags && flags[String(repo).toLowerCase()];
    const pr = rows && rows[number];
    const v = pr ? pr[flag] : null;
    return typeof v === "boolean" ? v : null;
  }

  /* Whether filter ``id`` can be offered over ``lists``, [[repo, prs]]:
   * its flag is known for every PR in them. */
  function offered(flags, id, lists) {
    const flag = flagName(id);
    if (!flag) return true;
    if (!flags) return false;
    return lists.every(([repo, prs]) => prs.every((pr) => flagOf(flags, repo, pr.number, flag) !== null));
  }

  /* Whether ``pr`` in ``repo`` passes filter ``id``. */
  function includes(flags, id, repo, pr) {
    const flag = flagName(id);
    return !flag || flagOf(flags, repo, pr.number, flag) === true;
  }

  return { FILTERS, index, flagOf, offered, includes };
})();

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
    // The Pull requests tab's Mine / Review requested pick, and the flags
    // GET /v1/pulls gave for it (PrFilter.index; null until it answers).
    prFilter: "all", flags: null, flagsSeq: 0,
    expanded: new Set(), openQuiet: new Set(),
    focus: null, sheet: null, observer: null, seq: 0,
    add: null, // the Add repository sheet's state while it is open
    issue: null, // the issue sheet's state while it is open
    // Pin writes: the gear popover, the one unpin or reorder in flight
    // (one at a time, as GitHubModel's mutating set), a newer drop queued
    // behind it, the last failure, and a drag in progress.
    managing: false, mutating: null, queuedOrder: null, pinError: null,
    pinGen: 0, // bumped as each pin write starts and ends
    dragging: null, repaintAfterDrag: false,
    // Re-runs: the open row menu, the runs being re-run, the last failure.
    runMenu: null, rerunning: new Set(), actionError: null,
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
    return prFiltered(repo, key, filtered(state, key, query(), showsAllRows(repo))).rows.length;
  }

  /* The open PRs each of ``repos`` shows under the search, as
   * PrFilter.offered wants them; repos still loading or failed have none. */
  function prLists(repos) {
    return repos.map((p) => {
      const state = filtered(surfaceState(boardOf(p.name), "pull_requests"), "pull_requests", query(), showsAllRows(p.name));
      return [p.name, state.kind === "loaded" ? state.rows : []];
    });
  }

  /* Every repo a pick reaches: the list's and the rail's, whose counts
   * and quiet fold follow the pick too. A filter is offered only when
   * its flag is known across all of them, so no repo reads as quiet for
   * want of flags. */
  function prCoverage() {
    const names = new Set();
    return prLists([...listedRepos(), ...visibleRepos()].filter((p) => !names.has(p.name) && names.add(p.name)));
  }

  /* The PR filter in force: the pick, while the Pull requests tab shows
   * and its flag is known for every PR it reaches; else All. A paint
   * works it out once (prMemo) instead of once per rail row. */
  let prMemo = null;
  function prFilterNow() {
    if (prMemo) return prMemo.now;
    if (page.tab !== "pulls" || page.prFilter === "all") return "all";
    return PrFilter.offered(page.flags, page.prFilter, prCoverage()) ? page.prFilter : "all";
  }

  /* A loaded pull_requests state narrowed to the PR filter in force. */
  function prFiltered(repo, key, state, id = prFilterNow()) {
    if (key !== "pull_requests" || state.kind !== "loaded" || id === "all") return state;
    return { kind: "loaded", rows: state.rows.filter((pr) => PrFilter.includes(page.flags, id, repo, pr)) };
  }

  /* All / Mine / Review requested with their counts, each offered only
   * while its flag is known; nothing when only All would show. */
  function prFilterBar() {
    if (page.tab !== "pulls") return "";
    const coverage = prCoverage();
    const shown = PrFilter.FILTERS.filter((f) => PrFilter.offered(page.flags, f.id, coverage));
    const lists = prLists(listedRepos());
    if (shown.length < 2) return "";
    const now = prFilterNow();
    return `<div class="tabs gh-pr-filter" role="radiogroup" aria-label="Which pull requests to list">${shown.map((f) => {
      const n = lists.reduce((sum, [repo, prs]) => sum + prs.filter((pr) => PrFilter.includes(page.flags, f.id, repo, pr)).length, 0);
      const on = f.id === now;
      return `<button type="button" class="tab${on ? " selected" : ""}" role="radio" aria-checked="${on}" tabindex="${on ? 0 : -1}"
        data-pr-filter="${f.id}">${f.title} <span class="num">${n}</span></button>`;
    }).join("")}</div>`;
  }

  /* GET /v1/pulls for the flags, while the Pull requests tab shows. Only
   * the newest read lands; a failed one clears the flags, which hides
   * the filter rather than leaving stale picks. */
  async function loadFlags(refresh = false) {
    const gen = routeGen;
    const seq = ++page.flagsSeq;
    const { status, data } = await api(`/v1/pulls${refresh ? "?refresh=1" : ""}`);
    if (gen !== routeGen || seq !== page.flagsSeq || !onDashboard()) return;
    if (status === 401) return;
    page.flags = status === 200 ? PrFilter.index(data) : null;
    GhUI.keepFocus("gh-search", paintDashboard);
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

  const link = (url, body, cls = "") => {
    const href = safeHref(url);
    return `<a class="gh-row ${cls}"${href ? ` href="${href}" target="_blank" rel="noopener"` : ""}>${body}</a>`;
  };

  /* An issue row opens the issue sheet, as the app's row does. */
  function issueRow(repo, issue) {
    const open = issue.state === "open";
    return `<button type="button" class="gh-row" data-issue="${esc(issue.number)}" data-repo="${esc(repo)}">
      <span class="gh-row-lead">${ico(open ? "issueOpen" : "issueClosed", open ? "gh-success" : "gh-muted")}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(issue.title)}</span>${(issue.labels || []).slice(0, 3).map(badge).join("")}</span>
        <span class="gh-row-sub">#${esc(issue.number)} · ${esc(issue.author)}${issue.comments > 0 ? ` · ${issue.comments} comments` : ""}${agoPart(issue.updated_at)}</span>
      </span>
      <span class="gh-row-side">${(issue.assignees || []).slice(0, 2).map((a) => badge(`@${a}`)).join("")}</span>
    </button>`;
  }

  /* A run row opens GitHub; its ⋯ button holds the app's context menu:
   * Re-run failed jobs (a failed run only), Re-run all jobs, Open on
   * GitHub. A run being re-run spins where its badge was. */
  function runRow(repo, run) {
    const key = `${repo}#${run.id}`;
    const side = page.rerunning.has(key) ? ico("running", "gh-muted gh-spin")
      : run.conclusion ? badge(run.conclusion === "success" ? "Passed" : capitalize(run.conclusion)) : "";
    const open = page.runMenu && page.runMenu.key === key;
    return `<div class="gh-run">${link(run.url, `<span class="gh-row-lead">${runIcon(run)}</span>
      <span class="gh-row-main">
        <span class="gh-row-title"><span class="gh-title">${esc(run.title)}</span></span>
        <span class="gh-row-sub">${esc(run.workflow)} · ${esc(run.branch)} · ${esc(run.event)}${agoPart(run.created_at)}</span>
      </span>
      <span class="gh-row-side">${side}</span>`)}
      ${run.id ? `<button type="button" class="icon-button gh-run-more${open ? " open" : ""}" data-run-menu="${esc(key)}"
        data-repo="${esc(repo)}" data-run="${esc(run.id)}" data-failed="${run.conclusion === "failure" ? "1" : ""}"
        data-url="${esc(run.url)}" title="Re-run…" aria-label="Re-run ${esc(run.title)}" aria-haspopup="menu"
        aria-expanded="${open}">${ico("more")}</button>` : ""}</div>`;
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
    pull_requests: (repo, r) => prRow(repo, r), issues: (repo, r) => issueRow(repo, r), runs: (repo, r) => runRow(repo, r),
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
      ${safeHref(site.url) ? `<a class="gh-pages-link" href="${safeHref(site.url)}" target="_blank" rel="noopener">${ico("external")}<span>${esc(site.url)}</span></a>` : ""}
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
    // opts.prFilter: the dashboard's Mine / Review requested pick.
    const state = prFiltered(repo, key, filtered(raw, key, opts.query, opts.unfiltered), opts.prFilter || "all");
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
    // Only the newest read paints: one begun before a merge must not put
    // the merged row back over the read the merge started.
    const seq = ++page.seq;
    const pinGen = page.pinGen;
    page.loading = true;
    GhUI.keepFocus("gh-search", paintDashboard);
    if (page.tab === "pulls") loadFlags(refresh);
    const { status, data } = await api(`/v1/app/repositories${refresh ? "?refresh=1" : ""}`);
    if (gen !== routeGen || seq !== page.seq) return;
    page.loading = false;
    // A read that began before an unpin or reorder, or lands while one
    // runs, may list the pins as they were: the page's own list wins.
    if (data && data.repos && page.data && (page.mutating || pinGen !== page.pinGen)) data.repos = page.data.repos;
    if (status === 401) return GhUI.gate("repositories");
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
      return `<div class="card"><div class="gh-empty">${ico("stack", "gh-muted")}<span>Pin a repository to see its pull requests, issues and CI here.</span>
        <button type="button" class="gh-button" data-add-repo>${ico("plus")}<span>Add repository</span></button></div></div>`;
    }
    const tabs = tabsFor(!!page.shown);
    const toolbar = `<div class="gh-toolbar">
      <div class="gh-tabs" role="tablist">${tabs.map((t) => `<button type="button" class="gh-tab${t.id === page.tab ? " selected" : ""}"
        data-tab="${t.id}" role="tab" aria-selected="${t.id === page.tab}">${t.title}</button>`).join("")}</div>
      <input class="field gh-search" id="gh-search" type="search" placeholder="Search" value="${esc(page.filter)}" autocomplete="off"
        spellcheck="false" aria-label="Search" title="Search titles, branches, authors and states like failing or draft (⌘K)">
    </div>`;
    const error = [page.pinError, page.actionError, page.error].filter(Boolean).map((e) => errorCard(e)).join("");
    const repos = listedRepos().filter((p) => rowCount(p.name, page.tab) !== 0);
    const prFilter = prFilterNow();
    let list;
    if (!repos.length) {
      const place = page.shown || "your pinned repos";
      const text = query() ? `Nothing matches "${query()}".` : prFilter !== "all" ? {
        mine: `None of the open pull requests in ${place} are yours.`,
        review: `No open pull request in ${place} asks for your review.`,
      }[prFilter] : {
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
            query: query(), unfiltered: showsAllRows(pin.name), preview: 5, expandKey: `${pin.name}:${page.tab}`, prFilter,
          })}</div>`;
      }).join("")}</div>`;
    }
    return `${toolbar}${prFilterBar()}${error}${list}`;
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
    // Each run (an owner's active repos, or its quiet ones) reorders on
    // its own, as in the app: a drop never moves a repo past another run.
    const row = (pin, quiet, run) => {
      const count = rowCount(pin.name, page.tab);
      return `<button type="button" class="gh-rail-row${page.shown === pin.name ? " selected" : ""}${quiet ? " quiet" : ""}"
          data-shown="${esc(pin.name)}" data-drag="${esc(pin.name)}" data-run="${esc(run)}" draggable="true"
          title="${esc(pin.name)}. Drag to reorder.">
        ${ico("grip", "gh-grip")}${dot(ciState(pin.name))}<span class="gh-rail-name">${esc(pin.name.split("/").pop())}</span>
        <span class="gh-spacer"></span>${count ? `<span class="caption num gh-muted">${count}</span>` : ""}</button>`;
    };
    const body = groups.map((owner) => {
      const list = byOwner.get(owner);
      const active = list.filter((p) => rowCount(p.name, page.tab) !== 0);
      const quiet = list.filter((p) => rowCount(p.name, page.tab) === 0);
      const open = page.openQuiet.has(owner);
      return `<div class="gh-owner">${esc(owner)}</div>${active.map((p) => row(p, false, `${owner}:active`)).join("")}
        ${quiet.length ? `<button type="button" class="gh-quiet-toggle${open ? " open" : ""}" data-quiet="${esc(owner)}"
          title="${open ? "Hide repos with nothing on this tab" : "Show repos with nothing on this tab"}">${ico("chevronRight")}<span>${quiet.length} quiet</span></button>
          <div class="gh-quiet${open ? " open" : ""}">${quiet.map((p) => row(p, true, `${owner}:quiet`)).join("")}</div>` : ""}`;
    }).join("");
    return `<aside class="gh-rail" aria-label="Pinned repos">
      <div class="gh-rail-head"><span class="caption gh-muted">Pinned repos</span><span class="gh-spacer"></span>
        <button type="button" class="gh-rail-btn${page.managing ? " active" : ""}" id="gh-manage" title="Manage pinned repositories"
          aria-label="Manage pinned repositories" aria-haspopup="dialog" aria-expanded="${page.managing}">${ico("gear")}</button>
        <button type="button" class="gh-rail-btn${page.loading ? " spinning" : ""}" id="page-refresh" title="Fetch again" aria-label="Fetch again">${ico("refresh")}</button></div>
      <div class="gh-rail-list">
        <button type="button" class="gh-rail-row${page.shown ? "" : " selected"}" data-shown="">${ico("stack")}<span>All repos</span>
          <span class="gh-spacer"></span><span class="caption num gh-muted">${total}</span></button>
        ${body}
        <button type="button" class="gh-rail-row gh-rail-add" data-add-repo title="Pin a repository">${ico("plus")}<span>Add repository</span></button>
      </div>
    </aside>`;
  }

  /* RepoManager: every pin grouped by owner (a header only when there is
   * more than one), each with Open on GitHub and Unpin, then the add path. */
  function managerHtml() {
    const byOwner = new Map();
    for (const pin of pins()) {
      const owner = pin.name.split("/")[0];
      if (!byOwner.has(owner)) byOwner.set(owner, []);
      byOwner.get(owner).push(pin);
    }
    const owners = [...byOwner.keys()].sort();
    const many = owners.length > 1;
    const rows = owners.map((owner) => `${many ? `<div class="gh-manage-owner caption gh-muted">${esc(owner)}</div>` : ""}${
      byOwner.get(owner).slice().sort((a, b) => a.name.localeCompare(b.name)).map((pin) => `<div class="gh-manage-row">
        <span class="gh-manage-name">${esc(many ? pin.name.split("/").pop() : pin.name)}</span>
        <a class="gh-manage-btn" href="https://github.com/${esc(pin.name)}" target="_blank" rel="noopener" title="Open on GitHub"
          aria-label="Open ${esc(pin.name)} on GitHub">${ico("external")}</a>
        ${page.mutating === pin.name ? ico("running", "gh-muted gh-spin gh-manage-busy")
          : `<button type="button" class="gh-manage-btn gh-danger-text" data-unpin="${esc(pin.name)}" title="Unpin ${esc(pin.name)}"
            aria-label="Unpin ${esc(pin.name)}"${page.mutating ? " disabled" : ""}>${ico("minusCircle")}</button>`}
      </div>`).join("")}`).join("");
    return `<div class="gh-manage" id="gh-manage-pop" role="dialog" aria-label="Pinned repositories">
      <div class="gh-manage-title">Pinned repositories</div>
      ${pins().length ? rows : '<div class="caption gh-muted">Nothing pinned yet.</div>'}
      ${page.pinError ? `<div class="caption gh-danger-text" role="alert">${esc(page.pinError)}</div>` : ""}
      <div class="gh-manage-divider"></div>
      <button type="button" class="gh-manage-add" data-add-repo>${ico("plus")}<span>Add repository…</span></button>
    </div>`;
  }

  /* The popover hangs under the gear. It lives outside the rail, which
   * scrolls and would clip it, so it is placed against the viewport. */
  function placeManager() {
    const pop = document.getElementById("gh-manage-pop");
    const gear = document.getElementById("gh-manage");
    if (!pop || !gear) return;
    const r = gear.getBoundingClientRect();
    const width = pop.offsetWidth;
    pop.style.top = `${Math.round(r.bottom + 6)}px`;
    pop.style.left = `${Math.round(Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)))}px`;
  }

  async function unpin(name) {
    if (page.mutating) return;
    page.mutating = name;
    page.pinGen++;
    page.pinError = null;
    paintDashboard();
    const { status, data } = await GhUI.post("/v1/app/repositories/remove", { repo: name });
    page.mutating = null;
    page.pinGen++;
    if (status === 200 && data && page.data) {
      page.data.repos = data.repos;
      delete page.data.boards[name];
      if (page.shown === name) page.shown = null;
    } else if (status === 404 && data && data.error && data.error.code === "not_pinned") {
      // Unpinned elsewhere already: the list is what's out of date.
      if (onDashboard()) renderDashboard(true);
      return;
    } else {
      page.pinError = status === 401 ? CREDENTIAL_REFUSED : GhUI.failMessage(status, data);
    }
    if (onDashboard()) paintDashboard();
  }

  /* PinOrder.move: the full pin order after moving `id` to sit before
   * `before` (null: the end) inside `group`; the group's members are
   * written back into the slots they held, so every other pin stays. */
  function movePin(id, before, group, all) {
    const reordered = group.filter((n) => n !== id);
    const at = before == null ? -1 : reordered.indexOf(before);
    if (at >= 0) reordered.splice(at, 0, id); else reordered.push(id);
    const members = new Set(group);
    let i = 0;
    return all.map((n) => (members.has(n) ? reordered[i++] : n));
  }

  /* Show the new order at once, then save it. A drop while a save runs
   * is queued and sent when that one answers, so it lands rather than
   * being lost; a failed save reads the stored order back. */
  async function saveOrder(order) {
    const lookup = new Map(pins().map((p) => [p.name, p]));
    page.data.repos = order.map((n) => lookup.get(n)).filter(Boolean);
    page.pinError = null;
    paintDashboard();
    if (page.mutating) { page.queuedOrder = order; return; }
    page.mutating = "<reorder>";
    page.pinGen++;
    let next = order;
    while (next) {
      const { status, data } = await GhUI.post("/v1/app/repositories/reorder", { order: next });
      next = page.queuedOrder;
      page.queuedOrder = null;
      if (status !== 200 || !data) {
        page.pinError = status === 401 ? CREDENTIAL_REFUSED : GhUI.failMessage(status, data);
        next = null;
        page.mutating = null;
        page.pinGen++;
        // Read the stored order back, but only onto this page.
        if (onDashboard()) renderDashboard(true);
        return;
      }
      if (!next && page.data) page.data.repos = data.repos;
    }
    page.mutating = null;
    page.pinGen++;
    if (onDashboard()) paintDashboard();
  }

  const onDashboard = () => currentRoute() === "repositories"
    && !GhUI.hashParams().get("repo");

  function bindDrag(panel) {
    const runOf = (run) => [...panel.querySelectorAll("[data-run]")].filter((r) => r.dataset.run === run);
    const clearMarks = () => {
      for (const r of panel.querySelectorAll(".drop-before, .drop-after")) r.classList.remove("drop-before", "drop-after");
    };
    // Rows run across in the narrow layout's chip strip, down otherwise.
    // Asked of the rail's list: a quiet row's own parent is display:
    // contents there and keeps its column direction.
    const across = (row) => getComputedStyle(row.closest(".gh-rail-list")).flexDirection === "row";
    const after = (row, e) => {
      const r = row.getBoundingClientRect();
      return across(row) ? e.clientX > r.left + r.width / 2 : e.clientY > r.top + r.height / 2;
    };
    for (const row of panel.querySelectorAll("[data-drag]")) {
      row.ondragstart = (e) => {
        if (page.mutating && page.mutating !== "<reorder>") { e.preventDefault(); return; }
        page.dragging = { name: row.dataset.drag, run: row.dataset.run };
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", row.dataset.drag);
        row.classList.add("dragging");
      };
      row.ondragend = () => {
        page.dragging = null;
        clearMarks();
        row.classList.remove("dragging");
        // A repaint that came in mid-drag waited for the drop.
        if (page.repaintAfterDrag) { page.repaintAfterDrag = false; paintDashboard(); }
      };
      row.ondragover = (e) => {
        const d = page.dragging;
        if (!d || d.run !== row.dataset.run) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        clearMarks();
        if (row.dataset.drag !== d.name) row.classList.add(after(row, e) ? "drop-after" : "drop-before");
      };
      row.ondrop = (e) => {
        const d = page.dragging;
        if (!d || d.run !== row.dataset.run) return;
        e.preventDefault();
        const run = runOf(d.run).map((r) => r.dataset.drag);
        let before = row.dataset.drag;
        if (after(row, e)) {
          const at = run.indexOf(row.dataset.drag);
          before = at + 1 < run.length ? run[at + 1] : null;
        }
        page.dragging = null;
        clearMarks();
        if (before === d.name) return;
        const order = movePin(d.name, before, run, pins().map((p) => p.name));
        if (order.join("\n") !== pins().map((p) => p.name).join("\n")) saveOrder(order);
      };
    }
  }

  function paintDashboard() {
    // Repainting mid-drag would drop the row being dragged.
    if (page.dragging) { page.repaintAfterDrag = true; return; }
    const d = page.data;
    const rail = d && !GhUI.envSetup(d.github) && pins().length;
    prMemo = null;
    prMemo = { now: prFilterNow() };
    try {
      document.getElementById("panel").innerHTML = `<div class="page gh-page">
        <div class="gh-dash${rail ? " with-rail" : ""}">
          <div class="gh-head">${HEADER(!rail)}</div>
          ${rail ? railHtml() : ""}
          <div class="gh-body stack">${dashboardContent()}</div>
        </div>${rail && page.managing ? managerHtml() : ""}</div>`;
    } finally {
      prMemo = null;
    }
    if (!rail) page.managing = false;
    placeManager();
    bindDashboard();
  }

  function bindDashboard() {
    const panel = document.getElementById("panel");
    bindRefresh(() => renderDashboard(true));
    GhUI.bindSetup(() => renderDashboard(true));
    for (const tab of panel.querySelectorAll("[data-tab]")) {
      tab.onclick = () => {
        page.tab = tab.dataset.tab;
        paintDashboard();
        if (page.tab === "pulls") loadFlags();
      };
    }
    for (const btn of panel.querySelectorAll("[data-pr-filter]")) {
      const pick = (id) => {
        page.prFilter = id;
        paintDashboard();
        const sel = document.querySelector('#panel [data-pr-filter][aria-checked="true"]');
        if (sel) sel.focus();
      };
      btn.onclick = () => pick(btn.dataset.prFilter);
      btn.onkeydown = (e) => {
        // radiogroup: arrows move to the next or previous offered filter
        if (!e.key.startsWith("Arrow")) return;
        e.preventDefault();
        const ids = [...panel.querySelectorAll("[data-pr-filter]")].map((b) => b.dataset.prFilter);
        const step = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
        pick(ids[(ids.indexOf(btn.dataset.prFilter) + step + ids.length) % ids.length]);
      };
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
    for (const btn of panel.querySelectorAll("[data-add-repo]")) btn.onclick = () => { page.managing = false; paintDashboard(); openAdd(); };
    const gear = document.getElementById("gh-manage");
    if (gear) gear.onclick = () => { page.managing = !page.managing; paintDashboard(); };
    for (const btn of panel.querySelectorAll("[data-unpin]")) btn.onclick = () => unpin(btn.dataset.unpin);
    bindDrag(panel);
    const search = document.getElementById("gh-search");
    if (search) {
      search.oninput = () => { page.filter = search.value; GhUI.keepFocus("gh-search", paintDashboard); };
    }
  }

  /* The run menu floats on <body>: both views repaint the panel. */
  function runMenuEl() {
    let menu = document.getElementById("gh-run-menu");
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "gh-run-menu";
      menu.className = "gh-menu";
      menu.setAttribute("role", "menu");
      menu.hidden = true;
      document.body.appendChild(menu);
    }
    return menu;
  }

  function closeRunMenu(refocus = false) {
    const m = page.runMenu;
    if (!m) return;
    page.runMenu = null;
    runMenuEl().hidden = true;
    const btn = document.querySelector(`[data-run-menu="${CSS.escape(m.key)}"]`);
    if (btn) { btn.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); if (refocus) btn.focus(); }
  }

  function openRunMenu(btn) {
    const key = btn.dataset.runMenu;
    if (page.runMenu && page.runMenu.key === key) return closeRunMenu(true);
    closeRunMenu();
    page.runMenu = { key, repo: btn.dataset.repo, run: btn.dataset.run, failed: !!btn.dataset.failed, url: btn.dataset.url };
    btn.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    const menu = runMenuEl();
    const busy = page.rerunning.has(key);
    menu.innerHTML = `${page.runMenu.failed ? `<button type="button" role="menuitem" data-rerun="failed"${busy ? " disabled" : ""}>${ico("rerun")}<span>Re-run failed jobs</span></button>` : ""}
      <button type="button" role="menuitem" data-rerun="all"${busy ? " disabled" : ""}>${ico("rerun")}<span>Re-run all jobs</span></button>
      ${safeHref(page.runMenu.url) ? `<div class="gh-menu-divider" role="separator"></div>
      <a role="menuitem" href="${safeHref(page.runMenu.url)}" target="_blank" rel="noopener">${ico("external")}<span>Open on GitHub</span></a>` : ""}`;
    menu.hidden = false;
    placeRunMenu();
    const m = page.runMenu;
    for (const item of menu.querySelectorAll("[data-rerun]")) item.onclick = () => rerun(m, item.dataset.rerun === "failed");
    const open = menu.querySelector("a");
    if (open) open.onclick = () => closeRunMenu();
    const first = menu.querySelector("[role=menuitem]:not([disabled])");
    if (first) first.focus();
    // A menu's keys: arrows move between items, Home and End jump.
    menu.onkeydown = (e) => {
      const items = [...menu.querySelectorAll("[role=menuitem]:not([disabled])")];
      const at = items.indexOf(document.activeElement);
      const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[e.key];
      if (to === undefined || !items.length) return;
      e.preventDefault();
      items[(to + items.length) % items.length].focus();
    };
  }

  function placeRunMenu() {
    const m = page.runMenu;
    const menu = document.getElementById("gh-run-menu");
    const btn = m && document.querySelector(`[data-run-menu="${CSS.escape(m.key)}"]`);
    if (!menu || menu.hidden) return;
    if (!btn) return closeRunMenu();
    const r = btn.getBoundingClientRect();
    const below = r.bottom + 4 + menu.offsetHeight <= window.innerHeight;
    menu.style.top = `${Math.round(below ? r.bottom + 4 : r.top - 4 - menu.offsetHeight)}px`;
    menu.style.left = `${Math.round(Math.max(8, Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8)))}px`;
  }

  /* The view on screen, whichever it is, repainted in place. */
  function repaintView() {
    const repo = GhUI.hashParams().get("repo");
    if (repo && page.focus && page.focus.repo === repo && page.focus.data) GhUI.keepFocus("gh-search", paintFocus);
    else if (onDashboard()) GhUI.keepFocus("gh-search", paintDashboard);
  }

  /* GitHubModel.rerunRun: success only makes the next read fresh (the
   * hub dropped the repo's cache); the row flips on GitHub's schedule. */
  async function rerun(m, failedOnly) {
    closeRunMenu(true);
    if (page.rerunning.has(m.key)) return;
    page.rerunning.add(m.key);
    page.actionError = null;
    repaintView();
    const { status, data } = await GhUI.post("/v1/app/repositories/rerun",
      { repo: m.repo, run: Number(m.run), failed: failedOnly });
    page.rerunning.delete(m.key);
    if (status === 200 && data) {
      const repo = GhUI.hashParams().get("repo");
      if (repo && page.focus && page.focus.repo === repo) return renderFocus(repo);
      if (onDashboard()) return renderDashboard();
      return;
    }
    page.actionError = status === 401 ? CREDENTIAL_REFUSED
      : GhUI.failMessage(status, data);
    repaintView();
  }

  /* Shared by both views: fold rows, PR rows and run menus. */
  function bindRows(root, repaint) {
    for (const btn of root.querySelectorAll("[data-run-menu]")) {
      btn.onclick = (e) => { e.stopPropagation(); openRunMenu(btn); };
    }
    // A repaint redrew the button the open menu hangs from.
    if (page.runMenu) placeRunMenu();
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
    for (const row of root.querySelectorAll("[data-issue]")) {
      row.onclick = () => openIssue(row.dataset.repo, row.dataset.issue);
    }
  }

  /* ---------------------------------------------------------- focus page */

  function newFocus(repo) {
    return { repo, seq: 0, data: null, error: null, loading: false, tab: "pulls", filter: "",
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
    const seq = ++f.seq; // newest read wins, as on the dashboard
    f.loading = true;
    paintFocus();
    const q = `repo=${encodeURIComponent(repo)}`;
    const { status, data } = await api(`/v1/app/repositories?${q}${refresh ? "&refresh=1" : ""}`);
    if (gen !== routeGen || page.focus !== f || seq !== f.seq) return;
    f.loading = false;
    if (status === 401) return GhUI.gate("repositories");
    if (status === 404 && data && data.error && data.error.code === "not_pinned") {
      // A link to a repo the hub hasn't pinned: offer the pin itself.
      f.data = null;
      f.unpinned = { message: data.error.message, pinning: false, error: null };
      return paintUnpinned();
    }
    f.unpinned = null;
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

  function paintUnpinned() {
    const f = page.focus;
    const u = f.unpinned;
    document.getElementById("panel").innerHTML = `<div class="page gh-page">${focusHeader(f)}<div class="stack">
      <div class="card"><div class="gh-empty">${ico("stack", "gh-muted")}<span>${esc(u.message)}</span>
        <button type="button" class="gh-button gh-primary" id="gh-pin-this"${u.pinning ? " disabled" : ""}
          title="Check that GitHub has the repo, then pin it on the hub">${u.pinning ? "Pinning…" : "Pin it"}</button></div>
        ${u.error ? `<p class="error" role="alert">${esc(u.error)}</p>` : ""}</div></div></div>`;
    bindRefresh(() => renderFocus(f.repo, true));
    document.getElementById("gh-pin-this").onclick = async () => {
      if (u.pinning) return;
      const gen = routeGen;
      u.pinning = true;
      u.error = null;
      paintUnpinned();
      const { status, data } = await GhUI.post("/v1/app/repositories/add", { repo: f.repo });
      // The pin stands either way; only the page that asked repaints.
      if (gen !== routeGen || page.focus !== f || f.unpinned !== u) return;
      u.pinning = false;
      if (status === 200 && data) {
        // The pin may be spelled differently ("O/Repo"): follow it.
        if (data.repo !== f.repo) { location.hash = `#/repositories?repo=${encodeURIComponent(data.repo)}`; return; }
        return renderFocus(f.repo, true);
      }
      u.error = status === 401 ? CREDENTIAL_REFUSED : GhUI.failMessage(status, data);
      paintUnpinned();
    };
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
        ${page.actionError ? errorCard(page.actionError) : ""}${f.error ? errorCard(f.error) : ""}
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
    const made = !document.getElementById("gh-pr-sheet");
    const dlg = bodySheet("gh-pr-sheet", "sheet gh-sheet", "Pull request");
    if (made) {
      // A click on the backdrop (the dialog box itself) closes it.
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
      dlg.addEventListener("close", () => { page.sheet = null; });
    }
    return dlg;
  }

  function openPr(repo, number) {
    page.sheet = { repo, number: String(number), data: null, status: 0, checksOpen: false,
      method: "squash", confirm: null, merging: false, mergeError: null, seq: 0,
      session: null }; // Open in session's panel while it is open
    const dlg = sheetEl();
    paintSheet();
    if (!dlg.open) dlg.showModal();
    loadPr(false);
  }

  async function loadPr(refresh) {
    const s = page.sheet;
    if (!s) return;
    const seq = ++s.seq; // a merge's answer or a newer read supersedes this one
    s.busy = true;
    paintSheet();
    const q = new URLSearchParams({ repo: s.repo, pr: s.number });
    if (refresh) q.set("refresh", "1");
    const { status, data } = await api(`/v1/app/repositories?${q}`);
    if (page.sheet !== s || seq !== s.seq) return;
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
            ${safeHref(c.url) ? `<a href="${safeHref(c.url)}" target="_blank" rel="noopener" title="Open the check" class="gh-muted">${ico("external", "gh-small")}</a>` : ""}
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
      const here = currentRoute();
      const focusRepo = GhUI.hashParams().get("repo");
      if (here === "repositories" && !focusRepo) renderDashboard();
      else if (here === "repositories" && page.focus && page.focus.repo === focusRepo) renderFocus(focusRepo);
    }
    if (page.sheet !== s) return;
    s.merging = false;
    if (status === 401) {
      s.mergeError = CREDENTIAL_REFUSED;
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
        // Reads begun before the merge answered are older than this.
        s.seq++;
        s.busy = false;
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
      content = errorCard(CREDENTIAL_REFUSED, false);
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
    // The first message survives a repaint (a refetch landing mid-typing).
    const typed = dlg.querySelector("#gh-session-prompt");
    const caret = typed && document.activeElement === typed ? [typed.selectionStart, typed.selectionEnd] : null;
    const typedModel = dlg.querySelector("#gh-session-model-other");
    const modelCaret = typedModel && document.activeElement === typedModel ? [typedModel.selectionStart, typedModel.selectionEnd] : null;
    dlg.innerHTML = `<div class="sheet-body">${content}${mergeErr}${confirmPanel(s, pr)}${sessionPanel(s, pr)}
      <div class="gh-sheet-actions">${mergeControls(s, pr)}<span class="gh-spacer"></span>
        ${pr && !s.session ? `<button type="button" class="icon-button" id="gh-session-open" title="Open in session: check the PR out on the hub and start an agent there"
          aria-label="Open in session">${ico("terminal")}</button>` : ""}
        ${safeHref(url) ? `<a class="icon-button" href="${safeHref(url)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open on GitHub">${ico("external")}</a>` : ""}
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
    bindSession(dlg, s, pr, caret, modelCaret);
    GhUI.bindSetup(() => loadPr(true));
  }

  /* ---------------------------------------------------------- open in session */

  /* app.openPrSession, on the hub: swe gh pr worktree checks the PR's
   * head out under ~/.quiver/worktrees, then the chosen harness starts
   * there under tmux with a first message (the hub's launch needs one;
   * the harness writes nothing until it has it), and the chat opens once
   * the session shows up, as the phone's new-session sheet does. */
  const LAUNCH_POLLS = 60;

  /* ``POST /v1/sessions`` takes an optional ``model`` pick where the
   * harness reports ``launch_model``: the shared Model menu
   * (model-menu.js) offers its ``recent_models``, newest first by
   * default, and Harness default or an empty Other… sends no ``model``
   * key. */
  const pickedHarness = (x) => (x.harnesses || []).find((h) => h.name === x.harness) || null;
  const recentModels = (x) => ModelMenu.harnessRecents(pickedHarness(x));

  function sessionPanel(s, pr) {
    const x = s.session;
    if (!x || !pr) return "";
    const busy = !!x.phase;
    let picker;
    if (x.harnesses === null) picker = `<div class="caption gh-muted">${ico("running", "gh-spin gh-small")} Reading what the hub can start…</div>`;
    else if (!x.harnesses.length) picker = `<div class="caption gh-muted">No harness can start on the hub: install one there, or turn it on in Settings › Providers.</div>`;
    else picker = `<select class="field" id="gh-session-harness" aria-label="Harness"${busy || x.started ? " disabled" : ""}>${
      x.harnesses.map((h) => `<option value="${esc(h.name)}"${h.name === x.harness ? " selected" : ""}>${esc(h.display_name)}</option>`).join("")}</select>`;
    const picked = pickedHarness(x);
    const model = picked && picked.launch_model
      ? ModelMenu.html(x.model, recentModels(x), "gh-session-model", busy || x.started)
      : "";
    const ready = x.harnesses && x.harnesses.length && x.prompt.trim() && !busy && !x.started;
    const where = s.data && s.data.machine ? ` on ${esc(s.data.machine)}` : " on the hub";
    return `<div class="gh-confirm gh-session" role="group" aria-label="Open in session">
      <div class="gh-confirm-text">Check out <strong>#${esc(pr.number)}</strong>'s head${where} and start an agent in it.</div>
      ${picker}
      ${model}
      <textarea class="field gh-session-prompt" id="gh-session-prompt" rows="3" aria-label="First message"
        placeholder="Its first message"${busy || x.started ? " disabled" : ""}>${esc(x.prompt)}</textarea>
      <div class="caption gh-muted">It runs under tmux in ~/.quiver/worktrees. The chat opens once the session shows up.</div>
      ${x.error ? `<div class="caption gh-danger-text" role="alert">${esc(x.error)}</div>` : ""}
      ${x.note ? `<div class="caption" role="status">${esc(x.note)}</div>` : ""}
      <div class="gh-confirm-buttons">
        <button type="button" class="gh-button" id="gh-session-cancel">${x.started ? "Close" : "Cancel"}</button>
        <button type="button" class="gh-button gh-primary" id="gh-session-start"${ready ? "" : " disabled"}>${esc(x.phase || "Start")}</button>
      </div></div>`;
  }

  function bindSession(dlg, s, pr, caret, modelCaret) {
    const open = dlg.querySelector("#gh-session-open");
    if (open) open.onclick = () => openSession(s, pr);
    const x = s.session;
    // The panel is drawn only over a loaded PR; a failed refetch hides it.
    if (!x || !pr) return;
    const harness = dlg.querySelector("#gh-session-harness");
    if (harness) harness.onchange = () => {
      x.harness = harness.value;
      // Re-default the model menu to this harness's newest unless the
      // user picked; the menu may appear or go away.
      ModelMenu.redefault(x.model, recentModels(x));
      paintSheet();
      const again = dlg.querySelector("#gh-session-harness");
      if (again) again.focus();
    };
    ModelMenu.bind(dlg, x.model, recentModels(x), "gh-session-model", paintSheet);
    const typedModel = dlg.querySelector("#gh-session-model-other");
    if (typedModel && modelCaret) { typedModel.focus(); typedModel.setSelectionRange(modelCaret[0], modelCaret[1]); }
    const prompt = dlg.querySelector("#gh-session-prompt");
    const start = dlg.querySelector("#gh-session-start");
    if (prompt) {
      prompt.oninput = () => {
        x.prompt = prompt.value;
        if (start) start.disabled = !(x.harnesses && x.harnesses.length && x.prompt.trim() && !x.phase && !x.started);
      };
      if (caret) { prompt.focus(); prompt.setSelectionRange(caret[0], caret[1]); }
    }
    if (start) start.onclick = () => startSession(s);
    dlg.querySelector("#gh-session-cancel").onclick = () => {
      s.session = null; // a launch still waiting is dropped from view; the hub keeps it
      paintSheet();
      const back = dlg.querySelector("#gh-session-open");
      if (back) back.focus();
    };
  }

  async function openSession(s, pr) {
    const x = { harnesses: null, harness: null, model: ModelMenu.fresh(),
      phase: null, error: null, note: null, started: false,
      prompt: `Review pull request #${pr.number}, "${pr.title}", in ${pr.repo}. Its head is checked out in this folder.` };
    s.session = x;
    paintSheet();
    const prompt = document.getElementById("gh-session-prompt");
    if (prompt) prompt.focus();
    const { status, data } = await api("/v1/app/repositories?harnesses=1");
    if (page.sheet !== s || s.session !== x) return;
    x.harnesses = status === 200 && data ? data.harnesses || [] : [];
    if (status !== 200) x.error = GhUI.failMessage(status, data);
    x.harness = x.harnesses.length ? x.harnesses[0].name : null;
    ModelMenu.redefault(x.model, recentModels(x));
    paintSheet();
  }

  async function startSession(s) {
    const x = s.session;
    if (!x || x.phase || x.started || !x.harness || !x.prompt.trim()) return;
    const alive = () => page.sheet === s && s.session === x;
    const say = (fields) => { Object.assign(x, fields); if (alive()) paintSheet(); };
    say({ phase: "Checking out…", error: null, note: null });
    const made = await GhUI.post("/v1/app/repositories/worktree", { repo: s.repo, number: Number(s.number) });
    if (!alive()) return;
    if (made.status !== 200 || !made.data) return say({ phase: null, error: GhUI.failMessage(made.status, made.data) });
    say({ phase: "Starting…" });
    const body = { harness: x.harness, cwd: made.data.path, prompt: x.prompt.trim() };
    const h = pickedHarness(x);
    const model = ModelMenu.value(x.model);
    if (h && h.launch_model && model) body.model = model;
    const launched = await GhUI.post("/v1/sessions", body);
    if (!alive()) return;
    await followLaunch(launched, "gh-pr-sheet", alive, say);
  }

  /* A launch's answer (POST /v1/sessions or /v1/issues/resolve), then
   * GET /v1/launches/<id> once a second until the session shows up: the
   * sheet ``sheetId`` closes and the chat opens. ``alive`` says the panel
   * that asked is still on screen; ``say`` updates it and repaints. */
  async function followLaunch(launched, sheetId, alive, say) {
    const id = launched.data && launched.data.launch_id;
    if ((launched.status !== 202 && launched.status !== 200) || !id) {
      return say({ phase: null, error: GhUI.failMessage(launched.status, launched.data) });
    }
    say({ phase: "Waiting for the session…" });
    let launch = launched.data;
    for (let polls = 0; ; polls++) {
      if (launch.state === "started" && launch.key) {
        document.getElementById(sheetId).close();
        location.hash = `#/sessions?chat=${encodeURIComponent(launch.key)}`;
        return;
      }
      if (launch.state === "untracked") {
        return say({ phase: null, started: true, note: "Started. Quiver can't read this harness's sessions yet, so there's no chat to open. It keeps running under tmux on the hub." });
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
      if (status === 200 && data) launch = data; // a dropped poll is retried
    }
    say({ phase: null, started: true, note: "It started but hasn't shown up yet. It'll appear in Sessions when it does." });
  }

  /* ---------------------------------------------------------- issue sheet */

  /* IssueDetailSheet: what GET /v1/issues/<o>/<r>/<n> answers (title,
   * labels, body, every comment), the comment box, Open on GitHub, and
   * Resolve with agent, which opens ResolveIssueSheet's form as a panel
   * in the sheet. A comment posts through the hub's own gh login (POST
   * /v1/issues/comment), as the Mac's posts through gh on the Mac. */
  function issueSheetEl() {
    const made = !document.getElementById("gh-issue-sheet");
    const dlg = bodySheet("gh-issue-sheet", "sheet gh-sheet", "Issue");
    if (made) {
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
      dlg.addEventListener("close", () => { page.issue = null; });
    }
    return dlg;
  }

  function openIssue(repo, number) {
    page.issue = { repo, number: String(number), data: null, status: 0, seq: 0, busy: false,
      resolve: null, // the Resolve panel while it is open
      comment: { draft: "", posting: false, error: null } }; // the comment box
    const dlg = issueSheetEl();
    paintIssue();
    if (!dlg.open) dlg.showModal();
    loadIssue(false);
  }

  async function loadIssue(refresh) {
    const s = page.issue;
    if (!s) return;
    const seq = ++s.seq;
    s.busy = true;
    paintIssue();
    const [owner, name] = s.repo.split("/", 2);
    const path = `/v1/issues/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${encodeURIComponent(s.number)}`;
    const { status, data } = await api(`${path}${refresh ? "?refresh=1" : ""}`);
    if (page.issue !== s || seq !== s.seq) return;
    s.busy = false;
    s.status = status;
    s.data = data;
    // The Resolve panel's first prompt follows the hub's brief until typed in.
    const r = s.resolve, issue = data && data.issue;
    if (r && !r.edited && issue && issue.suggested_prompt) r.prompt = issue.suggested_prompt;
    paintIssue();
  }

  function issueBody(issue, repo) {
    const open = issue.state === "open";
    const when = GhUI.ago(issue.created_at || issue.updated_at);
    const meta = [`#${esc(issue.number)}`, esc(repo), `by ${esc(issue.author)}`, when ? `opened ${when}` : ""].filter(Boolean).join(" · ");
    const marks = [...(issue.labels || []).slice(0, 5).map(badge), ...(issue.assignees || []).slice(0, 3).map((a) => badge(`@${a}`))].join("");
    const header = `<div class="gh-pr-head"><span class="gh-pr-icon">${ico(open ? "issueOpen" : "issueClosed", open ? "gh-success" : "gh-muted")}</span>
      <div class="gh-pr-headtext"><div class="gh-pr-title">${esc(issue.title)}</div>
        <div class="caption gh-ellipsis">${meta}</div>${marks ? `<div class="gh-pr-marks">${marks}</div>` : ""}</div></div>`;
    const body = String(issue.body || "").trim();
    const description = `<div class="gh-section"><div class="caption gh-muted">Description</div>
      <div class="gh-settings-card gh-md">${body ? GhUI.markdown(body) : '<span class="gh-muted">No description.</span>'}</div></div>`;
    const comments = issue.comments || [];
    const thread = `<div class="gh-section"><div class="caption gh-muted">${comments.length ? `Comments · ${comments.length}` : "Comments"}</div>
      <div class="gh-settings-card">${comments.length ? comments.map((c) => {
        const ago = GhUI.ago(c.created_at);
        const href = safeHref(c.url);
        return `<div class="gh-comment"><div class="gh-comment-head"><span class="gh-check-name">${esc(c.author)}</span>
            ${ago ? `<span class="caption gh-muted">${ago}</span>` : ""}<span class="gh-spacer"></span>
            ${href ? `<a href="${href}" target="_blank" rel="noopener" class="gh-muted" title="Open this comment on GitHub" aria-label="Open this comment on GitHub">${ico("external", "gh-small")}</a>` : ""}</div>
          <div class="gh-md gh-comment-body">${GhUI.markdown(String(c.body || ""))}</div></div>`;
      }).join("") : '<div class="gh-comment caption gh-muted">No comments yet.</div>'}</div></div>`;
    return `${header}${description}${thread}`;
  }

  function paintIssue() {
    const s = page.issue;
    const dlg = document.getElementById("gh-issue-sheet");
    if (!s || !dlg) return;
    const d = s.data;
    const retry = '<button type="button" class="gh-button" data-issue-refresh>Refresh</button>';
    let content, issue = null;
    if (!d && !s.status) {
      content = loadingCard("Fetching the issue…", false);
    } else if (s.status === 401) {
      content = errorCard(CREDENTIAL_REFUSED, false);
    } else if (s.status !== 200 || !d) {
      content = errorCard(GhUI.failMessage(s.status, d), false, retry);
    } else if (GhUI.envSetup(d.github)) {
      content = GhUI.setupCard(GhUI.envSetup(d.github), d.machine);
    } else if (d.status !== "ok" || !d.issue) {
      content = errorCard(d.error || d.status, false, retry);
    } else {
      issue = d.issue;
      content = `<div class="gh-sheet-scroll">${issueBody(issue, s.repo)}</div>`;
    }
    const url = issue && issue.url ? issue.url : `https://github.com/${s.repo}/issues/${s.number}`;
    const closed = issue && issue.state !== "open";
    // Typing survives a repaint (a refetch landing mid-word).
    const carets = {};
    for (const id of ["gh-resolve-prompt", "gh-resolve-context", "gh-resolve-model-other", "gh-issue-comment"]) {
      const el = dlg.querySelector("#" + id);
      if (el && document.activeElement === el) carets[id] = [el.selectionStart, el.selectionEnd];
    }
    dlg.innerHTML = `<div class="sheet-body">${content}${composer(s, issue)}${resolvePanel(s, issue)}
      <div class="gh-sheet-actions">
        ${issue && !s.resolve ? `<button type="button" class="gh-button gh-primary" id="gh-resolve-open"${closed ? " disabled" : ""}
          title="${closed ? "This issue is closed." : "Start an agent on this issue in its own worktree"}">${ico("terminal")}<span>Resolve with agent</span></button>` : ""}
        <span class="gh-spacer"></span>
        ${safeHref(url) ? `<a class="icon-button" href="${safeHref(url)}" target="_blank" rel="noopener" title="Open on GitHub" aria-label="Open on GitHub">${ico("external")}</a>` : ""}
        <button type="button" class="icon-button${s.busy && d ? " spinning" : ""}" data-issue-refresh title="Fetch again" aria-label="Fetch again">${icon("rescan")}</button>
        <button type="button" class="gh-button" id="gh-issue-done">Done</button>
      </div></div>`;
    for (const btn of dlg.querySelectorAll("[data-issue-refresh]")) btn.onclick = () => loadIssue(true);
    dlg.querySelector("#gh-issue-done").onclick = () => dlg.close();
    const open = dlg.querySelector("#gh-resolve-open");
    if (open) open.onclick = () => openResolve(s, issue);
    bindComposer(dlg, s, carets);
    bindResolve(dlg, s, issue, carets);
    GhUI.bindSetup(() => loadIssue(true));
  }

  /* The Mac sheet's comment box, under the thread: Return breaks the
   * line, Cmd or Ctrl+Return posts. Off while the Resolve panel is open,
   * which has its own "Also post as a comment". */
  function composer(s, issue) {
    const c = s.comment;
    if (!issue || s.resolve) return "";
    const d = s.data || {};
    const login = d.github && d.github.login;
    const off = c.posting ? " disabled" : "";
    return `<div class="gh-composer" role="group" aria-label="Add a comment">
      <div class="gh-composer-row">
        <textarea class="field gh-session-prompt" id="gh-issue-comment" rows="2" aria-label="Comment"
          placeholder="Add context for whoever picks this up, you or an agent…"${off}>${esc(c.draft)}</textarea>
        <button type="button" class="gh-button" id="gh-issue-comment-post" title="Post this comment on GitHub (⌘↩)"${
          c.posting || !c.draft.trim() ? " disabled" : ""}>${c.posting ? "Posting…" : "Comment"}</button>
      </div>
      <div class="caption gh-muted">Posts to GitHub as ${login ? esc(login) : "the hub's gh account"}, through gh on ${
        d.machine ? esc(d.machine) : "the hub"}. Markdown works.</div>
      ${c.error ? `<div class="caption gh-danger-text" role="alert">${esc(c.error)}</div>` : ""}
    </div>`;
  }

  function bindComposer(dlg, s, carets) {
    const box = dlg.querySelector("#gh-issue-comment");
    const btn = dlg.querySelector("#gh-issue-comment-post");
    if (!box || !btn) return;
    const c = s.comment;
    const caret = carets["gh-issue-comment"];
    if (caret) { box.focus(); box.setSelectionRange(caret[0], caret[1]); }
    // No repaint per keystroke: only the button follows the text.
    box.oninput = () => { c.draft = box.value; btn.disabled = c.posting || !c.draft.trim(); };
    box.onkeydown = (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); postComment(s); }
    };
    btn.onclick = () => postComment(s);
  }

  /* POST /v1/issues/comment, then read the issue again (the hub dropped
   * its cached copy) so the thread lists the new comment. */
  async function postComment(s) {
    const c = s.comment;
    const text = c.draft;
    if (!text.trim() || c.posting) return;
    c.posting = true;
    c.error = null;
    paintIssue();
    const { status, data } = await GhUI.post("/v1/issues/comment",
      { repo: s.repo, number: Number(s.number), body: text });
    if (page.issue !== s) return;
    c.posting = false;
    if (status !== 200) {
      c.error = GhUI.failMessage(status, data);
      paintIssue();
      const box = document.getElementById("gh-issue-comment");
      if (box) box.focus();
      return;
    }
    if (c.draft === text) c.draft = "";
    loadIssue(false);
  }

  /* ResolveIssueSheet's form: where it starts, the harness, the Model
   * menu (model-menu.js), Your context and the first prompt, prefilled
   * with the hub's brief; an empty prompt lets the hub write it. */
  function resolvePanel(s, issue) {
    const x = s.resolve;
    if (!x || !issue) return "";
    const busy = !!x.phase, off = busy || x.started ? " disabled" : "";
    let picker;
    if (x.harnesses === null) picker = `<div class="caption gh-muted">${ico("running", "gh-spin gh-small")} Reading what the hub can start…</div>`;
    else if (!x.harnesses.length) picker = `<div class="caption gh-muted">No harness can start on the hub: install one there, or turn it on in Settings › Providers.</div>`;
    else picker = `<select class="field" id="gh-resolve-harness" aria-label="Harness"${off}>${
      x.harnesses.map((h) => `<option value="${esc(h.name)}"${h.name === x.harness ? " selected" : ""}>${esc(h.display_name)}</option>`).join("")}</select>`;
    const picked = pickedHarness(x);
    const model = picked && picked.launch_model ? ModelMenu.html(x.model, recentModels(x), "gh-resolve-model", busy || x.started) : "";
    const where = s.data && s.data.machine ? ` on ${esc(s.data.machine)}` : " on the hub";
    const branch = issue.branch || `quiver/issue-${issue.number}`;
    // A refetch can find the issue closed since the panel opened: Start
    // goes off, as the sheet's Resolve with agent is for a closed issue.
    const closed = issue.state !== "open";
    const ready = x.harnesses && x.harnesses.length && !busy && !x.started && !closed;
    return `<div class="gh-confirm gh-session" role="group" aria-label="Resolve with agent">
      <div class="gh-confirm-text">Resolve <strong>#${esc(issue.number)}</strong> with an agent: it starts in <code>${esc(branch)}</code>,
        a worktree off the default branch${where}.</div>
      ${picker}
      ${model}
      <label class="caption gh-muted" for="gh-resolve-context">Your context (optional)</label>
      <textarea class="field gh-session-prompt" id="gh-resolve-context" rows="2"
        placeholder="Anything the agent should know that the issue doesn't say."${off}>${esc(x.context)}</textarea>
      <label class="gh-check caption gh-muted"><input type="checkbox" id="gh-resolve-post"${x.postContext ? " checked" : ""}${off}>
        <span>Also post as a comment on #${esc(issue.number)}</span></label>
      <label class="caption gh-muted" for="gh-resolve-prompt">First prompt</label>
      <textarea class="field gh-session-prompt" id="gh-resolve-prompt" rows="5"
        placeholder="The hub writes one from the issue when this is empty."${off}>${esc(x.prompt)}</textarea>
      <div class="caption gh-muted">It runs under tmux on the hub. The chat opens once the session shows up.</div>
      ${closed && !x.started ? '<div class="caption gh-danger-text" role="alert">This issue is closed.</div>' : ""}
      ${x.commentWarning ? `<div class="caption gh-warning-text" role="status">${esc(x.commentWarning)}</div>` : ""}
      ${x.error ? `<div class="caption gh-danger-text" role="alert">${esc(x.error)}</div>` : ""}
      ${x.note ? `<div class="caption" role="status">${esc(x.note)}</div>` : ""}
      <div class="gh-confirm-buttons">
        <button type="button" class="gh-button" id="gh-resolve-cancel">${x.started ? "Close" : "Cancel"}</button>
        <button type="button" class="gh-button gh-primary" id="gh-resolve-start"${ready ? "" : " disabled"}>${esc(x.phase || "Start")}</button>
      </div></div>`;
  }

  function bindResolve(dlg, s, issue, carets) {
    const x = s.resolve;
    if (!x || !issue) return;
    const harness = dlg.querySelector("#gh-resolve-harness");
    if (harness) harness.onchange = () => {
      x.harness = harness.value;
      ModelMenu.redefault(x.model, recentModels(x));
      paintIssue();
      const again = dlg.querySelector("#gh-resolve-harness");
      if (again) again.focus();
    };
    ModelMenu.bind(dlg, x.model, recentModels(x), "gh-resolve-model", paintIssue);
    const context = dlg.querySelector("#gh-resolve-context");
    if (context) context.oninput = () => { x.context = context.value; };
    const post = dlg.querySelector("#gh-resolve-post");
    if (post) post.onchange = () => { x.postContext = post.checked; };
    const prompt = dlg.querySelector("#gh-resolve-prompt");
    if (prompt) prompt.oninput = () => { x.prompt = prompt.value; x.edited = true; };
    for (const [id, caret] of Object.entries(carets)) {
      const el = dlg.querySelector("#" + id);
      if (el) { el.focus(); el.setSelectionRange(caret[0], caret[1]); }
    }
    const start = dlg.querySelector("#gh-resolve-start");
    if (start) start.onclick = () => startResolve(s);
    dlg.querySelector("#gh-resolve-cancel").onclick = () => {
      s.resolve = null; // a launch still waiting is dropped from view; the hub keeps it
      paintIssue();
      const back = dlg.querySelector("#gh-resolve-open");
      if (back) back.focus();
    };
  }

  async function openResolve(s, issue) {
    const x = { harnesses: null, harness: null, model: ModelMenu.fresh(), phase: null, error: null,
      note: null, started: false, context: "", prompt: issue.suggested_prompt || "", edited: false,
      postContext: true, posted: null, commentWarning: null };
    s.resolve = x;
    paintIssue();
    const ctx = document.getElementById("gh-resolve-context");
    if (ctx) ctx.focus();
    const { status, data } = await api("/v1/app/repositories?harnesses=1");
    if (page.issue !== s || s.resolve !== x) return;
    x.harnesses = status === 200 && data ? data.harnesses || [] : [];
    if (status !== 200) x.error = GhUI.failMessage(status, data);
    x.harness = x.harnesses.length ? x.harnesses[0].name : null;
    ModelMenu.redefault(x.model, recentModels(x));
    paintIssue();
  }

  /* The Mac's IssueResolve.postsComment: the context posts only when it
   * holds text, the box is ticked, and that text didn't already post (a
   * Start retried after a failed launch must not comment twice). */
  function postsComment(context, ticked, posted) {
    const text = String(context || "").trim();
    return !!ticked && !!text && text !== String(posted || "").trim();
  }

  /* POST /v1/issues/resolve: the hub clones or reuses the mirror, makes
   * the issue's worktree and starts the harness there; the launch it
   * answers with is followed like Open in session's. With "Also post as
   * a comment" ticked the context goes to POST /v1/issues/comment first;
   * a failed comment is a warning, and the launch goes ahead. */
  async function startResolve(s) {
    const x = s.resolve;
    if (!x || x.phase || x.started || !x.harness) return;
    const issue = s.data && s.data.issue;
    if (!issue || issue.state !== "open") return;
    const alive = () => page.issue === s && s.resolve === x;
    const say = (fields) => { Object.assign(x, fields); if (alive()) paintIssue(); };
    say({ phase: "Starting…", error: null, note: null, commentWarning: null });
    const context = x.context.trim();
    if (postsComment(context, x.postContext, x.posted)) {
      say({ phase: "Posting…", note: "Posting your context to GitHub…" });
      const posted = await GhUI.post("/v1/issues/comment", { repo: s.repo, number: Number(s.number), body: context });
      if (!alive()) return;
      if (posted.status === 200) x.posted = context;
      else {
        const why = GhUI.failMessage(posted.status, posted.data);
        x.commentWarning = `Couldn't post your context to GitHub: ${why}${/[.!?]$/.test(why) ? "" : "."} The agent still gets it in the prompt.`;
      }
      say({ phase: "Starting…", note: null });
    }
    const body = { repo: s.repo, number: Number(s.number), harness: x.harness, prompt: x.prompt.trim() };
    if (x.context.trim()) body.context = x.context.trim();
    const h = pickedHarness(x);
    const model = ModelMenu.value(x.model);
    if (h && h.launch_model && model) body.model = model;
    const launched = await GhUI.post("/v1/issues/resolve", body);
    if (!alive()) return;
    await followLaunch(launched, "gh-issue-sheet", alive, say);
  }

  /* ---------------------------------------------------------- add repository */

  /* AddRepoSheet: type owner/repo, paste a GitHub URL, or search the
   * account's repos and orgs (the hub's synced catalog first, then
   * GitHub) and pin a hit. Typing is the search, after a 350 ms pause. */
  function addEl() {
    const made = !document.getElementById("gh-add-sheet");
    const dlg = bodySheet("gh-add-sheet", "sheet gh-sheet gh-add-sheet", "Pin a repository");
    if (made) {
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
      dlg.addEventListener("close", () => {
        if (page.add) clearTimeout(page.add.timer);
        page.add = null;
      });
    }
    return dlg;
  }

  function openAdd() {
    page.add = { query: "", results: [], source: null, error: null, searching: false,
      pinError: null, working: null, seq: 0, timer: null, opened: Date.now() };
    const dlg = addEl();
    paintAdd();
    if (!dlg.open) dlg.showModal();
    const field = dlg.querySelector("#gh-add-query");
    if (field) field.focus();
  }

  /* Text repo add can take as it is: owner/repo or a github.com URL. */
  function directInput(text) {
    const t = text.trim();
    if (!t) return null;
    if (t.includes("github.com")) return t;
    const parts = t.split("/");
    return parts.length === 2 && parts.every((x) => x) ? t : null;
  }

  async function runSearch() {
    const a = page.add;
    if (!a) return;
    const q = a.query.trim();
    const seq = ++a.seq; // a newer keystroke supersedes this search
    if (!q) {
      Object.assign(a, { results: [], source: null, error: null, searching: false });
      return paintAdd();
    }
    a.searching = true;
    paintAdd();
    const { status, data } = await api(`/v1/app/repositories?search=${encodeURIComponent(q)}`);
    if (page.add !== a || seq !== a.seq) return;
    a.searching = false;
    if (status === 200 && data) {
      // Results stay on screen while a re-search runs, and a failed one
      // keeps the last good list under its error, as the app does.
      if (data.status === "ok") { a.results = data.results || []; a.source = data.source; a.error = null; }
      else a.error = data.error || data.status;
    } else {
      a.error = status === 401 ? CREDENTIAL_REFUSED : GhUI.failMessage(status, data);
    }
    paintAdd();
  }

  async function pinRepo(name) {
    const a = page.add;
    if (!a || a.working) return;
    a.working = name;
    a.pinError = null;
    paintAdd();
    const { status, data } = await GhUI.post("/v1/app/repositories/add", { repo: name });
    if (page.add !== a) return;
    a.working = null;
    if (status === 200 && data) {
      if (page.data) page.data.repos = data.repos;
      addEl().close();
      const here = currentRoute();
      if (here === "repositories" && !GhUI.hashParams().get("repo")) renderDashboard();
      return;
    }
    a.pinError = status === 401 ? CREDENTIAL_REFUSED : GhUI.failMessage(status, data);
    paintAdd();
  }

  function hitRow(hit, a) {
    const pinned = pins().some((p) => p.name.toLowerCase() === String(hit.name).toLowerCase());
    const stars = typeof hit.stars === "number" ? hit.stars.toLocaleString("en-US") : "0";
    const side = pinned ? badge("Pinned")
      : a.working === hit.name ? ico("running", "gh-muted gh-spin")
        : `<button type="button" class="gh-button" data-pin="${esc(hit.name)}"${a.working ? " disabled" : ""}>Pin</button>`;
    return `<div class="gh-hit">
      <div class="gh-hit-main">
        <div class="gh-hit-title"><span class="gh-title">${esc(hit.name)}</span>
          <span class="caption gh-muted gh-inline">${ico("star", "gh-small")}<span class="num">${stars}</span></span></div>
        ${hit.description ? `<div class="caption gh-clamp2">${esc(hit.description)}</div>` : ""}
      </div>
      <div class="gh-hit-side">${side}</div>
    </div>`;
  }

  /* The field is drawn once per opening and never repainted, so typing
   * (an IME composition included) is never interrupted; the droplist and
   * the buttons under it repaint on their own. */
  function paintAdd() {
    const a = page.add;
    const dlg = document.getElementById("gh-add-sheet");
    if (!a || !dlg) return;
    if (dlg.dataset.for !== String(a.opened)) {
      dlg.dataset.for = String(a.opened);
      dlg.innerHTML = `<div class="sheet-body">
        <div class="sheet-title">Pin a repository</div>
        <div class="caption">The pinned repos drive the Repositories page and act as the fallback when a command runs outside a checkout.</div>
        <label class="gh-add-label caption" for="gh-add-query">Repository</label>
        <input class="field gh-mono" id="gh-add-query" type="search" placeholder="owner/repo, GitHub URL, or search terms"
          autocomplete="off" spellcheck="false">
        <div class="gh-add-results" id="gh-add-results" aria-live="polite"></div>
        <div class="gh-sheet-actions" id="gh-add-actions"></div></div>`;
      const input = dlg.querySelector("#gh-add-query");
      input.oninput = () => {
        a.query = input.value;
        clearTimeout(a.timer);
        // An emptied field clears the list right away; text waits for a pause.
        if (!a.query.trim()) runSearch();
        else a.timer = setTimeout(runSearch, 350);
        paintAdd();
      };
      input.onkeydown = (e) => {
        if (e.key === "Enter") { e.preventDefault(); clearTimeout(a.timer); runSearch(); }
      };
    }
    const direct = directInput(a.query);
    const error = a.pinError || a.error;
    const list = a.searching || a.results.length ? `<div class="gh-hits" role="list">
        ${a.searching ? `<div class="gh-hits-busy caption gh-muted">${ico("running", "gh-spin")}<span>Searching your repos…</span></div>` : ""}
        <div class="gh-hits-scroll">${a.results.map((h) => hitRow(h, a)).join("")}</div>
      </div>` : "";
    const where = a.results.length && !a.searching ? `<div class="caption gh-muted">${a.source === "catalog"
      ? "From the hub's synced catalog. Not here? Sync it in Settings › Integrations, or type owner/name."
      : "From GitHub: your account's and your organizations' repos."}</div>` : "";
    const results = dlg.querySelector("#gh-add-results");
    results.innerHTML = `${error ? `<div class="caption gh-danger-text" role="alert">${esc(error)}</div>` : ""}${list}${where}`;
    for (const btn of results.querySelectorAll("[data-pin]")) btn.onclick = () => pinRepo(btn.dataset.pin);
    const actions = dlg.querySelector("#gh-add-actions");
    actions.innerHTML = `<span class="gh-spacer"></span>
      <button type="button" class="gh-button" id="gh-add-cancel">Cancel</button>
      ${direct ? `<button type="button" class="gh-button gh-primary" id="gh-add-direct"${a.working ? " disabled" : ""}
        title="Check that the repo exists, then pin it">${a.working === direct ? "Pinning…" : `Pin ${esc(direct)}`}</button>` : ""}`;
    actions.querySelector("#gh-add-cancel").onclick = () => dlg.close();
    const go = actions.querySelector("#gh-add-direct");
    if (go) go.onclick = () => pinRepo(direct);
  }

  /* ---------------------------------------------------------- entry */

  /* Either sheet open: the minute poll waits, so rows never shift
   * under a confirm or a search. */
  const SHEETS = ["gh-pr-sheet", "gh-issue-sheet", "gh-add-sheet"];
  const sheetOpen = () => SHEETS.some((id) => (document.getElementById(id) || {}).open);
  function closeSheets() {
    for (const id of SHEETS) {
      const dlg = document.getElementById(id);
      if (dlg && dlg.open) dlg.close();
    }
  }

  function render() {
    closeSheets();
    const repo = GhUI.hashParams().get("repo");
    if (repo) {
      renderFocus(repo);
      // A #/repositories?repo=…&pr=… link opens the PR sheet on the
      // focus page — the route the Tasks page's PR card deep-links to.
      const pr = GhUI.hashParams().get("pr");
      if (pr) openPr(repo, pr);
      GhUI.poll("repositories", () => {
        if (!page.focus || page.focus.loading || page.focus.unpinned || sheetOpen()) return false;
        renderFocus(page.focus.repo);
      });
    } else {
      renderDashboard();
      GhUI.poll("repositories", () => {
        if (page.loading || sheetOpen()) return false;
        renderDashboard();
      });
    }
  }

  window.addEventListener("resize", () => { if (page.managing) placeManager(); if (page.runMenu) placeRunMenu(); });
  document.addEventListener("scroll", () => { if (page.managing) placeManager(); if (page.runMenu) placeRunMenu(); }, true);
  document.addEventListener("click", (e) => {
    if (page.runMenu && !(e.target.closest && e.target.closest("#gh-run-menu"))) closeRunMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && page.runMenu) { e.stopPropagation(); closeRunMenu(true); }
  }, true);

  /* The gear popover closes on a click outside it or on Escape. */
  document.addEventListener("click", (e) => {
    if (!page.managing || !onDashboard()) return;
    if (e.target.closest && (e.target.closest("#gh-manage-pop") || e.target.closest("#gh-manage"))) return;
    page.managing = false;
    paintDashboard();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && page.managing && onDashboard() && !sheetOpen()) {
      page.managing = false;
      paintDashboard();
      const gear = document.getElementById("gh-manage");
      if (gear) gear.focus();
    }
  });

  /* ⌘K / Ctrl+K focuses the search, as in the app. */
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      const search = document.getElementById("gh-search");
      if (search) { e.preventDefault(); search.focus(); search.select(); }
    }
  });

  /* The sheets live on <body> as app.js's bodySheets, so route() closes
   * them on every navigation; the run menu and a stale action error go
   * here. */
  window.addEventListener("hashchange", () => {
    closeRunMenu();
    page.actionError = null;
  });

  registerPage("repositories", render);
})();
