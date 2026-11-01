"""Skills tree, link, unlink, and move CLI commands."""

from pathlib import Path

from swe.console import c, tilde
from swe.flags import expand_value_flags
from swe.json_output import emit, error_stream
from swe.skills.help_text import (
    print_skills_link_help,
    print_skills_move_help,
    print_skills_unlink_help,
)
from swe.skills.models import LinkReply, MoveReply, UnlinkReply
from swe.skills.link_ops import (
    SkillLayoutError,
    link_skill_root,
    move_skill,
    unlink_skill_root,
)


def _parse_flags(args: list[str], allowed: frozenset[str]) -> tuple[dict, list[str]]:
    args = expand_value_flags(args, {"--from", "--to"})
    opts = {"force": False, "mkdir": False, "from": None, "to": None, "json": False}
    rest = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--from", "--to"):
            if arg not in allowed:
                raise ValueError(f"{arg} is not valid here")
            opts[arg[2:]] = args[i + 1]
            i += 1
        elif arg in ("--force", "--mkdir", "--json"):
            if arg not in allowed:
                raise ValueError(f"{arg} is not valid here")
            opts[arg[2:]] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        elif arg.startswith("-"):
            raise ValueError(f"Unknown flag: {arg}")
        else:
            rest.append(arg)
        i += 1
    return opts, rest


def cmd_skills_link(args):
    json_view = "--json" in args
    try:
        opts, rest = _parse_flags(args, {"--force", "--json"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if rest and rest[0] in ("-h", "--help"):
        print_skills_link_help()
        return 0
    if not rest:
        print(c("red", "  Usage: swe skills link <source> [target] [--force]"),
              file=error_stream(json_view))
        return 1
    source = rest[0]
    target = rest[1] if len(rest) > 1 else None
    if len(rest) > 2:
        print(c("red", f"  Unexpected args: {' '.join(rest[2:])}"), file=error_stream(json_view))
        return 1
    try:
        label, src, tgt = link_skill_root(source, target, force=opts["force"])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if opts["json"]:
        return emit(LinkReply(label, str(src), str(tgt)).to_json())
    home = Path.home()
    print(c("green", f"  ✓ Linked {label}: {tilde(src, home)} → {tilde(tgt, home)}"))
    print(c("dim", "  Run `swe skills tree` to verify.\n"))
    return 0


def cmd_skills_unlink(args):
    json_view = "--json" in args
    try:
        opts, rest = _parse_flags(args, {"--mkdir", "--json"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if rest and rest[0] in ("-h", "--help"):
        print_skills_unlink_help()
        return 0
    if not rest or len(rest) > 1:
        print(c("red", "  Usage: swe skills unlink <harness|path> [--mkdir]"),
              file=error_stream(json_view))
        return 1
    try:
        label, path = unlink_skill_root(rest[0], mkdir=opts["mkdir"])
    except SkillLayoutError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if opts["json"]:
        return emit(UnlinkReply(label, str(path), opts["mkdir"]).to_json())
    home = Path.home()
    msg = f"  ✓ Unlinked {label}: {tilde(path, home)}"
    if opts["mkdir"]:
        msg += " (empty directory created)"
    print(c("green", msg))
    print(c("dim", "  Run `swe skills tree` to verify.\n"))
    return 0


def cmd_skills_move(args):
    json_view = "--json" in args
    try:
        opts, rest = _parse_flags(args, {"--from", "--to", "--force", "--json"})
    except ValueError as exc:
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if rest and rest[0] in ("-h", "--help"):
        print_skills_move_help()
        return 0
    if not rest or len(rest) > 1 or not opts["from"] or not opts["to"]:
        print(c("red", "  Usage: swe skills move <name> --from=<scope> --to=<scope>"),
              file=error_stream(json_view))
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
        print(c("red", f"  {exc}"), file=error_stream(json_view))
        return 1
    if opts["json"]:
        return emit(MoveReply(name, str(src), str(dest)).to_json())
    home = Path.home()
    print(c("green", f"  ✓ Moved {name}"))
    print(c("dim", f"    from {tilde(src, home)}"))
    print(c("dim", f"    to   {tilde(dest, home)}\n"))
    return 0
