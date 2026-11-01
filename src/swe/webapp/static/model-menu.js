/* The launch sheets' Model menu (#914), shared by sessions.js's New
 * session sheet, the Tasks page's New agent session panel and the
 * Repositories page's Open in session panel. The Mac and iPhone sheets
 * draw the same control.
 *
 * The menu lists "Harness default" (no `model` sent), the harness's
 * recent models newest first (the hub's `recent_models`), then
 * "Other…", which shows a text box for a typed model id. Each sheet
 * keeps one state object from `fresh()`, re-defaults it with
 * `redefault()` on a harness or machine change, and posts `value()`
 * when it is non-empty. Uses `esc` from app.js at call time. */
"use strict";

const ModelMenu = (() => {
  /* `kind` is "default", "recent" (`recent` names the model) or "other"
   * (`typed` holds the box). `dirty` is set by any pick the user makes,
   * Harness default included, and keeps it across re-defaults. */
  function fresh() {
    return { kind: "default", recent: "", typed: "", dirty: false };
  }

  /* The harness's recent models, newest first, deduped. An old hub sends
   * only the newest one (`last_models[h]` / `last_model`), so that one
   * stands in as a one-item list. */
  function recents(list, last) {
    const src = Array.isArray(list) ? list : (last ? [last] : []);
    const out = [];
    for (const m of src) if (typeof m === "string" && m.trim() && !out.includes(m)) out.push(m);
    return out;
  }

  /* The same for one `/v1/app/*?harnesses=1` item: its `recent_models`
   * list, or `last_model` from an old hub; none when it takes no model. */
  function harnessRecents(h) {
    return h && h.launch_model ? recents(h.recent_models, h.last_model) : [];
  }

  /* A harness or machine change: an untouched menu moves to the new
   * harness's newest model (or Harness default when it has none). A
   * picked recent model the new harness hasn't used counts as untouched,
   * so the sheet never sends another harness's model. `bare` makes the
   * untouched default Harness default even when the list has models: a
   * launch on a picked provider (#526), whose recent models came from
   * other providers' launches. The menu still lists them to pick. */
  function redefault(st, list, bare) {
    if (st.dirty && st.kind === "recent" && !list.includes(st.recent)) st.dirty = false;
    if (st.dirty) return;
    const auto = bare ? "" : (list[0] || "");
    st.kind = auto ? "recent" : "default";
    st.recent = auto;
  }

  /* The model to post: "" sends no `model` key. */
  function value(st) {
    if (st.kind === "recent") return st.recent;
    if (st.kind === "other") return st.typed.trim();
    return "";
  }

  /* Option values are positions, never the model ids themselves, so no
   * model name can collide with Harness default or Other…. */
  function optionValue(st, list) {
    if (st.kind === "other") return "other";
    const i = st.kind === "recent" ? list.indexOf(st.recent) : -1;
    return i >= 0 ? `r${i}` : "default";
  }

  /* The select, plus the typed-model box under it while Other… is
   * picked. `id` names the select; the box is `<id>-other`. */
  function html(st, list, id, disabled) {
    const on = optionValue(st, list);
    const opt = (v, label) => `<option value="${v}"${v === on ? " selected" : ""}>${esc(label)}</option>`;
    const off = disabled ? " disabled" : "";
    const box = st.kind === "other"
      ? `<input class="field gh-mono" id="${id}-other" type="text" autocomplete="off" spellcheck="false"
        aria-label="Model id" placeholder="Model id" value="${esc(st.typed)}"${off}>`
      : "";
    return `<select class="field" id="${id}" aria-label="Model"${off}>${
      opt("default", "Harness default")}${list.map((m, i) => opt(`r${i}`, m)).join("")}${
      opt("other", "Other…")}</select>${box}`;
  }

  function choose(st, list, v) {
    st.dirty = true;
    const i = /^r\d+$/.test(v) ? Number(v.slice(1)) : -1;
    if (v === "other") st.kind = "other";
    else if (i >= 0 && i < list.length) { st.kind = "recent"; st.recent = list[i]; }
    else st.kind = "default";
  }

  /* Wires the select and the box inside `root`. Showing or hiding the
   * box needs a repaint: `repaint()` redraws `root` (as little of the
   * sheet as it can), then focus lands on the box when it appeared, else
   * back on the select. A pick that leaves the box as it was repaints
   * nothing, so focus stays put. */
  function bind(root, st, list, id, repaint) {
    if (!root || !st) return;
    const sel = root.querySelector(`#${id}`);
    if (sel) sel.onchange = () => {
      const had = st.kind === "other";
      choose(st, list, sel.value);
      if ((st.kind === "other") === had) return;
      repaint();
      const next = root.querySelector(st.kind === "other" ? `#${id}-other` : `#${id}`);
      if (next) next.focus();
    };
    const box = root.querySelector(`#${id}-other`);
    if (box) box.oninput = () => { st.typed = box.value; st.dirty = true; };
  }

  return { fresh, recents, harnessRecents, redefault, value, html, bind };
})();
