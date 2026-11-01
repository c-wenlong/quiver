import AppKit
import SWECore
import SwiftUI

/// Settings › Providers › Inference: every key in the API keys file and every
/// plan SWE knows, in one list you can drag into your own order and
/// filter by what the key is known to be and by kind. A key is masked
/// until revealed; a click on a row opens it.
struct InferenceSection: View {
    /// `withKeys`, the default, hides every plan and provider with no key
    /// yet; "Any status" and the per-state picks show them.
    enum StateFilter: Hashable { case withKeys, all, state(InferenceKeyState) }
    enum KindFilter: Hashable { case all, kind(InferenceItem.Kind) }

    @Environment(AppModel.self) private var app
    @State private var editing: KeyDraft?
    @State private var editingPlan: InferenceItem?
    @State private var revealed: Set<String> = []
    @State private var actionError: String?
    @State private var stateFilter = StateFilter.withKeys
    @State private var kindFilter = KindFilter.all
    @State private var cataloguing = false
    @State private var query = ""
    @State private var tableWidth: CGFloat = 0
    /// The provider whose heading is being renamed, and its draft name.
    @State private var renamingProvider = false
    @State private var providerBeingRenamed = ""
    @State private var providerDraft = ""
    /// The startup file a confirmed "Add to" would edit, nil while asked.
    @State private var confirmingSource: URL?
    @State private var sourceError: String?
    @State private var sourceAdded: String?

    static let firstWidth: CGFloat = 200
    static let compactFirstWidth: CGFloat = 150
    static let typeWidth: CGFloat = 100
    /// Below this table width the Type column folds under the name, so
    /// the value keeps its room in a narrow window.
    static let compactBelow: CGFloat = 680
    static let statusWidth: CGFloat = 120

    var body: some View {
        let model = app.apiKeys
        let all = app.inferenceOrder.sorted(InferenceList.items(keys: model.rows))
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .center, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Keys and plans").font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                    Text(subtitle(model)).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 12)
                Button("Reload", systemImage: "arrow.clockwise") { actionError = nil; Task { await model.load() } }
                    .iconButton()
                    .disabled(model.isLoading)
                    .keyboardShortcut("r", modifiers: .command)
                    .help("Read the file and the shell again (⌘R)")
                Button("Check keys", systemImage: "checkmark.shield") { Task { await model.checkAll() } }
                    .secondaryButton()
                    .disabled(model.isChecking)
                    .hoverTip("Send each key to its own provider or plan to see if it still works", replacesLabel: false)
                Button("Catalogue", systemImage: "square.grid.2x2") { cataloguing = true }
                    .secondaryButton()
                    .hoverTip("Every plan and provider SWE knows; pick one to add its key", replacesLabel: false)
                Button("Add key", systemImage: "plus") { editing = KeyDraft(provider: "", name: "", value: "", isNew: true) }
                    .primaryButton()
            }
            if let error = model.error ?? actionError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
            }
            if let added = sourceAdded {
                Text(added).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let error = sourceError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            if case .notSourced(let rc) = model.sourcing, !model.rows.isEmpty {
                Card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("New terminals don't get these keys. SWE can add one line to \(rc.lastPathComponent).")
                            .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                        HStack {
                            Spacer()
                            Button("Add to \(rc.lastPathComponent)") { confirmingSource = rc }
                                .primaryButton()
                        }
                    }
                }
            }
            if !model.problems.isEmpty {
                Card(title: "Lines that set nothing") {
                    ForEach(model.problems, id: \.line) { problem in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Line \(problem.line): \(problem.reason)")
                                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            // Already redacted by APIKeysFile: the value never reaches the page.
                            Text(problem.text)
                                .font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                        }
                    }
                    Text("Fix these in an editor; SWE leaves lines it can't read untouched.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            filterBar(all)
            table(all)
            footer(model)
        }
        .task(id: model.path) { await model.load() }
        .onDisappear { revealed.removeAll() }
        .quiverSheet(isPresented: $cataloguing) {
            ProviderCatalogueSheet(entries: InferenceCatalogue.entries(keys: model.rows)) { entry in
                switch entry.target {
                case .plan(let plan):
                    editingPlan = planItem(plan)
                case .provider(let provider):
                    editing = KeyDraft(provider: "", name: "", value: "", isNew: true,
                                       providerID: provider.id)
                }
            }
        }
        .quiverSheet(item: $editing) { draft in
            KeyEditor(draft: draft, providers: model.providers, rows: model.rows,
                      pickPlan: { plan in
                editingPlan = planItem(plan)
                editing = nil
            }, save: { provider, name, value, tested in
                if draft.isNew {
                    try await model.set(name: name, value: value, provider: provider, tested: tested)
                } else {
                    try await model.update(name: draft.name, newName: name, provider: provider,
                                           value: value, tested: tested)
                    revealed.remove(draft.name)
                }
            }, saveCustom: { provider, entries, tested, testedName in
                try await model.setAll(entries, provider: provider,
                                       tested: tested, testedName: testedName)
            }, test: { name, value in
                await model.test(name: name, value: value)
            }, testCustom: { key, base in
                await model.test(key: key, baseURL: base)
            }, fetchModels: { base, key in
                try await model.models(baseURL: base, key: key)
            }, remove: { name in
                try await model.remove(name: name)
                revealed.remove(name)
            })
        }
        .quiverSheet(item: $editingPlan) { item in
            if let plan = item.plan {
                PlanKeyEditor(plan: plan, current: item.key?.value ?? "", exists: item.key != nil, save: { value in
                    try await model.set(name: plan.keyName, value: value,
                                        provider: item.key?.provider ?? InferenceCatalog.heading)
                    revealed.remove(plan.keyName)
                    Task { await model.check(name: plan.keyName) }
                }, remove: {
                    try await model.remove(name: plan.keyName)
                    revealed.remove(plan.keyName)
                })
            }
        }
        .alert("Rename provider", isPresented: $renamingProvider) {
            TextField("Name", text: $providerDraft)
            Button("Rename") {
                let from = providerBeingRenamed
                let to = providerDraft
                Task {
                    actionError = nil
                    do {
                        try await model.renameProvider(from: from, to: to)
                    } catch {
                        actionError = error.localizedDescription
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog(
            "Add a line to \(confirmingSource?.lastPathComponent ?? "the startup file")?",
            isPresented: Binding(get: { confirmingSource != nil }, set: { if !$0 { confirmingSource = nil } })) {
            Button("Add line") {
                let model = app.apiKeys
                sourceError = nil
                Task {
                    do {
                        let result = try await model.addSourceLine()
                        let file = result.file.lastPathComponent
                        sourceAdded = "Added to \(file)."
                            + (result.backup.map { " Backup at \($0.path)." } ?? "")
                            + " Open a new terminal to use the keys."
                    } catch {
                        sourceError = error.localizedDescription
                    }
                    confirmingSource = nil
                }
            }
            Button("Cancel", role: .cancel) { confirmingSource = nil }
        } message: {
            if let rc = confirmingSource {
                Text("SWE appends \(app.apiKeys.sourceLine) to \(rc.path). SWE backs up \(rc.lastPathComponent) first.")
            }
        }
    }

    private func state(of item: InferenceItem) -> InferenceKeyState {
        InferenceKeyState(hasKey: item.hasKey, check: item.key.flatMap { app.apiKeys.check(for: $0) }?.result)
    }

    private func visible(_ all: [InferenceItem]) -> [InferenceItem] {
        let state: InferenceKeyState? = if case .state(let value) = stateFilter { value } else { nil }
        let kind: InferenceItem.Kind? = if case .kind(let value) = kindFilter { value } else { nil }
        return all.filter {
            (stateFilter != .withKeys || $0.hasKey)
                && InferenceList.matches($0, state: state, kind: kind, query: query, stateOf: self.state(of:))
        }
    }

    /// The shared filter bar: selects, then search taking the slack, then the count.
    private func filterBar(_ all: [InferenceItem]) -> some View {
        func count(_ state: InferenceKeyState) -> Int { all.filter { self.state(of: $0) == state }.count }
        func count(_ kind: InferenceItem.Kind) -> Int { all.filter { $0.kind == kind }.count }
        let shown = visible(all).count
        return HStack(spacing: 10) {
            SelectMenu(options: [(StateFilter.withKeys, "With keys (\(all.count - count(.noKey)))"),
                                 (.all, "Any status (\(all.count))"),
                                 (.state(.valid), "Valid (\(count(.valid)))"),
                                 (.state(.invalid), "Invalid (\(count(.invalid)))"),
                                 (.state(.unchecked), "Not checked (\(count(.unchecked)))"),
                                 (.state(.noKey), "No key (\(count(.noKey)))")],
                       selection: $stateFilter)
            SelectMenu(options: [(KindFilter.all, "Any type (\(all.count))"),
                                 (.kind(.plan), "Coding plans (\(count(.plan)))"),
                                 (.kind(.apiKey), "API keys (\(count(.apiKey)))")],
                       selection: $kindFilter)
            SearchField(prompt: "Search providers, plans or variables", text: $query)
                .frame(maxWidth: .infinity)
            Text(shown == 1 ? "1 row" : "\(shown) rows")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                .fixedSize()
        }
    }

    private func table(_ all: [InferenceItem]) -> some View {
        let rows = visible(all)
        let compact = tableWidth > 0 && tableWidth < Self.compactBelow
        return VStack(spacing: 0) {
            HStack(spacing: 12) {
                Color.clear.frame(width: 12, height: 1)
                Text("Provider or plan").frame(width: compact ? Self.compactFirstWidth : Self.firstWidth,
                                                alignment: .leading)
                Text("Value").frame(maxWidth: .infinity, alignment: .leading)
                if !compact { Text("Type").frame(width: Self.typeWidth, alignment: .leading) }
                Text("Status").frame(width: Self.statusWidth, alignment: .leading)
            }
            .font(Theme.tableHeader).foregroundStyle(Theme.textSecondary)
            .padding(.horizontal, 16).padding(.vertical, 10)
            Divider().overlay(Theme.border)
            if rows.isEmpty {
                Text(emptyText(all))
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity).padding(.vertical, 20)
            } else {
                ReorderableRows(rows, showsDividers: true) { id, before in
                    app.inferenceOrder.move(id, before: before ?? after(rows, all: all, moving: id), in: all)
                } row: { item in
                    row(item, compact: compact)
                }
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { tableWidth = $0 }
    }

    /// What the table says when nothing shows. The `withKeys` default
    /// hiding every match gets its own line, so a fresh machine (where
    /// every row is a plan with no key) still points somewhere useful.
    private func emptyText(_ all: [InferenceItem]) -> String {
        if all.isEmpty {
            return "No keys yet. Add one and SWE writes it to \(displayPath(app.apiKeys.path))."
        }
        let kind: InferenceItem.Kind? = if case .kind(let value) = kindFilter { value } else { nil }
        let hiddenMatch = all.contains {
            !$0.hasKey
                && InferenceList.matches($0, state: nil, kind: kind, query: query, stateOf: self.state(of:))
        }
        if stateFilter == .withKeys && hiddenMatch {
            return "Only plans and providers with no key match. Open the catalogue to add one, or pick “Any status” to see them here."
        }
        return "Nothing matches these filters."
    }

    /// The catalogue's pick for `plan` as the plan editor expects it:
    /// with its key when the file has one, so an existing key edits
    /// instead of writing over a row it can't see.
    private func planItem(_ plan: InferencePlan) -> InferenceItem {
        InferenceItem(id: plan.keyName, plan: plan,
                      key: app.apiKeys.rows.first { $0.name == plan.keyName })
    }

    /// Dropped below the last shown row: land just after it in the full
    /// order, not after every row the filters hide.
    private func after(_ rows: [InferenceItem], all: [InferenceItem], moving id: String) -> String? {
        guard let last = rows.last(where: { $0.id != id }),
              let index = all.firstIndex(where: { $0.id == last.id }) else { return nil }
        return all[(index + 1)...].first { $0.id != id }?.id
    }

    private func row(_ item: InferenceItem, compact: Bool) -> some View {
        let model = app.apiKeys
        return InferenceRow(
            item: item, compact: compact, isRevealed: revealed.contains(item.id),
            check: item.key.flatMap { model.check(for: $0) }, isChecking: model.checking.contains(item.id),
            recheck: { Task { await model.check(name: item.id) } },
            toggleReveal: {
                if revealed.contains(item.id) { revealed.remove(item.id) } else { revealed.insert(item.id) }
            },
            copy: { copySecret(item.key?.value ?? "") },
            open: { open(item) },
            renameProvider: item.plan != nil || (item.key?.provider.isEmpty ?? true) ? nil : {
                providerBeingRenamed = item.key?.provider ?? ""
                providerDraft = providerBeingRenamed
                renamingProvider = true
            })
    }

    private func open(_ item: InferenceItem) {
        if item.plan != nil {
            editingPlan = item
        } else if let key = item.key {
            editing = KeyDraft(provider: key.provider, name: key.name, value: key.value, isNew: false)
        }
    }

    private func subtitle(_ model: APIKeysModel) -> String {
        switch model.sourcing {
        case .sourced:
            return "From \(displayPath(model.path)). New terminals pick up changes."
        case .notSourced:
            return "From \(displayPath(model.path)). New terminals don't read this file yet."
        case .unsupportedShell:
            return "From \(displayPath(model.path)). Add `source \(displayPath(model.path))` to your shell's startup file so new terminals get these keys."
        case .unknown:
            return "From \(displayPath(model.path))."
        }
    }

    private func footer(_ model: APIKeysModel) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text("\(model.rows.count) \(model.rows.count == 1 ? "key" : "keys") from \(model.providers.count) \(model.providers.count == 1 ? "provider" : "providers")")
                if let saved = model.savedAt {
                    Text("· saved \(saved, format: .relative(presentation: .named))")
                }
            }
            if model.savedAt != nil && model.sourcing == .sourced {
                Text("Terminals already open keep the old values until you run source \(displayPath(model.path)).")
            }
        }
        .font(Theme.caption).foregroundStyle(Theme.textMuted)
    }

    /// Copies a key marked concealed, so clipboard managers skip it.
    private func copySecret(_ value: String) {
        let board = NSPasteboard.general
        board.clearContents()
        board.declareTypes([.string, NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType")], owner: nil)
        board.setString(value, forType: .string)
        board.setString("", forType: NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"))
    }
}

/// One row: grip, logo and name, the variable with its value and two small
/// buttons beside it (reveal, copy), then the status. Anywhere but those
/// buttons and the status opens the key.
private struct InferenceRow: View {
    var item: InferenceItem
    /// Narrow table: the type goes under the name instead of its own column.
    var compact = false
    var isRevealed: Bool
    var check: APIKeyCheck?
    var isChecking: Bool
    var recheck: () -> Void
    var toggleReveal: () -> Void
    var copy: () -> Void
    var open: () -> Void
    /// Set on a key under a heading of its own, to rename that heading.
    var renameProvider: (() -> Void)?
    @State private var hovering = false

    private var logo: String? {
        item.plan?.logo ?? item.key.flatMap { APIKeyProviderLogo.logoID(for: $0.provider) }
    }

    private var type: String { item.kind == .plan ? "Coding plan" : "API key" }

    /// Nothing under the name, except in a narrow table, where the type
    /// moves there from its own column.
    private var subtitle: String? { compact ? type : nil }

    var body: some View {
        HStack(spacing: 12) {
            GripDots()
            HStack(spacing: 8) {
                Group {
                    if let logo { HarnessIcon(harness: logo, size: 16) } else { Color.clear }
                }
                .frame(width: 16, height: 16)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    if let subtitle {
                        Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                    }
                }
            }
            .frame(width: compact ? InferenceSection.compactFirstWidth : InferenceSection.firstWidth,
                   alignment: .leading)
            // The variable name lives in the editor; the row shows the value.
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(valueText)
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(item.hasKey ? Theme.textSecondary : Theme.textMuted)
                        .lineLimit(1).truncationMode(.middle)
                    if item.hasKey {
                        smallButton(isRevealed ? "Hide" : "Reveal", isRevealed ? "eye.slash" : "eye", action: toggleReveal)
                        CopyButton(help: "Copy the value", iconSize: 10, copy: copy)
                            .foregroundStyle(Theme.textMuted)
                            .buttonStyle(.plain)
                            .frame(width: 16, height: 16)
                            .contentShape(.rect)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if !compact {
                Text(type)
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(width: InferenceSection.typeWidth, alignment: .leading)
            }
            Group {
                if let key = item.key, item.hasKey {
                    StatusLabel(status: key.status, check: check, isChecking: isChecking, recheck: recheck)
                } else {
                    Label("No key", systemImage: "minus.circle")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            .frame(width: InferenceSection.statusWidth, alignment: .leading)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(hovering ? Theme.rowHover : .clear)
        .contentShape(.rect)
        .onTapGesture(perform: open)
        .onHover { hovering = $0 }
        .help(item.hasKey ? "Click to edit \(item.id)" : "Click to add a key for \(item.title)")
        .contextMenu {
            Button(item.hasKey ? "Edit…" : "Add key…", action: open)
            if let renameProvider { Button("Rename provider…", action: renameProvider) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: item.hasKey ? "Edit" : "Add key", open)
    }

    private var valueText: String {
        guard let key = item.key else { return "No key" }
        if key.value.isEmpty { return "Empty" }
        return isRevealed ? key.value : key.masked
    }

    /// Small and quiet beside the value, so the key stays the thing you read.
    private func smallButton(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(title, systemImage: symbol, action: action)
            .labelStyle(.iconOnly)
            .font(Theme.font(size: 10))
            .foregroundStyle(Theme.textMuted)
            .buttonStyle(.plain)
            .frame(width: 16, height: 16)
            .contentShape(.rect)
            .help(isRevealed ? "Hide the value" : "Show the value")
    }
}

/// Add or change a plan's key. Shows where to get one and the defaults the
/// vendor documents for Claude Code; saving writes the key to the keys
/// file under the plan's variable and checks it.
private struct PlanKeyEditor: View {
    @Environment(\.dismissSheet) private var dismiss
    var plan: InferencePlan
    var current: String
    /// Whether the keys file has the variable at all, empty or not, so an
    /// empty entry can still be removed.
    var exists: Bool
    var save: (String) async throws -> Void
    var remove: () async throws -> Void
    @State private var value: String
    @State private var showValue = false
    @State private var confirmingRemove = false
    @State private var error: String?
    @State private var working = false

    init(plan: InferencePlan, current: String, exists: Bool, save: @escaping (String) async throws -> Void,
         remove: @escaping () async throws -> Void) {
        self.plan = plan
        self.current = current
        self.exists = exists
        self.save = save
        self.remove = remove
        _value = State(initialValue: current)
    }

    private var trimmed: String { value.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    if let logo = plan.logo { HarnessIcon(harness: logo, size: 20) }
                    Text(exists ? plan.name : "Set up \(plan.name)")
                        .font(Theme.sectionTitle).tracking(-0.5).foregroundStyle(Theme.textPrimary)
                }
                Text("\(plan.vendor) · saved as \(plan.keyName)")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            FormField("Plan key", hint: "The key from the plan's own page, not a pay-as-you-go key.") {
                HStack(spacing: 6) {
                    Group {
                        if showValue {
                            TextField("Paste your \(plan.name) key", text: $value)
                        } else {
                            SecureField("Paste your \(plan.name) key", text: $value)
                        }
                    }
                    .sweField(monospaced: true)
                    Button(showValue ? "Hide" : "Show", systemImage: showValue ? "eye.slash" : "eye") { showValue.toggle() }
                        .iconButton()
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Where it's served").font(Theme.cardTitle).foregroundStyle(Theme.textSecondary)
                VStack(spacing: 0) {
                    ForEach(plan.endpoints, id: \.anthropicBaseURL) { endpoint in
                        HStack(spacing: 10) {
                            Badge(endpoint.region)
                            Text(endpoint.anthropicBaseURL)
                                .font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                                .lineLimit(1).truncationMode(.middle).textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Link("Get a key", destination: endpoint.console)
                                .font(Theme.caption)
                        }
                        .padding(.vertical, 7)
                        if endpoint != plan.endpoints.last { Divider().overlay(Theme.border) }
                    }
                }
                .padding(.horizontal, Theme.cardPadding).padding(.vertical, 4)
                .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
                Text(defaultsLine)
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if exists {
                    Button("Delete", role: .destructive) { confirmingRemove = true }
                        .secondaryButton()
                        .help("Delete the \(plan.name) key from the keys file")
                        .confirmationDialog("Delete the \(plan.name) key?", isPresented: $confirmingRemove) {
                            Button("Delete", role: .destructive) { run { try await remove() } }
                        }
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
                Button(working ? "Saving…" : "Save", action: { run { try await save(trimmed) } })
                    .keyboardShortcut(.defaultAction)
                    .primaryButton()
                    .disabled(working || trimmed.isEmpty || trimmed == current)
            }
        }
        // Wide enough for a full base URL beside its region and link.
        .sheetScaffold(width: 640)
        .background(Theme.panel)
    }

    /// The rest of what the vendor's Claude Code guide sets, for reference.
    private var defaultsLine: String {
        var parts = ["Claude Code takes the key as \(plan.claudeCodeKeyVar)"]
        if let model = plan.model { parts.append("default model \(model)") }
        return parts.joined(separator: ", ") + ". SWE only stores the key; it doesn't change any harness."
    }

    private func run(_ action: @escaping () async throws -> Void) {
        working = true
        error = nil
        Task {
            do {
                try await action()
                working = false
                dismiss()
            } catch {
                working = false
                self.error = error.localizedDescription
            }
        }
    }
}

/// Whether a new terminal gets the key, repainted by the last check:
/// accepted goes green, refused goes red behind `xmark.circle`, and a
/// spinner sits where the icon was while the provider is being asked.
/// Clicking asks again, queued behind the same limiter as a full run.
private struct StatusLabel: View {
    var status: APIKeysModel.Status
    var check: APIKeyCheck?
    var isChecking: Bool
    var recheck: () -> Void

    var body: some View {
        Button(action: recheck) {
            HStack(spacing: 5) {
                if isChecking {
                    ProgressView().controlSize(.mini)
                        .frame(width: 12, height: 12)
                } else {
                    Image(systemName: symbol).foregroundStyle(iconColor)
                }
                Text(text).foregroundStyle(labelColor)
            }
            .font(Theme.caption)
        }
        .buttonStyle(.plain)
        .hoverTip(tooltip, replacesLabel: false)
    }

    private var style: APIKeyCheckStyle { APIKeyCheckStyle(check?.result) }

    private var symbol: String {
        if style == .rejected { return "xmark.circle" }
        return switch status {
        case .loaded: "checkmark.circle"
        case .overridden: "exclamationmark.triangle"
        case .notLoaded: "minus.circle"
        case .unknown: "questionmark.circle"
        case .checking: "ellipsis.circle"
        }
    }

    private var iconColor: Color {
        switch style {
        case .success: Theme.success
        case .rejected: Theme.danger
        case .unchanged: statusIconColor
        }
    }

    private var labelColor: Color {
        switch style {
        case .success: Theme.success
        case .rejected: Theme.danger
        case .unchanged: Theme.textPrimary
        }
    }

    private var statusIconColor: Color {
        switch status {
        case .loaded, .notLoaded: Theme.textSecondary
        case .overridden: Theme.warningText
        case .unknown, .checking: Theme.textMuted
        }
    }

    private var text: String {
        switch status {
        case .loaded: "Loaded"
        case .overridden: "Overridden"
        case .notLoaded: "Not loaded"
        case .unknown: "Unknown"
        case .checking: "Checking…"
        }
    }

    private var tooltip: String {
        var text = switch status {
        case .loaded: "A new terminal gets this value."
        case .overridden: "A new terminal gets a different value: something later in your shell setup sets it again."
        case .notLoaded: "A new terminal doesn't have this variable. Is the file sourced from your shell setup?"
        case .unknown: "SWE couldn't read your login shell."
        case .checking: "Starting a login shell to see what a new terminal gets."
        }
        if let check {
            text += "\n\(check.result.words)\nChecked \(check.checkedAt.formatted(.relative(presentation: .named)))"
        }
        return text
    }
}

struct KeyDraft: Identifiable {
    var id: String { isNew ? "new" : name }
    var provider: String
    var name: String
    var value: String
    var isNew: Bool
    /// A catalogue pick on a new key: the `APIKeyProvider.id` to start
    /// the provider picker with. Nil on a blank add and on edits.
    var providerID: String? = nil
}

/// Add, change or remove one key. A new key first picks its provider from
/// the catalog: a known provider fixes the variable name (a pick when it
/// has several) and offers the page that hands out keys; "Custom provider"
/// takes an OpenAI-compatible base URL, lists its models for a searchable
/// pick and writes `X_BASE_URL`, `X_API_KEY` and `X_MODEL`. On an
/// existing key the provider and the variable name edit as before: a
/// rename, a move and a new value save together. Saving a non-empty
/// value tests it first; a rejection shows instead of saving until
/// "Save anyway".
private struct KeyEditor: View {
    @Environment(\.dismissSheet) private var dismiss
    @State var draft: KeyDraft
    /// The name the row had when the sheet opened; the draft's changes.
    private let originalName: String
    /// The original value, to tell whether a test is worth offering.
    private let originalValue: String
    var providers: [String]
    /// Every row in the file, for heading picks and "already set" notes.
    var rows: [APIKeysModel.Row]
    /// A plan picked in the catalogue while this sheet is up: the sheet
    /// closes and the plan's own editor opens for it.
    var pickPlan: (InferencePlan) -> Void
    /// provider, name, value, the test verdict to keep.
    var save: (String, String, String, APIKeyCheckResult?) async throws -> Void
    /// heading, the variables to write, the verdict, the name it belongs to.
    var saveCustom: (String, [(name: String, value: String)], APIKeyCheckResult?, String) async throws -> Void
    /// name, value -> the provider's verdict.
    var test: (String, String) async -> APIKeyCheckResult
    /// key, base URL -> the custom endpoint's verdict.
    var testCustom: (String, String) async -> APIKeyCheckResult
    /// base URL, key -> the models the endpoint lists.
    var fetchModels: (String, String) async throws -> [CustomProvider.Model]
    var remove: (String) async throws -> Void
    @State private var showValue = false
    @State private var confirmingRemove = false
    @State private var error: String?
    @State private var working = false
    /// The new-key pick: "" nothing yet, "custom" the endpoint path,
    /// else an `APIKeyProvider.id`. A catalogue pick seeds it through
    /// the draft's `providerID` or, while the sheet is up, Browse.
    @State private var providerChoice = ""
    /// The variable a multi-variable provider saves under.
    @State private var envVarChoice = ""
    /// The provider catalogue over this sheet, from Browse.
    @State private var browsing = false
    /// The last verdict and the (name, value, base) it describes; the
    /// custom path's base URL is part of the identity, so pointing the
    /// endpoint elsewhere asks for a fresh test.
    @State private var testResult: APIKeyCheckResult?
    @State private var testedFor = (name: "", value: "", base: "")
    @State private var testing = false
    /// A rejected test showed; the next click saves regardless.
    @State private var saveAnyway = false
    /// The test-then-save in flight; closing the sheet cancels it, so a
    /// Cancel during the test never saves.
    @State private var saveTask: Task<Void, Never>?
    /// The custom provider's name: the heading and the variables' prefix.
    @State private var customName = ""
    /// Its OpenAI-compatible root, e.g. https://soclaas-api.comp.nus.edu.sg/v1.
    @State private var baseURL = ""
    /// The base URL the model list may be fetched from: set when the
    /// field is left or submitted, never mid-typing, so the key never
    /// goes to a half-typed host ("https://api.example.co").
    @State private var committedBase = ""
    @FocusState private var baseFocused: Bool
    /// The picked or typed model id; "" writes no `X_MODEL`.
    @State private var model = ""
    /// The id a dropdown row last put in `model` — typing away from it is
    /// what reopens the list after a pick.
    @State private var pickedModel = ""
    /// Whether the dropdown is showing: opens on focus and on typing,
    /// closes on a pick, so an empty field can still browse the list.
    @State private var modelListOpen = false
    /// What the last `{base}/models` fetch brought back.
    @State private var fetchedModels: [CustomProvider.Model] = []
    @State private var modelsStatus = ModelsFetch.idle
    @FocusState private var modelFocused: Bool

    /// Where the model fetch stands, painted under the field.
    private enum ModelsFetch: Equatable {
        /// Not asked, or the URL and key can't produce an answer yet.
        case idle
        case loading
        case loaded
        case failed(String)
    }

    init(draft: KeyDraft, providers: [String], rows: [APIKeysModel.Row],
         pickPlan: @escaping (InferencePlan) -> Void,
         save: @escaping (String, String, String, APIKeyCheckResult?) async throws -> Void,
         saveCustom: @escaping (String, [(name: String, value: String)], APIKeyCheckResult?, String) async throws -> Void,
         test: @escaping (String, String) async -> APIKeyCheckResult,
         testCustom: @escaping (String, String) async -> APIKeyCheckResult,
         fetchModels: @escaping (String, String) async throws -> [CustomProvider.Model],
         remove: @escaping (String) async throws -> Void) {
        _draft = State(initialValue: draft)
        originalName = draft.name
        originalValue = draft.value
        self.providers = providers
        self.rows = rows
        self.pickPlan = pickPlan
        self.save = save
        self.saveCustom = saveCustom
        self.test = test
        self.testCustom = testCustom
        self.fetchModels = fetchModels
        self.remove = remove
        let prefill = draft.providerID.flatMap { APIKeyProviders.provider(forID: $0) }
        _providerChoice = State(initialValue: prefill?.id ?? "")
        _envVarChoice = State(initialValue: prefill?.envVars[0] ?? "")
    }

    /// The picked provider on a new key, nil for "custom" or none yet.
    private var picked: APIKeyProvider? {
        draft.isNew ? APIKeyProviders.provider(forID: providerChoice) : nil
    }

    /// Whether the custom-provider section shows.
    private var isCustom: Bool { draft.isNew && providerChoice == "custom" }

    /// The variable the save writes.
    private var finalName: String {
        if isCustom { return customPrefix.isEmpty ? "" : customPrefix + "_API_KEY" }
        if let picked {
            return envVarChoice.isEmpty ? picked.envVars[0] : envVarChoice
        }
        return draft.name.trimmingCharacters(in: .whitespaces)
    }

    /// The heading the save writes under.
    private var finalProvider: String {
        if isCustom {
            return customName.trimmingCharacters(in: .whitespaces)
        }
        if let picked {
            return APIKeyProviders.heading(for: picked, in: rows)
        }
        // Only an edit reaches here: the provider field's text, "" for
        // ungrouped.
        return draft.provider.trimmingCharacters(in: .whitespaces)
    }

    private var nameProblem: String? {
        let name = draft.name.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return "Enter a variable name, like ANTHROPIC_API_KEY" }
        if name.range(of: #"^[A-Za-z_][A-Za-z0-9_]*$"#, options: .regularExpression) == nil {
            return "Use letters, digits and underscores, not starting with a digit"
        }
        return nil
    }

    /// The free-text variable field shows on edits only: a new key
    /// always takes a catalog or custom variable name.
    private var freeFormName: Bool {
        !draft.isNew
    }

    /// The env-var prefix the provider name gives: "SoCLaaS" → SOCLAAS.
    private var customPrefix: String { CustomProvider.envPrefix(for: customName) }

    /// What stops a custom name, or nil: it must read back as a heading
    /// and prefix three valid variable names.
    private var customNameProblem: String? {
        let name = customName.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return nil }
        guard APIKeysFile.isLabel(name) else {
            return "Use a short label: letters, digits, spaces and ( ) . , & / -"
        }
        let prefix = CustomProvider.envPrefix(for: name)
        if prefix.isEmpty { return "The name needs a letter or digit for the variables' prefix" }
        if prefix.first?.isNumber == true {
            return "Start the name with a letter: \(prefix)_API_KEY isn't a variable name"
        }
        return nil
    }

    /// What stops a base URL, or nil.
    private var baseProblem: String? {
        let base = baseURL.trimmingCharacters(in: .whitespaces)
        guard !base.isEmpty else { return nil }
        guard CustomProvider.modelsURL(base) == nil else { return nil }
        return base.lowercased().hasPrefix("http://")
            ? "Use https: plain http is only for this Mac or a private network, since the key travels with every request"
            : "An https URL; the models list comes from {base}/models"
    }

    /// The variables a custom save would overwrite.
    private var customCollision: String? {
        guard !customPrefix.isEmpty else { return nil }
        let names = [customPrefix + "_API_KEY", customPrefix + "_BASE_URL", customPrefix + "_MODEL"]
        let existing = names.filter { name in rows.contains(where: { $0.name == name }) }
        guard !existing.isEmpty else { return nil }
        return "\(existing.joined(separator: ", ")) already set; saving replaces \(existing.count == 1 ? "it" : "them")."
    }

    /// The verdict still describes the current name, value and endpoint.
    private var currentTest: APIKeyCheckResult? {
        testedFor == (finalName, draft.value, testedBase) ? testResult : nil
    }

    /// The base a test answer is bound to; plain providers have none.
    private var testedBase: String {
        isCustom ? baseURL.trimmingCharacters(in: .whitespaces) : ""
    }

    /// A test is worth offering: a new non-empty value, or an edit that
    /// changed the value or the variable it answers to. The custom path
    /// also needs a base URL to test against.
    private var canTest: Bool {
        guard !draft.value.isEmpty,
              draft.isNew || draft.value != originalValue || finalName != originalName
        else { return false }
        return isCustom ? CustomProvider.modelsURL(baseURL) != nil : true
    }

    /// Where "Get a key" points on an existing key whose variable the
    /// catalog knows.
    private var editKeyURL: URL? {
        guard !draft.isNew else { return nil }
        return APIKeyProviders.provider(forVariable: finalName)?.keyURL
    }

    private var saveDisabled: Bool {
        if working || testing { return true }
        if draft.isNew && providerChoice.isEmpty { return true }
        if isCustom {
            if customName.trimmingCharacters(in: .whitespaces).isEmpty
                || baseURL.trimmingCharacters(in: .whitespaces).isEmpty
                || customNameProblem != nil || baseProblem != nil
                || draft.value.isEmpty { return true }
        }
        if freeFormName && nameProblem != nil { return true }
        return false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(draft.isNew ? "Add a key" : "Edit \(originalName)").font(Theme.sectionTitle).tracking(-0.5)
            Group {
                if draft.isNew { providerPicker }
                if isCustom { customFields }
                if freeFormName { providerField }
                if let picked { knownProviderFields(picked) }
                if freeFormName { variableField }
                valueField
                if isCustom { modelField }
            }
            .disabled(testing)
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                if draft.isNew {
                    Button("Browse…") { browsing = true }
                        .secondaryButton()
                        .help("Every plan and provider SWE knows")
                        .disabled(working || testing)
                } else if confirmingRemove {
                    Text("Delete \(originalName)?").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                    Button("Delete", role: .destructive) { run { try await remove(originalName) } }
                        .secondaryButton()
                    Button("Keep") { confirmingRemove = false }.secondaryButton()
                } else {
                    Button("Delete", role: .destructive) { confirmingRemove = true }
                        .secondaryButton()
                        .help("Delete \(originalName) from the keys file")
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
                Button(saveAnyway ? "Save anyway" : (draft.isNew ? "Add key" : "Save")) { saveTapped() }
                    .keyboardShortcut(.defaultAction)
                    .primaryButton()
                    .disabled(saveDisabled)
            }
        }
        .padding(24)
        .frame(width: 520)
        .background(Theme.panel)
        .onDisappear { saveTask?.cancel() }
        .task(id: fetchSeed) { await loadModels() }
        .quiverSheet(isPresented: $browsing) {
            ProviderCatalogueSheet(entries: InferenceCatalogue.entries(keys: rows)) { entry in
                switch entry.target {
                case .provider(let provider): providerChoice = provider.id
                case .plan(let plan): pickPlan(plan)
                }
            }
        }
    }

    /// The catalog picker a new key starts with: the built-ins, then
    /// "Custom provider" for an OpenAI-compatible endpoint.
    private var providerPicker: some View {
        FormField("Provider") {
            PopoverSelect(options: APIKeyProviders.all.map { ($0.id, $0.name, $0.logo) }
                              + [("custom", "Custom provider", nil)],
                          selection: $providerChoice,
                          title: providerChoice.isEmpty ? "Choose a provider" : nil)
            .onChange(of: providerChoice) {
                envVarChoice = picked?.envVars[0] ?? ""
                saveAnyway = false
                fetchedModels = []
                modelsStatus = .idle
            }
        }
    }

    /// The custom provider's own row of fields: the name that heads its
    /// group and prefixes its variables, then the OpenAI-compatible base
    /// URL the model list and key check come from.
    private var customFields: some View {
        Group {
            FormField("Provider name", hint: customNameHint, problem: customNameProblem != nil) {
                TextField("SoCLaaS", text: $customName)
                    .sweField()
                    .onChange(of: customName) { saveAnyway = false }
            }
            FormField("Base URL", hint: baseProblem ?? "The OpenAI-compatible root; models list at {base}/models.",
                      problem: baseProblem != nil) {
                TextField("https://soclaas-api.comp.nus.edu.sg/v1", text: $baseURL)
                    .sweField(monospaced: true)
                    .focused($baseFocused)
                    .onChange(of: baseURL) { saveAnyway = false }
                    .onSubmit { committedBase = baseURL }
                    .onChange(of: baseFocused) { _, focused in
                        if !focused { committedBase = baseURL }
                    }
            }
            if let collision = customCollision {
                Text(collision).font(Theme.caption).foregroundStyle(Theme.warningText)
            }
        }
    }

    /// The derived hint under the name field: the three variables the
    /// save writes, or the reason the name won't do.
    private var customNameHint: String {
        if let problem = customNameProblem { return problem }
        let prefix = customPrefix
        if prefix.isEmpty { return "The heading in the file; the variables take it as a prefix." }
        return "Saved as \(prefix)_BASE_URL, \(prefix)_API_KEY and \(prefix)_MODEL."
    }

    /// The model field: a free-text id with a dropdown of what
    /// `{base}/models` answered, filtered by the text as it changes.
    private var modelField: some View {
        FormField("Model", hint: modelHint, problem: modelProblem) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    TextField("Pick or type a model id", text: $model)
                        .sweField(monospaced: true)
                        .focused($modelFocused)
                    if case .loading = modelsStatus {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Reload models", systemImage: "arrow.clockwise") {
                            if committedBase != baseURL {
                                committedBase = baseURL   // the .task fetches
                            } else {
                                Task { await loadModels(now: true) }
                            }
                        }
                        .iconButton()
                        .disabled(!canLoadModels)
                        .help("Ask {base}/models again")
                    }
                }
                if modelFocused && modelListOpen && !filteredModels.isEmpty {
                    modelDropdown
                }
            }
            .onChange(of: modelFocused) { if modelFocused { modelListOpen = true } }
            .onChange(of: model) { if model != pickedModel { modelListOpen = true } }
        }
    }

    /// The fetched ids the field's text leaves; scrolling bounds the
    /// height, so the whole list stays reachable.
    private var filteredModels: [CustomProvider.Model] {
        let query = model.trimmingCharacters(in: .whitespaces).lowercased()
        guard !query.isEmpty else { return fetchedModels }
        return fetchedModels.filter {
            $0.id.lowercased().contains(query) || $0.label.lowercased().contains(query)
        }
    }

    /// Seven rows (~32 pt each) show before the dropdown scrolls.
    private static let modelListHeight: CGFloat = 7 * 32

    /// The dropdown itself: label on the left, id beside it when the
    /// endpoint names its models something else.
    private var modelDropdown: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(filteredModels) { item in
                    Button { model = item.id; pickedModel = item.id; modelListOpen = false } label: {
                        HStack(spacing: 8) {
                            Text(item.label).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).lineLimit(1)
                            if item.label != item.id {
                                Text(item.id).font(Theme.caption).foregroundStyle(Theme.textMuted)
                                    .lineLimit(1).truncationMode(.middle)
                            }
                            Spacer(minLength: 8)
                        }
                        .padding(.horizontal, 10).padding(.vertical, 7)
                        .contentShape(.rect)
                    }
                    .plainButton(cornerRadius: 0)
                    .modifier(RowHover())
                }
            }
            .padding(.vertical, 4)
        }
        .frame(maxHeight: Self.modelListHeight)
        .background(Theme.field, in: .rect(cornerRadius: Theme.fieldRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.fieldRadius).strokeBorder(Theme.border, lineWidth: 1))
        .padding(.top, 4)
    }

    /// What the model field's hint reports: the fetch's state, or how to
    /// get one going.
    private var modelHint: String? {
        switch modelsStatus {
        case .idle:
            "Optional. The list fills from {base}/models once the URL and key are set."
        case .loading:
            "Asking the endpoint for its models…"
        case .loaded:
            fetchedModels.isEmpty
                ? "The endpoint listed no models; type the id it expects."
                : "\(fetchedModels.count) \(fetchedModels.count == 1 ? "model" : "models") — pick one or type your own."
        case .failed(let message):
            message
        }
    }

    /// A failed fetch paints the hint red.
    private var modelProblem: Bool {
        if case .failed = modelsStatus { return true }
        return false
    }

    /// Whether `{base}/models` is worth asking: a valid URL and a key.
    private var canLoadModels: Bool {
        CustomProvider.modelsURL(baseURL) != nil && !draft.value.isEmpty
    }

    /// The inputs a fetch describes, as the `.task` id: changing either
    /// cancels the pending fetch and starts the debounce over.
    private var fetchSeed: String {
        isCustom ? committedBase + "\u{1F}" + draft.value : ""
    }

    /// Fetches the model list — debounced from `.task` edits, at once
    /// from the reload button. A fetch that lands after its inputs moved
    /// on writes nothing, so a stale answer can't erase a newer list.
    private func loadModels(now: Bool = false) async {
        guard isCustom, CustomProvider.modelsURL(committedBase) != nil,
              !draft.value.isEmpty else {
            fetchedModels = []
            modelsStatus = .idle
            return
        }
        let seed = fetchSeed
        if !now {
            try? await Task.sleep(nanoseconds: 400_000_000)
            guard !Task.isCancelled else { return }
        }
        modelsStatus = .loading
        let base = committedBase.trimmingCharacters(in: .whitespaces)
        do {
            let found = try await fetchModels(base, draft.value)
            guard seed == fetchSeed else { return }
            fetchedModels = found
            modelsStatus = .loaded
        } catch {
            guard seed == fetchSeed else { return }
            fetchedModels = []
            modelsStatus = .failed(error.localizedDescription)
        }
    }

    /// What a picked provider fixes: the variable it saves as (a pick
    /// when it has several), where to get a key, and a note when that
    /// variable is already in the file.
    private func knownProviderFields(_ provider: APIKeyProvider) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if provider.envVars.count > 1 {
                    Text("saved as").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    PopoverSelect(options: provider.envVars.map { ($0, $0, nil) },
                                  selection: $envVarChoice)
                        .onChange(of: envVarChoice) { saveAnyway = false }
                } else {
                    Text("saved as \(provider.envVars[0])")
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textSecondary)
                }
                Spacer(minLength: 8)
                Link("Get a key", destination: provider.keyURL).font(Theme.caption)
            }
            if rows.contains(where: { $0.name == finalName }) {
                Text("\(finalName) is already set; saving replaces it.")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
            }
        }
    }

    /// The group the key files under: edit-only now, free text plus the
    /// existing groups so a key can move or go ungrouped.
    private var providerField: some View {
        FormField("Provider") {
            HStack(spacing: 8) {
                // Another group's name moves the key to the end of it.
                TextField("ANTHROPIC", text: $draft.provider).sweField()
                if !providers.isEmpty {
                    // "" is a real choice (ungrouped keys); it still
                    // needs a label in the menu.
                    PopoverSelect(options: providers.map {
                                      ($0, $0.isEmpty ? "Ungrouped" : $0, APIKeyProviderLogo.logoID(for: $0))
                                  },
                                  selection: $draft.provider, title: "Existing")
                }
            }
            if let editKeyURL {
                Link("Get a key", destination: editKeyURL).font(Theme.caption)
            }
        }
    }

    private var variableField: some View {
        FormField("Variable") {
            TextField("ANTHROPIC_API_KEY", text: $draft.name)
                .sweField(monospaced: true)
                .onChange(of: draft.name) { saveAnyway = false }
            if !draft.name.isEmpty, let problem = nameProblem {
                Text(problem).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
        }
    }

    private var valueField: some View {
        FormField("Value") {
            HStack(spacing: 8) {
                Group {
                    if showValue {
                        TextField("sk-…", text: $draft.value)
                    } else {
                        SecureField("sk-…", text: $draft.value)
                    }
                }
                .sweField(monospaced: true)
                .onChange(of: draft.value) { saveAnyway = false }
                Button(showValue ? "Hide" : "Show", systemImage: showValue ? "eye.slash" : "eye") { showValue.toggle() }
                    .iconButton()
                    .help(showValue ? "Hide the value" : "Show the value")
                if canTest {
                    Button(testing ? "Testing…" : "Test", action: testTapped)
                        .secondaryButton()
                        .disabled(testing || freeFormName && nameProblem != nil)
                }
            }
            if let result = currentTest {
                Text(result.words)
                    .font(Theme.caption)
                    .foregroundStyle(testColor(result))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func testColor(_ result: APIKeyCheckResult) -> Color {
        switch APIKeyCheckStyle(result) {
        case .success: Theme.success
        case .rejected: Theme.danger
        case .unchanged: Theme.textSecondary
        }
    }

    private func testTapped() {
        testing = true
        let name = finalName
        let value = draft.value
        let base = baseURL.trimmingCharacters(in: .whitespaces)
        let custom = isCustom
        Task {
            let result = custom ? await testCustom(value, base) : await test(name, value)
            testing = false
            testResult = result
            testedFor = (name, value, custom ? base : "")
        }
    }

    /// Delete or any plain action: no test runs first.
    private func run(_ action: @escaping () async throws -> Void) {
        working = true
        Task {
            defer { working = false }
            do {
                try await action()
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    /// Test first, then save with the verdict. A rejection stops the save
    /// once and turns the button into "Save anyway". Everything the save
    /// writes is captured at the tap: a pick that slips in while the test
    /// runs (the fields disable on the next render, not at once) can't
    /// switch the save between the provider paths mid-flight.
    private func saveTapped() {
        working = true
        error = nil
        let name = finalName
        let provider = finalProvider
        let value = draft.value
        let base = baseURL.trimmingCharacters(in: .whitespaces)
        let custom = isCustom
        let prefix = customPrefix
        let modelID = model.trimmingCharacters(in: .whitespaces)
        let testable = canTest
        saveTask = Task {
            defer { working = false }
            var result = currentTest
            // A move to another provider alone keeps the key it had, so
            // it asks the provider nothing.
            if testable, result == nil {
                testing = true
                if custom {
                    result = await testCustom(value, base)
                } else {
                    result = await test(name, value)
                }
                testing = false
                testResult = result
                testedFor = (name, value, custom ? base : "")
            }
            guard !Task.isCancelled else { return }
            if case .rejected = result, !saveAnyway {
                saveAnyway = true
                return
            }
            do {
                if custom {
                    let entries = CustomProvider.entries(
                        prefix: prefix, baseURL: base, key: value, model: modelID)
                    try await saveCustom(provider, entries, value.isEmpty ? nil : result, name)
                } else {
                    try await save(provider, name, value, value.isEmpty ? nil : result)
                }
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Every plan and provider SWE knows, from the Catalogue button or
/// Browse in a key editor, in the shared "Connect apps" layout: a search
/// field, the kind chips, and a two-column grid of `CatalogueRow`s.
/// Picking a row starts its add-key flow and closes the sheet. A row the
/// keys file already covers reads Set and still picks: for a plan that
/// edits the existing key, for a provider it adds a sibling variable.
private struct ProviderCatalogueSheet: View {
    var entries: [InferenceCatalogue.Entry]
    /// Runs with the pick just before the sheet closes itself.
    var pick: (InferenceCatalogue.Entry) -> Void
    @Environment(\.dismissSheet) private var dismiss
    @State private var query = ""
    /// The chip pick; nil is "All".
    @State private var kind: InferenceItem.Kind?

    private static let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 2)

    private var shown: [InferenceCatalogue.Entry] {
        InferenceCatalogue.filter(entries, kind: kind, query: query)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Provider catalogue")
                .font(Theme.sectionTitle).tracking(-0.5).foregroundStyle(Theme.textPrimary)
            SearchField(prompt: "Search plans and providers", text: $query)
            Tabs(options: [(InferenceItem.Kind?.none, "All"),
                           (.plan, "Coding plans"), (.apiKey, "API keys")],
                 selection: $kind, style: .table)
            ScrollView {
                if shown.isEmpty {
                    Text("Nothing matches.")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity).padding(.vertical, 20)
                } else {
                    LazyVGrid(columns: Self.columns, spacing: 8) {
                        ForEach(shown) { entry in
                            CatalogueRow(logo: entry.logo, name: entry.name, detail: entry.detail,
                                         done: entry.hasKey ? "Set" : nil, reusable: true) {
                                pick(entry)
                                dismiss()
                            }
                        }
                    }
                }
            }
            .frame(height: 420)
            HStack {
                Text("\(shown.count) of \(entries.count)")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
            }
        }
        .sheetScaffold(width: Theme.sheetWidth)
    }
}
