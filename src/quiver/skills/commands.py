"""Skills CLI commands."""

from pathlib import Path

from quiver.console import c, truncate
from quiver.skills.discovery import discover_skills, skill_roots


def cmd_skills_scopes(args):
    skills = discover_skills()
    counts: dict[str, int] = {}
    for skill in skills:
        counts[skill["scope"]] = counts.get(skill["scope"], 0) + 1

    home = str(Path.home())
    roots = skill_roots()

    print(f"\n{c('bold', 'Skill Scopes')}\n")
    w_scope, w_count = 16, 8
    hdr = f"  {'SCOPE':<{w_scope}} {'SKILLS':>{w_count}}  PATH"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 100))

    for label, root in roots:
        rp = str(root).replace(home, "~")
        real = str(root.resolve()).replace(home, "~")
        arrow = c("dim", f"  → {real}") if real != rp else ""
        n = counts.get(label, 0)
        n_str = c("green", str(n)) if n > 0 else c("dim", "0")
        print(f"  {c('cyan', label):<{w_scope + 9}} {n_str:>{w_count + 9}}  {c('dim', rp)}{arrow}")

    print()
    print(
        c(
            "dim",
            f"  {len(roots)} scopes  ·  {len(skills)} skills total"
            f"  ·  swe skills <scope>  to filter",
        )
    )
    print()


def cmd_skills(args):
    if args and args[0] in ("scope", "scopes"):
        return cmd_skills_scopes(args[1:])

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
            s for s in skills if filt in s["name"].lower() or filt in s["scope"].lower()
        ]

    if not skills:
        print(c("dim", "\n  No skills found.\n"))
        return

    skills.sort(key=lambda s: (s["scope"], s["name"].lower()))

    print(f"\n{c('bold', 'Agent Skills')}\n")
    w_name, w_scope = 30, 16
    hdr = f"  {'NAME':<{w_name}} {'SCOPE':<{w_scope}} PATH"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 100))

    home = str(Path.home())
    for skill in skills:
        name = truncate(skill["name"], w_name)
        path = skill["path"].replace(home, "~")
        print(
            f"  {c('bold', name):<{w_name + 9}} {c('cyan', skill['scope']):<{w_scope + 9}} {c('dim', path)}"
        )
        if show_desc and skill["description"]:
            print(
                f"  {'':<{w_name}} {'':<{w_scope}} {c('dim', truncate(skill['description'], 96))}"
            )

    n_scopes = len({s["scope"] for s in skills})
    print()
    print(
        c(
            "dim",
            f"  {len(skills)} skills across {n_scopes} scopes"
            f"  ·  swe skills <filter>  │  swe skills -d",
        )
    )
    print(c("dim", "  roots:"))
    for label, root in skill_roots():
        rp = str(root).replace(home, "~")
        real = str(root.resolve()).replace(home, "~")
        arrow = c("dim", f"  → {real}") if real != rp else ""
        print(f"    {c('cyan', label):<{16 + 9}} {c('dim', rp)}{arrow}")
    print()
