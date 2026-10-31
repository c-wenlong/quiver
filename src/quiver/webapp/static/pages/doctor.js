/* Doctor: a port of the Mac app's Settings › Doctor (DoctorSection and
 * DoctorModel). Not in the sidebar, as in the app; Settings links to
 * #/doctor. The swe doctor report from GET /v1/app/doctor, each finding
 * with its fix command, and Run where swe can make the fix itself
 * (the finding's action): POST /v1/app/doctor/run names the finding, the
 * hub runs the fix it lists for it. Run asks first, inline, since setup
 * and init link files into the harnesses' own folders. When one leaves a
 * real file in the way (the reply's blocked), a card offers Replace and
 * back up: POST /v1/app/doctor/replace, swe init --force, after a confirm.
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
};
const DOCTOR_FOLD = 8;

// What each fix does, said before it runs.
const DOCTOR_RUNS = {
  setup: (m) => `This runs swe setup --apply on ${m}: it registers the harnesses it finds, imports their MCP servers into ~/.quiver/mcp.json, and links the shared AGENTS.md and skills into each harness. Real files in the way are left alone, and your stars stay as they are.`,
  init: (m) => `This runs swe init on ${m}: it links the shared AGENTS.md and skills into each harness that doesn't read them yet. Real files in the way are left alone, and new skills folders wait for a run in a terminal.`,
  discover: (m) => `This runs swe harness discover --apply on ${m}: it adds the high-confidence matches it finds to harness.json.`,
  install: (m, f) => `This runs ${f.fix} on ${m}: it installs ${Fmt.harnessName(f.harness)} again with ${f.via}, which puts it on PATH, and registers it. A cask or install script that asks for a password can't finish here; copy the command into a terminal instead.`,
};

const DOCTOR_ICONS = {
  seal: '<path d="m12 2.5 2.2 1.6 2.7-.1.8 2.6 2.2 1.6-.9 2.6.9 2.6-2.2 1.6-.8 2.6-2.7-.1L12 21.5l-2.2-1.6-2.7.1-.8-2.6-2.2-1.6.9-2.6-.9-2.6 2.2-1.6.8-2.6 2.7.1z"/><path d="m8.8 12 2.2 2.2 4.2-4.4"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
};
const doctorIcon = (name) =>
  `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${DOCTOR_ICONS[name]}</svg>`;

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
    body = `${doctorLostError()}${doctorBlocked()}<div class="card doctor-state doctor-clear">
      <span class="doctor-seal">${doctorIcon("seal")}</span><p class="empty">Everything checks out.</p>
      <button class="doctor-button" data-doctor="reload">Check again</button></div>`;
  } else {
    body = `${doctorLostError()}${doctorBlocked()}${doctorFindings(p.data.findings)}`;
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
 * it copies each path to ~/.quiver/backups before replacing it. */
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
      <p class="caption">This runs swe init --force on ${esc(machine)}: each of these is copied to ~/.quiver/backups, then replaced with a link to the shared copy.</p>
      <div class="doctor-confirm-buttons">
        <button class="doctor-button" data-doctor="replace-cancel">Cancel</button>
        <button class="doctor-button primary danger" data-doctor="replace-go">Replace and back up</button>
      </div></div>` : "";
  return `<div class="card doctor-blocked">
    ${blocked.length ? `<p class="doctor-blocked-text">A real file or folder is in the way, so swe init left ${blocked.length === 1 ? "it" : "them"} alone: ${list}.</p>` : ""}
    ${button ? `<div>${button}</div>` : ""}${confirm}
    ${p.replaceError ? `<p class="error" role="alert">${esc(p.replaceError)}</p>` : ""}</div>`;
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
      <button class="icon-button" data-doctor="reload" aria-label="Refresh" title="Run the checks again">${icon("rescan")}</button></div>
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
  let fix = "";
  if (f.fix) {
    const running = p.running === id;
    const run = !f.action ? ""
      : running ? `<span class="doctor-running" role="status"><span class="doctor-spinner" aria-hidden="true"></span>Running…</span>`
      : `<button class="doctor-button primary" data-doctor="run" aria-expanded="${p.confirming === id}"${p.running ? " disabled" : ""}>Run</button>`;
    fix = `<div class="doctor-fix"><code class="doctor-command">${esc(f.fix)}</code>${run}
      <button class="icon-button doctor-copy" data-doctor="copy" aria-label="Copy the command" title="Copy the command">${doctorIcon("copy")}</button></div>`;
    if (p.confirming === id && !running && DOCTOR_RUNS[f.action]) fix += doctorConfirm(f);
    if (p.runErrors[id]) fix += `<p class="error doctor-run-error doctor-pre" role="alert">${esc(p.runErrors[id])}</p>`;
  }
  return `<div class="doctor-row" data-index="${index}">
    <span class="doctor-dot ${sev}" aria-label="${esc(f.severity)}"></span>
    <div class="doctor-main">
      <div class="doctor-message">${logo}<span>${esc(f.message)}</span></div>
      <code class="doctor-check">${esc(f.check)}</code>
      ${fix}
    </div></div>`;
}

function doctorConfirm(f) {
  const machine = (doctorPage.data && doctorPage.data.machine) || "the hub";
  return `<div class="doctor-confirm" role="group" aria-label="Run ${esc(f.fix)}?">
    <p class="caption">${esc(DOCTOR_RUNS[f.action](machine, f))}</p>
    <div class="doctor-confirm-buttons">
      <button class="doctor-button" data-doctor="cancel">Cancel</button>
      <button class="doctor-button primary" data-doctor="go">Run ${esc(f.fix)}</button>
    </div></div>`;
}

function bindDoctor() {
  const p = doctorPage;
  for (const btn of document.querySelectorAll("[data-doctor]")) {
    const row = btn.closest(".doctor-row");
    const finding = row && p.data ? doctorVisible()[Number(row.dataset.index)] : null;
    /* The copy button's tick feedback comes from Fmt.copyTick; on a copy
     * that can't land, select the command for a manual copy. */
    if (btn.dataset.doctor === "copy" && finding) {
      Fmt.copyTick(btn, () => finding.fix, () => {
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
      else if (act === "fold") { p.showsAll = !p.showsAll; paintDoctor(); }
      else if (act === "run" && finding) {
        p.confirming = p.confirming === doctorId(finding) ? null : doctorId(finding);
        paintDoctor();
        const go = document.querySelector('[data-doctor="go"]');
        if (go) go.focus();
      } else if (act === "cancel" && finding) {
        p.confirming = null;
        paintDoctor();
        doctorFocus(finding, "run");
      } else if (act === "go" && finding) doctorRun(finding);
    };
  }
  const confirm = document.querySelector(".doctor-row .doctor-confirm");
  if (confirm) {
    confirm.onkeydown = (event) => {
      if (event.key !== "Escape") return;
      const row = confirm.closest(".doctor-row");
      const finding = doctorVisible()[Number(row.dataset.index)];
      p.confirming = null;
      paintDoctor();
      doctorFocus(finding, "run");
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

function doctorFocus(finding, act) {
  const at = doctorVisible().findIndex((f) => doctorId(f) === doctorId(finding));
  const el = at >= 0 && document.querySelector(`.doctor-row[data-index="${at}"] [data-doctor="${act}"]`);
  if (el && !el.disabled) el.focus();
}

/* DoctorModel.run: one at a time; the report is read again after, from
 * the hub's answer, whether the fix worked or not. */
async function doctorRun(finding) {
  const p = doctorPage, id = doctorId(finding);
  if (p.running) return;
  p.running = id;
  p.confirming = null;
  delete p.runErrors[id];
  p.lostError = null;
  if (onDoctorPage()) paintDoctor();
  const { status, data } = await apiPost("/v1/app/doctor/run", { check: finding.check, message: finding.message });
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
  doctorFocus(finding, "run");
}

/* Replace and back up: swe init --force on the hub. The answer carries the
 * report and whatever is still in the way. */
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

function onDoctorPage() {
  return currentRoute() === "doctor";
}

registerPage("doctor", renderDoctor);
