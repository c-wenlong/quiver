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
| `swe gh repo reorder <name>... --json` | `{"pinned": [name]}` — named pins take the given order; unnamed pins keep their relative order at the end | |
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

## Remote control (`swe remote serve`)

A separate channel from the `--json` envelope: the hub speaks REST + SSE
to phone clients over Tailscale, auth'd by a paired device credential
(`Authorization: Bearer`). Events are `{"v": 1, "type", "session",
"machine", "seq", "ts", "data"}`; `seq` is per-session and `?last=seq`
replays the journal.

| Route | Returns |
|---|---|
| `GET /v1/status` | `{ok, v, version, machine, uptime_s}` (unauthenticated) |
| `POST /v1/pair` | `{device_id, credential, name}` for a live pair token |
| `POST /v1/pair/request` | unauthenticated; identifies the caller via `tailscale whois` on the peer IP (behind `tailscale serve`, the `X-Forwarded-For` client). `201 {request_id, poll_secret, code, expires_in_s, node: {name, login}}`; `403 not_tailnet` when the peer can't be identified; `429 rate_limited` past 5 requests/600s per IP; `429 busy` past the pending cap |
| `GET /v1/pair/request/{id}?secret=` | unauthenticated; `200 {status: pending, expires_in_s}` or `{status: denied}` or `{status: approved, device_id, credential, name}` (approved answers once, then the row is gone); `404 unknown_request` |
| `GET /v1/pair/requests` | `{requests: [{request_id, name, node_name, login, peer_ip, created_at, expires_in_s}]}`; a paired bearer OR a peer whose tailnet login matches the hub owner's |
| `POST /v1/pair/requests/approve` | `{code, request_id?}` → `200 {ok, device_id, name}`; `401`, `403 self_approval` (a device can't approve itself), `404 no_match`; 5 wrong codes deny the request |
| `POST /v1/pair/requests/deny` | `{code, request_id?}` → `200 {ok}` or `404` |
| `GET /v1/sessions` | `{sessions: [SessionRow + key, machine_id, ownership, attachable], machines, server_time}` — the SessionRow fields are identical to `swe session --json` |
| `GET /v1/sessions/{key}/events` | SSE of `message.appended`, `session.status`, `notify`, `compaction`; `?last=seq` replays |
| `GET /v1/sessions/{key}/messages` | `{messages: [events], next_before, has_more}` journal backfill |
| `GET /v1/sessions/{key}/queue` | `{sends: [{client_id, text, attachments, queued_at}]}`: queue-mode sends with a `client_id` still waiting for idle, oldest first; always empty for a satellite session. `404 unknown_session` |
| `POST /v1/sessions/{key}/queue` | `{client_id, op: steer\|cancel}` → `200 {ok, client_id, op}`. `404 unknown_send`, `409 not_waiting` (already sent, steered, removed or failed), `409 not_attached` (steer on an observed session), `409 queue_unsupported` (satellite session), `422 bad_command`, `429 backlog_full` (steer queue full) |
| `GET /v1/events` | global SSE (all sessions + `machine.*`) |
| `GET /v1/inbox` | `{items: [{key, machine_id, harness, agent, title, cwd, status, last_active_at, request_id?}]}`: pending approvals (`status: approval`, any age), plus sessions in `followup`/`error`/`interrupted` active in the last 3 days, minus rows this device dismissed. `?done=1` adds sessions finished since the hub's local midnight as `status: done` |
| `POST /v1/inbox/dismiss` | `{key, last_active_at}` → `200 {ok}`; hides the row for this device until its `last_active_at` moves past the given one. `422 bad_dismiss` without a key or a numeric `last_active_at` |
| `GET /v1/machines` | `{machines}` |
| `GET /v1/app/overview?period=&tz=&rescan=1` | the web app's Overview: `{period, time_zone, range: {start_day, end_day} \| null, first_day, totals: {tokens, input, output, cache_read, cache_write, reasoning, cost_usd, unpriced_tokens, unpriced_models}, harnesses: [{harness, tokens, cost_usd, share, models}], days: [{date, tokens, input, output, cached, cost_usd}], quick: {active_days, tokens_per_day, busiest_day}, top_models: [{model, tokens, share, harnesses}], heatmap: {weeks, days: [{date, tokens, cost_usd, level} \| {date, future}]}, scanned_at, notes, machine}`. `period` is `today` \| `7d` \| `month` (default) \| `30d` \| `90d` \| `365d` \| `all`; `tz` is the browser's IANA zone for day boundaries. Built by `ledger/overview.py`; see docs/web-app.md |
| `GET /v1/app/sessions?rescan=1` | the web app's Sessions: `{sessions: [{key, id, harness, agent, title, cwd, project, project_key, last_active_at, status, location, machine_id, machine, remote, resume, usage, duration_s, trivial}], ledger_harnesses, scanned_at, notes, machine}`. Rows are `/v1/sessions`' catalog; `usage` is `{tokens, input, output, cache_read, cache_write, reasoning, cost_usd, unpriced_tokens, events, first_ts, last_ts, models: [{model, tokens}]}` over all history, joined by `(harness, id)` for hub-machine rows only, else null. `project_key` is the lowercased project, or `other` for hash-like names; `trivial` marks a short or empty session the page hides by default. Built by `webapp/sessions.py` and `ledger/sessions.py` |
| `GET /v1/app/harnesses` | the web app's Harnesses: `{harnesses: [HarnessRow], window_days, machine}`, the rows of `swe list --scope=all --json` for the hub machine (archived included, `rate_limit` always null; the Accounts page reads limits from `/v1/usage`). `503 registry_unavailable` when the registry can't be read |
| `GET /v1/app/<page>` · `POST /v1/app/<page>/<action>` | a web app page plugin (`quiver/webapp/pages/<page>.py`); each payload is documented in `docs/web-app/<page>.md`. `404` for an unknown page or action, `403 bad_origin` on a cross-site POST, `503 page_unavailable` when the page can't read its source |

`message.appended.data` is a RemoteMessage: `{kind: user|assistant|
thinking|tool_use|tool_result|compaction|system|approval|error, text,
ts, tool: {name, call_id, args_summary, args_full}?, result_ref?}`.
Writes (`POST /v1/sessions/{key}/message|approval|control`, `POST
/v1/sessions`) answer `409 not_attached` while a session is `observed`.

`POST /v1/sessions/{key}/message` takes `{text, mode: queue|steer,
attachments: [refs], client_id?}` and answers `202 {ok, queued: true,
client_id, waiting}` — the send runs off the request: `queue` waits for
the session to leave `active` (`waiting` reports whether it is), `steer`
writes immediately. `client_id` is optional, 1-64 chars of
`[A-Za-z0-9_-]`, and comes back both in the response and in the
`command.result` event so a client can settle an optimistic bubble.

The send's outcome lands as a `command.result` event on the session key
(same shape satellite results use): `{command_id, op: "message", ok,
note, degraded, client_id}`. Validation failures (bad body, unknown
attachment) still answer synchronously — `400`, `422` — before the 202.
A second send under a `client_id` that is still waiting in queue mode is
`409 duplicate_client_id`.

A waiting queue-mode send can be acted on through `POST .../queue`:
`steer` sends it now as a steer (its `command.result` arrives like any
steer's, `degraded` included), and `cancel` drops it and emits its
`command.result` at once with `ok: false`, `note: "removed before
sending"` and `cancelled: true`. `cancelled` appears only on a removed
send, so a client can tell a removal from a failure. Exactly one
`command.result` is emitted per send, whichever side wins a race.

For sessions owned by a satellite (`key@machine`) the same route returns
the same `202` shape, except `waiting` is always `false` — the satellite
re-reads status and decides for itself.

The CLI side of remote also speaks `--json`:

| Command | `data` |
|---|---|
| `swe remote status --json` | `{hub: {ok, version, uptime_s, machine} or {ok:false}, hub_addr, machines: [{id, name, online, last_seen_at}], devices: [DeviceRow]}`; exits 1 when the hub is unreachable but still emits the envelope |
| `swe remote devices --json` | `{devices: [DeviceRow]}`; `--revoke=<id>` → `{revoked, ok}`; `--requests` → `{requests: [PairRequestRow]}`; `--approve=<code>` → `{ok, device_id, name, node_name}`; `--deny=<code>` → `{ok}` |
| `swe remote pair --json` | `{host, port, token, name, url, deep_link, expires_in_s}` — `url` opens the PWA, `deep_link` is the `quiver://pair?…` form for the iOS shell; `expires_in_s` is the token TTL |
| `swe remote notify --json` | `{notify: {url?, topic?, ntfy_token?, enabled, action_credential?}}` with credential fields redacted to `***` |

`DeviceRow`: `{id, name, created_at, last_seen_at, revoked}`.

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
`--repo=` takes a comma list (`--repo=a/b,c/d`) which resolves every
part in order; one bad part fails the command before any fetch.

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
