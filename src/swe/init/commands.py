"""`swe init` — build the ~/.swe layout and link every harness to it."""

from __future__ import annotations

import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from swe.console import c
from swe.flags import expand_value_flags
from swe.json_output import emit, take_json_flag
from swe.multiselect import Choice, _supported, multiselect
from swe.paths import backup_tree, hub_dirs_for
from swe.prompt import read_line
from swe.init.accounts import (
    AccountRow,
    AccountSyncUnavailable,
    sync_accounts,
    sync_flags,
)
from swe.init.hooks import load_registry, plan_hooks
from swe.init.manage import (
    _valid_filename,
    has_harness_evidence,
    hold_unreviewed,
    home_relative,
    legacy_registry_pending,
    new_harnesses,
    register,
)
from swe.init.migrate import apply_migration, move_secrets, plan_migration, write_gitignore
from swe.init.models import LinkRow
from swe.init.layout import (
    LinkIgnoreError,
    LinkStatus,
    SEED_AGENTS_MD,
    SEED_LINKIGNORE,
    agents_file,
    aliases_of,
    backups_dir,
    linkignore_file,
    load_linkignore,
    plan,
    swe_dir,
    registry_name,
    skills_dir,
)

STATE_COLOR = {
    "linked": "green",
    "create": "cyan",
    "relink": "yellow",
    "absorb": "cyan",
    "conflict": "red",
    "keep": "yellow",
    "protected": "yellow",
    "blocked": "red",
    "skipped": "dim",
    "ignored": "dim",
    "unlink": "yellow",
    "unlinked": "yellow",
}


def _backup(path: Path, home: Path) -> Path:
    """Copy a real file or directory aside before it is replaced."""
    return backup_tree(path, home)


def _safe_to_replace(path: Path, home: Path) -> bool:
    """init deletes only real content inside $HOME that is not quiver's own.

    A registry-declared target can point anywhere — a capability of
    ``~/.swe/config/harness.json`` resolving into the data plane, or one
    slipping out of $HOME — and --force must never carry that through:
    deleting the registry mid-run or reaching outside the sandbox is a
    bug the flag was never meant to authorize.
    """
    try:
        resolved = path.resolve()
        return (
            resolved.is_relative_to(home.resolve())
            and not resolved.is_relative_to(swe_dir(home).resolve())
        )
    except OSError:
        return False


def _apply(status: LinkStatus, canonical: Path, home: Path, force: bool) -> str:
    """Carry out one status. Returns a short result word for the report."""
    if status.state in ("linked", "skipped", "ignored"):
        return status.state

    # A directory whose skills exist nowhere else is never replaced on a plain
    # run. Absorbing it would hide the only copy behind the shared tree, and
    # the backup would be the sole survivor. Reported instead.
    if status.state == "keep" and not force:
        return "protected"
    if status.state == "conflict" and not force:
        return "blocked"

    path = status.path
    if status.state in ("conflict", "absorb", "keep") and not path.is_symlink():
        if path.exists():
            if not _safe_to_replace(path, home):
                return "blocked"
            _backup(path, home)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    elif path.is_symlink():
        path.unlink()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(canonical)
    return "linked"


# Results that count as linked in the total: already a link, or a check-mode
# plan that would become one. would-conflict and would-keep are not links.
_LINKED_RESULTS = frozenset({"linked", "would-create", "would-relink", "would-absorb"})

# Order the per-section tallies read in: settled first, then work to do.
_TALLY_ORDER = (
    "linked", "create", "relink", "absorb", "keep", "protected",
    "conflict", "blocked", "unlink", "unlinked", "skipped", "ignored",
)


def _colour_for(result: str) -> str:
    """Colour of a result word; check mode's would-* forms share their state's."""
    return STATE_COLOR.get(result.removeprefix("would-"), "dim")


def _print_section(title: str, rows: list[tuple[LinkStatus, str]], home: Path) -> None:
    print(f"\n  {c('bold', title)}")
    for status, result in rows:
        colour = _colour_for(result)
        note = status.detail
        if result == "blocked":
            colour, note = "red", status.detail
        print(
            f"    {c(colour, result.ljust(8))} {home_relative(status.path, home).ljust(30)}"
            f" {c('dim', note)}"
        )


def _tally(rows: list[tuple[LinkStatus, str]]) -> str:
    """One line of counts per result word: `57 linked, 1 protected`."""
    counts = Counter(result for _, result in rows)

    def rank(result: str) -> int:
        state = result.removeprefix("would-")
        return _TALLY_ORDER.index(state) if state in _TALLY_ORDER else len(_TALLY_ORDER)

    parts = [
        c(_colour_for(result), f"{counts[result]} {result}")
        for result in sorted(counts, key=lambda r: (rank(r), r))
    ]
    return ", ".join(parts) if parts else c("dim", "nothing to link")


def _print_summary(sections: list[tuple[str, list[tuple[LinkStatus, str]]]]) -> None:
    """The default view: one tally per section instead of one row per path."""
    print()
    width = max(len(title) for title, _ in sections)
    for title, rows in sections:
        print(f"  {c('bold', title.ljust(width))}  {_tally(rows)}")


def _print_paths(title: str, rows: list[tuple[LinkStatus, str]], home: Path) -> None:
    print(f"  {c('yellow', title)}")
    for status, _ in rows:
        print(f"    {home_relative(status.path, home).ljust(30)} {c('dim', status.detail)}")


def _ensure_scaffold(home: Path, check_only: bool) -> list[str]:
    """Create the .swe directory, seed files and hub folders if absent.

    Run on an existing ~/.swe this is the repair: it adds whichever hub
    folders are missing and never touches a file that is already there.
    """
    import os

    notes = []
    root, agents = swe_dir(home), agents_file(home)
    dirs = [(root, "~/.swe")]
    dirs += [(path, f"{path.name}/") for path in hub_dirs_for(home)]
    dirs.append((backups_dir(home), "backups/"))
    # lexists, not exists: a dangling symlink is invisible to exists() but a
    # mkdir/write through it lands wherever the link points — possibly far
    # outside ~/.swe. A scaffold path that is any symlink is left alone.
    for path, label in dirs:
        if not os.path.lexists(path):
            notes.append(f"create {label}")
            if not check_only:
                path.mkdir(parents=True, exist_ok=True)
        elif path.is_symlink() and not path.is_dir():
            notes.append(f"{label} is a dangling symlink — left alone")
    if not os.path.lexists(agents):
        notes.append("seed AGENTS.md")
        if not check_only:
            agents.write_text(SEED_AGENTS_MD, encoding="utf-8")
    elif agents.is_symlink() and not agents.is_file():
        notes.append("AGENTS.md is a dangling symlink — left alone")
    ignore = linkignore_file(home)
    if not os.path.lexists(ignore):
        notes.append("seed .linkignore")
        if not check_only:
            ignore.write_text(SEED_LINKIGNORE, encoding="utf-8")
    return notes


def _instruction_filename(label: str) -> str | None:
    """Ask which filename one managed harness reads; None means no link."""
    value = ""
    for attempt in range(2):  # a bad name gets one re-ask, then the default
        if attempt:
            print(c("dim", "  just a filename such as AGENTS.md"))
        value = read_line(f"  {label} [AGENTS.md]: ").strip()
        if not value or _valid_filename(value):
            break
    if not _valid_filename(value):
        return "AGENTS.md"
    if value.lower() in ("skip", "none", "-"):
        return None
    return value


def _pick_new_harnesses(
    new: list[LinkStatus], home: Path, yes: bool, evidence_only: bool = False,
    ask: bool = True,
) -> tuple[dict[str, tuple[Path, str | None]], dict[str, Path]] | None:
    """Split ``new`` into (managed, declined); None when cancelled.

    ``--yes`` registers everything with the conventional AGENTS.md: the
    caller asked for that by name. ``evidence_only`` registers just the
    roots with harness evidence, the ones a terminal picker would pre-tick,
    and decides nothing about the rest. A non-terminal run decides nothing,
    since a skills/ dir alone does not make an app a harness and nobody is
    there to say so; it returns two empty dicts and the roots wait,
    unlinked, for an interactive run. In a terminal the multiselect
    decides, pre-ticking only roots with harness evidence. A cancelled
    picker leaves the registry alone entirely. ``ask=False`` is a run
    with no terminal however stdin looks (the web app's Doctor page, run
    on the hub's own thread), and says nothing about it.
    """
    if yes:
        return {s.label: (s.path, "AGENTS.md") for s in new}, {}
    if evidence_only:
        return {
            s.label: (s.path, "AGENTS.md")
            for s in new if has_harness_evidence(s.label)
        }, {}
    if not ask:
        return {}, {}
    if not _supported():
        names = ", ".join(s.label for s in new)
        print(c("dim", f"  not a terminal: left {len(new)} new skills "
                       f"folder(s) alone ({names})"))
        print(c("dim", "  run swe init in a terminal to choose, "
                       "or swe init --yes to take them all"))
        return {}, {}

    def about(status: LinkStatus) -> str:
        where = home_relative(status.path, home)
        if has_harness_evidence(status.label):
            return where
        return f"{where}  (no matching command or catalog entry)"

    choices = [Choice(key=s.label, label=s.label, about=about(s)) for s in new]
    chosen = multiselect(
        choices,
        selected=[s.label for s in new if has_harness_evidence(s.label)],
        title="New skills folders found, tick the ones that are coding harnesses",
    )
    if chosen is None:
        print(c("dim", "  cancelled, nothing registered"))
        return None

    print(c("dim", "  Instruction file per harness: Enter for AGENTS.md, "
                  "type another name such as CLAUDE.md, or skip"))
    by_label = {s.label: s.path for s in new}
    managed: dict[str, tuple[Path, str | None]] = {}
    for i, label in enumerate(chosen):
        try:
            filename = _instruction_filename(label)
        except EOFError:
            # No more input coming: this and every remaining pick default.
            for rest in chosen[i:]:
                managed[rest] = (by_label[rest], "AGENTS.md")
            break
        managed[label] = (by_label[label], filename)
    declined = {label: path for label, path in by_label.items() if label not in chosen}
    return managed, declined


def _link_json(instructions: list[LinkStatus], skills: list[LinkStatus]) -> list[dict]:
    """One row per harness, joining its instruction file and skills root.

    Keyed by the registry name, so a row joins ``swe list --json`` on
    ``name``. A harness with no instruction file (or no skills root) carries
    ``null`` for that half rather than an invented state. When two roots
    share a name (``~/.agents`` and ``~/.config/agents``) the first in plan
    order is the one reported.
    """
    rows: dict[str, LinkRow] = {}
    for key, statuses in (("instructions", instructions), ("skills", skills)):
        for status in statuses:
            name = registry_name(status.label)
            row = rows.setdefault(name, LinkRow(name))
            if getattr(row, key) is None:
                setattr(row, key, {"path": str(status.path), "state": status.state})
    return [rows[name].to_json() for name in sorted(rows)]


@dataclass
class InitRun:
    """What one `swe init` pass planned or did, before any printing."""

    scaffold: list[str]
    instructions: list[LinkStatus]
    skills: list[LinkStatus]
    inst_rows: list[tuple[LinkStatus, str]]
    skill_rows: list[tuple[LinkStatus, str]]
    hook_rows: list[tuple[LinkStatus, str]]
    # Targets in Quiver's extra account homes (accounts.sync.*), kept apart
    # from the rows above: --force never applies to them, so nothing that
    # offers "replace the blocked paths" may count them.
    account_rows: list[AccountRow] = field(default_factory=list)
    # Why the account pass did not run (an unreadable file), if it didn't.
    account_notes: list[str] = field(default_factory=list)

    def rows_json(self) -> list[dict]:
        """Every path with its result, for `swe setup --apply --json`."""
        return [
            {
                "kind": kind,
                "harness": registry_name(status.label),
                "path": str(status.path),
                "result": result,
                "detail": status.detail,
            }
            for kind, rows in (("instructions", self.inst_rows),
                               ("skills", self.skill_rows),
                               ("hooks", self.hook_rows))
            for status, result in rows
        ]


def run_init(home: Path, *, check_only: bool = False, force: bool = False,
             migrate: bool = False, yes: bool = False,
             evidence_only: bool = False, quiet: bool = False,
             ask: bool = True) -> InitRun:
    """Build the layout and link every harness; the body of `swe init`.

    ``evidence_only`` registers only the new skills folders whose owner has
    a command on PATH or a catalog entry and leaves the rest waiting, which
    is what `swe setup --apply` wants with nobody there to ask. ``quiet``
    keeps it from printing, for callers that emit one JSON document.
    ``ask=False`` never opens the picker: new skills folders wait, as on a
    run without a terminal.
    Raises LinkIgnoreError when ~/.swe/.linkignore can't be read.
    """
    patterns = load_linkignore(home)

    scaffold = _ensure_scaffold(home, check_only)

    # An old ~/.swe/ is reported whenever it exists, so a machine that
    # has not migrated says so rather than silently running two roots.
    migration = plan_migration(home)
    if migration and not migration.empty:
        if migrate and not check_only:
            apply_migration(migration)
            scaffold.append(
                f"migrated {len(migration.moved)} entries from ~/.config/swe"
            )
        else:
            scaffold.append(
                f"~/.config/swe still has {len(migration.moved)} entries, "
                "run `swe init --migrate`"
            )
    if not check_only:
        write_gitignore(home)
    scaffold.extend(move_secrets(home, apply=not check_only))

    instructions, skills = plan(home, patterns)
    # An uninstalled harness the registry does not know is noise: quiver can
    # link it, but this machine never asked. A registered one still shows as
    # skipped, since that tells you something you set up is missing.
    registry = load_registry(home)

    # A skills root no registry entry claims is a harness quiver has never
    # been asked about: offer to manage it (register + link instructions) or
    # archive it so it is never asked again.
    new = new_harnesses(skills, registry)
    # Every new root starts held. Only a managed tick releases one to be
    # linked; a declined one is archived, which plan() already ignores.
    held = {s.label for s in new}
    if check_only:
        if new:
            scaffold.append(
                f"{len(new)} new harness(es) found: "
                f"{', '.join(s.label for s in new)}, swe init will ask"
            )
    elif new:
        if legacy_registry_pending(home):
            if not quiet:
                print(c("dim", "  harness.json not migrated yet, "
                              "run swe list once, then swe init again"))
        else:
            decided = _pick_new_harnesses(new, home, yes, evidence_only, ask)
            if decided is not None:
                managed, declined = decided
                held -= set(managed) | set(declined)
                if managed or declined:
                    registry = register(home, registry, managed, declined)
                    if not quiet:
                        print(c("green", f"  registered {len(managed)} managed, "
                                         f"{len(declined)} declined"))
                    # Re-plan so the new instruction targets and the freshly
                    # archived roots show up in this same run.
                    instructions, skills = plan(home, patterns, registry)
    hold_unreviewed(skills, held)

    registered = set(registry) | {
        alias
        for entry in registry.values()
        for alias in aliases_of(entry)
    }
    instructions = [
        s for s in instructions
        if not (s.state == "skipped" and registry_name(s.label) not in registered)
    ]
    # The shared tier deploys its wrappers into the hub before planning,
    # so this same run links the _generated dirs it just wrote. Check
    # mode only counts what a real run would write.
    from swe.hooks import shared as _shared_hooks

    if check_only:
        pending = _shared_hooks.pending_deploy(home, registry)
        if pending:
            scaffold.append(
                f"{pending} generated hook file(s) would be written")
    else:
        written, pruned = _shared_hooks.apply_deploy(home, registry)
        if written or pruned:
            scaffold.append(
                f"shared hooks: {len(written)} file(s) generated, "
                f"{len(pruned)} pruned")
    hooks = plan_hooks(home, patterns)

    if check_only:
        inst_rows = [(s, "would-" + s.state if s.changed else s.state) for s in instructions]
        skill_rows = [(s, "would-" + s.state if s.changed else s.state) for s in skills]
        hook_rows = [(s, "would-" + s.state if s.changed else s.state) for s in hooks]
    else:
        inst_rows = [
            (s, _apply(s, agents_file(home), home, force)) for s in instructions
        ]
        skill_rows = [(s, _apply(s, skills_dir(home), home, force)) for s in skills]
        hook_rows = [(s, _apply(s, s.source, home, force)) for s in hooks]
    account_rows, account_notes = sync_accounts(
        home, patterns, registry, check_only=check_only)
    scaffold.extend(account_notes)
    return InitRun(scaffold, instructions, skills, inst_rows, skill_rows, hook_rows,
                   account_rows, account_notes)


def check_document(home: Path, run: InitRun | None = None) -> dict:
    """The ``swe init --check --json`` document: where the shared AGENTS.md
    is and each harness's link state. Writes nothing; ``run`` is a check
    already made, else one is made here without printing. Raises
    LinkIgnoreError when ~/.swe/.linkignore can't be read."""
    if run is None:
        run = run_init(home, check_only=True, quiet=True)
    try:
        flags = sync_flags(home)
    except AccountSyncUnavailable:
        flags = None
    return {
        "agents_file": str(agents_file(home)),
        "agents_file_exists": agents_file(home).exists(),
        "harnesses": _link_json(run.instructions, run.skills),
        "account_sync": flags,
        "accounts": [row.to_json() for row in run.account_rows],
        "account_notes": run.account_notes,
    }


# Larger than any instruction file anyone writes by hand; a guard against
# adopting a log or a binary by mistake.
_ADOPT_LIMIT = 1_000_000


def agents_file_customised(home: Path) -> bool:
    """True once ~/.swe/AGENTS.md says something other than the seed."""
    try:
        return agents_file(home).read_text(encoding="utf-8") != SEED_AGENTS_MD
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError):
        return True


def check_adoptable(home: Path, source: Path) -> str | None:
    """Why ``source`` can't start the shared AGENTS.md, or None if it can."""
    if agents_file_customised(home):
        return (f"{agents_file(home)} already has your own text, "
                "so nothing was adopted. Edit it instead.")
    if not source.is_file():
        return f"{source} is not a file."
    try:
        if source.stat().st_size > _ADOPT_LIMIT:
            return f"{source} is too large to be an instruction file."
        source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"Couldn't read {source}: {exc}"
    return None


def adopt_instructions(home: Path, source: Path) -> None:
    """Start the shared AGENTS.md from ``source``'s text.

    Only while AGENTS.md is missing or still the seed, which
    check_adoptable confirms. The source itself is left in place: the
    linking pass that follows sees it now matches the shared copy, backs
    it up and replaces it with a link (state ``absorb``).
    """
    reason = check_adoptable(home, source)
    if reason:
        raise ValueError(reason)
    target = agents_file(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _register_hub_marketplaces(home: Path) -> None:
    """After a --hub clone, hand the hub's marketplaces to each harness."""
    from swe.init.hub import register_marketplaces

    results = register_marketplaces(home)
    if not results:
        return
    print(f"  {c('bold', 'Plugin marketplaces')}")
    for harness, marketplace, error in results:
        if error is None:
            print(f"    {c('green', '✓')} {marketplace} registered in {harness}")
        else:
            print(f"    {c('yellow', '!')} {marketplace} not registered in {harness}: {error}")
    if any(error for *_, error in results):
        print(c("dim", "    swe find plugins lists what is left; --fix=<id> retries one."))
    print()


def _cmd_init_accounts(home: Path, check_only: bool, json_view: bool) -> int:
    """`swe init --accounts`: only the account-home pass, nothing else.

    What the app runs when a sync switch flips: it never touches the
    default harness homes, never prompts, and --json works for a real run
    too since there is nothing to ask. Exits 1 when the pass could not run.
    """
    out = sys.stderr if json_view else sys.stdout
    try:
        patterns = load_linkignore(home)
        registry = load_registry(home)
        flags = sync_flags(home)
    except (LinkIgnoreError, AccountSyncUnavailable, ValueError) as exc:
        print(str(exc), file=out)
        return 1
    rows, notes = sync_accounts(home, patterns, registry, check_only=check_only)
    if json_view:
        emit({"account_sync": flags, "accounts": [row.to_json() for row in rows],
              "notes": notes})
        return 1 if notes else 0
    for note in notes:
        print(c("red", f"  {note}"))
    pairs = [(row.status, row.result) for row in rows]
    if pairs:
        _print_section("Accounts", pairs, home)
        print()
    elif not notes:
        print(c("dim", "  No account sync switched on, nothing to do."))
    return 1 if notes else 0


def cmd_init(args) -> int:
    args = list(args or [])
    if args and args[0] in ("-h", "--help", "help"):
        print_init_help()
        return 0
    args, json_view = take_json_flag(args)
    try:
        args = expand_value_flags(args, {"--hub"})
    except ValueError as exc:
        print(str(exc), file=sys.stderr if json_view else sys.stdout)
        return 1
    hub_url = None
    if "--hub" in args:
        at = args.index("--hub")
        hub_url = args[at + 1]
        del args[at:at + 2]

    check_only = "--check" in args or "-n" in args
    force = "--force" in args
    migrate = "--migrate" in args
    full = "--full" in args
    yes = "--yes" in args
    accounts_only = "--accounts" in args

    known = ("--check", "-n", "--force", "--migrate", "--full", "--yes", "--accounts")
    unknown = [a for a in args if a not in known]
    if unknown:
        if json_view:
            print(f"Unknown option: {unknown[0]}", file=sys.stderr)
            return 1
        print(f"Unknown option: {unknown[0]}")
        print_init_help()
        return 1
    if accounts_only:
        if hub_url is not None or force or migrate or yes:
            print("--accounts only takes --check and --json",
                  file=sys.stderr if json_view else sys.stdout)
            return 1
        return _cmd_init_accounts(Path.home(), check_only, json_view)
    if json_view and not check_only:
        # A run that links things also prompts and prints progress. --json
        # promises one document and no writes, which only the preview keeps.
        print("--json only works with --check: swe init --check --json",
              file=sys.stderr)
        return 1

    home = Path.home()

    if hub_url is not None:
        if check_only:
            print("--hub clones, so it can't be combined with --check",
                  file=sys.stderr if json_view else sys.stdout)
            return 1
        from swe.init.hub import HubError, clone_hub, public_url

        print(f"\n  Cloning {public_url(hub_url.strip())} into ~/.swe…")
        try:
            clone_hub(home, hub_url)
        except HubError as exc:
            print(c("red", f"  {exc}"))
            return 1
        print(c("green", "  Cloned. Linking it into every harness."))

    # Read the ignore list before anything is written. An unreadable file
    # must stop the run: a plan built without it would let --force replace
    # the very paths the user listed to protect.
    try:
        result = run_init(home, check_only=check_only, force=force,
                          migrate=migrate, yes=yes)
    except LinkIgnoreError as exc:
        if json_view:
            print(str(exc), file=sys.stderr)
            return 1
        print(c("red", f"  {exc}"))
        print(c("dim", "  Fix or remove ~/.swe/.linkignore, then run swe init again."))
        return 1
    scaffold = result.scaffold
    instructions, skills = result.instructions, result.skills
    inst_rows, skill_rows, hook_rows = result.inst_rows, result.skill_rows, result.hook_rows
    acct_rows = [(row.status, row.result) for row in result.account_rows]

    if json_view:
        # Link state is data here, not a verdict: exit 0 even when a path
        # would be blocked, so the caller can tell "ran" from "failed".
        return emit(check_document(home, result))

    header = "Quiver layout" + (c("dim", "  (check only, nothing written)") if check_only else "")
    print(f"\n{c('bold', header)}")
    print(f"  {c('dim', 'root')}  {swe_dir(home)}")
    if scaffold:
        print(f"  {c('dim', 'scaffold')}  {', '.join(scaffold)}")

    if full:
        _print_section("Instructions", inst_rows, home)
        _print_section("Skills", skill_rows, home)
        _print_section("Hooks", hook_rows, home)
        if acct_rows:
            _print_section("Accounts", acct_rows, home)
    else:
        sections = [("Instructions", inst_rows), ("Skills", skill_rows), ("Hooks", hook_rows)]
        if acct_rows:
            sections.append(("Accounts", acct_rows))
        _print_summary(sections)

    everything = inst_rows + skill_rows + hook_rows
    blocked = [(s_, r) for s_, r in everything if r in ("blocked", "would-conflict")]
    # check mode renders an unchanged state verbatim, so "keep" arrives as-is.
    protected = [
        (s_, r) for s_, r in skill_rows if r in ("protected", "keep", "would-keep")
    ]
    linked = [r for _, r in everything if r in _LINKED_RESULTS]
    summary = (
        f"{len(linked)} linked, {len(blocked)} blocked, "
        f"edit {agents_file(home)} to change them all"
    )
    print(f"\n  {c('dim', summary)}\n")
    if protected:
        _print_paths("Left alone, these hold skills that exist nowhere else:", protected, home)
        print(f"  {c('dim', 'Move what you want to keep into ~/.swe/skills first, or --force.')}")
        print(f"  {c('dim', 'To leave one alone for good, list it in ~/.swe/.linkignore.')}\n")
    # The full view already paints every blocked row red; the summary only
    # has counts, so it names the paths the user has to do something about.
    if blocked and not full:
        _print_paths("Blocked, a real file or directory is in the way:", blocked, home)
        print()
    acct_conflicts = [(s_, r) for s_, r in acct_rows if r == "conflict"]
    if acct_conflicts:
        _print_paths("Not synced, the account already has its own:", acct_conflicts, home)
        print(f"  {c('dim', 'Move it aside, then run swe init again. --force never replaces these.')}\n")
    acct_blocked = [(s_, r) for s_, r in acct_rows if r == "blocked"]
    if acct_blocked:
        _print_paths("Not changed, the account's file could not be edited safely:",
                     acct_blocked, home)
        print()
    if not full:
        print(f"  {c('dim', 'swe init --full lists every path.')}\n")
    if hub_url is not None:
        _register_hub_marketplaces(home)
    if blocked:
        print(f"  {c('yellow', 'Re-run with --force to back up and replace the blocked paths.')}\n")
        return 1
    return 0


def print_init_help() -> None:
    print(f"""
  {c('bold', 'swe init')} — set up ~/.swe and link every harness to it

  {c('cyan', 'swe init')}            Create the layout and symlink all harnesses
  {c('cyan', 'swe init --full')}     List every path, not just the counts
  {c('cyan', 'swe init --check')}    Show what would change, write nothing
  {c('cyan', 'swe init --check --json')}  The same preview as one JSON document, nothing else
  {c('cyan', 'swe init --force')}    Replace real files too (backed up first)
  {c('cyan', 'swe init --migrate')}  Move a pre-0.2.7 ~/.config/swe into ~/.swe
  {c('cyan', 'swe init --yes')}      Register every new skills folder without asking
  {c('cyan', 'swe init --accounts')} Only sync the extra account homes (see Accounts)

  {c('bold', 'New harnesses')}
    Known harnesses are linked from their own footprint — a config dir
    like ~/.cline or ~/.config/kilo is enough, no skills folder needed
    first. A skills folder nothing claims is offered as new, since apps
    that are not harnesses keep one too. Only folders whose owner has a
    command on PATH or a catalog entry start ticked. Ticked ones are
    registered and linked (instructions too), unticked ones are archived
    in harness.json and left alone. `swe hs` changes either later.
    Without a terminal nothing is decided: new folders stay unlinked
    until an interactive run or --yes. A few known apps (Aside, Pinokio,
    TokenTracker, Ollama) are never offered.

  {c('bold', 'What it owns')}
    ~/.swe/AGENTS.md    one instruction file, linked in under each
                           harness's own name (CLAUDE.md, QWEN.md, CRUSH.md...)
    ~/.swe/skills/      one skill tree, linked in as every harness's skills/
    ~/.swe/hooks/       hook scripts, one folder per harness (hooks/claude/),
                           each script linked into that harness's hooks dir.
                           Only the file: declaring the hook in the harness's
                           settings stays with you.
    ~/.swe/.linkignore  paths to leave alone, one gitignore-style pattern
                           per line (.agents/skills, .agents, .config/*/AGENTS.md)
    ~/.swe/backups/     anything replaced, timestamped

  {c('bold', 'Accounts')}
    Extra Claude and Codex accounts (~/.swe/accounts/<vendor>/<id>/home)
    get none of this until you switch it on, one switch per kind:
      swe config set accounts.sync.instructions true
      swe config set accounts.sync.skills true
      swe config set accounts.sync.hooks true
      swe config set accounts.sync.mcp true
    then run swe init (or swe init --accounts, which does only this).
    Set one back to false and run it again to remove only the links swe
    made there. Anything the account already has in
    that place is reported and left alone, even with --force.
    mcp merges the servers in ~/.swe/mcp.json into each account's
    .claude.json (mcpServers only) or config.toml ([mcp_servers] only),
    never any other key; a server the account names itself is left alone.

  {c('bold', 'States')}
    {c('green', 'linked')}      already points at the canonical file
    {c('cyan', 'create')}      nothing there yet, will symlink (a shortcut file pointing at the one real copy)
    {c('yellow', 'relink')}      symlink pointing somewhere else, will repoint
    {c('cyan', 'absorb')}      a real directory of only duplicate or empty skills, replaced with a symlink
    {c('yellow', 'keep')}        a real directory holding skills that exist nowhere else, reported in --check plans
    {c('red', 'conflict')}    a real file or directory, needs --force
    {c('yellow', 'protected')}   ran without --force: a {c('bold', 'keep')} directory, left untouched
    {c('red', 'blocked')}     ran without --force: a {c('bold', 'conflict')} path, left untouched
    {c('dim', 'skipped')}     harness not installed, or a new folder nobody has reviewed
    {c('dim', 'ignored')}     listed in .linkignore, archived in harness.json, or a
                   known app that is not a harness; never touched or counted

  {c('dim', 'These are the same five ideas swe list legend (✓ ○ ↻ ✗) and swe init')}
  {c('dim', '(linked/create/relink/conflict) print under different names.')}
""")
