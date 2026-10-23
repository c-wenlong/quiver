# Harnesses: star, archive, restore

The web app's first writes. The Harnesses page (sidebar id `harnesses`,
drawn by `static/manage.js`) lists rows from `GET /v1/app/harnesses`; this
plugin, `quiver/webapp/pages/harness.py`, changes one row's state. It has
no `GET` of its own.

## What the page does

| Row | Control | Request |
|---|---|---|
| In use, not starred | Star (outline star) | `POST /v1/app/harness/star` |
| Starred | Unstar (filled star) | `POST /v1/app/harness/unstar` |
| In use | Archive (box icon) opens a form under the row: "Archive {name}", the app's line about nothing being uninstalled, a reason field ("Why? Like: too slow, or no MCP support"), Cancel and Archive. Archive stays off until the reason has text; Enter sends, Esc cancels. | `POST /v1/app/harness/archive` |
| Archived | Restore | `POST /v1/app/harness/restore` |

Nothing changes on screen until the hub answers. While a request runs its
row is dimmed and its buttons are off; the reply's row then replaces the
old one. A refusal or a lost connection shows in an error card above the
list (the Mac app's `actionError`), and an archive that failed keeps its
form open with the reason still typed.

## Payload

Every action takes a JSON body `{"name": "<harness or alias>"}`; `archive`
also takes `"reason"`. The reply is `{"name": <canonical name>, "harness":
HarnessRow | null}`, the row in the `/v1/app/harnesses` shape, or `null`
when the change left nothing for that name in the registry (a row that
held only an archive record).

| Status | Code | When |
|---|---|---|
| 400 | `bad_name` | `name` is missing, not a string, or over 200 characters |
| 400 | `reason_required` | archive with a reason that is empty once trimmed |
| 400 | `reason_too_long` | the reason is over 500 characters |
| 400 | `bad_reason` | `reason` is not a string |
| 404 | `unknown_harness` | the name (or alias) is not in the registry |
| 404 | `unknown_action` | any action but the four |
| 409 | `archived` | star on an archived harness (restore it first) |
| 403 | `bad_origin` | the hub's Origin check, for a cross-site POST |
| 503 | `page_unavailable` | harness.json can't be read (it is never rewritten then) |

The device bearer is required as on every `/v1` route.

## How it writes

The same functions `swe harness star|archive --json` call: `stars.star` /
`stars.unstar` and `archive.archive` / `archive.unarchive`, the shims over
`registry.py`, which is the only module that writes harness.json. One lock
runs the page's writes in turn, since each reads harness.json, changes a
row and writes it back (the Mac app's `SerialGate` does the same for its
`swe` calls).

## Differences from the Mac app and the CLI

- Star and unstar are two actions, not the CLI's toggle: a second click
  that lands after the first can't undo it, and starring a starred
  harness does not move it to the top.
- Starring an archived harness is refused. The CLI's toggle would star it
  and drop its archive reason; the page only offers Restore there.
- Restore on a harness that is not archived does nothing. The CLI form it
  stands for, `swe hs archive <name>` with no reason, would archive it.
- The reason is one line: newlines and runs of spaces collapse to single
  spaces, and it is capped at 500 characters.
- The archive popover is a form under the row, since a browser popover
  would not fit a phone.
- Clicking a row does not open the editor, and Detect and Add harness are
  not here: editing stays in `swe` and the Mac app.
