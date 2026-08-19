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

SKILL_TARGETS: tuple[tuple[str, Path], ...] = (
    ("claude", Path(".claude/skills")),
    ("codex", Path(".codex/skills")),
    ("cursor", Path(".cursor/skills")),
    ("qwen", Path(".qwen/skills")),
    ("forge", Path(".forge/skills")),
    ("cline", Path(".cline/skills")),
    ("kiro", Path(".kiro/skills")),
    ("vibe", Path(".vibe/skills")),
    ("augment", Path(".augment/skills")),
    ("continue", Path(".continue/skills")),
    ("pi", Path(".pi/skills")),
    ("grok", Path(".grok/skills")),
    ("crush", Path(".config/crush/skills")),
    # Kept so anything still pointing at the old shared root keeps resolving.
    ("agents-legacy", Path(".agents/skills")),
)

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

    @property
    def changed(self) -> bool:
        return self.state in ("create", "relink", "conflict")


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
    skills = [
        inspect(label, rel, skills_dir(home), home) for label, rel in SKILL_TARGETS
    ]
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

    for label, rel in SKILL_TARGETS:
        if label == "agents-legacy":
            continue  # not a harness, just a back-compat alias
        name = _registry_name(label)
        state = inspect(label, rel, skills_dir(home), home).state
        out.setdefault(name, {})["skills"] = state

    return out
