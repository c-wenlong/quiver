# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
