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
    ("use", "Launch a tool"),
    ("check", "Verify installs + versions"),
    ("doctor", "Diagnose Node/PATH issues"),
    ("install", "Install a harness"),
    ("session", "Show recent sessions"),
    ("models", "Show model usage"),
    ("skills", "List agent skills"),
    ("tags", "Show all tags"),
    ("aliases", "Show aliases"),
    ("mcp", "Manage MCP servers"),
    ("harness", "Harness registry utils"),
    ("find", "Locate shared assets across harnesses"),
    ("providers", "Manage API keys"),
    ("config", "View or update configuration"),
    ("report", "Summarize coding sessions"),
    ("discover", "Scan PATH for AI coding CLIs"),
    ("setup", "Onboarding wizard"),
    ("init", "Create ~/.quiver and link every harness"),
    ("autocomplete", "Generate shell completion"),
]

# Commands that take a tool name/alias as their first positional argument.
_TOOL_TARGET_COMMANDS = frozenset({
    "use", "run",
    "info", "edit", "remove", "rm", "install",
})

# Flags for specific commands. Keys are either a bare command ("list") or a
# "<domain> <sub>" pair referenced by _NESTED_FLAGS below.
_COMMAND_FLAGS: dict[str, list[tuple[str, str]]] = {
    "list": [
        ("--scope=active", "Hide archived harnesses (default)"),
        ("--scope=archived", "Show only archived harnesses"),
        ("--scope=all", "Show everything, archived marked"),
        ("--usage", "Show rate-limit column"),
        ("-u", "Short for --usage"),
        ("--links", "Show skills/instruction link state"),
        ("-L", "Short for --links"),
        ("--refresh", "Fetch new data"),
        ("-r", "Short for --refresh"),
        ("-n", "Fetch new data"),
    ],
    "list edit": [
        ("--reset", "Restore default columns and window"),
    ],
    "session": [
        ("--search=", "Filter sessions"), ("-q", "Short for --search"),
        ("--grep=", "Alias for --search"),
        ("--days=", "Past N calendar days"), ("-d", "Short for --days"),
        ("--weeks=", "Past N calendar weeks"), ("-w", "Short for --weeks"),
        ("--start=", "Range start date"), ("-s", "Short for --start"),
        ("--end=", "Range end date"), ("-e", "Short for --end"),
        ("--agent=", "Filter by agent"), ("--here", "Current project only"),
        ("--interactive", "Pick a session with arrow keys"),
        ("-i", "Short for --interactive"),
    ],
    "models": [
        ("--by-tool", "Group by harness"), ("-t", "Short for --by-tool"),
        ("--providers", "Group by provider"), ("-p", "Short for --providers"),
    ],
    "skills": [
        ("--desc", "Show skill descriptions"), ("-d", "Short for --desc"),
    ],
    "skills link": [
        ("--force", "Back up and replace a non-empty skills dir"),
        ("--json", "Print the result as JSON"),
    ],
    "skills unlink": [
        ("--mkdir", "Leave an empty directory behind"),
        ("--json", "Print the result as JSON"),
    ],
    "skills move": [
        ("--from=", "Source scope"), ("--to=", "Destination scope"),
        ("--force", "Back up and replace an existing destination"),
        ("--json", "Print the result as JSON"),
    ],
    "skills discover": [
        ("--apply", "Write catalog entries"),
        ("--json", "Machine-readable output"),
        ("--all", "Include already-cataloged skills"),
    ],
    "find": [
        ("--root", "Search ~/ instead of cwd"), ("-r", "Short for --root"),
        ("--scope=global", "Global roots only"),
        ("--scope=local", "Project-local roots only"),
        ("--scope=all", "Global and local roots"),
        ("--harness=active", "Only active harnesses (default)"),
        ("--harness=all", "Include archived harnesses"),
        ("--full", "Show every match, not just counts"),
        ("--interactive", "Browse matches with arrow keys"),
        ("-i", "Short for --interactive"),
    ],
    "find mcp": [
        ("--root", "Search ~/ instead of cwd"), ("-r", "Short for --root"),
        ("--scope=global", "Global roots only"),
        ("--scope=local", "Project-local roots only"),
        ("--scope=all", "Global and local roots"),
        ("--harness=active", "Only active harnesses (default)"),
        ("--harness=all", "Include archived harnesses"),
        ("--full", "Show every match, not just counts"),
    ],
    "report": [
        ("--days=", "Override with N calendar days"), ("-d", "Short for --days"),
        ("--weeks=", "Override with N calendar weeks"), ("-w", "Short for --weeks"),
        ("--start=", "Override range start"), ("-s", "Short for --start"),
        ("--end=", "Override range end"), ("-e", "Short for --end"),
        ("--here", "Current project only"), ("--agent=", "Filter by agent"),
        ("--search=", "Filter sessions"), ("-q", "Short for --search"),
        ("--session-harness=", "Cheap summarizer harness"),
        ("--session-model=", "Cheap summarizer model"),
        ("--session-arg=", "Extra arg for the summarizer"),
        ("--writer-harness=", "Final writer harness"),
        ("--writer-model=", "Final writer model"),
        ("--writer-arg=", "Extra arg for the writer"),
    ],
    "report followups": [
        ("--status=open", "Open follow-ups only"),
        ("--status=done", "Done follow-ups only"),
        ("--status=dismissed", "Dismissed follow-ups only"),
    ],
    "report followup add": [
        ("--project=", "Attach to a project"),
    ],
    "report followup work": [
        ("--resume", "Resume the session the follow-up came from"),
        ("--new", "Start a new session"),
        ("--harness=", "Harness to run the work in"),
    ],
    "providers list": [
        ("--desc", "Show provider descriptions"), ("-d", "Short for --desc"),
        ("--api-keys-dir=", "Read key files from this directory"),
    ],
    "providers add": [
        ("--url=", "Provider base URL"),
        ("--env=", "Environment variable holding the key"),
        ("--file=", "Path to a key file"),
    ],
    "mcp discover": [
        ("--apply", "Add discoveries to ~/.quiver/mcp.json"),
        ("--json", "Machine-readable output"),
        ("--all", "Include servers already in source-of-truth"),
        ("--prune", "Remove hub servers no harness configures"),
    ],
    "mcp sync": [
        ("--all", "Sync to every known tool"),
        ("--only=", "Limit to these targets"),
        ("--except=", "Skip these targets"),
        ("--force", "Overwrite conflicting servers"),
        ("--skip-conflicts", "Leave conflicting servers alone"),
        ("--no-interactive", "Never prompt"),
        ("--dry-run", "Show what would change"),
        ("--strict", "Exit nonzero on any conflict"),
        ("--prune", "Delete target servers not in the selection"),
    ],
    "mcp doctor": [
        ("--strict", "Exit nonzero on warnings"),
    ],
    "harness archive": [
        ("--usage=none", "Mark the harness as unused"),
        ("--usage=trial", "Mark the harness as tried once"),
        ("--usage=used", "Mark the harness as in use"),
        ("--usage=heavy", "Mark the harness as heavily used"),
    ],
    "discover": [
        ("--apply", "Register high/medium-confidence finds"),
        ("--apply-all", "Register every match"),
        ("--json", "Machine-readable output"),
        ("--all", "Include already-registered tools"),
    ],
    "add": [
        ("-i", "Interactive form"), ("--interactive", "Interactive form"),
        ("--aliases=", "Comma-separated aliases"),
        ("--tags=", "Comma-separated tags"),
        ("--command=", "Prefill the command (-i only)"),
        ("--description=", "Prefill the description (-i only)"),
    ],
    "edit": [
        ("--set=", "Set field=value"),
        ("--command=", "New launch command"),
        ("--description=", "New description"),
        ("--aliases=", "Replace aliases"),
        ("--tags=", "Replace tags"),
        ("--version=", "Recorded version"),
        ("--notes=", "Free-form notes"),
    ],
    "install": [
        ("--package=", "Package spec for the installer"),
        ("--command=", "Binary the package provides"),
        ("--dry-run", "Show what would happen"),
        ("-n", "Short for --dry-run"),
    ],
    "init": [
        ("--full", "List every path, not just the counts"),
        ("--check", "Show what would change, write nothing"),
        ("-n", "Short for --check"),
        ("--force", "Replace real files too (backed up first)"),
        ("--migrate", "Move a pre-0.2.7 ~/.config/swe into ~/.quiver"),
        ("--yes", "Register every new harness without asking"),
    ],
    "setup": [
        ("--quick", "Only missing or actionable stages"),
        ("--apply", "Apply safe discovery changes"),
        ("--json", "Print discovery preview as JSON"),
        ("--non-interactive", "Preview without prompts or writes"),
    ],
}

# Nested flag tables: cmd -> {subcommand: _COMMAND_FLAGS key}. `swe mcp sync
# --<TAB>` consults the "mcp sync" table; `swe hs list` shares `swe list`'s.
_NESTED_FLAGS: dict[str, dict[str, str]] = {
    "list": {"edit": "list edit"},
    "ls": {"edit": "list edit"},
    "harness": {
        "list": "list", "ls": "list",
        "discover": "discover", "archive": "harness archive",
    },
    "hs": {
        "list": "list", "ls": "list",
        "discover": "discover", "archive": "harness archive",
    },
    "mcp": {
        "discover": "mcp discover", "sync": "mcp sync", "doctor": "mcp doctor",
    },
    "skills": {
        "link": "skills link", "unlink": "skills unlink",
        "move": "skills move", "discover": "skills discover",
    },
    "sk": {
        "link": "skills link", "unlink": "skills unlink",
        "move": "skills move", "discover": "skills discover",
    },
    "find": {
        "amd": "find", "agents": "find", "agents.md": "find",
        "instructions": "find", "skills": "find", "skill": "find",
        "plugins": "find", "plugin": "find",
        "mcp": "find mcp", "mcps": "find mcp", "servers": "find mcp",
    },
    "providers": {
        "list": "providers list", "ls": "providers list", "add": "providers add",
    },
    "pv": {
        "list": "providers list", "ls": "providers list", "add": "providers add",
    },
    "report": {
        "daily": "report", "weekly": "report", "followups": "report followups",
        # `followup <action>` takes its own tables below; bare `followup`
        # must not fall through to report's generation flags.
        "followup": "report followup",
        "followup add": "report followup add",
        "followup work": "report followup work",
    },
}

# Subcommands whose first positional argument is a tool name,
# e.g. `swe hs star cl<TAB>` or `swe mcp status oc<TAB>`.
_NESTED_TOOL_TARGETS: dict[str, frozenset[str]] = {
    "harness": frozenset({"star", "archive", "favourite", "favorite", "shelve"}),
    "hs": frozenset({"star", "archive", "favourite", "favorite", "shelve"}),
    "mcp": frozenset({"list", "ls", "status", "edit"}),
}

# Subcommands that take several tool arguments (`swe mcp sync cc oc <TAB>`).
_NESTED_MULTI_TOOL: dict[str, frozenset[str]] = {
    "mcp": frozenset({"sync", "diff", "validate"}),
}

# Subcommands whose first positional is a provider name.
_NESTED_PROVIDER_TARGETS: dict[str, frozenset[str]] = {
    "providers": frozenset({"info", "remove", "rm"}),
    "pv": frozenset({"info", "remove", "rm"}),
}

# Third level of subcommands, e.g. `swe report followup <TAB>`.
_NESTED_SUBCOMMANDS: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("report", "followup"): [
        ("add", "Record a follow-up"),
        ("edit", "Edit a follow-up"),
        ("done", "Mark a follow-up done"),
        ("dismiss", "Dismiss a follow-up"),
        ("reopen", "Reopen a follow-up"),
        ("work", "Hand a follow-up to a harness"),
    ],
    ("skills", "catalog"): [
        ("add", "Register a skills directory"),
        ("list", "Show registered catalogs"),
        ("remove", "Unregister a catalog"),
        ("rm", "Short for remove"),
    ],
    ("sk", "catalog"): [
        ("add", "Register a skills directory"),
        ("list", "Show registered catalogs"),
        ("remove", "Unregister a catalog"),
        ("rm", "Short for remove"),
    ],
    ("config", "setup"): [
        ("report", "Configure report models"),
    ],
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
    "hs": [
        ("list", "List every harness (swe list is the shortcut)"),
        ("ls", "Short for list"),
        ("edit", "Review every harness at once"),
        ("star", "Toggle a favourite"),
        ("archive", "Shelve a harness you have ruled out"),
        ("discover", "Scan PATH for AI coding CLIs"),
    ],
    "session": [
        ("use", "Resume a session by index"),
    ],
    "mcp": [
        ("discover", "Find MCP servers across tool configs"),
        ("list", "Matrix of MCP servers across tools"),
        ("ls", "Short for list"),
        ("status", "List with health checks"),
        ("sync", "Copy servers between tools"),
        ("diff", "Compare two tools' configs"),
        ("edit", "Edit one server in one tool"),
        ("validate", "Check config shapes"),
        ("doctor", "Deep diagnostics"),
        ("help", "Detailed help for one command"),
    ],
    "find": [
        ("amd", "Locate AGENTS.md / CLAUDE.md instruction files"),
        ("skills", "Locate skill folders"),
        ("plugins", "Locate installed plugins"),
        ("mcp", "Locate MCP server configs"),
    ],
    "skills": [
        ("list", "List skills (bare `swe skills` does the same)"),
        ("ls", "Short for list"),
        ("tree", "Show the symlink layout (alias: swe find skills -r)"),
        ("scope", "Show which scope each harness uses"),
        ("link", "Point a harness at a shared skills root"),
        ("unlink", "Remove a skills-root symlink"),
        ("move", "Move a skill between scope roots"),
        ("discover", "Find skills not yet cataloged"),
        ("catalog", "Manage extra skills directories"),
        ("help", "Detailed help for one topic"),
    ],
    "sk": [
        ("list", "List skills (bare `swe skills` does the same)"),
        ("ls", "Short for list"),
        ("tree", "Show the symlink layout (alias: swe find skills -r)"),
        ("scope", "Show which scope each harness uses"),
        ("link", "Point a harness at a shared skills root"),
        ("unlink", "Remove a skills-root symlink"),
        ("move", "Move a skill between scope roots"),
        ("discover", "Find skills not yet cataloged"),
        ("catalog", "Manage extra skills directories"),
        ("help", "Detailed help for one topic"),
    ],
    "providers": [
        ("list", "List providers and key coverage"),
        ("ls", "Short for list"),
        ("info", "Show one provider"),
        ("add", "Register a provider"),
        ("remove", "Unregister a provider"),
        ("help", "Detailed help"),
    ],
    "pv": [
        ("list", "List providers and key coverage"),
        ("ls", "Short for list"),
        ("info", "Show one provider"),
        ("add", "Register a provider"),
        ("remove", "Unregister a provider"),
        ("help", "Detailed help"),
    ],
    "report": [
        ("daily", "Report since the previous daily report"),
        ("weekly", "Report since the previous weekly report"),
        ("warnings", "Show warnings for one report manifest"),
        ("followups", "List follow-ups"),
        ("followup", "Manage or work on a follow-up"),
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
        nested = _NESTED_FLAGS.get(cmd)
        if nested and rest:
            # Two-word subcommands first — but only once their positional
            # is filled (`followup work <id> --<TAB>`), else the flags would
            # be suggested where a required argument belongs.
            if len(rest) > 2:
                key = nested.get(" ".join(rest[:2]))
            else:
                key = None
            key = key or nested.get(rest[0])
            if key is not None:
                return _filter_by_prefix(_COMMAND_FLAGS.get(key, []), partial)
        flags = _COMMAND_FLAGS.get(cmd, [])
        return _filter_by_prefix(flags, partial)

    # Third-level subcommands, e.g. `swe report followup <TAB>`
    if len(rest) == 1 and (cmd, rest[0]) in _NESTED_SUBCOMMANDS:
        return _filter_by_prefix(_NESTED_SUBCOMMANDS[(cmd, rest[0])], partial)

    # Tool-name completion for commands that take a tool argument.
    # `install` also offers catalog names for harnesses not yet registered.
    if cmd in _TOOL_TARGET_COMMANDS and len(rest) == 0:
        if cmd == "install":
            return _install_completions(partial)
        return _tool_completions(partial)

    # `swe hs star <tool>` / `swe mcp status <tool>` — the tool name sits one
    # level deeper than the flat commands handled above.
    nested = _NESTED_TOOL_TARGETS.get(cmd)
    if nested and len(rest) == 1 and rest[0] in nested:
        return _tool_completions(partial)

    # `swe mcp sync cc oc <TAB>` — every positional is a tool; don't re-offer
    # ones already typed (by name or by alias).
    multi = _NESTED_MULTI_TOOL.get(cmd)
    if multi and rest and rest[0] in multi:
        try:
            registry = load_registry()
        except Exception:
            return []
        used = set()
        for arg in rest[1:]:
            used.add(arg if arg in registry else next(
                (n for n, t in registry.items() if arg in (t.get("aliases") or [])),
                arg,
            ))
        return _tool_completions(partial, exclude=used)

    # `swe mcp edit <tool> <server>` — server names from that tool's config.
    if cmd == "mcp" and len(rest) == 2 and rest[0] == "edit":
        return _mcp_server_completions(rest[1], partial)

    # `swe providers info <name>` — provider names from the registry.
    ptargets = _NESTED_PROVIDER_TARGETS.get(cmd)
    if ptargets and len(rest) == 1 and rest[0] in ptargets:
        return _provider_completions(partial)

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


def _tool_completions(
    partial: str = "", exclude: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """Return tool names + aliases from the registry."""
    try:
        registry = load_registry()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for name, tool in sorted(registry.items()):
        if name in exclude:
            continue
        desc = tool.get("description") or ""
        if not partial or name.startswith(partial):
            out.append((name, desc))
        for alias in tool.get("aliases") or []:
            if not partial or alias.startswith(partial):
                out.append((alias, f"alias for {name}"))
    return out


def _provider_completions(partial: str = "") -> list[tuple[str, str]]:
    """Return provider names + aliases from the provider registry."""
    try:
        from quiver.providers.registry import load_registry as load_providers

        providers = load_providers()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for name, info in sorted(providers.items()):
        if not isinstance(info, dict):
            continue
        if not partial or name.startswith(partial):
            out.append((name, str(info.get("name") or name)))
        for alias in info.get("aliases") or []:
            if alias == name:
                continue
            if not partial or alias.startswith(partial):
                out.append((alias, f"alias for {name}"))
    return out


def _install_completions(partial: str = "") -> list[tuple[str, str]]:
    """Registered tools plus catalog names not yet registered."""
    out = _tool_completions(partial)
    have = {name for name, _ in out}
    try:
        from quiver.harness.catalog import HARNESS_CATALOG
    except Exception:
        return out
    for name, spec in sorted(HARNESS_CATALOG.items()):
        if name in have or (partial and not name.startswith(partial)):
            continue
        out.append((name, str(spec.get("description") or "installable")))
    return out


def _mcp_server_completions(tool: str, partial: str = "") -> list[tuple[str, str]]:
    """Return server names configured for ``tool`` (for `mcp edit`)."""
    try:
        from quiver.mcp.cli import get_tool_config, get_tool_loader, load_registry as mcp_tools, resolve_tool_arg

        registry = mcp_tools()
        resolved = resolve_tool_arg(registry, tool)
        if not resolved:
            return []
        cfg = get_tool_config(resolved)
        loader = get_tool_loader(resolved)
        if not cfg or loader is None:
            return []
        names = sorted(loader(cfg["path"]))
    except Exception:
        return []
    return _filter_by_prefix([(n, f"server in {resolved}") for n in names], partial)


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
