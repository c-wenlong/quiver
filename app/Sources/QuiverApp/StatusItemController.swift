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
    private let model: AppModel

    init(model: AppModel) {
        self.model = model
        super.init()
        let content = MenuBarContent(openMain: { [weak self] in self?.openMainWindow() })
        let host = NSHostingController(rootView: content.environment(model))
        host.sizingOptions = [.preferredContentSize]
        panel = MenuBarPanel(contentViewController: host)
        if let button = item.button {
            button.action = #selector(toggle)
            button.target = self
            button.imagePosition = .imageLeading
        }
        show(model.menuBar, limit: nil, today: nil)
        Task { [weak self] in
            let changes = Observations { [weak model] in
                model.map { ($0.menuBar, $0.limits.readings, MenuBarDisplay.today(in: $0.overview?.spend)) }
            }
            for await change in changes {
                guard let (display, readings, today) = change else { continue }
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
        let window = limit.window.isEmpty ? "" : " \(limit.window)"
        button.toolTip = "\(limit.title)\(window) at \(percent)%"
        button.setAccessibilityLabel("Quiver. \(limit.title)\(window) limit at \(percent) percent")
    }

    @objc private func toggle() {
        panel.isVisible ? close() : open()
    }

    private func open() {
        guard let button = item.button, let window = button.window else { return }
        let anchor = window.convertToScreen(button.frame)
        let size = panel.contentViewController?.preferredContentSize ?? CGSize(width: 340, height: 300)
        panel.setContentSize(size)
        if NSScreen.screens.contains(where: { $0.frame.intersects(anchor) }) {
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
        // Clicks in other apps arrive at the global monitor; clicks in
        // Quiver's own windows only at a local one. Either closes the panel,
        // except a click on the panel itself or on the status item (which
        // toggles it).
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            Task { @MainActor in self?.close() }
        }
        localClickMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] event in
            if let self, event.window !== self.panel, event.window !== self.item.button?.window {
                self.close()
            }
            return event
        }
    }

    private func close() {
        panel.orderOut(nil)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Quiver").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                Spacer()
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task { await app.accounts.load(force: true) }
                    Task { await app.others.load() }
                    Task { await app.overview?.load(scan: true) }
                }
                .labelStyle(.iconOnly)
                .plainButton(cornerRadius: 4)
                .disabled(app.accounts.isLoading)
            }
            ServiceStatusMenuLine(openMain: openMain)
            // Everything else stays put; the limits scroll once there are
            // more than the screen can hold.
            ViewThatFits(in: .vertical) {
                PanelLimits()
                ScrollView { PanelLimits() }.scrollIndicators(.automatic)
            }
            .frame(maxHeight: Self.limitsMaxHeight)
            if let spend = app.overview?.spend {
                Divider()
                SpendDonut(slices: spend)
            }
            Divider()
            HStack {
                Button("Open Quiver", action: openMain).primaryButton()
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }.secondaryButton()
            }
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

    /// The screen's height less the header, status line, spend donut and buttons.
    static var limitsMaxHeight: CGFloat {
        max(220, (NSScreen.main?.visibleFrame.height ?? 800) - 480)
    }
}

/// Every account's windows, then the harnesses swe reports.
private struct PanelLimits: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(app.accounts.cards) { card in
                PanelAccountRow(card: card)
            }
            if app.accounts.cards.isEmpty {
                Text("No accounts yet.").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            let others = app.others.rows.filter { $0.limit.status == .ok }
            if !others.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Other harnesses").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    ForEach(others) { row in
                        // swe hands back its last reading, status ok, for up to
                        // a day when a provider fails; dim one that old.
                        let old = row.limit.fetchedAt.map { Date.now.timeIntervalSince($0) > LimitReading.sweStaleAfter } ?? true
                        PanelLimitRow(label: UsageFormat.harness(row.name), window: row.limit.window,
                                      percent: Double(row.limit.usedPercent), resetsAt: row.limit.resetAt)
                            .opacity(old ? 0.55 : 1)
                            .help(row.limit.fetchedAt.map { "Fetched \(UsageFormat.ago($0, now: .now))" } ?? "Fetch time unknown")
                    }
                }
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
                    if (totalCost) > 0 {
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
    var card: AccountsModel.Card

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Circle().fill(Color(hex: card.profile.color)).frame(width: 7, height: 7)
                Text("\(card.profile.vendor.title): \(card.profile.name)").font(Theme.rowTitle)
                if let plan = card.usage?.plan { Badge(planTitle(plan)) }
            }
            if let usage = card.usage, !usage.windows.isEmpty {
                ForEach(usage.windows, id: \.label) { window in
                    PanelLimitRow(label: window.label, percent: window.usedPercent, resetsAt: window.resetsAt)
                }
                .opacity(usage.isStale ? 0.55 : 1)
                if usage.isStale { StatusLine(usage: usage) }
            } else {
                StatusLine(usage: card.usage)
            }
        }
    }
}

/// One limit window on a single line: name, bar, percentage, reset.
private struct PanelLimitRow: View {
    var label: String
    var window: String? = nil
    var percent: Double
    var resetsAt: Date?

    var body: some View {
        let whole = Int(percent.rounded())
        HStack(spacing: 8) {
            HStack(spacing: 4) {
                Text(label).font(Theme.caption).foregroundStyle(Theme.textPrimary)
                if let window { Text(window).font(Theme.caption).foregroundStyle(Theme.textSecondary) }
            }
            .lineLimit(1)
            .frame(width: 86, alignment: .leading)
            UsageTrack(fraction: percent / 100, color: Theme.usage(whole))
            Text("\(whole)%").font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textPrimary)
                .frame(width: 34, alignment: .trailing)
            Text(resetsAt.map { $0 > .now ? UsageFormat.resetIn($0, now: .now) : "" } ?? "")
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .lineLimit(1)
            .frame(width: 64, alignment: .trailing)
        }
        .accessibilityElement(children: .combine)
    }
}
