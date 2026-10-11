import QuiverCore
import SwiftUI

/// The Plugins page's Explore tab: every plugin in every marketplace,
/// installed or not, with its state in each harness and the skills it
/// ships. Actions go through `swe plugins`, each after a confirmation.
struct PluginExplorer: View {
    @Bindable var model: PluginCatalogModel
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void

    var body: some View {
        // Outside the switch: when the reload after a failed action also
        // fails, the reason the action failed must stay on screen.
        if let error = model.actionError {
            ErrorCard(message: error)
        } else if let done = model.lastResult {
            Card {
                Label(done, systemImage: "checkmark.circle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            }
        }
        switch model.state {
        case .idle, .loading:
            LoadingCard(text: "Reading every marketplace…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded:
            if let index = model.index {
                content(index)
            } else {
                LoadingCard(text: "Reading every marketplace…")
            }
        }
    }

    @ViewBuilder private func content(_ index: PluginExploreIndex) -> some View {
        let catalog = index.catalog
        let groups = index.groups(source: model.source, harness: model.harness, query: model.query)
        filters(index, shown: groups.reduce(0) { $0 + $1.rows.count })
        if !catalog.errors.isEmpty { Problems(problems: catalog.errors) }
        if index.total == 0 {
            EmptyCard(text: "No plugin marketplaces registered in Claude Code, Codex or Cursor.",
                      icon: "puzzlepiece.extension")
        } else if groups.isEmpty {
            EmptyCard(text: "No plugins match.", icon: "puzzlepiece.extension")
        } else {
            // 372 plugins on one machine: a lazy stack builds only the rows
            // on screen. Straight on the panel, hairlines between rows.
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(groups) { group in
                    GroupHeader(group: group)
                        .padding(.top, group.kind == groups.first?.kind ? 0 : 28)
                    ForEach(group.rows) { row in
                        PluginRow(row: row, catalog: catalog, running: model.running,
                                  openHealth: openHealth, confirm: confirm)
                        if row.id != group.rows.last?.id { Rectangle().fill(Theme.border).frame(height: 1) }
                    }
                }
            }
        }
    }

    /// The tab's one filter bar: selects, then search taking the slack,
    /// then the count.
    private func filters(_ index: PluginExploreIndex, shown: Int) -> some View {
        let sources: [(ExploreSource, String)] = [(.all, "All sources"), (.hub, "Quiver hub")]
            + index.otherMarketplaces.map { (.marketplace($0), $0) }
        let harnesses = index.catalog.harnesses
        return HStack(spacing: 8) {
            SelectMenu(label: "Source", options: sources, selection: $model.source, title: sourceTitle)
            SelectMenu(label: "Harness",
                       options: [(String?.none, "All harnesses")] + harnesses.map { (String?.some($0), HarnessMark($0).name) },
                       selection: $model.harness,
                       title: model.harness.map { HarnessMark($0).name } ?? "All")
            SearchField(prompt: "Search plugins and their skills", text: $model.query)
                .frame(maxWidth: .infinity)
            Text("\(shown) of \(index.total) plugins")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
        }
    }

    private var sourceTitle: String {
        switch model.source {
        case .all: "All"
        case .hub: "Quiver hub"
        case .marketplace(let name): name
        }
    }
}

/// What swe could not read, one line each.
private struct Problems: View {
    var problems: [PluginCatalog.Problem]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(problems.enumerated()), id: \.offset) { _, problem in
                Label(line(problem), systemImage: "exclamationmark.triangle")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func line(_ problem: PluginCatalog.Problem) -> String {
        let where_ = [HarnessMark(problem.harness).name, problem.marketplace].compactMap { $0 }.joined(separator: ", ")
        return "\(where_): \(problem.detail)"
    }
}

private struct GroupHeader: View {
    var group: ExploreGroup

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(group.kind.title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Text(group.rows.count == 1 ? "1 plugin" : "\(group.rows.count) plugins")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
            }
            if let note = group.kind.note {
                Text(note)
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.bottom, 4)
    }
}

private struct PluginRow: View {
    var row: ExploreRow
    var catalog: PluginCatalog
    var running: PluginAction?
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void

    private static let shownSkills = 8

    var body: some View {
        let plugin = row.plugin
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(plugin.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    if let drift = plugin.worstDrift {
                        DriftBadge(state: drift.state, harnesses: drift.harnesses, action: openHealth)
                    }
                }
                if let description = plugin.description, !description.isEmpty {
                    Text(description)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                        .help(description)
                }
                if !row.skills.isEmpty {
                    FlowLayout(spacing: 4, lineSpacing: 4) {
                        ForEach(row.skills.prefix(Self.shownSkills)) { skill in
                            Badge(skill.name).help(skill.description)
                        }
                        if row.skills.count > Self.shownSkills {
                            let rest = row.skills.dropFirst(Self.shownSkills)
                            Badge("+\(rest.count) more").help(rest.map(\.name).joined(separator: ", "))
                        }
                    }
                    .padding(.top, 2)
                }
            }
            Spacer(minLength: 8)
            HStack(spacing: 6) {
                ForEach(plugin.shownHarnesses(in: catalog.harnesses), id: \.self) { harness in
                    if let state = plugin.harnesses[harness] {
                        HarnessChip(harness: harness, state: state,
                                    actions: catalog.actions(for: plugin, in: harness),
                                    running: running?.ref == plugin.ref && running?.harness == harness,
                                    busy: running != nil, confirm: confirm)
                    }
                }
            }
        }
        .padding(.vertical, 12)
    }

    private var subtitle: String {
        [row.plugin.marketplace, row.plugin.version.map { "v\($0)" }].compactMap { $0 }.joined(separator: " · ")
    }
}

/// A hub plugin the Health tab has a finding for. A click opens Health,
/// which has the fix; this tab never runs one itself.
private struct DriftBadge: View {
    var state: PluginDriftState.Kind
    var harnesses: [String]
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Circle().fill(state == .unregistered ? Theme.danger : Theme.warning).frame(width: 6, height: 6)
                Text(state.title)
            }
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(Theme.textLabel)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
            .background(Theme.subtle, in: .capsule)
            .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
            .contentShape(.capsule)
        }
        .plainButton(cornerRadius: 999)
        .fixedSize()
        .help("\(harnessNames(harnesses)): \(state.title.lowercased()). Open Health to fix it.")
    }
}

/// One harness's state for one plugin. A menu of its actions where swe can
/// act in that harness; plain text where it cannot (Cursor).
private struct HarnessChip: View {
    var harness: String
    var state: PluginHarnessState
    var actions: [PluginAction]
    var running: Bool
    var busy: Bool
    var confirm: (PluginAction) -> Void
    @State private var hovering = false

    var body: some View {
        if actions.isEmpty {
            label.help(help)
        } else {
            Menu {
                ForEach(actions) { action in
                    Button(action.menuTitle) { confirm(action) }
                }
            } label: {
                label
            }
            .menuStyle(.button)
            .menuIndicator(.hidden)
            .plainButton(cornerRadius: 999)
            .fixedSize()
            .onHover { hovering = $0 }
            .disabled(busy)
            .help(help)
        }
    }

    private var label: some View {
        let status = state.status
        return HStack(spacing: 6) {
            if running {
                ProgressView().controlSize(.mini)
            } else {
                StatusDot(status: status)
            }
            HarnessMarkView(harness: harness, size: 12)
            Text(status.title)
            if !actions.isEmpty {
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .font(.system(size: 11, weight: .medium))
        .foregroundStyle(status == .enabled || status == .installed ? Theme.textPrimary : Theme.textSecondary)
        .lineLimit(1)
        .padding(.horizontal, 8)
        .frame(height: 24)
        .background(hovering && !busy ? Theme.panelHover : .clear, in: .capsule)
        .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
        .contentShape(.capsule)
    }

    private var help: String {
        var parts = ["\(HarnessMark(harness).name): \(state.status.title.lowercased())"]
        if let version = state.version { parts.append("version \(version)") }
        if !state.scopes.isEmpty { parts.append("scope \(state.scopes.joined(separator: ", "))") }
        if actions.isEmpty && state.installed == nil { parts.append("read only") }
        return parts.joined(separator: " · ")
    }
}

/// Green only when on: enabled. Installed with no on or off known is
/// solid grey, disabled and cached a lighter grey, available a ring.
private struct StatusDot: View {
    var status: PluginHarnessState.Status

    var body: some View {
        switch status {
        case .enabled: Circle().fill(Theme.accentFill).frame(width: 7, height: 7)
        case .installed: Circle().fill(Theme.textSecondary).frame(width: 7, height: 7)
        case .disabled, .cached: Circle().fill(Theme.borderStrong).frame(width: 7, height: 7)
        case .available, .absent: Circle().strokeBorder(Theme.textMuted, lineWidth: 1.2).frame(width: 7, height: 7)
        }
    }
}
