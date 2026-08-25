"""Drift checks for ``swe doctor``.

``swe doctor`` used to diagnose one thing: Node/PATH mismatches that hide a
globally installed harness. This module adds a second, unrelated kind of
health check — places where two parts of quiver that are supposed to agree
have quietly drifted apart:

  * every ``help_text.py`` topic should have a matching command, and vice
    versa (``HELP vs DISPATCH``)
  * every ``~/.quiver/config/harness.json`` entry should have a sane shape
    (``REGISTRY SCHEMA``)
  * the fallback tables hardcoded in ``skills/layout.py`` and
    ``find/plugins.py`` should still *join* with the registry
    (``CODE-TABLE vs DATA``). Since capabilities-first landed, the registry
    is the source of truth and the tables only cover machines with no
    registry data — so a registry entry the tables lack is healthy, and a
    table entry the registry overrides is the design working. What still
    counts as drift: the same path under two different names (the join
    breaks), a table naming a harness the registry has never heard of, and
    a supported capability recorded with no root.
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


def check_subcommand_help(
    help_keys: set[str], command_keys: set[str], domain: str,
    *, whitelist: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Same idea as help-vs-dispatch, one level down.

    The top-level check compares help topics against ``swe``'s dispatch, but
    a domain's own help dict can drift against that domain's own dispatch
    just as silently — ``swe help mcp`` once documented four subcommands
    (add/remove/export/import) that had been deleted from ``mcp/cli.py``'s
    COMMANDS years of edits earlier. Dict-to-dict, so it survives restyles
    of how the help is printed.
    """
    findings: list[Finding] = []
    for key in sorted(help_keys - command_keys - whitelist):
        findings.append(Finding(
            "warn", "help",
            f"swe {domain}: help documents subcommand {key!r} but its COMMANDS "
            f"dispatch has no such entry",
        ))
    for key in sorted(command_keys - help_keys - whitelist):
        findings.append(Finding(
            "warn", "help",
            f"swe {domain}: subcommand {key!r} is dispatchable but has no entry "
            f"in the domain help dict",
        ))
    return findings


def _real_mcp_help_and_commands() -> tuple[set[str], set[str]]:
    from quiver.mcp.cli import COMMANDS as MCP_COMMANDS
    from quiver.mcp.cli import MCP_HELP

    return set(MCP_HELP), set(MCP_COMMANDS)


def check_prose_mentions(
    text: str, domain: str, command_keys: set[str],
    *, whitelist: frozenset[str] = frozenset({"help"}),
) -> list[Finding]:
    """Every ``swe <domain> <sub>`` the prose mentions must be dispatchable.

    The dict check above can't see this class: ``swe help mcp`` documented
    add/remove/export/import for months after they were deleted, because
    those lines lived in help_text.py's topic body, not in any dict.
    Deliberately narrow — a two-word command mention is unambiguous to
    match, whole sentences are not.
    """
    mentioned = set(re.findall(rf"swe {re.escape(domain)} ([a-z][a-z-]*)", text))
    findings: list[Finding] = []
    for sub in sorted(mentioned - command_keys - whitelist):
        findings.append(Finding(
            "warn", "help",
            f"help prose mentions 'swe {domain} {sub}' but {domain}'s COMMANDS "
            f"dispatch has no {sub!r}",
        ))
    return findings


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

# Mirror of find/plugins.py's PLUGIN_FALLBACK — the pre-capabilities table
# that only fires for a harness the registry has never heard of. Kept in
# sync by hand; keyed by registry names (droid, not its ~/.factory home),
# which is exactly what the name-mismatch check below enforces.
PLUGIN_HARNESSES_CODE_TABLE: tuple[tuple[str, Path], ...] = (
    ("claude", Path(".claude/plugins")),
    ("droid", Path(".factory/plugins")),
    ("codex", Path(".codex/plugins")),
    ("cursor", Path(".cursor/plugins")),
    ("grok", Path(".grok/marketplace-cache")),
)


def _diff_capability(
    registry: dict,
    capability: str,
    code_table: Sequence[tuple[str, Path]],
    table_desc: str,
) -> list[Finding]:
    """Check that a (name -> root relpath) fallback table still *joins* with
    harness.json's capabilities.<capability> entries.

    Not a set comparison: since capabilities-first, the registry is allowed
    to know more than the table (that is capabilities doing their job), and
    the table is allowed to claim things the registry overrides (that is
    the fallback being overridden as designed). Neither warns. What warns
    is anything that breaks the join itself: the same root path under two
    different names, a supported capability with no root to join on, or a
    table entry naming a harness the registry has never heard of.
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
            if table_name is not None:
                matched_table_names.add(table_name)
                if table_name != reg_name:
                    findings.append(Finding(
                        "warn", "code-vs-data",
                        f"name mismatch: registry calls it {reg_name!r}, {table_desc} "
                        f"calls the harness at the same path ({root}) {table_name!r}",
                    ))
            # No table entry for this root: fine — capabilities extend the
            # fallback, they don't have to mirror it.
        elif supported and not root:
            findings.append(Finding(
                "warn", "code-vs-data",
                f"registry says {reg_name!r} supports {capability} but records no root path",
            ))

    for name in sorted(table_names - matched_table_names):
        if name in registry:
            # The registry knows this harness; whether it agrees with the
            # table no longer matters at runtime, capabilities win.
            continue
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
    mcp_help, mcp_commands = _real_mcp_help_and_commands()
    findings += check_subcommand_help(
        mcp_help, mcp_commands, "mcp", whitelist=frozenset({"help"}),
    )
    help_text_path = Path(__file__).resolve().parent.parent / "help_text.py"
    try:
        help_text = help_text_path.read_text(encoding="utf-8")
    except OSError:
        help_text = ""
    findings += check_prose_mentions(help_text, "mcp", mcp_commands)
    findings += check_registry_schema(registry)
    findings += check_code_vs_data(registry, skill_roots=_real_skill_roots())
    findings += check_dangling_symlinks(_real_symlink_dirs(repo_root, home))
    return findings
