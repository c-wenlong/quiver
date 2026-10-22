import SwiftUI
import WebKit

/// The PWA in a WKWebView, with the paired credential seeded into
/// localStorage before any page script runs — the same shape
/// `remote/web/app.js` reads as `store.cred`.
struct HubWebView: UIViewRepresentable {
    let baseURL: URL
    let credentialJSON: String?

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        if let credentialJSON {
            let seed = """
                localStorage.setItem("quiver.remote", \(Self.jsString(credentialJSON)));
                """
            config.userContentController.addUserScript(
                WKUserScript(source: seed, injectionTime: .atDocumentStart,
                             forMainFrameOnly: true)
            )
        }
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.isInspectable = true
        view.load(URLRequest(url: baseURL))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    /// `quiver://pair` links navigated to inside the page are handed back
    /// to the system so `onOpenURL` sees them.
    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(
            _ webView: WKWebView,
            decidePolicyFor action: WKNavigationAction
        ) async -> WKNavigationActionPolicy {
            if let url = action.request.url, url.scheme == "quiver" {
                await UIApplication.shared.open(url)
                return .cancel
            }
            return .allow
        }
    }

    /// Wrap a Swift string as a JS string literal.
    static func jsString(_ value: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: value)) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }
}
