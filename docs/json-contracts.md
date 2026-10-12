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
| `swe doctor --json` | `DoctorReport` (`allowFailure`) | `swe-doctor.json` |
| `swe session <limit> [--days=N] --json` | `SessionListPayload` | `swe-session.json` |

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
  Devin `1d`/`7d` plus `extra_usage_usd`.
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
