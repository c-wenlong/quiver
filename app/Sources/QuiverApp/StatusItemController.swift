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
        show(nil)
        Task { [weak self] in
            let changes = Observations { [weak model] in model?.limits.tightest }
            for await tightest in changes { self?.show(tightest) }
        }
    }

    /// The tightest limit at a glance: a ring filled to its used share and
    /// the percentage. Red from 90%, like the bars; monochrome below.
    private func show(_ tightest: LimitReading??) {
        guard let button = item.button else { return }
        guard let reading = tightest ?? nil else {
            button.image = NSImage(systemSymbolName: "gauge.with.dots.needle.67percent",
                                   accessibilityDescription: "Quiver")
            button.attributedTitle = NSAttributedString()
            button.toolTip = "Quiver"
            return
        }
        let percent = reading.percent
        button.image = LimitRing.image(fraction: reading.usedPercent / 100, critical: percent >= 90)
        button.attributedTitle = NSAttributedString(
            string: " \(percent)%",
            attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)])
        let window = reading.window.isEmpty ? "" : " \(reading.window)"
        button.toolTip = "Tightest limit: \(reading.title)\(window) at \(percent)%"
        button.setAccessibilityLabel("Quiver. \(reading.title)\(window) limit at \(percent) percent")
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
                Text("Quiver").font(Theme.rowTitle)
                Spacer()
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task { await app.accounts.load(force: true) }
                    Task { await app.others.load() }
                    Task { await app.overview?.load(scan: true) }
                }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
                .disabled(app.accounts.isLoading)
            }
            ForEach(app.accounts.cards) { card in
                PanelAccountRow(card: card)
            }
            if app.accounts.cards.isEmpty {
                Text("No accounts yet.").foregroundStyle(.secondary)
            }
            let others = app.others.rows.filter { $0.limit.status == .ok }
            if !others.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Other harnesses").font(Theme.rowTitle)
                    ForEach(others) { row in
                        PanelLimitRow(label: row.name.capitalized, window: row.limit.window,
                                      percent: Double(row.limit.usedPercent), resetsAt: row.limit.resetAt)
                    }
                }
            }
            if let spend = app.overview?.spend {
                Divider()
                SpendDonut(slices: spend)
            }
            Divider()
            HStack {
                Button("Open Quiver", action: openMain).buttonStyle(PrimaryButtonStyle())
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }.buttonStyle(SecondaryButtonStyle())
            }
        }
        .padding(16)
        .frame(width: 340)
        .glassEffect(in: .rect(cornerRadius: 18))
    }
}

/// Spend by harness for today, yesterday or the last 30 days.
private struct SpendDonut: View {
    var slices: [SpendSlice]
    @State private var period: SpendSlice.Period = .today

    var slice: SpendSlice? { slices.first { $0.period == period } }

    var unpriced: Int { parts.reduce(0) { $0 + $1.totals.unpricedTokens } }

    var parts: [(harness: String, totals: UsageTotals)] {
        guard let slice else { return [] }
        let known = Theme.harnessOrder.compactMap { id in slice.byHarness[id].map { (id, $0) } }
        let rest = slice.byHarness.filter { !Theme.harnessOrder.contains($0.key) }.sorted { $0.key < $1.key }.map { ($0.key, $0.value) }
        return (known + rest).filter { $0.1.totalTokens > 0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Tabs(options: [(SpendSlice.Period.today, "Today"), (.yesterday, "Yesterday"), (.thirtyDays, "30 days")],
                 selection: $period)
            HStack(spacing: 16) {
                ZStack {
                    if (slice?.costUSD ?? 0) > 0 {
                        Chart(parts, id: \.harness) { part in
                            SectorMark(angle: .value("Spend", part.totals.costUSD),
                                       innerRadius: .ratio(0.68), angularInset: 1.5)
                                .cornerRadius(3)
                                .foregroundStyle(Theme.harness(part.harness))
                        }
                    } else {
                        Circle().stroke(Color.primary.opacity(0.1), lineWidth: 14).padding(7)
                    }
                    VStack(spacing: 0) {
                        Text(UsageFormat.dollars(slice?.costUSD ?? 0))
                            .font(.system(size: 17, weight: .semibold)).monospacedDigit()
                            .foregroundStyle(Theme.accent)
                        Text("spent").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .frame(width: 104, height: 104)
                VStack(alignment: .leading, spacing: 5) {
                    if parts.isEmpty {
                        Text("No usage yet.").font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(parts, id: \.harness) { part in
                        HStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 2).fill(Theme.harness(part.harness)).frame(width: 8, height: 8)
                            Text(UsageFormat.harness(part.harness)).font(.caption)
                            Spacer()
                            if part.totals.unpricedTokens > 0 {
                                Image(systemName: "questionmark.circle").font(.caption2).foregroundStyle(.secondary)
                                    .help("\(UsageFormat.tokens(part.totals.unpricedTokens)) tokens with no list price")
                            }
                            Text(UsageFormat.dollars(part.totals.costUSD)).font(.caption).monospacedDigit()
                        }
                    }
                }
            }
            if unpriced > 0 {
                // The total leaves these out, so say so rather than look complete.
                Text("Plus \(UsageFormat.tokens(unpriced)) tokens with no list price, not in the total.")
                    .font(.caption).foregroundStyle(.secondary)
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
