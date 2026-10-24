# MCP servers

The Mac app's MCP servers page (`McpServersPage`, `McpModel`,
`McpServerRepoModel`) in the web app, for the machine the hub runs on.
Its writes are a server's Sync, with its Preview, and its Remove.

## What it shows

- The header's **Check health** button and Refresh. Health is never
  probed on a page load: only the button asks for it, and Refresh keeps it
  on once a list with health is showing, as in the app.
- The **All / Out of sync / Conflicts** tabs with their counts, and the
  **Compare** chips. A harness with no servers gets no chip or column; the
  chips that are off are left out of the counts, the conflict letters and
  "Missing from", and are remembered in this browser's `localStorage`
  (`quiver.mcpHiddenHarnesses`).
- The server × harness grid, in two sections. **Local** is every server
  whose command or an argument is a path inside a folder in
  `~/.quiver/mcp/servers`, plus the folders no server uses ("Not in the
  hub"), sorted by name; each shows the folder name and its git branch,
  ahead/behind counts and a Local changes badge. **Remote** is everything
  else in swe's order. A cell is a tick when the compared harnesses agree,
  a letter (A, B, …) per distinct definition when they don't, and a dash
  when the harness lacks the server. A section folds after ten rows. A
  warning triangle marks a server whose hub definition has a `${NAME}`
  with no value; a dot shows each server's health once checked.
- Click a row for its sheet: transport, health detail, the command line or
  URL, env var and header **names**, which harnesses hold each definition,
  where it is missing, the `${NAME}`s that need values (with the `swe mcp
  secret set` hint), and **Sync**.
- **Sync** is the Mac sheet's: **Copy from** (each definition's first
  harness, plus the Quiver hub when `mcp.json` holds the server), then a
  switch per harness that lacks the server (on by default for compared
  harnesses that use MCP) and per harness with a different definition
  ("replaces its own definition", always opt-in). **Preview** shows swe's
  sentence ("Would add to claude. Would update codex.") and, per harness,
  the file it writes, the command line it gets (and the one it loses when
  replaced), the env var and header names it gains (+), loses (−) or whose
  value changes (~), and the harness's own keys it keeps (`disabled`,
  `autoApprove`, …). **Sync** opens an inline confirm that lists every
  file it will write and, when a definition is replaced, says whose ("Replace
  their definitions?"); confirming writes exactly the previewed plan.
  Changing a switch or the source drops the preview. While a `${NAME}` the
  hub copy uses has no value, Preview and Sync from the hub are off with
  "Set the values above first.", and the hub refuses them too.
- Below the grid: the harnesses with no servers, and the note that env and
  header values never leave swe. Then the servers folder's state when the
  scan has nothing to list (missing, empty, or unreadable).

## Payload

`GET /v1/app/mcp` (add `?health=1` to probe):

```json
{
  "harnesses": ["claude", "cursor", "codex"],
  "servers": [{
    "name": "linear", "transport": "stdio",
    "summary": "node ~/srv/linear-mcp/build/index.js --token …",
    "env_keys": ["LINEAR_API_KEY"], "header_keys": [],
    "harnesses": ["claude", "codex"], "conflict": true,
    "variants": [["claude"], ["codex"]],
    "secrets_missing": ["LINEAR_API_KEY"], "in_hub": true,
    "health": "ok", "detail": null
  }],
  "health": true,
  "error": null,
  "folders": {
    "root": "~/.quiver/mcp/servers", "exists": true, "error": null,
    "items": [{"name": "linear-mcp", "group": "custom",
               "path": "~/.quiver/mcp/servers/custom/linear-mcp",
               "git": {"branch": "main", "ahead": 0, "behind": 1,
                       "has_changes": true, "is_repo": true},
               "used_by": ["linear"]}]
  },
  "machine": "home-server"
}
```

`servers` is `swe mcp list --json`'s rows from `server_inventory`, built in
process with the registry read without its migration write, with
`command`, `args` and `url` replaced by `summary`. `health` and `detail`
appear only with `?health=1` (`swe mcp status --json`'s values). `error`
is set when the harness configs could not be read; the folders are still
listed then, as in the app.

### Sync

`POST /v1/app/mcp/preview` and `POST /v1/app/mcp/sync`, both with:

```json
{"server": "linear", "source": "claude",
 "targets": ["cursor", "codex"], "replace": ["cursor"],
 "fingerprint": "…"}
```

`source` is a harness or `quiver` (the hub); `targets` are harnesses from
the page's own list; `replace` names the targets whose own definition the
user agreed to replace. `fingerprint` (sync only) is the preview's. Both
answer:

```json
{
  "server": "linear", "source": "claude",
  "from": {"transport": "stdio", "summary": "node /srv/linear.js --token …",
           "env_keys": ["LINEAR_API_KEY"], "header_keys": []},
  "fingerprint": "9f2c…",
  "report": {"source": "claude", "dry_run": true, "selected": ["linear"],
             "targets": [{"name": "codex", "added": ["linear"], "updated": [],
                          "skipped": [], "pruned": []}, …]},
  "harnesses": [{
    "harness": "cursor", "action": "replace", "outcome": "replace",
    "path": "~/.cursor/mcp.json", "creates": false, "unverified": false,
    "before": {"transport": "stdio", "summary": "node /old/linear.js", …},
    "after": {"transport": "stdio", "summary": "node /srv/linear.js --token …", …},
    "command_changes": true,
    "env": {"added": ["LINEAR_API_KEY"], "removed": ["OLD_KEY"], "changed": ["DEBUG"]},
    "headers": {"added": [], "removed": [], "changed": []},
    "kept": ["disabled"]
  }],
  "files": ["~/.cursor/mcp.json", "~/.codex/config.toml"],
  "error": null
}
```

`report` is `McpSyncReport` (`swe mcp sync … --no-interactive --json`),
two runs merged as `McpModel.sync` merges them. `outcome` is what swe's
report says happened to that harness (`add`, `replace`, `skip`, or
`not_run` when an earlier call failed). A sync's answer adds `page`, the
`GET /v1/app/mcp` payload after the write, which the page repaints from
(keeping the health it already had).

How it runs:

- Preview calls `mcp/cli.py`'s `_sync` in process, exactly as the Mac
  app runs swe: `_sync([source, *adding, "--only=<name>",
  "--no-interactive", "--dry-run"])` for the harnesses that lack the
  server, then the same with `--force` for the ones whose definition is
  replaced. `--only` gets the name as a glob that matches only itself
  (`McpServer.onlyPattern`). `_sync`'s printing is captured, never shown,
  and its last line becomes the error when it refuses.
- Sync writes the snapshot the fingerprint was checked against, with
  `_sync`'s own per-target steps (`convert_server_for_target`,
  `_merge_format_keys` for a replace, `get_tool_saver`), adds first. It
  does not call `_sync` for the write, because `_sync` reads the source
  again, once per call, and an edit landing after the check would reach a
  target unpreviewed. Each target is read again just before its save and
  left alone if its entry changed.
- Everything is checked against what the server reads itself, fresh, under
  one lock: the server must be in the inventory (404 `unknown_server`),
  the source one of its sources (422 `bad_source`), each target a harness
  the page lists (422 `unknown_harness`), and still what the page showed:
  a target to add must still lack the server and a target to replace must
  still have its own (409 `target_changed`). A name with a comma can't be
  synced alone (422 `comma_name`, as the app says). Bad shapes are 422.
- **The fingerprint.** Preview returns an HMAC-SHA256, keyed per hub
  process, over the source's definition and each target's current
  definition, action and config path. Sync recomputes it and refuses with
  409 `plan_changed` when anything differs, so a config edited between
  Preview and Sync (or a hub restart) asks for a new preview instead of
  writing a plan nobody saw. The page's Sync button always previews first
  when the picks changed, so its confirm lists what is on disk now.
- **`${NAME}` with no value.** From the hub, a server whose `${NAME}` has
  no value in `~/.quiver/secrets/mcp/.api_keys` is refused with 409
  `secrets_missing` ("Not syncing: no value for NOTION_TOKEN (used by
  notion). Set it on the hub with: swe mcp secret set NOTION_TOKEN"),
  before `_sync` runs, and `_sync` refuses it again on its own. Nothing is
  written as a literal `${NAME}`.
- A failure part way (a config that won't parse or save, or a target that
  changed mid-sync) stops there and comes back with `error` set, and the
  report lists only the targets that were saved (the rest read
  `not_run`); if nothing was saved it is a 409 `sync_refused`. The page reads the list again after
  any failed sync.

### Remove

The sheet's footer has **Remove…** (red text) on the left, as the app's
does. It opens a confirm above the footer: "Remove demo? It goes from
Claude Code, Quiver hub on box." with the command, `swe mcp remove demo
--from=claude,quiver`, and a red Remove. On success the sheet closes and
the list repaints from the answer, with "Removed demo from Claude Code,
Quiver hub." above it until the list is read again; a refusal stays in
the sheet and the list is read again.

`POST /v1/app/mcp/remove` with `{"server", "from": [places]}` (`quiver`
is the hub) runs `mcp/cli.py`'s `remove_places` and `remove_server`, the
two halves of `swe mcp remove <name> --from=<places>`, under the sync
lock. `from` is what the confirm listed: every place must still hold the
server (409 `state_changed` otherwise), and a place it has reached since
is left alone, because the confirm didn't name it. 404 `unknown_server`,
422 `bad_server` / `bad_from` for bad shapes. The answer is `{"server",
"removed", "absent", "page"}`, `page` being the list after the write.

## Security

- Env and header values are never in the payload; swe sends only their
  names. A sync's preview compares values on the hub and sends only which
  names change. The command lines in a plan are swe-scrubbed (known secret
  values swapped back to `${NAME}`) and then redacted like `summary`.
- swe scrubs the credentials it recognises from commands, arguments and
  URLs. The page module then redacts again with a port of the app's
  `McpRedaction` (secret flags, header values, env assignments, URL
  userinfo, query values, fragments, key-shaped words and path segments),
  and sends only that one line, so an unrecognised token stays on the hub.
- The health probe is `swe mcp status`'s: a command is looked up on PATH,
  never started; a URL gets one HEAD request with a 5-second timeout.
  Probes run eight at a time and the whole check is capped at 30 seconds;
  a probe still running then reads "unknown, check timed out".
- Git runs `git status --porcelain=v1 --branch` per checkout with optional
  locks off, so a read never rewrites a repository's index; four at a
  time, five seconds each.

## Differences from the Mac app

- **Sync and Remove are the writes.** Add server and pasting a secret
  value stay in `swe mcp add|secret set` and the Mac app; the sheet shows
  the names that need values with the `swe mcp secret set` hint instead of
  paste fields.
- **Remove's confirm is inline** and names the command; the app's is a
  dialog. The web page also says what it removed after the sheet closes.
- **Preview says more.** The Mac app's Preview is swe's one sentence; the
  web page adds the per-harness plan (files, command line, key names
  gained, lost or changed), since the confirm has to name the files.
- **Sync always confirms.** The Mac app asks only when a definition is
  replaced; the web page always shows the inline confirm with the files,
  and writes only a previewed plan (the fingerprint). A Mac sync that
  finds a target changed since it was ticked drops it quietly; the web
  page refuses and asks for a refresh.
- **Show in Finder and Open in editor** are Mac only; a folder's full path
  shows on hover instead.
- **The servers folder is always `~/.quiver/mcp/servers`.** The Mac app
  reads a Settings > Paths override from its own preferences, which the
  web app never reads.
- **Git state arrives with the list** rather than filling in row by row.
- **No saved snapshot.** The app shows its last list from disk at launch;
  the web app keeps the last list in memory while the tab is open and reads
  again after a minute, as `loadIfStale` does.
- Sheet harness rows show each logo with the harness's full name.
