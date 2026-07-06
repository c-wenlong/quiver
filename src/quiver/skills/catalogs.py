"""User-configured skill catalog registry (~/.config/swe/skill_catalogs.json)."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from quiver.paths import CONFIG_DIR, SKILL_CATALOGS_FILE

DEFAULT_SEARCH_ROOTS = ("Desktop", "Documents")
DEFAULT_MAX_DEPTH = 12
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".Trash",
        "Library",
        "Applications",
        ".cursor",
        ".npm",
        ".cache",
    }
)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "catalog"


def suggest_catalog_label(path: Path) -> str:
    """Derive a short scope label from a catalog directory path."""
    if path.name.lower() == "skills" and path.parent.name:
        return _slug(path.parent.name)
    return _slug(path.name)


def load_skill_catalogs() -> list[dict]:
    """Return configured catalogs: [{label, path, added?}, ...]."""
    try:
        data = json.loads(SKILL_CATALOGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    catalogs = data.get("catalogs", [])
    if not isinstance(catalogs, list):
        return []
    out: list[dict] = []
    for entry in catalogs:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        path = str(entry.get("path", "")).strip()
        if label and path:
            out.append(dict(entry))
    return out


def save_skill_catalogs(catalogs: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SKILL_CATALOGS_FILE.with_suffix(".tmp")
    payload = {"catalogs": catalogs}
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.rename(SKILL_CATALOGS_FILE)


def count_skill_md(root: Path) -> int:
    """Count SKILL.md files under a catalog root."""
    if not root.is_dir():
        return 0
    total = 0
    try:
        for dirpath, _dirnames, filenames in os_walk_limited(root):
            if "SKILL.md" in filenames:
                total += 1
    except OSError:
        return 0
    return total


def os_walk_limited(root: Path):
    """Walk root, skipping noisy directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        yield dirpath, dirnames, filenames


def catalog_has_skills(root: Path) -> bool:
    return count_skill_md(root) > 0


def resolve_catalog_path(path: str | Path) -> Path:
    """Resolve a catalog path; '.' and relative paths use the current working directory."""
    return Path(path).expanduser().resolve()


def add_skill_catalog(path: str | Path, label: str | None = None) -> dict:
    """Register a catalog path; returns the saved entry."""
    resolved = resolve_catalog_path(path)
    if not resolved.is_dir():
        raise FileNotFoundError(f"Not a directory: {resolved}")
    if not catalog_has_skills(resolved):
        raise ValueError(f"No SKILL.md files found under {resolved}")

    entry_label = _slug(label) if label else suggest_catalog_label(resolved)
    catalogs = load_skill_catalogs()
    now = datetime.now().isoformat()
    new_entry = {
        "label": entry_label,
        "path": str(resolved),
        "added": now,
    }

    replaced = False
    for i, existing in enumerate(catalogs):
        if existing.get("label") == entry_label or Path(existing.get("path", "")).resolve() == resolved:
            new_entry["added"] = existing.get("added", now)
            catalogs[i] = new_entry
            replaced = True
            break
    if not replaced:
        catalogs.append(new_entry)

    save_skill_catalogs(catalogs)
    return new_entry


def remove_skill_catalog(key: str) -> bool:
    """Remove by label or path substring; returns True if removed."""
    catalogs = load_skill_catalogs()
    key_lower = key.lower()
    kept = [
        c
        for c in catalogs
        if c.get("label", "").lower() != key_lower
        and key_lower not in c.get("path", "").lower()
    ]
    if len(kept) == len(catalogs):
        return False
    save_skill_catalogs(kept)
    return True


def discover_catalog_dirs(
    home: Path | None = None,
    *,
    search_roots: tuple[str, ...] = DEFAULT_SEARCH_ROOTS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[Path]:
    """Find skill catalog directories under Desktop/Documents."""
    home = home or Path.home()
    found: list[Path] = []
    seen: set[Path] = set()

    for rel in search_roots:
        base = (home / rel).expanduser()
        if not base.is_dir():
            continue
        try:
            base = base.resolve()
        except OSError:
            continue

        for dirpath, dirnames, _filenames in os.walk(base):
            current = Path(dirpath)
            try:
                depth = len(current.relative_to(base).parts)
            except ValueError:
                continue
            if depth > max_depth:
                dirnames.clear()
                continue
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")
            ]
            if current.name.lower() != "skills":
                continue
            try:
                resolved = current.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            if not catalog_has_skills(resolved):
                continue
            seen.add(resolved)
            found.append(resolved)

    found.sort(key=lambda p: str(p).lower())
    return _prune_nested_catalogs(found)


def _prune_nested_catalogs(paths: list[Path]) -> list[Path]:
    """Drop skill catalogs that live inside another discovered catalog."""
    if len(paths) <= 1:
        return paths
    ordered = sorted(paths, key=lambda p: len(p.parts))
    kept: list[Path] = []
    for path in ordered:
        if any(path.is_relative_to(parent) for parent in kept):
            continue
        kept.append(path)
    return kept
