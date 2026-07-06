"""Link, unlink, and move operations for skill roots and skill folders."""

from __future__ import annotations

import shutil
from pathlib import Path

from quiver.skills.discovery import discover_skills
from quiver.skills.layout import (
    SHARED_LABEL,
    enumerate_skill_roots,
    record_symlink,
    remove_link_record,
    resolve_root_ref,
)


class SkillLayoutError(Exception):
    pass


def link_skill_root(
    source_ref: str,
    target_ref: str | None = None,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    force: bool = False,
) -> tuple[str, Path, Path]:
    """Point source root at target via symlink. Default target: shared."""
    home = home or Path.home()
    target_ref = target_ref or SHARED_LABEL
    src_label, src_path = resolve_root_ref(source_ref, home=home, cwd=cwd)
    _tgt_label, tgt_path = resolve_root_ref(target_ref, home=home, cwd=cwd)

    if src_path.resolve() == tgt_path.resolve():
        raise SkillLayoutError(f"{src_label} already resolves to {tgt_path}")

    if src_path.exists():
        if src_path.is_symlink():
            src_path.unlink()
        elif src_path.is_dir():
            if any(src_path.iterdir()) and not force:
                raise SkillLayoutError(
                    f"{src_path} is a non-empty directory. "
                    f"Move skills out first or pass --force (after backup)."
                )
            shutil.rmtree(src_path)
        else:
            src_path.unlink()

    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.symlink_to(tgt_path, target_is_directory=True)
    record_symlink(src_label, src_path, tgt_path)
    return src_label, src_path, tgt_path


def unlink_skill_root(
    source_ref: str,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    mkdir: bool = False,
) -> tuple[str, Path]:
    """Remove a skill-root symlink; optionally replace with an empty directory."""
    home = home or Path.home()
    src_label, src_path = resolve_root_ref(source_ref, home=home, cwd=cwd)

    if not src_path.exists():
        raise SkillLayoutError(f"{src_path} does not exist")
    if not src_path.is_symlink():
        raise SkillLayoutError(
            f"{src_path} is a real directory, not a symlink. "
            f"Move skills elsewhere before delinking."
        )

    src_path.unlink()
    remove_link_record(src_label)
    if mkdir:
        src_path.mkdir(parents=True, exist_ok=True)
    return src_label, src_path


def _find_skill_matches(name: str, scope: str, home: Path, cwd: Path) -> list[dict]:
    needle = name.lower()
    skills = discover_skills(home=home, cwd=cwd)
    matches = [
        s
        for s in skills
        if s["scope"].lower() == scope.lower()
        and (s["name"].lower() == needle or needle in s["name"].lower() or needle in s["path"].lower())
    ]
    return matches


def find_skill_directory(
    name: str,
    scope: str,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    matches = _find_skill_matches(name, scope, home, cwd)
    if not matches:
        raise SkillLayoutError(f"No skill matching {name!r} in scope {scope!r}")
    if len(matches) > 1:
        paths = [m["path"] for m in matches[:5]]
        raise SkillLayoutError(
            f"Multiple skills match {name!r} in {scope!r}. Be more specific.\n  " + "\n  ".join(paths)
        )
    return Path(matches[0]["path"]).parent


def move_skill(
    name: str,
    from_scope: str,
    to_scope: str,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    force: bool = False,
) -> tuple[Path, Path]:
    """Move a skill folder from one scope root to another."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    _from_label, from_root = resolve_root_ref(from_scope, home=home, cwd=cwd)
    _to_label, to_root = resolve_root_ref(to_scope, home=home, cwd=cwd)

    try:
        from_resolved = from_root.resolve()
        to_resolved = to_root.resolve()
    except OSError as exc:
        raise SkillLayoutError(str(exc)) from exc

    if from_resolved == to_resolved and not force:
        raise SkillLayoutError(
            f"Scopes {from_scope!r} and {to_scope!r} resolve to the same directory ({from_resolved}). "
            f"Unlink one harness first to give it a private skills folder, then move."
        )

    if not to_root.exists():
        raise SkillLayoutError(f"Destination root does not exist: {to_root}")
    if to_root.is_symlink() and not to_root.exists():
        raise SkillLayoutError(f"Destination symlink is broken: {to_root}")

    skill_dir = find_skill_directory(name, from_scope, home=home, cwd=cwd)
    try:
        skill_dir.relative_to(from_resolved)
    except ValueError as exc:
        raise SkillLayoutError(f"Skill {skill_dir} is not under {from_root}") from exc

    dest_root = to_resolved if to_root.is_symlink() else to_root
    dest = dest_root / skill_dir.name
    if dest.exists():
        raise SkillLayoutError(f"Destination already exists: {dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(skill_dir), str(dest))
    return skill_dir, dest
