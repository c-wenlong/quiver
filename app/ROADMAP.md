# quiver app roadmap

A native macOS app on top of `swe`. You see usage, limits and sessions at a
glance, and manage accounts, skills, AGENTS.md and MCP servers without
remembering CLI commands.

## Decisions so far

| Topic | Decision |
|---|---|
| Location | `app/` in this repo, SwiftPM package |
| Platform | macOS 26 only, SwiftUI with Liquid Glass |
| Look | Adapted from TokenTracker (dark, green-tinted neutrals, one accent, cards with 1px borders). Not a clone. |
| Data that refreshes often | Native Swift: usage, cost, limits, account profiles |
| Data that writes harness config | `swe … --json`, so the tested Python code stays the only writer |
| Sidebar | Usage: Overview, Sessions. Manage: Accounts, Skills, AGENTS.md, MCP servers. System: Doctor, Settings |
| Accounts page | Limits merged with Switchboard-style profiles: each account card has its limit bars, when they were fetched, and Open app / Terminal buttons |
| Dropped from TokenTracker | Leaderboard, Achievements, Pet, IP Check, Service Status, cloud sync |
| Other apps' data | None shared. Quiver replaces Switchboard, TokenTracker, AIUsage and OpenUsage, which get deleted, so it never reads or writes their files, settings or caches, not even to import. |
| Profile store | Switchboard's model (one config home and one desktop data folder per profile, launched with the env var and `--user-data-dir`), kept under `~/.quiver/accounts` |

## Borrowed code

| Source | License | What |
|---|---|---|
| OpenUsage | MIT | Menu bar `NSStatusItem` + key-capable `NSPanel`, provider fetchers, pricing snapshot, JSONL scanner. Not its name or icon (TRADEMARK.md). |
| AIUsage | Apache 2.0 | Per-file cursor ledger for fast incremental scans |
| TokenTracker | MIT | Design tokens and page layouts. Not its provider logos. |
| Switchboard | MIT | Profile directories, `open -n --env … --args --user-data-dir=…` launch, terminal launchers |

## Milestones

### M0. `swe` speaks JSON
- [x] `python -m quiver.mcp_server` starts the server ([#124](https://github.com/c-wenlong/quiver/pull/124))
- [x] `--json` on `swe list --usage` (rate limits per harness, with `fetched_at`)
- [x] `--json` on `swe session` (id, harness, cwd, title, timestamp, status)
- [x] `--json` on `swe find skills` and `swe init --check` (link state per harness)
- [x] `--json` on `swe mcp list|status` and `swe doctor` (findings with fix commands)
- [x] One shared envelope: `{"schema": 1, "data": …}`, documented in ARCHITECTURE.md

### M1. App shell
- [x] `app/` SwiftPM package, macOS 26, app target plus a testable core library
- [x] Main window: `NavigationSplitView` sidebar, glass background, empty pages
- [x] Menu bar item with a panel (OpenUsage pattern)
- [x] `SweClient`: runs `swe` with the login-shell PATH, decodes the M0 envelope
- [x] Doctor page as the first real consumer of `swe --json`
- [x] Settings: choose which `swe` to run, and a clear message when it predates `--json`
- [x] CI job on a macOS 26 runner: build and `swift test`

### M2. Accounts
- [ ] Profile store and directory layout under `~/.quiver/accounts`
- [ ] Open a desktop app or terminal per profile (Claude, Codex)
- [ ] Native limit fetchers: Claude, Codex, Devin, Cursor, Copilot
- [ ] Carry over the safeguards in `rate_limits.py`: honor provider cooldowns
  and `Retry-After`, keep the original `fetched_at` when reusing a reading,
  and expire stale readings instead of showing them as current
- [ ] Account cards with bars, plan badge, reset time and "fetched N min ago"
- [ ] Menu bar panel shows the same bars

### M3. Overview
- [ ] Incremental scanner for Claude and Codex JSONL, sqlite for OpenCode and Devin
- [ ] Pricing from a bundled LiteLLM snapshot, refreshed daily
- [ ] Overview page: total tokens and cost, harness split, daily table, heatmap
- [ ] Cost donut in the menu bar panel (Today, Yesterday, 30 days)

### M4. Sessions
- [ ] List from `swe session --json`, joined with M3 token and cost data by session id
- [ ] Filters: harness, time, project, model
- [ ] Resume button: opens the session in your terminal
- [ ] Session detail sheet

### M5. Manage
- [ ] Skills: per-harness link chips, link and unlink through `swe skills`
- [ ] AGENTS.md: edit `~/.quiver/AGENTS.md` in place, link status per harness
- [ ] MCP servers: list by harness, diff, sync
- [ ] Plugins: drift findings with one-click fixes

### M6. Ship it
- [ ] Notifications when a limit resets or crosses 90%
- [ ] Launch at login
- [ ] WidgetKit widgets for limits and today's cost
- [ ] Signing, notarization, Sparkle updates

## Open questions

- App name and icon.
