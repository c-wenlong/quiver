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
| `Sources/QuiverApp` | The SwiftUI shell: window, sidebar, pages, menu bar panel |
| `Tests/QuiverCoreTests` | Swift Testing suites; `Fixtures/` holds real `swe --json` output |
| `script/build-app.sh` | Bundles and ad-hoc signs `build/Quiver.app` |

Behaviour lives in QuiverCore behind seams the tests replace: `CommandRunning`
stands in for the `swe` process. The app target stays thin.

## Known rough edges

- The bundle is ad-hoc signed, so every rebuild looks like a new app to
  macOS. Permissions you granted (for example reading a `swe` installed
  under `~/Desktop`) are asked for again after each build.
