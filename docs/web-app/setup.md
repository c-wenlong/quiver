# Setup: first-run setup for the hub machine

A port of the Mac app's first-run setup sheet (`FirstRunSetupSheet`,
`QuiverCore/Onboarding/FirstRunSetup.swift`) for the machine the hub runs
on. Page id `setup`, at `#/setup`; it is not in the sidebar. Files:
`quiver/webapp/pages/setup.py`, `static/pages/setup.js` and `setup.css`,
`tests/test_webapp_setup.py`.

## What the page shows

The page opens on one card:

- **Set up this machine**, when the hub machine has no shared
  `~/.quiver/AGENTS.md` yet (`first_run`), with a Set up button.
- **This machine is set up** otherwise, with Run setup again (the Mac
  app reopens the sheet from Settings › General the same way).

The button reads the machine afresh (as the sheet loads its preview each
time it opens), then starts the sheet's steps, in a card laid out like the sheet:
the Quiver mark, the step bars and "1 of 3", the step's title and
subtitle, and a footer with Skip setup, Back and Continue.

1. **Pick the agents you use.** "Found on {machine}" with a count and a
   tile per agent found there, then "Show N more agents" for the rest of
   the catalog. A tile toggles its tick. The first ticks are the
   harnesses starred now. Ticking an agent that isn't installed adds the
   sheet's banner ("… isn't installed yet. Quiver will list it anyway…")
   with **Install…**, which opens swe's install commands for it (one per
   package manager, each with Copy; dimmed when that manager isn't on the
   hub's PATH).
2. **One setup for all of them.** Shared instructions, with the "Start
   from" picker when your agents already have instruction files (your
   first one by default, or Quiver's starter file) and the note for each
   file the choice leaves alone; Skills; MCP servers, naming the servers
   setup would import. **Set up** runs setup; while it runs the button
   reads "Setting up…" and every control is off. When setup left files
   alone or couldn't star a pick, the "Set up. A few things to know:"
   card lists them and you stay on the step until you click again, as in
   the sheet. A failure shows in an error card and Set up runs again.
   Going Back and changing the ticks runs setup again (without adopting a
   second time), as the sheet does.

3. **Pick a look.** System, Light and Dark, each drawn as a small Quiver
   window, with the Mac app's blurbs ("Matches this browser" for System).
   A click applies it at once and saves it in this browser under
   `quiver.appearance`, the key Settings › General uses, so the two stay
   in step. Start using Quiver finishes.

After setting up, Continue moves to the look; when setup left something
to read it stays on step 2 first, as the sheet does.

Finishing (or Skip setup) returns to the card, which then says what
changed and links the AGENTS.md, Skills and MCP servers pages. The
refresh button reads everything again and starts the steps over.

## Payload

`GET /v1/app/setup`:

```json
{
  "machine": "kai-home-server",
  "home": "/home/kai",
  "agents_file": "/home/kai/.quiver/AGENTS.md",
  "first_run": true,
  "agents": {
    "providers": [
      {"id": "claude", "installed": true},
      {"id": "gemini", "installed": false,
       "install": [{"via": "npm", "command": "npm install -g @google/gemini-cli", "available": true}]}
    ],
    "starred": ["claude"],
    "error": null
  },
  "preview": {"harness": [], "mcp": [], "skills": [], "instructions": {}}
}
```

- `providers` is `ProviderCatalog.all`, in its order. `installed` is
  `ProviderDetection.installed`: a registered, not archived harness whose
  command is on PATH (`swe list --json`), or an extra provider's own
  commands or paths. It also counts the harnesses setup's discovery would
  register (`preview.harness`), see below.
- `install` (only when not installed) is that harness's `methods` from
  `swe install --json`.
- `starred` is the registry's starred harnesses, in pin order. `error`
  is set when the registry couldn't be read.
- `preview` is `swe setup --json`'s `data`, built in process by
  `quiver.setup.commands.setup_preview`. A discovery or linking failure
  arrives as `preview.instructions.error`; step 2 then shows it with Try
  again, as the sheet does when the preview call fails.
- `first_run` is the Mac app's `FirstRun.shouldOffer` check: no shared
  `AGENTS.md`.

`POST /v1/app/setup/apply` with `{"starred": ["claude", "gemini"],
"adopt": "/home/kai/.claude/CLAUDE.md" | null}` is `swe setup --apply
--json --starred=claude,gemini [--adopt=<file>]`, in process
(`quiver.setup.commands.apply_setup`, which Doctor's setup fix also
runs), and makes the starred set exactly that list. The page
sends the ticks in the grid's order, then any other ticked harness. The
reply is `{"applied": <the envelope's applied>, "first_run", "starred"}`.
Runs are serialised by the page's lock and the registry lock the
Harnesses page and Doctor's fixes hold.

| Status | Code | When |
|---|---|---|
| 422 | `bad_starred` | `starred` is not a list of non-empty names of at most 200 characters |
| 422 | `bad_adopt` | `adopt` is not null or a non-empty path |
| 404 | `unknown_harness` | a starred name is neither a catalog agent nor in the registry |
| 404 | `unknown_file` | `adopt` is not one of the preview's `adopt_candidates` right now (no other path is opened) |
| 503 | `setup_failed` | discovery itself failed, before anything was written |
| 409 | `not_adoptable` | `adopt` sent once `AGENTS.md` already holds your own text |
| 404 | `unknown_action` | any action but `apply` |
| 403 | `bad_origin` | the hub's Origin check, for a cross-site POST |

Part-way failures are not refusals: as with `swe setup --apply --json`
they come back in `applied.errors` or `applied.links.error`, and the
page shows them.

## Not ported, and why

- **Step 4, notifications.** macOS notification permission and the limit
  alerts are the Mac app's own.
- **Opening at first launch.** The Mac app opens the sheet when it starts
  on a Mac with no shared AGENTS.md, and remembers per Mac that it was
  finished or skipped. The web app only shows the card on this page.
  A later change links it from Overview (when `first_run`) and from
  Settings (being built in parallel; it links to `#/setup`). So there is
  no "done" flag: the card keeps offering setup until AGENTS.md exists.
- **Install… runs nothing.** The Mac sheet can run an install command in
  Terminal. The browser may be on another machine, so the commands are
  shown to copy and run on the hub.
- **The Settings › Providers sentence** after the unstarred names is left
  out: that page is the Mac app's.

## Differences from the Mac app

- "Found on this Mac" names the hub machine instead.
- The look is kept per browser, not per machine, as Settings keeps it.
- The tiles are in the catalog's order. The Mac app uses the order you
  dragged in Settings › Providers, which the web app doesn't have.
- On a machine swe has never seen the registry is empty, so `swe list`
  alone would find nothing. The page also counts what setup's own
  discovery found on PATH (the harnesses it is about to register), so
  the agents installed there show as found on the first run.
- PATH is the hub process's PATH; the Mac app reads the login shell's.
