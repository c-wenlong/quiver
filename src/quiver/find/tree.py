"""Data behind `swe find`: where each shared asset lives and what links to it.

`swe init` answers "what would change". This answers "what is there", which is
the question you ask when a slash command has gone missing or a harness is not
picking something up. Same underlying classification, read-only presentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from quiver import paths
from quiver.init.layout import (
    INSTRUCTION_TARGETS,
    classify_skill_root,
    discover_skill_roots,
    inspect,
    skill_root_label,
)


@dataclass
class Node:
    """One path in the tree, plus why it is interesting."""

    label: str
    path: Path
    kind: str          # symlink | directory | missing | file
    state: str         # linked | absorb | keep | create | relink | conflict | skipped
    target: Path | None = None
    count: int = 0
    detail: str = ""


@dataclass
class PluginNode:
    marketplace: str
    name: str
    path: Path
    skills: list[str] = field(default_factory=list)


def _skill_names(root: Path) -> list[str]:
    """Skill folder names directly under ``root``.

    Not ``rglob``: it does not follow symlinks, and several skills are links
    into a vendor repo (lazyweb), so a recursive glob silently undercounts.
    Skills live exactly one level down, so a shallow scan is also correct.
    """
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if (d / "SKILL.md").is_file()
    )


def _describe(path: Path) -> tuple[str, Path | None]:
    if path.is_symlink():
        try:
            return "symlink", Path(path.readlink())
        except OSError:
            return "symlink", None
    if path.is_dir():
        return "directory", None
    if path.exists():
        return "file", None
    return "missing", None


def agents_tree(home: Path | None = None) -> tuple[Path, list[Node]]:
    """The canonical AGENTS.md and every harness file pointing at it."""
    home = home or Path.home()
    canonical = paths.agents_file_for(home)
    nodes = []
    for label, rel in INSTRUCTION_TARGETS:
        status = inspect(label, rel, canonical, home)
        kind, target = _describe(status.path)
        nodes.append(Node(label, status.path, kind, status.state, target,
                          detail=status.detail))
    return canonical, nodes


def skills_tree(home: Path | None = None) -> tuple[Path, list[Node]]:
    """Every skills root on the machine, classified against the shared tree."""
    home = home or Path.home()
    shared = paths.skills_dir_for(home)
    nodes = []
    for path in discover_skill_roots(home):
        state, detail = classify_skill_root(path, home)
        kind, target = _describe(path)
        count = 0
        if kind == "directory":
            count = len(_skill_names(path))
        nodes.append(Node(skill_root_label(path, home), path, kind, state,
                          target, count, detail))
    return shared, nodes


def plugin_tree(home: Path | None = None) -> list[PluginNode]:
    """Plugins grouped by the local marketplace directory that holds them."""
    home = home or Path.home()
    root = paths.quiver_dir_for(home) / "plugins"
    out: list[PluginNode] = []
    if not root.is_dir():
        return out
    for market in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        # A marketplace directory holds plugin dirs; a plugin holds skills/.
        children = [c for c in sorted(market.iterdir())
                    if c.is_dir() and not c.name.startswith(".")]
        if (market / "skills").is_dir():
            # Flat layout: this directory is itself a plugin.
            out.append(PluginNode("", market.name, market,
                                  _skill_names(market / "skills")))
            continue
        for plug in children:
            skills = _skill_names(plug / "skills")
            out.append(PluginNode(market.name, plug.name, plug, skills))
    return out


def flat_skills(home: Path | None = None) -> list[str]:
    shared = paths.skills_dir_for(home)
    if not shared.is_dir():
        return []
    return _skill_names(shared)


# Directories that are never worth walking into. Pruning these takes an
# unbounded scan of a home directory from minutes to ~3s; real hits reach
# depth 14, so there is no useful depth cap to apply instead.
PRUNE_DIRS = frozenset({
    # Build output and VCS
    "node_modules", ".git", ".venv", "venv", "dist", "build", ".next",
    "target", ".cache", "__pycache__", ".mypy_cache", ".pytest_cache",
    "site-packages", ".gradle", "Pods", ".terraform", ".tox",
    ".build", "checkouts", "vendor", "third_party", "Carthage",
    # macOS
    "Library", ".Trash", "Applications",
    # Package-manager homes. These dominate a home-directory walk (~/.npm
    # alone is ~20k directories) and cannot contain agent instructions.
    ".npm", ".go", ".bun", ".cargo", ".rustup", ".rbenv", ".pyenv", ".nvm",
    ".gem", ".m2", ".deno", ".pnpm-store", ".expo", ".yarn", ".cocoapods",
    ".android", ".gradle-cache", ".sdkman",
})

# Filenames a harness reads as its instructions, from INSTRUCTION_TARGETS plus
# the project-level names that never appear in a home directory.
AGENT_FILENAMES = frozenset(
    {rel.name for _, rel in INSTRUCTION_TARGETS} | {"AGENTS.md", "CLAUDE.local.md"}
)


def walk(root: Path):
    """Yield every directory under ``root``, skipping the prune list.

    os.walk with in-place dirnames pruning, so a skipped tree is never
    descended into rather than walked and filtered.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        yield Path(dirpath), dirnames, filenames


def scan_agents(root: Path, home: Path | None = None) -> list[Node]:
    """Every agent-instruction file under ``root``, with its link state."""
    home = home or Path.home()
    canonical = paths.agents_file_for(home)
    found: list[Node] = []
    for dirpath, _dirnames, filenames in walk(root):
        for fn in filenames:
            if fn not in AGENT_FILENAMES:
                continue
            path = dirpath / fn
            if path == canonical:
                continue
            kind, target = _describe(path)
            if kind == "symlink":
                state = "linked" if target == canonical else "relink"
            else:
                state = "unlinked"
            found.append(Node(fn, path, kind, state, target,
                              detail="" if state == "linked" else "not the shared copy"))
    return sorted(found, key=lambda n: str(n.path))


def scan_skill_roots(root: Path, home: Path | None = None) -> list[Node]:
    """Every directory named ``skills`` holding at least one SKILL.md."""
    home = home or Path.home()
    shared = paths.skills_dir_for(home)
    found: list[Node] = []
    seen: set[Path] = set()
    for dirpath, dirnames, _filenames in walk(root):
        for d in list(dirnames):
            if d != "skills":
                continue
            path = dirpath / d
            if path in seen or path == shared:
                continue
            seen.add(path)
            kind, target = _describe(path)
            if kind == "symlink":
                state = "linked" if target == shared else "relink"
                count = 0
            else:
                count = len(_skill_names(path))
                if not count:
                    continue          # an empty skills/ is noise in a project scan
                state, _detail = classify_skill_root(path, home)
            found.append(Node(path.parent.name, path, kind, state, target, count))
    return sorted(found, key=lambda n: str(n.path))


# --- scope ------------------------------------------------------------
#
# A file is GLOBAL when it sits directly in a harness root, i.e. ~/.<tool>/
# or ~/.config/<tool>/. Those are the ones a harness loads into every session
# regardless of where you are.
#
# One level deeper inside a harness directory is something else entirely:
# plugin caches, vendored repos, sandbox workspaces, editor extensions. Those
# ship their own AGENTS.md and you never see them while coding, which makes
# them the interesting surface for injected instructions. They are reported
# separately rather than folded into either bucket.
#
# Everything else is LOCAL: a project file, loaded only when you work there.

SCOPE_GLOBAL = "global"
SCOPE_LOCAL = "local"
SCOPE_VENDORED = "vendored"


def scope_of(path: Path, home: Path | None = None) -> str:
    """Classify a path as global, vendored, or local."""
    home = home or Path.home()
    try:
        parent = path.relative_to(home).parts[:-1]
    except ValueError:
        return SCOPE_LOCAL          # outside home entirely: a project path
    if not parent:
        return SCOPE_LOCAL
    if len(parent) == 1 and parent[0].startswith("."):
        return SCOPE_GLOBAL         # ~/.claude/CLAUDE.md
    if len(parent) == 2 and parent[0] == ".config":
        return SCOPE_GLOBAL         # ~/.config/opencode/AGENTS.md
    if parent[0].startswith(".") and parent[0] != ".config":
        return SCOPE_VENDORED       # ~/.codex/plugins/cache/.../AGENTS.md
    return SCOPE_LOCAL


def node_scope(node, home: Path | None = None) -> str:
    """Scope of a node, letting its link state override the path rule.

    A root symlinked to the shared tree is global however deep it sits:
    ~/.astrbot/data/skills is two levels down but quiver linked it on purpose,
    so it loads everywhere and is not vendored content.
    """
    if node.state == "linked":
        return SCOPE_GLOBAL
    return scope_of(node.path, home)


def filter_scope(nodes, scope: str, home: Path | None = None):
    """Split nodes into (shown, vendored_count) for the requested scope."""
    home = home or Path.home()
    tagged = [(n, node_scope(n, home)) for n in nodes]
    vendored = sum(1 for _n, sc in tagged if sc == SCOPE_VENDORED)
    if scope == "all":
        return [n for n, _ in tagged], 0
    keep = {"global": {SCOPE_GLOBAL}, "local": {SCOPE_LOCAL}}[scope]
    return [n for n, sc in tagged if sc in keep], vendored
