/* Compute: a port of the Mac app's ComputePage. What the starred
 * harnesses run on the hub machine, what it costs it, orphaned helpers,
 * and the RAM / CPU / GPU / Temperature trend card. The one write is
 * the settings sheet's auto clean: the hub quits old orphans itself.
 * Quit, Clean up and fan control stay in the Mac app. Data from
 * /v1/app/compute, polled every 3 seconds while the page is open, as the
 * Mac page refreshes. */
"use strict";

const COMPUTE_TABS = [["ram", "RAM"], ["cpu", "CPU"], ["gpu", "GPU"], ["temperature", "Temperature"]];
// TrendInterval: [seconds, picker title, span for "Collecting" text].
const COMPUTE_INTERVALS = [[3, "Every 3 s", "3 seconds"], [10, "Every 10 s", "10 seconds"],
  [30, "Every 30 s", "30 seconds"], [60, "Every minute", "a minute"], [300, "Every 5 minutes", "5 minutes"]];
const COMPUTE_COMPONENTS = [["cpu", "CPU"], ["gpu", "GPU"], ["memory", "Memory"], ["ssd", "SSD"],
  ["battery", "Battery"], ["airflow", "Airflow"], ["display", "Display"]];

const computePage = {
  data: null, seq: 0, timer: null, failed: null,
  // Per-browser choices, kept like the Mac app's UserDefaults. A stored
  // value this page doesn't know (an older build's, a hand edit) falls
  // back: an unknown tab threw in the chart and stopped the poll.
  tab: computeChoice(computeStored("tab", "ram"), COMPUTE_TABS.map(([id]) => id), "ram"),
  interval: computeChoice(Number(computeStored("interval", "10")), COMPUTE_INTERVALS.map(([s]) => s), 10),
  lines: computeLines(computeStoredJSON("temperatureLines", null)),
  limits: computeLimits(computeStoredJSON("limits", {})),
  collapsed: new Set(), showAll: new Set(), hover: null,
  // The settings sheet's hub write in flight and its last error.
  acBusy: false, acError: null,
};

const COMPUTE_VISIBLE_ROWS = 5;
const COMPUTE_ORPHAN_FOLD = 8;
const GB = 1024 * 1024 * 1024;

function computeStored(key, fallback) {
  try { return localStorage.getItem(`quiver.compute.${key}`) || fallback; } catch { return fallback; }
}
function computeChoice(value, known, fallback) {
  return known.includes(value) ? value : fallback;
}
/* The Temperature card's lines: known components only, in a list. */
function computeLines(saved) {
  const known = COMPUTE_COMPONENTS.map(([c]) => c);
  return Array.isArray(saved) ? saved.filter((c) => known.includes(c)) : ["cpu", "gpu", "ssd", "battery"];
}
function computeStoredJSON(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(`quiver.compute.${key}`)); return v == null ? fallback : v; }
  catch { return fallback; }
}
/* ComputeLimits: 1, 2, 4 or 8 GB (2 by default); an orphan age of a
 * minute to a year (a day by default). A bad stored value falls back. */
function computeLimits(saved) {
  const gb = [1, 2, 4, 8].includes(Number(saved && saved.memoryGB)) ? Number(saved.memoryGB) : 2;
  const age = Number(saved && saved.orphanAge);
  return { memoryGB: gb, orphanAge: age > 0 ? Math.min(Math.max(age, 60), 365 * 86400) : 24 * 3600 };
}

function computeStore(key, value) {
  try { localStorage.setItem(`quiver.compute.${key}`, typeof value === "string" ? value : JSON.stringify(value)); }
  catch { /* private window: the choice lasts this visit */ }
}

/* ---------------------------------------------------------------- formats */

const CFmt = {
  /* ByteCountFormatter's .memory style: binary units, whole KB, one
   * decimal for MB, two for GB and up, trailing zeros dropped; nothing
   * is "0 KB", as in ComputeSummary.bytes. */
  bytes(count) {
    if (!count) return "0 KB";
    if (count < 1024) return `${count} bytes`;
    const units = [["KB", 0], ["MB", 1], ["GB", 2], ["TB", 2]];
    let value = count / 1024, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return `${value.toLocaleString("en-US", { maximumFractionDigits: units[i][1] })} ${units[i][0]}`;
  },
  /* "1 thread", "28 threads", as in ComputeSummary.threads. */
  threads(n) { return `${n} ${n === 1 ? "thread" : "threads"}`; },
  /* Whole percent once it is two digits, one decimal below it. */
  percent(p) {
    const digits = p >= 10 ? 0 : 1;
    return p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  },
  /* "45m", "3h 12m", "3d 6h". */
  duration(seconds) {
    const minutes = Math.floor(seconds / 60);
    if (seconds >= 86400) return `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`;
    if (seconds >= 3600) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    return `${Math.max(minutes, 0)}m`;
  },
  celsius(c) { return `${Math.round(c)} °C`; },
};

/* Ages run on the hub's clock (its sample time), so a browser whose
 * clock is off never shows a wrong "running for". */
const nowS = () => (computePage.data && computePage.data.sampled_at) || Date.now() / 1000;

/* ---------------------------------------------------------------- page */

/* The route's entry and the Refresh button: paint what we have (or a
 * loading card), then fetch. `refresh` samples now, past the hub's
 * shared two-second minimum. */
function renderCompute(_id, refresh = false) {
  const p = computePage;
  clearTimeout(p.timer);
  if (!p.data) {
    document.getElementById("panel").innerHTML = `<div class="page compute-page">${computeHeader(true)}
      <div class="card"><p class="empty">Sampling processes…</p></div></div>`;
    bindComputeHeader();
  } else {
    paintCompute(refresh);
  }
  return pollCompute(routeGen, refresh);
}

/* One request, then the next in three seconds while the page stays open,
 * as the Mac page samples every three seconds. A hidden tab skips its
 * turn rather than sampling for nobody. */
async function pollCompute(gen, refresh = false) {
  const p = computePage;
  clearTimeout(p.timer);
  // Only the newest call paints and schedules the next: a reply still in
  // flight when the interval changes is dropped, so one loop ever runs.
  const mine = ++p.seq;
  const again = () => {
    p.timer = setTimeout(() => { if (gen === routeGen && mine === p.seq) pollCompute(gen); }, 3000);
  };
  if (document.hidden && !refresh && p.data) return again();
  const q = new URLSearchParams({ interval: String(p.interval) });
  if (refresh) q.set("refresh", "1");
  const { status, data } = await api(`/v1/app/compute?${q}`);
  if (gen !== routeGen || mine !== p.seq) return;
  if (status === 401) return renderGate();
  if (status !== 200 || !data) {
    if (!p.data) {
      pageError(computeHeader(false), status, data, () => renderCompute("compute", true));
      return again();
    }
    // Keep the last sample on screen and say the hub stopped answering.
    p.failed = (data && data.error && data.error.message)
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Retrying every few seconds.");
  } else {
    p.failed = null;
    p.data = data;
  }
  paintCompute(false);
  again();
}

function computeHeader(busy) {
  const d = computePage.data;
  const where = d && d.machine ? d.machine : "this machine";
  return pageHeader("Compute", `What your starred harnesses are running, and what it costs ${where}.`,
    `<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>
     <button class="icon-button" id="compute-settings" aria-label="Settings" title="Settings">${icon("settings")}</button>`);
}

function bindComputeHeader() {
  bindRefresh(() => renderCompute("compute", true));
  const gear = document.getElementById("compute-settings");
  if (gear) gear.onclick = openComputeLimits;
}

/* A selector for the control that has focus inside the body, so a
 * repaint can give focus back to its replacement. */
function computeFocusKey(body) {
  const el = document.activeElement;
  if (!el || !body.contains(el)) return null;
  if (el.id) return `#${CSS.escape(el.id)}`;
  for (const attr of ["data-trend-tab", "data-collapse", "data-show-all", "data-line"]) {
    if (el.hasAttribute(attr)) return `[${attr}="${CSS.escape(el.getAttribute(attr))}"]`;
  }
  return null;
}

/* The header and the body's skeleton are painted once per visit; each
 * sample repaints the readings around the trend card's tabs and picker,
 * which are rebuilt only when their choice changes. So a poll never
 * closes an open picker or takes its focus, the open settings sheet and
 * the chart's hover survive, and any other focused control gets its
 * focus back after the repaint. */
function paintCompute(busy) {
  const p = computePage, d = p.data;
  const panel = document.getElementById("panel");
  if (!document.getElementById("compute-body")) {
    panel.innerHTML = `<div class="page compute-page"><div id="compute-header"></div><div class="stack" id="compute-body"></div></div>`;
  }
  document.getElementById("compute-header").innerHTML = computeHeader(busy);
  bindComputeHeader();
  const body = document.getElementById("compute-body");
  if (!d.supported) {
    body.innerHTML = `<div class="card"><p class="empty">${esc(d.message)}</p></div>`;
    return;
  }
  if (!document.getElementById("compute-trend-head")) {
    body.innerHTML = `<div class="compute-part" id="compute-top"></div>
      <div class="card compute-trend"><div class="compute-trend-head" id="compute-trend-head"></div>
        <div class="compute-part" id="compute-trend-body"></div></div>
      <div class="compute-part" id="compute-rest"></div>`;
    p.headKey = null;
  }
  const focus = computeFocusKey(body);
  const key = `${p.tab}|${p.interval}`;
  if (p.headKey !== key) {
    document.getElementById("compute-trend-head").innerHTML = trendHead();
    p.headKey = key;
  }
  document.getElementById("compute-top").innerHTML = computeTop(d);
  document.getElementById("compute-trend-body").innerHTML = trendBody(d);
  document.getElementById("compute-rest").innerHTML = computeRest(d);
  bindComputeBody(body);
  drawComputeChart();
  if (focus && !body.contains(document.activeElement)) {
    const again = body.querySelector(focus);
    if (again) again.focus({ preventScroll: true });
  }
}

/* The totals line and the machine line, above the trend card. */
function computeTop(d) {
  const t = d.totals;
  const machine = machineLine(d);
  const n = t.process_count;
  return `<p class="caption num compute-totals">${CFmt.bytes(t.memory_bytes)} across ${n} ${n === 1 ? "process" : "processes"} · CPU ${CFmt.percent(t.cpu_percent)}% · ${CFmt.threads(t.threads)}</p>${
    machine ? `<p class="caption num compute-machine">${machine}</p>` : ""}`;
}

/* Errors, the empty state and the cards, below the trend card. */
function computeRest(d) {
  const p = computePage;
  const parts = [];
  if (p.failed) parts.push(`<div class="card"><p class="error">${esc(p.failed)}</p></div>`);
  if (d.error) parts.push(`<div class="card"><p class="error">${esc(d.error)}</p></div>`);
  if (!d.harnesses.length && !d.orphans.length) {
    // Only a successful read of the stars can say there are none.
    parts.push(d.matchers_loaded && !d.starred.length
      ? emptyCard("Nothing is starred. Star a harness to watch what it runs.",
        `<a class="compute-button" href="#/harnesses">Open Harnesses</a>`)
      : emptyCard("Nothing a starred harness runs is up right now."));
  }
  for (const usage of d.harnesses) parts.push(harnessCard(usage));
  if (d.orphans.length) parts.push(orphansCard(d.orphans));
  for (const harness of d.not_running) {
    parts.push(`<div class="card compute-harness"><div class="compute-head idle">
      ${Fmt.logo(harness, "logo big")}<span class="card-title">${esc(Fmt.harnessName(harness))}</span>
      <span class="caption muted compute-figures">Not running</span></div></div>`);
  }
  return parts.join("");
}

/* Web only: the machine as a whole, which the Mac page leaves to
 * Activity Monitor. */
function machineLine(d) {
  const s = d.system || {};
  const bits = [];
  if (s.cpu_percent != null) bits.push(`CPU ${CFmt.percent(s.cpu_percent)}% of ${s.cores} cores`);
  else if (s.cores) bits.push(`${s.cores} cores`);
  if (s.memory) {
    let mem = `Memory ${CFmt.bytes(s.memory.used_bytes)} of ${CFmt.bytes(s.memory.total_bytes)}`;
    if (s.memory.swap_used_bytes) mem += `, ${CFmt.bytes(s.memory.swap_used_bytes)} swap`;
    bits.push(mem);
  }
  if (s.load) bits.push(`Load ${s.load.map((v) => v.toFixed(2)).join(" ")}`);
  for (const g of d.gpus || []) {
    let gpu = esc(g.name);
    if (g.utilization_percent != null) gpu += ` ${Math.round(g.utilization_percent)}%`;
    if (g.memory_used_bytes != null && g.memory_total_bytes) {
      gpu += `, ${CFmt.bytes(g.memory_used_bytes)} of ${CFmt.bytes(g.memory_total_bytes)}`;
    }
    bits.push(gpu);
  }
  if (!bits.length) return "";
  return `${esc(d.machine || "This machine")}: ${bits.join(" · ")}`;
}

function emptyCard(text, action = "") {
  return `<div class="card compute-empty"><span class="compute-empty-label">${icon("compute")}<span>${esc(text)}</span></span>${action}</div>`;
}

function badge(text) { return `<span class="badge">${esc(text)}</span>`; }

/* ---------------------------------------------------------------- harness cards */

function harnessCard(usage) {
  const p = computePage;
  const limit = p.limits.memoryGB * GB;
  const id = usage.harness;
  const expanded = !p.collapsed.has(id);
  const rows = usage.processes;
  const all = p.showAll.has(id);
  const shown = all ? rows : rows.slice(0, COMPUTE_VISIBLE_ROWS);
  const hidden = Math.max(0, rows.length - COMPUTE_VISIBLE_ROWS);
  const flagged = (r) => r.memory_bytes > limit || r.busy;
  const hiddenFlagged = all ? 0 : rows.slice(COMPUTE_VISIBLE_ROWS).filter(flagged).length;
  const n = usage.process_count;
  const figures = `${CFmt.bytes(usage.memory_bytes)} · CPU ${CFmt.percent(usage.cpu_percent)}% · ${n} ${n === 1 ? "process" : "processes"} · ${CFmt.threads(usage.threads)} · running for ${CFmt.duration(nowS() - usage.running_since)}`;
  let body = "";
  if (expanded) {
    body = `<div class="compute-rows">${shown.map((r) => processRow(r, limit)).join("")}${hidden ? `
      <button class="compute-expand" data-show-all="${esc(id)}" title="${all ? `Show the top ${COMPUTE_VISIBLE_ROWS} by memory` : "Show every process"}">
        ${all ? "Show less" : `Show ${hidden} more`}${hiddenFlagged ? ` <span class="muted">· ${hiddenFlagged} flagged</span>` : ""}
        <span class="chevron">${all ? "⌃" : "⌄"}</span></button>` : ""}</div>`;
  }
  return `<div class="card compute-harness">
    <button class="compute-head" data-collapse="${esc(id)}" aria-expanded="${expanded}">
      ${Fmt.logo(id, "logo big")}<span class="card-title">${esc(Fmt.harnessName(id))}</span>
      <span class="caption num compute-figures" title="${esc(figures)}">${esc(figures)}</span>
      <span class="chevron muted" aria-hidden="true">${expanded ? "⌃" : "⌄"}</span>
    </button>${body}</div>`;
}

function processRow(r, limit) {
  const badges = (r.memory_bytes > limit ? badge(`Over ${CFmt.bytes(limit)}`) : "") + (r.busy ? badge("Busy") : "");
  return `<div class="compute-row" title="${esc(r.command_line)}">
    <div class="compute-main"><div class="compute-name"><span class="ellipsis">${esc(r.name)}</span>${badges}</div>
      <div class="caption num">PID ${r.pid} · ${CFmt.threads(r.threads)} · running for ${CFmt.duration(nowS() - r.started_at)}</div></div>
    <span class="caption num compute-mem">${CFmt.bytes(r.memory_bytes)}</span>
    <span class="caption num compute-cpu">${r.cpu_percent == null ? "–" : `${CFmt.percent(r.cpu_percent)}%`}</span>
  </div>`;
}

/* ---------------------------------------------------------------- orphans */

function orphansCard(rows) {
  const p = computePage;
  const limit = p.limits.memoryGB * GB;
  const all = p.showAll.has("orphaned");
  const shown = all ? rows : rows.slice(0, COMPUTE_ORPHAN_FOLD);
  const hidden = Math.max(0, rows.length - COMPUTE_ORPHAN_FOLD);
  const list = shown.map((o) => {
    const old = nowS() - o.started_at > p.limits.orphanAge;
    const folder = o.cwd ? o.cwd.replace(/\/+$/, "").split("/").pop() : "";
    const caption = [folder ? `in ${folder}` : "", `PID ${o.pid}`, `running for ${CFmt.duration(nowS() - o.started_at)}`]
      .filter(Boolean).join(" · ");
    return `<div class="compute-row orphan" title="${esc(o.command_line)}">
      <div class="compute-main"><div class="compute-name"><span class="ellipsis">${esc(o.command)}</span>${
        old ? badge("Old") : ""}${o.memory_bytes > limit ? badge(`Over ${CFmt.bytes(limit)}`) : ""}${
        o.quitting ? badge("Quitting") : ""}</div>
        <div class="caption num">${esc(caption)}</div></div>
      <span class="caption num compute-mem">${CFmt.bytes(o.memory_bytes)}</span>
    </div>`;
  }).join("");
  const more = hidden ? `<button class="compute-expand" data-show-all="orphaned" title="${all ? `Show only the first ${COMPUTE_ORPHAN_FOLD}` : "Show every orphaned process"}">
      ${all ? "Show less" : `Show ${hidden} more`} <span class="chevron">${all ? "⌃" : "⌄"}</span></button>` : "";
  return `<div class="card"><div><div class="card-title">Orphaned</div>
    <div class="card-subtitle">Processes whose parent has exited, usually dev servers or MCP servers an agent started and left behind.</div></div>
    <div class="compute-rows">${list}${more}</div></div>`;
}

function bindComputeBody(body) {
  const p = computePage;
  for (const btn of body.querySelectorAll("[data-collapse]")) {
    btn.onclick = () => {
      const id = btn.dataset.collapse;
      if (p.collapsed.has(id)) p.collapsed.delete(id); else p.collapsed.add(id);
      paintCompute(false);
    };
  }
  for (const btn of body.querySelectorAll("[data-show-all]")) {
    btn.onclick = () => {
      const id = btn.dataset.showAll;
      if (p.showAll.has(id)) p.showAll.delete(id); else p.showAll.add(id);
      paintCompute(false);
    };
  }
  for (const tab of body.querySelectorAll("[data-trend-tab]")) {
    tab.onclick = () => { p.tab = tab.dataset.trendTab; computeStore("tab", p.tab); p.hover = null; paintCompute(false); };
  }
  const interval = body.querySelector("#compute-interval");
  if (interval) {
    interval.onchange = () => {
      p.interval = Number(interval.value);
      computeStore("interval", String(p.interval));
      pollCompute(routeGen);
    };
  }
  for (const chip of body.querySelectorAll("[data-line]")) {
    chip.onclick = () => {
      const c = chip.dataset.line;
      p.lines = p.lines.includes(c) ? p.lines.filter((x) => x !== c) : [...p.lines, c];
      computeStore("temperatureLines", p.lines);
      paintCompute(false);
    };
  }
}

/* ---------------------------------------------------------------- trend card */

function intervalSpan() {
  return (COMPUTE_INTERVALS.find(([s]) => s === computePage.interval) || COMPUTE_INTERVALS[1])[2];
}

function trendHead() {
  const p = computePage;
  const tabs = `<div class="tabs" role="tablist">${COMPUTE_TABS.map(([id, label]) =>
    `<button class="tab${id === p.tab ? " selected" : ""}" data-trend-tab="${id}" role="tab" aria-selected="${id === p.tab}">${label}</button>`).join("")}</div>`;
  const picker = `<select class="field" id="compute-interval" aria-label="Interval">${options(
    COMPUTE_INTERVALS.map(([s, title]) => [String(s), title]), String(p.interval))}</select>`;
  return tabs + picker;
}

function trendBody(d) {
  const p = computePage;
  const tr = d.trend;
  const collecting = `Collecting, the first points arrive within ${intervalSpan()}.`;
  if (p.tab === "temperature") {
    if (tr.temperatures.length) return temperatureLegend(tr) + chartBox();
    if (tr.last_tick == null) return placeholder(collecting);
    return placeholder("Temperature sensors aren't available on this machine.");
  }
  if (p.tab === "gpu" && !tr.gpu_available) {
    return placeholder("GPU readings aren't available on this machine. They need an NVIDIA GPU and nvidia-smi.");
  }
  if (!Object.keys(tr.harnesses).length && !Object.keys(tr.peaks).length) return placeholder(collecting);
  if (!shownHarnesses(tr).length) {
    return placeholder(`No harness has gone above ${CFmt.bytes(p.limits.memoryGB * GB)} yet.`);
  }
  return chartBox();
}

function placeholder(text) { return `<p class="caption compute-placeholder">${esc(text)}</p>`; }
function chartBox() { return `<div class="compute-chart" id="compute-chart"></div>`; }

/* ComputeHistory.shown: every harness that went above the flag at any
 * retained point, sorted, the same set on every tab. */
function shownHarnesses(tr) {
  const limit = computePage.limits.memoryGB * GB;
  return Object.keys(tr.peaks).filter((h) => tr.peaks[h] > limit).sort();
}

function temperatureLegend(tr) {
  const p = computePage;
  const present = new Set(tr.temperatures.flatMap((pt) => Object.keys(pt.values)));
  const latest = tr.temperatures[tr.temperatures.length - 1];
  const chips = COMPUTE_COMPONENTS.filter(([c]) => present.has(c)).map(([c, title]) => {
    const on = p.lines.includes(c);
    const value = latest && latest.values[c] != null ? `<span class="muted num">${CFmt.celsius(latest.values[c])}</span>` : "";
    return `<button class="compute-chip${on ? " on" : ""}" data-line="${c}" title="${on ? "Hide" : "Show"} ${title}">
      <span class="dot" style="${on ? `background: var(--series-${c})` : ""}"></span>${title}${value}</button>`;
  });
  if (fanSpans(tr.temperatures).length) {
    chips.push(`<span class="compute-chip key" title="Shaded where a fan ran more than 15% above its minimum speed."><span class="shade"></span>Fans up</span>`);
  }
  return `<div class="compute-legend">${chips.join("")}</div>`;
}

/* ComputeHistory.fanSpans: each fans-up point reaches halfway to its
 * neighbours, at most 150 s, and touching spans merge. */
function fanSpans(points) {
  const spans = [];
  const maxReach = 150;
  points.forEach((pt, i) => {
    if (!(pt.fans && pt.fans.up)) return;
    const before = i > 0 ? (pt.at - points[i - 1].at) / 2 : null;
    const after = i + 1 < points.length ? (points[i + 1].at - pt.at) / 2 : null;
    const left = Math.min(before ?? after ?? computePage.interval / 2, maxReach);
    const right = Math.min(after ?? before ?? computePage.interval / 2, maxReach);
    const span = [pt.at - left, pt.at + right];
    const last = spans[spans.length - 1];
    if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
    else spans.push(span);
  });
  return spans;
}

/* TrendScale.top: the biggest value rounded up to the next whole step
 * (a value on a step gets the one above), at least `floor`. */
function scaleTop(values, step, floor) {
  const highest = values.length ? Math.max(...values) : 0;
  return Math.max((Math.floor(highest / step) + 1) * step, floor);
}

/* The chart spec for the selected tab, or null when it has nothing to draw. */
function chartSpec() {
  const p = computePage, tr = p.data.trend;
  const span = p.interval * tr.window;
  if (p.tab === "temperature") {
    const shown = COMPUTE_COMPONENTS.filter(([c]) => p.lines.includes(c)
      && tr.temperatures.some((pt) => pt.values[c] != null));
    const end = tr.last_tick;
    const series = shown.map(([c, title]) => ({
      key: c, label: title, color: `var(--series-${c})`, endLabel: title,
      points: tr.temperatures.filter((pt) => pt.values[c] != null).map((pt) => [pt.at, pt.values[c]]),
    }));
    const values = series.flatMap((s) => s.points.map((pt) => pt[1]));
    // TemperatureChart.domain: ten-degree steps, at least 30 °C tall.
    const low = Math.max(0, Math.floor(((values.length ? Math.min(...values) : 30) - 5) / 10) * 10);
    const top = scaleTop(values.map((v) => v + 5), 10, low + 30);
    return { series, x: [end - span, end], y: [low, top], format: CFmt.celsius, axis: (v) => `${Math.round(v)} °C`,
      spans: fanSpans(tr.temperatures), fans: tr.temperatures, empty: shown.length ? "" : "Every line is off. Pick one above." };
  }
  const harnesses = shownHarnesses(tr);
  const ram = p.tab === "ram", gpu = p.tab === "gpu";
  // TrendMetric.reads: a GPU point without a reading is left out, so the
  // line does not dip to zero for "no reading".
  const series = harnesses.filter((h) => tr.harnesses[h]).map((h) => ({
    key: h, label: Fmt.harnessName(h), color: Fmt.harnessColor(h), logo: h,
    points: tr.harnesses[h].filter((pt) => !gpu || pt[3] != null)
      .map(([at, mem, cpu, g]) => [at, ram ? mem / GB : gpu ? g : cpu]),
  }));
  // ComputeHistory.latest: the window ends at the newest point of any
  // harness, drawn or not, so a stopped harness's line slides off the
  // left while anything else still runs.
  const ends = Object.values(tr.harnesses).flatMap((pts) => pts.map((pt) => pt[0]));
  const end = ends.length ? Math.max(...ends) : tr.last_tick;
  const values = series.flatMap((s) => s.points.map((pt) => pt[1]));
  const limitGB = p.limits.memoryGB;
  return ram
    ? { series, x: [end - span, end], y: [0, scaleTop(values, 2, limitGB + 2)],
      format: (v) => CFmt.bytes(Math.max(0, v) * GB), axis: (v) => `${v.toFixed(1)} GB`,
      flag: { value: limitGB, label: `Flag above ${CFmt.bytes(limitGB * GB)}` } }
    : { series, x: [end - span, end], y: [0, scaleTop(values, 25, gpu ? 100 : 25)],
      format: (v) => `${CFmt.percent(v)}%`, axis: (v) => `${Math.round(v)}%`,
      // gpu_available stays true once pmon answered; it can stop since.
      empty: gpu && !series.some((s) => s.points.some(([t]) => t >= end - span))
        ? "nvidia-smi gave no GPU readings in this window." : "" };
}

/* Round tick values from lo to hi, about `count` of them. */
function niceTicks(lo, hi, count) {
  const raw = (hi - lo) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

function timeTicks(x0, x1, count) {
  const steps = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];
  const step = steps.find((s) => (x1 - x0) / s <= count) || 43200;
  const out = [];
  // Local minutes line up for whole-minute steps; the offset keeps hours on the hour.
  const offset = new Date().getTimezoneOffset() * 60;
  for (let t = Math.ceil((x0 - offset) / step) * step + offset; t <= x1; t += step) out.push(t);
  return { ticks: out, seconds: step < 60 };
}

/* Monotone cubic through pixel points (Fritsch-Carlson), like the Mac
 * chart's .monotone interpolation: no overshoot above a peak. */
function monotonePath(pts) {
  const n = pts.length;
  if (n < 2) return "";
  if (n === 2) return `M${pts[0][0]},${pts[0][1]}L${pts[1][0]},${pts[1][1]}`;
  const d = [], m = new Array(n);
  for (let i = 0; i < n - 1; i++) d.push((pts[i + 1][1] - pts[i][1]) / ((pts[i + 1][0] - pts[i][0]) || 1e-9));
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], s = a * a + b * b;
    if (s > 9) { const t = 3 / Math.sqrt(s); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
  }
  let path = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = pts[i + 1][0] - pts[i][0];
    path += `C${(pts[i][0] + h / 3).toFixed(1)},${(pts[i][1] + m[i] * h / 3).toFixed(1)} ${(pts[i + 1][0] - h / 3).toFixed(1)},${(pts[i + 1][1] - m[i + 1] * h / 3).toFixed(1)} ${pts[i + 1][0].toFixed(1)},${pts[i + 1][1].toFixed(1)}`;
  }
  return path;
}

function drawComputeChart() {
  const box = document.getElementById("compute-chart");
  if (!box) return;
  const spec = chartSpec();
  if (spec.empty) { box.innerHTML = placeholder(spec.empty); return; }
  const width = Math.max(240, Math.round(box.getBoundingClientRect().width) || 600);
  const height = 180;
  const names = spec.series.some((s) => s.endLabel);
  const m = { left: 48, right: names ? 70 : 26, top: 8, bottom: 22 };
  const pw = width - m.left - m.right, ph = height - m.top - m.bottom;
  const [x0, x1] = spec.x, [y0, y1] = spec.y;
  const X = (t) => m.left + ((t - x0) / ((x1 - x0) || 1)) * pw;
  const Y = (v) => m.top + ph - ((v - y0) / ((y1 - y0) || 1)) * ph;
  const svg = [];
  for (const [a, b] of spec.spans || []) {
    const l = X(Math.max(a, x0)), r = X(Math.min(b, x1));
    if (r > l) svg.push(`<rect class="fan-shade" x="${l.toFixed(1)}" y="${m.top}" width="${(r - l).toFixed(1)}" height="${ph}"/>`);
  }
  for (const v of niceTicks(y0, y1, 4)) {
    const y = Y(v).toFixed(1);
    svg.push(`<line class="grid" x1="${m.left}" x2="${m.left + pw}" y1="${y}" y2="${y}"/>`,
      `<text class="axis" x="${m.left - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${esc(spec.axis(v))}</text>`);
  }
  const tt = timeTicks(x0, x1, Math.max(2, Math.floor(pw / 110)));
  for (const t of tt.ticks) {
    const label = new Date(t * 1000).toLocaleTimeString([], tt.seconds
      ? { hour: "numeric", minute: "2-digit", second: "2-digit" } : { hour: "numeric", minute: "2-digit" });
    svg.push(`<text class="axis" x="${X(t).toFixed(1)}" y="${height - 6}" text-anchor="middle">${esc(label)}</text>`);
  }
  if (spec.flag) {
    const y = Y(spec.flag.value).toFixed(1);
    svg.push(`<line class="flag" x1="${m.left}" x2="${m.left + pw}" y1="${y}" y2="${y}"/>`,
      `<text class="flag-label" x="${m.left + 2}" y="${(Y(spec.flag.value) - 5).toFixed(1)}">${esc(spec.flag.label)}</text>`);
  }
  const ends = [];
  for (const s of spec.series) {
    const pts = s.points.filter(([t]) => t >= x0 && t <= x1).map(([t, v]) => [X(t), Y(v)]);
    if (!pts.length) continue;
    svg.push(`<path class="line" d="${monotonePath(pts)}" style="stroke: ${s.color}"/>`);
    const [ex, ey] = pts[pts.length - 1];
    svg.push(`<circle cx="${ex.toFixed(1)}" cy="${ey.toFixed(1)}" r="2.5" style="fill: ${s.color}"/>`);
    if (s.logo) ends.push(`<span class="end-icon" style="left:${(ex + 5).toFixed(1)}px; top:${(ey - 8).toFixed(1)}px">${Fmt.logo(s.logo)}</span>`);
    else if (s.endLabel) svg.push(`<text class="end-label" x="${(ex + 6).toFixed(1)}" y="${ey.toFixed(1)}" dominant-baseline="middle">${esc(s.endLabel)}</text>`);
  }
  box.innerHTML = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img"
      aria-label="${esc(COMPUTE_TABS.find(([id]) => id === computePage.tab)[1])} over time">${svg.join("")}
      <line class="hover-rule" id="compute-rule" y1="${m.top}" y2="${m.top + ph}" visibility="hidden"/></svg>
    ${ends.join("")}<div class="bubble compute-bubble" id="compute-bubble" hidden></div>`;
  const geometry = { m, pw, x0, x1, X, spec };
  box.onmousemove = (e) => {
    const rect = box.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (x < m.left || x > m.left + pw) { computePage.hover = null; showHover(box, geometry); return; }
    computePage.hover = x0 + ((x - m.left) / pw) * (x1 - x0);
    showHover(box, geometry);
  };
  box.onmouseleave = () => { computePage.hover = null; showHover(box, geometry); };
  showHover(box, geometry);
}

/* The hover rule and the readout: each line's value at the point
 * nearest the hovered time, in a bubble kept inside the plot. */
function showHover(box, g) {
  const rule = box.querySelector("#compute-rule"), bubble = box.querySelector("#compute-bubble");
  const t = computePage.hover;
  if (t == null || t < g.x0 || t > g.x1) { rule.setAttribute("visibility", "hidden"); bubble.hidden = true; return; }
  const nearest = (pts) => pts.reduce((best, pt) => (!best || Math.abs(pt[0] - t) < Math.abs(best[0] - t) ? pt : best), null);
  const rows = [];
  for (const s of g.spec.series) {
    const pt = nearest(s.points.filter(([at]) => at >= g.x0 && at <= g.x1));
    if (!pt) continue;
    const mark = s.logo ? Fmt.logo(s.logo, "logo logo-sm") : `<span class="dot" style="background: ${s.color}"></span>`;
    rows.push(`<div class="bubble-row">${mark}<span>${esc(s.label)}</span><span class="num">${esc(g.spec.format(pt[1]))}</span></div>`);
  }
  if (g.spec.fans) {
    const pt = g.spec.fans.reduce((best, p) => (!best || Math.abs(p.at - t) < Math.abs(best.at - t) ? p : best), null);
    if (pt && pt.fans) {
      rows.push(`<div class="bubble-row muted"><span class="shade${pt.fans.up ? " up" : ""}"></span><span>Fans</span><span class="num">${Math.round(pt.fans.rpm).toLocaleString("en-US")} rpm</span></div>`);
    }
  }
  if (!rows.length) { rule.setAttribute("visibility", "hidden"); bubble.hidden = true; return; }
  const x = g.X(t);
  rule.setAttribute("x1", x.toFixed(1)); rule.setAttribute("x2", x.toFixed(1));
  rule.setAttribute("visibility", "visible");
  bubble.innerHTML = rows.join("");
  bubble.hidden = false;
  const half = bubble.offsetWidth / 2;
  const lo = g.m.left + half + 4, hi = g.m.left + g.pw - half - 4;
  const cx = lo <= hi ? Math.min(Math.max(x, lo), hi) : g.m.left + g.pw / 2;
  bubble.style.left = `${(cx - half).toFixed(1)}px`;
  bubble.style.top = `${g.m.top + 4}px`;
}

/* ---------------------------------------------------------------- settings */

const AGE_UNITS = [["minute", "Min", 60], ["hour", "Hour", 3600], ["day", "Day", 86400]];

/* OrphanAgeUnit.split: the largest unit the age divides evenly into. */
function splitAge(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  if (minutes % 1440 === 0) return [minutes / 1440, "day"];
  if (minutes % 60 === 0) return [minutes / 60, "hour"];
  return [minutes, "minute"];
}

/* The settings page's set-switch as a button: the same markup so the
 * shared stylesheet draws the Mac's switch. `type="button"` matters —
 * the sheet is a form, and a bare button would submit and close it. */
function computeSwitch(id, on, opts = {}) {
  return `<button type="button" class="set-switch" role="switch" aria-checked="${on}" data-cswitch="${esc(id)}"
    aria-label="${esc(opts.label || id)}"${opts.disabled ? " disabled" : ""}><span></span></button>`;
}

function openComputeLimits() {
  document.getElementById("compute-limits")?.remove();
  const p = computePage;
  // Back can change the route while the sheet is open; a change saved
  // after that must not paint Compute over the page now showing.
  const gen = routeGen;
  const repaint = () => { if (p.data && gen === routeGen) paintCompute(false); };
  const [value, unit] = splitAge(p.limits.orphanAge);
  // Auto clean is the hub's own state, not this browser's; a non-Linux
  // hub has no /proc to work, so its section is left out entirely.
  const hasHub = p.data && p.data.supported !== false;
  // A route sheet (app.js bodySheet): leaving the page closes it, which
  // runs its close handler below, so it never stays over the next page.
  const dialog = bodySheet("compute-limits", "sheet compute-limits");
  dialog.innerHTML = `<form method="dialog" class="sheet-body">
      <div class="sheet-title">Settings</div>
      <div class="compute-setting">
        <div><div>Orphans count as old after</div>
          <div class="caption">Orphans older than this are flagged Old.</div></div>
        <div class="compute-setting-control">
          <input class="field num" id="compute-age" type="number" min="1" step="1" value="${value}" aria-label="Age">
          <select class="field" id="compute-age-unit" aria-label="Unit">${options(AGE_UNITS.map(([id, title]) => [id, title]), unit)}</select>
        </div>
      </div>
      <div class="compute-setting">
        <div><div>Flag a process above</div>
          <div class="caption">A process using more memory than this is flagged, and its harness is drawn on the trend card.</div></div>
        <div class="compute-setting-control">
          <select class="field" id="compute-memory" aria-label="Memory limit">${options(
            [1, 2, 4, 8].map((gb) => [String(gb), `${gb} GB`]), String(p.limits.memoryGB))}</select>
        </div>
      </div>
      <div id="compute-autoclean"></div>
      <p class="caption" id="compute-ac-note"></p>
      <p class="caption">${hasHub
        ? "Thresholds are kept in this browser; auto clean is kept on the hub. Quitting processes and Clean up are in the Mac app."
        : "Kept in this browser. Quitting processes and Clean up are in the Mac app."}</p>
      <div class="sheet-actions"><button class="compute-button" value="done">Done</button></div>
    </form>`;
  const age = dialog.querySelector("#compute-age"), ageUnit = dialog.querySelector("#compute-age-unit");
  const memory = dialog.querySelector("#compute-memory");

  /* The hub's saved auto clean, from the last payload. The sheet writes
   * its whole section at once, so each post carries the current of the
   * other knobs too. */
  const ac = () => (p.data && p.data.autoclean) || {};
  const acBody = () => {
    const on = ac().enabled === true;
    const ageText = CFmt.duration(ac().orphan_age_seconds || p.limits.orphanAge);
    const grace = Math.round(ac().grace_seconds || 10);
    return `<div class="compute-setting">
      <div><div>Auto clean</div>
        <div class="caption">The hub sends an orphan older than ${esc(ageText)} a quit signal, even with no browser open.</div></div>
      <div class="compute-setting-control">${computeSwitch("autoclean", on, { label: "Auto clean", disabled: p.acBusy })}</div>
    </div>
    ${on ? `<div class="compute-setting sub">
      <div><div>Force quit</div>
        <div class="caption">An orphan still running ${grace} seconds after its quit signal is force quit.</div></div>
      <div class="compute-setting-control">${computeSwitch("autoclean-force", ac().force === true, { label: "Force quit", disabled: p.acBusy })}</div>
    </div>` : ""}`;
  };
  const drawAutoclean = (refocus) => {
    const zone = document.getElementById("compute-autoclean");
    if (!zone) return;
    zone.innerHTML = hasHub ? acBody() : "";
    for (const sw of zone.querySelectorAll("[data-cswitch]")) {
      sw.onclick = () => {
        const cur = ac();
        const body = {
          enabled: cur.enabled === true,
          force: cur.force === true,
          orphan_age_seconds: cur.orphan_age_seconds || p.limits.orphanAge,
        };
        if (sw.dataset.cswitch === "autoclean") {
          body.enabled = !body.enabled;
          // "The orphaned setting" is the field above: switching auto
          // clean on takes this sheet's age as the hub's threshold too.
          if (body.enabled) body.orphan_age_seconds = resolveAge();
        } else {
          body.force = !body.force;
        }
        postClean(body, sw.dataset.cswitch);
      };
    }
    const note = document.getElementById("compute-ac-note");
    if (note) note.innerHTML = p.acError ? `<span class="error">${esc(p.acError)}</span>` : "";
    if (refocus) zone.querySelector(`[data-cswitch="${refocus}"]`)?.focus();
  };
  const postClean = (body, refocus) => {
    if (p.acBusy) return;
    p.acBusy = true;
    p.acError = null;
    drawAutoclean();
    apiPost("/v1/app/compute/settings", body).then(({ status, data }) => {
      p.acBusy = false;
      if (status === 200 && data && data.autoclean) {
        if (p.data) p.data.autoclean = data.autoclean;
        // An age typed while the request was out never reached the hub
        // (commitAge drops its own write while busy), so send it now —
        // carrying the switches just saved, not the stale request's.
        if (hasHub && p.limits.orphanAge !== data.autoclean.orphan_age_seconds) {
          postClean({ enabled: data.autoclean.enabled === true,
                      force: data.autoclean.force === true,
                      orphan_age_seconds: p.limits.orphanAge }, refocus);
          return;
        }
      } else {
        p.acError = (data && data.error && data.error.message)
          || (status ? `The hub answered ${status}.` : "Couldn't reach the hub.");
      }
      drawAutoclean(refocus);
    });
  };
  // OrphanAgeUnit.resolve: zero or less snaps back; the rest clamps to a
  // minute up to a year. resolveAge stores the field's value locally and
  // answers the seconds; commitAge also echoes it to the hub, which uses
  // it for auto clean.
  const resolveAge = () => {
    const n = Math.floor(Number(age.value));
    const secs = AGE_UNITS.find(([id]) => id === ageUnit.value)[2];
    if (!(n > 0)) {
      const [v, u] = splitAge(p.limits.orphanAge);
      age.value = v; ageUnit.value = u;
      return p.limits.orphanAge;
    }
    const seconds = Math.min(Math.max(n * secs, 60), 365 * 86400);
    if (seconds !== n * secs) { const [v, u] = splitAge(seconds); age.value = v; ageUnit.value = u; }
    if (seconds === p.limits.orphanAge) return seconds;
    p.limits.orphanAge = seconds;
    computeStore("limits", p.limits);
    repaint();
    return seconds;
  };
  const commitAge = () => {
    const before = p.limits.orphanAge;
    const seconds = resolveAge();
    if (!hasHub || seconds === before || p.acBusy) return;
    const cur = ac();
    postClean({ enabled: cur.enabled === true, force: cur.force === true,
                orphan_age_seconds: seconds });
  };
  age.onchange = commitAge;
  ageUnit.onchange = commitAge;
  memory.onchange = () => {
    p.limits.memoryGB = Number(memory.value);
    computeStore("limits", p.limits);
    repaint();
  };
  dialog.addEventListener("close", () => { commitAge(); dialog.remove(); });
  drawAutoclean();
  dialog.showModal();
}

registerPage("compute", renderCompute);
