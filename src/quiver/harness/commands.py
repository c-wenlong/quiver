"""Harness registry commands: list, info, add, remove, use, check, tags, aliases, star."""

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from quiver.console import c, lpad, truncate, visible_len
from quiver.harness.registry import load_registry, resolve, save_registry
from quiver.harness.stars import is_starred, load_stars, toggle_star, unstar
from quiver.harness.tools import extract_version_number, is_installed, live_version
from quiver.prompt import read_line


def _session_counts_100d():
    from quiver.sessions.usage import session_counts_100d

    return session_counts_100d()


def _sort_tools(tools: dict, counts: dict[str, int], stars: list[str]):
    """Starred first (pin order), then by 100d usage desc, then name."""
    star_index = {name: i for i, name in enumerate(stars)}

    def key(item):
        name = item[0]
        if name in star_index:
            return (0, star_index[name], 0, name)
        return (1, 0, -counts.get(name, 0), name)

    return sorted(tools.items(), key=key)


def _pad(text: str, width: int) -> str:
    """Pad plain text to width (ANSI-safe via lpad when colored)."""
    return lpad(text, width)


def cmd_list(args):
    # --refresh bypasses session cache and rate limits cache
    refresh = "--refresh" in args or "-r" in args
    args = [a for a in args if a not in ("--refresh", "-r")]
    if refresh:
        from quiver.sessions.aggregator import invalidate_cache as _inv_sessions
        from quiver.harness.rate_limits import invalidate_cache as _inv_rates

        _inv_sessions()
        _inv_rates()

    tools = load_registry()
    tag_filter = args[0].lstrip("-") if args else None
    counts = _session_counts_100d()
    stars = load_stars()
    starred_set = set(stars)

    # Fetch rate limits (cached 60s, --refresh bypasses)
    from quiver.harness.rate_limits import get_all_rate_limits

    rate_limits = get_all_rate_limits(use_cache=not refresh)

    print(f"\n{c('bold', 'AI Coding Tools')}\n")

    w_name, w_cmd, w_ver, w_alias, w_sess, w_rate, w_desc = 16, 18, 12, 12, 8, 14, 36
    hdr = (
        f"  {'':2}{'NAME':<{w_name}} {'COMMAND':<{w_cmd}} {'VERSION':<{w_ver}}"
        f" {'ALIASES':<{w_alias}} {'100d':>{w_sess}} {'RATE':<{w_rate}} {'INST':<4} DESCRIPTION"
    )
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * (112 + w_rate + 1)))

    shown_starred = False
    for name, info in _sort_tools(tools, counts, stars):
        if tag_filter and tag_filter not in info.get("tags", []):
            continue

        installed = is_installed(info["command"])
        status = c("green", "✓") if installed else c("red", "✗")
        ver = truncate(info.get("version") or "—", w_ver)
        aliases = ", ".join(a for a in info.get("aliases", []) if a != name)
        desc_plain = truncate(info.get("description", ""), w_desc)
        # 0 when a session parser exists but found nothing; — when untracked
        if name in counts:
            sess = counts.get(name, 0)
            sess_plain = str(sess)
            sess_known = True
        else:
            sess = 0
            sess_plain = "—"
            sess_known = False
        favourited = name in starred_set

        # Rate limit column (preserve ANSI colors while padding)
        rl = rate_limits.get(name)
        if rl:
            rate_str = rl.format_column()
        else:
            rate_str = c("dim", "—")
        rate_pad = w_rate - visible_len(rate_str)
        rate_col = rate_str + " " * max(rate_pad, 0)

        if favourited:
            shown_starred = True
            mark = c("neon_pink", "★")
            name_s = c("neon", _pad(name, w_name))
            cmd_s = c("neon", _pad(info["command"], w_cmd))
            ver_s = c("neon", _pad(ver, w_ver))
            alias_s = c("neon", _pad(aliases, w_alias))
            sess_s = c("neon", f"{sess_plain:>{w_sess}}")
            rate_s = c("neon", rate_col)
            desc_s = c("neon", desc_plain)
            border = c("neon", "║")
            print(
                f"{border}{mark} {name_s} {cmd_s} {ver_s} {alias_s} {sess_s} {rate_s} {status}   {desc_s}{border}"
            )
        else:
            mark = " "
            name_s = c("bold", _pad(name, w_name))
            if sess_known and sess > 0:
                sess_col = c("green", f"{sess_plain:>{w_sess}}")
            else:
                sess_col = c("dim", f"{sess_plain:>{w_sess}}")
            print(
                f"  {mark} {name_s} {info['command']:<{w_cmd}}"
                f" {ver:<{w_ver}} {c('cyan', _pad(aliases, w_alias))} "
                f"{sess_col}"
                f" {rate_col}"
                f" {status}   {c('dim', desc_plain)}"
            )

    print()
    n_inst = sum(1 for i in tools.values() if is_installed(i["command"]))
    n_star = sum(1 for n in tools if n in starred_set)
    hints = "swe use <name|alias>  │  swe star <name>  │  swe info <name>  │  swe check"
    print(c("dim", f"  {n_inst}/{len(tools)} installed  ·  {n_star} starred  ·  {hints}"))
    if shown_starred:
        print(f"  {c('neon_pink', '★')} {c('dim', '= favourited (pinned top, neon border)')}")

    all_tags = sorted({t for i in tools.values() for t in i.get("tags", [])})
    tag_str = "  ".join(c("cyan", t) for t in all_tags)
    print(f"  {c('dim', 'tags:')}  {tag_str}\n")


def cmd_star(args):
    """Favourite / pin harnesses to the top of `swe list`."""
    tools = load_registry()

    if not args:
        stars = [s for s in load_stars() if s in tools]
        orphan = [s for s in load_stars() if s not in tools]
        print(f"\n{c('bold', 'Starred harnesses')}\n")
        if not stars and not orphan:
            print(c("dim", "  None yet. Try: swe star droid\n"))
            return
        for i, name in enumerate(stars, 1):
            info = tools[name]
            aliases = ", ".join(a for a in info.get("aliases", []) if a != name)
            alias_str = f"  ({aliases})" if aliases else ""
            print(f"  {c('neon_pink', '★')} {c('neon', name)}{c('dim', alias_str)}")
        for name in orphan:
            print(f"  {c('yellow', '★')} {name}  {c('dim', '(not in registry)')}")
        print()
        print(c("dim", "  swe star <name|alias>   toggle  ·  swe unstar <name>  remove\n"))
        return

    if args[0] in ("clear", "--clear"):
        from quiver.harness.stars import save_stars

        save_stars([])
        print(f"  {c('green', '✓')} Cleared all stars.")
        return

    if args[0] in ("list", "ls"):
        return cmd_star([])

    key = args[0]
    name = resolve(tools, key)
    if not name:
        print(c("red", f"  Tool '{key}' not found. Try 'swe list'."))
        return

    now_starred = toggle_star(name)
    if now_starred:
        print(f"  {c('neon_pink', '★')} Starred {c('neon', name)} — pinned to top of {c('cyan', 'swe list')}")
    else:
        print(f"  {c('dim', '☆')} Unstarred {name}")


def cmd_unstar(args):
    if not args:
        print(c("red", "Usage: swe unstar <name|alias>"))
        return
    tools = load_registry()
    name = resolve(tools, args[0])
    if not name:
        # Allow unstarring orphans by raw name
        name = args[0]
        if not is_starred(name):
            print(c("red", f"  Tool '{args[0]}' not found / not starred."))
            return
    if unstar(name):
        print(f"  {c('green', '✓')} Unstarred '{name}'")
    else:
        print(c("dim", f"  '{name}' was not starred."))


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
    from quiver.harness.path_health import find_off_path_tools, preferred_npm_bin

    tools = load_registry()
    updated = False
    off_path_notes: list[str] = []
    print(f"\n{c('bold', 'Checking AI tools...')}\n")
    for name, info in sorted(tools.items()):
        aliases = [a for a in info.get("aliases", []) if a != name]
        alias_str = f"  ({', '.join(aliases)})" if aliases else ""
        command = info["command"]
        if is_installed(command):
            ver = live_version(command)
            if not ver:
                # Fall back to sanitizing whatever is already stored
                ver = extract_version_number(str(info.get("version") or ""))
            stored = info.get("version")
            if ver and ver != stored:
                tools[name]["version"] = ver
                updated = True
            elif not ver and stored:
                # Drop dirty banners/errors that aren't bare version numbers
                if extract_version_number(str(stored)) != stored:
                    tools[name]["version"] = None
                    updated = True
            display = tools[name].get("version") or "version unknown"
            print(f"  {c('green', '✓')}  {name:<22}{c('cyan', alias_str):<20} {c('dim', display)}")
        else:
            print(f"  {c('red', '✗')}  {name:<22}{c('dim', alias_str):<20} {c('dim', 'not installed')}")

    orphans = find_off_path_tools(tools)
    if orphans:
        print(f"\n{c('yellow', 'Off-PATH installs detected')} {c('dim', '(installed but invisible to swe)')}\n")
        npm = preferred_npm_bin() or "npm"
        for name, command, hit in orphans:
            print(f"  {c('yellow', '!')}  {name:<16} found at {c('dim', hit.path)}")
            print(c("dim", f"      source: {hit.source}  ·  not on current PATH"))
            print(c("dim", f"      fix: {npm} install -g {name}   # or: swe install {name}"))
            print(c("dim", f"      or:  swe edit {name} --command {hit.path}"))
            off_path_notes.append(name)
        print()

    if updated:
        save_registry(tools)
        print(c("dim", "  Registry updated."))
    if off_path_notes:
        print(c("dim", f"  Tip: run {c('cyan', 'swe doctor')} for full Node/PATH diagnosis."))
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


def cmd_doctor(args):
    """Diagnose Node/PATH mismatches that hide globally installed harnesses."""
    from quiver.harness.path_health import (
        find_off_path_tools,
        is_dir_on_path,
        nvm_bin_dirs,
        preferred_npm_bin,
        probe_node_env,
    )

    tools = load_registry()
    env = probe_node_env()
    home = Path.home()

    print(f"\n{c('bold', 'swe doctor')} — environment health\n")

    print(c("bold", "  Node / npm"))
    print(f"    node:         {env.node or c('red', 'not found')}"
          + (f"  {c('dim', '(' + env.node_version + ')')}" if env.node_version else ""))
    print(f"    npm:          {env.npm or c('red', 'not found')}"
          + (f"  {c('dim', '(' + env.npm_version + ')')}" if env.npm_version else ""))
    if env.global_prefix:
        on = c("green", "on PATH") if env.global_bin_on_path else c("red", "NOT on PATH")
        print(f"    global prefix:{' ' if True else ''} {env.global_prefix}")
        print(f"    global bin:    {env.global_bin or '—'}  ({on})")
    preferred = preferred_npm_bin()
    which_npm = shutil.which("npm")
    if preferred and which_npm and Path(preferred).resolve() != Path(which_npm).resolve():
        print(c("yellow", f"    note: which npm → {which_npm}; swe prefers {preferred}"))
    print()

    print(c("bold", "  nvm"))
    nvm_dirs = nvm_bin_dirs(home)
    nvm_root = Path(os.environ.get("NVM_DIR", home / ".nvm"))
    if not nvm_root.exists():
        print(c("dim", "    not installed (or NVM_DIR unset)"))
    else:
        print(f"    NVM_DIR:      {nvm_root}")
        print(f"    node bins:    {len(nvm_dirs)} version(s)")
        on_path_nvm = [d for d in nvm_dirs if is_dir_on_path(d)]
        if on_path_nvm:
            print(c("green", f"    on PATH:      {on_path_nvm[0]}"))
        else:
            print(c("yellow", "    on PATH:      none — nvm globals are invisible to swe/non-interactive shells"))
            if nvm_dirs:
                print(c("dim", f"    latest bin:   {nvm_dirs[-1]}"))
    print()

    print(c("bold", "  Registry tools"))
    n_inst = sum(1 for i in tools.values() if is_installed(i.get("command", "")))
    print(f"    registered:   {len(tools)}")
    print(f"    on PATH:      {c('green', str(n_inst))}/{len(tools)}")
    orphans = find_off_path_tools(tools)
    if orphans:
        print(f"    off-PATH:     {c('yellow', str(len(orphans)))}")
        print()
        print(c("yellow", "  Off-PATH installs (found on disk, not on PATH)"))
        for name, command, hit in orphans:
            print(f"    {c('yellow', '!')} {name}  ({command})")
            print(c("dim", f"        {hit.path}  [{hit.source}]"))
            print(c("dim", f"        fix: swe install {name}"))
            print(c("dim", f"         or: swe edit {name} --command {hit.path}"))
    else:
        print(c("dim", "    off-PATH:     none detected"))
    print()

    print(c("bold", "  Advice"))
    if not env.npm:
        print(c("red", "    • Install Node/npm, or put npm on PATH."))
    elif env.global_bin and not env.global_bin_on_path:
        print(c("yellow", f"    • Add npm global bin to PATH: export PATH=\"{env.global_bin}:$PATH\""))
    if nvm_dirs and not any(is_dir_on_path(d) for d in nvm_dirs):
        print(c("yellow", "    • Avoid `npm install -g` under nvm unless nvm is always on PATH."))
        print(c("dim", "      Prefer: swe install <name>   (uses PATH-visible Homebrew npm when available)"))
    if orphans:
        print(c("yellow", "    • Reinstall off-PATH tools with: swe install <name>"))
    if not orphans and env.global_bin_on_path:
        print(c("green", "    • Environment looks healthy for swe."))
    print()
    print(c("dim", "  Related: swe check  ·  swe install <name>  ·  swe help doctor\n"))
    return 1 if orphans or (env.global_bin and not env.global_bin_on_path) else 0


def cmd_install(args):
    """Install a harness via PATH-visible npm and register/update it in swe."""
    from quiver.harness.catalog import HARNESS_CATALOG
    from quiver.harness.path_health import preferred_npm_bin, resolve_npm_package
    from quiver.harness.tools import live_version

    if not args:
        print(c("red", "Usage: swe install <name|npm-package> [--package <pkg>] [--command <cmd>]"))
        print(c("dim", "  Example: swe install mastracode"))
        print(c("dim", "           swe install jules --package @google/jules"))
        return 1

    name = args[0]
    package = None
    command = None
    dry_run = False
    i = 1
    while i < len(args):
        if args[i] == "--package" and i + 1 < len(args):
            package = args[i + 1]
            i += 2
        elif args[i] == "--command" and i + 1 < len(args):
            command = args[i + 1]
            i += 2
        elif args[i] in ("--dry-run", "-n"):
            dry_run = True
            i += 1
        else:
            print(c("red", f"Unknown argument: {args[i]}"))
            return 1

    npm = preferred_npm_bin()
    if not npm:
        print(c("red", "  No npm found on PATH. Install Node or fix PATH first (see swe doctor)."))
        return 1

    tools = load_registry()
    # Allow installing by alias
    resolved = resolve(tools, name)
    reg_name = resolved or name

    catalog = HARNESS_CATALOG.get(reg_name, {})
    npm_pkg = resolve_npm_package(reg_name, package)
    # If user passed a scoped package as name
    if name.startswith("@") and "/" in name and not package:
        npm_pkg = name
        reg_name = name.split("/")[-1]
        catalog = HARNESS_CATALOG.get(reg_name, {})

    cmd_name = command or catalog.get("command") or (tools.get(reg_name, {}) or {}).get("command") or reg_name
    desc = catalog.get("description") or (tools.get(reg_name, {}) or {}).get("description") or ""
    tags = list(catalog.get("tags") or (tools.get(reg_name, {}) or {}).get("tags") or ["agentic", "coding"])
    aliases = list(catalog.get("aliases") or (tools.get(reg_name, {}) or {}).get("aliases") or [])

    print(f"\n{c('bold', 'swe install')}\n")
    print(f"  name:     {reg_name}")
    print(f"  package:  {npm_pkg}")
    print(f"  command:  {cmd_name}")
    print(f"  npm:      {npm}")
    print()

    if dry_run:
        print(c("dim", f"  dry-run: would run  {npm} install -g {npm_pkg}"))
        return 0

    print(c("dim", f"  → {npm} install -g {npm_pkg}\n"))
    try:
        result = subprocess.run(
            [npm, "install", "-g", npm_pkg],
            capture_output=False,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print(c("red", "  npm install timed out."))
        return 1
    except Exception as exc:
        print(c("red", f"  npm install failed: {exc}"))
        return 1

    if result.returncode != 0:
        print(c("red", f"  npm install exited with code {result.returncode}"))
        print(c("dim", "  Tip: if the package name differs, try --package <npm-name>"))
        return result.returncode

    # Re-hash PATH resolution
    installed_path = shutil.which(cmd_name)
    if not installed_path:
        print(c("yellow", f"  npm finished, but '{cmd_name}' is still not on PATH."))
        print(c("dim", "  Run: swe doctor"))
        # Still register so user can fix path later
    else:
        print(f"  {c('green', '✓')} on PATH: {installed_path}")

    ver = live_version(cmd_name) if installed_path else None
    # Fallback: read package.json next to npm global module
    if not ver:
        try:
            import json as _json

            prefix = subprocess.run(
                [npm, "prefix", "-g"],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            ).stdout.strip()
            if npm_pkg.startswith("@"):
                scope, pname = npm_pkg.split("/", 1)
                pkg_json = Path(prefix) / "lib" / "node_modules" / scope / pname / "package.json"
            else:
                pkg_json = Path(prefix) / "lib" / "node_modules" / npm_pkg / "package.json"
            if pkg_json.is_file():
                raw_ver = _json.loads(pkg_json.read_text()).get("version") or ""
                ver = extract_version_number(raw_ver) or raw_ver or None
        except Exception:
            pass

    entry = {
        "command": cmd_name,
        "description": desc,
        "version": ver,
        "tags": tags,
        "aliases": aliases,
    }
    if reg_name in tools:
        # Preserve notes / added if present
        prev = tools[reg_name]
        for keep in ("notes", "added"):
            if keep in prev and keep not in entry:
                entry[keep] = prev[keep]
        entry["description"] = desc or prev.get("description", "")
        entry["tags"] = tags or prev.get("tags", [])
        entry["aliases"] = aliases or prev.get("aliases", [])
        action = "Updated"
    else:
        entry["added"] = datetime.now().isoformat()
        action = "Added"

    tools[reg_name] = entry
    save_registry(tools)
    status = c("green", "installed") if installed_path else c("yellow", "registered (not on PATH yet)")
    print(f"  {c('green', '✓')} {action} '{reg_name}' → '{cmd_name}' ({status})"
          + (f"  v{ver}" if ver else ""))
    print(c("dim", f"  Try: swe info {reg_name}  ·  swe use {reg_name}\n"))
    return 0 if installed_path else 1


EDITABLE_FIELDS = ("command", "description", "aliases", "tags", "version", "notes")


def _split_csv(value: str) -> list[str]:
    if value is None:
        return []
    items = [part.strip() for part in str(value).split(",")]
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _format_list(values) -> str:
    if not values:
        return "—"
    return ", ".join(values)


def _show_edit_fields(name: str, info: dict) -> None:
    print(f"\n  {c('bold', name)}")
    rows = [
        ("command", info.get("command", "")),
        ("description", info.get("description") or "—"),
        ("aliases", _format_list([a for a in info.get("aliases", []) if a != name])),
        ("tags", _format_list(info.get("tags", []))),
        ("version", info.get("version") or "—"),
        ("notes", info.get("notes") or "—"),
    ]
    for label, val in rows:
        print(f"  {'  ' + label + ':':<16} {val}")
    print()


def _parse_set_string(raw: str) -> dict:
    """Parse ``field=value`` pairs; list values may contain commas.

    Example: ``tags=agentic,coding,notes=hi`` → tags='agentic,coding', notes='hi'
    """
    pattern = re.compile(
        r"(?:^|,)\s*(" + "|".join(EDITABLE_FIELDS) + r")="
    )
    matches = list(pattern.finditer(raw))
    if not matches:
        raise ValueError(f"Invalid --set value '{raw}' (expected field=value)")
    updates: dict = {}
    for idx, match in enumerate(matches):
        field = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        updates[field] = raw[start:end].strip().rstrip(",")
    return updates


def _parse_edit_flags(args: list[str]) -> tuple[dict, list[str]]:
    """Return (updates, remaining_positional_args)."""
    updates: dict = {}
    rest: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--set" and i + 1 < len(args):
            updates.update(_parse_set_string(args[i + 1]))
            i += 2
            continue
        if arg.startswith("--") and arg[2:] in EDITABLE_FIELDS:
            field = arg[2:]
            if i + 1 >= len(args):
                raise ValueError(f"Missing value for --{field}")
            updates[field] = args[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            raise ValueError(f"Unknown flag: {arg}")
        rest.append(arg)
        i += 1
    return updates, rest


def _normalize_field_value(field: str, value):
    if field in ("aliases", "tags"):
        if isinstance(value, list):
            return _split_csv(",".join(str(v) for v in value))
        return _split_csv(value if value is not None else "")
    if field == "version":
        text = "" if value is None else str(value).strip()
        return text or None
    if field == "notes":
        text = "" if value is None else str(value).strip()
        return text or None
    return "" if value is None else str(value).strip()


def _alias_collision(tools: dict, name: str, aliases: list[str]) -> str | None:
    mapping = {}
    for other, info in tools.items():
        if other == name:
            continue
        mapping[other] = other
        for alias in info.get("aliases", []):
            mapping[alias] = other
    for alias in aliases:
        if alias == name:
            continue
        owner = mapping.get(alias)
        if owner:
            return f"Alias '{alias}' already used by '{owner}'"
    return None


def _apply_edits(tools: dict, name: str, updates: dict) -> tuple[dict, list[str]]:
    """Apply updates to a copy of the entry. Returns (new_info, change_lines)."""
    info = dict(tools[name])
    changes: list[str] = []

    for field, raw in updates.items():
        if field not in EDITABLE_FIELDS:
            raise ValueError(f"Unknown field '{field}'")
        new_val = _normalize_field_value(field, raw)
        old_val = info.get(field)
        if field in ("aliases", "tags"):
            old_norm = list(old_val or [])
            if field == "aliases":
                old_norm = [a for a in old_norm if a != name]
            if old_norm == new_val:
                continue
            if field == "aliases":
                conflict = _alias_collision(tools, name, new_val)
                if conflict:
                    raise ValueError(conflict)
            info[field] = new_val
            changes.append(f"{field}: {_format_list(old_norm)} → {_format_list(new_val)}")
            continue

        if field == "command":
            if not new_val:
                raise ValueError("command cannot be empty")
            old_disp = old_val or "—"
            if old_val == new_val:
                continue
            info[field] = new_val
            changes.append(f"command: {old_disp} → {new_val}")
            continue

        old_disp = old_val if old_val not in (None, "") else "—"
        new_disp = new_val if new_val not in (None, "") else "—"
        if old_val == new_val or (old_val in (None, "") and new_val in (None, "")):
            continue
        if new_val in (None, ""):
            info.pop(field, None)
            if field in ("description",):
                info[field] = ""
        else:
            info[field] = new_val
        changes.append(f"{field}: {old_disp} → {new_disp}")

    return info, changes


def _edit_interactive(name: str, info: dict, tools: dict) -> dict | None:
    """Interactive field editor. Returns updates dict, or None if cancelled."""
    draft = dict(info)
    updates: dict = {}
    print(c("dim", "  Edit fields: command, description, aliases, tags, version, notes"))
    print(c("dim", "  Commands:  save  |  quit  |  show  |  <field>"))
    print(c("dim", "  Blank value keeps current. For lists, use commas (e.g. mc,ms).\n"))

    while True:
        try:
            choice = read_line(c("cyan", "  field> ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print(c("dim", "  Cancelled."))
            return None

        if not choice:
            continue
        if choice in ("quit", "q", "exit", "cancel"):
            print(c("dim", "  Cancelled."))
            return None
        if choice in ("show", "print", "fields"):
            # Merge pending updates for display
            preview = dict(draft)
            preview.update({k: _normalize_field_value(k, v) for k, v in updates.items()})
            _show_edit_fields(name, preview)
            continue
        if choice in ("save", "s", "done", "write"):
            if "aliases" in updates:
                new_aliases = _normalize_field_value("aliases", updates["aliases"])
                conflict = _alias_collision(tools, name, new_aliases)
                if conflict:
                    print(c("yellow", f"  ⚠ {conflict}"))
                    print(c("dim", "  Enter a different alias, then save again."))
                    continue
            return updates
        if choice not in EDITABLE_FIELDS:
            print(c("red", f"  Unknown field/command: {choice}"))
            print(c("dim", f"  Fields: {', '.join(EDITABLE_FIELDS)}"))
            continue

        current = draft.get(choice)
        if choice in ("aliases", "tags"):
            cur_list = list(current or [])
            if choice == "aliases":
                cur_list = [a for a in cur_list if a != name]
            current_disp = ", ".join(cur_list)
        else:
            current_disp = "" if current is None else str(current)

        print(c("dim", f"  current {choice}: {current_disp or '—'}"))
        try:
            new_raw = read_line(c("cyan", f"  new {choice}> "))
        except (EOFError, KeyboardInterrupt):
            print()
            print(c("dim", "  Cancelled."))
            return None

        if new_raw.strip() == "" and new_raw != "":
            # whitespace-only treated as clear for text? keep simple: blank keep
            continue
        if new_raw == "":
            print(c("dim", "  (kept)"))
            continue

        updates[choice] = new_raw
        normalized = _normalize_field_value(choice, new_raw)
        draft[choice] = normalized if normalized is not None else ""
        if choice in ("aliases", "tags"):
            print(c("green", f"  set {choice} = {_format_list(normalized)}"))
        else:
            print(c("green", f"  set {choice} = {normalized if normalized not in (None, '') else '—'}"))


def cmd_edit(args):
    """Edit harness registry fields (flags or interactive)."""
    if not args:
        print(c("red", "Usage: swe edit <name|alias> [--field value ...]"))
        print(c("dim", "  Interactive: swe edit mastracode"))
        print(c("dim", "  Flags:       swe edit mastracode --description '...' --aliases mc"))
        return 1

    try:
        updates, rest = _parse_edit_flags(args)
    except ValueError as exc:
        print(c("red", f"  {exc}"))
        return 1

    if not rest:
        print(c("red", "Usage: swe edit <name|alias> [--field value ...]"))
        return 1

    key = rest[0]
    if rest[1:]:
        print(c("red", f"Unexpected arguments: {' '.join(rest[1:])}"))
        return 1

    tools = load_registry()
    name = resolve(tools, key)
    if not name:
        print(c("red", f"  Tool '{key}' not found. Try 'swe list'."))
        return 1

    info = tools[name]
    _show_edit_fields(name, info)

    if not updates:
        interactive = _edit_interactive(name, info, tools)
        if interactive is None:
            return 1
        updates = interactive

    if not updates:
        print(c("dim", "  No changes."))
        return 0

    try:
        new_info, changes = _apply_edits(tools, name, updates)
    except ValueError as exc:
        print(c("red", f"  {exc}"))
        return 1

    if not changes:
        print(c("dim", "  No changes."))
        return 0

    tools[name] = new_info
    save_registry(tools)
    print(f"  {c('green', '✓')} Updated '{name}'")
    for line in changes:
        print(c("dim", f"    · {line}"))
    print()
    return 0
