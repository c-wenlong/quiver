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
| Sidebar | Usage: Overview, Sessions. Manage: Accounts, Harnesses, Compute, Skills, AGENTS.md, MCP servers, Plugins. System: Menu bar, Widgets, Service status, Doctor, Settings |
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
| TokenTracker | MIT | Design tokens, page layouts, and the harness logos it ships (most from LobeHub icons, MIT). The logos stay their owners' trademarks, used only to name each harness. |
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
  (`QuiverCore/Ledger`, persisted to `~/.quiver/data/ledger/ledger.sqlite`)
- [x] Pricing from a bundled LiteLLM snapshot, refreshed daily before a scan
  (`PricingStore`, kept with every past price in `~/.quiver/data/catalog/catalog.sqlite`)
- [x] Overview page: total tokens and cost, harness split, daily table, heatmap
- [x] Cost donut in the menu bar panel (Today, Yesterday, 30 days)

### Restyle
- [x] Shell, cards, controls and Overview after the TokenTracker style package ([#133](https://github.com/c-wenlong/quiver/pull/133))

### M4. Sessions
Modelled on TokenTracker's Sessions page.
- [x] List from `swe session --json`, joined with M3 token and cost data by session id
- [x] Search plus tool, time, project and model filters
- [x] Summary strip: matching sessions, tokens, cost
- [x] Rows grouped by date, with models, duration and project on a second line
- [x] Resume action: opens the session in your terminal, in its folder and account
- [x] Session detail sheet

### M5. Manage
- [x] Skills, built on quiver hub: per-harness link chips, link and unlink through `swe skills` ([#144](https://github.com/c-wenlong/quiver/pull/144))
- [x] AGENTS.md and CLAUDE.md, global and per project: edit in place, link status per harness ([#142](https://github.com/c-wenlong/quiver/pull/142))
- [x] MCP servers: list by harness, diff, sync (`swe mcp sync --json`) ([#142](https://github.com/c-wenlong/quiver/pull/142))
- [x] Plugins: drift findings with one-click fixes (`swe find plugins --fix=<id>`) ([#142](https://github.com/c-wenlong/quiver/pull/142))
- [x] Plugins Explore: every plugin in every marketplace, install, uninstall, enable and disable per harness (`swe plugins`); plugin skills listed there, not on Skills
- [x] Harnesses: discover installed ones, star and archive them ([#144](https://github.com/c-wenlong/quiver/pull/144)); install and uninstall steps open from Settings › Providers › Harnesses
- [ ] Launch a session in the right folder with one click
- [x] API keys: the provider keys in `~/.api_keys`, masked until revealed, edited in place; in Settings › Providers › Inference beside the inference plans

### M6. Menu bar, widgets and shipping
- [x] Limits in the menu bar: the status item shows the tightest limit; the panel lists every account's windows and the other harnesses
- [x] WidgetKit widgets after TokenTracker's designs (`screenshots/widgets-dark-full.png`): usage summary, activity heatmap, top models, usage limits
- [x] Notifications when a limit resets or crosses 90%, once per crossing
- [x] Launch at login (`SMAppService.mainApp`, a switch in Settings)
- [ ] Signing, notarization, Sparkle updates

### M7. Service status
- [x] Status of the services the harnesses depend on (Anthropic, OpenAI, GitHub, Cursor and more), from their public status pages
  (`QuiverCore/Status`: Statuspage feeds plus Google Cloud's for Gemini, cached at `~/.quiver/cache/service-status.json`)
- [x] Incidents surfaced in the menu bar when a service is degraded

### M8. Download and first run (DMG)

Goal: someone downloads a DMG onto a Mac with no `~/.quiver`, no `swe`, no
Python, no keys and no MCP servers. From there they reach a working setup,
and add or edit everything from the app, without a terminal. The audit
behind this list ran on 2026-10-19 and covered packaging, the `~/.quiver`
layout, keys, the Manage pages, and the usage and system pages.

Phases in order. Phase 4 can run alongside 1 to 3.

**Phase 0. Quick fixes**
- [x] `swe` version probe tried `claude version` first, which starts a Claude session ([#370](https://github.com/c-wenlong/quiver/pull/370))
- [x] Seeded `~/.quiver/.gitignore` lists `secrets/` ([#369](https://github.com/c-wenlong/quiver/pull/369))
- [ ] A missing `swe` shows raw `env: swe: No such file or directory`. `ProcessRunner` runs `/usr/bin/env`, so exit 127 lands as `commandFailed`, never `.notInstalled` (`CommandRunner.swift`, `SweClient.swift`). Map 127 to `.notInstalled`, and replace its "Install quiver with pip" text, since the PyPI name belongs to someone else.
- [ ] `swe providers add` tells users `~/.api_keys/<name>` is a directory of files, but the app reads `~/.api_keys` as one file and fails on a directory. Make them agree.

**Phase 1. Bundle `swe` inside the app**
- [x] python-build-standalone CPython 3.13 plus `src/quiver` at `Contents/Resources/swe`, used when Settings names no `swe` ([#371](https://github.com/c-wenlong/quiver/pull/371))
- [ ] Check Droid limits with the bundled Python. `rate_limits.py` loads `libcrypto` through ctypes and falls back to an unversioned dylib.

**Phase 2. First-run setup**
- [x] `swe setup --apply` seeds `~/.quiver` and links AGENTS.md, skills and hooks. `--adopt=<file>` and `--starred=a,b` added ([#373](https://github.com/c-wenlong/quiver/pull/373)).
- [x] Four-step setup window: agents, shared AGENTS.md, skills and MCP, look, notifications. Settings › General can open it again ([#375](https://github.com/c-wenlong/quiver/pull/375)).

**Phase 3. Add and edit from the app.** Every Manage page edits or syncs what exists, but almost none can create.
- [ ] API keys: a provider picker built from `providers/defaults.py` and `APIKeyCheck.swift`, with a "Get a key" link instead of the free-text Provider and Variable fields. Test a key before saving.
- [ ] API keys: offer to add `source ~/.api_keys` to `~/.zshrc` (back it up, ask first). Nothing does this today, so saved keys reach no new terminal, and the page subtitle claims they do.
- [ ] MCP servers: an Add server sheet (stdio command, args and env, or http url and headers) and Remove. `swe mcp` has neither, and `swe mcp edit` needs `$EDITOR`. Needs `swe mcp add|remove --json`.
- [ ] MCP secrets: `${NAME}` values live in `~/.quiver/secrets/.api_keys`, which nothing writes. Add `swe mcp secret set NAME` (0600) and a paste field per unresolved name. `swe mcp sync` writes the literal `${NAME}` into harness configs when a value is missing; refuse instead.
- [ ] Skills: New skill (writes `~/.quiver/skills/<name>/SKILL.md`) and Link all. Needs `swe skills new --json`.
- [ ] Plugins: Add marketplace (GitHub owner/repo or path) through `swe plugins marketplace add --json`. Open Explore first when the hub is empty.
- [ ] Harnesses: a Detect and register button (`swe setup --apply --json`), and a form for a custom harness.
- [ ] Accounts: Sign in opens a terminal that says "command not found" when the CLI isn't installed. Show Install there instead.
- [ ] Empty states: `EmptyCard` is a label only, and several pages tell users to run a command. Give each a button.
- [ ] Doctor: check for a missing `~/.quiver`, a missing AGENTS.md, unlinked harnesses and an empty registry. Each finding gets a Run button.
- [ ] Services: Sessions and Models say when a Devin or Artificial Analysis key is needed, and link to Settings › Services and the token page.

**Phase 4. Release pipeline**
- [ ] Build releases with `QUIVER_CHANNEL=prod`, and hide the channel picker and Rebuild in prod builds.
- [ ] Sign with Developer ID, hardened runtime and `--timestamp` (`build-app.sh` only finds Apple Development identities). Check what entitlements the bundled Python needs (ctypes). Then notarize and staple.
- [ ] A macOS 26 release job that builds, signs, notarizes, makes the DMG and attaches it to the GitHub release. The release workflow builds only the Python wheel today.
- [ ] Version stamps come from `git describe`, which reads `0.0.0` on a shallow CI checkout. Fetch tags or pass the version in.
- [ ] Universal build (arm64 and x86_64), Python included, or say Apple Silicon only on the download page. macOS 26 is the floor either way.
- [ ] In-app updates (Sparkle and an appcast). Today Settings only links to the releases page.
- [ ] `NSAppleEventsUsageDescription` (Sign in, Resume and Run in Terminal use `osascript`).
- [ ] Keychain writes pass the secret on `security`'s argv (`UsageProduction.swift`). Use stdin or the Security framework.
- [ ] The release check calls the `c-wenlong/quiver` releases API, so the repo has to be public.

**Phase 5. Polish**
- [ ] First ledger scan: show files done out of total, and partial results. A heavy user waits minutes behind a spinner.
- [ ] Explain the Desktop and Documents folder prompt before it appears (first Sessions or Calendar load).
- [ ] Don't start the Cursor, Devin cloud and Codex daily pollers for users without those harnesses.
- [ ] Fan helper: offer it only on Macs with fans, remove it when Quiver is removed, and reinstall a stale helper binary after an update.
- [ ] Menu bar incidents: show only the providers this Mac uses.

### Later
- [ ] Claude IP check (low priority)

## Open questions

- App name and icon.
