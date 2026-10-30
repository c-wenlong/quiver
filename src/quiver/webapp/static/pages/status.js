/* Service status: a port of the Mac app's ServiceStatusPage. One card per
 * tracked status page in two columns, grouped as the app groups them, each
 * folded to its health line with a "Show details" row that opens the
 * summary, affected components and open incidents. "Add service" opens
 * the catalogue sheet (the app's StatusCatalogueSheet): search, group
 * chips and one-click adds tracked in the hub's own store, so every
 * browser sees the same pages. A custom card's Remove untracks it.
 */
"use strict";

const statusPage = { data: null, loading: false, gen: 0, expanded: new Set(), loadedAt: 0, timer: null,
  catalogue: null, removing: null, removeError: "" };

const STATUS_ICONS = {
  open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  arrow: '<path d="M7 17 17 7M8 7h9v9"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  warn: '<path d="M12 4 2.5 20h19zM12 10v4M12 17.5v.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12 4.5 4.5L19 7"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
};
const statusIcon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${STATUS_ICONS[name]}</svg>`;

// The logos the Mac app draws for each page (StatusService.logoID), as
// shipped beside the page: colour ones as they are, the rest in the text colour.
const STATUS_LOGOS = {
  claude: "harness-claude-color.svg", openai: "harness-openai.svg", copilot: "harness-copilot.svg",
  cursor: "harness-cursor.svg", gemini: "harness-gemini-color.svg", devin: "harness-devin.svg",
  npm: "harness-npm.svg", vercel: "harness-vercel.svg", cloudflare: "harness-cloudflare-color.svg",
  // The catalogue's entries, each the mark its logoID resolves to.
  groq: "harness-groq.svg", perplexity: "harness-perplexity-color.svg",
  fireworks: "harness-fireworks-color.svg", cerebras: "harness-cerebras.svg",
  cohere: "harness-cohere-color.svg", baseten: "harness-baseten.svg",
  minimax: "harness-minimax-color.svg", elevenlabs: "harness-elevenlabs.svg",
  lambda: "harness-lambda.svg", assemblyai: "harness-assemblyai.svg",
  docker: "harness-docker.svg", tailscale: "harness-tailscale.svg",
  sentry: "harness-sentry.svg", datadog: "harness-datadog.svg",
  supabase: "harness-supabase.svg", digitalocean: "harness-digitalocean.svg",
  render: "harness-render.svg", upstash: "harness-upstash.svg",
  convex: "harness-convex.svg", linear: "harness-linear.svg",
  notion: "harness-notion.svg", twilio: "harness-twilio.svg", clerk: "harness-clerk.svg",
  "mistral-vibe": "harness-mistral-vibe-color.svg", kimi: "harness-kimi.svg",
  nebius: "harness-nebius.svg",
  stability: "harness-stability.svg", ai21: "harness-ai21.svg", sambanova: "harness-sambanova.svg",
  poe: "harness-poe.svg", writer: "harness-writer.svg", cartesia: "harness-cartesia-color.svg",
  deepgram: "harness-deepgram.svg", droid: "harness-droid.svg", windsurf: "harness-windsurf.svg",
  replit: "harness-replit.svg", sourcegraph: "harness-sourcegraph-color.svg",
  augment: "harness-augment.svg", warp: "harness-warp.svg", manus: "harness-manus.svg",
  lovable: "harness-lovable.svg", blackbox: "harness-blackbox.svg", kilo: "harness-kilo.svg",
  ona: "harness-ona.svg", e2b: "harness-e2b.svg", browserbase: "harness-browserbase-color.svg",
  tavily: "harness-tavily.svg", runloop: "harness-runloop.svg", arcade: "harness-arcade-color.svg",
  morph: "harness-morph.svg", supermemory: "harness-supermemory.svg",
  pypi: "harness-pypi.svg", crates: "harness-crates.svg", rubygems: "harness-rubygems.svg",
  maven: "harness-maven.svg", bitbucket: "harness-bitbucket.svg", circleci: "harness-circleci.svg",
  buildkite: "harness-buildkite.svg", travisci: "harness-travisci.svg", netlify: "harness-netlify.svg",
  flyio: "harness-flyio.svg", linode: "harness-linode.svg", expo: "harness-expo.svg",
  mongodb: "harness-mongodb.svg", pinecone: "harness-pinecone.svg", prisma: "harness-prisma.svg",
  sanity: "harness-sanity.svg", contentful: "harness-contentful.svg", temporal: "harness-temporal.svg",
  inngest: "harness-inngest.svg", astronomer: "harness-astronomer.svg", grafana: "harness-grafana.svg",
  newrelic: "harness-newrelic.svg", honeycomb: "harness-honeycomb-color.svg",
  incidentio: "harness-incidentio.svg", launchdarkly: "harness-launchdarkly.svg",
  statsig: "harness-statsig-color.svg", snyk: "harness-snyk.svg", codecov: "harness-codecov.svg",
  postman: "harness-postman.svg", ngrok: "harness-ngrok.svg", hashicorp: "harness-hashicorp.svg",
  figma: "harness-figma.svg", atlassian: "harness-atlassian.svg", workos: "harness-workos-color.svg",
  mailgun: "harness-mailgun.svg", discord: "harness-discord.svg", revenuecat: "harness-revenuecat.svg",
  nvidia: "harness-nvidia-color.svg", anyscale: "harness-anyscale.svg",
  bolt: "harness-bolt.svg", v0: "harness-v0.svg",
  snowflake: "harness-snowflake.svg", clickhouse: "harness-clickhouse.svg",
  cockroach: "harness-cockroach.svg", planetscale: "harness-planetscale.svg",
  semaphore: "harness-semaphore.svg", sonatype: "harness-sonatype.svg",
  langfuse: "harness-langfuse.svg", braintrust: "harness-braintrust.svg",
  prefect: "harness-prefect.svg",
  zapier: "harness-zapier-color.svg", make: "harness-make.svg", loom: "harness-loom.svg",
};
/* The logo cell: the shipped mark for a known id, else the service's
 * initial on a tile, so rows with no mark still line up (CatalogueRow
 * keeps the square empty; a letter says a little more). */
function statusLogo(id, name = "") {
  const file = STATUS_LOGOS[id];
  if (!file) {
    const letter = (name || "?").trim()[0] || "?";
    return `<span class="logo status-glyph" aria-hidden="true">${esc(letter.toUpperCase())}</span>`;
  }
  const mono = file.endsWith("-color.svg") ? "" : " mono";
  return `<img class="logo${mono}" src="/${file}" alt="">`;
}


/* .dateTime.day().month(.abbreviated).hour().minute(): "Oct 7, 1:25 PM". */
function statusWhen(epoch) {
  return new Date(epoch * 1000).toLocaleString("en-US",
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

async function renderStatus(_id, force = false) {
  const gen = routeGen;
  const p = statusPage;
  const mine = ++p.gen;
  startStatusClock();
  // Show the last readings at once: what this page already holds, else the
  // hub's cache, which costs no request to any status page.
  if (p.data) {
    paintStatus();
  } else {
    document.getElementById("panel").innerHTML = `<div class="page">${statusHeader()}
      <div class="card"><p class="empty">Reading the status pages…</p></div></div>`;
    const { status, data } = await api("/v1/app/status?cached=1");
    if (gen !== routeGen || mine !== p.gen) return;
    if (status === 401) return statusGate();
    if (status === 200 && data) statusKeep(data);
  }
  p.loading = true;
  if (p.data) paintStatus();
  const { status, data } = await api(`/v1/app/status${force ? "?force=1" : ""}`);
  if (gen !== routeGen || mine !== p.gen) return;
  p.loading = false;
  if (status === 401) return statusGate();
  if (status !== 200 || !data) {
    if (p.data) {
      // Keep the cards; say why they did not update.
      paintStatus(statusError(status, data));
      return;
    }
    return pageError(statusHeader(), status, data, () => renderStatus("status", true));
  }
  statusKeep(data);
  p.loadedAt = Date.now();
  paintStatus();
}

/* A payload, and when it arrived by this browser's clock. */
function statusKeep(data) {
  statusPage.data = data;
  statusPage.receivedAt = Date.now() / 1000;
}

/* A revoked credential drops the cards, so the clock's next repaint can't
 * put them back over the pairing steps. */
function statusGate() {
  statusPage.data = null;
  statusPage.loading = false;
  // A Track or Untrack reply can land after the browser moved on, and
  // the gate would replace whatever page shows now.
  if (!statusOnPage()) return;
  renderGate();
}

/* A card's reading, or none once it is older than an hour, as the hub
 * rules (STALE_AFTER). Cards kept while the hub can't be reached age on:
 * the hub's clock at the answer plus the time this browser has waited
 * since, so a clock difference between the two never matters. */
const STATUS_STALE_S = 3600;
function statusView(s) {
  const d = statusPage.data;
  if (!s.health || s.checked_at == null || d.server_time == null) return s;
  const now = d.server_time + (Date.now() / 1000 - (statusPage.receivedAt || Date.now() / 1000));
  if (now - s.checked_at <= STATUS_STALE_S) return s;
  return { ...s, health: null, health_title: null, summary: null, components: [], incidents: [], stale: false };
}

function statusError(status, data) {
  return failText(status, data, "then refresh");
}

function statusHeader() {
  const p = statusPage, d = p.data;
  const checked = d && d.checked_at ? `<span class="caption muted">Checked ${esc(Fmt.relative(d.checked_at))}</span>` : "";
  return pageHeader("Service status", "Live status from the official status pages of the services your harnesses use.",
    `${checked}<button class="status-add" id="status-add">${statusIcon("plus")}<span>Add service</span></button>
    <button class="icon-button${p.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh"
      title="Check every status page now"${p.loading ? " disabled" : ""}>${icon("rescan")}</button>`);
}

function paintStatus(error = "") {
  const p = statusPage, d = p.data;
  const groups = (d.groups || []).map((g) => `<section class="status-group">
      <h2 class="status-group-title">${esc(g.title)}</h2>
      <div class="status-grid">${g.services.map(statusCard).join("")}</div>
    </section>`).join("");
  document.getElementById("panel").innerHTML = `<div class="page">${statusHeader()}
    <div class="stack status-page">
      ${error ? `<div class="card"><p class="error">${esc(error)}</p></div>` : ""}
      ${groups}
      <p class="caption muted">Read from each provider's public status page, at most every five minutes. Gemini comes from Google Cloud's status feed, which covers the Gemini API. Pages you add are checked the same way.</p>
    </div></div>`;
  const refresh = document.getElementById("page-refresh");
  if (refresh) refresh.onclick = () => { if (!p.loading) renderStatus("status", true); };
  const add = document.getElementById("status-add");
  if (add) add.onclick = () => statusOpenCatalogue();
  for (const row of document.querySelectorAll("[data-status-expand]")) {
    row.onclick = () => {
      const id = row.dataset.statusExpand;
      if (p.expanded.has(id)) p.expanded.delete(id); else p.expanded.add(id);
      paintStatus(error);
      const again = document.querySelector(`[data-status-expand="${CSS.escape(id)}"]`);
      if (again) again.focus();
    };
  }
  for (const btn of document.querySelectorAll("[data-status-remove]")) {
    btn.onclick = () => {
      p.removing = btn.dataset.statusRemove;
      p.removeError = "";
      paintStatus(error);
      const go = document.querySelector("[data-status-remove-go]");
      if (go) go.focus();
    };
  }
  for (const btn of document.querySelectorAll("[data-status-remove-cancel]")) {
    btn.onclick = () => { p.removing = null; p.removeError = ""; paintStatus(error); };
  }
  for (const btn of document.querySelectorAll("[data-status-remove-go]")) {
    btn.onclick = () => statusUntrack(btn.dataset.statusRemoveGo);
  }
}

/* What the folded row can open. The summary only counts when the card
 * would show it (not on an all-clear reading). */
function statusSummary(s) {
  return s.summary && s.health && s.health !== "operational" ? s.summary : "";
}

function statusDetailNote(s) {
  const parts = [];
  const n = s.incidents.length, c = s.components.length;
  if (n) parts.push(n === 1 ? "1 incident" : `${n} incidents`);
  if (c) parts.push(c === 1 ? "1 component" : `${c} components`);
  if (statusSummary(s)) parts.push("summary");
  return parts.join(" · ");
}

const COMPONENT_LIMIT = 5, INCIDENT_LIMIT = 3;

function statusCard(raw) {
  const p = statusPage;
  const s = statusView(raw);
  const expanded = p.expanded.has(s.id);
  const note = statusDetailNote(s);
  const label = s.health_title || (p.loading && s.checked_at == null ? "Checking…" : "Unknown");
  // A page the user added carries Remove, as the Mac's ellipsis menu does;
  // built-ins cannot be removed. The confirm unfolds in the card.
  const remove = s.custom
    ? `<button class="status-open" data-status-remove="${esc(s.id)}" title="Stop tracking ${esc(s.name)}"
        aria-label="Stop tracking ${esc(s.name)}">${statusIcon("more")}</button>` : "";
  const confirming = p.removing === s.id ? `<div class="status-confirm" role="group" aria-label="Stop tracking ${esc(s.name)}?">
      <span>Stop tracking ${esc(s.name)}?</span>
      <span class="status-confirm-buttons">
        <button class="status-confirm-button" data-status-remove-cancel>Cancel</button>
        <button class="status-confirm-button danger" data-status-remove-go="${esc(s.id)}">Stop tracking</button>
      </span></div>` : "";
  return `<article class="card status-card">
    <div class="status-body">
      <div class="status-head">${statusLogo(s.logo, s.name)}<span class="status-name">${esc(s.name)}</span>
        ${safeHref(s.page_url) ? `<a class="status-open" href="${safeHref(s.page_url)}" target="_blank" rel="noopener noreferrer"
          title="${esc(s.page_url)}" aria-label="Open the ${esc(s.name)} status page">${statusIcon("open")}</a>` : ""}${remove}</div>
      <div class="status-health-block">
        <div class="status-health"><span class="health-dot health-${esc(s.health || "unknown")}" aria-hidden="true"></span>
          <span class="${s.health ? "" : "muted"}">${esc(label)}</span></div>
        ${s.note ? `<div class="caption muted">${esc(s.note)}</div>` : ""}
      </div>
      ${confirming}
      ${p.removeError && confirming ? `<div class="status-problem caption">${statusIcon("warn")}<span>${esc(p.removeError)}</span></div>` : ""}
      ${expanded ? statusDetails(s) : ""}
      ${statusProblem(s)}
    </div>
    ${note ? `<button class="status-expand" data-status-expand="${esc(s.id)}" aria-expanded="${expanded}"
        title="${expanded ? "Show only the health line" : "Show the summary, affected components and incidents"}">
        ${statusIcon(expanded ? "up" : "down")}<span>${expanded ? "Show less" : "Show details"}</span>
        <span class="muted">· ${esc(note)}</span></button>` : ""}
  </article>`;
}

/* True while the status page is the visible one, so a slow POST landing
 * after the browser moved on cannot paint cards over another page. */
function statusOnPage() {
  return currentRoute() === "status";
}

/* The card's Remove, confirmed inline. The hub's answer is a fresh
 * payload, so the card list repaints without it. */
async function statusUntrack(id) {
  const p = statusPage;
  // Bump the generation like renderStatus does: a list read still in
  // flight began before this write and must not put the card back. The
  // same stamp orders two writes, so only the latest reply is applied.
  const mine = ++p.gen;
  const { status, data } = await apiPost("/v1/app/status/untrack", { id });
  if (status === 401) return statusGate();
  if (status === 200 && data) {
    if (mine !== p.gen) return;
    p.removing = null;
    p.removeError = "";
    p.expanded.delete(id);
    statusKeep(data);
    if (statusOnPage()) paintStatus();
    return;
  }
  if (!statusOnPage()) return;
  p.removeError = statusError(status, data);
  paintStatus();
}

function statusDetails(s) {
  let out = "";
  const summary = statusSummary(s);
  if (summary) out += `<div class="caption">${esc(summary)}</div>`;
  if (s.components.length) {
    out += `<div class="status-components">${s.components.slice(0, COMPONENT_LIMIT).map((c) => `
      <div class="status-component"><span class="component-dot component-${esc(c.state)}" aria-hidden="true"></span>
        <span class="status-component-name" title="${esc(c.name)}">${esc(c.name)}</span>
        <span class="status-component-state">${esc(c.state_title)}</span></div>`).join("")}
      ${s.components.length > COMPONENT_LIMIT
        ? `<div class="caption muted">And ${s.components.length - COMPONENT_LIMIT} more</div>` : ""}</div>`;
  }
  if (s.incidents.length) {
    out += `<div class="status-incidents">${s.incidents.slice(0, INCIDENT_LIMIT).map(statusIncident).join("")}
      ${s.incidents.length > INCIDENT_LIMIT && safeHref(s.page_url)
        ? `<a class="status-more" href="${safeHref(s.page_url)}" target="_blank" rel="noopener noreferrer">${
          s.incidents.length - INCIDENT_LIMIT} more on the status page</a>` : ""}</div>`;
  }
  return out;
}

function statusIncident(i) {
  const meta = [];
  if (i.stage) meta.push(i.stage);
  if (i.started_at != null) meta.push(`${i.maintenance ? "began" : "started"} ${statusWhen(i.started_at)}`);
  if (i.updated_at != null) meta.push(`updated ${Fmt.relative(i.updated_at)}`);
  const link = safeHref(i.url) ? `<a class="status-incident-link" href="${safeHref(i.url)}" target="_blank" rel="noopener noreferrer"
      title="${esc(i.url)}" aria-label="Open incident">${statusIcon("arrow")}</a>` : "";
  return `<div class="status-incident">
    <div class="status-incident-name">${esc(i.name)}${link}</div>
    ${meta.length ? `<div class="caption">${esc(meta.join(" · "))}</div>` : ""}
    ${i.latest_update ? `<div class="caption status-update">${esc(statusPlain(i.latest_update))}</div>` : ""}
  </div>`;
}

/* Statuspage updates may carry <br /> tags (GitHub's do); shown as text,
 * they become line breaks rather than markup. */
function statusPlain(text) {
  return text.replace(/(<br\s*\/?>\s*)+/gi, "\n");
}

/* Why the reading is missing or old. The time of a good reading is in the
 * page header, once. */
function statusProblem(s) {
  if (!s.problem) return "";
  let text = s.problem;
  if (s.problem_until != null) {
    text += ` ${new Date(s.problem_until * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
  }
  if (s.stale && s.checked_at != null) text += `. Showing the reading from ${Fmt.relative(s.checked_at)}.`;
  return `<div class="status-problem caption">${statusIcon("warn")}<span>${esc(text)}</span></div>`;
}

/* ------------------------------------------------------- the catalogue */

const CAT_GROUPS = [[null, "All"], ["modelProviders", "Model providers"],
  ["agentPlatforms", "Agent platforms"], ["cloud", "Cloud & network"], ["data", "Data"],
  ["devtools", "Dev tools & quality"], ["registries", "Registries"],
  ["observability", "Observability"], ["services", "Services & productivity"]];
const CAT_TITLES = Object.fromEntries(CAT_GROUPS.slice(1));

/* The sheet lives on document.body, so a page repaint behind it does not
 * close it (the app's sheet stays up while adds land). */
function statusCatalogueDialog() {
  let dlg = document.getElementById("status-catalogue");
  if (dlg) return dlg;
  dlg = document.createElement("dialog");
  dlg.id = "status-catalogue";
  dlg.className = "sheet status-catalogue";
  dlg.setAttribute("aria-labelledby", "status-catalogue-title");
  dlg.addEventListener("close", () => { statusPage.catalogue = null; dlg.replaceChildren(); });
  document.body.appendChild(dlg);
  // A sheet belongs to the status page: leaving it closes the sheet.
  window.addEventListener("hashchange", () => {
    if (currentRoute() !== "status" && dlg.open) dlg.close();
  });
  return dlg;
}

async function statusOpenCatalogue() {
  const dlg = statusCatalogueDialog();
  const fresh = !statusPage.catalogue;
  if (fresh) statusPage.catalogue = { data: null, query: "", group: null, adding: new Set(), error: "" };
  statusPaintCatalogue();
  if (!dlg.open) dlg.showModal();
  const search = dlg.querySelector("#status-cat-search");
  if (search) search.focus();
  if (fresh || !statusPage.catalogue.data) {
    const { status, data } = await api("/v1/app/status?view=catalogue");
    const c = statusPage.catalogue;
    if (!c || !dlg.open) return;
    if (status === 401) { dlg.close(); return statusGate(); }
    if (status === 200 && data) { c.data = data; c.error = ""; }
    else c.error = statusError(status, data);
    statusPaintCatalogue(true);
  }
}

/* StatusCatalogue.filter, in the browser: the chip pick plus a search on
 * the name and the feed's host. */
function statusCatShown(c) {
  const entries = (c.data && c.data.entries) || [];
  const needle = c.query.trim().toLowerCase();
  return entries.filter((e) => (c.group == null || e.group === c.group)
    && (!needle || e.name.toLowerCase().includes(needle) || e.host.toLowerCase().includes(needle)));
}

function statusPaintCatalogue(keepFocus = false) {
  const dlg = document.getElementById("status-catalogue");
  const c = statusPage.catalogue;
  if (!dlg || !c) return;
  const search = dlg.querySelector("#status-cat-search");
  const caret = keepFocus && document.activeElement === search ? search.selectionStart : null;
  const chips = `<div class="tabs table status-cat-tabs" role="tablist">${CAT_GROUPS.map(([id, label]) =>
    `<button type="button" class="tab${c.group === id ? " selected" : ""}" role="tab" aria-selected="${c.group === id}"
      data-status-cat-group="${id == null ? "" : id}">${label}</button>`).join("")}</div>`;
  let body;
  if (!c.data && !c.error) {
    body = `<p class="empty status-cat-empty">Reading the catalogue…</p>`;
  } else {
    const shown = statusCatShown(c);
    const total = c.data ? c.data.entries.length : 0;
    const rows = shown.length ? CAT_GROUPS.slice(1).map(([gid]) => {
      const members = shown.filter((e) => e.group === gid);
      if (!members.length) return "";
      return `${c.group == null ? `<div class="status-cat-heading caption muted">${esc(CAT_TITLES[gid])}</div>` : ""}
        <div class="status-cat-grid">${members.map(statusCatRow).join("")}</div>`;
    }).join("") : `<p class="empty status-cat-empty">Nothing matches.</p>`;
    body = `<div class="status-cat-list">${rows}</div>
      <div class="sheet-actions status-cat-foot">
        <span class="caption muted num">${shown.length} of ${total}</span><span class="status-cat-spacer"></span>
        <button class="status-confirm-button" value="close">Done</button>
      </div>`;
  }
  dlg.innerHTML = `<div class="sheet-body status-cat-body">
    <div class="sheet-title" id="status-catalogue-title">Service catalogue</div>
    <input class="field search" id="status-cat-search" type="search" placeholder="Search status pages"
      value="${esc(c.query)}" autocomplete="off" spellcheck="false" aria-label="Search status pages">
    ${chips}${c.error ? `<p class="error" role="alert">${esc(c.error)}</p>` : ""}${body}
  </div>`;
  statusBindCatalogue(dlg, c);
  if (caret != null) {
    const again = dlg.querySelector("#status-cat-search");
    if (again) {
      again.focus();
      try { again.setSelectionRange(caret, caret); } catch { /* not text */ }
    }
  }
}

function statusCatRow(e) {
  const c = statusPage.catalogue;
  const adding = c.adding.has(e.id);
  const side = e.added
    ? `<span class="status-cat-added">${statusIcon("check")}<span>Added</span></span>`
    : `<button type="button" class="status-cat-add" data-status-cat-add="${esc(e.id)}"${adding ? " disabled" : ""}>${
        adding ? "Adding…" : "Add"}</button>`;
  return `<div class="status-cat-row">${statusLogo(e.logo, e.name)}
    <div class="status-cat-main"><div class="status-cat-name">${esc(e.name)}</div>
      <div class="caption muted status-cat-host">${esc(e.host)}</div></div>${side}</div>`;
}

function statusBindCatalogue(dlg, c) {
  const done = dlg.querySelector('[value="close"]');
  if (done) done.onclick = () => dlg.close();
  const search = dlg.querySelector("#status-cat-search");
  if (search) search.oninput = () => { c.query = search.value; statusPaintCatalogue(true); };
  for (const tab of dlg.querySelectorAll("[data-status-cat-group]")) {
    tab.onclick = () => {
      const next = tab.dataset.statusCatGroup || null;
      if (next === c.group) return;
      c.group = next;
      statusPaintCatalogue();
      const again = dlg.querySelector("#status-cat-search");
      if (again) again.focus();
    };
  }
  for (const btn of dlg.querySelectorAll("[data-status-cat-add]")) {
    btn.onclick = () => statusTrack(btn.dataset.statusCatAdd);
  }
}

/* A row's Add: POST /v1/app/status/track carries the fresh payload, so
 * the page behind repaints and the row reads Added. */
async function statusTrack(id) {
  const c = statusPage.catalogue;
  const dlg = document.getElementById("status-catalogue");
  if (!c || c.adding.has(id)) return;
  c.adding.add(id);
  c.error = "";
  statusPaintCatalogue();
  // Bump the generation as in statusUntrack, before the request goes
  // out: stale list reads and earlier write replies must not win.
  const mine = ++statusPage.gen;
  const { status, data } = await apiPost("/v1/app/status/track", { id });
  // The page's truth lands whether the sheet stayed open or not: the
  // service exists once the hub took the write.
  if (status === 401) {
    if (dlg && dlg.open) dlg.close();
    return statusGate();
  }
  if (status === 200 && data && mine === statusPage.gen) {
    statusKeep(data);
    if (statusOnPage()) paintStatus();
  }
  if (statusPage.catalogue !== c) return;
  c.adding.delete(id);
  if (status === 200 && data) {
    if (c.data) {
      const entry = c.data.entries.find((e) => e.id === id);
      if (entry) entry.added = true;
    }
    statusPaintCatalogue();
    return;
  }
  c.error = statusError(status, data);
  statusPaintCatalogue();
}

/* The app checks on a five-minute timer while it runs; the open page does
 * the same, and repaints its relative times every minute between checks. */
function startStatusClock() {
  const p = statusPage;
  if (p.timer) return;
  p.timer = setInterval(() => {
    const onPage = currentRoute() === "status";
    if (!onPage) { clearInterval(p.timer); p.timer = null; return; }
    if (document.hidden || p.loading || !p.data) return;
    if (Date.now() - p.loadedAt >= 300000) renderStatus("status");
    else paintStatus();
  }, 60000);
}

registerPage("status", renderStatus);
