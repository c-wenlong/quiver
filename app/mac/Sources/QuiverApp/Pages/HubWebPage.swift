import AppKit
import QuiverCore
import SwiftUI
import WebKit

/// Another machine's hub, picked in the sidebar's machine switcher: its web
/// app (`/`) fills the panel under a strip naming the machine. The hub lets
/// the owner's own tailnet devices in from their Tailscale identity, as it
/// does a browser on this Mac; anyone else gets the web app's pairing card.
struct HubWebPage: View {
    var hub: HubOrigin
    @Environment(AppModel.self) private var app

    var body: some View {
        let entry = app.hubWebViews.entry(for: hub)
        VStack(spacing: 0) {
            strip(entry)
            Theme.border.frame(height: 1)
            ZStack {
                HubWebView(entry: entry)
                if let failure = entry.failure {
                    failed(failure, entry: entry)
                }
            }
        }
    }

    /// The title bar row says which machine the window shows. It is the
    /// window's drag area, so it holds no buttons; Reload and Open in
    /// Browser live in the switcher's menu.
    private func strip(_ entry: HubWebEntry) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "server.rack")
                .font(Theme.font(size: 12))
            Text(hub.name)
                .font(Theme.font(size: 12, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
            Text(hub.address)
                .font(Theme.font(size: 12))
                .lineLimit(1)
                .truncationMode(.middle)
            if entry.loading {
                ProgressView().controlSize(.mini)
            }
        }
        .foregroundStyle(Theme.textSecondary)
        .frame(maxWidth: .infinity)
        .frame(height: Theme.titleBarHeight)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Showing \(hub.name)'s hub, \(hub.address)")
    }

    private func failed(_ message: String, entry: HubWebEntry) -> some View {
        VStack(spacing: 12) {
            Text("Couldn't reach \(hub.address)")
                .font(Theme.rowTitle)
                .foregroundStyle(Theme.textStrong)
            Text(message)
                .font(Theme.caption)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .textSelection(.enabled)
            HStack(spacing: 8) {
                Button("Try again") { entry.reload() }.primaryButton()
                Button("Show This Mac") { app.machines.showThisMac() }.secondaryButton()
            }
        }
        .frame(maxWidth: 420)
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.panel)
    }
}

/// Every hub's web view, made on first show and kept for the app's life,
/// so switching machines keeps each page where it was.
@MainActor
final class HubWebViews {
    private var entries: [HubOrigin: HubWebEntry] = [:]

    func entry(for hub: HubOrigin) -> HubWebEntry {
        if let entry = entries[hub] { return entry }
        let entry = HubWebEntry(hub: hub)
        entries[hub] = entry
        return entry
    }

    /// The picked hub's view, for the switcher menu's Reload.
    func reload(_ hub: HubOrigin) { entries[hub]?.reload() }

    /// A hub removed in Settings: its view goes, and its web data (the
    /// pairing credential included) with it once nothing holds the view.
    func forget(_ hub: HubOrigin) {
        entries[hub] = nil
        Task { try? await WKWebsiteDataStore.remove(forIdentifier: hub.dataStoreID) }
    }
}

/// One hub's web view and what the strip shows about it.
@MainActor
@Observable
final class HubWebEntry: NSObject {
    let hub: HubOrigin
    let webView: WKWebView
    private(set) var loading = false
    /// Why the last load failed; nil while the page is fine.
    private(set) var failure: String?

    init(hub: HubOrigin) {
        self.hub = hub
        let config = WKWebViewConfiguration()
        // Its own store per hub, kept on disk: the web app's pairing
        // credential (localStorage) outlives a restart, and no hub reads
        // another's.
        config.websiteDataStore = WKWebsiteDataStore(forIdentifier: hub.dataStoreID)
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.underPageBackgroundColor = NSColor(Theme.panel)
        webView.setAccessibilityLabel("\(hub.name)'s hub")
        load()
    }

    private func load() {
        failure = nil
        webView.load(URLRequest(url: hub.url))
    }

    /// Reloads the page, or the hub's root when the first load never got
    /// there.
    func reload() {
        failure = nil
        if webView.url.map(hub.contains) == true { webView.reload() } else { load() }
    }

    fileprivate func open(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    fileprivate func failed(_ error: Error) {
        loading = false
        let error = error as NSError
        // Our own cancels (a policy decision, a newer load) aren't failures.
        if error.domain == NSURLErrorDomain, error.code == NSURLErrorCancelled { return }
        if error.domain == WKError.errorDomain, error.code == 102 { return }  // frame load interrupted
        failure = error.localizedDescription
    }
}

extension HubWebEntry: WKNavigationDelegate, WKUIDelegate {
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        // No target frame: a new-window request (target=_blank or
        // window.open), which WebKit only lets a click make.
        let newWindow = action.targetFrame == nil
        let clicked = newWindow || action.navigationType == .linkActivated
        switch HubNavigation.decide(action.request.url, hub: hub, userInitiated: clicked) {
        case .allow where newWindow:
            // The hub's own page asked for a new window: show it here.
            decisionHandler(.cancel)
            webView.load(action.request)
        case .allow:
            decisionHandler(.allow)
        case .openExternally:
            decisionHandler(.cancel)
            if let url = action.request.url { open(url) }
        case .cancel:
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        loading = true
        failure = nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        reload()
    }

    /// Never a second window: `decidePolicyFor` already sent the request
    /// here or to the browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        nil
    }

    /// The chat's attach button: files the user picks in an open panel.
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping @MainActor ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.begin { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }
}

/// Hosts a hub's kept web view. A container, so SwiftUI rebuilding this
/// view never moves the web view between two parents at once.
private struct HubWebView: NSViewRepresentable {
    var entry: HubWebEntry

    func makeNSView(context: Context) -> NSView {
        let container = NSView()
        attach(to: container)
        return container
    }

    func updateNSView(_ container: NSView, context: Context) {
        if entry.webView.superview !== container { attach(to: container) }
    }

    static func dismantleNSView(_ container: NSView, coordinator: ()) {
        container.subviews.forEach { $0.removeFromSuperview() }
    }

    private func attach(to container: NSView) {
        container.subviews.forEach { $0.removeFromSuperview() }
        let view = entry.webView
        view.removeFromSuperview()
        view.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(view)
        NSLayoutConstraint.activate([
            view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            view.topAnchor.constraint(equalTo: container.topAnchor),
            view.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
    }
}
