#!/usr/bin/env python3
"""swe - Central manager for AI coding CLI tools"""

import json
import os
import re
import sys
import subprocess
import shutil
import time
from pathlib import Path
from datetime import datetime

from quiver import CONFIG_DIR_NAME

CONFIG_DIR   = Path.home() / ".config" / CONFIG_DIR_NAME
REGISTRY_FILE = CONFIG_DIR / "tools.json"

DEFAULT_TOOLS = {
    "claude": {
        "command":     "claude",
        "description": "Claude Code by Anthropic — agentic coding assistant",
        "version":     "2.1.104",
        "tags":        ["agentic", "coding"],
        "aliases":     ["cc"]
    },
    "gemini": {
        "command":     "gemini",
        "description": "Gemini CLI by Google — large context (1M token) assistant",
        "version":     "0.35.1",
        "tags":        ["agentic", "coding"],
        "aliases":     ["gg"]
    },
    "codex": {
        "command":     "codex",
        "description": "OpenAI Codex CLI",
        "version":     "0.120.0",
        "tags":        ["agentic", "coding"],
        "aliases":     ["cx"]
    },
    "copilot": {
        "command":     "copilot",
        "description": "GitHub Copilot CLI",
        "version":     "1.0.34",
        "tags":        ["agentic", "coding"],
        "aliases":     ["cp"]
    },
    "opencode": {
        "command":     "opencode",
        "description": "opencode — open source AI coding agent",
        "version":     "1.14.20",
        "tags":        ["agentic", "coding", "open-source"],
        "aliases":     ["oc"]
    },
    "forge": {
        "command":     "forge",
        "description": "Forge — AI coding assistant",
        "version":     "2.12.0",
        "tags":        ["agentic", "coding"],
        "aliases":     ["fc"]
    },
    "droid": {
        "command":     "droid",
        "description": "Factory Droids — autonomous background coding agent",
        "version":     "0.106.0",
        "tags":        ["agentic", "coding", "autonomous"],
        "aliases":     ["df"]
    },
    "ollama": {
        "command":     "ollama",
        "description": "Ollama — run local LLMs",
        "version":     "0.20.4",
        "tags":        ["local", "llm", "infrastructure"],
        "aliases":     ["olla"]
    },
    "pi": {
        "command":     "pi",
        "description": "pi — lightweight customisable coding agent harness",
        "version":     None,
        "tags":        ["agentic", "coding", "customisable"],
        "aliases":     ["pi"]
    },
"continue": {
            "command":     "continue",
            "description": "Continue — VS Code extension for AI pair programming",
            "version":     None,
            "tags":        ["agentic", "coding"],
            "aliases":     ["continue", "ct"]
        },
        "mimo": {
            "command":     "mimo",
            "description": "mimo — coding agent",
            "version":     None,
            "tags":        ["agentic", "coding"],
            "aliases":     ["mimo"]
        },
    }

C = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "green":  "\033[32m",
    "red":    "\033[31m",
    "yellow": "\033[33m",
    "cyan":   "\033[36m",
    "blue":   "\033[34m",
}

def c(color, text):
    return f"{C[color]}{text}{C['reset']}"

# ── registry ──────────────────────────────────────────────────────────────────

def load_registry():
    if not REGISTRY_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        save_registry(DEFAULT_TOOLS)
        return DEFAULT_TOOLS
    with open(REGISTRY_FILE) as f:
        return json.load(f)

def save_registry(tools):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(tools, f, indent=2)

def alias_map(tools):
    """Return {alias: canonical_name} for every tool."""
    m = {}
    for name, info in tools.items():
        m[name] = name
        for a in info.get("aliases", []):
            m[a] = name
    return m

def resolve(tools, key):
    """Resolve a name or alias to canonical name, or None."""
    return alias_map(tools).get(key)

# ── helpers ───────────────────────────────────────────────────────────────────

def is_installed(command):
    return shutil.which(command) is not None

def live_version(command):
    for flag in ["--version", "-v", "version", "-V"]:
        try:
            r = subprocess.run(
                [command, flag],
                capture_output=True, text=True, timeout=3
            )
            out = (r.stdout + r.stderr).strip()
            if out:
                return out.splitlines()[0][:60]
        except Exception:
            pass
    return None

def truncate(text, n):
    return text if len(text) <= n else text[:n - 3] + "..."

def session_counts_100d():
    """Return {tool_name: count} for sessions in the past 100 days."""
    from quiver.history import get_all_sessions
    cutoff = (time.time() - 100 * 86400) * 1000
    counts = {}
    for s in get_all_sessions(limit=None):
        if s.timestamp >= cutoff:
            counts[s.tool_name] = counts.get(s.tool_name, 0) + 1
    return counts

# ── commands ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    tools      = load_registry()
    tag_filter = args[0].lstrip("-") if args else None
    counts     = session_counts_100d()

    print(f"\n{c('bold', 'AI Coding Tools')}\n")

    W_NAME, W_CMD, W_VER, W_ALIAS, W_SESS, W_DESC = 16, 18, 12, 12, 8, 36

    hdr = (f"  {'NAME':<{W_NAME}} {'COMMAND':<{W_CMD}} {'VERSION':<{W_VER}}"
           f" {'ALIASES':<{W_ALIAS}} {'100d':>{W_SESS}} {'INSTALLED':<4} DESCRIPTION")
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 110))

    for name, info in sorted(tools.items(), key=lambda x: (-counts.get(x[0], 0), x[0])):
        if tag_filter and tag_filter not in info.get("tags", []):
            continue

        installed  = is_installed(info["command"])
        status     = c("green", "✓") if installed else c("red", "✗")
        ver        = truncate(info.get("version") or "—", W_VER)
        aliases    = ", ".join(a for a in info.get("aliases", []) if a != name)
        desc       = c("dim", truncate(info.get("description", ""), W_DESC))
        sess       = counts.get(name, 0)
        sess_str   = c("green", str(sess)) if sess > 0 else c("dim", "—")

        print(f"  {c('bold', name):<{W_NAME + 9}} {info['command']:<{W_CMD}}"
              f" {ver:<{W_VER}} {c('cyan', aliases):<{W_ALIAS + 9}} {sess_str:>{W_SESS + 9}} {status}   {desc}")

    print()
    n_inst = sum(1 for i in tools.values() if is_installed(i["command"]))
    hints  = "swe use <name|alias>  │  swe info <name>  │  swe list <tag>  │  swe check"
    print(c("dim", f"  {n_inst}/{len(tools)} installed  ·  {hints}"))

    all_tags = sorted(set(t for i in tools.values() for t in i.get("tags", [])))
    tag_str  = "  ".join(c("cyan", t) for t in all_tags)
    print(f"  {c('dim', 'tags:')}  {tag_str}\n")


def cmd_info(args):
    if not args:
        print(c("red", "Usage: swe info <name|alias>"))
        return
    tools = load_registry()
    name  = resolve(tools, args[0])
    if not name:
        print(c("red", f"  Tool '{args[0]}' not found. Try 'swe list'."))
        return

    info      = tools[name]
    installed = is_installed(info["command"])
    path      = shutil.which(info["command"]) or "not found"
    aliases   = [a for a in info.get("aliases", []) if a != name]

    print(f"\n  {c('bold', name)}")
    rows = [
        ("Command",     info["command"]),
        ("Aliases",     ", ".join(aliases) if aliases else "—"),
        ("Description", info.get("description", "—")),
        ("Version",     info.get("version") or "unknown"),
        ("Tags",        ", ".join(info.get("tags", []))),
        ("Status",      c("green", "installed") if installed else c("red", "not installed")),
        ("Path",        path),
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
    tools   = load_registry()
    name    = args[0]
    command = args[1]
    desc    = ""
    tags    = ["agentic", "coding"]
    aliases = []

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
        "command":     command,
        "description": desc,
        "version":     None,
        "tags":        tags,
        "aliases":     aliases,
        "added":       datetime.now().isoformat(),
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
    name  = resolve(tools, args[0])
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
    name  = resolve(tools, args[0])
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
    tools   = load_registry()
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
    tools   = load_registry()
    tag_map: dict[str, list[str]] = {}
    for name, info in tools.items():
        for tag in info.get("tags", []):
            tag_map.setdefault(tag, []).append(name)
    print(f"\n{c('bold', 'Available tags')}\n")
    for tag in sorted(tag_map):
        names = ", ".join(sorted(tag_map[tag]))
        print(f"  {c('cyan', tag):<20} {c('dim', names)}")
    print()


def cmd_aliases(args):
    tools = load_registry()
    print(f"\n{c('bold', 'Short aliases')}\n")
    for name, info in sorted(tools.items()):
        aliases = [a for a in info.get("aliases", []) if a != name]
        if aliases:
            print(f"  {c('cyan', ', '.join(aliases)):<10}  →  {name}")
    print()


def classify_provider(model: str) -> str:
    """Classify provider from model name using regex patterns."""
    import re
    model_lower = model.lower()
    # Order matters - more specific patterns first
    patterns = [
        (r'^accounts?/fireworks/', 'fireworks'),
        (r'^fireworks/', 'fireworks'),
        (r'^openai/', 'openai'),
        (r'^(gpt|o[1-9]|chatgpt)', 'openai'),
        (r'^anthropic/', 'anthropic'),
        (r'^(claude|claude-opus|claude-sonnet|claude-sonnet|haiku|<synthetic>)', 'anthropic'),
        (r'^google/', 'google'),
        (r'^(gemini|googlegemini)', 'google'),
        (r'^deepseek/', 'deepseek'),
        (r'^deepseek', 'deepseek'),
        (r'^meta/', 'meta'),
        (r'^(llama|llamameta)', 'meta'),
        (r'^mistral/', 'mistral'),
        (r'^mistral', 'mistral'),
        (r'^aws?/bedrock/', 'aws'),
        (r'^(aws|amazon)/', 'aws'),
        (r'^azure/', 'azure'),
        (r'^ollama/', 'ollama'),
        (r'^local/', 'local'),
        (r'^(qwen|qwen3|qwen2)', 'alibaba'),
        (r'^(kimi|moonshot)', 'moonshot'),
        (r'^minimax/', 'minimax'),
        (r'^minimax', 'minimax'),
        (r'^groq/', 'groq'),
        (r'^(glm|zhipu)', 'zhipu'),
        (r'^zhipu', 'zhipu'),
        (r'^gemma', 'google'),
        (r'^(big-pickle|bigpickle)', 'bigscience'),
        (r'^synthesis|synthetic', 'synthetic'),
        (r'^mimo', 'xiaomi'),
    ]
    for pattern, provider in patterns:
        if re.match(pattern, model_lower):
            return provider
    return 'other'

def cmd_models(args):
    import glob as globmod
    import re as re_mod

    by_tool = False
    show_providers = False
    for a in args:
        if a in ('--by-tool', '-t'):
            by_tool = True
        elif a in ('--providers', '-p'):
            show_providers = True

    # raw[tool][(provider, model)] = count
    raw: dict[str, dict[tuple[str, str], int]] = {}

    # ── OpenCode ──────────────────────────────────────────────────────────────
    db_path = os.path.expanduser('~/.local/share/opencode/opencode.db')
    if os.path.exists(db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                SELECT json_extract(data, '$.model.providerID'),
                       json_extract(data, '$.model.modelID'),
                       COUNT(*)
                FROM message
                WHERE json_extract(data, '$.model.modelID') IS NOT NULL
                GROUP BY 1, 2
            """)
            for provider, model, cnt in cur.fetchall():
                if model:
                    raw.setdefault('opencode', {})[(provider or '', model)] = cnt
            conn.close()
        except Exception:
            pass

    # ── Claude Code ───────────────────────────────────────────────────────────
    claude_dir = os.path.expanduser('~/.claude/projects/')
    if os.path.exists(claude_dir):
        try:
            seen: dict[tuple[str, str], int] = {}
            for d in os.listdir(claude_dir):
                dp = os.path.join(claude_dir, d)
                if not os.path.isdir(dp) or not d.startswith('-'):
                    continue
                for jf in globmod.glob(os.path.join(dp, '*.jsonl')):
                    with open(jf) as f:
                        for _ in range(30):
                            line = f.readline()
                            if not line:
                                break
                            m = re_mod.search(r'"model":"([^"]+)"', line)
                            if m:
                                seen[('', m.group(1))] = seen.get(('', m.group(1)), 0) + 1
                                break
            if seen:
                raw['claude'] = seen
        except Exception:
            pass

    # ── Codex CLI ─────────────────────────────────────────────────────────────
    codex_dir = os.path.expanduser('~/.codex/sessions/')
    if os.path.exists(codex_dir):
        try:
            seen = {}
            for jf in globmod.glob(os.path.join(codex_dir, '*', '*', '*', '*.jsonl')):
                with open(jf) as f:
                    for _ in range(20):
                        line = f.readline()
                        if not line:
                            break
                        m = re_mod.search(r'"model":"([^"]+)"', line)
                        if m:
                            seen[('', m.group(1))] = seen.get(('', m.group(1)), 0) + 1
                            break
            if seen:
                raw['codex'] = seen
        except Exception:
            pass

    # ── Freebuff ──────────────────────────────────────────────────────────────
    freebuff_dir = os.path.expanduser('~/.config/manicode/projects/')
    if os.path.exists(freebuff_dir):
        try:
            seen = {}
            for project in os.listdir(freebuff_dir):
                project_path = os.path.join(freebuff_dir, project)
                if not os.path.isdir(project_path):
                    continue
                chats_dir = os.path.join(project_path, 'chats')
                if not os.path.exists(chats_dir):
                    continue
                for session_dir in os.listdir(chats_dir):
                    session_path = os.path.join(chats_dir, session_dir)
                    if not os.path.isdir(session_path):
                        continue
                    log_path = os.path.join(session_path, 'log.jsonl')
                    if os.path.exists(log_path):
                        with open(log_path) as f:
                            for _ in range(50):
                                line = f.readline()
                                if not line:
                                    break
                                m = re_mod.search(r'"model":"([^"]+)"', line)
                                if m:
                                    seen[('', m.group(1))] = seen.get(('', m.group(1)), 0) + 1
                                    break
            if seen:
                raw['freebuff'] = seen
        except Exception:
            pass

    if not raw:
        print(c("dim", "\n  No model data found.\n"))
        return

    # ── aggregate ─────────────────────────────────────────────────────────────
    def model_key(provider, model):
        return f"{provider}/{model}" if show_providers and provider else model

    if by_tool:
        # grouped[tool][model_key] = count
        grouped: dict[str, dict[str, int]] = {}
        for tool, entries in raw.items():
            for (provider, model), cnt in entries.items():
                grouped.setdefault(tool, {})[model_key(provider, model)] = \
                    grouped.get(tool, {}).get(model_key(provider, model), 0) + cnt
    else:
        # flat[model_key] = count  (aggregate across tools)
        flat: dict[str, int] = {}
        for tool, entries in raw.items():
            for (provider, model), cnt in entries.items():
                k = model_key(provider, model)
                flat[k] = flat.get(k, 0) + cnt
        grouped = {'': flat}

    # ── display ───────────────────────────────────────────────────────────────
    W_TOOL, W_MODEL, W_PROVIDER, W_MSGS = 10, 42, 12, 8
    PREFIX = "  "

    def strip_ansi(s):
        """Remove ANSI color codes from string."""
        import re
        return re.sub(r'\x1b\[[0-9;]*m', '', s)

    def visible_len(s):
        """Get visible length of string ignoring ANSI color codes."""
        return len(strip_ansi(s))

    def lpad(text, width):
        """Left pad text to width, ignoring color codes."""
        return strip_ansi(text) + " " * (width - visible_len(text))

    def rpad(text, width):
        """Right pad text to width, ignoring color codes."""
        return " " * (width - visible_len(text)) + strip_ansi(text)

    def color(text, color_name):
        """Apply color to text."""
        return c(color_name, text)

    print(f"\n{c('bold', 'Model Usage')}\n")

    if by_tool:
        print(c("dim", f"  {'TOOL':<{W_TOOL}}{'MODEL':<{W_MODEL}}{'PROVIDER':<{W_PROVIDER}}{'MSGS':>{W_MSGS}}"))
        print(c("dim", f"  {'─'*W_TOOL}{'─'*W_MODEL}{'─'*W_PROVIDER}{'─'*W_MSGS}"))
    else:
        print(c("dim", f"  {'MODEL':<{W_MODEL}}{'PROVIDER':<{W_PROVIDER}}{'MSGS':>{W_MSGS}}"))
        print(c("dim", f"  {'─'*W_MODEL}{'─'*W_PROVIDER}{'─'*W_MSGS}"))

    grand_total = 0
    for tool in sorted(grouped):
        entries = sorted(grouped[tool].items(), key=lambda x: -x[1])
        for model, cnt in entries:
            grand_total += cnt
            provider = classify_provider(model)
            cnt_str = f"{cnt:>{W_MSGS}}"
            cnt_colored = c("green", cnt_str) if cnt >= 100 else cnt_str
            if by_tool:
                # Pad first, then apply color to tool name only
                tool_plain = lpad(tool, W_TOOL)
                tool_colored = c("green", tool_plain)
                line = f"  {tool_colored}{lpad(model, W_MODEL)}{lpad(provider, W_PROVIDER)}{cnt_colored}"
            else:
                line = f"  {lpad(model, W_MODEL)}{lpad(provider, W_PROVIDER)}{cnt_colored}"
            print(line)
        if by_tool:
            print()

    n_tools = len(raw)
    n_models = len(set(m for e in raw.values() for _, m in e.keys()))
    print(c("dim", f"  {grand_total} messages, {n_models} models across {n_tools} tools\n"))



def cmd_session(args):
    import time
    from quiver.history import get_all_sessions
    
    limit = 10
    agent_filter = None
    cwd_filter = None
    use_index = None
    
    i = 0
    while i < len(args):
        if args[i] == "use" and i + 1 < len(args) and args[i + 1].isdigit():
            use_index = int(args[i + 1])
            i += 2
        elif args[i] == "--agent" and i + 1 < len(args):
            agent_filter = args[i + 1]
            i += 2
        elif args[i] == "--here":
            cwd_filter = os.getcwd()
            i += 1
        elif args[i].isdigit() and use_index is None:
            limit = int(args[i])
            i += 1
        else:
            print(c("red", f"Unknown argument: {args[i]}"))
            return
            
    if use_index is not None and use_index > limit:
        limit = use_index
            
    sessions = get_all_sessions(limit=limit, agent=agent_filter, cwd=cwd_filter)
    if not sessions:
        print(c("dim", "  No sessions found."))
        print()
        return

    if use_index is not None:
        if use_index < 1 or use_index > len(sessions):
            print(c("red", f"Invalid session index: {use_index}. Pick a number between 1 and {len(sessions)}."))
            return
        
        session = sessions[use_index - 1]
        if not os.path.exists(session.path):
            print(c("red", f"Directory not found: {session.path}"))
            return
            
        print(c("cyan", f"Resuming {session.agent} session..."))
        os.chdir(session.path)
        
        cmd_args = [session.tool_name]
        if session.tool_name == "opencode" and session.session_id:
            cmd_args.extend(["--session", session.session_id])
        elif session.tool_name == "claude" and session.session_id:
            cmd_args.extend(["--resume", session.session_id])
        elif session.tool_name == "codex" and session.session_id:
            cmd_args.extend(["--resume", session.session_id])
        elif session.tool_name == "pi" and session.session_id:
            cmd_args.extend(["--session", session.session_id])
        elif session.tool_name == "gemini":
            print(c("yellow", "Note: Gemini does not support CLI resume flags. Please type /resume in the prompt if needed."))
        elif session.tool_name == "freebuff" and session.session_id:
            cmd_args.extend(["--continue", session.session_id])
            
        return cmd_use(cmd_args)

    print(f"\n{c('bold', 'Recent AI Sessions')}\n")

    W_IDX, W_TIME, W_AGENT, W_TITLE = 4, 14, 14, 50
    # Calculate dynamic width to show full paths
    max_path_len = max(len(s.path.replace(str(Path.home()), "~")) for s in sessions)
    W_PATH = max(45, max_path_len + 4)
    
    hdr = f"  {'[#]':<{W_IDX}} {'LAST ACTIVE':<{W_TIME}} {'AGENT':<{W_AGENT}} {'DIRECTORY':<{W_PATH}} {'TITLE/SUMMARY'}"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * (W_IDX + W_TIME + W_AGENT + W_PATH + W_TITLE + 3)))
    
    now = time.time()
    for idx, s in enumerate(sessions, start=1):
        # Format time
        diff = (now - (s.timestamp / 1000))
        if diff < 60:
            t_str = "Just now"
        elif diff < 3600:
            t_str = f"{int(diff/60)}m ago"
        elif diff < 86400:
            t_str = f"{int(diff/3600)}h ago"
        else:
            t_str = f"{int(diff/86400)}d ago"
            
        agent = s.agent
        # Shorten path by replacing home dir but don't truncate
        path = s.path.replace(str(Path.home()), "~")
        
        title = truncate(s.title or c('dim', '-'), W_TITLE)
        
        print(f"  [{c('bold', str(idx))}]{' ' * (W_IDX - len(str(idx)) - 1)} {c('cyan', t_str):<{W_TIME + 9}} {c('green', agent):<{W_AGENT + 9}} {path:<{W_PATH}} {title}")
        
    print()

# ── skills ──────────────────────────────────────────────────────────────────

def skill_roots():
    """Return [(scope_label, Path)] skill-root dirs that exist, deduped by realpath.

    Order matters: the first label wins when several roots resolve to the same
    real path (e.g. ~/.claude/skills, ~/.codex/skills and ~/.cursor/skills all
    symlink to ~/.agents/skills)."""
    home = Path.home()
    candidates = [
        ("shared",         home / ".agents" / "skills"),
        ("cursor-builtin", home / ".cursor" / "skills-cursor"),
        ("cursor-plugin",  home / ".cursor" / "plugins" / "cache"),
        ("claude-plugin",  home / ".claude" / "plugins" / "cache"),
        ("codex",          home / ".codex" / "skills"),
        ("claude",         home / ".claude" / "skills"),
        ("cursor",         home / ".cursor" / "skills"),
        ("project",        Path.cwd() / ".cursor" / "skills"),
    ]
    roots, seen = [], set()
    for label, p in candidates:
        try:
            if not p.exists():
                continue
            real = p.resolve()
        except Exception:
            continue
        if real in seen:
            continue
        seen.add(real)
        roots.append((label, p))
    return roots


def parse_skill_md(md_path):
    """Return (name, description) from a SKILL.md YAML frontmatter.

    Falls back to the containing directory name when no name field is present."""
    name, desc = None, ""
    try:
        with open(md_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return md_path.parent.name, ""

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            key, buf = None, []
            for line in fm.splitlines():
                m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
                if m:
                    if key == "description" and buf and not desc:
                        desc = " ".join(x.strip() for x in buf).strip()
                    buf = []
                    key = m.group(1).lower()
                    val = m.group(2).strip()
                    if key == "name":
                        name = val.strip("\"'")
                    elif key == "description" and val and val not in (">", ">-", "|", "|-"):
                        desc = val.strip("\"'")
                elif key == "description" and line.strip():
                    buf.append(line)
            if key == "description" and buf and not desc:
                desc = " ".join(x.strip() for x in buf).strip()

    return (name or md_path.parent.name), desc


def discover_skills():
    """Walk every skill root and return a list of skill dicts."""
    skills = []
    for label, root in skill_roots():
        try:
            root_real = root.resolve()
        except Exception:
            continue
        for dirpath, _dirnames, filenames in os.walk(root_real):
            if "SKILL.md" not in filenames:
                continue
            md = Path(dirpath) / "SKILL.md"
            name, desc = parse_skill_md(md)
            skills.append({
                "name":        name,
                "scope":       label,
                "path":        str(md),
                "description": desc,
            })
    return skills


def cmd_skills_scopes(args):
    """List the skill scopes (roots) available, with skill counts."""
    skills = discover_skills()
    counts = {}
    for s in skills:
        counts[s["scope"]] = counts.get(s["scope"], 0) + 1

    home = str(Path.home())
    roots = skill_roots()

    print(f"\n{c('bold', 'Skill Scopes')}\n")
    W_SCOPE, W_COUNT = 16, 8
    hdr = f"  {'SCOPE':<{W_SCOPE}} {'SKILLS':>{W_COUNT}}  PATH"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 100))

    for label, root in roots:
        rp = str(root).replace(home, "~")
        real = str(root.resolve()).replace(home, "~")
        arrow = c("dim", f"  → {real}") if real != rp else ""
        n = counts.get(label, 0)
        n_str = c("green", str(n)) if n > 0 else c("dim", "0")
        print(f"  {c('cyan', label):<{W_SCOPE + 9}} {n_str:>{W_COUNT + 9}}  {c('dim', rp)}{arrow}")

    print()
    print(c("dim", f"  {len(roots)} scopes  ·  {len(skills)} skills total"
                   f"  ·  swe skills <scope>  to filter"))
    print()


def cmd_skills(args):
    if args and args[0] in ("scope", "scopes"):
        return cmd_skills_scopes(args[1:])

    show_desc = False
    filt = None
    for a in args:
        if a in ("-d", "--desc"):
            show_desc = True
        elif a in ("list", "ls"):
            continue
        elif not a.startswith("-"):
            filt = a.lower()

    skills = discover_skills()
    if filt:
        skills = [s for s in skills
                  if filt in s["name"].lower() or filt in s["scope"].lower()]

    if not skills:
        print(c("dim", "\n  No skills found.\n"))
        return

    skills.sort(key=lambda s: (s["scope"], s["name"].lower()))

    print(f"\n{c('bold', 'Agent Skills')}\n")
    W_NAME, W_SCOPE = 30, 16
    hdr = f"  {'NAME':<{W_NAME}} {'SCOPE':<{W_SCOPE}} PATH"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * 100))

    home = str(Path.home())
    for s in skills:
        name = truncate(s["name"], W_NAME)
        path = s["path"].replace(home, "~")
        print(f"  {c('bold', name):<{W_NAME + 9}} {c('cyan', s['scope']):<{W_SCOPE + 9}} {c('dim', path)}")
        if show_desc and s["description"]:
            print(f"  {'':<{W_NAME}} {'':<{W_SCOPE}} {c('dim', truncate(s['description'], 96))}")

    n_scopes = len({s["scope"] for s in skills})
    print()
    print(c("dim", f"  {len(skills)} skills across {n_scopes} scopes"
                   f"  ·  swe skills <filter>  │  swe skills -d"))
    print(c("dim", "  roots:"))
    for label, root in skill_roots():
        rp = str(root).replace(home, "~")
        real = str(root.resolve()).replace(home, "~")
        arrow = c("dim", f"  → {real}") if real != rp else ""
        print(f"    {c('cyan', label):<{16 + 9}} {c('dim', rp)}{arrow}")
    print()


HELP = {
    "list": (
        "List all registered AI coding tools",
        f"""\
  {c('cyan', 'swe list')}                     List all tools (sorted by 100d usage)
  {c('cyan', 'swe list <tag>')}               Filter by tag (e.g. swe list agentic)

{c('bold', 'Flags')}
  None — just list and filter by tag."""
    ),
    "info": (
        "Show full details for a tool",
        f"""\
  {c('cyan', 'swe info <name|alias>')}        Show command, version, path, tags, aliases

{c('bold', 'Examples')}
  swe info claude
  swe info cc"""
    ),
    "use": (
        "Launch a tool (replaces current process)",
        f"""\
  {c('cyan', 'swe use <name|alias> [args]')}  Launch a registered tool
  {c('cyan', 'swe run <name|alias> [args]')}  Same as use

  Extra args are passed through to the underlying command.
  Uses {c('dim', 'os.execvp')} to replace the process cleanly.

{c('bold', 'Examples')}
  swe use cc
  swe use codex --help
  swe use gemini -p 'explain this codebase'"""
    ),
    "add": (
        "Register a new tool in the registry",
        f"""\
  {c('cyan', 'swe add <name> <command>')}             Add with defaults
  {c('cyan', 'swe add <name> <command> [desc]')}      Add with description
  {c('cyan', 'swe add <name> <cmd> --aliases a,b')}   Set short aliases
  {c('cyan', 'swe add <name> <cmd> --tags t1,t2')}    Set tags

  If the tool already exists, it updates the entry.

{c('bold', 'Examples')}
  swe add aider aider "AI pair programmer" --aliases ai --tags agentic,coding
  swe add mytool /usr/local/bin/mytool"""
    ),
    "remove": (
        "Remove a tool from the registry",
        f"""\
  {c('cyan', 'swe remove <name|alias>')}      Remove by name or alias
  {c('cyan', 'swe rm <name|alias>')}          Same as remove

  Does not uninstall the tool, only removes from swe registry."""
    ),
    "check": (
        "Verify install status and refresh versions",
        f"""\
  {c('cyan', 'swe check')}                    Probe each tool for live version

  Tries --version, -v, version, -V flags.
  Updates registry if version changed."""
    ),
    "session": (
        "Show recent AI sessions across all agents",
        f"""\
  {c('cyan', 'swe session')}                  Show last 10 sessions
  {c('cyan', 'swe session <N>')}              Show last N sessions
  {c('cyan', 'swe session use <N>')}          Resume session #N

{c('bold', 'Flags')}
  {c('cyan', '--agent <name>')}               Filter by agent (claude, codex, opencode, cursor, ...)
  {c('cyan', '--here')}                       Filter to current directory only

{c('bold', 'Examples')}
  swe session
  swe session 20
  swe session use 3
  swe session --agent claude
  swe session --here"""
    ),
    "models": (
        "Show model usage across all tools",
        f"""\
  {c('cyan', 'swe models')}                   Flat list, model name only, sorted by count
  {c('cyan', 'swe models -t')}                Group by tool
  {c('cyan', 'swe models -p')}                Show provider prefix (e.g. openai/gpt-5.4)
  {c('cyan', 'swe models -t -p')}             Both: grouped by tool with providers

{c('bold', 'Flags')}
  {c('cyan', '-t, --by-tool')}                Group results by tool instead of flat list
  {c('cyan', '-p, --providers')}              Show provider/model instead of just model

  Default aggregates across providers (gpt-5.4 = openai + codex combined).
  Flags can be combined: {c('dim', 'swe models -t -p')}"""
    ),
    "skills": (
        "List agent skills and their file paths",
        f"""\
  {c('cyan', 'swe skills')}                   List every SKILL.md across all skill roots
  {c('cyan', 'swe skills list')}              Same as above
  {c('cyan', 'swe skills <filter>')}          Filter by name or scope substring
  {c('cyan', 'swe skills -d')}                Also show each skill's description
  {c('cyan', 'swe skills scope list')}        List the scopes (roots) available with counts

{c('bold', 'Flags')}
  {c('cyan', '-d, --desc')}                   Show skill descriptions

{c('bold', 'Scopes scanned')}
  shared          ~/.agents/skills (the tree ~/.claude, ~/.codex, ~/.cursor symlink to)
  cursor-builtin  ~/.cursor/skills-cursor
  cursor-plugin   ~/.cursor/plugins/cache
  claude-plugin   ~/.claude/plugins/cache
  project         ./.cursor/skills (current directory)

  Roots that resolve to the same real path are shown once."""
    ),
    "tags": (
        "Show all tags and which tools use them",
        f"""\
  {c('cyan', 'swe tags')}                     List tags with associated tools"""
    ),
    "aliases": (
        "Show all short aliases for tools",
        f"""\
  {c('cyan', 'aliases')}                      List alias → tool mappings"""
    ),
    "mcp": (
        "Manage MCP servers across AI tools",
        f"""\
  {c('cyan', 'swe mcp list [tool]')}          Matrix view of MCP servers across tools
  {c('cyan', 'swe mcp status [tool]')}        List with health checks
  {c('cyan', 'swe mcp add <name> | -A')}      Stage server(s) for sync
  {c('cyan', 'swe mcp remove <name>')}        Remove from source of truth
  {c('cyan', 'swe mcp sync [tool...]')}       Push staged → tools (--force, --skip-conflicts)
  {c('cyan', 'swe mcp diff <t1> <t2>')}       Compare two tools' configs
  {c('cyan', 'swe mcp edit <name>')}          Edit a server's config
  {c('cyan', 'swe mcp export [--full]')}      Dump config (redacted by default)
  {c('cyan', 'swe mcp import <file>')}        Load config into source of truth
  {c('cyan', 'swe mcp doctor')}               Deep diagnostics

{c('bold', 'Help')}  {c('cyan', 'swe mcp <command> help')} for detailed help on each command
{c('bold', 'Source of truth')}  ~/.config/swe/mcp.json"""
    ),
}

COMMAND_CATEGORIES = [
    ("Registry", [
        ("list",    "ls"),
        ("info",    None),
        ("add",     None),
        ("remove",  "rm"),
        ("check",   None),
    ]),
    ("Launch", [
        ("use",     "run"),
    ]),
    ("Analytics", [
        ("session", None),
        ("models",  None),
    ]),
    ("Reference", [
        ("skills",  None),
        ("tags",    None),
        ("aliases", None),
    ]),
    ("MCP", [
        ("mcp",     None),
    ]),
]


def cmd_help(args):
    # ── per-command help ──────────────────────────────────────────────────────
    if args:
        cmd_name = args[0]
        if cmd_name in HELP:
            summary, detail = HELP[cmd_name]
            print(f"\n  {c('bold', 'swe ' + cmd_name)} — {summary}\n")
            print(detail)
            print()
            return
        # check aliases
        for cat, cmds in COMMAND_CATEGORIES:
            for primary, alias in cmds:
                if alias == cmd_name:
                    summary, detail = HELP[primary]
                    print(f"\n  {c('bold', 'swe ' + primary)} ({c('dim', alias)}) — {summary}\n")
                    print(detail)
                    print()
                    return
        print(c("red", f"  Unknown command: '{cmd_name}'"))
        return

    # ── full help ─────────────────────────────────────────────────────────────
    print(f"\n{c('bold', 'swe')} — Central manager for AI coding CLI tools\n")
    print(f"  {c('dim', 'USAGE')}  swe <command> [arguments]\n")

    for cat_name, cmds in COMMAND_CATEGORIES:
        print(f"  {c('bold', cat_name)}")
        for primary, alias in cmds:
            summary = HELP[primary][0]
            if alias:
                print(f"    {c('cyan', primary):<22} {c('dim', '(' + alias + ')'):<14} {summary}")
            else:
                print(f"    {c('cyan', primary):<22} {'':14} {summary}")
        print()

    print(f"  {c('dim', 'FLAGS')}")
    print(f"    {c('cyan', 'swe help')}              Full help")
    print(f"    {c('cyan', 'swe <cmd> --help')}      Detailed help for a command\n")

    print(f"  {c('dim', 'ALIASES')}   cc=claude  gg=gemini  cx=codex  cp=copilot  oc=opencode")
    print(f"  {'':>14}fc=forge  df=droid  olla=ollama  cs=cursor  cl=cline\n")

    n_inst = 0
    n_total = 0
    try:
        tools = load_registry()
        n_total = len(tools)
        n_inst = sum(1 for i in tools.values() if is_installed(i["command"]))
    except Exception:
        pass
    print(f"  {c('dim', 'REGISTRY')}  {REGISTRY_FILE}")
    print(f"  {c('dim', 'TOOLS')}     {n_inst}/{n_total} installed\n")

# ── dispatch ──────────────────────────────────────────────────────────────────

def cmd_mcp(args):
    """Dispatch to the packaged MCP subcommand handler."""
    from quiver import mcp
    return mcp.main(args)

COMMANDS = {
    "list":    cmd_list,
    "ls":      cmd_list,
    "info":    cmd_info,
    "add":     cmd_add,
    "remove":  cmd_remove,
    "rm":      cmd_remove,
    "use":     cmd_use,
    "run":     cmd_use,
    "check":   cmd_check,
    "session": cmd_session,
    "models":  cmd_models,
    "skills":  cmd_skills,
    "sk":      cmd_skills,
    "tags":    cmd_tags,
    "aliases": cmd_aliases,
    "mcp":     cmd_mcp,
    "help":    cmd_help,
    "--help":  cmd_help,
    "-h":      cmd_help,
}

def main():
    argv = sys.argv[1:]
    if not argv:
        cmd_help([])
        return 0
    cmd  = argv[0]
    rest = argv[1:]
    if cmd in COMMANDS:
        if rest and rest[0] in ('--help', '-h'):
            cmd_help([cmd])
            return 0
        result = COMMANDS[cmd](rest)
        return result if isinstance(result, int) else 0
    else:
        print(c("red", f"  Unknown command: '{cmd}'"))
        cmd_help([])
        return 1

if __name__ == "__main__":
    sys.exit(main())
