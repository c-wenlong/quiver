"""Discover skill catalogs on Desktop/Documents vs configured roots."""

from dataclasses import dataclass
from pathlib import Path

from quiver.skills.catalogs import (
    DEFAULT_SEARCH_ROOTS,
    add_skill_catalog,
    count_skill_md,
    discover_catalog_dirs,
    load_skill_catalogs,
    suggest_catalog_label,
)


@dataclass(frozen=True)
class SkillCatalogFinding:
    path: Path
    label: str
    skill_count: int
    source: str  # desktop | documents | configured
    status: str  # new | registered


def _known_root_paths(home: Path, cwd: Path) -> set[Path]:
    from quiver.skills.discovery import skill_roots

    known: set[Path] = set()
    for _label, root in skill_roots(home=home, cwd=cwd):
        try:
            known.add(root.resolve())
        except OSError:
            continue
    for entry in load_skill_catalogs():
        try:
            known.add(Path(entry["path"]).expanduser().resolve())
        except (OSError, KeyError):
            continue
    return known


def _source_for_path(path: Path, home: Path) -> str:
    try:
        rel = path.relative_to(home)
    except ValueError:
        return "catalog"
    if rel.parts and rel.parts[0].lower() == "desktop":
        return "desktop"
    if rel.parts and rel.parts[0].lower() == "documents":
        return "documents"
    return "catalog"


def discover_skill_catalogs(
    home: Path | None = None,
    cwd: Path | None = None,
    *,
    include_registered: bool = False,
) -> list[SkillCatalogFinding]:
    """Scan Desktop/Documents for skill catalog dirs not yet registered."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    known = _known_root_paths(home, cwd)
    findings: list[SkillCatalogFinding] = []

    for path in discover_catalog_dirs(home):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        status = "registered" if resolved in known else "new"
        if status == "registered" and not include_registered:
            continue
        findings.append(
            SkillCatalogFinding(
                path=resolved,
                label=suggest_catalog_label(resolved),
                skill_count=count_skill_md(resolved),
                source=_source_for_path(resolved, home),
                status=status,
            )
        )

    findings.sort(key=lambda f: (0 if f.status == "new" else 1, f.label))
    return findings


def apply_skill_catalog_findings(findings: list[SkillCatalogFinding]) -> list[str]:
    added: list[str] = []
    for finding in findings:
        if finding.status != "new":
            continue
        entry = add_skill_catalog(finding.path, finding.label)
        added.append(entry["label"])
    return added
