import Foundation
import SwiftUI

/// Owns the hub base URL and the paired device credential, and runs the
/// one-time-token exchange for `quiver://pair` deep links.
///
/// The credential is exactly what the PWA keeps in
/// `localStorage["quiver.remote"]` — `{ device, credential }` — so seeding
/// it into the webview localStorage puts the PWA straight into the
/// sessions view.
@MainActor
final class PairingState: ObservableObject {
    /// E.g. `http://100.x.y.z:8477` — the hub's Tailscale address.
    @Published var baseURL: URL? {
        didSet { UserDefaults.standard.set(baseURL?.absoluteString, forKey: "quiver.hub") }
    }

    /// `{"device": "…", "credential": "…"}` as stored by the PWA.
    @Published var credentialJSON: String? {
        didSet { UserDefaults.standard.set(credentialJSON, forKey: "quiver.credentialJSON") }
    }

    @Published var pairingError: String?

    init() {
        let defaults = UserDefaults.standard
        if let raw = defaults.string(forKey: "quiver.hub") {
            baseURL = URL(string: raw)
        }
        credentialJSON = defaults.string(forKey: "quiver.credentialJSON")
    }

    /// Handles `quiver://pair?host=…&port=…&token=…[&name=…]`.
    /// Unknown schemes are ignored so the app stays a pure wrapper.
    func handle(_ url: URL) async {
        guard url.scheme == "quiver", url.host == "pair",
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { return }
        let query = Dictionary(
            uniqueKeysWithValues: (parts.queryItems ?? []).map { ($0.name, $0.value ?? "") }
        )
        guard let host = query["host"], let token = query["token"], !token.isEmpty
        else {
            pairingError = "pair link missing host or token"
            return
        }
        let port = query["port"].flatMap(Int.init) ?? 8477
        let name = query["name"]?.isEmpty == false ? query["name"]! : "iphone"
        let hub = URL(string: "http://\(host):\(port)")!

        var request = URLRequest(url: hub.appending(path: "/v1/pair"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(
            withJSONObject: ["token": token, "name": name]
        )
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let body = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let credential = body["credential"] as? String,
                  let device = body["device"] as? String
            else {
                pairingError = "pairing failed — token expired or hub unreachable"
                return
            }
            let stored = try JSONSerialization.data(
                withJSONObject: ["device": device, "credential": credential]
            )
            baseURL = hub
            credentialJSON = String(decoding: stored, as: UTF8.self)
            pairingError = nil
        } catch {
            pairingError = error.localizedDescription
        }
    }
}
