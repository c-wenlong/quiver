# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 0.2.7

### Added

- `swe mcp sync` now works between any two registry harnesses, not just the
  10 with explicit MCP config entries. Harnesses without a verified entry
  (hermes, kiro, amp, crush, qwen-code, augment, continue, grok, etc.) get
  an optimistic default at `~/.<tool>/mcp.json` with the standard
  `mcpServers` shape, flagged `unverified`. Sync creates the file if absent
  and prints a note about the guessed path.
- Factory Droid (`droid` / `df`) is now a verified MCP peer, syncing to and
  from `~/.factory/mcp.json`. Remote servers get an explicit `type: "http"`
  field, which Droid requires.

### Changed

- `swe mcp list` / `status` / `validate` / `doctor` hide unverified
  harnesses whose config file does not exist yet, keeping the matrix
  readable. Name a harness explicitly to inspect it.
- `swe mcp sync <src> --all` broadcasts to verified peers plus unverified
  tools that already have a config file; it no longer risks creating many
  new files in one shot.

## [0.2.6] - 2026-07-17

### Added

- `swe autocomplete [zsh|bash|fish]` — generate and inject shell tab-completion
- `swe __complete <words>` — hidden command powering dynamic completions (tool names, aliases, tags, flags)
- `swe star` / `swe unstar` — favourite harnesses, pinned with neon highlight at top of `swe list`
- `swe edit <name> [--field val]` — interactive or flag-based registry field editor
- `swe doctor` — diagnose Node/npm/nvm PATH issues hiding global installs
- `swe install <name>` — install a harness via PATH-visible npm and register it
- `swe providers` — API key management for 27+ LLM providers (metadata only, no key strings)
- `swe session --search` / `-q` — filter sessions by title, path, agent, or session ID text
- `swe list --refresh` / `-r` — bypass session cache and re-parse all sessions
- Three reusable session parser family engines: SQLite (`sqlite_engine.py`), JSONL (`jsonl_engine.py`), JSON (`json_engine.py`)
- 20 session parsers: opencode, Claude Code, Gemini/Antigravity, Codex, Cursor, pi, Freebuff, Droid, Copilot, Continue, Crush, Amp, Kimi, Hermes, Grok, Cline, Forge, Mimo, Tau
- `identity.py` — centralized launch/registry tool mapping (antigravity launches via gemini)
- Disk cache (`session_cache.json`, 60s TTL) for session counts in `swe list`
- `prompt.py` — CR-safe `read_line()` for interactive prompts (fixes `^M` on Enter in cooked TTY)
- Hermes title cleanup: extracts actual task from cron/skill boilerplate

### Changed

- Generalized one-off parsers into declarative engine configs with per-tool callbacks
- `parsers.py` slimmed to config-only adapters over family engines
- `commands.py` and `usage.py` import from central `identity.py` instead of local mappings
- README updated with autocomplete, favourites, providers, doctor, install, edit, and --search/--refresh docs

## [0.2.5] - 2026-07-06

### Added

- `swe skills help <topic>` — per-topic help (catalog, discover, tree, link, unlink, move, scope)
- README **Skills** section with discover, catalog, symlink, and move workflows

### Changed

- Centralized skills help in `skills/help_text.py` (used by subcommands and `swe skills help`)
- Expanded `swe help skills`, `swe help setup`, and `swe help discover`
- CONTRIBUTING and tests README updated for package layout and new test files

## [0.2.4] - 2026-07-06

### Added

- `swe skills tree` — show harness symlink layout (codex/claude/cursor → shared)
- `swe skills tree --sync` — persist observed symlinks to `skill_links.json`
- `swe skills link` / `unlink` / `move` — manage symlinks and move skills between roots
- Skills list shows **VISIBLE VIA** (all harness scopes that see each skill)

## [0.2.3] - 2026-07-06

### Added

- `swe skills discover` — scan `~/Desktop` and `~/Documents` for `*/skills/` catalogs
- `swe skills catalog add|list|remove` — register extra skill directories in `skill_catalogs.json`

## [0.2.2] - 2026-07-06

### Added

- `swe mcp discover` — find MCP servers across tool configs vs `~/.config/swe/mcp.json`
- Skills-root symlink hints in `swe setup` (step 3/3)
- `assets/social-preview.png` (1280×640) for GitHub link previews
- `scripts/upload_github_images.py` — upload avatar/social preview via GitHub token

### Changed

- `swe setup` now covers harnesses, MCP, and skills roots in one wizard
- README commands table documents `setup`, `discover`, and `harness discover`

## [0.2.1] - 2026-07-06

### Added

- `swe harness discover` — scan PATH for AI coding CLIs not yet in `tools.json` (dry-run by default)
- `swe harness discover --apply` / `--apply-all` — register discovered harnesses
- `swe setup` — thin onboarding wizard with optional interactive confirm
- `swe discover` alias for `swe harness discover`
- Repo mascot (`assets/mascot.png`) in README

## [0.2.0] - 2026-07-06

### Changed

- Refactored monolithic `cli.py` into domain packages: `harness/`, `sessions/`, `skills/`, `mcp/`
- Extracted shared `console.py` and `paths.py` utilities
- Added unit tests for harness registry, sessions aggregator, models analytics, and skills discovery (30 tests total)
- CI runs tests verbosely across Python 3.10–3.13

## [0.1.0] - 2026-07-06

### Added

- Initial open-source release of **quiver** (CLI command: `swe`)
- Tool registry with tags, aliases, install checks, and 100-day usage sorting
- Launch any registered AI coding CLI via `swe use`
- Cross-agent session listing and resume (`swe session`)
- Model usage analytics mined read-only from tool logs (`swe models`)
- Agent skills discovery across shared, Cursor, Claude, Codex, and plugin roots (`swe skills`)
- MCP server sync, diff, validate, and doctor commands (`swe mcp`)
- Stdlib-only core; optional FastMCP server extra for session history MCP tool
- Test suite (15 unit tests) with isolated `$HOME` fixtures

[0.1.0]: https://github.com/c-wenlong/quiver/releases/tag/v0.1.0
