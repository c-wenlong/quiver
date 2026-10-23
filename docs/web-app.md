# Web app

The Mac app's pages as a web app, served by the remote hub, so they open in
any browser on the tailnet. Decided 2026-10-23: one UI, web first. Pages are
built once, in HTML, and the logic they need lives in Python on the hub. The
native app in `app/` is left exactly as it is while this is tried, so going
back means deleting this, not repairing anything.

## Why web first

The Mac app's busiest pages (Overview, Accounts, Calendar, Compute, Models,
Pricing, Service status) run on Swift code with no Python equivalent: the
usage ledger, pricing, the account fetchers. A browser on another machine
cannot reach that code, so a web version needs it in Python anyway. Built
alongside the native pages, every change would have to be made twice, and the
web version would always trail. With one UI the change lands once and every
client sees it.

## Shape

| Piece | Where | What |
|---|---|---|
| Pages | `src/quiver/webapp/static/` | Plain HTML, CSS and JS. No build step, no framework, no CDN. Same TokenTracker look as the Mac app. |
| Page data | `src/quiver/webapp/api.py` | Builds each page's JSON from the domain services, in process, the way `/v1/usage` does. |
| Ledger | `src/quiver/ledger/` | The Python port of `QuiverCore/Ledger`: scans harness logs into its own SQLite store and prices tokens. |
| Routes | `src/quiver/remote/hub.py` | `/app/` serves the pages; `/v1/app/*` serves their data. |

The hub already runs on the always-on machine, binds 127.0.0.1 and is
published by Tailscale Serve, so the web app needs no new port, process or
service: it is at `https://<host>:<hub port>/app/`.

## Auth

The pages call `/v1/app/*` with the device credential the hub's PWA already
pairs (`Authorization: Bearer`, kept in `localStorage` under the hub's
origin). `/app/` and `/` share that origin, so a browser paired for the PWA
is already paired for the web app; an unpaired one is sent to `/` to pair.
Behind `tailscale serve`, run the hub with `--public-url=https://…` so it
reads the tailnet client from Serve's headers: "Pair with Tailscale" then
works, and the Origin check accepts the `https://` origin Serve presents.
A pair token (`swe remote pair`) works either way.

The first pages only read. Writes (star a harness, link a skill, sync MCP)
come later, as `POST`s under the same Origin check the PWA's writes use.

## Data on the hub machine

The web app shows the machine the hub runs on. Its ledger lives in
`~/.quiver/data/web-ledger/`, apart from the Mac app's
`~/.quiver/data/ledger/`, so the two never write the same file even when
both run on one Mac. Other machines' usage reaches the hub the way limits
already do, through satellites; that is a later step.

## Pages, in order

1. Overview (tokens, cost, harness split, daily activity, top models). Shipped.
2. Sessions (the hub's catalog, with tokens and cost per session from the
   ledger). Shipped. Differences from the app: Resume becomes "Copy resume
   command" (the browser may be on another machine), transcript full-text
   search and the Claude desktop "Started from" label are left out, the model
   filter counts tokens of the sessions in the chosen window rather than the
   app's daily rows, and usage joins only for sessions on the hub machine
   (a satellite's rows list without tokens). Sessions only in a Quiver
   account profile's home, and Devin cloud sessions, are not listed yet.
3. Harnesses and Accounts limits. Shipped, read only. Harnesses lists
   `/v1/app/harnesses` (every registered harness, archived included, as
   `swe list --scope=all`) with the app's In use / Archived scopes, search,
   starred-first order and the fold for unstarred rows. Accounts is one
   card per starred harness from `/v1/usage`, per machine (hub and each
   satellite), with the app's limit bars, plan badge, on-demand and extra
   usage rows and status lines. Star, archive, edit, Detect and Add stay in
   `swe` and the Mac app; Quiver's own Claude and Codex account profiles,
   history, and Open app / Terminal are Mac-only.
4. Skills, AGENTS.md, MCP servers, Plugins, Doctor: read-only first, from the
   `swe … --json` builders.
5. Writes for the Manage pages.
6. Calendar, Compute, Models, Pricing, Service status.

Mac-only parts stay native whatever happens here: the menu bar, widgets,
notifications, and opening a terminal.

## Adding a page

Every page after the first four is a plugin, so pages are built in
parallel without touching a shared file. A page with the sidebar id
`skills` is:

| File | What |
|---|---|
| `src/quiver/webapp/pages/skills.py` | `get(ctx, args) -> dict` answers `GET /v1/app/skills` (`args`: each query parameter's first value). An optional `post(ctx, action, body) -> dict` answers `POST /v1/app/skills/<action>`. Raise `PageError(status, code, message)` to refuse. `ctx.state` is the hub's `HubState`, `ctx.home` its home. |
| `src/quiver/webapp/static/pages/skills.js` | `registerPage("skills", render)`. The bundle runs each script in its own function scope, so private names never collide with another page's. `render(id)` paints `#panel` with the shared helpers in `app.js` (`api`, `esc`, `icon`, `pageHeader`, `renderGate`) and `manage.js` (`pageError`, `bindRefresh`); take `const gen = routeGen` before awaiting and drop the reply when it changed. |
| `src/quiver/webapp/static/pages/skills.css` | Optional styles, using the tokens in `styles.css`. |
| `tests/test_webapp_skills.py` | The page's data, and the route with auth. |
| `docs/web-app/skills.md` | What the page shows, its payload, and what differs from the Mac app. |

The hub dispatches `/v1/app/<page>` to the module (device bearer as for
every `/v1` route; POSTs also pass the Origin check) and serves every
page script as one `/app/pages.js`, every stylesheet as `/app/pages.css`.
The page goes live in the sidebar as soon as its script registers. The
sidebar's order and icons are fixed in `app.js` and already list every
Mac app page; Menu bar and Widgets are marked Mac only.

A page that needs usage reads the shared ledger with `with ctx.state.webapp.ledger() as (store, catalog, scan_ok):`, never a store of its own.

Read data the way the Mac app does: from the same `swe … --json`
builders in process, not by shelling out, and never from the Mac app's
own stores.

## Going back

Nothing in `app/` changes. To drop the web app, delete `src/quiver/webapp/`,
`docs/web-app/`,
`src/quiver/ledger/`, the `/app/` and `/v1/app/` branches in `hub.py`, and
`~/.quiver/data/web-ledger/`.
