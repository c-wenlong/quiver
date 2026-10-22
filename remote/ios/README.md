# QuiverRemote (iOS)

A thin WKWebView wrapper over the hub PWA (`remote/web/`), per
`docs/remote-control.md` R4. The PWA is the product; this shell adds a
real icon, `quiver://pair` deep-link pairing, and room for a Face ID
gate or share-sheet target later. Push notifications come from ntfy,
not this app — there is deliberately no push code here.

## What it does

1. Scan `swe remote pair`'s QR with the Camera app. It encodes
   `quiver://pair?host=…&port=…&token=…`; iOS routes it here.
2. `PairingState.handle` POSTs the one-time token to `/v1/pair` on the
   hub and stores the returned device credential.
3. `HubWebView` loads `http://<tailscale-ip>:8477/` with the credential
   seeded into `localStorage["quiver.remote"]` before page scripts run —
   the exact shape the PWA writes for itself, so the app opens straight
   into the Sessions view.

WKWebView storage is separate from Safari's PWA storage, so pairing
once in each is expected, not a bug.

## Building

Xcode projects aren't checked in (they churn); the four Swift files
under `Sources/QuiverRemote/` are the whole app. To build:

1. Xcode → New Project → iOS App → SwiftUI, bundle id
   `com.quiver.remote`, deployment target iOS 17+.
2. Delete the generated `ContentView.swift`/`*App.swift`, drag
   `Sources/QuiverRemote/` into the target.
3. Info: add URL scheme `quiver` (`CFBundleURLTypes`) so the pair QR
   opens the app.
4. `NSAppTransportSecurity` — the hub is plain HTTP on the tailnet, so
   add `NSAllowsArbitraryLoads` or an exception domain for
   `*.ts.net`/your tailnet range.
5. Run on a device enrolled in the same tailnet.

## Explicit non-goals (for now)

- No `SwiftUI` port of the UI — the webview renders `remote/web/`.
- No push entitlements — ntfy owns notifications, including the
  lock-screen approve/deny action buttons.
- No transcript parsing on-device — decoding stays server-side; only
  protocol DTOs cross the wire.
