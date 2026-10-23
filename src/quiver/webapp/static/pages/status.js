/* Service status: a port of the Mac app's ServiceStatusPage. One card per
 * status page in two columns, grouped as the app groups them, each folded
 * to its health line with a "Show details" row that opens the summary,
 * affected components and open incidents. Read only: pages added with the
 * Mac app's "Add service" stay in the Mac app.
 */
"use strict";

const statusPage = { data: null, loading: false, gen: 0, expanded: new Set(), loadedAt: 0, timer: null };

const STATUS_ICONS = {
  open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  arrow: '<path d="M7 17 17 7M8 7h9v9"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  warn: '<path d="M12 4 2.5 20h19zM12 10v4M12 17.5v.01"/>',
};
const statusIcon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${STATUS_ICONS[name]}</svg>`;

// The logos the Mac app draws for each page (StatusService.logoID), as
// shipped in /app/: colour ones as they are, the rest in the text colour.
const STATUS_LOGOS = {
  claude: "harness-claude-color.svg", openai: "harness-openai.svg", copilot: "harness-copilot.svg",
  cursor: "harness-cursor.svg", gemini: "harness-gemini-color.svg", devin: "harness-devin.svg",
  npm: "harness-npm.svg", vercel: "harness-vercel.svg", cloudflare: "harness-cloudflare-color.svg",
};
function statusLogo(id) {
  const file = STATUS_LOGOS[id];
  if (!file) return "";
  const mono = file.endsWith("-color.svg") ? "" : " mono";
  return `<img class="logo${mono}" src="/app/${file}" alt="">`;
}

/* Foundation's .relative(presentation: .named): "now", "5 minutes ago",
 * "yesterday". */
function statusRelative(epoch) {
  // Every time shown this way is past; a hub clock a little ahead of this
  // browser's must not read "in 2 minutes".
  const diff = Math.min(0, epoch - Date.now() / 1000);
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
  for (const [unit, size] of [["year", 31536000], ["month", 2592000], ["week", 604800],
    ["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (abs >= size) return rtf.format(Math.trunc(diff / size), unit);
  }
  return abs < 1 ? "now" : rtf.format(Math.trunc(diff), "second");
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
  renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
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
  return (data && data.error && data.error.message)
    || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
}

function statusHeader() {
  const p = statusPage, d = p.data;
  const checked = d && d.checked_at ? `<span class="caption muted">Checked ${esc(statusRelative(d.checked_at))}</span>` : "";
  return pageHeader("Service status", "Live status from the official status pages of the services your harnesses use.",
    `${checked}<button class="icon-button${p.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh"
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
      <p class="caption muted">Read from each provider's public status page, at most every five minutes. Gemini comes from Google Cloud's status feed, which covers the Gemini API.</p>
    </div></div>`;
  const refresh = document.getElementById("page-refresh");
  if (refresh) refresh.onclick = () => { if (!p.loading) renderStatus("status", true); };
  for (const row of document.querySelectorAll("[data-status-expand]")) {
    row.onclick = () => {
      const id = row.dataset.statusExpand;
      if (p.expanded.has(id)) p.expanded.delete(id); else p.expanded.add(id);
      paintStatus(error);
      const again = document.querySelector(`[data-status-expand="${CSS.escape(id)}"]`);
      if (again) again.focus();
    };
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
  return `<article class="card status-card">
    <div class="status-body">
      <div class="status-head">${statusLogo(s.logo)}<span class="status-name">${esc(s.name)}</span>
        <a class="status-open" href="${esc(s.page_url)}" target="_blank" rel="noopener noreferrer"
          title="${esc(s.page_url)}" aria-label="Open the ${esc(s.name)} status page">${statusIcon("open")}</a></div>
      <div class="status-health-block">
        <div class="status-health"><span class="health-dot health-${esc(s.health || "unknown")}" aria-hidden="true"></span>
          <span class="${s.health ? "" : "muted"}">${esc(label)}</span></div>
        ${s.note ? `<div class="caption muted">${esc(s.note)}</div>` : ""}
      </div>
      ${expanded ? statusDetails(s) : ""}
      ${statusProblem(s)}
    </div>
    ${note ? `<button class="status-expand" data-status-expand="${esc(s.id)}" aria-expanded="${expanded}"
        title="${expanded ? "Show only the health line" : "Show the summary, affected components and incidents"}">
        ${statusIcon(expanded ? "up" : "down")}<span>${expanded ? "Show less" : "Show details"}</span>
        <span class="muted">· ${esc(note)}</span></button>` : ""}
  </article>`;
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
      ${s.incidents.length > INCIDENT_LIMIT
        ? `<a class="status-more" href="${esc(s.page_url)}" target="_blank" rel="noopener noreferrer">${
          s.incidents.length - INCIDENT_LIMIT} more on the status page</a>` : ""}</div>`;
  }
  return out;
}

function statusIncident(i) {
  const meta = [];
  if (i.stage) meta.push(i.stage);
  if (i.started_at != null) meta.push(`${i.maintenance ? "began" : "started"} ${statusWhen(i.started_at)}`);
  if (i.updated_at != null) meta.push(`updated ${statusRelative(i.updated_at)}`);
  const link = i.url ? `<a class="status-incident-link" href="${esc(i.url)}" target="_blank" rel="noopener noreferrer"
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
  if (s.stale && s.checked_at != null) text += `. Showing the reading from ${statusRelative(s.checked_at)}.`;
  return `<div class="status-problem caption">${statusIcon("warn")}<span>${esc(text)}</span></div>`;
}

/* The app checks on a five-minute timer while it runs; the open page does
 * the same, and repaints its relative times every minute between checks. */
function startStatusClock() {
  const p = statusPage;
  if (p.timer) return;
  p.timer = setInterval(() => {
    const onPage = (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0] === "status";
    if (!onPage) { clearInterval(p.timer); p.timer = null; return; }
    if (document.hidden || p.loading || !p.data) return;
    if (Date.now() - p.loadedAt >= 300000) renderStatus("status");
    else paintStatus();
  }, 60000);
}

registerPage("status", renderStatus);
