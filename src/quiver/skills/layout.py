"""Skill root layout: symlinks, canonical trees, and persisted link records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from quiver.paths import CONFIG_DIR, SKILL_LINKS_FILE
from quiver.skills.catalogs import load_skill_catalogs, count_skill_md

SHARED_LABEL = "shared"
SHARED_REL = Path(".agents/skills")

# Harness skill roots that can be symlinked to shared or each other.
HARNESS_ROOTS: tuple[tuple[str, Path], ...] = (
    (SHARED_LABEL, Path(".agents/skills")),
    ("cursor", Path(".cursor/skills")),
    ("codex", Path(".codex/skills")),
    ("claude", Path(".claude/skills")),
)

BUILTIN_ROOTS: tuple[tuple[str, Path], ...] = (
    *HARNESS_ROOTS,
    ("cursor-builtin", Path(".cursor/skills-cursor")),
    ("cursor-plugin", Path(".cursor/plugins/cache")),
    ("claude-plugin", Path(".claude/plugins/cache")),
)


@dataclass
class SkillRootEntry:
    label: str
    path: Path
    exists: bool
    kind: str  # missing | directory | symlink | file
    link_target: Path | None = None
    resolved: Path | None = None
    skill_count: int = 0
    aliases: list[str] = field(default_factory=list)
    canonical_label: str | None = None
    link_target_label: str | None = None


def _expand(home: Path, rel: Path) -> Path:
    return (home / rel).expanduser()


def load_link_records() -> list[dict]:
    try:
        data = json.loads(SKILL_LINKS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    links = data.get("links", [])
    return links if isinstance(links, list) else []


def save_link_records(links: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SKILL_LINKS_FILE.with_suffix(".tmp")
    payload = {"links": links, "updated": datetime.now().isoformat()}
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.rename(SKILL_LINKS_FILE)


def record_symlink(label: str, path: Path, target: Path) -> None:
    links = [l for l in load_link_records() if l.get("label") != label]
    links.append(
        {
            "label": label,
            "path": str(path),
            "target": str(target),
            "kind": "symlink",
            "updated": datetime.now().isoformat(),
        }
    )
    save_link_records(links)


def remove_link_record(label: str) -> None:
    links = [l for l in load_link_records() if l.get("label") != label]
    save_link_records(links)


def _analyze_root(label: str, path: Path) -> SkillRootEntry:
    exists = path.exists()
    kind = "missing"
    link_target = None
    resolved = None
    skill_count = 0

    if exists:
        if path.is_symlink():
            kind = "symlink"
            link_target = Path(os_readlink(path))
            try:
                resolved = path.resolve()
            except OSError:
                resolved = None
        elif path.is_dir():
            kind = "directory"
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            skill_count = count_skill_md(path)
        else:
            kind = "file"

    return SkillRootEntry(
        label=label,
        path=path,
        exists=exists,
        kind=kind,
        link_target=link_target,
        resolved=resolved,
        skill_count=skill_count,
    )


def os_readlink(path: Path) -> str:
    import os

    return os.readlink(path)


def all_skill_root_candidates(
    home: Path | None = None,
    cwd: Path | None = None,
) -> list[tuple[str, Path]]:
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    candidates: list[tuple[str, Path]] = [
        (label, _expand(home, rel)) for label, rel in BUILTIN_ROOTS
    ]
    candidates.append(("project", cwd / ".cursor" / "skills"))
    for entry in load_skill_catalogs():
        label = str(entry.get("label", "catalog"))
        path = Path(str(entry.get("path", ""))).expanduser()
        candidates.append((label, path))
    return candidates


def enumerate_skill_roots(
    home: Path | None = None,
    cwd: Path | None = None,
) -> list[SkillRootEntry]:
    """Return every configured root with symlink metadata (not deduped)."""
    home = home or Path.home()
    entries = [_analyze_root(label, path) for label, path in all_skill_root_candidates(home, cwd)]

    resolved_to_label: dict[Path, str] = {}
    for entry in entries:
        if entry.resolved is None:
            continue
        if entry.resolved not in resolved_to_label:
            resolved_to_label[entry.resolved] = entry.label

    for entry in entries:
        if entry.resolved is None:
            continue
        canonical = resolved_to_label.get(entry.resolved)
        entry.canonical_label = canonical
        if entry.kind == "symlink" and entry.link_target is not None:
            try:
                target_resolved = entry.link_target.expanduser().resolve()
                entry.link_target_label = resolved_to_label.get(target_resolved)
            except OSError:
                entry.link_target_label = None
        elif entry.label != canonical:
            entry.link_target_label = canonical

    # Attach alias labels sharing the same resolved path.
    by_resolved: dict[Path, list[str]] = {}
    for entry in entries:
        if entry.resolved is None:
            continue
        by_resolved.setdefault(entry.resolved, []).append(entry.label)

    for entry in entries:
        if entry.resolved is None:
            continue
        labels = by_resolved[entry.resolved]
        entry.aliases = [l for l in labels if l != entry.label]

    return entries


def layout_groups(home: Path | None = None, cwd: Path | None = None) -> list[dict]:
    """Group roots by resolved path for tree rendering."""
    entries = enumerate_skill_roots(home=home, cwd=cwd)
    groups: dict[Path | None, dict] = {}
    order: list[Path | None] = []

    for entry in entries:
        key = entry.resolved
        if key not in groups:
            groups[key] = {"resolved": key, "canonical": None, "members": []}
            order.append(key)
        groups[key]["members"].append(entry)
        if entry.canonical_label == entry.label or groups[key]["canonical"] is None:
            if entry.kind in ("directory", "symlink") and entry.exists:
                groups[key]["canonical"] = entry

    result = []
    for key in order:
        group = groups[key]
        members = group["members"]
        canonical = group["canonical"] or members[0]
        result.append({"resolved": key, "canonical": canonical, "members": members})
    return result


def resolve_root_ref(ref: str, home: Path | None = None, cwd: Path | None = None) -> tuple[str, Path]:
    """Resolve a scope label or path to (label, path)."""
    home = home or Path.home()
    ref = ref.strip()
    for label, path in all_skill_root_candidates(home, cwd):
        if label.lower() == ref.lower():
            return label, path
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    for label, candidate in all_skill_root_candidates(home, cwd):
        try:
            if candidate.resolve() == path.resolve():
                return label, candidate
        except OSError:
            continue
    slug = path.name or "custom"
    return slug, path


def sync_link_records_from_filesystem(home: Path | None = None) -> list[str]:
    """Persist observed harness symlinks into skill_links.json."""
    home = home or Path.home()
    harness_labels = {label for label, _ in HARNESS_ROOTS}
    updated: list[str] = []
    for entry in enumerate_skill_roots(home=home):
        if entry.label not in harness_labels:
            continue
        if entry.kind == "symlink" and entry.resolved is not None and entry.link_target is not None:
            try:
                target = entry.link_target.expanduser().resolve()
            except OSError:
                target = entry.resolved
            record_symlink(entry.label, entry.path, target)
            updated.append(entry.label)
        elif entry.label != SHARED_LABEL:
            remove_link_record(entry.label)
    return updated


def shared_scopes_for_skill(skill_path: Path, home: Path | None = None) -> list[str]:
    """Return scope labels whose roots contain this skill path (including via symlinks)."""
    home = home or Path.home()
    try:
        skill_resolved = skill_path.resolve()
    except OSError:
        return []
    scopes: list[str] = []
    for entry in enumerate_skill_roots(home=home):
        if entry.resolved is None:
            continue
        try:
            skill_resolved.relative_to(entry.resolved)
            scopes.append(entry.label)
        except ValueError:
            continue
    return scopes
