# Quiver for macOS

A native app on top of `swe`: usage, limits and sessions at a glance, and
accounts, skills, AGENTS.md and MCP servers without remembering commands.
See [ROADMAP.md](ROADMAP.md) for what is built and what is next.

## Build and run

Needs macOS 26 and Xcode 26 (Swift 6.2).

```bash
cd app
swift test                      # QuiverCore unit tests
./script/build-app.sh debug     # writes build/Quiver.app, widgets included
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
| `Sources/QuiverCore/Limits` | Limit readings from every source, the 90% and reset alerts, the widget snapshot |
| `Sources/QuiverApp` | The SwiftUI shell: window, sidebar, pages, menu bar panel |
| `Sources/QuiverWidgets` | The WidgetKit extension: usage limits, usage summary, activity heatmap, top models |
| `Tests/QuiverCoreTests` | Swift Testing suites; `Fixtures/` holds real `swe --json` output |
| `script/build-app.sh` | Bundles `build/Quiver.app` with the widget extension and signs both (Apple Development identity if you have one, else ad-hoc) |
| `script/QuiverWidgets.entitlements` | The extension's sandbox: read access to `~/.quiver/cache/widgets/` only |
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

## Menu bar, notifications and widgets

The status item shows the tightest limit: a ring filled to the most used
current window across every account and every harness swe reports, plus
its percentage, red from 90%. Stale readings and windows whose reset time
has passed don't count. The panel lists every window of every account,
then the other harnesses, then the spend donut.

`LimitAlerter` notifies once when a window reaches 90%, and once when a
window that did so resets (a reading taken after its reset time, or a
reset time that moved on by more than ten minutes). A window that falls
below 80% without a reset rearms quietly. What it has notified is kept in
`~/.quiver/cache/app-limit-alerts.json`, so a relaunch repeats nothing.

### How the widgets are built

SwiftPM has no app extension product, but a WidgetKit extension is only an
executable whose `@main` is a `WidgetBundle`, inside an `.appex` with an
`NSExtension` Info.plist. So `QuiverWidgets` is a plain executable target
sharing QuiverCore, and `build-app.sh` wraps it in
`Contents/PlugIns/QuiverWidgets.appex`. No Xcode project or XcodeGen spec.
An extension's `Bundle.main` is the .appex, so the script also copies
QuiverCore's resource bundle into the appex's own `Contents/Resources`,
where `CoreResources` finds the harness logos the widgets draw. A packaged
app or appex never falls back to SwiftPM's `Bundle.module`, which would
stop the process on any Mac but the one that built it.

Data goes one way, through a file. The app writes
`~/.quiver/cache/widgets/snapshot.json` (usage figures, top models and
limit readings; never a credential, email or session) whenever what the
widgets show changes, then asks WidgetKit to reload. macOS only loads
sandboxed extensions, so the extension's entitlements grant read access to
that one folder through a home-relative temporary exception. That keeps
everything under `~/.quiver`, and it means the app needs no App Group, so
an ad-hoc signed build works without a paid team: pluginkit registers the
extension and macOS launches it in its own sandbox container. A Mac App Store build
would need an App Group instead, since the store rejects temporary
exceptions.

## Known rough edges

- Privacy grants (Desktop folder access, Accessibility) stick across
  rebuilds only when the app is signed with a stable identity.
  `build-app.sh` uses your first "Apple Development" certificate, or
  `QUIVER_SIGN_IDENTITY`. Without one it falls back to ad-hoc signing, and
  every rebuild looks like a new app that asks again.
- `QUIVER_BUILD_DIR` overrides where the bundle lands (default
  `app/build`); the app ends up at `$QUIVER_BUILD_DIR/Quiver.app`.
  Settings > Updates uses this so a rebuild never overwrites the running
  copy.
- `QUIVER_CHANNEL=dev|prod` (default `dev`) stamps `QuiverChannel` in the
  app's Info.plist. Dev builds also stamp `QuiverCommit`,
  `QuiverSourcePath` and `QuiverDirty` from the checkout, which is what
  Settings > Updates compares against HEAD.
