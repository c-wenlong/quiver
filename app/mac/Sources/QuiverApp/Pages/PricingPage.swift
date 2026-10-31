import AppKit
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
            MultiSelectMenu(allTitle: "All harnesses",
                            options: model.harnessOptions.map {
                                (value: $0, title: HarnessMark($0).name, icon: $0)
                            },
                            selection: $model.harnessFilter,
                            countTitle: { "\($0) harnesses" })
            // A compact select, not the header-wide tab strip, so the
            // trailing controls fit at every window width.
            SelectMenu(options: OverviewPeriod.allCases.map { ($0, $0.title) },
                       selection: $model.period, width: nil)
        } content: {
            if model.groups.isEmpty {
                if model.isLoading {
                    ProviderSkeleton(label: "Reading your usage logs…")
                } else {
                    Card { Text("No usage in this period.")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary) }
                }
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
        // One task for every input: three separate ones each ran the
        // whole read on first open, one after another.
        .task(id: LoadKey(period: model.period, harness: model.harnessFilter,
                          scans: app.overview?.completedScans)) { await model.load() }
        .quiverSheet(item: $editing) { row in
            PriceSheet(model: model, row: row)
        }
    }
}

/// What a Pricing load reads: a change to any of them reloads.
private struct LoadKey: Equatable {
    var period: OverviewPeriod
    var harness: Set<String>
    var scans: Int?
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
                            BrandLogo(model: row.model, size: 14)
                            Text(row.model).font(Theme.caption).foregroundStyle(Theme.textPrimary)
                                .lineLimit(1).truncationMode(.middle)
                            ForEach(row.harnesses.prefix(3), id: \.self) { harness in
                                HarnessIcon(harness: harness, size: 12)
                            }
                            Spacer()
                            Text(UsageFormat.tokens(row.unpricedTokens))
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                        }
                        .padding(.horizontal, 10).padding(.vertical, 7)
                        .frame(width: 300)
                        .contentShape(.rect)
                    }
                    .plainButton(cornerRadius: 0)
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

    /// The card's content width: narrow cards drop columns rather than
    /// clipping them.
    @State private var width: CGFloat = 0

    var body: some View {
        let columns = PricingColumns.visible(forWidth: width)
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
            PricingRow.header(columns: columns)
                .padding(.horizontal, 28)
                .padding(.bottom, 8)
            ForEach(shown) { row in
                Rectangle().fill(Theme.rowDivider).frame(height: Theme.hairline)
                PricingRow(row: row, columns: columns) { edit(row) }
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
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
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
    var columns: PricingColumns
    var edit: () -> Void
    @Environment(AppModel.self) private var app

    private var rateColumns: [String] {
        let rates = row.rates
        var shown = [perMillion(rates?.input), perMillion(rates?.output)]
        if columns.cacheReadWrite {
            shown += [perMillion(rates?.cacheRead), perMillion(rates?.cacheWrite)]
        }
        return shown
    }

    /// The dropped cache columns as hover text: "Cache read $0.20 · Cache
    /// write $5.00 per 1M". With no Source column, where the spend comes
    /// from follows, since there's no mark to hover.
    private var help: String? {
        var lines: [String] = []
        if !columns.cacheReadWrite {
            lines.append("Cache read \(perMillion(row.rates?.cacheRead)) · "
                + "Cache write \(perMillion(row.rates?.cacheWrite)) per 1M")
        }
        if !columns.source { lines.append(sourceHelp(row)) }
        return lines.isEmpty ? nil : lines.joined(separator: "\n")
    }

    /// Charged tiers show the base rates plus what each applies:
    /// "fast ×2" when the catalog knows a multiplier, else just the tier
    /// name.
    private var tierNote: String? {
        guard !row.tiers.isEmpty else { return nil }
        return row.tiers.map { tier in
            guard let multiplier = row.rates?.tierMultipliers[tier] else { return tier }
            return "\(tier) ×\(String(format: "%g", multiplier))"
        }.joined(separator: " · ")
    }

    var body: some View {
        if let help {
            wrapped.help(help)
        } else {
            wrapped
        }
    }

    private var wrapped: some View {
        Button(action: edit) { rowBody }
            .plainButton(cornerRadius: 0)
            .modifier(RowHover())
            // SwiftUI sets and resets the cursor itself: a push on hover
            // with no pop when the row went away (a filter, a page switch)
            // left the hand stuck over the whole window.
            .pointerStyle(.link)
    }

    private var sourceColor: some ShapeStyle {
        row.spendSource == .unpriced ? Theme.warningText : Theme.textSecondary
    }

    private var rowBody: some View {
        HStack(spacing: 10) {
            // The lab's logo first, as everywhere a model is named; the
            // tools that ran it follow the name.
            BrandLogo(model: row.model, size: 16)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    // An opaque id shows the real name its alias gives it,
                    // the id muted after it.
                    if let alias = ModelBrands.resolvedName(row.model) {
                        Text(alias).lineLimit(1).truncationMode(.middle)
                        Text(row.model).font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .lineLimit(1).fixedSize()
                    } else {
                        Text(row.model).lineLimit(1).truncationMode(.middle)
                    }
                    HStack(spacing: 2) {
                        ForEach(row.harnesses.prefix(3), id: \.self) { harness in
                            HarnessIcon(harness: harness, size: 12)
                                .accessibilityLabel(UsageFormat.harness(harness))
                        }
                    }
                    .fixedSize()
                    if let tierNote {
                        Text(tierNote).font(Theme.caption).foregroundStyle(Theme.textMuted)
                            .lineLimit(1).fixedSize()
                    }
                }
                // With no Source column, a caption under the name says it.
                if !columns.source {
                    Text(sourceName(row)).font(Theme.caption).foregroundStyle(sourceColor)
                        .lineLimit(1)
                }
            }
            .frame(minWidth: 120, alignment: .leading)
            Spacer()
            ForEach(rateColumns.indices, id: \.self) { index in
                // A harness-billed row's rates are kept but unused: dimmed.
                Text(rateColumns[index])
                    .foregroundStyle(row.harnessBilled ? Theme.textMuted : Theme.textPrimary)
                    .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                    .frame(width: Self.rateColumnWidth, alignment: .trailing)
            }
            if columns.source {
                SourceMark(row: row)
                    .frame(width: Self.sourceColumnWidth, alignment: .trailing)
            }
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
    /// Wide enough for the "Source" header over one or two marks.
    static let sourceColumnWidth: CGFloat = 56

    static func header(columns: PricingColumns) -> some View {
        HStack(spacing: 10) {
            Text("Model").frame(maxWidth: .infinity, alignment: .leading)
            ForEach(["Input", "Output"]
                    + (columns.cacheReadWrite ? ["Cache read", "Cache write"] : []),
                    id: \.self) { label in
                Text(label)
                    .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                    .frame(width: rateColumnWidth, alignment: .trailing)
            }
            if columns.source {
                Text("Source").frame(width: sourceColumnWidth, alignment: .trailing)
            }
            Text("Tokens").frame(width: 68, alignment: .trailing)
        }
        .font(Theme.tableHeader).foregroundStyle(Theme.tableHeaderText)
    }
}

/// Who bills the row's logged charges ("Cursor", or "Cursor and Devin").
private func billers(_ row: PricingModel.PricedModel) -> String {
    var names: [String] = []
    for biller in row.billedBy {
        let name = UsageFormat.harness(biller)
        if !names.contains(name) { names.append(name) }
    }
    return names.joined(separator: " and ")
}

/// The source in a word or two: the caption under a narrow row's name and
/// the mark's accessibility label.
private func sourceName(_ row: PricingModel.PricedModel) -> String {
    switch row.spendSource {
    case .billed: "\(billers(row)) bills"
    case .free: "Free"
    case .yours: "Your price"
    case .quiver: "Quiver"
    case .liteLLM: "LiteLLM"
    case .unpriced: "No price"
    }
}

/// The Source mark's hover text: whose price the spend uses.
private func sourceHelp(_ row: PricingModel.PricedModel) -> String {
    switch row.spendSource {
    case .billed:
        let billed = "Billed by \(billers(row)): spend uses the charge \(billers(row)) logs for each request."
        return switch row.source {
        case .yours: billed + " Your saved price is kept but not used."
        case .liteLLM, .quiver: billed + " The rates shown are the API list price, for comparison."
        case nil: billed
        }
    default:
        break
    }
    // Part of a mixed row's usage may still carry a logged charge: the
    // sentence then speaks only for the rest, and says who bills that part.
    let mixed = !row.billedBy.isEmpty
    let rest = mixed ? "usage without a logged charge" : "spend"
    var text = switch row.spendSource {
    case .free: mixed ? "Free model: usage without a logged charge is priced at $0."
                      : "Free model: priced at $0."
    case .yours: "Your price: \(rest) uses the rates you saved."
    case .quiver: "API pricing from Quiver's own list, which fills in or corrects LiteLLM's"
        + (mixed ? ", for usage without a logged charge." : ".")
    case .liteLLM: "API pricing from LiteLLM's price list" + (mixed ? ", for usage without a logged charge." : ".")
    case .unpriced: mixed
        ? "No price: usage without a logged charge isn't in your spend. Click the row to set one."
        : "No price: these tokens aren't in your spend. Click the row to set one."
    case .billed: ""
    }
    if mixed { text += " Usage billed by \(billers(row)) uses the charge \(billers(row)) logs." }
    return text
}

/// The Source column's mark, with `sourceHelp` on hover: the biller's
/// logo, LiteLLM's, Quiver's app icon, or a symbol for your own price, a
/// free model and a missing price.
private struct SourceMark: View {
    var row: PricingModel.PricedModel
    static let size: CGFloat = 16

    var body: some View {
        // `hoverTip`, not `.help`: a tooltip inside the row's button never
        // shows. The tip opens on a short rest and pins on click (the click
        // stays on the mark rather than opening the price sheet).
        mark
            .padding(4)
            .hoverTip(sourceHelp(row), title: sourceName(row))
    }

    @ViewBuilder private var mark: some View {
        switch row.spendSource {
        case .billed(let harnesses):
            HStack(spacing: 3) {
                ForEach(harnesses.prefix(2), id: \.self) { HarnessIcon(harness: $0, size: Self.size) }
            }
        case .liteLLM:
            if let image = Self.liteLLM {
                Image(nsImage: image).renderingMode(.template).resizable().interpolation(.high)
                    .scaledToFit().foregroundStyle(Theme.textPrimary)
                    .frame(width: Self.size, height: Self.size)
            } else {
                Text("LiteLLM").foregroundStyle(Theme.textSecondary)
            }
        case .quiver:
            Image(nsImage: NSApp.applicationIconImage).resizable().interpolation(.high)
                .frame(width: Self.size + 2, height: Self.size + 2)
        case .yours:
            symbol("person.crop.circle", Theme.textSecondary)
        case .free:
            symbol("gift", Theme.textSecondary)
        case .unpriced:
            symbol("exclamationmark.triangle", Theme.warningText)
        }
    }

    private func symbol(_ name: String, _ color: Color) -> some View {
        Image(systemName: name).font(Theme.font(size: 13)).foregroundStyle(color)
            .frame(width: Self.size, height: Self.size)
    }

    @MainActor private static let liteLLM: NSImage? = PricingSourceIcons.liteLLM
        .flatMap(NSImage.init(contentsOf:))
        .map { image in image.isTemplate = true; return image }
}

/// "Spend uses the charge Cursor logs for each request, not these rates."
/// With a saved price, says outright that it's kept but not used.
private func harnessBilledNote(_ row: PricingModel.PricedModel) -> String {
    let charge = "the charge \(billers(row)) logs for each request"
    return row.source == .yours
        ? "Your price is saved but not used: spend uses \(charge)."
        : "Spend uses \(charge), not these rates."
}

// MARK: - Price form

/// "Set a price": the logged model as the title, the base name as the key
/// the price is saved under, and the four rates per million tokens.
private struct PriceSheet: View {
    var model: PricingModel
    var row: PricingModel.PricedModel

    @Environment(\.dismissSheet) private var dismiss
    @State private var key: String
    @State private var input = ""
    @State private var output = ""
    @State private var cacheRead = ""
    @State private var cacheWrite = ""
    /// What the sheet is writing now ("Saving…"), nil when idle: the
    /// spinner's label, and the lock on both writing buttons.
    @State private var working: String?
    @State private var saveError: String?

    private var saving: Bool { working != nil }

    /// Editing one of the user's own prices rather than adding a new one.
    private var editing: Bool { row.source == .yours }

    /// With a downloaded price under the override, deleting it restores
    /// the list price; without one the price goes away entirely.
    private var resetTitle: String {
        switch row.resetKind {
        case .resetToList: "Reset to list price"
        case .remove: "Remove price"
        }
    }

    /// The key the row's price is saved under: the entry the lookup
    /// answered, not whatever the "Price applies to" field now says.
    private var originalKey: String {
        row.entry ?? PricingCatalog.baseName(for: row.model)
    }

    init(model: PricingModel, row: PricingModel.PricedModel) {
        self.model = model
        self.row = row
        _key = State(initialValue: row.source == .yours && row.entry != nil
                     ? row.entry! : PricingCatalog.baseName(for: row.model))
        // A priced row pre-fills the fields with its current rates
        // (stored per token, shown per million).
        if let rates = row.rates {
            _input = State(initialValue: Self.rate(rates.input))
            _output = State(initialValue: Self.rate(rates.output))
            _cacheRead = State(initialValue: Self.rate(rates.cacheRead))
            _cacheWrite = State(initialValue: Self.rate(rates.cacheWrite))
        }
    }

    /// A per-token rate as the per-million text the fields hold.
    private static func rate(_ perToken: Double) -> String {
        String(format: "%g", perToken * 1_000_000)
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
            HStack(spacing: 8) {
                BrandLogo(model: row.model, size: 20)
                Text(row.model).font(Theme.sectionTitle).tracking(-0.5)
                    .lineLimit(1).truncationMode(.middle)
            }
            if let listPrice = row.listPrice {
                Text("List price (\(listPrice.source == .quiver ? "Quiver" : "LiteLLM")): "
                     + "\(perMillion(listPrice.rates.input)) in · "
                     + "\(perMillion(listPrice.rates.output)) out · "
                     + "\(perMillion(listPrice.rates.cacheRead)) cache read · "
                     + "\(perMillion(listPrice.rates.cacheWrite)) cache write per 1M")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Price applies to").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                TextField("Model name", text: $key).quiverField(monospaced: true)
                if !covered.isEmpty {
                    Text("Covers \(covered.joined(separator: ", ")) in this period.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !model.harnessFilter.isEmpty {
                    // One price per model: a saved rate reprices every
                    // harness's usage of it, filter or not.
                    Text("Price applies to every harness")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            if row.harnessBilled {
                // A saved price fills the rate columns and prices any
                // harness that logs no charge; the logged charge stays.
                Text(harnessBilledNote(row))
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
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
            if let saveError {
                Text(saveError)
                    .font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if editing {
                    Button(resetTitle) { remove() }
                        .secondaryButton()
                        .disabled(saving)
                }
                Spacer()
                if let working {
                    // The sheet closes by itself once the write lands;
                    // Cancel just closes it sooner, the write still runs.
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(working).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                    .transition(.opacity)
                }
                Button("Cancel", role: .cancel) { dismiss() }
                    .secondaryButton()
                Button("Save") { save() }
                    .primaryButton()
                    .keyboardShortcut(.defaultAction)
                    .disabled(!valid || saving)
            }
        }
        .animation(.easeOut(duration: Theme.fadeDuration), value: working)
        .sheetScaffold(width: Theme.formSheetWidth, padding: Theme.formSheetPadding)
    }

    private func rateField(_ label: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            TextField("0.00", text: text).quiverField()
                .accessibilityLabel(label)
        }
    }

    private func save() {
        guard !saving, let input = number(input), let output = number(output) else { return }
        let key = key.trimmingCharacters(in: .whitespaces)
        let prices = PricingOverrides.Prices(
            input: input, output: output,
            cacheRead: number(cacheRead, orBlankAs: 0) ?? 0,
            cacheWrite: number(cacheWrite, orBlankAs: 0) ?? 0)
        write("Saving…") {
            try await model.save(key: key, prices: prices,
                                 replacing: editing ? originalKey : nil)
        }
    }

    private func remove() {
        guard !saving else { return }
        write(row.resetKind == .remove ? "Removing…" : "Resetting…") {
            try await model.remove(matching: row.model)
        }
    }

    /// Runs one write with the spinner showing `label`, then closes the
    /// sheet, or keeps it open with the error.
    private func write(_ label: String, _ body: @escaping @MainActor () async throws -> Void) {
        working = label
        saveError = nil
        Task {
            do {
                try await body()
                dismiss()
            } catch {
                saveError = error.localizedDescription
                working = nil
            }
        }
    }
}

// MARK: - Loading

/// The loading stand-in for a provider card: the same head strip,
/// column headers and rate/token columns the real card lays out.
private struct ProviderSkeleton: View {
    var rows = 5
    var label: String
    @State private var width: CGFloat = 0

    var body: some View {
        let columns = PricingColumns.visible(forWidth: width)
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                SkeletonBlock(width: 16, height: 16, radius: 4)
                SkeletonBlock(width: 110, height: 15)
                Spacer()
                SkeletonBlock(width: 120, height: 10)
            }
            .padding(.horizontal, 20)
            .padding(.top, 20)
            .padding(.bottom, 12)
            header(columns)
                .padding(.horizontal, 28)
                .padding(.bottom, 8)
            ForEach(0..<rows, id: \.self) { row in
                Rectangle().fill(Theme.rowDivider).frame(height: Theme.hairline)
                rowBody(row, columns)
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(label))
    }

    private func header(_ columns: PricingColumns) -> some View {
        HStack(spacing: 10) {
            SkeletonBlock(width: 36, height: 10)
                .frame(maxWidth: .infinity, alignment: .leading)
            ForEach(0..<(columns.cacheReadWrite ? 4 : 2), id: \.self) { _ in
                SkeletonBlock(width: 44, height: 10)
                    .frame(width: PricingRow.rateColumnWidth, alignment: .trailing)
            }
            if columns.source {
                SkeletonBlock(width: 38, height: 10)
                    .frame(width: PricingRow.sourceColumnWidth, alignment: .trailing)
            }
            SkeletonBlock(width: 40, height: 10)
                .frame(width: 68, alignment: .trailing)
        }
    }

    private func rowBody(_ row: Int, _ columns: PricingColumns) -> some View {
        HStack(spacing: 10) {
            SkeletonBlock(width: 16, height: 16, radius: 4)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    SkeletonBlock(width: 130 - CGFloat(row % 3) * 26, height: 12)
                    HStack(spacing: 2) {
                        SkeletonBlock(width: 12, height: 12, radius: 3)
                        if row % 2 == 0 { SkeletonBlock(width: 12, height: 12, radius: 3) }
                    }
                }
                if !columns.source { SkeletonBlock(width: 90, height: 9) }
            }
            .frame(minWidth: 120, alignment: .leading)
            Spacer()
            ForEach(0..<(columns.cacheReadWrite ? 4 : 2), id: \.self) { _ in
                SkeletonBlock(width: 42, height: 11)
                    .frame(width: PricingRow.rateColumnWidth, alignment: .trailing)
            }
            if columns.source {
                SkeletonBlock(width: 16, height: 16, radius: 4)
                    .frame(width: PricingRow.sourceColumnWidth, alignment: .trailing)
            }
            SkeletonBlock(width: 44, height: 11)
                .frame(width: 68, alignment: .trailing)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 9)
    }
}
