#!/usr/bin/env python3
"""Assemble the Zensical docs site into ``build/docs-site/``.

The docs live where they always have (README, ARCHITECTURE, docs/, app/).
This script stages them under one ``docs_dir`` for ``zensical build``,
rewrites every link so a page that resolves inside the repo resolves
inside the site, copies referenced assets, and points anything else at
github.com. Run it, then ``zensical build`` (or ``zensical serve``).

stdlib only — CI installs zensical itself; nothing here imports it.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build" / "docs-site"
GITHUB = "https://github.com/c-wenlong/quiver/blob/main/"
GITHUB_TREE = "https://github.com/c-wenlong/quiver/tree/main/"

# repo-relative source -> staged path, in nav order. cli.md is generated.
PAGES: list[tuple[str, str]] = [
    ("README.md", "overview.md"),
    ("docs/harness-support.md", "harness-support.md"),
    ("app/README.md", "app/index.md"),
    ("app/ROADMAP.md", "app/roadmap.md"),
    ("docs/app-architecture.md", "app/architecture.md"),
    ("docs/coding-plans.md", "app/coding-plans.md"),
    ("ARCHITECTURE.md", "architecture/index.md"),
    ("docs/backend-architecture.md", "architecture/backend-architecture.md"),
    ("docs/data-model.md", "architecture/data-model.md"),
    ("docs/json-contracts.md", "architecture/json-contracts.md"),
    ("CONTRIBUTING.md", "contributing/index.md"),
    ("docs/testing.md", "contributing/testing.md"),
    ("docs/COVERAGE.md", "contributing/coverage.md"),
    ("AGENTS.md", "contributing/agents.md"),
    ("SECURITY.md", "contributing/security.md"),
    ("SUPPORT.md", "contributing/support.md"),
    ("CODE_OF_CONDUCT.md", "contributing/code-of-conduct.md"),
    ("CHANGELOG.md", "changelog.md"),
]

# Extra repo files the site itself needs (logo, favicon).
ASSETS = ["app/Icons/AppIcon-light.png", "app/Icons/AppIcon-dark.png"]

# The hand-written landing page. The folder is copied to the site root as
# it is (media/ included), so landing/index.html becomes the front page.
LANDING = "landing"

# Extensions copied into the site and served, rather than linked to GitHub.
ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".pdf", ".mp4", ".mov", ".woff", ".woff2",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# [text](target) / ![alt](target), optionally followed by a "title".
_INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Reference-style definitions at line start: [id]: target
_REF_DEF = re.compile(r"(?m)^([ ]{0,3})\[[^\]]+\]:[ ]*(\S+)")
_FENCE = re.compile(r"(?m)^(```|~~~)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
_URLISH = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)")
# <img src="..."> / <a href="..."> inside markdown pages (README badges etc.)
_HTML_ATTR = re.compile(r'\b(src|href)="([^"]+)"')


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


def _relpath(staged_from: str, staged_to: str) -> str:
    return posixpath.relpath(staged_to, posixpath.dirname(staged_from) or ".")


def rewrite_links(text: str, src_rel: str, staged_map: dict[str, str],
                  root: Path, missing: list[str], assets: set[str]) -> str:
    """Rewrite every link target in ``text`` (a page from ``src_rel``).

    Staged pages become relative links inside the site, local assets are
    collected in ``assets`` for copying at their repo path, other repo
    files and directories link to GitHub, and anything unresolvable lands
    in ``missing``. Fenced code blocks and inline code are left alone.
    """
    staged_here = staged_map[src_rel]

    def rewrite_one(target: str) -> str:
        if not target or _URLISH.match(target):
            return target
        path, _, anchor = target.partition("#")
        if not path:
            return target
        repo_rel = _resolve(src_rel, path)
        suffix = "#" + anchor if anchor else ""
        if repo_rel in staged_map:
            return _relpath(staged_here, staged_map[repo_rel]) + suffix
        abs_path = root / repo_rel
        if Path(repo_rel).suffix.lower() in ASSET_SUFFIXES:
            if abs_path.is_file():
                assets.add(repo_rel)
                return _relpath(staged_here, repo_rel) + suffix
        elif abs_path.is_dir():
            return GITHUB_TREE + repo_rel + suffix
        elif abs_path.is_file():
            return GITHUB + repo_rel + suffix
        missing.append(f"{src_rel}: {target}")
        return target

    def rewrite_segment(segment: str) -> str:
        # Mask inline code spans so `[x](y)` inside `code` is not rewritten.
        spans: list[str] = []

        def stash(match: re.Match) -> str:
            spans.append(match.group(0))
            return f"\x00{len(spans) - 1}\x00"

        masked = _CODE_SPAN.sub(stash, segment)

        def splice(match: re.Match, group: int) -> str:
            offset = match.start(group) - match.start(0)
            whole = match.group(0)
            target = match.group(group)
            return (whole[:offset] + rewrite_one(target)
                    + whole[offset + len(target):])

        masked = _INLINE_LINK.sub(lambda m: splice(m, 1), masked)
        masked = _REF_DEF.sub(lambda m: splice(m, 2), masked)
        masked = _HTML_ATTR.sub(lambda m: splice(m, 2), masked)

        return re.sub("\x00(\\d+)\x00", lambda m: spans[int(m.group(1))], masked)

    # Split on fenced code blocks; rewrite only the prose between them.
    parts = _FENCE.split(text)
    out = [rewrite_segment(parts[0])]
    in_fence = False
    for chunk in parts[1:]:
        if _FENCE.fullmatch(chunk):
            in_fence = not in_fence
            out.append(chunk)
        else:
            out.append(chunk if in_fence else rewrite_segment(chunk))
    return "".join(out)


def stage(root: Path = ROOT, stage_dir: Path = STAGE) -> int:
    """Write every staged page/asset under ``stage_dir``; return 0 or 1."""
    root = Path(root)
    stage_dir = Path(stage_dir)
    staged_map = dict(PAGES)
    missing: list[str] = []
    assets: set[str] = set()

    if stage_dir.exists():
        shutil.rmtree(stage_dir)

    for src_rel, staged_rel in PAGES:
        src = root / src_rel
        if not src.is_file():
            missing.append(f"{src_rel}: source file is missing")
            continue
        text = rewrite_links(src.read_text(encoding="utf-8"),
                             src_rel, staged_map, root, missing, assets)
        dst = stage_dir / staged_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")

    (stage_dir / "cli.md").write_text(cli_page(root), encoding="utf-8")

    landing = root / LANDING
    if (landing / "index.html").is_file():
        shutil.copytree(landing, stage_dir, dirs_exist_ok=True)
    else:
        missing.append(f"{LANDING}/index.html: landing page is missing")

    for asset in set(ASSETS) | assets:
        src = root / asset
        if src.is_file():
            dst = stage_dir / asset
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        else:
            missing.append(f"{asset}: referenced asset is missing")

    for item in missing:
        print(f"docs_site: missing link target: {item}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(stage())
