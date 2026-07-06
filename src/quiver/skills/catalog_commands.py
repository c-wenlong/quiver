"""Skills catalog and discover CLI commands."""

import json
import sys
from pathlib import Path

from quiver.console import c, truncate
from quiver.skills.catalog_discover import apply_skill_catalog_findings, discover_skill_catalogs
from quiver.skills.help_text import print_skills_catalog_help, print_skills_discover_help
from quiver.skills.catalogs import (
    add_skill_catalog,
    count_skill_md,
    load_skill_catalogs,
    remove_skill_catalog,
    resolve_catalog_path,
    suggest_catalog_label,
)

_CATALOG_SUBCOMMANDS = frozenset({"list", "ls", "add", "remove", "rm", "help", "-h", "--help"})


def _looks_like_catalog_path(arg: str) -> bool:
    if arg in (".", ".."):
        return True
    expanded = Path(arg).expanduser()
    if expanded.exists():
        return True
    return arg.startswith(("/", "~", "."))


def cmd_skills_catalog(args):
    if not args or args[0] in ("-h", "--help", "help"):
        print_skills_catalog_help()
        return 0

    sub = args[0]
    rest = args[1:]

    if sub == "add" or sub not in _CATALOG_SUBCOMMANDS or _looks_like_catalog_path(sub):
        if sub == "add":
            path = rest[0] if rest else "."
            label = rest[1] if len(rest) > 1 else None
        elif sub not in _CATALOG_SUBCOMMANDS or _looks_like_catalog_path(sub):
            path = sub
            label = rest[0] if rest and rest[0] not in ("-h", "--help") else None
            if rest and rest[0] in ("-h", "--help"):
                print_skills_catalog_help()
                return 0
        else:
            path = None
            label = None

        if path is not None:
            try:
                entry = add_skill_catalog(path, label)
            except FileNotFoundError as exc:
                print(c("red", f"  {exc}"))
                return 1
            except ValueError as exc:
                print(c("red", f"  {exc}"))
                return 1
            display = str(resolve_catalog_path(path)).replace(str(Path.home()), "~")
            print(c("green", f"  ✓ Added catalog {entry['label']}: {display}"))
            print(c("dim", "  Run `swe skills` to list skills from this catalog.\n"))
            return 0

    if sub in ("list", "ls"):
        catalogs = load_skill_catalogs()
        print(f"\n{c('bold', 'Skill Catalogs')}\n")
        if not catalogs:
            print(c("dim", "  No catalogs configured.\n"))
            print(c("dim", "  Try: swe skills discover  │  swe skills catalog add <path>\n"))
            return 0
        home = str(Path.home())
        w_label, w_count = 18, 8
        print(c("dim", f"  {'LABEL':<{w_label}} {'SKILLS':>{w_count}}  PATH"))
        print(c("dim", "  " + "─" * 100))
        for entry in catalogs:
            path = Path(entry["path"])
            n = count_skill_md(path) if path.is_dir() else 0
            n_str = c("green", str(n)) if n > 0 else c("dim", "0")
            print(
                f"  {c('cyan', entry['label']):<{w_label + 9}} {n_str:>{w_count + 9}}  "
                f"{c('dim', str(path).replace(home, '~'))}"
            )
        print()
        return 0

    if sub in ("remove", "rm"):
        if not rest:
            print(c("red", "  Usage: swe skills catalog remove <label|path>"))
            return 1
        if remove_skill_catalog(rest[0]):
            print(c("green", f"  ✓ Removed catalog matching {rest[0]!r}\n"))
            return 0
        print(c("red", f"  No catalog matching {rest[0]!r}\n"))
        return 1

    print(c("red", f"  Unknown catalog subcommand: {sub}"))
    print_skills_catalog_help()
    return 1


def _parse_discover_flags(args: list[str]) -> tuple[dict, list[str]]:
    opts = {"apply": False, "json": False, "all": False}
    rest = []
    for arg in args:
        if arg == "--apply":
            opts["apply"] = True
        elif arg == "--json":
            opts["json"] = True
        elif arg == "--all":
            opts["all"] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        else:
            rest.append(arg)
    return opts, rest


def cmd_skills_discover(args):
    opts, rest = _parse_discover_flags(args)
    if rest and rest[0] in ("-h", "--help"):
        print_skills_discover_help()
        return 0
    if rest:
        print(c("red", f"  Unknown argument(s): {' '.join(rest)}"))
        print_skills_discover_help()
        return 1

    findings = discover_skill_catalogs(include_registered=opts["all"])

    if opts["json"]:
        print(
            json.dumps(
                [
                    {
                        "label": f.label,
                        "path": str(f.path),
                        "skill_count": f.skill_count,
                        "source": f.source,
                        "status": f.status,
                    }
                    for f in findings
                ],
                indent=2,
            )
        )
    else:
        print(f"\n{c('bold', 'Skills Discover')}\n")
        print(c("dim", "  Scanning ~/Desktop and ~/Documents for */skills/ trees\n"))
        if not findings:
            print(c("dim", "  No new skill catalogs found.\n"))
            print(
                c(
                    "dim",
                    "  Add manually: swe skills catalog add ~/path/to/skills [label]\n",
                )
            )
        else:
            w_label, w_src, w_stat, w_count = 18, 10, 12, 8
            print(
                c(
                    "dim",
                    f"  {'LABEL':<{w_label}} {'SOURCE':<{w_src}} {'STATUS':<{w_stat}} "
                    f"{'SKILLS':>{w_count}}  PATH",
                )
            )
            print(c("dim", "  " + "─" * 100))
            home = str(Path.home())
            for f in findings:
                path = str(f.path).replace(home, "~")
                stat = c("cyan", f.status) if f.status == "new" else c("dim", f.status)
                print(
                    f"  {c('bold', f.label):<{w_label + 9}} {f.source:<{w_src}} "
                    f"{stat:<{w_stat + 9}} {f.skill_count:>{w_count}}  {c('dim', truncate(path, 52))}"
                )
            print()
            print(
                c(
                    "dim",
                    "  dry-run  ·  swe skills discover --apply  │  swe skills catalog add <path>",
                )
            )
            print()

    if opts["apply"]:
        added = apply_skill_catalog_findings(findings)
        if opts["json"]:
            print(json.dumps({"added": added}, indent=2))
        elif added:
            print(c("green", f"  ✓ Registered {len(added)} catalog(s): {', '.join(added)}"))
            print(c("dim", "  Run `swe skills` to list all skills.\n"))
        elif not opts["json"]:
            print(c("dim", "  Nothing to add.\n"))
    elif not opts["json"] and findings and not sys.stdin.isatty():
        new = [f for f in findings if f.status == "new"]
        if new:
            print(c("dim", "  Tip: pass --apply to register catalogs\n"))

    return 0
