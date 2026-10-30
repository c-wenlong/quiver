/* Pricing: the models the ledger saw, grouped by provider, with the price
 * each one resolves to and where it came from. Ports the Mac app's
 * PricingPage: a row opens its price form (PricingEditSheet), which saves
 * your own price or removes it, POST /v1/app/pricing/save|remove. Also
 * exports the helpers the Models page shares (QuiverPricing).
 * Everything sits in one closure: pages.js is a single script, so a
 * top-level name here could clash with another page's. */
(() => {
  "use strict";

  /* ------------------------------------------------------------ shared */

  /* The lab's logo (BrandLogo): an empty slot when the web app has none,
   * so names stay aligned. */
  function brandLogo(brand, cls = "logo") {
    if (!brand || !brand.logo) return `<span class="${cls}" aria-hidden="true"></span>`;
    return `<img class="${cls}${brand.mono ? " mono" : ""}" src="/${esc(brand.logo)}" alt="${esc(brand.name)}" title="${esc(brand.name)}">`;
  }

  /* A per-1M rate in plain dollars, as Overview's spend figures. */
  function perMillion(rate) {
    return rate == null ? "—" : `$${rate.toFixed(2)}`;
  }

  /* MultiSelectMenu's button title: "All harnesses", the one picked, or a count. */
  function harnessTitle(picked) {
    if (!picked.length) return "All harnesses";
    return picked.length === 1 ? Fmt.harnessName(picked[0]) : `${picked.length} harnesses`;
  }

  /* The harness filter: "All harnesses" plus every harness with usage in
   * the period, most tokens first, each a toggle. `open` keeps it open
   * across a repaint while the user is still picking. */
  function harnessMenu(id, options, picked, open) {
    const check = (on) => `<span class="qp-check">${on ? "✓" : ""}</span>`;
    const rows = [`<button type="button" class="qp-menu-row" data-pick="">${check(!picked.length)}<span>All harnesses</span></button>`]
      .concat(options.map((h) => `<button type="button" class="qp-menu-row" data-pick="${esc(h)}">${
        check(picked.includes(h))}${Fmt.logo(h)}<span>${esc(Fmt.harnessName(h))}</span></button>`));
    return `<details class="qp-menu" id="${id}"${open ? " open" : ""}>
      <summary class="field qp-menu-button">${esc(harnessTitle(picked))}<span class="qp-chevron" aria-hidden="true"></span></summary>
      <div class="qp-menu-list" role="menu">${rows.join("")}</div></details>`;
  }

  /* Wires a harnessMenu: `change(next picked list)` on every toggle. */
  function bindHarnessMenu(id, picked, change) {
    const menu = document.getElementById(id);
    if (!menu) return;
    for (const row of menu.querySelectorAll("[data-pick]")) {
      row.onclick = () => {
        const h = row.dataset.pick;
        const next = !h ? [] : picked.includes(h) ? picked.filter((x) => x !== h) : picked.concat([h]);
        change(next);
      };
    }
  }

  function periodSelect(id, period) {
    return `<select class="field" id="${id}" aria-label="Period">${options(PERIODS, period)}</select>`;
  }

  /* A click outside an open menu or popover closes it. */
  document.addEventListener("click", (e) => {
    // A pick repaints the page before the click reaches here: its target
    // is gone, and the menu it came from was redrawn open on purpose.
    if (!e.target.isConnected) return;
    for (const open of document.querySelectorAll("details.qp-menu[open], details.qp-pop[open]")) {
      if (!open.contains(e.target)) open.removeAttribute("open");
    }
  });

  /* A row name's hover text: an alias's real name with the logged id it
   * stands for, else the logged name. */
  function fullName(r) {
    return r.alias ? `${r.alias} (${r.model})` : r.model;
  }

  window.QuiverPricing = { brandLogo, fullName, perMillion, harnessMenu, bindHarnessMenu, periodSelect };

  /* ------------------------------------------------------------ page */

  const SUBTITLE = "Per 1M tokens. Your prices win over downloaded ones; saving reprices past usage.";
  const FOLD = 4;
  const state = { data: null, period: "month", harness: [], expanded: new Set(), seq: 0, menuOpen: false };

  async function render(_id, rescan = false) {
    const gen = routeGen, seq = ++state.seq;
    const panel = document.getElementById("panel");
    if (!state.data || rescan) {
      panel.innerHTML = `<div class="page qp-page">${header(true)}<div class="card"><p class="empty">${
        state.data ? "Rescanning…" : "Reading your usage logs…"}</p></div></div>`;
      bindHeader();
    } else {
      paint();
    }
    const q = new URLSearchParams({ period: state.period, tz: timeZone() });
    if (state.harness.length) q.set("harness", state.harness.join(","));
    if (rescan) q.set("rescan", "1");
    const { status, data } = await api(`/v1/app/pricing?${q}`);
    if (gen !== routeGen || seq !== state.seq) return;
    if (status === 401) return renderGate();
    if (status !== 200 || !data) return pageError(header(false), status, data, () => render("pricing"));
    state.data = data;
    state.harness = data.harness_filter || [];
    paint();
  }

  function header(busy) {
    const d = state.data;
    const unpriced = d && d.unpriced.length ? unpricedBadge(d.unpriced) : "";
    const controls = d ? harnessMenu("qp-harness", d.harness_options, state.harness, state.menuOpen)
      + periodSelect("qp-period", state.period) : "";
    return pageHeader("Pricing", SUBTITLE, `${unpriced}${controls}
      <button class="icon-button${busy ? " spinning" : ""}" id="page-refresh" aria-label="Rescan" title="Rescan">${icon("rescan")}</button>`);
  }

  function bindHeader() {
    bindRefresh(() => render("pricing", true));
    bindHarnessMenu("qp-harness", state.harness, (next) => {
      state.harness = next;
      state.menuOpen = true;
      render("pricing");
    });
    const menu = document.getElementById("qp-harness");
    if (menu) menu.ontoggle = () => { state.menuOpen = menu.open; };
    const period = document.getElementById("qp-period");
    if (period) period.onchange = () => { state.period = period.value; state.menuOpen = false; render("pricing"); };
    for (const row of document.querySelectorAll("[data-goto]")) {
      row.onclick = () => goTo(row.dataset.goto);
    }
  }

  /* The warning button: models with unpriced tokens, largest first. A
   * row scrolls to the model's row, unfolding its card if needed. */
  function unpricedBadge(list) {
    const help = list.length === 1 ? "1 model has no price" : `${list.length} models have no price`;
    return `<details class="qp-pop"><summary class="icon-button qp-warn" title="${help}" aria-label="${help}">${WARN}</summary>
      <div class="qp-menu-list qp-unpriced">${list.map((r) => `<button type="button" class="qp-menu-row" data-goto="${esc(r.model)}">
        ${brandLogo(r.brand, "logo qp-logo-14")}<span class="ellipsis qp-grow" title="${esc(fullName(r))}">${esc(r.alias || r.model)}</span>
        ${r.harnesses.slice(0, 3).map((h) => Fmt.logo(h, "logo logo-sm")).join("")}
        <span class="caption num">${Fmt.tokens(r.unpriced_tokens)}</span></button>`).join("")}</div></details>`;
  }

  function goTo(model) {
    const group = state.data.groups.find((g) => g.models.some((m) => m.model === model));
    if (group && !state.expanded.has(group.id) && group.models.findIndex((m) => m.model === model) >= FOLD) {
      state.expanded.add(group.id);
    }
    paint();
    const row = [...document.querySelectorAll(".qp-row")].find((r) => r.dataset.model === model);
    if (row) {
      row.scrollIntoView({ block: "center", behavior: "smooth" });
      row.classList.add("qp-flash");
      setTimeout(() => row.classList.remove("qp-flash"), 1600);
    }
  }

  function paint() {
    const d = state.data;
    const notes = (d.notes || []).map((n) => `<div class="card"><p class="note">${esc(n)}</p></div>`).join("");
    const body = d.groups.length ? d.groups.map(card).join("")
      : `<div class="card"><p class="empty">No usage in this period.</p></div>`;
    document.getElementById("panel").innerHTML = `<div class="page qp-page">${header(false)}
      <div class="stack">${notes}${body}
        <p class="caption footer">Click a model to set your own price. It saves to
          <code>~/.quiver/config/pricing.json</code> on ${esc(d.machine || "the hub")}, which the Mac app reads too.</p>
      </div></div>`;
    bindHeader();
    for (const el of document.querySelectorAll(".qp-row")) {
      el.onclick = () => openPrice(el.dataset.model);
      el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPrice(el.dataset.model); } };
    }
    for (const btn of document.querySelectorAll("[data-expand]")) {
      btn.onclick = () => {
        const id = btn.dataset.expand;
        if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
        paint();
      };
    }
  }

  function card(group) {
    const open = state.expanded.has(group.id);
    const shown = open ? group.models : group.models.slice(0, FOLD);
    const n = group.models.length;
    const hidden = n - FOLD;
    const fold = n > FOLD ? `<button type="button" class="fold qp-expand" data-expand="${esc(group.id)}"
        title="${esc(open ? `Show only the first ${FOLD}` : `Show every ${group.name} model`)}">
        <span class="qp-chevron${open ? " up" : ""}" aria-hidden="true"></span>${
        open ? "Show less" : hidden === 1 ? "Show 1 more" : `Show ${hidden} more`}</button>` : "";
    return `<div class="card qp-card">
      <div class="qp-card-head">${brandLogo(group.brand)}<span class="card-title">${esc(group.name)}</span>
        <span class="caption qp-count">${n} ${n === 1 ? "model" : "models"} · ${Fmt.tokens(group.tokens)} tokens</span></div>
      <div class="qp-grid qp-head"><span>Model</span><span class="qp-rate">Input</span><span class="qp-rate">Output</span>
        <span class="qp-rate qp-cache">Cache read</span><span class="qp-rate qp-cache">Cache write</span>
        <span class="qp-src">Source</span><span class="qp-num">Tokens</span></div>
      ${shown.map(row).join("")}${fold}</div>`;
  }

  /* "fast ×2" when the catalog knows the tier's multiplier, else the name. */
  function tierNote(row) {
    return row.tiers.map((t) => (t.multiplier == null ? t.name : `${t.name} ×${Number(t.multiplier)}`)).join(" · ");
  }

  function row(r) {
    const rates = r.rates || {};
    const cacheLine = `Cache read ${perMillion(rates.cache_read)} · Cache write ${perMillion(rates.cache_write)} per 1M`;
    const rate = (v, extra = "") => `<span class="qp-rate num${extra}${r.harness_billed ? " muted" : ""}" title="${esc(cacheLine)}">${perMillion(v)}</span>`;
    const help = sourceHelp(r);
    const note = tierNote(r);
    return `<div class="qp-grid qp-row" data-model="${esc(r.model)}" role="button" tabindex="0"
      aria-label="${esc(`Set a price for ${r.alias || r.model}`)}">
      <div class="qp-model">${brandLogo(r.brand)}
        <div class="qp-name"><div class="qp-line"><span class="ellipsis" title="${esc(fullName(r))}">${esc(r.alias || r.model)}</span>
          ${r.alias ? `<span class="caption muted qp-raw">${esc(r.model)}</span>` : ""}
          <span class="qp-harnesses">${r.harnesses.slice(0, 3).map((h) => Fmt.logo(h, "logo logo-sm")).join("")}</span>
          ${note ? `<span class="caption muted qp-tier">${esc(note)}</span>` : ""}</div>
          <div class="caption qp-src-caption${r.spend_source === "unpriced" ? " warn" : ""}" title="${esc(help)}">${esc(sourceName(r))}</div>
        </div></div>
      ${rate(rates.input)}${rate(rates.output)}${rate(rates.cache_read, " qp-cache")}${rate(rates.cache_write, " qp-cache")}
      <span class="qp-src" title="${esc(help)}" aria-label="${esc(sourceName(r))}">${sourceMark(r)}</span>
      <span class="qp-num num">${Fmt.tokens(r.tokens)}</span></div>`;
  }

  /* Who bills the row's logged charges: "Cursor", or "Cursor and Devin". */
  function billers(r) {
    return [...new Set(r.billed_by.map((b) => Fmt.harnessName(b)))].join(" and ");
  }

  function sourceName(r) {
    return { billed: `${billers(r)} bills`, free: "Free", yours: "Your price", quiver: "Quiver",
      litellm: "LiteLLM", unpriced: "No price" }[r.spend_source];
  }

  /* The Source mark's hover text: whose price the spend uses. */
  function sourceHelp(r) {
    if (r.spend_source === "billed") {
      const billed = `Billed by ${billers(r)}: spend uses the charge ${billers(r)} logs for each request.`;
      if (r.source === "yours") return `${billed} Your saved price is kept but not used.`;
      if (r.source) return `${billed} The rates shown are the API list price, for comparison.`;
      return billed;
    }
    // Part of a mixed row's usage may still carry a logged charge: the
    // sentence then speaks only for the rest, and says who bills that part.
    const mixed = r.billed_by.length > 0;
    const rest = mixed ? "usage without a logged charge" : "spend";
    const set = "Click the row to set one.";
    let text = {
      free: mixed ? "Free model: usage without a logged charge is priced at $0." : "Free model: priced at $0.",
      yours: `Your price: ${rest} uses the rates you saved.`,
      quiver: `API pricing from Quiver's own list, which fills in or corrects LiteLLM's${mixed ? ", for usage without a logged charge." : "."}`,
      litellm: `API pricing from LiteLLM's price list${mixed ? ", for usage without a logged charge." : "."}`,
      unpriced: mixed ? `No price: usage without a logged charge isn't in your spend. ${set}`
        : `No price: these tokens aren't in your spend. ${set}`,
    }[r.spend_source];
    if (mixed) text += ` Usage billed by ${billers(r)} uses the charge ${billers(r)} logs.`;
    return text;
  }

  const WARN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/></svg>';
  const PERSON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.5 18.5c1.2-2 3.2-3 5.5-3s4.3 1 5.5 3"/></svg>';
  const GIFT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v8h14v-8M12 8v12M12 8c-1.5-3-5-3.5-5-1.5S10 8 12 8zM12 8c1.5-3 5-3.5 5-1.5S14 8 12 8z"/></svg>';

  /* The biller's logo, LiteLLM's, Quiver's icon, or a symbol for your own
   * price, a free model and a missing price. */
  function sourceMark(r) {
    switch (r.spend_source) {
      case "billed": return r.billed_by.slice(0, 2).map((h) => Fmt.logo(h)).join("");
      case "litellm": return '<img class="logo mono" src="/source-litellm.svg" alt="LiteLLM">';
      case "quiver": return '<img class="logo qp-quiver" src="/icon-192.png" alt="Quiver">';
      case "yours": return `<span class="qp-symbol">${PERSON}</span>`;
      case "free": return `<span class="qp-symbol">${GIFT}</span>`;
      default: return `<span class="qp-symbol warn">${WARN}</span>`;
    }
  }

  /* ---------------------------------------------------------- price form */

  const modelRow = (model) => {
    for (const g of (state.data && state.data.groups) || []) {
      const r = g.models.find((m) => m.model === model);
      if (r) return r;
    }
    return null;
  };

  /* PricingEditSheet.rate: a per-1M rate as the text a field holds. */
  const rateText = (v) => (v == null ? "" : String(Number(v.toPrecision(6))));

  /* A field's number, null when it isn't one; blank is `blank`. */
  function rateValue(text, blank) {
    const t = text.trim();
    if (!t) return blank;
    const n = Number(t);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }

  /* The sheet lives outside the panel, so a repaint landing while it is
   * open (a rescan, a filter) never throws away what is being typed. */
  function priceSheet() {
    let sheet = document.getElementById("qp-sheet");
    if (!sheet) {
      sheet = document.createElement("dialog");
      sheet.className = "sheet qp-sheet";
      sheet.id = "qp-sheet";
      sheet.setAttribute("aria-label", "Price");
      document.body.appendChild(sheet);
    }
    return sheet;
  }

  function openPrice(model) {
    const r = modelRow(model), sheet = priceSheet();
    if (!r) return;
    const rates = r.rates || {};
    const field = (id, label, value) => `<label class="qp-field"><span class="caption">${label}</span>
      <input class="field" id="${id}" inputmode="decimal" placeholder="0.00" autocomplete="off" value="${esc(rateText(value))}"></label>`;
    const where = state.data.machine || "the hub";
    sheet.innerHTML = `<form class="sheet-body" id="qp-form" novalidate>
      <div class="sheet-title">${brandLogo(r.brand)}<span class="ellipsis" title="${esc(fullName(r))}">${esc(r.alias || r.model)}</span></div>
      ${r.harness_billed ? `<p class="caption">${esc(sourceHelp(r))} A saved price fills the rate columns and prices usage with no logged charge.</p>` : ""}
      <label class="qp-field"><span class="caption">Price applies to</span>
        <input class="field qp-key" id="qp-key" autocomplete="off" spellcheck="false" maxlength="200" value="${esc(r.key)}">
        <span class="caption muted">${state.harness.length ? "Price applies to every harness, filter or not." : "Covers every harness's usage of this name."}</span></label>
      <div class="qp-fields">${field("qp-in", "Input", rates.input)}${field("qp-out", "Output", rates.output)}
        ${field("qp-cr", "Cache read", rates.cache_read)}${field("qp-cw", "Cache write", rates.cache_write)}</div>
      <p class="caption muted">USD per 1M tokens. Blank cache fields count as 0. Saves to ~/.quiver/config/pricing.json on ${esc(where)}.</p>
      <p class="error" id="qp-error" role="alert" hidden></p>
      <div class="sheet-actions qp-actions">
        ${r.source === "yours" ? '<button type="button" class="qp-button danger-text" id="qp-remove">Remove your price</button>' : ""}
        <span class="qp-spacer"></span><span class="caption" id="qp-working"></span>
        <button type="button" class="qp-button" id="qp-cancel">Cancel</button>
        <button type="submit" class="qp-button primary" id="qp-save">Save</button>
      </div></form>`;
    const $ = (id) => document.getElementById(id);
    const valid = () => !!$("qp-key").value.trim() && rateValue($("qp-in").value, null) != null
      && rateValue($("qp-out").value, null) != null && rateValue($("qp-cr").value, 0) != null && rateValue($("qp-cw").value, 0) != null;
    const sync = () => { $("qp-save").disabled = !valid() || !!sheet.dataset.working; $("qp-error").hidden = true; };
    for (const id of ["qp-key", "qp-in", "qp-out", "qp-cr", "qp-cw"]) $(id).oninput = sync;
    $("qp-cancel").onclick = () => sheet.close();
    $("qp-form").onsubmit = (e) => {
      e.preventDefault();
      if (!valid()) return;
      writePrice(sheet, "save", "Saving…", {
        model: r.model, key: $("qp-key").value.trim(),
        input: rateValue($("qp-in").value, null), output: rateValue($("qp-out").value, null),
        cache_read: rateValue($("qp-cr").value, 0), cache_write: rateValue($("qp-cw").value, 0),
        replacing: r.source === "yours" ? r.entry : null,
      });
    };
    if ($("qp-remove")) $("qp-remove").onclick = () => writePrice(sheet, "remove", "Removing…", { model: r.model });
    // Each opening is its own form: a reply to an earlier one leaves it be.
    sheet.dataset.working = "";
    sheet.dataset.token = String(++priceForms);
    sync();
    if (!sheet.open) sheet.showModal();
    $("qp-in").focus();
  }

  let priceForms = 0;
  const onPricing = () => currentRoute() === "pricing";
  // The form sits outside the panel, so leaving the page must close it.
  window.addEventListener("hashchange", () => {
    const sheet = document.getElementById("qp-sheet");
    if (sheet && sheet.open && !onPricing()) sheet.close();
  });

  /* The sheet closes once the hub has written and repriced; the page then
   * repaints from its answer, read with the same period and filter. */
  async function writePrice(sheet, action, label, body) {
    const $ = (id) => document.getElementById(id);
    if (sheet.dataset.working) return;
    const token = sheet.dataset.token;
    sheet.dataset.working = "1";
    $("qp-working").textContent = label;
    for (const id of ["qp-save", "qp-remove", "qp-cancel"]) if ($(id)) $(id).disabled = true;
    const q = { period: state.period, tz: timeZone() };
    if (state.harness.length) q.harness = state.harness.join(",");
    const asked = JSON.stringify([state.period, state.harness]);
    const { status, data } = await apiPost(`/v1/app/pricing/${action}`, { ...body, ...q });
    // Only the form this request came from reacts to its answer.
    const mine = sheet.dataset.token === token && sheet.open && !!$("qp-form");
    if (mine) sheet.dataset.working = "";
    if (status === 401) {
      if (mine) sheet.close();
      if (onPricing()) renderGate();
      return;
    }
    if (status === 200 && data && data.page) {
      if (mine) sheet.close();
      if (!onPricing()) return;
      // The answer was read with the period and filter of the time; if
      // they have changed since, read the page again rather than show it.
      if (JSON.stringify([state.period, state.harness]) !== asked) { render("pricing"); return; }
      state.seq++;  // a read still in flight began before the write
      state.data = data.page;
      state.harness = data.page.harness_filter || [];
      paint();
      return;
    }
    if (!mine) return;
    $("qp-working").textContent = "";
    for (const id of ["qp-remove", "qp-cancel"]) if ($(id)) $(id).disabled = false;
    $("qp-save").disabled = false;
    const err = $("qp-error");
    err.textContent = failText(status, data);
    err.hidden = false;
  }

  registerPage("pricing", render);
})();
