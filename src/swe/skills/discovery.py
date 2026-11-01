"""Discover agent skills across known skill roots."""

import os
import re
from pathlib import Path

from swe.paths import expand_user
from swe.skills.catalogs import load_skill_catalogs
from swe.skills.layout import SHARED_REL, shared_scopes_for_skill


def skill_roots(home: Path | None = None, cwd: Path | None = None) -> list[tuple[str, Path]]:
    """Return [(scope_label, Path)] skill-root dirs that exist, deduped by realpath.

    The harness roots come from the same filesystem scan that backs
    ``swe find skills``, so the two commands cannot disagree about where
    skills live. Before this shared a hardcoded list of eight paths, it
    missed roots like ~/.pane/skills and ~/.zcode/skills entirely.

    Three kinds of root that scan does not reach are added here: plugin
    caches (a level deeper than a harness root), directories whose name is
    not "skills" (Cursor ships ~/.cursor/skills-cursor), and catalogs the
    user registered outside their home harness dirs.
    """
    from swe.init.layout import discover_skill_roots, skill_root_label

    home = home or Path.home()
    cwd = cwd or Path.cwd()

    candidates: list[tuple[str, Path]] = [("shared", home / SHARED_REL)]

    # Every ~/.<harness>/skills and ~/.config/<harness>/skills on disk.
    for path in discover_skill_roots(home):
        candidates.append((skill_root_label(path, home), path))

    candidates += [
        ("cursor-builtin", home / ".cursor" / "skills-cursor"),
        ("cursor-plugin", home / ".cursor" / "plugins" / "cache"),
        ("claude-plugin", home / ".claude" / "plugins" / "cache"),
        ("project", cwd / ".cursor" / "skills"),
    ]

    for entry in load_skill_catalogs():
        label = entry.get("label", "catalog")
        path = expand_user(entry.get("path", ""))
        candidates.append((label, path))

    roots: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for label, path in candidates:
        try:
            if not path.exists():
                continue
            real = path.resolve()
        except Exception:
            continue
        if real in seen:
            continue
        seen.add(real)
        roots.append((label, path))
    return roots


def _unquote(val: str) -> str:
    """A one-line YAML scalar's text: one matching pair of quotes comes off,
    with YAML's escapes for that style (``''`` in single quotes, ``\\"`` and
    ``\\\\`` in double quotes). A bare value is returned as written."""
    if len(val) >= 2 and val[0] == val[-1] == "'":
        return val[1:-1].replace("''", "'")
    if len(val) >= 2 and val[0] == val[-1] == '"':
        return re.sub(r'\\(["\\])', r"\1", val[1:-1])
    return val


def parse_skill_md(md_path: Path) -> tuple[str, str]:
    """Return (name, description) from SKILL.md YAML frontmatter."""
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return md_path.parent.name, ""
    return parse_skill_text(text, md_path.parent.name)


def parse_skill_text(text: str, default_name: str = "") -> tuple[str, str]:
    """``parse_skill_md`` on text already read, for callers that bound the
    read themselves. ``default_name`` stands in for a missing ``name``."""
    name, desc = None, ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            frontmatter = text[3:end]
            key, buf = None, []
            for line in frontmatter.splitlines():
                match = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
                if match:
                    if key == "description" and buf and not desc:
                        desc = " ".join(x.strip() for x in buf).strip()
                    buf = []
                    key = match.group(1).lower()
                    val = match.group(2).strip()
                    if key == "name":
                        name = _unquote(val)
                    elif key == "description" and val and val not in (">", ">-", "|", "|-"):
                        desc = _unquote(val)
                elif key == "description" and line.strip():
                    buf.append(line)
            if key == "description" and buf and not desc:
                desc = " ".join(x.strip() for x in buf).strip()

    return (name or default_name), desc


def discover_skills(home: Path | None = None, cwd: Path | None = None,
                    visibility: bool = True) -> list[dict]:
    """Walk every skill root and return skill metadata dicts.

    ``visibility=False`` leaves ``visible_via`` empty. Working it out walks
    every known root once per skill, which is most of the cost on a machine
    with a few hundred plugin skills, and ``swe skills --json`` derives
    visibility its own way.
    """
    skills: list[dict] = []
    for label, root in skill_roots(home=home, cwd=cwd):
        try:
            root_real = root.resolve()
        except Exception:
            continue
        for dirpath, _dirnames, filenames in os.walk(root_real):
            if "SKILL.md" not in filenames:
                continue
            md = Path(dirpath) / "SKILL.md"
            skill_name, desc = parse_skill_md(md)
            skills.append(
                {
                    "name": skill_name,
                    "scope": label,
                    "path": str(md),
                    "description": desc,
                    "visible_via": (shared_scopes_for_skill(md, home=home)
                                    if visibility else []),
                }
            )
    return skills
