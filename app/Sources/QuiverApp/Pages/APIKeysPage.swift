import AppKit
import QuiverCore
import SwiftUI

/// The keys in `~/.api_keys`, grouped by provider, masked until revealed.
struct APIKeysPage: View {
    @Environment(AppModel.self) private var app
    @State private var editing: KeyDraft?
    @State private var revealed: Set<String> = []
    @State private var actionError: String?
    /// The provider whose heading is being renamed, and its draft name.
    @State private var renamingProvider = false
    @State private var providerBeingRenamed = ""
    @State private var providerDraft = ""

    var body: some View {
        @Bindable var model = app.apiKeys
        Page(title: "API keys", subtitle: "From \(displayPath(model.path)). New terminals pick up changes.") {
            Button("Reload", systemImage: "arrow.clockwise") { actionError = nil; Task { await model.load() } }
                .iconButton()
                .disabled(model.isLoading)
                .keyboardShortcut("r", modifiers: .command)
                .help("Read the file and the shell again (⌘R)")
            Button("Check keys", systemImage: "checkmark.shield") { Task { await model.checkAll() } }
                .secondaryButton()
                .disabled(model.isChecking)
                .help("Send each key to its own provider to see if it still works")
            Button("Add key", systemImage: "plus") { editing = KeyDraft(provider: "", name: "", value: "", isNew: true) }
                .primaryButton()
        } content: {
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
            SearchField(prompt: "Search providers or variables", text: $model.search)
            if model.rows.isEmpty && !model.isLoading {
                Card {
                    ContentUnavailableView {
                        Label("No keys yet", systemImage: "key")
                    } description: {
                        Text("Add a key and Quiver writes it to \(displayPath(model.path)) as an export line.")
                    } actions: {
                        Button("Add key") { editing = KeyDraft(provider: "", name: "", value: "", isNew: true) }
                            .primaryButton()
                    }
                }
            } else {
                table(model)
            }
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

    private func table(_ model: APIKeysModel) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("Provider").frame(width: 150, alignment: .leading)
                Text("Variable and value").frame(maxWidth: .infinity, alignment: .leading)
                Text("New terminals").frame(width: 120, alignment: .leading)
                Color.clear.frame(width: KeyRow.actionsWidth, height: 1)
            }
            .font(Theme.tableHeader).foregroundStyle(Theme.textSecondary)
            .padding(.horizontal, 16).padding(.vertical, 10)
            ForEach(Array(model.visible.enumerated()), id: \.element.id) { index, row in
                let firstOfGroup = index == 0 || model.visible[index - 1].provider != row.provider
                Divider().overlay(Theme.border).opacity(firstOfGroup ? 1 : 0.5)
                KeyRow(row: row, showsProvider: firstOfGroup, isRevealed: revealed.contains(row.name),
                       check: model.check(for: row), isChecking: model.checking.contains(row.name),
                       // Keys before any heading have no provider, so there
                       // is no heading to rename; a real "Other" heading has one.
                       renameProvider: !firstOfGroup || row.provider.isEmpty ? nil : {
                           providerBeingRenamed = row.provider
                           providerDraft = row.provider
                           renamingProvider = true
                       },
                       recheck: { Task { await model.check(name: row.name) } },
                       toggleReveal: {
                           if revealed.contains(row.name) { revealed.remove(row.name) } else { revealed.insert(row.name) }
                       },
                       copy: { copySecret(row.value) },
                       edit: { editing = KeyDraft(provider: row.provider, name: row.name, value: row.value, isNew: false) })
            }
            if model.visible.isEmpty && !model.search.isEmpty {
                Divider().overlay(Theme.border)
                Text("Nothing matches \u{201C}\(model.search)\u{201D}.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity).padding(.vertical, 20)
            }
        }
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
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

private struct KeyRow: View {
    /// Three 36pt icon buttons 4pt apart.
    static let actionsWidth: CGFloat = 3 * Theme.iconButton + 2 * 4
    var row: APIKeysModel.Row
    var showsProvider: Bool
    var isRevealed: Bool
    var check: APIKeyCheck?
    var isChecking: Bool
    /// Set on a group's first row when its heading can be renamed.
    var renameProvider: (() -> Void)?
    var recheck: () -> Void
    var toggleReveal: () -> Void
    var copy: () -> Void
    var edit: () -> Void
    @State private var hovering = false
    @State private var providerHovering = false

    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 6) {
                // A provider a harness logo stands for shows it; the rest show the heading only.
                if showsProvider, let logo = APIKeyProviderLogo.logoID(for: row.provider) {
                    HarnessIcon(harness: logo, size: 16)
                }
                Text(showsProvider ? row.providerLabel : "").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                if let renameProvider, providerHovering {
                    Button("Rename provider", systemImage: "pencil", action: renameProvider)
                        .labelStyle(.iconOnly)
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.textSecondary)
                        .buttonStyle(.plain)
                        .help("Rename \(row.provider)")
                }
            }
            .frame(width: 150, alignment: .leading)
            .contentShape(.rect)
            .onHover { providerHovering = $0 }
            .contextMenu {
                if let renameProvider {
                    Button("Rename provider…", action: renameProvider)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(row.providerLabel)
            VStack(alignment: .leading, spacing: 3) {
                Text(row.name).font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundStyle(Theme.textPrimary)
                Text(isRevealed ? row.value : (row.value.isEmpty ? "Empty" : row.masked))
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(row.value.isEmpty ? Theme.textMuted : Theme.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            StatusLabel(status: row.status, check: check, isChecking: isChecking,
                        recheck: recheck)
            .frame(width: 120, alignment: .leading)
            HStack(spacing: 4) {
                // One ring per button, so focus shows which one it's on.
                Button(isRevealed ? "Hide" : "Reveal", systemImage: isRevealed ? "eye.slash" : "eye", action: toggleReveal)
                    .help(isRevealed ? "Hide the value" : "Show the value")
                    .iconButton()
                Button("Copy", systemImage: "doc.on.doc", action: copy).help("Copy the value").iconButton()
                Button("Edit", systemImage: "pencil", action: edit).help("Edit or remove").iconButton()
            }
            .frame(width: Self.actionsWidth, alignment: .trailing)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(hovering ? Theme.rowHover : .clear)
        .onHover { hovering = $0 }
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
            field("Provider") {
                HStack(spacing: 8) {
                    // Another group's name moves the key to the end of it.
                    TextField("ANTHROPIC", text: $draft.provider).quiverField()
                    if !providers.isEmpty {
                        // "" is a real choice (ungrouped keys); it still
                        // needs a label in the menu.
                        SelectMenu(options: providers.map { ($0, $0.isEmpty ? "Ungrouped" : $0) },
                                   selection: $draft.provider, width: nil, title: "Existing")
                    }
                }
            }
            field("Variable") {
                TextField("ANTHROPIC_API_KEY", text: $draft.name)
                    .quiverField(monospaced: true)
                if !draft.name.isEmpty, let problem = nameProblem {
                    Text(problem).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
            }
            field("Value") {
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
                        Text("Remove \(originalName)?").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        Button("Remove", role: .destructive) { run { try await remove(originalName) } }
                            .secondaryButton()
                        Button("Keep") { confirmingRemove = false }.secondaryButton()
                    } else {
                        Button("Remove…") { confirmingRemove = true }.secondaryButton()
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

    private func field<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Theme.control).foregroundStyle(Theme.textSecondary)
            content()
        }
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
