/* Doctor: a port of the Mac app's Settings › Doctor (DoctorSection and
 * DoctorModel). Not in the sidebar, as in the app; Settings links to
 * #/doctor. The swe doctor report from GET /v1/app/doctor, each finding
 * with its fix command, and Run where swe can make the fix itself
 * (the finding's action): POST /v1/app/doctor/run names the finding, the
 * hub runs the fix it lists for it. Run asks first, inline, since setup
 * and init link files into the harnesses' own folders. When one leaves a
 * real file in the way (the reply's blocked), a card offers Replace and
 * back up: POST /v1/app/doctor/replace, swe init --force, after a confirm.
 *
 * Fix all (POST /v1/app/doctor/fix-all, after an inline confirm) runs
 * swe doctor --fix's ordered batch on the hub: setup, discover, init and
 * plugin installs. Installs and fixes that delete or overwrite keep
 * their own row's Run. Its summary card says what it fixed and what is
 * left.
 *
 * A finding can carry ``source`` (the file it comes from) and
 * ``remedies``: action rows run like the finding's own Run, agent rows
 * open a picker that launches a session through POST /v1/sessions with
 * the remedy's prompt, command rows copy, steps rows only describe.
 */
"use strict";

const doctorPage = {
  state: "idle", data: null, error: null, gen: 0, showsAll: false,
  // The finding whose Run is in flight, the one asking to confirm, and the
  // last failure per finding (DoctorModel.running / runErrors).
  running: null, confirming: null, runErrors: {}, lostError: null,
  // Paths the last setup or init left alone, and Replace and back up's
  // own confirm and failure.
  blocked: [], confirmingReplace: false, replaceError: null,
  // The open Fix-with-agent picker {id, idx, harness, harnessName, phase,
  // error}, a launch being followed, and each row's last launch outcome.
  agent: null, agentBusy: false, agentNote: {},
  // Fix all's confirm, its last result and its failure.
  confirmingFixAll: false, fixAll: null, fixAllError: null,
};
const DOCTOR_FOLD = 8;

// What each fix does, said before it runs.
const DOCTOR_RUNS = {
  setup: (m) => `This runs swe setup --apply on ${m}: it registers the harnesses it finds, imports their MCP servers into ~/.swe/mcp.json, and links the shared AGENTS.md and skills into each harness. Real files in the way are left alone, and your stars stay as they are.`,
  init: (m) => `This runs swe init on ${m}: it links the shared AGENTS.md and skills into each harness that doesn't read them yet. Real files in the way are left alone, and new skills folders wait for a run in a terminal.`,
  discover: (m) => `This runs swe harness discover --apply on ${m}: it adds the high-confidence matches it finds to harness.json.`,
  install: (m, f, r) => `This runs ${(r && r.command) || f.fix} on ${m}: it installs ${Fmt.harnessName(f.harness)} again with ${(r && r.via) || f.via}, which puts it on PATH, and registers it. A cask or install script that asks for a password can't finish here; copy the command into a terminal instead.`,
  undeclare: (m, f, r) => `On ${m}: deletes the hook entries naming ${(r && r.script) || "the missing script"} from the settings files in the source line, after backing each one up to ~/.swe/backups. Nothing else in the files changes.`,
  "plugin-fix": (m, f, r) => `On ${m}: runs ${(r && r.detail) || f.fix}, which registers the marketplace folder or installs the plugin from it. Nothing is removed.`,
  "plugin-reinstall": (m, f, r) => `On ${m}: runs ${(r && r.detail) || f.fix}: Codex's copy of the plugin is removed, then installed again from your folder in ~/.swe/plugins.`,
  "dangling-link": (m, f, r) => `On ${m}: copies the broken link ${(r && r.path) || ""} to ~/.swe/backups, then removes it. A link synced from another machine may still work there.`,
  "mcp-sync": (m, f, r) => `On ${m}: ${(r && r.command) || f.fix} replaces this one server entry with the hub's copy from ~/.swe/mcp.json, after backing the config file up to ~/.swe/backups.`,
  "marketplace-drop": (m, f, r) => `On ${m}: deletes the missing marketplace entries from the settings files, after backing each one up to ~/.swe/backups. The folders stay gone; restore them by copying them over.`,
};

const DOCTOR_ICONS = {
  seal: '<path d="m12 2.5 2.2 1.6 2.7-.1.8 2.6 2.2 1.6-.9 2.6.9 2.6-2.2 1.6-.8 2.6-2.7-.1L12 21.5l-2.2-1.6-2.7.1-.8-2.6-2.2-1.6.9-2.6-.9-2.6 2.2-1.6.8-2.6 2.7.1z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  terminal: '<path d="M4 17l6-5-6-5"/><path d="M12 19h8"/>',
};
const doctorIcon = (name) =>
  `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${DOCTOR_ICONS[name]}</svg>`;
const doctorSourceIcon = (source) =>
  doctorIcon(source && source.startsWith("PATH") ? "terminal" : "file");

/* DoctorReport.Finding.id, with a separator: check "a" with message
 * "bc" and check "ab" with message "c" are two findings, not one. */
const doctorId = (f) => `${f.check}\u0000${f.message}`;

async function renderDoctor() {
  const p = doctorPage;
  const gen = routeGen, mine = ++p.gen;
  p.state = "loading";
  paintDoctor();
  const { status, data } = await api("/v1/app/doctor");
  if (gen !== routeGen || mine !== p.gen) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) {
    p.state = "failed";
    p.error = failText(status, data);
  } else {
    p.state = "loaded";
    p.data = data;
  }
  paintDoctor();
}

function doctorHeader() {
  return pageHeader("Doctor", "Checks on PATH, Node and drift between quiver and your harnesses.",
    `<a class="doctor-back caption" href="#/settings">Settings</a>`);
}

function paintDoctor() {
  const p = doctorPage;
  let body;
  if (p.state === "failed") {
    body = `<div class="card doctor-state"><p class="error" role="alert">${esc(p.error)}</p>
      <div><button class="doctor-button" data-doctor="reload">Try again</button></div></div>`;
  } else if (p.state !== "loaded" || !p.data) {
    body = doctorSkeleton();
  } else if (!p.data.findings.length) {
    body = `${doctorLostError()}${doctorFixAllCard()}${doctorBlocked()}<div class="card doctor-state doctor-clear">
      <span class="doctor-seal">${doctorIcon("seal")}</span><p class="empty">Everything checks out.</p>
      <button class="doctor-button" data-doctor="reload">Check again</button></div>`;
  } else {
    body = `${doctorLostError()}${doctorFixAllCard()}${doctorBlocked()}${doctorFindings(p.data.findings)}`;
  }
  document.getElementById("panel").innerHTML = `<div class="page">${doctorHeader()}<div class="stack">${body}</div></div>`;
  bindDoctor();
}

/* A Run whose finding is gone from the new report has no row left to
 * show its error under. */
function doctorLostError() {
  const e = doctorPage.lostError;
  return e ? `<div class="card doctor-lost"><p class="error" role="alert">${esc(e)}</p></div>` : "";
}

/* What a setup or init left alone, with swe init --force behind a confirm:
 * it copies each path to ~/.swe/backups before replacing it. */
function doctorBlocked() {
  const p = doctorPage, blocked = p.blocked;
  const replacing = p.running === "replace";
  if (!blocked.length && !replacing && !p.replaceError) return "";
  const machine = (p.data && p.data.machine) || "the hub";
  const list = blocked.map((path) => `<code>${esc(path)}</code>`).join(", ");
  const button = replacing
    ? `<span class="doctor-running" role="status"><span class="doctor-spinner" aria-hidden="true"></span>Replacing…</span>`
    : blocked.length ? `<button class="doctor-button" data-doctor="replace-ask" aria-expanded="${p.confirmingReplace}"${p.running ? " disabled" : ""}>Replace and back up…</button>` : "";
  const confirm = p.confirmingReplace && !replacing ? `<div class="doctor-confirm" role="group" aria-label="Run swe init --force?">
      <p class="caption">This runs swe init --force on ${esc(machine)}: each of these is copied to ~/.swe/backups, then replaced with a link to the shared copy.</p>
      <div class="doctor-confirm-buttons">
        <button class="doctor-button" data-doctor="replace-cancel">Cancel</button>
        <button class="doctor-button primary danger" data-doctor="replace-go">Replace and back up</button>
      </div></div>` : "";
  return `<div class="card doctor-blocked">
    ${blocked.length ? `<p class="doctor-blocked-text">A real file or folder is in the way, so swe init left ${blocked.length === 1 ? "it" : "them"} alone: ${list}.</p>` : ""}
    ${button ? `<div>${button}</div>` : ""}${confirm}
    ${p.replaceError ? `<p class="error" role="alert">${esc(p.replaceError)}</p>` : ""}</div>`;
}

/* Fix all's confirm, spinner and summary: what ran, what it fixed, what
 * is left for a person. */
function doctorFixAllCard() {
  const p = doctorPage;
  const machine = (p.data && p.data.machine) || "the hub";
  if (p.running === "fix-all") {
    return `<div class="card doctor-fixall"><span class="doctor-running" role="status"><span class="doctor-spinner" aria-hidden="true"></span>Fixing…</span></div>`;
  }
  if (p.confirmingFixAll) {
    const n = (p.data && p.data.fixable) || 0;
    return `<div class="card doctor-fixall"><div class="doctor-confirm" role="group" aria-label="Fix all?">
      <p class="caption">This runs swe doctor --fix on ${esc(machine)}: the ${n === 1 ? "local fix" : `${n} local fixes`} in order (setup, discover, init, plugin installs), checking again after each. Real files in the way are left alone. Installs, and fixes that delete or overwrite, wait for their own Run.</p>
      <div class="doctor-confirm-buttons">
        <button class="doctor-button" data-doctor="fixall-cancel">Cancel</button>
        <button class="doctor-button primary" data-doctor="fixall-go">Fix all</button>
      </div></div></div>`;
  }
  if (p.fixAllError) {
    return `<div class="card doctor-fixall"><p class="error" role="alert">${esc(p.fixAllError)}</p></div>`;
  }
  const r = p.fixAll;
  if (!r) return "";
  const failed = r.ran.filter((s) => s.error);
  // Everything still in the report counts, manual findings included.
  const left = (r.still_failing || []).length;
  const line = r.ran.length
    ? `Fixed ${r.fixed.length} finding${r.fixed.length === 1 ? "" : "s"}${failed.length ? `, ${failed.length} fix${failed.length === 1 ? "" : "es"} failed` : ""}${left ? `; ${left} left for you below` : ""}.`
    : "Nothing to fix here: what is left needs you.";
  const errors = failed.map((s) => `<p class="error" role="alert"><code>${esc(s.fix || s.action)}</code>: ${esc(s.error)}</p>`).join("");
  return `<div class="card doctor-fixall"><div class="doctor-card-head"><span class="caption" role="status">${esc(line)}</span>
    <button class="doctor-button" data-doctor="fixall-dismiss">Dismiss</button></div>${errors}</div>`;
}

function doctorFindings(findings) {
  const p = doctorPage;
  const hidden = findings.length > DOCTOR_FOLD ? findings.length - DOCTOR_FOLD : 0;
  const shown = hidden && !p.showsAll ? findings.slice(0, DOCTOR_FOLD) : findings;
  const fold = hidden ? `<button class="fold doctor-fold" data-doctor="fold"
      title="${p.showsAll ? `Show only the first ${DOCTOR_FOLD}` : "Show every finding"}">${
      p.showsAll ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</button>` : "";
  return `<div class="card doctor-card">
    <div class="doctor-card-head"><span class="card-title">${findings.length === 1 ? "1 finding" : `${findings.length} findings`}</span>
      <span class="doctor-head-actions">${(p.data && p.data.fixable) ? `<button class="doctor-button" data-doctor="fixall-ask" aria-expanded="${p.confirmingFixAll}"${p.running ? " disabled" : ""} title="Run every safe local fix, in order">Fix all (${p.data.fixable})</button>` : ""}
      <button class="icon-button" data-doctor="reload" aria-label="Refresh" title="Run the checks again">${icon("rescan")}</button></span></div>
    <div class="doctor-rows">${shown.map(doctorRow).join("")}</div>${fold}
  </div>`;
}

/* The findings card's own rows: a severity dot, the message with a
 * harness logo, the check name, and the fix command strip. */
function doctorSkeleton() {
  const row = (i) => `<div class="doctor-row">
    <span class="doctor-dot" style="background:var(--border-strong)"></span>
    <div class="doctor-main" style="flex:1;min-width:0">
      <div class="doctor-message">${i % 2 ? "" : `<span class="doctor-logo">${Sk.bar(16, 16)}</span>`}${Sk.bar(`${58 - (i % 3) * 10}%`, 13)}</div>
      ${Sk.bar(`${44 - (i % 2) * 8}%`, 11)}
      ${i % 3 === 2 ? "" : `<div class="doctor-fix">${Sk.bar(`${46}%`, 18)}${Sk.bar(30, 30, "", "border-radius:8px;flex:none")}</div>`}
    </div></div>`;
  return `<div class="card doctor-card" role="status" aria-label="Loading">
    <div class="doctor-card-head"><span class="card-title">${Sk.bar(80, 14)}</span></div>
    <div class="doctor-rows">${Array.from({ length: 4 }, (_, i) => row(i)).join("")}</div></div>`;
}

function doctorRow(f, index) {
  const p = doctorPage, id = doctorId(f);
  const sev = { error: "error", warning: "warning" }[f.severity] || "info";
  // A finding about one harness leads with its logo, when there is one.
  const mark = f.harness ? Fmt.logo(f.harness) : "";
  const logo = mark.startsWith("<img") ? `<span class="doctor-logo" title="${esc(Fmt.harnessName(f.harness))}">${mark}</span>` : "";
  const source = f.source
    ? `<div class="doctor-source">${doctorSourceIcon(f.source)}<span>${esc(f.source)}</span></div>` : "";
  const fix = (f.remedies || []).length ? doctorRemedies(f, id) : doctorLegacyFix(f, id);
  return `<div class="doctor-row" data-index="${index}">
    <span class="doctor-dot ${sev}" aria-label="${esc(f.severity)}"></span>
    <div class="doctor-main">
      <div class="doctor-message">${logo}<span>${esc(f.message)}</span></div>
      <code class="doctor-check">${esc(f.check)}</code>
      ${source}${fix}
    </div></div>`;
}

/* The one-strip fix findings without remedies keep: the command (or
 * prose), the finding's own Run, and copy. */
function doctorLegacyFix(f, id) {
  const p = doctorPage;
  if (!f.fix) return "";
  const running = p.running === id;
  const run = !f.action ? ""
    : running ? `<span class="doctor-running" role="status"><span class="doctor-spinner" aria-hidden="true"></span>Running…</span>`
    : `<button class="doctor-button primary" data-doctor="run" aria-expanded="${p.confirming === id}"${p.running ? " disabled" : ""}>Run</button>`;
  let fix = `<div class="doctor-fix"><code class="doctor-command">${esc(f.fix)}</code>${run}
    <button class="icon-button doctor-copy" data-doctor="copy" aria-label="Copy the command" title="Copy the command">${doctorIcon("copy")}</button></div>`;
  if (p.confirming === id && !running && DOCTOR_RUNS[f.action]) fix += doctorConfirm(f, f.action);
  if (p.runErrors[id]) fix += `<p class="error doctor-run-error doctor-pre" role="alert">${esc(p.runErrors[id])}</p>`;
  return fix;
}

/* A confirming key joins the finding id and the remedy index with a
 * separator neither check nor message can hold. remedyIdx reads the
 * index back: undefined for a legacy finding's own Run. */
const remedyKey = (id, i) => `${id}\u0001${i}`;
const remedyIdx = (key) =>
  key && key.includes("\u0001") ? Number(key.split("\u0001")[1]) : undefined;

/* The remedies box: one row per way out, best first. */
function doctorRemedies(f, id) {
  const p = doctorPage;
  const rows = f.remedies.map((r, i) => doctorRemedy(f, id, r, i)).join("");
  let extra = "";
  if (p.confirming && p.confirming.startsWith(id + "\u0001")) {
    const idx = Number(p.confirming.split("\u0001")[1]);
    const r = f.remedies[idx];
    if (r && r.kind === "action" && DOCTOR_RUNS[r.action]) extra = doctorConfirm(f, r.action, r, idx);
  }
  const agent = doctorPage.agent;
  if (agent && agent.id === id) {
    const r = f.remedies[agent.idx];
    if (r && r.kind === "agent") extra += doctorAgentPanel(f, r);
  }
  if (doctorPage.agentNote[id]) {
    extra += `<p class="caption doctor-agent-note" role="status">${esc(doctorPage.agentNote[id])}</p>`;
  }
  if (doctorPage.runErrors[id]) {
    extra += `<p class="error doctor-run-error doctor-pre" role="alert">${esc(doctorPage.runErrors[id])}</p>`;
  }
  return `<div class="doctor-remedies">${rows}</div>${extra}`;
}

function doctorRemedy(f, id, r, i) {
  const p = doctorPage, key = remedyKey(id, i);
  let affordance = "";
  if (r.kind === "action") {
    // A copyable command beside Run keeps the terminal path the confirm
    // text offers when the runner needs a password.
    const copy = r.command
      ? `<button class="icon-button doctor-copy" data-doctor="copy" data-idx="${i}" aria-label="Copy the command" title="Copy the command">${doctorIcon("copy")}</button>` : "";
    affordance = p.running === key
      ? `<span class="doctor-running" role="status"><span class="doctor-spinner" aria-hidden="true"></span>Running…</span>`
      : `${copy}<button class="doctor-button primary" data-doctor="run" data-idx="${i}" aria-expanded="${p.confirming === key}"${p.running ? " disabled" : ""}>Run</button>`;
  } else if (r.kind === "agent") {
    affordance = `<button class="doctor-button" data-doctor="agent" data-idx="${i}" aria-expanded="${p.agent && p.agent.id === id && p.agent.idx === i}"${p.agentBusy ? " disabled" : ""}>${esc(r.title)}</button>`;
  } else if (r.kind === "command" && r.command) {
    affordance = `<button class="icon-button doctor-copy" data-doctor="copy" data-idx="${i}" aria-label="Copy the command" title="Copy the command">${doctorIcon("copy")}</button>`;
  }
  const detail = r.command
    ? `<code class="doctor-command">${esc(r.command)}</code>${r.detail ? `<div class="doctor-remedy-detail">${esc(r.detail)}</div>` : ""}`
    : r.detail ? `<div class="doctor-remedy-detail">${esc(r.detail)}</div>` : "";
  const steps = (r.steps || []).map((s) => `<div class="doctor-remedy-step">${esc(s)}</div>`).join("");
  return `<div class="doctor-remedy">
    <div class="doctor-remedy-text">
      <div class="doctor-remedy-title">${esc(r.title)}</div>
      ${detail}${steps}
    </div>${affordance}</div>`;
}

function doctorConfirm(f, action, r, idx) {
  const machine = (doctorPage.data && doctorPage.data.machine) || "the hub";
  const label = (r && r.title) || f.fix;
  return `<div class="doctor-confirm" role="group" aria-label="Run ${esc(label)}?">
    <p class="caption">${esc(DOCTOR_RUNS[action](machine, f, r))}</p>
    <div class="doctor-confirm-buttons">
      <button class="doctor-button" data-doctor="cancel">Cancel</button>
      <button class="doctor-button primary" data-doctor="go" data-action="${esc(action)}"${idx === undefined ? "" : ` data-idx="${idx}"`}>Run ${esc(label)}</button>
    </div></div>`;
}

/* The Fix-with-agent picker: prompt preview, harness chips from the
 * report's launchable list, Launch through POST /v1/sessions. */
function doctorAgentPanel(f, r) {
  const p = doctorPage, agent = p.agent;
  const machine = (p.data && p.data.machine) || "the hub";
  const launchable = (p.data && p.data.launchable) || [];
  const chips = launchable.map((h) =>
    `<button class="doctor-chip${agent.harness === h.name ? " selected" : ""}" data-doctor="agent-chip" data-harness="${esc(h.name)}"${agent.phase ? " disabled" : ""}>${Fmt.logo(h.name)}${esc(h.display_name || h.name)}</button>`).join("");
  const pick = launchable.length ? chips
    : `<span class="caption">No harness can start on ${esc(machine)}: install one there, or turn it on in Settings › Providers.</span>`;
  const phase = agent.phase
    ? `<span class="doctor-running" role="status"><span class="doctor-spinner" aria-hidden="true"></span>${esc(agent.phase)}</span>` : "";
  return `<div class="doctor-confirm" role="group" aria-label="Fix with agent">
    <p class="caption">Launch an agent on ${esc(machine)}. It gets this prompt:</p>
    <pre class="doctor-prompt">${esc(r.prompt || "")}</pre>
    <div class="doctor-chips">${pick}</div>
    ${agent.error ? `<p class="error" role="alert">${esc(agent.error)}</p>` : ""}
    <div class="doctor-confirm-buttons">${phase}
      <button class="doctor-button" data-doctor="agent-cancel"${agent.phase ? " disabled" : ""}>Cancel</button>
      <button class="doctor-button primary" data-doctor="agent-go"${agent.phase || !agent.harness || !r.prompt ? " disabled" : ""}>Launch ${esc(agent.harnessName || "")}</button>
    </div></div>`;
}

function bindDoctor() {
  const p = doctorPage;
  for (const btn of document.querySelectorAll("[data-doctor]")) {
    const row = btn.closest(".doctor-row");
    const finding = row && p.data ? doctorVisible()[Number(row.dataset.index)] : null;
    /* The copy button's tick feedback comes from Fmt.copyTick; on a copy
     * that can't land, select the command for a manual copy. A remedy's
     * copies its own command, a legacy finding's its fix line. */
    if (btn.dataset.doctor === "copy" && finding) {
      const idx = btn.dataset.idx;
      const text = () => idx === undefined
        ? finding.fix
        : ((finding.remedies || [])[Number(idx)] || {}).command;
      Fmt.copyTick(btn, text, () => {
        const code = btn.parentElement.querySelector(".doctor-command");
        if (code) window.getSelection().selectAllChildren(code);
      });
    }
    btn.onclick = () => {
      const act = btn.dataset.doctor;
      if (act === "reload") { p.lostError = null; p.blocked = []; p.replaceError = null; renderDoctor(); }
      else if (act === "replace-ask" || act === "replace-cancel") {
        p.confirmingReplace = act === "replace-ask" ? !p.confirmingReplace : false;
        paintDoctor();
        const next = document.querySelector(`[data-doctor="${p.confirmingReplace ? "replace-go" : "replace-ask"}"]`);
        if (next) next.focus();
      } else if (act === "replace-go") doctorReplace();
      else if (act === "fixall-ask" || act === "fixall-cancel") {
        p.confirmingFixAll = act === "fixall-ask" ? !p.confirmingFixAll : false;
        paintDoctor();
        const next = document.querySelector(`[data-doctor="${p.confirmingFixAll ? "fixall-go" : "fixall-ask"}"]`);
        if (next) next.focus();
      } else if (act === "fixall-go") doctorFixAll();
      else if (act === "fixall-dismiss") { p.fixAll = null; p.fixAllError = null; paintDoctor(); }
      else if (act === "fold") { p.showsAll = !p.showsAll; paintDoctor(); }
      else if (act === "run" && finding) {
        const idx = btn.dataset.idx;
        const key = idx === undefined
          ? doctorId(finding) : remedyKey(doctorId(finding), Number(idx));
        p.confirming = p.confirming === key ? null : key;
        paintDoctor();
        const go = document.querySelector('[data-doctor="go"]');
        if (go) go.focus();
      } else if (act === "cancel" && finding) {
        const idx = remedyIdx(p.confirming);
        p.confirming = null;
        paintDoctor();
        doctorFocus(finding, "run", idx);
      } else if (act === "go" && finding) {
        doctorRun(finding, btn.dataset.action, btn.dataset.idx);
      } else if (act === "agent" && finding) {
        const idx = Number(btn.dataset.idx);
        const id = doctorId(finding);
        if (p.agent && p.agent.id === id && p.agent.idx === idx) {
          p.agent = null;
        } else {
          const first = ((p.data && p.data.launchable) || [])[0];
          p.agent = { id, idx,
                      harness: first ? first.name : "",
                      harnessName: first ? (first.display_name || first.name) : "" };
        }
        paintDoctor();
        doctorAgentFocus(finding, idx);
      } else if (act === "agent-chip" && p.agent) {
        p.agent.harness = btn.dataset.harness;
        p.agent.harnessName = btn.textContent.trim();
        paintDoctor();
        const go = document.querySelector('[data-doctor="agent-go"]');
        if (go) go.focus();
      } else if (act === "agent-cancel" && finding) {
        const idx = p.agent ? p.agent.idx : undefined;
        p.agent = null;
        paintDoctor();
        doctorFocus(finding, "agent", idx);
      } else if (act === "agent-go" && finding && p.agent) {
        doctorAgentLaunch(finding, p.agent.idx);
      }
    };
  }
  for (const confirm of document.querySelectorAll(".doctor-row .doctor-confirm")) {
    confirm.onkeydown = (event) => {
      if (event.key !== "Escape") return;
      const row = confirm.closest(".doctor-row");
      const finding = doctorVisible()[Number(row.dataset.index)];
      if (confirm.querySelector('[data-doctor="agent-go"]')) {
        // Escape leaves a launch in flight alone: it only drops the wait.
        if (p.agent && p.agent.phase) return;
        const idx = p.agent ? p.agent.idx : undefined;
        p.agent = null;
        paintDoctor();
        doctorFocus(finding, "agent", idx);
      } else {
        const idx = remedyIdx(p.confirming);
        p.confirming = null;
        paintDoctor();
        doctorFocus(finding, "run", idx);
      }
    };
  }
  const replace = document.querySelector(".doctor-blocked .doctor-confirm");
  if (replace) {
    replace.onkeydown = (event) => {
      if (event.key !== "Escape") return;
      p.confirmingReplace = false;
      paintDoctor();
      const ask = document.querySelector('[data-doctor="replace-ask"]');
      if (ask) ask.focus();
    };
  }
}

function doctorVisible() {
  const all = doctorPage.data ? doctorPage.data.findings : [];
  return all.length > DOCTOR_FOLD && !doctorPage.showsAll ? all.slice(0, DOCTOR_FOLD) : all;
}

function doctorFocus(finding, act, idx) {
  const at = doctorVisible().findIndex((f) => doctorId(f) === doctorId(finding));
  const pick = idx === undefined
    ? `[data-doctor="${act}"]` : `[data-doctor="${act}"][data-idx="${idx}"]`;
  const el = at >= 0 && document.querySelector(`.doctor-row[data-index="${at}"] ${pick}`);
  if (el && !el.disabled) el.focus();
}

/* Focus into a picker that just opened (its first chip), or back on the
 * row's Fix-with-agent button when it closed. */
function doctorAgentFocus(finding, idx) {
  const p = doctorPage;
  if (!p.agent) {
    doctorFocus(finding, "agent", idx);
    return;
  }
  const at = doctorVisible().findIndex((f) => doctorId(f) === doctorId(finding));
  const el = at >= 0 && document.querySelector(
    `.doctor-row[data-index="${at}"] [data-doctor="agent-chip"], ` +
    `.doctor-row[data-index="${at}"] [data-doctor="agent-cancel"]`);
  if (el && !el.disabled) el.focus();
}

/* DoctorModel.run: one at a time; the report is read again after, from
 * the hub's answer, whether the fix worked or not. A remedy's Run names
 * its own ``action`` so the hub runs that fix, not the finding's. */
async function doctorRun(finding, action, idx) {
  const p = doctorPage, id = doctorId(finding);
  if (p.running) return;
  p.running = idx === undefined ? id : remedyKey(id, Number(idx));
  p.confirming = null;
  delete p.runErrors[id];
  p.lostError = null;
  if (onDoctorPage()) paintDoctor();
  const body = { check: finding.check, message: finding.message };
  // Only a remedy's Run names an action: a finding's own Run leaves the
  // body's out, and the hub runs the finding's top-level action as ever.
  if (action && idx !== undefined) body.action = action;
  const { status, data } = await apiPost("/v1/app/doctor/run", body);
  p.running = null;
  if (status === 401) {
    if (onDoctorPage()) renderGate();
    return;
  }
  let error;
  if (status === 200 && data && data.report) {
    // A load still in flight read the report before this fix: drop it.
    p.gen++;
    p.data = data.report;
    p.state = "loaded";
    error = data.error;
    p.blocked = data.blocked || [];
    p.confirmingReplace = false;
    p.replaceError = null;
  } else {
    error = failText(status, data);
  }
  if (error) {
    const still = p.data && p.data.findings.some((f) => doctorId(f) === id);
    if (still) p.runErrors[id] = error;
    else p.lostError = `${finding.fix}: ${error}`;
  }
  if (!onDoctorPage()) return;
  paintDoctor();
  doctorFocus(finding, "run", idx === undefined ? undefined : Number(idx));
}

/* Fix with agent: POST /v1/sessions with the remedy's prompt, the way
 * the new-session sheet launches, then followLaunch watches it settle.
 * Closing the picker drops the wait, not the session: it keeps running
 * and shows up under Sessions. */
async function doctorAgentLaunch(finding, idx) {
  const p = doctorPage, id = doctorId(finding), agent = p.agent;
  const r = (finding.remedies || [])[idx];
  if (!agent || agent.id !== id || p.agentBusy || !r || !r.prompt || !agent.harness) return;
  const live = () => p.agent === agent;
  agent.phase = "Starting…";
  agent.error = null;
  p.agentBusy = true;
  if (onDoctorPage()) paintDoctor();
  const body = { harness: agent.harness, cwd: (p.data || {}).home || "", prompt: r.prompt };
  const pick = ((p.data || {}).launchable || []).find((h) => h.name === agent.harness);
  if (pick && pick.launch_yolo) body.yolo = true;
  const { status, data } = await apiPost("/v1/sessions", body);
  if (status === 401) {
    p.agent = null;
    p.agentBusy = false;
    if (onDoctorPage()) renderGate();
    return;
  }
  if (!live()) { p.agentBusy = false; return; }
  if (!((status === 200 || status === 202) && data && data.launch_id)) {
    agent.phase = null;
    agent.error = controlErrorText(status, data,
      "Couldn't reach the hub. Check the connection, then try again.");
    p.agentBusy = false;
    if (onDoctorPage()) paintDoctor();
    return;
  }
  agent.phase = "Waiting for the session…";
  if (onDoctorPage()) paintDoctor();
  const launch = await followLaunch(data, live);
  p.agentBusy = false;
  if (!launch) return;
  if (launch.state === "started") {
    p.agent = null;
    p.agentNote[id] = "The agent is on it: the session runs under Sessions, on the hub. Run the checks again when it finishes.";
  } else {
    const [message, failed] = launchNote(launch, data.queued === true);
    if (failed) {
      agent.phase = null;
      agent.error = message;
    } else {
      p.agent = null;
      p.agentNote[id] = message;
    }
  }
  if (!onDoctorPage()) return;
  paintDoctor();
}
async function doctorReplace() {
  const p = doctorPage;
  if (p.running) return;
  p.running = "replace";
  p.confirmingReplace = false;
  p.replaceError = null;
  if (onDoctorPage()) paintDoctor();
  const { status, data } = await apiPost("/v1/app/doctor/replace", {});
  p.running = null;
  if (status === 401) {
    if (onDoctorPage()) renderGate();
    return;
  }
  if (status === 200 && data && data.report) {
    p.gen++;
    p.data = data.report;
    p.state = "loaded";
    p.blocked = data.blocked || [];
    p.replaceError = data.error || null;
  } else {
    p.replaceError = failText(status, data);
  }
  if (!onDoctorPage()) return;
  paintDoctor();
  const ask = document.querySelector('[data-doctor="replace-ask"]');
  if (ask) ask.focus();
}

/* Fix all: swe doctor --fix on the hub, one at a time with the Runs;
 * the reply carries the report read again after it. */
async function doctorFixAll() {
  const p = doctorPage;
  if (p.running) return;
  p.running = "fix-all";
  p.confirmingFixAll = false;
  p.fixAll = null;
  p.fixAllError = null;
  p.lostError = null;
  if (onDoctorPage()) paintDoctor();
  const { status, data } = await apiPost("/v1/app/doctor/fix-all", {});
  p.running = null;
  if (status === 401) {
    if (onDoctorPage()) renderGate();
    return;
  }
  if (status === 200 && data && data.report) {
    p.gen++;
    p.data = data.report;
    p.state = "loaded";
    p.fixAll = data.result;
    p.blocked = data.blocked || [];
  } else {
    p.fixAllError = failText(status, data);
  }
  if (!onDoctorPage()) return;
  paintDoctor();
  const dismiss = document.querySelector('[data-doctor="fixall-dismiss"]');
  if (dismiss) dismiss.focus();
}

function onDoctorPage() {
  return currentRoute() === "doctor";
}

registerPage("doctor", renderDoctor);
