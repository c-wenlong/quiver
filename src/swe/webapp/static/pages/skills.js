/* Skills: a port of the Mac app's SkillsPage and SkillDetailView. The
 * Harness links popover links, unlinks and links all, through
 * POST /v1/app/skills/<action>; New skill and frontmatter edits stay in
 * `swe skills` and the Mac app. The list is #/skills; one skill is
 * #/skills?skill=<its folder>. Data: /v1/app/skills (pages/skills.py). */
"use strict";

const skillsPage = {
  data: null, seq: 0, query: "", harness: "", source: "", showsAll: false,
  links: null,            // null closed, "" the chip list, else a root's path
  // Writes: folders waiting on the hub, the confirm on show ({kind, path}
  // or {kind: "link_all", paths}), the last failure and Link all's summary.
  busy: new Set(), confirm: null, actionError: null, summary: null,
  newToken: 0,            // the New skill form on show; older replies are stale
  fileToken: 0,           // the file sheet's current read, likewise
  detail: {},             // folder -> detail payload
  fmShowsAll: false, treeShowsAll: false, open: new Set(),
};

// Skill.Source, in the enum's order, with its title.
const SKILL_SOURCES = [["shared", "Shared"], ["plugin", "Plugin"], ["harness", "Harness"],
  ["builtin", "Built in"], ["catalog", "Catalog"], ["project", "Project"]];
const SKILL_RANK = { shared: 0, harness: 1, catalog: 2, project: 3, plugin: 4, builtin: 5 };
const SKILL_FOLD = 15, DETAIL_FOLD = 3;
const LINK_TITLE = { linked: "Linked", notLinked: "Not linked", blocked: "Blocked" };

const skillSourceTitle = (s) => (SKILL_SOURCES.find(([id]) => id === s) || [s, "Harness"])[1];

/* SkillRoot.link */
function rootLink(root) {
  if (root.state === "linked") return "linked";
  return ["create", "relink", "absorb"].includes(root.state) ? "notLinked" : "blocked";
}

/* SkillRoot.explanation */
function rootExplanation(root) {
  const d = root.detail || "";
  switch (root.state) {
    case "linked": return root.archived
      ? "Reads the shared skills. Archived in swe, so swe init won't relink it if you unlink it, but you can link it here."
      : "Reads the shared skills in ~/.swe/skills.";
    case "create": return root.archived
      ? "No skills folder yet. Archived in swe, so swe init leaves it alone; linking here creates it."
      : "No skills folder yet. Linking creates it.";
    case "relink": return d ? `Points somewhere else: ${d.replace("points at ", "")}.` : "Points somewhere else.";
    case "absorb": return d === "empty" ? "An empty folder. Linking replaces it."
      : `Holds only copies of shared skills (${d}). Linking backs it up and replaces it.`;
    case "keep": return `Holds skills that exist nowhere else (${d}). Move them into ~/.swe/skills before linking.`;
    case "ignored": return d ? `Ignored: ${d}.` : "Ignored by swe init.";
    case "skipped": return "The harness isn't installed.";
    default: return d ? `${root.state}: ${d}` : root.state;
  }
}

/* `~/…` for a path under the hub's home (the list's, when not given). */
function skillsPath(path, home) {
  return Fmt.tilde(path, home || (skillsPage.data && skillsPage.data.home));
}

/* Too long for a row: keep both ends, as .truncationMode(.middle) does. */
function skillsMiddle(text, max) {
  if (text.length <= max) return text;
  const head = Math.ceil((max - 1) / 2), tail = Math.floor((max - 1) / 2);
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`;
}

/* HarnessMarkView (Fmt.markView). */
const skillsMark = (h, cls = "logo") => Fmt.markView(h, "sk-mark", cls);

/* SeenBy: one harness as its mark, several as up to three logos and the count. */
function skillsSeenBy(harnesses) {
  if (!harnesses.length) return "No harness";
  if (harnesses.length === 1) return skillsMark(harnesses[0]);
  const seen = new Set(), logos = [];
  for (const h of harnesses) {
    const key = h === "claude-desktop" ? "claude" : h;
    if (!Fmt.logo(key).startsWith("<img") || seen.has(key)) continue;
    seen.add(key);
    if (logos.length < 3) logos.push(Fmt.logo(key));
  }
  const names = harnesses.map(Fmt.harnessName).join(", ");
  return `<span class="sk-seen-many" title="${esc(names)}" aria-label="${harnesses.length} harnesses: ${esc(names)}">
    <span class="sk-logos">${logos.join("")}</span>${harnesses.length} harnesses</span>`;
}

function skillsLinkDot(link) {
  return `<span class="sk-dot ${link}" aria-hidden="true"></span>`;
}

function skillsChip(name, link, { compact = false, root = "", busy = false } = {}) {
  const tag = root ? "button" : "span";
  const data = root ? ` data-root="${esc(root)}" type="button"` : "";
  const dot = busy ? '<span class="sk-spin" aria-label="Working"></span>' : skillsLinkDot(link);
  return `<${tag} class="sk-chip ${link}${compact ? " compact" : ""}"${data}
    title="${esc(Fmt.harnessName(name))}: ${LINK_TITLE[link].toLowerCase()}">${dot}${skillsMark(name, compact ? "logo logo-sm" : "logo")}</${tag}>`;
}

/* SkillRoot.linkNeedsForce */
const rootNeedsForce = (root) => root.state === "absorb" && root.detail !== "empty";

/* SkillsModel.linkAllTargets: loose folders that cost nothing to point at
 * the shared root, and the ones a person should look at. */
function linkAllTargets(roots) {
  const link = [], skipped = [];
  for (const r of roots) {
    const l = rootLink(r);
    if (l === "notLinked" && !r.archived && !rootNeedsForce(r)) link.push(r);
    else if (l === "notLinked" && !r.archived) skipped.push(r);
    else if (l === "blocked" && (r.state === "keep" || r.state === "conflict")) skipped.push(r);
  }
  return { link, skipped };
}

/* What a confirm says will happen, in full, before anything moves. */
function skillsConfirmText(c, d) {
  const shared = skillsPath(d.shared_dir, d.home);
  if (c.kind === "link_all") {
    const roots = c.paths.map((path) => d.harnesses.find((r) => r.path === path)).filter(Boolean);
    const names = roots.map((r) => `${Fmt.harnessName(r.name)} (${skillsPath(r.path, d.home)})`).join(", ");
    return `Points ${roots.length === 1 ? "this folder" : `these ${roots.length} folders`} at ${shared}: ${names}. Each one is missing, empty or a link elsewhere, so nothing is backed up or lost.`;
  }
  const root = d.harnesses.find((r) => r.path === c.path);
  const path = skillsPath(root.path, d.home), name = Fmt.harnessName(root.name);
  if (c.kind === "unlink") {
    return `Removes the link at ${path}. ${name} stops loading the shared skills; nothing in ${shared} changes.`
      + (root.archived ? " It's archived in swe, so swe init won't link it again." : "");
  }
  switch (root.state) {
    case "create": return `Creates ${path} as a link to ${shared}, so ${name} loads every shared skill.`;
    case "relink": return `Points ${path} at ${shared} instead of where it points now, so ${name} loads every shared skill.`;
    default: return rootNeedsForce(root)
      ? `Copies ${path} into ~/.swe/backups, then replaces it with a link to ${shared}. The skills in it (${root.detail}) are all in the shared library already.`
      : `Removes ${path}, which holds no files, and puts a link to ${shared} in its place.`;
  }
}

/* ------------------------------------------------------------------ list */

function renderSkills() {
  // The Harness links popover never stays open across a page change.
  skillsPage.links = null;
  skillsPage.confirm = null;
  const q = new URLSearchParams((location.hash.split("?")[1]) || "");
  const path = q.get("skill");
  skillsPage.onDetail = !!path;
  if (path) return renderSkillDetail(path);
  return renderSkillsList();
}

async function renderSkillsList() {
  const p = skillsPage, gen = routeGen, mine = ++p.seq;
  if (!p.data) {
    document.getElementById("panel").innerHTML = `<div class="page">${skillsHeader(true)}
      ${skillsSkeleton()}</div>`;
    bindSkillsHeader();
  } else {
    paintSkills();
  }
  const { status, data } = await api("/v1/app/skills");
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) {
    // New skill and Harness links work beside the error too.
    pageError(skillsHeader(false), status, data, renderSkills);
    return bindSkillsHeader();
  }
  p.data = data;
  p.detail = {};
  paintSkills();
}

/* The payload of what is on screen: a skill's own on its detail page,
 * so the header and the chips read the same link states. */
function skillsShown() {
  const p = skillsPage;
  return (p.onDetail && p.headerData) || p.data || p.headerData;
}

function skillsHeader(busy) {
  const p = skillsPage, d = skillsShown();
  const where = d && d.machine ? d.machine : "the hub";
  let links = "";
  if (d) {
    const roots = d.harnesses, linked = roots.filter((r) => rootLink(r) === "linked").length;
    const blocked = roots.some((r) => rootLink(r) === "blocked");
    links = `<div class="sk-links-wrap">
      <button class="sk-links-button${skillsPage.links !== null ? " open" : ""}" id="sk-links" type="button"
        aria-expanded="${skillsPage.links !== null}" title="Which harnesses load the shared skills">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>
        <span class="primary">Harness links</span>
        <span class="num">${roots.length ? `${linked} of ${roots.length} linked` : "None found"}</span>
        ${blocked ? skillsLinkDot("blocked") : ""}
        <span class="sk-caret" aria-hidden="true">▾</span>
      </button>
      ${skillsPage.links !== null ? skillsLinksPanel(d) : ""}
    </div>`;
  }
  const add = p.onDetail ? "" : `<button class="sk-button primary sk-new-button" id="sk-new" type="button"
      title="Write a new SKILL.md into ~/.swe/skills on ${esc(where)}">${SKILLS_PLUS}New skill</button>`;
  return pageHeader("Skills", `Every skill on ${where} outside a plugin, and which harnesses load it.`,
    `${links}${add}<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

/* HarnessLinksPanel, and RootDetail in its place once a chip is picked. */
function skillsLinksPanel(d) {
  const roots = d.harnesses;
  const root = skillsPage.links ? roots.find((r) => r.path === skillsPage.links) : null;
  if (root) {
    const link = rootLink(root);
    return `<div class="sk-popover" role="dialog" aria-label="${esc(Fmt.harnessName(root.name))}">
      <button class="sk-back-small" id="sk-links-back" type="button">‹ Harness links</button>
      <div class="sk-root-head">${Fmt.logo(root.name).startsWith("<img") ? Fmt.logo(root.name) : ""}<span class="name">${esc(Fmt.harnessName(root.name))}</span>
        <span class="badge">${LINK_TITLE[link]}</span></div>
      <code class="sk-mono">${esc(skillsPath(root.path, d.home))}</code>
      <p class="sk-body">${esc(rootExplanation(root))}</p>
      ${skillsRootActions(root, d, link)}
    </div>`;
  }
  const p = skillsPage, { link: targets } = linkAllTargets(roots);
  const confirmAll = p.confirm && p.confirm.kind === "link_all" ? p.confirm : null;
  let all = "";
  if (confirmAll) {
    all = skillsConfirm(confirmAll, d, `Link ${confirmAll.paths.length}`, p.busy.size > 0);
  } else if (targets.length) {
    all = `<div class="sk-actions"><button class="sk-button" type="button" id="sk-link-all"${p.busy.size ? " disabled" : ""}>Link all (${targets.length})</button></div>`;
  }
  const body = roots.length
    ? `<div class="sk-chips">${roots.map((r) => skillsChip(r.name, rootLink(r), { root: r.path, busy: p.busy.has(r.path) })).join("")}</div>
       <div class="sk-legend">${["linked", "notLinked", "blocked"].map((l) =>
         `<span>${skillsLinkDot(l)}${LINK_TITLE[l]}</span>`).join("")}<span class="muted">Click one to link or unlink it.</span></div>
       ${all}`
    : `<div class="sk-none"><span>No harness skills folders found. Register a harness first.</span>
       <a class="sk-button" href="#/harnesses">Open Harnesses</a></div>`;
  return `<div class="sk-popover" role="dialog" aria-label="Harness links">
    <div><div class="card-title">Harness links</div>
      <p class="caption">Each harness reads one skills folder. A linked folder points at ${esc(skillsPath(d.shared_dir, d.home))}, so every shared skill reaches it.</p></div>
    ${body}
    ${p.summary ? `<p class="caption sk-summary">${esc(p.summary)}</p>` : ""}
    ${p.actionError ? `<p class="error sk-popover-error" role="alert">${esc(p.actionError)}</p>` : ""}
  </div>`;
}

/* RootDetail's buttons: Unlink for a linked folder, Link to shared skills
 * for one that isn't, nothing for a blocked one; each asks first. */
function skillsRootActions(root, d, link) {
  // One write at a time, as the hub runs them: a reply can then never
  // land after a newer one and paint older states over it.
  const p = skillsPage, busy = p.busy.size > 0;
  const c = p.confirm && p.confirm.path === root.path ? p.confirm : null;
  if (c) return skillsConfirm(c, d, c.kind === "unlink" ? "Unlink" : "Link", busy);
  if (link === "linked") {
    return `<div class="sk-actions"><button class="sk-button" type="button" data-sk-ask="unlink"${busy ? " disabled" : ""}>Unlink</button></div>`;
  }
  if (link === "notLinked") {
    return `<div class="sk-actions"><button class="sk-button primary" type="button" data-sk-ask="link"${busy ? " disabled" : ""}>Link to shared skills</button></div>`;
  }
  return "";
}

function skillsConfirm(c, d, go, busy) {
  return `<div class="sk-confirm" role="group" aria-label="Confirm">
    <p class="sk-body">${esc(skillsConfirmText(c, d))}</p>
    <div class="sk-actions">
      <button class="sk-button" type="button" id="sk-confirm-cancel">Cancel</button>
      <button class="sk-button primary" type="button" id="sk-confirm-go"${busy ? " disabled" : ""}>${esc(go)}</button>
    </div></div>`;
}

function bindSkillsHeader() {
  const p = skillsPage;
  const add = document.getElementById("sk-new");
  if (add) add.onclick = (e) => { e.stopPropagation(); openNewSkill(); };
  bindRefresh(renderSkills, () => { p.data = null; p.detail = {}; });
  const repaintHeader = () => {
    const head = document.querySelector("#panel .page-header");
    if (!head) return;
    head.outerHTML = skillsHeader(false);
    bindSkillsHeader();
  };
  const btn = document.getElementById("sk-links");
  if (btn) btn.onclick = (e) => { e.stopPropagation(); p.links = p.links === null ? "" : null; p.confirm = null; repaintHeader(); };
  const back = document.getElementById("sk-links-back");
  if (back) back.onclick = (e) => { e.stopPropagation(); p.links = ""; p.confirm = null; repaintHeader(); };
  for (const chip of document.querySelectorAll(".sk-popover [data-root]")) {
    chip.onclick = (e) => { e.stopPropagation(); p.links = chip.dataset.root; p.confirm = null; repaintHeader(); };
  }
  for (const ask of document.querySelectorAll(".sk-popover [data-sk-ask]")) {
    ask.onclick = (e) => { e.stopPropagation(); p.confirm = { kind: ask.dataset.skAsk, path: p.links }; repaintHeader(); };
  }
  const all = document.getElementById("sk-link-all");
  if (all) all.onclick = (e) => {
    e.stopPropagation();
    const d = skillsShown();
    p.confirm = { kind: "link_all", paths: linkAllTargets(d.harnesses).link.map((r) => r.path) };
    repaintHeader();
  };
  const cancel = document.getElementById("sk-confirm-cancel");
  if (cancel) cancel.onclick = (e) => { e.stopPropagation(); p.confirm = null; repaintHeader(); };
  const go = document.getElementById("sk-confirm-go");
  if (go) go.onclick = (e) => { e.stopPropagation(); skillsWrite(p.confirm); };
  const pop = document.querySelector(".sk-popover");
  if (pop) pop.onclick = (e) => e.stopPropagation();
  if (!p.outsideBound) {
    p.outsideBound = true;
    const close = () => {
      if (p.links === null) return;
      p.links = null;
      p.confirm = null;
      repaintHeader();
    };
    document.addEventListener("click", () => { if (document.querySelector(".sk-links-wrap")) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.querySelector(".sk-popover")) close(); });
  }
}

/* ------------------------------------------------------------- new skill */

const SKILLS_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

/* NewSkillSheet.nameProblem: the Agent Skills name rule. */
function newSkillProblem(name) {
  if (!name) return "Lowercase, digits and single hyphens: my-skill";
  if (name.length > 64) return "At most 64 characters";
  return /^[a-z0-9]+(-[a-z0-9]+)*$/.test(name) ? null : "Lowercase letters, digits, and single hyphens between them";
}

/* NewSkillSheet: swe skills new <name> on the hub, POST
 * /v1/app/skills/new. The sheet stays open on "Created …" with Open it. */
function openNewSkill() {
  const sheet = bodySheet("sk-new-sheet", "sheet sk-new-sheet", "New skill");
  const where = (skillsPage.data && skillsPage.data.machine) || "the hub";
  sheet.innerHTML = `<form class="sheet-body" id="sk-new-form" novalidate>
    <div class="sheet-title">New skill</div>
    <label class="sk-new-field"><span class="sk-new-label">Name</span>
      <input class="field sk-new-name" id="sk-new-name" type="text" placeholder="my-skill" autocomplete="off" spellcheck="false" maxlength="200">
      <span class="caption" id="sk-new-hint"></span></label>
    <label class="sk-new-field"><span class="sk-new-label">Description</span>
      <input class="field" id="sk-new-desc" type="text" placeholder="When should an agent use this skill?" autocomplete="off" maxlength="1024"></label>
    <p class="caption" id="sk-new-where"></p>
    <p class="error" id="sk-new-error" role="alert" hidden></p>
    <div class="sheet-actions" id="sk-new-actions">
      <button class="sk-button" type="button" id="sk-new-close">Cancel</button>
      <button class="sk-button primary" type="submit" id="sk-new-create" disabled>Create</button>
    </div></form>`;
  const $ = (id) => document.getElementById(id);
  const sync = () => {
    const name = $("sk-new-name").value.trim(), problem = newSkillProblem(name);
    $("sk-new-hint").textContent = problem || "";
    $("sk-new-where").textContent = `Writes ~/.swe/skills/${problem ? "<name>" : name}/SKILL.md on ${where}.`;
    $("sk-new-create").disabled = !!problem || sheet.dataset.working === "1";
    $("sk-new-error").hidden = true;
  };
  $("sk-new-name").oninput = sync;
  $("sk-new-desc").oninput = () => { $("sk-new-error").hidden = true; };
  $("sk-new-close").onclick = () => sheet.close();
  $("sk-new-form").onsubmit = (e) => { e.preventDefault(); createSkill(sheet); };
  // A reply to a form closed and opened again since must not touch this one.
  sheet.dataset.token = String(++skillsPage.newToken);
  sheet.dataset.working = "";
  sync();
  if (!sheet.open) sheet.showModal();
  $("sk-new-name").focus();
}

async function createSkill(sheet) {
  const p = skillsPage, $ = (id) => document.getElementById(id);
  const name = $("sk-new-name").value.trim(), description = $("sk-new-desc").value.trim();
  if (newSkillProblem(name) || sheet.dataset.working === "1") return;
  const token = sheet.dataset.token;
  sheet.dataset.working = "1";
  $("sk-new-create").disabled = true;
  $("sk-new-create").textContent = "Creating…";
  const { status, data } = await apiPost("/v1/app/skills/new", { name, description });
  const current = sheet.dataset.token === token && sheet.open && !!document.getElementById("sk-new-form");
  if (current) sheet.dataset.working = "";
  if (status === 401) {
    if (current) sheet.close();
    if (skillsOnRoute()) renderGate();
    return;
  }
  // The skill exists whatever happened to the form: show it in the list.
  if (status === 200 && data) {
    p.data = data;
    p.detail = {};
    if (document.getElementById("sk-list")) {
      const menus = skillFilterOptions();
      document.getElementById("sk-harness").innerHTML = menus.harness;
      document.getElementById("sk-source").innerHTML = menus.source;
      paintSkillRows();
    }
  }
  if (!current) return;
  if (status !== 200 || !data) {
    $("sk-new-create").textContent = "Create";
    $("sk-new-create").disabled = false;
    const err = $("sk-new-error");
    err.textContent = failText(status, data);
    err.hidden = false;
    return;
  }
  const open = data.skill ? `<a class="sk-button" href="#/skills?skill=${esc(encodeURIComponent(data.skill.path))}" id="sk-new-open">Open it</a>` : "";
  $("sk-new-form").innerHTML = `<div class="sheet-title">New skill</div>
    <p class="caption">Created ${esc(skillsPath(data.created))}</p>
    <div class="sheet-actions">${open}<button class="sk-button primary" type="button" id="sk-new-done">Done</button></div>`;
  $("sk-new-done").onclick = () => sheet.close();
  if ($("sk-new-open")) $("sk-new-open").onclick = () => sheet.close();
  $("sk-new-done").focus();
}

/* ---------------------------------------------------------------- writes */

const skillsOnRoute = () => /^#\/skills(\?|$)/.test(location.hash);
const skillsRoutePath = () => new URLSearchParams(location.hash.split("?")[1] || "").get("skill");

/* Link, Unlink or Link all, once its confirm is accepted. As the Mac
 * app's RootDetail does, a folder's own action goes back to the chip
 * list first, where its chip spins until the hub answers. */
async function skillsWrite(c) {
  const p = skillsPage, d = skillsShown();
  if (!c || !d) return;
  const skill = p.onDetail ? skillsRoutePath() : null;
  let action, body, paths;
  if (c.kind === "link_all") {
    paths = c.paths;
    action = "link_all";
    body = { harnesses: paths };
  } else {
    const root = d.harnesses.find((r) => r.path === c.path);
    if (!root) return;
    paths = [root.path];
    action = c.kind;
    // The state and its detail as the confirm described them: an empty
    // folder that fills with copies stays "absorb" but now needs a backup.
    body = { harness: root.path, expect: root.state, expect_detail: root.detail || "" };
  }
  if (skill) body.skill = skill;
  if (p.busy.size) return;
  p.writes = (p.writes || 0) + 1;
  p.confirm = null;
  if (c.kind !== "link_all") p.links = "";
  p.actionError = null;
  for (const x of paths) p.busy.add(x);
  skillsRepaint();
  const { status, data } = await apiPost(`/v1/app/skills/${action}`, body);
  for (const x of paths) p.busy.delete(x);
  if (status === 401) {
    if (skillsOnRoute()) renderGate();
    return;
  }
  if (status !== 200 || !data) {
    p.actionError = failText(status, data);
    // A failed change may still have moved something: read the list again.
    return skillsReload();
  }
  if (action === "link_all") p.summary = skillsSummary(data.result);
  skillsApply(data, data.skill);
  skillsRepaint();
}

/* SkillsModel.linkAll's summary caption. */
function skillsSummary(r) {
  const n = r.linked.length, parts = [];
  if (n > 0 || !r.failed.length) parts.push(`Linked ${n} ${n === 1 ? "harness" : "harnesses"}.`);
  if (r.failed.length) parts.push(r.failed.map((f) => `${f.name}: ${f.message}`).join("; "));
  if (r.skipped.length) {
    const names = r.skipped.map((s) => `${s.name} (${rootExplanation(s)})`).join(", ");
    parts.push(`${r.skipped.length} ${r.skipped.length === 1 ? "needs" : "need"} a look: ${names}`);
  }
  return parts.join(" ");
}

/* Takes a list payload read after a write as the page's own: the list,
 * and for the skill whose detail is open, its row and the folders. Any
 * read still in flight began before the write, so it is dropped. */
function skillsApply(list, skillRow) {
  const p = skillsPage, open = p.onDetail ? skillsRoutePath() : null;
  p.seq++;
  p.data = {
    machine: list.machine, home: list.home, shared_dir: list.shared_dir, harnesses: list.harnesses,
    skills: list.skills, plugin_skill_count: list.plugin_skill_count, catalog_error: list.catalog_error,
  };
  const cur = open && p.detail[open];
  // The reply's row is for the skill open when the write began; another
  // may be open now, so take that one's own row from the list.
  const row = skillRow && skillRow.path === open ? skillRow
    : open ? list.skills.find((s) => s.path === open) : null;
  p.detail = {};
  if (cur && row) {
    p.detail[open] = { ...cur, skill: row, harnesses: list.harnesses, shared_dir: list.shared_dir };
    p.headerData = p.detail[open];
  } else if (p.headerData) {
    p.headerData = { ...p.headerData, harnesses: list.harnesses, shared_dir: list.shared_dir };
  }
}

async function skillsReload() {
  const writes = skillsPage.writes;
  const { status, data } = await api("/v1/app/skills");
  // A write started meanwhile answers with newer states than this read.
  if (writes !== skillsPage.writes) return;
  if (status === 200 && data) skillsApply(data, null);
  skillsRepaint();
}

/* Paints whatever the Skills route shows now from the page's data,
 * keeping the popover as it is; another page is left alone. */
function skillsRepaint() {
  if (!skillsOnRoute()) return;
  const p = skillsPage, path = skillsRoutePath();
  if (path) {
    if (p.detail[path]) paintSkillDetail(p.detail[path]);
    else renderSkillDetail(path);
  } else if (p.data) {
    paintSkills();
  }
}

/* The page's own error card for the last failed write (the Mac app's
 * ErrorCard over the list). */
function skillsActionCard() {
  const e = skillsPage.actionError;
  return e ? `<div class="card sk-action-error"><p class="error" role="alert">${esc(e)}</p></div>` : "";
}

/* Skills that ship inside a plugin move to the Plugins page, but only
 * once the web app has one (app.js's PAGES); until then they stay here,
 * as the Mac app keeps them when its catalog is unknown. */
function skillsPluginsLive() {
  return typeof PAGES !== "undefined" && Object.prototype.hasOwnProperty.call(PAGES, "plugins");
}

function listedSkills(d) {
  return skillsPluginsLive() ? d.skills.filter((s) => !s.plugin) : d.skills;
}

function movedSkillCount(d) {
  return skillsPluginsLive() ? d.plugin_skill_count : 0;
}

/* SkillsModel.visibleSkills: harness, source and search, then your own
 * library first, then what harnesses and catalogs bring. */
function visibleSkills() {
  const p = skillsPage, needle = p.query.trim().toLowerCase();
  return listedSkills(p.data)
    .map((s, i) => [s, i])
    .filter(([s]) => (!p.harness || s.harnesses.includes(p.harness))
      && (!p.source || s.source === p.source)
      && (!needle || s.name.toLowerCase().includes(needle)
        || (s.description || "").toLowerCase().includes(needle) || s.root.toLowerCase().includes(needle)))
    .sort(([a, ia], [b, ib]) => ((SKILL_RANK[a.source] ?? 9) - (SKILL_RANK[b.source] ?? 9)) || ia - ib)
    .map(([s]) => s);
}

/* The harness and source menus, from what the list holds now. */
function skillFilterOptions() {
  const p = skillsPage, listed = listedSkills(p.data);
  // dropMissingFilters: a filter the list no longer offers would hide every row.
  if (p.harness && !listed.some((s) => s.harnesses.includes(p.harness))) p.harness = "";
  if (p.source && !listed.some((s) => s.source === p.source)) p.source = "";
  const harnesses = [...new Set(listed.flatMap((s) => s.harnesses))].sort();
  const sources = SKILL_SOURCES.filter(([id]) => listed.some((s) => s.source === id));
  return {
    harness: options([["", "All harnesses"], ...harnesses.map((h) => [h, Fmt.harnessName(h)])], p.harness),
    source: options([["", "All sources"], ...sources], p.source),
  };
}

function paintSkills() {
  const p = skillsPage, d = p.data, moved = movedSkillCount(d), menus = skillFilterOptions();
  const note = moved > 0 ? `<a class="sk-plugin-note" href="#/plugins"
      title="Open Plugins, where each plugin lists the skills it ships">${icon("plugins")}
      <span class="num">${moved === 1 ? "1 skill ships inside a plugin;" : `${moved} skills ship inside plugins;`}</span>
      <span class="go">${moved === 1 ? "see it under Plugins" : "see them under Plugins"}</span><span aria-hidden="true">→</span></a>` : "";
  document.getElementById("panel").innerHTML = `<div class="page">${skillsHeader(false)}
    <div class="stack">
      ${skillsActionCard()}
      <div class="filters">
        <select class="field" id="sk-harness" aria-label="Harness">${menus.harness}</select>
        <select class="field" id="sk-source" aria-label="Source">${menus.source}</select>
        <input class="field search" id="sk-search" type="search" placeholder="Filter skills"
          value="${esc(p.query)}" autocomplete="off" spellcheck="false">
        <span class="caption count num" id="sk-count"></span>
      </div>
      ${d.catalog_error ? `<p class="caption">Couldn't read the plugin catalog, so plugin skills stay listed here: ${esc(d.catalog_error)}</p>` : ""}
      ${note}
      <div id="sk-list"></div>
    </div></div>`;
  bindSkillsHeader();
  document.getElementById("sk-harness").onchange = (e) => { p.harness = e.target.value; paintSkillRows(); };
  document.getElementById("sk-source").onchange = (e) => { p.source = e.target.value; paintSkillRows(); };
  document.getElementById("sk-search").oninput = (e) => { p.query = e.target.value; paintSkillRows(); };
  paintSkillRows();
}

function paintSkillRows() {
  const p = skillsPage, d = p.data, skills = visibleSkills(), where = d.machine || "the hub";
  const listed = listedSkills(d);
  document.getElementById("sk-count").textContent = `${skills.length} of ${listed.length} skills`;
  const list = document.getElementById("sk-list");
  if (!skills.length) {
    const empty = !d.skills.length;
    const text = empty ? `No skills on ${where} yet.`
      : !listed.length ? `Every skill on ${where} ships inside a plugin.` : "No skills match.";
    list.innerHTML = `<div class="card sk-empty"><p class="empty">${esc(text)}</p>${empty
      ? '<div><button class="sk-button primary" id="sk-new-empty" type="button">New skill</button></div>' : ""}</div>`;
    const add = document.getElementById("sk-new-empty");
    if (add) add.onclick = () => openNewSkill();
    return;
  }
  const hidden = p.showsAll ? 0 : Math.max(0, skills.length - SKILL_FOLD);
  const shown = hidden ? skills.slice(0, SKILL_FOLD) : skills;
  const fold = skills.length > SKILL_FOLD
    ? `<button class="fold sk-fold" id="sk-fold" type="button" title="${p.showsAll ? `Show only the first ${SKILL_FOLD}` : "Show every skill"}">${
      p.showsAll ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</button>` : "";
  list.innerHTML = `<div class="sk-rows">${shown.map(skillRow).join("")}${fold}</div>`;
  const btn = document.getElementById("sk-fold");
  if (btn) btn.onclick = () => { p.showsAll = !p.showsAll; paintSkillRows(); };
}

/* The skills list's own rows: name, description and path on the left,
 * source badge, seen count and chevron on the right. */
function skillsSkeleton() {
  const row = (i) => `<div class="sk-row"><span class="sk-main">
    ${Sk.bar(100 - (i % 3) * 16, 14)}
    ${i % 3 === 2 ? "" : Sk.bar(`${56 - (i % 2) * 12}%`, 11)}
    ${Sk.bar(`${48 - (i % 3) * 8}%`, 10)}
  </span><span class="sk-side">${Sk.pill(56)}${Sk.num(20)}${Sk.bar(8, 16)}</span></div>`;
  return `<div class="sk-rows" role="status" aria-label="Loading">${Array.from({ length: 6 }, (_, i) => row(i)).join("")}</div>`;
}

function skillRow(s) {
  return `<a class="sk-row" href="#/skills?skill=${esc(encodeURIComponent(s.path))}" title="Open ${esc(s.name)}">
    <span class="sk-main">
      <span class="sk-name">${esc(s.name)}</span>
      ${s.description ? `<span class="sk-desc">${esc(s.description)}</span>` : ""}
      <span class="sk-path" title="${esc(s.path)}">${esc(skillsMiddle(skillsPath(s.path), 90))}</span>
    </span>
    <span class="sk-side"><span class="badge">${esc(skillSourceTitle(s.source))}</span>
      <span class="sk-seen num">${skillsSeenBy(s.harnesses)}</span>
      <span class="sk-chevron" aria-hidden="true">›</span></span>
  </a>`;
}

/* ---------------------------------------------------------------- detail */

async function renderSkillDetail(path) {
  const p = skillsPage, gen = routeGen, mine = ++p.seq;
  const cached = p.detail[path];
  if (p.lastDetail !== path) {
    p.lastDetail = path; p.fmShowsAll = false; p.treeShowsAll = false; p.open = new Set();
  }
  if (cached) paintSkillDetail(cached);
  else {
    document.getElementById("panel").innerHTML = `<div class="page">${skillsHeader(true)}${skillsBack()}
      <div class="stack" role="status" aria-label="Loading"><div class="card sk-head">
        <div class="sk-title">${Sk.bar(120, 18)}${Sk.pill(56)}</div>
        ${Sk.bar("52%", 11)}
        ${Sk.bar("72%", 12)}
        <div class="sk-chips">${Sk.pill(72, 26)}${Sk.pill(72, 26)}</div></div>
      <div class="card"><div class="skel-para">${Sk.linesInner(8)}</div></div></div></div>`;
    bindSkillsHeader();
  }
  const { status, data } = await api(`/v1/app/skills?skill=${encodeURIComponent(path)}`);
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) {
    const msg = failText(status, data);
    document.getElementById("panel").innerHTML = `<div class="page">${skillsHeader(false)}${skillsBack()}
      <div class="card"><p class="error">${esc(msg)}</p></div></div>`;
    bindSkillsHeader();
    return;
  }
  p.detail[path] = data;
  p.headerData = data;
  paintSkillDetail(data);
}

function skillsBack() {
  return `<a class="sk-back" href="#/skills">‹ Skills</a>`;
}

/* SkillsModel.chips: each harness folder and whether it loads the skill.
 * Only a shared skill can be reached by linking. */
function skillChips(skill, roots) {
  const seeing = new Set(skill.harnesses);
  if (skill.source !== "shared") return roots.filter((r) => seeing.has(r.name)).map((r) => [r, "linked"]);
  return roots.map((r) => [r, seeing.has(r.name) ? "linked" : rootLink(r) === "blocked" ? "blocked" : "notLinked"]);
}

/* SkillLinksBlock's note. */
function skillNote(skill) {
  const first = skill.harnesses[0] || "its harness";
  switch (skill.source) {
    case "shared": return "In the shared library. Every harness whose folder is linked loads it.";
    case "plugin": return `Shipped by a plugin, so only ${first} loads it.`;
    case "builtin": return `Built into ${first}.`;
    case "catalog": return `In the ${skill.root} catalog. Harnesses load it only once it is moved or linked into a skills folder.`;
    case "project": return "In a project's own skills folder.";
    default: return skill.harnesses.length
      ? `In ${skill.root}'s own folder, outside the shared library.`
      : `In ${skill.root}'s own folder, which no linked harness reads.`;
  }
}

function paintSkillDetail(d) {
  const p = skillsPage, s = d.skill, home = d.home;
  const chips = skillChips(s, d.harnesses).map(([r, link]) => skillsChip(r.name, link, { compact: true })).join("");
  const lock = d.editable
    ? `The web app shows skills read only. Edit ${s.name} in the Mac app, or its files on the hub.`
    : `Only skills that live under ~/.swe can be edited, so ${s.name} is read-only.`;
  const header = `<div class="card sk-head">
    <div class="sk-title"><span class="name">${esc(s.name)}</span><span class="badge">${esc(skillSourceTitle(s.source))}</span></div>
    <code class="sk-mono">${esc(skillsPath(s.path, home))}</code>
    ${s.description ? `<p class="sk-body muted-2">${esc(s.description)}</p>` : ""}
    <div class="sk-links-block"><p class="caption">${esc(skillNote(s))}</p>
      ${chips ? `<div class="sk-chips">${chips}</div>` : ""}</div>
  </div>`;
  let fm = "";
  if (d.skill_file) {
    const fields = d.frontmatter || [];
    const hidden = p.fmShowsAll ? 0 : Math.max(0, fields.length - DETAIL_FOLD);
    const shown = hidden ? fields.slice(0, DETAIL_FOLD) : fields;
    fm = `<div class="card">${skillsCardHead("Frontmatter", skillsPath(d.skill_file, home))}
      ${fields.length ? `<div class="sk-fields">${shown.map((f) =>
        `<span class="label">${esc(f.key)}</span><span class="value">${esc(f.value)}</span>`).join("")}</div>`
        : '<p class="caption">No frontmatter fields.</p>'}
      ${fields.length > DETAIL_FOLD ? `<button class="fold sk-fold" id="sk-fm-fold" type="button">${
        p.fmShowsAll ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</button>` : ""}
    </div>
    <div class="card">${skillsCardHead("SKILL.md", skillsPath(d.skill_file, home))}
      ${d.skill_md == null ? '<p class="caption">Can\'t preview this file.</p>'
        : `<pre class="sk-text">${esc(d.skill_md)}</pre>`}
    </div>`;
  }
  document.getElementById("panel").innerHTML = `<div class="page">${skillsHeader(false)}${skillsBack()}
    <div class="stack">
      ${skillsActionCard()}
      <div class="sk-lock">${skillsLockIcon()}<span>${esc(lock)}</span></div>
      ${header}${fm}${skillsContents(d)}
    </div></div>`;
  bindSkillsHeader();
  const fmFold = document.getElementById("sk-fm-fold");
  if (fmFold) fmFold.onclick = () => { p.fmShowsAll = !p.fmShowsAll; paintSkillDetail(d); };
  const treeFold = document.getElementById("sk-tree-fold");
  if (treeFold) treeFold.onclick = () => { p.treeShowsAll = !p.treeShowsAll; paintSkillDetail(d); };
  for (const row of document.querySelectorAll("[data-folder]")) {
    row.onclick = () => {
      const key = row.dataset.folder;
      if (p.open.has(key)) p.open.delete(key); else p.open.add(key);
      paintSkillDetail(d);
    };
  }
  for (const row of document.querySelectorAll("[data-file]")) {
    row.onclick = () => openSkillFile(d, row.dataset.file);
  }
}

function skillsCardHead(title, subtitle) {
  return `<div><div class="card-title">${esc(title)}</div><div class="card-subtitle sk-mono-sub">${esc(subtitle)}</div></div>`;
}

function skillsLockIcon() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>`;
}

/* PartsCard: a unit per section header and per top-level entry, folded
 * to three; an opened folder's files ride along with its row. */
function skillsContents(d) {
  const p = skillsPage;
  const units = d.tree.flatMap((sec) => [{ head: sec }, ...sec.entries.map((e) => ({ entry: e }))]);
  const hidden = p.treeShowsAll ? 0 : Math.max(0, units.length - DETAIL_FOLD);
  const shown = hidden ? units.slice(0, DETAIL_FOLD) : units;
  const fileRow = (e, child = false) => `<button class="sk-file${child ? " child" : ""}" type="button" data-file="${esc(e.preview || e.path)}">
      <span class="sk-ficon" aria-hidden="true">▤</span><span>${esc(e.name)}</span></button>`;
  const rows = shown.map((u, i) => {
    if (u.head) return `<div class="sk-section${i ? "" : " first"}">${esc(u.head.title)} · ${u.head.entries.length}</div>`;
    const e = u.entry;
    if (!e.folder) return fileRow(e);
    const open = p.open.has(e.path);
    return `<button class="sk-file folder" type="button" data-folder="${esc(e.path)}" aria-expanded="${open}">
        <span class="sk-twisty${open ? " open" : ""}" aria-hidden="true">›</span><span class="sk-ficon folder" aria-hidden="true">▰</span><span>${esc(e.name)}</span></button>
      ${open ? e.children.map((c) => fileRow(c, true)).join("") : ""}`;
  }).join("");
  const fold = units.length > DETAIL_FOLD ? `<button class="fold sk-fold" id="sk-tree-fold" type="button">${
    p.treeShowsAll ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</button>` : "";
  return `<div class="card">${skillsCardHead("Contents", skillsPath(d.skill.path, d.home))}
    ${units.length ? `<div class="sk-tree">${rows}</div>${fold}` : '<p class="caption">This folder is empty.</p>'}</div>`;
}

/* PrimitiveFileSheet, read only: the file in a large sheet over the page. */
async function openSkillFile(d, name) {
  const sheet = bodySheet("sk-sheet", "sheet sk-sheet");
  const path = d.skill.path + "/" + name;
  const head = (inner) => `<form method="dialog" class="sheet-body">
    <div class="sk-sheet-head"><div class="titles"><div class="card-title">${esc(name.split("/").pop())}</div>
      <div class="card-subtitle sk-mono-sub">${esc(skillsPath(path, d.home))}</div></div>
      <button class="sk-button" value="close">Close</button></div>${inner}</form>`;
  sheet.innerHTML = head('<p class="caption">Reading…</p>');
  // Each opening has its own token: the same file name in another skill,
  // opened after this one closed, must not take this read's answer.
  const token = String(++skillsPage.fileToken);
  sheet.dataset.token = token;
  if (!sheet.open) sheet.showModal();
  const { status, data } = await api(`/v1/app/skills?skill=${encodeURIComponent(d.skill.path)}&file=${encodeURIComponent(name)}`);
  // Closed, or another file opened while this one was read.
  if (!sheet.open || sheet.dataset.token !== token) return;
  if (status === 401) {
    sheet.close();
    if (skillsOnRoute()) renderGate();
    return;
  }
  let body;
  if (status !== 200 || !data) {
    body = `<p class="error">${esc(failText(status, data))}</p>`;
  } else if (data.outside) {
    body = '<p class="caption">This file links outside the skill\'s folder, so the web app doesn\'t open it.</p>';
  } else if (data.text == null) {
    body = '<p class="caption sk-cant">Can\'t preview this file.</p>';
  } else {
    body = `<pre class="sk-text">${esc(data.text)}</pre>`;
  }
  sheet.innerHTML = head(body);
}

registerPage("skills", renderSkills);
