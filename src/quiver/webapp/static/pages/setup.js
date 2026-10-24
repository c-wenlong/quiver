/* First-run setup: a port of the Mac app's FirstRunSetupSheet (and
 * QuiverCore's FirstRunSetupModel) for the machine the hub runs on, at
 * #/setup. Not in the sidebar. The page opens on a "Set up this machine"
 * card; its steps are the sheet's first three: the agents you use, one
 * AGENTS.md, skills folder and MCP list for all of them, and the look
 * (kept per browser, under the key Settings › General uses). The
 * notifications step is the Mac's own and is left out.
 * Data: GET /v1/app/setup (swe setup --json in process); the one write
 * is POST /v1/app/setup/apply (swe setup --apply --json). */
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
  data: null, loadError: null, loading: false,
  wizard: false, step: 0, selected: new Set(), start: "seed",
  applying: false, outcome: null, applyError: null, appliedSelection: null, adopted: false,
  showingMore: false, installing: null, copied: null, finished: false,
};

/* HarnessMark.accountName: a Claude account is "Claude". */
function agentName(id) {
  return id === "claude" ? "Claude" : Fmt.harnessName(id);
}

function tilde(path) {
  const home = page.data && page.data.home;
  return home && path.startsWith(home + "/") ? "~" + path.slice(home.length) : path;
}

function machineName() {
  return (page.data && page.data.machine) || "this machine";
}

/* Logos shipped in /app/ that Fmt.logo doesn't list yet. */
const MORE_LOGOS = {
  copilot: "harness-copilot.svg", grok: "harness-grok.svg", kimi: "harness-kimi.svg",
  "mistral-vibe": "harness-mistral-vibe-color.svg", "qwen-code": "harness-qwen-code-color.svg",
};

function agentLogo(id) {
  const img = Fmt.logo(id, "setup-logo");
  if (img.startsWith("<img")) return img;
  const file = MORE_LOGOS[id];
  if (file) {
    const mono = file.endsWith("-color.svg") ? "" : " mono";
    return `<img class="setup-logo${mono}" src="/app/${file}" alt="">`;
  }
  return `<span class="setup-logo setup-monogram" aria-hidden="true">${esc(agentName(id).slice(0, 1))}</span>`;
}

const CHECK = `<svg class="setup-check" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="currentColor"/>
  <path d="m7.5 12.3 3 3 6-6.3" fill="none" stroke="var(--card)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

/* ------------------------------------------------------------------ data */

async function load(gen) {
  page.loading = true;
  page.loadError = null;
  const { status, data } = await api("/v1/app/setup");
  if (gen !== routeGen) return false;
  page.loading = false;
  if (status === 401) {
    renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    return false;
  }
  if (status !== 200 || !data) {
    page.loadError = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");
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
    installing: null, finished: false,
  });
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
    page.applyError = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");
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
  return (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "setup";
}

/* --------------------------------------------------------------- render */

async function render() {
  const gen = routeGen;
  if (!page.data) {
    paintShell(`<div class="card"><p class="empty">Looking for coding agents on this machine…</p></div>`);
    if (!(await load(gen))) return;
  }
  paint();
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
    if (page.applying) return;
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
  return page.applying || (STEPS[page.step] === "links" && !!previewError());
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
    panel = methods.length
      ? `<p class="caption">Install commands for each package manager. Run one in a terminal on ${esc(machineName())}.</p>
        <div class="setup-methods">${methods.map((m, i) => `<div class="setup-method${m.available ? "" : " off"}">
          <span class="setup-via">${esc(m.via)}</span>
          <code title="${esc(m.command)}">${esc(m.command)}</code>
          <button class="setup-button small" data-copy="${i}" data-agent="${esc(p.id)}">${
            page.copied === `${p.id}:${i}` ? "Copied" : "Copy"}</button></div>`).join("")}</div>`
      : `<p class="caption">swe doesn't know an installer for ${esc(agentName(p.id))}. Get it from its own website.</p>`;
  }
  return `<div class="setup-missing">
    <div class="setup-missing-row">
      <p class="setup-text"><span class="strong">${esc(agentName(p.id))}</span> isn't installed yet. Quiver will list it anyway, and you can install it any time.</p>
      <button class="setup-button" data-install="${esc(p.id)}" aria-expanded="${open}"
        title="Show the install commands for ${esc(agentName(p.id))}">Install…</button>
    </div>${panel}</div>`;
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
    const wash = dark ? "#171717" : "#f5f5f5", panel = dark ? "#212121" : "#ffffff", line = dark ? "#424242" : "#dbdbdb";
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
  for (const btn of document.querySelectorAll("[data-copy]")) {
    btn.onclick = async () => {
      const p = page.data.agents.providers.find((x) => x.id === btn.dataset.agent);
      const m = p && (p.install || [])[Number(btn.dataset.copy)];
      if (!m) return;
      try { await navigator.clipboard.writeText(m.command); } catch { return; }
      page.copied = `${p.id}:${btn.dataset.copy}`;
      paint();
    };
  }
}

registerPage("setup", render);
