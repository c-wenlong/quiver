/* First-run setup: a port of the Mac app's FirstRunSetupSheet (and
 * QuiverCore's FirstRunSetupModel) for the machine the hub runs on, at
 * #/setup. Not in the sidebar. The page opens on a "Set up this machine"
 * card; its steps are the sheet's first three: the agents you use, one
 * AGENTS.md, skills folder and MCP list for all of them, and the look
 * (kept per browser, under the key Settings › General uses). The
 * notifications step is the Mac's own and is left out.
 * Data: GET /v1/app/setup (swe setup --json in process); the writes are
 * POST /v1/app/setup/apply (swe setup --apply --json), from a missing
 * agent's Install sheet POST /v1/app/setup/install (swe install --via), and
 * from step 2's hub field POST /v1/app/setup/hub (swe setup --hub), which
 * clones on the hub's own thread while this page polls GET ?hub=1. */
"use strict";

const STEPS = ["agents", "links", "look"];
// Settings › General's appearance, in this browser: absent means System.
const APPEARANCE_KEY = "quiver.appearance";
const LOOKS = [
  ["system", "System", "Matches this browser"],
  ["light", "Light", "Bright and clear"],
  ["dark", "Dark", "Easy on the eyes"],
];
// SetupPreview.LinkRow.isLeftAlone
const LEFT_ALONE = new Set(["blocked", "protected", "conflict", "keep", "would-conflict", "would-keep"]);

const page = {
  data: null, loadError: null, loading: false, loadSeq: 0,
  wizard: false, step: 0, selected: new Set(), start: "seed",
  applying: false, outcome: null, applyError: null, appliedSelection: null, adopted: false,
  showingMore: false, installing: null, finished: false,
  // The install in flight ("<id>:<via>") and each agent's last failure.
  installRun: null, installErrors: {},
  // Step 2's hub field: what's typed, a refusal from the hub, and whether
  // this page is polling a clone.
  hubUrl: "", hubError: null, hubPolling: false,
  // A clone finished while the page wasn't shown: read the machine again
  // before the next paint, so no choice from the old preview is sent.
  hubStale: false,
};

// What swe setup prints beside its hub prompt (init/hub.py FORK_NOTE).
const FORK_NOTE = "Fork a hub repo first (or use it as a template for a private copy), then paste your fork's URL.";
const HUB_POLL_MS = 1500;

/* HarnessMark.accountName: a Claude account is "Claude". */
function agentName(id) {
  return id === "claude" ? "Claude" : Fmt.harnessName(id);
}

function tilde(path) {
  return Fmt.tilde(path, page.data && page.data.home);
}

function machineName() {
  return (page.data && page.data.machine) || "this machine";
}

function agentLogo(id) {
  const img = Fmt.logo(id, "setup-logo");
  if (img.startsWith("<img")) return img;
  return `<span class="setup-logo setup-monogram" aria-hidden="true">${esc(agentName(id).slice(0, 1))}</span>`;
}

const CHECK = `<svg class="setup-check" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="currentColor"/>
  <path d="m7.5 12.3 3 3 6-6.3" fill="none" stroke="var(--card)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

/* ------------------------------------------------------------------ data */

/* Only the newest read lands: an older one answering last would put its
 * stale preview back. */
async function load(gen) {
  const seq = ++page.loadSeq;
  page.loading = true;
  page.loadError = null;
  const { status, data } = await api("/v1/app/setup");
  if (seq !== page.loadSeq) return false;
  // Done even when the page was left meanwhile, so Refresh works on return.
  page.loading = false;
  if (gen !== routeGen) return false;
  if (status === 401) {
    renderGate();
    return false;
  }
  if (status !== 200 || !data) {
    page.loadError = failText(status, data);
    return true;
  }
  page.data = data;
  return true;
}

/* FirstRunSetupModel.defaultStart: your own instructions beat the starter
 * file, unless AGENTS.md already has your text. */
function defaultStart(instructions) {
  if (!instructions || instructions.agents_file_customised) return "seed";
  const first = (instructions.adopt_candidates || [])[0];
  return first ? first.path : "seed";
}

function startWizard() {
  const d = page.data;
  Object.assign(page, {
    wizard: true, step: 0, selected: new Set(d.agents.starred || []),
    start: defaultStart(d.preview.instructions), applying: false, outcome: null,
    applyError: null, appliedSelection: null, adopted: false, showingMore: false,
    installing: null, finished: false, installErrors: {},
  });
  // An install still running on the hub keeps its row's "Installing…".
}

function order() {
  return page.data.agents.providers.map((p) => p.id);
}

/* starredList(order:): the ticked ones in the grid's order, then any others. */
function starredList() {
  const ids = order();
  const rest = [...page.selected].filter((id) => !ids.includes(id)).sort();
  return ids.filter((id) => page.selected.has(id)).concat(rest);
}

function sameSet(a, b) {
  return a && b && a.size === b.size && [...a].every((x) => b.has(x));
}

function needsApply() {
  return !page.outcome || !!page.applyError || !sameSet(page.appliedSelection, page.selected);
}

function unstarred(outcome) {
  return (outcome && outcome.starred && outcome.starred.unknown) || [];
}

function leftAlone(outcome) {
  return ((outcome && outcome.links && outcome.links.rows) || []).filter((r) => LEFT_ALONE.has(r.result));
}

function needsReading(outcome) {
  return leftAlone(outcome).length > 0 || unstarred(outcome).length > 0;
}

async function apply() {
  if (page.applying) return;
  page.applying = true;
  page.applyError = null;
  paint();
  // Only the first run adopts: after it AGENTS.md holds that text, and
  // swe refuses a second adopt as "your own text".
  const adopt = page.start !== "seed" && !page.adopted ? page.start : null;
  const picks = new Set(page.selected);
  const { status, data } = await apiPost("/v1/app/setup/apply", { starred: starredList(), adopt });
  page.applying = false;
  if (status === 200 && data && data.applied) {
    const outcome = data.applied;
    page.outcome = outcome;
    page.appliedSelection = picks;
    if (outcome.links && outcome.links.adopted) page.adopted = true;
    page.data.first_run = data.first_run;
    page.data.agents.starred = data.starred || [];
    const failures = (outcome.errors || []).concat(outcome.links && outcome.links.error ? [outcome.links.error] : []);
    if (failures.length) page.applyError = failures.join("\n");
    // Setup changed the stars, harnesses, links and MCP servers: drop the
    // Harnesses page's copy so it reads them afresh, as the Mac app reloads.
    if (typeof harnessesPage !== "undefined") harnessesPage.data = null;
  } else {
    page.applyError = failText(status, data);
  }
}

/* next(order:): leaving the links step runs setup; a failure, or files
 * setup left alone, keep you there to read them, and a second click moves on. */
async function next() {
  if (STEPS[page.step] === "links") {
    if (needsApply()) {
      await apply();
      if (!onPage()) return;
      if (page.applyError || needsReading(page.outcome)) return paint();
    }
  }
  if (page.step === STEPS.length - 1) return finish();
  page.step += 1;
  paint();
}

function finish() {
  page.wizard = false;
  page.finished = !!page.outcome;
  paint();
}

function onPage() {
  return currentRoute() === "setup";
}

/* --------------------------------------------------------------- render */

async function render() {
  const gen = routeGen;
  if (!page.data) {
    paintShell(`<div class="card"><p class="empty">Looking for coding agents on this machine…</p></div>`);
    if (!(await load(gen))) return;
  } else if (page.hubStale && !(await reloadAfterClone())) {
    return;
  }
  paint();
  // A clone started before the page was left is still followed when you
  // come back.
  if (cloning()) pollHub();
}

function header() {
  return pageHeader("Setup", `Quiver's first-run setup, for ${machineName()}: the machine this hub runs on.`,
    `<button class="icon-button" id="page-refresh" aria-label="Check again" title="Check again">${icon("rescan")}</button>`);
}

function paintShell(body) {
  document.getElementById("panel").innerHTML = `<div class="page setup-page">${header()}${body}</div>`;
  bindRefreshButton();
}

function bindRefreshButton() {
  const btn = document.getElementById("page-refresh");
  if (!btn) return;
  btn.disabled = page.applying;
  btn.onclick = async () => {
    // One read at a time: a second click while one runs waits for it.
    if (page.applying || page.loading) return;
    const gen = routeGen;
    btn.classList.add("spinning");
    const ok = await load(gen);
    if (!ok) return;
    // A fresh preview starts the steps over, as reopening the sheet would.
    if (page.wizard && page.data) startWizard();
    paint();
  };
}

function paint() {
  if (!onPage()) return;
  if (page.loadError) {
    return paintShell(`<div class="card"><p class="error" role="alert">${esc(page.loadError)}</p>
      <div><button class="setup-button" id="setup-retry">Try again</button></div></div>`), bindRetry();
  }
  if (!page.wizard) return paintLanding();
  paintWizard();
}

function bindRetry() {
  const btn = document.getElementById("setup-retry");
  if (!btn) return;
  btn.onclick = async () => {
    btn.disabled = true;
    const gen = routeGen;
    if (!(await load(gen))) return;
    // FirstRunSetupModel.load: each fresh preview resets where AGENTS.md
    // starts from; the step and the ticks stay.
    if (page.data && page.wizard) page.start = defaultStart(page.data.preview.instructions);
    paint();
  };
}

/* The page's own entry: a "Set up this machine" card. */
function paintLanding() {
  const d = page.data;
  const file = tilde(d.agents_file);
  let card;
  if (d.first_run) {
    card = `<div class="card setup-entry">
      <div class="card-title">Set up this machine</div>
      <p class="setup-text">Quiver hasn't been set up on ${esc(machineName())} yet: there's no shared <code>${esc(file)}</code>.
        Pick the agents you use, then link one AGENTS.md, one skills folder and one list of MCP servers into each of them.</p>
      <div><button class="setup-button primary" id="setup-start">Set up</button></div></div>`;
  } else {
    const done = page.finished
      ? `<p class="setup-text">Set up. The Harnesses, <a href="#/agents">AGENTS.md</a>, <a href="#/skills">Skills</a> and
          <a href="#/mcp">MCP servers</a> pages show what changed.</p>` : "";
    card = `<div class="card setup-entry">
      <div class="card-title">This machine is set up</div>
      ${done}
      <p class="setup-text">${esc(machineName())} has a shared <code>${esc(file)}</code>. Run setup again to pick other agents,
        or to link ones installed since.</p>
      <div><button class="setup-button" id="setup-start">Run setup again</button></div></div>`;
  }
  paintShell(card);
  const start = document.getElementById("setup-start");
  start.onclick = async () => {
    // Read the machine afresh each time, as the sheet loads its preview
    // when it opens: after a run the old preview would offer the adopted
    // file again, and the hub would refuse it.
    start.disabled = true;
    start.textContent = "Checking…";
    const gen = routeGen;
    if (!(await load(gen))) return;
    if (page.loadError) return paint();
    startWizard();
    paint();
    focusTitle();
  };
}

const TITLES = {
  agents: ["Pick the agents you use",
    "Quiver shows usage, limits and sessions for every coding agent. The ones you pick go on Accounts. Change them any time on Harnesses."],
  links: ["One setup for all of them",
    "Quiver keeps one AGENTS.md, one skills folder and one list of MCP servers, and links them into each agent."],
  look: ["Pick a look", "Change it later in Settings › General. It's kept in this browser."],
};

function paintWizard() {
  const step = STEPS[page.step];
  const [title, subtitle] = TITLES[step];
  const dots = STEPS.map((_, i) => `<span class="setup-dot${i === page.step ? " on" : ""}"></span>`).join("");
  const body = step === "agents" ? agentsStep() : step === "links" ? linksStep() : lookStep();
  paintShell(`<section class="card setup-sheet" aria-labelledby="setup-title">
    <div class="setup-head">
      <div class="setup-brand"><img src="/icon-192.png" alt=""><span>Quiver</span></div>
      <div class="setup-steps" role="img" aria-label="Step ${page.step + 1} of ${STEPS.length}">${dots}
        <span class="caption">${page.step + 1} of ${STEPS.length}</span></div>
    </div>
    <div class="setup-body">
      <div class="setup-titles">
        ${step === "agents" ? `<div class="caption">Welcome to Quiver</div>` : ""}
        <h2 class="setup-title" id="setup-title" tabindex="-1">${esc(title)}</h2>
        <p class="setup-subtitle">${esc(subtitle)}</p>
      </div>
      ${body}
    </div>
    <div class="setup-foot">
      <button class="setup-link" id="setup-skip" title="Close this and set things up later"${page.applying ? " disabled" : ""}>Skip setup</button>
      <span class="setup-spacer"></span>
      ${page.step > 0 ? `<button class="setup-button" id="setup-back"${page.applying ? " disabled" : ""}>Back</button>` : ""}
      <button class="setup-button primary" id="setup-next"${continueDisabled() ? " disabled" : ""}>${esc(continueTitle())}</button>
    </div>
  </section>`);
  bindWizard();
}

function previewError() {
  const p = page.data.preview;
  return p.instructions && p.instructions.error;
}

function continueDisabled() {
  return page.applying || (STEPS[page.step] === "links" && (!!previewError() || cloning()));
}

function continueTitle() {
  if (page.step === STEPS.length - 1) return "Start using Quiver";
  if (STEPS[page.step] !== "links") return "Continue";
  if (page.applying) return "Setting up…";
  if (needsApply()) return "Set up";
  return "Continue";
}

function focusTitle() {
  const t = document.getElementById("setup-title");
  if (t) t.focus();
}

/* ------------------------------------------------------- step 1: agents */

function agentsStep() {
  const a = page.data.agents;
  const installed = a.providers.filter((p) => p.installed);
  const others = a.providers.filter((p) => !p.installed);
  const missing = others.filter((p) => page.selected.has(p.id));
  let html = "";
  if (a.error) html += `<div class="card setup-error"><p class="error" role="alert">${esc(a.error)}</p></div>`;
  // A failed install whose agent now reads as found (its command landed on
  // PATH before the installer failed) has no banner left to show it in.
  for (const [id, error] of Object.entries(page.installErrors)) {
    if (!missing.some((p) => p.id === id)) {
      html += `<div class="card setup-error"><p class="error setup-pre" role="alert">Installing ${esc(agentName(id))}: ${esc(error)}</p></div>`;
    }
  }
  html += missing.map(missingBanner).join("");
  html += `<div class="setup-found"><span>Found on ${esc(machineName())}</span><span class="setup-count num">${installed.length}</span></div>`;
  html += installed.length ? grid(installed)
    : `<p class="setup-text">No coding agents found yet. Pick the ones you plan to use below.</p>`;
  if (others.length) {
    html += `<button class="setup-link underline" id="setup-more" aria-expanded="${page.showingMore}">${
      page.showingMore ? "Hide agents" : `Show ${others.length} more agents`}</button>`;
    if (page.showingMore) html += grid(others);
  }
  return `<div class="setup-step">${html}</div>`;
}

function grid(providers) {
  return `<div class="setup-grid">${providers.map((p) => {
    const on = page.selected.has(p.id);
    return `<button class="setup-tile${on ? " on" : ""}" data-agent="${esc(p.id)}" aria-pressed="${on}">
      <span class="setup-tile-logo">${agentLogo(p.id)}</span>
      <span class="setup-tile-name">${esc(agentName(p.id))}</span>
      ${on ? CHECK : ""}</button>`;
  }).join("")}</div>`;
}

function missingBanner(p) {
  const open = page.installing === p.id;
  const methods = p.install || [];
  let panel = "";
  if (open) {
    const error = page.installErrors[p.id];
    const site = /^https?:\/\//i.test(p.website || "") ? p.website : null;
    const host = site ? site.replace(/^https?:\/\//i, "").replace(/\/.*$/, "") : "";
    const websiteLink = site
      ? `<a class="setup-site-link" href="${safeHref(site)}" target="_blank" rel="noopener noreferrer">Get ${esc(agentName(p.id))} from ${esc(host)}</a>`
      : "";
    panel = methods.length
      ? `<p class="caption">Install it on ${esc(machineName())} with one of these, or copy a command to run in a terminal there.</p>
        ${p.app ? `<p class="caption">The ${esc(agentName(p.id))} app is already on ${esc(machineName())} at ${esc(p.app)}; these commands install its command line tool.</p>` : ""}
        <div class="setup-methods">${methods.map((m, i) => `<div class="setup-method${m.available ? "" : " off"}">
          <span class="setup-via">${esc(m.via)}</span>
          <code title="${esc(m.command)}">${esc(m.command)}</code>
          ${installButton(p, m)}
          <button type="button" class="icon-button copy-inline" data-copy="${i}" data-agent="${esc(p.id)}"
            aria-label="Copy command" title="Copy command">${Fmt.copyIcon()}</button></div>`).join("")}</div>
        ${error ? `<p class="error setup-pre setup-install-error" role="alert">${esc(error)}</p>` : ""}
        <p class="caption">A cask or install script that asks for a password can't finish here. Copy it into a terminal instead.</p>
        ${websiteLink}`
      : `<p class="caption">swe doesn't know an installer for ${esc(agentName(p.id))}. Get it from its own website.</p>
        ${site ? `<div class="setup-download"><code>${esc(host)}</code><a class="setup-button small" href="${safeHref(site)}" target="_blank" rel="noopener noreferrer">Download</a></div>` : ""}`;
  }
  return `<div class="setup-missing">
    <div class="setup-missing-row">
      <p class="setup-text"><span class="strong">${esc(agentName(p.id))}</span> isn't installed yet. Quiver will list it anyway, and you can install it any time.</p>
      <button class="setup-button" data-install="${esc(p.id)}" aria-expanded="${open}"
        title="Install ${esc(agentName(p.id))} on ${esc(machineName())}">Install…</button>
    </div>${panel}</div>`;
}

/* Runs swe install <id> --via=<via> on the hub; off when its tool isn't
 * on PATH there, and every one waits while an install runs. */
function installButton(p, m) {
  const key = `${p.id}:${m.via}`;
  if (page.installRun === key) {
    return `<span class="setup-installing" role="status"><span class="setup-spinner" aria-hidden="true"></span>Installing…</span>`;
  }
  if (!m.available) return `<span class="caption setup-off-path">${esc(m.via === "cask" ? "brew" : m.via === "script" ? "curl" : m.via)} not on PATH</span>`;
  return `<button class="setup-button small primary" data-run-install="${esc(m.via)}" data-agent="${esc(p.id)}"${
    page.installRun ? " disabled" : ""} title="Run ${esc(m.command)} on ${esc(machineName())}">Install</button>`;
}

async function runInstall(id, via) {
  if (page.installRun) return;
  page.installRun = `${id}:${via}`;
  delete page.installErrors[id];
  paint();
  const { status, data } = await apiPost("/v1/app/setup/install", { id, via });
  page.installRun = null;
  // Kept even if the page was left meanwhile, so coming back shows the
  // result rather than a row still installing.
  const here = onPage();
  if (status === 401) {
    if (here) renderGate();
    return;
  }
  if (status === 200 && data && data.setup) {
    page.data = data.setup;
    if (data.error) page.installErrors[id] = data.error;
  } else {
    page.installErrors[id] = failText(status, data);
  }
  if (!here || !page.wizard) return;
  paint();
  const again = document.querySelector(`[data-run-install="${CSS.escape(via)}"][data-agent="${CSS.escape(id)}"]`)
    || document.querySelector(".setup-tile");
  if (again) again.focus();
}

/* -------------------------------------- step 2: AGENTS.md, skills, MCP */

function linksStep() {
  const preview = page.data.preview;
  const err = previewError();
  if (err) {
    return `<div class="setup-step"><div class="card setup-error"><p class="error" role="alert">${esc(err)}</p></div>
      <div><button class="setup-button" id="setup-retry">Try again</button></div></div>`;
  }
  let html = "";
  if (page.applyError) html += `<div class="card setup-error"><p class="error setup-pre" role="alert">${esc(page.applyError)}</p></div>`;
  if (page.outcome && needsReading(page.outcome)) html += doneCard(page.outcome);
  html += hubCard();
  html += `<div class="card setup-rows">
    ${row("agents", "Shared instructions",
      "~/.quiver/AGENTS.md, linked into each agent as its own file (CLAUDE.md, AGENTS.md, GEMINI.md…).",
      instructionsExtra(preview.instructions))}
    ${row("skills", "Skills",
      "~/.quiver/skills becomes every agent's skills folder, so a skill you add shows up in all of them.")}
    ${row("mcp", "MCP servers", mcpDetail(preview.mcp || []))}
  </div>
  <p class="caption">Anything replaced is backed up to ~/.quiver/backups first. A real file that would be lost stays where it is.</p>`;
  return `<div class="setup-step">${html}</div>`;
}

/* ------------------------------------------------- step 2: your hub repo */

function hubJob() {
  const hub = page.data && page.data.hub;
  return (hub && hub.job) || { state: "idle" };
}

function cloning() {
  return hubJob().state === "cloning";
}

/* swe setup's hub step: shown while ~/.quiver has no hub yet, and after a
 * clone this page started, to say how it went. */
function hubCard() {
  const hub = page.data.hub;
  const job = hubJob();
  if (!hub || (!hub.can_clone && job.state === "idle")) return "";
  if (job.state === "done" && !hub.can_clone) {
    const failed = ((job.cloned && job.cloned.marketplaces) || []).filter((m) => m.error);
    return `<div class="card setup-hub" role="status">
      <div class="setup-done-title">${CHECK}<span>Cloned ${esc(job.url)} into ~/.quiver.</span></div>
      <p class="caption">Its AGENTS.md, skills, plugins and MCP servers are on ${esc(machineName())} now. Set up links them into each agent.</p>
      ${failed.map((m) => `<p class="caption note">${esc(m.marketplace)} isn't registered in ${esc(agentName(m.harness))}: ${esc(m.error)}</p>`).join("")}
    </div>`;
  }
  if (!hub.can_clone && job.state !== "cloning" && job.state !== "failed") return "";
  const busy = job.state === "cloning";
  const error = page.hubError || (job.state === "failed" ? job.error : null);
  const demo = /^https?:\/\//i.test(hub.demo_url || "")
    ? `<p class="caption">Demo hub: <a href="${safeHref(hub.demo_url)}" target="_blank" rel="noopener noreferrer">${esc(hub.demo_url)}</a></p>` : "";
  return `<div class="card setup-hub">
    <div class="card-title">Start from your hub repo</div>
    <p class="setup-text">Optional. Clone your own hub repo into ~/.quiver on ${esc(machineName())}, so its AGENTS.md, skills,
      plugins and MCP servers come with it. Leave it empty to start an empty hub.</p>
    <p class="caption">${esc(FORK_NOTE)}</p>
    ${demo}
    <form class="setup-hub-form" id="setup-hub-form">
      <input class="field" id="setup-hub-url" type="text" inputmode="url" autocomplete="off" spellcheck="false"
        aria-label="Hub repo URL" placeholder="https://github.com/you/quiver-hub.git"
        value="${esc(page.hubUrl)}"${busy || page.applying || !hub.can_clone ? " disabled" : ""}>
      <button class="setup-button" type="submit" id="setup-hub-clone"${busy || page.applying || !hub.can_clone || !page.hubUrl.trim() ? " disabled" : ""}>Clone</button>
    </form>
    ${busy ? `<p class="setup-installing" role="status"><span class="setup-spinner" aria-hidden="true"></span>Cloning ${esc(job.url)}… This can take a few minutes.</p>` : ""}
    ${error ? `<p class="error setup-pre" role="alert">${esc(error)}</p>` : ""}
  </div>`;
}

async function cloneHub() {
  const url = page.hubUrl.trim();
  if (!url || cloning()) return;
  page.hubError = null;
  const { status, data } = await apiPost("/v1/app/setup/hub", { url });
  if (status === 401) {
    if (onPage()) renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    return;
  }
  if (status === 200 && data && data.hub) {
    page.data.hub = data.hub;
    // A small or local repo can be cloned before the reply leaves the
    // hub: that job is done already and needs the same reload.
    if (hubJob().state === "done") page.hubStale = true;
  } else {
    page.hubError = failText(status, data);
  }
  // A stale preview is never painted: pollHub reloads it first.
  if (!page.hubStale) paint();
  pollHub();
}

/* Poll the clone until it ends, then read the machine again: from then on
 * the preview is of the cloned hub (its AGENTS.md, nothing to adopt). */
async function pollHub() {
  if (page.hubPolling) return;
  page.hubPolling = true;
  try {
    while (cloning() && onPage()) {
      await new Promise((resolve) => setTimeout(resolve, HUB_POLL_MS));
      const { status, data } = await api("/v1/app/setup?hub=1");
      if (status !== 200 || !data || !data.hub) continue;
      page.data.hub = data.hub;
      if (hubJob().state === "done") page.hubStale = true;
    }
    if (!onPage() || cloning()) return;
    if (page.hubStale && !(await reloadAfterClone())) return;
    paint();
  } finally {
    page.hubPolling = false;
  }
}

/* Read the machine again once a clone is done: the preview is of the
 * cloned hub from then on (its AGENTS.md, nothing to adopt), and the
 * start choice from the old one is dropped. False when the route moved
 * on, so the reload is still owed when the page comes back. */
async function reloadAfterClone() {
  const job = page.data.hub.job;
  const gen = routeGen;
  if (!(await load(gen))) return false;
  page.hubStale = false;
  if (page.data) {
    page.start = defaultStart(page.data.preview.instructions);
    // The reload carries the same finished job; keep it either way.
    if (page.data.hub && page.data.hub.job.state === "idle") page.data.hub.job = job;
  }
  page.hubUrl = "";
  return true;
}

function row(iconName, title, detail, extra = "") {
  return `<div class="setup-row"><span class="setup-row-icon">${icon(iconName)}</span>
    <div class="setup-row-main"><div class="card-title">${esc(title)}</div>
      <p class="setup-text">${esc(detail)}</p>${extra}</div></div>`;
}

function mcpDetail(servers) {
  if (!servers.length) return "None found in your agents yet. Add servers later on the MCP servers page.";
  const names = servers.map((s) => s.name).join(", ");
  return `Copies ${servers.length} server${servers.length === 1 ? "" : "s"} your agents already use into ~/.quiver/mcp.json: ${names}.`;
}

function instructionsExtra(ins) {
  if (!ins) return "";
  let html = "";
  const candidates = ins.adopt_candidates || [];
  if (ins.agents_file_customised) {
    html += `<p class="caption">Your AGENTS.md is already there and stays as it is.</p>`;
  } else if (candidates.length) {
    const opts = candidates.map((c) => `<option value="${esc(c.path)}"${page.start === c.path ? " selected" : ""}>Your ${esc(tilde(c.path))}</option>`)
      .concat(`<option value="seed"${page.start === "seed" ? " selected" : ""}>Quiver's starter file</option>`);
    html += `<label class="setup-start"><span>Start from</span>
      <select class="field" id="setup-start-from"${page.outcome || page.applying ? " disabled" : ""}>${opts.join("")}</select></label>`;
  }
  // untouchedCandidates: the ones the chosen start leaves as they are.
  for (const c of candidates.filter((c) => c.path !== page.start)) {
    html += `<p class="caption note">${esc(tilde(c.path))} has other text, so it stays as it is. Link it later on the AGENTS.md page.</p>`;
  }
  return html;
}

function doneCard(outcome) {
  const skipped = unstarred(outcome);
  const alone = leftAlone(outcome);
  return `<div class="card setup-done" role="status">
    <div class="setup-done-title">${CHECK}<span>Set up. A few things to know:</span></div>
    ${skipped.length ? `<p class="caption">Not starred, since swe has no entry for them yet: ${esc(skipped.map(agentName).join(", "))}.</p>` : ""}
    ${alone.length ? `<p class="caption">These stay as they are:</p>` : ""}
    ${alone.map((r) => `<div class="setup-alone"><code>${esc(tilde(r.path))}</code>${
      r.detail ? `<span class="caption">${esc(r.detail)}</span>` : ""}</div>`).join("")}
    <p class="caption">Continue when you've read this. The Harnesses and AGENTS.md pages show them again.</p>
  </div>`;
}

/* ---------------------------------------------------------- step 3: look */

function readLook() {
  let mode = null;
  try { mode = localStorage.getItem(APPEARANCE_KEY); } catch { /* storage off */ }
  return mode === "light" || mode === "dark" ? mode : "system";
}

/* What Settings › General does: Light and Dark set data-theme on <html>,
 * System removes it and follows the browser. */
function setLook(mode) {
  try {
    if (mode === "system") localStorage.removeItem(APPEARANCE_KEY); else localStorage.setItem(APPEARANCE_KEY, mode);
  } catch { /* storage off: the choice lasts until reload */ }
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.dataset.theme = mode; else delete root.dataset.theme;
}

/* WindowSketch: a tiny Quiver window in light, dark, or both halves for
 * System. Fixed colours on purpose: it shows the mode, whatever is on now. */
function sketch(mode) {
  const win = (dark) => {
    const wash = dark ? "#141414" : "#fcfcfc", panel = dark ? "#252525" : "#fefefe", line = dark ? "#3a3a3a" : "#e3e3e3";
    return `<rect width="200" height="110" fill="${wash}"/>
      <rect x="10" y="10" width="30" height="4" rx="2" fill="#10b981"/>
      ${[0, 1, 2].map((i) => `<rect x="10" y="${20 + i * 10}" width="38" height="4" rx="2" fill="${line}"/>`).join("")}
      <rect x="64" y="0" width="136" height="110" fill="${panel}"/>
      <rect x="74" y="10" width="50" height="6" rx="3" fill="${line}"/>
      <rect x="74" y="23" width="116" height="4" rx="2" fill="${line}"/>
      <rect x="74" y="33" width="80" height="4" rx="2" fill="${line}"/>
      <rect x="74" y="84" width="116" height="16" rx="4" fill="${line}"/>`;
  };
  const both = mode === "system"
    ? `<clipPath id="setup-half"><rect x="90" width="110" height="110"/></clipPath><g clip-path="url(#setup-half)">${win(false)}</g>` : "";
  return `<svg class="setup-sketch" viewBox="0 0 200 110" preserveAspectRatio="none" aria-hidden="true">${win(mode !== "light")}${both}</svg>`;
}

function lookStep() {
  const current = readLook();
  return `<div class="setup-looks" role="radiogroup" aria-label="Appearance">${LOOKS.map(([id, title, blurb]) => {
    const on = id === current;
    return `<button class="setup-look${on ? " on" : ""}" role="radio" aria-checked="${on}" data-look="${id}">
      <span class="setup-sketch-wrap">${sketch(id)}${on ? CHECK : ""}</span>
      <span class="setup-look-label"><span class="strong">${esc(title)}</span><span class="caption">${esc(blurb)}</span></span>
    </button>`;
  }).join("")}</div>`;
}

/* --------------------------------------------------------------- events */

function bindWizard() {
  const $ = (id) => document.getElementById(id);
  $("setup-skip").onclick = () => finish();
  if ($("setup-back")) $("setup-back").onclick = () => { page.step -= 1; paint(); focusTitle(); };
  $("setup-next").onclick = async () => {
    const before = page.step;
    await next();
    if (page.step !== before) focusTitle();
  };
  if ($("setup-more")) $("setup-more").onclick = () => { page.showingMore = !page.showingMore; paint(); $("setup-more").focus(); };
  if ($("setup-hub-url")) {
    $("setup-hub-url").oninput = (e) => {
      page.hubUrl = e.target.value;
      page.hubError = null;
      $("setup-hub-clone").disabled = !page.hubUrl.trim();
    };
  }
  if ($("setup-hub-form")) $("setup-hub-form").onsubmit = (e) => { e.preventDefault(); cloneHub(); };
  if ($("setup-start-from")) $("setup-start-from").onchange = (e) => { page.start = e.target.value; paint(); $("setup-start-from").focus(); };
  bindRetry();
  for (const card of document.querySelectorAll("[data-look]")) {
    card.onclick = () => {
      setLook(card.dataset.look);
      paint();
      const again = document.querySelector(`[data-look="${card.dataset.look}"]`);
      if (again) again.focus();
    };
  }
  for (const tile of document.querySelectorAll(".setup-tile")) {
    tile.onclick = () => {
      const id = tile.dataset.agent;
      if (page.selected.has(id)) page.selected.delete(id); else page.selected.add(id);
      paint();
      const again = document.querySelector(`.setup-tile[data-agent="${CSS.escape(id)}"]`);
      if (again) again.focus();
    };
  }
  for (const btn of document.querySelectorAll("[data-install]")) {
    btn.onclick = () => {
      const id = btn.dataset.install;
      page.installing = page.installing === id ? null : id;
      paint();
      const again = document.querySelector(`[data-install="${CSS.escape(id)}"]`);
      if (again) again.focus();
    };
  }
  for (const btn of document.querySelectorAll("[data-run-install]")) {
    btn.onclick = () => runInstall(btn.dataset.agent, btn.dataset.runInstall);
  }
  for (const btn of document.querySelectorAll("[data-copy]")) {
    Fmt.copyTick(btn, () => {
      const p = page.data.agents.providers.find((x) => x.id === btn.dataset.agent);
      const m = p && (p.install || [])[Number(btn.dataset.copy)];
      return m ? m.command : "";
    }, () => {
      // A failed copy (plain http has no clipboard API) selects the
      // command to grab by hand, as the session sheet's byHand does.
      btn.title = "Couldn't copy — select it by hand";
      const el = btn.parentElement && btn.parentElement.querySelector("code");
      if (el) window.getSelection().selectAllChildren(el);
    });
  }
}

registerPage("setup", render);
