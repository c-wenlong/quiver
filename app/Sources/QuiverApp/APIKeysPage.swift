import AppKit
import QuiverCore
import SwiftUI

/// The keys in `~/.api_keys`, grouped by provider, masked until revealed.
struct APIKeysPage: View {
    @Environment(AppModel.self) private var app
    @State private var editing: KeyDraft?
    @State private var revealed: Set<String> = []
    @State private var actionError: String?

    var body: some View {
        @Bindable var model = app.apiKeys
        Page(title: "API keys", subtitle: "From \(displayPath(model.path)). New terminals pick up changes.") {
            Button("Reload", systemImage: "arrow.clockwise") { Task { await model.load() } }
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
            if let summary = checkSummary(model) {
                Text(summary).font(Theme.caption).foregroundStyle(Theme.textMuted)
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
                try await model.set(name: name, value: value, provider: provider)
            }, remove: { name in
                try await model.remove(name: name)
                revealed.remove(name)
            })
        }
    }

    /// "18 work · 2 rejected · 1 couldn't check · 3 no check", counting
    /// only results still current for the row's value; parts with none
    /// are left out.
    private func checkSummary(_ model: APIKeysModel) -> String? {
        var works = 0
        var noCredit = 0
        var rejected = 0
        var failed = 0
        var noCheck = 0
        for row in model.rows {
            switch model.check(for: row)?.result {
            case .works: works += 1
            case .noCredit: noCredit += 1
            case .rejected: rejected += 1
            case .couldNotCheck: failed += 1
            case .noCheck: noCheck += 1
            case nil: break
            }
        }
        var parts: [String] = []
        if works > 0 { parts.append("\(works) work") }
        if noCredit > 0 { parts.append("\(noCredit) no credit") }
        if rejected > 0 { parts.append("\(rejected) rejected") }
        if failed > 0 { parts.append("\(failed) couldn't check") }
        if noCheck > 0 { parts.append("\(noCheck) no check") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
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
    var recheck: () -> Void
    var toggleReveal: () -> Void
    var copy: () -> Void
    var edit: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 12) {
            HStack(spacing: 6) {
                // A provider a harness logo stands for shows it; the rest show the heading only.
                if showsProvider, let logo = APIKeyProviderLogo.logoID(for: row.provider) {
                    HarnessIcon(harness: logo, size: 16)
                }
                Text(showsProvider ? row.provider : "").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
            }
            .frame(width: 150, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(row.provider)
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
            VStack(alignment: .leading, spacing: 3) {
                StatusLabel(status: row.status)
                CheckChip(check: check, isChecking: isChecking, recheck: recheck)
            }
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

/// What the last key check said, or a spinner while it runs. Clicking
/// asks the provider again.
private struct CheckChip: View {
    var check: APIKeyCheck?
    var isChecking: Bool
    var recheck: () -> Void

    var body: some View {
        if isChecking {
            HStack(spacing: 5) {
                ProgressView().controlSize(.mini)
                Text("Checking").foregroundStyle(Theme.textMuted)
            }
            .font(Theme.caption)
            .help("Asking the provider whether the key still works")
        } else if let check {
            Button(action: recheck) {
                HStack(spacing: 5) {
                    Circle().fill(tint(check.result)).frame(width: 6, height: 6)
                    Text(label(check.result)).foregroundStyle(Theme.textSecondary)
                }
                .font(Theme.caption)
            }
            .buttonStyle(.plain)
            .help(tooltip(check))
        }
    }

    private func tint(_ result: APIKeyCheckResult) -> Color {
        switch result {
        case .works: return Theme.success
        case .noCredit: return Theme.warning
        case .rejected: return Theme.danger
        case .couldNotCheck, .noCheck: return Theme.textMuted
        }
    }

    private func label(_ result: APIKeyCheckResult) -> String {
        switch result {
        case .works: return "Works"
        case .noCredit: return "No credit"
        case .rejected: return "Rejected"
        case .couldNotCheck: return "Couldn't check"
        case .noCheck: return "No check"
        }
    }

    private func tooltip(_ check: APIKeyCheck) -> String {
        let when = "Checked \(check.checkedAt.formatted(.relative(presentation: .named)))"
        switch check.result {
        case .works: return "The provider accepted this key. \(when)"
        case .noCredit: return "The key works but the account has no credit. \(when)"
        case .rejected(let status, let message):
            return "HTTP \(status)\(message.map { ": \($0)" } ?? "") · \(when)"
        case .couldNotCheck(let reason), .noCheck(let reason):
            return "\(reason) · \(when)"
        }
    }
}

private struct StatusLabel: View {
    var status: APIKeysModel.Status

    var body: some View {
        switch status {
        case .loaded: label("Loaded", "checkmark.circle", Theme.textSecondary, help: "A new terminal gets this value.")
        case .overridden: label("Overridden", "exclamationmark.triangle", Theme.warningText,
                                help: "A new terminal gets a different value: something later in your shell setup sets it again.")
        case .notLoaded: label("Not loaded", "minus.circle", Theme.textSecondary,
                               help: "A new terminal doesn't have this variable. Is the file sourced from your shell setup?")
        case .unknown: label("Unknown", "questionmark.circle", Theme.textMuted, help: "Quiver couldn't read your login shell.")
        case .checking: label("Checking…", "ellipsis.circle", Theme.textMuted, help: "Starting a login shell to see what a new terminal gets.")
        }
    }

    private func label(_ text: String, _ symbol: String, _ color: Color, help: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: symbol).foregroundStyle(color)
            Text(text).foregroundStyle(Theme.textPrimary)
        }
        .font(Theme.caption)
        .help(help)
    }
}

struct KeyDraft: Identifiable {
    var id: String { isNew ? "new" : name }
    var provider: String
    var name: String
    var value: String
    var isNew: Bool
}

/// Add, change or remove one key.
private struct KeyEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State var draft: KeyDraft
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
            Text(draft.isNew ? "Add a key" : "Edit \(draft.name)").font(Theme.sectionTitle).tracking(-0.5)
            field("Provider") {
                HStack(spacing: 8) {
                    // A key stays in its group; moving it isn't supported yet.
                    TextField("ANTHROPIC", text: $draft.provider).quiverField()
                        .disabled(!draft.isNew)
                    if draft.isNew && !providers.isEmpty {
                        SelectMenu(options: providers.map { ($0, $0) }, selection: $draft.provider,
                                   width: nil, title: "Existing")
                    }
                }
            }
            field("Variable") {
                TextField("ANTHROPIC_API_KEY", text: $draft.name)
                    .quiverField(monospaced: true)
                    .disabled(!draft.isNew)
                if draft.isNew, !draft.name.isEmpty, let problem = nameProblem {
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
                        Text("Remove \(draft.name)?").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        Button("Remove", role: .destructive) { run { try await remove(draft.name) } }
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
                        try await save(draft.provider.trimmingCharacters(in: .whitespaces).isEmpty ? "OTHER" : draft.provider,
                                       draft.name.trimmingCharacters(in: .whitespaces), draft.value)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .primaryButton()
                .disabled(working || (draft.isNew && nameProblem != nil))
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
