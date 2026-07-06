"""Skills-root symlink recommendations for unified skill discovery."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillsSymlinkHint:
    label: str
    path: Path
    action: str  # create_shared | symlink | ok
    command: str
    reason: str


SHARED_REL = Path(".agents/skills")

SYMLINK_TARGETS = (
    ("cursor", Path(".cursor/skills")),
    ("codex", Path(".codex/skills")),
    ("claude", Path(".claude/skills")),
)


def skills_symlink_hints(home: Path | None = None) -> list[SkillsSymlinkHint]:
    """Return symlink setup hints when per-tool skill roots diverge from shared tree."""
    home = home or Path.home()
    shared = home / SHARED_REL
    hints: list[SkillsSymlinkHint] = []

    if not shared.exists():
        hints.append(
            SkillsSymlinkHint(
                label="shared",
                path=shared,
                action="create_shared",
                command=f"mkdir -p {shared}",
                reason="Shared skills root ~/.agents/skills does not exist yet",
            )
        )
        return hints

    try:
        shared_real = shared.resolve()
    except OSError:
        return hints

    for label, rel in SYMLINK_TARGETS:
        path = home / rel
        if not path.exists():
            hints.append(
                SkillsSymlinkHint(
                    label=label,
                    path=path,
                    action="symlink",
                    command=f"ln -sf {shared} {path}",
                    reason=f"Link {label} skills to the shared tree",
                )
            )
            continue
        if path.is_symlink():
            try:
                if path.resolve() == shared_real:
                    hints.append(
                        SkillsSymlinkHint(
                            label=label,
                            path=path,
                            action="ok",
                            command="",
                            reason=f"{label} skills already point at shared tree",
                        )
                    )
                else:
                    hints.append(
                        SkillsSymlinkHint(
                            label=label,
                            path=path,
                            action="symlink",
                            command=f"ln -sf {shared} {path}",
                            reason=f"{label} symlink points elsewhere — update to shared tree",
                        )
                    )
            except OSError:
                pass
            continue
        # Real directory — do not auto-overwrite; suggest manual merge
        try:
            if path.resolve() == shared_real:
                hints.append(
                    SkillsSymlinkHint(
                        label=label,
                        path=path,
                        action="ok",
                        command="",
                        reason=f"{label} skills resolve to shared tree",
                    )
                )
            else:
                hints.append(
                    SkillsSymlinkHint(
                        label=label,
                        path=path,
                        action="manual",
                        command=f"# after backing up: rm -rf {path} && ln -sf {shared} {path}",
                        reason=f"{label} has a separate skills directory (merge manually before symlinking)",
                    )
                )
        except OSError:
            pass

    return hints


def apply_skills_symlink_hints(hints: list[SkillsSymlinkHint], home: Path | None = None) -> list[str]:
    """Apply safe symlink hints (mkdir shared, ln -sf when target missing)."""
    home = home or Path.home()
    applied: list[str] = []

    for hint in hints:
        if hint.action == "create_shared":
            hint.path.mkdir(parents=True, exist_ok=True)
            applied.append(f"mkdir {hint.label}")
        elif hint.action == "symlink" and not hint.path.exists():
            hint.path.parent.mkdir(parents=True, exist_ok=True)
            hint.path.symlink_to(home / SHARED_REL)
            applied.append(f"symlink {hint.label}")

    return applied
