# Hooks

The Mac app's Hooks page (`HooksPage.swift`, `HooksModel`, `Hook`) as a
web page. Module `swe/webapp/pages/hooks.py`, script
`static/pages/hooks.js`, styles `static/pages/hooks.css`.

## What it shows

The list is `#/hooks`: every script under `~/.swe/hooks` — the
per-harness tier, the shared tier (one row per deploy target, plus
dormant scripts listed under "Shared") and the foreign tier (scripts a
harness's hooks dir or settings file runs that no `~/.swe` source
claims) — grouped Shared first, Foreign last, each harness in first-seen
order between. A row carries the name, the link-state word (Linked,
Points elsewhere, Not linked, Copy to absorb, Only copy kept, File in
the way, Not installed, Archived), Undeclared when a linked script has
no declaration, Enabled/Disabled, the `~`-shortened destination, the
state's detail note, its `Event:Matcher` declarations and the events the
harness supports.

Above the groups:

- **Action error**: the last link/replace run's failure, when there is one.
- **Check note**: "The hooks list loaded, but its check didn't run" when
  `swe hooks check` itself failed (the list still shows), and the
  `.linkignore` warning when that file can't be read.
- **Links strip**: "N hooks will be linked into place" with "Link with
  swe init" when rows sit in `create`/`relink`/`absorb`, and the blocked
  note with "Replace and back up…" — an inline confirm listing every
  path `swe init --force` would replace — when a real file is in the way
  (`conflict`, `keep`).
- **Check card**: the `swe hooks check` findings — Declared/missing,
  Undeclared, Unlinked and the dimmed, informational Managed.

One hook is `#/hooks?hook=<name>&harness=<h>`: the row plus the script's
summary (docstring or leading comments), Linked-at destination, the
declarations in full (label, timeout, command, settings file), the
supported events, and a read-only preview of the script text. A hook
that is not editable — any source that does not resolve inside
`~/.swe/hooks` — gets the lock note instead of pretending it could be
edited; a missing or directory source gets a note rather than a preview.

**New hook** is `swe hooks new`: a sheet with the harness picker (every
non-archived registry name plus every name the inventory rows carry,
plus "shared") and the name field live-checked against
`^[A-Za-z0-9][A-Za-z0-9_-]*$`. On success the sheet stays open to show
"Created <path>", the stanza to paste into the harness's settings file
(swe never writes one), a copy button, and "Then swe init links it
into place".

## Payload

`GET /v1/app/hooks` returns `{machine, home, hooks, findings, warning,
check_error, blocked, harnesses}`. Each hook is the `swe hooks list
--json` row — `name`, `harness`, `tier`, `source_path`, `dest_path`,
`link_status`, `enabled`, `declared` (`{event, matcher, file}`),
`events_supported` — plus the row's `detail`. `findings` is
`{kind, harness, message}` (`declared-missing`, `undeclared`, `unlinked`,
`managed`); a check that fails returns `findings: []` with `check_error`
set instead. `warning` carries a `.linkignore` read failure, matched to
the CLI's behaviour of listing without it. `blocked` is the
`~/`-relative paths `swe init --force` would replace
(`doctor.blocked_paths` over instructions, skills and hooks), and
`harnesses` the New-hook options.

`GET /v1/app/hooks?hook=<name>[&harness=<h>]` finds the row the way
`swe hooks info` does — exact filename, else stem, optionally scoped by
`harness` through the registry's aliases — and returns it plus
`summary`, `exists`, `is_dir`, `editable` (the source resolves inside
`~/.swe/hooks`), `text` (the Skills page's preview rule: a real file
under 1 MB with no NUL in the first 8 KB) and the declarations in full
(`timeout`, `command`, `file` too). No match is a 404 `unknown_hook`.

`POST /v1/app/hooks/link` runs `swe init` and `POST
/v1/app/hooks/replace` runs `swe init --force`, in process through
`doctor.run_init_now` exactly as the AGENTS.md page does, sharing the
Doctor one-fix lock (409 `busy` while one runs). The reply is
`{ran, error}` plus the list read again. `POST /v1/app/hooks/new`
`{"harness", "name"}` writes the skeleton only — never a settings file —
under the page write lock, then answers `{action: "new", created, ...}`
with the list; `created` is `{path, harness, command, settings[],
stanza}` for a harness hook and `{path, harness: "shared",
tier: "shared", commands, deploys_to[], stanza}` for a shared one, the
same fields `swe hooks new --json` emits. Bad names are 422 `bad_hook`;
any other action is a 404.

## Differences from the Mac app

- Script editing is Mac-only: the InstructionEditor, the per-hook drafts
  (`PrimitiveDrafts`) and the "Edit" affordance stay there, so every
  preview here is read-only and `editable` only decides the lock note's
  wording.
- "Show in Finder" has no web equivalent.
- The rest of `swe hooks` — `delete`, `disable`, `enable`, `sync` and
  `edit` — is not exposed; linking and replacing go through `swe init`
  like the app's strip, and `new` through the POST above.
