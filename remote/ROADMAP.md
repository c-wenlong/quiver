# Quiver Remote roadmap

The phone client for remote control: watch every agent session on your
machines, unblock the ones waiting on you, and steer the ones you
attached. The hub, protocol and milestones R1 to R5 are in
[docs/remote-control.md](../docs/remote-control.md). This file covers the
client: every page, component and feature of the redesign, and the hub
work each one needs.

## Decisions so far

| Topic | Decision |
|---|---|
| Look | The TokenTracker style package (`systems/tokentracker` in design-systems), dark first, the same as the Mac app. Near-black page, `#171717` hairline cards, white primary buttons, green only for "active" and "on", amber for "needs you", red for errors and destructive actions, sentence case everywhere, a visible focus ring. Values come from its `tokens.json`. |
| Icon | The Mac app's dark icon (`app/Icons/AppIcon-dark.png`) |
| Client | Native SwiftUI on iPhone (`remote/ios/`), decided 2026-10-23. The PWA stays as the client for browsers and other devices. See "SwiftUI or PWA" below. |
| Signing | Paid team `MAK855QGSV`, so profiles last a year and APNs, TestFlight, widgets and Live Activities are all available |
| Push | ntfy stays the push path until the APNs work under "Features" lands |
| Shared code | Protocol DTOs only. Decoding stays on the hub; most of QuiverCore is AppKit-bound. |

## SwiftUI or PWA

| | SwiftUI app | PWA in a WKWebView |
|---|---|---|
| Feel | Real navigation stack, swipe back, collapsing large titles, swipe actions, context menus, haptics, pull to refresh | Imitations of each, which never feel quite right in a webview |
| Composer and keyboard | Native text field that stays above the keyboard, system dictation, a photo picker | Webview keyboard handling jumps and covers the composer |
| Long transcripts | `LazyVStack` renders only what is on screen | The DOM holds all 500 backfilled messages |
| Offline | SwiftData cache of sessions and recent messages, so the app opens instantly with the last known state | Service workers in WKWebView are flaky; the app is blank until the hub answers |
| Push | APNs with notification actions handled in the app (approve from the lock screen, open the right chat) | ntfy only; a tap opens the app, not the session |
| Widgets, Live Activities, Siri, Shortcuts | Share the app's Swift networking and models | Each extension needs that Swift code anyway, since extensions cannot run the webview |
| Face ID, Dynamic Type, VoiceOver | Built in | Partial; Dynamic Type does not reach the page |
| Rendering code and diffs | More work: SwiftUI's markdown has no code blocks, so they need a custom view | Already works (`md()`, `renderDiff()` in `app.js`) |
| Shipping a change | Rebuild and reinstall (or TestFlight) | The hub serves the new page on next load |
| Version drift | The app can lag the hub, so the protocol needs a version field and the app must handle a newer hub | None; the hub serves the client that matches it |
| Other devices | iPhone and iPad only | Android, any browser, and the `#approve` page on a Mac |

Decision: the iPhone client is SwiftUI, and the PWA stays for every
other device. The checkboxes below track the SwiftUI app; the PWA
restyle is its own item under "Features".

## Pages

Each page lists what it shows, then the hub work it needs. "Hub:" items
are server changes in `src/quiver/remote/`.

### P1. Welcome
- [x] App icon, name and a one-line description, shown before any hub is paired
- [x] Primary "Pair with Tailscale" button, secondary "Scan pair QR" button
- [x] Hint line: on the hub, run `swe remote pair --app`
- [x] In-app QR scanner (VisionKit `DataScannerViewController`), so pairing no longer goes through the Camera app

### P2. Pair this device
- [ ] Device name field, prefilled with the phone's name
- [ ] Hub card: the hub's name and a status dot once it is found
- [x] "Pair with Tailscale" primary button
- [x] "Use a pair token instead" link that opens a token field
- [x] Errors in plain words: hub unreachable, not on the tailnet, rate limited
- [ ] Hub: a discovery route, or a tailnet lookup, so the app finds the hub without typing an address

### P3. Approve this device
- [x] The 6-digit code, large and monospaced
- [x] The approve command with the code filled in, tap to copy
- [x] Waiting card with a spinner and an expiry countdown
- [x] Expired, denied and "too many wrong codes" states, each with "Try again"
- [x] Cancel
- [x] Drive the countdown from `expires_in_s`, which `POST /v1/pair/request` already returns

### P4. Sessions
- [x] Large title, and a hub chip with a connection dot (green connected, grey reconnecting, red offline)
- [x] Filter chips with counts: All, Active, Needs you
- [x] Groups: machine, then project folder; finished sessions fold into an "Earlier" group
- [x] Session row: status dot, title, harness, status and age; an eye icon for observed and a link icon for attached
- [x] Pull to refresh
- [ ] Swipe to attach or open
- [x] Empty state: "No sessions on this machine yet"
- [ ] More than one machine: a machine switcher in the hub chip
- [ ] New-session sheet: machine, folder, harness, first prompt
- [ ] Hub: a route to start a session (machine, cwd, harness, prompt)

### P5. Chat, observed
- [x] Header: title, harness and folder, and a menu (copy session id, interrupt)
- [ ] "Open on Mac" in that menu
- [x] Read-only transcript
- [x] Opens on the newest 100 messages; the next older page loads once you stop scrolling within 15 entries of the oldest one loaded, and the entry at the top of the screen stays put (a button on iOS 17)
- [x] The last 8 chats opened stay in memory, so going back into one shows it at once and replays only what changed
- [x] Attach card above the keyboard area: "Watching only", plus Attach when the session can be attached
- [x] Mid-turn and "this harness cannot be attached" wording on the card, as `updateOwnership()` has today

### P6. Chat, attached
- [x] Message bubbles: yours on the right, the agent's as plain text
- [x] Agent Markdown: headings, lists, quotes, rules, code blocks, and tables that scroll sideways inside their own box when wider than the screen
- [x] Task checkboxes, image and rendered-view chips, local file links shown as file names, MCP tool names as "server · tool"
- [x] Thinking card, collapsed to "Thought"
- [ ] Thinking duration ("Thought for 12s"); the hub sends no duration yet
- [x] Tool card: `Edit · app.js` with an added and removed line count, tap to expand
- [x] Diff view inside tool cards for edit and write tools
- [x] Runs of tool calls fold into "Used 6 tools"
- [x] Approval card: the prompt, then Allow, Always and Deny; queued, resolved and failed states
- [x] Compaction divider
- [x] Composer: Queue and Steer segment, Stop, a round send button
- [x] Send states on your bubble: sending, queued (with the hub's reason), failed with retry
- [ ] `/` command shortcuts and `@` file completion
- [ ] Photo and file attach, landing as `@` refs on the machine
- [ ] Hub: `@` file completion proxied from the machine

### P7. Inbox
- [x] Groups: Waiting on you (approvals and follow-ups), Errors, Finished today
- [ ] Approval rows show the prompt itself and a Review button
- [x] Clear a row (long press) and "Clear" in the header, kept on the phone until the hub has a dismiss route
- [ ] Swipe to dismiss a row
- [x] Tab badge counts only Waiting on you, not every row
- [x] Empty state: "Nothing needs you"
- [x] Hub: an age cutoff on `catalog.inbox()`: follow-ups, errors and interrupts older than 3 days drop out (it used to return every one ever seen, which is how the badge reached 278). Approvals never age out.
- [x] Hub: a dismiss route, stored per device, so a cleared item stays cleared (`POST /v1/inbox/dismiss`; new activity brings it back)
- [x] Hub: include finished sessions from today, marked as finished (`GET /v1/inbox?done=1`)
- [ ] App: use the hub's cutoff, dismiss route and `?done=1` in place of `Staleness` and the on-phone `InboxDismissals`

### P8. Pair requests and devices
- [x] Pending requests: device, Tailscale node, login, age, a code field, Approve and Deny
- [ ] Paired devices list: name, "This device", last seen, Remove
- [x] Reached from the hub chip menu
- [ ] Opened straight from a "new pair request" push
- [ ] Hub: `GET /v1/devices` and a revoke route (today `swe remote devices` and `--revoke` are CLI only)
- [ ] Show `last_seen_at`, which the hub already updates on each authenticated request; the devices route above only needs to return it

### P9. Machine
- [ ] Per-machine strip: name, online state, satellite version
- [ ] CPU, GPU and temperatures from QuiverCore's Compute samplers
- [ ] Hub: carry those samples from the satellite

### P10. Settings
- [ ] Hub address and connection test
- [x] Unpair this device
- [ ] Notification preferences: which events push
- [ ] Face ID lock on or off
- [ ] App and hub versions

## Components

- [ ] Status dot: active, needs you, error, idle, offline
- [ ] Session row, and grouped hairline card lists
- [ ] Filter chip with a count
- [ ] Hub chip with a connection dot and a menu
- [ ] Tab bar with icons and a badge
- [ ] Primary, secondary and destructive buttons at TokenTracker sizes
- [ ] Text field and code field
- [ ] Attach card
- [ ] Composer: segment, Stop, send
- [ ] Message bubble with send states
- [ ] Tool card and diff view
- [ ] Approval card
- [ ] Thinking card and compaction divider
- [ ] Pair code display and waiting card
- [ ] Device row
- [ ] Empty, loading and error states for every list
- [ ] Toast for failures that have no bubble to sit on

## Features

- [x] Live updates over SSE, resuming from the last seq after a reconnect or relaunch
- [ ] Offline cache: last sessions list and recent messages per session
- [ ] APNs push for needs you, approval, finished, error and limit events, with Approve and Deny actions on the lock screen
- [ ] Hub: an APNs sender alongside ntfy, and device token registration at pairing
- [ ] Live Activity for a running session: title, status, and an approval button when one is pending
- [ ] Home and lock screen widgets: inbox count, active sessions
- [ ] Face ID gate on launch
- [ ] Share sheet target: send text, a link or a file to a session as a prompt
- [ ] Siri and Shortcuts: "Approve the pending request", "What needs me"
- [ ] Deep links: `quiver://pair` (exists), `quiver://session/<key>`
- [ ] Dictation in the composer
- [x] Search sessions from Sessions: transcripts through the hub's FTS index when present, titles and folders as text always (`GET /v1/search`)
- [ ] Search satellite transcripts (the index covers the hub machine only)
- [ ] Usage and limits view from `swe list --usage --json`. Hub: `GET /v1/usage` serves those rows for the hub machine, and the PWA's Usage tab shows them
- [x] Hub: satellites report their own usage, so a MacBook's quota shows too (`POST /v1/agent/usage`, `machines` in `GET /v1/usage`)
- [ ] Diff viewer with commit and push through `swe gh`
- [ ] iPad layout: sessions list and chat side by side
- [ ] Protocol version check, with a "hub is newer, update the app" message
- [ ] PWA restyle to the same look, for browsers and Android
