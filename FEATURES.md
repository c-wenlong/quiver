# Feature matrix

Where every user-facing feature stands on each client. One row per feature,
one column per client. The rules that keep this file honest are in
[AGENTS.md](AGENTS.md), "Keeping the clients in step".

This file records where features stand, not the order they were built in.
Build order stays in [app/mac/ROADMAP.md](app/mac/ROADMAP.md),
[app/ios/ROADMAP.md](app/ios/ROADMAP.md) and
[docs/web-app.md](docs/web-app.md).

Status words:

- `yes`: shipped and working on that client today
- `part`: exists, but a documented slice is missing; the Notes name the slice
- `planned`: on a roadmap, not built
- `-`: not on that client; the Notes say whether that is by design or just
  not built yet. The iOS app is the remote-control client, so a `-` there on
  a control-plane page needs no note.

## Usage

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Overview | yes | yes | part | Mac and Web: tokens, cost, harness split, daily activity, top models. iOS: the Usage tab has limits and spend; harness split, daily activity and top models are missing |
| Sessions | yes | yes | yes | Web: "Copy resume command" instead of Resume, no Devin-cloud or account-profile rows yet. iOS: the hub's remote sessions grouped by machine and folder |
| Session chat | yes | yes | yes | Transcript, composer (Queue/Steer/Stop), approvals. Web renders its own chat page (`static/pages/chat.js`); iOS observe and attach is the app's core |
| Calendar | yes | yes | - | |
| Pricing | yes | yes | - | |

## GitHub

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Repositories | yes | yes | - | |
| Pull requests | yes | yes | yes | Mac and Web: inside Repositories, with merge, rerun and "Open in session". iOS: own tab and detail page |
| Issues, resolve with agent | yes | yes | yes | iOS: own tab plus a Resolve sheet; Mac: detail sheet and ResolveIssueSheet. All sheets carry the model picker prefilled with the last-used model (#914) |
| Tasks | yes | yes | yes | Mac: sidebar page with the same filters, groups, state stepper, sessions and link/unlink |
| Inbox | yes | yes | yes | Pending decisions. iOS badges count only "Waiting on you"; dismissals live on the hub per device |

## Manage

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Accounts | yes | part | - | Web: limit cards per starred harness and machine. Quiver account profiles, history and Open app / Terminal are Mac-only |
| Harnesses | yes | yes | - | Web: star, archive, restore, edit, detect, add |
| Compute | yes | yes | planned | Read-only on web. iOS P9 wants the Compute samplers carried over the hub |

## Primitives

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Plugins | yes | yes | - | |
| Skills | yes | yes | - | |
| AGENTS.md | yes | yes | - | |
| MCP servers | yes | yes | - | |
| Hooks | yes | yes | - | |
| Models | yes | yes | - | |

## System

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Menu bar | yes | - | - | Mac-only by design |
| Widgets | yes | - | - | Mac-only by design |
| Notifications | yes | - | part | Mac: limit resets and 90% crossings. iOS: ntfy today, APNs planned |
| Service status | yes | yes | - | |
| Settings | yes | yes | part | iOS carries the pairing and notification subset |
| Doctor | yes | yes | - | Mac: a Settings section; web: `#/doctor`, no sidebar entry |
| First-run setup | yes | yes | - | Web: `#/setup` |
| API keys | yes | yes | - | Settings › Providers on both |

## Remote control

The hub (`swe remote serve`) is the shared backend for all of these. The Mac
hosts the hub; the PWA and the iOS app are its clients.

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Pair a device | host | yes | yes | Tailscale, pair token, QR scan, 6-digit approval |
| Devices list and revoke | yes | yes | yes | Mac and Web: Settings. iOS: under the hub chip menu |
| Launch a new session | yes | yes | yes | All three: machine, folder and harness pickers, first prompt, launch poll, then the chat opens. All sheets carry the model picker prefilled with the last-used model (`last_models`/`last_model`, #914) |
| Machine switcher | - | - | planned | iOS P4: one machine today |
| Machine vitals | yes | yes | planned | Compute page on Mac and Web; iOS P9 carries the samples over the hub |
| Push notifications | - | - | part | ntfy today; APNs planned |

## Ported logic

Behaviour implemented in two codebases at once: Swift for the Mac app (and
where noted, the iOS app), Python for the hub and web app. A bug in one of
these is the same bug twice by default. A fix that lands on only one side
owes the other the same change; say so in the row's Notes until it is paid.

| Logic | Swift | Python and JS |
| --- | --- | --- |
| Usage ledger: scan, store, pricing, overview figures, per-session joins | `app/mac/Sources/QuiverCore/Ledger/` | `src/quiver/ledger/` |
| Model brands table (names, colours, logos, keywords, patterns) | `app/mac/Sources/QuiverCore/Models/ModelBrands.swift` | `src/quiver/webapp/pages/_brands.py` |
| Sessions rows and filters | `Sessions.swift`, `SessionFilter.swift` | `src/quiver/webapp/sessions.py` |
| Models page usage figures | `ModelsModel.swift` | `src/quiver/webapp/pages/models.py` |
| Calendar figures | `CalendarPage.swift` and its model | `src/quiver/webapp/pages/calendar.py` |
| API key file and store | `APIKeysFile`, `APIKeysStore` | `src/quiver/providers/keyfile.py` |
| MCP redaction and the managed server repo | `McpRedaction`, `SecretText.maskingKeys`, `McpServerRepo` | `src/quiver/webapp/pages/mcp.py` |
| Plugin drift severity | `CatalogPlugin.worstDrift` | `src/quiver/webapp/pages/plugins.py` |
| OpenCode model naming | `openCodeModel` | `src/quiver/ledger/parsers.py` |
| Chat rendering: Markdown, diffs, tool cards | Mac chat views, and iOS's own SwiftUI set | `src/quiver/webapp/static/pages/chat.js` and `src/quiver/remote/web/app.js` (`md`, `renderDiff` in each) |

The iOS app shares only the protocol DTOs with the Mac app today; its
decoding lives on the hub. So a Swift-side bug repeats on iOS only where
logic is written there too, which today is mostly chat rendering.
