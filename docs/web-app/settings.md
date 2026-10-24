# Settings

Sidebar id `settings`. A port of the Mac app's `SettingsPage`
(`app/Sources/QuiverApp/Pages/SettingsPage.swift`, the sections in
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
| General | Open at login, Terminal, Show in Dock: Mac app only. First-run setup links to `#/setup` ("Set up this machine"). Display › Appearance (System, Light, Dark) works, per browser. App icon and Hide accidental sessions: Mac app only. Updates › Version shows the hub's swe version; Latest release and Automatic checks are Mac app only. |
| Sidebar | A switch per page in every group, Enable all / Disable all, Overview, Accounts and Settings locked on ("Always on"), per browser. |
| Notifications | All three rows Mac app only. |
| Providers | Harnesses tab: Provider visibility is Mac app only, with a link to the Harnesses page. Inference tab: the API keys, read only (below). |
| Services | Mac app only (keys in the Mac's Keychain). |
| Integrations | GitHub: gh's login (with the app's warnings and a re-check button) and the synced repo catalog. Remote: the setup checklist until it's done, the hub, paired devices, Pair a device, and pair requests waiting for approval (below). |
| Paths | The hub's swe, API keys file and MCP servers folder, read only. Editor: Mac app only. |
| Doctor | A link to the Doctor page (`#/doctor`); the app draws Doctor inside Settings. |
| Experimental | Session search: Mac app only. |

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
  (`ctx.device_id`). Revoke opens an inline confirm in the row that says
  what will happen. On the browser's own row the confirm says the page
  stops working until it is paired again, and the request carries
  `confirm_this_device`. After it the page reloads: a browser that
  only had that pairing gets the pair gate, while the hub owner's own
  tailnet device keeps working through its Tailscale sign-in.
- **Signed in through Tailscale**: the hub lets the owner's own tailnet
  devices in without pairing (`ctx.device_id` is `tailnet:<node id>`).
  Such a browser has no row in the list, so no row says "This device";
  a line in the card says it is signed in through Tailscale and has
  nothing to revoke. The hub refuses a revoke of a `tailnet:` id.
- **Pair a device** mints a token and opens a sheet like the app's: the
  QR code, a Browser / Quiver app switch (the PWA link or the
  `quiver://pair` deep link), the app's instructions, the link, Copy
  link, and a countdown that turns into "Expired — mint a new code".
  Closing the sheet reloads the list, so a phone that paired shows up.
- **Waiting for approval**: tailnet pair requests (`swe remote devices
  --requests`), no codes or secrets. The Mac section has no approve
  controls, so neither does this page: "Open approvals" goes to the hub's
  remote page (`/#approve`), where the code is typed.

### Providers › Inference

`swe providers list`'s rows: each provider in the registry, the env var
it is read from, whether the API keys file sets it, and the value masked
the way `mask_key` prints it (first 8, last 4, length). A status select
(Any, Key set, No key), a search over names and variables, and a row
count. Adding, editing, checking and revealing keys, and the coding
plans, stay in the Mac app.

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
  "machine": "kai-home-server",
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
    "rows": [{"id": "openai", "name": "Openai", "env_var": "OPENAI_API_KEY", "env_vars": ["OPENAI_API_KEY"],
              "has_key": true, "masked": "sk-proj-***1234 (32)", "url": "https://…", "description": "…"}]
  },
  "paths": {"swe": "~/.local/bin/swe", "api_keys": "~/.quiver/secrets/providers/.api_keys",
            "mcp_servers": "~/.quiver/mcp/servers"}
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

Both behind the device bearer and the hub's Origin check, one at a time.

`POST /v1/app/settings/pair`, body `{}`: mints a token
(`pairing.mint_pair_token`) and answers `{"ticket": {"host", "port",
"token", "name", "url", "deep_link", "expires_in_s", "expires_at",
"qr": {"web": "<svg…>", "app": "<svg…>"}}}`. The address is the one
`swe remote pair --json` gives phones: the hub's `--public-url` when it
runs behind `tailscale serve`, else the address it advertises, else the
tailnet IP.

`POST /v1/app/settings/revoke`, body `{"device_id": "…"}` (plus
`"confirm_this_device": true` for the device sending it): answers
`{"revoked", "this_device", "devices"}`, the list in the `get` shape.

| Status | Code | When |
|---|---|---|
| 400 | `bad_device` | `device_id` missing, not a string, or over 64 characters |
| 400 | `not_paired` | a `tailnet:` id: a Tailscale sign-in, not a pairing |
| 404 | `unknown_device` | no paired device has that id |
| 409 | `already_revoked` | the device is already signed out |
| 409 | `confirm_this_device` | revoking the sender's own device without `confirm_this_device: true` |
| 404 | `unknown_action` | any action but `pair` and `revoke` |
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
- Key editing, checking, revealing, adding the `source` line, and coding
  plans in Inference.
- Editing paths, and GitHub's "Sync now" (run `swe gh sync` on the hub).
- Approving and denying pair requests here: the Mac section has neither;
  the hub's remote page does it.
