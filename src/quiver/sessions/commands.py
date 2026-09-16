"""Session and model analytics CLI commands."""

import os
import time
from dataclasses import dataclass
from pathlib import Path

from quiver.console import c, cpad, fit_widths, terminal_width, truncate, visible_len
from quiver.markdown import render_markdown
from quiver.sessions import failures
from quiver.sessions.aggregator import get_all_sessions
from quiver.sessions.identity import launch_tool
from quiver.sessions.models_analytics import classify_provider, collect_model_usage
from quiver.sessions.picker import pick_session
from quiver.sessions.query import SessionQuery, calendar_range_ms
from quiver.sessions.status import session_statuses
from quiver.table import Table

def _codex_resume_args(session_id: str) -> list[str]:
    """``codex resume <uuid>``, or nothing when there is no uuid to pass.

    Codex resumes through a subcommand rather than a flag, and its argument
    is the thread uuid or a thread name. Quiver's session id is the rollout
    file's stem, which is neither, so the uuid has to be lifted out of it: a
    ``--resume rollout-2026-09-09T14-52-42-<uuid>`` never resumed anything.
    """
    from quiver.sessions.parsers import codex_thread_id

    thread_id = codex_thread_id(session_id)
    return ["resume", thread_id] if thread_id else []


# Resume flag strategies keyed by tool_name (not launch key)
_RESUME_FLAGS = {
    "opencode": lambda sid: ["--session", sid] if sid else [],
    "kilo": lambda sid: ["--session", sid] if sid else [],
    "claude": lambda sid: ["--resume", sid] if sid else [],
    "codex": _codex_resume_args,
    "pi": lambda sid: ["--session", sid] if sid else [],
    "droid": lambda sid: ["--resume", sid] if sid else [],
    "copilot": lambda sid: ["--resume", sid] if sid else [],
    "devin": lambda sid: ["--resume", sid] if sid else [],
    "freebuff": lambda sid: ["--continue", sid] if sid else [],
    "cursor": lambda sid: ["--resume", sid] if sid else [],
    "cline": lambda sid: ["--id", sid] if sid else [],
}

_LIMITED_RESUME = frozenset(
    {
        "gemini",
        "continue",
        "crush",
        "grok",
        "forge",
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
    interactive: bool = False

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
    interactive = False

    i = 0
    while i < len(args):
        if args[i] == "use" and i + 1 < len(args) and args[i + 1].isdigit():
            use_index = int(args[i + 1])
            i += 2
        elif args[i] in ("--interactive", "-i"):
            interactive = True
            i += 1
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
    if interactive and use_index is not None:
        print(c("red", "Cannot combine use <N> with --interactive"))
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
        interactive=interactive,
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
        if session.tool_name == "gemini":
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
        shown = truncate(title, width)
        # A name the user set is the only title shown at full intensity, in
        # italics. Titles lifted from the first prompt or generated by the
        # harness are dimmed so the renamed rows are the ones that stand out.
        if getattr(session, "title_source", "") == "rename":
            return c("italic", shown)
        return c("dim", shown)
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


def _relative_time(diff: float) -> str:
    """A session's age as the listing's short stamp ("3m ago", "Just now")."""
    if diff < 60:
        return "Just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return f"{int(diff / 86400)}d ago"


# status -> (glyph, colour, legend label), in legend order. Glyphs are
# single-width on purpose: a real emoji is two columns and the grid would
# drift. UNKNOWN rows show "-" and never appear in the legend.
_STATUS_GLYPHS = {
    "active": ("●", "green", "active"),
    "done": ("✓", "dim", "done"),
    "followup": ("?", "yellow", "followup"),
    "error": ("✗", "red", "error"),
    "interrupted": ("■", "blue", "interrupted"),
}


def _status_legend(statuses) -> str | None:
    """The one-line key for the ST column, or None when nothing is known."""
    if not any(statuses):
        return None
    parts = [
        f"{c(colour, glyph)} {c('dim', label)}"
        for glyph, colour, label in _STATUS_GLYPHS.values()
    ]
    return "  " + "  ·  ".join(parts)


def _build_session_table(sessions, reserve: int = 0, statuses=None) -> Table:
    # Six-column table: IDX | LAST ACTIVE | AGENT | ST | DIRECTORY | TITLE/SUMMARY.
    #
    # IDX, TIME, AGENT, TITLE all use ``kind="preformatted"`` with
    # ``trust_cell_width=True`` because their cells ship pre-coloured
    # ANSI (bold idx, cyan relative time, green agent, dim title
    # fallback). Each TIME/AGENT cell is run through ``cpad`` so the
    # rendered column visible-width matches what the column declares
    # (mirroring cmd_list's pre-pad pattern from the cmd_list migration).
    # DIRECTORY uses ``kind="path"``: plain text like ``text``, no ANSI,
    # auto-padded so rows stay aligned without manual padding, but cut
    # from the middle. Every row here tends to share a long prefix, so
    # cutting from the right renders whole runs of sessions identical.
    # DIRECTORY and TITLE are the only free-text columns; the rest are
    # fixed. Their cells are pre-padded, so the budget has to be settled
    # before any row is built.
    # ``reserve`` shrinks the fit cap so a caller that will prefix every
    # rendered row with extra characters (the interactive picker's
    # pointer + space) still fits the terminal after that prefix is
    # added; the static path passes 0 and behaves exactly as before.
    #
    # ``statuses`` is one label per session, same order; callers pass the
    # values they already computed for the rows shown, otherwise they are
    # probed here.
    if statuses is None:
        statuses = session_statuses(sessions)
    # IDX, TIME and AGENT are sized to what this run actually holds
    # rather than to a worst case, so the row carries no dead columns:
    # a listing of Codex and Claude rows spent 14 columns on an AGENT
    # field whose longest value was 11, and another 3 on a LAST ACTIVE
    # field no stamp fills. Each still floors at its own header, and
    # IDX grows with the digit count so ``swe session 200`` keeps the
    # grid: a 3-digit "[100]" cell is 5 columns and used to overflow the
    # hardcoded 4, shifting every column after it from row 100 on.
    now = time.time()
    stamps = [_relative_time(now - (s.timestamp / 1000)) for s in sessions]
    agents = [s.agent or "" for s in sessions]
    idx_w = max(len("[#]"), len(f"[{len(sessions)}]"))
    time_w = max([len("LAST ACTIVE")] + [len(s) for s in stamps])
    agent_w = max([len("AGENT")] + [len(a) for a in agents])
    status_w = max(len("ST"), 1)

    # ``fixed`` must include the two column_gap=2 gaps *between* idx,
    # time, and agent (the "+2 +2" below), not just their own widths.
    # fit_widths treats every column folded into ``fixed`` as a single
    # blob and only charges one connecting gap per flex column on top
    # of it; the gaps *inside* that blob have to be paid for up front,
    # or the returned widths under-budget the real rendered row by
    # exactly those two gaps (4 columns) once every time this table is
    # built with a tight cap.
    _w = fit_widths(fixed=idx_w + 2 + time_w + 2 + agent_w + 2 + status_w,
                    flex={"directory": 45, "title": 50}, gap=2,
                    cap=terminal_width() - reserve)
    dir_w, title_w = _w["directory"], _w["title"]

    table = Table()
    table.add_column(
        "idx", "[#]", width=idx_w,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "time", "LAST ACTIVE", width=time_w,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "agent", "AGENT", width=agent_w,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "status", "ST", width=status_w,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "directory", "DIRECTORY", width=dir_w, max_width=dir_w, kind="path",
    )
    table.add_column(
        "title", "TITLE/SUMMARY", width=title_w, max_width=title_w,
        kind="preformatted", trust_cell_width=True,
    )

    home_str = str(Path.home())
    for idx, (session, t_str, agent, status) in enumerate(
        zip(sessions, stamps, agents, statuses), start=1
    ):
        path = session.path.replace(home_str, "~")
        # IDX cell: ``[BOLD<N>]`` padded to the column. ``trust_cell_width``
        # skips renderer pad so we manually pad for column-grid alignment.
        bold_idx = c("bold", str(idx))
        idx_cell = f"[{bold_idx}]" + " " * max(0, idx_w - len(str(idx)) - 2)
        # TIME/AGENT cells go through ``cpad`` (coloured + literal-space
        # pad to width) — this is the cmd_list migration's pre-pad
        # pattern generalised. TITLE has multiple visual flavours
        # (italic rename, dim default, dim fallback) so we add the pad
        # outside cpad to keep the ANSI wrap contiguous.
        title_raw = _display_title(session, title_w)
        title = title_raw + " " * max(0, title_w - visible_len(title_raw))
        glyph, colour, _ = _STATUS_GLYPHS.get(status, ("-", "dim", ""))
        table.add_row({
            "idx": idx_cell,
            "time": cpad("cyan", t_str, time_w),
            "agent": cpad("green", agent, agent_w),
            "status": cpad(colour, glyph, status_w),
            "directory": path,
            "title": title,
        })

    return table


def _tool_run_line(count: int) -> str:
    """One dim line standing in for a run of ``count`` tool calls."""
    return c("dim", f"  called {count} tool{'' if count == 1 else 's'}")


def _session_preview(session) -> list[str]:
    """A session's transcript as document lines for the picker's view.

    Two forks share a title and a directory, so the listing cannot tell them
    apart; the conversation can. Reads through the report readers so every
    harness the reports can summarise can also be previewed. The import is
    function-local because ``reports`` already imports ``sessions`` at
    module level, and this keeps that the only direction.

    The view is for recognising a session at a glance, so it shows the
    conversation and not the machinery. A run of tool calls collapses to a
    count, because their arguments are the bulkiest thing in a transcript
    and the least useful for telling two sessions apart.

    Turns carry no ``you``/``ai`` label. A prompt is marked by painting it,
    the way a chat UI shades what you typed: the line goes out wearing
    ``user_bg``, and the view fills each wrapped row out to the full width
    so the slab is a block rather than a ragged tail. Anything else is the
    assistant. Message bodies keep one document line per source line so the
    view can wrap them to the terminal instead of cutting them, and an
    assistant turn is markdown, so it is rendered rather than shown as a
    wall of hashes and asterisks.
    """
    from quiver.reports.transcripts import read_transcript

    transcript = read_transcript(session)
    if not transcript.readable:
        return [f"(no preview: {transcript.error})"]
    home_str = str(Path.home())
    sources = ", ".join(p.replace(home_str, "~") for p in transcript.source_paths)
    n = len(transcript.messages)
    lines = [c("dim", f"{n} message{'' if n == 1 else 's'}")]
    if sources:
        # Plain so the view wraps it: the file name at the end is the part
        # that tells two forks apart, and a cut label would lose it.
        lines.append(sources)
    if not transcript.messages:
        lines.append("(no messages)")
        return lines
    pending_tools = 0
    for m in transcript.messages:
        if m.kind == "tool" or m.role == "tool":
            pending_tools += 1
            continue
        if pending_tools:
            lines.append(_tool_run_line(pending_tools))
            pending_tools = 0
        lines.append("")
        if m.role == "human":
            # A human turn is a prompt, not a document: markup in it is
            # usually meant literally, so it goes out as typed, painted so
            # the eye can find where the user spoke without a label.
            lines.extend(c("user_bg", line) for line in m.text.splitlines() or [""])
        else:
            lines.extend(render_markdown(m.text) or [""])
    if pending_tools:
        lines.append(_tool_run_line(pending_tools))
    return lines


def _resume_session(session) -> int:
    if not os.path.exists(session.path):
        print(c("red", f"Directory not found: {session.path}"))
        return 1

    print(c("cyan", f"Resuming {session.agent} session..."))
    os.chdir(session.path)

    cmd_args = _resume_cmd_args(session)
    from quiver.harness.commands import cmd_use

    return cmd_use(cmd_args)


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

        return _resume_session(sessions[use_index - 1])

    # The picker prefixes every row with a 2-char pointer + space, so its
    # table has to be built 2 columns narrower or the redraw wraps.
    statuses = session_statuses(sessions)
    table = _build_session_table(
        sessions, reserve=2 if parsed.interactive else 0, statuses=statuses
    )

    print(f"\n{c('bold', 'Recent AI Sessions')}")
    legend = _status_legend(statuses)
    if legend:
        print(legend)
    print()

    if parsed.interactive:
        lines = table.render()
        header, rows = lines[:2], lines[2:]
        # Space opens the highlighted session's transcript in a read-only
        # view. Reading one means a glob over the harness's session root,
        # so each row is read at most once per picker run.
        cache: dict[int, list[str]] = {}

        def preview(index: int) -> list[str]:
            if index not in cache:
                cache[index] = _session_preview(sessions[index])
            return cache[index]

        choice = pick_session(rows, header=header, preview=preview)
        if choice is None:
            return 0
        return _resume_session(sessions[choice])

    for line in table.render():
        print(line)
    print()
    if search:
        print(c("dim", f"  filter: --search {search!r}  ·  {len(sessions)} match(es)"))
        print()
    _print_parser_failures()
    return 0
