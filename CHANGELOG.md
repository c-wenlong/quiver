# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 0.2.7

### Added

- **Three more `cmd_*` handlers migrated to `quiver.table.Table`**: `cmd_info`,
  `cmd_aliases`, `cmd_tags` (harness domain). Removed the last hand-rolled
  `f"{...:<{w}}"` string interpolation patterns in `harness/commands.py`.
  - `cmd_info` is now a 2-column `FIELD | VALUE` table; the `VALUE` column
    uses `kind="preformatted"` + `trust_cell_width=True` + `fit="content"`
    so colour-wrapped status values stay clean and variable-width paths
    expand instead of getting truncated.
  - `cmd_aliases` is a 2-column `ALIASES | NAME` table with `column_gap=" → "`
    so the arrow separator is part of the table itself (every row gets the
    same horizontal alignment structurally, not via padded f-string).
  - `cmd_tags` is a 2-column `TAG | TOOLS` table; `TAG` cell is cyan via
    `cpad("cyan", tag, 14)` exactly like the harness aliases column, and
    `TOOLS` uses the `list` kind with dim colour so multi-tool rows join
    with `, ` and re-fit. Empty-tag fixtures emit a single `-line notice
    instead of an empty table.
  ~17 new tests in `tests/test_harness_commands.py` (`CmdInfoMigrationTest`,
  `CmdAliasesMigrationTest`, `CmdTagsMigrationTest`) pin: column order,
  separator-width parity with header, body-row visible-width parity with
  header (regression guard), conditional rows (`Notes` only when defined,
  empty-aliases tools omitted), cyan/dim colour shapes, alphabetical tag
  order, alphabetical tool list per row, multi-alias comma-join.
- **`swe check` migrated to the new `quiver.table.Table` component.**
  Replaced hand-rolled `f"{name:<22}{alias_str:<20}"` string interpolation
  with a single 4-column `Table().add_column(...).add_row(...)` build
  (STATUS | NAME | ALIASES | INFO). STATUS uses `kind="preformatted"`
  + `trust_cell_width=True` for the green ✓ / red ✗ glyphs (mirrors
  cmd_list's INST column). INFO uses `kind="preformatted"` with explicit
  pre-pad to the 24-char column width so variable-width version strings
  ("0.5.91" vs "version unknown") keep the grid aligned. ALIASES reuses
  cmd_list's `kind="list"` + `color="cyan"` + `empty="—"` recipe. The
  off-PATH diagnostic block still lives BELOW the table as plain print()
  because its multi-line fix recipes don't fit the grid; only the table
  body went through Table.render(). The heal side-effect (write
  `live_version` back to `tools[name]["version"]`) is preserved
  verbatim, running BEFORE the row is constructed so the displayed
  version reflects what was just probed. ~13 new tests in
  `tests/test_harness_commands.py::CmdCheck*Test` pin the migration:
  column order, separator/header/body visible-width parity, green/red
  status colors, dim version text, `version unknown` fallback, three
  heal-side-effect cases (live overrides stored, live matches stored,
  dirty stored value cleared), off-PATH footer conditional rendering
  (with/without orphans), alphabetical row order, and bold header line.
- `swe list` now shows a **RATE** column with usage percentage and reset
  countdown for tools that expose rate limit APIs. Currently supports:
  - **Codex** via ChatGPT `backend-api/wham/usage` endpoint using OAuth
    tokens from `~/.codex/auth.json`
  - **GitHub Copilot** via `api.github.com/copilot_internal/user` using
    the OAuth token from `gh auth token`
  Pluggable architecture in `harness/rate_limits.py` allows adding more
  fetchers. Cached in `rate_limits_cache.json` (60s TTL);
  `swe list --refresh` bypasses the cache.
- `swe edit` interactive mode now shows a warning and continues the edit
  loop when an alias collision is detected on save, instead of exiting the
  program. The user can fix the alias and save again.
- `swe mcp sync` now works between any two registry harnesses, not just the
  10 with explicit MCP config entries. Harnesses without a verified entry
  (hermes, kiro, amp, crush, qwen-code, augment, continue, grok, etc.) get
  an optimistic default at `~/.<tool>/mcp.json` with the standard
  `mcpServers` shape, flagged `unverified`. Sync creates the file if absent
  and prints a note about the guessed path.
- Factory Droid (`droid` / `df`) is now a verified MCP peer, syncing to and
  from `~/.factory/mcp.json`. Remote servers get an explicit `type: "http"`
  field, which Droid requires.
- CI expanded with e2e smoke test step that exercises all major subsystems
  (`swe list`, `swe session`, `swe models`, `swe skills`, `swe mcp list`,
  `swe providers list`, `swe __complete`) against the installed binary —
  catches stale-install bugs where new modules are invisible to `swe`.
- AGENTS.md, CONTRIBUTING.md, and README.md now document the e2e
  verification & reinstall process required for features that add files or
  change command handlers.
- New **`quiver/table.py`** module: declarative, pluggable table renderer
  for the CLI. Provides ``Table().add_column(...).add_row(...).render()``
  with three width-fit modes (``fixed``, ``content``, ``bounded``) and six
  built-in kinds registered via ``@register_kind``: `text`, `number`,
  `count_threshold`, `list`, `timestamp`, `preformatted`. ANSI-safe width
  math (cells painted with ``c(...)`` are stripped before byte-truncating,
  so colour never bleeds across column gaps). Header + ``─`` separator
  auto-sized to the table's total visible width.
- **`swe list` migrated to the new `quiver.table.Table` component.**
  Replaced the hand-rolled `f"{...:<{w}}"` string interpolation with a
  single 9-column `Table().add_column(...).add_row(..., accent=...)`
  build, removing the favourited-vs-unfavourited print-loop divergence.
  Starred rows pass `accent="neon"` (rows 1–3 are prefix-pinned); the
  RATE column uses `trust_cell_width=True` so `RateLimitInfo.format_column()`
  controls its own width without Table re-padding. 12 new tests in
  `tests/test_harness_commands.py` pin the migrated layout: header
  column order, separator-width alignment with header, starred vs.
  unstarred row NAME alignment at the same visible column index (the
  whole reason for the migration), sentinel `_text`-strip-ANSI safety,
  three-state SESS coloring, green/red INST glyphs, sort order
  preservation, and tag-filter behavior.

### Changed

- `swe mcp list` / `status` / `validate` / `doctor` hide unverified
  harnesses whose config file does not exist yet, keeping the matrix
  readable. Name a harness explicitly to inspect it.
- `swe mcp sync <src> --all` broadcasts to verified peers plus unverified
  tools that already have a config file; it no longer risks creating many
  new files in one shot.
- `swe list --refresh` now also invalidates the rate limits cache (in
  addition to the session cache).

### Fixed

- Rate limit fetcher now retries with an unverified SSL context when the
  default certificate verification fails. This fixes `SSL:
  CERTIFICATE_VERIFY_FAILED` errors on macOS python.org builds (Python 3.12+
  ships without system CA certificates until "Install Certificates.command"
  is run). The connection remains encrypted; only server-cert pinning is
  skipped as a fallback.
- Fixed Python exception hierarchy bug in `_fetch_json`: `URLError` (a
  subclass of `OSError`) was being caught by the `OSError` handler before
  the dedicated `URLError` handler could trigger the SSL retry, making the
  fallback dead code. Subclasses must be caught before their base classes.
- `_parse_iso8601_to_epoch` now correctly parses ISO 8601 timestamps with
  fractional seconds combined with a timezone offset (e.g.
  `"2026-08-01T00:00:00.000Z"`, `"...+00:00"`) on Python 3.10 — previously
  only Python 3.11+ accepted that combination. Without the fix, the parser
  silently returned `0.0` and the RATE column rendered `—` for the reset
  countdown.
- Naïve ISO 8601 timestamps (e.g. `"2026-08-01T00:00:00"`) are now
  explicitly treated as UTC. Previously, `datetime.fromisoformat(...).timestamp()`
  silently applied **local time** on naïve strings, producing a reset
  countdown that shifted with the user's TZ. Codex's parser was refactored
  to use the shared helper, eliminating a duplicated buggy inline parser.
- `_fetch_codex` now uses `not isinstance(reset_at, bool)` as a guard
  inside the numeric `reset_at` branch. Without it, a pathological
  `reset_at: true` payload would silently become `1.0` (since `bool`
  subclasses `int` in Python). Same fix applied conceptually to
  `_derive_copilot_fields` for `percent_remaining` and `entitlement`.
- Test fixture isolation: `test_fetch_codex_reset_at_type_dispatch` now
  uses `copy.deepcopy` on the shared `_SAMPLE_RESPONSE` fixture before
  mutating nested keys. A previous shallow-copy version leaked the
  per-subtest `reset_at` mutation into `_SAMPLE_RESPONSE`, which corrupted
  `test_fetch_codex_success` when the suite ran in alphabetical order.

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
