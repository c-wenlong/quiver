# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`quiver` is the Python package behind the `swe` CLI: one front door over the ~28 AI
coding CLIs ("harnesses") a developer might have installed. It never wraps or replaces
them. It shells out to the real binaries (`os.execvp`) and reads their session logs
read-only.

Two companion docs carry detail this file deliberately does not repeat:

- [AGENTS.md](AGENTS.md): per-subsystem gotchas and the checklist for adding a command.
  Read it before touching `cli.py`, `rate_limits.py`, `table.py`, or session parsers.
- [ARCHITECTURE.md](ARCHITECTURE.md): the design rationale behind every decision below.

## Commands

```bash
pip install -e .                                            # editable install; provides `swe`
pip install -e ".[test]"                                    # adds coverage tooling
pip install -e ".[server]"                                  # adds the MCP history server extra

export HOME="$(mktemp -d)"                                  # ALWAYS, before running tests
python -m unittest discover -s tests -p 'test_*.py'         # full suite (~1300 tests, ~6s)
python -m unittest tests.test_table                         # one file
python -m unittest tests.test_table.TableFitModesTest       # one class
python -m unittest tests.test_table.TableFitModesTest.test_fit_fixed_ignores_content_width   # one test

python -m coverage run -m unittest discover -s tests -p 'test_*.py'
python -m coverage combine && python -m coverage report     # fail_under = 69 (.coveragerc)
```

Without an install, prefix with `PYTHONPATH=src`. There is no linter or formatter in
this repo and no pre-commit config; CI runs the unittest suite on Python 3.10 through
3.13, a wheel smoke test, and a macOS path test.

### Testing rules that bite

- **Throwaway `$HOME` is mandatory.** Tests read and write real config paths under
  `~/.quiver`. CI sets `HOME="$(mktemp -d)"` for every step. Forget it and you edit your
  own machine's registry.
- **`tests/conftest.py` is pytest-only.** It pins `COLUMNS=200` so table-rendering
  assertions do not depend on terminal width. The project runs under `unittest`, which
  ignores conftest entirely, so any new width-sensitive test must patch
  `quiver.console.terminal_width` itself.
- **Some tests need binaries this container lacks.** The Copilot rate-limit tests mock
  `subprocess.run` but not `shutil.which("gh")`, so they fail wherever the `gh` CLI is
  absent. macOS keychain paths gate on `shutil.which("security")` the same way.
- **The suite is not green on `main`.** Establish the baseline before assuming your
  change caused a failure.
- Class-level JSON fixtures are shallow-copied. A test that mutates *nested* keys must
  `copy.deepcopy` or it leaks into later tests reading the same fixture.

### Verify against the installed binary, not just `PYTHONPATH=src`

`swe` is a pip console entry point. A stale non-editable install will not see new
modules under `src/quiver/`, so unit tests pass while the real binary fails on import.
Any change that adds a file or touches a `cmd_*` handler ends with `pip install -e .`
and running the actual `swe <command>`. CI's e2e job does exactly this against a fresh
`$HOME`.

## Architecture

### Control plane vs data plane

This repo is code, public, in git. `~/.quiver` is machine state, private, in its own git
repo (the companion `quiver-hub` repo). Cloning and building this repo gets you a `swe`
that knows about no harnesses at all, which is correct.

The boundary is a rule, not just a directory split: **code here never hardcodes what
`harness.json` can say.** Whether a harness is starred, supports plugins, or where its
skills live is a question for the registry, not a constant in source. `.gitignore`
enforces this mechanically for `harness.json`, `mcp.json`, `providers.json`, and caches.

### Layering

```
                  cli.py  (dispatch only, ~170 lines)
                     |
   init · harness · sessions · skills · mcp · reports · providers · find · setup
                     |
        console · table · paths · configuration   (import nothing else in-project)
```

1. `console.py`, `table.py`, `paths.py`, `configuration.py` are leaves. Keep them that way.
2. Command modules own printing. Logic modules mostly do not print. `setup/wizard.py` is
   a deliberate exception; `harness/rate_limits.py` printing diagnostics is a known leak.
3. `cli.py` dispatches and nothing else. `COMMANDS` maps name to handler.

One import cycle exists, `harness <-> sessions`. Both directions are function-local
imports, so it does not break module loading. Do not promote either to module level.

### Registry: one file, one module

`~/.quiver/config/harness.json`, keyed by harness name, is everything the CLI knows about
a harness. `harness/registry.py` is the **only** module that touches it. Each row carries
`state` (`active` | `starred` | `archived`), plus `pin`, `archived`, and optional
`capabilities`.

`harness/stars.py` and `harness/archive.py` are compatibility shims: old signatures, old
return shapes, every read and write routed through `registry.py`. Same story for
`mcp_formats.py` (use `quiver.mcp.formats`), `mcp_server.py` (use `quiver.mcp.server`),
and `history/` (use `quiver.sessions`). Do not add logic to a shim.

### Capabilities-first

`skills/layout.py`'s `HARNESS_ROOTS` and `find/plugins.py`'s `PLUGIN_FALLBACK` are
**fallbacks, not sources of truth**. A harness's own `capabilities.skills.{supported,root}`
wins whenever the registry has one; the table only fires for a harness the registry has
never heard of. A registry entry the table lacks is the design working, not drift.

### Sessions

Every harness stores history differently, so parsing splits into three engines under
`sessions/engines/`: `jsonl_engine` (claude, codex, most), `json_engine` (cursor and
forks), `sqlite_engine` (editor-embedded tools). Adapters in `parsers.py` are thin
declarative `*ParserConfig` dataclasses over those engines. `aggregator.py` fans out and
merges; `failures.py` records parser crashes so a broken parser reports an error instead
of reading as "no history".

`sessions/identity.py` holds two maps worth knowing: `LAUNCH_TOOL` (session `tool_name`
to the binary used for resume) and `COUNT_TO_REGISTRY` (to the registry key for `swe list`
counts).

### Caching

| cache | TTL | invalidated by |
| --- | --- | --- |
| `cache/session_cache.json` | 60s | `swe list --refresh` |
| `cache/rate_limits_cache.json` | 300s | `swe list --refresh` |
| `cache/session_counts.json` | 24h | `swe list --refresh` |

`swe list` runs in ~70ms because usage is opt-in and only fetched for starred harnesses.
It was 870ms when fetched unconditionally. Keep new network work behind an explicit flag.

### Rate limits are pluggable and fragile

`harness/rate_limits.py` registers one fetcher per tool via `register(tool_name, fn)`,
each returning a `RateLimitInfo`. Two live fetchers hit **undocumented** endpoints:
Codex (ChatGPT `backend-api/wham/usage`) and Copilot (`api.github.com/copilot_internal/user`,
which 403s unless `Editor-Version` and `Editor-Plugin-Version` mimic the official VS Code
client). If a provider rotates these, the fetcher breaks silently. Read AGENTS.md before
editing this file: it documents the bool-vs-int guard on `reset_at`, the Python 3.10
ISO 8601 workaround, and the `URLError`-before-`OSError` ordering that keeps the SSL
fallback from becoming dead code.

### Table rendering

`table.py` is the declarative replacement for hand-rolled `f"{...:<{w}}"` padding. Three
fit modes (`fixed`, `content`, `bounded`) and six built-in column kinds; third-party
kinds register through `@register_kind("name")`. Migration of existing `cmd_*` handlers is
opt-in and deliberately incomplete. When migrating one, follow the `cmd_check` pattern in
AGENTS.md: pre-compute outside the loop, build the `Table` with columns pinned, add rows,
render once.

### Drift checks

`swe doctor` runs `harness/drift.py`, which catches places where two parts of quiver that
should agree have separated: help topics vs `cli.py`'s `COMMANDS`, registry row schema,
fallback tables vs registry capabilities, dangling symlinks. Every check is a pure
`check_*` function (data in, findings out, no I/O) plus a `run_drift_checks()` that wires
real data in. A warning must be actionable, so an asymmetry the design expects is not a
finding.

## Constraints

- **Stdlib only.** The single runtime dependency is `tomli`, and only below Python 3.11.
  Anything else goes in `[project.optional-dependencies]`.
- **Python 3.10 is the floor.** `tests/test_syntax_floor.py` hands every source file to
  an older interpreter, because PEP 701 nested f-strings parse fine on 3.12 and crash the
  CLI on import for 3.10 users. That shipped once already, in v0.2.8.
- **Never commit machine state.** No `harness.json`, `mcp.json`, `providers.json`, or
  absolute paths from your box.
- **Never write to a harness's session logs.** Sessions, models, and reports are read-only
  consumers of those files.

## Adding a command

Four files move together, and `swe doctor` fails if they drift:

1. `cli.py`: add to `COMMANDS`.
2. `completion.py`: add to `_PRIMARY_COMMANDS`, plus `_TOOL_TARGET_COMMANDS` if the first
   argument is a harness name and `_COMMAND_FLAGS` if it takes flags.
3. `help_text.py`: add a `HELP` entry and place it in `COMMAND_CATEGORIES`.
4. `tests/test_completion.py`: run it.

Top level is reserved for shortcuts to the harness verbs run most often. Everything else
lives under the domain that owns it (`swe harness`, `swe mcp`, `swe find`, `swe skills`,
`swe report`, `swe providers`, `swe config`).
