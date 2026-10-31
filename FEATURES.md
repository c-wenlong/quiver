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
| Overview | yes | yes | yes | Mac and Web: tokens, cost, harness split, daily activity, top models, seven periods. The Mac's breakdown tab is "By account", the web's "By harness". iOS: the Usage tab has the same seven periods (a sideways-scrolling chip row), spend by harness, a daily cost chart, quick figures, top models, a heatmap of the weeks that fit the phone (tap a day for its tokens) and the daily table (newest ten days, then 30 more a tap); no By account tab |
| Sessions | yes | yes | yes | Mac: this machine's sessions from `swe session --json`, then an "Other machines" group from the hub's satellite rows (`OtherMachines`), one subheader per machine with its online dot; filters and search skip that group and its rows have no tokens or cost. Web: "Copy resume command" instead of Resume, no Devin-cloud or account-profile rows yet, accidental sessions always hidden. iOS: the hub's remote sessions grouped by machine and folder; no tool, window, project or model filters, no tokens or cost |
| Session pin, rename, fork | yes | yes | yes | The hub's `/pin`, `/title` and `/fork` routes. iOS: the Sessions rows' swipe-left actions and long-press menu. Web: the rows' More actions button and right-click menu, and the chat header's More actions button; pinned rows lead the list in a Pinned group whatever the time filter. Mac: the list stays `swe session --json`'s rows, with the hub's pin, name and `can_fork` laid over each row whose `harness:id` the local hub lists (read with the hub's keys on the list's cadence); the row's right-click menu and the chat header's More menu, and the same Pinned group. Rows the hub doesn't list, and every row while the hub is down or the app isn't paired yet, have none of these. Fork shows only where the row's `can_fork` is true |
| Session chat | yes | yes | yes | Transcript, composer (Queue/Steer/Stop), approvals. All three: a thinking card reads "Thought for 12s" from the hub's `duration_s` (Claude Code, Codex, opencode), plain "Thought" when the hub sent none (#1440). All three: a tray above the composer holds Queue-mode sends the hub keeps until the agent is idle, to edit, remove or send now (a steer). iOS: no Queue/Steer toggle (every send is Queue mode). Approvals show the pane's menu rows as the answers when the hub read them (numbered rows send their own digit; unnumbered arrow-select lists carry positional ids the hub walks with arrows), else Allow/Always/Deny.
| Calendar | yes | part | - | Web: ledger harnesses (Claude Code, Codex, Devin, Grok Bot, OpenCode) plus Pi, Factory Droid and Freebuff session files and the Cursor app's own database. Mac-only by design: Devin cloud activity, which comes from the Mac app's own store of Devin's API (`~/.quiver/data/devin-cloud`, synced with the token in Settings › Services) that the hub doesn't share. The web always hides accidental sessions; the Mac follows the setting. Mac-only: the side panel's Usage section, per-account remaining % plus dashed reset-time markers on the grid |
| Pricing | yes | yes | - | |

## GitHub

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Repositories | yes | yes | - | |
| Pull requests | yes | yes | yes | Mac and Web: inside Repositories, with merge, rerun and "Open in session". iOS: own tab and detail page with Open in session, merge with a strategy or arm auto-merge (behind a confirm, as on the web), Open on GitHub, and a check row's menu re-runs its Actions run's failed or all jobs (the web and Mac re-run from Actions rows). All three have the Mine and Review requested filters with counts (web: offered only while `GET /v1/pulls` knows the flag for every listed PR; not on the focus page. Mac: on the Repositories dashboard and the repo focus page; Mine compares the `gh` login with each PR's author, Review requested reads `swe gh pr status` per repo and hides while any listed repo's reply is unknown) |
| Issues, resolve with agent | yes | yes | yes | Web: an issue row in Repositories opens a detail sheet with the body, comments, a comment composer and Resolve with agent with "Also post as a comment" (Resolve is also on a Tasks detail, without the switch, as on the Mac's Tasks page). iOS: own tab, a detail page with comments and a comment composer, and a Resolve sheet with Your context and "Also post as a comment". The web and iOS post through the hub's `POST /v1/issues/comment` (the hub's `gh` login); the Mac calls `gh` itself. Mac: detail sheet with comments and a composer, and ResolveIssueSheet. All sheets carry the model picker prefilled with the last-used model (#914); on the Mac and the web it is a menu of Harness default, the harness's `recent_models`, then Other… (web: `static/model-menu.js`) |
| Tasks | yes | yes | yes | Mac: sidebar page with the same filters, groups, state stepper, sessions and link/unlink. All three have five-row groups with "Show N more" and the Repositories rail's scopes (#941, #942); on iOS the rail is a menu above the filter chips, with quiet scopes in an "N quiet" submenu. Sources: GitHub, plus Linear and Notion when configured on the hub; all three clients show a failing Linear or Notion as its own banner or card and link out with "Open in Linear" / "Open in Notion" (#847) |
| GitHub notifications | yes | yes | part | Mac and Web: the Inbox page, Unread/All and mark read; Mac also posts banners. iOS: a GitHub group inside its Inbox tab, which can't mark threads read |
| Needs you (pending decisions) | yes | yes | yes | iOS Inbox tab: Waiting on you, Errors, GitHub, Finished today, cleared per device on the hub; badges count only "Waiting on you". Web: the Inbox page's Needs you card over the GitHub notifications list (which stands in for the iOS GitHub group), with Clear per row and per section, read again every minute; no sidebar badge. Mac: Waiting on you, Errors and Finished today above the GitHub list on the Inbox page (GitHub rows come from `swe gh inbox`, not the hub), a row opens its chat, Clear per section or row, Finished today folds after five rows, read every 15 s, no sidebar badge, nothing shown while the hub is down |

## Manage

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Accounts | yes | part | part | Web: limit cards per starred harness and machine. Quiver account profiles, history and Open app / Terminal are Mac-only. A failed read's "Action needed" state, Fix sheet and banner are Mac-only too; web cards still word only `no_reading`, not `issue.remedy`. iOS: the Usage tab's Limits section shows the same limit cards per harness and machine |
| Harnesses | yes | yes | - | Web: star, archive, restore, edit, detect, add |
| Compute | yes | part | planned | Web: auto clean writes the hub's config; Quit, Clean up and fan control stay Mac-only. iOS P9 wants the Compute samplers carried over the hub |

## Primitives

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Plugins | yes | part | - | Web: no rename or Add marketplace |
| Skills | yes | part | - | Web: no frontmatter edits |
| AGENTS.md | yes | part | - | Web: read and link, no editing |
| Instruction chapters | - | - | - | CLI only, macOS/Linux: `swe instructions build/check/watch` compiles `~/.quiver/AGENTS_MD/index.txt` into the shared `~/.quiver/AGENTS.md`. Clients and harness links read that output. Chapter editing and watching have no app controls; the Mac's AGENTS.md editor still edits the output directly, so use a text editor for chapters in this mode. A build or watcher overwrites direct output edits |
| MCP servers | yes | yes | - | |
| MCP gateway (shared servers) | - | - | - | CLI only: `swe mcp gateway`. The Mac and web MCP pages show a shared server's harness entry as its 127.0.0.1 URL |
| Hooks | yes | yes | - | |
| Shared content in extra accounts | yes | - | - | `accounts.sync.instructions` / `skills` / `hooks` / `mcp` in config.json, applied by `swe init` (#888). `mcp` merges the hub's servers into each account's `.claude.json` `mcpServers` or `config.toml` `[mcp_servers]`. Mac: the Accounts page's Shared content switches, shown once an extra Claude or Codex account exists; a new account is synced when it is added. Web: the AGENTS.md and Hooks pages don't list account rows yet, and there are no switches. |
| Models | yes | part | - | Web: no Artificial Analysis benchmarks (the hub holds no AA key), and each logged name is its own row |

## System

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Menu bar | yes | - | - | Mac-only by design |
| Widgets | yes | - | - | Mac-only by design |
| Notifications | yes | - | - | Mac: limit resets and 90% crossings, and GitHub notification banners. iOS: the app has no notification code; the hub posts to ntfy (`remote/notify.py`) and the ntfy app delivers it. APNs planned |
| Service status | yes | yes | - | |
| Settings | yes | yes | part | iOS: the hub chip menu holds switch hub, devices, Console, Refresh and Unpair; no notification settings |
| Doctor | yes | yes | - | Mac: a Settings section; web: `#/doctor`, no sidebar entry |
| First-run setup | yes | yes | - | Web: `#/setup`. Hub URL step on all three: `swe setup --hub`, web step 2 (clones on the hub's thread, page polls), Mac links step (runs `swe setup --hub --json`) |
| API keys | yes | part | - | Settings › Providers on both. Web: no key check, rename, move, custom providers or coding plans |
| Fan control, Clean up, updates, Report a bug | yes | - | - | Mac-only by design |

## Remote control

The hub (`swe remote serve`) is the shared backend for all of these. The Mac
hosts the hub; the web app and the iOS app are its clients.

| Feature | Mac | Web | iOS | Notes |
| --- | --- | --- | --- | --- |
| Pair a device | host | yes | yes | Tailscale, pair token, QR scan, 6-digit approval. Web: Approve/Deny in Settings › Integrations; iOS: type the code. Mac: Settings › Remote › Pending requests approves or denies a code for a hub on the same Mac (`swe remote devices --requests`, polled while shown); a hub on another machine is reached through the machine switcher, whose embedded web app pairs or approves there. Owner auto-approve (off by default) is a hub setting: `swe remote devices --auto-approve=on\|off` and a switch in that Mac group; Web and iOS don't show it |
| Devices list and revoke | yes | yes | yes | Mac and Web: Settings. iOS: under the hub chip menu |
| Launch a new session | yes | yes | yes | All three: machine, folder and harness pickers, first prompt, launch poll, then the chat opens. All sheets carry the model picker prefilled with the last-used model (`last_models`/`last_model`, #914); on all three it is a menu of Harness default, the harness's `recent_models` newest first, then Other… (web: `static/model-menu.js`; iOS: `ModelSelection`). All three also carry the Autonomy checkbox (`yolo`, on by default): the launch appends the harness's approval-bypass argv per `capabilities.yolo`/`launch_yolo.YOLO_FALLBACK`, and the row hides on a machine row without `yolo_at_launch` |
| Session model (show and switch) | part | part | yes | Hub rows carry `model`, the newest reply's model, and `model_modes` (#643). iOS shows the model in the chat header and the session list, and the chat's menu has a Model submenu (recent models plus Other…) on sessions attached through a channel in `model_modes` (tmux for Claude Code and Codex, headless for Claude Code, the app-server for Codex, serve for opencode); the header shows "→ id" until the transcript catches up (`ModelSwitch`). On an API channel the hub records the pick and the next turn runs it. The web app shows the model (never switches it): the chat header after the folder and the Sessions row, as `Fmt.modelName` in `static/format.js` spells it, the same rule as iOS `ModelLabel.short`. The Mac shows it too (never switches it), the same places, from the hub row (`LocalHub.models`, `SessionChatModel.refreshModel`) through `ModelLabel` in QuiverCore; the web and the Mac chat read the row again when a turn ends |
| Keep a satellite running | yes | - | - | `swe remote agent --install` runs the satellite as its own LaunchAgent (`com.cwenlong.quiver.remote-agent`) or systemd user unit (`quiver-remote-agent.service`), refused on a machine whose own hub is installed. Mac: Settings › Remote › "Keep this Mac's agent running", shown only when this Mac is paired as a satellite and its own hub is not installed (`RemoteAgentModel`). Web and iOS are clients of the hub, so they have nothing to keep running |
| Machine switcher | yes | - | yes | iOS: switch between paired hubs, a machine picker in the launch sheet, sessions and limits grouped by machine. Web: each hub serves its own web app. Mac: the sidebar's first row picks This Mac (native pages) or another machine's hub, whose web app then fills the window in a web view pinned to that hub's origin (#681); hubs come from this Mac's `agent.json` and Settings › Remote › Other hubs |
| Machine strip on Sessions | - | yes | yes | One chip per machine from the hub's machine list (online dot, name, "N working · M waiting", "last seen 2h" offline); tap filters the list to that machine, tap again clears it; hidden with one machine (#1443). Web and iOS only: the Mac already groups other machines' sessions (#1436) |
| SSH console | - | - | yes | iOS-only by design: a shell to the hub machine over tailnet sshd, usable when the hub is down |
| Machine vitals | yes | yes | planned | Compute page on Mac and Web; iOS P9 carries the samples over the hub |
| Push notifications | - | - | - | The hub's ntfy posts reach the ntfy app, not Quiver. Approval pushes carry the menu's own option buttons up to ntfy's three-action cap (a longer menu's last slot links to the chat), else Allow/Deny/Always; APNs planned |

## Ported logic

Behaviour implemented in two codebases at once: Swift for the Mac app (and
where noted, the iOS app), Python for the hub and web app. A bug in one of
these is the same bug twice by default. A fix that lands on only one side
owes the other the same change; say so in the row's Notes until it is paid.

| Logic | Swift | Python and JS |
| --- | --- | --- |
| Usage ledger: scan, store, pricing, overview figures, per-session joins | `app/mac/Sources/QuiverCore/Ledger/` | `src/quiver/ledger/`. Owed by Python: price history (Swift charges each event the rate its entry had then; Python prices every event at today's rate, so Mac and web dollars differ after a price change), and Swift's resume rules (middle-sample fingerprint, ctime check, append-only resume) |
| Model brands table (names, colours, logos, keywords, patterns) | `app/mac/Sources/QuiverCore/Models/ModelBrands.swift` | `src/quiver/webapp/pages/_brands.py` |
| Sessions rows and filters | `Sessions.swift`, `SessionFilter.swift` | `src/quiver/webapp/sessions.py`. Owed by Swift: `ledger_covers` (Python keeps Cline `taskHistory.json`-only and Droid no-settings rows visible; the Mac hides them as accidental) and the per-thread Codex usage merge. Search text: the Mac matches account, the web machine |
| Machine strip chips: online first then name, "N working · M waiting" (working is `active`; waiting is `followup` or `approval`), "last seen 2h" | `OtherMachines.build` ordering (Mac); iOS `Hub/MachineStrip.swift` | `src/quiver/webapp/static/machine-strip.js` |
| Models page usage figures | `ModelsModel.swift` | `src/quiver/webapp/pages/models.py` |
| Calendar figures | `CalendarPage.swift` and its model | `src/quiver/webapp/pages/calendar.py` |
| Grok Bot roster read: which persistence blob is the roster (base32 key ending `.roster.last-roster`), only each row's `id` and `name`, "Subagent" for `sand-subagent-` ids | `app/mac/Sources/QuiverCore/Calendar/GrokBotRoster.swift` | `src/quiver/webapp/pages/calendar.py` (`_grok_names`, `session_title`). Differs by design: the Mac remembers names in its own `~/.quiver/data/grok-bot-names.json` and titles a Bot missing from a non-empty roster "Deleted Bot (<short id>)"; the web never reads the Mac app's store, so a deleted Bot titles by its id |
| Calendar file sources: which files each source lists (hidden ones skipped), which lines are responses, the cwd and session id each takes, the trivial-session drop, and the block rule over a session's whole history. Shared fixtures in `app/mac/Tests/QuiverCoreTests/Fixtures/activity/`, checked by `ActivityParityTests.swift` and `tests/test_webapp_calendar_sources.py` | `QuiverCore/Calendar/FileActivitySources.swift`, `ActivityFileIndex.swift`, `ActivityBlocks.make` | `src/quiver/webapp/pages/_activity.py`, `calendar.py` (`file_pieces`). Pi, Droid and Freebuff today; the Cursor app has its own row. Differs by design: the hub keeps its file index in memory (the same 30-second relist, skipped by a rescan) and never reads the Mac's `~/.quiver/cache/calendar`; it always drops accidental sessions, where the Mac follows its setting |
| Calendar Cursor app source: one `state.vscdb` per account root (the app's own `~/Library/Application Support/Cursor` plus each extra Cursor profile's `desktop` folder), the db and WAL stamp, the `bubbleId:` key range query, composer ids (Swift `split` drops empty pieces; `task-` composers skipped), `createdAt` read as Swift reads the text column (ISO, else `Double(text)` milliseconds), one BUSY retry. Fixture `Fixtures/activity/cursor/state.sql`, checked by both suites | `QuiverCore/Calendar/CursorAppActivity.swift`, `AccountDirectory` (Cursor homes and names) | `src/quiver/webapp/pages/_activity.py` (`parse_cursor`, `cursor_*`), `calendar.py` (`Accounts`) |
| API key file and store | `APIKeysFile`, `APIKeysStore` | `src/quiver/providers/keyfile.py` |
| MCP redaction and the managed server repo | `McpRedaction`, `SecretText.maskingKeys`, `McpServerRepo` | `src/quiver/webapp/pages/mcp.py` |
| Plugin drift severity | `CatalogPlugin.worstDrift` | `src/quiver/webapp/pages/plugins.py` |
| OpenCode model naming | `openCodeModel` | `src/quiver/ledger/parsers.py` |
| Thinking card title: "Thought for 12s" / "1m 5s" / "1h 2m", never under 1s, plain "Thought" when unknown | `ThoughtLabel.text` (`app/ios/Sources/QuiverRemote/Hub/Models.swift` and `app/mac/Sources/QuiverCore/Hub/ThoughtLabel.swift`) | `src/quiver/webapp/static/format.js` (`Fmt.thought`) |
| Session model on screen: drop the provider path and the 8-digit date stamp | `ModelLabel.short` (`app/ios/Sources/QuiverRemote/Hub/Models.swift` and `app/mac/Sources/QuiverCore/Hub/ModelLabel.swift`) | `src/quiver/webapp/static/format.js` (`Fmt.modelName`) |
| Page preview targets and link cards: which tool calls get Preview, path to the route's relative one, which links get a card | `app/ios/Sources/QuiverRemote/Hub/PagePreview.swift`, `Views/LinkCards.swift` (iOS only) | `src/quiver/webapp/static/pages/chat.js` (`pageFiles`, `relativePath`, `linkUrls`) |
| Tasks list rules: filters, groups, state stepper | `QuiverCore/Tasks/Tasks.swift`; iOS `Hub/TasksModel.swift` | `src/quiver/webapp/static/pages/tasks.js` |
| Chat rendering: Markdown, diffs, tool cards, syntax highlighting | `QuiverCore/Syntax/SyntaxHighlight.swift` + `Hub/ToolDiff.swift` (`ToolDisplay`) under the Mac chat views; iOS has `ToolDiff`/`ToolArgs` and is owed highlighting. Readable args differ: the Mac's pretty JSON sorts keys, chat.js keeps insertion order, and iOS writes `key: value` lines | `src/quiver/webapp/static/pages/chat.js` (`md`, `hl`, `readableArgs`, `renderDiff`) |

The iOS app shares only the protocol DTOs with the Mac app today; its
decoding lives on the hub. So a Swift-side bug repeats on iOS only where
logic is written there too, which today is mostly chat rendering.
