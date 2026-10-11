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
        let searching = model.isSearching
        let sources = index.sources(harness: model.harness, query: model.query)
        filters(index, shown: PluginExploreIndex.distinctPlugins(in: sources))
        if !catalog.errors.isEmpty { Problems(problems: catalog.errors) }
        if index.total == 0 {
            EmptyCard(text: "No plugin marketplaces registered in Claude Code, Codex or Cursor.",
                      icon: "puzzlepiece.extension")
        } else if sources.isEmpty {
            EmptyCard(text: "No plugins match.", icon: "puzzlepiece.extension")
        } else {
            // Source, then marketplace, then plugin, flattened into one lazy
            // stack: it builds only the lines on screen, and a closed
            // marketplace's plugins are not lines at all. Straight on the
            // panel, hairlines between plugin rows.
            let lines = ExploreLine.lines(sources, disclosure: model.disclosure, searching: searching)
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(lines) { line in
                    switch line {
                    case .source(let source, let open):
                        SourceHeader(source: source, open: open, searching: searching) { model.toggle(source.id) }
                            .padding(.top, source.id == sources.first?.id ? 0 : 16)
                    case .marketplace(let market, let open):
                        MarketplaceHeader(market: market, open: open, searching: searching) { model.toggle(market.id) }
                            .padding(.leading, Self.marketIndent)
                    case .plugin(let entry, let last):
                        VStack(spacing: 0) {
                            PluginRow(entry: entry, catalog: catalog, running: model.running, busy: model.work.isBusy,
                                      openHealth: openHealth, confirm: confirm)
                            if !last { Rectangle().fill(Theme.border).frame(height: 1) }
                        }
                        .padding(.leading, Self.pluginIndent)
                        .padding(.bottom, last ? 8 : 0)
                    }
                }
            }
        }
    }

    /// A marketplace's chevron sits under its source's title; a plugin's
    /// name under its marketplace's.
    private static let marketIndent: CGFloat = 20
    private static let pluginIndent: CGFloat = 40

    /// The tab's one filter bar: the harness select, then search taking
    /// the slack, then the count. No source select: the sources are the
    /// list's top level, and a marketplace is opened in place.
    private func filters(_ index: PluginExploreIndex, shown: Int) -> some View {
        let harnesses = index.catalog.harnesses
        return HStack(spacing: 8) {
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

/// A source: Local, a harness, or Not registered. The whole line is the
/// button that opens or closes it.
private struct SourceHeader: View {
    var source: ExploreSource
    var open: Bool
    var searching: Bool
    var toggle: () -> Void

    var body: some View {
        let count = source.countLine(searching: searching)
        DisclosureHeader(open: open, verticalPadding: 10, label: "\(source.title), \(source.note), \(count)",
                         toggle: toggle) {
            Text(source.title)
                // Body size at the section weight: above a marketplace's
                // 14/500 name, well under the page title.
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
                .fixedSize()
            Text(source.note)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
            Spacer(minLength: 8)
            Text(count)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
        }
    }
}

/// A marketplace under its source: its name, where it comes from, and
/// how many of its plugins the source's harness has installed.
private struct MarketplaceHeader: View {
    var market: ExploreMarketplace
    var open: Bool
    var searching: Bool
    var toggle: () -> Void

    var body: some View {
        let count = market.countLine(searching: searching)
        DisclosureHeader(open: open, verticalPadding: 8,
                         label: [market.name, market.tag, count].compactMap { $0 }.joined(separator: ", "),
                         toggle: toggle) {
            Text(market.name)
                .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
            if let tag = market.tag { SourceTag(text: tag) }
            Spacer(minLength: 8)
            Text(count)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
        }
    }
}

/// A full-width button that opens or closes what is under it: a chevron
/// that turns down when open, then the caller's line. VoiceOver reads it
/// as a header with an expanded or collapsed value; the focus ring is the
/// shared one.
private struct DisclosureHeader<Content: View>: View {
    var open: Bool
    var verticalPadding: CGFloat
    var label: String
    var toggle: () -> Void
    @ViewBuilder var content: Content
    @State private var hovering = false

    var body: some View {
        Button(action: toggle) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(hovering ? Theme.textPrimary : Theme.textMuted)
                    .rotationEffect(.degrees(open ? 90 : 0))
                    .animation(.easeOut(duration: 0.15), value: open)
                    .frame(width: 12)
                content
            }
            .padding(.vertical, verticalPadding)
            // The fill reaches past the text, which stays in line with the
            // rows; the stack's own edge is outside the page's padding.
            .padding(.horizontal, 8)
            .background(hovering ? Theme.panelHover : .clear, in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .padding(.horizontal, -8)
        .onHover { hovering = $0 }
        .accessibilityLabel(label)
        .accessibilityValue(open ? "Expanded" : "Collapsed")
        .accessibilityAddTraits(.isHeader)
        .help(open ? "Collapse" : "Expand")
    }
}

/// Where a marketplace comes from (local, github, git, cache): quieter
/// than a skill badge, a hairline and secondary text.
private struct SourceTag: View {
    var text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(Theme.textSecondary)
            .lineLimit(1)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
            .fixedSize()
    }
}

private struct PluginRow: View {
    var entry: ExplorePlugin
    var catalog: PluginCatalog
    var running: PluginAction?
    /// An action or a Health fix is running: one change at a time.
    var busy: Bool
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void

    private static let shownSkills = 8

    var body: some View {
        let row = entry.row
        let plugin = row.plugin
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(plugin.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    // The marketplace is the header above; the version is all that is left.
                    if let version = plugin.version {
                        Text("v\(version)").font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    }
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
                // Only the source's own harness, both of the hub's, or
                // wherever an unregistered plugin is installed.
                ForEach(entry.harnesses, id: \.self) { harness in
                    if let state = plugin.harnesses[harness] {
                        HarnessChip(harness: harness, state: state,
                                    actions: catalog.actions(for: plugin, in: harness),
                                    running: running?.ref == plugin.ref && running?.harness == harness,
                                    busy: busy, confirm: confirm)
                    }
                }
            }
        }
        .padding(.vertical, 12)
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
