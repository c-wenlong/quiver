import AppKit
import QuiverCore
import SwiftUI

/// The hover card every model label opens: the AA model it matched, its
/// cost rank against the whole list, the three headline benchmarks, and
/// the user's own tokens in the period. Unmatched names get a "Link…"
/// action instead of benchmarks. The same card hangs off Models bars and
/// rows, Overview's top models and Pricing's model cells.
struct ModelCard: View {
    var row: ModelsModel.Row
    /// The period "Your use" reads from (the Models page's own).
    var period: OverviewPeriod
    /// Opens the link sheet; nil on pages that can't offer it.
    var link: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Text(title)
                    .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                if let qualifier = row.qualifier {
                    Badge(qualifier)
                }
            }
            Text(subtitle)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .lineLimit(2)
            if let aa = row.aa {
                cost(aa)
                tiles(aa)
            } else {
                Text("Not on Artificial Analysis")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                if let link {
                    Button("Link…", action: link).secondaryButton()
                }
            }
            HStack {
                Text("Data from Artificial Analysis")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                Spacer()
                Text("Your use: \(UsageFormat.tokens(row.tokens)) tokens \(periodLabel)")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
        .padding(14)
        .frame(width: 300)
        .fixedSize(horizontal: false, vertical: true)
    }

    /// The AA name, minus a trailing "(max)" only when the badge already
    /// says exactly that; a "(preview)" or any other tail stays.
    private var title: String {
        guard let aa = row.aa else { return row.logged }
        guard let qualifier = row.qualifier, aa.name.hasSuffix(")"),
              let paren = aa.name.range(of: " (", options: .backwards),
              aa.name[paren.upperBound..<aa.name.index(before: aa.name.endIndex)]
                  .caseInsensitiveCompare(qualifier) == .orderedSame
        else { return aa.name }
        return String(aa.name[..<paren.lowerBound])
    }

    /// "Anthropic · used by Claude Code, Devin" — the vendor first, then
    /// the harnesses that ran the model in the period.
    private var subtitle: String {
        let vendor = row.aa.map { ModelVendor.of($0) } ?? ModelVendor.of(logged: row.logged)
        var parts = [vendor.name]
        let harnesses = row.harnesses.map { UsageFormat.harness($0) }
        if !harnesses.isEmpty { parts.append("used by \(harnesses.joined(separator: ", "))") }
        return parts.joined(separator: " · ")
    }

    private var periodLabel: String {
        period.title.prefix(1).lowercased() + period.title.dropFirst()
    }

    /// The green→orange→purple scale with a dot at the model's rank.
    @ViewBuilder private func cost(_ model: AALanguageModel) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("Cost").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Spacer()
                if let price = model.priceBlended {
                    Text(UsageFormat.dollars(price))
                        .font(Theme.caption).monospacedDigit().foregroundStyle(Theme.textPrimary)
                    Text("/1M").font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            Text("vs. every model Artificial Analysis lists")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
            if let rank = row.costPercentile {
                GeometryReader { geo in
                    let x = rank * geo.size.width
                    ZStack(alignment: .leading) {
                        LinearGradient(colors: [Color(hex: "#2fa35a"), Color(hex: "#e8913a"),
                                                Color(hex: "#a46ae0")],
                                       startPoint: .leading, endPoint: .trailing)
                            .clipShape(Capsule())
                            .frame(height: 8)
                            .padding(.top, 14)
                        // The model's name sits above its dot, measured
                        // up front and capped to the bar so an edge dot
                        // or a long name can't push it out.
                        let labelWidth = min(
                            (model.name as NSString).size(withAttributes: [
                                .font: NSFont.systemFont(ofSize: 9, weight: .medium)]).width.rounded(.up),
                            max(0, geo.size.width))
                        Text(model.name)
                            .font(.system(size: 9, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(1).truncationMode(.middle)
                            .frame(width: labelWidth)
                            .position(x: min(max(x, labelWidth / 2),
                                             geo.size.width - labelWidth / 2),
                                      y: 5)
                        Circle().fill(.white).frame(width: 10, height: 10)
                            .overlay(Circle().strokeBorder(Theme.borderStrong, lineWidth: 1))
                            .position(x: min(max(x, 5), geo.size.width - 5), y: 18)
                    }
                }
                .frame(height: 26)
                HStack {
                    Text("Cheapest")
                    Spacer()
                    Text("Most expensive")
                }
                .font(.system(size: 9)).foregroundStyle(Theme.textMuted)
            }
        }
    }

    /// Intelligence / Coding / Speed, "—" where AA publishes nothing.
    private func tiles(_ model: AALanguageModel) -> some View {
        HStack(spacing: 8) {
            tile("Intelligence", model.intelligence.map { "\(Int($0.rounded()))" }, of: "/100")
            tile("Coding", model.coding.map { "\(Int($0.rounded()))" }, of: "/100")
            tile("Speed", model.speed.map { "\(Int($0.rounded()))" }, of: "t/s")
        }
    }

    private func tile(_ label: String, _ value: String?, of unit: String) -> some View {
        VStack(spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value ?? "—")
                    .font(Theme.rowTitle).monospacedDigit().foregroundStyle(Theme.textPrimary)
                if value != nil {
                    Text(unit).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Theme.subtle, in: .rect(cornerRadius: Theme.blockRadius))
    }
}

/// The model card's open-on-rest, pin-on-click wrapper: HoverTip's feel
/// with arbitrary content, plus the Calendar card's pointer-watched safe
/// region so the pointer can cross the arrow gap into the card (and
/// reach its Link button). `models` nil (ledger down) leaves the view
/// plain. `row` hands over the caller's already-merged row — re-resolving
/// the logged name would lose the aliases folded into it.
private struct ModelCardHover: ViewModifier {
    // Optional: the app always provides it, an offscreen test render
    // does not need a whole AppModel for the stamp.
    @Environment(AppModel.self) private var app: AppModel?
    var models: ModelsModel?
    var logged: String
    var row: ModelsModel.Row?
    var link: (() -> Void)?

    /// The geometry key this instance registers under; each modifier has
    /// its own CardGeometry, so one key suffices.
    private static let key = "model"

    @State private var open = false
    @State private var pinned = false
    @State private var rest: Task<Void, Never>?
    @State private var watch: Task<Void, Never>?
    @State private var hovering = false
    @State private var geometry = CardGeometry()

    func body(content: Content) -> some View {
        // Read outside the popover's task so the card's usage reloads
        // when the overview's ledger re-scan count moves.
        let stamp = app?.overview?.completedScans
        if let models {
            content
                .contentShape(.rect)
                .background(HoverRegionView { view, attached in
                    geometry.register(chip: Self.key, view: view, attached: attached)
                })
                .onHover { inside in
                    if inside == hovering { return }
                    hovering = inside
                    if inside {
                        rest = Task { @MainActor in
                            try? await Task.sleep(for: .milliseconds(150))
                            guard !Task.isCancelled else { return }
                            open = true
                        }
                    } else {
                        // Leaving the anchor only cancels the show delay;
                        // once open, the pointer watcher owns the close.
                        rest?.cancel()
                    }
                }
                .onTapGesture {
                    rest?.cancel()
                    if open && pinned { open = false }
                    else if open { pinned = true }
                    else { open = true; pinned = true }
                }
                .onChange(of: open) { _, isOpen in
                    if isOpen {
                        watch?.cancel()
                        watch = Task { @MainActor in
                            var region = HoverSafeRegion()
                            while !Task.isCancelled {
                                region.anchor = geometry.anchorRect(for: Self.key)
                                region.card = geometry.cardRect(for: Self.key)
                                if !pinned,
                                   region.update(point: NSEvent.mouseLocation,
                                                 now: ContinuousClock.now) {
                                    open = false
                                    return
                                }
                                try? await Task.sleep(for: .milliseconds(50))
                            }
                        }
                    } else {
                        watch?.cancel()
                        watch = nil
                        pinned = false
                    }
                }
                .onDisappear {
                    rest?.cancel()
                    watch?.cancel()
                    open = false
                }
                // AppKit can resolve the binding to closed while the
                // pointer is inside the card's safe region; that case is
                // ignored, like the calendar's card binding.
                .popover(isPresented: Binding(
                    get: { open },
                    set: { newValue in
                        if newValue {
                            open = true
                        } else if !geometry.safeRect(for: Self.key)
                            .contains(NSEvent.mouseLocation) {
                            open = false
                        }
                    }), arrowEdge: .bottom) {
                    ModelCard(row: row ?? models.card(for: logged), period: models.period,
                              link: link.map { go in { open = false; go() } })
                        .background(HoverRegionView { view, attached in
                            geometry.register(card: Self.key, view: view, attached: attached)
                        })
                        .task { await models.loadIfNeeded(usageStamp: stamp) }
                }
        } else {
            content
        }
    }
}

extension View {
    /// Hover a model label for its card: benchmark, cost rank and the
    /// user's own usage, from the shared `ModelsModel`. Pages whose rows
    /// merge aliases pass the row itself so the card keeps the merged
    /// harnesses and tokens.
    func modelCard(_ models: ModelsModel?, for logged: String,
                   row: ModelsModel.Row? = nil,
                   link: (() -> Void)? = nil) -> some View {
        modifier(ModelCardHover(models: models, logged: logged, row: row, link: link))
    }
}
