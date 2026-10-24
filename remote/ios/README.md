# QuiverRemote (iOS)

The native SwiftUI client for remote control. It talks to the hub that
`swe remote serve` runs, over the tailnet: REST for lists and actions,
one SSE stream per open chat for live events. The protocol is in
[docs/remote-control.md](../../docs/remote-control.md); the plan for
every page is in [../ROADMAP.md](../ROADMAP.md). The PWA in
`src/quiver/remote/web/` stays as the client for browsers and other
devices.

## Layout

| Folder | What |
|---|---|
| `Sources/QuiverRemote/Hub/` | Wire types, `HubClient` (one method per route), the SSE reader, `AppModel` (pairing, sessions, inbox), `ChatModel` (one open chat), `UsageModel` and `UsageFormat` (the Usage tab's data and number formatting), and the diff and markdown helpers |
| `Sources/QuiverRemote/Theme/` | The Mac app's theme ported to iOS: the same hex values and roles, cards, list rows, badges, buttons |
| `Sources/QuiverRemote/Views/` | Welcome and pairing, Sessions, Chat, Inbox, Usage (limits and spend), Pair requests, the QR scanner |
| `Tests/QuiverRemoteTests/` | Unit tests for parsing, pairing links, diffs, grouping and chat event handling |

The look follows the Mac app (`app/Sources/QuiverApp/Theme/Theme.swift`):
dark and light from the same tokens, sentence case, green only for
"active" and "on", amber for "needs you", red for errors. Type runs one
point larger than the Mac's so captions stay readable on a phone. Every
list scrolls vertically only and is pinned to the screen width.
Harnesses show as their logos, not their names: `script/import-harness-icons.py`
copies the Mac app's `harness-*.svg` into `Assets.xcassets/Harnesses`.

## Pairing

- **Tailscale:** type the hub's Tailscale name or address and pair. The
  app shows a 6-digit code; approve it from a paired device (Pair
  requests, in the hub menu on Sessions), from the hub with
  `swe remote devices --approve=<code>`, or from the `#approve` page.
- **QR:** run `swe remote pair --app` on the hub and scan its QR, either
  in the app or with the Camera app (the `quiver://pair` link opens the
  app).
- **Token:** paste the token `swe remote pair` prints.

The credential goes in the Keychain. A phone paired under the old
webview shell keeps its pairing: the app reads the old stored
credential once and moves it.

## Building

Xcode projects aren't checked in (they churn); `project.yml` generates
one. `build_ios.sh` builds and installs on the first paired iPhone
(needs `xcodegen` and `jq`, both on Homebrew). By hand:

```sh
cd remote/ios
xcodegen generate
xcodebuild -project QuiverRemote.xcodeproj -scheme QuiverRemote \
  -destination 'platform=iOS,name=<your phone>' \
  -allowProvisioningUpdates -derivedDataPath build build
xcrun devicectl device install app --device <device-id> \
  build/Build/Products/Debug-iphoneos/QuiverRemote.app
```

Tests run on a simulator:

```sh
xcodebuild -project QuiverRemote.xcodeproj -scheme QuiverRemote \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -derivedDataPath build-sim test
```

`project.yml` carries the pieces that matter: bundle id
`com.quiver.remote`, the `quiver` URL scheme so pair QRs open the app,
`NSAllowsArbitraryLoads` (the hub is plain HTTP on the tailnet), the
camera prompt for the QR scanner, iOS 17+, and `DEVELOPMENT_TEAM`. The
icon is the Mac app's dark icon (`app/Icons/AppIcon-dark.png`) at 1024.

On-device launch needs Developer Mode (Settings, Privacy and Security,
Developer Mode).

## Not yet

- Push: ntfy still sends notifications, including the lock-screen
  approve and deny buttons. APNs is on the roadmap.
- Listing and removing paired devices needs a hub route (roadmap P8).
- Decoding stays on the hub; the app only reads protocol DTOs.
