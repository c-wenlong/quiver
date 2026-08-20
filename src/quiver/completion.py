"""Context-aware completion engine for `swe __complete`."""

from __future__ import annotations

from quiver.harness.registry import load_registry

# Primary subcommands shown in completion (excludes aliases and hidden commands).
# (name, description) — descriptions are short for shell display.
_PRIMARY_COMMANDS: list[tuple[str, str]] = [
    ("list", "List all tools"),
    ("info", "Show tool details"),
    ("add", "Register a new tool"),
    ("edit", "Edit tool fields"),
    ("remove", "Remove a tool"),
    ("harness", "Registry utilities: star, archive, discover"),
    ("hs", "Short for swe harness"),
    ("use", "Launch a tool"),
    ("check", "Verify installs + versions"),
    ("doctor", "Diagnose Node/PATH issues"),
    ("install", "Install a harness"),
    ("session", "Show recent sessions"),
    ("report", "Summarize coding sessions"),
    ("models", "Show model usage"),
    ("skills", "List agent skills"),
    ("tags", "Show all tags"),
    ("aliases", "Show aliases"),
    ("providers", "Manage API keys"),
    ("mcp", "Manage MCP servers"),
    ("harness", "Harness registry utils"),
    ("setup", "Onboarding wizard"),
    ("config", "View or update configuration"),
    ("autocomplete", "Generate shell completion"),
]

# Commands that take a tool name/alias as their first positional argument.
_TOOL_TARGET_COMMANDS = frozenset({
    "use", "run",
    "info", "edit", "remove", "rm",
})

# Flags for specific commands.
_COMMAND_FLAGS: dict[str, list[tuple[str, str]]] = {
    "list": [
        ("--scope=active", "Hide archived harnesses (default)"),
        ("--scope=archived", "Show only archived harnesses"),
        ("--scope=all", "Show everything, archived marked"),
        ("--refresh", "Fetch new data"),
        ("-r", "Short for --refresh"),
        ("-n", "Fetch new data"),
    ],
    "session": [
        ("--search", "Filter sessions"), ("-q", "Short for --search"),
        ("--days", "Past N calendar days"), ("-d", "Short for --days"),
        ("--weeks", "Past N calendar weeks"), ("-w", "Short for --weeks"),
        ("--start", "Range start date"), ("-s", "Short for --start"),
        ("--end", "Range end date"), ("-e", "Short for --end"),
        ("--agent", "Filter by agent"), ("--here", "Current project only"),
    ],
    "report": [
        ("--days", "Override with N calendar days"), ("-d", "Short for --days"),
        ("--weeks", "Override with N calendar weeks"), ("-w", "Short for --weeks"),
        ("--start", "Override range start"), ("-s", "Short for --start"),
        ("--end", "Override range end"), ("-e", "Short for --end"),
        ("--here", "Current project only"), ("--agent", "Filter by agent"),
        ("--session-harness", "Cheap summarizer harness"),
        ("--session-model", "Cheap summarizer model"),
        ("--writer-harness", "Final writer harness"),
        ("--writer-model", "Final writer model"),
    ],
    "setup": [
        ("--quick", "Only missing or actionable stages"),
        ("--apply", "Apply safe discovery changes"),
        ("--json", "Print discovery preview as JSON"),
        ("--non-interactive", "Preview without prompts or writes"),
    ],
    "add": [("-i", "Interactive form"), ("--interactive", "Interactive form")],
}

# Subcommands that themselves take a tool name, e.g. `swe hs star cl<TAB>`.
# `swe hs list --scope=...` should offer the same flags as `swe list`.
_NESTED_FLAG_PARENTS: dict[str, frozenset[str]] = {
    "harness": frozenset({"list", "ls"}),
    "hs": frozenset({"list", "ls"}),
}

_NESTED_TOOL_TARGETS: dict[str, frozenset[str]] = {
    "harness": frozenset({"star", "archive", "favourite", "favorite", "shelve"}),
    "hs": frozenset({"star", "archive", "favourite", "favorite", "shelve"}),
}

_SUBCOMMANDS: dict[str, list[tuple[str, str]]] = {
    "harness": [
        ("list", "List every harness (swe list is the shortcut)"),
        ("ls", "Short for list"),
        ("edit", "Review every harness at once"),
        ("star", "Toggle a favourite"),
        ("archive", "Shelve a harness you have ruled out"),
        ("discover", "Scan PATH for AI coding CLIs"),
    ],
    "report": [
        ("daily", "Report since the previous daily report"),
        ("weekly", "Report since the previous weekly report"),
        ("warnings", "Show warnings for one report manifest"),
        ("followups", "List follow-ups"),
        ("followup", "Manage or work on a follow-up"),
    ],
    "hs": [
        ("list", "List every harness (swe list is the shortcut)"),
        ("ls", "Short for list"),
        ("edit", "Review every harness at once"),
        ("star", "Toggle a favourite"),
        ("archive", "Shelve a harness you have ruled out"),
        ("discover", "Scan PATH for AI coding CLIs"),
    ],
    "config": [
        ("get", "Read a resolved value"),
        ("set", "Set a value"),
        ("unset", "Remove a value"),
        ("edit", "Open config in an editor"),
        ("check", "Validate configuration"),
        ("setup", "Run interactive setup"),
    ],
    "setup": [
        ("harnesses", "Discover and register coding CLIs"),
        ("providers", "Review provider credential coverage"),
        ("mcp", "Import MCP servers"),
        ("skills", "Unify shared skill roots"),
        ("report", "Configure report models"),
        ("check", "Verify setup state"),
    ],
}


def get_completions(words: list[str]) -> list[tuple[str, str]]:
    """Return [(candidate, description)] for the given word stack.

    ``words`` is the list of words after ``swe`` on the command line.
    The last element may be empty (user pressed TAB after a space) or a
    partial word being typed.
    """
    if not words:
        return list(_PRIMARY_COMMANDS)

    # Only one word — completing the subcommand itself
    if len(words) == 1:
        partial = words[0]
        if partial.startswith("-"):
            return []
        return _filter_by_prefix(_PRIMARY_COMMANDS, partial)

    cmd = words[0]
    # Drop the partial last word for context analysis
    partial = words[-1]
    rest = words[1:-1]  # positional args between cmd and partial

    # Flag completion
    if partial.startswith("-"):
        nested = _NESTED_FLAG_PARENTS.get(cmd)
        if nested and len(rest) == 1 and rest[0] in nested:
            return _filter_by_prefix(_COMMAND_FLAGS.get("list", []), partial)
        flags = _COMMAND_FLAGS.get(cmd, [])
        return _filter_by_prefix(flags, partial)

    # Tool-name completion for commands that take a tool argument
    if cmd in _TOOL_TARGET_COMMANDS and len(rest) == 0:
        return _tool_completions(partial)

    # `swe hs star <tool>` — the tool name sits one level deeper than the
    # flat commands handled above.
    nested = _NESTED_TOOL_TARGETS.get(cmd)
    if nested and len(rest) == 1 and rest[0] in nested:
        return _tool_completions(partial)

    if cmd in _SUBCOMMANDS and len(rest) == 0:
        return _filter_by_prefix(_SUBCOMMANDS[cmd], partial)

    # Tag completion for `swe list [tag]`
    if cmd in ("list", "ls") and len(rest) == 0:
        return _tag_completions(partial)

    return []


def _filter_by_prefix(
    candidates: list[tuple[str, str]], prefix: str
) -> list[tuple[str, str]]:
    if not prefix:
        return list(candidates)
    return [(c, d) for c, d in candidates if c.startswith(prefix)]


def _tool_completions(partial: str = "") -> list[tuple[str, str]]:
    """Return tool names + aliases from the registry."""
    try:
        registry = load_registry()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for name, tool in sorted(registry.items()):
        desc = tool.get("description") or ""
        if not partial or name.startswith(partial):
            out.append((name, desc))
        for alias in tool.get("aliases") or []:
            if not partial or alias.startswith(partial):
                out.append((alias, f"alias for {name}"))
    return out


def _tag_completions(partial: str = "") -> list[tuple[str, str]]:
    """Return tag names from the registry."""
    try:
        registry = load_registry()
    except Exception:
        return []
    tags: dict[str, int] = {}
    for tool in registry.values():
        for tag in tool.get("tags") or []:
            tags[tag] = tags.get(tag, 0) + 1
    out = [(tag, f"{count} tool(s)") for tag, count in sorted(tags.items())]
    return _filter_by_prefix(out, partial)
