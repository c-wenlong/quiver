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
  * hook declarations, link states and the files on disk should agree:
    a settings file naming a script that exists nowhere, a hub script
    linked but never declared, and a hub script never linked in are all
    drift (``swe hooks check`` prints the same findings)

Every check is read-only and cheap — no network, no subprocesses, a handful
of stats at most — so it is safe to run on every ``swe doctor`` invocation
rather than gating it behind a flag.

Each check is exposed both as a pure function (``check_*``, takes plain data
in, returns findings, no I/O) for unit testing, and wired up with real data
by ``run_drift_checks()``, which is what ``cmd_doctor`` calls.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from quiver.paths import expand_user


@dataclass(frozen=True)
class Finding:
    severity: str  # "warn" | "error"
    area: str
    message: str
    # The fields DoctorFinding understands, filled by checks that know a
    # fix: ``source`` names the file the finding is about, ``remedies``
    # its ways out (same dict shape as DoctorFinding's), ``action`` a fix
    # swe can run itself.
    fix: str | None = None
    harness: str | None = None
    source: str | None = None
    action: str | None = None
    remedies: tuple | None = None


# ---------------------------------------------------------------------------
# 1. HELP vs DISPATCH
# ---------------------------------------------------------------------------

# Top-level commands that are intentionally not full help topics: aliases of
# a command that does have a topic (``ls`` -> ``list``, etc.), and the hidden
# shell-completion entry point. Keep this in sync by hand if COMMANDS grows a
# new alias-only entry.
NO_TOPIC_WHITELIST = frozenset({
    "__complete", "ls", "rm", "run", "sk", "hs", "pv", "-h", "--help", "help",
    "-v", "-V", "--version",
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


def check_completion_drift(
    primary: list[str],
    flag_keys: set[str],
    subcommand_tables: dict[str, list[str]],
    nested_subcommands: dict[tuple[str, str], list[str]],
    commands: dict,
    *,
    whitelist: frozenset[str] = NO_TOPIC_WHITELIST,
) -> list[Finding]:
    """completion.py's hand-maintained tables vs cli.py's dispatch.

    The tables drifted twice already: ``harness`` listed twice while
    ``find``/``discover`` were absent, and ``list`` offered no
    ``--usage``/``--links`` for months. ``primary`` must be exactly the
    dispatchable, non-alias commands; flag/subcommand table keys name
    either a command or ``"<command> <sub>"`` (or a third word for
    ``report followup`` style keys), and each named sub must exist in the
    domain's subcommand table.

    Scope: this checks that table *keys* point at real commands and real
    subcommands. It cannot prove each listed flag is accepted by the
    parser — that would need flag tables generated from the parsers, not
    checked after the fact.
    """
    findings: list[Finding] = []
    seen: set[str] = set()
    for name in primary:
        if name in seen:
            findings.append(Finding(
                "warn", "help",
                f"completion lists primary command {name!r} twice",
            ))
        seen.add(name)
        if name not in commands:
            findings.append(Finding(
                "warn", "help",
                f"completion offers {name!r} but cli.py's COMMANDS has no such entry",
            ))
    for name in sorted(set(commands) - seen - whitelist):
        findings.append(Finding(
            "warn", "help",
            f"command {name!r} is dispatchable but missing from completion's "
            f"primary list",
        ))
    for key in sorted(flag_keys | set(subcommand_tables)):
        words = key.split()
        if words[0] not in commands:
            findings.append(Finding(
                "warn", "help",
                f"completion table key {key!r} starts with {words[0]!r}, which is "
                f"not a dispatchable command",
            ))
            continue
        # "cmd sub" and "cmd sub subsub" keys must name real subcommands
        # when the domain exposes a table to check against. Domains with
        # no subcommand table (e.g. `list edit`) can't be verified here.
        subs = set(subcommand_tables.get(words[0]) or ())
        if len(words) > 1 and subs and words[1] not in subs:
            findings.append(Finding(
                "warn", "help",
                f"completion flag table {key!r} names subcommand "
                f"{words[1]!r}, which swe {words[0]} does not offer",
            ))
        elif len(words) > 2:
            deep = set(nested_subcommands.get((words[0], words[1])) or ())
            if deep and words[2] not in deep:
                findings.append(Finding(
                    "warn", "help",
                    f"completion flag table {key!r} names action "
                    f"{words[2]!r}, which swe {words[0]} {words[1]} does not offer",
                ))
    return findings


def _real_completion_tables() -> tuple[
    list[str], set[str], dict[str, list[str]], dict[tuple[str, str], list[str]]
]:
    import quiver.completion as comp

    return (
        [name for name, _ in comp._PRIMARY_COMMANDS],
        set(comp._COMMAND_FLAGS),
        {cmd: [sub for sub, _ in subs] for cmd, subs in comp._SUBCOMMANDS.items()},
        {
            pair: [sub for sub, _ in subs]
            for pair, subs in comp._NESTED_SUBCOMMANDS.items()
        },
    )


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
            # expand_user, not Path.expanduser: a ``~missing-user`` root
            # raises RuntimeError there, and a drift checker must report
            # bad registry data rather than die on it.
            if not expand_user(root).exists():
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
# sync by hand; keyed by registry names (droid, not its ~/.factory home;
# devin's store lives under ~/.local/share, not ~/.devin), which is exactly
# what the name-mismatch check below enforces.
PLUGIN_HARNESSES_CODE_TABLE: tuple[tuple[str, Path], ...] = (
    ("claude", Path(".claude/plugins")),
    ("droid", Path(".factory/plugins")),
    ("codex", Path(".codex/plugins")),
    ("cursor", Path(".cursor/plugins")),
    ("grok", Path(".grok/marketplace-cache")),
    ("devin", Path(".local/share/devin/cli/plugins")),
)


def _diff_capability(
    registry: dict,
    capability: str,
    code_table: Sequence[tuple[str, Path]],
    table_desc: str,
    home: Path | None = None,
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

    With ``home``, that last warning also needs the table's root to exist on
    this machine. A fallback row for a harness that is neither registered nor
    installed is the table covering someone else's machine, and removing a
    harness you stopped using should not leave ``swe doctor`` complaining.
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
        relpath = dict(code_table)[name]
        if home is not None and not (home / relpath).exists():
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
    hook_roots: Sequence[tuple[str, Path]] = (),
    home: Path | None = None,
) -> list[Finding]:
    """Compare code-side "what a harness supports" tables against harness.json.

    ``skill_roots`` defaults to nothing (callers pass skills/layout.py's
    HARNESS_ROOTS); ``plugin_roots`` defaults to the hardcoded
    PLUGIN_HARNESSES_CODE_TABLE above, but can be overridden for tests.
    ``hook_roots`` defaults to nothing (callers pass init/hooks.py's
    HOOK_FALLBACK). ``home`` silences a table row for a harness this
    machine has neither registered nor installed.
    """
    findings = _diff_capability(registry, "skills", skill_roots, "skills/layout.py's HARNESS_ROOTS", home)
    findings += _diff_capability(registry, "plugins", plugin_roots, "find/plugins.py's plugin-capable harnesses", home)
    findings += _diff_capability(registry, "hooks", hook_roots, "init/hooks.py's HOOK_FALLBACK", home)
    return findings


def _real_skill_roots() -> Sequence[tuple[str, Path]]:
    from quiver.skills.layout import HARNESS_ROOTS, SHARED_LABEL

    # SHARED_LABEL isn't a harness — it's the cross-harness shared skills
    # dir — so it has no capabilities.skills entry to join against.
    return [(label, relpath) for label, relpath in HARNESS_ROOTS if label != SHARED_LABEL]


def _real_hook_roots() -> Sequence[tuple[str, Path]]:
    from quiver.init.hooks import HOOK_FALLBACK

    return list(HOOK_FALLBACK.items())


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
        try:
            # is_dir() propagates EACCES when a parent lacks search
            # permission, so it belongs inside the guard too.
            if not directory.is_dir():
                continue
            entries = sorted(directory.iterdir())
        except OSError as exc:
            # An unreadable directory is itself worth reporting; dying on
            # the PermissionError would hide every check that runs after.
            findings.append(Finding(
                "warn", "symlinks",
                f"{directory} cannot be scanned for broken symlinks: {exc.strerror or exc}",
            ))
            continue
        for entry in entries:
            try:
                if entry.is_symlink() and not entry.exists():
                    findings.append(Finding(
                        "warn", "symlinks",
                        f"{entry} is a broken symlink (target does not exist)",
                    ))
            except OSError:
                # A directory we can list but not stat through (read
                # without search permission) yields entries that cannot
                # be classified; skip them like a non-symlink.
                continue
    return findings


def _real_symlink_dirs(repo_root: Path, home: Path) -> list[Path]:
    from quiver.paths import skills_dir_for

    return [repo_root, home / ".quiver", skills_dir_for(home)]


def _default_repo_root() -> Path:
    # src/quiver/harness/drift.py -> .../harness -> .../quiver (src) -> repo root
    return Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 5. HOOK DECLARATIONS vs LINKS vs DISK
# ---------------------------------------------------------------------------


def check_hook_drift(home: Path) -> list[Finding]:
    """``swe hooks check``'s findings, re-wrapped for doctor.

    The checks live in ``hooks/inventory.py`` (declared-but-missing,
    linked-but-undeclared, unlinked); this adapts them to Finding and
    names each finding's source file and remedies. A hook inventory that
    cannot run at all — a corrupt registry or linkignore — is itself one
    warning, never a crash for doctor.
    """
    from quiver.hooks.inventory import hook_findings

    try:
        # "managed" notes (a read-only or symlinked declaration file) are
        # reported by swe hooks check but are not drift a user can fix —
        # leaving them out keeps doctor's warning list actionable.
        return [_hook_finding(d, home)
                for d in hook_findings(home) if d.kind != "managed"]
    except Exception as exc:
        return [Finding("warn", "hooks",
                        f"hook drift check could not run: {exc}")]


def _agent(prompt: str, detail: str) -> dict:
    return {"kind": "agent", "title": "Fix with agent",
            "detail": detail, "prompt": prompt}


def _hook_finding(d, home: Path) -> Finding:
    """One HookDrift as a doctor Finding, with its source and remedies."""
    from quiver.console import tilde

    files = [tilde(p, home) for p in d.settings]
    if d.kind == "declared-missing":
        joined = ", ".join(files)
        # A settings file resolved into /nix/store is generated: the edit
        # belongs in the Nix config, never in the file itself.
        nix = any(os.path.realpath(p).startswith("/nix/store/")
                  for p in d.settings)
        if nix:
            prompt = (f"A hook declared in {joined} names {d.script}, but "
                      "no such script exists on this machine. The settings "
                      "file resolves into /nix/store, so do NOT edit it: "
                      "find the Nix or home-manager config that generates "
                      "it and remove the hook entry there (or restore the "
                      "script). Verify with `swe doctor`: the drift.hooks "
                      "finding should be gone.")
            fix = (f"{joined} is managed by Nix: drop the declaration "
                   f"in your Nix config, or restore {d.script}")
            steps_title = f"Drop the declaration in your Nix config"
        else:
            prompt = (f"A hook in {joined} declares {d.script}, but no such "
                      f"script exists on this machine. Remove that hook "
                      f"entry, backing up the file first, or restore the "
                      f"script if it should exist. Verify with `swe "
                      f"doctor`: the drift.hooks finding should be gone. "
                      f"Touch nothing else in the file.")
            fix = f"remove the declaration from {joined}, or restore {d.script}"
            steps_title = f"Remove the declaration from {joined}"
        from quiver.hooks.contracts import contract_for
        from quiver.hooks.settings import declaration_managed_elsewhere
        # No Run on a file owned by a generator (symlinked, read-only) or
        # on a harness like muse whose file only points at the real one.
        managed = (contract_for({}, d.harness, home).managed_hooks_key
                   or any(declaration_managed_elsewhere(p)
                          for p in d.settings))
        remedies: list = []
        if not nix and not managed and d.settings:
            remedies.append({
                "kind": "action", "action": "undeclare",
                "title": "Drop the declaration",
                "detail": "Removes the hook entries naming it from "
                          f"{joined}, with each file backed up to "
                          "~/.quiver/backups first.",
                "script": d.script,
                "settings": [str(p) for p in d.settings],
            })
        remedies += [
            _agent(prompt,
                   "Restores the script or removes the hook, then "
                   "verifies with swe doctor."),
            {"kind": "steps",
             "title": steps_title,
             "detail": "Delete its hook entry under each event that "
                       "declares it."},
        ]
        return Finding(
            "warn", "hooks", d.message,
            fix=fix,
            harness=d.harness,
            source=f"declared in {joined}" if files else None,
            remedies=tuple(remedies))
    if d.kind == "undeclared":
        file = files[0] if files else ""
        nix = file and any(os.path.realpath(p).startswith("/nix/store/")
                           for p in d.settings[:1])
        if nix:
            prompt = (f"{d.message}. The settings file {file} resolves "
                      "into /nix/store, so do NOT edit it: add the hook "
                      "entry in the Nix or home-manager config that "
                      "generates it, or remove the link if the hook "
                      "should not fire. Verify with `swe doctor`: the "
                      "drift.hooks finding should be gone.")
            fix = (f"{file} is managed by Nix: declare it in your Nix "
                   "config, or delete the link")
            steps_title = "Declare it in your Nix config"
        else:
            prompt = (f"{d.message}. Add a hook entry for it"
                      + (f" to {file}" if file else " to the harness's "
                                                   "settings file")
                      + ", or remove the link if the hook should not "
                        "fire. Verify with `swe doctor`: the drift.hooks "
                        "finding should be gone.")
            fix = ((f"declare it in {file}" if file
                    else "declare it in the harness's settings file")
                   + ", or delete the link")
            steps_title = (f"Declare it in {file}" if file
                           else "Declare it in the harness's settings "
                                "file")
        return Finding(
            "warn", "hooks", d.message,
            fix=fix,
            harness=d.harness,
            source=f"would be declared in {file}" if file else None,
            remedies=(
                _agent(prompt,
                       "Adds the declaration or removes the link, then "
                       "verifies with swe doctor."),
                {"kind": "steps",
                 "title": steps_title,
                 "detail": "swe hooks new writes the stanza to paste."},
            ))
    if d.kind == "unlinked":
        return Finding(
            "warn", "hooks", d.message,
            fix="swe init", harness=d.harness, action="init",
            remedies=(
                {"kind": "action", "action": "init",
                 "title": "Link it with swe init",
                 "detail": "A real file in the way is left alone and "
                          "listed for Replace and back up."},
            ))
    return Finding("warn", "hooks", d.message, harness=d.harness)


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
        mcp_help, mcp_commands, "mcp", whitelist=frozenset({"help", "ls"}),
    )
    help_text_path = Path(__file__).resolve().parent.parent / "help_text.py"
    try:
        help_text = help_text_path.read_text(encoding="utf-8")
    except OSError:
        help_text = ""
    findings += check_prose_mentions(help_text, "mcp", mcp_commands)
    from quiver.instructions.commands import SUBCOMMANDS as instruction_commands
    from quiver.completion import _SUBCOMMANDS

    instruction_keys = set(instruction_commands)
    instruction_completions = {name for name, _ in _SUBCOMMANDS["instructions"]}
    findings += check_subcommand_help(
        instruction_completions, instruction_keys, "instructions",
    )
    findings += check_prose_mentions(help_text, "instructions", instruction_keys)
    primary, flag_keys, sub_tables, nested_subs = _real_completion_tables()
    findings += check_completion_drift(
        primary, flag_keys, sub_tables, nested_subs, _real_commands()
    )
    findings += check_registry_schema(registry)
    findings += check_code_vs_data(
        registry, skill_roots=_real_skill_roots(), hook_roots=_real_hook_roots(), home=home,
    )
    findings += check_dangling_symlinks(_real_symlink_dirs(repo_root, home))
    findings += check_hook_drift(home)
    return findings
