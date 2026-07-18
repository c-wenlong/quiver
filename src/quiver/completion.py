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
    ("star", "Favourite a harness"),
    ("unstar", "Remove favourite"),
    ("use", "Launch a tool"),
    ("check", "Verify installs + versions"),
    ("doctor", "Diagnose Node/PATH issues"),
    ("install", "Install a harness"),
    ("session", "Show recent sessions"),
    ("models", "Show model usage"),
    ("skills", "List agent skills"),
    ("tags", "Show all tags"),
    ("aliases", "Show aliases"),
    ("providers", "Manage API keys"),
    ("mcp", "Manage MCP servers"),
    ("harness", "Harness registry utils"),
    ("setup", "Onboarding wizard"),
    ("autocomplete", "Generate shell completion"),
]

# Commands that take a tool name/alias as their first positional argument.
_TOOL_TARGET_COMMANDS = frozenset({
    "use", "run", "star", "favourite", "favorite",
    "unstar", "info", "edit", "remove", "rm",
})

# Flags for specific commands.
_COMMAND_FLAGS: dict[str, list[tuple[str, str]]] = {
    "list": [("--refresh", "Bypass session cache"), ("-r", "Short for --refresh")],
    "session": [("--search", "Filter sessions"), ("-q", "Short for --search")],
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
        flags = _COMMAND_FLAGS.get(cmd, [])
        return _filter_by_prefix(flags, partial)

    # Tool-name completion for commands that take a tool argument
    if cmd in _TOOL_TARGET_COMMANDS and len(rest) == 0:
        return _tool_completions(partial)

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
