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
- The server × harness grid, in two sections by how a server runs.
  **Local: runs on <hub machine>** is every server a harness launches with
  a command (stdio, and an `mcp-remote` wrapper), plus the folders in
  `~/.swe/mcp/servers` no server uses ("Not in the hub"), sorted by
  name. A server whose command or an argument is a path inside one of
  those folders gets a **checkout** tag, the folder name and its git
  branch, ahead/behind counts and a Local changes badge. **Remote** is
  every server reached at a URL (http, sse), in swe's order. A cell is a
  tick when the compared harnesses agree,
  a letter (A, B, …) per distinct definition when they don't, and a dash
  when the harness lacks the server. A section folds after ten rows. A
  warning triangle marks a server whose hub definition has a `${NAME}`
  with no value; a dot shows each server's health once checked.
- **Add server** in the header adds a server to the hub and the harnesses
  picked (below).
- Click a row for its sheet: its type ("Local · stdio", "Remote · http"), health detail, the command line or
  URL, env var and header **names**, which harnesses hold each definition,
  where it is missing, **Secrets** (below), and **Sync**.
- **Secrets** lists every `${NAME}` the hub definition uses, as the Mac
  app's Settings › Providers › Inference lists keys: a set value shows
  masked (`ntn_••••••••1234`, the app's `APIKeyMask`), with an eye to
  reveal it, a copy button and **Replace**; a missing one is a password
  field with **Save**. Saving writes `~/.swe/secrets/mcp/.api_keys` on
  the hub (`set_secret`, as `swe mcp secret set NAME` does), clears the
  field and repaints the sheet. Revealed values are forgotten when the
  sheet closes.
- **Sync** is the Mac sheet's: **Copy from** (each definition's first
  harness, plus the SWE hub when `mcp.json` holds the server), then a
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
    "secrets_missing": ["LINEAR_API_KEY"], "in_hub": true, "remote_in": [],
    "secrets": [{"name": "LINEAR_API_KEY", "set": false, "masked": null}],
    "health": "ok", "detail": null
  }],
  "caps": {"claude": {"remote": true, "sse": true}, "codex": {"remote": true, "sse": false}},
  "health": true,
  "error": null,
  "folders": {
    "root": "~/.swe/mcp/servers", "exists": true, "error": null,
    "items": [{"name": "linear-mcp", "group": "custom",
               "path": "~/.swe/mcp/servers/custom/linear-mcp",
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
                          "skipped": [], "pruned": [], "unsupported": []}, …]},
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
  no value in `~/.swe/secrets/mcp/.api_keys` is refused with 409
  `secrets_missing` ("Not syncing: no value for NOTION_TOKEN (used by
  notion). Set NOTION_TOKEN in this server's sheet, or on the hub with: swe
  mcp secret set NOTION_TOKEN"),
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
Claude Code, SWE hub on box." with the command, `swe mcp remove demo
--from=claude,swe`, and a red Remove. On success the sheet closes and
the list repaints from the answer, with "Removed demo from Claude Code,
SWE hub." above it until the list is read again; a refusal stays in
the sheet and the list is read again.

`POST /v1/app/mcp/remove` with `{"server", "from": [places]}` (`quiver`
is the hub) runs `mcp/cli.py`'s `remove_places` and `remove_server`, the
two halves of `swe mcp remove <name> --from=<places>`, under the sync
lock. `from` is what the confirm listed: every place must still hold the
server (409 `state_changed` otherwise), and a place it has reached since
is left alone, because the confirm didn't name it. 404 `unknown_server`,
422 `bad_server` / `bad_from` for bad shapes. The answer is `{"server",
"removed", "absent", "page"}`, `page` being the list after the write.

### Secrets

`POST /v1/app/mcp/secret_set` with `{"name", "value"}` stores a value
(trimmed; empty, multi-line or over 16 KB is 422 `bad_value`) and answers
`{"name", "page"}`, never the value. `POST /v1/app/mcp/secret_value` with
`{"name"}` answers `{"name", "value"}`: the one reply that carries a
value, sent only when the eye or copy button asks. Both take only a name
some hub definition refers to (404 `unknown_secret`), so the page can't
read the rest of the store; an unset name is 404 `secret_not_set`. Error
messages never include a value.

### Add server

The header's **Add server** opens the Mac app's "Add a server" sheet. It
starts with where the server runs, in the grid's words: **Local: runs on
<hub machine>** (a command the hub's harnesses launch) or **Remote** (a
URL, reached from any machine). Only that kind's fields show.

- **Local**: Name; the command line with the app's "command npx · args …"
  hint and its shell-like splitting; **Env vars**, a row per key and value
  with a Secret switch that turns itself on for a key named like a
  credential until flipped by hand, and "saved as ${GITHUB_GITHUB_TOKEN}"
  under a secret. A working folder isn't offered: the hub's server shape
  has no `cwd`.
- **Remote**: Name; the URL with HTTP or SSE; **Sign-in**, one of None,
  **Bearer token** (a password field with an eye, stored as a secret and
  sent as `Authorization: Bearer ${SERVER_TOKEN}`) and **OAuth** (nothing
  is saved: each harness signs in the first time it connects, if it
  supports OAuth); and **Other headers**, rows like the env vars.
- **Where**: Add to, defaulting to the compared harnesses, with All active
  and None; "Always saved to the SWE hub too"; Replace if it already
  exists. For a Remote server a harness that can't take it is switched off
  with the reason: Claude Desktop takes local servers only in its config,
  and opencode and Codex can't use an SSE server. The page reads this from
  `caps` (`{harness: {"remote", "sse"}}`) in the payload.

A secret's value is a password field. On success the sheet closes and the
list repaints with "Added github in SWE hub, Codex." above it.

`POST /v1/app/mcp/add` with `{"name", "kind": "command" | "url",
"command": [words] | "url" + "transport": "http" | "sse" + "sign_in":
"none" | "bearer" | "oauth" (+ "token"), "vars": [{"key", "value",
"secret"}], "targets": [harnesses], "force"}` runs `swe mcp add`
in process: `mcp/cli.py`'s `add_plan` (the definition and targets,
checked) and `add_server` (the writes), the two halves the CLI shares.
A secret var's value goes to `~/.swe/secrets/mcp/.api_keys` through
`set_secret` under `SERVER_KEY` (the app's `mcpSecretName`). The hub's
`mcp.json` keeps `${SERVER_KEY}`; the harness configs picked get the value
itself, as `swe mcp add` and a sync write them. A value already written as
`${NAME}` is kept. Nothing is stored or written for an add the hub
refuses: every value is checked as storable first, a name already in the
hub or a picked harness without `force` is 409 `exists`, a `SERVER_KEY`
another hub server already refers to is 409 `secret_taken` (two servers
would otherwise share one saved value), two keys that fold to the same
name are 422 `secret_clash`, swe's own refusals are 422 `add_refused` (an
unknown harness, an SSE server for a harness that can't take one) or 409
(a `${NAME}` the store can't fill, counting the values this add would
store), and bad shapes are 422. The answer is swe's `--json` report, `{"name", "hub",
"targets"}`, with `page`, the list after the write, and never a value.

## Security

- Env and header values are never in the payload; swe sends only their
  names. A `${NAME}`'s value is in the payload only masked, and in full
  only in a `secret_value` answer. A sync's preview compares values on the hub and sends only which
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

- **Add server, Sync, Remove and Secrets are the writes.**
- **Add server's "Add to"** is a list of switches under the face ("2 of 3
  harnesses") rather than the app's popover checklist, and a secret value
  has an eye to show it while typing. Secret values are cleared from the
  sheet once sent, whether or not the hub took them. Enter in a field adds;
  the sheet can't be closed while the hub is adding.
- **Secrets look like Inference keys.** The Mac sheet pastes a missing
  value only; the web sheet also shows set values masked, with reveal,
  copy and Replace.
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
- **The servers folder is always `~/.swe/mcp/servers`.** The Mac app
  reads a Settings > Paths override from its own preferences, which the
  web app never reads.
- **Git state arrives with the list** rather than filling in row by row.
- **No saved snapshot.** The app shows its last list from disk at launch;
  the web app keeps the last list in memory while the tab is open and reads
  again after a minute, as `loadIfStale` does.
- Sheet harness rows show each logo with the harness's full name.
