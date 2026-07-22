# AGENTS.md

## When adding or changing CLI commands

The `swe autocomplete` feature relies on a hardcoded list of primary subcommands in `src/quiver/completion.py` (`_PRIMARY_COMMANDS`) and context-aware completion rules (`_TOOL_TARGET_COMMANDS`, `_COMMAND_FLAGS`).

**When you add a new command to `COMMANDS` in `cli.py`:**

1. Add the command to `_PRIMARY_COMMANDS` in `completion.py` with a short description.
2. If the command takes a tool name/alias as its first argument, add it to `_TOOL_TARGET_COMMANDS`.
3. If the command accepts flags, add them to `_COMMAND_FLAGS`.
4. If the command should appear in `swe help`, add it to `COMMAND_CATEGORIES` in `help_text.py` and add a `HELP` entry.
5. Run `tests/test_completion.py` to verify completions still work.

**When you remove or rename a command:**

1. Remove it from `_PRIMARY_COMMANDS` in `completion.py`.
2. Remove it from `_TOOL_TARGET_COMMANDS` or `_COMMAND_FLAGS` if present.
3. Remove it from `COMMAND_CATEGORIES` and `HELP` in `help_text.py`.

## Test conventions

- Session parser tests mock `os.path.expanduser` in `quiver.sessions.parsers` to redirect `~/.tool/` paths to temp dirs.
- Engine tests use `expand_path()` which only expands `~` prefixes (mock-safe).
- Completion tests mock `load_registry` for tool/tag completions and `SHELL_CONFIGS` for script generation.
- Run all tests: `python -m unittest discover -s tests -p 'test_*.py'`

## End-to-end verification & reinstall

**Every e2e feature must be verified against the installed `swe` binary, not just unit tests.**

The `swe` command is a pip-installed console entry point (declared in `[project.scripts]` in `pyproject.toml`). New Python files added to `src/quiver/` are **not** available to the installed `swe` until you reinstall. Unit tests with `PYTHONPATH=src` can pass while the installed binary silently fails because it doesn't see the new module.

### Required steps for any feature that adds files or changes `cmd_*` handlers:

1. **Write tests** — `python -m unittest discover -s tests -p 'test_*.py'`
2. **Reinstall the package** — `pip install -e .` (or `pipx install --force git+...` for pipx installs). With an editable install new files are picked up automatically, but a stale non-editable install won't see them until you reinstall.
3. **Verify e2e** — run the actual `swe <command>` and confirm the feature works against the installed binary, not just `PYTHONPATH=src python -m quiver.cli <command>`
4. **Open a PR** — one concern per PR, with a clear description of what changed and why

### Common pitfall

If a feature works with `PYTHONPATH=src python -m quiver.cli` but not with the installed `swe` command, the installed copy is stale (likely a non-editable install). Re-run `pip install -e .` to switch to editable mode and sync with the source tree.

## Architecture notes

- **Session engines** (`sessions/engines/`): three family engines (SQLite, JSONL, JSON) with declarative `*ParserConfig` dataclasses. Adapters in `parsers.py` are thin configs over these engines.
- **Tool identity** (`sessions/identity.py`): `LAUNCH_TOOL` maps session `tool_name` to the CLI binary for resume; `COUNT_TO_REGISTRY` maps to the registry key for `swe list` counts. Antigravity sessions launch via `gemini` and count under gemini.
- **Session cache** (`aggregator.py`): `get_all_sessions(use_cache=True)` reads from `~/.config/swe/session_cache.json` with a 60s TTL. `swe list` uses cache; `swe session` bypasses it. `swe list --refresh` invalidates.
- **Interactive prompts** (`prompt.py`): `read_line()` restores TTY cooked mode and handles CR/LF/CRLF. Used by `swe edit` and `swe setup` instead of `input()`.
- **Rate limits** (`harness/rate_limits.py`): pluggable fetcher architecture — each tool registers a fetch function returning `RateLimitInfo` (used_percent, limit_reached, reset_at, plan_type). `get_all_rate_limits()` aggregates across fetchers with a 60s disk cache (`rate_limits_cache.json`). `swe list` shows a RATE column; `swe list --refresh` invalidates. Currently supports **Codex** (queries ChatGPT `backend-api/wham/usage` with OAuth tokens from `~/.codex/auth.json`) and **GitHub Copilot** (queries `api.github.com/copilot_internal/user` with OAuth tokens from `gh auth token`). Add new fetchers with `register(tool_name, fetcher_fn)`.
- **Copilot header spoofing** (`harness/rate_limits.py:_fetch_github_copilot`): the undocumented `api.github.com/copilot_internal/user` endpoint gates access on `Editor-Version` and `Editor-Plugin-Version` matching the official VS Code Copilot Chat client. Without those exact headers the endpoint returns 403. The `User-Agent` is set to `quiver/<version>` for traceability, but otherwise the request is wired to look like the official client. If GitHub rotates these values the fetcher will break silently — be alert when touching this code.
- **Copilot plan decoration** (`harness/rate_limits.py:_decorate_copilot_plan_type`): appends `/edu` to `copilot_plan` when the SKU signals an educational quota. Only applies when `copilot_plan == "individual"` **and** `access_type_sku` contains `"educational"` (case-insensitive). Paid individual plans, business plans, and enterprise plans are left untouched — only free educational individual accounts get the suffix, so users can tell them apart from paid Copilot Pro at a glance in `swe list`.
- **ISO 8601 parser** (`harness/rate_limits.py:_parse_iso8601_to_epoch`): shared `str → epoch float` helper used by Copilot's fetcher and Codex's string-`reset_at` fallback path. Compatible with Python 3.10+ (which doesn't accept fractional seconds combined with a timezone offset in `datetime.fromisoformat` — that was added in 3.11). Naïve datetimes (no offset) are explicitly treated as UTC to avoid `datetime.timestamp()` silently applying local time and producing TZ-dependent reset countdowns. Returns `0.0` for any unparseable / falsy input so a bad timestamp never breaks `swe list`.
- **Bool guard for `reset_at` type dispatch** (`harness/rate_limits.py:_fetch_codex`): the numeric branch is `isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool)`. Without the `not bool` part, a pathological `reset_at: true` payload would silently become `1.0` because `bool` subclasses `int` in Python. Same conceptual guard pattern is applied in `_derive_copilot_fields` with explicit `try/except (TypeError, ValueError)` around float/int casts, returning `(0, False)` rather than crashing `swe list` mid-render.
- **Test fixture isolation**: class-level JSON fixtures like `_SAMPLE_RESPONSE` are shallow-copied by default. Tests that **mutate nested keys** (e.g. `body["rate_limit"]["primary_window"]["reset_at"]`) MUST use `copy.deepcopy` to avoid leaking the mutation into later tests that read the same class fixture. Tests that only **replace top-level keys** (e.g. `body["quota_snapshots"] = {...}`) are safe with shallow copies. Watch for this whenever adding a parameterized test that walks a class-level fixture.
- **SSL fallback** (`harness/rate_limits.py:_fetch_json`): macOS python.org builds (Python 3.12+) ship without CA certificates, causing `urllib.request.urlopen` to fail with `SSL: CERTIFICATE_VERIFY_FAILED`. The helper retries with an unverified SSL context (`ssl.CERT_NONE`) as a fallback — the connection stays encrypted, just without server-cert pinning. **Gotcha:** `urllib.error.URLError` is a subclass of `OSError`; always catch `URLError` before `OSError` in except chains or the SSL retry handler becomes dead code.- **Interactive alias collision** (`harness/commands.py:_edit_interactive`):
  when the user types `save` with a colliding alias, the loop shows
  a yellow warning and continues instead of exiting. The collision check
  runs inside the save handler (not in `_apply_edits`) so the user
  stays in the editor and can fix it. `_apply_edits` retains its own
  check as a safety net for the flag-based path.
- **Table renderer** (`table.py`): declarative, pluggable component
  replacing hand-rolled `f"{...:<{w}}"` string interpolation. Three
  width-fit modes — `fixed` (ignore content), `content` (grow to longest
  cell), `bounded` (grow to longest cell up to `max_width`) — and six
  built-in column kinds: `text` (plain string, strips ANSI on input to
  prevent mid-escape slicing across column gaps), `number` (right-aligned
  int, optional `thousands=True`), `count_threshold` (right-aligned int,
  green when above `threshold`), `list` (CSV-joined, color via
  `attrs["color"]`, uses `cpad` for color+pad consistency),
  `timestamp` (column-level `formatter` callable strips the lambda-from-row
  ceremony), and `preformatted` (cells ship their own ANSI; combined with
  `trust_cell_width=True` the column skips re-padding). Third-party
  kinds register via `@register_kind("name")` decorator. Header is dim
  ANSI; separator is `─` repeated; both share the table's total visible
  width via `visible_len`. Contract tests live in `tests/test_table.py`.
- **cmd_list migration to Table** (`harness/commands.py::cmd_list`,
  `tests/test_harness_commands.py`): the 9-column schema is
  `mark` (preformatted w=2), `name` (text w=16), `command` (text w=18),
  `version` (text w=12), `aliases` (list w=12, color=cyan),
  `sess` (preformatted w=8), `rate` (preformatted w=14,
  `trust_cell_width=True`), `inst` (preformatted w=4),
  `desc` (text w=36, value prefaced with one space cell-internally
  to maintain a 3-char visible gap before DESCRIPTION given the
  default `column_gap=2`). Starred rows pass
  `accent="neon"` to `add_row(..., accent=...)`; the mark cell is
  `c("neon_pink", " ★")` for the `★`-prefix variant or `"  "` for the
  unstarred variant — **the mark cell's `visible_len` MUST equal 2**
  (the column width) or the rendered offset of the tool NAME shifts
  in the post-`★` columns. Defense-in-depth: `Table._compute_widths`
  bounded-fit samples `visible_len` of every row's cell, so a future
  contributor who passes a 3-visible-char mark_cell will silently see
  the whole `mark` column auto-grow to 3 — alignment looks fine to the
  unit-test eye (allocator reduced to a single visible column) but
  every neighbouring column shifts right by 1 in actual rendering.
  Migration closes the pre-existing column-justification drift between
  favourited and unfavourited rows: in the OLD f-string, starred rows
  rendered with the `║` border eating one column (`║` + `★` + ` ` +
  `name` → `name` at column 3), while unstarred rows rendered with
  the 2-space indent + `mark` (a single space) + ` ` separator →
  `name` at column 4. Both styles are now replaced by a 2-char-wide
  prefixed mark cell + `column_gap=2` series, so  both styles land
  `name` at column 4 uniformly. **cmd_list is currently the ONLY
  `cmd_*` handler that uses Table; `cmd_info`, `cmd_check`,
  `cmd_doctor`, `cmd_tags`, `cmd_aliases` still hand-roll
  `f"{...:<{w}}"` strings. Migrate intentionally, one handler at a
  time rather than as a sweep — each handler has its own column
  shape semantics and a bulk conversion would risk subtle drift
  in non-trivial rows.** Pattern for migrating another `cmd_*` handler: build
  the `Table` schema in one place, replace the render loop's
  favourited/unfavourited branch with a single `add_row(..., accent=..)`
  call, and rely on `accent` + `trust_cell_width` + per-kind renders
  to preserve colour differences the old code had to special-case.
