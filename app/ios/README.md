# QuiverRemote (iOS)

The native SwiftUI client for remote control. It talks to the hub that
`swe remote serve` runs, over the tailnet: REST for lists and actions,
one SSE stream per open chat for live events. The protocol is in
[docs/remote-control.md](../../docs/remote-control.md); the plan for
every page is in [ROADMAP.md](ROADMAP.md). The web app the hub serves
at `/` is the client for browsers and other devices.

## Layout

| Folder | What |
|---|---|
| `Sources/QuiverRemote/Hub/` | Wire types, `HubClient` (one method per route), the SSE reader, `AppModel` (pairing, sessions, inbox), `ChatModel` (one open chat), `Attachments` (the composer's picked files), `TasksModel`, `IssuesModel` and `PullsModel` (the Tasks and GitHub tabs' data), `NewSession` (what the launch sheets offer, and the launch poll), `UsageModel` and `UsageFormat` (the Usage tab's data and number formatting), `PromptCompletion` (the prompt box's `@` and `/` menus), and the diff, markdown and page-preview helpers |
| `Sources/QuiverRemote/Hub/SSH*.swift`, `ConsoleModel.swift` | The console: this phone's SSH key (`SSHIdentity`), the hub's `/v1/ssh` answer and the saved targets (`SSHTarget`), the SwiftNIO SSH shell with pinned host keys (`SSHShell`), and the console's state (`ConsoleModel`) |
| `Sources/QuiverRemote/Share/`, `Sources/QuiverShare/` | The share extension: what a share carries and how it sends (`ShareItems`, `ShareModel`, compiled into the app too so the tests reach them), and the extension's own view controller and sheet |
| `Sources/QuiverRemote/Theme/` | The Mac app's theme ported to iOS: the same hex values and roles, cards, list rows, badges, buttons |
| `Sources/QuiverRemote/Views/` | Welcome and pairing, Sessions (with rename, fork and the new-session sheet), Chat, Inbox, Tasks, GitHub (issues and pull requests, their detail pages and launch sheets), Usage (limits and spend), Pair requests and devices, Console, the QR scanner |
| `Tests/QuiverRemoteTests/` | Unit tests for parsing, pairing links, diffs, grouping, chat event handling, the GitHub and Tasks rules, usage formatting and the console |
| `Tests/QuiverRemoteUITests/` | UI tests for the launch sheets' Model menu; they need a paired hub on the simulator and skip without one |

The look follows the Mac app (`app/mac/Sources/QuiverApp/Theme/Theme.swift`):
dark and light from the same tokens, sentence case, green only for
"active" and "on", amber for "needs you", red for errors. Type runs one
point larger than the Mac's so captions stay readable on a phone, and
every font comes through `Theme.font(size:weight:design:)`, which follows
the phone's text size (Dynamic Type) from that size at the default. Every
list scrolls vertically only and is pinned to the screen width.
Harnesses show as their logos, not their names: `script/import-harness-icons.py`
copies the Mac app's `harness-*.svg` into `Assets.xcassets/Harnesses`.

## Pairing

- **Tailscale:** type the hub's Tailscale name or address and pair. The
  app shows a 6-digit code; approve it from a paired device (Pair
  requests, in the hub menu on Sessions), from the hub with
  `swe remote devices --approve=<code>`, or from the web app's
  Settings › Integrations › Remote.
- **QR:** on the hub machine, open Quiver › Settings › Remote › Pair a
  device (the Mac app, or the hub's web app), choose Quiver app, and
  scan its code, either in the app or with the Camera app (the
  `quiver://pair` link opens the app, which names the hub and asks
  before pairing with it). A server with neither runs
  `swe remote setup` and then `swe remote pair --app`.
- **Token:** paste the token `swe remote pair` prints.

The app can be paired with several hubs at once, say a Mac and a home
server: pairing another hub (Pair another hub in the hub menu, or a
scanned code) adds it instead of replacing the first, and the hub menu
at the top right of Sessions switches between them. Unpair drops only
the hub on screen, and asks that hub to sign this phone out (its
device row and console key go too) when it can be reached. Each hub's credential is its own Keychain item, in the
shared access group `<team>.com.quiver.remote.shared`; the addresses,
names and the hub on screen are in the App Group
`group.com.quiver.remote`'s defaults. Extensions (the share sheet, the
widget) read the paired hubs from there. A phone paired before the
groups keeps its hubs: the first launch copies each into the groups
and drops the old copy only once the new one is stored. A phone
paired before this keeps its hub: the app moves the old single pairing
into the list on first launch, and a phone paired under the old webview
shell keeps its pairing the same way.

## Share sheet

The `QuiverShare` extension shows as Quiver in any app's share sheet.
It takes text, a link, up to 10 images and files, lists the active
hub's attached sessions (most recent first; the hub refuses a message
to a session it only observes), and sends an optional message. Text and
links go in the message; images go up as downscaled JPEGs and other
files stream from disk, one at a time, through the composer's
attachments route and caps. It reads the paired hubs from the App
Group and the shared Keychain group, so it needs both entitlements on
its own App ID (`com.quiver.remote.share`); without a pairing or a
reachable hub it says so and sends nothing. The extension compiles the
app's hub files it needs (listed under `QuiverShare` in `project.yml`)
rather than a shared framework.

## Console

Console, in the hub menu on Sessions, is a shell on the hub's machine
for debugging when chat can't help, including when the hub itself is
down: it dials the machine's own sshd over the tailnet, not the hub.

- **Key:** one per phone, a Secure Enclave P-256 key (the private half
  never leaves the chip; a software key in the Keychain on the
  simulator). The first console sends its public half to the hub
  (`POST /v1/ssh/key`).
- **On the hub:** `swe remote ssh --on` writes paired phones' keys into
  `~/.ssh/authorized_keys`; until then the console says so and shows
  the command. A Mac also needs Remote Login (System Settings, General,
  Sharing).
- **Host keys:** the phone trusts only the host keys `GET /v1/ssh`
  listed, and saves them with the login user per hub. When the hub
  doesn't answer, the console dials with that saved target and says it
  did. A phone that never opened a console on a hub can't, since it
  has nothing to pin.
- **Libraries:** SwiftNIO SSH (Apple) for SSH and SwiftTerm 1.11.2 for
  the terminal, pinned to exact versions in `project.yml`. SwiftTerm
  1.12 and later add a Metal renderer that needs Xcode's separate Metal
  Toolchain download, so the pin stays below it. Citadel was passed over:
  its 0.12 releases take `swift-nio-ssh` from a third-party fork.

## Building

Xcode projects aren't checked in (they churn); `project.yml` generates
one. `build_ios.sh` builds and installs on the first paired iPhone
(needs `xcodegen` and `jq`, both on Homebrew). By hand:

```sh
cd app/ios
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

The console's SSH tests need a server and skip without one.
`script/console-sshd.sh` starts a throwaway sshd on 127.0.0.1:2222 (its
own host key, config and authorized_keys in a temp folder; nothing in
`~/.ssh` or `/etc/ssh` changes) that trusts a key made fresh for that
run, and prints the `TEST_RUNNER_QUIVER_SSH_*` variables, the key
included, to run the tests with.

`project.yml` carries the pieces that matter: bundle id
`com.quiver.remote`, the `quiver` URL scheme so pair QRs open the app,
the scoped ATS exceptions for a plain-HTTP hub on the tailnet
(`NSAllowsLocalNetworking` for IP literals and local names — which is
what covers Tailscale CGNAT addresses — plus an `NSExceptionDomains`
entry for `*.ts.net` MagicDNS names; arbitrary domains still get full
ATS),
the camera prompt for the QR scanner, iOS 17+, `DEVELOPMENT_TEAM`, and
the entitlements for the App Group and the shared Keychain group. The
Keychain group is written as `$(AppIdentifierPrefix)com.quiver.remote.shared`
(in the entitlements and in Info.plist's `QuiverKeychainGroup`), so the
signing team fills it in. Automatic signing registers both groups on
the team's App ID the first time a device build runs with
`-allowProvisioningUpdates`.
The icon is the Mac app's dark icon (`app/mac/Icons/AppIcon-dark.png`) at 1024.

On-device launch needs Developer Mode (Settings, Privacy and Security,
Developer Mode).

## Not yet

- Push: ntfy still sends notifications, including the lock-screen
  approve and deny buttons. APNs is on the roadmap.
- Decoding stays on the hub; the app only reads protocol DTOs.
- The console reaches the hub's own machine only; satellites don't
  install phone keys or report host keys yet.
- iOS closes the console's connection soon after the app leaves the
  screen. Run long work in tmux on the machine and attach again.
