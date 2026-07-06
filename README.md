# quiver

**One command to launch, resume, and analyze every AI coding CLI on your machine.**

`quiver` is a central manager for the growing zoo of AI coding command-line
tools — Claude Code, Codex, Gemini CLI, Cursor CLI, opencode, Copilot, and
many more. It keeps a small registry of the harnesses you use, launches any of
them (by name or short alias), lets you resume recent sessions across *any*
agent, mines read-only usage analytics from each tool's own logs, discovers the
agent "skills" installed across your machine, and keeps MCP server configs in
sync between tools.

The command you type is **`swe`** (short, fits in muscle memory). The project
and Python package are named **quiver** — think of it as the quiver that holds
all your arrows.

```
$ swe list

AI Coding Tools

  NAME             COMMAND            VERSION      ALIASES       100d  INSTALLED  DESCRIPTION
  ──────────────────────────────────────────────────────────────────────────────────────────
  claude           claude             2.1.126      cc              412  ✓   Claude Code by Anthropic …
  codex            codex              0.133.0      cx              288  ✓   OpenAI Codex CLI
  opencode         opencode           1.17.11      oc               96  ✓   opencode — open source …
  gemini           gemini             0.35.1       gg               12  ✓   Gemini CLI by Google …
  cursor           agent              2026.06.24   cs                4  ✓   Cursor CLI — AI-powered …
  ollama           ollama             0.20.4       olla              —  ✓   Ollama — run local LLMs

  6/6 installed  ·  swe use <name|alias>  │  swe info <name>  │  swe list <tag>  │  swe check
  tags:  agentic  byok  coding  local  …
```

## Why

If you juggle more than one AI coding agent you end up with a mess: different
launch commands, different flags to resume a session, usage scattered across a
dozen log formats, and MCP server definitions copy-pasted between tools. `quiver`
puts a single, consistent front door on all of it — without wrapping or
replacing the tools themselves. It reads their logs read-only and shells out to
the real binaries.

## Features

- **Registry** — one place to list every AI coding CLI you have, with tags,
  short aliases, versions, and install status (`swe list`, `swe info`, `swe add`,
  `swe remove`, `swe check`).
- **Launch** — start any tool by name or alias, passing extra args straight
  through; the process is replaced cleanly via `execvp` (`swe use <name>`).
- **Session resume across agents** — a unified, time-sorted view of recent
  sessions from Claude Code, Codex, opencode, Gemini/Antigravity, Cursor, pi,
  and more — and resume any of them with the right per-tool flag (`swe session`).
- **Model usage analytics** — aggregate which models you actually use, parsed
  read-only from each tool's session logs, optionally grouped by tool or by
  provider (`swe models`).
- **Skills discovery** — find every `SKILL.md` across all your agent skill roots
  (shared, Cursor, Claude, Codex, plugin caches, project-local) and see where
  each one lives (`swe skills`, `swe skills scope list`).
- **MCP sync** — inspect, compare, validate, and copy MCP server definitions
  between tools that store them in different formats (`swe mcp …`).

## Install

Once published:

```bash
pipx install quiver      # recommended (isolated), exposes the `swe` command
# or
pip install quiver
```

From source (this repo):

```bash
git clone <your-fork-url> quiver
cd quiver
python -m venv .venv && source .venv/bin/activate
pip install -e .
swe --help
```

The optional MCP-history server (exposes recent sessions as an MCP tool) needs
one extra dependency:

```bash
pip install -e ".[server]"
python -m quiver.mcp_server
```

Requires Python 3.10+. The core CLI has **no third-party runtime dependencies**
(standard library only).

## Quick start

```bash
swe list                     # all registered tools, sorted by recent usage
swe list agentic             # filter by tag
swe info claude              # command, version, path, tags, aliases
swe check                    # probe installed tools and refresh versions

swe use cc                   # launch Claude Code (alias for `claude`)
swe use codex --help         # extra args are passed straight through
swe use gemini -p 'explain this codebase'

swe session                  # last 10 sessions across ALL agents
swe session 20               # last 20
swe session --agent claude   # only Claude Code sessions
swe session --here           # only sessions in the current directory
swe session use 3            # cd into session #3 and resume it

swe models                   # model usage across all tools, most-used first
swe models -t                # grouped by tool
swe models -p                # show provider/model (e.g. openai/gpt-5.4)

swe skills                   # every SKILL.md across all skill roots
swe skills -d                # include descriptions
swe skills scope list        # list skill roots (scopes) with counts

swe mcp list                 # matrix of MCP servers across tools
swe mcp status               # matrix + health checks
swe mcp sync opencode cursor # copy MCP servers from opencode → cursor
```

## Commands

| Command | Aliases | What it does |
| --- | --- | --- |
| `swe list [tag]` | `ls` | List registered tools, sorted by 100-day usage; optionally filter by tag. |
| `swe info <name\|alias>` | | Show command, version, path, tags, and aliases for a tool. |
| `swe add <name> <command> [desc] [--aliases a,b] [--tags t1,t2]` | | Register (or update) a tool. |
| `swe remove <name\|alias>` | `rm` | Remove a tool from the registry (does not uninstall it). |
| `swe check` | | Probe each installed tool for its live version and refresh the registry. |
| `swe use <name\|alias> [args…]` | `run` | Launch a tool, replacing the current process; extra args pass through. |
| `swe session [N] [use N] [--agent X] [--here]` | | List recent sessions across agents; resume one with `use N`. |
| `swe models [-t] [-p]` | | Model usage analytics; `-t` groups by tool, `-p` shows provider. |
| `swe skills [filter] [-d]` | `sk` | List agent skills across all roots; `-d` shows descriptions. |
| `swe skills scope list` | | List the skill scopes (roots) with per-scope skill counts. |
| `swe tags` | | List all tags and which tools use them. |
| `swe aliases` | | List all short alias → tool mappings. |
| `swe mcp <subcommand> …` | | Manage MCP servers across tools (see below). |
| `swe help [command]` | `-h`, `--help` | Full help, or detailed help for one command. |

### `swe mcp` subcommands

| Subcommand | What it does |
| --- | --- |
| `swe mcp list [tool]` | Matrix view of MCP servers across all tools (or one tool). |
| `swe mcp status [tool]` | Same matrix, plus per-server health checks. |
| `swe mcp sync <source> <target…>` / `--all` | Copy MCP servers between tools, converting formats. Flags: `--only=a,b`, `--force`, `--skip-conflicts`, `--no-interactive`, `--dry-run`, `--strict`. |
| `swe mcp diff <tool1> <tool2>` | Compare two tools' MCP configs. |
| `swe mcp edit <tool> <name>` | Open one server's config in `$EDITOR`. |
| `swe mcp validate [tool…]` | Validate MCP config shape for one/all tools. |
| `swe mcp doctor [--strict]` | Deep diagnostics across every configured server. |

Run `swe <command> --help` or `swe mcp <subcommand> help` for detailed,
per-command help.

## How it works

- **Registry.** Your tool list lives in `~/.config/swe/tools.json`. It's created
  automatically from a sensible built-in default the first time you run `swe`,
  and edited by `swe add` / `swe remove` / `swe check`. It is *your* machine
  state — it is not shipped with the package (see `examples/tools.example.json`
  for the shape).
- **Launching.** `swe use` resolves a name or alias to a real command and
  replaces the current process with it via `os.execvp`, so the tool behaves
  exactly as if you'd typed it directly.
- **Analytics are read-only.** `swe session` and `swe models` parse each tool's
  own on-disk logs and databases (e.g. `~/.claude/projects`, `~/.codex/sessions`,
  `~/.local/share/opencode/opencode.db`, `~/.gemini`, `~/.cursor/projects`).
  quiver never writes to those files.
- **Skills.** `swe skills` walks known skill roots under your home directory
  (and the current project's `.cursor/skills`), de-duplicating roots that
  symlink to the same place, and reads each `SKILL.md`'s front matter.
- **MCP sync.** MCP server definitions are read from each tool's config file,
  normalized to a canonical shape, and re-emitted in the target tool's format
  (standard `mcpServers`, opencode `mcp`, Copilot, …). Nothing is written unless
  you run a real (non-`--dry-run`) `sync` or `edit`.

## Configuration

Everything quiver persists lives under `~/.config/swe/`:

| File | Purpose | Shipped? |
| --- | --- | --- |
| `~/.config/swe/tools.json` | Your tool registry (versions/paths for this machine). | No — auto-created; `examples/tools.example.json` shows the format. |
| `~/.config/swe/mcp.json` | Your MCP source-of-truth (may contain tokens). | No — never committed; git-ignored. |

The MCP subsystem reads/writes each tool's native config (for example
`~/.claude.json`, `~/.cursor/mcp.json`, `~/.config/opencode/opencode.json`).

## Renaming the project

quiver keeps the user-facing command as `swe`, but the name is centralized so
you can change it:

1. **CLI command name** — edit `[project.scripts]` in `pyproject.toml`
   (`swe = "quiver.cli:main"`), then reinstall (`pip install -e .`).
2. **Config directory** (`~/.config/swe`) — edit `CONFIG_DIR_NAME` in
   `src/quiver/__init__.py` (both `cli.py` and `mcp.py` read it from there).
3. **Python package name** (`quiver`) — rename `src/quiver/`, update
   `name`/`packages` in `pyproject.toml`, and the `from quiver…` imports in
   `cli.py`, `mcp.py`, `mcp_server.py`, and `tests/`.
4. **Help text** — the literal `swe` strings in the `HELP` dict and category
   help in `src/quiver/cli.py` (and `MCP_HELP` in `mcp.py`) are cosmetic; update
   them to match your new command name.

## Contributing

Contributions are welcome. To get set up:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -p 'test_*.py'
```

The test suite runs against a throwaway `$HOME`, so it never touches your real
config. Please keep the core CLI standard-library-only, and add tests for new
MCP format handlers or parsers.

## License

MIT — see [LICENSE](LICENSE).
