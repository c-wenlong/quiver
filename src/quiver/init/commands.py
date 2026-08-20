"""`swe init` — build the ~/.quiver layout and link every harness to it."""

from __future__ import annotations

import shutil
from pathlib import Path

from quiver.console import c
from quiver.paths import backup_tree
from quiver.init.migrate import apply_migration, plan_migration, write_gitignore
from quiver.init.layout import (
    LinkStatus,
    SEED_AGENTS_MD,
    agents_file,
    backups_dir,
    plan,
    quiver_dir,
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
}


def _short(path: Path, home: Path) -> str:
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _backup(path: Path, home: Path) -> Path:
    """Copy a real file or directory aside before it is replaced."""
    return backup_tree(path, home)


def _apply(status: LinkStatus, canonical: Path, home: Path, force: bool) -> str:
    """Carry out one status. Returns a short result word for the report."""
    if status.state in ("linked", "skipped"):
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


def _print_section(title: str, rows: list[tuple[LinkStatus, str]], home: Path) -> None:
    print(f"\n  {c('bold', title)}")
    for status, result in rows:
        colour = STATE_COLOR.get(result, "dim")
        note = status.detail
        if result == "blocked":
            colour, note = "red", status.detail
        print(
            f"    {c(colour, result.ljust(8))} {_short(status.path, home).ljust(30)}"
            f" {c('dim', note)}"
        )


def _ensure_scaffold(home: Path, check_only: bool) -> list[str]:
    """Create the .quiver directory, seed file, and skills dir if absent."""
    notes = []
    root, agents, skills, backups = (
        quiver_dir(home),
        agents_file(home),
        skills_dir(home),
        backups_dir(home),
    )
    for path, label in ((root, "~/.quiver"), (skills, "skills/"), (backups, "backups/")):
        if not path.exists():
            notes.append(f"create {label}")
            if not check_only:
                path.mkdir(parents=True, exist_ok=True)
    if not agents.exists():
        notes.append("seed AGENTS.md")
        if not check_only:
            agents.write_text(SEED_AGENTS_MD, encoding="utf-8")
    return notes


def cmd_init(args) -> int:
    args = list(args or [])
    if args and args[0] in ("-h", "--help", "help"):
        print_init_help()
        return 0

    check_only = "--check" in args or "-n" in args
    force = "--force" in args
    migrate = "--migrate" in args

    unknown = [a for a in args if a not in ("--check", "-n", "--force", "--migrate")]
    if unknown:
        print(f"Unknown option: {unknown[0]}")
        print_init_help()
        return 1

    home = Path.home()
    scaffold = _ensure_scaffold(home, check_only)

    # An old ~/.quiver/ is reported whenever it exists, so a machine that
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

    instructions, skills = plan(home)

    if check_only:
        inst_rows = [(s, "would-" + s.state if s.changed else s.state) for s in instructions]
        skill_rows = [(s, "would-" + s.state if s.changed else s.state) for s in skills]
    else:
        inst_rows = [
            (s, _apply(s, agents_file(home), home, force)) for s in instructions
        ]
        skill_rows = [(s, _apply(s, skills_dir(home), home, force)) for s in skills]

    header = "Quiver layout" + (c("dim", "  (check only, nothing written)") if check_only else "")
    print(f"\n{c('bold', header)}")
    print(f"  {c('dim', 'root')}  {quiver_dir(home)}")
    if scaffold:
        print(f"  {c('dim', 'scaffold')}  {', '.join(scaffold)}")

    _print_section("Instructions", inst_rows, home)
    _print_section("Skills", skill_rows, home)

    blocked = [r for _, r in inst_rows + skill_rows if r in ("blocked", "would-conflict")]
    # check mode renders an unchanged state verbatim, so "keep" arrives as-is.
    protected = [
        (s_, r) for s_, r in skill_rows if r in ("protected", "keep", "would-keep")
    ]
    changed = [r for _, r in inst_rows + skill_rows if r.startswith("would-") or r == "linked"]
    summary = (
        f"{len(changed)} linked, {len(blocked)} blocked, "
        f"edit {agents_file(home)} to change them all"
    )
    print(f"\n  {c('dim', summary)}\n")
    if protected:
        print(f"  {c('yellow', 'Left alone, these hold skills that exist nowhere else:')}")
        for s_, _ in protected:
            print(f"    {_short(s_.path, home).ljust(30)} {c('dim', s_.detail)}")
        print(f"  {c('dim', 'Move what you want to keep into ~/.quiver/skills first, or --force.')}\n")
    if blocked:
        print(f"  {c('yellow', 'Re-run with --force to back up and replace the blocked paths.')}\n")
        return 1
    return 0


def print_init_help() -> None:
    print(f"""
  {c('bold', 'swe init')} — set up ~/.quiver and link every harness to it

  {c('cyan', 'swe init')}            Create the layout and symlink all harnesses
  {c('cyan', 'swe init --check')}    Show what would change, write nothing
  {c('cyan', 'swe init --force')}    Replace real files too (backed up first)
  {c('cyan', 'swe init --migrate')}  Move a pre-0.2.7 ~/.config/swe into ~/.quiver

  {c('bold', 'What it owns')}
    ~/.quiver/AGENTS.md    one instruction file, linked in under each
                           harness's own name (CLAUDE.md, QWEN.md, CRUSH.md...)
    ~/.quiver/skills/      one skill tree, linked in as every harness's skills/
    ~/.quiver/backups/     anything replaced, timestamped

  {c('bold', 'States')}
    {c('green', 'linked')}    already points at the canonical file
    {c('cyan', 'create')}    nothing there yet, will symlink
    {c('yellow', 'relink')}    symlink pointing somewhere else, will repoint
    {c('red', 'conflict')}  a real file or directory, needs --force
    {c('dim', 'skipped')}   harness not installed on this machine
""")
