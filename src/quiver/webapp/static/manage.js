/* Harnesses and Accounts: ports of the Mac app's HarnessesPage and the
 * starred-harness cards on AccountsPage. Harnesses can star, unstar,
 * archive (with a reason) and restore, and has the app's three sheets: the
 * row editor, Detect and Add harness, all through POST
 * /v1/app/harness/<action>. Loaded before app.js.
 */
"use strict";

/* ---------------------------------------------------------------- Harnesses */

const harnessesPage = {
  data: null, gen: 0, scope: "in-use", query: "", expanded: false,
  // Writes: rows waiting on the hub, the row whose archive form is open
  // (and what is typed in it), and the last action's error.
  busy: new Set(), archiving: null, reason: "", actionError: null,
  // The open sheet: { kind: "edit" | "detect" | "add", ... } or null.
  sheet: null,
};
const REASON_MAX = 500;

async function renderHarnesses() {
  const panel = document.getElementById("panel");
  const gen = ++harnessesPage.gen;
  // Paint before awaiting, cached or loading, so the previous page's
  // controls never stay on screen while the request runs.
  if (!harnessesPage.data) {
    panel.innerHTML = `<div class="page">${harnessesHeader()}<div class="card"><p class="empty">Reading the registry…</p></div></div>`;
    bindRefresh(renderHarnesses, () => { harnessesPage.data = null; });
  } else {
    paintHarnesses();
  }
  const { status, data } = await api("/v1/app/harnesses");
  if (gen !== harnessesPage.gen) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) return pageError(harnessesHeader(), status, data, renderHarnesses);
  harnessesPage.data = data;
  paintHarnesses();
}

/* Detect and Add harness only once the list is painted: paintHarnesses
 * binds them, so in the loading and error states they would do nothing. */
function harnessesHeader(actions = false) {
  const where = harnessesPage.data && harnessesPage.data.machine ? ` on ${harnessesPage.data.machine}` : "";
  return pageHeader("Harnesses", `The AI coding CLIs swe knows about${where}. Star the ones you use; archive the ones you've ruled out.`,
    `${actions ? `<button class="harness-button" data-sheet="detect">Detect</button>
     <button class="harness-button primary harness-add" data-sheet="add">${harnessIcon("plus")}Add harness</button>` : ""}
     <button class="icon-button" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

/* Starred first, then the busiest, then by name. */
function harnessOrder(a, b) {
  const star = (h) => (h.state === "starred" ? 0 : 1);
  return star(a) - star(b) || (b.sessions_100d || 0) - (a.sessions_100d || 0) || a.name.localeCompare(b.name);
}

function harnessTitle(h) {
  return (h.display_name || "").trim() || Fmt.harnessName(h.name);
}

function paintHarnesses() {
  const p = harnessesPage, all = p.data.harnesses;
  const archived = all.filter((h) => h.state === "archived");
  const inUse = all.filter((h) => h.state !== "archived");
  let list = (p.scope === "archived" ? archived : inUse).slice().sort(harnessOrder);
  const q = p.query.trim().toLowerCase();
  if (q) {
    list = list.filter((h) => [h.name, harnessTitle(h), h.description, ...(h.tags || [])]
      .join(" ").toLowerCase().includes(q));
  }
  // In use, unsearched: starred rows show and the rest fold away.
  let hidden = 0;
  if (p.scope === "in-use" && !q && !p.expanded) {
    const starred = list.filter((h) => h.state === "starred");
    if (starred.length) { hidden = list.length - starred.length; list = starred; }
  }
  const count = list.length + hidden;
  let body;
  if (!all.length) {
    body = `<div class="harness-none"><p class="empty">No harnesses registered yet.</p>
      <button class="harness-button" data-sheet="detect" title="Scan the hub for harnesses to register">Detect</button></div>`;
  }
  else if (!list.length) {
    body = `<p class="empty">${q ? "No harnesses match." : p.scope === "archived" ? "Nothing archived." : "Every harness is archived."}</p>`;
  } else {
    body = list.map(harnessRow).join("");
    if (hidden || (p.expanded && p.scope === "in-use" && !q)) {
      body += `<button class="fold" id="harness-fold">${p.expanded
        ? "Hide the harnesses you haven't starred" : `Show ${hidden} more you haven't starred`}</button>`;
    }
  }
  document.getElementById("panel").innerHTML = `<div class="page">${harnessesHeader(true)}
    <div class="stack">
      <div class="filters">
        <select class="field" id="harness-scope" aria-label="Scope">${options(
          [["in-use", `In use (${inUse.length})`], ["archived", `Archived (${archived.length})`]], p.scope)}</select>
        <input class="field search" id="harness-search" type="search" placeholder="Filter harnesses"
          value="${esc(p.query)}" autocomplete="off" spellcheck="false">
        <span class="caption count">${count} harness${count === 1 ? "" : "es"}</span>
      </div>
      ${p.actionError ? `<div class="card harness-error"><p class="error" role="alert">${esc(p.actionError)}</p></div>` : ""}
      <div class="card list">${body}</div>
    </div></div>`;
  bindRefresh(renderHarnesses, () => { harnessesPage.data = null; });
  const scope = document.getElementById("harness-scope");
  scope.onchange = () => { p.scope = scope.value; paintHarnesses(); };
  const search = document.getElementById("harness-search");
  bindSearch(search, (text) => { p.query = text; }, () => {
    const at = search.selectionStart;
    paintHarnesses();
    const again = document.getElementById("harness-search");
    again.focus();
    again.setSelectionRange(at, at);
  });
  const fold = document.getElementById("harness-fold");
  if (fold) fold.onclick = () => { p.expanded = !p.expanded; paintHarnesses(); };
  bindHarnessActions();
  bindSheetButtons();
}

function archivedLine(a) {
  // An ISO string (naive local time) in the archive record; epoch seconds tolerated.
  const when = a.archived_at == null ? null
    : new Date(typeof a.archived_at === "number" ? a.archived_at * 1000 : a.archived_at);
  const date = when && !isNaN(when) ? ` ${when.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}` : "";
  const usage = a.usage && a.usage !== "unknown" ? `, usage: ${a.usage}` : "";
  const reason = (a.reason || "").trim() || "no reason given";
  return `Archived${date}${usage}. ${reason[0].toUpperCase()}${reason.slice(1)}`;
}

function harnessRow(h) {
  const aliases = (h.aliases || []).map((a) => `<span class="badge">${esc(a)}</span>`).join("");
  const busy = harnessesPage.busy.has(h.name);
  const installed = h.installed == null ? ""
    : `<span class="badge">${h.installed ? "Installed" : "Not on PATH"}</span>`;
  const sessions = h.sessions_100d == null ? ""
    : `<span class="caption">${h.sessions_100d} session${h.sessions_100d === 1 ? "" : "s"} in 100 days</span>`;
  return `<div class="list-row harness-row${busy ? " busy" : ""}" data-harness="${esc(h.name)}"
    title="Click to edit ${esc(harnessTitle(h))}"${busy ? ' aria-busy="true"' : ""}>
    <span class="session-logo">${Fmt.logo(h.name)}</span>
    <div class="list-main">
      <div class="list-title"><button type="button" class="name harness-open" data-act="edit"
        title="Click to edit ${esc(harnessTitle(h))}">${esc(harnessTitle(h))}</button>
        ${h.version ? `<code class="muted">${esc(h.version)}</code>` : ""}
        <code>${esc(h.command)}</code>${aliases}</div>
      ${h.description ? `<div class="caption">${esc(h.description)}</div>` : ""}
      ${h.archived ? `<div class="caption warn">${esc(archivedLine(h.archived))}</div>` : ""}
      ${(h.tags || []).length ? `<div class="caption muted">${h.tags.map(esc).join(" · ")}</div>` : ""}
    </div>
    <div class="list-side">${installed}${sessions}</div>
    ${harnessActions(h, busy)}
    ${harnessesPage.archiving === h.name && h.state !== "archived" ? archiveForm(h, busy) : ""}
  </div>`;
}

/* ------------------------------------------------------- Harnesses: writes */

const HARNESS_ICON = {
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
};

function harnessIcon(name, filled = false) {
  return `<svg viewBox="0 0 24 24" width="15" height="15" fill="${filled ? "currentColor" : "none"}" stroke="currentColor"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${HARNESS_ICON[name]}</svg>`;
}

/* Archived rows get Restore; the rest get Archive over Star, as in the app. */
function harnessActions(h, busy) {
  const off = busy ? " disabled" : "";
  const name = esc(harnessTitle(h));
  if (h.state === "archived") {
    return `<div class="harness-actions">
      <button class="harness-button" data-act="restore"${off}>Restore</button></div>`;
  }
  const starred = h.state === "starred";
  const open = harnessesPage.archiving === h.name;
  return `<div class="harness-actions">
    <button class="icon-button harness-icon${open ? " pressed" : ""}" data-act="archive-open" aria-expanded="${open}"
      aria-label="Archive ${name}" title="Archive: hide it from swe list and record why"${off}>${harnessIcon("archive")}</button>
    <button class="icon-button harness-icon${starred ? " starred" : ""}" data-act="${starred ? "unstar" : "star"}"
      aria-pressed="${starred}" aria-label="${starred ? "Unstar" : "Star"} ${name}"
      title="${starred ? "Unstar: stop pinning it to the top" : "Star: pin it to the top and track its limits"}"${off}>${harnessIcon("star", starred)}</button>
  </div>`;
}

/* The app's archive popover, inline under the row: a reason is required. */
function archiveForm(h, busy) {
  const p = harnessesPage;
  const empty = !p.reason.trim();
  return `<form class="harness-archive" novalidate>
    <div class="card-title">Archive ${esc(harnessTitle(h))}</div>
    <p class="caption">It drops out of swe list. Nothing is uninstalled, and the reason is kept so you remember why.</p>
    <input class="field" id="harness-reason" type="text" maxlength="${REASON_MAX}" autocomplete="off"
      placeholder="Why? Like: too slow, or no MCP support" aria-label="Why are you archiving it?"
      value="${esc(p.reason)}"${busy ? " disabled" : ""}>
    <div class="harness-form-buttons">
      <button type="button" class="harness-button" data-act="archive-cancel"${busy ? " disabled" : ""}>Cancel</button>
      <button type="submit" class="harness-button primary" id="harness-archive-go"${empty || busy ? " disabled" : ""}>${busy ? "Archiving…" : "Archive"}</button>
    </div>
  </form>`;
}

function bindHarnessActions() {
  const p = harnessesPage;
  for (const btn of document.querySelectorAll(".harness-row [data-act]")) {
    const name = btn.closest(".harness-row").dataset.harness;
    btn.onclick = (event) => {
      event.stopPropagation();
      const act = btn.dataset.act;
      if (act === "edit") {
        openHarnessSheet("edit", name);
      } else if (act === "archive-open") {
        p.archiving = p.archiving === name ? null : name;
        p.reason = "";
        paintHarnesses();
        const field = document.getElementById("harness-reason");
        if (field) field.focus();
      } else if (act === "archive-cancel") {
        closeArchiveForm();
      } else {
        harnessWrite(act, name);
      }
    };
  }
  // A click anywhere on a row but its buttons and form opens the editor.
  for (const row of document.querySelectorAll(".harness-row")) {
    row.onclick = (event) => {
      if (event.target.closest("button, input, form, a") || window.getSelection().toString()) return;
      openHarnessSheet("edit", row.dataset.harness);
    };
  }
  const form = document.querySelector(".harness-archive");
  if (!form) return;
  const name = form.closest(".harness-row").dataset.harness;
  const field = document.getElementById("harness-reason");
  const go = document.getElementById("harness-archive-go");
  field.oninput = () => {
    p.reason = field.value;
    go.disabled = !field.value.trim() || p.busy.has(name);
  };
  field.onkeydown = (event) => {
    if (event.key === "Escape") { event.preventDefault(); closeArchiveForm(); }
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    const why = p.reason.trim();
    if (why && !p.busy.has(name)) harnessWrite("archive", name, { reason: why });
  };
}

function closeArchiveForm() {
  const p = harnessesPage, name = p.archiving;
  p.archiving = null;
  p.reason = "";
  paintHarnesses();
  const back = name && document.querySelector(`.harness-row[data-harness="${CSS.escape(name)}"] [data-act="archive-open"]`);
  if (back) back.focus();
}

/* POST one write. The row changes only once the hub answers, from the row
 * it sends back; until then it is dimmed and its buttons are off. */
async function harnessWrite(action, name, extra = {}) {
  const p = harnessesPage;
  if (p.busy.has(name)) return;
  p.busy.add(name);
  p.actionError = null;
  if (onHarnessesPage()) paintHarnesses();
  const { status, data } = await apiPost(`/v1/app/harness/${action}`, { name, ...extra });
  p.busy.delete(name);
  // A list read still in flight may have read the registry before this
  // write landed: drop it, so it can't paint the old state over the reply.
  if (status === 200) p.gen++;
  if (status === 401) {
    if (onHarnessesPage()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    p.actionError = failText(status, data);
  } else if (p.data) {
    const rows = p.data.harnesses;
    const at = rows.findIndex((h) => h.name === data.name);
    if (data.harness && at >= 0) rows[at] = data.harness;
    else if (data.harness) rows.push(data.harness);
    else if (at >= 0) rows.splice(at, 1);
    if (action === "archive" && p.archiving === name) { p.archiving = null; p.reason = ""; }
  } else {
    // A refresh is under way; let it read the registry after this write.
    if (action === "archive" && p.archiving === name) { p.archiving = null; p.reason = ""; }
    if (onHarnessesPage()) renderHarnesses();
    return;
  }
  if (!onHarnessesPage() || !p.data) return;
  const ok = status === 200;
  repaintHarnesses(() => {
    // Nothing kept focus (the clicked button was disabled while it ran):
    // put it back on the row's star, or on the reason after a failed archive.
    const next = { star: ok ? "unstar" : "star", unstar: ok ? "star" : "unstar" }[action];
    const row = document.querySelector(`.harness-row[data-harness="${CSS.escape(name)}"]`);
    return row && (next ? row.querySelector(`[data-act="${next}"]`)
      : action === "archive" && !ok ? row.querySelector("#harness-reason") : null);
  });
}

/* Repaint without taking focus from where the user is now: a reply can
 * land while they type in another row's reason or the search field. Only
 * when focus had nowhere to stay does ``fallback()`` pick the element. */
function repaintHarnesses(fallback) {
  const el = document.activeElement;
  const row = el && el.closest ? el.closest(".harness-row") : null;
  const key = !el || el === document.body ? null
    : el.id ? `#${CSS.escape(el.id)}`
    : row && el.dataset.act ? `.harness-row[data-harness="${CSS.escape(row.dataset.harness)}"] [data-act="${el.dataset.act}"]`
    : null;
  const caret = key && typeof el.selectionStart === "number" ? [el.selectionStart, el.selectionEnd] : null;
  paintHarnesses();
  const again = key ? document.querySelector(key) : null;
  if (again && !again.disabled) {
    again.focus();
    if (caret) { try { again.setSelectionRange(caret[0], caret[1]); } catch { /* not a text field */ } }
    return;
  }
  const target = fallback && fallback();
  if (target) target.focus();
}

function onHarnessesPage() {
  return currentRoute() === "harnesses";
}

/* app.js's api() only reads; writes send JSON with the same headers. */
/* ------------------------------------------------- Harnesses: the sheets */

/* The Mac app's three sheets as one <dialog>: the row editor (HarnessEditor),
 * Detect (DetectSheet) and Add harness (AddHarnessSheet). Each changes
 * harness.json only through the hub, and the list repaints from its answer. */

const HARNESS_NAME_RULE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
// HarnessEdit's fields: [field, swe edit's name for it, is a list].
const EDIT_FIELDS = [["displayName", "display_name", false], ["command", "command", false],
  ["description", "description", false], ["version", "version", false],
  ["aliases", "aliases", true], ["tags", "tags", true], ["notes", "notes", false]];

function bindSheetButtons() {
  for (const btn of document.querySelectorAll("[data-sheet]")) {
    btn.onclick = () => openHarnessSheet(btn.dataset.sheet);
  }
}

/* app.js's bodySheet: route() closes it when the page is left. */
function harnessDialog() {
  const dlg = bodySheet("harness-sheet", "sheet harness-sheet");
  // Esc closes, except while a save is in flight (the app's sheet can't be
  // dismissed then either).
  dlg.oncancel = (event) => {
    event.preventDefault();
    closeHarnessSheet();
  };
  dlg.onclose = () => { harnessesPage.sheet = null; };
  return dlg;
}

/* HarnessEdit(harness): the draft starts from the row. */
function editDraft(h) {
  return {
    displayName: h.display_name || "", command: h.command || "", description: h.description || "",
    version: h.version || "", aliases: (h.aliases || []).join(", "), tags: (h.tags || []).join(", "),
    notes: h.notes || "",
  };
}

/* HarnessEdit.normalised: lists are trimmed, de-duplicated and comma-joined. */
function editNormalised(text, isList) {
  if (!isList) return text.trim();
  const seen = new Set();
  return text.split(",").map((t) => t.trim()).filter((t) => t && !seen.has(t) && seen.add(t)).join(",");
}

/* HarnessEdit.flags(from:): the fields that changed, as swe edit takes them. */
function editChanges(draft, original) {
  const fields = {};
  for (const [key, field, isList] of EDIT_FIELDS) {
    const now = editNormalised(draft[key], isList);
    if (now !== editNormalised(original[key], isList)) fields[field] = now;
  }
  return fields;
}

const commandProblem = (command) => (command.trim() ? null : "Enter the command that runs it.");

function openHarnessSheet(kind, name) {
  const p = harnessesPage;
  if (!p.data) return;
  if (kind === "edit") {
    const h = p.data.harnesses.find((r) => r.name === name);
    if (!h) return;
    p.sheet = { kind, name, original: editDraft(h), draft: editDraft(h), working: false, error: null };
  } else if (kind === "detect") {
    p.sheet = { kind, findings: null, checked: new Set(), registered: null, working: false, error: null, gen: 0 };
  } else {
    p.sheet = { kind, draft: { name: "", command: "", description: "", aliases: "", tags: "" }, working: false, error: null };
  }
  const dlg = harnessDialog();
  paintHarnessSheet();
  if (!dlg.open) dlg.showModal();
  const first = dlg.querySelector("input, textarea");
  if (first) first.focus();
  if (kind === "detect") detectScan();
}

function closeHarnessSheet(force = false) {
  const p = harnessesPage, dlg = document.getElementById("harness-sheet");
  if (!dlg) return;
  if (p.sheet && p.sheet.working && !force) return;
  const name = p.sheet && p.sheet.kind === "edit" ? p.sheet.name : null;
  p.sheet = null;
  if (dlg.open) dlg.close();
  // Back to where the sheet was opened from.
  const back = name ? document.querySelector(`.harness-row[data-harness="${CSS.escape(name)}"] .harness-open`) : null;
  if (back) back.focus();
}

function paintHarnessSheet() {
  const p = harnessesPage, s = p.sheet, dlg = document.getElementById("harness-sheet");
  if (!s || !dlg) return;
  const el = document.activeElement;
  const focusId = el && dlg.contains(el) && el.id ? el.id : null;
  const caret = focusId && typeof el.selectionStart === "number" ? [el.selectionStart, el.selectionEnd] : null;
  dlg.dataset.kind = s.kind;
  dlg.innerHTML = s.kind === "edit" ? editSheet(s) : s.kind === "detect" ? detectSheet(s) : addSheet(s);
  bindHarnessSheet();
  const again = focusId && document.getElementById(focusId);
  if (again && !again.disabled) {
    again.focus();
    if (caret) { try { again.setSelectionRange(caret[0], caret[1]); } catch { /* not a text field */ } }
  }
}

/* A FormField: label, the control, and a hint or problem under it. */
function sheetField(id, label, control, hint = "", problem = false) {
  return `<label class="harness-field" for="${id}"><span class="harness-label">${esc(label)}</span>${control}
    <span class="caption harness-hint${problem ? " problem" : ""}" id="${id}-hint">${esc(hint)}</span></label>`;
}

function sheetInput(id, value, placeholder, { mono = false, multiline = false, rows = 1, disabled = false } = {}) {
  const cls = `field harness-input${mono ? " mono" : ""}`;
  const off = disabled ? " disabled" : "";
  return multiline
    ? `<textarea class="${cls}" id="${id}" rows="${rows}" placeholder="${esc(placeholder)}" spellcheck="false"${off}>${esc(value)}</textarea>`
    : `<input class="${cls}" id="${id}" type="text" value="${esc(value)}" placeholder="${esc(placeholder)}"
        autocomplete="off" spellcheck="false"${off}>`;
}

function editFacts(h) {
  const parts = [{ starred: "Starred", archived: "Archived" }[h.state] || "Not starred"];
  if (h.installed != null) parts.push(h.installed ? "Installed" : "Not on PATH");
  if (h.sessions_100d != null) parts.push(h.sessions_100d === 1 ? "1 session in 100 days" : `${h.sessions_100d} sessions in 100 days`);
  return parts.join(" · ");
}

function editSheet(s) {
  const h = harnessesPage.data.harnesses.find((r) => r.name === s.name) || { name: s.name };
  const d = s.draft, problem = commandProblem(d.command);
  const unchanged = !Object.keys(editChanges(d, s.original)).length;
  const off = { disabled: s.working };
  return `<form class="sheet-body harness-form" id="harness-sheet-form" novalidate>
    <div class="harness-sheet-head">
      <div class="sheet-title">${Fmt.logo(s.name)}<span>Edit ${esc(harnessTitle(h))}</span></div>
      <div class="caption">${esc(editFacts(h))}</div>
    </div>
    <div class="harness-pair">
      ${sheetField("he-displayName", "Name", sheetInput("he-displayName", d.displayName, Fmt.harnessName(s.name), off),
        `Shown across Quiver. Empty uses ${Fmt.harnessName(s.name)}.`)}
      ${sheetField("he-command", "Command", sheetInput("he-command", d.command, s.name, { ...off, mono: true }),
        problem || "What swe runs to start it.", !!problem)}
    </div>
    ${sheetField("he-description", "Description", sheetInput("he-description", d.description, "What it's good for", { ...off, multiline: true, rows: 2 }))}
    <div class="harness-pair">
      ${sheetField("he-version", "Version", sheetInput("he-version", d.version, "1.0.0", { ...off, mono: true }), "Leave empty to clear it.")}
      ${sheetField("he-aliases", "Aliases", sheetInput("he-aliases", d.aliases, "cc, claude-code", { ...off, mono: true }), "Comma-separated short names.")}
    </div>
    ${sheetField("he-tags", "Tags", sheetInput("he-tags", d.tags, "agentic, coding", off), "Comma-separated, like agentic, coding, byok.")}
    ${sheetField("he-notes", "Notes", sheetInput("he-notes", d.notes, "Anything worth remembering about it", { ...off, multiline: true, rows: 3 }))}
    ${s.error ? `<p class="error harness-sheet-error" role="alert">${esc(s.error)}</p>` : ""}
    <div class="sheet-actions">
      <button type="button" class="harness-button" data-sheet-act="cancel"${s.working ? " disabled" : ""}>Cancel</button>
      <button type="submit" class="harness-button primary" id="harness-sheet-go"${s.working || unchanged || problem ? " disabled" : ""}>${s.working ? "Saving…" : "Save"}</button>
    </div>
  </form>`;
}

function detectSheet(s) {
  const machine = (harnessesPage.data && harnessesPage.data.machine) || "the hub";
  let body;
  if (s.registered) {
    const added = s.registered.added;
    body = `<p class="harness-sheet-text">${added.length ? `Registered ${esc(added.join(", "))}.` : "Nothing was registered."}</p>
      ${s.registered.skipped.map((k) => `<p class="caption">Skipped ${esc(k.name)}: ${esc(k.reason)}</p>`).join("")}`;
  } else if (s.findings) {
    body = !s.findings.length
      ? `<p class="harness-sheet-text">Every harness on ${esc(machine)} is already registered.</p>`
      : `<div class="harness-detect-list">${s.findings.map((f) => `<label class="harness-detect-row">
          <input type="checkbox" data-detect="${esc(f.name)}"${s.checked.has(f.name) ? " checked" : ""}${s.working ? " disabled" : ""}>
          <span class="harness-detect-name">${esc(f.name)}</span>
          <code class="caption">${esc(f.command)}</code>
          <span class="badge">${esc(f.confidence)}</span>
          ${f.short_path ? `<span class="caption muted harness-detect-path">${esc(f.short_path)}</span>` : ""}
        </label>`).join("")}</div>`;
  } else if (!s.error) {
    body = `<p class="caption harness-scanning"><span class="harness-spinner" aria-hidden="true"></span>Scanning PATH and ~/ for coding CLIs…</p>`;
  } else {
    body = "";
  }
  const offer = !s.registered && s.findings && s.findings.length;
  return `<form class="sheet-body harness-form harness-detect" id="harness-sheet-form" novalidate>
    <div class="sheet-title"><span>Detect harnesses</span></div>
    ${s.error ? `<p class="error harness-sheet-error" role="alert">${esc(s.error)}</p>` : ""}
    ${body}
    <div class="sheet-actions">
      <button type="button" class="harness-button" data-sheet-act="cancel"${s.working ? " disabled" : ""}>${s.registered ? "Done" : "Cancel"}</button>
      ${offer ? `<button type="submit" class="harness-button primary" id="harness-sheet-go"${s.working || !s.checked.size ? " disabled" : ""}>Register (${s.checked.size})</button>` : ""}
    </div>
  </form>`;
}

/* AddHarnessSheet.nameProblem */
function addNameProblem(name) {
  if (!name) return "Letters, digits, then . _ -";
  if ((harnessesPage.data.harnesses || []).some((h) => h.name === name)) return `${name} is already registered`;
  return HARNESS_NAME_RULE.test(name) ? null : "Start with a letter or digit; then . _ -";
}

function addSheet(s) {
  const d = s.draft, problem = addNameProblem(d.name);
  const off = { disabled: s.working };
  return `<form class="sheet-body harness-form harness-add-form" id="harness-sheet-form" novalidate>
    <div class="sheet-title"><span>Add harness</span></div>
    ${sheetField("ha-name", "Name", sheetInput("ha-name", d.name, "mycli", { ...off, mono: true }), problem || "", !!problem)}
    ${sheetField("ha-command", "Command", sheetInput("ha-command", d.command, "binary name or path", { ...off, mono: true }))}
    ${sheetField("ha-description", "Description", sheetInput("ha-description", d.description, "What it does", off))}
    ${sheetField("ha-aliases", "Aliases", sheetInput("ha-aliases", d.aliases, "comma-separated", { ...off, mono: true }))}
    ${sheetField("ha-tags", "Tags", sheetInput("ha-tags", d.tags, "agentic, coding", { ...off, mono: true }))}
    ${s.error ? `<p class="error harness-sheet-error" role="alert">${esc(s.error)}</p>` : ""}
    <div class="sheet-actions">
      <button type="button" class="harness-button" data-sheet-act="cancel"${s.working ? " disabled" : ""}>Cancel</button>
      <button type="submit" class="harness-button primary" id="harness-sheet-go"${s.working || problem || !d.command ? " disabled" : ""}>${s.working ? "Adding…" : "Add"}</button>
    </div>
  </form>`;
}

function bindHarnessSheet() {
  const p = harnessesPage, s = p.sheet, dlg = document.getElementById("harness-sheet");
  const cancel = dlg.querySelector('[data-sheet-act="cancel"]');
  if (cancel) cancel.onclick = () => closeHarnessSheet();
  const go = document.getElementById("harness-sheet-go");
  // Typing updates the draft and the button and hints in place, so the
  // field being typed in is never redrawn.
  const refresh = () => {
    if (s.kind === "edit") {
      const problem = commandProblem(s.draft.command);
      const hint = document.getElementById("he-command-hint");
      hint.textContent = problem || "What swe runs to start it.";
      hint.classList.toggle("problem", !!problem);
      go.disabled = s.working || !!problem || !Object.keys(editChanges(s.draft, s.original)).length;
    } else if (s.kind === "add") {
      const problem = addNameProblem(s.draft.name);
      const hint = document.getElementById("ha-name-hint");
      hint.textContent = problem || "";
      hint.classList.toggle("problem", !!problem);
      go.disabled = s.working || !!problem || !s.draft.command;
    }
  };
  for (const input of dlg.querySelectorAll(".harness-input")) {
    const key = input.id.slice(3);
    input.oninput = () => { s.draft[key] = input.value; refresh(); };
  }
  for (const box of dlg.querySelectorAll("[data-detect]")) {
    box.onchange = () => {
      if (box.checked) s.checked.add(box.dataset.detect); else s.checked.delete(box.dataset.detect);
      if (go) { go.textContent = `Register (${s.checked.size})`; go.disabled = s.working || !s.checked.size; }
    };
  }
  const form = document.getElementById("harness-sheet-form");
  form.onsubmit = (event) => {
    event.preventDefault();
    if (!go || go.disabled) return;
    if (s.kind === "edit") editSave(s);
    else if (s.kind === "detect") detectRegister(s);
    else addSave(s);
  };
}

function sheetFailure(status, data) {
  return failText(status, data);
}

/* The sheet's request: working while it runs, the error in the sheet. A
 * 401 closes it for the pairing card. Returns the reply, or null; a 401
 * also sets ``gated``, and the caller then leaves the page alone so the
 * pairing card stays up. */
async function sheetPost(s, action, body) {
  s.working = true;
  s.error = null;
  paintHarnessSheet();
  const { status, data } = await apiPost(`/v1/app/harness/${action}`, body);
  s.working = false;
  if (status === 401) {
    s.gated = true;
    closeHarnessSheet(true);
    if (onHarnessesPage()) renderGate();
    return null;
  }
  // A list read still in flight may predate this write: drop it.
  if (status === 200) harnessesPage.gen++;
  if (status !== 200 || !data) {
    s.error = sheetFailure(status, data);
    if (harnessesPage.sheet === s) paintHarnessSheet();
    return null;
  }
  return data;
}

async function editSave(s) {
  const p = harnessesPage, name = s.name;
  const fields = editChanges(s.draft, s.original);
  if (!Object.keys(fields).length || commandProblem(s.draft.command)) return;
  p.busy.add(name);
  if (onHarnessesPage() && p.data) repaintHarnesses();
  const data = await sheetPost(s, "edit", { name, fields });
  p.busy.delete(name);
  if (s.gated) return;
  if (data && p.data) {
    const at = p.data.harnesses.findIndex((h) => h.name === data.name);
    if (at >= 0 && data.harness) p.data.harnesses[at] = data.harness;
  }
  if (data && p.sheet === s) closeHarnessSheet(true);
  if (onHarnessesPage() && p.data) repaintHarnesses();
}

async function detectScan() {
  const p = harnessesPage, s = p.sheet;
  const mine = ++s.gen;
  const { status, data } = await api("/v1/app/harness?view=discover");
  if (p.sheet !== s || mine !== s.gen) return;
  if (status === 401) {
    closeHarnessSheet(true);
    if (onHarnessesPage()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    s.error = sheetFailure(status, data);
  } else {
    // HarnessDiscovery.selection: new matches only, the sure ones ticked.
    s.findings = data.findings.filter((f) => f.status === "new");
    s.checked = new Set(s.findings.filter((f) => f.confidence === "high").map((f) => f.name));
  }
  paintHarnessSheet();
  const go = document.getElementById("harness-sheet-go");
  if (go && !go.disabled) go.focus();
}

async function detectRegister(s) {
  const p = harnessesPage;
  const names = s.findings.filter((f) => s.checked.has(f.name)).map((f) => f.name);
  if (!names.length) return;
  const data = await sheetPost(s, "register", { names });
  if (!data) return;
  if (p.data) p.data.harnesses = data.harnesses;
  s.registered = { added: data.applied.added || [], skipped: data.applied.skipped || [] };
  if (p.sheet === s) {
    paintHarnessSheet();
    const done = document.querySelector('[data-sheet-act="cancel"]');
    if (done) done.focus();
  }
  if (onHarnessesPage() && p.data) repaintHarnesses();
}

async function addSave(s) {
  const p = harnessesPage, d = s.draft;
  if (addNameProblem(d.name) || !d.command) return;
  // AddHarnessSheet.csv
  const csv = (text) => text.split(",").map((t) => t.trim()).filter(Boolean);
  const data = await sheetPost(s, "add", {
    name: d.name, command: d.command, description: d.description,
    aliases: csv(d.aliases), tags: csv(d.tags),
  });
  if (!data) return;
  if (p.data && data.harness) {
    const at = p.data.harnesses.findIndex((h) => h.name === data.name);
    if (at >= 0) p.data.harnesses[at] = data.harness; else p.data.harnesses.push(data.harness);
  }
  if (p.sheet === s) closeHarnessSheet(true);
  if (onHarnessesPage() && p.data) repaintHarnesses();
}

/* ---------------------------------------------------------------- Accounts */

const accountsPage = { data: null, gen: 0, machine: null };
// Harnesses whose limits swe reads (HarnessAccess); others have none to show.
const LIMIT_HARNESSES = new Set(["claude", "codex", "copilot", "cursor", "devin", "droid", "freebuff"]);
const STALE_S = 20 * 60;
// The command that signs each one in (HarnessAccess.loginCommand, plus
// Claude and Codex, which the Mac app signs in per account). Droid signs
// in from inside its own TUI.
const LOGIN_COMMANDS = {
  claude: "claude auth login", codex: "codex login", copilot: "gh auth login",
  cursor: "cursor-agent login", devin: "devin auth login", freebuff: "freebuff login",
};

async function renderAccounts(_id, refresh = false) {
  const panel = document.getElementById("panel");
  const gen = ++accountsPage.gen;
  if (!accountsPage.data || refresh) {
    panel.innerHTML = `<div class="page">${accountsHeader(true)}<div class="card"><p class="empty">${
      accountsPage.data ? "Refreshing…" : "Reading limits…"}</p></div></div>`;
  } else {
    paintAccounts();
  }
  startAccountsClock();
  accountsPage.loading = true;
  const { status, data } = await api(`/v1/usage${refresh ? "?refresh=1" : ""}`);
  if (gen !== accountsPage.gen) return;
  accountsPage.loading = false;
  // A failed request pauses the clock's repaints, which would otherwise
  // put the old readings back over the error or the pairing steps.
  accountsPage.failed = status !== 200 || !data;
  if (status === 401) return renderGate();
  if (accountsPage.failed) return pageError(accountsHeader(false), status, data, () => renderAccounts("accounts", true));
  accountsPage.data = data;
  paintAccounts();
}

function accountsHeader(busy) {
  return pageHeader("Accounts", "Usage limits for the harnesses you starred, on the hub and each paired machine.",
    `<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

function paintAccounts() {
  const a = accountsPage, d = a.data;
  const machines = d.machines && d.machines.length ? d.machines
    : [{ machine: d.machine, harnesses: d.harnesses, hub: true, reported_at: d.server_time, received_at: d.server_time }];
  const ids = machines.map((m) => (m.machine && m.machine.id) || "hub");
  if (!a.machine || !ids.includes(a.machine)) a.machine = ids[0];
  const current = machines[ids.indexOf(a.machine)];
  const chips = machines.length > 1 ? `<div class="tabs" role="tablist">${machines.map((m, i) =>
    `<button class="tab${ids[i] === a.machine ? " selected" : ""}" data-machine="${esc(ids[i])}">${
      esc((m.machine && m.machine.name) || ids[i])}${m.hub ? " (hub)" : ""}</button>`).join("")}</div>` : "";
  // A satellite's figures are as old as its last report.
  const age = current.hub ? 0 : Math.max(0, (Date.now() / 1000) - (current.received_at || 0));
  const lateNote = !current.hub && age > 2 * 300
    ? `<div class="card"><p class="note">${esc((current.machine && current.machine.name) || "This machine")} last reported ${Fmt.ago(current.received_at)}; its figures may be out of date.</p></div>` : "";
  const starred = (current.harnesses || []).filter((h) => h.state === "starred");
  const body = starred.length
    ? `<div class="account-grid">${starred.map((h) => accountCard(h, current)).join("")}</div>
       <p class="caption footer">Only starred harnesses show here. Star one on the <a href="#/harnesses">Harnesses</a> page, or with <code>swe harness star &lt;name&gt;</code>.</p>`
    : `<div class="card"><p class="empty">Nothing starred. Star a harness on the <a href="#/harnesses">Harnesses</a> page to see its limits here.</p></div>`;
  document.getElementById("panel").innerHTML = `<div class="page">${accountsHeader(false)}
    <div class="stack">${chips}${lateNote}${body}</div></div>`;
  bindRefresh(() => renderAccounts("accounts", true));
  for (const chip of document.querySelectorAll("[data-machine]")) {
    chip.onclick = () => { a.machine = chip.dataset.machine; paintAccounts(); };
  }
}

/* Ages, countdowns, staleness and the late-satellite note move with the
 * clock, so the open page repaints them every 30 s (no provider call). */
function startAccountsClock() {
  if (accountsPage.timer) return;
  accountsPage.timer = setInterval(() => {
    const onPage = currentRoute() === "accounts";
    if (!onPage) { clearInterval(accountsPage.timer); accountsPage.timer = null; return; }
    if (accountsPage.data && !accountsPage.loading && !accountsPage.failed && !document.hidden) paintAccounts();
  }, 30000);
}

/* A reading's windows, or one built from its top-level fields. */
function readingWindows(rl) {
  if (rl.windows && rl.windows.length) return rl.windows;
  return [{ window: rl.window, used_percent: rl.used_percent, reset_at: rl.reset_at }];
}

function barColor(pct) {
  return pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warning)" : "var(--accent-fill)";
}

/* ``now`` ages the machine's own stamps (as_of); a reset time is the
 * provider's absolute clock, so it counts down on this browser's. */
function windowRow(w, now) {
  const pct = Math.max(0, Math.min(100, Math.round(w.used_percent || 0)));
  const failed = !!w.unavailable;
  const real = Date.now() / 1000;
  const right = failed
    ? (w.as_of ? `as of ${Fmt.ago(w.as_of, now)}` : "")
    : (w.reset_at && w.reset_at > real ? `Resets ${Fmt.resetIn(w.reset_at, real)}` : "");
  return `<div class="limit${failed ? " failed" : ""}">
    <div class="limit-head"><span class="limit-label">${esc(Fmt.windowLabel(w.window))}</span>
      <span class="num">${failed && !w.as_of ? "—" : `${pct}%`}</span></div>
    <div class="track"><div class="fill" style="width:${pct}%; background:${barColor(pct)}"></div></div>
    ${right ? `<div class="caption">${esc(right)}</div>` : ""}
  </div>`;
}

function accountCard(h, machine) {
  const rl = h.rate_limit;
  // A satellite's clock may differ: age its reading on the hub's clock.
  const skew = machine.hub ? 0 : (machine.received_at || 0) - (machine.reported_at || machine.received_at || 0);
  const now = Date.now() / 1000 - skew;
  const plan = rl ? Fmt.planTitle(rl.plan_type) : "";
  let body = "", status, statusHTML = "";
  if (!rl && h.no_reading === "signed_out") {
    // Credentials live on the machine the reading comes from, so name it.
    const where = (machine.machine && machine.machine.name) || "this machine";
    const login = LOGIN_COMMANDS[h.name];
    statusHTML = login
      ? `Not signed in on ${esc(where)}. Run <code>${esc(login)}</code> there.`
      : `Not signed in on ${esc(where)}. Sign in from ${esc(Fmt.harnessName(h.name))} there.`;
  } else if (!rl && h.no_reading === "error") {
    status = "Couldn't fetch usage. Refresh to try again.";
  } else if (!rl) {
    status = LIMIT_HARNESSES.has(h.name) ? "No reading yet" : "No usage limits to read";
  } else if (rl.status === "ok") {
    body = readingWindows(rl).map((w) => windowRow(w, now)).join("");
    if (rl.on_demand) {
      const [value, detail] = Fmt.onDemand(rl.on_demand);
      body += `<div class="limit-extra"><span class="caption">On-demand</span><span>${esc(value)} · <span class="caption">${esc(detail)}</span></span></div>`;
    }
    if (rl.extra_usage_usd != null) {
      body += `<div class="limit-extra"><span class="caption">Extra usage balance</span><span class="num">${Fmt.balance(rl.extra_usage_usd)}</span></div>`;
    }
    status = rl.fetched_at ? `Fetched ${Fmt.ago(rl.fetched_at, now)}` : "No reading yet";
  } else {
    status = { "auth-required": `Signed out. Sign in again in ${Fmt.harnessName(h.name)}.`,
      "no-sub": "No subscription" }[rl.status] || "Didn't answer in time";
  }
  // Older than 20 minutes reads as stale, as in the menu bar.
  const stale = rl && rl.status === "ok" && (!rl.fetched_at || now - rl.fetched_at > STALE_S);
  return `<div class="card account${stale ? " stale" : ""}">
    <div class="account-head">${Fmt.logo(h.name)}<span class="card-title">${esc(harnessTitle(h))}</span>
      ${plan ? `<span class="badge">${esc(plan)}</span>` : ""}</div>
    ${body}
    <div class="caption">${statusHTML || esc(status)}</div>
  </div>`;
}

/* ---------------------------------------------------------------- shared */

/* A filter box whose page repaints, and so replaces it, as you type.
 * ``onText`` keeps every keystroke, but the repaint waits while an IME
 * composition is open (it would end the composition mid-word) and runs
 * once the composition ends. ``repaint`` puts the focus and caret back.
 * ``input.composing`` says a composition is open, so a background repaint
 * (GhUI.keepFocus) can wait for that same compositionend repaint. */
function bindSearch(input, onText, repaint) {
  input.oncompositionstart = () => { input.composing = true; };
  input.oninput = (e) => { onText(input.value); if (!e.isComposing) repaint(); };
  input.oncompositionend = () => { input.composing = false; onText(input.value); repaint(); };
}

function bindRefresh(render, before) {
  const btn = document.getElementById("page-refresh");
  if (btn) btn.onclick = () => { if (before) before(); render(); };
}

function pageError(header, status, data, retry) {
  const msg = failText(status, data, "then refresh");
  document.getElementById("panel").innerHTML = `<div class="page">${header}<div class="card"><p class="error">${esc(msg)}</p></div></div>`;
  bindRefresh(retry);
}
