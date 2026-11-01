"""Skills-root symlink recommendations for unified skill discovery."""

from dataclasses import dataclass
from pathlib import Path

from swe.skills.layout import HARNESS_ROOTS, SHARED_LABEL, SHARED_REL

# Backward-compatible alias used by setup wizard.
SYMLINK_TARGETS = tuple((label, rel) for label, rel in HARNESS_ROOTS if label != SHARED_LABEL)


def _may_link(label: str, path: Path, home: Path) -> bool:
    """Whether a missing skills root ``path`` may be linked.

    Yes when the harness's folder (the root's parent) exists. When it does
    not, only an install that never ran qualifies: the binary answers on
    PATH (``init.layout.command_on_path``, the test swe init uses, with
    the registry's ``command`` honoured) and the harness is not archived,
    since init leaves archived harnesses alone. A file or broken link
    where the folder should be is left alone, and so is everything when
    harness.json will not parse, since the archive cannot be read.
    """
    parent = path.parent
    if parent.is_dir():
        return True
    if parent.is_symlink() or parent.exists():
        return False

    from swe.configuration import CorruptConfigurationError
    from swe.init.layout import command_on_path, load_registry

    try:
        registry = load_registry(home)
    except CorruptConfigurationError:
        return False
    entry = registry.get(label)
    if isinstance(entry, dict) and entry.get("state") == "archived":
        return False
    return command_on_path(label, registry)


@dataclass(frozen=True)
class SkillsSymlinkHint:
    label: str
    path: Path
    action: str  # create_shared | symlink | ok | manual
    command: str
    reason: str


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
                reason="Shared skills root ~/.quiver/skills does not exist yet",
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
            if not _may_link(label, path, home):
                # Not installed here. Linking it anyway made ~/.augment,
                # ~/.pi and the like on every run after the first, and
                # swe init then registered them and linked AGENTS.md in.
                continue
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
    """Apply safe symlink hints (mkdir shared, ln -sf when target missing).

    A harness's home folder is created only for an install that never ran
    (see ``_may_link``). Making one for a harness that is not installed
    makes it look installed.
    """
    home = home or Path.home()
    applied: list[str] = []

    for hint in hints:
        if hint.action == "create_shared":
            hint.path.mkdir(parents=True, exist_ok=True)
            applied.append(f"mkdir {hint.label}")
        elif hint.action == "symlink" and not hint.path.exists():
            if not _may_link(hint.label, hint.path, home):
                continue
            try:
                hint.path.parent.mkdir(parents=True, exist_ok=True)
            except (FileExistsError, NotADirectoryError):
                continue  # a file further up the path; leave it alone
            if hint.path.is_symlink():
                # A dangling link (one into the removed ~/.agents/skills,
                # say) fails exists() but still holds the path, and
                # symlink_to raised FileExistsError on every apply. Nothing
                # sits behind it to lose.
                hint.path.unlink()
            hint.path.symlink_to(home / SHARED_REL)
            applied.append(f"symlink {hint.label}")

    return applied
