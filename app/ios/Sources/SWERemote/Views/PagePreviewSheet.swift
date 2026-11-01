import SwiftUI
import WebKit

/// A tool card's Preview: the page as it is on disk now, fetched from the
/// hub when the sheet opens and drawn in a locked web view.
struct PagePreviewSheet: View {
    let target: PagePreview.Target
    let load: (PagePreview.Target) async throws -> PagePreview.Page
    @Environment(\.dismiss) private var dismiss
    @State private var state: LoadState = .loading
    /// Bumped by Reload and Try again: fetches the file again.
    @State private var attempt = 0

    enum LoadState {
        case loading
        case failed(String)
        case loaded(PagePreview.Page, WKContentRuleList)
    }

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.wash)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .principal) {
                        VStack(spacing: 1) {
                            Text(target.name).font(Theme.barTitle).lineLimit(1)
                            Text(target.path).font(Theme.small).foregroundStyle(Theme.textSecondary)
                                .lineLimit(1).truncationMode(.middle)
                        }
                        .frame(maxWidth: 240)
                    }
                    ToolbarItem(placement: .topBarLeading) {
                        Button { attempt += 1 } label: { Image(systemName: "arrow.clockwise") }
                            .accessibilityLabel("Reload")
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Done") { dismiss() }
                    }
                }
        }
        .task(id: attempt) { await fetch() }
    }

    @ViewBuilder private var content: some View {
        switch state {
        case .loading:
            LoadingRow(text: "Loading \(target.name)")
        case .failed(let message):
            ScrollView {
                StateCard(icon: "exclamationmark.triangle", title: "Couldn't preview \(target.name)",
                          message: message, tone: .error, actionTitle: "Try again") { attempt += 1 }
                    .padding(Theme.gutter)
            }
        case .loaded(let page, let rules):
            VStack(spacing: 0) {
                LockedWebView(page: page, rules: rules)
                Rectangle().fill(Theme.border).frame(height: 1)
                Text("Scripts and network are off in previews.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .frame(maxWidth: .infinity).padding(.vertical, 8)
                    .background(Theme.wash)
            }
        }
    }

    private func fetch() async {
        state = .loading
        let rules: WKContentRuleList
        do {
            rules = try await PreviewRules.compiled()
        } catch {
            // Without the rules the page could reach the network: draw nothing.
            guard !Task.isCancelled else { return }
            state = .failed("Couldn't lock down the preview, so the page isn't drawn.")
            return
        }
        do {
            let page = try await load(target)
            guard !Task.isCancelled else { return }
            state = .loaded(page, rules)
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed(PagePreview.message(for: error))
        }
    }
}

/// The content rule list that blocks every load but the blank document
/// and `data:` URLs (`PagePreview.contentRules`), compiled once.
@MainActor
enum PreviewRules {
    private static var cached: WKContentRuleList?

    struct Unavailable: Error {}

    static func compiled() async throws -> WKContentRuleList {
        if let cached { return cached }
        guard let store = WKContentRuleListStore.default() else { throw Unavailable() }
        let list: WKContentRuleList = try await withCheckedThrowingContinuation { done in
            store.compileContentRuleList(forIdentifier: "quiver.preview.block-network",
                                         encodedContentRuleList: PagePreview.contentRules) { list, error in
                if let list { done.resume(returning: list) } else { done.resume(throwing: error ?? Unavailable()) }
            }
        }
        cached = list
        return list
    }
}

/// A web view that draws one page and nothing else:
///
/// - JavaScript off, set on the configuration and again on every
///   navigation decision.
/// - No network: the content rules block every load except the blank
///   document and `data:` URLs, and the data store is non-persistent,
///   so nothing is kept between previews.
/// - No navigation: the page goes in with `loadHTMLString` and no base
///   URL (so relative links resolve to nothing), and the navigation
///   delegate cancels everything after that first load: link taps, form
///   posts, meta refreshes, frames. New windows are refused, and a
///   long-press shows no link preview.
struct LockedWebView: UIViewRepresentable {
    let page: PagePreview.Page
    let rules: WKContentRuleList

    func makeCoordinator() -> Lock { Lock() }

    func makeUIView(context: Context) -> WKWebView {
        let view = Self.make(rules: rules, lock: context.coordinator)
        Self.draw(page, in: view, lock: context.coordinator)
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    static func make(rules: WKContentRuleList, lock: Lock) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.userContentController.add(rules)
        config.mediaTypesRequiringUserActionForPlayback = .all
        config.dataDetectorTypes = []
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = lock
        view.uiDelegate = lock
        view.allowsLinkPreview = false
        view.allowsBackForwardNavigationGestures = false
        return view
    }

    static func draw(_ page: PagePreview.Page, in view: WKWebView, lock: Lock) {
        lock.firstLoadPending = true
        view.loadHTMLString(page.document, baseURL: nil)
    }

    /// The web view's navigation and UI delegate.
    final class Lock: NSObject, WKNavigationDelegate, WKUIDelegate {
        /// The sheet's own load hasn't been allowed yet.
        var firstLoadPending = false

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     preferences: WKWebpagePreferences,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy, WKWebpagePreferences) -> Void) {
            preferences.allowsContentJavaScript = false
            let allow = PagePreview.allowsNavigation(firstLoadPending: firstLoadPending,
                                                     isMainFrame: action.targetFrame?.isMainFrame == true,
                                                     url: action.request.url)
            if allow { firstLoadPending = false }
            decisionHandler(allow ? .allow : .cancel, preferences)
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            nil
        }
    }
}
