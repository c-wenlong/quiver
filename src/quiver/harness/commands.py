"""Harness registry commands: list, info, add, remove, use, check, tags, aliases."""

import os
import shutil
from datetime import datetime

from quiver.console import c, truncate
from quiver.harness.registry import load_registry, resolve, save_registry
from quiver.harness.tools import is_installed, live_version


def _session_counts_100d():
    from quiver.sessions.usage import session_counts_100d

    return session_counts_100d()


def cmd_list(args):
    tools = load_registry()
    tag_filter = args[0].lstrip("-") if args else None
    counts = _session_counts_100d()

    print(f"\n{c('bold', 'AI Coding Tools')}\n")

    w_name, w_cmd, w_ver, w_alias, w_sess, w_desc = 16, 18, 12, 12, 8, 36
    hdr = (
        f"  {'NAME':<{w_name}} {'COMMAND':<{w_cmd}} {'VERSION':<{w_ver}}"
        f" {'ALIASES':<{w_alias}} {'100d':>{w_sess}} {'INSTALLED':<4} DESCRIPTION"
    )
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 110))

    for name, info in sorted(tools.items(), key=lambda x: (-counts.get(x[0], 0), x[0])):
        if tag_filter and tag_filter not in info.get("tags", []):
            continue

        installed = is_installed(info["command"])
        status = c("green", "✓") if installed else c("red", "✗")
        ver = truncate(info.get("version") or "—", w_ver)
        aliases = ", ".join(a for a in info.get("aliases", []) if a != name)
        desc = c("dim", truncate(info.get("description", ""), w_desc))
        sess = counts.get(name, 0)
        sess_str = c("green", str(sess)) if sess > 0 else c("dim", "—")

        print(
            f"  {c('bold', name):<{w_name + 9}} {info['command']:<{w_cmd}}"
            f" {ver:<{w_ver}} {c('cyan', aliases):<{w_alias + 9}} {sess_str:>{w_sess + 9}} {status}   {desc}"
        )

    print()
    n_inst = sum(1 for i in tools.values() if is_installed(i["command"]))
    hints = "swe use <name|alias>  │  swe info <name>  │  swe list <tag>  │  swe check"
    print(c("dim", f"  {n_inst}/{len(tools)} installed  ·  {hints}"))

    all_tags = sorted({t for i in tools.values() for t in i.get("tags", [])})
    tag_str = "  ".join(c("cyan", t) for t in all_tags)
    print(f"  {c('dim', 'tags:')}  {tag_str}\n")


def cmd_info(args):
    if not args:
        print(c("red", "Usage: swe info <name|alias>"))
        return
    tools = load_registry()
    name = resolve(tools, args[0])
    if not name:
        print(c("red", f"  Tool '{args[0]}' not found. Try 'swe list'."))
        return

    info = tools[name]
    installed = is_installed(info["command"])
    path = shutil.which(info["command"]) or "not found"
    aliases = [a for a in info.get("aliases", []) if a != name]

    print(f"\n  {c('bold', name)}")
    rows = [
        ("Command", info["command"]),
        ("Aliases", ", ".join(aliases) if aliases else "—"),
        ("Description", info.get("description", "—")),
        ("Version", info.get("version") or "unknown"),
        ("Tags", ", ".join(info.get("tags", []))),
        ("Status", c("green", "installed") if installed else c("red", "not installed")),
        ("Path", path),
    ]
    if info.get("notes"):
        rows.append(("Notes", info["notes"]))
    for label, val in rows:
        print(f"  {'  ' + label + ':':<16} {val}")
    print()


def cmd_add(args):
    if len(args) < 2:
        print(c("red", "Usage: swe add <name> <command> [description] [--aliases a,b] [--tags t1,t2]"))
        return
    tools = load_registry()
    name = args[0]
    command = args[1]
    desc = ""
    tags = ["agentic", "coding"]
    aliases: list[str] = []

    i = 2
    while i < len(args):
        if args[i] == "--aliases" and i + 1 < len(args):
            aliases = [a.strip() for a in args[i + 1].split(",")]
            i += 2
        elif args[i] == "--tags" and i + 1 < len(args):
            tags = [t.strip() for t in args[i + 1].split(",")]
            i += 2
        elif not args[i].startswith("--"):
            desc = args[i]
            i += 1
        else:
            i += 1

    action = "Updated" if name in tools else "Added"
    tools[name] = {
        "command": command,
        "description": desc,
        "version": None,
        "tags": tags,
        "aliases": aliases,
        "added": datetime.now().isoformat(),
    }
    save_registry(tools)
    status = c("green", "installed") if is_installed(command) else c("yellow", "not yet in PATH")
    alias_str = f"  aliases: {', '.join(aliases)}" if aliases else ""
    print(f"  {c('green', '✓')} {action} '{name}' → '{command}' ({status}){alias_str}")


def cmd_remove(args):
    if not args:
        print(c("red", "Usage: swe remove <name|alias>"))
        return
    tools = load_registry()
    name = resolve(tools, args[0])
    if not name:
        print(c("red", f"  Tool '{args[0]}' not found."))
        return
    del tools[name]
    save_registry(tools)
    print(f"  {c('green', '✓')} Removed '{name}' from registry.")


def cmd_use(args):
    if not args:
        print(c("red", "Usage: swe use <name|alias> [extra args...]"))
        cmd_list([])
        return
    tools = load_registry()
    name = resolve(tools, args[0])
    extra = args[1:]
    if not name:
        print(c("red", f"  Tool '{args[0]}' not found. Try 'swe list'."))
        return
    command = tools[name]["command"]
    if not is_installed(command):
        print(c("red", f"  Command '{command}' not found in PATH."))
        return
    label = f"{command} {' '.join(extra)}".strip()
    print(c("dim", f"  → {label}\n"))
    os.execvp(command, [command] + extra)


def cmd_check(args):
    tools = load_registry()
    updated = False
    print(f"\n{c('bold', 'Checking AI tools...')}\n")
    for name, info in sorted(tools.items()):
        aliases = [a for a in info.get("aliases", []) if a != name]
        alias_str = f"  ({', '.join(aliases)})" if aliases else ""
        if is_installed(info["command"]):
            ver = live_version(info["command"])
            if ver and ver != info.get("version"):
                tools[name]["version"] = ver
                updated = True
            display = info.get("version") or "version unknown"
            print(f"  {c('green', '✓')}  {name:<22}{c('cyan', alias_str):<20} {c('dim', display)}")
        else:
            print(f"  {c('red', '✗')}  {name:<22}{c('dim', alias_str):<20} {c('dim', 'not installed')}")
    if updated:
        save_registry(tools)
        print(c("dim", "\n  Registry updated."))
    print()


def cmd_tags(args):
    tools = load_registry()
    tag_map: dict[str, list[str]] = {}
    for name, info in tools.items():
        for tag in info.get("tags", []):
            tag_map.setdefault(tag, []).append(name)
    print(f"\n{c('bold', 'Available tags')}\n")
    for tag in sorted(tag_map):
        names = ", ".join(sorted(tag_map[tag]))
        print(f"  {c('cyan', tag):<{20}} {c('dim', names)}")
    print()


def cmd_aliases(args):
    tools = load_registry()
    print(f"\n{c('bold', 'Short aliases')}\n")
    for name, info in sorted(tools.items()):
        aliases = [a for a in info.get("aliases", []) if a != name]
        if aliases:
            print(f"  {c('cyan', ', '.join(aliases)):<10}  →  {name}")
    print()
