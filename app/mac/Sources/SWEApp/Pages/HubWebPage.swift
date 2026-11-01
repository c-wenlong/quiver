import AppKit
import SWECore
import SwiftUI
import WebKit

/// Another machine's hub, picked in the sidebar's machine switcher: its web
/// app (`/`) takes over the whole window, native sidebar included — the web
/// app brings its own sidebar, and nesting it inside this app's would show
/// two. A strip in the title bar row names the machine and holds the
/// machine menu, the way back to This Mac. The hub lets the owner's own
/// tailnet devices in from their Tailscale identity, as it does a browser
/// on this Mac; anyone else gets the web app's pairing card.
struct HubWebPage: View {
    var hub: HubOrigin
    @Environment(AppModel.self) private var app
    /// The window's title bar, read from AppKit: the strip clears the
    /// traffic lights by it, as the sidebar's header does.
    @State private var titleBar = TitleBar()

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

    /// The title bar row: the machine menu (the window's only machine
    /// chrome while a hub fills it), then the hub's address. Outside the
    /// menu button the row stays the window's drag area.
    private func strip(_ entry: HubWebEntry) -> some View {
        HStack(spacing: 6) {
            StripSwitcher(hub: hub)
                .fixedSize()
            Text(hub.address)
                .font(Theme.font(size: 12))
                .lineLimit(1)
                .truncationMode(.middle)
            if entry.loading {
                ProgressView().controlSize(.mini)
            }
        }
        .foregroundStyle(Theme.textSecondary)
        .padding(.leading, titleBar.isFullScreen
            ? Theme.sidebarLeadingPadding + 8 : Theme.titleBarLeading)
        .padding(.trailing, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: Theme.titleBarHeight)
        .background(TitleBarReader(titleBar: $titleBar))
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
    /// pairing credential included) with it. Clearing the store's data
    /// can't fail, even while a view still holds the store, so a hub added
    /// again starts signed out; dropping the then-empty store from disk is
    /// tidy-up only, and it may refuse while the old view is still alive.
    func forget(_ hub: HubOrigin) {
        entries[hub] = nil
        let id = hub.dataStoreID
        Task {
            let store = WKWebsiteDataStore(forIdentifier: id)
            await store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)
            try? await WKWebsiteDataStore.remove(forIdentifier: id)
        }
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
        // While a hub fills the window, keystrokes land on the web page
        // and the strip's switcher shows its ring only when focus is
        // really on it: left to the default key-view pick the switcher
        // would take first responder and wear the ring from open.
        DispatchQueue.main.async {
            container.window?.initialFirstResponder = view
        }
    }
}

/// The strip's machine menu, an `NSButton` for the same reason the
/// sidebar's collapse toggle is one: in the title bar row a SwiftUI
/// button never fires — the window's root hosting view takes the click
/// for a window drag. The face names the shown machine; a click pops
/// the same menu the sidebar's machine switcher offers.
struct StripSwitcher: NSViewRepresentable {
    var hub: HubOrigin
    @Environment(AppModel.self) private var app

    func makeNSView(context: Context) -> SwitcherButton { SwitcherButton() }

    func updateNSView(_ button: SwitcherButton, context: Context) {
        button.app = app
        button.hub = hub
        button.syncFace()
    }

    final class SwitcherButton: NSButton {
        var app: AppModel?
        var hub: HubOrigin?
        private var hovering = false { didSet { updateLayer() } }

        init() {
            super.init(frame: .zero)
            isBordered = false
            title = ""
            imagePosition = .imageLeft
            imageHugsTitle = true
            focusRingType = .default
            // The normal action path, not a mouseDown override, so a
            // keyboard press and VoiceOver's press open the menu too:
            // while a hub fills the window this is the only machine
            // chrome. The window hands first responder to the web view
            // (`HubWebView`), so the ring shows only when focus is
            // actually here.
            target = self
            action = #selector(showMenu(_:))
            wantsLayer = true
            layer?.cornerRadius = Theme.controlRadius
            toolTip = "Choose the machine this window shows"
        }

        required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

        /// The shown machine's icon, name and a menu chevron. Called on
        /// every SwiftUI update so a new pick redraws the face.
        func syncFace() {
            guard let hub else { return }
            title = hub.name
            image = NSImage(systemSymbolName: "server.rack", accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: Theme.scaled(12), weight: .regular))
            let face = NSMutableAttributedString(string: hub.name, attributes: [
                .font: Theme.nsFont(size: 12, weight: .semibold),
                .foregroundColor: NSColor(Theme.textPrimary),
            ])
            face.append(NSAttributedString(string: "  ", attributes: [
                .font: Theme.nsFont(size: 12, weight: .semibold),
            ]))
            let chevron = NSTextAttachment()
            let symbol = NSImage(systemSymbolName: "chevron.up.chevron.down",
                                 accessibilityDescription: nil)?
                .withSymbolConfiguration(.init(pointSize: Theme.scaled(9), weight: .medium))
            chevron.image = symbol
            chevron.bounds = CGRect(origin: CGPoint(x: 0, y: -2), size: symbol?.size ?? .zero)
            face.append(NSAttributedString(attachment: chevron))
            attributedTitle = face
            setAccessibilityLabel("Machine: \(hub.name)")
        }

        /// The cell's own measurement plus the hover pill's breathing
        /// room; `super` answers no intrinsic width for this face, which
        /// SwiftUI reads as "stretch me".
        override var intrinsicContentSize: NSSize {
            var size = cell?.cellSize ?? super.intrinsicContentSize
            size.width += 12
            size.height = 22
            return size
        }

        /// Click, keyboard press or VoiceOver press: the menu drops
        /// below the face.
        @objc private func showMenu(_: NSButton) {
            guard let app, let hub else { return }
            Self.menu(machines: app.machines, shown: hub,
                      reload: { [weak app] in
                          if let shown = app?.machines.selected {
                              app?.hubWebViews.reload(shown)
                          }
                      },
                      addHub: { [weak app] in
                          UserDefaults.standard.set(SettingsSection.integrations.rawValue,
                                                    forKey: "settingsSection")
                          app?.select(.settings)
                      })
                .popUp(positioning: nil, at: NSPoint(x: 0, y: bounds.height + 4), in: self)
        }

        /// The picks the sidebar's machine switcher offers: This Mac,
        /// every listed hub, then the shown hub's Reload and Open in
        /// Browser and the list's Add a Hub. The app-level actions come
        /// in as closures so the menu can be built without an AppModel.
        @MainActor
        static func menu(machines: MachineSwitcher, shown: HubOrigin,
                         reload: @escaping @MainActor () -> Void,
                         addHub: @escaping @MainActor () -> Void) -> NSMenu {
            func row(_ title: String, symbol: String? = nil, subtitle: String? = nil,
                     on: Bool = false, run: @escaping @MainActor () -> Void) -> NSMenuItem {
                let action = MenuAction(run)
                let item = NSMenuItem(title: title, action: #selector(MenuAction.fire(_:)),
                                      keyEquivalent: "")
                // An item's target is weak; the object stays alive as
                // the represented object for the popped menu's life.
                item.target = action
                item.representedObject = action
                item.state = on ? .on : .off
                if let symbol {
                    item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
                }
                if let subtitle {
                    let text = NSMutableAttributedString(string: title, attributes: [
                        .font: Theme.nsFont(size: 13),
                    ])
                    text.append(NSAttributedString(string: "\n" + subtitle, attributes: [
                        .font: Theme.nsFont(size: 11),
                        .foregroundColor: NSColor.secondaryLabelColor,
                    ]))
                    item.attributedTitle = text
                }
                return item
            }
            let menu = NSMenu()
            menu.addItem(row("This Mac", symbol: "laptopcomputer",
                             on: machines.selected == nil) { machines.showThisMac() })
            for remote in machines.hubs {
                let origin = remote.origin
                menu.addItem(row(origin.name, symbol: "server.rack", subtitle: origin.address,
                                 on: machines.selected == origin) { machines.select(origin) })
            }
            menu.addItem(.separator())
            menu.addItem(row("Reload \(shown.name)", symbol: "arrow.clockwise") { reload() })
            menu.addItem(row("Open in Browser", symbol: "safari") {
                NSWorkspace.shared.open(shown.url)
            })
            menu.addItem(.separator())
            menu.addItem(row("Add a Hub…", symbol: "plus") { addHub() })
            return menu
        }

        /// One menu row's action, wrapped so `representedObject` keeps
        /// the closure alive for the popped menu's life.
        @MainActor
        final class MenuAction: NSObject {
            private let run: @MainActor () -> Void
            init(_ run: @escaping @MainActor () -> Void) { self.run = run }
            @objc func fire(_: NSMenuItem) { run() }
        }

        override var wantsUpdateLayer: Bool { true }

        override func updateLayer() {
            super.updateLayer()
            layer?.backgroundColor = hovering ? NSColor(Theme.hover).cgColor : nil
        }

        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

        override var focusRingMaskBounds: NSRect { bounds }

        override func drawFocusRingMask() {
            NSBezierPath(roundedRect: bounds, xRadius: Theme.controlRadius,
                         yRadius: Theme.controlRadius).fill()
        }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(rect: .zero,
                options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                owner: self))
            // A new area sends no exit for a pointer the button slid out
            // from under, so read where the pointer is now.
            if let window {
                hovering = bounds.contains(convert(window.mouseLocationOutsideOfEventStream, from: nil))
            }
        }

        override func mouseEntered(with event: NSEvent) { hovering = true }
        override func mouseExited(with event: NSEvent) { hovering = false }
    }
}
