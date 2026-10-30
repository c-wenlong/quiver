import Charts
import AppKit
import QuiverCore
import SwiftUI

/// The menu bar item and its panel.
///
/// A borderless, key-capable, non-activating NSPanel instead of NSPopover
/// or MenuBarExtra (the OpenUsage pattern): a popover in an app that is not
/// frontmost only takes key focus on a second click, so its controls feel
/// dead on the first one.
@MainActor
final class StatusItemController: NSObject {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var panel: MenuBarPanel!
    private var outsideClickMonitor: Any?
    private var localClickMonitor: Any?
    private var sizeObservation: NSKeyValueObservation?
    private let model: AppModel
    /// The screen the panel opened on, read by the Usage tab's height cap.
    private let panelScreen = PanelScreen()

    init(model: AppModel) {
        self.model = model
        super.init()
        let content = MenuBarContent(
            openMain: { [weak self] in self?.openMainWindow() },
            dismiss: { [weak self] in self?.close() })
        let host = NSHostingController(
            rootView: content.environment(model).environment(\.panelScreen, panelScreen))
        host.sizingOptions = [.preferredContentSize]
        panel = MenuBarPanel(contentViewController: host)
        panel.onEscape = { [weak self] in self?.close() }
        // Rows that load, refresh away or wrap while the panel is open
        // change the content's size; follow it rather than keep the size
        // it opened with, which would leave empty space or clip.
        sizeObservation = host.observe(\.preferredContentSize) { [weak self] _, _ in
            Task { @MainActor in self?.fitPanel() }
        }
        if let button = item.button {
            button.action = #selector(toggle)
            button.target = self
            button.imagePosition = .imageLeading
        }
        show(model.menuBar, limit: nil, today: nil)
        Task { [weak self] in
            // `tightest` too: the minute timer moves it when a window resets,
            // even if the readings themselves don't change.
            let changes = Observations { [weak model] in
                model.map { ($0.menuBar, $0.limits.readings, $0.limits.tightest,
                             MenuBarDisplay.today(in: $0.overview?.spend)) }
            }
            for await change in changes {
                guard let (display, readings, _, today) = change else { continue }
                self?.show(display, limit: display.limit(in: readings, now: .now), today: today)
            }
        }
    }

    /// The watched limit's ring, then whatever figures the Menu bar and
    /// widgets page chose (nothing by default). Red from 90%, like the bars;
    /// monochrome below. Hidden altogether when switched off there.
    private func show(_ display: MenuBarDisplay, limit: LimitReading?, today: UsageTotals?) {
        item.isVisible = display.isVisible
        if !display.isVisible { close() }
        guard let button = item.button else { return }
        let text = display.text(limit: limit, today: today)
        button.attributedTitle = NSAttributedString(
            string: text.isEmpty ? "" : " \(text)",
            attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)])
        guard let limit else {
            button.image = NSImage(systemSymbolName: "gauge.with.dots.needle.67percent",
                                   accessibilityDescription: "Quiver")
            button.toolTip = text.isEmpty ? "Quiver" : "Quiver: \(text)"
            button.setAccessibilityLabel(text.isEmpty ? "Quiver" : "Quiver. \(text)")
            return
        }
        let percent = limit.percent
        button.image = LimitRing.image(fraction: limit.usedPercent / 100, critical: percent >= 90)
        let window = limit.window.isEmpty ? "" : " \(UsageFormat.window(limit.window))"
        button.toolTip = "\(limit.title)\(window) at \(percent)%"
        // VoiceOver hears the same figures the item shows.
        let shown = text.isEmpty ? "" : ". Showing \(text)"
        button.setAccessibilityLabel("Quiver. \(limit.title)\(window) limit at \(percent) percent\(shown)")
    }

    @objc private func toggle() {
        panel.isVisible ? close() : open()
    }

    private func open() {
        guard let button = item.button, let window = button.window else { return }
        let anchor = window.convertToScreen(button.frame)
        let size = panel.contentViewController?.preferredContentSize ?? CGSize(width: 340, height: 300)
        panel.setContentSize(size)
        let anchorScreen = NSScreen.screens.first { $0.frame.intersects(anchor) }
        panelScreen.screen = anchorScreen ?? NSScreen.main
        if anchorScreen != nil {
            panel.setFrameTopLeftPoint(NSPoint(x: anchor.midX - size.width / 2, y: anchor.minY - 6))
        } else if let screen = NSScreen.main {
            // macOS hides a status item that overflows behind the notch or
            // that the user switched off, parking it far off-screen. Anchoring
            // to it would open the panel off-screen too, so use the top-right
            // corner, where the item would have been.
            let visible = screen.visibleFrame
            panel.setFrameTopLeftPoint(NSPoint(x: visible.maxX - size.width - 8, y: visible.maxY - 6))
        }
        panel.makeKeyAndOrderFront(nil)
        // Any way the panel hides without close() leaves the last pair
        // installed; drop it so each open holds exactly one pair.
        removeClickMonitors()
        // Clicks in other apps arrive at the global monitor; clicks in
        // Quiver's own windows only at a local one. Either closes the panel,
        // except a click on the panel itself, on a popover it hosts or one
        // nested deeper (a child window like the fan popover's, or a select
        // opened inside one), or on the status item (which toggles it).
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            Task { @MainActor in self?.close() }
        }
        localClickMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] event in
            if let self, !self.hosts(event.window) { self.close() }
            return event
        }
    }

    /// Whether `window` is the panel, the status item's own window, or a
    /// popover anywhere down the panel's child chain — a select's list
    /// inside the fan popover is the panel's grandchild, not a direct
    /// child.
    private func hosts(_ window: NSWindow?) -> Bool {
        var window = window
        while let current = window {
            if current === panel || current === item.button?.window { return true }
            window = current.parent
        }
        return false
    }

    /// Resizes the open panel to its content, keeping the top edge where
    /// `open()` put it, under the status item.
    private func fitPanel() {
        guard panel.isVisible, let size = panel.contentViewController?.preferredContentSize,
              size != panel.contentRect(forFrameRect: panel.frame).size else { return }
        let topLeft = NSPoint(x: panel.frame.minX, y: panel.frame.maxY)
        panel.setContentSize(size)
        panel.setFrameTopLeftPoint(topLeft)
    }

    private func close() {
        panel.orderOut(nil)
        removeClickMonitors()
    }

    private func removeClickMonitors() {
        for monitor in [outsideClickMonitor, localClickMonitor].compactMap({ $0 }) {
            NSEvent.removeMonitor(monitor)
        }
        outsideClickMonitor = nil
        localClickMonitor = nil
    }

    private func openMainWindow() {
        close()
        NSApp.activate()
        if let open = model.openMainWindow {
            open()
        } else if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "main" }) {
            window.makeKeyAndOrderFront(nil)
        }
    }
}

/// The screen the menu bar panel opens on, set by `StatusItemController`
/// before it shows the panel. Views outside the panel (the Menu bar page's
/// preview) leave it unset and fall back to `NSScreen.main`.
@MainActor @Observable
final class PanelScreen {
    var screen: NSScreen?
}

extension EnvironmentValues {
    @Entry var panelScreen: PanelScreen? = nil
}

/// The status item's gauge, drawn so it follows the menu bar's appearance.
enum LimitRing {
    static func image(fraction: Double, critical: Bool) -> NSImage {
        let fraction = max(0, min(1, fraction))
        let image = NSImage(size: NSSize(width: 16, height: 16), flipped: false) { rect in
            let ring = rect.insetBy(dx: 2.5, dy: 2.5)
            let center = NSPoint(x: ring.midX, y: ring.midY)
            let radius = ring.width / 2
            let track = NSBezierPath(ovalIn: ring)
            track.lineWidth = 2.2
            (critical ? NSColor.secondaryLabelColor : NSColor.black.withAlphaComponent(0.3)).setStroke()
            track.stroke()
            guard fraction > 0 else { return true }
            let arc = NSBezierPath()
            arc.appendArc(withCenter: center, radius: radius, startAngle: 90,
                          endAngle: 90 - 360 * fraction, clockwise: true)
            arc.lineWidth = 2.2
            arc.lineCapStyle = .round
            (critical ? NSColor(Theme.danger) : NSColor.black).setStroke()
            arc.stroke()
            return true
        }
        // A template image takes the menu bar's own colour; the red one can't.
        image.isTemplate = !critical
        return image
    }
}

final class MenuBarPanel: NSPanel {
    /// ⌘W reaches the panel through PopupKeyRouting as a synthesized Esc:
    /// the controller's close removes the click monitors a bare
    /// `window.close()` would leave behind.
    var onEscape: (() -> Void)?

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53,
           event.modifierFlags.intersection(.deviceIndependentFlagsMask).isEmpty {
            onEscape?()
        } else {
            super.keyDown(with: event)
        }
    }

    init(contentViewController: NSViewController) {
        super.init(contentRect: .zero,
                   styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: true)
        self.contentViewController = contentViewController
        isFloatingPanel = true
        level = .statusBar
        hasShadow = true
        isOpaque = false
        backgroundColor = .clear
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    }

    override var canBecomeKey: Bool { true }
}

struct MenuBarContent: View {
    @Environment(AppModel.self) private var app
    var openMain: () -> Void
    /// Closes the panel without opening anything: what a launched row does.
    var dismiss: () -> Void = {}
    /// The Menu bar page's preview: draw this tab, whatever the popup
    /// itself has open, even while the tab is switched off.
    var previewTab: PopupTab? = nil

    private var tab: PopupTab { previewTab ?? app.popupTabs.current }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // The tabs where the title was; one per-tab action, open and
            // quit as icons beside them, so the panel has no button row
            // at the bottom.
            HStack(spacing: 12) {
                PopupTabBar(current: tab)
                Spacer()
                // Usage's action is Refresh; Compute's is the fans — its
                // charts tick on their own, so a reload buys nothing.
                switch tab {
                case .usage:
                    PanelIconButton(title: "Refresh", symbol: "arrow.clockwise") { tab.refresh(app) }
                        .disabled(tab.isRefreshing(app))
                case .compute:
                    PanelFanButton()
                }
                PanelIconButton(title: "Open Quiver", symbol: "macwindow", action: openMain)
                PanelIconButton(title: "Quit Quiver", symbol: "power") { NSApp.terminate(nil) }
            }
            tab.content(openMain: openMain, dismiss: dismiss)
        }
        .padding(16)
        .frame(width: 340)
        // TokenTracker's dialog: bg.dialog at radius 16 with the 1px ring in
        // the border colour; the drop comes from the panel's window shadow,
        // which follows this shape. Solid rather than glass, so the theme's
        // text colours keep their contrast whatever the wallpaper is.
        .background(Theme.dialog, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .clipShape(.rect(cornerRadius: Theme.cardRadius))
    }
}

/// The shown tabs as a segmented control, or the one tab's name alone
/// when only one is switched on.
private struct PopupTabBar: View {
    @Environment(AppModel.self) private var app
    var current: PopupTab

    var body: some View {
        let tabs = app.popupTabs.visible
        if tabs.count > 1 {
            Tabs(options: tabs.map { ($0, $0.title) },
                 selection: Binding(get: { current }, set: { app.popupTabs.selected = $0 }),
                 style: .segmented)
        } else {
            Text(current.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
        }
    }
}

/// The Usage tab: the incidents line, every shown limit, and spend.
struct UsageTabContent: View {
    @Environment(AppModel.self) private var app
    @Environment(\.panelScreen) private var panelScreen
    var openMain: () -> Void
    var dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if app.popup.showIncidents { ServiceStatusMenuLine(openMain: openMain) }
            // Everything else stays put; the limits scroll once there are
            // more than the screen can hold. Fixed vertically, the scroll
            // view takes the rows' own height, capped by the frame, so a
            // short list leaves no empty space above the spend donut
            // however much height the panel or the Menu bar page offers.
            ScrollView { PanelLimits(dismiss: dismiss) }
                .scrollIndicators(.automatic)
                .scrollBounceBehavior(.basedOnSize)
                .frame(maxHeight: Self.limitsMaxHeight(on: panelScreen?.screen))
                .fixedSize(horizontal: false, vertical: true)
            if app.popup.showStats, let spend = app.overview?.spend {
                Divider()
                SpendDonut(slices: spend)
            }
        }
    }

    /// The screen's height less the header, status line and spend donut.
    /// `screen` is the one the panel opened on; the main screen stands in
    /// when it is unknown, as in the Menu bar page's preview.
    static func limitsMaxHeight(on screen: NSScreen?) -> CGFloat {
        max(220, ((screen ?? NSScreen.main)?.visibleFrame.height ?? 800) - 430)
    }
}

/// A row's logo as the launch affordance: the desktop app when there is
/// one, else the CLI in a terminal — the same LaunchTarget the Accounts
/// page's buttons resolve. Disabled, and not rendered as a button, when
/// neither is available; a launch closes the panel like Quit's neighbours.
private struct LogoLaunchButton: View {
    var target: LaunchTarget
    var dismiss: () -> Void
    var size: CGFloat = 14
    @State private var hovering = false

    var body: some View {
        Button {
            Task {
                do {
                    if try await target.openPreferred() != .none { dismiss() }
                } catch {
                    // The panel has no error area; the system sound is it.
                    NSSound.beep()
                }
            }
        } label: {
            HarnessIcon(harness: target.harness, size: size)
                .opacity(hovering && target.canLaunch ? 1 : 0.8)
        }
        .buttonStyle(.plain)
        .disabled(!target.canLaunch)
        .onHover { hovering = $0 }
        .help(help)
        .accessibilityLabel(help)
    }

    /// One phrasing for the tooltip and the VoiceOver label.
    private var help: String {
        !target.canLaunch
            ? "\(target.title) isn't installed"
            : target.appPath != nil
            ? "Open \(target.title) app"
            : "Open \(target.title) in Terminal"
    }
}

/// A 14pt header icon that brightens under the pointer, named by its tooltip
/// and VoiceOver label.
private struct PanelIconButton: View {
    var title: String
    var symbol: String
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(title, systemImage: symbol, action: action)
            .labelStyle(.iconOnly)
            .font(Theme.font(size: 14))
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(width: 22, height: 22)
            .contentShape(.rect)
            .plainButton(cornerRadius: Theme.smallRadius)
            .onHover { hovering = $0 }
            .help(title)
    }
}

/// Every account's and harness's windows in the order dragged on the
/// Menu bar page, less whatever the page hides.
private struct PanelLimits: View {
    @Environment(AppModel.self) private var app
    /// Closes the panel after a logo launches something.
    var dismiss: () -> Void = {}

    /// One shown account or harness row, as the popup lists them.
    private enum PanelEntry: Identifiable {
        case account(AccountsModel.Card)
        case harness(OtherLimitsModel.Row)

        var id: String {
            switch self {
            case .account(let card): "account:\(card.id)"
            case .harness(let row): "harness:\(row.id)"
            }
        }
    }

    /// Accounts the popup shows, with something left to show: a shown
    /// window, or no windows at all (so its status line explains why).
    private var cards: [AccountsModel.Card] {
        app.starredAccountCards.filter { card in
            let source = card.profile.id, harness = card.profile.vendor.rawValue
            guard app.popup.shows(source: source, harness: harness) else { return false }
            guard let windows = card.usage?.windows, !windows.isEmpty else { return true }
            let labels = windows.map(\.label)
            return windows.contains {
                app.popup.shows(window: $0.label, source: source, harness: harness,
                                allWindows: labels)
            }
        }
    }

    /// Other harnesses that are shown and have a shown window left.
    private var others: [OtherLimitsModel.Row] {
        app.others.rows.filter { row in
            let windows = row.limit.allWindows.map(\.window)
            return row.limit.status == .ok && app.popup.shows(source: row.id, harness: row.name)
                && row.limit.allWindows.contains {
                    app.popup.shows(window: $0.window, source: row.id, harness: row.name,
                                    allWindows: windows)
                }
        }
    }

    /// Every shown source in the dragged order, accounts and other
    /// harnesses interleaved where the user put them.
    private var entries: [PanelEntry] {
        let sources = cards.map { (source: $0.profile.id, harness: $0.profile.vendor.rawValue) }
            + others.map { (source: $0.id, harness: $0.name) }
        return app.popup.ordered(sources).compactMap { source in
            if let card = cards.first(where: { $0.profile.id == source }) {
                return .account(card)
            }
            if let row = others.first(where: { $0.id == source }) {
                return .harness(row)
            }
            return nil
        }
    }

    var body: some View {
        let others = others
        VStack(alignment: .leading, spacing: 12) {
            ForEach(entries) { entry in
                switch entry {
                case .account(let card): PanelAccountRow(card: card, dismiss: dismiss)
                case .harness(let row): PanelOtherRow(row: row, dismiss: dismiss)
                }
            }
            // Limits exist but every one is hidden: say where to bring them
            // back, ahead of "nothing starred", which would read as nothing to show.
            let available = !app.starredAccountCards.isEmpty || app.others.rows.contains { $0.limit.status == .ok }
            if available && cards.isEmpty && others.isEmpty {
                Text("Every limit is hidden in Menu bar.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else if cards.isEmpty && others.isEmpty {
                Text(app.starredAccountCards.isEmpty && app.others.cards.isEmpty
                     ? "Nothing starred. Star a harness on Harnesses to see its limits here."
                     : "No limits to show yet.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Spend by harness for today, yesterday or the last 30 days.
private struct SpendDonut: View {
    @Environment(AppModel.self) private var app
    var slices: [SpendSlice]
    @State private var period: SpendSlice.Period = .today

    var slice: SpendSlice? { slices.first { $0.period == period } }

    /// Every provider's tokens without a price, hidden ones included: the
    /// total leaves them all out, so the note must count them all.
    var unpriced: Int { slice?.byHarness.values.reduce(0) { $0 + $1.unpricedTokens } ?? 0 }

    var parts: [(harness: String, totals: UsageTotals)] {
        guard let slice else { return [] }
        let known = Theme.harnessOrder.compactMap { id in slice.byHarness[id].map { (id, $0) } }
        let rest = slice.byHarness.filter { !Theme.harnessOrder.contains($0.key) }.sorted { $0.key < $1.key }.map { ($0.key, $0.value) }
        let shown = (known + rest).filter { $0.1.totalTokens > 0 && !app.providers.isHidden($0.0) }
        // In the order chosen in Settings.
        return app.providers.ordered(shown.map(\.0)).compactMap { id in shown.first { $0.0 == id } }
    }

    /// Every provider's spend: hiding one in Settings hides its entry, not its money.
    var totalCost: Double { slice?.costUSD ?? 0 }
    /// Spend of providers hidden in Settings, said in a line under the legend.
    var hiddenCost: Double { totalCost - parts.reduce(0) { $0 + $1.totals.costUSD } }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Tabs(options: [(SpendSlice.Period.today, "Today"), (.yesterday, "Yesterday"), (.thirtyDays, "30 days")],
                 selection: $period, style: .period)
            HStack(spacing: 16) {
                ZStack {
                    // Every priced provider hidden still has money in the
                    // total, but no slice to draw: show the empty ring.
                    if parts.contains(where: { $0.totals.costUSD > 0 }) {
                        Chart(parts, id: \.harness) { part in
                            SectorMark(angle: .value("Spend", part.totals.costUSD),
                                       innerRadius: .ratio(0.68), angularInset: 1.5)
                                .cornerRadius(3)
                                .foregroundStyle(Theme.harness(part.harness))
                        }
                    } else {
                        Circle().stroke(Theme.usageTrack, lineWidth: 14).padding(7)
                    }
                    VStack(spacing: 0) {
                        Text(UsageFormat.dollars(totalCost))
                            .font(Theme.figure).monospacedDigit()
                            .foregroundStyle(Theme.accent)
                        Text("spent").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                }
                .frame(width: 104, height: 104)
                VStack(alignment: .leading, spacing: 5) {
                    if parts.isEmpty {
                        Text((slice?.byHarness.values.contains { $0.totalTokens > 0 } ?? false)
                             ? "Every provider with usage is hidden in Settings." : "No usage yet.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    ForEach(parts, id: \.harness) { part in
                        HStack(spacing: 6) {
                            ChartSwatch(harness: part.harness, size: 6)
                            HarnessIcon(harness: part.harness, size: 12)
                            Text(UsageFormat.harness(part.harness)).font(Theme.caption).foregroundStyle(Theme.textPrimary)
                            Spacer()
                            if part.totals.unpricedTokens > 0 {
                                Image(systemName: "questionmark.circle").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                    .help("\(UsageFormat.tokens(part.totals.unpricedTokens)) tokens with no list price")
                            }
                            Text(UsageFormat.dollars(part.totals.costUSD)).font(Theme.caption).monospacedDigit()
                                .foregroundStyle(Theme.textPrimary)
                        }
                    }
                }
            }
            if hiddenCost >= 0.01 {
                Text("Includes \(UsageFormat.dollars(hiddenCost)) from providers hidden in Settings.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if unpriced > 0 {
                // The total leaves these out, so say so rather than look complete.
                Text("Plus \(UsageFormat.tokens(unpriced)) tokens with no list price, not in the total.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

/// One account in the panel: its name, then every window it reports.
private struct PanelAccountRow: View {
    @Environment(AppModel.self) private var app
    var card: AccountsModel.Card
    var dismiss: () -> Void = {}

    var body: some View {
        let profile = card.profile
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                LogoLaunchButton(
                    target: LaunchTarget(kind: .account(profile), model: app.accounts,
                        installedApp: { app.apps.installedApp(for: $0) },
                        cliInstalled: { app.others.cliInstalled[$0] ?? app.accounts.cliOnPath[profile.vendor] }),
                    dismiss: dismiss)
                Text("\(card.profile.vendor.title): \(card.profile.name)").font(Theme.rowTitle)
                if let plan = card.usage?.plan { Badge(planTitle(plan)) }
            }
            if let usage = card.usage, !usage.windows.isEmpty {
                // A kept reading after a failed fetch gets the unavailable
                // treatment row by row; a stale ok reading merely dims.
                let failed = usage.isStale && usage.status != .ok
                ForEach(usage.windows.filter {
                            app.popup.shows(window: $0.label, source: card.profile.id,
                                            harness: card.profile.vendor.rawValue,
                                            allWindows: usage.windows.map(\.label))
                        }, id: \.label) { window in
                    PanelLimitRow(label: window.label, percent: window.usedPercent, resetsAt: window.resetsAt,
                                  unavailable: failed, asOf: usage.fetchedAt)
                }
                .opacity(usage.isStale && !failed ? 0.55 : 1)
                // A live reading still names when its sign-in lapses.
                if usage.isStale || usage.signInExpiresAt != nil { StatusLine(usage: usage, card: card) }
            } else {
                StatusLine(usage: card.usage, card: card)
            }
        }
    }
}

/// A harness swe reports, laid out like an account: logo, name and plan,
/// then each shown window, then any extra usage balance.
private struct PanelOtherRow: View {
    @Environment(AppModel.self) private var app
    var row: OtherLimitsModel.Row
    var dismiss: () -> Void = {}

    var body: some View {
        // swe hands back its last reading, status ok, for up to a day when a
        // provider fails; dim one that old.
        let old = row.limit.fetchedAt.map {
            Date.now.timeIntervalSince($0)
                > LimitReading.sweStaleLimit(freshFor: app.others.acceptedCacheTTL) } ?? true
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                LogoLaunchButton(
                    target: LaunchTarget(kind: row.profile.map { .account($0) }
                                             ?? .harness(name: row.name, command: row.command),
                        model: app.accounts,
                        installedApp: { app.apps.installedApp(for: $0) },
                        cliInstalled: { _ in row.cliInstalled }),
                    dismiss: dismiss)
                Text(row.accountTitle).font(Theme.rowTitle)
                if let plan = row.limit.planType { Badge(planTitle(plan)) }
            }
            ForEach(row.limit.allWindows.filter {
                        app.popup.shows(window: $0.window, source: row.id, harness: row.name,
                                        allWindows: row.limit.allWindows.map(\.window))
                    }, id: \.window) { window in
                PanelLimitRow(label: window.window.isEmpty ? "Usage" : UsageFormat.window(window.window),
                              percent: Double(window.usedPercent), resetsAt: window.resetAt,
                              unavailable: window.unavailable, asOf: window.asOf)
            }
            .opacity(old ? 0.55 : 1)
            if let onDemand = row.limit.onDemand {
                Text("On-demand \(onDemand.value) · \(onDemand.detail)")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .opacity(old ? 0.55 : 1)
            }
            if let balance = row.limit.extraUsageUSD {
                // As old as the windows above it, so dimmed with them.
                Text("Extra usage balance \(UsageFormat.balance(balance))")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .opacity(old ? 0.55 : 1)
            }
            // Like an account's stale reading: say how old it is.
            if old, let fetched = row.limit.fetchedAt {
                Text("Fetched \(UsageFormat.ago(fetched, now: .now)), couldn't refresh")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .help(row.limit.fetchedAt.map { "Fetched \(UsageFormat.ago($0, now: .now))" } ?? "Fetch time unknown")
    }
}

/// One limit window on a single line: name, bar, percentage, reset.
private struct PanelLimitRow: View {
    var label: String
    var percent: Double
    var resetsAt: Date?

    /// The reading is a kept older one after a failed or unread fetch:
    /// red label, greyed bar and value, its age for the reset time.
    var unavailable: Bool = false
    var asOf: Date? = nil

    var body: some View {
        let whole = Int(percent.rounded())
        HStack(spacing: 8) {
            Text(label).font(Theme.caption)
                .foregroundStyle(unavailable ? Theme.dangerText : Theme.textPrimary)
                .lineLimit(1)
                .frame(width: 86, alignment: .leading)
            UsageTrack(fraction: percent / 100, color: Theme.usage(whole))
                .opacity(unavailable ? 0.45 : 1)
            Text(unavailable && asOf == nil ? "—" : "\(whole)%")
                .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textPrimary)
                .opacity(unavailable ? 0.45 : 1)
                .frame(width: 34, alignment: .trailing)
            // Just the age: "as of 13h ago" doesn't fit the column, and the
            // red label and greyed bar already say the reading is old.
            Text(unavailable
                    ? (asOf.map { UsageFormat.ago($0, now: .now) } ?? "")
                    : (resetsAt.map { $0 > .now ? UsageFormat.resetIn($0, now: .now) : "" } ?? ""))
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .lineLimit(1)
            .frame(width: 64, alignment: .trailing)
        }
        .accessibilityElement(children: .combine)
    }
}
