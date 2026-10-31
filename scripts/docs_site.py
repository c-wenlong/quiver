"""Render the docs pages under ``app/landing-page/``.

The landing page's nav and footer links (``overview/``, ``cli/``,
``changelog/``, ``contributing/``) point at these generated pages. The
Markdown stays the source of truth; run this after changing a source or
``swe help``, and commit the result. ``--check`` exits 1 when committed
output is stale, so CI can enforce regeneration.

Markdown renders with ``mistune``, a docs-only dependency pinned in
``docs/requirements.txt``; nothing under ``src/`` imports it.
"""

from __future__ import annotations

import posixpath
import re
import sys
from pathlib import Path

GITHUB = "https://github.com/c-wenlong/quiver/blob/main/"
GITHUB_TREE = "https://github.com/c-wenlong/quiver/tree/main/"
RAW = "https://raw.githubusercontent.com/c-wenlong/quiver/main/"

# Markdown source -> the directory under app/landing-page/ that serves it.
SOURCES = {
    "README.md": "overview",
    "CHANGELOG.md": "changelog",
    "CONTRIBUTING.md": "contributing",
}

# Titles and descriptions for the rendered <head>; "cli" has no source
# file because it is generated from `swe help`.
TITLES = {
    "overview": ("Documentation — quiver", "Install, configure and use quiver."),
    "cli": ("CLI reference — quiver", "Every swe command and flag."),
    "changelog": ("Changelog — quiver", "Notable changes to quiver."),
    "contributing": ("Contributing — quiver", "How to work on quiver."),
}

# Repo files a staged page may link to that are copied into the site as-is.
ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".mp4", ".mov", ".woff", ".woff2",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# [text](target) / ![alt](target), optionally followed by a "title".
_INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Reference-style definitions at line start: [id]: target
_REF_DEF = re.compile(r"(?m)^([ ]{0,3}\[[^\]]+\]:[ ]*)(\S+)")
_FENCE = re.compile(r"(?m)^(```|~~~)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
_URLISH = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)")
# <img src="..."> / <a href="..."> inside markdown (README badges etc.)
_HTML_ATTR = re.compile(r'\b(src|href)="([^"]+)"')
_NON_WORD = re.compile(r"[^\w\- ]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def cli_page(root: Path) -> str:
    """The `swe help` output rendered as one page, one H3 per command."""
    sys.path.insert(0, str(root / "src"))
    from quiver.help_text import COMMAND_CATEGORIES, HELP  # noqa: PLC0415

    out = ["# CLI reference", "",
           "> Generated from `swe help` by `scripts/docs_site.py` — do not edit.",
           ""]
    for category, commands in COMMAND_CATEGORIES:
        out += [f"## {category}", ""]
        for primary, alias in commands:
            summary, detail = HELP[primary]
            title = f"swe {primary}" + (f" ({alias})" if alias else "")
            out += [f"### `{title}`", "", f"*{strip_ansi(summary)}*", "",
                    "```text", strip_ansi(detail).rstrip(), "```", ""]
    return "\n".join(out)


def _resolve(src_rel: str, target: str) -> str:
    """The repo-relative path a link in ``src_rel`` points at."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(src_rel), target))


def _slug_for(src_rel: str) -> str:
    return SOURCES.get(src_rel, "")


def rewrite_links(text: str, src_rel: str, root: Path, missing: list[str]) -> str:
    """Rewrite every link target in ``text`` (a page from ``src_rel``).

    Links to other staged pages become ``../<slug>/`` links inside the
    site, images and other copied assets point at raw.githubusercontent,
    other repo files and directories link to GitHub, and anything
    unresolvable lands in ``missing``. Fenced code blocks and inline code
    are left alone.
    """
    here = _slug_for(src_rel)

    def rewrite_one(target: str) -> str:
        if not target or _URLISH.match(target):
            return target
        path, _, anchor = target.partition("#")
        suffix = "#" + anchor if anchor else ""
        if not path:
            return target
        repo_rel = _resolve(src_rel, path)
        if repo_rel in SOURCES:
            return "../" + SOURCES[repo_rel] + "/" + suffix
        abs_path = root / repo_rel
        if Path(repo_rel).suffix.lower() in ASSET_SUFFIXES:
            if abs_path.is_file():
                return RAW + repo_rel + suffix
        elif abs_path.is_dir():
            return GITHUB_TREE + repo_rel + suffix
        elif abs_path.is_file():
            return GITHUB + repo_rel + suffix
        missing.append(f"{src_rel}: {target}")
        return target

    def rewrite_segment(segment: str) -> str:
        spans: list[str] = []

        def stash(match: re.Match) -> str:
            spans.append(match.group(0))
            return f"\x00{len(spans) - 1}\x00"

        segment = _CODE_SPAN.sub(stash, segment)
        segment = _INLINE_LINK.sub(
            lambda m: (m.group(0)[:m.start(1) - m.start(0)]
                       + rewrite_one(m.group(1))
                       + m.group(0)[m.end(1) - m.start(0):]),
            segment)
        segment = _REF_DEF.sub(
            lambda m: m.group(1) + rewrite_one(m.group(2)), segment)
        segment = _HTML_ATTR.sub(
            lambda m: f'{m.group(1)}="{rewrite_one(m.group(2))}"', segment)

        def unstash(match: re.Match) -> str:
            return spans[int(match.group(1))]

        segment = re.sub(r"\x00(\d+)\x00", unstash, segment)
        return segment

    # Rewrite only outside fenced code blocks; fences pair open/close.
    out: list[str] = []
    pos = 0
    while True:
        fence = _FENCE.search(text, pos)
        if not fence:
            break
        end = _FENCE.search(text, fence.end())
        stop = end.end() if end else len(text)
        out.append(rewrite_segment(text[pos:fence.start()]))
        out.append(text[fence.start():stop])
        pos = stop
    out.append(rewrite_segment(text[pos:]))
    return "".join(out)


PAGE_CSS = """
:root {
  --bg: #08090a;
  --surface: #0f1011;
  --surface-2: #151617;
  --line: rgba(255, 255, 255, 0.07);
  --line-strong: rgba(255, 255, 255, 0.16);
  --text: #f7f8f8;
  --text-2: #a1a4a8;
  --text-3: #6b6f75;
  --radius: 12px;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  color-scheme: dark;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth;
       scroll-padding-top: 76px; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.6 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: inherit; }
code { font-family: var(--mono); font-size: 0.92em; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 0 32px; }

header.top { position: sticky; top: 0; z-index: 20; background: rgba(8, 9, 10, 0.8); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); border-bottom: 1px solid var(--line); }
nav { display: flex; align-items: center; gap: 28px; height: 60px; }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 600; text-decoration: none; font-size: 16px; }
.brand img { width: 26px; height: 26px; border-radius: 6px; }
nav .links { display: flex; gap: 24px; font-size: 14px; }
nav .links a, nav .right a.plain { color: var(--text-2); text-decoration: none; }
nav .links a:hover, nav .right a.plain:hover { color: var(--text); }
nav .right { margin-left: auto; display: flex; align-items: center; gap: 18px; font-size: 14px; }
nav .right a.plain { display: inline-flex; align-items: center; gap: 6px; }
svg.i { width: 16px; height: 16px; flex: none; }

.btn { display: inline-flex; align-items: center; gap: 8px; height: 38px; padding: 0 16px; border-radius: 8px; font: 500 14px var(--sans); text-decoration: none; border: 1px solid var(--line-strong); background: var(--surface-2); color: var(--text); cursor: pointer; white-space: nowrap; }
.btn:hover { border-color: rgba(255, 255, 255, 0.28); }
.btn.light { background: var(--text); color: #08090a; border-color: var(--text); }
.btn.sm { height: 32px; padding: 0 12px; font-size: 13px; }

.doc { max-width: 800px; padding-top: 48px; padding-bottom: 88px; }
.doc h1 { font-size: 34px; letter-spacing: -0.02em; line-height: 1.2; margin: 0 0 8px; }
.doc h2 { font-size: 24px; letter-spacing: -0.01em; margin: 48px 0 12px; padding-top: 16px; border-top: 1px solid var(--line); }
.doc h3 { font-size: 18px; margin: 32px 0 8px; }
.doc h4 { font-size: 16px; margin: 24px 0 8px; }
.doc p, .doc li { color: var(--text-2); }
.doc a { color: var(--text); text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 3px; }
.doc a:hover { text-decoration-color: var(--text); }
.doc :is(h1, h2, h3, h4) a { color: inherit; text-decoration: none; }
.doc :is(h1, h2, h3, h4):hover a.anchor::after { content: " #"; color: var(--text-3); }
.doc code { background: var(--surface-2); border: 1px solid var(--line); border-radius: 5px; padding: 1px 5px; }
.doc pre { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; overflow-x: auto; }
.doc pre code { background: none; border: none; padding: 0; font-size: 13.5px; line-height: 1.55; }
.doc blockquote { margin: 16px 0; padding: 2px 16px; border-left: 3px solid var(--line-strong); color: var(--text-3); }
.doc table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14.5px; }
.doc th, .doc td { border: 1px solid var(--line); padding: 8px 12px; text-align: left; }
.doc th { background: var(--surface-2); }
.doc hr { border: none; border-top: 1px solid var(--line); margin: 32px 0; }
.doc img { max-width: 100%; }

footer { border-top: 1px solid var(--line); padding: 32px; margin-top: 64px; display: flex; justify-content: space-between; align-items: center; gap: 24px; color: var(--text-3); font-size: 13.5px; }
footer .right { display: flex; gap: 20px; }
footer a { color: var(--text-3); text-decoration: none; }
footer a:hover { color: var(--text); }

@media (max-width: 640px) {
  .wrap { padding: 0 16px; }
  nav { gap: 16px; }
  nav .links, nav .right a.plain { display: none; }
  .doc { padding-top: 32px; }
  .doc h1 { font-size: 27px; }
  .doc h2 { font-size: 21px; }
  footer { flex-direction: column; align-items: flex-start; }
}
"""


def _nav() -> str:
    return """<header class="top">
  <div class="wrap">
    <nav>
      <a class="brand" href="../"><img src="../logo-rounded.svg" alt="">quiver</a>
      <div class="links">
        <a href="../overview/">Docs</a>
        <a href="../cli/">CLI reference</a>
        <a href="../changelog/">Changelog</a>
      </div>
      <div class="right">
        <a class="plain" href="https://github.com/c-wenlong/quiver"><svg class="i"><use href="#gh"/></svg>GitHub</a>
        <a class="btn light sm" href="../#install"><span>Install</span></a>
      </div>
    </nav>
  </div>
</header>"""


_GH_SVG = ('<svg width="0" height="0" style="position:absolute" aria-hidden="true">'
           '<symbol id="gh" viewBox="0 0 16 16"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></symbol>'
           '</svg>')


def _footer() -> str:
    return """<footer>
  <span>quiver is MIT licensed.</span>
  <div class="right">
    <a href="../overview/">Docs</a>
    <a href="../changelog/">Changelog</a>
    <a href="../contributing/">Contributing</a>
    <a href="https://github.com/c-wenlong/quiver">GitHub</a>
  </div>
</footer>"""


def github_slug(text: str) -> str:
    """GitHub's heading-anchor rule: strip tags and punctuation, keep
    word chars and hyphens, lowercase, spaces to hyphens."""
    plain = re.sub(r"<[^>]+>", "", text).lower()
    return _NON_WORD.sub("", plain).replace(" ", "-")


def _renderer():
    """A mistune renderer that adds ``id`` anchors to h1-h4. One per
    document: the slug dedupe counter is per page, like GitHub's."""
    import mistune

    class Anchored(mistune.HTMLRenderer):
        def __init__(self):
            super().__init__(escape=False)
            self._used: dict[str, int] = {}

        def heading(self, text: str, level: int, **attrs) -> str:
            html = super().heading(text, level, **attrs)
            if level > 4:
                return html
            slug = github_slug(text)
            n = self._used.get(slug, 0)
            self._used[slug] = n + 1
            slug = slug if n == 0 else f"{slug}-{n}"
            return html.replace(f"<h{level}>",
                                f'<h{level} id="{slug}">'
                                f'<a class="anchor" href="#{slug}"></a>', 1)

    return mistune.create_markdown(
        escape=False, renderer=Anchored(),
        plugins=["table", "strikethrough", "task_lists"])


_MERMAID = """<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({ startOnLoad: false, theme: 'dark' });
  for (const el of document.querySelectorAll('pre > code.language-mermaid')) {
    const div = document.createElement('div');
    div.className = 'mermaid';
    div.textContent = el.textContent;
    el.parentElement.replaceWith(div);
  }
  await mermaid.run();
</script>
"""


def render_page(slug: str, body_html: str) -> str:
    """Wrap rendered Markdown ``body_html`` in the site chrome."""
    title, desc = TITLES[slug]
    mermaid = _MERMAID if 'class="language-mermaid"' in body_html else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="icon" href="../favicon.ico" sizes="16x16 32x32 48x48">
<link rel="icon" href="../favicon-32.png" type="image/png" sizes="32x32">
<link rel="icon" href="../favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="../apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{PAGE_CSS}</style>
</head>
<body>
{_GH_SVG}
{_nav()}
<main class="wrap doc">
{body_html}
</main>
{_footer()}
{mermaid}</body>
</html>
"""


def build_pages(root: Path) -> tuple[dict[str, str], list[str]]:
    """All generated pages, ``slug -> html``, plus unresolvable links."""
    pages: dict[str, str] = {}
    missing: list[str] = []
    for src, slug in SOURCES.items():
        text = (root / src).read_text(encoding="utf-8")
        text = rewrite_links(text, src, root, missing)
        pages[slug] = render_page(slug, _renderer()(text))
    pages["cli"] = render_page("cli", _renderer()(cli_page(root)))
    return pages, missing


def write_pages(root: Path, pages: dict[str, str]) -> None:
    for slug, html in pages.items():
        out = root / "app" / "landing-page" / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    pages, missing = build_pages(root)
    for line in missing:
        print(f"warning: unresolvable link {line}", file=sys.stderr)
    if "--check" in argv:
        stale = [slug for slug, html in pages.items()
                 if not (root / "app" / "landing-page" / slug / "index.html")
                 .is_file()
                 or (root / "app" / "landing-page" / slug / "index.html")
                 .read_text(encoding="utf-8") != html]
        if stale:
            print("docs pages are stale; run python3 scripts/docs_site.py: "
                  + ", ".join(sorted(stale)))
            return 1
        return 0
    write_pages(root, pages)
    for slug in sorted(pages):
        print(f"wrote app/landing-page/{slug}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
