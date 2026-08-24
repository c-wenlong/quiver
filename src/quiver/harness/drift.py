"""Drift checks for ``swe doctor``.

``swe doctor`` used to diagnose one thing: Node/PATH mismatches that hide a
globally installed harness. This module adds a second, unrelated kind of
health check — places where two parts of quiver that are supposed to agree
have quietly drifted apart:

  * every ``help_text.py`` topic should have a matching command, and vice
    versa (``HELP vs DISPATCH``)
  * every ``~/.quiver/config/harness.json`` entry should have a sane shape
    (``REGISTRY SCHEMA``)
  * the harness names/paths hardcoded in ``skills/layout.py`` and (here)
    mirrored from ``find/plugins.py`` should still match what the registry
    says each harness supports (``CODE-TABLE vs DATA``)
  * nothing at the top level of the repo or ``~/.quiver`` should be a
    symlink pointing at a target that no longer exists (``DANGLING
    SYMLINKS``)

Every check is read-only and cheap — no network, no subprocesses, a handful
of stats at most — so it is safe to run on every ``swe doctor`` invocation
rather than gating it behind a flag.

Each check is exposed both as a pure function (``check_*``, takes plain data
in, returns findings, no I/O) for unit testing, and wired up with real data
by ``run_drift_checks()``, which is what ``cmd_doctor`` calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class Finding:
    severity: str  # "warn" | "error"
    area: str
    message: str


# ---------------------------------------------------------------------------
# 1. HELP vs DISPATCH
# ---------------------------------------------------------------------------

# Top-level commands that are intentionally not full help topics: aliases of
# a command that does have a topic (``ls`` -> ``list``, etc.), and the hidden
# shell-completion entry point. Keep this in sync by hand if COMMANDS grows a
# new alias-only entry.
NO_TOPIC_WHITELIST = frozenset({
    "__complete", "ls", "rm", "run", "sk", "hs", "pv", "-h", "--help", "help",
})

_HELP_KEY_RE = re.compile(r'^    "([A-Za-z0-9_-]+)":\s*\(', re.MULTILINE)


def check_help_vs_dispatch(help_topics: set[str], commands: dict) -> list[Finding]:
    """Compare help_text.py's topics against cli.py's COMMANDS dispatch.

    ``help_topics`` and ``commands`` are passed in rather than imported here
    so this stays a pure function callers can unit test with fabricated
    data — see ``_real_help_topics`` / ``run_drift_checks`` for how the real
    ones are gathered.
    """
    findings: list[Finding] = []

    orphan_topics = sorted(t for t in help_topics if t not in commands)
    for topic in orphan_topics:
        findings.append(Finding(
            "warn", "help",
            f"help topic {topic!r} has no matching command in cli.py's COMMANDS",
        ))

    missing_topics = sorted(
        name for name in commands
        if name not in help_topics and name not in NO_TOPIC_WHITELIST
    )
    for name in missing_topics:
        findings.append(Finding(
            "warn", "help",
            f"command {name!r} has no help topic in help_text.py's HELP",
        ))

    return findings


def _real_help_topics() -> set[str]:
    """Topic names in help_text.py's HELP dict, found by regex.

    Regex rather than import: help_text.py is being actively edited
    elsewhere while this module is developed, and a topic-name check has no
    business importing (and therefore depending on the current syntactic
    validity of) a file another change is mid-flight on.
    """
    help_text_path = Path(__file__).resolve().parent.parent / "help_text.py"
    try:
        text = help_text_path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(_HELP_KEY_RE.findall(text))


def _real_commands() -> dict:
    from quiver.cli import COMMANDS
    return COMMANDS


# ---------------------------------------------------------------------------
# 2. REGISTRY SCHEMA
# ---------------------------------------------------------------------------

VALID_STATES = frozenset({"active", "starred", "archived"})


def check_registry_schema(registry: dict) -> list[Finding]:
    """Validate the shape of every harness.json entry.

    An unknown ``state`` is an error: ``state_of()`` treats any truthy
    string as-is, so a typo'd state silently falls out of both the starred
    and archived buckets instead of raising — this is the one drift finding
    that gates ``swe doctor``'s exit code. Everything else here is stale
    data left behind by hand-editing the file rather than going through
    ``swe hs star`` / ``swe hs archive``, which is a smell worth a warning
    but nothing broken yet.
    """
    findings: list[Finding] = []

    for name, entry in registry.items():
        if not isinstance(entry, dict):
            findings.append(Finding("error", "registry", f"{name}: entry is not an object"))
            continue

        state = entry.get("state")
        if state is not None and state not in VALID_STATES:
            findings.append(Finding(
                "error", "registry",
                f"{name}: state {state!r} is not one of {sorted(VALID_STATES)}",
            ))

        if "pin" in entry and state != "starred":
            findings.append(Finding(
                "warn", "registry",
                f"{name}: has a pin but state is {(state or 'active')!r}, not starred",
            ))

        if "archived" in entry and state != "archived":
            findings.append(Finding(
                "warn", "registry",
                f"{name}: has an archived record but state is {(state or 'active')!r}, not archived",
            ))

        capabilities = entry.get("capabilities")
        if not isinstance(capabilities, dict):
            continue
        for cap_name, cap in capabilities.items():
            if not isinstance(cap, dict) or not cap.get("supported"):
                continue
            root = cap.get("root")
            if not root:
                continue
            if not Path(root).expanduser().exists():
                findings.append(Finding(
                    "warn", "registry",
                    f"{name}: capabilities.{cap_name}.root {root} does not exist on disk",
                ))

    return findings


# ---------------------------------------------------------------------------
# 3. CODE-TABLE vs DATA DRIFT
# ---------------------------------------------------------------------------

# find/plugins.py's discover_plugins() reads five harnesses' plugin state,
# each in its own on-disk format, by name — this list has to be kept in
# sync with that function by hand. Path is each harness's plugin root,
# relative to $HOME, matching the ``root`` field harness.json records for
# capabilities.plugins.
PLUGIN_HARNESSES_CODE_TABLE: tuple[tuple[str, Path], ...] = (
    ("claude", Path(".claude/plugins")),
    ("factory", Path(".factory/plugins")),
    ("codex", Path(".codex/plugins")),
    ("cursor", Path(".cursor/plugins")),
    ("grok", Path(".grok/plugins")),
)


def _diff_capability(
    registry: dict,
    capability: str,
    code_table: Sequence[tuple[str, Path]],
    table_desc: str,
) -> list[Finding]:
    """Compare a (name -> root relpath) code table against harness.json's
    capabilities.<capability> entries.

    Joins on root path where the registry has one, which is what turns a
    rename (droid's row now points at ~/.factory/... while the code table
    still says "factory") into a single "name mismatch" finding instead of
    two unrelated "missing" ones. Falls back to joining on name for
    unsupported registry entries, which normally have no root to join on.
    """
    findings: list[Finding] = []
    table_by_path = {f"~/{relpath.as_posix()}": name for name, relpath in code_table}
    table_names = {name for name, _ in code_table}
    matched_table_names: set[str] = set()

    for reg_name, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        cap = (entry.get("capabilities") or {}).get(capability)
        cap = cap if isinstance(cap, dict) else {}
        supported = bool(cap.get("supported"))
        root = cap.get("root")

        if supported and root:
            table_name = table_by_path.get(root)
            if table_name is None:
                findings.append(Finding(
                    "warn", "code-vs-data",
                    f"registry says {reg_name!r} supports {capability} at {root}, "
                    f"but {table_desc} has no entry for that path",
                ))
            else:
                matched_table_names.add(table_name)
                if table_name != reg_name:
                    findings.append(Finding(
                        "warn", "code-vs-data",
                        f"name mismatch: registry calls it {reg_name!r}, {table_desc} "
                        f"calls the harness at the same path ({root}) {table_name!r}",
                    ))
        elif supported and not root:
            findings.append(Finding(
                "warn", "code-vs-data",
                f"registry says {reg_name!r} supports {capability} but records no root path",
            ))
        elif not supported and reg_name in table_names and reg_name not in matched_table_names:
            findings.append(Finding(
                "warn", "code-vs-data",
                f"{table_desc} lists {reg_name!r} as {capability}-capable, "
                f"but the registry says it doesn't support {capability}",
            ))

    for name in sorted(table_names - matched_table_names):
        if name in registry:
            continue  # already covered by the not-supported branch above
        findings.append(Finding(
            "warn", "code-vs-data",
            f"{table_desc} lists {name!r} as {capability}-capable, "
            f"but there is no such harness in the registry",
        ))

    return findings


def check_code_vs_data(
    registry: dict,
    *,
    skill_roots: Sequence[tuple[str, Path]] = (),
    plugin_roots: Sequence[tuple[str, Path]] = PLUGIN_HARNESSES_CODE_TABLE,
) -> list[Finding]:
    """Compare code-side "what a harness supports" tables against harness.json.

    ``skill_roots`` defaults to nothing (callers pass skills/layout.py's
    HARNESS_ROOTS); ``plugin_roots`` defaults to the hardcoded
    PLUGIN_HARNESSES_CODE_TABLE above, but can be overridden for tests.
    """
    findings = _diff_capability(registry, "skills", skill_roots, "skills/layout.py's HARNESS_ROOTS")
    findings += _diff_capability(registry, "plugins", plugin_roots, "find/plugins.py's plugin-capable harnesses")
    return findings


def _real_skill_roots() -> Sequence[tuple[str, Path]]:
    from quiver.skills.layout import HARNESS_ROOTS, SHARED_LABEL

    # SHARED_LABEL isn't a harness — it's the cross-harness shared skills
    # dir — so it has no capabilities.skills entry to join against.
    return [(label, relpath) for label, relpath in HARNESS_ROOTS if label != SHARED_LABEL]


# ---------------------------------------------------------------------------
# 4. DANGLING SYMLINKS
# ---------------------------------------------------------------------------


def check_dangling_symlinks(directories: Sequence[Path]) -> list[Finding]:
    """Top-level-only scan of each directory for symlinks whose target is gone.

    Not recursive on purpose: this is meant to catch a stray dev-only
    symlink left behind at a directory's top level (the repo root, or
    ~/.quiver), not to walk every skill/plugin tree looking for broken
    internal links.
    """
    findings: list[Finding] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink() and not entry.exists():
                findings.append(Finding(
                    "warn", "symlinks",
                    f"{entry} is a broken symlink (target does not exist)",
                ))
    return findings


def _real_symlink_dirs(repo_root: Path, home: Path) -> list[Path]:
    from quiver.paths import skills_dir_for

    return [repo_root, home / ".quiver", skills_dir_for(home)]


def _default_repo_root() -> Path:
    # src/quiver/harness/drift.py -> .../harness -> .../quiver (src) -> repo root
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Wiring: real data in, one list of findings out.
# ---------------------------------------------------------------------------


def run_drift_checks(*, home: Path | None = None, repo_root: Path | None = None) -> list[Finding]:
    from quiver.harness.registry import load_registry

    home = home or Path.home()
    repo_root = repo_root or _default_repo_root()
    registry = load_registry()

    findings: list[Finding] = []
    findings += check_help_vs_dispatch(_real_help_topics(), _real_commands())
    findings += check_registry_schema(registry)
    findings += check_code_vs_data(registry, skill_roots=_real_skill_roots())
    findings += check_dangling_symlinks(_real_symlink_dirs(repo_root, home))
    return findings
