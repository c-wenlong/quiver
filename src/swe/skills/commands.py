"""Skills CLI commands."""

import sys

from swe.console import c, cpad, elide, terminal_width, tilde, truncate
from swe.json_output import emit, take_json_flag, error_stream
from swe.skills.catalog_commands import cmd_skills_catalog, cmd_skills_discover
from swe.skills.discovery import discover_skills
from swe.skills.help_text import cmd_skills_help, print_skills_overview
from swe.skills.layout_commands import (
    cmd_skills_link,
    cmd_skills_move,
    cmd_skills_unlink,
)
from swe.table import Table


def cmd_skills_new(args):
    """Create a SKILL.md skeleton in the shared skills root."""
    from swe.flags import expand_value_flags
    from swe.skills.create import create

    args, json_view = take_json_flag(list(args))
    # Only the flags: "help" is a valid skill name and must reach create().
    if args and args[0] in ("-h", "--help"):
        from swe.skills.help_text import print_skills_new_help
        print_skills_new_help()
        return 0
    refusal = error_stream(json_view)
    try:
        args = expand_value_flags(args, {"--description"})
    except ValueError as exc:
        print(c("red", str(exc)), file=refusal)
        return 1
    positional = []
    description = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--description":
            description = args[i + 1]
            i += 2
        elif arg.startswith("--"):
            print(c("red", f"Unknown flag: {arg}"), file=refusal)
            return 1
        else:
            positional.append(arg)
            i += 1
    if len(positional) != 1:
        print(c("red", "Usage: swe skills new <name> [--description=<text>]"),
              file=refusal)
        return 1
    try:
        path, _ = create(positional[0], description)
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=refusal)
        return 1
    if json_view:
        return emit({"name": positional[0], "path": str(path)})
    print(c("green", f"  ✓ Created {positional[0]}: {path}"))
    return 0


def _superseded_by_find(name: str, args) -> int:
    """`swe skills tree` and `swe skills scope list` both drew a layout map.

    `swe find skills` draws the same map from a filesystem scan instead of a
    hardcoded candidate list, so it saw 60 roots where these saw 18, and it
    reported 0 skills for every symlinked root because it counted the link
    rather than its target. Rather than fail on muscle memory, forward.
    """
    from swe.find.commands import cmd_find_skills

    if name == "list":
        print(c("dim", "\n  swe skills <filter> searches; this is the tree view\n"))
    else:
        print(c("dim", f"\n  swe skills {name} is now swe find skills\n"))
    scope = "all" if any(a == "--scope=all" for a in (args or [])) else "global"
    # full: "this is the tree view" is the promise above, and the forwarded
    # commands always drew every root, so they skip find's count summary.
    return cmd_find_skills([], root_flag=True, scope=scope, full=True)


def cmd_skills(args):
    if not args or args[0] in ("-h", "--help"):
        print_skills_overview()
        return 0
    if args[0] == "help":
        return cmd_skills_help(args[1:])
    if args[0] in ("discover",):
        return cmd_skills_discover(args[1:])
    if args[0] in ("catalog", "catalogs"):
        return cmd_skills_catalog(args[1:])
    if args[0] in ("scope", "scopes"):
        return _superseded_by_find("scope list", args[1:])
    if args[0] == "tree":
        return _superseded_by_find("tree", args[1:])
    if args[0] == "link":
        return cmd_skills_link(args[1:])
    if args[0] == "unlink":
        return cmd_skills_unlink(args[1:])
    if args[0] == "move":
        return cmd_skills_move(args[1:])
    if args[0] == "new":
        return cmd_skills_new(args[1:])
    if args[0] == "hub":
        return cmd_skills_hub(args[1:])

    args, json_view = take_json_flag(list(args))
    if json_view:
        return _skills_json(args)

    # Browsing is a tree; searching is a table. A bare `list` is browsing,
    # and a flat run of several hundred rows was the wrong shape for it, so
    # it renders through the same view as `swe find skills`. A filter or
    # --desc is a search, and keeps the table because a tree cannot show
    # you which of 400 skills matched.
    if args[0] in ("list", "ls") and len(args) == 1:
        return _superseded_by_find("list", [])

    show_desc = False
    filt = None
    for arg in args:
        if arg in ("-d", "--desc"):
            show_desc = True
        elif arg in ("list", "ls"):
            continue
        elif arg.startswith("-"):
            print(c("red", f"  Unknown flag: {arg}"))
            return 1
        else:
            filt = arg.lower()

    skills = discover_skills()
    if filt:
        skills = [
            s
            for s in skills
            if filt in s["name"].lower()
            or filt in s["scope"].lower()
            or any(filt in v.lower() for v in s.get("visible_via", []))
        ]

    if not skills:
        print(c("dim", "\n  No skills found.\n"))
        print(c("dim", "  Try: swe skills discover  │  swe skills catalog .  │  swe skills help\n"))
        return 0

    skills.sort(key=lambda s: (s["scope"], s["name"].lower()))

    print(f"\n{c('bold', 'Agent Skills')}\n")

    # One line per skill: NAME | SCOPE | PATH. The old layout spent three
    # lines each and carried a VISIBLE VIA column that repeated SCOPE in
    # 133 of 147 rows; in the rest it listed all 14 harnesses sharing the
    # one linked root, which is what SCOPE "shared" already says. Now that
    # root discovery reaches every root on disk this list is several
    # hundred rows, so the per-skill line budget matters.
    name_w, scope_w = 30, 16
    # No floor: a path that will not fit is better elided than wrapped,
    # and elide keeps both ends so the harness and the filename survive.
    path_w = max(16, terminal_width() - name_w - scope_w - 8)

    table = Table()
    table.add_column("name", "NAME", width=name_w,
                     kind="preformatted", trust_cell_width=True)
    table.add_column("scope", "SCOPE", width=scope_w,
                     kind="preformatted", trust_cell_width=True)
    table.add_column("path", "PATH", width=path_w,
                     kind="preformatted", trust_cell_width=True)

    for skill in skills:
        table.add_row({
            "name": cpad("bold", truncate(skill["name"], name_w), name_w),
            "scope": cpad("cyan", truncate(skill["scope"], scope_w), scope_w),
            "path": cpad(
                "dim", elide(tilde(skill["path"]), path_w), path_w
            ),
        })

    if not show_desc:
        for line in table.render():
            print(line)
    else:
        rendered = table.render()
        print(rendered[0])
        print(rendered[1])
        for skill, row_line in zip(skills, rendered[2:]):
            print(row_line)
            if skill.get("description"):
                print(c("dim", " " * 2 + truncate(skill["description"], 100)))

    n_scopes = len({s["scope"] for s in skills})
    print()
    print(c("dim", f"  {len(skills)} skills across {n_scopes} roots"
                   f"  ·  swe find skills -r  │  swe skills help"))
    print()
    return 0


def _skills_json(args) -> int:
    """`swe skills [list] [filter] --json`: every skill and who sees it.

    A refusal leaves stdout empty: the app decodes it whole.
    """
    from swe.skills.inventory import installed_skills

    filt = None
    for arg in args:
        if arg in ("list", "ls"):
            continue
        if arg.startswith("-"):
            print(f"Unknown flag for swe skills --json: {arg}", file=sys.stderr)
            return 1
        if filt is not None:
            print(f"Unexpected args: {arg}", file=sys.stderr)
            return 1
        filt = arg.lower()
    data = installed_skills()
    if filt:
        data["skills"] = [
            s for s in data["skills"]
            if filt in s["name"].lower() or filt in s["root"].lower()
            or any(filt in h for h in s["harnesses"])
        ]
    return emit(data)


# Install states, worst first, with how each reads in the listing.
_HUB_STATES = {
    "unregistered": ("red", "marketplace not added"),
    "not-installed": ("yellow", "not installed"),
    "linked-manifest": ("yellow", "linked manifest"),
    "stale": ("yellow", "stale"),
    "disabled-here": ("yellow", "disabled"),
    "disabled": ("yellow", "disabled"),
    "installed": ("green", "installed"),
}


def cmd_skills_hub(args) -> int:
    """What the quiver hub (~/.swe/plugins) offers, and where it landed."""
    from swe.skills.help_text import print_skills_hub_help
    from swe.skills.inventory import hub_plugins

    args, json_view = take_json_flag(list(args or []))
    if args and args[0] in ("-h", "--help", "help"):
        print_skills_hub_help()
        return 0
    if args:
        print(c("red", f"  Unexpected args: {' '.join(args)}"),
              file=error_stream(json_view))
        return 1
    data = hub_plugins()
    if json_view:
        return emit(data)

    plugins = data["plugins"]
    if not plugins:
        print(c("dim", "\n  No plugins in ~/.swe/plugins yet.\n"))
        return 0
    print(f"\n{c('bold', 'SWE hub')}  {c('dim', data['hub'])}\n")
    width = max(len(f"{p['name']}@{p['marketplace']}") for p in plugins) + 2
    for plugin in plugins:
        ref = f"{plugin['name']}@{plugin['marketplace']}"
        n = len(plugin["skills"])
        skills = f"{n} skill" + ("" if n == 1 else "s")
        states = "  ".join(
            f"{harness} {c(*_HUB_STATES.get(gap['state'], ('yellow', gap['state'])))}"
            for harness, gap in plugin["install"].items()
        )
        print(f"  {c('bold', ref.ljust(width))}{c('dim', skills.ljust(10))}{states}")
        if plugin["description"]:
            print(f"  {' ' * width}{c('dim', truncate(plugin['description'], 90))}")
    print(f"\n  {c('dim', 'swe skills hub --json lists each fix  ·  swe find plugins --full')}\n")
    return 0
