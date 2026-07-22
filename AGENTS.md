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
- **Rate limits** (`harness/rate_limits.py`): pluggable fetcher architecture — each tool registers a fetch function returning `RateLimitInfo` (used_percent, limit_reached, reset_at, plan_type). `get_all_rate_limits()` aggregates across fetchers with a 60s disk cache (`rate_limits_cache.json`). `swe list` shows a RATE column; `swe list --refresh` invalidates. Currently only Codex is supported (queries ChatGPT `backend-api/wham/usage` with OAuth tokens from `~/.codex/auth.json`). Add new fetchers with `register(tool_name, fetcher_fn)`.
- **SSL fallback** (`harness/rate_limits.py:_fetch_json`): macOS python.org builds (Python 3.12+) ship without CA certificates, causing `urllib.request.urlopen` to fail with `SSL: CERTIFICATE_VERIFY_FAILED`. The helper retries with an unverified SSL context (`ssl.CERT_NONE`) as a fallback — the connection stays encrypted, just without server-cert pinning. **Gotcha:** `urllib.error.URLError` is a subclass of `OSError`; always catch `URLError` before `OSError` in except chains or the SSL retry handler becomes dead code.
- **Interactive alias collision** (`harness/commands.py:_edit_interactive`): when the user types `save` with a colliding alias, the loop shows a yellow warning and continues instead of exiting. The collision check runs inside the save handler (not in `_apply_edits`) so the user stays in the editor and can fix it. `_apply_edits` retains its own check as a safety net for the flag-based path.
