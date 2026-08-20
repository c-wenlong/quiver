"""`swe find` — show where the shared assets live and what links to them."""

from __future__ import annotations

from pathlib import Path

from quiver import paths
from quiver.console import c, elide, terminal_width, truncate
from quiver.init.layout import skill_folder_names
from quiver.find.tree import (
    agents_tree,
    flat_skills,
    plugin_tree,
    scan_agents,
    scan_skill_roots,
    skills_tree,
)
from quiver.find.plugins import discover_plugins, filter_plugins
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


def _rel(path: Path, root: Path, home: Path, width: int = PATH_WIDTH) -> str:
    """Path relative to the scan root, so a deep tree stays readable.

    Falls back to the ~/ form when the path sits outside the root, which a
    symlink target usually does.
    """
    try:
        text = "./" + str(path.relative_to(root))
    except ValueError:
        text = _short(path, home)
    return elide(text, width).ljust(width) + " "


# A path can be both a result and a parent of results: ~/.codex/vendor_imports/
# skills holds another skills/ inside it. So every trie value is a dict, and a
# node attaches under this sentinel key rather than replacing the dict.
LEAF = "\x00leaf"


def _build_trie(nodes, root: Path, home: Path) -> dict:
    """Nest nodes by path segment. A node attaches at LEAF on its own dict."""
    trie: dict = {}
    for n in nodes:
        try:
            parts = list(n.path.relative_to(root).parts)
        except ValueError:
            parts = [_short(n.path, home)]
        cur = trie
        for seg in parts:
            cur = cur.setdefault(seg, {})
        cur[LEAF] = n
    return trie


def _is_pure_leaf(sub: dict) -> bool:
    """A result with nothing nested under it."""
    return LEAF in sub and len(sub) == 1


def _collapse(trie: dict) -> dict:
    """Fold single-child directory chains into one segment.

    Without this a vendored path becomes a staircase of one-child levels:
    plugins/ then cache/ then openai-curated/, each saying nothing on its own.
    A directory holding exactly one file collapses onto that file's row too.
    """
    out: dict = {}
    for name, child in trie.items():
        if name == LEAF:
            out[name] = child
            continue
        child = _collapse(child)
        while len(child) == 1 and LEAF not in child:
            only_name, only_child = next(iter(child.items()))
            name = f"{name}/{only_name}"
            child = only_child
        out[name] = child
    return out


def _walk_trie(trie: dict, prefix: str = ""):
    """Yield (indent, label, node_or_None) in display order, files first."""
    items = [(k, v) for k, v in trie.items() if k != LEAF]
    # Files before subdirectories: a directory's own entry is what you are
    # looking for, and burying it under nested branches hides it.
    items.sort(key=lambda kv: (not _is_pure_leaf(kv[1]), kv[0].lower()))
    for i, (name, child) in enumerate(items):
        last = i == len(items) - 1
        branch = TREE_END if last else TREE_MID
        deeper = prefix + ("   " if last else TREE_BAR)
        if _is_pure_leaf(child):
            yield prefix + branch, name, child[LEAF]
            continue
        # Both a result and a parent: show it on its own row, then descend.
        yield prefix + branch, name + "/", child.get(LEAF)
        yield from _walk_trie(child, deeper)


def _render_tree(nodes, root: Path, home: Path) -> None:
    """Print nodes as a nested tree with the status column aligned."""
    rows = list(_walk_trie(_collapse(_build_trie(nodes, root, home))))
    if not rows:
        return
    # Fit the longest row when everything is short, which is the common case
    # and means no elision at all. Only once something exceeds the cap does a
    # percentile take over, so one deeply vendored path cannot pad every other
    # row out to match it.
    lengths = sorted(len(indent + label) for indent, label, _ in rows)
    if lengths[-1] <= PATH_WIDTH:
        width = lengths[-1]
    else:
        p95 = lengths[min(len(lengths) - 1, int(len(lengths) * 0.95))]
        width = max(min(p95, PATH_WIDTH), 24)

    for indent, label, node in rows:
        line = indent + label
        if len(line) > width:
            # Elide the label, never the indent: the branch lines carry the
            # structure and are meaningless once broken.
            label = elide(label, max(4, width - len(indent)))
            line = indent + label
        if node is None:
            print(c("dim", line))
            continue
        colour = STATE_COLOR.get(node.state, "dim")
        word = STATE_WORD.get(node.state, node.state)
        extra = ""
        if node.target:
            extra = f" {c('dim', '-> ' + _short(node.target, home))}"
        elif node.count:
            extra = f" {c('dim', str(node.count) + ' skills')}"
        print(f"{c('dim', indent)}{c(colour, label.ljust(width - len(indent)))}"
              f"  {c(colour, word.ljust(13))}{extra}")


def _render_scan(title: str, root: Path, nodes, home: Path, empty: str,
                 scope: str = "global") -> None:
    nodes, vendored = filter_scope(nodes, scope, home)
    label = {"global": "loaded in every session",
             "local": "project files",
             "all": "everything"}[scope]
    print(f"\n{c('bold', title)}  {c('dim', f'--scope={scope} · {label}')}")
    print(f"  {c('dim', elide('scanning ' + _short(root, home), _path_budget(4)))}\n")
    if not nodes:
        print(f"  {c('dim', empty)}\n")
        return
    _render_tree(nodes, root, home)
    synced = sum(1 for n in nodes if n.state == "linked")
    print(f"\n  {c('dim', f'{len(nodes)} found · {synced} synced to the shared copy')}")
    if vendored:
        # Plugin caches and vendored repos ship their own instruction files.
        # You never see them while coding, so name the count even when hiding
        # them: an unexpected jump here is worth a look.
        note = f"{vendored} more inside harness directories"
        tail = "(plugin caches, vendored repos), see --scope=all"
        if len(note) + len(tail) + 3 > terminal_width():
            tail = "see --scope=all"
        print(f"  {c('yellow', note)} {c('dim', tail)}")
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


def _skill_columns(names: list[str], indent: str = "    ", cols: int = 4,
                   width: int = 34, limit: int = 0) -> None:
    """Print skill names in columns under their root.

    A root can hold 117 skills, so one name per line would bury the tree
    it is meant to sit inside. ``limit`` caps a very long list and says
    how many were left out rather than truncating silently.
    """
    # Fit the grid to the window: drop columns first, then narrow them,
    # so names stay readable rather than every row wrapping.
    avail = max(20, terminal_width() - len(indent))
    while cols > 1 and cols * width > avail:
        cols -= 1
    width = max(12, min(width, avail // cols))

    shown = names if not limit or len(names) <= limit else names[:limit]
    line: list[str] = []
    for name in shown:
        line.append(elide(name, width - 2).ljust(width))
        if len(line) == cols:
            print(indent + c("dim", "".join(line).rstrip()))
            line = []
    if line:
        print(indent + c("dim", "".join(line).rstrip()))
    if len(shown) < len(names):
        rest = len(names) - len(shown)
        print(indent + c("dim", f"... and {rest} more (swe skills <filter> to search)"))


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
    if flat:
        _skill_columns(flat)

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
    root_w = 30
    cols = max(1, (terminal_width() - 4) // root_w)
    line: list[str] = []
    for n in linked:
        line.append(_short(n.path, home).ljust(root_w))
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
                  f"{c('dim', elide(n.detail, _path_budget(48)))}")
            # These roots hold the only copy of what is in them, so their
            # contents are the part worth seeing.
            _skill_columns(sorted(skill_folder_names(n.path)), indent="      ",
                           limit=40)

    summary = (f"{total} skills · {len(linked)} roots synced · "
               f"{len(other)} left alone")
    print(f"\n  {c('dim', elide(summary, terminal_width() - 4))}\n")
    return 0


def _path_budget(used: int = 34) -> int:
    """Room left for a path once the fixed columns have taken theirs."""
    return max(16, terminal_width() - used)


PREFIX_MEANING = {
    "dv": "development",
    "pd": "productivity",
    "rf": "reference",
    "so": "social",
    "sr": "search",
}


def cmd_find_mcps(args=None, root_flag: bool = False, scope: str = "global") -> int:
    """Where MCP servers live, and how far the hub has reached.

    `swe mcp list` gives the tool-by-server matrix, which is the right
    shape once you know what you are looking for. This answers the prior
    question: what the hub holds, and which harnesses are behind it.
    """
    from quiver.find.mcps import (
        PREFIX_UNFILED,
        hub_view,
        scan_configs,
        tool_views,
        unmanaged,
    )

    home = Path.home()
    hub = hub_view()
    total = len(hub.servers)

    print(f"\n{c('bold', 'MCP servers')}"
          f"  {c('dim', '--scope=' + scope)}\n")

    if not total:
        print(f"  {c('dim', 'no hub yet — swe mcp discover --apply')}\n")
        return 0

    print(f"  {c('green', '~/.quiver/mcp.json')}  {c('dim', f'{total} servers')}")
    for prefix in sorted(hub.by_prefix, key=lambda p: (p == PREFIX_UNFILED, p)):
        names = hub.by_prefix[prefix]
        if prefix:
            head = c("cyan", f"{prefix}@".ljust(8))
            note = c("dim", PREFIX_MEANING.get(prefix, ""))
        else:
            head = c("yellow", "(none)".ljust(8))
            note = c("dim", "no prefix, outside the taxonomy")
        print(f"    {head}{note}")
        _skill_columns([n.split("__")[-1] for n in names], indent="      ",
                       cols=4, width=26)

    views = tool_views(hub.servers)
    if views:
        print(f"\n  {c('bold', 'Harness configs')}")
        for i, t in enumerate(views):
            got = len(t.present)
            colour = "green" if got == total else "yellow" if got else "red"
            bar = f"{got}/{total}"
            print(f"  {c('dim', _branch(i, len(views)))}"
                  f"{c(colour, t.name.ljust(18))}"
                  f"{c(colour, bar.ljust(9))}"
                  f"{c('dim', elide(_short(Path(t.path), home) if t.path else '', _path_budget()))}")
            if t.only_here:
                print(f"      {c('yellow', 'only here: ')}"
                      f"{c('dim', ', '.join(sorted(t.only_here)[:6]))}")

    # The sections above read the config paths quiver has registered. This
    # one reads the disk, which is where a config nobody registered hides.
    configs = scan_configs(home, scope=scope)
    stray = unmanaged(home, hub.servers, scope=scope)
    # Compare resolved paths, not harness names: ~/.factory/mcp.json is read
    # under the name "droid", and ~/.gemini has four config files of which
    # quiver reads exactly one.
    known = set()
    for t in views:
        if t.path:
            try:
                known.add(Path(t.path).expanduser().resolve())
            except OSError:
                pass
    hub_path = (paths.quiver_dir_for(home) / "mcp.json").resolve()
    known.add(hub_path)

    unregistered = []
    for cfg in configs:
        try:
            real = cfg.path.resolve()
        except OSError:
            continue
        if real not in known:
            unregistered.append(cfg)

    if unregistered:
        print(f"\n  {c('bold', 'Configs quiver does not read')}")
        for i, cfg in enumerate(unregistered):
            marks = f"{len(cfg.local)} local"
            if cfg.remote:
                marks += f", {len(cfg.remote)} remote"
            print(f"  {c('dim', _branch(i, len(unregistered)))}"
                  f"{c('yellow', cfg.harness.ljust(18))}"
                  f"{c('dim', marks.ljust(20))}"
                  f"{c('dim', elide(_short(cfg.path, home), _path_budget(44)))}")

    if stray:
        print(f"\n  {c('bold', 'On disk but not in the hub')}")
        for name in sorted(stray):
            where = ", ".join(sorted({x.harness for x in stray[name]}))
            kind = "remote" if any(name in x.remote for x in stray[name]) else "local"
            print(f"    {c('yellow', name.ljust(28))}"
                  f"{c('dim', kind.ljust(8))}{c('dim', where)}")

    if hub.duplicates:
        print(f"\n  {c('bold', 'Same server under two names')}")
        print(f"  {c('dim', 'one local and one remote copy is fine; two of a kind is not')}")
        for pair in hub.duplicates:
            print(f"    {c('yellow', ' == '.join(pair))}")

    behind = [t for t in views if len(t.present) < total]
    summary = (f"{total} servers · {len(configs)} configs on disk · "
               f"{len(behind)} tools behind the hub")
    if stray:
        summary += f" · {len(stray)} unmanaged"
    print(f"\n  {c('dim', elide(summary, terminal_width() - 4))}\n")
    return 0


def cmd_find_plugins(args=None, root_flag: bool = False, scope: str = "global") -> int:
    """Plugins across every harness that has a plugin system.

    Nested harness -> marketplace -> plugin, because that is the directory
    layout on disk: ~/.quiver/plugins/dv/cloudflare, and claude's cache
    mirrors it at ~/.claude/plugins/cache/dv/cloudflare/0.1.0.
    """
    home = Path.home()
    plugins = discover_plugins(home)
    shown, hidden = filter_plugins(plugins, scope)

    label = {"global": "installed and enabled",
             "local": "installed but disabled",
             "all": "everything on disk"}[scope]
    print(f"\n{c('bold', 'Plugins')}  {c('dim', f'--scope={scope} · {label}')}\n")
    if not shown:
        print(f"  {c('dim', 'none')}\n")
        return 0

    tree: dict = {}
    for p in shown:
        tree.setdefault(p.harness, {}).setdefault(p.marketplace or "(none)", []).append(p)

    totals: dict = {}
    for harness in sorted(tree):
        markets = tree[harness]
        # cursor and grok expose no install record, so say so once per harness
        # rather than implying every cached copy is active.
        unknown = all(p.enabled is None for m in markets.values() for p in m)
        note = c("dim", "  (cached; no install record)") if unknown else ""
        print(f"  {c('bold', harness)}{note}")

        every = [p for m in markets.values() for p in m]
        name_w = min(max(len(p.name) for p in every) + 2, 34)
        ver_w = min(max(len(p.version or "-") for p in every) + 2, 16)

        for mi, market in enumerate(sorted(markets)):
            m_last = mi == len(markets) - 1
            print(f"  {c('dim', TREE_END if m_last else TREE_MID)}{c('cyan', market + '/')}")
            bar = "   " if m_last else TREE_BAR
            group = sorted(markets[market], key=lambda p: p.name)
            for i, p in enumerate(group):
                branch = TREE_END if i == len(group) - 1 else TREE_MID
                state = ("enabled" if p.enabled else "disabled") if p.enabled is not None else "cached"
                colour = {"enabled": "green", "disabled": "yellow", "cached": "dim"}[state]
                parts = ", ".join(f"{n} {k}" for k, n in sorted(p.components.items()))
                for k, n in p.components.items():
                    totals[k] = totals.get(k, 0) + n
                print(f"  {c('dim', bar + branch)}"
                      f"{c(colour, elide(p.name, name_w - 1).ljust(name_w))}"
                      f"{c('dim', elide(p.version or '-', ver_w - 1).ljust(ver_w))}"
                      f"{c(colour, state.ljust(9))}"
                      f"{c('dim', truncate(parts, _path_budget(name_w + ver_w + 17)))}")
        print()

    n_markets = sum(len(m) for m in tree.values())
    summary = f"{len(shown)} plugins · {n_markets} marketplaces · {len(tree)} harnesses"
    if totals:
        summary += "  ·  " + ", ".join(f"{n} {k}" for k, n in sorted(totals.items()))
    print(f"  {c('dim', elide(summary, terminal_width() - 4))}")
    if hidden:
        print(f"  {c('yellow', f'{hidden} more')} "
              f"{c('dim', 'not in this scope — see --scope=all')}")
    print()
    return 0


def cmd_find(args) -> int:
    args = list(args or [])
    # Anywhere, not just first: `swe find plugins --help` should print help
    # rather than the listing.
    if any(a in ("-h", "--help", "help") for a in args):
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
    if topic in ("plugins", "plugin"):
        return cmd_find_plugins(args[1:], root_flag, scope)
    if topic in ("mcp", "mcps", "servers"):
        return cmd_find_mcps(args[1:], root_flag, scope)
    if topic is None:
        cmd_find_agents([], root_flag, scope)
        cmd_find_skills([], root_flag, scope)
        cmd_find_plugins([], root_flag, scope)
        cmd_find_mcps([], root_flag, scope)
        return 0

    print(f"Unknown topic: {topic}")
    print_find_help()
    return 1


def print_find_help() -> None:
    print(f"""
  {c('bold', 'swe find')} — where the shared assets live and what links to them

  {c('cyan', 'swe find')}              Every tree
  {c('cyan', 'swe find amd')}          AGENTS.md and every harness pointing at it
  {c('cyan', 'swe find skills')}       Skills, plugins, and every harness skill root
  {c('cyan', 'swe find plugins')}      Plugins across every harness that has them
  {c('cyan', 'swe find mcps')}         MCP servers in the hub, and which harnesses have them

  {c('dim', 'swe mcp list gives the tool-by-server matrix once you know what')}
  {c('dim', 'you are looking for; swe find mcps answers what the hub holds.')}

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
