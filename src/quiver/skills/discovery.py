"""Discover agent skills across known skill roots."""

import os
import re
from pathlib import Path


def skill_roots(home: Path | None = None, cwd: Path | None = None) -> list[tuple[str, Path]]:
    """Return [(scope_label, Path)] skill-root dirs that exist, deduped by realpath."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    candidates = [
        ("shared", home / ".agents" / "skills"),
        ("cursor-builtin", home / ".cursor" / "skills-cursor"),
        ("cursor-plugin", home / ".cursor" / "plugins" / "cache"),
        ("claude-plugin", home / ".claude" / "plugins" / "cache"),
        ("codex", home / ".codex" / "skills"),
        ("claude", home / ".claude" / "skills"),
        ("cursor", home / ".cursor" / "skills"),
        ("project", cwd / ".cursor" / "skills"),
    ]
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


def parse_skill_md(md_path: Path) -> tuple[str, str]:
    """Return (name, description) from SKILL.md YAML frontmatter."""
    name, desc = None, ""
    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return md_path.parent.name, ""

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
                        name = val.strip("\"'")
                    elif key == "description" and val and val not in (">", ">-", "|", "|-"):
                        desc = val.strip("\"'")
                elif key == "description" and line.strip():
                    buf.append(line)
            if key == "description" and buf and not desc:
                desc = " ".join(x.strip() for x in buf).strip()

    return (name or md_path.parent.name), desc


def discover_skills(home: Path | None = None, cwd: Path | None = None) -> list[dict]:
    """Walk every skill root and return skill metadata dicts."""
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
                }
            )
    return skills
