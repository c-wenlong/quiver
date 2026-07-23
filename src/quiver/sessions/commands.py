"""Session and model analytics CLI commands."""

import os
import time
from pathlib import Path

from quiver.console import c, cpad, truncate, visible_len
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.identity import launch_tool
from quiver.sessions.models_analytics import classify_provider, collect_model_usage
from quiver.table import Table

# Resume flag strategies keyed by tool_name (not launch key)
_RESUME_FLAGS = {
    "opencode": lambda sid: ["--session", sid] if sid else [],
    "claude": lambda sid: ["--resume", sid] if sid else [],
    "codex": lambda sid: ["--resume", sid] if sid else [],
    "pi": lambda sid: ["--session", sid] if sid else [],
    "droid": lambda sid: ["--resume", sid] if sid else [],
    "copilot": lambda sid: ["--resume", sid] if sid else [],
    "freebuff": lambda sid: ["--continue", sid] if sid else [],
}

_LIMITED_RESUME = frozenset(
    {
        "gemini",
        "antigravity",
        "continue",
        "crush",
        "amp",
        "kimi",
        "hermes",
        "grok",
        "cline",
        "forge",
        "mimo",
        "tau",
        "cursor",
    }
)


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

    print(f"\n{c('bold', 'Model Usage')}\n")

    # Build the table once, columns swap between by-tool and default
    # modes. The MSGS column uses ``count_threshold`` with threshold=100
    # so cells >= 100 picks up green ANSI automatically; the column
    # also adapts to ``attrs[\"threshold\"]`` rather than requiring the
    # caller to pre-color the value (the old code path did the colour
    # decision imperatively in the print loop).
    threshold = 100
    if by_tool:
        t = Table()
        t.add_column("tool", "TOOL", width=10, kind="text")
        t.add_column("model", "MODEL", width=42, kind="text")
        t.add_column("provider", "PROVIDER", width=12, kind="text")
        t.add_column(
            "msgs", "MSGS", width=8, kind="count_threshold",
            threshold=threshold,
        )
    else:
        t = Table()
        t.add_column("model", "MODEL", width=42, kind="text")
        t.add_column("provider", "PROVIDER", width=12, kind="text")
        t.add_column(
            "msgs", "MSGS", width=8, kind="count_threshold",
            threshold=threshold,
        )

    grand_total = 0
    last_tool = None
    for tool in sorted(grouped):
        entries = sorted(grouped[tool].items(), key=lambda x: -x[1])
        for model, cnt in entries:
            grand_total += cnt
            provider = classify_provider(model)
            if by_tool:
                # Visual separator between tool groups (preserves the
                # blank-line behaviour the old hand-rolled print loop
                # used to insert).
                if last_tool is not None and last_tool != tool:
                    print()
                t.add_row({
                    "tool": tool,
                    "model": model,
                    "provider": provider,
                    "msgs": cnt,
                })
            else:
                t.add_row({
                    "model": model,
                    "provider": provider,
                    "msgs": cnt,
                })
        if by_tool:
            last_tool = tool

    for line in t.render():
        print(line)
    print()

    n_tools = len(raw)
    n_models = len({m for entries in raw.values() for _, m in entries.keys()})
    print(c("dim", f"  {grand_total} messages, {n_models} models across {n_tools} tools\n"))


def _parse_session_args(args: list[str]):
    limit = 10
    agent_filter = None
    cwd_filter = None
    use_index = None
    search = None

    i = 0
    while i < len(args):
        if args[i] == "use" and i + 1 < len(args) and args[i + 1].isdigit():
            use_index = int(args[i + 1])
            i += 2
        elif args[i] == "--agent" and i + 1 < len(args):
            agent_filter = args[i + 1]
            i += 2
        elif args[i] in ("--search", "-q", "--grep") and i + 1 < len(args):
            search = args[i + 1]
            i += 2
        elif args[i] == "--here":
            cwd_filter = os.getcwd()
            i += 1
        elif args[i].isdigit() and use_index is None:
            limit = int(args[i])
            i += 1
        else:
            print(c("red", f"Unknown argument: {args[i]}"))
            return None
    return limit, agent_filter, cwd_filter, use_index, search


def _filter_search(sessions, search: str | None):
    if not search:
        return sessions
    needle = search.lower()
    out = []
    for s in sessions:
        hay = " ".join(
            [
                s.agent or "",
                s.tool_name or "",
                s.path or "",
                s.title or "",
                s.session_id or "",
            ]
        ).lower()
        if needle in hay:
            out.append(s)
    return out


def _launch_tool_name(tool_name: str) -> str:
    return launch_tool(tool_name)


def _resume_cmd_args(session) -> list[str]:
    launch = _launch_tool_name(session.tool_name)
    cmd_args = [launch]
    builder = _RESUME_FLAGS.get(session.tool_name)
    if builder:
        cmd_args.extend(builder(session.session_id))
    elif session.tool_name in _LIMITED_RESUME:
        if session.tool_name in ("gemini", "antigravity"):
            print(
                c(
                    "yellow",
                    f"Note: {session.agent} does not support CLI resume flags. "
                    "Type /resume in the prompt if needed.",
                )
            )
        else:
            print(
                c(
                    "yellow",
                    f"Note: {session.agent} resume flags are limited; "
                    "launching in session directory.",
                )
            )
    return cmd_args


def _display_title(session, width: int) -> str:
    title = (session.title or "").strip()
    if title:
        return truncate(title, width)
    sid = (session.session_id or "").strip()
    if sid:
        short = sid if len(sid) <= 12 else sid[:8] + "…"
        return c("dim", f"#{short}")
    return c("dim", "-")


def cmd_session(args):
    parsed = _parse_session_args(args)
    if parsed is None:
        return 1
    limit, agent_filter, cwd_filter, use_index, search = parsed

    # Fetch a wider window when searching, then re-limit
    fetch_limit = None if search else (max(limit, use_index or 0) if use_index else limit)
    if use_index is not None and use_index > limit:
        limit = use_index
        if not search:
            fetch_limit = limit

    sessions = get_all_sessions(limit=fetch_limit, agent=agent_filter, cwd=cwd_filter)
    sessions = _filter_search(sessions, search)
    if search:
        sessions = sessions[:limit]

    if not sessions:
        print(c("dim", "  No sessions found."))
        print()
        return 0

    if use_index is not None:
        if use_index < 1 or use_index > len(sessions):
            print(
                c(
                    "red",
                    f"Invalid session index: {use_index}. "
                    f"Pick a number between 1 and {len(sessions)}.",
                )
            )
            return 1

        session = sessions[use_index - 1]
        if not os.path.exists(session.path):
            print(c("red", f"Directory not found: {session.path}"))
            return 1

        print(c("cyan", f"Resuming {session.agent} session..."))
        os.chdir(session.path)

        cmd_args = _resume_cmd_args(session)
        from quiver.harness.commands import cmd_use

        return cmd_use(cmd_args)

    print(f"\n{c('bold', 'Recent AI Sessions')}\n")

    # Five-column table: IDX | LAST ACTIVE | AGENT | DIRECTORY | TITLE/SUMMARY.
    #
    # IDX, TIME, AGENT, TITLE all use ``kind="preformatted"`` with
    # ``trust_cell_width=True`` because their cells ship pre-coloured
    # ANSI (bold idx, cyan relative time, green agent, dim title
    # fallback). Each TIME/AGENT cell is run through ``cpad`` so the
    # rendered column visible-width never drifts below 14 (mirroring
    # cmd_list's pre-pad pattern from the cmd_list migration).
    # DIRECTORY uses ``kind="text"`` because paths are plain — no
    # ANSI — and ``fit="content"`` so the longest visible path drives
    # the column width. ``text`` auto-pads cells, so DIRECTORY rows
    # stay aligned without manual padding.
    table = Table()
    table.add_column(
        "idx", "[#]", width=4,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "time", "LAST ACTIVE", width=14,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "agent", "AGENT", width=14,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "directory", "DIRECTORY", width=45, fit="content", kind="text",
    )
    table.add_column(
        "title", "TITLE/SUMMARY", width=50, max_width=50,
        kind="preformatted", trust_cell_width=True,
    )

    now = time.time()
    home_str = str(Path.home())
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

        path = session.path.replace(home_str, "~")
        # IDX cell: ``[BOLD<N>]`` padded to width=4. ``trust_cell_width``
        # skips renderer pad so we manually pad for column-grid alignment.
        bold_idx = c("bold", str(idx))
        idx_cell = f"[{bold_idx}]" + " " * max(0, 4 - len(str(idx)) - 2)
        # TIME/AGENT cells go through ``cpad`` (coloured + literal-space
        # pad to width) — this is the cmd_list migration's pre-pad
        # pattern generalised. TITLE has multiple visual flavours
        # (plain text OR dim fallback) so we add the pad outside cpad to
        # keep the dim wrap contiguous.
        title_raw = _display_title(session, 50)
        title = title_raw + " " * max(0, 50 - visible_len(title_raw))
        table.add_row({
            "idx": idx_cell,
            "time": cpad("cyan", t_str, 14),
            "agent": cpad("green", session.agent, 14),
            "directory": path,
            "title": title,
        })

    for line in table.render():
        print(line)
    print()
    if search:
        print(c("dim", f"  filter: --search {search!r}  ·  {len(sessions)} match(es)"))
        print()
    return 0
