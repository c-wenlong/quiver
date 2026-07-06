<p align="center">
  <img src="assets/mascot.png" alt="quiver mascot — a friendly quiver holding arrows for terminal, code, AI, and cursor" width="360">
</p>

<h1 align="center">
quiver
</h1>

<p align="center">
  <strong>One command to launch, resume, and analyze every AI coding CLI on your machine.</strong>
</p>

<p align="center">
  <a href="https://github.com/c-wenlong/quiver/actions/workflows/ci.yml"><img src="https://github.com/c-wenlong/quiver/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
  <img src="https://img.shields.io/badge/deps-stdlib--only-brightgreen.svg" alt="stdlib only">
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#contributing">Contributing</a>
</p>

---

**quiver** is a central manager for the growing zoo of AI coding command-line tools — Claude Code, Codex, Gemini CLI, Cursor CLI, opencode, Copilot, and many more.

It keeps a small registry of the harnesses you use, launches any of them (by name or short alias), lets you resume recent sessions across *any* agent, mines read-only usage analytics from each tool's own logs, discovers agent skills installed across your machine, and keeps MCP server configs in sync between tools.

The command you type is **`swe`** (short, fits in muscle memory). The project and Python package are named **quiver** — think of it as the quiver that holds all your arrows (see the mascot above).

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

  6/6 installed  ·  swe use <name|alias>  │  swe info <name>  │  swe list <tag>  │  swe check
  tags:  agentic  byok  coding  local  …
```

## Why quiver?

If you juggle more than one AI coding agent you end up with a mess:

- Different launch commands and resume flags for every tool
- Usage scattered across a dozen log formats
- MCP server definitions copy-pasted between configs
- Skills installed in five different directory trees

**quiver** puts a single, consistent front door on all of it — without wrapping or replacing the tools themselves. It reads their logs read-only and shells out to the real binaries.

## Features

| Area | What you get |
| --- | --- |
| **Registry** | List every AI coding CLI with tags, aliases, versions, and install status |
| **Launch** | Start any tool by name or alias; extra args pass straight through (`execvp`) |
| **Sessions** | Unified, time-sorted view of recent sessions across agents + one-command resume |
| **Models** | Aggregate model usage parsed read-only from each tool's session logs |
| **Skills** | Discover every `SKILL.md` across shared, Cursor, Claude, Codex, and plugin roots |
| **MCP sync** | Inspect, compare, validate, and copy MCP servers between tools |

## Install

### pipx (recommended)

```bash
pipx install git+https://github.com/c-wenlong/quiver.git
swe --help
```

### From source

```bash
git clone https://github.com/c-wenlong/quiver.git
cd quiver
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
swe --help
```

### Optional: MCP history server

Exposes recent sessions as an MCP tool (requires the `server` extra):

```bash
pip install -e ".[server]"
python -m quiver.mcp_server
```

**Requirements:** Python 3.10+. The core CLI has **no third-party runtime dependencies** (standard library only).

## Quick start

```bash
swe setup                    # onboarding wizard (harness + MCP + skills)
swe setup --apply            # apply safe defaults without prompting
swe harness discover         # scan PATH for unregistered AI CLIs
swe mcp discover             # find MCP servers not in ~/.config/swe/mcp.json

swe list                     # all registered tools, sorted by recent usage
swe list agentic             # filter by tag
swe info claude              # command, version, path, tags, aliases
swe check                    # probe installed tools and refresh versions

swe use cc                   # launch Claude Code (alias for `claude`)
swe use codex --help         # extra args are passed straight through

swe session                  # last 10 sessions across ALL agents
swe session use 3            # cd into session #3 and resume it
swe session --agent claude   # filter by agent
swe session --here           # only sessions in the current directory

swe models                   # model usage across all tools
swe models -t -p             # grouped by tool, with provider prefix

swe skills                   # every SKILL.md across all skill roots
swe skills scope list        # list skill roots (scopes) with counts

swe mcp list                 # matrix of MCP servers across tools
swe mcp sync opencode cursor # copy MCP servers between tools
```

## Commands

| Command | Aliases | Description |
| --- | --- | --- |
| `swe setup [--apply]` | | Onboarding wizard (harnesses, MCP, skills roots) |
| `swe list [tag]` | `ls` | List registered tools, sorted by 100-day usage |
| `swe info <name\|alias>` | | Show command, version, path, tags, aliases |
| `swe add <name> <cmd> …` | | Register or update a tool |
| `swe remove <name\|alias>` | `rm` | Remove from registry (does not uninstall) |
| `swe check` | | Probe live versions and refresh registry |
| `swe harness discover [--apply]` | | Scan PATH for unregistered AI coding CLIs |
| `swe discover [--apply]` | | Alias for `swe harness discover` |
| `swe use <name\|alias> [args…]` | `run` | Launch a tool (replaces current process) |
| `swe session [N] [use N] [--agent X] [--here]` | | List or resume recent sessions |
| `swe models [-t] [-p]` | | Model usage analytics |
| `swe skills [filter] [-d]` | `sk` | List agent skills and paths |
| `swe skills scope list` | | List skill scopes (roots) with counts |
| `swe tags` | | List tags and associated tools |
| `swe aliases` | | List alias → tool mappings |
| `swe mcp <subcommand> …` | | MCP server management (see below) |
| `swe help [command]` | `-h` | Full or per-command help |

Run `swe <command> --help` for detailed help on any command.

### `swe mcp` subcommands

| Subcommand | Description |
| --- | --- |
| `swe mcp discover [--apply]` | Find MCP servers across tools vs `mcp.json` |
| `swe mcp list [tool]` | Matrix view of MCP servers across tools |
| `swe mcp status [tool]` | Matrix + health checks |
| `swe mcp sync <source> <target…>` | Copy servers between tools (format conversion) |
| `swe mcp diff <t1> <t2>` | Compare two tools' MCP configs |
| `swe mcp edit <tool> <name>` | Edit one server in `$EDITOR` |
| `swe mcp validate [tool…]` | Validate MCP config shape |
| `swe mcp doctor [--strict]` | Deep diagnostics |

Flags for `sync`: `--only=a,b`, `--force`, `--skip-conflicts`, `--dry-run`, `--strict`.

## How it works

```mermaid
flowchart LR
  subgraph swe["swe CLI"]
    R[Registry]
    L[Launch]
    S[Sessions]
    M[Models]
    K[Skills]
    P[MCP sync]
  end

  R --> TJ["~/.config/swe/tools.json"]
  S --> Logs["Tool session logs\n(read-only)"]
  M --> Logs
  K --> Roots["Skill roots\n~/.agents/skills, plugins, …"]
  P --> MCP["Per-tool MCP configs"]
  L --> Bin["Real CLI binaries\nclaude, codex, …"]
```

- **Registry** — your tool list lives in `~/.config/swe/tools.json`, auto-created from built-in defaults on first run. Edited by `swe add` / `remove` / `check`. Not shipped with the package (see `examples/tools.example.json`).
- **Launching** — `swe use` resolves a name or alias and replaces the current process via `os.execvp`, so the tool behaves exactly as if you'd typed it directly.
- **Analytics** — `swe session` and `swe models` parse each tool's on-disk logs (e.g. `~/.claude/projects`, `~/.codex/sessions`, `~/.local/share/opencode/opencode.db`). quiver **never writes** to those files.
- **Skills** — walks known skill roots under `$HOME` (and `./.cursor/skills`), de-duplicates symlinked paths, reads each `SKILL.md` front matter.
- **MCP sync** — reads each tool's native MCP config, normalizes to a canonical shape, re-emits in the target format. Nothing is written unless you run a real (non-`--dry-run`) `sync` or `edit`.

## Configuration

Everything quiver persists lives under `~/.config/swe/`:

| File | Purpose | Shipped? |
| --- | --- | --- |
| `tools.json` | Your tool registry (versions for this machine) | No — auto-created |
| `mcp.json` | MCP source-of-truth (may contain tokens) | No — git-ignored |

The MCP subsystem also reads/writes each tool's native config (e.g. `~/.claude.json`, `~/.cursor/mcp.json`, `~/.config/opencode/opencode.json`).

## Supported tools

quiver ships with defaults for Claude Code, Codex, Gemini CLI, GitHub Copilot CLI, opencode, Forge, Factory Droids, Ollama, pi, Continue, Cursor CLI, Cline, and more. Register your own with `swe add`.

Session parsers currently cover: **opencode**, **Claude Code**, **Gemini/Antigravity**, **Codex**, **Cursor**, **pi**, and **Freebuff**. Model analytics cover opencode, Claude Code, Codex, and Freebuff.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -p 'test_*.py'
```

The test suite runs against a throwaway `$HOME`, so it never touches your real config.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Renaming

quiver centralizes naming so you can change it:

1. **CLI command** — `[project.scripts]` in `pyproject.toml`, then reinstall
2. **Config dir** — `CONFIG_DIR_NAME` in `src/quiver/__init__.py`
3. **Package name** — rename `src/quiver/`, update imports and `pyproject.toml`

## License

MIT — see [LICENSE](LICENSE).
