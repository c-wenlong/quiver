# swe JSON contracts

The app reads `swe` only through `--json`. Every response is the envelope
`{"schema": 1, "data": ...}`; `SweClient` decodes with
`.convertFromSnakeCase` and `.secondsSince1970` dates, and refuses a schema
other than 1. Each command below names the Swift type that decodes `data`
and the fixture that pins the shape. Fixtures live under
`app/Tests/QuiverCoreTests/Fixtures/` and run through
`SweContractTests`.

| Command | `data` decodes to | Fixture |
|---|---|---|
| `swe list --json` · `swe list --scope=all --json` · `swe list --usage --json` | `HarnessList` → `[Harness]` | `swe-list-scope-all.json`, `swe-list-usage.json` |
| `swe harness star <name> --json` | `HarnessStateChange` | `swe-harness-star.json` |
| `swe harness archive <name> [why] --json` | `HarnessStateChange` (with `archived`) | `swe-harness-archive.json` |
| `swe edit <name> ... --json` | `HarnessEditResult` | `swe-edit.json` |
| `swe skills --json` | `SkillInventory` | `swe-skills.json` |
| `swe skills link|unlink ... --json` | `LinkReply` | `swe-skills-link.json`, `swe-skills-unlink.json` |
| `swe init --check --json` | `InitCheck` | `swe-init-check.json` |
| `swe mcp list --json` · `swe mcp status --json` | `McpInventory` | `swe-mcp-list.json`, `swe-mcp-status.json` |
| `swe mcp sync ... --no-interactive --json` | `McpSyncReport` | |
| `swe find plugins --json` | `PluginDriftReport` | `swe-find-plugins.json` |
| `swe find plugins --fix=<id> --json` | `FixedDrift` | `swe-find-plugins-fix.json` |
| `swe plugins catalog --json` | `PluginCatalog` | `swe-plugins-catalog.json` |
| `swe plugins install|uninstall|enable|disable <ref> --harness=<id> --json` | `PluginActionResult` | `swe-plugins-action.json` |
| `swe install --json` | `InstallList` → `[InstallableHarness]` | `swe-install.json` |
| `swe harness usage <devin|cursor> --account-dir=<dir> --json` | `{"name", "account_dir", "limit"}` where `limit` is a row's `rate_limit` object or null | |
| `swe harness usage-events cursor [--account-dir=<dir>] [--since=<s>] --json` | `{"status", "account_id", "events"}` — `status` is `ok` \| `incomplete` \| `signed-out` \| `auth-required` \| `unavailable` (`incomplete` means a 7-day slice filled all 50 pages; the events are real but the window may have more). `account_id` is the Cursor user id from the token's `sub` claim, null when there is no token; each event has `timestamp` (epoch seconds), `model`, `kind`, `conversation_id`, `headless`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd` (dollars, null when Cursor logged none) | |
| `swe harness usage-daily codex [--account-dir=<dir>] --json` | `{"status", "summary", "days"}` — `status` is `ok` \| `auth-required` \| `unsupported` \| `unavailable`. `summary` is `{lifetime_tokens, peak_daily_tokens, current_streak_days, longest_streak_days}` or null; each `days` entry is `{"date": "YYYY-MM-DD", "tokens": int}` | |
| `swe doctor --json` | `DoctorReport` (`allowFailure`) | `swe-doctor.json` |
| `swe session <limit> [--days=N] --json` | `SessionListPayload` | `swe-session.json` |
| `swe gh --json` | `{"github": GhEnv, "repos": [PinnedRepo], "catalog": GhCatalog?}` | |
| `swe gh sync --json` | `{"repos": int, "synced_at": epoch, "owners": [name], "failed": [{"owner", "error"}]}` — rewrites the local repo catalog (`~/.quiver/data/github/catalog.sqlite`) in place; `failed` names owners whose `gh repo list` errored | |
| `swe gh repo list --json` | `{"repos": [PinnedRepo]}` | |
| `swe gh repo add <name|url> --json` | `{"repo", "pinned": [name]}` — verifies via `gh repo view` before pinning | |
| `swe gh repo remove <name> --json` | `{"removed": bool, "repo", "pinned": [name]}` | |
| `swe gh repo search <query> --json` | `{"query", "results": [RepoHit], "source": "catalog"\|"github"}` — reads the synced catalog first (no `gh` call on a hit), falls back to the live scoped search on a miss or when never synced; `--all` always searches public GitHub | |
| `swe gh pr [--state=S] [--repo=R] [--limit=N] --json` | `GhResults` — per-repo `pull_requests: [GhPr]` | |
| `swe gh pr status [--repo=R] --json` | `GhResults` — per-repo `current_branch` (`GhPr` or null), `authored: [GhPr]`, `review_requested: [GhPr]` | |
| `swe gh pr view <N|url> --repo=R --json` | `{"github": GhEnv, "repo", "status", "pr": GhPrDetail?}` — one repo only; `status` is `ok` or a `gh` status, `error` on failure (including a blocked env) | |
| `swe gh pr merge <N|url> --repo=R [--method=squash|merge|rebase] [--auto] --json` | `{"github": GhEnv, "repo", "ref", "method", "auto", "merged", "error"?}` — `merged` is false under `--auto` (armed, not merged) | |
| `swe gh pr worktree <N|url> --repo=R --json` | `{"github": GhEnv, "repo", "pr", "path", "head", "created", "error"?}` — `path` is the checkout under `~/.quiver/worktrees` a session launches from; `created` false means it already existed | |
| `swe gh run rerun <id> --repo=R [--failed] --json` | `{"github": GhEnv, "repo", "run", "rerun", "failed_only"?, "error"?}` | |
| `swe gh inbox read <id>...|--all --json` | `{"github": GhEnv, "all", "marked": [id], "failed": [{"id","error"}]}` — `--all` reports `marked: ["*"]` | |
| `swe gh issue [--state=S] [--repo=R] [--limit=N] --json` | `GhResults` — per-repo `issues: [GhIssue]` | |
| `swe gh run [--repo=R] [--limit=N] --json` (aliases `runs`, `actions`, `ci`) | `GhResults` — per-repo `runs: [GhRun]` | |
| `swe gh pages [--repo=R] --json` | `GhResults` — per-repo `pages` (see below) | |
| `swe gh releases [--repo=R] [--limit=N] --json` | `GhResults` — per-repo `releases: [GhRelease]` | |
| `swe gh tags [--repo=R] [--limit=N] --json` | `GhResults` — per-repo `tags: [GhTag]` | |
| `swe gh branches [--repo=R] [--limit=N] --json` | `GhResults` — per-repo `branches: [GhBranch]` plus `default_branch` | |
| `swe gh inbox [--all] [--limit=N] --json` | `{"github": GhEnv, "status", "notifications": [GhNotification]}` — `gh api notifications`, not per-repo; `--all` adds `all=true` so read rows come back too (default is unread-only) | |

## Shape notes

- `Harness`: `state` is `active` | `starred` | `archived`; `rate_limit` is
  null unless `--usage` and starred; `sessions_100d` decodes through the
  custom key `sessions100D`. `archived` carries `reason`, `archived_at`,
  `usage`, or is null.
- `RateLimit`: `status`, `used_percent`, `remaining_percent`,
  `limit_reached`, `reset_at`, `window`, `window_seconds`, `plan_type`,
  `remaining_units`, `total_units`, `fetched_at`, `windows`,
  `extra_usage_usd` (`extraUsageUsd`), `on_demand`. Providers differ:
  Claude reports one `7d` window, Cursor several windows plus `on_demand`,
  Devin `1d`/`7d` plus `extra_usage_usd`. A `windows` entry can carry
  `unavailable: true` when the provider left that meter unread — the
  figures are the last known values, and `as_of` (epoch seconds) is when
  they were read; absent keys mean a normal, current window.
- `InstallableHarness`: `uninstall` is an install-method object (`via`,
  `package`, `command`, `available`) for an installed harness whose binary
  resolves into a package manager's tree, else null. `via` is `cask`,
  `brew`, `npm`, `uv` or `nix`; a vendor script install has none.
- `McpInventory`: `harnesses` lists every config read; `servers` carry
  `args`, `command`, `conflict`, `detail`, `env_keys`, `harnesses`,
  `header_keys`, `health` (a string, not an object), `name`, `transport`,
  `url`, `variants`.
- `DoctorReport`: `ok` plus `findings` (`check`, `severity`, `message`,
  `fix`, optional `harness`). The command may exit nonzero with valid
  JSON, so the client is called with `allowFailure`.
- `SessionListPayload`: `sessions` (`id`, `harness`, `agent`, `cwd`,
  `title`, `title_source`, `last_active_at`, `status`, `resume`) plus
  `failures` for parsers that crashed.
- `PluginCatalog`: `harnesses`, `actions`, `marketplaces` (each with
  `harnesses` metadata and `plugins`), `errors`. Plugin `components` keys
  like `mcp_servers` stay snake_case in saved snapshots.
- No `--json` output carries secret values; MCP env/header values arrive
  as key names only.

### `swe gh` shapes

All GitHub data comes from the ambient `gh` login; no tokens are stored.
Repo resolution is `--repo=` first, then the working directory's git
remote, then every pinned repo — a bare query outside a checkout runs
against all pins, so `results` is always a list of per-repo entries.

- `GhEnv`: `{"status", "login", "error"}` — `status` is `ok` |
  `unavailable` (gh missing) | `auth-required` | `upstream-error`
  (the `gh auth status` probe itself failed, e.g. a timeout — not proof
  the login is gone). `error` carries the probe failure or is null. On a
  blocked env the command exits 1, prints the reason to stderr, and
  still emits the envelope with empty results.
- `GhResults`: `{"github": GhEnv, "results": [entry]}` where each entry
  is `{"repo", "status", <rows>, "error"?}`. `status` is `ok` |
  `not-found` | `unavailable` | `auth-required` | `upstream-error`; on
  `ok` the row list is populated, otherwise it is `[]`/`null` and
  `error` carries gh's stderr. Distinguish `ok` + empty rows (a repo
  with no PRs) from an upstream failure.
- `PinnedRepo`: `{"name": "owner/repo", "added_at": epoch}`.
- `GhCatalog`: `{"repos": int, "synced_at": epoch, "owners": [name]}` —
  the synced repo store `swe gh repo search` reads; `synced_at` is when
  the last `swe gh sync` wrote it. Null until the first sync.
- `RepoHit`: `{name, description, stars, updated_at, url}`.
- `GhPr`: `{number, title, state, draft, author, branch, base, labels,
  mergeable, review_decision, checks, updated_at, url}`. `state` is
  `open`|`closed`|`merged`; `checks` is a rollup `none`|`passing`|
  `running`|`failing`|`unknown`; `review_decision` is `approved`|
  `changes_requested`|`review_required`|`none`; `mergeable` is GitHub's
  value lowercased (`mergeable`|`conflicting`|`unknown`).
- `GhPrDetail`: every `GhPr` field plus `{repo, body, merge_state,
  additions, deletions, changed_files, check_runs, reviews,
  review_requests, auto_merge, created_at, merged_at}`. `check_runs`
  entries are `{name, workflow, status, conclusion, state, url}` where
  `state` folds the entry to `passing`|`running`|`failing`|`none`;
  `reviews`
  are `{author, state, submitted_at}`; `review_requests` is a list of
  names (a user login or a team slug); `auto_merge` is
  `{method, enabled_at}` or null.
- `GhIssue`: `{number, title, state, author, labels, assignees,
  comments, updated_at, url}`.
- `GhRun`: `{id, workflow, title, branch, event, status, conclusion,
  created_at, updated_at, url}`; `conclusion` is null while running.
- `pages` entry: `{"repo", "status", "pages": null | {url, cname,
  status, source_branch, latest_build: null | {status, error, commit,
  updated_at}}}`. `pages: null` means the repo has no Pages site;
  `latest_build: null` means no builds yet, while a failed build read
  arrives as `{"status": "fetch-failed", "error": <gh stderr>}`.
- `GhRelease`: `{tag, name, latest, draft, prerelease, published_at,
  url}`. `GhTag`: `{name, sha, url}`. `GhBranch`: `{name, protected,
  default, sha, url}`.
- `GhNotification`: `{id, repo, title, type, reason, unread,
  updated_at, url}` — `url` is rewritten from the API subject URL to the
  github.com page (`/pulls/N` → `/pull/N`), falling back to the repo
  page when the subject has none. `type` is GitHub's (`PullRequest`,
  `Issue`, `CheckRun`, …), `reason` is `review_requested`, `mention`,
  etc.
- Timestamp fields (`updated_at`, `published_at`, `created_at`,
  `added_at`): `added_at` is epoch seconds; the rest are ISO 8601
  strings straight from `gh` — decode with `.iso8601`, not
  `.secondsSince1970`.
