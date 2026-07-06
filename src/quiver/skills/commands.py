"""Skills CLI commands."""

from pathlib import Path

from quiver.console import c, truncate
from quiver.skills.catalog_commands import cmd_skills_catalog, cmd_skills_discover
from quiver.skills.discovery import discover_skills, skill_roots
from quiver.skills.help_text import cmd_skills_help, print_skills_overview
from quiver.skills.layout import enumerate_skill_roots
from quiver.skills.layout_commands import (
    cmd_skills_link,
    cmd_skills_move,
    cmd_skills_tree,
    cmd_skills_unlink,
)


def cmd_skills_scopes(args):
    if args and args[0] in ("-h", "--help", "help"):
        from quiver.skills.help_text import print_skills_scope_help

        print_skills_scope_help()
        return 0

    skills = discover_skills()
    counts: dict[str, int] = {}
    for skill in skills:
        counts[skill["scope"]] = counts.get(skill["scope"], 0) + 1

    home = Path.home()
    home_str = str(home)
    entries = enumerate_skill_roots(home=home)

    print(f"\n{c('bold', 'Skill Scopes')}\n")
    w_scope, w_kind, w_count = 16, 10, 8
    hdr = f"  {'SCOPE':<{w_scope}} {'KIND':<{w_kind}} {'SKILLS':>{w_count}}  PATH"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 100))

    for entry in entries:
        if not entry.exists:
            continue
        rp = str(entry.path).replace(home_str, "~")
        n = entry.skill_count if entry.skill_count else counts.get(entry.label, 0)
        n_str = c("green", str(n)) if n > 0 else c("dim", "0")
        if entry.kind == "symlink":
            kind = c("yellow", "symlink")
            tgt = entry.link_target_label or (
                str(entry.link_target).replace(home_str, "~") if entry.link_target else "?"
            )
            link_note = c("dim", f"  → {tgt}")
        elif entry.canonical_label and entry.canonical_label != entry.label:
            kind = c("dim", "alias")
            link_note = c("dim", f"  → {entry.canonical_label}")
        else:
            kind = c("green", entry.kind)
            link_note = ""
        print(
            f"  {c('cyan', entry.label):<{w_scope + 9}} {kind:<{w_kind + 9}} {n_str:>{w_count + 9}}  "
            f"{c('dim', rp)}{link_note}"
        )

    print()
    print(
        c(
            "dim",
            f"  {sum(1 for e in entries if e.exists)} roots  ·  {len(skills)} unique skills"
            f"  ·  swe skills tree  │  swe skills help scope",
        )
    )
    print()


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
        return cmd_skills_scopes(args[1:])
    if args[0] == "tree":
        return cmd_skills_tree(args[1:])
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
    w_name, w_scope = 28, 14
    hdr = f"  {'NAME':<{w_name}} {'SCOPE':<{w_scope}} VISIBLE VIA"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 100))

    home_str = str(Path.home())
    for skill in skills:
        name = truncate(skill["name"], w_name)
        via = skill.get("visible_via", [skill["scope"]])
        via_text = ", ".join(via) if len(via) > 1 else via[0]
        if len(via) > 1:
            via_text = c("cyan", via_text)
        else:
            via_text = c("dim", via_text)
        path = skill["path"].replace(home_str, "~")
        print(
            f"  {c('bold', name):<{w_name + 9}} {c('cyan', skill['scope']):<{w_scope + 9}} {via_text}"
        )
        print(f"  {'':<{w_name}} {'':<{w_scope}} {c('dim', path)}")
        if show_desc and skill["description"]:
            print(
                f"  {'':<{w_name}} {'':<{w_scope}} {c('dim', truncate(skill['description'], 96))}"
            )

    n_scopes = len({s["scope"] for s in skills})
    print()
    print(
        c(
            "dim",
            f"  {len(skills)} skills across {n_scopes} canonical scopes"
            f"  ·  swe skills tree  │  swe skills help",
        )
    )
    print()
