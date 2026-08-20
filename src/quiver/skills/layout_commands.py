"""Skills tree, link, unlink, and move CLI commands."""

import sys
from pathlib import Path

from quiver.console import c, truncate
from quiver.skills.help_text import (
    print_skills_link_help,
    print_skills_move_help,
    print_skills_unlink_help,
)
from quiver.skills.layout import (
    enumerate_skill_roots,
)
from quiver.skills.link_ops import (
    SkillLayoutError,
    link_skill_root,
    move_skill,
    unlink_skill_root,
)


def _tilde(path: Path, home: Path) -> str:
    text = str(path)
    home_text = str(home)
    return text.replace(home_text, "~") if text.startswith(home_text) else text


def _parse_flags(args: list[str]) -> tuple[dict, list[str]]:
    opts = {"force": False, "mkdir": False, "from": None, "to": None, "json": False}
    rest = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--force":
            opts["force"] = True
        elif arg == "--mkdir":
            opts["mkdir"] = True
        elif arg == "--from" and i + 1 < len(args):
            opts["from"] = args[i + 1]
            i += 1
        elif arg == "--to" and i + 1 < len(args):
            opts["to"] = args[i + 1]
            i += 1
        elif arg == "--json":
            opts["json"] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        else:
            rest.append(arg)
        i += 1
    return opts, rest


def cmd_skills_link(args):
    opts, rest = _parse_flags(args)
    if rest and rest[0] in ("-h", "--help"):
        print_skills_link_help()
        return 0
    if not rest:
        print(c("red", "  Usage: swe skills link <source> [target] [--force]"))
        return 1
    source = rest[0]
    target = rest[1] if len(rest) > 1 else None
    if len(rest) > 2:
        print(c("red", f"  Unexpected args: {' '.join(rest[2:])}"))
        return 1
    try:
        label, src, tgt = link_skill_root(source, target, force=opts["force"])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"))
        return 1
    home = Path.home()
    print(c("green", f"  ✓ Linked {label}: {_tilde(src, home)} → {_tilde(tgt, home)}"))
    print(c("dim", "  Run `swe skills tree` to verify.\n"))
    return 0


def cmd_skills_unlink(args):
    opts, rest = _parse_flags(args)
    if rest and rest[0] in ("-h", "--help"):
        print_skills_unlink_help()
        return 0
    if not rest:
        print(c("red", "  Usage: swe skills unlink <harness|path> [--mkdir]"))
        return 1
    try:
        label, path = unlink_skill_root(rest[0], mkdir=opts["mkdir"])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"))
        return 1
    home = Path.home()
    msg = f"  ✓ Unlinked {label}: {_tilde(path, home)}"
    if opts["mkdir"]:
        msg += " (empty directory created)"
    print(c("green", msg))
    print(c("dim", "  Run `swe skills tree` to verify.\n"))
    return 0


def cmd_skills_move(args):
    opts, rest = _parse_flags(args)
    if rest and rest[0] in ("-h", "--help"):
        print_skills_move_help()
        return 0
    if not rest or not opts["from"] or not opts["to"]:
        print(c("red", "  Usage: swe skills move <name> --from <scope> --to <scope>"))
        return 1
    name = rest[0]
    try:
        src, dest = move_skill(
            name,
            opts["from"],
            opts["to"],
            force=opts["force"],
        )
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"))
        return 1
    home = Path.home()
    print(c("green", f"  ✓ Moved {name}"))
    print(c("dim", f"    from {_tilde(src, home)}"))
    print(c("dim", f"    to   {_tilde(dest, home)}\n"))
    return 0
