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
                Text(row.aa?.name ?? row.logged)
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
                        // The model's name sits above its dot, clamped so
                        // a cheapest/most-expensive label stays inside.
                        Text(model.name)
                            .font(.system(size: 9, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(1).fixedSize()
                            .position(x: min(max(x, 30), geo.size.width - 30), y: 5)
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
/// with arbitrary content. `models` nil (ledger down) leaves the view
/// plain.
private struct ModelCardHover: ViewModifier {
    var models: ModelsModel?
    var logged: String
    var link: (() -> Void)?

    @State private var open = false
    @State private var pinned = false
    @State private var rest: Task<Void, Never>?
    @State private var hovering = false

    func body(content: Content) -> some View {
        if let models {
            content
                .contentShape(.rect)
                .onHover { inside in
                    if inside == hovering { return }
                    hovering = inside
                    if inside {
                        rest = Task {
                            try? await Task.sleep(for: .milliseconds(150))
                            guard !Task.isCancelled else { return }
                            open = true
                        }
                    } else {
                        rest?.cancel()
                        if !pinned { open = false }
                    }
                }
                .onTapGesture {
                    rest?.cancel()
                    if open && pinned { open = false }
                    else if open { pinned = true }
                }
                .onChange(of: open) { _, open in if !open { pinned = false } }
                .popover(isPresented: $open, arrowEdge: .bottom) {
                    ModelCard(row: models.card(for: logged), period: models.period, link: link)
                        .task { await models.load() }
                }
        } else {
            content
        }
    }
}

extension View {
    /// Hover a model label for its card: benchmark, cost rank and the
    /// user's own usage, from the shared `ModelsModel`.
    func modelCard(_ models: ModelsModel?, for logged: String,
                   link: (() -> Void)? = nil) -> some View {
        modifier(ModelCardHover(models: models, logged: logged, link: link))
    }
}
