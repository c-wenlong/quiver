# Setup: first-run setup for the hub machine

A port of the Mac app's first-run setup sheet (`FirstRunSetupSheet`,
`QuiverCore/Onboarding/FirstRunSetup.swift`) for the machine the hub runs
on. Page id `setup`, at `#/setup`; it is not in the sidebar. While the hub
machine has no shared `~/.quiver/AGENTS.md`, opening a bare `/` goes
here once per browser (remembered under `quiver.setup.offered`), and
Overview shows a **Set up** card linking here. Settings links it too. Files:
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
   with **Install…**, which opens swe's install commands for it, one per
   package manager. Each has **Install**, which runs it on the hub, and
   Copy; a manager that isn't on the hub's PATH is dimmed and says so in
   place of Install. Under them sits the provider's "Get … from {host}"
   vendor link (`website`) and, when the desktop app is on the machine
   while the CLI is not, a note naming it (`app`) — both from the same
   `swe install` catalog the Mac's `ProviderSetupSheet` reads. An agent
   with no known install method gets a download card to the vendor site
   instead. While one runs it reads "Installing…" and the other
   Install buttons wait, across a refresh or a trip to another page. A
   failure shows under the list with the end of the installer's output (in
   a card above the grid if the agent now reads as found anyway); a
   success re-reads the page, so the agent moves to "Found" with its tick
   kept.
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

   Above them, while `~/.quiver` has no hub yet (`hub.can_clone`: no
   `AGENTS.md`, no `.git`; the hub's own state there doesn't count),
   **Start from your hub repo** offers `swe setup --hub`: a URL field,
   the note "Fork a hub repo first (or use it as a template for a private
   copy), then paste your fork's URL.", the demo hub link once
   `DEMO_HUB_URL` is set, and **Clone**. The clone runs on the hub's own
   thread; the card reads "Cloning <url>… This can take a few minutes."
   and Continue waits while the page polls `GET /v1/app/setup?hub=1`.
   A refusal or failure (an existing hub, a URL git can't reach, a
   timeout, a path on both sides) shows under the field. Once it's done
   the page reads the machine again, so the step shows the cloned hub's
   AGENTS.md, and the card says what was cloned and any plugin
   marketplace that couldn't be registered. Coming back to the page
   while a clone runs picks the polling up again.

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
  "machine": "home-server",
  "home": "/home/dev",
  "agents_file": "/home/dev/.quiver/AGENTS.md",
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
  "preview": {"harness": [], "mcp": [], "skills": [], "instructions": {}, "hub": {}},
  "hub": {"can_clone": true, "demo_url": null,
          "job": {"state": "idle", "url": null, "error": null, "cloned": null}}
}
```

- `providers` is `ProviderCatalog.all`, in its order. `installed` is
  `ProviderDetection.installed`: a registered, not archived harness whose
  command is on PATH (`swe list --json`), or an extra provider's own
  commands or paths. It also counts the harnesses setup's discovery would
  register (`preview.harness`), see below.
- `install` (only when not installed) is that harness's `methods` from
  `swe install --json`, `website` its vendor link from the same catalog
  (an http(s) URL or absent), and `app` the detected desktop app when one
  is installed while the CLI is not.
- `starred` is the registry's starred harnesses, in pin order. `error`
  is set when the registry couldn't be read.
- `preview` is `swe setup --json`'s `data`, built in process by
  `swe.setup.commands.setup_preview`. A discovery or linking failure
  arrives as `preview.instructions.error`; step 2 then shows it with Try
  again, as the sheet does when the preview call fails.
- `first_run` is the Mac app's `FirstRun.shouldOffer` check: no shared
  `AGENTS.md`.
- `hub` is `hub_status`: `can_clone` (whether `~/.quiver` can take a
  clone now), `demo_url` (null until the demo hub exists) and `job`, this
  hub process's last clone: `state` is `idle`, `cloning`, `done` or
  `failed`, `url` is the URL with any credentials removed, `error` is
  why it failed, and `cloned` is `swe setup --hub`'s
  `{url, path, marketplaces}`. `GET /v1/app/setup?hub=1` answers
  `{"hub"}` alone, with no discovery, for the page to poll.

`POST /v1/app/setup/apply` with `{"starred": ["claude", "gemini"],
"adopt": "/home/dev/.claude/CLAUDE.md" | null}` is `swe setup --apply
--json --starred=claude,gemini [--adopt=<file>]`, in process
(`swe.setup.commands.apply_setup`, which Doctor's setup fix also
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
| 404 | `unknown_action` | any action but `apply`, `install` and `hub` |
| 409 | `busy` | a hub clone is running |
| 403 | `bad_origin` | the hub's Origin check, for a cross-site POST |

`POST /v1/app/setup/install` with `{"id": "gemini", "via": "brew"}` is
`swe install gemini --via=brew` in process
(`harness.commands.install_harness`): the agent's own install command for
that method from `swe install`'s table, never a package the request
names, and the id is never read as another harness's alias. It runs with
nothing on stdin and its output written to a temporary file (the last
4,000 bytes come back, a timeout's included), so a cask or script that
asks for a password fails instead of waiting. It shares
Doctor's one-fix lock (409 `busy`) and the registry lock, and on success
registers the harness as `swe install` does. The reply is `{"error",
"setup"}`: what went wrong with the last lines of the installer's output
(`null` once the command is on PATH), and this page's `GET` data read
again. 404 `unknown_harness` for an id the page doesn't list, 422
`bad_via` for a method that agent has no command for.

`POST /v1/app/setup/hub` with `{"url": "https://github.com/me/hub.git"}`
is `swe setup --hub=<url>`'s clone (`init.hub.clone_and_register`:
`git ls-remote`, a clone into a staging folder moved into `~/.quiver`
around what is there, then the hub's plugin marketplaces registered).
It answers at once with `{"hub"}` and runs on its own thread, holding
the page's setup lock, so apply answers 409 `busy` until it ends. The
URL is kept and answered only with its credentials removed.

| Status | Code | When |
|---|---|---|
| 422 | `bad_url` | `url` is not a one-line string of at most 2,048 characters, or starts with `-` |
| 409 | `has_hub` | `~/.quiver` already holds a hub (`AGENTS.md` or `.git`), or is a file or a symlink; the message is swe's |
| 409 | `busy` | a clone or a setup run is going |

Failures after it starts (a URL git can't reach, a timeout, a path on
both sides) come back in `hub.job.error` with `state: "failed"`.

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
- **Install runs on the hub with no terminal.** The Mac sheet runs an
  install command in Terminal, where a cask or script can ask for a
  password. The web one runs it on the hub in process; one that needs a
  password fails there, and Copy is beside it for a terminal.
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
