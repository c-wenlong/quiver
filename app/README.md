# Quiver for macOS

A native app on top of `swe`: usage, limits and sessions at a glance, and
accounts, skills, AGENTS.md and MCP servers without remembering commands.
See [ROADMAP.md](ROADMAP.md) for what is built and what is next.

## Build and run

Needs macOS 26 and Xcode 26 (Swift 6.2).

```bash
cd app
swift test                      # QuiverCore unit tests
./script/build-app.sh debug     # writes build/Quiver.app
open build/Quiver.app
```

The app runs `swe … --json`, so it needs a quiver with JSON output (M0).
If the `swe` your login shell finds is older, the app says so; point it at
a newer one in Settings.

## Layout

| Path | What |
|---|---|
| `Sources/QuiverCore` | Everything testable: the `swe` client, models, view models |
| `Sources/QuiverCore/Ledger` | Usage ledger: harness log scanners, the deduplicated event store, pricing |
| `Sources/QuiverCore/Status` | Service status: the status-page catalog, Statuspage and Google Cloud parsers, the polling cache |
| `Sources/QuiverCore/Resources` | Trimmed LiteLLM price snapshot and Quiver's pricing supplement |
| `Sources/QuiverApp` | The SwiftUI shell: window, sidebar, pages, menu bar panel |
| `Tests/QuiverCoreTests` | Swift Testing suites; `Fixtures/` holds real `swe --json` output |
| `script/build-app.sh` | Bundles and ad-hoc signs `build/Quiver.app` |
| `script/trim-litellm-pricing.py` | Regenerates `Resources/pricing.json` from LiteLLM's price list |
| `THIRD_PARTY_NOTICES.md` | Licences for the LiteLLM snapshot and ported OpenUsage rules |

Behaviour lives in QuiverCore behind seams the tests replace: `CommandRunning`
stands in for the `swe` process, `PricingTransport` for the network. The app
target stays thin.

## Usage ledger

`Ledger` (an actor) reads harness logs into a deduplicated SQLite store under a
cache directory you pass in (production: `~/.quiver/cache/ledger/`):

| Harness | Source | One event per | Dedup key |
|---|---|---|---|
| Claude | `<home>/projects/**/*.jsonl`, every home in `LedgerRoots.claudeHomes` | API response | `message.id` + `requestId`; sidechain copies also collide on `message.id` |
| Codex | `<home>/sessions/**` and `archived_sessions/` | response (`token_usage_record`) or turn (`token_count`) | `response_id`, else session + timestamp |
| OpenCode | `opencode.db`, read-only | assistant message | message id |
| Devin | `sessions.db`, read-only | request | `metadata.request_id` |

`scan(roots:)` reads only what changed: files resume from a per-file cursor
(inode, size, mtime, offset, head and tail hashes) and are re-read from the
start when replaced or truncated. Rows keep token counts, not dollars, and
outlive the logs they came from. `totals`, `daily`, `bySession`, `byAccount`
and `byHarness` price at query time with the current `PricingCatalog`; a model
the catalog does not know is counted in `unpricedTokens`, never as $0.

## Known rough edges

- The bundle is ad-hoc signed, so every rebuild looks like a new app to
  macOS. Permissions you granted (for example reading a `swe` installed
  under `~/Desktop`) are asked for again after each build.
