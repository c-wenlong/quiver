"""Session and model analytics CLI commands."""

import os
import time
from pathlib import Path

from quiver.console import c, lpad, truncate
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.models_analytics import classify_provider, collect_model_usage


def cmd_models(args):
    by_tool = False
    show_providers = False
    for arg in args:
        if arg in ("--by-tool", "-t"):
            by_tool = True
        elif arg in ("--providers", "-p"):
            show_providers = True

    raw = collect_model_usage()
    if not raw:
        print(c("dim", "\n  No model data found.\n"))
        return

    def model_key(provider, model):
        return f"{provider}/{model}" if show_providers and provider else model

    if by_tool:
        grouped: dict[str, dict[str, int]] = {}
        for tool, entries in raw.items():
            for (provider, model), cnt in entries.items():
                key = model_key(provider, model)
                grouped.setdefault(tool, {})[key] = grouped.get(tool, {}).get(key, 0) + cnt
    else:
        flat: dict[str, int] = {}
        for tool, entries in raw.items():
            for (provider, model), cnt in entries.items():
                key = model_key(provider, model)
                flat[key] = flat.get(key, 0) + cnt
        grouped = {"": flat}

    w_tool, w_model, w_provider, w_msgs = 10, 42, 12, 8

    print(f"\n{c('bold', 'Model Usage')}\n")
    if by_tool:
        print(c("dim", f"  {'TOOL':<{w_tool}}{'MODEL':<{w_model}}{'PROVIDER':<{w_provider}}{'MSGS':>{w_msgs}}"))
        print(c("dim", f"  {'─' * w_tool}{'─' * w_model}{'─' * w_provider}{'─' * w_msgs}"))
    else:
        print(c("dim", f"  {'MODEL':<{w_model}}{'PROVIDER':<{w_provider}}{'MSGS':>{w_msgs}}"))
        print(c("dim", f"  {'─' * w_model}{'─' * w_provider}{'─' * w_msgs}"))

    grand_total = 0
    for tool in sorted(grouped):
        entries = sorted(grouped[tool].items(), key=lambda x: -x[1])
        for model, cnt in entries:
            grand_total += cnt
            provider = classify_provider(model)
            cnt_str = f"{cnt:>{w_msgs}}"
            cnt_colored = c("green", cnt_str) if cnt >= 100 else cnt_str
            if by_tool:
                tool_colored = c("green", lpad(tool, w_tool))
                line = f"  {tool_colored}{lpad(model, w_model)}{lpad(provider, w_provider)}{cnt_colored}"
            else:
                line = f"  {lpad(model, w_model)}{lpad(provider, w_provider)}{cnt_colored}"
            print(line)
        if by_tool:
            print()

    n_tools = len(raw)
    n_models = len({m for entries in raw.values() for _, m in entries.keys()})
    print(c("dim", f"  {grand_total} messages, {n_models} models across {n_tools} tools\n"))


def cmd_session(args):
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

        from quiver.harness.commands import cmd_use

        return cmd_use(cmd_args)

    print(f"\n{c('bold', 'Recent AI Sessions')}\n")

    w_idx, w_time, w_agent, w_title = 4, 14, 14, 50
    max_path_len = max(len(s.path.replace(str(Path.home()), "~")) for s in sessions)
    w_path = max(45, max_path_len + 4)

    hdr = f"  {'[#]':<{w_idx}} {'LAST ACTIVE':<{w_time}} {'AGENT':<{w_agent}} {'DIRECTORY':<{w_path}} {'TITLE/SUMMARY'}"
    print(c("dim", hdr))
    print(c("dim", "  " + "─" * (w_idx + w_time + w_agent + w_path + w_title + 3)))

    now = time.time()
    for idx, session in enumerate(sessions, start=1):
        diff = now - (session.timestamp / 1000)
        if diff < 60:
            t_str = "Just now"
        elif diff < 3600:
            t_str = f"{int(diff / 60)}m ago"
        elif diff < 86400:
            t_str = f"{int(diff / 3600)}h ago"
        else:
            t_str = f"{int(diff / 86400)}d ago"

        path = session.path.replace(str(Path.home()), "~")
        title = truncate(session.title or c("dim", "-"), w_title)
        print(
            f"  [{c('bold', str(idx))}]{' ' * (w_idx - len(str(idx)) - 1)} "
            f"{c('cyan', t_str):<{w_time + 9}} {c('green', session.agent):<{w_agent + 9}} "
            f"{path:<{w_path}} {title}"
        )
    print()
