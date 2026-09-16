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

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

from quiver import paths as _paths

# The layout itself is defined in quiver.paths so runtime code and this
# module cannot drift apart. Only the harness target maps live here.

@dataclass(frozen=True)
class HarnessSignature:
    """One known harness: where it reads shared assets and what proves it is
    installed.

    ``evidence`` is an any-of list of home-relative paths; a target's own
    parent counts implicitly, so most entries only name the harness's home.
    Evidence is a separate field because it can live away from the targets:
    kilo proves itself with ~/.config/kilo or ~/.local/share/kilo while its
    skills root is ~/.kilo/skills, and cline CLI leaves only ~/.cline/data
    on first run. A "does the skills parent exist" test misses both shapes,
    as does the skills/ glob below.
    """

    skills: Path | None = None
    instructions: Path | None = None
    evidence: tuple[Path, ...] = ()


# Keyed by the label init prints for the harness. Registry keys can differ
# (qwen -> qwen-code); REGISTRY_ALIASES translates on the way out.
HARNESS_SIGNATURES: dict[str, HarnessSignature] = {
    "claude": HarnessSignature(
        skills=Path(".claude/skills"),
        instructions=Path(".claude/CLAUDE.md"),
        evidence=(Path(".claude"),),
    ),
    "codex": HarnessSignature(
        skills=Path(".codex/skills"),
        instructions=Path(".codex/AGENTS.md"),
        evidence=(Path(".codex"),),
    ),
    "cursor": HarnessSignature(
        skills=Path(".cursor/skills"),
        instructions=Path(".cursor/AGENTS.md"),
        evidence=(Path(".cursor"),),
    ),
    "gemini": HarnessSignature(
        skills=Path(".gemini/skills"),
        instructions=Path(".gemini/GEMINI.md"),
        evidence=(Path(".gemini"),),
    ),
    "qwen": HarnessSignature(
        skills=Path(".qwen/skills"),
        instructions=Path(".qwen/QWEN.md"),
        evidence=(Path(".qwen"),),
    ),
    "crush": HarnessSignature(
        skills=Path(".config/crush/skills"),
        instructions=Path(".config/crush/CRUSH.md"),
        evidence=(Path(".config/crush"),),
    ),
    "opencode": HarnessSignature(
        skills=Path(".config/opencode/skills"),
        instructions=Path(".config/opencode/AGENTS.md"),
        evidence=(Path(".config/opencode"), Path(".local/share/opencode")),
    ),
    "droid": HarnessSignature(
        skills=Path(".factory/skills"),
        instructions=Path(".factory/AGENTS.md"),
        evidence=(Path(".factory"),),
    ),
    "copilot": HarnessSignature(
        skills=Path(".copilot/skills"),
        evidence=(Path(".copilot"),),
    ),
    "cline": HarnessSignature(
        skills=Path(".cline/skills"),
        # The cross-tool standard file: docs.cline.bot lists
        # ~/.agents/AGENTS.md as the global instructions cline reads, and
        # its own rules dir (~/Documents/Cline/Rules) is a directory of
        # files, which one symlinked file cannot stand in for.
        instructions=Path(".agents/AGENTS.md"),
        evidence=(Path(".cline"), Path("Documents/Cline")),
    ),
    "kilo": HarnessSignature(
        # Confirmed in the installed binary and kilo.ai/docs: global skills
        # at ~/.kilo/skills, global instructions at ~/.config/kilo/AGENTS.md.
        # Neither directory is made on install — config lives in
        # ~/.config/kilo, session data in ~/.local/share/kilo — so evidence
        # is what the CLI actually creates.
        skills=Path(".kilo/skills"),
        instructions=Path(".config/kilo/AGENTS.md"),
        evidence=(Path(".kilo"), Path(".config/kilo"), Path(".local/share/kilo")),
    ),
}


def _signature_installed(sig: HarnessSignature, home: Path) -> bool:
    """True when any evidence path exists, or the skills root's parent does.

    The implicit parent rule keeps the old seed behaviour: a harness that
    already made its config dir counts as installed even when the skills
    dir inside it does not exist yet. Only the skills parent can imply a
    harness — instruction files may live in shared locations (cline reads
    ``~/.agents/AGENTS.md``, and ``~/.agents`` was quiver's own pre-0.2.7
    root, so a leftover proves nothing about cline) — so a signature with
    no skills root must name its evidence explicitly.
    """
    marks = list(sig.evidence)
    if sig.skills is not None:
        marks.append(sig.skills.parent)
    return any((home / rel).exists() for rel in marks)


# (label, path) view of the signature table, kept for `swe find`'s agents
# tree and the AGENT_FILENAMES set built from it there.
INSTRUCTION_TARGETS: tuple[tuple[str, Path], ...] = tuple(
    (label, sig.instructions)
    for label, sig in HARNESS_SIGNATURES.items()
    if sig.instructions is not None
)

# Skill roots beyond signatures are still discovered, not listed: a harness
# quiver has no signature for still creates its own skills/ directory on
# first run, and the scan finds it. ~60 roots where a fixed list found 14.
SKILL_SCAN_GLOBS: tuple[str, ...] = (".*/skills", ".config/*/skills")

# Directories that look like harness config but are not.
SKILL_SCAN_EXCLUDE: tuple[str, ...] = (
    ".Trash", ".git", ".cache", ".local", ".npm", ".cargo",
)


def _looks_like_backup(name: str) -> bool:
    """e.g. .hermes.pre-bootstrap-20260730-110640 — a snapshot, not a harness."""
    return any(m in name for m in ("pre-bootstrap", ".bak", ".backup", ".old"))


def discover_skill_roots(
    home: Path | None = None, registry: dict | None = None
) -> list[Path]:
    """Every skills/ directory a harness might read, one level into a dotdir.

    Three sources: signature roots for known harnesses whose evidence is on
    disk, the shallow glob for harnesses quiver has no signature for, and
    roots harness.json declares — a ``capabilities.skills.root`` need not be
    glob-shaped (``~/.pi/agent/skills`` is two levels down). Deliberately
    not a full recursive walk: project-level .cursor/skills lives all over
    Desktop and is none of quiver's business. Only the shared root itself
    is excluded, since it is the link target rather than a target.
    """
    home = home or Path.home()
    if registry is None:
        registry = load_registry(home)
    shared = skills_dir(home)
    found: list[Path] = []

    for sig in HARNESS_SIGNATURES.values():
        if sig.skills is not None and _signature_installed(sig, home):
            candidate = home / sig.skills
            if candidate not in found:
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

    declared = _registry_skill_roots(registry, home)
    for path in declared:
        if path not in found:
            found.append(path)

    disabled = _skills_disabled(registry)
    if disabled:
        # The registry wins over inference: supported: false keeps a root
        # out however it was found — declared, signature or glob.
        found = [
            path
            for path in found
            if declared.get(path) not in disabled
            and registry_name(skill_root_label(path, home, registry))
            not in disabled
        ]

    return sorted(found)


def _registry_skill_roots(registry: dict, home: Path) -> dict[Path, str]:
    """``capabilities.skills.root`` paths -> the registry key declaring them."""
    roots: dict[Path, str] = {}
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        skills = (entry.get("capabilities") or {}).get("skills")
        if not isinstance(skills, dict):
            continue
        root = skills.get("root")
        if isinstance(root, str) and root.startswith("~/"):
            roots[home / root[2:]] = key
    return roots


def _skills_disabled(registry: dict) -> set[str]:
    """Registry keys (and their aliases) with ``skills.supported: false``."""
    disabled: set[str] = set()
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        skills = (entry.get("capabilities") or {}).get("skills")
        if isinstance(skills, dict) and skills.get("supported") is False:
            disabled.add(key)
            disabled.update(aliases_of(entry))
    return disabled


def skill_root_label(
    path: Path, home: Path | None = None, registry: dict | None = None
) -> str:
    """`~/.config/opencode/skills` -> `opencode`, `~/.qwen/skills` -> `qwen`.

    A registry-declared root keeps its registry key rather than the parent
    dirname — ``~/.pi/agent/skills`` is ``pi``, never ``agent`` — so picker
    claims and archived overrides join on the name harness.json uses.
    """
    home = home or Path.home()
    if registry is None:
        registry = load_registry(home)
    key = _registry_skill_roots(registry, home).get(path)
    if key is not None:
        return key
    owner = path.parent.name
    return owner[1:] if owner.startswith(".") else owner


def skill_folder_names(root: Path) -> set[str]:
    """Names of skill folders under ``root`` at any depth, following symlinks.

    Not ``rglob``: it does not follow symlinks, so it missed skills that are
    links into a vendor repo and disagreed with what `swe find` reported for
    the same directory.
    """
    import os

    if not root.is_dir():
        return set()
    seen: set[tuple[int, int]] = set()
    names: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        try:
            st = os.stat(dirpath)
        except OSError:
            dirnames[:] = []
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen:
            dirnames[:] = []
            continue
        seen.add(key)
        if "SKILL.md" in filenames:
            names.add(Path(dirpath).name)
    return names


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
        if current == shared or _chain_lands_on(path, shared):
            return "linked", ""
        return "relink", f"points at {current}"

    if not path.is_dir():
        return "create", ""

    names = skill_folder_names(path)
    if not names:
        return "absorb", "empty"

    shared_names = skill_folder_names(shared)
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
    state: str  # linked | create | relink | conflict | skipped | ignored
    detail: str = ""
    # What the link should point at, when that differs per path. Hooks
    # need it: every hook script is its own canonical file, where every
    # instruction file shares AGENTS.md and every skill root shares skills/.
    source: Path | None = None

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
linkignore_file = _paths.linkignore_file_for
skills_dir = _paths.skills_dir_for
hooks_dir = _paths.hooks_dir_for
backups_dir = _paths.backups_dir_for

IGNORED_DETAIL = "listed in ~/.quiver/.linkignore"
ARCHIVED_DETAIL = "archived in harness.json"


def load_registry(home: Path) -> dict:
    """harness.json under ``home``, read-only; {} when absent or unreadable.

    Not ``harness.registry.load_registry``: that resolves the file against the
    real home at import time and seeds one when it is missing, and init must
    neither read the wrong machine's registry in a test nor write one here.
    """
    path = _paths.config_dir_for(home) / "harness.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def aliases_of(entry) -> list[str]:
    """A registry entry's aliases, tolerant of a hand-edited string.

    ``"aliases": "foo"`` in a hand-edited harness.json would otherwise be
    iterated as characters; treat it as one alias, and drop non-strings.
    """
    if not isinstance(entry, dict):
        return []
    aliases = entry.get("aliases")
    if isinstance(aliases, str):
        return [aliases]
    if isinstance(aliases, (list, tuple)):
        return [a for a in aliases if isinstance(a, str)]
    return []


def archived_names(registry: dict) -> set[str]:
    """Registry keys marked ``archived``, plus their aliases."""
    names: set[str] = set()
    for key, entry in registry.items():
        if isinstance(entry, dict) and entry.get("state") == "archived":
            names.add(key)
            names.update(aliases_of(entry))
    return names

SEED_LINKIGNORE = """# Paths swe init leaves alone, one per line, relative to your home.
# Same idea as .gitignore: blank lines and # comments are skipped, * is a
# wildcard (and crosses /), and naming a directory covers everything in it.
# An ignored path is reported as "ignored" and never linked or counted as
# left alone, so a harness can keep its own skills and instructions.
#
#   .agents/skills          leave that one skills directory alone
#   .agents                 leave the whole harness alone
#   .config/*/AGENTS.md     every instruction file under ~/.config
#   .claude/hooks/guard.py  one hook script
"""


class LinkIgnoreError(OSError):
    """~/.quiver/.linkignore exists but cannot be read.

    Deliberately not swallowed: a plan built without the user's ignore list
    would let ``swe init --force`` replace exactly the paths they asked it to
    leave alone. A missing file is the normal case and means no patterns.
    """


def load_linkignore(home: Path | None = None) -> list[str]:
    """Patterns from ~/.quiver/.linkignore, normalised to home-relative form."""
    path = linkignore_file(home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LinkIgnoreError(f"cannot read {path}: {exc.strerror or exc}") from exc
    patterns: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("~/"):
            line = line[2:]
        line = line.strip("/")
        if line:
            patterns.append(line)
    return patterns


def is_linkignored(path: Path, home: Path, patterns: list[str]) -> bool:
    """True when ``path`` or any directory above it matches a pattern."""
    if not patterns:
        return False
    try:
        rel = path.relative_to(home)
    except ValueError:
        return False
    candidates = [rel.as_posix()] + [p.as_posix() for p in rel.parents if p.as_posix() != "."]
    return any(
        fnmatch.fnmatchcase(candidate, pattern)
        for candidate in candidates
        for pattern in patterns
    )


def _chain_lands_on(path: Path, canonical: Path) -> bool:
    """True when a symlink chain fully resolves to the canonical file."""
    try:
        return path.resolve() == canonical.resolve()
    except OSError:
        return False


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
        if current == canonical or _chain_lands_on(path, canonical):
            # The chain case is Home Manager: the harness file links into
            # the nix store, whose entry links back out to the quiver copy.
            # quiver still owns the only real content, so init must leave
            # it alone rather than fight nix over the first hop.
            return LinkStatus(label, path, "linked", "")
        return LinkStatus(label, path, "relink", f"points at {current}")

    if path.exists():
        kind = "directory" if path.is_dir() else "file"
        return LinkStatus(label, path, "conflict", f"real {kind}, needs --force")

    return LinkStatus(label, path, "create", "")


def _instruction_targets(registry: dict, home: Path) -> list[tuple[str, Path, bool]]:
    """(label, home-relative target, installed) per instruction file.

    Signature rows come first, marked installed when the harness's evidence
    is on disk, so a missing parent reads ``create`` rather than ``skipped``
    — evidence, not the file's own directory, is the install test (kilo's
    AGENTS.md lives in ~/.config/kilo but so does its whole config; cline's
    sits in ~/.agents, which nothing else made). A
    ``capabilities.instructions.file`` entry (``~/``-relative) adds a target
    labelled by its registry key, and replaces a signature entry for the
    same harness, so a harness that reads ``AGENTS.md`` where the table
    guessed ``CLAUDE.md`` ends up with exactly one target: the right one.
    Registry rows are never marked installed: a declared file on a machine
    the harness left behind is skipped noise, not a reason to recreate it.
    """
    targets: list[tuple[str, Path, bool]] = []
    seen: set[Path] = set()
    for label, sig in HARNESS_SIGNATURES.items():
        if sig.instructions is None or sig.instructions in seen:
            continue
        seen.add(sig.instructions)
        targets.append((label, sig.instructions, _signature_installed(sig, home)))
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        caps = entry.get("capabilities") or {}
        file = (caps.get("instructions") or {}).get("file")
        if not (isinstance(file, str) and file.startswith("~/")):
            continue
        targets = [t for t in targets if registry_name(t[0]) != key]
        targets.append((key, Path(file[2:]), False))
    return targets


def archived_override(status: LinkStatus, archived: set[str]) -> None:
    """Archived means unmanaged: anything init would change becomes ignored.

    A path already ``linked`` or ``skipped`` keeps its state (init never
    unlinks), and a ``.linkignore`` ignore is applied before this, so its
    own detail wins.
    """
    if (
        status.state in ("create", "relink", "absorb", "keep", "conflict")
        and registry_name(status.label) in archived
    ):
        status.state, status.detail = "ignored", ARCHIVED_DETAIL


def plan(
    home: Path | None = None,
    patterns: list[str] | None = None,
    registry: dict | None = None,
) -> tuple[list[LinkStatus], list[LinkStatus]]:
    """Return (instruction statuses, skill statuses) for the current machine.

    A path listed in ~/.quiver/.linkignore is still in the plan, as
    ``ignored``, so the report can say it was seen and deliberately left out.
    Nothing acts on that state. ``patterns`` lets a caller that has already
    loaded (and validated) the ignore file pass it in; otherwise it is read
    here and an unreadable file raises ``LinkIgnoreError``. ``registry`` is
    the harness.json dict: it supplies extra instruction targets and marks
    archived harnesses unmanaged; ``None`` loads it read-only.
    """
    home = home or Path.home()
    if patterns is None:
        patterns = load_linkignore(home)
    if registry is None:
        registry = load_registry(home)
    archived = archived_names(registry)
    instructions = []
    for label, rel, installed in _instruction_targets(registry, home):
        if is_linkignored(home / rel, home, patterns):
            instructions.append(LinkStatus(label, home / rel, "ignored", IGNORED_DETAIL))
        else:
            status = inspect(label, rel, agents_file(home), home)
            if status.state == "skipped" and installed:
                # Evidence says the harness is here; only the directory is
                # missing, and init creates that anyway.
                status = LinkStatus(label, home / rel, "create", "")
            archived_override(status, archived)
            instructions.append(status)
    skills = []
    for path in discover_skill_roots(home, registry):
        if is_linkignored(path, home, patterns):
            state, detail = "ignored", IGNORED_DETAIL
        else:
            state, detail = classify_skill_root(path, home)
        status = LinkStatus(skill_root_label(path, home, registry), path, state, detail)
        archived_override(status, archived)
        skills.append(status)
    return instructions, skills


# Quiver's harness labels are the short names each tool calls itself. The
# registry in harness.json uses a few longer keys, so translate on the way out.
REGISTRY_ALIASES: dict[str, str] = {
    "qwen": "qwen-code",
    "vibe": "mistral-vibe",
    "factory": "droid",  # droid's config dir is ~/.factory
}


def registry_name(label: str) -> str:
    return REGISTRY_ALIASES.get(label, label)


def link_states(home: Path | None = None) -> dict[str, dict[str, str]]:
    """Map registry tool name -> {"agents": state, "skills": state}.

    States are the same vocabulary ``inspect`` produces, so a caller can render
    them without knowing how quiver decides them. Tools with no known
    instruction convention simply do not appear under "agents".
    """
    home = home or Path.home()
    out: dict[str, dict[str, str]] = {}

    # A read-only listing should not die on a bad ignore file, but it must
    # not pretend the file is empty either: say so, then show real states.
    try:
        patterns = load_linkignore(home)
    except LinkIgnoreError as exc:
        import sys

        print(f"warning: {exc}; showing link states without it", file=sys.stderr)
        patterns = []
    instructions, skills = plan(home, patterns)
    for status in instructions:
        out.setdefault(registry_name(status.label), {})["agents"] = status.state

    for status in skills:
        if status.label == "agents":
            continue  # ~/.config/agents is a shared dir, not a harness
        out.setdefault(registry_name(status.label), {})["skills"] = status.state

    return out
