# AGENTS.md

Instructions for any coding agent working in this repository. This file holds the rules and judgment calls; the reference material lives in the docs below. Read the doc for an area before changing it.

## What this is

`quiver` is a Python package whose console entry point is `swe`. It manages the AI coding CLIs on a machine (Claude Code, Codex, Gemini, Cursor, opencode, ...): one registry, one shared instruction file and skills tree symlinked into every harness, cross-agent session history, MCP config sync, rate limits, and coding-session reports.

The core CLI is stdlib-only with no runtime dependencies. Optional extras: `server` (FastMCP session server via `python -m quiver.mcp_server`) and `test` (coverage). Python floor is 3.11; never add a 3.10 compatibility shim (3.10 users get `v0.2.9`, see [docs/testing.md](docs/testing.md#interpreter-matrix)).

| Doc | Read it before you touch |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | layering, the `~/.quiver` layout, registry and link states, `swe init` and archived harnesses, session engines, caches, drift checks |
| [docs/backend-architecture.md](docs/backend-architecture.md) | what each package owns, adding a command / `--json` payload / parser / fetcher / MCP format, the Python [gotchas](docs/backend-architecture.md#gotchas) |
| [docs/app-architecture.md](docs/app-architecture.md) | the macOS app's structure and conventions, the app [gotchas](docs/app-architecture.md#gotchas) |
| [docs/testing.md](docs/testing.md) | every test suite, coverage, fixtures, the end-to-end checks CI runs |
| [docs/data-model.md](docs/data-model.md), [docs/json-contracts.md](docs/json-contracts.md) | any store, cache, schema or `--json` shape |
| [docs/remote-control.md](docs/remote-control.md), [docs/web-app.md](docs/web-app.md) | `swe remote`, the hub, the web app |
| [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md) | the PR process and the changelog |

## The clients

| Client | Where | Builds with |
|---|---|---|
| macOS app | `app/mac/` | SwiftPM; see [app/mac/README.md](app/mac/README.md) |
| iPhone app | `app/ios/` | XcodeGen and Xcode; see [app/ios/README.md](app/ios/README.md) |
| Web app | `src/quiver/webapp/` | nothing: it ships in the Python package and `swe remote serve` serves it; see [docs/web-app.md](docs/web-app.md) |

The web app stays inside the Python package because pipx installs and the `swe` bundled in Quiver.app serve it from there.

### Keeping the clients in step

[FEATURES.md](FEATURES.md) is the cross-client matrix: one row per feature, one column per client, plus the table of logic ported between Swift and Python.

- A change that adds, ships or drops a user-facing feature updates its row in the same change. Differences between clients go in the row's Notes.
- Before calling a fix done, check whether the same bug exists on the other clients. Anything in the ported-logic table has the same bug twice by default: fix both in one change, or write what is still owed in the row's Notes.
- Per-client build order stays in `app/mac/ROADMAP.md`, `app/ios/ROADMAP.md` and `docs/web-app.md`. FEATURES.md records where each feature stands, not when it was built.

### The macOS app

`app/mac/` replaces Switchboard, TokenTracker, AIUsage and OpenUsage ([app/mac/ROADMAP.md](app/mac/ROADMAP.md)).

- **Share no data with those apps.** Never read or write `~/.switchboard`, `~/.tokentracker`, `~/.config/aiusage`, OpenUsage's Application Support folder, or their caches, settings or keychain items, not even to import. Everything Quiver keeps lives under `~/.quiver`. The harnesses' own files (`~/.claude`, `~/.codex`, ...) are not another app's data; reading them is the point.
- **Borrow ideas and code, not state.** Their source is a reference; Quiver re-implements against its own files. Keep the licence notices for any code copied.
- **Never write to `~/Applications/Quiver.app` by hand.** Several sessions work on this repo at once, so the installed app is built only from `origin/main` by `app/mac/script/install-app.sh`. Check your change with your own build: `./script/build-app.sh debug` in your worktree, then `open -n build/Quiver.app`.
- **Reinstall the app after every push to `main`**, not only `app/mac/` changes. Once your PR merges, run `app/mac/script/install-app.sh` from any checkout, `git fetch`, then confirm `git merge-base --is-ancestor <your merge commit> "$(defaults read ~/Applications/Quiver.app/Contents/Info QuiverCommit)"`. A newer commit is fine. If the script reports another install running, wait and run it again, since that one may have built an older `main`. Nothing to install after a push to a PR branch.

## Commands

```bash
# Setup (editable install; the venv here is .venv, Python 3.12)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

# Full suite, always against a throwaway HOME. In a git worktree add
# PYTHONPATH=src, or the editable install imports the main checkout's code.
HOME="$(mktemp -d)" python3 -m unittest discover -s tests -p 'test_*.py'

# One file / one test
python3 -m unittest tests.test_table
python3 -m unittest tests.test_table -k render
python3 -m unittest tests.test_rate_limits.TestClass.test_method

# Coverage as CI measures it (CI shards with scripts/run_test_shard.py)
test_home="$(mktemp -d)"
HOME="$test_home" python3 -m coverage run -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage combine && python3 -m coverage report

# CLI from source; MCP sync subsystem directly
PYTHONPATH=src python3 -m quiver.cli list
python3 -m quiver.mcp list

# Docs pages under app/landing-page/: regenerate after doc or help changes
python3 scripts/docs_site.py  # or: --check to verify freshness
```

There is no linter or formatter, and no pytest: `tests/conftest.py` pins terminal width to 200 columns but only fires under pytest, so a unittest test that asserts on rendered width patches `quiver.console.terminal_width` itself.

### Rules a test or CI already enforces

Break one and a check fails; the enforcer's message says what to fix.

| Rule | Enforced by |
|---|---|
| The full suite runs against a throwaway `HOME` (integration tests spawn `swe` and write into whatever `HOME` they get) | `tests/test_agent_rules.py`; CI exports a `mktemp -d` HOME for every job |
| Source compiles on Python 3.11: no PEP 701 nested f-strings (shipped as a real bug in 0.2.8) | `tests/test_syntax_floor.py` |
| Every command in `cli.py`'s `COMMANDS` has a `help_text.py` entry and completion tables, and no dead help topic | `tests/test_drift.py`, `tests/test_completion.py`, `swe doctor` |
| Coverage stays at or above 84% | `.coveragerc`, CI's coverage job |
| User state (`harness.json`, `mcp.json`, `tools.json`, `providers.json`, `stars.json`, caches) is never committed | `.gitignore` |
| This file stays under 25KB and its relative links resolve | `tests/test_agent_rules.py` |

Manual `swe` runs that check a change also use `HOME="$(mktemp -d)"`, so they never touch the real registry or count real sessions. No test can see those runs; that one is on you.

## End-to-end verification

Verify every e2e feature against the installed `swe` binary, not only unit tests. A new module under `src/quiver/` passes with `PYTHONPATH=src` but stays invisible to a stale non-editable install. For any change that adds files or changes `cmd_*` handlers:

1. Write tests and run the full suite against a throwaway HOME.
2. Reinstall with `pip install -e .` (or `pipx install --force git+...`).
3. Run the real `swe <command>` against a temp HOME. Read `.github/workflows/ci.yml` and `release.yml` for what CI repeats; [docs/testing.md](docs/testing.md#end-to-end-checks-against-the-installed-swe) has the detail.
4. Open a PR, one concern per PR.

If it works with `PYTHONPATH=src python3 -m quiver.cli` but not with `swe`, the installed copy is stale: re-run `pip install -e .`.

## Architecture rules

[docs/backend-architecture.md](docs/backend-architecture.md) has the package map and per-extension checklists; [ARCHITECTURE.md](ARCHITECTURE.md#layering-rules) the reasons.

- `cli.py` is dispatch only: `COMMANDS` maps the first argv token to a `cmd_*`. Top-level entries are shortcuts for the most-used harness verbs; everything else lives under a domain command (`harness`, `mcp`, `find`, `plugins`, `providers`, `report`, `skills`, `session`, `config`, `setup`, `init`, `hooks`, `gh`, `remote`) that owns its own sub-dispatch.
- Six bottom-layer modules never import a domain package: `console.py`, `keys.py` and `flags.py` import nothing from the project, `paths.py` only `quiver/__init__.py` constants, `table.py` imports `console`, `configuration.py` imports `paths`. Use `paths.py`'s `*_for(home)` helpers in tests.
- Command modules own presentation; logic modules do not print (`setup/wizard.py` is the one exception). The one cycle, `harness <-> sessions`, stays function-local.
- `harness/registry.py` is the only module that touches `harness.json`. `harness/stars.py` and `harness/archive.py` are compatibility shims: add no state to them.
- **Code never hardcodes what `harness.json` can say.** `HARNESS_ROOTS` and `PLUGIN_FALLBACK` are read-only fallbacks for harnesses the registry has never heard of; a harness's `capabilities` entry always wins.
- Within `github/`, only `gh.py` spawns `gh`.
- `ledger/`, `status/` and the search index writer are ports of `QuiverCore` Swift code. Keep their rules in step with the Swift side, per the ported-logic table in FEATURES.md.

## Conventions

- **Flags:** a long flag that takes a value is always `--name=value` (comma-separate lists: `--tags=a,b`), parsed with `expand_value_flags` at the top of the handler. Short flags keep the space form. Check boolean membership (`"-i" in args`) on raw argv before expansion, and never expand free-text trailing args.
- **New or renamed command:** follow the checklist in [docs/backend-architecture.md](docs/backend-architecture.md#adding-removing-or-renaming-a-command) (`completion.py` tables, `help_text.py`).
- **Terminal input:** raw-mode widgets read keys only through `keys.py::read_key`, never a hand-rolled reader; line input goes through `prompt.read_line()`, not `input()`. Shaded rows pad with `console.fill_ansi`, never `lpad`.
- **Tables** use `table.py` kinds, not hand-rolled f-string padding.
- **Rate-limit fetchers** return `None` on failure, never a remembered value, and catch `urllib.error.URLError` before `OSError`.
- **No `glob` inside a per-session loop**; index the root once.

## Test conventions

[docs/testing.md](docs/testing.md) is the full guide (Swift suite, coverage, fixture capture, gates and clocks, no busy-waits). The ones that bite most:

- A test's temp folder comes from `temp_dir(self)` in `tests/_tempdir.py` (or `self.enterContext(tempfile.TemporaryDirectory())`), never a bare `tempfile.mkdtemp()`.
- A test that mutates nested keys of a class-level JSON fixture must `copy.deepcopy` it first.
- Session parser tests mock `os.path.expanduser` in `quiver.sessions.parsers`; completion tests mock `load_registry` and `SHELL_CONFIGS`.

## Gotchas

Each entry records a trap that already shipped as a bug or cost a long debug. Before changing a file below, read its entry in [docs/backend-architecture.md](docs/backend-architecture.md#gotchas) (Python) or [docs/app-architecture.md](docs/app-architecture.md#gotchas) (macOS app). The one-line versions:

- **Session titles** (`sessions/parsers.py`): only harnesses with an on-disk marker emit `rename`. Never invent heuristics for opencode, Forge, Continue or Cursor. A false "rename" is worse than a missed one.
- **Codex threads**: names live in sidecars (`state_<n>.sqlite`, `session_index.jsonl`), not the rollout. An empty `preview` is the only reason to drop a thread.
- **Claude handoffs**: a `continued-in` marker supersedes a session only when nothing follows it and the successor is on disk.
- **Parse and status caches** (`sessions/parse_cache.py`, `sessions/status.py`): only values derived from the file's own bytes may be cached. A new module feeding a cached value joins `_FINGERPRINT_MODULES`.
- **Search index** (`sessions/search_writer.py`): two writers, swe and the app. Keep the DDL, schema version and stat arithmetic in step with Swift; never delete an index at another schema version.
- **OpenCode 2.x, Devin, Cursor, Copilot** stores and APIs: read the entry first. Each has a non-obvious shape (forests, sidecar ids, spoofed headers, cookie-only auth) that looks like a bug and is not.
- **Session listing widths**: settled from the rows before cells are built; do not go back to hardcoded widths.
- **TLS** (`harness/rate_limits.py`): never disable verification; a missing CA bundle prints one warning and sends nothing.
- **Web ledger reads** (`ledger/store.py`): commit after any write on the read connection, roll back on `sqlite3.Error`.
- **The app ignores `$HOME`**: point app data at scratch with the per-store env overrides, or test through a unit test.
- **Keychain**: never probe a keychain read by hand on a desktop someone is using; the dialog steals focus.
- **App UI**: fonts through `Theme`, hover text through `.hoverTip` (not `.help`), brand names and colours only in `ModelBrands.swift` / `HarnessColor.fixed`, every account card the same history section.
- **App performance**: no filesystem calls on the main actor for Plugins and Skills (`LatestBuild`), slow ledger reads off the `Ledger` actor, and the window shell never measures a page (`PanelFrame`).
- **Scratch app builds** register with LaunchServices: unregister them with `lsregister -u` after testing, or widgets break.

Add a new gotcha to the matching doc, and a one-liner here only if an agent would otherwise walk into it.
