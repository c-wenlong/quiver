# Data model

Every persistent store `swe` and the Quiver app touch, what each holds, and
how long its data is trusted. `paths.py` is the single source of truth for
the `~/.quiver` layout; `AppModel.swift` names the app files under `cache/`
and `config/`.

"Rebuildable" means the file can be deleted and regenerated from source data
(transcripts, harness configs, the network). Files marked "authored" cannot
be rebuilt; deleting them loses user state.

## Store inventory

### Authored state (must never be dropped)

| path / key | owner | format | holds | written by | read by |
|---|---|---|---|---|---|
| `config/harness.json` | swe | JSON object keyed by harness name | registry: state (`active`/`starred`/`archived`), install evidence, capabilities, order | `harness/registry.py` only | every swe command, the app via `swe list` |
| `config/config.json` | swe | JSON object | `swe config` values (report settings etc.), secret keys rejected | `configuration.save_config` | `configuration.load_config` |
| `mcp.json` | swe | JSON | hub MCP server definitions, `${NAME}` secret refs | `swe mcp` commands | `swe mcp`, app via `swe mcp list` |
| `config/providers.json` | swe | JSON | provider registry, seeded from built-ins, `_removed` list blocks re-merge | `providers/registry.py` | `swe providers`, doctor |
| `config/skill_catalogs.json` | swe | JSON list | extra skill catalog paths | `swe skills catalog` | `skills/discovery.py` |
| `config/skill_links.json` | swe | JSON `{links, updated}` | recorded harness skill symlinks | `skills` link ops | `swe skills`, `init --check` |
| `config/plugin-display-names.json` | app | JSON object | marketplace display-name overrides | `PluginDisplayNames` (QuiverCore) | plugin explorer, skill detail |
| `accounts/profiles.json` (+ `profiles.lock`, `<vendor>/<id>/home/`) | app | JSON + per-profile home dirs | extra harness accounts the app manages | `ProfileStore` | accounts page, ledger roots, session listing |
| `accounts/harness-names.json` | app | JSON `{schema, names}` | account names for harness cards, app-owned (renaming in Accounts never writes harness.json) | `HarnessAccountNameStore` | accounts cards, menu bar, widgets |
| `secrets/mcp/.api_keys` | swe | shell-style file, mode 600 | MCP credential values (was `secrets/.api_keys`, now a symlink here) | you, by hand | MCP format writers |
| `secrets/providers/.api_keys` | swe + app | shell-style file, mode 600 | LLM API keys (was `~/.api_keys`, now a symlink here; `--api-keys-dir=DIR` may name a directory of per-provider files instead) | app's key editor | `swe providers`, app key checks |
| `data/ledger/ledger.sqlite` | app | SQLite (WAL) | deduplicated token events, dimension rows, scan cursors. Rows outlive their source logs (Claude Code prunes transcripts after 30 days), so this is the only copy of that history: never delete it to "reset" usage. A schema change archives it first (see below) | `Ledger` scans | Overview, Sessions, Calendar, menu bar, widgets |
| `data/ledger/backups/ledger-YYYY-MM-DD.sqlite` | app | SQLite, one file each | a copy of the ledger per local day through SQLite's online backup API, newest 7 kept | `Ledger.keepBackedUp` (hourly check) | restore by hand: quit Quiver, copy one over `ledger.sqlite` |
| `data/web-ledger/ledger.sqlite` | swe (hub) | SQLite (WAL) | the web app's usage ledger: `events` keyed by the same dedup keys as the app's ledger, `files` read cursors, `meta`. Its own file, never the app's `data/ledger/`, so both can run on one Mac. Rows outlive their logs too | `ledger/scan.py`, when a `/v1/app/*` page asks | the web app's Overview (`docs/web-app.md`) |
| `cache/web-ledger/litellm.json` | swe (hub) | JSON | LiteLLM's price list, downloaded at most once a day; the snapshot in `ledger/pricing.json` stands in when it is missing | `ledger/pricing.py` | web ledger pricing |
| `data/search/search.sqlite` | app + swe | SQLite (WAL, FTS5) | per-message transcript text for session search: `sessions` + `files` cursors, `messages`, `messages_fts` (schema version 1 in `meta`). Both writers share one file: each commit is a `BEGIN IMMEDIATE` transaction fenced on the file's `files` row, and swe never touches an index at another schema version | app index writer (every harness), `swe session index` and the remote hub's loop (Claude Code, Codex, Pi, Cursor, Qoder, CodeBuddy, Grok, Kiro and Hermes transcripts, Devin's `sessions.db` plus its `transcripts/*.json` exports, and OpenCode's `opencode.db`, `sessions/search_writer.py`; a session inside a database indexes under the synthetic path `<db>#<id>`) | `swe session --search`, `GET /v1/search`, MCP `get_recent_sessions` (`sessions/search_index.py`) |
| `data/search/indexer.json`, `index.lock` | swe | JSON; empty lock file | whether the hub keeps the index current (`swe session index --on`/`--off`) and the last pass's counts; `index.lock` is an `flock` so only one swe pass runs at a time | `sessions/search_writer.py` | `swe session index --status` |
| `data/catalog/catalog.sqlite` | app | SQLite (WAL) | the model catalog: every price each LiteLLM key has held, Artificial Analysis models and raw feeds, the price-key to AA-model join, and a row per fetch (see below). Price history exists only here: a dropped file reprices past usage at today's rates. `QUIVER_CATALOG_DB` overrides the path | `PricingStore`, `ArtificialAnalysisStore` | ledger pricing, Pricing and Models pages |
| `skills/`, `hooks/`, `plugins/`, `AGENTS.md`, `.linkignore` | swe | files and symlinks | shared instruction/skill/hook/plugin trees | `swe init`, `swe skills`, `swe plugins` | the harnesses that link them |
| `data/account-links.json` | swe | JSON `{version, links: [{path, source, primitive, account}]}` | every link `swe init` made in an extra account home for an `accounts.sync.*` switch; switching one off removes only the links listed here that still point at their `source` | `init/accounts.py` (malformed raises, the pass then touches nothing) | `swe init` |
| `fan/control.json` | app | JSON `{level, owner, requested}`, folder mode 700 | the Compute page's manual fan request: a level from 0 (each fan's minimum) to 1 (its maximum), the requesting Quiver's pid and when it was asked. No level means automatic | `FanControl` (QuiverCore) | the root fan helper (`Quiver --fan-daemon`, run by launchd from `/Library/LaunchDaemons/com.cwenlong.quiver.fan.plist`), which holds the fans only while that pid is a running Quiver. It answers in `/var/run/com.cwenlong.quiver.fan.json` (`{state, reason, requested, owner, at}`, root-owned, mode 644, outside `~/.quiver` so root never writes into a folder the user owns), which `FanControl` reads to show why a request was refused |
| `reports/` | swe | markdown + JSON | generated reports, `session-summaries/`, `exclusions.json`, `cursors.json`, `followups.json` | `reports/store.py` (atomic writes, malformed state raises) | `swe report` |

Legacy registry names `tools.json`, `stars.json`, `archived.json` (pre
`harness.json`) are lazily migrated by `harness/registry.py`; the old
`~/.config/swe` root is moved by `init/migrate.py`. Do not add new state
under those names.

### Caches (rebuildable)

| path | owner | format | holds | TTL / invalidation |
|---|---|---|---|---|
| `cache/session_cache.json` | swe | JSON `{cached_at, sessions}` | all parsed sessions | 60s; `invalidate_cache()`; bypassed with `use_cache=False` (`swe session` itself always reparses) |
| `cache/session_parse_cache.json` | swe | JSON `{version, entries: {"<harness>:<what>": {abs path: [[size, mtime_ns, ctime_ns, ino], value]}}}`, mode 600 | what each transcript (and freebuff `run-state.json`) says on its own: cwd, title, title source, cursor tool paths, claude handoff target | per file: a changed stat signature re-reads it; `version` holds format, package version and a hash of the parser modules, so any code change discards it; entries a harness's parser no longer visits are pruned when it ran; sidecars (codex index and thread store, cursor `meta.json`) are read fresh every run |
| `cache/session_status_cache.json` | swe | JSON `{version, entries: {"<harness>": {abs path: [[size, mtime_ns, ctime_ns, ino], [phase, followup]]}}}`, mode 600 | what each transcript's tail decides for the `swe session` status column (claude, codex, cursor, pi) | per file, like the parse cache; `version` hashes `status.py` and `parse_cache.py`; an entry goes once its file is gone |
| `cache/session_model_cache.json` | swe (hub and satellite catalog) | JSON `{version, entries: {"<harness>:model": {abs path: [[size, mtime_ns, ctime_ns, ino], "<model>"]}}}`, mode 600 | the model of each transcript's newest reply, for the remote session rows' `model` (claude, codex, pi) | per file, like the status cache; `version` hashes `session_model.py`, `recent_models.py`, `protocol.py` and `parse_cache.py`; an entry goes once its file is gone |
| `cache/task_states.json` | swe (hub) | JSON `{states: {"<task id>": {state, pr}}}`, mode 600 | the last state each stored task's source reported (`pr` is the brief an issue's branch PR or a pull's own PR carries), so `GET /v1/tasks` serves a task the sources can't read this round as `state_source: cached` | written only when a fresh source read changes a stored task's `{state, pr}`, never on a quiet poll; a read that couldn't see the PR (the branch lookup failed, or only the open list saw the issue) keeps the remembered `pr` unless it was an open PR the open list no longer shows; a write drops tasks `tasks.json` no longer holds; corrupt or missing reads as empty |
| `cache/session_counts.json` | swe | JSON per window (`"7"`, `"30"`, `"100"`, `"all"`) | session counts + broken parser names | 24h, `SWE_SESSION_COUNTS_TTL`; `invalidate_counts_cache()`; rebuilt on `swe list` |
| `cache/rate_limits_cache.json` | swe | JSON per provider | last limit reading per provider | 300s, `SWE_RATE_LIMITS_TTL`; stale fallback up to 24h; per-provider `updated_at` |
| `cache/claude_usage_cache.json` | swe | JSON `{info, retry_at, credential_fingerprint, fetched_at}` | Claude usage endpoint's last reading + 429 cooldown | reading kept up to 24h; cooldown from `Retry-After` |
| `cache/app-usage.json` (+ `.lock`) | app | JSON `{version: 1, accounts}` | Claude/Codex usage readings, cooldowns, credential fingerprints, last failure per account (`lastFailure: {at, status}`) (no secrets) | 60s poll; flock-shared read-modify-write |
| `cache/app-other-limits.json` | app | JSON `{savedAt, rows, cards, starred, cliInstalled}` | the last `swe list --usage` cards OtherLimitsModel showed | written after each successful load; hydrate at init; corrupt or missing files are ignored |
| `cache/app-limit-alerts.json` | app | JSON | `LimitAlerter` dedupe state (alerted ids, reset timestamps) | pruned per evaluation |
| `cache/api-key-checks.json` (+ `.lock`) | app | JSON `{version: 2, files}` | per-key validity results salted by `apiKeyCheckSalt` | no expiry: kept until the next check, shown only while the key's salted fingerprint still matches; flock-shared |
| `cache/service-status.json` (+ `.lock`) | app | JSON `{version: 1}` | status-page readings per service | per-service refresh intervals in `ServiceStatusService` |
| `cache/skills.json` | app | snapshot v1 | last `swe skills --json` result | restored instantly, refreshed via `swe` |
| `cache/mcp-servers.json` | app | snapshot v1 | last `swe mcp list --json` result | `SnapshotLoader`, 60s stale |
| `cache/plugins.json` | app | snapshot v1 | installed plugin list from `swe find plugins --json` | restored, refreshed via `swe` |
| `cache/plugin-catalog.json` | app | snapshot v2 | `swe plugins catalog` output | 24h (`86400`s); v1 was bundled, never released |
| `cache/sessions.json` | app | snapshot v1 | last session listing shown | `SessionSnapshot` v1; swe asked again when listing is over 60s old |
| `cache/widgets/snapshot.json` | app | snapshot v2 | widget payload: summaries, heatmap, models, limit readings | rewritten on limit refresh; WidgetKit reads it |
| `cache/pricing/litellm.json` | app | JSON | superseded: the old LiteLLM download | imported once into `data/catalog/catalog.sqlite` (dated by its mtime) and left in place; nothing writes it |
| `cache/skills-hub.json` | app | superseded snapshot | old skills-hub payload | deleted at launch by `AppModel`; nothing reads it |

Files nothing in this repo owns but observed in real `cache/` dirs:
`.last-backup-stamp` (external backup tooling), `.DS_Store`, `evals/` and
`.backup/` at the `~/.quiver` root. Left alone.

### UserDefaults keys (app)

All app preferences. Read/write goes through the `Key` enums and `@AppStorage`;
every key below is live.

| key | holds |
|---|---|
| `swePath`, `apiKeysPath`, `mcpServersPath`, `codeEditor`, `terminalApp` | user-set paths and app choices |
| `appearance`, `appIcon`, `showsInDock`, `lastSection`, `settingsSection`, `menuBarPageTab`, `desktopWidgetTab`, `agentsMdView` | window and page state |
| `agentsProjectFolder` | last Agents.md project |
| `limitNotifications` | notification opt-in |
| `menuBarVisible`, `menuBarPrimary`, `menuBarSecondary`, `menuBarWatchedLimit` | menu bar display |
| `popupShowIncidents`, `popupShowStats`, `popupHiddenSources`, `popupHiddenSourceWindows`, `popupSourceOrder` | menu bar popup content and order |
| `popupHiddenHarnesses`, `popupHiddenWindows`, `popupHarnessOrder` | legacy popup keys, read-only migration fallbacks; keep until the new keys have covered every install |
| `mcpHiddenHarnesses` | harness columns hidden on the MCP page |
| `providerOrder`, `providerDisabled` | provider list order and switches |
| `computeTrendInterval`, `computeTrendTab`, `computeMemoryLimitGB`, `computeOrphanAgeMinutes`, `fanManualLevel`, `fanThresholdEnabled`, `fanThresholdCelsius`, `fanReleaseBelowCelsius`, `computeTemperatureLines` | Compute page preferences (`computeTrendTab` also takes `temperature`; `computeTemperatureLines` lists the Temperature tab's switched-on lines, default `cpu`, `gpu`, `ssd`, `battery`) |
| `refreshFreshMinutes` | the "Refresh only what failed for" window (default 5, clamped to 1..120) |
| `customStatusServices` | user-added status services |
| `apiKeyCheckSalt` | salt for cached key-check results (not a secret itself) |
| `widgetHarnesses.summary`, `widgetHarnesses.heatmap`, `widgetHarnesses.models` | per-widget harness picks |
| `widgetLimitsHiddenSources`, `widgetLimitsHiddenSourceWindows` | per-widget limit filters |
| `widgetLimitsSourceOrder`, `widgetSummaryShowsTotal` | limits source order; summary widget's total line |
| `migratedCursorAutoToIncluded` | one-time marker: Cursor `auto` rewritten to `included` |

## Ledger schema (`data/ledger/ledger.sqlite`, schema version 1)

Rows hold token counts, not dollars: `PricingCatalog` prices them at query
time, at the rate each event's price entry had when it happened (names
resolve through today's list; rates come from the catalog's history). `msg`, `head_hash` and
`tail_hash` are FNV-1a 64-bit hashes (`StableHash`), never raw text.

- `meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`: the `schema` marker
  plus generic key-value cursors for non-file sources (SQLite databases).
- `dims(id, harness, account, session, cwd, model, tier,
  UNIQUE(harness, account, session, cwd, model, tier))`: every repeated
  string lives here once; events reference `dims.id`.
- `events(key INTEGER PRIMARY KEY, msg INTEGER, sidechain, dim REFERENCES
  dims(id), ts REAL, input, output, cache_read, cache_write,
  cache_write_1h, reasoning, cost REAL)`: one row per deduplicated response.
  `key` is `StableHash` of the response id; `msg` dedupes retried writes of
  the same message; `cost` is the log's own dollar figure when it has one.
- `cursors(path TEXT PRIMARY KEY, inode, size, modified REAL, offset,
  head_hash, head_length, tail_hash, tail_length, state BLOB, skipped)`:
  per-file scan position; a scan commits events and cursor in one
  transaction so a crash cannot double-count.

Indexes: `events_ts(ts)`, `events_msg(msg) WHERE msg IS NOT NULL`, plus the
`dims` UNIQUE index and the `path`/`key` primary keys. Every hot query is
covered: range scans use `events_ts`, the dims join is a rowid lookup, and
`ORDER BY ts, key` is satisfied by `events_ts` since rowid is the implicit
last index column. `GROUP BY d.*` uses a temp b-tree, unavoidable without
denormalizing.

```
groups()          SEARCH e USING INDEX events_ts (ts>? AND ts<?) + PK join + temp b-tree group
events(in:)       SEARCH e USING INDEX events_ts (ts>? AND ts<?) + PK join
selectByMessage   SEARCH events USING COVERING INDEX events_msg (msg=?)
selectDim         SEARCH dims USING COVERING INDEX sqlite_autoindex_dims_1 (all six cols)
cursor(for:)      SEARCH cursors USING INDEX sqlite_autoindex_cursors_1 (path=?)
MAX(ts)           SEARCH events USING COVERING INDEX events_ts
```

### Migration history

- v1: initial schema (dims / events / cursors / meta). No v0 file ever
  shipped, so no migration exists.
- Location: builds before the move kept the ledger in `cache/ledger/`.
  `LedgerLocation.prepare` copies it to `data/ledger/` at launch through the
  backup API (rows still in the WAL included), checks every table's row
  count, renames the copy into place and only then deletes the old files.
  It holds the old file in an exclusive lock throughout, so while another
  Quiver has it open the move waits for a later launch. A failure leaves the
  old rows untouched, opens the old file where it is, and tries again next
  launch. When `data/ledger/ledger.sqlite` exists already the old one is
  never read. `ledger.schema-*.sqlite` archives move at every launch (a
  `.N` suffix when the name is taken), so one that failed is retried.
- Versioning lives in `meta.schema`, not `user_version`. A mismatch rebuilds
  the tables (the ledger is a cache of logs) but first archives the old file
  via `VACUUM INTO` to `ledger.schema-<version>.sqlite` beside it (a `.N`
  suffix is appended when the name is taken), so history whose logs are gone
  stays recoverable. A future bump should still prefer a real `ALTER`
  migration over the rebuild.

## Catalog schema (`data/catalog/catalog.sqlite`, schema version 1)

`CatalogDatabase` owns it; one connection behind a lock.

- `fetches(id, source, url, fetched_at, status, error, content_sha,
  row_count, rate_limit_headers)`: every download attempt. `source` is
  `litellm` or `aa:<endpoint>`. A failure deletes the failures recorded
  since that source's last success, so an outage is one row.
- `prices(key, valid_from, valid_to, input, output, cache_read, cache_write,
  cache_write_1h, reasoning, entry, fetch_id, PRIMARY KEY(key, valid_from))`:
  one row per price a LiteLLM key held, USD per token; `entry` is the
  entry's price fields as sorted-key JSON. A row is added only when those
  fields change, and `valid_to` is null on the row in force. A key that
  leaves the list has its row closed.
- `models(id, position, slug, name, creator_*, aa_price_*, speed, ttft,
  ttfat, evaluations, raw, fetch_id)`: AA's language models in AA's order.
- `model_aliases(name, model_id, method)`: every price key and the AA model
  it names (`method` is `auto`, `manual` or `none`). Derived: rebuilt from
  `ModelMatcher` and `config/model-links.json` at launch and after any
  refresh or link. `prices.key` references it, so `prices JOIN
  model_aliases` lists every priced key of one model.
- `payloads(source, fetch_id, body)`: each AA endpoint's last good body.
- `meta(key, value)`: the `schema` marker.

Builds before the catalog kept `cache/pricing/litellm.json` and
`data/artificial-analysis/<endpoint>.json` + `meta.json`. They are imported
once (the price list per source, AA per endpoint, so an interrupted import
resumes) and left in place.

## JSON formats and versions

| file | envelope | notes |
|---|---|---|
| Snapshot stores (`skills.json`, `mcp-servers.json`, `plugins.json`, `plugin-catalog.json`, `sessions.json`, `widgets/snapshot.json`) | `{version, savedAt, ...}` via `SnapshotFile` | decoded only when `version` equals the type's `snapshotVersion`; missing, malformed or mismatched files read as absent; atomic private writes |
| `app-usage.json` | `{version: 1, accounts: {id: {reading, cooldownUntil, fingerprint, lastFailure?}}}` | fingerprints are SHA-256 of credentials; `lastFailure` is `{at, status}` and absent on success and in files from before it existed; no secret values |
| `app-other-limits.json` | `{savedAt, rows, cards, starred, cliInstalled}` | no version; decode failure reads as absent |
| `api-key-checks.json` | `{version: 2, files: {path: {salt, entries}}}` | version 1 reads as empty |
| `service-status.json` | `{version: 1, ...}` | readings and incident state per service |
| `session_cache.json` | `{cached_at, sessions}` | 60s |
| `session_parse_cache.json` | `{version: "<format>:<package version>:<source hash>", entries}` | per-file stat signature; a version mismatch, unreadable file or wrong-shaped entry reads as a miss |
| `session_status_cache.json` | `{version: "<format>:<package version>:<source hash>", entries}` | per-file stat signature; a version mismatch, unreadable file or wrong-shaped entry reads as a miss |
| `session_model_cache.json` | `{version: "<format>:<package version>:<source hash>", entries}` | per-file stat signature; a version mismatch, unreadable file or wrong-shaped entry reads as a miss |
| `session_counts.json` | `{windows: {<days>: {cached_at, counts, broken}}}` | 24h |
| `rate_limits_cache.json` | `{cached_at, limits, updated_at, no_data: {<provider>: ts}, no_data_status: {<provider>: "signed_out" \| "error"}}` | 300s, per-provider; a `signed_out` entry is refetched every call |
| `claude_usage_cache.json` | `{info, retry_at, credential_fingerprint, fetched_at}` | cooldown + last good reading |
| `harness.json` | `{<name>: {state, ...}}` | the registry; only `registry.py` writes it |

## Caching map

| fetch | cache | TTL / interval | coalesced? |
|---|---|---|---|
| `swe list --json` (provider detection) | `ProviderVisibilityModel` memory | once per swe path; activation rechecks throttled to 120s | yes, `isLoading` + generation |
| `swe list --usage --json` | `UsageModel` memory + `app-usage.json` disk | 60s poll | yes |
| `swe list --usage --exclude` (other providers) | `OtherLimitsModel` memory | 300s poll | yes |
| swe rate-limit fetches (Claude/Codex/...) | `rate_limits_cache.json` + `claude_usage_cache.json` | 300s, per-provider deadlines, negative caching | threads, one run per invocation |
| `swe session --json` | `sessions.json` snapshot + `SessionsModel` inflight task | listing reused under 60s; swe itself always reparses (freshness wins over the aggregator cache) | yes, shared inflight |
| swe session parsing | `session_cache.json` (`swe list` only) + `session_parse_cache.json` (every listing, per file) + `session_status_cache.json` (status column, per file) | 60s disk / until the file changes | n/a (one process) |
| `swe session` counts | `session_counts.json` | 24h per window | n/a |
| `swe skills --json` | `skills.json` snapshot + model | reload on visit/edit | yes |
| `swe find plugins --json` | `plugins.json` snapshot | reload on visit/edit | yes |
| `swe plugins catalog --json` | `plugin-catalog.json` | 24h | `SnapshotLoader` joins in-flight loads |
| `swe mcp list` / `mcp status` | `mcp-servers.json` | 60s via `SnapshotLoader` | yes |
| `swe doctor` | `DoctorModel` memory | page visit + manual refresh | generation guard |
| `swe init --check` | `AgentsModel` memory | page visit; kept current because link states change outside the app | generation guard |
| status pages | `service-status.json` + `ServiceStatusService` | per-service intervals; `ensureServices` registers once | yes, per-service tasks |
| Claude/Codex usage APIs | `app-usage.json` + model memory | 60s; cooldowns honored across processes via flock | yes |
| API key checks | `api-key-checks.json` | 900s, salted | flock-shared |
| ledger aggregates | `OverviewModel` memory summaries | per period, rebuilt after scans | n/a |
| ledger scans | `cursors` table | incremental by file offset; unchanged files skipped | n/a |
| git status for MCP folders | `McpServerRepoModel` memory | per page visit, 4 concurrent | ticket guard |
| GPU + process samplers | `sampledAt` reuse | half the trend interval, plus 3s on-page refresh | shared sampler |
| login shell environment | `LoginShell.cachedEnvironment` | once per launch | single shared task |
| pricing catalog | `data/catalog/catalog.sqlite` + bundled fallback | daily, from the last good fetch's time | n/a |
