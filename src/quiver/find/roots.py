"""The top-level rows an interactive `swe find` starts from, per resource type.

`swe find` prints a tree and the browser walks one, so the two should agree:
each function here mirrors the grouping its print command in
``quiver.find.commands`` uses. Below the roots the browser reads real
directories, which is why a plugin row carries the plugin's own directory
rather than a precomputed listing of the skills inside it.

Nothing here raises. The opening screen of a browser is the worst place to
surface a filesystem error, so an unreadable path costs its own row and
leaves the rest of the list standing.
"""

from __future__ import annotations

from pathlib import Path

from quiver import paths
from quiver.find.entries import Entry
from quiver.find.plugins import Plugin, discover_plugins, filter_plugins
from quiver.find.tree import (
    SCOPE_GLOBAL,
    SCOPE_LOCAL,
    Node,
    agents_tree,
    count_skills,
    filter_scope,
    scope_of,
    skills_tree,
)
from quiver.harness.registry import is_active, load_registry_if_present

SCOPE_ALL = "all"
SCOPES = (SCOPE_GLOBAL, SCOPE_LOCAL, SCOPE_ALL)
SCOPE_DEFAULT = SCOPE_GLOBAL

# --- harness activity -------------------------------------------------
#
# Every `swe find` view enumerates harnesses somehow — a fixed instruction
# target, a directory named after one, a plugin's install record — and every
# one of those enumerations should agree on which harnesses are worth
# showing by default. That agreement lives here, in one place, so a browser
# root and a printed row can never say different things about the same
# harness.

HARNESS_ACTIVE = "active"
HARNESS_ALL = "all"
HARNESS_STATES = (HARNESS_ACTIVE, HARNESS_ALL)
HARNESS_DEFAULT = HARNESS_ACTIVE


def normalise_harness(harness: str) -> str:
    """Fall back to the default rather than reject an unknown word, the
    same policy ``_normalise`` applies to scope."""
    return harness if harness in HARNESS_STATES else HARNESS_DEFAULT


def _label_from_capability_root(root) -> str | None:
    """``~/.factory/skills`` -> ``factory``, ``~/.config/opencode/skills``
    -> ``opencode``: the directory-derived label a filesystem scan would
    produce for a capability's root path.

    Deliberately string-based rather than ``Path.expanduser()``: this runs
    against whatever ``home`` a caller passed in (a tmp dir under test),
    and expanding against the real machine's ``$HOME`` would silently
    resolve against the wrong tree.
    """
    if not root:
        return None
    raw = str(root)
    if raw.startswith("~/"):
        raw = raw[2:]
    elif raw.startswith("~"):
        raw = raw[1:]
    raw = raw.lstrip("/")
    parts = Path(raw).parts
    if not parts:
        return None
    first = parts[0]
    if first == ".config" and len(parts) > 1:
        return parts[1]
    return first[1:] if first.startswith(".") else first


def _capability_aliases(reg: dict) -> dict[str, str]:
    """Directory-derived label -> canonical registry name, read off every
    entry's ``capabilities.*.root``.

    Exists for cases where a harness's registry key and its home directory
    disagree: droid installs to ``~/.factory``, so a filesystem scan derives
    the label "factory", which is not the key ``harness.json`` stores it
    under ("droid"). Only entries that carry a capability root contribute;
    every other harness is expected to match by identity (label == name).
    """
    aliases: dict[str, str] = {}
    for name, entry in reg.items():
        caps = entry.get("capabilities") or {}
        for kind in ("skills", "plugins"):
            label = _label_from_capability_root((caps.get(kind) or {}).get("root"))
            if label and label != name:
                aliases[label] = name
    return aliases


def dir_label(path: Path, home: Path) -> str | None:
    """The harness-shaped first path segment: ``~/.claude/x`` -> "claude",
    ``~/.config/opencode/x`` -> "opencode". None for anything not sitting
    directly under a harness-looking directory, which includes everything
    outside ``home`` and everything at ``home`` itself.
    """
    try:
        parts = path.relative_to(home).parts
    except ValueError:
        return None
    if not parts:
        return None
    first = parts[0]
    if first == ".config" and len(parts) > 1:
        return parts[1]
    return first[1:] if first.startswith(".") else None


def resolve_harness(label: str | None, reg: dict, aliases: dict) -> str | None:
    """A discovered label's canonical registry name, or None when it
    cannot be mapped.

    None is not a failure state to work around — a row that resolves to
    nothing must never be filtered. Hiding an unrecognised row would be the
    one silent hide this feature promises never to do; the shared quiver
    copy and a root nobody has registered are exactly the "unknown" case
    `swe find` exists to keep visible.
    """
    if not label:
        return None
    if label in reg:
        return label
    return aliases.get(label)


def harness_filter(reg: dict, harness: str):
    """A (visible, hidden) pair bound to one registry and one --harness flag.

    ``visible(label)`` resolves the label and returns whether its row should
    show; every archived name it rejects goes into ``hidden`` as a side
    effect, so a caller that runs a whole list through the filter ends up
    with both the kept rows and the exact set to report in the footer,
    without a second pass over the same data.
    """
    aliases = _capability_aliases(reg)
    hidden: set[str] = set()

    def visible(label: str | None) -> bool:
        name = resolve_harness(label, reg, aliases)
        if name is None or harness == HARNESS_ALL:
            return True
        if is_active(reg[name]):
            return True
        hidden.add(name)
        return False

    return visible, hidden


def harness_footer_text(hidden: int) -> str:
    """Match the tone of `swe list`'s existing
    '▪ = archived (23 hidden; --scope=all to show)' footer — named so it
    hides nothing silently: every filtered view says how many and how to
    see them anyway."""
    word = "harness" if hidden == 1 else "harnesses"
    return f"{hidden} archived {word} hidden; --harness=all to show"


def _harness_footer_entry(hidden: int) -> Entry:
    return Entry(f"⋯ {harness_footer_text(hidden)}", None, "")


# A plugin installed outside any marketplace still needs a row to sit under.
# cmd_find_plugins labels that case the same way.
MARKET_NONE = "(none)"

# What each link state means for a file a harness reads as its instructions.
# Only these three ever get a row: "create" and "skipped" describe paths that
# do not exist, and there is nothing to open.
AGENT_STATE_WORD = {
    "linked": "symlinked to the shared copy",
    "relink": "symlink pointing somewhere else",
    "conflict": "its own copy, not linked",
}


def _safe(call, default):
    """Run a discovery call, falling back to ``default`` when it fails.

    The discovery helpers each tolerate their own missing file, but they walk
    real directories, and one looping symlink or one directory the user cannot
    read should not be the difference between a browser that opens and one
    that does not.
    """
    try:
        return call()
    except Exception:
        return default


def _normalise(scope: str) -> str:
    """Fall back to the default rather than reject an unknown scope word.

    ``filter_scope`` indexes a dict with the scope, so anything unrecognised
    would surface as a KeyError from deep inside a discovery call.
    """
    return scope if scope in SCOPES else SCOPE_DEFAULT


def _short(path: Path, home: Path) -> str:
    """The ~/ form of a path, which is how every other view names one."""
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def _is_dir(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_dir()
    except OSError:
        return False


def _exists(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.exists()
    except OSError:
        return False


def _in_scope(path: Path, scope: str, home: Path) -> bool:
    """Scope test for a bare path, where there is no Node to hand filter_scope."""
    if scope == SCOPE_ALL:
        return True
    return scope_of(path, home) == scope


def _plural(n: int, word: str) -> str:
    suffix = "" if n == 1 else "s"
    return f"{n} {word}{suffix}"


def _by_path(node: Node) -> tuple:
    return (str(node.path), node.label)


def agents_roots(home: Path | None = None, scope: str = "global",
                 harness: str = HARNESS_DEFAULT) -> list[Entry]:
    """Every agent-instruction file on this machine, the shared copy first.

    Built from ``agents_tree`` rather than ``scan_agents`` for two reasons.
    The tree knows which harness reads each file, which is the one thing a row
    here has to say and a filename alone cannot. And it checks nine fixed
    paths instead of walking a home directory, which matters because these
    rows are what the browser waits on before it can draw anything.
    """
    home = home or Path.home()
    scope = _normalise(scope)
    harness = normalise_harness(harness)
    reg = _safe(load_registry_if_present, {})
    visible, hidden = harness_filter(reg, harness)
    canonical, nodes = _safe(
        lambda: agents_tree(home), (paths.agents_file_for(home), [])
    )

    out: list[Entry] = []
    # Most rows below are symlinks pointing here, so the one real file gets a
    # row of its own instead of being something you reach by accident. It
    # names no harness of its own, so the filter never touches it.
    if _exists(canonical) and _in_scope(canonical, scope, home):
        out.append(Entry(_short(canonical, home), canonical, "quiver, the shared copy"))

    shown, _vendored = _safe(lambda: filter_scope(nodes, scope, home), ([], 0))
    for node in sorted(shown, key=_by_path):
        if not _exists(node.path):
            continue          # harness not installed, so no file to open
        if not visible(node.label):
            continue          # harness archived, --harness=all to show
        word = AGENT_STATE_WORD.get(node.state, node.state)
        out.append(Entry(_short(node.path, home), node.path,
                         f"{node.label}, {word}"))
    if hidden:
        out.append(_harness_footer_entry(len(hidden)))
    return out


def skills_roots(home: Path | None = None, scope: str = "global",
                 harness: str = HARNESS_DEFAULT) -> list[Entry]:
    """Every skills root on disk, with the synced ones folded into one row.

    Roughly 57 harness roots are symlinks to the same shared tree, so listing
    them individually would be 57 rows that open the same directory. They
    collapse into one grouping row. The unsynced ones stay individual because
    each holds the only copy of what is in it, which is the whole reason they
    were left alone.
    """
    home = home or Path.home()
    scope = _normalise(scope)
    harness = normalise_harness(harness)
    reg = _safe(load_registry_if_present, {})
    visible, hidden = harness_filter(reg, harness)
    shared, nodes = _safe(
        lambda: skills_tree(home), (paths.skills_dir_for(home), [])
    )
    shared_count = _safe(lambda: count_skills(shared), 0)

    out: list[Entry] = []
    if _is_dir(shared) and _in_scope(shared, scope, home):
        skills = _plural(shared_count, "skill")
        out.append(Entry(_short(shared, home), shared, f"{skills}, the shared tree"))

    shown, _vendored = _safe(lambda: filter_scope(nodes, scope, home), ([], 0))
    synced: list[Node] = []
    unsynced: list[Node] = []
    for node in sorted(shown, key=_by_path):
        if not _is_dir(node.path):
            continue          # a root a harness has not created yet
        if node.path == shared:
            continue          # already listed above as the shared tree
        # skill_root_label derives a label from the directory name (droid
        # reads "factory"), which is why this goes through the same
        # capability-aware resolver as every other view rather than
        # indexing the registry by the label directly.
        if not visible(node.label):
            continue          # harness archived, --harness=all to show
        (synced if node.state == "linked" else unsynced).append(node)

    # Unsynced first. They hold content that exists nowhere else, and the
    # synced group is a single row that can sit under them without crowding.
    for node in unsynced:
        count = node.count or _safe(lambda: count_skills(node.path), 0)
        skills = _plural(count, "skill")
        detail = f"{skills}, not synced"
        if node.detail:
            detail = f"{detail} ({node.detail})"
        out.append(Entry(_short(node.path, home), node.path, detail))

    if synced:
        skills = _plural(shared_count, "skill")
        children = [
            Entry(_short(n.path, home), n.path, f"{skills}, synced")
            for n in synced
        ]
        target = _short(shared, home)
        roots = _plural(len(synced), "root")
        out.append(Entry("synced roots", None,
                         f"{roots}, all resolve to {target}", children))
    if hidden:
        out.append(_harness_footer_entry(len(hidden)))
    return out


def _plugin_entry(plugin: Plugin) -> Entry:
    """One plugin, carrying its directory so the browser can walk into it."""
    bits: list[str] = []
    if plugin.version:
        bits.append(plugin.version)
    bits.extend(f"{n} {kind}" for kind, n in sorted(plugin.components.items()))

    path = plugin.path if _is_dir(plugin.path) else None
    if path is None:
        # A harness can record an install whose cache has since been cleaned.
        # The row still says the plugin is configured, it just cannot open.
        bits.append("no directory on disk")
    return Entry(plugin.name, path, ", ".join(bits))


def _harness_detail(plugins: list[Plugin], markets: int) -> str:
    counted = _plural(len(plugins), "plugin")
    where = _plural(markets, "marketplace")
    detail = f"{counted} in {where}"
    if all(p.enabled is None for p in plugins):
        # cursor and grok expose no install record, so say it once per harness
        # rather than letting every cached copy read as active.
        detail = f"{detail}, cached; no install record"
    return detail


def plugins_roots(home: Path | None = None, scope: str = "global",
                  harness: str = HARNESS_DEFAULT) -> list[Entry]:
    """Plugins nested harness -> marketplace -> plugin, as cmd_find_plugins prints them.

    That nesting is the directory layout on disk, so a reader who followed the
    printed tree finds the same three levels here. Only the plugin rows carry
    a path; the two above them exist to organise, and descend into children.
    """
    home = home or Path.home()
    scope = _normalise(scope)
    harness = normalise_harness(harness)
    reg = _safe(load_registry_if_present, {})
    visible, hidden = harness_filter(reg, harness)
    plugins = _safe(lambda: discover_plugins(home), [])
    shown, _hidden = _safe(lambda: filter_plugins(plugins, scope), ([], 0))

    by_harness: dict[str, dict[str, list[Plugin]]] = {}
    for plugin in shown:
        # discover_plugins already emits canonical registry names (droid,
        # not the ~/.factory directory it lives in), so this is the same
        # resolver every other view uses, just fed an already-correct label.
        if not visible(plugin.harness):
            continue          # harness archived, --harness=all to show
        market = plugin.marketplace or MARKET_NONE
        by_harness.setdefault(plugin.harness, {}).setdefault(market, []).append(plugin)

    out: list[Entry] = []
    for hname in sorted(by_harness):
        markets = by_harness[hname]
        market_rows: list[Entry] = []
        for market in sorted(markets):
            group = sorted(markets[market], key=lambda p: (p.name.lower(), p.name))
            market_rows.append(Entry(market, None, _plural(len(group), "plugin"),
                                     [_plugin_entry(p) for p in group]))
        every = [p for group in markets.values() for p in group]
        out.append(Entry(hname, None, _harness_detail(every, len(markets)),
                         market_rows))
    if hidden:
        out.append(_harness_footer_entry(len(hidden)))
    return out
