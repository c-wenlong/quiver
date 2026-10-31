# macOS app architecture

The app under `app/mac/` is a SwiftPM package. Its main targets are
`QuiverCore` (models, decoders, stores, samplers), `QuiverApp` (the window,
menu bar and pages) and `QuiverWidgetsUI` (views shared by the app preview
and the widget extension); `QuiverWidgets` is the widget extension itself
and `QuiverFanHelper` the root fan-control helper, which links only
`QuiverCore`. Views live in `QuiverApp`; logic that deserves a test lives in
`QuiverCore`. This document is the frontend map; [ARCHITECTURE.md](../ARCHITECTURE.md)
covers the whole repo and [json-contracts.md](json-contracts.md) the `swe`
decoder shapes.

## Folder layout

```
Sources/QuiverApp/
  App/         entry point and app-wide state
    QuiverApp.swift     @main, the Window and menu bar scenes
    main.swift          NSApplication bootstrap
    AppModel.swift      the one app-wide model every page reads
    LimitsMonitor.swift limit notifications and the menubar figure refresh
  Navigation/
    MainWindow.swift    sidebar (collapsible to a rail) plus the page
  MenuBar/
    StatusItemController.swift  the status item, its panel and rows
  Pages/       one file per page (a page may hold private subviews)
  Components/  reusable views and controls
  Theme/       design tokens: colors, type, metrics, Card and Page
Sources/QuiverCore/
  one folder per domain: Swe, Primitives, Mcp, Sessions, Hub, Ledger,
  Accounts, Limits, Status, APIKeys, Agents, Calendar, Catalog,
  Compute, GitHub, Models, Onboarding, Search, Services, Syntax,
  Tasks, Usage, Vector, Providers, MenuBar, Markdown, Lists,
  Snapshots, Support, App, Updates; Resources holds the bundled fonts
  and logos
```

## AppModel

`AppModel` (`App/AppModel.swift`, `@Observable`, `AppModel.shared`) is the
single environment object. It owns the `SweClient`, the page models that must
outlive their page (`accounts`, `others`, `skills`, `mcp`, `plugins`,
`pluginCatalog`, `sessions`, `limits`, `serviceStatus`, `apiKeys`,
`overview`, `providers`, `apps`, `popup`, `widgetSelection`, `menuBar`), and
the UserDefaults-backed settings (`swePath`, `terminal`, `appIcon`,
`appearance`, Dock presence, `lastSection`, `codeEditor`, `navReselects`,
`pluginsTab`). `start()` kicks off the watchers: model restores, the
skills/catalog cross-feed, sessions, limit refresh loops, widget snapshot
writes and the activation-time provider recheck.

Page models are `@Observable` classes in `QuiverCore` (`OverviewModel`,
`HarnessesModel`, `SkillsModel`, `McpModel`, `McpServerRepoModel`,
`PluginsModel`, `PluginCatalogModel`, `SessionsModel`, `ServiceStatusModel`,
`DoctorModel`, `APIKeysModel`, `AccountsModel`, `OtherLimitsModel`,
`ComputeModel`, `AgentsModel`, `ProviderVisibilityModel`, `UpdatesModel`).
Pages get them
from `AppModel` or build them in a `.task` and keep them in `@State`
(`DoctorModel`).

## Navigation

`MainWindow` lays the sidebar and the selected page out in an `HStack`:
the layout reserves `sidebarCollapsedWidth` or `sidebarWidth` and the
`Sidebar` view overlays it at its own width. `AppModel.selection` and
`sidebarCollapsed` persist across launches; `QuiverCore`'s
`SidebarExpansion` carries the pointer state so a collapsed rail floats
open under hover and a toggle under the pointer stays shut until the
pointer leaves once (`hoverSuppressed`). The View menu's Toggle Sidebar
(⌃⌘S) and the sidebar's own first row flip the same flag. While pinned
open, a drag strip on the sidebar's trailing edge resizes it live
(`AppModel.sidebarWidth`, clamped by `QuiverCore`'s `SidebarWidth` to
160...360, standard 196; double-click resets, the value persists).
Reselecting the current sidebar item (or the Plugins > Explore tab) bumps
`navReselects`, which detail views watch to go back through their own
unsaved-changes ask. The menu bar and the desktop widgets are separate
pages: `MenuBarPage.swift` (status item and popup tabs) and
`WidgetsPage.swift` (one underline tab per `WidgetCatalog` kind, its
previews and settings, the chosen kind stored in `desktopWidgetTab`).

## Theme

`Theme` (`Theme/Theme.swift`) is an enum of tokens, never a view: surfaces
(`wash`, `panel`, `card`, `field`, `code`, `subtle`, `hover`, `selected`),
text roles (`textPrimary` through `textMuted`, `dangerText`...), borders and
accents (`accent` emerald for money and "on", `interactive` blue for links,
focus rings and selected controls, `link` at 85% of it), type (`display`,
`figure`, `sectionTitle`, `cardTitle`, `rowTitle`,
`bodySmall`, `caption`, `control`, `tableHeader`...), and metrics (card and
control radii, paddings, field/row/icon heights, sheet sizes, animation
durations, the heatmap palette). The colour values come from the
design-systems repo's `systems/devin-desktop` package: one ink at fixed
alphas, three surfaces, blue kept for interaction. Feature-specific numbers stay near their
view as `private static let`. `Card` (surface with optional title/accessory)
and `Page` (title, subtitle, actions, centred 1152pt column) are the two
scaffolds; `PageHeader` is the non-scrolling variant used by sheets.

## Components

| View | What it is |
|---|---|
| `Card`, `Page`, `PageHeader` | the card surface and the page scaffold (Theme) |
| `Tabs` | the one tab switcher; `.underline`, `.segmented`, `.period` styles; `onSelect` fires on reselect |
| `ExpandRow` | the "Show N more" / "Show less" fold row; `.cardBottom` at a card's edge, `.inset` inside one |
| `SelectMenu` | the themed dropdown; Quiver-drawn list (`SelectOptionList`), `icons` draws a logo per option, `symbols` an SF Symbol |
| `SearchField` | the filter text field |
| `EllipsisMenu` | a row's "more" menu: quiet ellipsis, hover shape |
| `FormField` | a labelled sheet field with an optional hint/problem |
| `Badge` | a small capsule label (plan, state, count) |
| `HarnessIcon`, `HarnessMark`, `HarnessMarkView`, `ChartSwatch` | the logo lookup: icon, icon+wordmark, grid cell, legend swatch |
| `EmptyCard`, `LoadingCard`, `ErrorCard` | status and empty states, always inside a card |
| `SkeletonBlock` | a placeholder bar with a light band sweeping it, for loading states laid out like their content |
| `ExpandRow` + `RowFold` (QuiverCore) | the fold pair: `RowFold.visible`/`hiddenCount` cut the rows, `ExpandRow` draws the toggle |
| `ProviderVisibilityCard` | the Settings provider list (order and show/hide) |
| `ReorderableRows` | drag-to-reorder rows with grip dots |
| `RailRow`, `RailIconButton` | a right-hand rail's tap row (selected/hover treatment) and its header icon button |
| `HoverTip` | the themed tooltip modifier |
| `MarkdownTextView` | the rich markdown editor (NSViewRepresentable) |
| Button styles | `primaryButton`, `secondaryButton`, `iconButton`, `plainButton`, `AccentSwitchStyle`, `SegmentActionStyle` |
| `focusRing`, `quiverField`, `sheetScaffold` | modifiers: keyboard ring, themed field, sheet frame |
| `FlowLayout` | left-to-right wrapping layout |

## Conventions

- Every list that can grow long folds with `RowFold` + `ExpandRow`; each
  view picks its limit as `private static let foldLimit`.
- Every harness or provider shown by name gets a `HarnessIcon` or
  `HarnessMark` beside it.
- Status and empty states render inside `EmptyCard` / `LoadingCard` /
  `ErrorCard`, never as loose text.
- User-facing strings use Theme text roles and contain no em dashes.
- Page actions sit in `Page`'s actions with ⌘R on refresh.
- Decoding `swe` output happens only in `QuiverCore` decoders, matched to
  fixtures under `app/mac/Tests/QuiverCoreTests/Fixtures` (see
  [json-contracts.md](json-contracts.md)).
- Persistent stores, caches and UserDefaults keys are inventoried in
  `docs/data-model.md`.
