# Contributing to quiver

Thanks for your interest in contributing! quiver is a small, stdlib-only CLI — we want to keep it fast, portable, and easy to hack on.

## Getting started

```bash
git clone https://github.com/c-wenlong/quiver.git
cd quiver
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -p 'test_*.py'
```

## Project layout

The CLI is organized by domain under `src/quiver/`:

| Package | Role |
| --- | --- |
| `cli.py` | Top-level command dispatch |
| `help_text.py` | `swe help` and per-command summaries |
| `harness/` | Tool registry, discover, setup |
| `sessions/` | Cross-agent session and model analytics |
| `skills/` | Skill discovery, catalogs, symlink layout (`commands.py`, `catalog_commands.py`, `layout_commands.py`, `help_text.py`) |
| `mcp/` | MCP sync, discover, format handlers |

Shared utilities: `console.py`, `paths.py`.

## What to contribute

- **New tool parsers** — session history or model usage for another AI coding CLI
- **MCP format handlers** — support for a tool's MCP config format in `mcp_formats.py`
- **Skills roots** — additional skill directory locations in `skill_roots()` or catalog discovery
- **Bug fixes and docs** — always welcome

## Guidelines

1. **Keep the core stdlib-only.** Optional deps belong in `[project.optional-dependencies]` (see the `server` extra).
2. **Never commit user state.** Do not add `tools.json`, `mcp.json`, or machine-specific paths to the repo.
3. **Add tests** for new MCP format handlers, skills layout/catalog logic, or non-trivial parser logic. Tests use a throwaway `$HOME` — they must not depend on your real config.
4. **Match existing style** — simple functions, minimal abstractions; follow patterns in the domain packages above.
5. **Update help text** when adding user-facing commands — top-level `help_text.py`, and `skills/help_text.py` or `mcp/` help for subcommands.
6. **One concern per PR** — easier to review and merge.

## Pull request process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes and ensure tests pass locally.
3. Update README, `swe help`, or `swe skills help` if you add user-facing commands or flags.
4. Open a PR with a clear description of what changed and why.

## Reporting bugs

Use the [bug report template](https://github.com/c-wenlong/quiver/issues/new?template=bug_report.yml) and include:

- Your OS and Python version
- Output of `swe --help` and the failing command
- Steps to reproduce

## Questions?

Open a [discussion](https://github.com/c-wenlong/quiver/discussions) or an issue — we're happy to help you find a good first task.
