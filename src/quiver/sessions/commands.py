"""Session and model analytics CLI commands."""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from quiver.console import c, cpad, fit_widths, truncate, visible_len
from quiver.sessions import failures
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.identity import launch_tool
from quiver.sessions.models_analytics import classify_provider, collect_model_usage
from quiver.sessions.query import SessionQuery, calendar_range_ms
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
    # ``column_gap=" │ "`` matches ``swe list``'s visible column-border
    # pattern (see harness/commands.py::cmd_list) so the three
    # listing tables render with the same visual rhythm. Both
    # by_tool + default Table builds opt in.
    if by_tool:
        t = Table(column_gap=" │ ")
        t.add_column("tool", "TOOL", width=10, kind="text")
        t.add_column("model", "MODEL", width=42, kind="text")
        t.add_column("provider", "PROVIDER", width=12, kind="text")
        t.add_column(
            "msgs", "MSGS", width=8, kind="count_threshold",
            threshold=threshold,
        )
    else:
        t = Table(column_gap=" │ ")
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


@dataclass
class _SessionArgs:
    limit: int = 10
    agent_filter: str | None = None
    cwd_filter: str | None = None
    use_index: int | None = None
    search: str | None = None
    days: int | None = None
    weeks: int | None = None
    start: str | None = None
    end: str | None = None
    limit_explicit: bool = False

    def __iter__(self):
        # Preserve the original five-value helper contract for callers/tests.
        yield self.limit
        yield self.agent_filter
        yield self.cwd_filter
        yield self.use_index
        yield self.search


def _parse_session_args(args: list[str]):
    limit = 10
    agent_filter = None
    cwd_filter = None
    use_index = None
    search = None
    days = None
    weeks = None
    start = None
    end = None
    limit_explicit = False

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
        elif args[i] in ("--days", "-d") and i + 1 < len(args):
            try:
                days = int(args[i + 1])
            except ValueError:
                print(c("red", "--days must be a positive integer"))
                return None
            i += 2
        elif args[i] in ("--weeks", "-w") and i + 1 < len(args):
            try:
                weeks = int(args[i + 1])
            except ValueError:
                print(c("red", "--weeks must be a positive integer"))
                return None
            i += 2
        elif args[i] in ("--start", "-s") and i + 1 < len(args):
            start = args[i + 1]
            i += 2
        elif args[i] in ("--end", "-e") and i + 1 < len(args):
            end = args[i + 1]
            i += 2
        elif args[i].isdigit() and use_index is None:
            limit = int(args[i])
            limit_explicit = True
            i += 1
        else:
            print(c("red", f"Unknown argument: {args[i]}"))
            return None
    if any(value is not None for value in (days, weeks, start, end)):
        try:
            calendar_range_ms(days=days, weeks=weeks, start=start, end=end)
        except ValueError as exc:
            print(c("red", str(exc)))
            return None
    return _SessionArgs(
        limit=limit,
        agent_filter=agent_filter,
        cwd_filter=cwd_filter,
        use_index=use_index,
        search=search,
        days=days,
        weeks=weeks,
        start=start,
        end=end,
        limit_explicit=limit_explicit,
    )


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


def _print_parser_failures() -> None:
    """Name any parser that crashed, so it is not mistaken for no history.

    A parser that raises returns an empty list, which reads identically to a
    harness you have never used. That is how a NameError in the cursor parser
    hid 84 sessions.
    """
    broken = failures.snapshot()
    if not broken:
        return
    print(c("yellow", f"  {len(broken)} parser(s) failed, "
                      "so those harnesses show no sessions:"))
    for tool, message in sorted(broken.items()):
        print(f"    {c('red', tool.ljust(12))}{c('dim', message[:88])}")
    print()


def cmd_session(args):
    parsed = _parse_session_args(args)
    if parsed is None:
        return 1
    limit, agent_filter, cwd_filter, use_index, search = parsed
    has_date_filter = any(
        value is not None
        for value in (parsed.days, parsed.weeks, parsed.start, parsed.end)
    )

    # Search and date filtering must inspect the complete local inventory.
    fetch_limit = (
        None
        if search or has_date_filter
        else (max(limit, use_index or 0) if use_index else limit)
    )
    if use_index is not None and use_index > limit:
        limit = use_index
        if not search and not has_date_filter:
            fetch_limit = limit

    sessions = get_all_sessions(limit=fetch_limit, agent=agent_filter, cwd=cwd_filter)
    result_limit = limit if parsed.limit_explicit or not has_date_filter else None
    if use_index is not None:
        result_limit = max(result_limit or 0, use_index)
    if has_date_filter:
        start_ms, end_ms = calendar_range_ms(
            days=parsed.days,
            weeks=parsed.weeks,
            start=parsed.start,
            end=parsed.end,
        )
        query = SessionQuery(
            start_ms=start_ms,
            end_ms=end_ms,
            search=search,
            limit=result_limit,
        )
    else:
        query = SessionQuery(search=search, limit=result_limit)
    sessions = query.apply(sessions)

    if not sessions:
        print(c("dim", "  No sessions found."))
        _print_parser_failures()
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
    # DIRECTORY and TITLE are the only free-text columns; the rest are
    # fixed. Their cells are pre-padded, so the budget has to be settled
    # before any row is built.
    _w = fit_widths(fixed=4 + 14 + 14,
                    flex={"directory": 45, "title": 50}, gap=2)
    dir_w, title_w = _w["directory"], _w["title"]

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
        "directory", "DIRECTORY", width=dir_w, max_width=dir_w, kind="text",
    )
    table.add_column(
        "title", "TITLE/SUMMARY", width=title_w, max_width=title_w,
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
        title_raw = _display_title(session, title_w)
        title = title_raw + " " * max(0, title_w - visible_len(title_raw))
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
    _print_parser_failures()
    return 0
