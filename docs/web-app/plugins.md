# Plugins

The Mac app's Plugins page (`PluginsPage.swift`, `PluginExplorer.swift`),
read only, at `/app/#/plugins`. Module `quiver/webapp/pages/plugins.py`,
script and styles `static/pages/plugins.js` and `plugins.css`.

## What it shows

Same title and subtitle as the app, and its two tabs.

- **Health**: `swe find plugins --json`. One card per harness with drift,
  in report order; each finding has its ref, kind badge (Not installed,
  Out of date, ...), detail, the differing files (stale only), the fix
  swe would run with a copy button, and its fix id. A card folds after 8
  findings. Empty states as in the app: nothing in `~/.quiver/plugins`, or
  every harness runs what it holds.
- **Explore**: `swe plugins catalog --json` placed the way
  `PluginExploreIndex` places it: Local (the hub), then each harness in
  swe's order, then Not registered. Each marketplace is a card that opens
  in place (closed by default; a search opens every match) with its
  source tag (`local`, `github`, `git`, `cache`), a lock for GitHub ones,
  and its installed count. Each plugin row has its name, version, a drift
  badge that opens Health, its ref when a display name differs, its
  description, up to 8 skill badges, and a chip per harness with the
  app's status dot (Enabled, Disabled, Installed, Available, Cached).
  The harness select and search ("Search plugins and their skills")
  filter rows as the app does, and the count reads "N of M plugins".
  What swe could not read is listed above the sources. A plugin's name
  opens a sheet with its marketplace, per-harness state, components,
  category, author, homepage, tags, folders and skills.

Like the app, the page opens on Health, and on Explore when Health has
nothing to show (no hub plugins and no drift) until a tab is picked.

## Payload

`GET /v1/app/plugins?view=health|explore[&refresh=1]` (default `health`).
Both carry `view`, `built_at` (epoch seconds the view was read) and
`machine`.

- `health`: `{sources, drift: [...]}` as `swe find plugins --json`, each
  finding with `kind_title` and `severe` added.
- `explore`: `harnesses`, `actions`, `errors` and `total` from the
  catalog; `plugins`, each catalog plugin once keyed by ref, slimmed to
  what the page shows (`title` is the display name, `harnesses` maps each
  harness to `{status, version, scopes, path, copies}` with `copies` a
  count, `skills` are `{name, description}`, `drift` is the worst gap as
  `{state, title, harnesses}` or null); and `sources`, each
  `{id, kind, harness, title, note, marketplaces}`, each marketplace
  `{id, name, title, tag, plugins}` and each row `{ref, harnesses}`, the
  harnesses whose chips it shows. Filtering and installed counts run in
  the browser.

Each view is kept in the page module for 60 s (the app's snapshot age),
one read per view at a time; Refresh passes `refresh=1`.

## Differences from the Mac app

Writes are left out; they stay in `swe` and the Mac app:

- Health's **Fix** and **Fix all** (`swe find plugins --fix=<id>`). The
  web shows the fix and its id to run by hand.
- Explore's per-harness **Install**, **Uninstall**, **Enable** and
  **Disable** menus (`swe plugins <verb> <ref> --harness=<h>`). The chips
  show the state only.
- **Add marketplace** (`swe plugins marketplace add`).
- **Rename** of a marketplace or plugin display name. The names the app
  saved in `~/.quiver/config/plugin-display-names.json` are read (never
  written) so rows are named the same.
- The plugin detail page's manifest form and file editor, and Show in
  Finder. The web detail is a read-only sheet.

Also different:

- A plugin's skills are the catalog's (read from each harness's loaded
  copy). The app also folds in installed copies `swe skills` finds inside
  the plugin and drops one shown on the Skills page; that placement waits
  for the Skills page.
- The app's last-result and error banners for actions do not exist, since
  there are no actions.
