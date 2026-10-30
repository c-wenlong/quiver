# Backend architecture

How `src/quiver` is organised: the controller / service / model split inside
each domain package, the bottom layer, and where every `--json` payload is
defined. For the data model itself (stores, caches, schemas) see
[docs/data-model.md](data-model.md); for the app's view of the JSON shapes
see `docs/json-contracts.md` on the frontend audit branch.

## The convention

Every domain package (`harness`, `sessions`, `mcp`, `plugins`, `skills`,
`providers`, `find`, `init`, `reports`, `setup`) keeps three kinds of module:

- **controller** — `commands.py` (or `*_commands.py`, `cli.py` for mcp):
  argv parsing through `expand_value_flags`, presentation (tables, colours,
  `--json` output), exit codes. No business logic: a controller gathers
  service results and renders them.
- **service** — the logic modules: reading and writing state, scanning the
  machine, calling harness CLIs, computing results. They never print;
  `setup/wizard.py` is the documented exception because it is an
  interactive flow. Diagnostics that must surface go to stderr through a
  helper the controller owns.
- **model** — `models.py`: dataclasses or TypedDicts for the domain's
  records AND for the `--json` payloads the app reads. Every `--json`
  emitter builds its payload from these models (one `to_json()` /
  `as_dict()` per model), so each JSON shape has exactly one definition.

An example per layer, from the harness domain:

- controller: `harness/commands.py::cmd_list` parses flags, calls services,
  prints the table or `emit({"harnesses": [...]})`.
- service: `harness/doctor.py::collect_findings` walks the path-health
  probes and drift checks and returns `DoctorFinding` models.
- model: `harness/models.py::HarnessRow.of(...).to_json()` is the only
  place the `swe list --json` row shape exists.

## The domain map

| package | controller | service | model (`--json` payloads) |
|---|---|---|---|
| `harness/` | `commands.py`, `discover_commands.py` | `registry.py`, `stars.py`, `archive.py`, `catalog.py`, `columns.py`, `tools.py`, `path_health.py`, `doctor.py`, `drift.py`, `rate_limits.py`, `discover.py` | `models.py`: `HarnessRow`, `RateLimitInfo`, `DoctorFinding`, `DoctorReport`, `StateReply`, `ArchiveReply`, `EditReply`, `DiscoveryRow` |
| `sessions/` | `commands.py` | `aggregator.py`, `parse_cache.py`, `parsers.py`, `engines/`, `identity.py`, `status.py`, `query.py`, `picker.py`, `resume.py`, `usage.py`, `models_analytics.py`, `failures.py` | `models.py`: `Session`, `SessionRow`, `ResumeCmd` |
| `mcp/` | `cli.py`, `discover_commands.py`, `gateway_commands.py` | `inventory.py`, `formats.py`, `secrets.py`, `codex_io.py`, `discover.py`, `server.py`, `gateway.py`, `gateway_server.py` | `models.py`: `ServerRow`, `DiscoveryRow` |
| `plugins/` | `commands.py` | `actions.py`, `catalog.py` | `models.py`: `ActionResult` |
| `skills/` | `commands.py`, `layout_commands.py`, `catalog_commands.py` | `layout.py`, `link_ops.py`, `inventory.py`, `discovery.py`, `catalog_discover.py`, `catalogs.py`, `symlinks.py`, `help_text.py` | `models.py`: `LinkReply`, `UnlinkReply`, `MoveReply`, `CatalogRow` |
| `providers/` | `commands.py`, `cli.py` | `keys.py`, `registry.py`, `defaults.py`, `discover.py` | (no `--json` yet) |
| `find/` | `commands.py`, `browser.py` | `roots.py`, `plugins.py`, `mcps.py`, `plugin_drift.py`, `entries.py`, `tree.py`, `summary.py`, `highlight.py` | `models.py`: `DriftRow` |
| `init/` | `commands.py` | `layout.py`, `manage.py`, `migrate.py`, `hooks.py` | `models.py`: `LinkRow` |
| `hooks/` | `commands.py` | `inventory.py`, `settings.py`, `create.py`, `contracts.py` (per-harness hook contracts + registry overrides), `shared.py` (shared-hook deploy: events headers, generated wrappers, `.disabled`, pruning), `shim_runtime.py` (stdlib-only payload translator copied to `~/.quiver/hooks/_runtime/`) | `models.py`: `Declaration`, `HookRow`, `HookDrift` |
| `reports/` | `commands.py` | `pipeline.py`, `transcripts.py`, `triage.py`, `batching.py`, `runners.py`, `work.py`, `followups.py`, `prompts.py`, `store.py` | `models.py`: report records |
| `setup/` | `commands.py`, `wizard.py` | (wizard is the whole domain; allowed to print) | `models.py`: `HarnessPreview`, `McpPreview`, `SkillsPreview` |

## The bottom layer

Six modules never import a domain package, ordered among themselves:

1. `console.py` — colours, sanitise, ANSI-aware width helpers. Imports nothing.
2. `keys.py` — the shared raw-mode key reader. Imports nothing.
3. `flags.py` — `expand_value_flags`, the `--name=value` normaliser. Imports nothing.
4. `paths.py` — every path under `~/.quiver`. Takes only `quiver.__init__` constants.
5. `table.py` — the declarative renderer. Imports `console`.
6. `configuration.py` — `ConfigurationError` and config plumbing. Imports `paths`.

`json_output.py` sits with them in practice: `take_json_flag`, `emit` (the
`{"schema": 1, "data": ...}` envelope) and `error_stream(json_view)`, which
sends refusals to stderr under `--json` so stdout stays a clean document.

`cli.py` is dispatch only: `COMMANDS` maps the first argv token to a
`cmd_*` function. One deliberate module-level cycle, `harness <-> sessions`,
uses function-local imports.

## The `--json` payload models

`emit()` wraps `data` in the envelope; the shape of `data` belongs to the
domain's `models.py` (a `to_json()` per model) or, for a handful of whole
documents, the service function that builds them:

| command | model / builder |
|---|---|
| `swe list --json` | `harness/models.py::HarnessRow` (`rate_limit` is `RateLimitInfo.to_json()`) |
| `swe harness star / archive / edit --json` | `StateReply`, `ArchiveReply`, `EditReply` |
| `swe doctor --json` | `DoctorFinding`, `DoctorReport` (assembled by `harness/doctor.py::collect_findings`) |
| `swe session --json` | `SessionRow` (+ `ResumeCmd` via `sessions/resume.py::resume_json`) |
| `swe skills link / unlink / move --json` | `LinkReply`, `UnlinkReply`, `MoveReply` |
| `swe skills --json`, `swe skills hub --json` | `skills/inventory.py::installed_skills`, `hub_plugins` |
| `swe skills discover --json` | `CatalogRow` |
| `swe init --check --json` | `init/models.py::LinkRow` |
| `swe mcp list / status --json` | `mcp/models.py::ServerRow` (built by `mcp/inventory.py::server_inventory`; `status` adds `health`/`detail`) |
| `swe mcp discover --json` | `mcp/models.py::DiscoveryRow` |
| `swe mcp sync --json` | report dict built by `mcp/cli.py::_sync` |
| `swe find plugins --json`, `--fix=<id> --json` | `find/models.py::DriftRow` |
| `swe find skills -r --json` | `find/commands.py::_skills_json` |
| `swe plugins catalog --json` | `plugins/catalog.py::build_catalog` |
| `swe plugins install/enable/disable/uninstall --json` | `plugins/models.py::ActionResult` |
| `swe harness discover --json` | `harness/models.py::DiscoveryRow` |
| `swe setup --json` | `setup/models.py::{Harness,Mcp,Skills}Preview` |

The discovery and setup payloads keep their findings under a `findings`
key (or the `harness` / `mcp` / `skills` preview keys for `swe setup`),
and an `--apply` run adds an `applied` object inside the same `data`, so
every `--json` command emits exactly one `{schema, data}` document.

## Extending

- **New command:** add a `cmd_*` to the domain's controller, register it in
  `cli.py::COMMANDS` (or the domain's sub-dispatch), then update
  `completion.py` (`_PRIMARY_COMMANDS`, `_TOOL_TARGET_COMMANDS`,
  `_COMMAND_FLAGS`, `_SUBCOMMANDS`, the `_NESTED_*` tables) and
  `help_text.py` (`COMMAND_CATEGORIES` + a `HELP` entry). `swe doctor`
  fails on drift between the three. Flags take `--name=value`, parsed with
  `expand_value_flags`; boolean membership checks run on raw argv before
  expansion.
- **New `--json` command:** put a model in the domain's `models.py` with a
  `to_json()` and emit it through `json_output.emit`. Refusals print to
  `error_stream(json_view)`, never stdout.
- **New domain:** a package with the three layers above. Register its
  `cmd_*` in `cli.py`, its help in `help_text.py`, its completion tables in
  `completion.py`.
- **New session parser:** a `parse_<tool>()` in `sessions/parsers.py`
  returning a `*ParserConfig`, plus the tool in `sessions/identity.py`.
  Parser crashes are recorded by `sessions/failures.py`.
- **New rate-limit fetcher:** a function returning `RateLimitInfo`
  (from `harness/models.py`) registered via `rate_limits.register`. Only
  starred harnesses are polled; cache TTL 300s (`SWE_RATE_LIMITS_TTL`).
- **New MCP config format:** subclass `McpFormatHandler` in `mcp/formats.py`,
  `register_format_handler(name, handler)`.
- **New table:** `table.py` kinds `text`, `number`, `count_threshold`,
  `list`, `timestamp`, `preformatted`; custom kinds via `@register_kind`.
- **Interactive input:** `prompt.read_line()` (restores TTY cooked mode).
- **Terminal key readers:** `keys.py::read_key` only; never hand-roll.

## Known remaining violations

- `mcp/cli.py` is still both the domain's controller and its de-facto
  service module: `get_tool_servers`, `server_type`, `server_summary`,
  `get_mcp_handler`, `convert_server_for_target`, `load_json`/`save_json`
  and friends are imported by `mcp/discover.py`, `find/mcps.py`,
  `completion.py` and tests. Splitting them into a `mcp/servers.py`
  service module is a larger move left for a dedicated PR.
- `harness/rate_limits.py` fetchers print diagnostics straight to stderr
  (401/429 hints, CA warnings). Tests call the fetchers directly and
  assert on that stderr, so the print stays put; routing it through a
  controller-owned diagnostic list is the eventual fix.
- `find/commands.py` still holds the `swe find skills/plugins --json`
  document assemblers (`_skills_json`, `_plugins_json`); the rows are
  models, the document assembly could move to a `find/reports.py`.
- Functions over ~80 lines remain in the bigger controllers and service
  modules (`cmd_list` in harness, `_sync` and `cmd_status` in mcp,
  `cmd_init`, `cmd_setup`, `cmd_session`, `build_catalog`, `plugin_drift`,
  `get_all_rate_limits`, the larger fetchers). They are sequential
  flag-then-render flows; split them as they are next touched rather than
  in one sweep.
