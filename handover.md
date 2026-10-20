# Handover: Quiver session, 2026-10-16

Read [AGENTS.md](AGENTS.md) first: the test, throwaway-HOME, PR-loop and LaunchServices rules there all apply. This file is a snapshot. It is not committed, and `git` plus `gh` are the source of truth.

## State right now

| Thing | State |
|---|---|
| Installed app | `~/Applications/Quiver.app`, built from `c18afa6` (PR 303 merge), CFBundleVersion 411 |
| Main checkout | on `main` at `c18afa6`, clean. `origin/main` is one ahead: `637196c` ([PR 304](https://github.com/c-wenlong/quiver/pull/304)), merged after the install, so **not installed yet** |
| Open PRs | only Dependabot [PR 106](https://github.com/c-wenlong/quiver/pull/106) |
| LaunchServices | only `~/Applications/Quiver.app` and its `QuiverWidgets.appex` are registered |
| Scratch worktrees | `/tmp/quiver-{codex-compare,codex-usage,models,services,session-filter}`, all merged and safe to remove with `git worktree remove` |
| Draft file | `/tmp/quiver-models-pr.md` (PR 303 body), safe to delete |

PR 304 ("Sessions: list Cursor and Grok Bot usage-only sessions, mark Cloud or Local") was worked on outside this session in the main checkout, on branch `sessions-cloud-usage-rows`. Check it with the user before assuming anything about it.

## What shipped this session (all merged)

| PR | What |
|---|---|
| [290](https://github.com/c-wenlong/quiver/pull/290) | Pricing: edit list prices, "Reset to list price" clears every matching alias but keeps effort-specific prices |
| [291](https://github.com/c-wenlong/quiver/pull/291) | Calendar day headers stop wrapping in narrow windows |
| [292](https://github.com/c-wenlong/quiver/pull/292) | Wider timing bounds on the flaky wall-clock tests |
| [293](https://github.com/c-wenlong/quiver/pull/293) | Calendar scroll lag: account names cached per load, header-only redraw while scrolling |
| [294](https://github.com/c-wenlong/quiver/pull/294) | Calendar edge cases: Devin helper-only sessions (`summarizer`, `compactor`) hidden, full fallback titles, single-moment "11:02 · <1m", empty or root folder shown as "No folder" |
| [295](https://github.com/c-wenlong/quiver/pull/295) | Pricing harness filter. Prices still apply to every harness |
| [296](https://github.com/c-wenlong/quiver/pull/296) | Every calendar hover card stays open while the pointer is on the anchor, the gap or the card (`HoverSafeRegion`, `CardGeometry`) |
| [297](https://github.com/c-wenlong/quiver/pull/297) | Grok Bot sessions named after their Bots from Grok Bot's local roster. One project per Bot; "Subagents" and "Grok Bot" placeholders for helpers and unknown IDs. No grouping by sidebar section (the user's choice) |
| [298](https://github.com/c-wenlong/quiver/pull/298) | `swe harness usage-daily codex [--account-dir=<dir>] [--json]`: account-wide daily tokens via `codex app-server` `account/usage/read` |
| [299](https://github.com/c-wenlong/quiver/pull/299) | Accounts page: Codex account total vs This Mac, weekly and monthly. Stale-request generation guards, also in `CursorUsageFeed` |
| [300](https://github.com/c-wenlong/quiver/pull/300) | Sessions: harness icon centred on the row |
| [301](https://github.com/c-wenlong/quiver/pull/301) | Settings › Services: Quiver's own keys in Keychain (`com.cwenlong.quiver.services`), metadata only in `~/.quiver/config/services.json`. Devin (`DEVIN_API_KEY`, `DEVIN_ORG_ID`) and Artificial Analysis (`ARTIFICIAL_ANALYSIS_API_KEY`), each with a test button |
| [302](https://github.com/c-wenlong/quiver/pull/302) | "Hide accidental sessions" (default on), on Sessions and Calendar, including Pi and Droid file sources. Rule in `SessionFilter` |
| [303](https://github.com/c-wenlong/quiver/pull/303) | Models page (Primitives, after MCP servers): Artificial Analysis chart and table, shared model hover card on Models, Overview top models and Pricing; manual links in `~/.quiver/config/model-links.json`; Manage reordered to Accounts, API keys, Harnesses, Compute |

## Decisions the user made (don't relitigate)

- **Service keys are not harness keys.** Devin and Artificial Analysis keys go in Settings › Services (Keychain), never in `~/.api_keys`. Future benchmark or usage services use the same mechanism.
- **Codex accounts:** the Business 5X account is the default at `~/.codex`; EDU stays as Codex 2. The two share conversations. Account-wide totals are unpriced (no model split), so cost stays based on local logs. Compare weekly and monthly, since OpenAI's day boundaries differ from local midnight.
- **Grok Bot:** one calendar project per Bot. Cloud paths (`/home/box/sand-data/...`) are not local folders.
- **Accidental-session rule:** hide when a session has zero tokens or zero responses, or when it has 2 or fewer responses, ran under 60 s and produced under 100 output+reasoning tokens. Cursor, Droid and Cline use only the zero rule. Missing usage hides a session only after a ledger scan has completed, and only for local-log harnesses. The thresholds came from analysis of real session data, so don't change them without new data.
- **Artificial Analysis:** 1,000 requests a day. A refresh fetches all six endpoints (LLMs plus five media leaderboards) into `~/.quiver/data/artificial-analysis/` (data, not cache). It runs on reload or when the data is a week old, never on launch and never on hover. The cost bar ranks against every model AA lists, by price rank (not linear). Only LLM data is shown so far.
- **Models UI:** follows Artificial Analysis's look (provider-coloured bars, a white outline on used models, a chart/table switch in the toolbar) but keeps Quiver's own typography and colours. Unmatched models live in the ⚠︎ popover, as on Pricing.

## Gotchas learned the hard way

- **Selectable text plus rotated text crashes AppKit.** `Page` applies `.textSelection(.enabled)` to page content. A rotated `Text` under it becomes an AppKit-hosted view that gets a NaN frame and aborts in `_nsis_frameInEngine`. Put `.textSelection(.disabled)` on any subtree with rotated text. The regression test is `app/Tests/QuiverAppUITests/ModelChartLayoutTests.swift`.
- **Check where a crash came from.** Builds run from `/tmp` register with LaunchServices and crash under a Quiver-like name. Read `procPath` and the bundle ID in the `.ips` before blaming the installed app. All 7 crash reports from 2026-10-16 came from a `/tmp` test build.
- **Concurrency pattern.** Async models in this app use generation counters: bump before the await, drop the result if a newer one started. Copy this for any new reload or refresh path.
- **Test gates.** Store the parked continuation and set the "called" flag in the same locked section that drains the waiter. Check-or-register the waiter under one lock. Greptile has caught this race twice.
- **Flaky Python test (not fixed).** `tests/test_providers_commands.py::CmdListMigrationTest::test_filter_drops_non_matching_providers` asserts `'xai'` isn't in the output, but the output includes the random temp folder name (it failed once on `tmp4xaifqgl`). Fix: assert on the table rows, not the whole output.
- **Install recipe:**
  1. `git pull --ff-only` on a clean `main`.
  2. From `app/`, run `QUIVER_BUILD_DIR=/tmp/quiver-install-build ./script/build-app.sh`.
  3. Quit Quiver with `osascript` and replace `~/Applications/Quiver.app`.
  4. Relaunch, `lsregister -u` every scratch bundle, then `killall chronod`.

## Follow-ups, in priority order

1. **Install PR 304.** Ask the user about it first, then follow the install recipe above.
2. **Fix the flaky `xai` test** in its own PR. The user hasn't answered yet; ask, or just do it since it is test-only.
3. **Wait on the user** for these:
   - The Artificial Analysis key in Settings › Services. Until then the Models page shows its no-key state.
   - `cd ~/dotfiles && ./rebuild.sh`, so the installed `swe` has `harness usage-daily`. Until then Codex account totals stay hidden.
   - A Devin personal access token (`cog_…`, plus the org ID) in Settings › Services. Then build the **Devin cloud import**: list cloud sessions and ACUs through the Devin API (`https://api.devin.ai/v3/...`, Bearer token), reading the token through `ServiceCredentials`. Never ask for the token in chat.
   - Sign in to the NUS/EDU Codex account. Its account totals show "sign in required" until then.
4. **Model hover cards in the session details sheet.** Not built: the sheet shows models as one joined string, so it needs one view per model first.
5. **Media leaderboards.** The five stored Artificial Analysis media responses aren't shown anywhere yet. That is a product decision for the user.
6. **Housekeeping.** Remove the merged `/tmp/quiver-*` worktrees and stale local branches (`app-refresh-failed`, `claude-renew-signin`, `compute-*`, `menu-logo-launch`, `swe-refresh-failed`, `widget-limits-order`). Check each is merged first.
7. **Dependabot [PR 106](https://github.com/c-wenlong/quiver/pull/106)** is still open.

## Working style the user expects

- The full PR loop: branch, PR, CI, Greptile, fix or rebut every thread, resolve it, merge yourself, then install. Report in the emoji-tag format from the global AGENTS.md, with the legend line at the end.
- Show a mockup before building any UI the user hasn't seen. Quiver is a native SwiftUI app with no browser, so mockups are static HTML.
- Use a throwaway `HOME` and the `com.cwenlong.quiver.services.test` Keychain service for every test or manual run. Never touch the real `~/.quiver`, Keychain items or ledger.
