#!/usr/bin/env python3
"""swe mcp — MCP server manager for AI coding tools.

Usage: swe mcp <command> [args] [--help]

Commands:
  list [tool]                  Matrix view of MCP servers across tools
  status [tool]                List with health checks
  sync <source> <target...>    Copy servers source → target(s)
  diff <tool1> <tool2>         Compare two tools' configs
  edit <tool> <name>           Edit one server config in one tool
  validate [tool...]           Validate MCP config shape for one/all tools
  doctor                       Deep diagnostics

Run 'swe mcp <command> help' for detailed help on each command.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tty
import termios
from pathlib import Path

from quiver import CONFIG_DIR_NAME
from quiver.mcp_formats import (
    McpFormatHandler,
    convert_server_between_formats,
    get_conversion_issues,
    get_format_handler,
    normalize_server as normalize_server_any,
)

CONFIG_DIR = Path.home() / ".config" / CONFIG_DIR_NAME
REGISTRY_FILE = CONFIG_DIR / "tools.json"

COLORS = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "red":    "\033[31m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "blue":   "\033[34m",
    "cyan":   "\033[36m",
}

def c(color: str, text: str) -> str:
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def cpad(color: str, text: str, width: int) -> str:
    """Pad a colored string to a visual width."""
    visible_len = len(text)
    padding = max(0, width - visible_len)
    return f"{c(color, text)}{' ' * padding}"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

def getch():
    """Read a single character from stdin."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch

def interactive_select(
    items: list[str],
    headers: list[str] = None,
    source_label: str = "",
    target_label: str = "",
    preselected: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Interactive multi-select with arrow keys and spacebar.

    Args:
        items: list of item names to select
        headers: column headers
        source_label: label for source column
        target_label: label for target column
        preselected: initial checked items; defaults to all items

    Returns:
        list of selected item names
    """
    selected = set(items) if preselected is None else (set(preselected) & set(items))
    cursor = 0

    def render():
        # Move cursor up to redraw
        if hasattr(render, 'last_lines'):
            sys.stdout.write(f"\033[{render.last_lines}A")

        lines = []
        if source_label and target_label:
            lines.append(f"  Copy from {c('cyan', source_label)} → {c('cyan', target_label)}")
            lines.append("")

        for i, item in enumerate(items):
            prefix = "  "
            if i == cursor:
                prefix = c("cyan", "▸ ")
            check = c("green", "✓") if item in selected else c("dim", "·")
            lines.append(f"{prefix}[{check}] {item}")

        lines.append("")
        lines.append(f"  {c('dim', '↑↓ navigate   Space toggle   a select all   n select none   Enter confirm')}")

        for line in lines:
            sys.stdout.write(f"\033[2K{line}\n")
        sys.stdout.flush()
        render.last_lines = len(lines)

    render.last_lines = 0
    render()

    while True:
        ch = getch()

        if ch == '\x1b':  # escape sequence
            ch2 = getch()
            if ch2 == '[':
                ch3 = getch()
                if ch3 == 'A':  # up
                    cursor = max(0, cursor - 1)
                elif ch3 == 'B':  # down
                    cursor = min(len(items) - 1, cursor + 1)
        elif ch == ' ':  # spacebar toggle
            item = items[cursor]
            if item in selected:
                selected.discard(item)
            else:
                selected.add(item)
        elif ch == 'a':  # select all
            selected = set(items)
        elif ch == 'n':  # select none
            selected = set()
        elif ch == '\r' or ch == '\n':  # enter
            sys.stdout.write("\n")
            return [item for item in items if item in selected]
        elif ch == '\x03':  # ctrl-c
            sys.stdout.write("\n")
            return []

        render()

# Maps canonical tool name → MCP config location.
# Tools not listed here are skipped by mcp commands.
MCP_CONFIG_MAP = {
    "claude": {
        "path": Path.home() / ".claude.json",
        "key": "mcpServers",
        "label": "Claude Code",
    },
    "claude-desktop": {
        "path": Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "key": "mcpServers",
        "label": "Claude Desktop",
    },
    "cursor": {
        "path": Path.home() / ".cursor" / "mcp.json",
        "key": "mcpServers",
        "label": "Cursor",
    },
    "copilot": {
        "path": Path.home() / ".copilot" / "mcp-config.json",
        "key": "mcpServers",
        "label": "Copilot",
        "format": "copilot",
    },
    "cline": {
        "path": Path.home() / ".cline" / "data" / "settings" / "cline_mcp_settings.json",
        "key": "mcpServers",
        "label": "Cline",
    },
    "lmstudio": {
        "path": Path.home() / ".lmstudio" / "mcp.json",
        "key": "mcpServers",
        "label": "LM Studio",
    },
    "gemini": {
        "path": Path.home() / ".gemini" / "antigravity" / "mcp_config.json",
        "key": "mcpServers",
        "label": "Gemini",
    },
    "opencode": {
        "path": Path.home() / ".config" / "opencode" / "opencode.json",
        "key": "mcp",
        "label": "opencode",
        "format": "opencode",
    },
}

# Alias for claude-desktop that isn't in tools.json
_EXTRA_ALIASES = {
    "claude-desktop": "claude-desktop",
    "cd": "claude-desktop",
}


# ── Registry & alias resolution (mirrors swe.py) ─────────────────────


def load_registry() -> dict:
    """Load tools.json registry."""
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def alias_map(registry: dict) -> dict:
    """Return {alias_or_name: canonical_name} for every tool."""
    amap = {}
    for name, info in registry.items():
        amap[name] = name
        for a in info.get("aliases", []):
            amap[a] = name
    # Add extras not in registry
    amap.update(_EXTRA_ALIASES)
    return amap


def resolve(registry: dict, key: str) -> str | None:
    """Resolve a name or alias to canonical name."""
    return alias_map(registry).get(key)


def get_mcp_tools(registry: dict) -> dict:
    """Return {canonical_name: mcp_config} for tools that have MCP support."""
    amap = alias_map(registry)
    result = {}
    for name, mcp_cfg in MCP_CONFIG_MAP.items():
        # Check if this tool is in the registry (or is an extra like claude-desktop)
        if name in registry or name in _EXTRA_ALIASES:
            result[name] = mcp_cfg
        else:
            # Check if it's an alias that resolves to something in registry
            resolved = amap.get(name)
            if resolved and resolved in MCP_CONFIG_MAP:
                result[resolved] = MCP_CONFIG_MAP[resolved]
    return result


# ── JSON helpers ──────────────────────────────────────────────────────


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(path)


# ── Server helpers ────────────────────────────────────────────────────


def get_tool_servers(tool_name: str) -> dict:
    cfg = MCP_CONFIG_MAP.get(tool_name)
    if not cfg:
        return {}
    data = load_json(cfg["path"])
    return data.get(cfg["key"], {})


def get_tool_format(tool_name: str) -> str:
    return MCP_CONFIG_MAP.get(tool_name, {}).get("format", "standard")


def get_mcp_handler(tool_name: str) -> McpFormatHandler:
    return get_format_handler(get_tool_format(tool_name))


def get_tool_servers_canonical(tool_name: str) -> dict:
    """Return {server_name: canonical_server_dict} for a tool."""
    handler = get_mcp_handler(tool_name)
    out = {}
    for name, raw_cfg in get_tool_servers(tool_name).items():
        if isinstance(raw_cfg, dict):
            out[name] = handler.parse(raw_cfg)
    return out


def normalize_server(cfg: dict) -> dict:
    """Normalize mixed tool formats to canonical standard MCP shape."""
    return normalize_server_any(cfg)


def convert_server_for_target(cfg: dict, source_tool: str, target_tool: str) -> dict:
    """Convert server config between tool-specific MCP formats via handlers."""
    return convert_server_between_formats(
        cfg,
        source_format=get_tool_format(source_tool),
        target_format=get_tool_format(target_tool),
    )


def server_type(cfg: dict) -> str:
    cfg = normalize_server(cfg)
    if cfg.get("url"):
        return "http"
    if cfg.get("command"):
        if "mcp-remote" in cfg.get("args", []):
            return "wrapped"
        return "stdio"
    return "unknown"


def server_summary(cfg: dict) -> str:
    cfg = normalize_server(cfg)
    st = server_type(cfg)
    if st == "http":
        return f"http → {cfg.get('url', '?')}"
    if st == "stdio":
        cmd = cfg.get("command", "?")
        args = cfg.get("args", [])
        parts = [cmd]
        for a in args:
            if not a.startswith("-"):
                parts.append(a)
                break
        return " ".join(parts)
    if st == "wrapped":
        args = cfg.get("args", [])
        url = next((a for a in args if a.startswith("http")), "?")
        return f"wrapped → {url}"
    return "unknown"


def check_server_health(name: str, cfg: dict) -> str:
    cfg = normalize_server(cfg)
    st = server_type(cfg)

    if st == "http":
        url = cfg.get("url", "")
        try:
            import urllib.request
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, timeout=5)
            return c("green", "✓")
        except Exception:
            return c("red", "✗ url unreachable")

    if st == "stdio":
        cmd = cfg.get("command", "")
        if cmd.startswith("/") or cmd.startswith("~"):
            resolved = os.path.expanduser(cmd)
            if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
                pass
            else:
                return c("red", "✗ binary not found")
        else:
            if not shutil.which(cmd):
                return c("red", f"✗ '{cmd}' not in PATH")
        env = cfg.get("env", {})
        missing = [k for k, v in env.items() if not v or v.startswith("YOUR_")]
        if missing:
            return c("red", f"✗ missing env: {', '.join(missing)}")
        return c("green", "✓")

    if st == "wrapped":
        args = cfg.get("args", [])
        url = next((a for a in args if a.startswith("http")), None)
        if url:
            try:
                import urllib.request
                req = urllib.request.Request(url, method="HEAD")
                urllib.request.urlopen(req, timeout=5)
                return c("green", "✓")
            except Exception:
                return c("red", "✗ url unreachable")
        return c("red", "✗ no url found")

    return c("yellow", "? unknown")


def resolve_tool_arg(registry: dict, arg: str) -> str | None:
    """Resolve a CLI arg to a canonical tool name that has MCP support."""
    resolved = resolve(registry, arg)
    if resolved and resolved in MCP_CONFIG_MAP:
        return resolved
    # Also check direct MCP_CONFIG_MAP keys (e.g. claude-desktop)
    if arg in MCP_CONFIG_MAP:
        return arg
    return None


# ── Subcommands ──────────────────────────────────────────────────────


def cmd_list(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["list"])
        return 0
    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)

    target = None
    if args:
        target = resolve_tool_arg(registry, args[0])
        if not target:
            print(f"Unknown tool: {args[0]}")
            print(f"Available: {', '.join(mcp_tools.keys())}")
            return 1

    tools = {target: mcp_tools[target]} if target else mcp_tools

    all_servers = set()
    tool_data = {}
    for name in tools:
        servers = get_tool_servers(name)
        tool_data[name] = servers
        all_servers.update(servers.keys())

    if not all_servers:
        print("No MCP servers found.")
        return 0

    sorted_servers = sorted(all_servers)
    tool_names = list(tools.keys())
    col_width = max(len(n) for n in tool_names + ["SERVER"])
    server_width = max(len(n) for n in sorted_servers)

    header = f"{'SERVER':<{server_width}}"
    for t in tool_names:
        header += f"  {t:^{col_width}}"
    print(header)
    print("─" * len(header))

    for server in sorted_servers:
        row = f"{server:<{server_width}}"
        for t in tool_names:
            if server in tool_data[t]:
                row += f"  {cpad('green', '✓', col_width)}"
            else:
                row += f"  {cpad('dim', '—', col_width)}"
        print(row)

    print(f"\n{len(sorted_servers)} servers across {len(tool_names)} tools")
    return 0


def cmd_status(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["status"])
        return 0
    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)

    target = None
    if args:
        target = resolve_tool_arg(registry, args[0])
        if not target:
            print(f"Unknown tool: {args[0]}")
            return 1

    tools = {target: mcp_tools[target]} if target else mcp_tools

    all_servers = set()
    tool_data = {}
    for name in tools:
        servers = get_tool_servers(name)
        tool_data[name] = servers
        all_servers.update(servers.keys())

    if not all_servers:
        print("No MCP servers found.")
        return 0

    sorted_servers = sorted(all_servers)
    tool_names = list(tools.keys())
    col_width = max(len(n) for n in tool_names + ["SERVER"])
    server_width = max(len(n) for n in sorted_servers)
    health_width = 20

    header = f"{'SERVER':<{server_width}}"
    for t in tool_names:
        header += f"  {t:^{col_width}}"
    header += f"  {'HEALTH':<{health_width}}"
    print(header)
    print("─" * len(header))

    for server in sorted_servers:
        row = f"{server:<{server_width}}"
        first_cfg = None
        for t in tool_names:
            if server in tool_data[t]:
                row += f"  {cpad('green', '✓', col_width)}"
                if first_cfg is None:
                    first_cfg = tool_data[t][server]
            else:
                row += f"  {cpad('dim', '—', col_width)}"
        if first_cfg:
            health = check_server_health(server, first_cfg)
            row += f"  {health:<{health_width}}"
        print(row)

    print(f"\n{len(sorted_servers)} servers across {len(tool_names)} tools")
    return 0


def cmd_sync(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["sync"])
        return 0

    allowed_flags = {"--force", "--skip-conflicts", "--all", "--no-interactive", "--dry-run", "--strict"}
    unknown_flags = [a for a in args if a.startswith("--") and not a.startswith("--only=") and a not in allowed_flags]
    if unknown_flags:
        print(f"Unknown flag(s): {', '.join(unknown_flags)}")
        return 1

    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)

    force = "--force" in args
    skip_conflicts = "--skip-conflicts" in args
    all_targets = "--all" in args
    no_interactive = "--no-interactive" in args
    dry_run = "--dry-run" in args
    strict = "--strict" in args

    only_flag = None
    for a in args:
        if a.startswith("--only="):
            only_flag = [s for s in a.split("=", 1)[1].split(",") if s]

    positional = [a for a in args if not a.startswith("--")]

    if not positional or (len(positional) < 2 and not all_targets):
        print(f"Usage: {c('cyan', 'swe mcp sync <source> <target...> [--only=a,b] [--force|--skip-conflicts] [--dry-run] [--strict]')}")
        print(f"       {c('cyan', 'swe mcp sync <source> --all [--only=a,b] [--force|--skip-conflicts] [--dry-run] [--strict]')}")
        print(f"\nRun {c('cyan', 'swe mcp sync help')} for details.")
        return 1

    source = resolve_tool_arg(registry, positional[0])
    if not source:
        print(f"Unknown source tool: {positional[0]}")
        return 1

    if all_targets:
        targets = [t for t in mcp_tools if t != source]
    else:
        targets = []
        for a in positional[1:]:
            resolved = resolve_tool_arg(registry, a)
            if resolved:
                targets.append(resolved)
            else:
                print(f"Unknown target tool: {a}")
                return 1
        targets = [t for i, t in enumerate(targets) if t != source and t not in targets[:i]]

    if not targets:
        print("No valid target tools provided.")
        return 1

    source_servers = get_tool_servers(source)
    if not source_servers:
        print(f"{source} has no MCP servers.")
        return 0

    if only_flag:
        source_servers = {k: v for k, v in source_servers.items() if k in only_flag}
        if not source_servers:
            print(f"No matching servers found in {source}.")
            return 0

    server_names = sorted(source_servers.keys())
    target_server_names = set()
    for target in targets:
        target_server_names.update(get_tool_servers(target).keys())

    interactive = (not no_interactive) and sys.stdin.isatty() and sys.stdout.isatty()

    if interactive:
        print(f"\n  Select servers to copy from {c('cyan', source)}:\n")
        selected = interactive_select(
            server_names,
            source_label=source,
            target_label=", ".join(targets),
            preselected=target_server_names,
        )
    else:
        selected = server_names
        print(f"{c('dim', 'Non-interactive mode: selecting all eligible servers.')}")

    if not selected:
        print("Nothing selected.")
        return 0

    print(f"\n{c('green', f'Selected {len(selected)} server(s)')} from {c('cyan', source)}")
    if dry_run:
        print(f"{c('yellow', 'Dry-run mode: no files will be modified.')}\n")

    if strict:
        source_format = get_tool_format(source)
        strict_errors = []
        for target in targets:
            target_format = get_tool_format(target)
            for name in selected:
                issues = get_conversion_issues(source_servers[name], source_format, target_format)
                if issues:
                    strict_errors.append((target, name, issues))

        if strict_errors:
            print(c("red", "Strict mode blocked sync due to lossy conversion:"))
            for target, name, issues in strict_errors[:20]:
                print(f"  {c('cyan', target)}:{name} -> " + "; ".join(issues))
            if len(strict_errors) > 20:
                print(f"  ... and {len(strict_errors) - 20} more")
            print(c("dim", "Tip: rerun without --strict to proceed."))
            return 1

    for target in targets:
        target_cfg = MCP_CONFIG_MAP[target]
        target_data = load_json(target_cfg["path"])
        target_servers = target_data.get(target_cfg["key"], {})

        conflicts = [s for s in selected if s in target_servers]
        overwrite = set()

        if conflicts:
            if force:
                overwrite = set(conflicts)
            elif skip_conflicts:
                overwrite = set()
            elif interactive:
                print(f"\n  {c('yellow', f'{len(conflicts)} conflict(s)')} with {c('cyan', target)}:\n")
                for name in conflicts:
                    src_summary = server_summary(source_servers[name])
                    tgt_summary = server_summary(target_servers[name])
                    print(f"    {c('bold', name)}")
                    print(f"      {source}: {c('dim', src_summary)}")
                    print(f"      {target}: {c('dim', tgt_summary)}")

                print(f"\n  Overwrite conflicting servers in {c('cyan', target)}?")
                overwrite = set(interactive_select(
                    conflicts,
                    source_label=source,
                    target_label=target,
                ))
            else:
                print(f"{c('yellow', f'{target}: {len(conflicts)} conflict(s) skipped in non-interactive mode (use --force to overwrite).')}")

        added = 0
        updated = 0
        skipped = 0

        for name in selected:
            converted = convert_server_for_target(source_servers[name], source, target)
            if name in target_servers:
                if force or name in overwrite:
                    target_servers[name] = converted
                    updated += 1
                else:
                    skipped += 1
            else:
                target_servers[name] = converted
                added += 1

        if (added or updated) and not dry_run:
            target_data[target_cfg["key"]] = target_servers
            save_json(target_cfg["path"], target_data)

        parts = []
        if added:
            parts.append(f"{added} {'would add' if dry_run else 'added'}")
        if updated:
            parts.append(f"{updated} {'would update' if dry_run else 'updated'}")
        if skipped:
            parts.append(f"{skipped} {'would skip' if dry_run else 'skipped'}")

        if parts:
            status = c('cyan', '•') if dry_run else c('green', '✓')
            print(f"\n  {status} {target}: {', '.join(parts)}")
        else:
            print(f"\n  {target}: nothing changed")

    return 0


def cmd_diff(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["diff"])
        return 0
    registry = load_registry()

    if len(args) < 2:
        print("Usage: swe mcp diff <tool1> <tool2>")
        return 1

    t1 = resolve_tool_arg(registry, args[0])
    t2 = resolve_tool_arg(registry, args[1])
    for t, a in [(t1, args[0]), (t2, args[1])]:
        if not t:
            print(f"Unknown tool: {a}")
            return 1

    s1 = get_tool_servers(t1)
    s2 = get_tool_servers(t2)
    all_names = sorted(set(s1.keys()) | set(s2.keys()))

    if not all_names:
        print("No servers in either tool.")
        return 0

    width = max(len(n) for n in all_names)
    print(f"{'SERVER':<{width}}  {t1:^12}  {t2:^12}  DIFF")
    print("─" * (width + 40))

    only1, only2, both, different = [], [], [], []
    for name in all_names:
        in1, in2 = name in s1, name in s2
        if in1 and not in2:
            only1.append(name)
            print(f"{name:<{width}}  {cpad('green', '✓', 12)}  {cpad('dim', '—', 12)}  only in {t1}")
        elif in2 and not in1:
            only2.append(name)
            print(f"{name:<{width}}  {cpad('dim', '—', 12)}  {cpad('green', '✓', 12)}  only in {t2}")
        else:
            both.append(name)
            if s1[name] == s2[name]:
                print(f"{name:<{width}}  {cpad('green', '✓', 12)}  {cpad('green', '✓', 12)}  identical")
            else:
                different.append(name)
                print(f"{name:<{width}}  {cpad('green', '✓', 12)}  {cpad('green', '✓', 12)}  {c('yellow', 'DIFFERENT')}")

    print(f"\nOnly in {t1}: {c('cyan', str(len(only1)))}")
    print(f"Only in {t2}: {c('cyan', str(len(only2)))}")
    print(f"In both: {c('cyan', str(len(both)))} ({c('yellow', str(len(different)))} differ)")
    return 0


def cmd_edit(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["edit"])
        return 0
    if len(args) < 2:
        print("Usage: swe mcp edit <tool> <name>")
        return 1

    registry = load_registry()
    tool = resolve_tool_arg(registry, args[0])
    if not tool:
        print(f"Unknown tool: {args[0]}")
        return 1

    name = args[1]
    tool_cfg = MCP_CONFIG_MAP[tool]
    tool_data = load_json(tool_cfg["path"])
    tool_servers = tool_data.get(tool_cfg["key"], {})

    if name not in tool_servers:
        print(f"'{name}' not found in {tool}.")
        return 1

    editor = os.environ.get("EDITOR", "vim")
    tmp_file = CONFIG_DIR / ".mcp-edit-tmp.json"
    tmp_file.write_text(json.dumps({name: tool_servers[name]}, indent=2) + "\n")

    subprocess.run([editor, str(tmp_file)])

    try:
        edited = json.loads(tmp_file.read_text())
        if name in edited and isinstance(edited[name], dict):
            tool_servers[name] = edited[name]
            tool_data[tool_cfg["key"]] = tool_servers
            save_json(tool_cfg["path"], tool_data)
            print(f"Updated '{name}' in {tool}")
        else:
            print("Server name removed from edit — no changes.")
    except json.JSONDecodeError:
        print("Invalid JSON — no changes saved.")

    tmp_file.unlink(missing_ok=True)
    return 0


def cmd_validate(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["validate"])
        return 0

    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)

    targets = []
    if args:
        for a in args:
            t = resolve_tool_arg(registry, a)
            if not t:
                print(f"Unknown tool: {a}")
                return 1
            if t not in targets:
                targets.append(t)
    else:
        targets = list(mcp_tools.keys())

    if not targets:
        print("No MCP-capable tools found.")
        return 1

    total_servers = 0
    total_errors = 0

    for tool in targets:
        fmt = get_tool_format(tool)
        handler = get_mcp_handler(tool)
        servers = get_tool_servers(tool)

        print(f"{tool} ({fmt})")
        if not servers:
            print(f"  {c('dim', '— no servers configured')}")
            print()
            continue

        for name, raw_cfg in sorted(servers.items()):
            total_servers += 1
            issues = []

            if not isinstance(raw_cfg, dict):
                issues.append("server config is not an object")
            else:
                canonical = handler.parse(raw_cfg)
                if raw_cfg and not canonical:
                    issues.append("could not parse tool format")
                issues.extend(get_conversion_issues(raw_cfg, fmt, fmt))

            # de-duplicate while preserving order
            seen = set()
            deduped = []
            for i in issues:
                if i not in seen:
                    seen.add(i)
                    deduped.append(i)

            if deduped:
                total_errors += 1
                print(f"  {c('red', '✗')} {name}: {c('yellow', '; '.join(deduped))}")
            else:
                print(f"  {c('green', '✓')} {name}")
        print()

    if total_errors:
        print(c("red", f"Validation failed: {total_errors}/{total_servers} server entries have issues."))
        return 1

    print(c("green", f"Validation passed: {total_servers} server entries checked."))
    return 0


def cmd_doctor(args):
    if args and args[0] in ("-h", "--help", "help"):
        print(MCP_HELP["doctor"])
        return 0

    unknown = [a for a in args if a != "--strict"]
    if unknown:
        print(f"Unknown arg(s): {', '.join(unknown)}")
        return 1

    strict = "--strict" in args

    registry = load_registry()
    mcp_tools = get_mcp_tools(registry)

    print("MCP Doctor — diagnosing all configured servers\n")

    failures = []

    for tool_name, tool_cfg in mcp_tools.items():
        if not tool_cfg["path"].exists():
            continue
        servers = get_tool_servers(tool_name)
        if not servers:
            continue
        print(f"{tool_name} ({tool_cfg['label']})")
        for name, server_cfg in servers.items():
            health = check_server_health(name, server_cfg)
            st = server_type(server_cfg)
            summary = server_summary(server_cfg)
            print(f"  {health} {name} ({st}): {summary}")

            plain_health = strip_ansi(health).strip()
            if plain_health != "✓":
                failures.append((tool_name, name, plain_health))
        print()

    if strict and failures:
        print(c("red", f"Doctor strict failed: {len(failures)} unhealthy server(s)."))
        for tool_name, name, reason in failures[:20]:
            print(f"  {c('cyan', tool_name)}:{name} -> {c('yellow', reason)}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")
        return 1

    return 0


MCP_HELP = {
    "list": f"""\
  {c('cyan', 'swe mcp list')}                 Matrix of all MCP servers across all tools
  {c('cyan', 'swe mcp list <tool>')}          Show servers for one tool only

{c('bold', 'Args')}
  <tool>              Tool name or alias (e.g. cc, cs, oc)

{c('bold', 'Examples')}
  swe mcp list
  swe mcp list cc""",

    "status": f"""\
  {c('cyan', 'swe mcp status')}               Matrix with health checks for every server
  {c('cyan', 'swe mcp status <tool>')}        Health check for one tool only

{c('bold', 'Health indicators')}
  {c('green', '✓')}   Server binary found / URL reachable
  {c('red', '✗')}   Issue detected (binary missing, URL down, env var empty)

{c('bold', 'Args')}
  <tool>              Tool name or alias (e.g. cc, cs, oc)

{c('bold', 'Examples')}
  swe mcp status
  swe mcp status oc""",

    "sync": f"""\
  {c('cyan', 'swe mcp sync <source> <target...>')}   Copy MCP servers source → target(s)
  {c('cyan', 'swe mcp sync <source> --all')}          Copy MCP servers source → all other MCP tools

{c('bold', 'Flags')}
  --only=a,b,...      Copy only specific server names
  --force             Overwrite existing servers without prompting
  --skip-conflicts    Keep existing target servers on conflict
  --no-interactive    Disable interactive picker (select all)
  --dry-run           Preview changes without writing files
  --strict            Fail if conversion would be lossy

{c('bold', 'Conflict resolution')}
  Interactive mode:
    - server picker starts unchecked
    - servers already present in target(s) start checked
    - pick conflicting servers to overwrite

  Non-interactive mode:
    - all eligible servers are selected
    - conflicts are skipped unless --force is used

  Strict mode:
    - aborts before writing if any selected server loses/changes canonical fields

{c('bold', 'Examples')}
  swe mcp sync oc cursor
  swe mcp sync opencode --all
  swe mcp sync opencode cursor --only=dv__github,dv__linear
  swe mcp sync opencode cursor --force
  swe mcp sync opencode cursor --no-interactive --skip-conflicts
  swe mcp sync opencode cursor --dry-run
  swe mcp sync opencode cursor --strict""", 

    "diff": f"""\
  {c('cyan', 'swe mcp diff <tool1> <tool2>')}  Compare MCP configs between two tools

{c('bold', 'Args')}
  <tool1> <tool2>     Tool names or aliases to compare

{c('bold', 'Examples')}
  swe mcp diff claude cursor
  swe mcp diff cc cs""",

    "edit": f"""\
  {c('cyan', 'swe mcp edit <tool> <name>')}    Edit one server config in one tool

  Opens server config JSON in your editor. Save and exit to apply.
  If you remove the server key from the file, no changes are saved.

{c('bold', 'Args')}
  <tool>              Tool name or alias (e.g. oc, cc, cursor)
  <name>              Server name in that tool's config

{c('bold', 'Editor')}
  Uses $EDITOR env var, falls back to vim.

{c('bold', 'Examples')}
  swe mcp edit opencode dv__github
  EDITOR=code swe mcp edit cursor GitKraken""",

    "validate": f"""\
  {c('cyan', 'swe mcp validate')}             Validate all MCP-capable tools
  {c('cyan', 'swe mcp validate <tool...>')}    Validate one or more tools

  Validation checks:
    - server entry is a JSON object
    - tool-specific parser can parse it
    - same-format roundtrip does not lose canonical fields

{c('bold', 'Examples')}
  swe mcp validate
  swe mcp validate opencode
  swe mcp validate oc cc""",

    "doctor": f"""\
  {c('cyan', 'swe mcp doctor')}               Deep diagnostics for all configured servers

  Checks every server across all tools:
    - Binary exists in PATH
    - URL is reachable
    - Required env vars are set

{c('bold', 'Flags')}
  --strict            Return non-zero if any server is unhealthy

{c('bold', 'Examples')}
  swe mcp doctor
  swe mcp doctor --strict""",
}

def cmd_help(args=None):
    if args:
        cmd_name = args[0]
        if cmd_name in MCP_HELP:
            print(MCP_HELP[cmd_name])
            return 0
        print(f"Unknown command: {cmd_name}")
        return 1
    print(__doc__)
    return 0


COMMANDS = {
    "list": cmd_list,
    "status": cmd_status,
    "sync": cmd_sync,
    "diff": cmd_diff,
    "edit": cmd_edit,
    "validate": cmd_validate,
    "doctor": cmd_doctor,
    "help": cmd_help,
}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        return cmd_help()
    cmd = args[0]
    if cmd == "help":
        return cmd_help(args[1:])
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        return 1
    return COMMANDS[cmd](args[1:])


if __name__ == "__main__":
    sys.exit(main())
