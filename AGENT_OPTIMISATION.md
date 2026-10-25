# Agent optimisation: what changed

Landed changes from the Oct 2026 transcript audit (123 sessions, ~1.5GB). Tracking issue: [#752](https://github.com/c-wenlong/quiver/issues/752). Audit items: [#742](https://github.com/c-wenlong/quiver/issues/742)–[#751](https://github.com/c-wenlong/quiver/issues/751), [#758](https://github.com/c-wenlong/quiver/issues/758).

## Hooks (quiver-hub + dotfiles, activates on `./rebuild.sh`)

Scripts in `~/.quiver/hooks/claude/`, committed `7e11faa` on `device/kai-4-pro`, linked into `~/.claude/hooks/` by `swe init`. Declared in `home/.claude/settings.json` in dotfiles, commit `ce37d40` on `macos/MacBook-Pro-M4-Pro`.

- `no-sleep-poll.py` (#742): PreToolUse on Bash. Blocks `sleep`-then-poll shapes (`sleep N; gh …`, `for`/`while` loops calling `gh`/`pgrep`, `sleep; cat scratchpad`). Allows short startup sleeps. Tells the model to arm the monitor or use `run_in_background` and end the turn.
- `session-size-warn.py` (#743): PreToolUse on all tools. Blocks once per session when the transcript file exceeds 25MB, telling the model to wrap up and hand off; then lets it work.
- `task-model-nudge.py` (#745): PreToolUse on Task. Blocks once per session when a mechanical subagent call has no model tier, telling it to use Sonnet-class for grep/edit/summarise.
- `pr-behind-main.py` (#748): PreToolUse on Bash, fires on `gh pr create|merge`. Blocks when the branch is behind `origin/main`, so conflicts get resolved before review, not during.

## quiver repo (merged PRs)

- [#759](https://github.com/c-wenlong/quiver/pull/759) `agent/ci-lean-pr-lane` (#749): PR lane = Linux unittest shards + coverage + editable-install e2e, ~5 min. Moved to new `native.yml` (nightly, `merge_group`, dispatch): macOS `app` Swift tests, `macos-smoke`, `nix`. `release.yml` gained `macos-check` (built-wheel install + entry points + `test_path_health.py` on macOS) and a Swift test step before signing; `attach-to-release` requires it. Pip + SwiftPM caches added.
- [#760](https://github.com/c-wenlong/quiver/pull/760) `agent/reaper-script` (#747): `scripts/reaper.sh`. Dry-run by default, `--yes` applies, `--days=N` (14). Reaps `.claude/worktrees/` whose branches are merged or whose PRs closed, `[gone]`+merged local branches, and build dirs inside removed worktrees. Never touches dirty worktrees, open-PR branches, or `main`. Pid-owned lock; dry-run does not fetch.
- [#761](https://github.com/c-wenlong/quiver/pull/761) `agent/app-assertive-tests` (#744): `SessionFileParserTests.swift`, 21 tests on the Pi/Droid/Cline ledger parsers (the data behind the screens agents screenshot). First batch of assertion-over-eyeball coverage.
- [#762](https://github.com/c-wenlong/quiver/pull/762) `agent/hooks-domain` (#758): `swe hooks` domain — `list`, `info`, `edit` (opens the hub source, not the symlink), `new` (skeleton + settings stanza, never writes settings), `check` (declared-vs-linked drift, wired into `swe doctor`). New `hooks/` package, 65 tests, completion/help tables updated.

## dotfiles (activates on `./rebuild.sh`)

- `permissions.allow` (#750): added read-only ops (`git status/diff/log/show/fetch`, `ls`, `cat`, `grep`, `rg`, `find`, `sed -n`, `head`, `tail`, `wc`, `Read`, `Glob`, `Grep`) to `home/.claude/settings.json`. Write/exec/network ops still prompt.

## Not yet done (prose-rule batch)

- #743 rule text: one feature per session + handover convention (hook warns, the rule still needs writing)
- #746 batch read-only tool calls rule
- #748/#750 briefing template: decisions up front, file-ownership partition for parallel features
- #751 halve AGENTS.md: now safe to start, the enforceable rules have hooks

## Pending user actions

1. `./rebuild.sh` in `~/dotfiles` to activate the 4 hooks + permission allow-list.
2. Schedule the reaper, e.g. cron: `15 4 * * * <repo>/scripts/reaper.sh --yes >> ~/.quiver/logs/reaper.log 2>&1`, or a launchd agent in `home.nix`.
3. Watch the first `native.yml` nightly run (04:33 UTC) — it cannot be exercised on a PR by design; `workflow_dispatch` can kick it manually.

## Re-audit metrics (next transcript pass)

- `sleep`-poll commands: was ~2,690 / 49h → target ~0
- Parallel-batch share of tool calls: was 18.6% → target >40%
- Sessions over ~300k context / 25MB transcript: was common (one feature ran 13 files at ~730k) → target rare
- Screenshot/GUI calls per UI feature: was ~1,400 in 3 weeks → trending down as #744-style coverage grows
- Sonnet/Haiku share on Task calls: was ~0.3% → should rise once the nudge bites
