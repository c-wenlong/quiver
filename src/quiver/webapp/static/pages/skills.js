/* Skills: a port of the Mac app's SkillsPage and SkillDetailView. Read
 * only: linking, unlinking, New skill and frontmatter edits stay in
 * `swe skills` and the Mac app. The list is #/skills; one skill is
 * #/skills?skill=<its folder>. Data: /v1/app/skills (pages/skills.py). */
"use strict";

const skillsPage = {
  data: null, seq: 0, query: "", harness: "", source: "", showsAll: false,
  links: null,            // null closed, "" the chip list, else a root's path
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
      ? "Reads the shared skills. Archived in swe, so swe init won't relink it if you unlink it."
      : "Reads the shared skills in ~/.quiver/skills.";
    case "create": return root.archived
      ? "No skills folder yet. Archived in swe, so swe init leaves it alone."
      : "No skills folder yet. Linking creates it.";
    case "relink": return d ? `Points somewhere else: ${d.replace("points at ", "")}.` : "Points somewhere else.";
    case "absorb": return d === "empty" ? "An empty folder. Linking replaces it."
      : `Holds only copies of shared skills (${d}). Linking backs it up and replaces it.`;
    case "keep": return `Holds skills that exist nowhere else (${d}). Move them into ~/.quiver/skills before linking.`;
    case "ignored": return d ? `Ignored: ${d}.` : "Ignored by swe init.";
    case "skipped": return "The harness isn't installed.";
    default: return d ? `${root.state}: ${d}` : root.state;
  }
}

/* `~/…` for a path under the hub's home. */
function skillsPath(path, home) {
  const h = home || (skillsPage.data && skillsPage.data.home) || "";
  return h && path.startsWith(h + "/") ? "~" + path.slice(h.length) : path;
}

/* Too long for a row: keep both ends, as .truncationMode(.middle) does. */
function skillsMiddle(text, max) {
  if (text.length <= max) return text;
  const head = Math.ceil((max - 1) / 2), tail = Math.floor((max - 1) / 2);
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`;
}

/* HarnessMarkView: the logo, a word after it when the logo is shared,
 * or the name alone when there is no logo. */
function skillsMark(h, cls = "logo") {
  const name = Fmt.harnessName(h);
  const has = (id) => Fmt.logo(id).startsWith("<img");
  let inner;
  if (h === "claude") inner = `${Fmt.logo("claude", cls)}<span>CLI</span>`;
  else if (h === "claude-desktop") inner = `${Fmt.logo("claude", cls)}<span>Desktop</span>`;
  else if (has(h)) inner = Fmt.logo(h, cls);
  else {
    const dash = h.lastIndexOf("-");
    const base = dash > 0 ? h.slice(0, dash) : "", suffix = dash > 0 ? h.slice(dash + 1) : "";
    inner = base && suffix && has(base) ? `${Fmt.logo(base, cls)}<span>${esc(suffix)}</span>` : `<span>${esc(name)}</span>`;
  }
  return `<span class="sk-mark" title="${esc(name)}" aria-label="${esc(name)}">${inner}</span>`;
}

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

function skillsChip(name, link, { compact = false, root = "" } = {}) {
  const tag = root ? "button" : "span";
  const data = root ? ` data-root="${esc(root)}" type="button"` : "";
  return `<${tag} class="sk-chip ${link}${compact ? " compact" : ""}"${data}
    title="${esc(Fmt.harnessName(name))}: ${LINK_TITLE[link].toLowerCase()}">${skillsLinkDot(link)}${skillsMark(name, compact ? "logo logo-sm" : "logo")}</${tag}>`;
}

/* ------------------------------------------------------------------ list */

function renderSkills() {
  // The Harness links popover never stays open across a page change.
  skillsPage.links = null;
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
      <div class="card"><p class="empty">Reading skills…</p></div></div>`;
    bindSkillsHeader();
  } else {
    paintSkills();
  }
  const { status, data } = await api("/v1/app/skills");
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (status !== 200 || !data) return pageError(skillsHeader(false), status, data, renderSkills);
  p.data = data;
  p.detail = {};
  paintSkills();
}

function skillsHeader(busy) {
  // The payload of what is on screen: a skill's own on its detail page,
  // so the header and the chips read the same link states.
  const p = skillsPage, d = (p.onDetail && p.headerData) || p.data || p.headerData;
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
  return pageHeader("Skills", `Every skill on ${where} outside a plugin, and which harnesses load it.`,
    `${links}<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
}

/* HarnessLinksPanel, and RootDetail in its place once a chip is picked. */
function skillsLinksPanel(d) {
  const roots = d.harnesses;
  const root = skillsPage.links ? roots.find((r) => r.path === skillsPage.links) : null;
  if (root) {
    const link = rootLink(root);
    return `<div class="sk-popover" role="dialog" aria-label="${esc(Fmt.harnessName(root.name))}">
      <button class="sk-back-small" id="sk-links-back" type="button">‹ Harness links</button>
      <div class="sk-root-head">${skillsMark(root.name)}<span class="name">${esc(Fmt.harnessName(root.name))}</span>
        <span class="badge">${LINK_TITLE[link]}</span></div>
      <code class="sk-mono">${esc(skillsPath(root.path, d.home))}</code>
      <p class="sk-body">${esc(rootExplanation(root))}</p>
      <p class="caption">Link or unlink it with <code>swe skills link</code> on the hub, or in the Mac app.</p>
    </div>`;
  }
  const body = roots.length
    ? `<div class="sk-chips">${roots.map((r) => skillsChip(r.name, rootLink(r), { root: r.path })).join("")}</div>
       <div class="sk-legend">${["linked", "notLinked", "blocked"].map((l) =>
         `<span>${skillsLinkDot(l)}${LINK_TITLE[l]}</span>`).join("")}<span class="muted">Click one for its folder.</span></div>`
    : `<div class="sk-none"><span>No harness skills folders found. Register a harness first.</span>
       <a class="sk-button" href="#/harnesses">Open Harnesses</a></div>`;
  return `<div class="sk-popover" role="dialog" aria-label="Harness links">
    <div><div class="card-title">Harness links</div>
      <p class="caption">Each harness reads one skills folder. A linked folder points at ${esc(skillsPath(d.shared_dir, d.home))}, so every shared skill reaches it.</p></div>
    ${body}
  </div>`;
}

function bindSkillsHeader() {
  const p = skillsPage;
  bindRefresh(renderSkills, () => { p.data = null; p.detail = {}; });
  const repaintHeader = () => {
    const head = document.querySelector("#panel .page-header");
    if (!head) return;
    head.outerHTML = skillsHeader(false);
    bindSkillsHeader();
  };
  const btn = document.getElementById("sk-links");
  if (btn) btn.onclick = (e) => { e.stopPropagation(); p.links = p.links === null ? "" : null; repaintHeader(); };
  const back = document.getElementById("sk-links-back");
  if (back) back.onclick = (e) => { e.stopPropagation(); p.links = ""; repaintHeader(); };
  for (const chip of document.querySelectorAll(".sk-popover [data-root]")) {
    chip.onclick = (e) => { e.stopPropagation(); p.links = chip.dataset.root; repaintHeader(); };
  }
  const pop = document.querySelector(".sk-popover");
  if (pop) pop.onclick = (e) => e.stopPropagation();
  if (!p.outsideBound) {
    p.outsideBound = true;
    const close = () => {
      if (p.links === null) return;
      p.links = null;
      repaintHeader();
    };
    document.addEventListener("click", () => { if (document.querySelector(".sk-links-wrap")) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.querySelector(".sk-popover")) close(); });
  }
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

function paintSkills() {
  const p = skillsPage, d = p.data, listed = listedSkills(d), moved = movedSkillCount(d);
  // dropMissingFilters: a filter the list no longer offers would hide every row.
  if (p.harness && !listed.some((s) => s.harnesses.includes(p.harness))) p.harness = "";
  if (p.source && !listed.some((s) => s.source === p.source)) p.source = "";
  const harnesses = [...new Set(listed.flatMap((s) => s.harnesses))].sort();
  const sources = SKILL_SOURCES.filter(([id]) => listed.some((s) => s.source === id));
  const note = moved > 0 ? `<a class="sk-plugin-note" href="#/plugins"
      title="Open Plugins, where each plugin lists the skills it ships">${icon("plugins")}
      <span class="num">${moved === 1 ? "1 skill ships inside a plugin;" : `${moved} skills ship inside plugins;`}</span>
      <span class="go">${moved === 1 ? "see it under Plugins" : "see them under Plugins"}</span><span aria-hidden="true">→</span></a>` : "";
  document.getElementById("panel").innerHTML = `<div class="page">${skillsHeader(false)}
    <div class="stack">
      <div class="filters">
        <select class="field" id="sk-harness" aria-label="Harness">${options(
          [["", "All harnesses"], ...harnesses.map((h) => [h, Fmt.harnessName(h)])], p.harness)}</select>
        <select class="field" id="sk-source" aria-label="Source">${options([["", "All sources"], ...sources], p.source)}</select>
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
    list.innerHTML = `<div class="card"><p class="empty">${esc(text)}</p>${empty
      ? '<p class="caption">Create one with <code>swe skills new &lt;name&gt;</code> on the hub.</p>' : ""}</div>`;
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
      <div class="card"><p class="empty">Reading the skill…</p></div></div>`;
    bindSkillsHeader();
  }
  const { status, data } = await api(`/v1/app/skills?skill=${encodeURIComponent(path)}`);
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
  if (status !== 200 || !data) {
    const msg = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then try again.");
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
    : `Only skills that live under ~/.quiver can be edited, so ${s.name} is read-only.`;
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
      <div class="sk-lock">${skillsLockIcon()}<span>${esc(lock)}</span></div>
      ${header}${fm}${skillsContents(d)}
    </div>
    <dialog class="sheet sk-sheet" id="sk-sheet"></dialog></div>`;
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
  const sheet = document.getElementById("sk-sheet");
  const path = d.skill.path + "/" + name;
  const head = (inner) => `<form method="dialog" class="sheet-body">
    <div class="sk-sheet-head"><div class="titles"><div class="card-title">${esc(name.split("/").pop())}</div>
      <div class="card-subtitle sk-mono-sub">${esc(skillsPath(path, d.home))}</div></div>
      <button class="sk-button" value="close">Close</button></div>${inner}</form>`;
  sheet.innerHTML = head('<p class="caption">Reading…</p>');
  sheet.dataset.file = name;
  if (!sheet.open) sheet.showModal();
  const { status, data } = await api(`/v1/app/skills?skill=${encodeURIComponent(d.skill.path)}&file=${encodeURIComponent(name)}`);
  // Closed, or another file picked while this one was read.
  if (!sheet.isConnected || !sheet.open || sheet.dataset.file !== name) return;
  let body;
  if (status !== 200 || !data) {
    body = `<p class="error">${esc((data && data.error && data.error.message) || `The hub answered ${status}.`)}</p>`;
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
