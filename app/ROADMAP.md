# quiver app roadmap

A native macOS app on top of `swe`. You see usage, limits and sessions at a
glance, and manage accounts, skills, AGENTS.md and MCP servers without
remembering CLI commands.

## Decisions so far

| Topic | Decision |
|---|---|
| Location | `app/` in this repo, SwiftPM package |
| Platform | macOS 26 only, native SwiftUI. Not a fork of TokenTracker (Node and React in a web view): its look and best features are ported instead. |
| Look | The TokenTracker style package in design-systems (`systems/tokentracker`); values come from its `tokens.json`. Borderless sidebar on the window wash, rounded content panel, hairline cards, sentence case everywhere (no all caps), green only for money and "on" states, and a visible focus ring. |
| Data that refreshes often | Native Swift: usage, cost, limits, account profiles |
| Data that writes harness config | `swe … --json`, so the tested Python code stays the only writer |
| Sidebar | Usage: Overview, Sessions. Manage: Accounts, Skills, AGENTS.md, MCP servers. System: Doctor, Settings |
| Accounts page | Limits merged with Switchboard-style profiles: each account card has its limit bars, when they were fetched, and Open app / Terminal buttons |
| Kept from TokenTracker | Sessions and Limits (the most important), menu bar and widgets, Skills (built on quiver hub), Service Status (extended to more services), Settings, Tokens overview. Claude IP Check at low priority. |
| Out of scope | Leaderboard, Achievements, Pet, Cloud sync |
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
- [x] Profile store and directory layout under `~/.quiver/accounts`
- [x] Open a desktop app or terminal per profile (Claude, Codex)
- [x] Limit fetchers: Claude and Codex natively, per account; Devin, Cursor and Copilot through `swe list --usage --json`, which already polls them with its safeguards
- [x] Carry over the safeguards in `rate_limits.py`: honor provider cooldowns
  and `Retry-After`, keep the original `fetched_at` when reusing a reading,
  and expire stale readings instead of showing them as current
- [x] Account cards with bars, plan badge, reset time and "fetched N min ago"
- [x] Menu bar panel shows the same bars
- [x] Ask `swe` only for the harnesses Quiver has no account for (`--exclude`, #131), so Claude and Codex are not polled twice

### M3. Overview
- [x] Incremental scanner for Claude and Codex JSONL, sqlite for OpenCode and Devin
  (`QuiverCore/Ledger`, persisted to `~/.quiver/cache/ledger/ledger.sqlite`)
- [x] Pricing from a bundled LiteLLM snapshot, refreshed daily before a scan
  (`PricingStore`, cached at `~/.quiver/cache/pricing/litellm.json`)
- [x] Overview page: total tokens and cost, harness split, daily table, heatmap
- [x] Cost donut in the menu bar panel (Today, Yesterday, 30 days)

### Restyle
- [ ] Shell, cards, controls and Overview after the TokenTracker style package ([#133](https://github.com/c-wenlong/quiver/pull/133))

### M4. Sessions
Modelled on TokenTracker's Sessions page.
- [ ] List from `swe session --json`, joined with M3 token and cost data by session id
- [ ] Search plus tool, time, project and model filters
- [ ] Summary strip: matching sessions, tokens, cost
- [ ] Rows grouped by date, with models, duration and project on a second line
- [ ] Resume action: opens the session in your terminal, in its folder and account
- [ ] Session detail sheet

### M5. Manage
- [ ] Skills, built on quiver hub: per-harness link chips, link and unlink through `swe skills`
- [x] AGENTS.md and CLAUDE.md, global and per project: edit in place, link status per harness
- [x] MCP servers: list by harness, diff, sync (`swe mcp sync --json`)
- [x] Plugins: drift findings with one-click fixes (`swe find plugins --fix=<id>`)
- [ ] Harnesses: discover installed ones, star and archive them
- [ ] Launch a session in the right folder with one click

### M6. Menu bar, widgets and shipping
- [ ] Limits in the menu bar: every account's bars at a glance
- [ ] WidgetKit widgets after TokenTracker's designs (`screenshots/widgets-dark-full.png`): usage summary, activity heatmap, top models, usage limits
- [ ] Notifications when a limit resets or crosses 90%
- [ ] Launch at login
- [ ] Signing, notarization, Sparkle updates

### M7. Service status
- [ ] Status of the services the harnesses depend on (Anthropic, OpenAI, GitHub, Cursor and more), from their public status pages
- [ ] Incidents surfaced in the menu bar when a service is degraded

### Later
- [ ] Claude IP check (low priority)

## Open questions

- App name and icon.
