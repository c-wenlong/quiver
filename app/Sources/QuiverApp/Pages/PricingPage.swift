import QuiverCore
import SwiftUI

/// The models the ledger saw, grouped by provider, with the price each one
/// resolves to and where that price came from. Unpriced rows open the form
/// that writes `~/.quiver/config/pricing.json`.
struct PricingPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.pricingModel {
            PricingContent(model: model)
        } else {
            Page(title: "Pricing", subtitle: "Per 1M tokens. Your prices win over downloaded ones; saving reprices past usage.") {
                ErrorCard(message: app.overviewError ?? "The usage ledger is unavailable.")
            }
        }
    }
}

private struct PricingContent: View {
    @Bindable var model: PricingModel
    @Environment(AppModel.self) private var app
    /// The model whose price form is open, keyed by its logged name.
    @State private var editing: PricingModel.PricedModel?
    /// Which provider cards show all their rows.
    @State private var expanded: Set<String> = []

    var body: some View {
        Page(title: "Pricing",
             subtitle: "Per 1M tokens. Your prices win over downloaded ones; saving reprices past usage.") {
            if !model.unpriced.isEmpty {
                UnpricedBadge(unpriced: model.unpriced) { editing = $0 }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                Tabs(options: OverviewPeriod.allCases.map { ($0, $0.title) },
                     selection: $model.period, style: .period)
            }
        } content: {
            if model.groups.isEmpty {
                Card { Text(model.isLoading ? "Reading your usage logs…" : "No usage in this period.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary) }
            }
            ForEach(model.groups) { group in
                ProviderCard(group: group, expanded: expanded.contains(group.id),
                             edit: { editing = $0 },
                             toggle: {
                    withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                        if expanded.contains(group.id) { expanded.remove(group.id) }
                        else { expanded.insert(group.id) }
                    }
                })
            }
        }
        .task(id: model.period) { await model.load() }
        .task(id: app.overview?.completedScans) { await model.load() }
        .sheet(item: $editing) { row in
            PriceSheet(model: model, row: row)
        }
    }
}

// MARK: - Header

/// The warning button left of the period control: a popover of the models
/// with unpriced tokens, each opening the price form.
private struct UnpricedBadge: View {
    var unpriced: [PricingModel.PricedModel]
    var open: (PricingModel.PricedModel) -> Void

    @State private var show = false
    @State private var rest: Task<Void, Never>?

    var body: some View {
        Button { show.toggle() } label: {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(Theme.warningText)
        }
        .iconButton()
        .help(unpriced.count == 1 ? "1 model has no price" : "\(unpriced.count) models have no price")
        .onHover { inside in
            if inside {
                rest = Task {
                    try? await Task.sleep(for: .milliseconds(150))
                    guard !Task.isCancelled else { return }
                    show = true
                }
            } else {
                rest?.cancel()
            }
        }
        .popover(isPresented: $show, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(unpriced) { row in
                    Button { show = false; open(row) } label: {
                        HStack(spacing: 6) {
                            ForEach(row.harnesses.prefix(3), id: \.self) { harness in
                                HarnessIcon(harness: harness, size: 14)
                            }
                            Text(row.model).font(Theme.caption).foregroundStyle(Theme.textPrimary)
                                .lineLimit(1).truncationMode(.middle)
                            Spacer()
                            Text(UsageFormat.tokens(row.unpricedTokens))
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                        }
                        .padding(.horizontal, 10).padding(.vertical, 7)
                        .frame(width: 300)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    .modifier(RowHover())
                }
            }
            .padding(.vertical, 6)
        }
    }
}

// MARK: - Provider card

private struct ProviderCard: View {
    var group: PricingModel.ProviderGroup
    var expanded: Bool
    var edit: (PricingModel.PricedModel) -> Void
    var toggle: () -> Void

    /// Rows seen before the fold.
    static let foldLimit = 4

    var body: some View {
        let shown = expanded ? group.models : Array(group.models.prefix(Self.foldLimit))
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                if let icon = group.icon {
                    HarnessIcon(harness: icon, size: 16)
                }
                Text(group.name).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Spacer()
                Text("\(group.models.count) \(group.models.count == 1 ? "model" : "models") · \(UsageFormat.tokens(group.tokens)) tokens")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 20)
            .padding(.top, 20)
            .padding(.bottom, 12)
            PricingRow.header
                .padding(.horizontal, 28)
                .padding(.bottom, 8)
            ForEach(shown) { row in
                Rectangle().fill(Theme.rowDivider).frame(height: Theme.hairline)
                PricingRow(row: row, clickable: row.unpricedTokens > 0) { edit(row) }
            }
            if group.models.count > Self.foldLimit {
                if !shown.isEmpty { Rectangle().fill(Theme.rowDivider).frame(height: Theme.hairline) }
                ExpandRow(isExpanded: expanded, hidden: group.models.count - Self.foldLimit,
                          help: (expand: "Show every \(group.name) model",
                                 collapse: "Show only the first \(Self.foldLimit)"),
                          edge: .cardBottom, action: toggle)
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

// MARK: - Rows

/// `price` comes in per token; the page shows it per million, in plain
/// dollars like Overview's spend figures.
private func perMillion(_ rate: Double?) -> String {
    guard let rate else { return "—" }
    return String(format: "$%.2f", rate * 1_000_000)
}

private struct PricingRow: View {
    var row: PricingModel.PricedModel
    var clickable = false
    var edit: () -> Void = {}

    private var rateColumns: [String] {
        let rates = row.rates
        return [perMillion(rates?.input), perMillion(rates?.output),
                perMillion(rates?.cacheRead), perMillion(rates?.cacheWrite)]
    }

    private var sourceText: String {
        if row.harnessBilled { return "Billed by harness" }
        if row.isFree { return "Free" }
        return switch row.source {
        case .yours: "Yours"
        case .quiver: "Quiver"
        case .liteLLM: "LiteLLM"
        case nil: "No price"
        }
    }

    /// A split `-fast`-style name shows the base rates plus what the tier
    /// applies: "fast ×2" when the catalog knows a multiplier, else just
    /// the tier name.
    private var tierNote: String? {
        guard let tier = row.tier else { return nil }
        guard let multiplier = row.rates?.tierMultipliers[tier] else { return tier }
        return "\(tier) ×\(String(format: "%g", multiplier))"
    }

    var body: some View {
        let content = rowBody
        if clickable {
            Button(action: edit) { content }
                .buttonStyle(.plain)
                .modifier(RowHover())
                .onHover { inside in if inside { NSCursor.pointingHand.push() } else { NSCursor.pop() } }
        } else {
            content
        }
    }

    private var rowBody: some View {
        HStack(spacing: 10) {
            HStack(spacing: 2) {
                ForEach(row.harnesses.prefix(3), id: \.self) { harness in
                    HarnessIcon(harness: harness, size: 14)
                        .accessibilityLabel(UsageFormat.harness(harness))
                }
            }
            .fixedSize()
            Text(row.model).lineLimit(1).truncationMode(.middle)
            if let tierNote {
                Text(tierNote).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .lineLimit(1).fixedSize()
            }
            Spacer()
            ForEach(rateColumns, id: \.self) { rate in
                Text(rate)
                    .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                    .frame(width: Self.rateColumnWidth, alignment: .trailing)
            }
            Text(sourceText)
                .foregroundStyle(row.source == nil && !row.harnessBilled && !row.isFree
                                 ? Theme.warningText : Theme.textSecondary)
                .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                .frame(width: 96, alignment: .trailing)
            Text(UsageFormat.tokens(row.totals.totalTokens))
                .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                .frame(width: 68, alignment: .trailing)
        }
        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).monospacedDigit()
        .padding(.horizontal, 28)
        .padding(.vertical, 9)
    }

    /// Wide enough for "$50.00" on a value and "Cache write" on the header.
    static let rateColumnWidth: CGFloat = 76

    static var header: some View {
        HStack(spacing: 10) {
            Text("Model").frame(maxWidth: .infinity, alignment: .leading)
            ForEach(["Input", "Output", "Cache read", "Cache write"], id: \.self) { label in
                Text(label)
                    .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                    .frame(width: rateColumnWidth, alignment: .trailing)
            }
            Text("Source").frame(width: 96, alignment: .trailing)
            Text("Tokens").frame(width: 68, alignment: .trailing)
        }
        .font(Theme.tableHeader).foregroundStyle(Theme.tableHeaderText)
    }
}

/// `rowHover` under the pointer (the same modifier Overview's breakdown rows use).
private struct RowHover: ViewModifier {
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .background(hovering ? Theme.rowHover : .clear)
            .onHover { hovering = $0 }
    }
}

// MARK: - Price form

/// "Set a price": the logged model as the title, the base name as the key
/// the price is saved under, and the four rates per million tokens.
private struct PriceSheet: View {
    var model: PricingModel
    var row: PricingModel.PricedModel

    @Environment(\.dismiss) private var dismiss
    @State private var key: String
    @State private var input = ""
    @State private var output = ""
    @State private var cacheRead = ""
    @State private var cacheWrite = ""
    @State private var saving = false

    init(model: PricingModel, row: PricingModel.PricedModel) {
        self.model = model
        self.row = row
        _key = State(initialValue: PricingCatalog.baseName(for: row.model))
    }

    private func number(_ text: String, orBlankAs blank: Double? = nil) -> Double? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { return blank }
        guard let value = Double(trimmed), value >= 0 else { return nil }
        return value
    }

    private var valid: Bool {
        !key.trimmingCharacters(in: .whitespaces).isEmpty
            && number(input) != nil && number(output) != nil
            && number(cacheRead, orBlankAs: 0) != nil && number(cacheWrite, orBlankAs: 0) != nil
    }

    /// The logged names in this period the saved key would cover.
    private var covered: [String] {
        model.coveredBy(key.trimmingCharacters(in: .whitespaces))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(row.model).font(Theme.sectionTitle).tracking(-0.5)
                .lineLimit(1).truncationMode(.middle)
            VStack(alignment: .leading, spacing: 6) {
                Text("Price applies to").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                TextField("Model name", text: $key).quiverField(monospaced: true)
                if !covered.isEmpty {
                    Text("Covers \(covered.joined(separator: ", ")) in this period.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            HStack(spacing: 10) {
                rateField("Input", text: $input)
                rateField("Output", text: $output)
            }
            HStack(spacing: 10) {
                rateField("Cache read", text: $cacheRead)
                rateField("Cache write", text: $cacheWrite)
            }
            Text("USD per 1M tokens. Blank cache fields count as 0.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .secondaryButton()
                Button("Save") { save() }
                    .primaryButton()
                    .keyboardShortcut(.defaultAction)
                    .disabled(!valid || saving)
            }
        }
        .sheetScaffold(width: Theme.formSheetWidth, padding: Theme.formSheetPadding)
    }

    private func rateField(_ label: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            TextField("0.00", text: text).quiverField()
        }
    }

    private func save() {
        guard let input = number(input), let output = number(output) else { return }
        let key = key.trimmingCharacters(in: .whitespaces)
        let prices = PricingOverrides.Prices(
            input: input, output: output,
            cacheRead: number(cacheRead, orBlankAs: 0) ?? 0,
            cacheWrite: number(cacheWrite, orBlankAs: 0) ?? 0)
        saving = true
        Task {
            try? await model.save(key: key, prices: prices)
            dismiss()
        }
    }
}
