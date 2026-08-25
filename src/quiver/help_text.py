"""CLI help text and help command."""

from quiver.console import c
from quiver.harness.registry import load_registry
from quiver.harness.tools import is_installed
from quiver.paths import HARNESS_FILE

HELP = {
    "list": (
        "List all registered AI coding tools (short for swe harness list)",
        f"""\
  {c('cyan', 'swe list')}                     List all tools (starred first, then 100d usage)
  {c('dim', 'Short for')} {c('cyan', 'swe harness list')}{c('dim', '; every harness verb lives under swe harness.')}
  {c('cyan', 'swe list <tag>')}               Filter by tag (e.g. swe list agentic)
  {c('cyan', 'swe list --usage')}             Add 100d sessions and remaining quota
  {c('cyan', 'swe list --links')}             Add AGENTS.MD and SKILLS link status
  {c('cyan', 'swe list -n')}                  Fetch new session and rate-limit data
  {c('cyan', 'swe list edit')}                Choose which columns to show
  {c('cyan', 'swe list edit --reset')}        Restore the default columns
  {c('cyan', 'swe list legend')}             Explain the AGENTS.MD / SKILLS glyphs

  Starring pins a tool to the top of {c('cyan', 'swe list')} ({c('neon_pink', '★')}) and opts it into
  rate-limit / quota fetching — the {c('cyan', '--usage')} column only polls starred
  tools. It does not change what {c('cyan', 'swe use')}, {c('cyan', 'swe report')}, or {c('cyan', 'swe session')} do.
  Use {c('cyan', 'swe hs star <name>')} to favourite, {c('cyan', 'swe hs archive <name>')} to shelve.

  Archiving hides a tool from {c('cyan', 'swe list')} and records why; nothing gets
  uninstalled. {c('cyan', 'swe list --scope=all')} brings it back.

{c('bold', 'Flags')}
  {c('cyan', '--usage')} / {c('cyan', '-u')}            Show usage. This is the only part of swe list
                          that touches the network, so it is opt-in: the plain
                          listing runs in ~70ms, --usage costs ~700ms on a cold
                          cache. Implied by --refresh.
  {c('cyan', '--links')} / {c('cyan', '-L')}            Show quiver link status instead of usage.
  {c('cyan', '--refresh')} / {c('cyan', '-r')} / {c('cyan', '-n')}   Bypass caches and fetch new data.
  {c('cyan', '--scope')}=active|archived|all  Which harnesses show. Default active (archived
                          hidden); archived shows only shelved tools; all shows both.
  {c('dim', 'Collision: swe find also has a --scope, but it means something else there')}
  {c('dim', '(where a file lives, not archived-visibility). In swe find, archived-')}
  {c('dim', 'visibility is --harness=all instead.')}

{c('bold', 'Link states')}
  {c('green', '✓')}  linked to ~/.quiver      {c('yellow', '○')}  nothing there yet, or safe to absorb
  {c('yellow', '↻')}  symlink points elsewhere  {c('red', '✗')}  real file in the way, needs --force
  {c('yellow', '○')}  holds files that exist nowhere else, left alone on purpose
  {c('dim', '·')}  no known convention, or harness not installed

  {c('cyan', 'swe list legend')} prints this with each state named.
  Run {c('cyan', 'swe init')} to link everything."""
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
  {c('cyan', 'swe add -i')}                           Interactive form (walk each field)
  {c('cyan', 'swe add <name> -i')}                    Interactive, pre-filled name

  The {c('dim', 'registry')} is one file, {c('bold', '~/.quiver/config/harness.json')}, listing every
  tool quiver knows. Adding a tool here does not install it — it tells
  quiver the tool exists, so {c('cyan', 'swe list')}/{c('cyan', 'swe use')}/{c('cyan', 'swe edit')} can find it.
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

  Does not uninstall the tool, only removes it from the registry.
  {c('dim', 'See')} {c('cyan', 'swe help add')} {c('dim', 'for what the registry is.')}"""
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

  Also runs read-only drift checks: places two parts of quiver were supposed
  to agree and quietly did not — help text vs dispatch, registry shape,
  hardcoded fallback tables vs harness.json, dangling symlinks. A finding
  looks like:
    {c('dim', "! [registry] cursor: state 'disabled' is not one of ['active', 'archived', 'starred']")}

{c('bold', 'Exit codes')}
  0  healthy
  1  off-PATH tools, global bin not on PATH, or an error-severity drift
     finding. Warn-level drift (stale help, code-table mismatches, symlink
     notices) is printed but does not fail the exit code."""
    ),
    "install": (
        "Install a harness via PATH-visible npm and register it",
        f"""\
  {c('cyan', 'swe install <name>')}                    npm install -g + register in harness.json
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
  {c('cyan', '--search <text>')}              Filter title/path/agent/session id (alias: -q, --grep)
  {c('cyan', '-d, --days <N>')}               Include today and the preceding N-1 calendar dates
  {c('cyan', '-w, --weeks <N>')}              Include the latest N times 7 calendar dates
  {c('cyan', '-s, --start <YYYY-MM-DD>')}      Inclusive range start; use together with --end
  {c('cyan', '-e, --end <YYYY-MM-DD>')}        Inclusive range end; use together with --start

{c('bold', 'Examples')}
  swe session
  swe session 20
  swe session use 3
  swe session --agent claude
  swe session --here
  swe session -d 5
  swe session -w 3
  swe session -s 2026-07-01 -e 2026-07-30
  swe session --search login
  swe session 30 -q quiver"""
    ),
    "report": (
        "Summarize coding sessions and manage follow-ups",
        f"""\
  {c('cyan', 'swe report daily')}              Preview and generate a daily coding-session report
  {c('cyan', 'swe report weekly')}             Preview and generate a weekly coding-session report
  {c('cyan', 'swe report warnings <manifest>')} Print warnings recorded for one specific report
  {c('cyan', 'swe report followups')}                     List open follow-ups
  {c('cyan', 'swe report followups --status=open|done|dismissed')}  Filter by status (default open)
  {c('cyan', 'swe report followup done <id>')}  Mark a follow-up done manually
  {c('cyan', 'swe report followup work <id>')}  Resume its source or start a contextual session

Before any model runs, Quiver displays session counts, exclusions, cache hits,
planned calls, and estimated input tokens. Normal plans ask for y/N. Plans over
configured limits require the exact phrase {c('bold', 'process all')}.
When a report completes with warnings, Quiver prints the exact
{c('cyan', 'swe report warnings <manifest.json>')} command for that report.

{c('bold', 'Source flags')}
  {c('cyan', '-d, --days <N>')}                Override the report with N calendar dates
  {c('cyan', '-w, --weeks <N>')}               Override the report with N times 7 calendar dates
  {c('cyan', '-s, --start <YYYY-MM-DD>')}       Inclusive custom start; requires --end
  {c('cyan', '-e, --end <YYYY-MM-DD>')}         Inclusive custom end; requires --start
  {c('cyan', '--here')}                        Include only the current project
  {c('cyan', '--agent <name>')}                Include only one coding harness
  {c('cyan', '--search <text>')}               Match session title/path/agent/id (alias: -q)

Reports run in two passes: a cheap model summarizes each session, then a
stronger model writes the final report from those summaries. The flags below
pick the harness/model for each pass; leave any of them out and Quiver falls
back to {c('cyan', 'report.session.*')} / {c('cyan', 'report.writer.*')} in {c('cyan', 'swe config')}.

{c('bold', 'Runner override flags')}
  {c('cyan', '--session-harness <name>')}       Cheap summarizer harness: claude or codex
  {c('cyan', '--session-model <model>')}        Model used for project/session summaries
  {c('cyan', '--session-arg <arg>')}            Pass one extra summarizer argument; repeat as needed
  {c('cyan', '--writer-harness <name>')}        Harness used for the final report
  {c('cyan', '--writer-model <model>')}         Strong model used for the final report
  {c('cyan', '--writer-arg <arg>')}             Pass one extra writer argument; repeat as needed

{c('bold', 'Follow-up actions')}
  add <text> [--project PATH]   Add an item; project defaults to the current directory
  edit <id> <text>             Correct an item's text
  done|dismiss|reopen <id>     Change status explicitly
  work <id> --resume           Resume the newest referenced supported session
  work <id> --new --harness X  Start a new contextual session with harness X"""
    ),
    "config": (
        "View or update Quiver configuration",
        f"""\
  {c('cyan', 'swe config')}                     Print resolved configuration
  {c('cyan', 'swe config get <key>')}           Print one resolved dotted key
  {c('cyan', 'swe config set <key> <value>')}   Save a string, number, boolean, or JSON array
  {c('cyan', 'swe config unset <key>')}         Remove a user-set value
  {c('cyan', 'swe config edit')}                Open ~/.quiver/config/config.json in VISUAL or EDITOR
  {c('cyan', 'swe config check')}               Validate types, report setup, and secret safety
  {c('cyan', 'swe config setup report')}        Configure cheap summary and strong writer models

{c('bold', 'Examples')}
  swe config set report.session.harness claude
  swe config get report.writer.model
  swe config unset report.session.model

Quiver never stores model credentials in this file. Claude and Codex continue
to use their own login state and environment."""
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
  {c('cyan', 'swe skills tree')}              Symlink layout — now the same view as swe find skills
  {c('dim', 'swe skills scope list forwards to the same place; --sync is accepted but ignored.')}
  {c('cyan', 'swe skills link <harness> [target]')}   Symlink a harness root to shared/other
  {c('cyan', 'swe skills unlink <harness> [--mkdir]')} Break a harness symlink
  {c('cyan', 'swe skills move <name> --from A --to B')} Move a skill folder between roots
  {c('cyan', 'swe skills discover [--apply]')} Scan Desktop/Documents for skill catalogs
  {c('cyan', 'swe skills catalog add [path] [label]')} Register a skills directory (default: .)
  {c('cyan', 'swe skills catalog .')}                Add the current directory as a catalog

{c('bold', 'Flags')}
  {c('cyan', '-d, --desc')}                   Show skill descriptions

{c('bold', 'Scopes scanned')}
  shared          ~/.quiver/skills (the tree every harness skills/ symlinks to)
  cursor-builtin  ~/.cursor/skills-cursor
  cursor-plugin   ~/.cursor/plugins/cache
  claude-plugin   ~/.claude/plugins/cache
  project         ./.cursor/skills (current directory)
  <catalog>       Paths from ~/.quiver/config/skill_catalogs.json

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
  {c('cyan', 'swe mcp discover [--apply]')}          Find MCP servers across tool configs
  {c('cyan', 'swe mcp list [tool]')}                 Matrix view of MCP servers across tools
  {c('cyan', 'swe mcp status [tool]')}               List with health checks
  {c('cyan', 'swe mcp sync <source> <target...>')}   Copy servers source → target(s) (--force, --skip-conflicts)
  {c('cyan', 'swe mcp diff <t1> <t2>')}              Compare two tools' configs
  {c('cyan', 'swe mcp edit <tool> <name>')}          Edit one server's config in one tool
  {c('cyan', 'swe mcp validate [tool...]')}          Validate MCP config shape for one/all tools
  {c('cyan', 'swe mcp doctor')}                      Deep diagnostics

{c('bold', 'Help')}  {c('cyan', 'swe mcp <command> help')} for details on each — flags, examples, the exact
        model each command uses.
{c('bold', 'Source of truth')}  ~/.quiver/mcp.json"""
    ),
    "providers": (
        "Manage AI provider API keys and metadata",
        f"""\
  {c('cyan', 'swe providers list [-d] [--api-keys-dir=DIR] [<filter>]')}
      List registered providers + masked key status (`-` = no key)
  {c('cyan', 'swe providers info <name|alias>')}
      Show details for one provider, including key status + path
  {c('cyan', 'swe providers add <name> [--url URL] [--env ENV] [--file NAME]')}
      Register a provider in ~/.quiver/config/providers.json
  {c('cyan', 'swe providers remove <name>')}
      Unregister a provider (does not delete your key file)

  Keys live as plain-text files in {c('bold', '~/.api_keys/')} (override
  with --api-keys-dir=DIR). quiver stores metadata only — never the
  raw key. Run {c('cyan', 'swe providers --help')} for full key-storage and masking details."""
    ),
    "harness": (
        "Everything about the harnesses you have",
        f"""\
  {c('cyan', 'swe harness list')}                  List them  {c('dim', '(swe list is the shortcut)')}
  {c('cyan', 'swe harness edit')}                  Review every harness at once
  {c('cyan', 'swe harness star <name>')}           Toggle a favourite
  {c('cyan', 'swe harness archive <name> [why]')}  Shelve one you have ruled out
  {c('cyan', 'swe harness discover')}              Scan PATH and home dirs for AI tools (dry-run)
  {c('cyan', 'swe harness discover --apply')}      Add high-confidence matches to harness.json
  {c('cyan', 'swe harness discover --apply-all')}  Add high + medium confidence matches
  {c('cyan', 'swe harness discover --json')}       Machine-readable output
  {c('cyan', 'swe harness discover --all')}        Include already-registered and missing tools too

{c('bold', 'Alias')}  {c('cyan', 'swe discover')} is the same as {c('cyan', 'swe harness discover')}"""
    ),
    "find": (
        "Show where shared assets live and what links to them",
        f"""\
  Run this when a skill, plugin, or MCP server is not showing up somewhere
  and you want to know why.

  {c('cyan', 'swe find')}                   Every tree
  {c('cyan', 'swe find amd')}               AGENTS.md and every harness pointing at it
  {c('cyan', 'swe find skills')}            Skills, plugins, and every harness skill root
  {c('cyan', 'swe find plugins')}           Plugins across every plugin-capable harness
  {c('cyan', 'swe find mcps')}              MCP servers in the hub, and which harnesses have them
  {c('cyan', 'swe find plugins -i')}        Browse them: arrows to move and descend, q to quit
  {c('cyan', 'swe find amd --scope=all')}   Include project and vendored files
  {c('cyan', 'swe find --harness=all')}     Include archived harnesses too

{c('bold', 'Scope')}  {c('cyan', '--scope=global|local|all')}, default global
  {c('cyan', 'global')}   loaded into every harness session (for plugins: installed
           and enabled; a cached copy with no install record is not)
  {c('cyan', 'local')}    project files only
  {c('cyan', 'all')}      both, plus vendored plugin and extension copies

  {c('dim', 'Collision: swe list also has a --scope, but it means something else there')}
  {c('dim', '(active/archived/all harness visibility, not where a file lives).')}

{c('bold', 'Harness activity')}  {c('cyan', '--harness=active|all')}, default active, every view
  {c('cyan', 'active')}   hides rows whose harness is archived in harness.json
           (starred still counts as active — it is only pinned, not shelved)
  {c('cyan', 'all')}      shows archived harnesses' rows too

  A row hidden this way is never silent about it: the listing ends with
  {c('dim', 'N archived harnesses hidden; --harness=all to show')}. A row that cannot
  be mapped to a harness in harness.json — the shared quiver copy, an
  unregistered config — is never filtered, active or not: unknown is the
  one thing {c('cyan', 'swe find')} exists to keep visible.

  Which harnesses get walked, and where each one's files live, comes from
  harness.json's own {c('cyan', 'capabilities')} first — the per-harness field in
  harness.json recording what a tool supports (skills? plugins?) and where —
  and only falls back to a hardcoded guess for a harness the registry has
  never heard of.

{c('bold', 'States')}
  {c('green', 'synced')}         symlinked to the shared copy
  {c('cyan', 'unsynced')}       a real directory that could be absorbed
  {c('yellow', 'own copy')}       a real (non-symlink) file, not pointing at the shared one
  {c('yellow', 'separate')}       holds content that exists nowhere else, left alone
  {c('yellow', 'wrong target')}   symlink pointing somewhere unexpected
  {c('red', 'in the way')}     a real file where a link should be
  {c('dim', 'not installed')}  harness absent from this machine

  {c('dim', 'Same ideas swe list and swe init print under different names:')}
  {c('dim', 'linked=synced, create=missing, relink=wrong target, conflict=in the way.')}

  Read-only. {c('cyan', 'swe init')} is what changes anything."""
    ),
    "init": (
        "Create ~/.quiver and symlink every harness to it",
        f"""\
  {c('cyan', 'swe init')}                    Create the layout and link all harnesses
  {c('cyan', 'swe init --check')}            Show what would change, write nothing
  {c('cyan', 'swe init --force')}            Replace real files too (backed up first)
  {c('cyan', 'swe init --migrate')}          Move a pre-0.2.7 ~/.config/swe into ~/.quiver

  A {c('dim', 'symlink')} is a shortcut file: each harness's folder points at the one
  real copy in ~/.quiver, so editing once updates it everywhere.

{c('bold', 'What it owns')}
  ~/.quiver/AGENTS.md   one instruction file, linked in under each harness's
                        own name (CLAUDE.md, QWEN.md, CRUSH.md, GEMINI.md)
  ~/.quiver/skills/     one skill tree, linked in as every harness's skills/
  ~/.quiver/backups/    anything replaced, timestamped

{c('bold', 'States')}
  {c('green', 'linked')}    already points at the canonical file
  {c('cyan', 'create')}    nothing there yet, will symlink
  {c('yellow', 'relink')}    symlink pointing elsewhere, will repoint
  {c('red', 'conflict')}  a real file or directory, needs --force
  {c('dim', 'skipped')}   harness not installed on this machine"""
    ),
    "setup": (
        "Sectioned setup wizard for Quiver",
        f"""\
  {c('cyan', 'swe setup')}                    Run all six interactive setup stages
  {c('cyan', 'swe setup --quick')}            Only visit missing or actionable stages
  {c('cyan', 'swe setup harnesses')}          Discover and register coding CLIs
  {c('cyan', 'swe setup providers')}          Review provider credential coverage
  {c('cyan', 'swe setup mcp')}                Import MCP servers into mcp.json
  {c('cyan', 'swe setup skills')}             Unify safe roots under ~/.quiver/skills
  {c('cyan', 'swe setup report')}             Configure summarizer and writer models
  {c('cyan', 'swe setup check')}              Verify the resulting setup

  Returning-user prompts show current values; Enter keeps them. Quiver creates a
  timestamped backup before changing harness.json, mcp.json, or config.json.

  {c('cyan', 'swe setup --apply')}            Apply safe discovery changes without prompts
  {c('cyan', 'swe setup --json')}             Print the discovery preview as JSON
  {c('cyan', 'swe setup --non-interactive')}  Preview without prompts or writes

  Provider credentials remain external. Use {c('cyan', 'swe providers info <name>')} for the
  expected key file and environment variable; harness OAuth remains harness-owned."""
    ),
    "discover": (
        "Scan PATH and home dirs for unregistered AI tools",
        f"""\
  {c('cyan', 'swe discover [--apply]')}   Alias for {c('cyan', 'swe harness discover')}

  See {c('cyan', 'swe help harness')} for flags (--apply, --apply-all, --json, --all)."""
    ),
    "autocomplete": (
        "Generate and inject shell completion script",
        f"""\
  {c('cyan', 'swe autocomplete zsh')}    Generate + inject zsh completion
  {c('cyan', 'swe autocomplete bash')}   Generate + inject bash completion
  {c('cyan', 'swe autocomplete fish')}   Generate + inject fish completion

  Writes a completion script to {c('dim', '~/.quiver/completions/')} and adds a
  source line to your shell profile (~/.zshrc, ~/.bashrc, or fish config).

  After running, restart your terminal or run:
    {c('cyan', 'source ~/.zshrc')}  (or the equivalent for your shell)

  The completion script calls {c('cyan', 'swe __complete')} under the hood to
  provide dynamic completions for tool names, aliases, tags, and flags."""
    ),
}

COMMAND_CATEGORIES = [
    ("Setup", [
        ("init",    None),
        ("setup",   None),
        ("config",  None),
        ("doctor",  None),
        ("install", None),
    ]),
    ("Registry", [
        ("list",     "ls"),
        ("info",     None),
        ("add",      None),
        ("edit",     None),
        ("remove",   "rm"),
        ("check",    None),
        ("harness",  "hs"),
        ("discover", None),
    ]),
    ("Launch", [
        ("use",     "run"),
    ]),
    ("Analytics", [
        ("session", None),
        ("report",  None),
        ("models",  None),
    ]),
    ("Reference", [
        ("find",    None),
        ("skills",  "sk"),
        ("tags",    None),
        ("aliases", None),
        ("providers", "pv"),
    ]),
    ("MCP", [
        ("mcp",     None),
    ]),
    ("Shell", [
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
    print("  One home for every AI coding CLI you use. Each one invents its own config")
    print("  files; quiver gives them one shared instruction file, one skills folder,")
    print(f"  one MCP list, one registry. quiver calls each AI coding tool (Claude Code,")
    print(f"  Codex, Cursor…) a {c('bold', 'harness')}.\n")
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
    print(f"  {c('dim', 'REGISTRY')}  {HARNESS_FILE}")
    print(f"  {c('dim', 'TOOLS')}     {n_inst}/{n_total} installed\n")
