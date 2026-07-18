"""CLI help text and help command."""

from quiver.console import c
from quiver.harness.registry import load_registry
from quiver.harness.tools import is_installed
from quiver.paths import REGISTRY_FILE

HELP = {
    "list": (
        "List all registered AI coding tools",
        f"""\
  {c('cyan', 'swe list')}                     List all tools (starred first, then 100d usage)
  {c('cyan', 'swe list <tag>')}               Filter by tag (e.g. swe list agentic)
  {c('cyan', 'swe list --refresh')}           Bypass session cache, re-parse all sessions

  Favourited harnesses are pinned to the top with a neon border ({c('neon_pink', '★')}).
  Use {c('cyan', 'swe star <name>')} to favourite / unfavourite.

{c('bold', 'Flags')}
  {c('cyan', '--refresh')} / {c('cyan', '-r')}   Force re-parse of session data (bypass cache)."""
    ),
    "star": (
        "Favourite / pin a harness to the top of swe list",
        f"""\
  {c('cyan', 'swe star')}                     List starred harnesses
  {c('cyan', 'swe star <name|alias>')}        Toggle star (pin + neon highlight)
  {c('cyan', 'swe unstar <name|alias>')}      Remove star
  {c('cyan', 'swe star clear')}               Clear all stars

  Stars are stored in {c('dim', '~/.config/swe/stars.json')} (separate from tools.json).
  Starred harnesses sort above unstarred ones in {c('cyan', 'swe list')}.

{c('bold', 'Examples')}
  swe star droid
  swe star df
  swe unstar claude
  swe favourite opencode"""
    ),
    "unstar": (
        "Remove a harness from favourites",
        f"""\
  {c('cyan', 'swe unstar <name|alias>')}      Remove star

  See also: {c('cyan', 'swe star')}"""
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
    "edit": (
        "Edit fields of a registered harness",
        f"""\
  {c('cyan', 'swe edit <name|alias>')}                Interactive field editor
  {c('cyan', 'swe edit <name> --description "..."')}  Set description
  {c('cyan', 'swe edit <name> --aliases a,b')}        Replace aliases (comma-separated)
  {c('cyan', 'swe edit <name> --tags t1,t2')}         Replace tags
  {c('cyan', 'swe edit <name> --command <cmd>')}      Change launch command
  {c('cyan', 'swe edit <name> --version <ver>')}      Set version string
  {c('cyan', 'swe edit <name> --notes "..."')}        Set notes
  {c('cyan', 'swe edit <name> --set field=value')}    Compact multi-set form

  Editable fields: command, description, aliases, tags, version, notes.
  With no field flags, opens an interactive prompt loop (save / quit).
  Alias collisions with other tools are rejected.

{c('bold', 'Examples')}
  swe edit mastracode
  swe edit mastracode --description "Mastra Code — AI coding agent" --aliases mc
  swe edit droid --set tags=agentic,coding,autonomous"""
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

  Tries version / --version / -v / -V flags.
  Stores bare version numbers only (no tool-name prefix).
  Also warns about off-PATH installs (e.g. nvm globals invisible to swe).
  Updates registry if version changed."""
    ),
    "doctor": (
        "Diagnose Node/PATH issues that hide global installs",
        f"""\
  {c('cyan', 'swe doctor')}                   Report node/npm, global bin PATH, nvm, off-PATH tools

  Catches the common failure mode: {c('dim', 'npm install -g')} under nvm while
  interactive/non-interactive shells use Homebrew Node (tool missing from PATH).

{c('bold', 'Exit codes')}
  0  healthy
  1  off-PATH tools or global bin not on PATH"""
    ),
    "install": (
        "Install a harness via PATH-visible npm and register it",
        f"""\
  {c('cyan', 'swe install <name>')}                    npm install -g + register in tools.json
  {c('cyan', 'swe install <name> --package <pkg>')}    Override npm package name
  {c('cyan', 'swe install <name> --command <cmd>')}    Override CLI binary name
  {c('cyan', 'swe install <name> --dry-run')}          Show what would run

  Uses a PATH-visible npm (prefers Homebrew over nvm) so the binary lands where
  {c('cyan', 'swe list')} / {c('cyan', 'swe check')} can see it.

{c('bold', 'Examples')}
  swe install mastracode
  swe install jules --package @google/jules
  swe install claude --package @anthropic-ai/claude-code"""
    ),
    "session": (
        "Show recent AI sessions across all agents",
        f"""\
  {c('cyan', 'swe session')}                  Show last 10 sessions
  {c('cyan', 'swe session <N>')}              Show last N sessions
  {c('cyan', 'swe session use <N>')}          Resume session #N

{c('bold', 'Flags')}
  {c('cyan', '--agent <name>')}               Filter by agent (claude, codex, opencode, droid, ...)
  {c('cyan', '--here')}                       Filter to current directory only
  {c('cyan', '--search <text>')}              Filter title/path/agent/session id (alias: -q)

{c('bold', 'Examples')}
  swe session
  swe session 20
  swe session use 3
  swe session --agent claude
  swe session --here
  swe session --search login
  swe session 30 -q quiver"""
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
  {c('cyan', 'swe skills tree [--sync]')}     Show symlink layout between harness roots
  {c('cyan', 'swe skills link <harness> [target]')}   Symlink a harness root to shared/other
  {c('cyan', 'swe skills unlink <harness> [--mkdir]')} Break a harness symlink
  {c('cyan', 'swe skills move <name> --from A --to B')} Move a skill folder between roots
  {c('cyan', 'swe skills discover [--apply]')} Scan Desktop/Documents for skill catalogs
  {c('cyan', 'swe skills catalog add [path] [label]')} Register a skills directory (default: .)
  {c('cyan', 'swe skills catalog .')}                Add the current directory as a catalog

{c('bold', 'Flags')}
  {c('cyan', '-d, --desc')}                   Show skill descriptions

{c('bold', 'Scopes scanned')}
  shared          ~/.agents/skills (the tree ~/.claude, ~/.codex, ~/.cursor symlink to)
  cursor-builtin  ~/.cursor/skills-cursor
  cursor-plugin   ~/.cursor/plugins/cache
  claude-plugin   ~/.claude/plugins/cache
  project         ./.cursor/skills (current directory)
  <catalog>       Paths from ~/.config/swe/skill_catalogs.json

  {c('dim', 'Discover')} finds folders named skills under ~/Desktop and ~/Documents,
  then {c('cyan', 'swe skills discover --apply')} or {c('cyan', 'swe skills catalog add')} registers them.

{c('bold', 'Help')}  {c('cyan', 'swe skills help <topic>')} — catalog, discover, tree, link, unlink, move, scope"""
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
  {c('cyan', 'swe mcp discover [--apply]')}   Find MCP servers across tool configs
  {c('cyan', 'swe mcp list [tool]')}          Matrix view of MCP servers across tools
  {c('cyan', 'swe mcp status [tool]')}        List with health checks
  {c('cyan', 'swe mcp add <name> | -A')}      Stage server(s) for sync
  {c('cyan', 'swe mcp remove <name>')}        Remove from source of truth
  {c('cyan', 'swe mcp sync [tool...]')}       Push staged → tools (--force, --prune, --skip-conflicts)
  {c('cyan', 'swe mcp diff <t1> <t2>')}       Compare two tools' configs
  {c('cyan', 'swe mcp edit <name>')}          Edit a server's config
  {c('cyan', 'swe mcp export [--full]')}      Dump config (redacted by default)
  {c('cyan', 'swe mcp import <file>')}        Load config into source of truth
  {c('cyan', 'swe mcp doctor')}               Deep diagnostics

{c('bold', 'Help')}  {c('cyan', 'swe mcp <command> help')} for detailed help on each command
{c('bold', 'Source of truth')}  ~/.config/swe/mcp.json"""
    ),
    "providers": (
        "Manage AI provider API keys and metadata",
        f"""\
  {c('cyan', 'swe providers list [-d] [--api-keys-dir=DIR] [<filter>]')}
      List registered providers + masked key status (`-` = no key)
  {c('cyan', 'swe providers info <name|alias>')}
      Show details for one provider, including key status + path
  {c('cyan', 'swe providers add <name> [--url URL] [--env ENV] [--file NAME] [--aliases a,b]')}
      Register a provider in ~/.config/swe/providers.json
  {c('cyan', 'swe providers remove <name>')}
      Unregister a provider (does not delete your key file)

  Keys live as plain-text files in {c('bold', '~/.api_keys/')} (override
  with --api-keys-dir=DIR). quiver stores metadata only — never the
  raw key. See `swe providers help` for masking format."""
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
  {c('cyan', 'swe setup')}              Scan harnesses, MCP, and skills roots (dry-run)
  {c('cyan', 'swe setup --apply')}      Apply safe setup changes without prompting

  Three steps: (1) register AI CLIs via harness discover, (2) import MCP servers,
  (3) symlink ~/.codex, ~/.claude, ~/.cursor/skills → ~/.agents/skills when safe.

  On a TTY, {c('cyan', 'swe setup')} prompts before writing registry, mcp.json, or symlinks."""
    ),
    "discover": (
        "Scan PATH for unregistered AI coding CLIs",
        f"""\
  {c('cyan', 'swe discover [--apply]')}   Alias for {c('cyan', 'swe harness discover')}

  See {c('cyan', 'swe help harness')} for flags (--apply, --apply-all, --json)."""
    ),
    "autocomplete": (
        "Generate and inject shell completion script",
        f"""\
  {c('cyan', 'swe autocomplete zsh')}    Generate + inject zsh completion
  {c('cyan', 'swe autocomplete bash')}   Generate + inject bash completion
  {c('cyan', 'swe autocomplete fish')}   Generate + inject fish completion

  Writes a completion script to {c('dim', '~/.config/swe/completions/')} and adds a
  source line to your shell profile (~/.zshrc, ~/.bashrc, or fish config).

  After running, restart your terminal or run:
    {c('cyan', 'source ~/.zshrc')}  (or the equivalent for your shell)

  The completion script calls {c('cyan', 'swe __complete')} under the hood to
  provide dynamic completions for tool names, aliases, tags, and flags."""
    ),
}

COMMAND_CATEGORIES = [
    ("Setup", [
        ("setup",   None),
        ("doctor",  None),
        ("install", None),
    ]),
    ("Registry", [
        ("list",    "ls"),
        ("info",    None),
        ("add",     None),
        ("edit",    None),
        ("remove",  "rm"),
        ("star",    "favourite"),
        ("unstar",  None),
        ("check",   None),
        ("harness", "discover"),
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
        ("providers", "pv"),
    ]),
    ("MCP", [
        ("mcp",     None),
    ]),
    ("Setup", [
        ("autocomplete", None),
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
    print(f"    {c('cyan', 'swe <cmd> --help')}      Detailed help for a command")
    print(f"    {c('cyan', 'swe skills help <topic>')} Per-topic skills help\n")

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
