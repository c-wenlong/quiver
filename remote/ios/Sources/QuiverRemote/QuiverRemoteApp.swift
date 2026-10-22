import SwiftUI

/// QuiverRemote — a thin WKWebView shell over the hub's PWA.
///
/// The whole remote-control UI lives in `remote/web/` and is served by
/// `swe remote serve`. This wrapper adds what a home-screen PWA can't:
/// a real icon, `quiver://pair` deep links for pairing, and later a
/// Face ID gate / share-sheet target. Push notifications come from
/// ntfy, never from this app — the wrapper holds no push code.
///
/// Pairing: scanning the `swe remote pair` QR opens
/// `quiver://pair?host=…&port=…&token=…`. The wrapper exchanges the
/// one-time token for a device credential, stores it, and seeds the
/// webview's localStorage (`quiver.remote`) so the PWA sees itself as
/// already paired. WKWebView storage is separate from Safari's, so a
/// credential paired in the PWA does not carry over — re-scan inside
/// the app, which this deep link makes one tap.
@main
struct QuiverRemoteApp: App {
    @StateObject private var pairing = PairingState()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(pairing)
                .onOpenURL { url in
                    Task { await pairing.handle(url) }
                }
        }
    }
}
