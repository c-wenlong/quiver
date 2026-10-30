/* Shared by the Repositories and Inbox pages (repositories.js, inbox.js):
 * line icons for GitHub states, the gh setup card, a minute poll, the
 * writes' POST, and the PR body's markdown. The bundle runs each script in its own scope, so this
 * one hands the others a single global, GhUI. Not a page itself. */
"use strict";

const GhUI = (() => {
  /* A seal outline for "approved", like SF Symbols' checkmark.seal. */
  const sealPoints = (() => {
    const pts = [];
    for (let i = 0; i < 24; i++) {
      const r = i % 2 ? 9 : 10.6, a = ((i * 15) - 90) * Math.PI / 180;
      pts.push(`${(12 + r * Math.cos(a)).toFixed(2)},${(12 + r * Math.sin(a)).toFixed(2)}`);
    }
    return pts.join(" ");
  })();
  const filled = (shape, mark) => `${shape}<g style="stroke: var(--card)" stroke-width="2">${mark}</g>`;
  const disc = '<circle cx="12" cy="12" r="10" fill="currentColor" stroke="none"/>';

  /* Stand-ins for the Mac app's octicons and SF Symbols, 24-unit grid. */
  const PATHS = {
    prOpen: '<circle cx="6" cy="5.5" r="2.2"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="18" cy="18.5" r="2.2"/><path d="M6 7.7v8.6M18 16.3V9.5a3 3 0 0 0-3-3h-4.5"/><path d="m12.5 4-2.5 2.5L12.5 9"/>',
    prMerged: '<circle cx="6" cy="5.5" r="2.2"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="18" cy="12.5" r="2.2"/><path d="M6 7.7v8.6M6 7.7c0 3.2 2.6 4.8 5.5 4.8h4.3"/>',
    prClosed: '<circle cx="6" cy="5.5" r="2.2"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="18" cy="18.5" r="2.2"/><path d="M6 7.7v8.6M18 16.3v-5.3M15.8 3.6l4.4 4.4M20.2 3.6l-4.4 4.4"/>',
    prDraft: '<circle cx="6" cy="5.5" r="2.2"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="18" cy="18.5" r="2.2"/><path d="M6 7.7v8.6M18 13.6v.4M18 9v.4M18 4.4v.4"/>',
    issueOpen: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.4v.1"/>',
    issueClosed: '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16.2 9.6"/>',
    checkFill: filled(disc, '<path d="m7.6 12.4 3 3 5.8-6.3"/>'),
    xFill: filled(disc, '<path d="m8.8 8.8 6.4 6.4M15.2 8.8l-6.4 6.4"/>'),
    sealFill: filled(`<polygon points="${sealPoints}" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/>`,
      '<path d="m8 12.4 2.8 2.8 5.2-5.7"/>'),
    pencilFill: filled(disc, '<path d="m14.2 7.6 2.2 2.2-6.2 6.2H8v-2.2z"/>'),
    eyeFill: filled(disc, '<path d="M6.4 12s2-3.4 5.6-3.4 5.6 3.4 5.6 3.4-2 3.4-5.6 3.4S6.4 12 6.4 12z"/><circle cx="12" cy="12" r="1.2"/>'),
    running: '<path d="M4.5 11a7.6 7.6 0 0 1 13-4.4L20 9M20 4v5h-5M19.5 13a7.6 7.6 0 0 1-13 4.4L4 15M4 20v-5h5"/>',
    question: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.4v.6M12 16.6v.1"/>',
    slash: '<circle cx="12" cy="12" r="9"/><path d="M5.7 18.3 18.3 5.7"/>',
    minus: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    alert: '<path d="M12 3.6 2.6 20h18.8z"/><path d="M12 9.6v4.6M12 17v.1"/>',
    tag: '<path d="M3.5 11.5V4.5a1 1 0 0 1 1-1h7l9 9-8 8z"/><circle cx="8" cy="8" r="1.4"/>',
    branch: '<circle cx="6" cy="5.5" r="2.2"/><circle cx="6" cy="18.5" r="2.2"/><circle cx="18" cy="6.5" r="2.2"/><path d="M6 7.7v8.6M18 8.7v.8a3.5 3.5 0 0 1-3.5 3.5h-5A3.5 3.5 0 0 0 6 16.3"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.4 2.6 3.6 5.6 3.6 9s-1.2 6.4-3.6 9c-2.4-2.6-3.6-5.6-3.6-9S9.6 5.6 12 3z"/>',
    external: '<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5"/>',
    chevronRight: '<path d="m9.5 6 6 6-6 6"/>',
    chevronLeft: '<path d="m14.5 6-6 6 6 6"/>',
    chevronDown: '<path d="m6 9.5 6 6 6-6"/>',
    chevronUp: '<path d="m6 14.5 6-6 6 6"/>',
    stack: '<path d="m12 3.5 8.5 4.3-8.5 4.3-8.5-4.3z"/><path d="m3.5 12 8.5 4.3 8.5-4.3M3.5 16.2l8.5 4.3 8.5-4.3"/>',
    bell: '<path d="M6 10a6 6 0 0 1 12 0c0 5.5 2.5 7 2.5 7h-17S6 15.5 6 10z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    bubble: '<path d="M4 5h16v11H9.5L4 20z"/>',
    commit: '<circle cx="12" cy="12" r="3.5"/><path d="M3 12h5.5M15.5 12H21"/>',
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 10 3 2-3 2M12 15h5"/>',
    copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v9.5a1 1 0 0 0 1 1h3.5"/>',
    tray: '<path d="M3 13h5l1 3h6l1-3h5M5 5h14l2 8v6H3v-6z"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    envelope: '<rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="m3.5 7 8.5 6 8.5-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    more: '<circle cx="6" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="18" cy="12" r="1.3" fill="currentColor"/>',
    rerun: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4.5v4h4"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/>',
    minusCircle: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    grip: '<circle cx="9" cy="6" r="1" fill="currentColor"/><circle cx="15" cy="6" r="1" fill="currentColor"/><circle cx="9" cy="12" r="1" fill="currentColor"/><circle cx="15" cy="12" r="1" fill="currentColor"/><circle cx="9" cy="18" r="1" fill="currentColor"/><circle cx="15" cy="18" r="1" fill="currentColor"/>',
    star: '<path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>',
    envelopeOpen: '<path d="M3 10.5 12 4l9 6.5V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="m3.5 11 8.5 5.5 8.5-5.5"/>',
    xmark: '<path d="M7 7l10 10M17 7 7 17"/>',
  };

  function ico(name, cls = "", title = "") {
    const t = title ? `<title>${esc(title)}</title>` : "";
    return `<svg class="gh-ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round" ${title ? 'role="img"' : 'aria-hidden="true"'}>${t}${PATHS[name] || ""}</svg>`;
  }

  /* gh's ISO 8601 stamps as the app's "4m ago"; "" when there is none. */
  function ago(iso) {
    const t = typeof iso === "number" ? iso : Date.parse(iso || "");
    if (!t || isNaN(t)) return "";
    return Fmt.ago(typeof iso === "number" ? iso : t / 1000);
  }

  /* GitHubModel.envSetup: gh missing or signed out gets the setup card;
   * an upstream-error probe (the probe failed, not the login) does not. */
  function envSetup(github) {
    const s = github && github.status;
    return s === "unavailable" || s === "auth-required" ? s : null;
  }

  /* GhSetupCard, with a line saying which machine's gh it means. */
  function setupCard(status, machine) {
    const title = status === "unavailable"
      ? "Download the gh CLI to enable GitHub features." : "Sign in to GitHub to enable GitHub features.";
    const command = status === "unavailable" ? "brew install gh" : "gh auth login";
    return `<div class="card gh-setup">
      <div class="gh-setup-title">${ico("terminal")}<span>${esc(title)}</span></div>
      <div class="gh-setup-row">
        <code class="gh-command" id="gh-command">${esc(command)}</code>
        <button class="icon-button gh-copy" id="gh-copy" title="Copy the command" aria-label="Copy the command">${ico("copy")}</button>
        <span class="gh-spacer"></span>
        <button class="gh-button" id="gh-setup-refresh">${ico("refresh")}<span>Refresh</span></button>
      </div>
      <p class="caption">The web app reads GitHub through the hub's own gh login${
        machine ? `, so run it on ${esc(machine)}` : ", so run it on the hub machine"}.</p>
    </div>`;
  }

  function bindSetup(refresh) {
    const btn = document.getElementById("gh-setup-refresh");
    if (btn) btn.onclick = refresh;
    const copyBtn = document.getElementById("gh-copy");
    const cmd = document.getElementById("gh-command");
    /* On a copy that can't land, select the command so the user can copy
     * it by hand. */
    if (copyBtn && cmd) {
      Fmt.copyTick(copyBtn, () => cmd.textContent,
        () => window.getSelection().selectAllChildren(cmd));
    }
  }

  /* The pages refetch every minute while open and on screen, as the
   * app's do. The hub caches gh replies for that long, so a poll costs
   * at most one round. A tab in the background skips its ticks; coming
   * back after a minute or more reads at once instead of waiting up to
   * a minute for the next tick, as the app reloads a page it shows.
   * ``fn`` returns false when it skipped the read (a sheet open, a read
   * already running), so only a real read counts as the last one. */
  const POLL_MS = 60000;
  const polls = {};
  function stopPoll(page) {
    if (!polls[page]) return;
    clearInterval(polls[page].timer);
    delete polls[page];
  }
  function runPoll(page) {
    const entry = polls[page];
    if (!entry) return;
    if (currentRoute() !== page) return stopPoll(page);
    if (document.hidden) return;
    if (entry.fn() !== false) entry.last = Date.now();
  }
  let watching = false;
  function poll(page, fn) {
    stopPoll(page);
    polls[page] = { fn, last: Date.now(), timer: setInterval(() => runPoll(page), POLL_MS) };
    if (watching) return;
    watching = true;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) return;
      for (const name of Object.keys(polls)) {
        if (Date.now() - polls[name].last >= POLL_MS) runPoll(name);
      }
    });
  }

  /* A read answered 401: the gate, and no more ticks for ``page``. A
   * tick would paint the page's last rows over the gate (and the pair
   * fields typed into it) before its own read came back 401 again. */
  function gate(page) {
    stopPoll(page);
    renderGate();
  }

  function hashParams() {
    return new URLSearchParams(location.hash.split("?")[1] || "");
  }

  /* A JSON POST through app.js's apiSend(): never throws, { status: 0 } when
   * the hub is unreachable. */
  const post = (path, body) => apiPost(path, body);

  function failMessage(status, data) {
    return (data && data.error && (data.error.message || data.error))
      || (status ? `The hub answered ${status}.` : "Couldn't reach the hub. Check the connection, then refresh.");
  }

  /* Keep a search box's focus and caret across a repaint. While the box
   * is mid-composition (bindSearch) the repaint would end the IME's
   * unfinished word, so it is skipped: the box's compositionend repaints
   * with whatever landed meanwhile (a poll's spinner or its reply). */
  function keepFocus(id, paint) {
    const el = document.getElementById(id);
    if (el && el.composing) return;
    const had = el && document.activeElement === el;
    const at = had ? el.selectionStart : 0;
    paint();
    if (had) {
      const again = document.getElementById(id);
      if (again) { again.focus(); again.setSelectionRange(at, at); }
    }
  }

  /* ------------------------------------------------------------ markdown */
  /* A port of the app's GitHubMarkdown: <details> folds, an HTML-to-
   * markdown pass, then fences, headings, quotes, lists, tasks, rules and
   * paragraphs whose soft breaks stay line breaks. Every piece of text is
   * escaped; links open only http(s) and mailto. */

  function decode(s) {
    return s.replace(/&nbsp;/g, " ").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");
  }

  function attr(attrs, name) {
    const m = new RegExp(`${name}\\s*=\\s*("([^"]*)"|'([^']*)'|([^\\s>]+))`, "i").exec(attrs || "");
    return m ? (m[2] ?? m[3] ?? m[4] ?? "") : "";
  }

  /* A fence runs to its closing marker, or to the end of the body. */
  const FENCE = /(^ {0,3}(?:`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n {0,3}(?:`{3,}|~{3,})[ \t]*(?=\n|$)|(?![\s\S])))/m;

  /* An inline code span: a run of backticks, then the same run closing
   * it, so ``a `tick` inside`` stays one span. */
  const CODE_SPAN = /(`+)(?!`)([^\n]*?[^`\n])\1(?!`)/g;
  /* A link target: no spaces, parentheses only in balanced pairs. */
  const URL_PART = "((?:[^()\\s]|\\([^()\\s]*\\))+)";

  /* The body with fenced and inline code blanked out, same length, so a
   * tag search on it never matches an example inside code. */
  function maskCode(text) {
    const blank = (c) => c.replace(/[^\n]/g, " ");
    return text.replace(new RegExp(FENCE.source, "gm"), blank).replace(CODE_SPAN, blank);
  }

  /* Top-level <details> blocks become folds; unclosed ones stay text, and
   * so does one inside fenced or inline code. */
  function segments(body) {
    const out = [];
    let rest = body;
    for (;;) {
      const masked = maskCode(rest);
      const open = /<details\b[^>]*>/i.exec(masked);
      if (!open) break;
      const tagRe = /<(\/?)details\b[^>]*>/gi;
      tagRe.lastIndex = open.index + open[0].length;
      let depth = 1, m, close = null;
      while ((m = tagRe.exec(masked))) {
        depth += m[1] ? -1 : 1;
        if (!depth) { close = m; break; }
      }
      if (!close) break;
      if (open.index) out.push({ md: rest.slice(0, open.index) });
      const inside = rest.slice(open.index + open[0].length, close.index);
      const sum = /<summary\b[^>]*>[\s\S]*?<\/summary>/i.exec(maskCode(inside));
      const summary = sum ? inside.slice(sum.index, sum.index + sum[0].length) : "";
      const title = sum ? htmlToMd(summary.replace(/^<summary\b[^>]*>|<\/summary>$/gi, "")).replace(/\s+/g, " ").trim() : "Details";
      const inner = sum ? inside.slice(0, sum.index) + inside.slice(sum.index + sum[0].length) : inside;
      out.push({ title: title || "Details", body: segments(inner) });
      rest = rest.slice(close.index + close[0].length);
    }
    if (rest.trim()) out.push({ md: rest });
    return out;
  }

  /* HTML in a body as markdown, leaving fenced code alone. */
  function htmlToMd(text) {
    const parts = text.split(FENCE);
    // Blank-line cleanup only outside fences: code keeps its own lines.
    return parts.map((part, i) => (i % 2 ? part : convertHtml(part).replace(/\n{3,}/g, "\n\n"))).join("");
  }

  /* ``nested`` is a blockquote's inside: the outer call already set its
   * code spans aside and puts them back itself. */
  function convertHtml(s, nested = false) {
    if (!/[<&]/.test(s)) return s;
    // Inline code spans pass through as they are.
    const codes = [];
    if (!nested) s = s.replace(CODE_SPAN, (c) => { codes.push(c); return `\u0001${codes.length - 1}\u0001`; });
    s = s.replace(/<!--[\s\S]*?-->/g, "")
      .replace(/<(script|style|textarea|template)\b[\s\S]*?<\/\1\s*>/gi, "")
      .replace(/<pre\b[^>]*>([\s\S]*?)<\/pre\s*>/gi, (_m, inner) =>
        `\n\n\`\`\`\n${decode(inner.replace(/<[^>]+>/g, "")).replace(/\n+$/, "")}\n\`\`\`\n\n`)
      .replace(/<blockquote\b[^>]*>([\s\S]*?)<\/blockquote\s*>/gi, (_m, inner) =>
        `\n\n${convertHtml(inner, true).trim().split("\n").map((l) => `> ${l}`).join("\n")}\n\n`)
      .replace(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi, (_m, attrs, inner) => {
        const href = attr(attrs, "href");
        return href ? `[${inner}](${href})` : inner;
      })
      .replace(/<img\b([^>]*)>/gi, (_m, attrs) => {
        const src = attr(attrs, "src");
        return src ? `![${attr(attrs, "alt") || "image"}](${src})` : "";
      });
    const lists = [];
    s = s.replace(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?>/g, (_m, close, name) => {
      const tag = name.toLowerCase();
      if (/^h[1-6]$/.test(tag)) return close ? "\n\n" : `\n\n${"#".repeat(+tag[1])} `;
      switch (tag) {
        case "code": case "tt": return "`";
        case "strong": case "b": return "**";
        case "em": case "i": return "*";
        case "s": case "del": case "strike": return "~~";
        case "br": case "wbr": return "\n";
        case "hr": return "\n\n---\n\n";
        case "ul": case "ol":
          if (close) { lists.pop(); return "\n"; }
          lists.push(tag); return "\n";
        case "li":
          if (close) return "";
          return `\n${"  ".repeat(Math.max(0, lists.length - 1))}${lists[lists.length - 1] === "ol" ? "1." : "-"} `;
        case "tr": return close ? "" : "\n";
        case "td": case "th": return close ? " " : "";
        case "p": case "div": case "section": case "article": case "figure": case "figcaption":
        case "table": case "thead": case "tbody": case "details": case "summary":
          return "\n\n";
        default: return "";
      }
    });
    s = decode(s);
    return nested ? s : s.replace(/\u0001(\d+)\u0001/g, (_m, i) => codes[+i]);
  }

  function blocks(md) {
    const lines = md.split("\n");
    const out = [];
    let para = [];
    const flush = () => { if (para.length) { out.push({ t: "p", text: para.join("\n") }); para = []; } };
    const isList = (l) => /^(\s*)([-+*]|\d+[.)])\s+/.test(l);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      let m;
      if ((m = /^ {0,3}(`{3,}|~{3,})\s*([^`\s]*)/.exec(line))) {
        flush();
        const marker = m[1];
        const closeRe = new RegExp(`^ {0,3}${marker[0] === "`" ? "`" : "~"}{${marker.length},}\\s*$`);
        const code = [];
        for (i++; i < lines.length && !closeRe.test(lines[i]); i++) code.push(lines[i]);
        out.push({ t: "code", code: code.join("\n"), lang: m[2] || "" });
        continue;
      }
      if (!line.trim()) { flush(); continue; }
      if ((m = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line))) {
        flush(); out.push({ t: "h", level: m[1].length, text: m[2] }); continue;
      }
      if (para.length && /^ {0,3}(=+|-+)\s*$/.test(line)) {
        const level = line.trim()[0] === "=" ? 1 : 2;
        out.push({ t: "h", level, text: para.join(" ") }); para = []; continue;
      }
      if (/^ {0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push({ t: "hr" }); continue; }
      if (/^ {0,3}>/.test(line)) {
        flush();
        const quote = [];
        for (; i < lines.length && /^ {0,3}>/.test(lines[i]); i++) quote.push(lines[i].replace(/^ {0,3}> ?/, ""));
        i--;
        out.push({ t: "quote", blocks: blocks(quote.join("\n")) });
        continue;
      }
      if (isList(line)) {
        flush();
        const items = [];
        for (; i < lines.length && isList(lines[i]); i++) {
          const lm = /^(\s*)([-+*]|\d+[.)])\s+(.*)$/.exec(lines[i]);
          const depth = Math.floor(lm[1].replace(/\t/g, "    ").length / 2);
          const task = /^\[([ xX])\]\s+(.*)$/.exec(lm[3]);
          items.push({
            depth,
            kind: task ? (task[1] === " " ? "todo" : "done") : /\d/.test(lm[2]) ? "ordered" : "bullet",
            n: parseInt(lm[2], 10) || 0,
            text: task ? task[2] : lm[3],
          });
        }
        i--;
        out.push({ t: "list", items });
        continue;
      }
      para.push(line.trim());
    }
    flush();
    return out;
  }

  /* [![alt](badge)](href) and [[alt](badge)](href) keep the outer link;
   * a bare image becomes a link to its source. */
  function flatLinks(s) {
    return s.replace(new RegExp(`\\[!?\\[([^\\]]*)\\]\\([^)]*\\)\\]\\(${URL_PART}\\)`, "g"), "[$1]($2)")
      .replace(new RegExp(`!\\[([^\\]]*)\\]\\(${URL_PART}[^)]*\\)`, "g"), (_m, alt, src) => `[${alt || "image"}](${src})`);
  }

  const safeUrl = (url) => safeHref(url) !== "";

  function inline(text) {
    const codes = [];
    let s = flatLinks(text.replace(CODE_SPAN, (_m, _ticks, c) => {
      // One space padding both ends is the delimiter's, not the code's.
      codes.push(/^ .* $/.test(c) && c.trim() ? c.slice(1, -1) : c);
      return `\u0001${codes.length - 1}\u0001`;
    }));
    s = esc(s);
    s = s.replace(new RegExp(`\\[([^\\]]*)\\]\\(${URL_PART}(?:\\s+&quot;.*?&quot;)?\\)`, "g"), (m, label, url) => {
      // Code spans bind tighter than links (CommonMark): a target holding
      // one is no link, just text with code in it.
      if (url.includes("\u0001")) return m;
      return safeUrl(url) ? `<a href="${url}" target="_blank" rel="noopener">${label || url}</a>` : label;
    });
    // Emphasis and bare links only in text, never inside a tag.
    s = s.split(/(<a [^>]*>[\s\S]*?<\/a>|<[^>]+>)/).map((part, i) => {
      if (i % 2) return part;
      // Bare URLs are set aside first, so emphasis never reaches inside one.
      const urls = [];
      return part
        .replace(/\bhttps?:\/\/[^\s<\u0001]+[^\s<\u0001.,;:!?)\]'"]/g, (url) => { urls.push(url); return `\u0002${urls.length - 1}\u0002`; })
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^\w])__([^_]+)__(?!\w)/g, "$1<strong>$2</strong>")
        .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
        .replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>")
        .replace(/~~([^~]+)~~/g, "<del>$1</del>")
        .replace(/\u0002(\d+)\u0002/g, (_m, n) => `<a href="${urls[+n]}" target="_blank" rel="noopener">${urls[+n]}</a>`);
    }).join("");
    return s.replace(/\u0001(\d+)\u0001/g, (_m, i) => `<code>${esc(codes[+i])}</code>`);
  }

  function renderBlocks(list) {
    return list.map((b) => {
      switch (b.t) {
        case "h": return `<div class="gh-md-h gh-md-h${Math.min(b.level, 4)}">${inline(b.text)}</div>`;
        case "p": return `<p>${b.text.split("\n").map(inline).join("<br>")}</p>`;
        case "code": return `<div class="gh-md-code">${b.lang ? `<div class="caption">${esc(b.lang)}</div>` : ""}<pre>${esc(b.code)}</pre></div>`;
        case "quote": return `<blockquote>${renderBlocks(b.blocks)}</blockquote>`;
        case "hr": return "<hr>";
        case "list": return `<div class="gh-md-list">${b.items.map((it) => {
          const marker = it.kind === "bullet" ? "•" : it.kind === "ordered" ? `${it.n}.`
            : `<input type="checkbox" disabled${it.kind === "done" ? " checked" : ""}>`;
          return `<div class="gh-md-item" style="padding-left:${it.depth * 14}px"><span class="gh-md-marker">${marker}</span><span>${inline(it.text)}</span></div>`;
        }).join("")}</div>`;
        default: return "";
      }
    }).join("");
  }

  function renderSegments(segs) {
    return segs.map((seg) => (seg.md != null
      ? renderBlocks(blocks(htmlToMd(seg.md)))
      : `<details class="gh-md-fold"><summary>${ico("chevronRight", "gh-fold-chevron")}<span>${inline(seg.title)}</span></summary>
          <div class="gh-md-fold-body">${renderSegments(seg.body)}</div></details>`)).join("");
  }

  function markdown(body) {
    return renderSegments(segments(String(body || "").replace(/\r\n?/g, "\n")));
  }

  return { ico, ago, envSetup, setupCard, bindSetup, poll, gate, hashParams, post, failMessage, keepFocus, markdown };
})();
globalThis.GhUI = GhUI;
