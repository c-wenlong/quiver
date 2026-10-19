import AppKit
import QuiverCore
import SwiftUI

/// Settings › Providers › Inference: every key in the API keys file and every
/// plan Quiver knows, in one list you can drag into your own order and
/// filter by what the key is known to be and by kind. A key is masked
/// until revealed; a click on a row opens it.
struct InferenceSection: View {
    enum StateFilter: Hashable { case all, state(InferenceKeyState) }
    enum KindFilter: Hashable { case all, kind(InferenceItem.Kind) }

    @Environment(AppModel.self) private var app
    @State private var editing: KeyDraft?
    @State private var editingPlan: InferenceItem?
    @State private var revealed: Set<String> = []
    @State private var actionError: String?
    @State private var stateFilter = StateFilter.all
    @State private var kindFilter = KindFilter.all
    @State private var query = ""
    @State private var tableWidth: CGFloat = 0
    /// The provider whose heading is being renamed, and its draft name.
    @State private var renamingProvider = false
    @State private var providerBeingRenamed = ""
    @State private var providerDraft = ""

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
                    Text("From \(displayPath(model.path)). New terminals pick up changes.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
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
                    .help("Send each key to its own provider or plan to see if it still works")
                Button("Add key", systemImage: "plus") { editing = KeyDraft(provider: "", name: "", value: "", isNew: true) }
                    .primaryButton()
            }
            if let error = model.error ?? actionError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
            }
            if !model.problems.isEmpty {
                Card(title: "Lines that set nothing") {
                    ForEach(model.problems, id: \.line) { problem in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Line \(problem.line): \(problem.reason)")
                                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            // Already redacted by APIKeysFile: the value never reaches the page.
                            Text(problem.text)
                                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                        }
                    }
                    Text("Fix these in an editor; Quiver leaves lines it can't read untouched.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            filterBar(all)
            table(all)
            footer(model)
        }
        .task(id: model.path) { await model.load() }
        .onDisappear { revealed.removeAll() }
        .sheet(item: $editing) { draft in
            KeyEditor(draft: draft, providers: model.providers, save: { provider, name, value in
                if draft.isNew {
                    try await model.set(name: name, value: value, provider: provider)
                } else {
                    try await model.update(name: draft.name, newName: name, provider: provider, value: value)
                    revealed.remove(draft.name)
                }
            }, remove: { name in
                try await model.remove(name: name)
                revealed.remove(name)
            })
        }
        .sheet(item: $editingPlan) { item in
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
    }

    private func state(of item: InferenceItem) -> InferenceKeyState {
        InferenceKeyState(hasKey: item.hasKey, check: item.key.flatMap { app.apiKeys.check(for: $0) }?.result)
    }

    private func visible(_ all: [InferenceItem]) -> [InferenceItem] {
        let state: InferenceKeyState? = if case .state(let value) = stateFilter { value } else { nil }
        let kind: InferenceItem.Kind? = if case .kind(let value) = kindFilter { value } else { nil }
        return all.filter { InferenceList.matches($0, state: state, kind: kind, query: query, stateOf: self.state(of:)) }
    }

    /// The shared filter bar: selects, then search taking the slack, then the count.
    private func filterBar(_ all: [InferenceItem]) -> some View {
        func count(_ state: InferenceKeyState) -> Int { all.filter { self.state(of: $0) == state }.count }
        func count(_ kind: InferenceItem.Kind) -> Int { all.filter { $0.kind == kind }.count }
        let shown = visible(all).count
        return HStack(spacing: 10) {
            SelectMenu(options: [(StateFilter.all, "Any status (\(all.count))"),
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
                Text(all.isEmpty ? "No keys yet. Add one and Quiver writes it to \(displayPath(app.apiKeys.path))."
                                 : "Nothing matches these filters.")
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

    private func footer(_ model: APIKeysModel) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text("\(model.rows.count) \(model.rows.count == 1 ? "key" : "keys") from \(model.providers.count) \(model.providers.count == 1 ? "provider" : "providers")")
                if let saved = model.savedAt {
                    Text("· saved \(saved, format: .relative(presentation: .named))")
                }
            }
            if model.savedAt != nil {
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
                        .font(.system(size: 12, design: .monospaced))
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
            .font(.system(size: 10))
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
    @Environment(\.dismiss) private var dismiss
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
                    .quiverField(monospaced: true)
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
                                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
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
        return parts.joined(separator: ", ") + ". Quiver only stores the key; it doesn't change any harness."
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
        .help(tooltip)
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
        case .unknown: "Quiver couldn't read your login shell."
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
}

/// Add, change or remove one key. On an existing key the provider and the
/// variable name edit too: a rename, a move and a new value save together.
private struct KeyEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State var draft: KeyDraft
    /// The name the row had when the sheet opened; the draft's changes.
    private let originalName: String
    var providers: [String]
    var save: (String, String, String) async throws -> Void
    var remove: (String) async throws -> Void
    @State private var showValue = false
    @State private var confirmingRemove = false
    @State private var error: String?
    @State private var working = false

    init(draft: KeyDraft, providers: [String], save: @escaping (String, String, String) async throws -> Void,
         remove: @escaping (String) async throws -> Void) {
        _draft = State(initialValue: draft)
        originalName = draft.name
        self.providers = providers
        self.save = save
        self.remove = remove
    }

    private var nameProblem: String? {
        let name = draft.name.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { return "Enter a variable name, like ANTHROPIC_API_KEY" }
        if name.range(of: #"^[A-Za-z_][A-Za-z0-9_]*$"#, options: .regularExpression) == nil {
            return "Use letters, digits and underscores, not starting with a digit"
        }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(draft.isNew ? "Add a key" : "Edit \(originalName)").font(Theme.sectionTitle).tracking(-0.5)
            FormField("Provider") {
                HStack(spacing: 8) {
                    // Another group's name moves the key to the end of it.
                    TextField("ANTHROPIC", text: $draft.provider).quiverField()
                    if !providers.isEmpty {
                        // "" is a real choice (ungrouped keys); it still
                        // needs a label in the menu.
                        PopoverSelect(options: providers.map {
                                          ($0, $0.isEmpty ? "Ungrouped" : $0, APIKeyProviderLogo.logoID(for: $0))
                                      },
                                      selection: $draft.provider, title: "Existing")
                    }
                }
            }
            FormField("Variable") {
                TextField("ANTHROPIC_API_KEY", text: $draft.name)
                    .quiverField(monospaced: true)
                if !draft.name.isEmpty, let problem = nameProblem {
                    Text(problem).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
            }
            FormField("Value") {
                HStack(spacing: 8) {
                    Group {
                        if showValue {
                            TextField("sk-…", text: $draft.value)
                        } else {
                            SecureField("sk-…", text: $draft.value)
                        }
                    }
                    .quiverField(monospaced: true)
                    Button(showValue ? "Hide" : "Show", systemImage: showValue ? "eye.slash" : "eye") { showValue.toggle() }
                        .iconButton()
                        .help(showValue ? "Hide the value" : "Show the value")
                }
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack {
                if !draft.isNew {
                    if confirmingRemove {
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
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
                Button(draft.isNew ? "Add key" : "Save") {
                    run {
                        // An existing key keeps an empty provider (it is
                        // ungrouped); a new one gets the OTHER group.
                        let provider = draft.provider.trimmingCharacters(in: .whitespaces)
                        try await save(draft.isNew && provider.isEmpty ? "OTHER" : provider,
                                       draft.name.trimmingCharacters(in: .whitespaces), draft.value)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .primaryButton()
                .disabled(working || nameProblem != nil)
            }
        }
        .padding(24)
        .frame(width: 520)
        .background(Theme.panel)
    }

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
}
