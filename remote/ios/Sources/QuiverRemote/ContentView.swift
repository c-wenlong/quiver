import SwiftUI

/// Loads the hub PWA once a hub URL + credential are known; otherwise
/// shows a minimal "scan the `swe remote pair` QR" prompt.
struct ContentView: View {
    @EnvironmentObject private var pairing: PairingState

    var body: some View {
        if let baseURL = pairing.baseURL {
            HubWebView(baseURL: baseURL, credentialJSON: pairing.credentialJSON)
                // Re-pairing rebuilds the view so the at-document-start
                // credential seed is the fresh one, not the old pair.
                .id("\(baseURL.absoluteString)|\(pairing.credentialJSON ?? "")")
                .ignoresSafeArea()
        } else {
            VStack(spacing: 16) {
                Text("Quiver Remote").font(.title2.bold())
                Text("On the hub, run `swe remote pair --app` and scan the QR " +
                     "with the Camera app — the quiver:// link opens here.")
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                if let error = pairing.pairingError {
                    Text(error).foregroundStyle(.red).font(.footnote)
                }
            }
            .padding()
        }
    }
}
