# MCP servers

The Mac app's MCP servers page (`McpServersPage`, `McpModel`,
`McpServerRepoModel`) in the web app, for the machine the hub runs on.
Read only.

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
  where it is missing, the `${NAME}`s that need values, and the `swe mcp
  sync … --only=<name>` command that copies it to the compared harnesses
  that lack it.
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
  "machine": "kai-home-server"
}
```

`servers` is `swe mcp list --json`'s rows from `server_inventory`, built in
process with the registry read without its migration write, with
`command`, `args` and `url` replaced by `summary`. `health` and `detail`
appear only with `?health=1` (`swe mcp status --json`'s values). `error`
is set when the harness configs could not be read; the folders are still
listed then, as in the app.

## Security

- Env and header values are never in the payload; swe sends only their
  names.
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

- **No writes.** Add server, Sync (and its Preview), Remove, and pasting a
  secret value all change config files or the secrets store, so they stay
  in `swe mcp add|sync|remove|secret set` and the Mac app. The sheet shows
  the `swe mcp sync` command to run on the hub instead of the sync form,
  and the names that need values with the `swe mcp secret set` hint
  instead of paste fields.
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
