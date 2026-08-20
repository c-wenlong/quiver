"""Declarative map of what ~/.quiver owns and where each harness expects it.

The premise: every harness wants the same global instructions and the same
skill tree, but each insists on its own filename and location. Quiver keeps one
real copy and symlinks it into place under whatever name the harness wants.

Instruction filenames below were confirmed by grepping the installed binaries
for the literal string, not from memory. Harnesses whose convention could not
be confirmed are deliberately absent: creating a file a tool never reads is
noise, and guessing wrong is worse than skipping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quiver import paths as _paths

# The layout itself is defined in quiver.paths so runtime code and this
# module cannot drift apart. Only the harness target maps live here.

# (harness label, path relative to home)
INSTRUCTION_TARGETS: tuple[tuple[str, Path], ...] = (
    ("claude", Path(".claude/CLAUDE.md")),
    ("codex", Path(".codex/AGENTS.md")),
    ("cursor", Path(".cursor/AGENTS.md")),
    ("gemini", Path(".gemini/GEMINI.md")),
    ("qwen", Path(".qwen/QWEN.md")),
    ("crush", Path(".config/crush/CRUSH.md")),
    ("opencode", Path(".config/opencode/AGENTS.md")),
    ("droid", Path(".factory/AGENTS.md")),
    ("amp", Path(".amp/AGENTS.md")),
)

# Skill roots are discovered rather than listed. A hardcoded list goes stale
# the moment a new harness is installed, and each one creates its own
# skills/ directory on first run. Scanning finds ~60 where a list found 14.
SKILL_SCAN_GLOBS: tuple[str, ...] = (".*/skills", ".config/*/skills")

# Discovery only finds roots that exist. A harness installed but never run has
# no skills/ yet, and linking it up front is the difference between skills
# working on first launch and not. These are seeded when their parent dir
# exists, which is the same "is it installed" test discovery uses.
SKILL_SEED_ROOTS: tuple[Path, ...] = (
    Path(".claude/skills"),
    Path(".codex/skills"),
    Path(".cursor/skills"),
    Path(".gemini/skills"),
    Path(".qwen/skills"),
    Path(".factory/skills"),
    Path(".copilot/skills"),
    Path(".amp/skills"),
    Path(".config/opencode/skills"),
    Path(".config/crush/skills"),
)

# Directories that look like harness config but are not.
SKILL_SCAN_EXCLUDE: tuple[str, ...] = (
    ".Trash", ".git", ".cache", ".local", ".npm", ".cargo",
)


def _looks_like_backup(name: str) -> bool:
    """e.g. .hermes.pre-bootstrap-20260730-110640 — a snapshot, not a harness."""
    return any(m in name for m in ("pre-bootstrap", ".bak", ".backup", ".old"))


def discover_skill_roots(home: Path | None = None) -> list[Path]:
    """Every skills/ directory a harness might read, one level into a dotdir.

    Deliberately not a full recursive walk: project-level .cursor/skills lives
    all over Desktop and is none of quiver's business. Only the shared root
    itself is excluded, since it is the link target rather than a target.
    """
    home = home or Path.home()
    shared = skills_dir(home)
    found: list[Path] = []

    for rel in SKILL_SEED_ROOTS:
        candidate = home / rel
        if candidate.parent.is_dir() and candidate not in found:
            found.append(candidate)

    for pattern in SKILL_SCAN_GLOBS:
        for path in home.glob(pattern):
            owner = path.parent.name
            if owner in SKILL_SCAN_EXCLUDE or _looks_like_backup(owner):
                continue
            try:
                if path.resolve() == shared.resolve():
                    if not path.is_symlink():
                        continue  # the shared tree itself
            except OSError:
                pass
            if path not in found:
                found.append(path)
    return sorted(found)


def skill_root_label(path: Path, home: Path | None = None) -> str:
    """`~/.config/opencode/skills` -> `opencode`, `~/.qwen/skills` -> `qwen`."""
    owner = path.parent.name
    return owner[1:] if owner.startswith(".") else owner


def classify_skill_root(path: Path, home: Path | None = None) -> tuple[str, str]:
    """Return (state, detail) for one discovered skills directory.

    Splits the old catch-all "conflict" into two very different cases. A real
    directory holding only copies of skills already in the shared tree can be
    replaced with nothing lost. One holding skills that exist nowhere else must
    never be replaced silently, however tempting the tidiness.
    """
    home = home or Path.home()
    shared = skills_dir(home)

    if path.is_symlink():
        try:
            current = Path(path.readlink())
        except OSError:
            return "relink", "unreadable symlink"
        return ("linked", "") if current == shared else ("relink", f"points at {current}")

    if not path.is_dir():
        return "create", ""

    names = {p.parent.name for p in path.rglob("SKILL.md")}
    if not names:
        return "absorb", "empty"

    shared_names = {p.parent.name for p in shared.rglob("SKILL.md")} if shared.is_dir() else set()
    unique = names - shared_names
    if not unique:
        return "absorb", f"{len(names)} skills, all already shared"
    return "keep", f"{len(unique)} of {len(names)} skills exist nowhere else"


SEED_AGENTS_MD = """# Agent instructions

Canonical global instructions for every coding harness. Lives at
`~/.quiver/AGENTS.md` and is symlinked into each harness under that harness's
own filename, so editing this one file changes all of them.

Run `swe init --check` to see the link status.

## Response style

Replace this section with your own rules.
"""


@dataclass
class LinkStatus:
    """One harness path and what quiver would do with it."""

    label: str
    path: Path
    state: str  # linked | create | relink | conflict | skipped
    detail: str = ""

    # "absorb" replaces a real directory whose contents are all duplicates or
    # empty, so nothing is lost. "keep" is a directory holding skills that
    # exist nowhere else: reported, never touched without --force.
    SAFE_TO_CHANGE = ("create", "relink", "absorb")

    @property
    def changed(self) -> bool:
        return self.state in self.SAFE_TO_CHANGE or self.state == "conflict"

    @property
    def protected(self) -> bool:
        return self.state == "keep"


quiver_dir = _paths.quiver_dir_for
agents_file = _paths.agents_file_for
skills_dir = _paths.skills_dir_for
backups_dir = _paths.backups_dir_for


def inspect(label: str, rel: Path, canonical: Path, home: Path) -> LinkStatus:
    """Classify one target without touching the filesystem."""
    path = home / rel

    # A missing parent means the harness was never installed. Creating its
    # config dir just to drop a file in would be litter.
    if not path.parent.exists():
        return LinkStatus(label, path, "skipped", "harness not installed")

    if path.is_symlink():
        try:
            current = Path(path.readlink())
        except OSError:
            return LinkStatus(label, path, "relink", "unreadable symlink")
        if current == canonical:
            return LinkStatus(label, path, "linked", "")
        return LinkStatus(label, path, "relink", f"points at {current}")

    if path.exists():
        kind = "directory" if path.is_dir() else "file"
        return LinkStatus(label, path, "conflict", f"real {kind}, needs --force")

    return LinkStatus(label, path, "create", "")


def plan(home: Path | None = None) -> tuple[list[LinkStatus], list[LinkStatus]]:
    """Return (instruction statuses, skill statuses) for the current machine."""
    home = home or Path.home()
    instructions = [
        inspect(label, rel, agents_file(home), home)
        for label, rel in INSTRUCTION_TARGETS
    ]
    skills = []
    for path in discover_skill_roots(home):
        state, detail = classify_skill_root(path, home)
        skills.append(
            LinkStatus(skill_root_label(path, home), path, state, detail)
        )
    return instructions, skills


# Quiver's harness labels are the short names each tool calls itself. The
# registry in tools.json uses a few longer keys, so translate on the way out.
REGISTRY_ALIASES: dict[str, str] = {
    "qwen": "qwen-code",
    "vibe": "mistral-vibe",
}


def _registry_name(label: str) -> str:
    return REGISTRY_ALIASES.get(label, label)


def link_states(home: Path | None = None) -> dict[str, dict[str, str]]:
    """Map registry tool name -> {"agents": state, "skills": state}.

    States are the same vocabulary ``inspect`` produces, so a caller can render
    them without knowing how quiver decides them. Tools with no known
    instruction convention simply do not appear under "agents".
    """
    home = home or Path.home()
    out: dict[str, dict[str, str]] = {}

    for label, rel in INSTRUCTION_TARGETS:
        name = _registry_name(label)
        state = inspect(label, rel, agents_file(home), home).state
        out.setdefault(name, {})["agents"] = state

    for path in discover_skill_roots(home):
        label = skill_root_label(path, home)
        if label == "agents":
            continue  # ~/.config/agents is a shared dir, not a harness
        state, _ = classify_skill_root(path, home)
        out.setdefault(_registry_name(label), {})["skills"] = state

    return out
