"""CLI help text and help command."""

from quiver.console import c
from quiver.harness.registry import load_registry
from quiver.harness.tools import is_installed
from quiver.paths import REGISTRY_FILE

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
    "harness": (
        "Harness registry utilities",
        f"""\
  {c('cyan', 'swe harness discover')}              Scan PATH for AI coding CLIs (dry-run)
  {c('cyan', 'swe harness discover --apply')}      Add high-confidence matches to tools.json
  {c('cyan', 'swe harness discover --apply-all')}  Add high + medium confidence matches
  {c('cyan', 'swe harness discover --json')}       Machine-readable output

{c('bold', 'Alias')}  {c('cyan', 'swe discover')} is the same as {c('cyan', 'swe harness discover')}"""
    ),
    "setup": (
        "Onboarding wizard for new installs",
        f"""\
  {c('cyan', 'swe setup')}              Scan for harnesses and show recommendations
  {c('cyan', 'swe setup --apply')}      Register high-confidence harnesses without prompting

  On a TTY, {c('cyan', 'swe setup')} prompts before writing to tools.json."""
    ),
}

COMMAND_CATEGORIES = [
    ("Setup", [
        ("setup",   None),
    ]),
    ("Registry", [
        ("list",    "ls"),
        ("info",    None),
        ("add",     None),
        ("remove",  "rm"),
        ("check",   None),
        ("harness", None),
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
