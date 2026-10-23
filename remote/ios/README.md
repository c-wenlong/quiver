# QuiverRemote (iOS)

A thin WKWebView wrapper over the hub PWA (`remote/web/`), per
`docs/remote-control.md` R4. The PWA is the product; this shell adds a
real icon, `quiver://pair` deep-link pairing, and room for a Face ID
gate or share-sheet target later. Push notifications come from ntfy,
not this app — there is deliberately no push code here.

## What it does

1. Run `swe remote pair --app` on the hub and scan the QR with the
   Camera app. With `--app` the QR encodes
   `quiver://pair?host=…&port=…&token=…`, so iOS routes it here rather
   than to Safari. (Without `--app` the QR carries the `http://` PWA
   link for pairing in a browser.)
2. `PairingState.handle` POSTs the one-time token to `/v1/pair` on the
   hub and stores the returned device credential.
3. `HubWebView` loads `http://<tailscale-ip>:8477/` with the credential
   seeded into `localStorage["quiver.remote"]` before page scripts run —
   the exact shape the PWA writes for itself, so the app opens straight
   into the Sessions view.

WKWebView storage is separate from Safari's PWA storage, so pairing
once in each is expected, not a bug.

## Building

Xcode projects aren't checked in (they churn); `project.yml` generates
one. `build_ios.sh` does all of the below on the first paired iPhone
(needs `xcodegen` and `jq`, both on Homebrew) — or by hand:

```sh
cd remote/ios
xcodegen generate                 # writes QuiverRemote.xcodeproj
xcodebuild -project QuiverRemote.xcodeproj -scheme QuiverRemote \
  -destination 'platform=iOS,name=<your phone>' \
  -allowProvisioningUpdates -derivedDataPath build build
xcrun devicectl device install app --device <device-id> \
  build/Build/Products/Debug-iphoneos/QuiverRemote.app
```

`project.yml` carries the pieces that matter: bundle id
`com.quiver.remote`, the `quiver` URL scheme (`CFBundleURLTypes`) so
`--app` pair QRs open the app, `NSAllowsArbitraryLoads` (the hub is
plain HTTP on the tailnet), iOS 17+ deployment, and `DEVELOPMENT_TEAM`
set to the account's team id. The icon in `Assets.xcassets` is the Mac
app's dark icon (`app/Icons/AppIcon-dark.png`) scaled to 1024.

On-device launch needs Developer Mode (Settings → Privacy & Security →
Developer Mode) and, on a personal (free) team, a one-time trust of the
profile under Settings → General → VPN & Device Management. Paid-team
development profiles are trusted automatically.

## Explicit non-goals (for now)

The redesign in [../ROADMAP.md](../ROADMAP.md) proposes a SwiftUI
client, which would lift the first one.


- No `SwiftUI` port of the UI — the webview renders `remote/web/`.
- No push entitlements — ntfy owns notifications, including the
  lock-screen approve/deny action buttons.
- No transcript parsing on-device — decoding stays server-side; only
  protocol DTOs cross the wire.
