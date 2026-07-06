# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
