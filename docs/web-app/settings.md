# Settings

Sidebar id `settings`. A port of the Mac app's `SettingsPage`
(`app/mac/Sources/QuiverApp/Pages/SettingsPage.swift`, the sections in
`QuiverCore/App/SettingsSection.swift`, and `RemoteSection.swift`,
`ProvidersSection.swift`, `SidebarSection.swift`,
`ExperimentalSection.swift`). Files: `webapp/pages/settings.py`,
`webapp/pages/_qr.py`, `static/pages/settings.js` and `.css`.

## What the page shows

The same title, subtitle, 176px sub-nav (App, Usage, Advanced), section
titles and one-line details, cards of rows, and the footer (version,
Doctor, Service status). Below 720px of page width the sub-nav becomes a
row of underline tabs, as in the app. The chosen section is kept in
`localStorage` (`quiver.settings.section`); `#/settings?section=<id>`
opens one directly (`github` and `remote` land on Integrations, as the
app's saved value does).

| Section | On the web |
|---|---|
| General | Open at login, Terminal, Show in Dock: Mac app only. First-run setup links to `#/setup` ("Set up this machine"). Display › Appearance (System, Light, Dark) works, per browser. Hide accidental sessions: Mac app only. Updates › Version shows the hub's swe version; Latest release and Automatic checks are Mac app only. |
| Sidebar | A switch per page in every group, Enable all / Disable all, Overview, Accounts and Settings locked on ("Always on"), per browser. |
| Notifications | All three rows Mac app only. |
| Providers | Harnesses tab: Provider visibility is Mac app only, with a link to the Harnesses page. Inference tab: the API keys file, with Add key, the provider Catalogue, edit, delete, reveal and copy (below). |
| Services | Mac app only (keys in the Mac's Keychain). |
| Integrations | GitHub: gh's login (with the app's warnings and a re-check button) and the synced repo catalog with Sync now. Remote: the setup checklist until it's done, the hub, paired devices, Pair a device, and pair requests waiting for approval (below). |
| Files | The hub's swe, API keys file and MCP servers folder, read only. Editor: Mac app only. |
| Doctor | A link to the Doctor page (`#/doctor`); the app draws Doctor inside Settings. |
| Experimental | Session index: a switch for the hub's transcript index (`swe session index --on`/`--off`), its status underneath, and while it is on a resync button and Clear search data (below). |

A greyed row keeps the app's title and wording, with a "Mac app only"
badge where its control would be.

### Integrations › Remote

- **Set up remote control**: while any step `swe remote setup` checks is
  left, a card lists them all with a Done, Needs you, Quiver does this,
  Failed or Waiting badge. A step only a person can do links to its
  page (Download Tailscale, Sign in, Open admin console) or copies its
  command; "Pair a phone" mints a code like Pair a device. The steps
  Quiver does itself are not run from here, since moving the hub can cut
  off the page it serves: the card's last row points to the Mac app's
  Set up remote control or `swe remote setup --apply` on the hub, with
  Check again. The card is gone once every step is done. Each visit to
  Settings checks again, as do a revoke and closing the pair sheet, so a
  step that came undone (the last phone revoked) brings the card back.
- **Hub**: "Hub running", the machine name, the public URL (or the
  listen address), uptime, and an Online badge. The page is served by
  the hub, so the app's "No hub reachable" state can't occur here.
- **Paired devices**: every device not revoked, its id and when it was
  last seen; the browser's own device has a "This device" badge
  (`ctx.device_id`). As in the app, the list folds after two rows into
  "Show N more" / "Show less". Revoke opens an inline confirm in the row
  that says what will happen. On the browser's own row the confirm says the page
  stops working until it is paired again, and the request carries
  `confirm_this_device`. After it the page reloads: a browser that
  only had that pairing gets the pair gate, while the hub owner's own
  tailnet device keeps working through its Tailscale sign-in.
- **Signed in through Tailscale**: the hub lets the owner's own tailnet
  devices in without pairing (`ctx.device_id` is `tailnet:<node id>`).
  Such a browser has no row in Paired devices; a line there points to
  the "Signed in through Tailscale" card, which lists every owner node
  the hub has let in (`remote.signins`), the browser's own with "This
  device". Revoke works as for a paired device: the node stops getting
  in, and any device paired from it is signed out, until a pairing from
  it is approved again.
- **Pair a device** mints a token and opens a sheet like the app's: the
  QR code, a Browser / Quiver app switch (the `/#pair=` link or the
  `quiver://pair` deep link), the app's instructions, the link, Copy
  link, and a countdown that turns into "Expired — mint a new code".
  Closing the sheet reloads the list, so a phone that paired shows up.
- **Waiting for approval**: tailnet pair requests (`swe remote devices
  --requests`), no codes or secrets, relisted every few seconds while
  the section is open so a phone's new request (or an expired one
  leaving) shows without a reload. Each row takes the code the asking
  device shows and decides it inline — Approve or Deny posts
  `/v1/pair/requests/<decide>` with the request id pinned, the same
  routes a paired device or the owner's tailnet identity may call. A
  half-typed code survives the relist.

### Providers › Inference

The Mac app's Keys and plans table, read from the API keys file
(`quiver.providers.keyfile`, a port of the app's `APIKeysFile`): every
`export` in file order under its `# Provider` heading ("Other" before the
first one), the variable, the value masked as the app's `APIKeyMask` does
(`sk-ant-••••••••WXYZ`: the vendor prefix up to a dash in the first eight
characters, then the last four; eight dots for anything under 16), the
eye to reveal it and a copy button, the type and the status. A status
select (Any, Key set, No key — Key set on open, as the app's `withKeys`
default), a search over headings and variables, and a row count; Reload
reads the file again.

- **Catalogue** opens the app's `ProviderCatalogueSheet` (API-key
  providers only): a search and a grid of catalog providers, a Set badge
  on any whose variables already have a value in the file (`has_key`),
  and a pick that opens the Add-key editor with that provider chosen.

- **Add key** opens the app's "Add a key": a provider from `swe providers
  list`'s catalog, "saved as" its variable (a pick when it has several),
  Get a key, "X is already set; saving replaces it.", and the value, a
  password field with Show. The key goes where the app puts it: beside a
  variable of the same provider, else under a heading that reads as its
  name or id, else under a new `# <Name>` heading at the end.
- **A row** opens "Edit NAME": the heading it sits under, Get a key, a new
  value (the field starts empty; the eye shows the current one), and
  Delete, which asks first. Saving replaces the line where it is.
- The value field is emptied after every save, whether the hub took it or
  not. A file changed since the page read it is refused, read again, and
  the sheet says to make the change again.

Checking keys, renaming a variable, moving it to another heading, custom
providers, adding the `source` line, the shell status and coding plans
stay in the Mac app.

### Experimental › Session index

The switch is the hub's own setting, `enabled` in
`~/.quiver/data/search/indexer.json`: while it is on, the hub's
`remote-search-index` thread runs a pass over every transcript swe can
decode (the list is in [data-model.md](../data-model.md)) every two minutes (`sessions/search_writer.py`). Turning it
on also starts a pass at once, so the line under the switch reads
"Indexing: the first pass reads every transcript and can take a few
minutes." and the page re-reads the status every 3 seconds until
`running` clears. Settled, it reads "Synced: N sessions, M messages · last
pass <ago>", with files behind or failed added, as the Mac row's status
does. Off, it says how much the index still holds (the Mac app's rows
count too). A failed pass or an unreadable index shows as a warning. A
hub from before the Python indexer sends no `session_search`, and the row
stays greyed as Mac app only.

While the switch is on, two of the app's `SessionSearchRow` actions ride
the status line, both off mid-pass: **resync** (the app's resync — a pass
over every transcript now, so a behind file is caught at once) and
**Clear search data**, which empties the index in place, drops its
prints and the pass record while keeping `enabled`, so an enabled index
rebuilds from scratch. The rows go rather than the file because the Mac
app shares the database without taking `index.lock`: an unlink under
its open connection would strand it on a ghost inode. A clear asked for
while a pass holds `index.lock` is a 409 `busy`; it deletes nothing.

### Kept per browser

Appearance (`quiver.appearance`) sets `data-theme` on `<html>`, which
`styles.css` already honours. The hidden pages (`quiver.sidebar.hidden`)
are hidden by a stylesheet this script adds, which also hides a group's
heading once none of its pages show. `settings.js` applies both when the
app loads, not only when Settings is open, so `app.js` needs no change.

## Payload

`GET /v1/app/settings`:

```json
{
  "machine": "home-server",
  "version": "1.0.0",
  "remote": {
    "caller": {"kind": "device", "device_id": "1fc1…"},
    "hub": {"ok": true, "version": "1.0.0", "uptime_s": 340, "machine": {"id": "...", "name": "..."}},
    "hub_addr": "127.0.0.1:8477",
    "public_url": "https://box.tailnet.ts.net:8477",
    "devices": [{"id": "1fc1…", "name": "laptop", "created_at": 1792829921.0, "last_seen_at": 1792829980.0,
                 "revoked": false, "paired_via": "token", "this_device": true}],
    "requests": [{"request_id": "…", "name": "pixel", "node_name": "pixel", "login": "me@example.com",
                  "peer_ip": "100.64.0.9", "created_at": 1792830000.0, "expires_in_s": 240}],
    "token_ttl_s": 300
  },
  "providers": {
    "path": "~/.quiver/secrets/providers/.api_keys", "exists": true, "error": null,
    "stamp": "9f2c…",
    "keys": [{"name": "OPENAI_API_KEY", "provider": "OpenAI", "has_value": true,
              "masked": "sk-proj-••••••••1234"}],
    "catalog": [{"id": "openai", "name": "OpenAI", "env_vars": ["OPENAI_API_KEY"],
                 "url": "https://platform.openai.com/api-keys", "has_key": true}]
  },
  "paths": {"swe": "~/.local/bin/swe", "api_keys": "~/.quiver/secrets/providers/.api_keys",
            "mcp_servers": "~/.quiver/mcp/servers"},
  "session_search": {"enabled": true, "running": false, "exists": true, "schema_version": "1",
                     "files": {"current": 909}, "sessions_by_agent": {"claude": 850, "codex": 58},
                     "sessions": 908, "messages": 326558, "last_error": null,
                     "last_pass": {"files": 909, "indexed": 3, "current": 906, "failed": 0, "moved": 0,
                                   "drained": 0, "seconds": 0.4, "finished_at": 1792864722.4}}
}
```

`remote.caller` is `{"kind": "device", "device_id"}` for a paired
browser, or `{"kind": "tailnet", "node"}` for the owner's own tailnet
device.

The providers are read the way `load_registry` merges providers.json
with the defaults, without saving, so a page view never seeds or
rewrites the file. A corrupt providers.json, or an entry whose fields
have the wrong types, fills `error` and leaves the rest of the page
working. `url` is only ever an http(s) link.

`GET /v1/app/settings?part=github[&refresh=1]` answers `{"github": {"env":
{"status", "login", "error"}, "catalog": {"repos", "synced_at", "owners"}
| null}}`, through the Repositories page's cached `gh auth status` probe.
It is separate because that probe can take a second or two.

`GET /v1/app/settings?part=remote_setup` answers `{"remote_setup":
<swe remote setup --json data>}` for the hub machine (see
`docs/json-contracts.md`). It is separate because it calls tailscale and
probes the hub's own https address, and it never fetches a certificate:
it shows the cached result, so the page stays quick.

## Writes

All behind the device bearer and the hub's Origin check, one at a time.

`POST /v1/app/settings/pair`, body `{}`: mints a token
(`pairing.mint_pair_token`) and answers `{"ticket": {"host", "port",
"token", "name", "url", "deep_link", "expires_in_s", "expires_at",
"qr": {"web": "<svg…>", "app": "<svg…>"}}}`. The address is the one
`swe remote pair --json` gives phones: the hub's `--public-url` when it
runs behind `tailscale serve`, else the address it advertises, else the
tailnet IP.

`POST /v1/app/settings/session_search`, body `{"enabled": true|false}`:
`swe session index --on`/`--off`. On also starts a pass on a background
thread (quietly skipped if the hub loop or a CLI pass holds the lock; any
other failure lands in `last_error`). `{"resync": true}` starts the same
pass without touching `enabled`; `{"clear": true}` empties the index and
drops its prints (`search_writer.clear_index`, which keeps `enabled`) and
starts a pass when the index stays on — a 409 `busy` while a pass holds
`index.lock`. Answers `{"session_search": …}` in the `get` shape; `400
bad_enabled` when `enabled` isn't a boolean.

`POST /v1/app/settings/sync`, body `{}`: Integrations › GitHub › Sync
now, `swe gh sync --json` in process (`quiver.github.manage.sync_catalog`)
with the hub's `gh` login, into the hub's catalog. It probes `gh` afresh
first. Answers `{"github": {"env", "catalog"}, "failed": [{"owner",
"error"}]}`: the GitHub card read again, and the owners whose listing
failed (`<orgs>` when the org list itself failed); their old rows stay.
The page shows "Skipped … — their old repos stayed." for them, as the app
does. The button is off while a sync runs and while `gh` is not signed
in. A sync can take a minute on a big account, so it does not hold the
other writes' lock: a second sync while one runs is a 409 `busy`, a
missing or signed-out `gh` a 409 `gh_unavailable` / `gh_auth_required`
in the setup words, and an account with no login or orgs a 502
`sync_failed`.

`POST /v1/app/settings/key_set`, body `{"name", "value", "stamp"}`,
plus `"provider"` (a catalog id) for a variable not in the file yet:
replaces the line in place, or adds the key where Add key says above.
`POST /v1/app/settings/key_remove`, body `{"name", "stamp"}`: drops the
line, and its heading once nothing is left under it. Both write as the
Mac app's `APIKeysStore` does: the previous file copied to
`~/.quiver/backups/api-keys/api_keys.bak` (folder mode 700), the new one
written mode 600 through a temp file and a rename (through a symlink to
its target), `~/.quiver/secrets` given its own `.gitignore` of `*`, and
the file checked again just before the swap. They answer `{"providers"}`
in the `get` shape, never a value. `stamp` is an HMAC of the file's bytes
keyed per hub process; a file that no longer matches it is a 409
`changed_on_disk`.

`POST /v1/app/settings/key_value`, body `{"name"}`: `{"name", "value"}`,
the one answer that carries a value, sent when the eye or copy button
asks. Values are trimmed; error messages name variables, never values.

| Status | Code | When |
|---|---|---|
| 422 | `bad_name` | not a variable name, or not one of the picked provider's variables |
| 422 | `bad_value` | empty, more than one line, or over 16 KB |
| 422 | `bad_provider` | a new variable with no provider, or a heading the file couldn't read back |
| 404 | `unknown_provider` | a provider id the catalog doesn't have |
| 404 | `not_found` | the variable isn't in the file |
| 409 | `changed_on_disk` | the file changed since `stamp` |
| 409 | `duplicate` | the variable is exported twice; fix it in an editor |
| 409 | `unreadable` | the file isn't UTF-8 or can't be read |

`POST /v1/app/settings/revoke`, body `{"device_id": "…"}` (plus
`"confirm_this_device": true` for the device sending it): answers
`{"revoked", "this_device", "devices", "signins"}`, the lists in the `get` shape.

| Status | Code | When |
|---|---|---|
| 400 | `bad_device` | `device_id` missing, not a string, or over 64 characters |
| 404 | `unknown_device` | no paired device or tailnet sign-in has that id |
| 409 | `already_revoked` | the device is already signed out |
| 409 | `confirm_this_device` | revoking the sender's own device without `confirm_this_device: true` |
| 404 | `unknown_action` | any action but `pair`, `revoke`, `session_search`, `sync` and the `key_` ones |
| 403 | `bad_origin` | a cross-site POST |
| 401 | `unauthorized` | neither a paired device nor the owner's tailnet device |

## QR codes

`_qr.py` is a port of the byte mode of Project Nayuki's QR Code
generator (MIT, notice kept in the file): versions 1 to 40, every error
correction level, automatic mask choice, SVG output with a 4-module
quiet zone. Pair codes use level M as the app's `CIQRCodeGenerator`
does, raised while the code stays the same size. The tests check the
alignment positions, masks and byte capacities against the standard's
tables, read codes back with a decoder that shares nothing with the
encoder (its own module layout, masks and Galois field, checking each
block's Reed-Solomon syndromes), and, when `zbarimg` is installed,
decode a PNG drawn from the SVG.

## Not ported

- Every row marked Mac app only above, and Services (Keychain).
- Reordering pages in Sidebar and providers in Provider visibility.
- In Inference: checking keys, renaming and moving them, custom
  providers, the shell status and the `source` line, drag to reorder,
  and coding plans.
- Editing paths.
