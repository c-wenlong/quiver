/* Models: the models in the hub's usage logs, with the lab behind each,
 * the harnesses that ran it, its list price and your tokens. The Mac
 * app's Models page is built on Artificial Analysis benchmarks fetched
 * with an API key only the app holds; that part stays in the Mac app,
 * and the page says so. Shares helpers with pricing.js (QuiverPricing),
 * which loads after this file but before any page renders. */
(() => {
  "use strict";

  const SUBTITLE = "The models you use and their list price. Benchmarks from Artificial Analysis are in the Mac app for now.";
  const RESULT_LIMIT = 100;
  const state = { data: null, period: "month", harness: [], query: "", search: null, seq: 0, searchSeq: 0,
    menuOpen: false, timer: null };
  const kit = () => window.QuiverPricing;

  async function render(_id, rescan = false) {
    const gen = routeGen, seq = ++state.seq;
    if (!state.data || rescan) {
      document.getElementById("panel").innerHTML = `<div class="page qp-page">${header(true)}<div class="card"><p class="empty">${
        state.data ? "Rescanning…" : "Reading your usage…"}</p></div></div>`;
      bindHeader();
    } else {
      paint();
    }
    const q = new URLSearchParams({ period: state.period, tz: timeZone() });
    if (state.harness.length) q.set("harness", state.harness.join(","));
    if (rescan) q.set("rescan", "1");
    const { status, data } = await api(`/v1/app/models?${q}`);
    if (gen !== routeGen || seq !== state.seq) return;
    if (status === 401) return renderGate("The hub doesn't know this browser's credential. It may have been revoked.");
    if (status !== 200 || !data) return pageError(header(false), status, data, () => render("models"));
    state.data = data;
    state.harness = data.harness_filter || [];
    paint();
  }

  function header(busy) {
    return pageHeader("Models", SUBTITLE,
      `<button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Rescan" title="Rescan">${icon("rescan")}</button>`);
  }

  function bindHeader() {
    bindRefresh(() => render("models", true));
  }

  function toolbar() {
    const d = state.data;
    return `<div class="filters qm-toolbar">
      ${kit().harnessMenu("qm-harness", d.harness_options, state.harness, state.menuOpen)}
      ${kit().periodSelect("qm-period", state.period)}
      <input class="field search" id="qm-search" type="search" placeholder="Search your models and LiteLLM's price list"
        value="${esc(state.query)}" autocomplete="off" spellcheck="false" aria-label="Search">
    </div>`;
  }

  /* What needs the Artificial Analysis key, said once above the table. */
  function macOnlyCard() {
    return `<div class="card qm-mac">
      <div class="card-title">Benchmarks need the Mac app</div>
      <p class="caption">Intelligence, coding, reasoning, speed and time-to-first-token scores, the chart,
        Artificial Analysis's blended cost and the top-model leaderboard come from Artificial Analysis with an
        API key only the Mac app holds (Settings › Services). Here: the models in the hub's usage logs and the
        list price the ledger charges them.</p></div>`;
  }

  function paint() {
    const d = state.data;
    const notes = (d.notes || []).map((n) => `<div class="card"><p class="note">${esc(n)}</p></div>`).join("");
    const { rows, overflow, empty } = visibleRows();
    const body = rows.length ? table(rows)
      + (overflow > 0 ? `<p class="caption muted qm-overflow">+${overflow} more matches. Tighten the query to list them.</p>` : "")
      : `<div class="card"><p class="empty">${esc(empty)}</p></div>`;
    document.getElementById("panel").innerHTML = `<div class="page qp-page">${header(false)}
      <div class="stack">${notes}${toolbar()}${macOnlyCard()}${body}</div></div>`;
    bindHeader();
    kit().bindHarnessMenu("qm-harness", state.harness, (next) => {
      state.harness = next;
      state.menuOpen = true;
      render("models");
    });
    const menu = document.getElementById("qm-harness");
    if (menu) menu.ontoggle = () => { state.menuOpen = menu.open; };
    const period = document.getElementById("qm-period");
    period.onchange = () => { state.period = period.value; state.menuOpen = false; render("models"); };
    const search = document.getElementById("qm-search");
    search.oninput = () => {
      state.query = search.value;
      clearTimeout(state.timer);
      // Keystrokes coalesce: one catalog search once the typist pauses.
      state.timer = setTimeout(runSearch, 150);
    };
  }

  /* Rows on screen: your models, or under a query the ones whose name
   * holds it followed by matching price list keys nobody ran, capped. */
  function visibleRows() {
    const used = state.data.rows;
    const needle = normalized(state.query);
    if (!needle) {
      return { rows: used, overflow: 0, empty: state.harness.length
        ? "None of these harnesses ran a model this period." : "No usage in this period." };
    }
    const hits = used.filter((r) => normalized(r.model).includes(needle));
    const s = state.search && normalized(state.search.query) === needle ? state.search : null;
    const seen = new Set(hits.map((r) => r.model));
    const lite = s ? s.matches.filter((m) => !seen.has(m.model)) : [];
    const all = hits.concat(lite);
    const shown = all.slice(0, RESULT_LIMIT);
    const overflow = s ? Math.max(0, s.total - s.matches.length) + (all.length - shown.length) : 0;
    return { rows: shown, overflow, empty: s ? "No model or LiteLLM price key matches." : "Searching…" };
  }

  async function runSearch() {
    const query = state.query, seq = ++state.searchSeq, gen = routeGen;
    if (!normalized(query)) { state.search = null; return repaintKeepingFocus(); }
    repaintKeepingFocus();
    const { status, data } = await api(`/v1/app/models?${new URLSearchParams({ q: query })}`);
    if (gen !== routeGen || seq !== state.searchSeq) return;
    state.search = status === 200 && data ? data : { query, matches: [], total: 0 };
    repaintKeepingFocus();
  }

  function repaintKeepingFocus() {
    const before = document.getElementById("qm-search");
    const focused = before && document.activeElement === before;
    const at = before ? before.selectionStart : null;
    paint();
    const again = document.getElementById("qm-search");
    if (focused && again) { again.focus(); if (at != null) again.setSelectionRange(at, at); }
  }

  function normalized(text) {
    return (text || "").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  }

  const PRICE_TIP = "Cost: LiteLLM list price. Per 1M tokens: input · output · cache read. The price the ledger charges your usage; hover a row's price for its key and cache-write rate.";

  function table(rows) {
    return `<div class="card qm-table-card"><div class="table-wrap"><table class="qm-table">
      <thead><tr><th>Model</th><th>Ran in</th><th class="qm-price" title="${esc(PRICE_TIP)}">Cost / 1M</th>
        <th class="qm-tokens">Your tokens</th></tr></thead>
      <tbody>${rows.map(tableRow).join("")}</tbody></table></div></div>`;
  }

  function tableRow(r) {
    const more = r.harnesses.length > 3 ? `<span class="caption">+${r.harnesses.length - 3}</span>` : "";
    return `<tr>
      <td><div class="qm-model">${kit().brandLogo(r.brand)}<span class="ellipsis" title="${esc(r.model)}">${esc(r.model)}${
        r.qualifier ? `<span class="caption"> ${esc(r.qualifier)}</span>` : ""}</span></div></td>
      <td><div class="qm-ran">${r.harnesses.slice(0, 3).map((h) =>
        `<span title="${esc(Fmt.harnessName(h))}">${Fmt.logo(h)}</span>`).join("")}${more}</div></td>
      <td class="qm-price num">${priceCell(r.list_price)}</td>
      <td class="qm-tokens num caption">${r.tokens > 0 ? Fmt.tokens(r.tokens) : "—"}</td></tr>`;
  }

  /* ModelCard.price: whole dollars when round, "<$0.01" for a sliver. */
  function price(value) {
    if (value > 0 && value < 0.01) return "<$0.01";
    return Math.round(value) === value ? `$${value}` : `$${value.toFixed(2)}`;
  }

  function priceCell(p) {
    if (!p) return '<span class="muted" title="No LiteLLM price for this model">—</span>';
    const source = { litellm: "LiteLLM", quiver: "Quiver's price list", yours: "your price" }[p.source];
    const help = `${p.key}, from ${source}\nCache write ${price(p.cache_write)} / 1M`;
    return `<span title="${esc(help)}">${[p.input, p.output, p.cache_read].map(price).join(" · ")}</span>`;
  }

  registerPage("models", render);
})();
