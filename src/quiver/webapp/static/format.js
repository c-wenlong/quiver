/* Number, name and colour formatting, ported from the Mac app's
 * UsageFormat, HarnessMark and HarnessColor so both read the same. */
"use strict";

const Fmt = (() => {
  function compact(value) {
    const r = Math.round(value * 10) / 10;
    return r === Math.round(r) ? String(Math.round(r)) : r.toFixed(1);
  }

  function tokens(count) {
    for (const [limit, suffix] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
      if (count >= limit) return compact(count / limit) + suffix;
    }
    return String(count || 0);
  }

  function dollars(amount) {
    if (!amount) return "$0";
    if (amount < 0.01) return "<$0.01";
    return amount.toLocaleString("en-US", {
      style: "currency", currency: "USD",
      minimumFractionDigits: amount < 100 ? 2 : 0,
      maximumFractionDigits: amount < 100 ? 2 : 0,
    });
  }

  function ago(epoch, now = Date.now() / 1000) {
    const minutes = Math.floor((now - epoch) / 60);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}h ago`;
    return `${Math.floor(minutes / (24 * 60))}d ago`;
  }

  /* HarnessMark.names */
  const NAMES = {
    "claude": "Claude Code", "claude-desktop": "Claude Desktop", "codex": "Codex",
    "opencode": "OpenCode", "devin": "Devin", "devin-cloud": "Devin cloud",
    "droid": "Factory Droid", "copilot": "GitHub Copilot", "lmstudio": "LM Studio",
    "qwen-code": "Qwen Code", "mistral-vibe": "Mistral Vibe", "commandcode": "Command Code",
    "qoder-cn": "Qoder CN", "grok-bot": "Grok Bot", "grok": "Grok Build", "kilo": "Kilo Code",
    "goose": "Goose", "aider": "Aider", "zcode": "ZCode",
  };
  function harnessName(id) {
    return NAMES[id] || (id ? id[0].toUpperCase() + id.slice(1) : "");
  }

  /* HarnessColor.fixed, then the same fallback palette by FNV-1a. */
  const COLORS = {
    claude: "#d97757", codex: "#3b7ff0", opencode: "#139f8b", devin: "#c4860f",
    cursor: "#c08ce0", "grok-bot": "#8a73e8", pi: "#5fb3d9", droid: "#e06c8f",
    freebuff: "#8ac74f", cline: "#9aa0aa", gemini: "#e3b51e",
  };
  const PALETTE = ["#5aa2ff", "#f0a04b", "#4ec9b0", "#e06c8f", "#9b7ff0",
    "#8ac74f", "#5ac8e0", "#f0755f", "#d9b44a", "#c08ce0"];
  function harnessColor(id) {
    if (COLORS[id]) return COLORS[id];
    let h = 0x811c9dc5;
    for (const ch of new TextEncoder().encode(id || "")) {
      h ^= ch;
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return PALETTE[h % PALETTE.length];
  }

  /* The logos shipped in /app/: colour ones drawn as they are, mono ones
   * inverted in dark mode. */
  const LOGOS = {
    claude: "harness-claude-color.svg", codex: "harness-codex-color.svg",
    opencode: "harness-opencode-color.svg", gemini: "harness-gemini-color.svg",
    devin: "harness-devin.svg", cursor: "harness-cursor.svg", pi: "harness-pi.svg",
    droid: "harness-droid.svg", freebuff: "harness-freebuff.svg", cline: "harness-cline.svg",
  };
  function logo(id, cls = "logo") {
    const file = LOGOS[id];
    if (!file) return `<span class="${cls}" aria-hidden="true"></span>`;
    const mono = file.endsWith("-color.svg") ? "" : " mono";
    return `<img class="${cls}${mono}" src="/app/${file}" alt="${harnessName(id)}">`;
  }

  /* MarbleScale: one marble per round amount of spend (1, 2, 5 times a
   * power of ten, from a cent), the smallest that keeps the pile within
   * `capacity`. With no priced spend it counts tokens instead. */
  function marbleScale(shares, capacity) {
    const shown = shares.filter((s) => s.tokens > 0);
    const bySpend = shown.some((s) => s.cost_usd > 0);
    const values = shown.map((s) => (bySpend ? s.cost_usd : s.tokens));
    const largest = Math.max(0, ...values);
    const countsAt = (unit) => values.map((v) => Math.max(1, Math.round(v / unit)));
    const nextStep = (step) => {
      const magnitude = Math.pow(10, Math.floor(Math.log10(step) + 1e-9));
      const lead = Math.round(step / magnitude);
      const next = lead < 2 ? 2 * magnitude : lead < 5 ? 5 * magnitude : 10 * magnitude;
      return Math.round(next * 100) / 100;
    };
    let step = bySpend ? 0.01 : 1;
    let counts = countsAt(step);
    const sum = (xs) => xs.reduce((a, b) => a + b, 0);
    while (sum(counts) > Math.max(capacity, 1) && step <= largest) {
      step = nextStep(step);
      counts = countsAt(step);
    }
    return {
      bySpend, step,
      counts: shown.map((s, i) => ({ harness: s.harness, count: counts[i] })),
    };
  }

  /* UsageFormat.resetIn: "in 12m", "in 4h 28m", "in 25d"; minutes round up. */
  function resetIn(epoch, now = Date.now() / 1000) {
    const seconds = epoch - now;
    if (seconds <= 0) return "now";
    const minutes = Math.ceil(seconds / 60);
    if (minutes < 60) return `in ${minutes}m`;
    if (minutes < 24 * 60) {
      const rest = minutes % 60;
      return rest ? `in ${Math.floor(minutes / 60)}h ${rest}m` : `in ${minutes / 60}h`;
    }
    return `in ${Math.floor(minutes / (24 * 60))}d`;
  }

  /* UsageFormat.window: a limit window named as the harness's CLI does. */
  function windowLabel(label) {
    return { included: "Included", auto: "Auto", api: "API", grok_bot: harnessName("grok-bot") }[label]
      || label || "Usage";
  }

  /* "max_20x" -> "Max 20x"; blank or a dash means no plan. */
  function planTitle(plan) {
    if (!plan || plan === "—" || plan === "-") return "";
    return plan.replace(/_/g, " ").split(" ").filter(Boolean)
      .map((w) => (/^\d/.test(w) ? w : w[0].toUpperCase() + w.slice(1))).join(" ");
  }

  /* Money: whole dollars when integral, else cents. Balances: always cents. */
  function money(amount) {
    const whole = Math.round(amount) === amount;
    return amount.toLocaleString("en-US", { style: "currency", currency: "USD",
      minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
  }
  function balance(amount) {
    return amount.toLocaleString("en-US", { style: "currency", currency: "USD",
      minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* Harness.onDemandLines: [value, detail] for Cursor's on-demand spend. */
  function onDemand(od) {
    const used = od.used_usd || 0;
    if (od.state === "fixed" && od.limit_usd != null) {
      return [`${money(used)} / ${money(od.limit_usd)}`, `${money(Math.max(0, od.limit_usd - used))} remaining`];
    }
    if (od.state === "unlimited") {
      return [money(used), od.scope === "team-member" ? "No personal limit" : "No monthly limit"];
    }
    if (od.state === "disabled") return [used > 0 ? money(used) : "Disabled", "On-demand usage is off"];
    return [used > 0 ? money(used) : "Unavailable", "On-demand limit unavailable"];
  }

  /* Copy icon → green tick for a moment, the Mac app's doc.on.doc →
   * checkmark port. `copy` writes and answers whether it landed (plain
   * http has no clipboard API, so it falls back to the old command);
   * `copyTick` wires a button so a copy swaps its icon for a tick. */
  const COPY_PATHS = '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>';
  const TICK_PATHS = '<path d="m5 12 5 5 9-10"/>';
  const strokeIcon = (paths) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const copyIcon = () => strokeIcon(COPY_PATHS);

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    area.remove();
    return ok;
  }

  /* ``text`` may be a string or a function answering it (the value is
   * read at click time). ``onFail`` runs when neither clipboard path
   * worked, so the caller can show its own hint. */
  function copyTick(btn, text, onFail) {
    if (!btn || btn.dataset.copyWired) return;
    btn.dataset.copyWired = "1";
    const svg = btn.querySelector("svg");
    const restore = svg ? svg.outerHTML : btn.innerHTML;
    btn.addEventListener("click", async () => {
      // ``text`` may answer a promise (a value fetched at click time).
      const value = await (typeof text === "function" ? text() : text);
      if (value == null || value === "") return;
      if (!(await copy(String(value)))) {
        if (onFail) onFail(btn);
        return;
      }
      clearTimeout(Number(btn.dataset.copyTimer || 0));
      btn.classList.add("copied");
      const cur = btn.querySelector("svg");
      if (cur) cur.outerHTML = strokeIcon(TICK_PATHS);
      else btn.innerHTML = strokeIcon(TICK_PATHS);
      btn.dataset.copyTimer = String(setTimeout(() => {
        btn.classList.remove("copied");
        const done = btn.querySelector("svg");
        if (done) done.outerHTML = restore;
        else btn.innerHTML = restore;
        delete btn.dataset.copyTimer;
      }, 2000));
    });
  }

  return { tokens, dollars, ago, harnessName, harnessColor, logo, marbleScale,
    resetIn, windowLabel, planTitle, money, balance, onDemand,
    copyIcon, copy, copyTick };
})();
