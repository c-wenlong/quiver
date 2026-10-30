# Contributing to quiver

Thanks for your interest in contributing! quiver is a small, stdlib-only CLI — we want to keep it fast, portable, and easy to hack on.

## Getting started

```bash
git clone https://github.com/c-wenlong/quiver.git
cd quiver
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
HOME="$(mktemp -d)" python3 -m unittest discover -s tests -p 'test_*.py'
```

Every command reads and writes `~/.quiver`, so run the suite — and any manual
`swe` check — against a throwaway `$HOME`. Otherwise a test run edits your real
registry and counts your real sessions. CI does this for every job.

Nix users can skip the venv entirely: `nix develop` opens a shell with Python,
`hatchling` and `coverage`, and `PYTHONPATH` already pointing at `src/`.
`nix build` builds the package and runs the suite inside the sandbox.

## Project layout

The CLI is organized by domain under `src/quiver/`:

| Path | Role |
| --- | --- |
| `cli.py` | Top-level dispatch — a `COMMANDS` dict and nothing else |
| `help_text.py` | `swe help` topics and command categories |
| `harness/` | The registry (`harness.json`), list/info/add/edit/check/doctor, discover, rate limits, drift checks |
| `sessions/` | Cross-agent history: three parser engines, per-tool adapters, aggregation, model analytics, the picker |
| `skills/` | SKILL.md discovery, catalogs, harness symlink layout |
| `mcp/` | The `~/.quiver/mcp.json` hub, per-tool format handlers, sync/diff/validate/doctor, `${NAME}` secret refs |
| `find/` | Read-only views of shared assets — `amd`, `skills`, `plugins`, `mcps` |
| `init/` | Writes the shared `AGENTS.md` and skills tree, symlinks them into each harness, migrates the old root |
| `plugins/` | `swe plugins`: every marketplace's plugins with per-harness state, and install/enable/disable through the harness CLIs |
| `hooks/` | `swe hooks`: the hook scripts under `~/.quiver/hooks/<harness>/` and their drift check |
| `reports/` | Daily and weekly session reports: transcripts, triage, batching, model runners, follow-up ledger |
| `providers/` | LLM provider metadata and API-key file lookup |
| `setup/` | The interactive onboarding wizard |
| `github/` | `swe gh` and the hub's GitHub routes; within the package only `gh.py` spawns `gh` |
| `remote/` | `swe remote`: the hub, the satellite agent, pairing and devices ([docs/remote-control.md](docs/remote-control.md)) |
| `webapp/` | The web app the hub serves, and its page data ([docs/web-app.md](docs/web-app.md)) |
| `ledger/`, `status/` | Python ports of the Mac app's usage ledger and service-status reader, for the web app |
| `history/` | Backward-compatible re-exports — write new code against `sessions/` |
| `config_commands.py` | `swe config` |
| `bug.py` | `swe bug`: opens an issue form with this machine's versions filled in |
| `completion.py`, `completion_scripts.py` | Completion candidates and the per-shell scripts `swe autocomplete` installs |
| `mcp_server.py` | Optional FastMCP session server (the `server` extra) |
| `mcp_formats.py` | Compatibility shim re-exporting `mcp/formats.py`; do not add to it |

The bottom layer imports nothing above itself: `paths.py` (every path under
`~/.quiver`), `console.py` (colour, width, truncation), `table.py` (the table
renderer), `configuration.py` (`config/config.json`), `keys.py` (the one
raw-mode key reader) and `flags.py` (the `--name=value` argv normaliser).
`json_output.py` (the `--json` envelope), `prompt.py`, `multiselect.py` and
`markdown.py` sit beside them for output, terminal input and rendering.

## What to contribute

- **New tool parsers** — session history or model usage for another AI coding CLI
- **MCP format handlers** — support for a tool's MCP config format in `mcp/formats.py`
- **Skills roots** — additional skill directory locations in `skill_roots()` or catalog discovery
- **Bug fixes and docs** — always welcome

## Guidelines

1. **Keep the core stdlib-only.** Optional deps belong in `[project.optional-dependencies]` (see the `server` extra).
2. **Never commit user state.** Do not add `tools.json`, `mcp.json`, or machine-specific paths to the repo.
3. **Add tests** for new MCP format handlers, skills layout/catalog logic, or non-trivial parser logic. Tests run against a throwaway `$HOME` — they must not depend on your real config.
4. **Match existing style** — simple functions, minimal abstractions; follow patterns in the domain packages above.
5. **Update help text** when adding user-facing commands — top-level `help_text.py`, and `skills/help_text.py` or `mcp/` help for subcommands.
6. **One concern per PR** — easier to review and merge.

## GitHub Actions are pinned

Every `uses:` in `.github/workflows/` points at a full 40-character commit SHA with the version as a trailing comment:

    uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4

A tag is a movable pointer, so a tag reference is not reproducible. Resolve a new SHA with `gh api repos/OWNER/REPO/git/ref/tags/TAG` (if the ref object type is `tag`, dereference it with `gh api repos/OWNER/REPO/git/tags/SHA`). Dependabot rewrites both the SHA and the comment on its weekly grouped pull request, so you rarely have to do this by hand.

## Docs site

The docs site at <https://c-wenlong.github.io/quiver/> is built with [Zensical](https://zensical.org) from the Markdown already in this repo. `scripts/docs_site.py` stages it into `build/docs-site/` (rewriting links so repo-relative references resolve inside the site), then `zensical` builds `site/`. The front page is the hand-written [app/landing-page/index.html](app/landing-page/index.html), copied to the site root as is; the README renders at `/overview/`. To preview locally:

```bash
python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r docs/requirements.txt
python3 scripts/docs_site.py && zensical serve
```

`zensical` is a docs-only dependency pinned in `docs/requirements.txt`; the core package stays stdlib-only, so it never goes in `pyproject.toml`.

## Code of Conduct

Taking part in this project means agreeing to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Pull request process

1. Fork the repo and create a feature branch from `main`.
2. Make your changes and ensure tests pass locally.
3. Update README, `swe help`, or `swe skills help` if you add user-facing commands or flags.
4. **Reinstall and verify e2e** — re-run `pip install -e .` so the installed `swe` binary picks up new files, then run the actual `swe <command>` to confirm the feature works end-to-end (not just `PYTHONPATH=src` unit tests).
5. Open a PR with a clear description of what changed and why.

> **Why reinstall?** The `swe` command is a pip entry point. With an editable install (`pip install -e .`), new files are picked up automatically. But a stale non-editable install (e.g. from `pip install .` or pipx) won't see new modules until you reinstall. Unit tests with `PYTHONPATH=src` can pass while `swe` silently fails because it doesn't see the new module.

## Reporting bugs

Use the [bug report template](https://github.com/c-wenlong/quiver/issues/new?template=bug_report.yml) and include:

- Your OS and Python version, plus the quiver version from `swe version`
- The exact command that failed and its full output
- Steps to reproduce

## Questions?

[SUPPORT.md](SUPPORT.md) has the full ladder: read the docs, run `swe doctor`,
then open an [issue](https://github.com/c-wenlong/quiver/issues). Discussions
are not enabled on this repo, so issues are the only channel — and we're happy
to help you find a good first task there.
