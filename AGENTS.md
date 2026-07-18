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

## Architecture notes

- **Session engines** (`sessions/engines/`): three family engines (SQLite, JSONL, JSON) with declarative `*ParserConfig` dataclasses. Adapters in `parsers.py` are thin configs over these engines.
- **Tool identity** (`sessions/identity.py`): `LAUNCH_TOOL` maps session `tool_name` to the CLI binary for resume; `COUNT_TO_REGISTRY` maps to the registry key for `swe list` counts. Antigravity sessions launch via `gemini` and count under gemini.
- **Session cache** (`aggregator.py`): `get_all_sessions(use_cache=True)` reads from `~/.config/swe/session_cache.json` with a 60s TTL. `swe list` uses cache; `swe session` bypasses it. `swe list --refresh` invalidates.
- **Interactive prompts** (`prompt.py`): `read_line()` restores TTY cooked mode and handles CR/LF/CRLF. Used by `swe edit` and `swe setup` instead of `input()`.
