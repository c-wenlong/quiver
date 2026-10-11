import QuiverCore
import SwiftUI

/// Every AI coding CLI swe knows about. Starring and archiving go through
/// `swe harness`, which owns harness.json.
struct HarnessesPage: View {
    @Environment(AppModel.self) private var app
    @State private var model: HarnessesModel?

    var body: some View {
        Page(title: "Harnesses",
             subtitle: "The AI coding CLIs swe knows about. Star the ones you use; archive the ones you've ruled out.") {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model?.load() } }
                .iconButton()
                .help("Refresh")
        } content: {
            if let model { HarnessList(model: model) }
        }
        .task(id: app.swePath) {
            let harnesses = HarnessesModel(swe: app.swe)
            model = harnesses
            await harnesses.load()
        }
    }
}

private struct HarnessList: View {
    @Bindable var model: HarnessesModel

    var body: some View {
        switch model.state {
        case .idle, .loading:
            ProgressView().frame(maxWidth: .infinity)
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let all):
            let rows = model.visible
            // The shared filter bar: selects, then search taking the slack, then the count.
            HStack(spacing: 10) {
                SelectMenu(options: [(HarnessesModel.Scope.inUse, "In use (\(model.count(.inUse)))"),
                                     (.archived, "Archived (\(model.count(.archived)))")],
                           selection: $model.scope)
                SearchField(prompt: "Filter harnesses", text: $model.query)
                    .frame(maxWidth: .infinity)
                Text(rows.count == 1 ? "1 harness" : "\(rows.count) harnesses")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                    .fixedSize()
            }
            if let error = model.actionError { ErrorCard(message: error) }
            if rows.isEmpty {
                Card {
                    Text(emptyText(all: all))
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
            } else {
                let shown = model.shown
                VStack(spacing: 0) {
                    ForEach(shown) { harness in
                        HarnessRow(harness: harness, model: model)
                        if harness.id != shown.last?.id { Divider().overlay(Theme.border) }
                    }
                    if model.canCollapse {
                        if !shown.isEmpty { Divider().overlay(Theme.border) }
                        ExpandRow(isExpanded: model.showsUnstarred, hidden: model.collapsedCount) {
                            withAnimation(.easeOut(duration: 0.15)) { model.showsUnstarred.toggle() }
                        }
                    }
                }
                .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
            }
        }
    }

    private func emptyText(all: [Harness]) -> String {
        if all.isEmpty { return "No harnesses registered yet. Run swe discover --apply to register what is installed." }
        if !model.query.isEmpty { return "No harnesses match." }
        return model.scope == .archived ? "Nothing archived." : "Every harness is archived."
    }
}

/// The last row of the list: unfolds the unstarred harnesses, or folds
/// them away again. It is the card's bottom edge, so its hover fill and its
/// focus ring follow the card's bottom corners and stay inside the card.
private struct ExpandRow: View {
    var isExpanded: Bool
    var hidden: Int
    var action: () -> Void
    @State private var hovering = false
    @FocusState private var focused: Bool

    private var shape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(bottomLeadingRadius: Theme.cardRadius, bottomTrailingRadius: Theme.cardRadius)
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(.system(size: 12, weight: .semibold))
                Text(isExpanded ? "Show less" : hidden == 1 ? "Show 1 more" : "Show \(hidden) more")
                    .font(Theme.control)
            }
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(maxWidth: .infinity)
            .frame(height: 36)
            .background(hovering ? Theme.rowHover : .clear, in: shape)
            .contentShape(shape)
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: 0))
        .focused($focused)
        .focusEffectDisabled()
        .overlay(
            shape.inset(by: 2)
                .stroke(Theme.focus, lineWidth: Theme.focusRing)
                .opacity(focused && FocusVisibility.shared.fromKeyboard ? 1 : 0)
                .allowsHitTesting(false))
        .onHover { hovering = $0 }
        .help(isExpanded ? "Hide the harnesses you haven't starred" : "Show the harnesses you haven't starred")
    }
}

private struct HarnessRow: View {
    var harness: Harness
    var model: HarnessesModel
    @State private var archiving = false
    @State private var reason = ""
    @State private var hovering = false
    @State private var editing = false

    private var busy: Bool { model.busy.contains(harness.name) }

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    HarnessIcon(harness: harness.name, size: 16)
                        .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
                    Text(harness.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    if let version = harness.version {
                        Text(version).font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                            .lineLimit(1).truncationMode(.middle)
                    }
                    // The name is for people; the command is what runs.
                    Text(harness.command)
                        .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).truncationMode(.middle)
                    ForEach(harness.aliases, id: \.self) { Badge($0) }
                }
                if !harness.description.isEmpty {
                    Text(harness.description)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let archived = harness.archived {
                    Text(archivedLine(archived))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                if !harness.tags.isEmpty {
                    Text(harness.tags.joined(separator: " · "))
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            Spacer(minLength: 12)
            VStack(alignment: .trailing, spacing: 6) {
                if let installed = harness.installed {
                    Badge(installed ? "Installed" : "Not on PATH")
                }
                if let sessions = harness.sessions100d {
                    Text(sessions == 1 ? "1 session in 100 days" : "\(sessions) sessions in 100 days")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                }
            }
            actions
        }
        .padding(.horizontal, Theme.cardPadding)
        .padding(.vertical, 12)
        .background(hovering ? Theme.rowHover : .clear)
        // A click anywhere but the buttons opens the editor.
        .contentShape(.rect)
        .onTapGesture { editing = true }
        .onHover { hovering = $0 }
        .accessibilityAction(named: "Edit") { editing = true }
        .help("Click to edit \(harness.title)")
        .sheet(isPresented: $editing) { HarnessEditor(harness: harness, model: model) }
        .opacity(busy ? 0.6 : 1)
        .disabled(busy)
    }

    @ViewBuilder private var actions: some View {
        if harness.state == .archived {
            Button("Restore") { Task { await model.restore(harness) } }
                .secondaryButton()
        } else {
            // Archive on top, star under it, one column of icon buttons.
            VStack(spacing: 6) {
                archiveButton
                Button(harness.state == .starred ? "Unstar" : "Star",
                       systemImage: harness.state == .starred ? "star.fill" : "star") {
                    Task { await model.toggleStar(harness) }
                }
                .iconButton()
                .help(harness.state == .starred ? "Unstar: stop pinning it to the top" : "Star: pin it to the top and track its limits")
            }
        }
    }

    private var archiveButton: some View {
        Button("Archive", systemImage: "archivebox") { archiving = true }
            .iconButton()
            .help("Archive: hide it from swe list and record why")
            .popover(isPresented: $archiving, arrowEdge: .bottom) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        if HarnessIcon.exists(for: harness.name) { HarnessIcon(harness: harness.name, size: 16) }
                        Text("Archive \(harness.title)").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    }
                    Text("It drops out of swe list. Nothing is uninstalled, and the reason is kept so you remember why.")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    TextField("Why? Like: too slow, or no MCP support", text: $reason)
                        .quiverField()
                        .onSubmit(archive)
                    HStack {
                        Spacer()
                        Button("Cancel") { archiving = false }
                            .secondaryButton()
                            .keyboardShortcut(.cancelAction)
                        Button("Archive", action: archive)
                            .primaryButton()
                            .disabled(reason.trimmingCharacters(in: .whitespaces).isEmpty)
                            .keyboardShortcut(.defaultAction)
                    }
                }
                .padding(16)
                .frame(width: 340)
            }
    }

    private func archive() {
        let why = reason.trimmingCharacters(in: .whitespaces)
        guard !why.isEmpty else { return }
        archiving = false
        reason = ""
        Task { await model.archive(harness, reason: why) }
    }

    private func archivedLine(_ archived: Harness.Archive) -> String {
        var parts: [String] = []
        if let date = Self.parse(archived.archivedAt) {
            parts.append("Archived \(date.formatted(date: .abbreviated, time: .omitted))")
        } else {
            parts.append("Archived")
        }
        if archived.usage != "unknown" { parts.append("usage: \(archived.usage)") }
        let reason = archived.reason.isEmpty ? "no reason given" : archived.reason
        return parts.joined(separator: ", ") + ". " + reason.prefix(1).uppercased() + reason.dropFirst()
    }

    private static func parse(_ text: String) -> Date? {
        let full = ISO8601DateFormatter()
        if let date = full.date(from: text) { return date }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd"] {
            local.dateFormat = format
            if let date = local.date(from: text) { return date }
        }
        return nil
    }
}

/// Every registry field swe keeps for a harness, edited through `swe edit`.
private struct HarnessEditor: View {
    @Environment(\.dismiss) private var dismiss
    var harness: Harness
    var model: HarnessesModel
    @State private var draft: HarnessEdit
    @State private var error: String?
    @State private var working = false

    init(harness: Harness, model: HarnessesModel) {
        self.harness = harness
        self.model = model
        _draft = State(initialValue: HarnessEdit(harness))
    }

    private var unchanged: Bool { draft.flags(from: HarnessEdit(harness)).isEmpty }

    /// State, PATH and usage: what swe knows but the sheet doesn't edit.
    private var facts: String {
        let state = switch harness.state {
        case .starred: "Starred"
        case .active: "Not starred"
        case .archived: "Archived"
        }
        var parts = [state]
        if let installed = harness.installed { parts.append(installed ? "Installed" : "Not on PATH") }
        if let sessions = harness.sessions100d {
            parts.append(sessions == 1 ? "1 session in 100 days" : "\(sessions) sessions in 100 days")
        }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 10) {
                    HarnessIcon(harness: harness.name, size: 20)
                    Text("Edit \(harness.title)").font(Theme.sectionTitle).tracking(-0.5)
                        .foregroundStyle(Theme.textPrimary)
                }
                Text(facts).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            HStack(alignment: .top, spacing: 12) {
                field("Name", hint: "Shown across Quiver. Empty uses \(UsageFormat.harness(harness.name)).") {
                    TextField(UsageFormat.harness(harness.name), text: $draft.displayName).quiverField()
                }
                field("Command", hint: draft.commandProblem ?? "What swe runs to start it.", problem: draft.commandProblem != nil) {
                    TextField(harness.name, text: $draft.command).quiverField(monospaced: true)
                }
            }
            field("Description") {
                TextField("What it's good for", text: $draft.description, axis: .vertical)
                    .lineLimit(1...4)
                    .quiverField(multiline: true)
            }
            HStack(alignment: .top, spacing: 12) {
                field("Version", hint: "Leave empty to clear it.") {
                    TextField("1.0.0", text: $draft.version).quiverField(monospaced: true)
                }
                field("Aliases", hint: "Comma-separated short names.") {
                    TextField("cc, claude-code", text: $draft.aliases).quiverField(monospaced: true)
                }
            }
            field("Tags", hint: "Comma-separated, like agentic, coding, byok.") {
                TextField("agentic, coding", text: $draft.tags).quiverField()
            }
            field("Notes") {
                TextField("Anything worth remembering about it", text: $draft.notes, axis: .vertical)
                    .lineLimit(2...6)
                    .quiverField(multiline: true)
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).secondaryButton()
                Button(working ? "Saving…" : "Save", action: save)
                    .keyboardShortcut(.defaultAction)
                    .primaryButton()
                    .disabled(working || unchanged || draft.commandProblem != nil)
            }
        }
        .padding(24)
        .frame(width: 520)
        .background(Theme.panel)
    }

    private func field<Content: View>(_ label: String, hint: String? = nil, problem: Bool = false,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Theme.control).foregroundStyle(Theme.textSecondary)
            content()
            if let hint {
                Text(hint).font(Theme.caption).foregroundStyle(problem ? Theme.dangerText : Theme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func save() {
        working = true
        error = nil
        Task {
            let failure = await model.save(harness, draft)
            working = false
            if let failure { error = failure } else { dismiss() }
        }
    }
}
