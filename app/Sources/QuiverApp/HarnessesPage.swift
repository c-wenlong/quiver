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
                .buttonStyle(IconButtonStyle())
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
            HStack(spacing: 8) {
                Tabs(options: [(HarnessesModel.Scope.inUse, "In use \(model.count(.inUse))"),
                               (.archived, "Archived \(model.count(.archived))")],
                     selection: $model.scope)
                SearchField(prompt: "Filter harnesses", text: $model.query)
                    .frame(maxWidth: 320)
                Spacer(minLength: 0)
            }
            if let error = model.actionError { ErrorCard(message: error) }
            let rows = model.visible
            if rows.isEmpty {
                Card {
                    Text(emptyText(all: all))
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                }
            } else {
                VStack(spacing: 0) {
                    ForEach(rows) { harness in
                        HarnessRow(harness: harness, model: model)
                        if harness.id != rows.last?.id { Divider().overlay(Theme.border) }
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

private struct HarnessRow: View {
    var harness: Harness
    var model: HarnessesModel
    @State private var archiving = false
    @State private var reason = ""
    @State private var hovering = false

    private var busy: Bool { model.busy.contains(harness.name) }

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            if harness.state != .archived {
                Button(harness.state == .starred ? "Unstar" : "Star",
                       systemImage: harness.state == .starred ? "star.fill" : "star") {
                    Task { await model.toggleStar(harness) }
                }
                .labelStyle(.iconOnly)
                .font(.system(size: 14))
                .foregroundStyle(harness.state == .starred ? Theme.textPrimary : Theme.textMuted)
                .frame(width: 24, height: 24)
                .contentShape(.rect)
                .buttonStyle(RingedPlainStyle(cornerRadius: 6))
                .help(harness.state == .starred ? "Unstar: stop pinning it to the top" : "Star: pin it to the top and track its limits")
                .padding(.top, -2)
            }
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(harness.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    if let version = harness.version {
                        Text(version).font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                    }
                    if harness.command != harness.name {
                        Text(harness.command)
                            .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary)
                    }
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
                    HStack(spacing: 4) {
                        ForEach(harness.tags, id: \.self) { tag in
                            Text(tag).font(Theme.caption).foregroundStyle(Theme.textMuted)
                        }
                    }
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
        .padding(.vertical, 16)
        .background(hovering ? Theme.rowHover : .clear)
        .onHover { hovering = $0 }
        .opacity(busy ? 0.6 : 1)
        .disabled(busy)
    }

    @ViewBuilder private var actions: some View {
        if harness.state == .archived {
            Button("Restore") { Task { await model.restore(harness) } }
                .buttonStyle(SecondaryButtonStyle())
        } else {
            Button("Archive", systemImage: "archivebox") { archiving = true }
                .buttonStyle(IconButtonStyle())
                .help("Archive: hide it from swe list and record why")
                .popover(isPresented: $archiving, arrowEdge: .bottom) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Archive \(harness.name)").font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                        Text("It drops out of swe list. Nothing is uninstalled, and the reason is kept so you remember why.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        TextField("Why? Like: too slow, or no MCP support", text: $reason)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit(archive)
                        HStack {
                            Spacer()
                            Button("Cancel") { archiving = false }
                                .buttonStyle(SecondaryButtonStyle())
                                .keyboardShortcut(.cancelAction)
                            Button("Archive", action: archive)
                                .buttonStyle(PrimaryButtonStyle())
                                .disabled(reason.trimmingCharacters(in: .whitespaces).isEmpty)
                                .keyboardShortcut(.defaultAction)
                        }
                    }
                    .padding(16)
                    .frame(width: 340)
                }
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
