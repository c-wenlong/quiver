"""Skills CLI commands."""

from pathlib import Path

from quiver.console import c, cpad, elide, terminal_width, truncate
from quiver.skills.catalog_commands import cmd_skills_catalog, cmd_skills_discover
from quiver.skills.discovery import discover_skills, skill_roots
from quiver.skills.help_text import cmd_skills_help, print_skills_overview
from quiver.skills.layout_commands import (
    cmd_skills_link,
    cmd_skills_move,
    cmd_skills_unlink,
)
from quiver.table import Table


def _superseded_by_find(name: str, args) -> int:
    """`swe skills tree` and `swe skills scope list` both drew a layout map.

    `swe find skills` draws the same map from a filesystem scan instead of a
    hardcoded candidate list, so it saw 60 roots where these saw 18, and it
    reported 0 skills for every symlinked root because it counted the link
    rather than its target. Rather than fail on muscle memory, forward.
    """
    from quiver.find.commands import cmd_find_skills

    print(c("dim", f"\n  swe skills {name} is now swe find skills\n"))
    scope = "all" if any(a == "--scope=all" for a in (args or [])) else "global"
    return cmd_find_skills([], root_flag=True, scope=scope)


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

    show_desc = False
    filt = None
    for arg in args:
        if arg in ("-d", "--desc"):
            show_desc = True
        elif arg in ("list", "ls"):
            continue
        elif not arg.startswith("-"):
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
    path_w = max(40, terminal_width() - name_w - scope_w - 8)

    table = Table()
    table.add_column("name", "NAME", width=name_w,
                     kind="preformatted", trust_cell_width=True)
    table.add_column("scope", "SCOPE", width=scope_w,
                     kind="preformatted", trust_cell_width=True)
    table.add_column("path", "PATH", width=path_w,
                     kind="preformatted", trust_cell_width=True)

    home_str = str(Path.home())
    for skill in skills:
        table.add_row({
            "name": cpad("bold", truncate(skill["name"], name_w), name_w),
            "scope": cpad("cyan", truncate(skill["scope"], scope_w), scope_w),
            "path": cpad(
                "dim", elide(skill["path"].replace(home_str, "~"), path_w), path_w
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
