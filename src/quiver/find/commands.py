"""`swe find` — show where the shared assets live and what links to them."""

from __future__ import annotations

from pathlib import Path

from quiver import paths
from quiver.console import c
from quiver.find.tree import (
    agents_tree,
    flat_skills,
    plugin_tree,
    scan_agents,
    scan_skill_roots,
    skills_tree,
)
from quiver.find.tree import filter_scope

# Colour by what you would do about it, not by filesystem type.
STATE_COLOR = {
    "unlinked": "yellow",
    "linked": "green",
    "keep": "yellow",
    "absorb": "cyan",
    "relink": "yellow",
    "conflict": "red",
    "create": "cyan",
    "skipped": "dim",
}
STATE_WORD = {
    "unlinked": "own copy",
    "linked": "synced",
    "keep": "separate",
    "absorb": "unsynced",
    "relink": "wrong target",
    "conflict": "in the way",
    "create": "missing",
    "skipped": "not installed",
}

TREE_MID, TREE_END, TREE_BAR = "├─ ", "└─ ", "│  "


def _short(path: Path, home: Path) -> str:
    if path == home:
        return "~"
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _branch(i: int, total: int) -> str:
    return TREE_END if i == total - 1 else TREE_MID


def _scan_root(root_flag: bool) -> Path:
    """Where to scan from: the directory holding ~/.quiver, or the cwd."""
    return paths.quiver_dir_for().parent if root_flag else Path.cwd()


PATH_WIDTH = 104


def _elide(text: str, width: int) -> str:
    """Shorten from the middle, keeping both ends.

    Truncating from the left kept the filename but threw away which harness a
    path belonged to, so every vendored hit looked the same. Both ends carry
    meaning: the head says whose directory it is, the tail says which file.
    """
    if len(text) <= width:
        return text
    keep = width - 1                     # one char for the ellipsis
    head = (keep + 1) // 2               # bias to the head on an odd split
    tail = keep - head
    return text[:head] + "…" + (text[-tail:] if tail else "")


def _rel(path: Path, root: Path, home: Path, width: int = PATH_WIDTH) -> str:
    """Path relative to the scan root, so a deep tree stays readable.

    Falls back to the ~/ form when the path sits outside the root, which a
    symlink target usually does.
    """
    try:
        text = "./" + str(path.relative_to(root))
    except ValueError:
        text = _short(path, home)
    return _elide(text, width).ljust(width) + " "


def _render_scan(title: str, root: Path, nodes, home: Path, empty: str,
                 scope: str = "global") -> None:
    nodes, vendored = filter_scope(nodes, scope, home)
    label = {"global": "loaded in every session",
             "local": "project files",
             "all": "everything"}[scope]
    print(f"\n{c('bold', title)}  {c('dim', f'--scope={scope} · {label}')}")
    print(f"  {c('dim', 'scanning ' + _short(root, home))}\n")
    if not nodes:
        print(f"  {c('dim', empty)}\n")
        return
    for i, n in enumerate(nodes):
        colour = STATE_COLOR.get(n.state, "dim")
        word = STATE_WORD.get(n.state, n.state)
        extra = ""
        if n.target:
            extra = f" {c('dim', '-> ' + _short(n.target, home))}"
        elif n.count:
            extra = f" {c('dim', str(n.count) + ' skills')}"
        print(f"  {c('dim', _branch(i, len(nodes)))}"
              f"{c(colour, _rel(n.path, root, home))}"
              f"{c(colour, word.ljust(13))}{extra}")
    synced = sum(1 for n in nodes if n.state == "linked")
    print(f"\n  {c('dim', f'{len(nodes)} found · {synced} synced to the shared copy')}")
    if vendored:
        # Plugin caches and vendored repos ship their own instruction files.
        # You never see them while coding, so name the count even when hiding
        # them: an unexpected jump here is worth a look.
        print(f"  {c('yellow', f'{vendored} more inside harness directories')}"
              f" {c('dim', '(plugin caches, vendored repos) — see --scope=all')}")
    print()


def _harness_summary(home: Path) -> None:
    """The nine files quiver actually manages, called out from the scan noise.

    A recursive scan finds every AGENTS.md on the machine, most of them
    project files quiver has no business touching. This names the ones it does.
    """
    canonical, nodes = agents_tree(home)
    live = [n for n in nodes if n.state != "skipped"]
    synced = sum(1 for n in live if n.state == "linked")
    print(f"  {c('bold', 'Managed by quiver')}  {c('dim', _short(canonical, home))}")
    for i, n in enumerate(live):
        colour = STATE_COLOR.get(n.state, "dim")
        print(f"  {c('dim', _branch(i, len(live)))}"
              f"{c(colour, _short(n.path, home).ljust(32))}"
              f"{c(colour, STATE_WORD.get(n.state, n.state))}")
    print(f"\n  {c('dim', f'{synced} of {len(nodes)} harnesses synced')}\n")


def cmd_find_agents(args=None, root_flag: bool = False, scope: str = "global") -> int:
    home = Path.home()
    root = _scan_root(root_flag)
    _render_scan("AGENTS.md", root, scan_agents(root, home), home,
                 "no agent instruction files here", scope)
    if root_flag:
        _harness_summary(home)
    return 0
    canonical, nodes = agents_tree(home)
    print(f"\n{c('bold', 'AGENTS.md')}\n")
    print(f"  {c('green', _short(canonical, home))}"
          f"  {c('dim', 'the one real file')}\n")

    live = [n for n in nodes if n.state != "skipped"]
    gone = [n for n in nodes if n.state == "skipped"]

    for i, n in enumerate(live):
        colour = STATE_COLOR.get(n.state, "dim")
        arrow = f" {c('dim', '->')} {c('dim', _short(n.target, home))}" if n.target else ""
        print(f"  {c('dim', _branch(i, len(live)))}{c(colour, _short(n.path, home).ljust(30))}"
              f"{c(colour, STATE_WORD.get(n.state, n.state).ljust(14))}{arrow}")

    if gone:
        print(f"\n  {c('dim', 'not installed on this machine:')}")
        print(f"  {c('dim', '  ' + ', '.join(n.label for n in gone))}")

    synced = sum(1 for n in nodes if n.state == "linked")
    print(f"\n  {c('dim', f'{synced} of {len(nodes)} harnesses synced')}\n")
    return 0


def cmd_find_skills(args=None, root_flag: bool = False, scope: str = "global") -> int:
    home = Path.home()
    root = _scan_root(root_flag)
    _render_scan("Skills", root, scan_skill_roots(root, home), home,
                 "no skills directories here", scope)
    if not root_flag:
        return 0
    shared, nodes = skills_tree(home)
    plugins = plugin_tree(home)
    flat = flat_skills(home)

    total = len(flat) + sum(len(p.skills) for p in plugins)
    print(f"\n{c('bold', 'Skills')}\n")
    print(f"  {c('green', _short(shared, home))}"
          f"  {c('dim', f'{len(flat)} always-on')}")

    # Plugins, grouped by the marketplace directory holding them.
    by_market: dict[str, list] = {}
    for p in plugins:
        by_market.setdefault(p.marketplace, []).append(p)
    if plugins:
        print(f"  {c('green', _short(shared.parent / 'plugins', home))}"
              f"  {c('dim', f'{sum(len(p.skills) for p in plugins)} in {len(plugins)} plugins')}")
        for market in sorted(by_market):
            group = by_market[market]
            head = f"{market}@" if market else ""
            print(f"    {c('cyan', head or '(flat)')}")
            for i, p in enumerate(group):
                print(f"      {c('dim', _branch(i, len(group)))}"
                      f"{c('cyan', p.name.ljust(16))}{c('dim', f'{len(p.skills):3} skills')}")

    linked = [n for n in nodes if n.state == "linked"]
    other = [n for n in nodes if n.state not in ("linked", "skipped")]

    print(f"\n  {c('bold', f'Harness roots ({len(linked)} synced)')}")
    print(f"  {c('dim', 'all resolve to ' + _short(shared, home))}")
    cols, line = 3, []
    for n in linked:
        line.append(_short(n.path, home).ljust(30))
        if len(line) == cols:
            print("    " + c("dim", "".join(line))); line = []
    if line:
        print("    " + c("dim", "".join(line)))

    if other:
        print(f"\n  {c('bold', 'Not synced')}")
        for i, n in enumerate(other):
            colour = STATE_COLOR.get(n.state, "dim")
            print(f"  {c('dim', _branch(i, len(other)))}"
                  f"{c(colour, _short(n.path, home).ljust(30))}"
                  f"{c(colour, STATE_WORD.get(n.state, n.state).ljust(12))}"
                  f"{c('dim', n.detail)}")

    summary = (f"{total} skills · {len(linked)} roots synced · "
               f"{len(other)} left alone")
    print(f"\n  {c('dim', summary)}\n")
    return 0


def cmd_find(args) -> int:
    args = list(args or [])
    if args and args[0] in ("-h", "--help", "help"):
        print_find_help()
        return 0

    root_flag = any(a in ("--root", "-r") for a in args)
    args = [a for a in args if a not in ("--root", "-r")]

    scope = "global"
    for a in list(args):
        if a.startswith("--scope"):
            scope = a.split("=", 1)[1] if "=" in a else ""
            args.remove(a)
    if scope not in ("all", "global", "local"):
        print(f"Unknown scope: {scope or '(empty)'}. Use all, global, or local.")
        return 1

    topic = args[0] if args else None
    if topic in ("amd", "agents", "agents.md", "instructions"):
        return cmd_find_agents(args[1:], root_flag, scope)
    if topic in ("skills", "skill"):
        return cmd_find_skills(args[1:], root_flag, scope)
    if topic is None:
        cmd_find_agents([], root_flag, scope)
        cmd_find_skills([], root_flag, scope)
        return 0

    print(f"Unknown topic: {topic}")
    print_find_help()
    return 1


def print_find_help() -> None:
    print(f"""
  {c('bold', 'swe find')} — where the shared assets live and what links to them

  {c('cyan', 'swe find')}              Both trees
  {c('cyan', 'swe find amd')}          AGENTS.md and every harness pointing at it
  {c('cyan', 'swe find skills')}       Skills, plugins, and every harness skill root

  {c('bold', 'Where it scans')}
    default        the current directory, recursively
    {c('cyan', '--root')} / {c('cyan', '-r')}   from wherever ~/.quiver lives, recursively

  {c('bold', 'What it shows')}  {c('cyan', '--scope=global|local|all')}
    {c('cyan', 'global')}   (default) files a harness loads into every session:
             ones sitting directly in ~/.<tool>/ or ~/.config/<tool>/,
             plus anything symlinked to the shared copy
    {c('cyan', 'local')}    project files, loaded only when you work in that tree
    {c('cyan', 'all')}      both, plus the vendored ones

  Files nested deeper inside a harness directory (plugin caches, vendored
  repos, editor extensions) ship their own instructions and never show up
  while you code. They are counted in every view and listed under --scope=all.

  {c('bold', 'States')}
    {c('green', 'synced')}         symlinked to the shared copy
    {c('cyan', 'unsynced')}       a real directory that could be absorbed
    {c('yellow', 'separate')}       holds content that exists nowhere else, left alone
    {c('yellow', 'wrong target')}   symlink pointing somewhere unexpected
    {c('red', 'in the way')}     a real file where a link should be
    {c('dim', 'not installed')}  harness absent from this machine

  {c('dim', 'swe init links what is unsynced · swe init --check previews it')}
""")
