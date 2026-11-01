# Harnesses: star, archive, restore, edit, Detect, Add

The web app's first writes. The Harnesses page (sidebar id `harnesses`,
drawn by `static/manage.js`) lists rows from `GET /v1/app/harnesses`; this
plugin, `swe/webapp/pages/harness.py`, changes them. Its one `GET` is
Detect's scan.

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

The header has the app's Detect, Add harness and Refresh buttons. Each of
the app's three sheets is a `<dialog>` over the list:

| Sheet | Opens from | What it does | Request |
|---|---|---|---|
| Edit {name} | a click anywhere on a row but its buttons (or its name, for the keyboard) | The app's `HarnessEditor`: the row's state, PATH and 100-day count, then Name, Command, Description, Version, Aliases, Tags and Notes with the app's placeholders and hints. Save stays off until something changed and while Command is empty; only the changed fields are sent, as `HarnessEdit.flags` does. | `POST /v1/app/harness/edit` |
| Detect harnesses | Detect, or the Detect button on an empty registry | "Scanning PATH and ~/ for coding CLIs…", then the new matches as a tick-list (name, command, confidence badge, path with `~` for the hub's home), high-confidence ones ticked. Register (N) registers exactly the ticked ones, then says "Registered …" and "Skipped name: reason" for any refused. | `GET /v1/app/harness?view=discover`, then `POST /v1/app/harness/register` |
| Add harness | Add harness | The app's `AddHarnessSheet`: Name (checked as you type: the name rule, and not a registered name), Command, Description, Aliases, Tags. Add stays off until both are valid. | `POST /v1/app/harness/add` |

While a sheet's request runs its fields and buttons are off and Esc does
nothing; a refusal shows in red in the sheet, which stays open with what
was typed. On success the editor and Add close and the row changes from
the reply; Detect's list repaints from the rows the reply carries.

## Payload

Every state action takes a JSON body `{"name": "<harness or alias>"}`; `archive`
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

### The sheets

`edit` takes `{"name": "<harness or alias>", "fields": {...}}` where each
key is one of `display_name`, `command`, `description`, `version`,
`aliases`, `tags`, `notes` and each value is the text `swe edit
--<field>=` would get: lists comma-separated, an empty value clears the
field. It runs `harness.commands.edit_harness`, the body of `swe edit
<name> … --json`, and replies with that command's `{"name", "changes",
"harness"}` (`changes` lists the fields that changed; empty, and nothing
written, when none did).

`GET /v1/app/harness?view=discover` is `swe harness discover --json`'s
findings (`DiscoveryRow`), each with `short_path` added. It writes nothing.

`register` takes `{"names": [...]}`, each a match the scan finds as new
now; it runs `apply_findings(…, names=…)`, as `swe harness discover
--apply <names> --json` does, and replies `{"applied": {"added",
"skipped"}, "harnesses": [rows]}`.

`add` takes `{"name", "command", "description", "aliases", "tags"}`
(aliases and tags as a list or comma-separated text). It runs
`harness.commands.add_harness(…, no_replace=True)`, the body of `swe add
<name> <command> … --no-replace --json`: an empty description, alias or
tag list is left off, so a new entry gets swe's default tags (agentic,
coding). The reply is `swe add`'s `{"name", "command", "action",
"installed", "aliases", "tags"}` plus `harness`, the new row.

| Status | Code | When |
|---|---|---|
| 400 | `bad_names` | `names` is not a list of short strings |
| 404 | `unknown_harness` | edit: the name (or alias) is not in the registry |
| 404 | `not_new` | register: a name that is not a new match now |
| 404 | `unknown_view` | `GET` without `?view=discover` |
| 409 | `alias_taken` | edit or add: an alias another harness answers to (`swe edit`'s refusal, "Alias 'x' already used by 'y'") |
| 409 | `already_registered` | add: the name or an alias is registered (never overwritten) |
| 422 | `no_changes` | edit with no fields |
| 422 | `bad_field` | a field the editor doesn't have, a value that isn't text, or a newline in a one-line field |
| 422 | `too_long` | a field over its cap (notes 10000, description 4000, command and lists 1000, name 200, version 100) |
| 422 | `command_required` | an empty command |
| 422 | `bad_command` | add: a command starting with `-`, or over one line |
| 422 | `bad_name` | add: a name that isn't letters, digits, then `. _ -` |
| 422 | `nothing_ticked` | register with an empty list |
| 422 | `refused` | any other edit `swe edit` refuses |

## How it writes

The same functions `swe harness star|archive --json` call: `stars.star` /
`stars.unstar` and `archive.archive` / `archive.unarchive`, the shims over
`registry.py`, which is the only module that writes harness.json. The
sheets call the bodies of `swe edit`, `swe harness discover --apply` and
`swe add`, split out of their `cmd_*` handlers so both share them
(`edit_harness`, `add_harness`, `discover_harnesses` + `apply_findings`).
One lock (`REGISTRY_LOCK`) runs the page's writes in turn, since each
reads harness.json, changes a row and writes it back (the Mac app's
`SerialGate` does the same for its `swe` calls); the Doctor page's fixes
take it too.

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
- The editor doesn't edit the colour (`primary_color`), as the app's
  sheet doesn't.
- Add harness can't say "Not on PATH yet" while you type the command: the
  browser can't see the hub's PATH. The new row says Installed or Not on
  PATH once it is added.
- Detect says "Every harness on {machine} is already registered" rather
  than "on this Mac", and a Register that added nothing says "Nothing was
  registered." rather than an empty "Registered .".
