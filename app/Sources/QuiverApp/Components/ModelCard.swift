import AppKit
import QuiverCore
import SwiftUI

/// The hover card every model label opens, laid out like Devin's model
/// picker card: the foundation model's name with the effort the user ran
/// it at in grey, who made it and which harnesses ran it, the headline
/// benchmarks and the user's tokens, then the cost scale and the price
/// tiles. Unmatched names get a "Link…" action instead. The same card
/// hangs off Models bars and rows, Overview's top models and Pricing's
/// model cells.
struct ModelCard: View {
    var row: ModelsModel.Row
    /// The period "Your use" reads from (the Models page's own).
    var period: OverviewPeriod
    /// Opens the link sheet; nil on pages that can't offer it.
    var link: (() -> Void)? = nil

    static let width: CGFloat = 300

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                ModelName(name: title, qualifier: row.qualifier, font: .system(size: 15))
                Text(subtitle)
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(2)
            }
            if let aa = row.aa {
                VStack(spacing: 8) {
                    line("Intelligence", aa.intelligence.map { "\(Int($0.rounded()))" })
                    line("Coding", aa.coding.map { "\(Int($0.rounded()))" })
                    line("Speed", aa.speed.flatMap { $0 > 0 ? "\(Int($0.rounded())) t/s" : nil })
                    line("Your use", row.tokens > 0
                         ? "\(UsageFormat.tokens(row.tokens)) tokens \(periodLabel)" : nil)
                }
                Divider().overlay(Theme.border)
                cost(aa)
            } else {
                Text("Not on Artificial Analysis")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                if row.tokens > 0 {
                    line("Your use", "\(UsageFormat.tokens(row.tokens)) tokens \(periodLabel)")
                }
                if let link {
                    Button("Link…", action: link).secondaryButton()
                }
            }
        }
        .padding(16)
        .frame(width: Self.width)
        .fixedSize(horizontal: false, vertical: true)
    }

    /// The foundation model's name, without AA's "(Max, Default
    /// Fallback)" tail: the effort shown is the one the user ran.
    private var title: String {
        row.aa.map(ModelMatcher.displayName) ?? row.logged
    }

    /// "Anthropic · Claude Code, Devin": the vendor, then the harnesses
    /// that ran the model in the period.
    private var subtitle: String {
        let vendor = row.aa.map { ModelVendor.of($0) } ?? ModelVendor.of(logged: row.logged)
        return ([vendor.name] + row.harnesses.map { UsageFormat.harness($0) })
            .joined(separator: " · ")
    }

    private var periodLabel: String {
        period.title.prefix(1).lowercased() + period.title.dropFirst()
    }

    /// A label on the left, its value in grey on the right ("—" when
    /// there is none), like the picker card's Effort row.
    private func line(_ label: String, _ value: String?) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 12)
            Text(value ?? "—")
                .font(Theme.bodySmall).monospacedDigit()
                .foregroundStyle(value == nil ? Theme.textMuted : Theme.textSecondary)
                .lineLimit(1)
        }
    }

    /// "Cost", the green→orange→purple scale with the model's name over
    /// its dot, and the blended, input and output prices as tiles.
    @ViewBuilder private func cost(_ model: AALanguageModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Cost").font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            if let rank = row.costPercentile {
                CostDotScale(name: title, rank: rank)
            }
            HStack(spacing: 6) {
                priceTile("Blended", model.priceBlended, highlighted: true)
                priceTile("Input", model.priceInput)
                priceTile("Output", model.priceOutput)
            }
        }
    }

    private func priceTile(_ label: String, _ price: Double?,
                           highlighted: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(price.map(Self.price) ?? "—")
                    .font(.system(size: 13, weight: .medium)).monospacedDigit()
                    .foregroundStyle(price == nil ? Theme.textMuted : Theme.textPrimary)
                if price != nil {
                    Text("/ 1M").font(.system(size: 11)).foregroundStyle(Theme.textMuted)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 8).padding(.vertical, 7)
        .background(highlighted ? Theme.subtle : .clear, in: .rect(cornerRadius: 8))
    }

    /// "$4", "$0.75", "$1.25": whole dollars lose their cents.
    static func price(_ value: Double) -> String {
        value == value.rounded() ? "$\(Int(value))" : String(format: "$%.2f", value)
    }
}

/// The cost scale: a gradient track with a white dot centred on it at
/// the model's rank, and the model's name over the dot, nudged in so it
/// never runs past either end.
struct CostDotScale: View {
    var name: String
    var rank: Double

    private static let track: CGFloat = 8
    private static let dot: CGFloat = 10
    private static let labelFont = NSFont.systemFont(ofSize: 11, weight: .semibold)

    var body: some View {
        VStack(spacing: 5) {
            GeometryReader { geo in
                let width = min((name as NSString).size(withAttributes: [.font: Self.labelFont])
                    .width.rounded(.up), geo.size.width)
                Text(name)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                    .frame(width: width)
                    .position(x: min(max(rank * geo.size.width, width / 2),
                                     geo.size.width - width / 2),
                              y: geo.size.height / 2)
            }
            .frame(height: 14)
            GeometryReader { geo in
                Capsule()
                    .fill(LinearGradient(colors: [Color(hex: "#2fa35a"), Color(hex: "#e8913a"),
                                                  Color(hex: "#a46ae0")],
                                         startPoint: .leading, endPoint: .trailing))
                    .frame(height: Self.track)
                    .frame(maxHeight: .infinity)
                    .overlay(alignment: .leading) {
                        Circle().fill(.white).frame(width: Self.dot, height: Self.dot)
                            .shadow(color: .black.opacity(0.25), radius: 1, y: 0.5)
                            .offset(x: min(max(rank * geo.size.width - Self.dot / 2, 0),
                                           geo.size.width - Self.dot))
                    }
            }
            .frame(height: Self.dot)
        }
    }
}

/// A model's name with the effort or mode the user ran it at in grey
/// after it: "Claude Opus 5.5 High · Fast". Used by the card and the
/// Models table so the qualifier reads the same everywhere.
struct ModelName: View {
    var name: String
    var qualifier: String?
    var font: Font = Theme.bodySmall

    var body: some View {
        let tail = Text(qualifier.map { " \($0)" } ?? "").foregroundStyle(Theme.textSecondary)
        Text("\(Text(name).foregroundStyle(Theme.textPrimary))\(tail)")
            .font(font)
            .lineLimit(1).truncationMode(.middle)
    }
}

/// Which model card is open. Opening one closes any other, so sliding
/// down the table never leaves the last row's card fighting the new one.
@MainActor @Observable
final class ModelCardFocus {
    static let shared = ModelCardFocus()
    var owner: UUID?
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
    @State private var id = UUID()

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
                            try? await Task.sleep(for: ChartCard.openDelay)
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
                .onChange(of: ModelCardFocus.shared.owner) { _, owner in
                    if open, owner != id { open = false }
                }
                .onChange(of: open) { _, isOpen in
                    if isOpen {
                        ModelCardFocus.shared.owner = id
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
                        if ModelCardFocus.shared.owner == id { ModelCardFocus.shared.owner = nil }
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
