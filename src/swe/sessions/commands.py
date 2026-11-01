"""Session and model analytics CLI commands."""

import os
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from swe.console import c, cpad, fit_widths, sanitize, terminal_width, tilde, truncate, visible_len
from swe.flags import expand_value_flags
from swe.json_output import emit, error_stream, take_json_flag
from swe.markdown import render_markdown
from swe.sessions import failures
from swe.sessions import search_index
from swe.sessions.aggregator import get_all_sessions
from swe.sessions.identity import launch_tool
from swe.sessions.models import SessionRow
from swe.sessions.models_analytics import classify_provider, collect_model_usage
from swe.sessions.picker import pick_session
from swe.sessions.resume import (
    ACCOUNT_HOME_ENV,
    LIMITED_RESUME,
    RESUME_FLAGS,
    codex_resume_args,
    launch_tool_name,
    resume_flags,
    resume_json,
)
from swe.sessions.query import SessionQuery, calendar_range_ms
from swe.sessions.status import session_statuses
from swe.table import Table

# Kept importable from here: tests and older call sites use these names.
_codex_resume_args = codex_resume_args
_launch_tool_name = launch_tool_name
_RESUME_FLAGS = RESUME_FLAGS


def _help_requested(args: list[str], value_flags: set[str]) -> bool:
    """`-h`/`--help` anywhere, or bare `help` in first position.

    Tokens consumed as values of ``value_flags`` don't count, so
    ``--search -h``'s ``-h`` is a search term, not a help request.
    """
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            return True
        if a in value_flags:
            i += 2
            continue
        i += 1
    return bool(args) and args[0] == "help"


def cmd_models(args):
    if _help_requested(args, set()):
        from swe.help_text import cmd_help

        return cmd_help(["models"]) or 0
    by_tool = False
    show_providers = False
    for arg in args:
        if arg in ("--by-tool", "-t"):
            by_tool = True
        elif arg in ("--providers", "-p"):
            show_providers = True
        else:
            print(c("red", f"Unknown argument: {arg}"))
            return 1

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
    row_index = 0
    # Row indices where a new tool group starts; the blank separators have
    # to land inside the rendered table, not above it — printing them in
    # this loop ran ahead of the deferred render() and pushed every one
    # of them to the top.
    group_starts: list[int] = []
    for tool in sorted(grouped):
        entries = sorted(grouped[tool].items(), key=lambda x: -x[1])
        for model, cnt in entries:
            grand_total += cnt
            provider = classify_provider(model)
            if by_tool:
                if last_tool is not None and last_tool != tool:
                    group_starts.append(row_index)
                # Update inside the row loop: last_tool must trail the
                # current row, not lag a whole group, or every row after
                # the first would read as a new boundary.
                last_tool = tool
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
            row_index += 1

    lines = t.render()
    # Header and separator occupy the first two rendered lines; each prior
    # insertion shifts every later boundary down by one.
    for i, boundary in enumerate(group_starts):
        lines.insert(2 + boundary + i, "")
    for line in lines:
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
    try:
        args = expand_value_flags(args, {
            "--agent", "--search", "--grep",
            "--days", "--weeks", "--start", "--end",
        })
    except ValueError as exc:
        print(c("red", str(exc)))
        return None

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
        if args[i] == "use":
            if i + 1 >= len(args) or not args[i + 1].isdigit():
                print(c("red", "Usage: swe session use <N>  (N is the # column from swe session)"))
                return None
            use_index = int(args[i + 1])
            i += 2
        elif args[i] in ("--interactive", "-i"):
            interactive = True
            i += 1
        elif args[i] == "--agent":
            agent_filter = args[i + 1]
            i += 2
        elif args[i] in ("--search", "--grep") or (args[i] == "-q" and i + 1 < len(args)):
            search = args[i + 1]
            i += 2
        elif args[i] == "--here":
            cwd_filter = os.getcwd()
            i += 1
        elif args[i] == "--days" or (args[i] == "-d" and i + 1 < len(args)):
            try:
                days = int(args[i + 1])
            except ValueError:
                print(c("red", "--days must be a positive integer"))
                return None
            i += 2
        elif args[i] == "--weeks" or (args[i] == "-w" and i + 1 < len(args)):
            try:
                weeks = int(args[i + 1])
            except ValueError:
                print(c("red", "--weeks must be a positive integer"))
                return None
            i += 2
        elif args[i] == "--start" or (args[i] == "-s" and i + 1 < len(args)):
            start = args[i + 1]
            i += 2
        elif args[i] == "--end" or (args[i] == "-e" and i + 1 < len(args)):
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
        except (ValueError, OverflowError) as exc:
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


def _resume_cmd_args(session) -> list[str]:
    launch = launch_tool_name(session.tool_name)
    cmd_args = [launch, *resume_flags(session)]
    if session.tool_name in LIMITED_RESUME and session.tool_name not in RESUME_FLAGS:
        if session.tool_name == "gemini":
            print(
                c(
                    "yellow",
                    f"Note: {sanitize(session.agent)} does not support CLI resume flags. "
                    "Type /resume in the prompt if needed.",
                )
            )
        else:
            print(
                c(
                    "yellow",
                    f"Note: {sanitize(session.agent)} resume flags are limited; "
                    "launching in session directory.",
                )
            )
    return cmd_args


def _hit_evidence(hit) -> dict:
    """The ``evidence`` payload one FTS hit adds to a ``--json`` row."""
    return {"snippet": hit.snippet, "role": hit.role, "ts": hit.ts}


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


def _display_title(session, width: int) -> str:
    # Titles live in harness-owned files, so they are untrusted: a stored
    # title carrying an OSC link or a BEL must not reach the terminal.
    title = sanitize(session.title or "").strip()
    if title:
        shown = truncate(title, width)
        # A name the user set is the only title shown at full intensity, in
        # italics. Titles lifted from the first prompt or generated by the
        # harness are dimmed so the renamed rows are the ones that stand out.
        if getattr(session, "title_source", "") == "rename":
            return c("italic", shown)
        return c("dim", shown)
    sid = sanitize(session.session_id or "").strip()
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


def relative_time(diff: float) -> str:
    """A session's age as the listing's short stamp ("3m ago", "Just now").

    The MCP server's recent-sessions tool prints the same stamp."""
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
    stamps = [relative_time(now - (s.timestamp / 1000)) for s in sessions]
    # Agent names come out of harness files, so they carry the same
    # escape-sequence risk titles do; the AGENT column is preformatted,
    # which means nothing downstream will clean them again.
    agents = [sanitize(s.agent or "") for s in sessions]
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

    for idx, (session, t_str, agent, status) in enumerate(
        zip(sessions, stamps, agents, statuses), start=1
    ):
        path = tilde(session.path)
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
    from swe.reports.transcripts import read_transcript

    transcript = read_transcript(session)
    if not transcript.readable:
        return [f"(no preview: {transcript.error})"]
    sources = ", ".join(tilde(p) for p in transcript.source_paths)
    n = len(transcript.messages)
    lines = [c("dim", f"{n} message{'' if n == 1 else 's'}")]
    if sources:
        # Plain so the view wraps it: the file name at the end is the part
        # that tells two forks apart, and a cut label would lose it.
        lines.append(sanitize(sources))
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
        print(c("red", f"Directory not found: {sanitize(session.path)}"))
        return 1
    if not os.path.isdir(session.path):
        # A session can record a transcript file rather than its project
        # directory; chdir on it raises NotADirectoryError.
        print(c("red", f"Not a directory: {sanitize(session.path)}"))
        return 1

    print(c("cyan", f"Resuming {sanitize(session.agent)} session..."))
    try:
        os.chdir(session.path)
    except OSError as exc:
        print(c("red", f"Cannot enter {sanitize(session.path)}: {exc.strerror or exc}"))
        return 1

    cmd_args = _resume_cmd_args(session)
    from swe.harness.commands import cmd_use

    # A session in a Quiver account home only resumes when its harness
    # looks there, not in the default home.
    env = None
    variable = ACCOUNT_HOME_ENV.get(session.tool_name)
    if variable and session.account_home:
        env = {variable: session.account_home}
    return cmd_use(cmd_args, env=env)


_INDEX_FLAGS = {"--on", "--off", "--status", "--json"}


def _index_status_line(body: dict) -> str:
    if not body.get("exists"):
        return "No search index on this machine yet."
    def count(n: int, noun: str) -> str:
        return f"{n:,} {noun}" + ("" if n == 1 else "s")

    files = body.get("files") or {}
    line = (f"{count(body.get('sessions', 0), 'session')}, "
            f"{count(body.get('messages', 0), 'message')} indexed")
    if files.get("due"):
        line += f" · {count(files['due'], 'file')} behind"
    if files.get("failed"):
        line += f" · {count(files['failed'], 'file')} failed"
    return line


def cmd_session_index(args) -> int:
    """``swe session index``: run one pass of the transcript search index,
    or with ``--on``/``--off`` choose whether the hub keeps it current."""
    from swe.sessions import search_writer

    args, json_view = take_json_flag(args)
    out = error_stream(json_view)
    unknown = [a for a in args if a not in _INDEX_FLAGS]
    if unknown:
        print(c("red", f"Unknown option for swe session index: {unknown[0]}"), file=out)
        return 1
    if "--on" in args and "--off" in args:
        print(c("red", "pick one of --on and --off"), file=out)
        return 1
    home = Path.home()
    if "--on" in args or "--off" in args:
        search_writer.set_enabled(home, "--on" in args)
    result = None
    if "--status" not in args and "--off" not in args:
        try:
            result = search_writer.run_pass(home)
        except (search_writer.IndexBusy, search_writer.IndexUnavailable) as exc:
            print(c("red", f"Search index: {exc}"), file=out)
            return 1
    body = search_writer.status(home)
    if json_view:
        if result is not None:
            body["pass"] = result.as_dict()
        emit(body)
        return 1 if body.get("error") else 0
    if result is not None:
        print(f"Read {result.indexed:,} of {result.files:,} transcripts "
              f"({result.current:,} already current) in {result.seconds:.1f}s.")
        if result.failed:
            print(c("yellow", f"{result.failed:,} transcripts could not be read."))
    if body.get("error"):
        print(c("red", f"Couldn't read the search index: {body['error']}"))
    else:
        print(_index_status_line(body))
    if body.get("last_error"):
        print(c("yellow", f"The hub's last pass failed: {body['last_error'].get('message')}"))
    state = "on" if body.get("enabled") else "off"
    print(c("dim", f"Hub keeps it current: {state} (swe session index --on|--off)"))
    return 1 if body.get("error") else 0


def cmd_session(args):
    if args and args[0] == "index":
        if any(a in ("-h", "--help", "help") for a in args[1:]):
            from swe.help_text import cmd_help

            return cmd_help(["session"]) or 0
        return cmd_session_index(args[1:])
    # `session --help` mid-listing (`session use --help`, `session -i -h`)
    # still prints help; bare `help` counts only in first position and
    # flag values are skipped, so `--search help` / `--search -h` search.
    if _help_requested(args, {
        "--agent", "--search", "--grep", "-q",
        "--days", "-d", "--weeks", "-w", "--start", "-s", "--end", "-e",
    }):
        from swe.help_text import cmd_help

        return cmd_help(["session"]) or 0
    args, json_view = take_json_flag(args)
    # Under --json a refusal must leave stdout empty, so the message goes
    # to stderr instead.
    with redirect_stdout(error_stream(json_view)):
        parsed = _parse_session_args(args)
    if parsed is None:
        return 1
    if json_view and (parsed.use_index is not None or parsed.interactive):
        print(c("red", "--json lists sessions; it cannot be combined "
                       "with use <N> or --interactive"), file=sys.stderr)
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

    # With the app's FTS index present, --search also matches transcript
    # content: metadata matches union with the sessions the index names.
    # ``search_ids`` carries those ids through SessionQuery's shared
    # filters; ``evidence`` holds the winning snippet for --json rows.
    search_ids: set[str] | None = None
    evidence: dict[str, dict] = {}
    if search:
        search_ids, best = search_index.merge_hits(
            Path.home(), search, sessions, limit=limit, agent_filter=agent_filter)
        evidence = {sid: _hit_evidence(hit) for sid, hit in best.items()}

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
            agent=agent_filter,
            cwd=cwd_filter,
            search=search,
            search_ids=frozenset(search_ids) if search_ids else None,
            limit=result_limit,
        )
    else:
        query = SessionQuery(
            agent=agent_filter,
            cwd=cwd_filter,
            search=search,
            search_ids=frozenset(search_ids) if search_ids else None,
            limit=result_limit,
        )
    sessions = query.apply(sessions)

    if json_view:
        statuses = session_statuses(sessions)
        rows = []
        for s, status in zip(sessions, statuses):
            row = SessionRow.of(s, status, resume_json(s)).to_json()
            ev = evidence.get(s.session_id)
            if ev is not None:
                row["evidence"] = ev
            rows.append(row)
        return emit({
            "sessions": rows,
            # A parser that crashed returns nothing, which reads exactly like
            # a harness with no history unless it is named.
            "failures": [
                {"harness": tool, "error": message}
                for tool, message in sorted(failures.snapshot().items())
            ],
        })

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
