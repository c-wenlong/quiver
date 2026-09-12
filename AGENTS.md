# AGENTS.md

Instructions for any coding agent working in this repository.

## What this is

`quiver` is a Python package whose console entry point is `swe`. It manages the AI coding CLIs on a machine (Claude Code, Codex, Gemini, Cursor, opencode, ...): one registry, one shared instruction file and skills tree symlinked into every harness, cross-agent session history, MCP config sync, rate limits, and coding-session reports.

The core CLI is stdlib-only with no runtime dependencies at all. Optional extras: `server` (FastMCP session server via `python -m quiver.mcp_server`) and `test` (coverage). Python floor is 3.11 and CI runs the endpoints, 3.11 and 3.13. No version-gated code remains in the tree: `tomllib` is stdlib throughout, so the two legs guard against stdlib drift rather than covering a branch of our own. Do not add a 3.10 compatibility shim back: if a user reports a 3.10 failure, the answer is `v0.2.9`, the last release that runs on 3.10, not a new fork in the source. Ubuntu 22.04 LTS is the distro this actually affects.

Deeper references, read before touching the relevant area:

- [ARCHITECTURE.md](ARCHITECTURE.md): layering rules, `~/.quiver` layout, registry states, link-state vocabulary, session engines, caches, drift checks.
- [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Commands

```bash
# Setup (editable install; the venv here is .venv, Python 3.12)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

# Full test suite (unittest is the canonical runner; CI uses exactly this)
python3 -m unittest discover -s tests -p 'test_*.py'

# One file / one test method
python3 -m unittest tests.test_table
python3 -m unittest tests.test_table -k render
python3 -m unittest tests.test_rate_limits.TestClass.test_method

# Coverage, as CI runs it (floor is 69%, configured in .coveragerc)
test_home="$(mktemp -d)"
HOME="$test_home" python -m coverage run -m unittest discover -s tests -p 'test_*.py'
python -m coverage combine && python -m coverage report

# Run the CLI from source without reinstalling
PYTHONPATH=src python -m quiver.cli list

# Run the MCP sync subsystem directly
python -m quiver.mcp list
```

There is no linter or formatter configured. No pytest is installed; `tests/conftest.py` is a pytest fixture that pins terminal width to 200 columns, so it only fires if someone runs pytest. Under unittest, tests that assert on rendered width must patch `quiver.console.terminal_width` themselves.

### Always run tests against a throwaway HOME

Every command reads and writes `~/.quiver`. Run the suite and any manual `swe` verification with `HOME="$(mktemp -d)"` so you never pollute the real registry or count real sessions. CI does this for every job.

### Syntax floor

`tests/test_syntax_floor.py` compiles every source file with the oldest interpreter it can find. Do not use PEP 701 nested f-strings (quotes reused inside a replacement field); they pass on 3.12 and crash on import for 3.11 users. This shipped as a real bug in 0.2.8.

## End-to-end verification and reinstall

**Every e2e feature must be verified against the installed `swe` binary, not just unit tests.**

The `swe` command is a pip-installed console entry point (declared in `[project.scripts]` in `pyproject.toml`). New Python files added to `src/quiver/` are **not** available to the installed `swe` until you reinstall. Unit tests with `PYTHONPATH=src` can pass while the installed binary silently fails because it doesn't see the new module.

Required steps for any feature that adds files or changes `cmd_*` handlers:

1. **Write tests** and run `python -m unittest discover -s tests -p 'test_*.py'`.
2. **Reinstall the package** with `pip install -e .` (or `pipx install --force git+...` for pipx installs). With an editable install new files are picked up automatically, but a stale non-editable install won't see them until you reinstall.
3. **Verify e2e** by running the actual `swe <command>` against a temp HOME, not just `PYTHONPATH=src python -m quiver.cli <command>`. CI does the same in three separate jobs — a built wheel installed into a clean venv, the Nix derivation's output, and a macOS run — each exercising the installed binary against a `mktemp -d` home. Read `.github/workflows/ci.yml` for the exact commands rather than assuming; that list gets trimmed from time to time.
4. **Open a PR**, one concern per PR, with a clear description of what changed and why.

Common pitfall: if a feature works with `PYTHONPATH=src python -m quiver.cli` but not with the installed `swe` command, the installed copy is stale (likely a non-editable install). Re-run `pip install -e .` to switch to editable mode and sync with the source tree.

## Architecture in one screen

`src/quiver/cli.py` is dispatch only: a `COMMANDS` dict mapping the first argv token to a `cmd_*` function. Top-level entries are shortcuts for the most-used harness verbs. Everything else lives under a domain command (`harness`, `mcp`, `find`, `providers`, `report`, `skills`, `session`, `config`, `setup`, `init`), each of which owns its own sub-dispatch.

Domain packages under `src/quiver/`:

| package | owns |
|---|---|
| `harness/` | registry (`registry.py` is the only module that touches `harness.json`), list/info/add/edit/check/doctor, discover, rate limits, drift checks |
| `sessions/` | three parser engines (`engines/jsonl_engine`, `json_engine`, `sqlite_engine`), per-tool `*ParserConfig` adapters in `parsers.py`, aggregator with 60s disk cache, `identity.py` maps session tool names to launch binaries and registry keys |
| `skills/` | SKILL.md discovery, catalogs, harness symlink layout (`HARNESS_ROOTS` fallback table) |
| `mcp/` | hub `~/.quiver/mcp.json`, per-tool format handlers in `formats.py`, sync/diff/validate/doctor, `${NAME}` secret refs |
| `find/` | read-only views of shared assets (`amd`, `skills`, `plugins`, `mcps`), `PLUGIN_FALLBACK` table |
| `init/` | writes the shared `AGENTS.md` and skills tree, symlinks them and per-harness hook scripts (`hooks.py`) into each harness, migrates the old `~/.config/swe` root |
| `reports/` | daily/weekly session reports: transcripts, triage, batching, model runners, follow-up ledger |
| `providers/` | LLM provider metadata and API key file lookup |
| `setup/` | interactive onboarding wizard (the one logic module allowed to print) |
| `history/` | backward-compatible re-exports; prefer `quiver.sessions` |

Five modules form the bottom layer and never import a domain package: `paths.py` (every path under `~/.quiver`, use `*_for(home)` helpers in tests), `console.py`, `table.py`, `configuration.py`, `keys.py` (the shared terminal key reader). They are ordered among themselves — `console.py` and `keys.py` import nothing from the project at all, `paths.py` takes only the constants in `quiver/__init__.py`, `table.py` imports `console`, `configuration.py` imports `paths`. Command modules own presentation; logic modules should not print. One intentional cycle, `harness <-> sessions`, uses function-local imports.

`harness/stars.py` and `harness/archive.py` are compatibility shims over `registry.py`, kept so old call sites work. Do not add new state to them.

### Rules that are enforced mechanically

- **Code never hardcodes what `harness.json` can say.** `HARNESS_ROOTS` and `PLUGIN_FALLBACK` are read-only fallbacks for harnesses the registry has never heard of. A harness's `capabilities.skills` / `capabilities.plugins` entry always wins. `swe doctor` warns only on broken joins (name mismatch, `supported: true` with no `root`, table entry for an unknown harness).
- **Help must match dispatch.** `swe doctor` (via `harness/drift.py`) compares `help_text.py`'s `HELP` topics and `COMMAND_CATEGORIES` against `cli.py`'s `COMMANDS`, checks subcommand help dicts, and scans prose for commands that do not exist. Adding a command without a help entry, or leaving a dead topic, fails doctor. Follow the checklist under "When adding or changing CLI commands" below.
- **Never commit user state.** `harness.json`, `mcp.json`, `tools.json`, `providers.json`, `stars.json`, and caches are gitignored. This repo is the control plane; `~/.quiver` is the data plane with its own git history.

### Registry states and link states

A `harness.json` entry has `state` in `{"active", "starred", "archived"}` (absent means active), matching `multiselect.py`'s `STATES` tuple exactly. Archiving is not removal: it records that a harness was evaluated. `swe list --scope` and `swe find --harness` filter on this.

Symlink status shared by `init`, `list`, and `find`: `linked`, `relink`, `create`, `absorb`, `keep`, `conflict`, `skipped`. `keep` is never overwritten on a plain run because it marks the only copy of something.

### Extending

- **New session parser:** add a `parse_<tool>()` in `sessions/parsers.py` returning a `SqliteParserConfig`, `JsonlParserConfig`, or `JsonParserConfig`, and register the tool in `sessions/identity.py`. Parser crashes are recorded by `sessions/failures.py` so a broken parser reports an error instead of reading as zero sessions.
- **New rate-limit fetcher:** define a fetch function returning `RateLimitInfo` and call `register(tool_name, fetch)` in `harness/rate_limits.py`. Rate limits are fetched only for starred harnesses; cache TTL is 300s, overridable with `SWE_RATE_LIMITS_TTL`.
- **New MCP config format:** subclass `McpFormatHandler` in `mcp/formats.py` and call `register_format_handler(name, handler)`.
- **New table:** use `table.py` (`Table`, column kinds `text`, `number`, `count_threshold`, `list`, `timestamp`, `preformatted`; custom kinds via `@register_kind`). Pre-compute rows outside the loop, pin columns once, `add_row` per item, `render` once. Do not hand-roll f-string padding.
- **Interactive input:** use `prompt.read_line()` rather than `input()`; it restores TTY cooked mode and handles CR/LF/CRLF.
- **Terminal key readers.** Every raw-mode widget reads keys through `keys.py::read_key(fd, letters=None, sequences=None)`. It reads a whole CSI/SS3 sequence to its final byte and treats only a bare Esc as `"escape"`; wheel reports (`\x1b[<64;x;yM`), application-cursor arrows (`\x1bOA`) and unknown sequences are mapped or ignored, never cancel. A widget supplies its own `letters` (plain bytes) and `sequences` (post-Esc bytes) maps, which win over the shared defaults, and writes `keys.MOUSE_ON` after `tty.setraw` and `keys.MOUSE_OFF` in the `finally` before `tcsetattr`. `sessions/picker.py::_read_key`, `multiselect.py::_read_key` / `_read_state_key` and `find/browser.py::_read_key` are all thin wrappers over it. A new widget must not hand-roll a reader: the two-byte version this replaced turned one wheel notch into a cancel plus a handful of stray letters.
- **Highlighted rows.** A line the transcript view shades (a user prompt) is painted with `COLORS["user_bg"]` at its head, and `picker._wrap` fills every row it wraps to out to the full width with `console.fill_ansi`. Use `fill_ansi`, never `lpad`: `lpad` puts the padding *after* the trailing reset, so the shading stops at the last word and the rest of the row stays bare. A blank line inside a painted turn becomes a full bar rather than an empty line, or a paragraph break cuts the slab in half. `SWE_USER_BG` overrides the shade for a light terminal.
- **Session picker preview:** `sessions/picker.py::pick_session` takes an optional `preview(index) -> list[str]` callable. `cmd_session`'s interactive path passes `_session_preview`, which reads the tail through `reports/transcripts.py::read_transcript` (function-local import, so `reports -> sessions` stays the only module-level direction) and memoises per row. The widget shows the lines in a pager on the alternate screen, wrapping them with `console.wrap_ansi` so styled text keeps its colour across a break, and shows the footer hint only when a callable is given.

## When adding or changing CLI commands

The `swe autocomplete` feature relies on a hardcoded list of primary subcommands in `src/quiver/completion.py` (`_PRIMARY_COMMANDS`) and context-aware completion rules (`_TOOL_TARGET_COMMANDS`, `_COMMAND_FLAGS`, `_SUBCOMMANDS`).

**When you add a new command to `COMMANDS` in `cli.py`:**

1. Add the command to `_PRIMARY_COMMANDS` in `completion.py` with a short description.
2. If the command takes a tool name/alias as its first argument, add it to `_TOOL_TARGET_COMMANDS`.
3. If the command accepts flags, add them to `_COMMAND_FLAGS`.
4. If the command should appear in `swe help`, add it to `COMMAND_CATEGORIES` in `help_text.py` and add a `HELP` entry. `swe doctor` fails on a command without one.
5. Run `tests/test_completion.py` and `tests/test_drift.py` to verify completions and help drift.

**When you remove or rename a command:**

1. Remove it from `_PRIMARY_COMMANDS` in `completion.py`.
2. Remove it from `_TOOL_TARGET_COMMANDS` or `_COMMAND_FLAGS` if present.
3. Remove it from `COMMAND_CATEGORIES` and `HELP` in `help_text.py`.

## Test conventions

- Session parser tests mock `os.path.expanduser` in `quiver.sessions.parsers` to redirect `~/.tool/` paths to temp dirs.
- Engine tests use `expand_path()` which only expands `~` prefixes (mock-safe).
- Completion tests mock `load_registry` for tool/tag completions and `SHELL_CONFIGS` for script generation.
- Class-level JSON fixtures like `_SAMPLE_RESPONSE` are shallow-copied by default. Tests that **mutate nested keys** (e.g. `body["rate_limit"]["primary_window"]["reset_at"]`) MUST use `copy.deepcopy` to avoid leaking the mutation into later tests that read the same class fixture. Tests that only **replace top-level keys** are safe with shallow copies.
- Run all tests: `python -m unittest discover -s tests -p 'test_*.py'`

## Gotchas log

Non-obvious decisions and traps, recorded so they are not re-learned.

- **Never invoke `tau` or `mimo`, not even `--help`.** Tau treats any unrecognised argv as a prompt and creates a real session (and may hang at 100% CPU waiting on a model). Mimo re-imports every Claude Code transcript into its own db on startup, re-stamping 150+ sessions with fresh timestamps so they flood the top of `swe session`. Inspect their on-disk data instead.

- **Session engines** (`sessions/engines/`): three family engines (SQLite, JSONL, JSON) with declarative `*ParserConfig` dataclasses. Adapters in `parsers.py` are thin configs over these engines.
- **Title provenance** (`Session.title_source`): "" = first user prompt, "rename" = user-set name, "auto" = harness-generated. `swe session` renders "rename" bright italic and everything else dim. Only harnesses with an on-disk discriminator ever emit "rename": Claude (`custom-title` event in the transcript tail), Copilot (`user_named:` in `session-state/<id>/workspace.yaml`), Kimi (`state.json` `title_generated`), Antigravity (`conversation_summaries.db` `title` column), Grok (`summary.json` `title_is_manual`), Droid (`session_start.isSessionTitleManuallySet`), Pi (`session_info` event), Tau (index `title`), Devin (an inline `/rename <name>` or `/rename-chat <name>` in `prompt_history` whose argument equals the title; a name typed into the interactive rename prompt is never recorded, so that session reads as auto), Codex (inferred, see below). opencode, Forge, Mimo, Continue and Cursor write a rename into the same field as their auto-titler with no marker, so they can never be told apart; do not invent heuristics for them. Cline, Freebuff and Gemini have no rename at all.
- **Codex threads** (`sessions/parsers.py::parse_codex`): a codex thread's name is not in its rollout transcript. Two sidecars hold it, both keyed by the uuid at the end of the `rollout-<iso>-<uuid>.jsonl` filename, and `parse_codex` reads the rollouts then rewrites titles from them:
  - `~/.codex/state_<n>.sqlite` (highest `<n>` is live; older files are migration leftovers) `threads` table: `name` is the current display name, `preview` is codex's own note of the first message the *user* sent. `preview` beats scanning the transcript, whose first user-role item is usually injected context. An **empty `preview` means codex considers the thread empty** and hides it — its own listing filters `preview <> ''`, see the `idx_threads_visible_*` partial indexes — so `parse_codex` drops those rows. That is the only thing allowed to drop a session; a thread the store has never heard of is kept.
  - `~/.codex/session_index.jsonl`: one append per name change. This is the *only* signal separating a `/name` from codex's auto-titler, since both write the same field.
- **Codex rename inference** (`sessions/parsers.py::_codex_is_rename`): codex records no flag, so provenance is inferred from the index history's shape and timing. Codex writes at most two names itself, in order: a prefix of the first prompt, then a generated title within seconds. So a deduped sequence longer than that budget (one entry, not two, when the first name is not a prefix of `preview`) ends in a rename, as does any thread whose last record is over `_CODEX_AUTO_TITLE_WINDOW_MS` after its first. This deliberately under-marks: a rename leaving a single record inside the window reads as a generated title. If you widen it, re-check it marks no auto-title — a false "rename" is worse than a missed one, because the bright italic is the listing's only signal of a name a human chose.
- **Session listing widths** (`sessions/commands.py::_build_session_table`): IDX, LAST ACTIVE and AGENT are sized from the rows this run actually holds, each floored at its own header, before any cell is built. They pre-pad their own cells (`trust_cell_width=True`), so the width has to be settled up front and cannot be handed to the table's own fit modes. Do not go back to hardcoded 4/14/14: that left dead columns on every listing and overflowed the IDX cell once an index reached three digits.
- **Claude handoffs** (`sessions/parsers.py::_claude_handed_off`): Claude Code can continue one conversation in a fresh transcript, writing `{"type": "continued-in", "continuedInSessionId": ...}` into the old file. Both files then carry the same title, so `parse_claude` drops the predecessor. The marker alone is **not** enough: work often carries on in the old file afterwards, and the two are then separate sessions (one machine had both shapes, a handoff with nothing after it and a handoff followed by 860 more turns). A session is superseded only when no `user`/`assistant` record follows the marker, and only when the successor is really on disk. That test is why an 8KB tail read suffices: a marker with conversation behind it is not in the tail at all.
- **Tool identity** (`sessions/identity.py`): `LAUNCH_TOOL` maps session `tool_name` to the CLI binary for resume; `COUNT_TO_REGISTRY` maps to the registry key for `swe list` counts. Antigravity sessions launch via `gemini` and count under gemini.
- **Session cache** (`sessions/aggregator.py`): `get_all_sessions(use_cache=True)` reads from `~/.quiver/cache/session_cache.json` with a 60s TTL. `swe list` uses cache; `swe session` bypasses it. `swe list --refresh` invalidates.
- **Interactive prompts** (`prompt.py`): `read_line()` restores TTY cooked mode and handles CR/LF/CRLF. Used by `swe edit` and `swe setup` instead of `input()`.
- **Rate limits** (`harness/rate_limits.py`): pluggable fetcher architecture. Each tool registers a fetch function returning `RateLimitInfo` (used_percent, limit_reached, reset_at, plan_type). `get_all_rate_limits()` aggregates across fetchers with a 300s disk cache (`~/.quiver/cache/rate_limits_cache.json`, TTL overridable via `SWE_RATE_LIMITS_TTL`). Only starred harnesses are polled. Registered fetchers: Codex (ChatGPT `backend-api/wham/usage` with OAuth tokens from `~/.codex/auth.json`), GitHub Copilot (`api.github.com/copilot_internal/user` with `gh auth token`), Claude, Droid, Antigravity (loopback quota RPC of the running app), Freebuff, Cursor (dashboard `get-current-period-usage` with the editor's or CLI's session JWT), Devin (Windsurf's `SeatManagementService/GetUserStatus` with the key from `~/.local/share/devin/credentials.toml`). Add new fetchers with `register(tool_name, fetcher_fn)`.
- **Devin sessions** (`sessions/parsers.py::parse_devin`, `reports/transcripts.py::_read_devin`): the store is `~/.local/share/devin/cli/sessions.db`. `sessions.hidden = 1` rows are ones Devin itself hides. `prompt_history` holds every prompt typed at the REPL, including `/usage` and `/login-status` typed *before* the session existed and stamped with its id anyway, so only rows at or after `sessions.created_at` belong to it. `message_nodes` is a forest, not a list: a streamed assistant reply is stored again each time it grows, as a sibling under the same parent, and `sessions.main_chain_id` points at the head of the chain holding the final version of every message. Read by walking parents from that head; reading `ORDER BY node_id` shows each reply several times.
- **Devin usage** (`harness/rate_limits.py:_fetch_devin`): Devin CLI is Windsurf-backed, so the quota comes from `POST https://server.codeium.com/exa.seat_management_pb.SeatManagementService/GetUserStatus`, a Connect RPC. The API key (`windsurf_api_key` in `credentials.toml`) travels in the JSON body's `metadata`, not a header, and the server answers `invalid_argument` unless `ide_name` and version fields accompany it. `planStatus` carries `dailyQuotaRemainingPercent` / `weeklyQuotaRemainingPercent` with `*ResetAtUnix` as decimal-string seconds; the more used window is shown (`1d` / `7d`). Plans on prompt credits report `availablePromptCredits` against `planInfo.monthlyPromptCredits` (`-1` is unlimited) and show as `remaining/total`.
- **Cursor usage** (`harness/rate_limits.py:_fetch_cursor`): the token is the session JWT the editor keeps in `state.vscdb` (`ItemTable` key `cursorAuth/accessToken`) and `cursor-agent` keeps in the macOS keychain (`cursor-access-token` / `cursor-user`); both are the same session. The dashboard API takes it only as the cookie `WorkosCursorSessionToken=<sub>::<jwt>` (URL-encoded, `sub` read from the unverified JWT payload); a bearer header gets 401. Every dashboard POST also needs `Origin: https://cursor.com` or it is refused with 403 "Invalid origin for state-changing request", reads included. The column shows the more exhausted of `totalPercentUsed` (label `auto`, the figure the dashboard quotes for Auto mode) and `apiPercentUsed` (label `api`, named models). `autoPercentUsed` exists in the payload but the dashboard never shows it, so neither do we. The legacy `GET /api/usage` endpoint still answers but reports request counts that are always zero on current plans.
- **Copilot header spoofing** (`harness/rate_limits.py:_fetch_github_copilot`): the undocumented `api.github.com/copilot_internal/user` endpoint gates access on `Editor-Version` and `Editor-Plugin-Version` matching the official VS Code Copilot Chat client. Without those exact headers the endpoint returns 403. The `User-Agent` is set to `quiver/<version>` for traceability, but otherwise the request is wired to look like the official client. If GitHub rotates these values the fetcher will break silently. Be alert when touching this code.
- **Copilot plan decoration** (`harness/rate_limits.py:_decorate_copilot_plan_type`): appends `/edu` to `copilot_plan` when the SKU signals an educational quota. Only applies when `copilot_plan == "individual"` **and** `access_type_sku` contains `"educational"` (case-insensitive). Paid individual plans, business plans, and enterprise plans are left untouched, so users can tell free educational accounts apart from paid Copilot Pro at a glance in `swe list`.
- **ISO 8601 parser** (`harness/rate_limits.py:_parse_iso8601_to_epoch`): shared `str -> epoch float` helper used by Copilot's fetcher and Codex's string-`reset_at` fallback path. A malformed fractional part sitting next to a timezone offset is salvaged to whole-second precision by a retry at the end of the function, rather than being thrown away; that arm is live on every supported interpreter, so do not delete it as version-specific. Naive datetimes (no offset) are explicitly treated as UTC to avoid `datetime.timestamp()` silently applying local time and producing TZ-dependent reset countdowns. Returns `0.0` for any unparseable / falsy input so a bad timestamp never breaks `swe list`.
- **Bool guard for `reset_at` type dispatch** (`harness/rate_limits.py:_fetch_codex`): the numeric branch is `isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool)`. Without the `not bool` part, a pathological `reset_at: true` payload would silently become `1.0` because `bool` subclasses `int` in Python. The same guard pattern is applied in `_derive_copilot_fields` with explicit `try/except (TypeError, ValueError)` around float/int casts, returning `(0, False)` rather than crashing `swe list` mid-render.
- **TLS trust store** (`harness/rate_limits.py:_fetch_json` / `_verified_context`): python.org builds on macOS ship without CA certificates, so `urllib.request.urlopen` fails with `SSL: CERTIFICATE_VERIFY_FAILED` on a healthy connection. The helper retries once with a context built from the first bundle in `_SYSTEM_CA_BUNDLES` that exists (`/etc/ssl/cert.pem` on macOS, the Debian / RHEL / SUSE paths on Linux), then `certifi` if importable. Verification is never disabled: with no bundle at all it prints one warning per process and sends nothing, because the bearer token would otherwise go to whoever answers. That warning is the whole story when every starred harness goes blank at once. **Gotcha:** `urllib.error.URLError` is a subclass of `OSError`; always catch `URLError` before `OSError` in except chains or the SSL retry handler becomes dead code.
- **Fetchers return `None` on failure, never a remembered value** (`harness/rate_limits.py:_fetch_claude`): `get_all_rate_limits` dates whatever a fetcher returns to the moment it returned, and the 24h outage fallback in `_load_stale_cached` keys on that date. A fetcher that hands back its own cached reading when the network call fails launders an old figure into a fresh one every run, for as long as the failure lasts. Claude did exactly this through its `claude_usage_cache.json` and showed a three-week-old 7d figure as current. The state file now records `fetched_at`, `_load_claude_state` drops anything older than `_STALE_CACHE_TTL` or undated, and the only time the last reading is returned is inside a 429 cooldown, where the endpoint itself asked not to be called.
- **Interactive alias collision** (`harness/commands.py:_edit_interactive`): when the user types `save` with a colliding alias, the loop shows a yellow warning and continues instead of exiting. The collision check runs inside the save handler (not in `_apply_edits`) so the user stays in the editor and can fix it. `_apply_edits` retains its own check as a safety net for the flag-based path.
- **Table renderer** (`table.py`): declarative, pluggable component replacing hand-rolled `f"{...:<{w}}"` string interpolation. Three width-fit modes: `fixed` (ignore content), `content` (grow to longest cell), `bounded` (grow to longest cell up to `max_width`). Six built-in column kinds: `text` (plain string, strips ANSI on input to prevent mid-escape slicing across column gaps), `number` (right-aligned int, optional `thousands=True`), `count_threshold` (right-aligned int, green when above `threshold`), `list` (CSV-joined, color via `attrs["color"]`, uses `cpad` for color+pad consistency), `timestamp` (column-level `formatter` callable), and `preformatted` (cells ship their own ANSI; combined with `trust_cell_width=True` the column skips re-padding). Third-party kinds register via `@register_kind("name")`. Header is dim ANSI; separator is `─` repeated; both share the table's total visible width via `visible_len`. Contract tests live in `tests/test_table.py`.
- **cmd_check -> Table migration pattern**: `cmd_check` migrated after `cmd_list` validated that Table scales beyond the 9-column / mixed-kind case. cmd_check is intentionally simpler: 4 columns (STATUS | NAME | ALIASES | INFO), all `kind` values are `preformatted`+`trust_cell_width` or `text`/`list`. Status column is `kind="preformatted"` width=2, glyph is `c("green", "✓") + " "` (or red/yellow for the footer marker); info column is `kind="preformatted"` with explicit pre-pad `c("dim", text) + " " * max(0, INFO_COL_WIDTH - visible_len(text))` so variable-width version strings keep the grid intact. The off-PATH diagnostic block stays as plain print() BELOW the table because each orphan has a multi-line fix recipe that doesn't fit a single grid row. The heal side-effect (`live_version -> tools[name]["version"]`) MUST run BEFORE the table row is constructed so the displayed version reflects what was just probed and saved. When migrating similar cmd_* handlers, follow this pattern: pre-compute everything outside the loop, build the Table once with columns pinned, then `table.add_row(...)` per item, then `table.render()`.
