/* Calendar: ports the Mac app's CalendarPage. When each harness was
 * working, as blocks of activity per project on a day, week, N-day or
 * month grid, with the mini month and the harness legend on the right.
 * Data from /v1/app/calendar (quiver/webapp/pages/calendar.py); the lane
 * layout (CalendarLayout), month cells (MonthLayout) and the legend's
 * hidden toggles live here. Read only.
 *
 * Day, week and N-day grids slide sideways like the app's slidingGrid:
 * buffer days render each side of the visible range, a horizontal
 * scroll snaps to days (weeks in week mode), and once it comes to rest
 * the page fetches the range starting there and re-centres on it.
 */
"use strict";

(() => {
  const HOUR = 48;            // px per hour, as the app's hourHeight
  const MIN_HEIGHT = 18;      // a one-response block still gets a sliver
  const LANE_MIN = 56;        // narrower lanes fold into a "+N" chip
  const ROW = 16;             // a month entry row
  const CARD_ROWS = 8;        // sessions a hover card lists
  const STORE = "quiver.calendar.";

  const load = (key, fallback) => {
    try { const v = localStorage.getItem(STORE + key); return v == null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  };
  const save = (key, value) => {
    try { localStorage.setItem(STORE + key, JSON.stringify(value)); } catch { /* private window */ }
  };

  const S = {
    data: null, gen: 0, loading: false,
    mode: load("mode", "week"), n: load("n", 7), anchor: null,
    hidden: new Set(load("hiddenHarnesses", [])),
    hiddenAccounts: new Set(load("hiddenAccounts", [])),
    collapsed: new Set(load("collapsedAccounts", [])),
    panelCollapsed: !!load("panelCollapsed", false),
    mini: null, miniFor: null, scrolledKey: null, keepHour: false,
    blocks: new Map(), chips: new Map(),
    // The sliding grid: the day a swipe or step came to rest on (until
    // its range arrives), the column it rests at, a key step's column
    // still animating, the last scrollLeft seen, a response held back
    // while a swipe is moving, and when a hand (or a step) last moved it.
    lead: null, restIndex: 0, stepTarget: null, left: 0, scrolling: false, pending: null, armedAt: 0,
  };

  function persist() {
    save("mode", S.mode); save("n", S.n);
    save("hiddenHarnesses", [...S.hidden]); save("hiddenAccounts", [...S.hiddenAccounts]);
    save("collapsedAccounts", [...S.collapsed]); save("panelCollapsed", S.panelCollapsed);
  }

  /* ------------------------------------------------------------ formats */

  const pad = (n) => String(n).padStart(2, "0");
  const clock = (epoch) => { const d = new Date(epoch * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

  /* CalendarFormat.range: "09:41–10:02", or "09:41" inside one minute. */
  function span(start, end) {
    const a = clock(start), b = clock(end);
    return a === b && Math.floor(start / 60) === Math.floor(end / 60) ? a : `${a}–${b}`;
  }

  /* CalendarFormat.duration: "2h 5m", "47m", "<1m". */
  function duration(seconds) {
    const minutes = Math.floor(seconds / 60);
    if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    return minutes < 1 ? "<1m" : `${minutes}m`;
  }

  const plural = (n, word) => `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;

  /* Calendar dates as UTC midnights, so arithmetic never meets a DST day. */
  const D = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const ISO = (dt) => dt.toISOString().slice(0, 10);
  const addDays = (dt, n) => { const x = new Date(dt); x.setUTCDate(x.getUTCDate() + n); return x; };
  const isoWeekday = (dt) => ((dt.getUTCDay() + 6) % 7) + 1;
  const weekStart = (dt, fw) => addDays(dt, -((isoWeekday(dt) - fw + 7) % 7));
  /* Calendar.component(.weekOfYear), as pages/calendar.py's week_of_year
   * that titles the page "Week N": ISO weeks for a Monday-first calendar
   * (a first week needs four days of the year), and the week holding
   * January 1 as week 1 for any other first weekday (ISO, 1 = Monday). */
  function weekOfYear(dt, fw) {
    const minDays = fw === 1 ? 4 : 1;
    const firstWeek = (year) => {
      const jan1 = new Date(Date.UTC(year, 0, 1));
      const start = weekStart(jan1, fw);
      return 7 - (jan1 - start) / 864e5 >= minDays ? start : addDays(start, 7);
    };
    const year = dt.getUTCFullYear();
    if (dt >= firstWeek(year + 1)) return 1;
    let start = firstWeek(year);
    if (dt < start) start = firstWeek(year - 1);
    return Math.floor((dt - start) / 864e5 / 7) + 1;
  }
  const weekdayName = (iso, style) => D(iso).toLocaleDateString("en-US", { weekday: style, timeZone: "UTC" });
  const monthTitle = (y, m) => new Date(Date.UTC(y, m, 1)).toLocaleDateString("en-US",
    { month: "long", year: "numeric", timeZone: "UTC" });

  function firstWeekday() {
    try {
      const locale = new Intl.Locale(navigator.language || "en-US");
      const info = locale.getWeekInfo ? locale.getWeekInfo() : locale.weekInfo;
      if (info && info.firstDay >= 1 && info.firstDay <= 7) return info.firstDay;
    } catch { /* older browser */ }
    return 1;
  }

  const color = (harness) => Fmt.harnessColor(harness);
  const isShown = (b) => !S.hidden.has(b.harness) && !S.hiddenAccounts.has(`${b.harness}|${b.account}`);

  /* ------------------------------------------------------------ data */

  async function render(_id, rescan = false) {
    const gen = routeGen;
    const mine = ++S.gen;
    S.loading = true;
    if (!S.data) {
      document.getElementById("panel").innerHTML = `<div class="page cal-page">${header()}
        <div class="card"><p class="empty">Reading activity…</p></div></div>`;
      bindHeader();
    } else if (S.lead) {
      // A swipe landed: the grid already shows where it rests.
      const spin = document.getElementById("page-refresh");
      if (spin) spin.classList.add("spinning");
    } else {
      paint();
    }
    const q = new URLSearchParams({ mode: S.mode, n: S.n, anchor: S.anchor || "",
      tz: timeZone(), first_weekday: firstWeekday() });
    if (rescan) q.set("rescan", "1");
    const { status, data } = await api(`/v1/app/calendar?${q}`);
    if (gen !== routeGen || mine !== S.gen) return;
    S.loading = false;
    if (status === 401) return renderGate();
    if (status !== 200 || !data) {
      S.data = null;
      return pageError(header(), status, data, () => render("calendar"));
    }
    // Repainting under a moving swipe would yank it back; settle() paints.
    if (S.scrolling && data.mode !== "month") { S.pending = data; return; }
    apply(data);
  }

  function apply(data) {
    S.data = data;
    S.anchor = data.anchor;
    if (S.miniFor !== data.anchor) {
      const a = D(data.anchor);
      S.mini = { y: a.getUTCFullYear(), m: a.getUTCMonth() };
      S.miniFor = data.anchor;
    }
    paint();
  }

  function go(change, keepHour = false) {
    S.lead = null;
    Object.assign(S, change);
    S.keepHour = keepHour;
    persist();
    render("calendar");
  }

  /* A key or chevron step scrolls the grid one window, as a swipe
   * would, and lands when the scroll comes to rest. Past the buffer (a
   * fast run of presses before the last landing re-centred) it jumps. */
  function step(dir) {
    if (!S.data) return;
    if (!slides() || !document.getElementById("cal-scroll")) {
      return go({ anchor: dir < 0 ? S.data.previous : S.data.next }, true);
    }
    const days = renderDays(), span = S.data.days.length;
    const base = S.stepTarget != null ? S.stepTarget : S.restIndex;
    const target = base + dir * windowDays(S.data.mode, S.data.n);
    if (target >= 0 && target <= days.length - span) {
      S.stepTarget = target;
      S.armedAt = Date.now();
      slideTo(target);
      return;
    }
    const lead = ISO(addDays(D(days[base].date), dir * windowDays(S.data.mode, S.data.n)));
    halt();
    go({ anchor: lead, lead }, true);
  }
  const setMode = (mode, n) => {
    if (mode === "days" && n <= 1) mode = "day";
    go({ mode, n: mode === "days" ? Math.min(n, 9) : S.n });
  };

  /* ------------------------------------------------------------ header */

  const CHEVRON = {
    left: '<path d="m15 6-6 6 6 6"/>', right: '<path d="m9 6 6 6-6 6"/>',
    up: '<path d="m6 15 6-6 6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
  };
  const svg = (name, width = 1.6) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${width}"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CHEVRON[name]}</svg>`;

  const MODE_TITLE = { day: "Day", week: "Week", month: "Month" };
  const modeTitle = () => (S.mode === "days" ? `${S.n} days` : MODE_TITLE[S.mode] || "Week");

  function header() {
    const d = S.data;
    const title = d ? d.title : "Calendar";
    const subtitle = d ? d.subtitle : "When each harness was working";
    const modes = [["day", "Day"], ["week", "Week"], ["month", "Month"]];
    if (S.mode === "days") modes.push(["days", `${S.n} days`]);
    const noun = modeTitle().toLowerCase();
    return pageHeader(title, subtitle, `
      <select class="field cal-mode" id="cal-mode" aria-label="View" title="Day (D), Week (W), Month (M), 1 to 9 days">${options(modes, S.mode)}</select>
      <button class="tab cal-today" id="cal-today" title="Today (T)">Today</button>
      <button class="icon-button" id="cal-prev" aria-label="Previous ${noun}" title="Previous ${noun} (←)">${svg("left")}</button>
      <button class="icon-button" id="cal-next" aria-label="Next ${noun}" title="Next ${noun} (→)">${svg("right")}</button>
      <button class="icon-button" id="cal-panel" aria-label="${S.panelCollapsed ? "Expand" : "Collapse"} calendar panel"
        title="${S.panelCollapsed ? "Expand" : "Collapse"} calendar panel (S)">${svg("panel")}</button>
      <button class="icon-button${S.loading ? " spinning" : ""}" id="page-refresh" aria-label="Refresh" title="Refresh">${icon("rescan")}</button>`);
  }

  function bindHeader() {
    const byId = (id) => document.getElementById(id);
    if (byId("cal-mode")) byId("cal-mode").onchange = (e) => setMode(e.target.value, S.n);
    if (byId("cal-today")) byId("cal-today").onclick = () => go({ anchor: null });
    if (byId("cal-prev")) byId("cal-prev").onclick = () => step(-1);
    if (byId("cal-next")) byId("cal-next").onclick = () => step(1);
    if (byId("cal-panel")) byId("cal-panel").onclick = togglePanel;
    bindRefresh(() => render("calendar", true));
  }

  function togglePanel() {
    S.panelCollapsed = !S.panelCollapsed;
    persist();
    if (S.data) paint();
  }

  /* ------------------------------------------------------------ paint */

  /* Blocks overlapping the visible window, legend toggles applied. */
  function windowBlocks() {
    const { start, end } = S.data.range;
    return S.data.blocks.filter((b) => isShown(b) && b.start < end
      && (b.end > start || (b.start === b.end && b.start >= start)));
  }

  function paint() {
    const d = S.data;
    const old = document.getElementById("cal-scroll");
    const scrollTop = old ? old.scrollTop : null;
    hideCard();
    S.blocks = new Map(d.blocks.map((b) => [b.id, b]));
    S.chips = new Map();
    const notes = (d.notes || []).map((n) => `<div class="card"><p class="note">${esc(n)}</p></div>`).join("");
    const empty = windowBlocks().length ? "" : `<div class="cal-empty">${esc(d.empty)}</div>`;
    document.getElementById("panel").innerHTML = `<div class="page cal-page">
      ${header()}${notes}
      <div class="cal-body${S.panelCollapsed ? " collapsed" : ""}">
        <div class="cal-main">${d.mode === "month" ? monthGrid() : timeGrid()}${empty}</div>
        ${S.panelCollapsed ? "" : sidePanel()}
      </div></div>
      <dialog class="sheet" id="session-sheet"></dialog>
      <dialog class="sheet" id="cal-sheet"></dialog>
      <div class="cal-card" id="cal-card" hidden></div>`;
    bindHeader();
    bindPanel();
    bindCards();
    if (d.mode === "month") layoutMonth(); else layoutColumns();
    scrollGrid(scrollTop);
    if (slides()) {
      const days = renderDays();
      let i = S.lead ? days.findIndex((day) => day.date === S.lead) : -1;
      if (i < 0) i = days.findIndex((day) => day.date === d.days[0].date);
      S.stepTarget = null;
      place(Math.max(0, i));
      bindSlide();
    }
  }

  /* ------------------------------------------------------------ sliding */

  const slides = () => !!S.data && S.data.mode !== "month";
  const renderDays = () => S.data.render_days || S.data.days;
  /* Days one step moves, and days one snap target spans. */
  const windowDays = (mode, n) => (mode === "week" ? 7 : mode === "days" ? n : 1);
  const snapDays = (mode) => (mode === "week" ? 7 : 1);

  /* CalendarModel.settledLeadingDay: the column a horizontal scroll
   * rests on, the nearest snap target that keeps the visible span
   * inside the rendered columns. */
  function restingIndex(left, width, unit, count, span) {
    if (!(width > 0) || count <= span) return 0;
    const i = Math.round(left / (width * unit)) * unit;
    return Math.max(0, Math.min(count - span, i));
  }

  const colWidth = (scroll) => {
    const col = scroll.querySelector(".cal-col");
    return col ? col.getBoundingClientRect().width : 0;
  };
  const reduceMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Puts column i at the leading edge without counting as a swipe. */
  function place(i) {
    const scroll = document.getElementById("cal-scroll");
    if (!scroll) return;
    S.restIndex = i;
    scroll.scrollLeft = i * colWidth(scroll);
    S.left = scroll.scrollLeft;
  }

  function slideTo(i) {
    const scroll = document.getElementById("cal-scroll");
    if (scroll) scroll.scrollTo({ left: i * colWidth(scroll), behavior: reduceMotion() ? "auto" : "smooth" });
  }

  let settleTimer = 0;

  /* Stops a step's smooth scroll where it is and drops its landing, so
   * a jump past the buffer is not overtaken by the older step. */
  function halt() {
    const scroll = document.getElementById("cal-scroll");
    if (scroll) {
      scroll.scrollTo({ left: scroll.scrollLeft, behavior: "auto" });
      S.left = scroll.scrollLeft;
    }
    clearTimeout(settleTimer);
    Object.assign(S, { scrolling: false, stepTarget: null, armedAt: 0, pending: null });
  }

  function bindSlide() {
    const scroll = document.getElementById("cal-scroll");
    const rest = () => { clearTimeout(settleTimer); settle(); };
    // A hand on the grid takes over from a key step still animating.
    const arm = () => { S.armedAt = Date.now(); S.stepTarget = null; };
    for (const type of ["wheel", "touchstart", "pointerdown"]) scroll.addEventListener(type, arm, { passive: true });
    scroll.addEventListener("scroll", () => {
      if (scroll.scrollLeft === S.left) return;   // the hours, or the page's own place()
      S.left = scroll.scrollLeft;
      if (!S.scrolling && S.stepTarget == null && Date.now() - S.armedAt > 1000) {
        // The browser moved it (a re-snap after a layout change): no swipe to land.
        S.restIndex = restingIndex(scroll.scrollLeft, colWidth(scroll), snapDays(S.data.mode),
          renderDays().length, S.data.days.length);
        return;
      }
      S.scrolling = true;
      clearTimeout(settleTimer);
      settleTimer = setTimeout(rest, 150);         // Safari before scrollend
    });
    scroll.addEventListener("scrollend", () => { if (S.scrolling) rest(); });
  }

  /* The swipe came to rest: its leading column becomes the anchor and
   * the range starting there loads, the hour kept. A response that
   * arrived mid-swipe paints now, on the column the swipe rests at. */
  function settle() {
    if (!S.scrolling) return;
    const scroll = document.getElementById("cal-scroll");
    // A key step lands where it was headed, not on a pause mid-way; one
    // that never gets there lands where it stopped.
    if (scroll && S.stepTarget != null
        && Math.abs(scroll.scrollLeft - S.stepTarget * colWidth(scroll)) > colWidth(scroll) / 4) {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => { S.stepTarget = null; settle(); }, 1000);
      return;
    }
    S.scrolling = false;
    S.stepTarget = null;
    if (!scroll || !slides()) return;
    const days = renderDays();
    const i = restingIndex(scroll.scrollLeft, colWidth(scroll), snapDays(S.data.mode),
      days.length, S.data.days.length);
    const lead = days[i].date;
    S.restIndex = i;
    if (S.pending) {
      const data = S.pending;
      S.pending = null;
      S.lead = lead;
      apply(data);
    }
    // Compared with the range on its way too: swiping back to the shown
    // range before the next one arrives must replace that request.
    const wanted = S.loading && S.lead ? S.lead : S.data.days[0].date;
    if (lead !== wanted) go({ anchor: lead, lead }, true);
  }

  /* ------------------------------------------------------------ week grid */

  function timeGrid() {
    const d = S.data, today = d.today, days = renderDays(), unit = snapDays(d.mode);
    const heads = days.map((day) => {
      const now = day.date === today ? " today" : "";
      return `<div class="cal-dayhead${now}"><span class="wd">${weekdayName(day.date, "short")}</span>
        <span class="wdn">${weekdayName(day.date, "narrow")}</span><span class="dn">${D(day.date).getUTCDate()}</span></div>`;
    }).join("");
    const hours = Array.from({ length: 24 }, (_, h) => `<span class="cal-hour">${pad(h)}:00</span>`).join("");
    const cols = days.map((_, i) => `<div class="cal-col${i % unit ? "" : " snap"}" data-i="${i}"></div>`).join("");
    // One scroller both ways: the header row sticks to the top and the
    // hour axis to the left, so both track the columns' offsets.
    return `<div class="cal-time" style="--cols:${days.length};--span:${d.days.length}">
      <div class="cal-scroll" id="cal-scroll"><div class="cal-canvas">
        <div class="cal-head"><div class="cal-axis-gap"></div><div class="cal-heads" id="cal-heads">${heads}</div></div>
        <div class="cal-grid">
          <div class="cal-axis">${hours}<span class="cal-now-pill" id="cal-now-pill" hidden></span></div>
          <div class="cal-cols" id="cal-cols">${cols}</div>
        </div>
      </div></div></div>`;
  }

  /* Clock hours into the day, so a DST day never moves a block. */
  function clockOffset(epoch, day) {
    if (epoch >= day.end) return 24;
    if (epoch < day.start) return 0;
    const t = new Date(epoch * 1000);
    return t.getHours() + t.getMinutes() / 60 + t.getSeconds() / 3600;
  }

  /* CalendarLayout.layout: a day's pieces packed into overlap lanes; a
   * cluster needing more than maxLanes keeps its longest blocks and folds
   * the rest into "+N" chips on the last lane (a lone hidden piece keeps
   * the lane itself). */
  function layoutDay(blocks, day, minimum, maxLanes) {
    maxLanes = Math.max(2, maxLanes);
    const pieces = [];
    for (const block of blocks) {
      if (!(block.start < day.end && block.end >= day.start)) continue;
      // The stretch runs from the clipped start, so a block running past
      // midnight takes as much lane on the next day as it is drawn with.
      const start = Math.max(block.start, day.start);
      const end = Math.min(Math.max(block.end, start + minimum), day.end);
      if (end > start) pieces.push({ block, start, end });
    }
    pieces.sort((a, b) => a.start - b.start || b.end - a.end);
    const segments = [], overflows = [];
    let cluster = [], clusterEnd = -Infinity;
    const realSpan = (p) => p.block.end - p.block.start;
    const flush = () => {
      const laneEnds = [], lanes = [];
      for (const p of cluster) {
        let lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] > p.start) lane++;
        if (lane === laneEnds.length) laneEnds.push(p.end); else laneEnds[lane] = p.end;
        lanes.push(lane);
      }
      const needed = laneEnds.length;
      if (needed <= maxLanes) {
        cluster.forEach((p, i) => segments.push({ ...p, lane: lanes[i], count: needed }));
      } else {
        const ranked = [...cluster].sort((a, b) => realSpan(b) - realSpan(a)
          || a.block.start - b.block.start || (a.block.id < b.block.id ? -1 : 1));
        const placed = Array.from({ length: maxLanes - 1 }, () => []);
        const hidden = [];
        for (const p of ranked) {
          let lane = 0;
          while (lane < placed.length && placed[lane].some((q) => p.start < q.end && q.start < p.end)) lane++;
          if (lane < placed.length) placed[lane].push(p); else hidden.push(p);
        }
        placed.forEach((list, lane) => list.forEach((p) => segments.push({ ...p, lane, count: maxLanes })));
        hidden.sort((a, b) => a.start - b.start || (a.block.id < b.block.id ? -1 : 1));
        const groups = [];
        let groupEnd = -Infinity;
        for (const p of hidden) {
          if (groups.length && p.start < groupEnd) {
            groups[groups.length - 1].push(p);
            groupEnd = Math.max(groupEnd, p.end);
          } else {
            groups.push([p]);
            groupEnd = p.end;
          }
        }
        for (const group of groups) {
          if (group.length === 1) {
            segments.push({ ...group[0], lane: maxLanes - 1, count: maxLanes });
            continue;
          }
          overflows.push({ hidden: group, start: Math.min(...group.map((p) => p.start)),
            end: Math.max(...group.map((p) => p.end)), lane: maxLanes - 1, count: maxLanes });
        }
      }
      cluster = [];
      clusterEnd = -Infinity;
    };
    for (const p of pieces) {
      if (cluster.length && p.start >= clusterEnd) flush();
      cluster.push(p);
      clusterEnd = Math.max(clusterEnd, p.end);
    }
    if (cluster.length) flush();
    return { segments, overflows };
  }

  /* Harnesses whose blocks need the account named (more than one). */
  function multiAccount() {
    return new Set((S.data.legend || []).filter((l) => l.accounts.length > 1).map((l) => l.harness));
  }

  function layoutColumns() {
    const cols = document.querySelectorAll(".cal-col");
    if (!cols.length) return;
    const width = cols[0].clientWidth;
    const heads = document.getElementById("cal-heads");
    heads.className = `cal-heads ${width >= 64 ? "inline" : width >= 36 ? "stacked" : "compact"}`;
    const maxLanes = Math.max(2, Math.floor(width / LANE_MIN));
    const shown = S.data.blocks.filter(isShown);
    const multi = multiAccount();
    const pos = (lane, count, top, height) => `top:${top}px;height:${height}px;`
      + `left:calc(${lane} * 100% / ${count} + 1px);width:calc(100% / ${count} - 2px)`;
    renderDays().forEach((day, i) => {
      const { segments, overflows } = layoutDay(shown, day, (MIN_HEIGHT / HOUR) * 3600, maxLanes);
      const y = (t) => clockOffset(t, day) * HOUR;
      const parts = segments.map((s) => {
        const b = s.block, top = y(s.start), height = Math.max(MIN_HEIGHT, y(s.end) - top);
        const detail = s.end - s.start >= 1800
          ? `<span class="cal-block-range">${esc(span(b.start, b.end) + (multi.has(b.harness) ? ` · ${b.account_label}` : ""))}</span>` : "";
        return `<button class="cal-block" data-block="${esc(b.id)}" style="--c:${color(b.harness)};${pos(s.lane, s.count, top, height)}"
          aria-label="${esc(`${Fmt.harnessName(b.harness)}, ${b.project}, ${span(b.start, b.end)}`)}">
          <span class="cal-block-title">${esc(b.project)}</span>${detail}</button>`;
      });
      overflows.forEach((o, k) => {
        const id = `${i}:${k}`;
        S.chips.set(id, o);
        const top = y(o.start), height = Math.max(MIN_HEIGHT, y(o.end) - top);
        parts.push(`<button class="cal-chip" data-chip="${id}" style="${pos(o.lane, o.count, top, height)}"
          aria-label="${o.hidden.length} more blocks, ${clock(o.start)} to ${clock(o.end)}">+${o.hidden.length}</button>`);
      });
      cols[i].innerHTML = parts.join("") + '<div class="cal-now" hidden></div>';
    });
    updateNow();
  }

  /* The red now-line on today's column and its pill on the hour axis. */
  function updateNow() {
    if (!S.data || S.data.mode === "month") return;
    const now = Date.now() / 1000;
    const pill = document.getElementById("cal-now-pill");
    if (!pill) return;
    const days = renderDays();
    const i = days.findIndex((d) => d.start <= now && now < d.end);
    document.querySelectorAll(".cal-now").forEach((line, k) => {
      line.hidden = k !== i;
      if (k === i) line.style.top = `${clockOffset(now, days[i]) * HOUR}px`;
    });
    pill.hidden = i < 0;
    if (i >= 0) {
      pill.textContent = clock(now);
      pill.style.top = `${clockOffset(now, days[i]) * HOUR - 9}px`;
    }
  }

  /* Open each range at its earliest block (08:00 when empty), once; a
   * step sideways keeps the hour like a swipe; a repaint keeps it too. */
  function scrollGrid(previous) {
    const scroll = document.getElementById("cal-scroll");
    if (!scroll) return;
    const key = `${S.data.mode}|${S.data.range.start}`;
    if (S.scrolledKey === key) {
      if (previous != null) scroll.scrollTop = previous;
      return;
    }
    if (S.keepHour) {
      if (previous != null) scroll.scrollTop = previous;
      S.scrolledKey = key;
      S.keepHour = false;
      return;
    }
    const first = windowBlocks().map((b) => b.start).sort((a, b) => a - b)[0];
    const hour = first != null ? new Date(Math.max(first, S.data.range.start) * 1000).getHours() : 8;
    scroll.scrollTop = Math.max(0, Math.min(23, hour)) * HOUR;
    S.scrolledKey = key;
  }

  /* ------------------------------------------------------------ month grid */

  function monthGrid() {
    const d = S.data;
    const month = D(d.anchor).getUTCMonth();
    const heads = d.days.slice(0, 7).map((day) =>
      `<span class="cal-mwd">${weekdayName(day.date, "short")}</span>`).join("");
    const rows = [];
    for (let r = 0; r < 6; r++) {
      const week = d.days.slice(r * 7, r * 7 + 7);
      if (!week.length) break;
      const cells = week.map((day, c) => {
        const dt = D(day.date);
        const out = dt.getUTCMonth() !== month ? " out" : "";
        const today = day.date === d.today ? " today" : "";
        const label = dt.getUTCDate() === 1
          ? dt.toLocaleDateString("en-US", { month: "long", day: "numeric", timeZone: "UTC" })
          : String(dt.getUTCDate());
        return `<div class="cal-mcell${out}${today}${dt.getUTCDate() === 1 ? " first" : ""}" data-i="${r * 7 + c}" data-date="${day.date}">
          <div class="cal-mday"><span>${label}</span></div><div class="cal-mentries"></div></div>`;
      }).join("");
      rows.push(`<div class="cal-mrow"><span class="cal-mweek">${weekOfYear(D(week[0].date), d.first_weekday)}</span>${cells}</div>`);
    }
    return `<div class="cal-month"><div class="cal-mhead"><span class="cal-mweek"></span>${heads}</div>${rows.join("")}</div>`;
  }

  function dayCounts(ref, date) {
    const c = (ref.days || {})[date] || {};
    return { responses: c.responses || 0, tokens: c.tokens || 0, cost_usd: c.cost_usd || 0,
      unpriced_tokens: c.unpriced_tokens || 0 };
  }

  function unionRefs(a, b) {
    const out = new Map(a.map((r) => [r.id, { ...r }]));
    for (const r of b) {
      const have = out.get(r.id);
      if (!have) { out.set(r.id, { ...r }); continue; }
      have.start = Math.min(have.start, r.start);
      have.end = Math.max(have.end, r.end);
      for (const k of ["responses", "tokens", "cost_usd", "unpriced_tokens"]) have[k] += r[k];
      have.cwd = have.cwd || r.cwd;
    }
    return [...out.values()].sort((x, y) => x.start - y.start || (x.id < y.id ? -1 : 1));
  }

  /* MonthLayout.entries: one row per harness, account and project with
   * blocks that day; its block is the day's union of their sessions. */
  function monthEntries(blocks, day) {
    const groups = new Map();
    for (const b of blocks) {
      if (!(b.start < day.end && b.end >= day.start)) continue;
      const key = `${b.harness}|${b.account}|${b.project_key || b.project}`;
      const clippedStart = Math.max(b.start, day.start);
      const clipped = Math.min(b.end, day.end) - clippedStart;
      // Each session's counts for this day only, so an overnight block
      // never counts its responses, tokens or spend on both days.
      const refs = b.sessions.filter((r) => r.start < day.end && r.end >= day.start)
        .map((r) => ({ ...r, ...dayCounts(r, day.date),
          start: Math.max(r.start, day.start), end: Math.min(r.end, day.end) }));
      const g = groups.get(key);
      if (g) {
        g.active += clipped;
        if (clippedStart < g.start) g.start = clippedStart;
        if (b.start < g.earliest.start) g.earliest = b;
        g.sessions = unionRefs(g.sessions, refs);
      } else {
        groups.set(key, { key, earliest: b, start: clippedStart, active: clipped, sessions: refs });
      }
    }
    return [...groups.values()].map((g) => {
      const sum = (k) => g.sessions.reduce((n, r) => n + r[k], 0);
      const id = `month|${g.key}|${g.start}`;
      const block = { ...g.earliest, id, sessions: g.sessions, start: g.start,
        end: g.sessions.length ? Math.max(...g.sessions.map((r) => r.end)) : g.start,
        responses: sum("responses"), tokens: sum("tokens"), cost_usd: sum("cost_usd"),
        unpriced_tokens: sum("unpriced_tokens") };
      return { id, block, start: g.start, active: g.active };
    }).sort((a, b) => a.start - b.start || (a.id < b.id ? -1 : 1));
  }

  function layoutMonth() {
    const shown = S.data.blocks.filter(isShown);
    for (const cell of document.querySelectorAll(".cal-mcell")) {
      const day = S.data.days[Number(cell.dataset.i)];
      const entries = monthEntries(shown, day);
      const fitting = Math.max(0, Math.floor((cell.clientHeight - 18) / ROW));
      const shownCount = entries.length <= fitting ? entries.length : Math.max(0, fitting - 1);
      const more = entries.length - shownCount;
      const rows = entries.slice(0, shownCount).map((e) => {
        S.blocks.set(e.id, e.block);
        e.block.active = e.active;
        return `<button class="cal-mentry" data-block="${esc(e.id)}"
          aria-label="${esc(`${Fmt.harnessName(e.block.harness)}, ${e.block.project}, ${duration(e.active)}`)}">
          <span class="bar" style="background:${color(e.block.harness)}"></span>
          <span class="t num">${clock(e.start)}</span><span class="p">${esc(e.block.project)}</span></button>`;
      });
      if (more > 0) {
        const label = D(day.date).toLocaleDateString("en-US", { day: "numeric", month: "long", timeZone: "UTC" });
        rows.push(`<button class="cal-more" data-day="${day.date}" title="Show ${label} in the day view">${more} more</button>`);
      }
      cell.querySelector(".cal-mentries").innerHTML = rows.join("");
    }
    for (const cell of document.querySelectorAll(".cal-mcell")) {
      cell.onclick = (e) => {
        if (e.target.closest("[data-block]")) return;
        go({ anchor: cell.dataset.date, mode: "day" });
      };
    }
  }

  /* ------------------------------------------------------------ side panel */

  function sidePanel() {
    return `<aside class="cal-panel">${miniMonth()}
      <div class="cal-legend-head">Harnesses</div>
      <div class="cal-legend">${(S.data.legend || []).map(legendRows).join("")}</div></aside>`;
  }

  /* The mini month: week numbers down the edge, the visible range lit
   * (not in month mode), today a red dot, a click jumping there. */
  function miniMonth() {
    const d = S.data, fw = d.first_weekday;
    const { y, m } = S.mini;
    const first = new Date(Date.UTC(y, m, 1));
    const next = new Date(Date.UTC(y, m + 1, 1));
    let day = weekStart(first, fw);
    const weeks = [];
    while (weeks.length < 6 && (!weeks.length || day < next)) {
      weeks.push(Array.from({ length: 7 }, (_, i) => addDays(day, i)));
      day = addDays(day, 7);
    }
    const lo = d.days[0].date, hi = d.days[d.days.length - 1].date;
    const lit = d.mode !== "month";
    const initials = weeks[0].map((dt) => `<span>${weekdayName(ISO(dt), "narrow")}</span>`).join("");
    const rows = weeks.map((week) => {
      const cells = week.map((dt, c) => {
        const iso = ISO(dt);
        const inRange = lit && iso >= lo && iso <= hi;
        const cls = ["cal-mini-day", dt.getUTCMonth() !== m ? "out" : "", iso === d.today ? "today" : "",
          inRange ? "lit" : "",
          inRange && (c === 0 || ISO(week[c - 1]) < lo) ? "lit-start" : "",
          inRange && (c === 6 || ISO(week[c + 1]) > hi) ? "lit-end" : ""].filter(Boolean).join(" ");
        return `<button class="${cls}" data-go="${iso}"><span>${dt.getUTCDate()}</span></button>`;
      }).join("");
      return `<div class="cal-mini-row"><span class="cal-mini-week">${weekOfYear(week[0], fw)}</span>${cells}</div>`;
    }).join("");
    return `<div class="cal-mini">
      <div class="cal-mini-head"><span>${monthTitle(y, m)}</span>
        <button class="cal-mini-nav" data-mini="-1" aria-label="Previous month">${svg("up")}</button>
        <button class="cal-mini-nav" data-mini="1" aria-label="Next month">${svg("down")}</button></div>
      <div class="cal-mini-row cal-mini-initials"><span class="cal-mini-week"></span>${initials}</div>${rows}</div>`;
  }

  function checkbox(shown, harness) {
    return `<span class="cal-check${shown ? " on" : ""}" style="--c:${color(harness)}">${shown ? svg("check", 3) : ""}</span>`;
  }

  /* One legend row, its account rows under it once the harness has more
   * than one account (expanded until collapsed). Totals keep counting
   * while a row is hidden. */
  function legendRows(l) {
    const h = l.harness, name = Fmt.harnessName(h);
    const shown = !S.hidden.has(h);
    const multi = l.accounts.length > 1;
    const expanded = multi && !S.collapsed.has(h);
    const chevron = multi
      ? `<button class="cal-chev" data-expand="${esc(h)}" aria-label="${expanded ? "Collapse" : "Expand"} ${esc(h)} accounts"
          title="${expanded ? "Collapse" : "Expand"} accounts">${svg(expanded ? "down" : "right", 2)}</button>`
      : '<span class="cal-chev-gap"></span>';
    let html = `<div class="cal-lrow">${chevron}
      <button class="cal-toggle${shown ? "" : " off"}" data-harness="${esc(h)}" aria-pressed="${shown}"
        title="${shown ? "Hide" : "Show"} ${esc(name)}">${checkbox(shown, h)}${Fmt.logo(h, "logo logo-sm")}
        <span class="name">${esc(name)}</span><span class="time num">${duration(l.total_s)}</span></button></div>`;
    if (expanded) {
      html += l.accounts.map((a) => {
        const on = shown && !S.hiddenAccounts.has(`${h}|${a.home}`);
        return `<div class="cal-lrow acct"><span class="cal-chev-gap"></span>
          <button class="cal-toggle${on ? "" : " off"}" data-harness="${esc(h)}" data-home="${esc(a.home)}" aria-pressed="${on}"
            title="${on ? "Hide" : "Show"} ${esc(a.label)}">${checkbox(on, h)}
            <span class="name">${esc(a.label)}</span><span class="time num">${duration(a.total_s)}</span></button></div>`;
      }).join("");
    }
    return html;
  }

  function bindPanel() {
    for (const btn of document.querySelectorAll(".cal-toggle")) {
      btn.onclick = () => {
        const h = btn.dataset.harness, home = btn.dataset.home;
        const set = home == null ? S.hidden : S.hiddenAccounts;
        const key = home == null ? h : `${h}|${home}`;
        if (set.has(key)) set.delete(key); else set.add(key);
        persist();
        paint();
      };
    }
    for (const btn of document.querySelectorAll("[data-expand]")) {
      btn.onclick = () => {
        const h = btn.dataset.expand;
        if (S.collapsed.has(h)) S.collapsed.delete(h); else S.collapsed.add(h);
        persist();
        paint();
      };
    }
    for (const btn of document.querySelectorAll("[data-mini]")) {
      btn.onclick = () => {
        const dt = new Date(Date.UTC(S.mini.y, S.mini.m + Number(btn.dataset.mini), 1));
        S.mini = { y: dt.getUTCFullYear(), m: dt.getUTCMonth() };
        paint();
      };
    }
    for (const btn of document.querySelectorAll("[data-go]")) {
      btn.onclick = () => go({ anchor: btn.dataset.go });
    }
    for (const btn of document.querySelectorAll(".cal-more")) {
      btn.onclick = (e) => { e.stopPropagation(); go({ anchor: btn.dataset.day, mode: "day" }); };
    }
  }

  /* ------------------------------------------------------------ cards */

  function sessionRows(block, limit, open) {
    const refs = block.sessions;
    const list = limit ? refs.slice(0, limit) : refs;
    const rows = list.map((r) => `<div class="cal-srow">
        <div class="cal-srow-main"><span class="cal-srow-title">${esc(r.title || r.id)}</span>
          <span class="caption num">${esc(span(r.start, r.end))} · ${plural(r.responses, "response")}${
            r.tokens ? ` · ${Fmt.tokens(r.tokens)} tokens · ${Fmt.dollars(r.cost_usd)}` : ""}</span></div>
        ${open ? `<button type="button" class="cal-open" data-row="${esc(r.row)}" aria-label="Open session" title="Open session">${svg("right", 2)}</button>` : ""}
      </div>`).join("");
    const more = limit && refs.length > limit ? `<div class="caption cal-srow-more">+${refs.length - limit} more</div>` : "";
    return rows + more;
  }

  /* The block's figures: range, time, responses, tokens and spend. File
   * sources (Pi, Factory Droid, Freebuff) carry no tokens, so their
   * blocks stop at responses, as on the Mac. */
  function figures(block, active) {
    const unpriced = block.unpriced_tokens
      ? ` <span title="Plus ${Fmt.tokens(block.unpriced_tokens)} tokens with no list price">ⓘ</span>` : "";
    const spend = block.tokens || block.unpriced_tokens
      ? `<div class="caption num">${Fmt.tokens(block.tokens)} tokens · <span class="spend">${Fmt.dollars(block.cost_usd)}</span>${unpriced}</div>` : "";
    return `<div class="caption num">${esc(span(block.start, block.end))} · ${duration(active)} · ${plural(block.responses, "response")}</div>
      ${spend}`;
  }

  /* BlockCard: icon, project, account when the harness has several,
   * figures and up to eight sessions. */
  function blockCard(block) {
    const active = block.active != null ? block.active : block.end - block.start;
    const account = multiAccount().has(block.harness) ? `<div class="caption">${esc(block.account_label)}</div>` : "";
    return `<div class="cal-card-head">${Fmt.logo(block.harness)}<div class="cal-card-titles">
        <div class="cal-card-title">${esc(block.project)}</div>${account}</div></div>
      ${figures(block, active)}
      <div class="cal-sessions">${sessionRows(block, CARD_ROWS, false)}</div>`;
  }

  /* The chip's card: the hidden blocks, the hovered one's card beside. */
  function chipCard(chip) {
    const rows = chip.hidden.map((p, i) => {
      const b = p.block;
      const account = multiAccount().has(b.harness) ? `${b.account_label} · ` : "";
      return `<button class="cal-orow${i === 0 ? " on" : ""}" data-pick="${esc(b.id)}">${Fmt.logo(b.harness)}
        <span class="cal-orow-main"><span class="cal-orow-title">${esc(b.project)}</span>
        <span class="caption">${esc(account)}${esc(span(b.start, b.end))} · ${duration(b.end - b.start)} · ${plural(b.responses, "response")}</span></span></button>`;
    }).join("");
    return `<div class="cal-overflow"><div class="cal-overflow-list">
        <div class="caption cal-overflow-head">${chip.hidden.length} more · ${esc(span(chip.start, chip.end))}</div>${rows}</div>
      <div class="cal-overflow-detail" id="cal-overflow-detail">${blockCard(chip.hidden[0].block)}</div></div>`;
  }

  let showTimer = 0, hideTimer = 0, cardFor = null;

  function hideCard() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    const card = document.getElementById("cal-card");
    if (card) card.hidden = true;
    cardFor = null;
  }

  function placeCard(card, anchor) {
    card.hidden = false;
    const r = anchor.getBoundingClientRect();
    const w = card.offsetWidth, h = card.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = r.right + 8;
    if (left + w > vw - 8) left = r.left - w - 8;
    if (left < 8) left = Math.max(8, Math.min(vw - w - 8, r.left));
    const top = Math.max(8, Math.min(vh - h - 8, r.top));
    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
  }

  function showCard(anchor) {
    const card = document.getElementById("cal-card");
    if (!card) return;
    if (anchor.dataset.chip) {
      const chip = S.chips.get(anchor.dataset.chip);
      if (!chip) return;
      card.innerHTML = chipCard(chip);
      card.classList.add("wide");
      for (const row of card.querySelectorAll("[data-pick]")) {
        const block = S.blocks.get(row.dataset.pick);
        row.onmouseenter = () => {
          card.querySelectorAll(".cal-orow").forEach((x) => x.classList.toggle("on", x === row));
          document.getElementById("cal-overflow-detail").innerHTML = blockCard(block);
        };
        row.onclick = () => openBlock(block);
      }
    } else {
      const block = S.blocks.get(anchor.dataset.block);
      if (!block) return;
      card.innerHTML = blockCard(block);
      card.classList.remove("wide");
    }
    cardFor = anchor;
    placeCard(card, anchor);
  }

  function bindCards() {
    const card = document.getElementById("cal-card");
    const main = document.querySelector(".cal-main");
    if (!card || !main) return;
    const target = (e) => e.target.closest("[data-block], [data-chip]");
    // A mouse only: a tap opens the block straight away.
    main.onpointerover = (e) => {
      const el = target(e);
      if (!el || e.pointerType !== "mouse") return;
      clearTimeout(hideTimer);
      if (el === cardFor) return;
      clearTimeout(showTimer);
      showTimer = setTimeout(() => showCard(el), cardFor ? 80 : 450);
    };
    main.onpointerout = (e) => {
      if (!target(e) || e.pointerType !== "mouse") return;
      clearTimeout(showTimer);
      hideTimer = setTimeout(hideCard, 250);
    };
    card.onmouseenter = () => clearTimeout(hideTimer);
    card.onmouseleave = () => { hideTimer = setTimeout(hideCard, 250); };
    main.onclick = (e) => {
      const el = target(e);
      if (!el) return;
      e.stopPropagation();
      if (el.dataset.chip) { clearTimeout(showTimer); showCard(el); return; }
      const block = S.blocks.get(el.dataset.block);
      if (block) openBlock(block);
    };
    const scroll = document.getElementById("cal-scroll");
    if (scroll) scroll.onscroll = () => { if (cardFor) hideCard(); };
  }

  /* ------------------------------------------------------------ sheets */

  /* One session opens its detail straight away; several open the
   * project's list first, as in the app. */
  function openBlock(block) {
    hideCard();
    if (block.sessions.length === 1) return openSession(block.sessions[0].row);
    const sheet = document.getElementById("cal-sheet");
    const active = block.active != null ? block.active : block.end - block.start;
    sheet.innerHTML = `<form method="dialog" class="sheet-body">
      <div class="sheet-title">${Fmt.logo(block.harness)}<div class="cal-card-titles">
        <div>${esc(block.project)}</div>
        <div class="caption">${esc(span(block.start, block.end))} · ${plural(block.sessions.length, "session")}${
          multiAccount().has(block.harness) ? ` · ${esc(block.account_label)}` : ""}</div></div></div>
      <div class="cal-sheet-figures">${figures(block, active)}</div>
      <div class="cal-sessions boxed">${sessionRows(block, 0, true)}</div>
      <div class="sheet-actions"><button class="tab selected" value="close">Done</button></div></form>`;
    for (const btn of sheet.querySelectorAll("[data-row]")) {
      btn.onclick = () => openSession(btn.dataset.row);
    }
    sheet.showModal();
  }

  function openSession(key) {
    const row = S.data && S.data.sessions[key];
    if (row && typeof openSheet === "function") openSheet(row);
  }

  /* ------------------------------------------------------------ keys */

  const onCalendar = () => currentRoute() === "calendar";

  document.addEventListener("keydown", (e) => {
    if (!onCalendar() || !S.data || e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) return;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const act = {
      ArrowLeft: () => step(-1), ArrowRight: () => step(1),
      d: () => setMode("day", 1), w: () => setMode("week", 7), m: () => setMode("month", 42),
      t: () => go({ anchor: null }), s: togglePanel,
    }[key] || (/^[1-9]$/.test(key) ? () => setMode("days", Number(key)) : null);
    if (!act) return;
    e.preventDefault();
    act();
  });

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    if (!onCalendar() || !S.data) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      hideCard();
      if (S.data.mode === "month") layoutMonth(); else { layoutColumns(); place(S.restIndex); }
    }, 100);
  });
  const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  /* Each minute moves the now-line; past midnight the page reloads once
   * so the today marks follow (same range, so the hour stays). A reload
   * repaints the sheets, so it waits while one is open. */
  let reloadedFor = null;
  setInterval(() => {
    if (!onCalendar() || !S.data) return;
    const today = localDate();
    if (!S.loading && S.data.today !== today && reloadedFor !== today
        && !document.querySelector("dialog[open]")) {
      reloadedFor = today;
      render("calendar");
      return;
    }
    updateNow();
  }, 60 * 1000);

  registerPage("calendar", render);
  window.CalendarPage = { _test: { restingIndex, windowDays, snapDays, weekOfYear, D } };
})();
