# swe JSON contracts

The app reads `swe` only through `--json`. Every response is the envelope
`{"schema": 1, "data": ...}`; `SweClient` decodes with
`.convertFromSnakeCase` and `.secondsSince1970` dates, and refuses a schema
other than 1. Each command below names the Swift type that decodes `data`
and the fixture that pins the shape. Fixtures live under
`app/mac/Tests/QuiverCoreTests/Fixtures/` and run through
`SweContractTests`.

| Command | `data` decodes to | Fixture |
|---|---|---|
| `swe list --json` · `swe list --scope=all --json` · `swe list --usage --json` | `HarnessList` → `[Harness]` | `swe-list-scope-all.json`, `swe-list-usage.json` |
| `swe harness star <name> --json` | `HarnessStateChange` | `swe-harness-star.json` |
| `swe harness archive <name> [why] --json` | `HarnessStateChange` (with `archived`) | `swe-harness-archive.json` |
| `swe edit <name> ... --json` | `HarnessEditResult` | `swe-edit.json` |
| `swe skills --json` | `SkillInventory` | `swe-skills.json` |
| `swe skills link|unlink ... --json` | `LinkReply` | `swe-skills-link.json`, `swe-skills-unlink.json` |
| `swe init --check --json` | `InitCheck`. Also `account_sync` (`{instructions, skills, hooks, mcp}` booleans, null when config.json is unreadable) and `accounts` (`[{account, primitive, path, state, detail}]`, one row per extra-account target; an `mcp` row also has `name`, the server, or null for a row about the whole file, and `path` is the account's `.claude.json` or `config.toml`; `blocked` means that file could not be read or edited safely and was not written; `state` is the init vocabulary in its `would-` form plus `would-unlink`) and `account_notes` (why the account pass was skipped, empty when it ran). `swe init --accounts [--check] --json` is `{account_sync, accounts, notes}` and exits 1 when `notes` is not empty | `swe-init-check.json` |
| `swe instructions check --json` | `{"directory", "index", "output", "index_exists", "output_exists", "output_generated", "chapters": [{"name", "path", "exists"}], "error", "stale"}` — `index_exists` false means the shared file is not chapter-managed (flat-file editing); `stale` is null then. `error` carries the compiler's refusal while the chapter list still shows what index.txt names. Exits 1 when `error` is set or `stale` is true | |
| `swe instructions build --json` · `swe instructions init --json` | `{"changed": bool}`; init is `{"created": true, "chapter", "source", "built"}` — it folds the flat AGENTS.md into `AGENTS_MD/<chapter>` (compiler comments stripped when it was generated) and builds | |
| `swe hooks list [--harness=<id>] --json` | `{"hooks": [HookRow]}` — `name`, `harness`, `tier` (`shared`/`harness`/`foreign`), `source_path`, `dest_path`, `link_status` (the init link vocabulary plus `foreign`), `enabled`, `events_supported` (normalized event names), `declared` (`{event, matcher, file}`) | |
| `swe hooks info <name> --json` | `{"script", "harnesses": [row]}` — the `list` row shape plus `detail`, `summary`, and `declared` carrying `timeout` and `command` too | |
| `swe hooks check --json` | `{"findings": [{"kind", "harness", "message"}]}` — `kind` is `declared-missing`/`undeclared`/`unlinked`/`managed` (`managed` is a note, not drift; exit 1 when anything else is present) | |
| `swe hooks sync [--force] --json` | `{"generated": [path], "pruned": [path], "links": {state: count}}` | |
| `swe hooks delete <name> [--harness=<id>] [--foreign] [--yes] --json` | `{"deleted": [str], "declarations_left": [file]}` — deletes need `--yes` or a tty confirm; foreign files need `--foreign` | |
| `swe hooks disable <name>` / `swe hooks enable <name>` `[--harness=<id>] --json` | `{"name", "state": "disabled"|"enabled", "sources": [path], "links": {state: count}}` | |
| `swe hooks new <harness> <name> --json` | `{"path", "harness", "command", "settings": [file], "stanza"}`; `swe hooks new shared <name> --json` returns `{"path", "harness": "shared", "tier", "commands": {h: cmd}, "deploys_to": [h], "stanza"}` | |
| `swe mcp list --json` · `swe mcp status --json` | `McpInventory` | `swe-mcp-list.json`, `swe-mcp-status.json` |
| `swe mcp sync ... --no-interactive --json` | `McpSyncReport` | |
| `swe mcp gateway status --json` | `{"enabled", "port", "installed", "pid", "reachable", "servers": [{"name", "url", "state", "pid", "uptime_s", "starts", "error"}]}` — one row per shared hub server; `state` is `running` \| `idle` (not started yet) \| `error` \| `down` (the gateway does not answer). A sync report's targets also carry `via_gateway`: the servers written as the gateway's URL | |
| `swe find plugins --json` | `PluginDriftReport` | `swe-find-plugins.json` |
| `swe find plugins --fix=<id> --json` | `FixedDrift` | `swe-find-plugins-fix.json` |
| `swe plugins catalog --json` | `PluginCatalog` | `swe-plugins-catalog.json` |
| `swe plugins install|uninstall|enable|disable <ref> --harness=<id> --json` | `PluginActionResult` | `swe-plugins-action.json` |
| `swe install --json` | `InstallList` → `[InstallableHarness]` | `swe-install.json` |
| `swe harness usage <devin|cursor> --account-dir=<dir> --json` | `{"name", "account_dir", "status", "remedy", "limit"}` — `status` is `ok` \| `no-sub` \| `signed-out` \| `auth-required` \| `permission-required` \| `unavailable` \| `timeout`; `remedy` is a `Remedy` aimed at this account's own files, null for `ok`; `limit` is a row's `rate_limit` object or null. Exits 1 for any status but `ok`, `no-sub` and `signed-out`, after printing the document | |
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
| `swe gh issue view <N|url> --repo=R --json` | `{"github": GhEnv, "repo", "status", "issue": GhIssueDetail?, "error"?}`: `GhIssueDetail` is `{number, title, state (open\|closed), author, labels: [str], assignees: [str], url, body, created_at, updated_at, comments: [{author, body, created_at, url}], branch, worktree, suggested_prompt}`: `branch` is `quiver/issue-<N>`, `worktree` the absolute path `swe gh issue worktree` checks it out at, `suggested_prompt` the first prompt `POST /v1/issues/resolve` would build (every comment folded in, capped near 30k characters, oldest comments dropped first) | |
| `swe gh issue comment <N|url> --repo=R --body=TEXT --json` (or `--body-file=PATH`, `-` for stdin, so the text stays off argv) | `{"github": GhEnv, "repo", "status", "issue": N, "comment": {author, body, created_at, url}?, "error"?}`: the body reaches `gh issue comment --body-file -` on stdin, so any text survives; `author` is the signed-in login (`""` when unknown), `created_at` the post time (UTC, ISO 8601), `url` the new comment's. An empty or blank body exits 1 with `error` | |
| `swe gh issue worktree <N|url> --repo=R --json` | `{"github": GhEnv, "repo", "issue", "path", "branch", "base", "created", "error"?}`: branch `quiver/issue-<N>` cut from the fetched default branch (`base`, e.g. `origin/main`) and checked out under `~/.quiver/worktrees/<owner>__<repo>-issue<N>`; asking again returns the same checkout (`created` false), and a branch whose folder was deleted is checked out again with its commits | |
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
| `GET /v1/status` | `{ok, v}` (unauthenticated reachability check) |
| `POST /v1/pair` | `{device_id, credential, name}` for a live pair token; `429 rate_limited` past 5 attempts/600s per IP |
| `POST /v1/pair/request` | unauthenticated; identifies the caller via `tailscale whois` on the peer IP (behind `tailscale serve`, the `X-Forwarded-For` client). `201 {request_id, poll_secret, code, expires_in_s, node: {name, login}, auto_approved?}`; `auto_approved: true` when owner auto-approve took it (the first poll then answers `approved`); `403 not_tailnet` when the peer can't be identified; `429 rate_limited` past 5 requests/600s per IP; `429 busy` past the pending cap |
| `GET /v1/pair/request/{id}?secret=` | unauthenticated; `200 {status: pending, expires_in_s}` or `{status: denied}` or `{status: approved, device_id, credential, name}` (approved answers once, then the row is gone); `404 unknown_request` |
| `GET /v1/pair/requests` | `{requests: [{request_id, name, node_name, login, peer_ip, created_at, expires_in_s}]}`; a paired bearer OR a peer whose tailnet login matches the hub owner's |
| `POST /v1/pair/requests/approve` | `{code, request_id?}` → `200 {ok, device_id, name}`; `401`, `403 self_approval` (a device can't approve itself), `404 no_match`; 5 wrong codes deny the request |
| `POST /v1/pair/requests/deny` | `{code, request_id?}` → `200 {ok}` or `404` |
| `GET /v1/devices` | `{devices: [DeviceRow + paired_via + this_device], requests: [PairRequestRow]}`: every `devices.json` row as `device_view` (no credential), `paired_via` defaulted to `token`, `this_device` marking the caller (a `tailnet:` owner sign-in marks nothing); `requests` is the same pending-request list `GET /v1/pair/requests` serves |
| `POST /v1/devices/revoke` | `{device_id, confirm_this_device?}` → `200 {revoked, this_device, devices: [DeviceRow + paired_via + this_device], signins}`: signs one paired device out and drops its `authorized_keys` line; a `tailnet:<node>` id revokes that owner tailnet sign-in (and devices paired from it). `400 bad_device`, `404 unknown_device`, `409 already_revoked`, `409 confirm_this_device` for the caller's own row without `confirm_this_device: true`, `500 ssh_key_left` |
| `GET /v1/sessions` | `{sessions: [SessionRow + key, machine_id, ownership, attachable, can_fork, model?, model_modes, mode?, tmux?, kept?, harness_title, pinned, pinned_at, unread], machines, server_time}` — the SessionRow fields are identical to `swe session --json`. `title` is the display name set with `/title` when there is one, else the harness's own; `harness_title` is always the harness's own. `pinned` is a bool and `pinned_at` the epoch seconds of the pin (`null` when unpinned), so a client orders the Pinned section by it. On an attached row, `mode` is the supervised channel (`tmux`, `headless`, `serve`, `appserver`) and `tmux` the `qv-…` pane name when the mode has one. On an observed row whose pane a `keep` detach left running, `kept` is true and `tmux` names it, and `attachable` covers it (attach adopts the pane live, no resume flags needed). `model` is the model of the newest reply in the session's transcript, absent when unknown; `model_modes` lists the supervised modes in which the `model` control op switches it (`["tmux", "headless"]` for claude, `["tmux", "appserver"]` for codex, `["serve"]` for opencode, else `[]`). `unread` is true when the row's `last_active_at` is newer than the calling device's last-read cursor (`POST /v1/sessions/{key}/read`); a session this device never opened is judged against the later of when the device paired and when the hub's read store began, so old rows start read |
| `POST /v1/sessions/{key}/pin` | `{pinned: bool}` → `200 {ok, title, harness_title, pinned, pinned_at}`. Stored on the hub, so every device agrees; works for satellite sessions. `404 unknown_session`, `422 bad_command` when `pinned` is not a bool |
| `POST /v1/sessions/{key}/title` | `{title: string}` → `200 {ok, title, harness_title, pinned, pinned_at}`. Whitespace is trimmed and collapsed to one line, the name is capped at 120 characters, and an empty string clears the override. `404 unknown_session`, `422 bad_command` when `title` is not a string |
| `POST /v1/sessions/{key}/read` | `{at?}` → `200 {ok, at}`: moves the calling device's last-read cursor for the session to `at` (epoch seconds), defaulting to now. Any `at` is capped at the later of now and the row's `last_active_at`, and a cursor never moves back, so `at` in the answer is the stored cursor. Hub-side for satellite sessions too; stored per device id in `session_reads.json`. `400 bad_body`, `404 unknown_session`, `422 bad_read` when `at` is not a number, `500 store_failed` |
| `GET /v1/sessions/{key}/events` | SSE of `message.appended`, `session.status`, `session.meta`, `session.ownership` (`ownership` plus the row's `tmux`/`mode`/`kept` on hubs that carry them), `notify`; a compaction is a `message.appended` with `kind: "compaction"`; `?last=seq` replays |
| `GET /v1/sessions/{key}/messages` | `{messages: [events], next_before, has_more}` journal backfill |
| `GET /v1/sessions/{key}/queue` | `{sends: [{client_id, text, attachments, queued_at}]}`: queue-mode sends with a `client_id` still waiting for idle, oldest first; always empty for a satellite session (its queue-mode sends wait on the satellite). `404 unknown_session` |
| `GET /v1/sessions/{key}/preview?path=` | the raw bytes of one `.html`, `.htm` or `.svg` file under the session's working folder (`path` relative to it), not JSON: `Content-Type` `text/html; charset=utf-8` or `image/svg+xml`, `Cache-Control: no-store`, `Last-Modified`, `X-Content-Type-Options: nosniff` and a `Content-Security-Policy` with `default-src 'none'` and `sandbox` (no scripts, no network). Confined like the `@` file search: no absolute path, no `..`, no symlink out, regular files only, 2 MB cap. Errors are JSON: `404 unknown_session`, `422 bad_path`, `415 unsupported_type`, `403 outside_folder`, `404 not_found`, `422 not_a_file`, `413 too_large`, `403 not_readable`, `422 bad_cwd`, `422 cwd_missing`, `422 preview_unavailable` (satellite session) |
| `POST /v1/sessions/{key}/queue` | `{client_id, op: steer\|cancel\|edit, text?}` → `200 {ok, client_id, op}` (`edit` adds `text`). `404 unknown_send`, `409 not_waiting` (already sent, steered, removed or failed), `409 not_attached` (steer on an observed session), `409 queue_unsupported` (satellite session), `422 bad_command`, `429 backlog_full` (steer queue full) |
| `POST /v1/issues/resolve` | `{repo: "owner/repo", number, harness, prompt?, context?, model?, effort?}` → `202 {ok, launch_id, state: "pending", note: "preparing worktree"}`; follow it with `GET /v1/launches/{launch_id}`, whose `note` names each step while pending ("Cloning mirror…" on a repo's first run, "Creating worktree quiver/issue-<n>…", "Starting <harness>…"), whose `cwd` is the worktree once made, whose `title` is the name the session gets on adoption (`<owner>/<repo>#<n> <issue title>`), and whose `origin` is the `TaskRef` the launch was for (the launch links to that task in `tasks.json` while pending, the session on adoption). Hub machine only. `400 bad_body`; `422 bad_command`, `not_pinned` (repo not pinned on the hub), `not_launchable`, `launch_failed` (no tmux), `gh_unavailable` (gh missing or signed out; the message says which); `409 unsupported_launch_option` (a `model`/`effort` the harness's launch can't take, see `POST /v1/sessions`); `409 resolve_pending` while another resolve of the same issue is still starting in its worktree |
| `POST /v1/issues/comment` | `{repo: "owner/repo", number, body}` → `200 {ok, github, repo, number, comment: {author, body, created_at, url}, machine, machine_id}`: posts `body` (trimmed) on a pinned repo's issue as the hub's `gh` login, through `swe gh issue comment`'s code with the text on `gh`'s stdin, never argv; then drops the issue's cached reads. Paired phone or owner device only. `400 bad_body`; `422 bad_repo`, `bad_issue`, `bad_comment` (not a string, or blank), `comment_too_long` (over 65,536 characters); `404 not_pinned`; `409 gh_unavailable`, `gh_auth_required`, `gh_upstream_error` (the `gh auth status` check itself failed or timed out); `404 unknown_issue` and `502 comment_failed` carry `gh`'s words |
| `GET /v1/issues` | `{repos: [{name, status, issues: [GhIssue + mine + agent_running?], error?}], machine, machine_id, github, env_blocked?, fetched_at}` — every pinned repo's issues through the shared 60s `gh` cache; `mine` is the hub's `gh` login authoring the row, `agent_running` joins the row when the task store has the issue's task linked to an active session or a pending launch (absent otherwise). `?state=open|closed|all` picks the `gh issue list --state` the rows come from (`open` default; a non-`open` list caches apart from the open slot `tasks.py` shares); anything else is `422 bad_state`. A repo failure keeps its `status`/`error`, `env_blocked` answers before any fetch when `gh` is missing or signed out, `?refresh=1` bypasses the cache |
| `GET /v1/issues/{owner}/{repo}/{n}` | `{github, repo, machine, machine_id, status, issue: GhIssueDetail, error?, fetched_at}` — `GhIssueDetail` carries `branch` (`quiver/issue-<n>`), `worktree` and `suggested_prompt`, what a resolve would start with. `400 bad_repo`, `422 bad_issue`, `404 not_pinned`; a `gh` failure answers `200` with `status`/`issue: null`/`error` |
| `GET /v1/pulls` | `{repos: [{name, status, pulls: [GhPr + mine + review_requested + agent_running], error?}], machine, machine_id, github, env_blocked?, fetched_at}` — every pinned repo's open PRs through the shared 60s `gh` cache; a repo failure keeps its `status`/`error`, `env_blocked` answers before any fetch when `gh` is missing or signed out, `?refresh=1` bypasses the cache. `agent_running` is true when the task store has the PR's task linked to an active session or a pending launch |
| `GET /v1/pulls/{owner}/{repo}/{n}` | `{github, repo, machine, machine_id, status, pr: GhPrDetail + worktree + suggested_prompt, error?, fetched_at}` — `worktree` is where an open checks the PR out, `suggested_prompt` the review brief an open would start with. `400 bad_repo`, `422 bad_pr`, `404 not_pinned`; a `gh` failure answers `200` with `status`/`pr: null`/`error` |
| `POST /v1/pulls/{owner}/{repo}/{n}/open` | `{harness, prompt?, model?, effort?}` → `202 {ok, launch_id, state: "pending", note: "preparing worktree"}`, the same launch shape as an issue resolve: `note` steps "Cloning mirror…" / "Checking out PR #<n>…" / "Starting <harness>…", `cwd` is the detached `refs/pull/<n>/head` checkout under `~/.quiver/worktrees/<owner>__<repo>-pr<n>`, `title` is `<owner>/<repo>#<n> <pr title>` on adoption, and `origin` is the PR's `TaskRef`, as on a resolve. `400 bad_body`; `422 bad_repo`, `bad_pr`, `bad_command`, `not_pinned`, `not_launchable`, `launch_failed`, `gh_unavailable`; `409 unsupported_launch_option`; `409 open_pending` while another open of the same PR is still starting |
| `GET /v1/tasks` | `{tasks: [TaskRow], repos?: [{name, status, error}], github, linear, notion, env_blocked?, machine, machine_id, fetched_at?}` — the hub's work-item store overlaid on the sources' open items (GitHub: every pinned repo's open issues and PRs, the same cache `/v1/pulls` reads; Linear: the configured key owner's assigned issues plus configured teams', via `api.linear.app/graphql`; Notion: the configured databases' rows owned by the configured user, via `api.notion.com/v1`). `TaskRow` is the `TaskRef` fields plus `{id: "source:kind:scope:ref" lowercased, state, sessions: [session keys], launches: [launch ids], agent_running, linked, last_active_at, created_at?, pr?, updated_at?, state_source, status}`; `state` is derived `open` → `in_progress` → `pr_open` → `merged`\|`closed`, a sort and group key that clients never show as text — for an issue, `pr` is the PR on its `quiver/issue-<n>` branch (open or merged), for a pull the PR itself, and a Linear `started` state reads `in_progress` on its own. `agent_running` is true while a linked session is `active` or a linked launch is still pending. `?refresh=1` bypasses the cache. Each source reports its env block: `github` as `gh` plus top-level `env_blocked` when it blocks, `linear` and `notion` each as `{status: disabled\|ok\|auth-required\|bad-config\|upstream-error, login, error}` (`disabled` = no such section in `config.json`, `bad-config` = a malformed setting like a non-list `teams` or a Notion property the database doesn't have; a blocked source still serves its stored links with last-known state). `state_source` says where a row's `state` and `pr` came from: `live` (a source read it this round), `cached` (a stored task no source could read this round, because the source is blocked or down or the task is past the 24 most recently active and off the open list; both are its source's last reported values from `cache/task_states.json`, to show as last known), `links` (a stored task never read, so `state` is inferred from its links alone). `status` is the label clients show, in the source's words: for GitHub, `Agent running` while `agent_running`, then `PR open`, `Merged` or `Closed`, `Draft` for a draft pull, otherwise `Open` (a linked session that has ended reads `Open` again); for Linear, the workflow state's own name (`In Review`), or the ladder's word when no read gave one. A `github` block of `upstream-error` (gh timed out or failed) sets no `env_blocked`: GitHub rows are then the store's last-known ones, and clients say so |
| `POST /v1/sessions/{key}/task` | `{task_ref: TaskRef}` (or the ref itself as the body) → `200 {ok, task_id, sessions}`: links the session to the task in `tasks.json` (0600, hub-side — works for satellite session keys). `400 bad_body`, `422 bad_command`, `404 unknown_session`, `500 store_failed` |
| `DELETE /v1/sessions/{key}/task` | `{task_ref: TaskRef}?` → `200 {ok, unlinked: [task_id]}`: drops the session's link to that task, or to every task without a ref. `404 unknown_session` |
| `GET /v1/events` | global SSE (all sessions + `machine.*`) |
| `GET /v1/inbox` | `{items: [{key, machine_id, harness, agent, title (the display name when set), cwd, status, last_active_at, request_id?}]}`: pending approvals (`status: approval`, any age), plus sessions in `followup`/`error`/`interrupted` active in the last 3 days, minus rows this device dismissed. `?done=1` adds sessions finished since the hub's local midnight as `status: done`. Unread GitHub notifications follow as one group, newest first: `{kind: "github", key: "gh:<thread id>", repo, subject_type: pr\|issue\|ci\|release\|other, title, reason: review_requested\|assignee\|mention\|comment\|ci_fail\|<github's name>, url, gh_thread_id, updated_at, unread: true, status: "github", last_active_at}` — `last_active_at` is `updated_at` in epoch seconds; being unread, these never hit the 3-day cutoff. Mark a thread read with `POST /v1/app/inbox/read` `{"ids": [gh_thread_id]}` |
| `POST /v1/inbox/dismiss` | `{key, last_active_at}` → `200 {ok}`; hides the row for this device until its `last_active_at` moves past the given one — for a `gh:<thread id>` key, until GitHub bumps the thread's `updated_at`. `422 bad_dismiss` without a key or a numeric `last_active_at` |
| `POST /v1/sessions` | `{harness, cwd, prompt, machine?, model?, effort?, yolo?}` → `202 {ok, launch_id, state: "pending", note}` (`queued: true` for a satellite); follow it with `GET /v1/launches/{launch_id}`. `model` and `effort` are optional single tokens (`[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,127}`, so never a leading `-` or whitespace); absent or `""` means the harness's own default. `model` goes on the harness's command line as its harness.json `capabilities.model` declares (`--model`, `-m`, droid's `--settings <file>`, `opencode mini -m <provider/model>`); `effort` is typed as `/effort <v>` (claude). `yolo` is a strict bool: `true` appends the harness's approval-bypass argv as `capabilities.yolo` declares (`--dangerously-skip-permissions`, `--yolo`, `--auto`, ...), absent or `false` runs the usual checks. `422 bad_command` for a bad shape; `409 unsupported_launch_option` for a pick the harness's launch can't take (no `capabilities.model`/`capabilities.yolo`, a bare opencode name, a satellite whose row doesn't list the harness in `model_at_launch`/`effort_at_launch`/`yolo_at_launch`); also `422 not_launchable`, `bad_cwd`, `launch_failed`, `429 backlog_full`. See docs/remote-control.md |
| `GET /v1/machines` | `{machines}`: each row carries `launchable`, `harness_names`, `untracked_harnesses`, `model_at_launch`, `effort_at_launch` and `yolo_at_launch` (harnesses whose launch takes that pick or switch), `recent_models` (`{harness: [model, ...]}`, each launchable harness's recently used models, newest first, at most 5, junk names dropped) and `last_models` (`{harness: model}`, the head of each list) |
| `GET /v1/app/overview?period=&tz=&rescan=1` | the web app's Overview: `{period, time_zone, range: {start_day, end_day} \| null, first_day, totals: {tokens, input, output, cache_read, cache_write, reasoning, cost_usd, unpriced_tokens, unpriced_models}, harnesses: [{harness, tokens, cost_usd, share, models}], days: [{date, tokens, input, output, cached, cost_usd}], quick: {active_days, tokens_per_day, busiest_day}, top_models: [{model, tokens, share, harnesses}], heatmap: {weeks, days: [{date, tokens, cost_usd, level} \| {date, future}]}, scanned_at, notes, machine, first_run}`. `first_run` is true while the hub machine has no shared `~/.quiver/AGENTS.md` (the Setup page's own check), so Overview can offer setup. `period` is `today` \| `7d` \| `month` (default) \| `30d` \| `90d` \| `365d` \| `all`; `tz` is the browser's IANA zone for day boundaries. Built by `ledger/overview.py`; see docs/web-app.md |
| `GET /v1/app/sessions?rescan=1` | the web app's Sessions: `{sessions: [{key, id, harness, agent, title, cwd, project, project_key, last_active_at, status, location, machine_id, machine, remote, resume, ownership, attachable, mode, tmux, kept, model, harness_title, pinned, pinned_at, can_fork, usage, duration_s, trivial, unread}], ledger_harnesses, open_terminal, scanned_at, notes, machine}`. Rows are `/v1/sessions`' catalog; `mode`, `tmux` and `kept` are its supervised fields (empty/false when none); `usage` is `{tokens, input, output, cache_read, cache_write, reasoning, cost_usd, unpriced_tokens, events, first_ts, last_ts, models: [{model, tokens}]}` over all history, joined by `(harness, id)` for hub-machine rows only, else null. `project_key` is the lowercased project, or `other` for hash-like names; `trivial` marks a short or empty session the page hides by default; `ownership` (`attached` \| `observed`) and `attachable` are the catalog's, for the page's chat (docs/web-app/sessions.md). `title`, `harness_title`, `pinned`, `pinned_at` and `can_fork` are the catalog's, as on `GET /v1/sessions` (`pinned_at` is null when unpinned). `unread` is the calling device's flag, as on `GET /v1/sessions`. `open_terminal` is true when the hub can open a terminal on a pane (macOS). Built by `webapp/sessions.py` and `ledger/sessions.py` |
| `GET /v1/app/harnesses` | the web app's Harnesses: `{harnesses: [HarnessRow], window_days, machine}`, the rows of `swe list --scope=all --json` for the hub machine (archived included, `rate_limit` always null; the Accounts page reads limits from `/v1/usage`). `503 registry_unavailable` when the registry can't be read |
| `GET /v1/app/tasks` | the web app's Tasks: the `GET /v1/tasks` answer verbatim — `{tasks: [TaskRow], repos?, github, linear, notion, env_blocked?, machine, machine_id, fetched_at?}` — plus two joins on each `TaskRow`: `session_rows`, its linked session keys joined to catalog rows trimmed to `{key, id?, harness, agent, title, status, cwd, machine, machine_id, remote, ownership, attachable, last_active_at, trivial}` (`{"key", "gone": true}` for a key the catalog lost; a remote row's `machine` is its name from `machines.json`), and `launch_rows`, its linked launch ids joined to live launch views `{id, state: pending\|started\|failed\|untracked, harness, note, key, cwd, machine}` (a launch the hub forgot is left off). `?refresh=1` bypasses the cache as on `/v1/tasks`; `?harnesses=1` answers only `{harnesses: [{name, display_name, tracked, launch_model, launch_effort, launch_yolo, last_model, recent_models}], machine}` (`launch_model`/`launch_effort`/`launch_yolo`: the launch takes that pick or switch; `recent_models`: the harness's recently used models, newest first, at most 5; `last_model`: its head, "" when none) — `catalog.launchable_harnesses`. Built by `webapp/pages/tasks.py`; see docs/web-app/tasks.md |
| `GET /v1/app/<page>` · `POST /v1/app/<page>/<action>` | a web app page plugin (`quiver/webapp/pages/<page>.py`); each payload is documented in `docs/web-app/<page>.md`. `404` for an unknown page or action, `403 bad_origin` on a cross-site POST, `503 page_unavailable` when the page can't read its source |

`message.delta` is live text for a reply still being written (Codex
app-server, opencode serve and headless Claude sessions only; a tmux
session never streams). The envelope has no `seq` and its SSE frame no
`id:` line: deltas are never journaled, never replayed, and must not
move a client's resume cursor. `data` is `{id, kind, text, seq, done}`:

| Field | Meaning |
|---|---|
| `id` | the channel's id for the streamed message (Codex item id, opencode part id, `<Claude API message id>:<block index>`); one bubble per id |
| `kind` | the RemoteMessage kind it will become; `assistant` for now |
| `text` | the cleaned, redacted text appended since this id's last delta (concatenate in `seq` order) |
| `seq` | this id's delta count, from 1; a gap means one was dropped, so stop drawing that bubble and wait for the final message |
| `done` | true on the id's last delta |

The source machine coalesces: at most one delta per id every 100 ms,
plus the `done` one. The final `message.appended` stays the record and
replaces the bubble. The transcript does not carry the channel's id, so
a client matches by order: the next `message.appended` of the same
`kind` in the session replaces the oldest open bubble of that kind, and
later deltas for a replaced id are dropped. A client that does not know
`message.delta` ignores it and sees no change.

`message.appended.data` is a RemoteMessage: `{kind: user|assistant|
thinking|tool_use|tool_result|compaction|system|approval|error, text,
ts, tool: {name, call_id, args_summary, args_full}?, result_ref?,
duration_s?}`. `duration_s` is on `thinking` only: how long the model
thought, in seconds rounded to 0.1, and absent when unknown (clients
then show plain "Thought"). For Claude Code and Codex it is the gap from
the record before the thinking record to the thinking record, because
both write each block as its own record when the block finishes; for
opencode it is the part's own `time` span. It is set when the message is
first emitted and never patched later.
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
`edit` takes a non-empty `text`, replaces the waiting send's text in
place and answers `200 {ok, client_id, op, text}`; it emits no event.
An edit that answers `200` is always the text that goes out.

For sessions owned by a satellite (`key@machine`) the same route returns
the same `202` shape, except `waiting` is always `false` — the satellite
re-reads status and decides for itself.

The CLI side of remote also speaks `--json`:

| Command | `data` |
|---|---|
| `swe remote status --json` | `{hub: {ok, v} or {ok:false}, hub_addr, machines: [{id, name, online, last_seen_at}], devices: [DeviceRow]}`; exits 1 when the hub is unreachable but still emits the envelope |
| `swe remote devices --json` | `{devices: [DeviceRow]}`; `--revoke=<id>` → `{revoked, ok}`; `--requests` → `{requests: [PairRequestRow], auto_approve_owner}`; `--auto-approve=on|off` → `{auto_approve_owner}`; `--approve=<code>` → `{ok, device_id, name, node_name, login}`; `--deny=<code>` → `{ok}`. A code that matches nothing answers `{ok: false}`; `--approve` then exits 1 but `--deny` exits 0, so read `ok`. `--request-id=<id>` holds either to that one request; wrong `--approve` codes count toward the request's five-try lockout, wrong `--deny` codes don't |
| `swe remote agent --status --json` | `{configured, hub, supported, problem, installed, running, service, program, log, hub_installed}`: `configured` is true when `agent.json` holds a hub and a credential (`hub` is that URL, never the credential); `supported` is false with `problem` saying why when this machine has no launchd or systemd user manager; `installed` and `running` describe the satellite's own LaunchAgent or systemd unit at `service`, `program` is its command line (null when not installed); `hub_installed` is true when this machine's hub service is installed, which makes `--install` refuse. `--install --json` and `--uninstall --json` do the action, print its prose and any refusal on stderr, and emit the same report after it (nothing on stdout when they fail) |
| `swe remote pair --json` | `{host, port, token, name, url, deep_link, expires_in_s}` — `url` opens the web app's pair handler (`/#pair=…`), `deep_link` is the `quiver://pair?…` form for the iOS app; `expires_in_s` is the token TTL |
| `swe remote setup --json` | `{ready, platform, url, steps: [SetupStep]}` in order: `tailscale.installed`, `tailscale.signed_in`, `tailscale.magicdns`, `tailscale.https`, `tailscale.cert`, `hub.service`, `hub.reachable`, `phone.paired`. `SetupStep` is `{id, title, state, detail, action}`; `state` is `ok`, `todo` (Quiver does it with `--apply`), `needs_you`, `failed` or `blocked` (waits for an earlier step). `action` is null or `{kind, label, url? , app?, command?}` with `kind` `open` (a URL), `launch` (a macOS app), `command` (to copy), `apply` (run `swe remote setup --apply`) or `pair`. `url` is the https address phones reach, or `""` before MagicDNS. `--apply` emits the report after doing every `todo` it can; `--recheck` fetches the certificate again instead of using the cached result |
| `swe remote notify --json` | `{notify: {url?, topic?, ntfy_token?, enabled, action_credential?}}` with credential fields redacted to `***` |
| `swe remote ssh --json` | `{enabled, user, port, listening, host_keys: [{key, fingerprint}], devices: [{id, name, fingerprint, installed, revoked}]}`; `--on`/`--off` apply first, and `devices` lists only devices that sent a key |

`DeviceRow`: `{id, name, created_at, last_seen_at, revoked}`.

## Shape notes

- `Harness`: `state` is `active` | `starred` | `archived`; `rate_limit` is
  null unless `--usage` and starred; `sessions_100d` decodes through the
  custom key `sessions100D`. `archived` carries `reason`, `archived_at`,
  `usage`, or is null. `no_reading` says why `rate_limit` is null:
  `signed_out` (no credential swe can read with), `error` (credentials,
  but the fetch failed), or null with a reading, without `--usage`, or
  when swe can't tell. Readers that predate it keep showing "no reading";
  treat any other value as null.
- `Harness.issue` (with `--usage`): `{status, remedy}` when the row's usage
  read failed or came back as a sentinel, null on a healthy row. `status` is
  the same vocabulary as `swe harness usage`, finer than `no_reading`.
- `Remedy`: `{kind, title, summary, steps: [str], command?, url?}` from
  `harness/remedies.py`'s `REMEDIES`. `kind` is `relogin`, `run-command`,
  `open-app`, `grant-permission` or `retry`; `command` is a line to copy or
  run in a terminal, `url` a link to open (the macOS privacy pane for
  `grant-permission`). `swe doctor`'s `usage.*` findings carry the same fix.
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
  `path` is the install evidence — the binary on PATH, or for a
  GUI-only provider its `.app` bundle or Homebrew Caskroom record — and
  `installed` follows it. `app` is the provider's `.app` bundle when one
  is on disk; for a provider with its own CLI it stays informational
  (the IDE existing does not mark the command installed), for a
  GUI-only provider it is what made `installed` true. `website` is the
  vendor's download page for providers swe has audited; absent, or
  null, for the rest.
- `McpInventory`: `harnesses` lists every config read; `servers` carry
  `args`, `command`, `conflict`, `detail`, `env_keys`, `harnesses`,
  `header_keys`, `health` (a string, not an object), `name`, `transport`,
  `url`, `variants`.
- `DoctorReport`: `ok` plus `findings` (`check`, `severity`, `message`,
  `fix`, optional `harness`, `action`, `via`). A finding may also carry
  `source` (the file or place behind it, e.g. `~/.claude/settings.json`)
  and `remedies`: the ways out, best first, each `{kind, title, detail?}`.
  `kind` is `action` (swe can run the fix; `action`/`via` name it),
  `agent` (a `prompt` to hand a coding agent), `command` (a `command` to
  copy) or `steps` (manual work, `steps` listed). An `action` remedy can
  carry the target the runner edits: `script` + `settings` + `targets`
  (the missing command tokens, so a working entry that shares the
  basename survives) for `undeclare`, `settings_entries` + `cli_entries`
  for `marketplace-drop`, `id` (the `swe find plugins` drift id) for
  `plugin-fix` and `plugin-reinstall`, `path` (the broken link) for
  `dangling-link`. `fix` stays the one-line summary for clients
  without remedy support. The command may exit nonzero with valid JSON,
  so the client is called with `allowFailure`.
- `swe doctor --fix --json`: `{dry_run, ran, fixed, still_failing,
  skipped}`. `ran` lists each fix as `{action, fix, findings, error}`
  (`findings` are `{check, message, harness?}`; one `setup`, `discover`
  or `init` run covers every finding naming it), `fixed` the findings
  gone afterwards, `still_failing` the full findings in scope still
  there, `skipped` the ones whose only fixes need a click (`actions`
  lists them). Exits 1 when a fix failed or a batch fix is still owed.
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
- `GhPr`: `{number, title, state, draft, author, head_owner, branch,
  base, labels, mergeable, review_decision, checks, updated_at, url}`;
  `head_owner` is the login whose repo holds the head branch ("" when
  `gh` doesn't say — a fork PR names its owner, a same-repo one the
  repo's). `state` is
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
- `TaskRef`: `{source: github|linear|notion, kind: issue|pull|card|page,
  repo?|project?, ref, title, url}` — the normalized work item the
  hub's task store keys on. `repo` (`owner/name`) is required for
  `github`; `project` is the scope elsewhere — for a `linear` issue
  that is its team key (`"QUI"`), and its `ref` is the issue
  identifier (`"QUI-123"`); for a `notion` page it is the configured
  database name, and its `ref` is the page id. A github `ref` is the
  issue/PR number as a string.
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
