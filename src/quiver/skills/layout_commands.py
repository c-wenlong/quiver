"""Skills tree, link, unlink, and move CLI commands."""

import sys
from pathlib import Path

from quiver.console import c, truncate
from quiver.paths import SKILL_LINKS_FILE
from quiver.skills.help_text import (
    print_skills_link_help,
    print_skills_move_help,
    print_skills_tree_help,
    print_skills_unlink_help,
)
from quiver.skills.layout import (
    enumerate_skill_roots,
    layout_groups,
    load_link_records,
    sync_link_records_from_filesystem,
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


def cmd_skills_tree(args):
    json_out = "--json" in args
    do_sync = "--sync" in args
    if "-h" in args or "--help" in args:
        print_skills_tree_help()
        return 0

    home = Path.home()
    if do_sync:
        synced = sync_link_records_from_filesystem(home=home)
        if synced and not json_out:
            print(c("dim", f"  synced link records: {', '.join(synced)}"))
    groups = layout_groups(home=home)

    if json_out:
        import json

        payload = []
        for group in groups:
            canonical = group["canonical"]
            payload.append(
                {
                    "resolved": str(group["resolved"]) if group["resolved"] else None,
                    "canonical": canonical.label if canonical else None,
                    "members": [
                        {
                            "label": m.label,
                            "path": str(m.path),
                            "kind": m.kind,
                            "link_target": str(m.link_target) if m.link_target else None,
                            "link_target_label": m.link_target_label,
                            "skill_count": m.skill_count,
                            "aliases": m.aliases,
                        }
                        for m in group["members"]
                    ],
                }
            )
        print(json.dumps(payload, indent=2))
        return 0

    print(f"\n{c('bold', 'Skill Layout')}\n")
    records = {r["label"]: r for r in load_link_records()}

    for group in groups:
        canonical = group["canonical"]
        if canonical is None:
            continue
        resolved = group["resolved"]
        count = canonical.skill_count
        count_str = f"{count} skill{'s' if count != 1 else ''}" if count else "empty"

        if canonical.kind == "directory":
            header = f"{c('bold', canonical.label)}  {c('dim', _tilde(canonical.path, home))}"
            print(f"  {header}")
            print(f"    {c('green', 'directory')} · {count_str}")
        elif canonical.kind == "symlink":
            header = f"{c('bold', canonical.label)}  {c('dim', _tilde(canonical.path, home))}"
            print(f"  {header}")
            target = canonical.link_target_label or _tilde(canonical.link_target or Path("?"), home)
            print(f"    {c('yellow', 'symlink')} → {c('cyan', target)} · {count_str}")
        else:
            print(f"  {c('bold', canonical.label)}  {c('dim', _tilde(canonical.path, home))}")
            print(f"    {c('dim', canonical.kind)}")

        linked = [m for m in group["members"] if m.label != canonical.label]
        for member in linked:
            if member.kind == "symlink":
                tgt = member.link_target_label or _tilde(member.link_target or Path("?"), home)
                print(
                    f"    {c('cyan', member.label):<12} {c('yellow', 'symlink')} → {c('cyan', tgt)}"
                    f"  {c('dim', _tilde(member.path, home))}"
                )
            elif member.kind == "directory":
                print(
                    f"    {c('cyan', member.label):<12} {c('green', 'alias')} of {canonical.label}"
                    f"  {c('dim', _tilde(member.path, home))}"
                )

        if canonical.label in records:
            updated = str(records[canonical.label].get("updated", ""))[:19]
            if updated:
                print(f"    {c('dim', f'recorded {updated}')}")

        print()

    print(
        c(
            "dim",
            "  swe skills link <harness> [target]  │  swe skills unlink <harness>  │  swe skills move <name> --from A --to B",
        )
    )
    print(c("dim", f"  records: {SKILL_LINKS_FILE}\n"))
    return 0


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
