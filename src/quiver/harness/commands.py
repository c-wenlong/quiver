"""Harness registry commands: list, info, add, remove, use, check, tags, aliases, star."""

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from quiver.console import c, cpad, truncate, visible_len
from quiver.harness.columns import (
    COLUMNS,
    DEFAULT_COLUMNS,
    load_columns,
    load_window,
    next_window,
    save_columns,
    save_window,
    window_label,
)
from quiver.harness.registry import load_registry, resolve, save_registry
from quiver.init.layout import link_states
from quiver.harness.stars import is_starred, load_stars, toggle_star, unstar
from quiver.harness.tools import extract_version_number, is_installed, live_version
from quiver.prompt import read_line
from quiver.table import Table


def _session_counts(days=None):
    from quiver.sessions.usage import session_counts

    return session_counts(load_window() if days is None else days)


def _sort_tools(tools: dict, counts: dict[str, int], stars: list[str]):
    """Starred first, then unstarred. Each group by 100d usage, then name.

    Starred rows used to hold their pin order, which meant a favourite you had
    not touched in months sat above one you use daily. Both groups now answer
    the same question, "what am I actually using", with the star deciding only
    which block you are in.
    """
    starred = set(stars)

    def key(item):
        name = item[0]
        return (0 if name in starred else 1, -counts.get(name, 0), name)

    return sorted(tools.items(), key=key)


# Which instruction filename each harness insists on, so the column shows
# what is actually linked rather than a bare tick.
_AGENTS_FILENAMES = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "cursor": "AGENTS.md",
    "gemini": "GEMINI.md",
    "qwen-code": "QWEN.md",
    "crush": "CRUSH.md",
    "opencode": "AGENTS.md",
    "droid": "AGENTS.md",
    "amp": "AGENTS.md",
}


# Link-state glyphs for `swe list --links`. "linked" is the goal state;
# "conflict" means a real file is sitting where the symlink should be;
# "skipped" means the harness is not installed. A tool with no known
# instruction-file convention gets None and renders as a dim dot.
_LINK_GLYPH = {
    "linked": ("green", "\u2713"),
    "create": ("yellow", "\u25cb"),
    "relink": ("yellow", "\u21bb"),
    "conflict": ("red", "\u2717"),
    "skipped": ("dim", "\u00b7"),
    # Holds skills that exist nowhere else, so quiver deliberately leaves it
    # alone. Not an error, and not a tick either.
    "keep": ("yellow", "\u25cb"),
    # A real directory whose contents are all duplicates or empty, so init can
    # replace it with the link without losing anything.
    "absorb": ("yellow", "\u25cb"),
}


def _link_cell(state, label, width):
    """Render one link column: glyph plus what it links, padded to width."""
    if state is None:
        return c("dim", "\u00b7".ljust(width))
    colour, glyph = _LINK_GLYPH.get(state, ("dim", "?"))
    return c(colour, glyph) + " " + c("dim" if state != "linked" else "green", label) + " " * max(
        0, width - visible_len(c(colour, glyph)) - 1 - len(label)
    )



def cmd_list_edit(args=None) -> int:
    """Pick which columns `swe list` shows."""
    from quiver.multiselect import Choice, multiselect

    args = list(args or [])
    if args and args[0] in ("-h", "--help", "help"):
        print(f"\n  {c('bold', 'swe list edit')} — choose the columns swe list shows\n")
        print(f"  {c('dim', 'space toggles · a selects all · n clears · enter saves · q cancels')}")
        print(f"  {c('dim', 'NAME and the favourite marker are always shown.')}\n")
        return 0
    if args and args[0] == "--reset":
        save_columns(DEFAULT_COLUMNS)
        save_window(100)
        print(f"  {c('green', 'reset')} {c('dim', 'to ' + ', '.join(DEFAULT_COLUMNS))}")
        return 0

    current = load_columns()
    window = load_window()
    choices = []
    for col in COLUMNS:
        if col.key == "sess":
            choices.append(Choice(
                col.key, col.label, col.about, col.locked,
                value=window, cycle=next_window, render_value=window_label))
        else:
            choices.append(Choice(col.key, col.label, col.about, col.locked))
    picked = multiselect(choices, current, title="  Columns for swe list")
    if picked is None:
        print(f"  {c('dim', 'cancelled, nothing changed')}")
        return 0

    chosen_window = next(
        (ch.value for ch in choices if ch.key == "sess"), window)
    if chosen_window != window:
        save_window(chosen_window)
        print(f"  {c('green', 'window')} {c('dim', window_label(window) + ' -> ' + window_label(chosen_window))}")
    saved = save_columns(picked)
    added = [k for k in saved if k not in current]
    removed = [k for k in current if k not in saved]
    print(f"  {c('green', 'saved')}  {c('dim', ', '.join(saved))}")
    if added:
        print(f"  {c('dim', '+ ' + ', '.join(added))}")
    if removed:
        print(f"  {c('dim', '- ' + ', '.join(removed))}")
    if "rate" in saved:
        note = ("REMAINING fetches over the network, so swe list will be "
                "slower on a cold cache")
        print(f"  {c('yellow', 'note')} {c('dim', note)}")
    return 0


def cmd_list_legend(args=None) -> int:
    """Explain the glyphs in the AGENTS.MD and SKILLS columns.

    The columns render as single characters with no key anywhere, so a
    reader who forgot what a yellow circle meant had nowhere to look.
    """
    print(f"\n  {c('bold', 'AGENTS.MD and SKILLS glyphs')}\n")
    rows = [
        ("linked", "points at the shared copy in ~/.quiver"),
        ("relink", "is a symlink, but aimed somewhere other than ~/.quiver"),
        ("create", "nothing there yet; swe init would create the link"),
        ("absorb", "a real directory, but its contents are all duplicates or empty"),
        ("keep", "a real directory holding files that exist nowhere else, left alone"),
        ("conflict", "a real file sits where the link should go; needs --force"),
        ("skipped", "harness is not installed"),
    ]
    for state, meaning in rows:
        colour, glyph = _LINK_GLYPH.get(state, ("dim", "?"))
        print(f"  {c(colour, glyph)}  {c('bold', state.ljust(9))} {c('dim', meaning)}")
    print(f"  {c('dim', '·')}  {c('bold', 'n/a'.ljust(9))} "
          f"{c('dim', 'quiver knows no instruction filename for this harness')}")
    print(f"\n  {c('dim', 'swe find skills -r shows the same states as words, per path.')}\n")
    return 0


def cmd_list(args):
    args = list(args or [])
    if args and args[0] == "edit":
        return cmd_list_edit(args[1:])
    if args and args[0] in ("legend", "--legend", "key"):
        return cmd_list_legend(args[1:])

    # Refresh aliases bypass both the session and rate-limit caches.
    refresh_flags = {"--refresh", "-r", "-n"}
    refresh = any(arg in refresh_flags for arg in args)
    args = [arg for arg in args if arg not in refresh_flags]

    # Usage is opt-in. Rate limits are the only part of `swe list` that
    # touches the network, and on a cold cache they cost ~850ms against
    # ~15ms for everything else combined. Asking for them explicitly keeps
    # the default listing instant. Refreshing implies wanting to see them.
    usage_flags = {"--usage", "-u"}
    usage_view = refresh or any(arg in usage_flags for arg in args)
    args = [arg for arg in args if arg not in usage_flags]

    # --links swaps in quiver link status. It is a different question about
    # the same rows, so it replaces columns rather than widening an already
    # wide table.
    link_flags = {"--links", "-L"}
    links_view = any(arg in link_flags for arg in args)
    args = [arg for arg in args if arg not in link_flags]
    if links_view:
        usage_view = False
    if refresh:
        from quiver.sessions.aggregator import invalidate_cache as _inv_sessions
        from quiver.sessions.usage import invalidate_counts_cache

        _inv_sessions()
        # The 100d counts cache holds for a day, so --refresh has to clear it
        # too or the flag would silently do nothing for that column.
        invalidate_counts_cache()

    tools = load_registry()
    tag_filter = args[0].lstrip("-") if args else None
    counts = _session_counts()
    stars = load_stars()
    starred_set = set(stars)

    # Fetch rate limits (cached 5min by default, refresh flags bypass;
    # override TTL with SWE_RATE_LIMITS_TTL=<seconds>)
    from quiver.harness.rate_limits import get_all_rate_limits

    # Which columns will actually render, so the data behind them is fetched
    # exactly when it is needed. Gating on the flags alone was wrong once the
    # set became configurable: a saved AGENTS.MD column rendered every row as
    # "no known convention" because nothing had populated the link state.
    wanted = set(load_columns())
    if links_view:
        wanted |= {"agents", "skills"}
    if usage_view:
        wanted |= {"sess", "rate"}

    rate_limits = (
        get_all_rate_limits(
            use_cache=not refresh,
            tool_names={name for name in starred_set if name in tools},
        )
        if "rate" in wanted
        else {}
    )
    link_status = link_states() if wanted & {"agents", "skills"} else {}

    print(f"\n{c('bold', 'AI Coding Tools')}\n")

    table = Table(column_gap=" │ ")
    _head = wanted
    table.add_column("mark", "", width=2, kind="preformatted")
    table.add_column("name", "NAME", width=16, kind="text")
    if "command" in _head:
        table.add_column("command", "COMMAND", width=18, kind="text")
    if "version" in _head:
        table.add_column("version", "VERSION", width=12, kind="text")
    if "aliases" in _head:
        table.add_column("aliases", "ALIASES", width=12, kind="list",
                         color="cyan", empty="—")
    if "sess" in wanted:
        table.add_column("sess", window_label(load_window()), width=8,
                         kind="preformatted", empty="—")
    if "rate" in wanted:
        table.add_column(
            "rate", "REMAINING", width=14, kind="preformatted",
            trust_cell_width=True,
        )
    if "agents" in wanted:
        table.add_column("agents", "AGENTS.MD", width=22, kind="preformatted")
    if "skills" in wanted:
        table.add_column("skills", "SKILLS", width=12, kind="preformatted")
    if "inst" in wanted:
        table.add_column("inst", "INST", width=4, kind="preformatted")
    if "desc" in wanted:
        # Wider when nothing else is competing for the row.
        narrow = wanted & {"sess", "rate", "agents", "skills"}
        table.add_column("desc", "DESCRIPTION",
                         width=36 if narrow else 46, kind="text")

    shown_starred = False
    for name, info in _sort_tools(tools, counts, stars):
        if tag_filter and tag_filter not in info.get("tags", []):
            continue

        installed = is_installed(info["command"])
        status = c("green", "✓") if installed else c("red", "✗")
        ver = truncate(info.get("version") or "—", 12)
        aliases = [a for a in info.get("aliases", []) if a != name]
        desc_text = info.get("description", "")

        # desc cell now sits behind the explicit " | " separator, so
        # no extra prepend is needed - Table renders `cell | cell`
        # directly with the gap string itself providing the visual gap.
        desc_padded = truncate(desc_text, 36)

        # Session column: 3 visual states (absent=dim em-dash,
        # present-zero=dim digit, positive=green digit). All are
        # right-aligned within the 8-char width.
        if name in counts:
            sess_n = counts.get(name, 0)
            sess_cell = (
                c("green", f"{sess_n:>8}") if sess_n > 0
                else c("dim", f"{sess_n:>8}")
            )
        else:
            sess_cell = c("dim", f"{'—':>8}")

        favourited = name in starred_set
        accent = None
        if favourited:
            shown_starred = True
            mark_cell = c("neon_pink", " \u2605")  # 1 space + ★ = 2 visible (matches column width)
            accent = "neon"
        else:
            mark_cell = "  "  # 2 spaces of plain indent

        # Inst cell: padded status glyph (visible_width(status)=1).
        inst_cell = status + " " * max(0, 4 - visible_len(status))

        # Remaining cell: format_column returns its own ANSI-coloured string
        # but its visible width is variable ("70% —" = 5 chars vs
        # "100% 5d18h" = 10 chars). With trust_cell_width=True the
        # Table does NOT pad to the column width, so rows with longer
        # quota content would push INST/DESCRIPTION columns right and
        # break the grid. Pre-pad to the column width (14) here so the
        # remaining cell is exactly 14 visible chars regardless of payload
        # — the actual character gap remains _column_gap_str (" | ").
        rate_cell_width = 14
        rl = rate_limits.get(name)
        rate_cell = (
            "".join((
                rl.format_column(),
                " " * max(0, rate_cell_width - visible_len(rl.format_column())),
            ))
            if rl else
            c("dim", "—") + " " * max(0, rate_cell_width - visible_len(c("dim", "—")))
        )

        row = {"mark": mark_cell, "name": name}
        if "command" in wanted:
            row["command"] = info["command"]
        if "version" in wanted:
            row["version"] = ver
        if "aliases" in wanted:
            row["aliases"] = aliases
        if "inst" in wanted:
            row["inst"] = inst_cell
        if "agents" in wanted or "skills" in wanted:
            states = link_status.get(name, {})
            if "agents" in wanted:
                row["agents"] = _link_cell(
                    states.get("agents"), _AGENTS_FILENAMES.get(name, ""), 22)
            if "skills" in wanted:
                row["skills"] = _link_cell(states.get("skills"), "skills/", 12)
        if "sess" in wanted:
            row["sess"] = sess_cell
        if "rate" in wanted:
            row["rate"] = rate_cell
        if "desc" in wanted:
            narrow = wanted & {"sess", "rate", "agents", "skills"}
            row["desc"] = desc_padded if narrow else truncate(desc_text, 46)

        table.add_row(row, accent=accent)

    for line in table.render():
        print(line)

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

    # Two-column FIELD | VALUE table.
    # VALUE uses ``preformatted`` + ``trust_cell_width=True`` because
    # the value cell is sometimes ANSI-coloured (green/red for Status)
    # and sometimes variable-width (paths, multi-line descriptions).
    # ``fit="content"`` lets long paths / descriptions expand beyond
    # the column width rather than truncate — matches the old
    # ``print(f"  {label:<16} {val}")`` behaviour where overflow
    # scrolled rather than got cut.
    table = Table()
    table.add_column("label", "FIELD", width=16, kind="text")
    table.add_column(
        "value", "VALUE", width=64,
        kind="preformatted", trust_cell_width=True, fit="content",
    )

    rows = [
        ("Command:", info["command"]),
        ("Aliases:", ", ".join(aliases) if aliases else "—"),
        ("Description:", info.get("description") or "—"),
        ("Version:", info.get("version") or "unknown"),
        ("Tags:", ", ".join(info.get("tags", [])) or "—"),
        (
            "Status:",
            c("green", "installed") if installed else c("red", "not installed"),
        ),
        ("Path:", path),
    ]
    for label, val in rows:
        table.add_row({"label": label, "value": val})
    if info.get("notes"):
        # ``Notes:`` is conditional — matches the pre-migration
        # behaviour where the optional row was only appended when
        # ``info["notes"]`` was truthy.
        table.add_row({"label": "Notes:", "value": info["notes"]})

    for line in table.render():
        print(line)
    print()


_ADD_FIELDS = ("name", "command", "description", "aliases", "tags")


def _prompt_field(label: str, default: str = "") -> str:
    """Prompt for one field; Enter keeps the default (shown dim in brackets)."""
    hint = f" {c('dim', '[' + default + ']')}" if default else ""
    try:
        return read_line(c("cyan", f"  {label}>{hint} "))
    except (EOFError, KeyboardInterrupt):
        print()
        raise


def _ask(label: str, default: str) -> str:
    """Prompt for a field; an empty answer keeps the default."""
    raw = _prompt_field(label, default)
    val = raw.strip()
    return val if val else default


def _add_interactive(args: list[str]) -> int:
    """Interactive form: walk each field, show a summary, confirm before saving."""
    tools = load_registry()

    # Pre-fill from positional args / flags (same shape as flag-based cmd_add).
    rest = [a for a in args if a not in ("-i", "--interactive")]
    name_pre = command_pre = desc_pre = ""
    aliases_pre = ""
    tags_pre = "agentic, coding"
    positional: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--aliases" and i + 1 < len(rest):
            aliases_pre = rest[i + 1]; i += 2; continue
        if a == "--tags" and i + 1 < len(rest):
            tags_pre = rest[i + 1]; i += 2; continue
        if a == "--description" and i + 1 < len(rest):
            desc_pre = rest[i + 1]; i += 2; continue
        if a == "--command" and i + 1 < len(rest):
            command_pre = rest[i + 1]; i += 2; continue
        if not a.startswith("--"):
            positional.append(a)
        i += 1
    if positional:
        name_pre = positional[0]
    if len(positional) > 1:
        command_pre = positional[1]
    if len(positional) > 2:
        desc_pre = positional[2]

    draft = {
        "name": name_pre,
        "command": command_pre,
        "description": desc_pre,
        "aliases": aliases_pre,
        "tags": tags_pre,
    }

    print(c("bold", "\n  swe add — interactive"))
    print(c("dim", "  Walk through each field. Enter keeps the [default]. Ctrl-C to cancel.\n"))

    while True:
        try:
            # --- name (required, collision-checked) ---
            draft["name"] = _ask("name", draft["name"])
            while not draft["name"]:
                print(c("red", "  name is required"))
                draft["name"] = _ask("name", draft["name"])
            owner = resolve(tools, draft["name"])
            if owner and owner != draft["name"]:
                print(c("yellow", f"  ⚠ '{draft['name']}' is an alias of '{owner}' — choose a different name."))
                continue
            if draft["name"] in tools:
                print(c("yellow", f"  ⚠ '{draft['name']}' already exists — saving will update it."))

            # --- command (required) ---
            draft["command"] = _ask("command", draft["command"])
            while not draft["command"]:
                print(c("red", "  command is required"))
                draft["command"] = _ask("command", draft["command"])

            # --- optional fields ---
            draft["description"] = _ask("description", draft["description"])
            draft["aliases"] = _ask("aliases", draft["aliases"])
            draft["tags"] = _ask("tags", draft["tags"])
        except (EOFError, KeyboardInterrupt):
            print(c("dim", "  Cancelled."))
            return 1

        # alias collision check
        aliases_list = _split_csv(draft["aliases"])
        collision = _alias_collision(tools, draft["name"], aliases_list)
        if collision:
            print(c("yellow", f"  ⚠ {collision}"))
            print(c("dim", "  Re-enter aliases (Enter to keep, or type new ones)."))
            try:
                draft["aliases"] = _ask("aliases", draft["aliases"])
            except (EOFError, KeyboardInterrupt):
                print(c("dim", "  Cancelled."))
                return 1
            aliases_list = _split_csv(draft["aliases"])
            if _alias_collision(tools, draft["name"], aliases_list):
                print(c("yellow", f"  ⚠ {collision} — not saved."))
                print(c("dim", "  Restarting field walk…\n"))
                continue

        # --- summary ---
        tags_list = _split_csv(draft["tags"])
        installed = is_installed(draft["command"])
        version = live_version(draft["command"]) if installed else None
        status_label = c("green", "installed") if installed else c("yellow", "not in PATH")
        print()
        print(c("bold", "  Summary"))
        print(f"    name:        {draft['name']}")
        print(f"    command:     {draft['command']}  ({status_label})")
        print(f"    description: {draft['description'] or c('dim', '—')}")
        print(f"    aliases:     {_format_list(aliases_list)}")
        print(f"    tags:        {_format_list(tags_list)}")
        if version:
            print(f"    version:     {c('dim', version)}  {c('dim', '(auto-detected)')}")
        print()

        # --- confirm ---
        try:
            choice = _prompt_field("Save? [Y]es / [e]dit / [c]ancel", "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(c("dim", "  Cancelled."))
            return 1
        if choice in ("", "y", "yes"):
            action = "Updated" if draft["name"] in tools else "Added"
            tools[draft["name"]] = {
                "command": draft["command"],
                "description": draft["description"],
                "version": version,
                "tags": tags_list,
                "aliases": aliases_list,
                "added": datetime.now().isoformat(),
            }
            save_registry(tools)
            saved_status = c("green", "installed") if installed else c("yellow", "not yet in PATH")
            alias_str = f"  aliases: {', '.join(aliases_list)}" if aliases_list else ""
            print(f"  {c('green', '✓')} {action} '{draft['name']}' → '{draft['command']}' ({saved_status}){alias_str}")
            return 0
        if choice in ("e", "edit"):
            print(c("dim", "  Re-editing fields…\n"))
            continue
        print(c("dim", "  Cancelled."))
        return 1


def cmd_add(args):
    if "-i" in args or "--interactive" in args:
        return _add_interactive(args)
    if len(args) < 2:
        print(c("red", "Usage: swe add <name> <command> [description] [--aliases a,b] [--tags t1,t2]"))
        print(c("dim", "  Interactive: swe add -i   ·   swe add <name> -i"))
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

    # Widths for the two cells that ship self-coloured ANSI via the
    # `preformatted`+`trust_cell_width=True` path; the names matter
    # because they're used by ``cpad`` AND by ``table.add_column`` so
    # the rendered cell and the schema header agree on column width.
    STATUS_COL_WIDTH = 2  # "✓ " or "✗ " (1 glyph + 1 trailing space)
    INFO_COL_WIDTH = 24   # "version unknown" + headroom

    tools = load_registry()
    updated = False
    off_path_notes: list[str] = []
    print(f"\n{c('bold', 'Checking AI tools...')}\n")

    table = Table()
    table.add_column("status", "", width=STATUS_COL_WIDTH,
                     kind="preformatted", trust_cell_width=True)
    table.add_column("name", "NAME", width=22, kind="text")
    table.add_column("aliases", "ALIASES", width=18,
                     kind="list", color="cyan", empty="—")
    table.add_column("info", "INFO", width=INFO_COL_WIDTH,
                     kind="preformatted", trust_cell_width=True)

    for name, info in sorted(tools.items()):
        aliases = [a for a in info.get("aliases", []) if a != name]
        command = info["command"]
        if is_installed(command):
            # Probe live version + heal stored version BEFORE we render
            # the row (this is the check-and-heal flow's side-effect,
            # not just a display step).
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
            table.add_row({
                "status": cpad("green", "✓", STATUS_COL_WIDTH),
                "name": name,
                "aliases": aliases,
                "info": cpad("dim", display, INFO_COL_WIDTH),
            })
        else:
            table.add_row({
                "status": cpad("red", "✗", STATUS_COL_WIDTH),
                "name": name,
                "aliases": aliases,
                "info": cpad("dim", "not installed", INFO_COL_WIDTH),
            })

    for line in table.render():
        print(line)

    orphans = find_off_path_tools(tools)
    if orphans:
        # Off-PATH diagnostic block lives BELOW the table — these
        # are not table rows (each orphan has a multi-line fix
        # recipe, so they don't fit the grid). Keeping them as
        # plain strings preserves the original output structure.
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

    if not tag_map:
        # No tags in registry at all — render a one-line notice
        # rather than an empty table (preserves the old behaviour
        # of just printing the title and trailing newline).
        print(f"\n{c('bold', 'Available tags')}\n")
        print(c("dim", "  No tags found.\n"))
        return

    print(f"\n{c('bold', 'Available tags')}\n")

    # Two-column TAG | TOOLS table. The TAG cell is ``preformatted``
    # + ``trust_cell_width=True`` so the cyan colour from cpad()
    # isn't double-wrapped by the Table renderer; the TOOLS cell
    # uses the list kind so multi-tool lists join with ``, `` and
    # adapt to fit mode.
    table = Table()
    table.add_column(
        "tag", "TAG", width=14,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "tools", "TOOLS", width=40,
        kind="list", color="dim", empty="—",
    )

    for tag in sorted(tag_map):
        table.add_row({
            "tag": cpad("cyan", tag, 14),
            "tools": sorted(tag_map[tag]),
        })

    for line in table.render():
        print(line)
    print()


def cmd_aliases(args):
    tools = load_registry()
    print(f"\n{c('bold', 'Short aliases')}\n")

    # Two-column ALIASES | NAME table; the ``column_gap=" → "`` makes
    # the arrow separator part of the table so every row gets the
    # same horizontal alignment structurally (not visually padded).
    table = Table(column_gap=" → ")
    table.add_column(
        "aliases", "ALIASES", width=14,
        kind="list", color="cyan", empty="—",
    )
    table.add_column("name", "NAME", width=20, kind="text", fit="content")

    for name, info in sorted(tools.items()):
        aliases = [a for a in info.get("aliases", []) if a != name]
        if aliases:
            table.add_row({"aliases": aliases, "name": name})

    for line in table.render():
        print(line)
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
