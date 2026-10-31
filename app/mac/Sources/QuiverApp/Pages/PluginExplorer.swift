import QuiverCore
import SwiftUI

/// The Plugins page's Explore tab: every plugin in every marketplace,
/// installed or not, with its state in each harness and the skills it
/// ships. Actions go through `swe plugins`, each after a confirmation.
struct PluginExplorer: View {
    @Environment(AppModel.self) private var app
    @Bindable var model: PluginCatalogModel
    var openHealth: () -> Void
    var addMarketplace: () -> Void = {}
    var confirm: (PluginAction) -> Void
    /// The plugin open in its detail page, nil while the list shows.
    @State private var opened: ExplorePlugin?
    /// A marketplace being renamed (display name only) when editable.
    @State private var renaming: String?
    @State private var renameText = ""
    @State private var renameError: String?
    private var home: URL { FileManager.default.homeDirectoryForCurrentUser }

    var body: some View {
        // Outside the switch: when the reload after a failed action also
        // fails, the reason the action failed must stay on screen.
        if let error = model.actionError ?? renameError {
            ErrorCard(message: error)
        } else if let done = model.lastResult {
            Card {
                Label(done, systemImage: "checkmark.circle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            }
        }
        switch model.state {
        case .idle, .loading:
            SkeletonRows(rows: 6, label: "Reading every marketplace…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded:
            if let opened, let index = model.index {
                // A reload swaps the index wholesale, so the opened plugin
                // is re-derived by ref — like SkillsPage follows its row —
                // or the detail keeps showing a version the catalog no
                // longer has. A ref the reload dropped takes the detail
                // back to the list rather than showing what is gone.
                if let entry = index.entry(for: opened.row.plugin.ref),
                   let market = index.catalog.marketplaces
                       .first(where: { $0.name == entry.row.plugin.marketplace }) {
                    PluginDetailView(entry: entry, market: market, home: home) { self.opened = nil }
                } else {
                    content(index)
                        .onAppear { self.opened = nil }
                }
            } else if let index = model.index {
                content(index)
            } else {
                SkeletonRows(rows: 6, label: "Reading every marketplace…")
            }
        }
    }

    @ViewBuilder private func content(_ index: PluginExploreIndex) -> some View {
        let catalog = index.catalog
        let searching = model.isSearching
        let sources = index.sources(harness: model.harness, query: model.query)
        filters(index, shown: PluginExploreIndex.distinctPlugins(in: sources))
        if !index.problems.other.isEmpty { Card { Problems(problems: index.problems.other) } }
        if let line = index.missingNote {
            // One quiet line, not a warning each: a registration synced
            // from another Mac is nothing to fix from here.
            Label(line, systemImage: "info.circle")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .hoverTip(index.problems.missing.joined(separator: ", "), title: "Registered, not on this Mac")
        }
        if index.total == 0 {
            EmptyCard(text: index.emptyText,
                      icon: "puzzlepiece.extension",
                      actionTitle: "Add marketplace", action: addMarketplace)
        } else if sources.isEmpty {
            EmptyCard(text: "No plugins match.", icon: "puzzlepiece.extension")
        } else {
            // A source is a static heading; each marketplace under it is
            // a card whose bottom row opens the plugins inside it.
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(sources) { source in
                    SourceHeader(source: source, searching: searching)
                        .padding(.top, source.id == sources.first?.id ? 0 : 32)
                    VStack(spacing: 12) {
                        ForEach(source.marketplaces) { market in
                            MarketplaceCard(market: market,
                                            name: app.primitiveNames.name(forMarketplace: market.name) ?? market.name,
                                            // Worked out with the index, off the main actor.
                                            canRename: index.isEditable(marketplace: market.name),
                                            catalog: catalog, running: model.running, busy: model.work.isBusy,
                                            open: model.isOpen(market.id), searching: searching,
                                            openHealth: openHealth, confirm: confirm,
                                            onRename: { renaming = market.name
                                                renameError = nil
                                                renameText = app.primitiveNames.name(forMarketplace: market.name) ?? market.name },
                                            openPlugin: { opened = $0 },
                                            toggle: { model.toggle(market.id) })
                        }
                    }
                    .padding(.top, 16)
                }
            }
            // A rename's failure goes once the next action has its own
            // result to show; the card above shows one or the other.
            .onChange(of: model.lastResult) { renameError = nil }
            .alert("Rename marketplace", isPresented: .init(
                get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Display name", text: $renameText)
                Button("Rename") {
                    guard let id = renaming else { return }
                    do {
                        try app.primitiveNames.saved({ $0.set(renameText, forMarketplace: id) },
                                                     to: PluginDisplayNames.file(home: home))
                        renameError = nil
                        renaming = nil
                    } catch {
                        renameError = "Couldn't save the name: \(error.localizedDescription)"
                        renaming = nil
                    }
                }
                Button("Cancel", role: .cancel) { renaming = nil }
            } message: {
                Text("Display name only: \(renaming ?? "") keeps its id.")
            }
        }
    }

    /// The tab's one filter bar: the harness select, then search taking
    /// the slack, then the count. No source select: the sources are the
    /// list's top level, and a marketplace is opened in place.
    private func filters(_ index: PluginExploreIndex, shown: Int) -> some View {
        let harnesses = index.catalog.harnesses
        return HStack(spacing: 8) {
            SelectMenu(label: "Harness",
                       options: [(String?.none, "All harnesses", nil)]
                           + harnesses.map { (String?.some($0), HarnessMark($0).name, $0) },
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

/// A source: Local, a harness, or Not registered. A static heading, never
/// a fold: the marketplaces under it are the cards that open and close.
private struct SourceHeader: View {
    var source: ExploreSource
    var searching: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            icon
            Text(source.title)
                // Body size at the section weight: above a marketplace's
                // 14/500 name, well under the page title.
                .font(Theme.font(size: 16, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
                .fixedSize()
            Text(source.note)
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
            Spacer(minLength: 8)
            Text(source.countLine(searching: searching))
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .monospacedDigit()
                .lineLimit(1)
                .fixedSize()
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    /// Local gets a folder, a harness its logo (the same id and title
    /// Settings' provider list pairs up), the unregistered a question mark.
    @ViewBuilder private var icon: some View {
        switch source.kind {
        case .local:
            Image(systemName: "folder")
                .font(Theme.font(size: 14, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
        case .harness(let harness):
            HarnessIcon(harness: harness, size: 16)
        case .unregistered:
            Image(systemName: "questionmark.circle")
                .font(Theme.font(size: 14, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
        }
    }
}

/// A marketplace under its source: a card in the service status style.
/// Folded it is the header row alone (name, source tag, a lock note for a
/// GitHub source, and the plugin and installed counts); the counts are the
/// fold button, opening the plugin rows inside the card, a hairline
/// between each. One line said the installed count twice, and a separate
/// fold row left an empty band under every header.
private struct MarketplaceCard: View {
    var market: ExploreMarketplace
    /// The display name: Quiver's overlay name or the marketplace id.
    var name: String
    /// A directory-sourced marketplace under ~/.quiver/plugins can rename.
    var canRename: Bool
    var catalog: PluginCatalog
    var running: PluginAction?
    /// An action or a Health fix is running: one change at a time.
    var busy: Bool
    var open: Bool
    var searching: Bool
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void
    var onRename: () -> Void
    /// The name block opens the plugin's detail page.
    var openPlugin: (ExplorePlugin) -> Void
    var toggle: () -> Void
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, Theme.cardPadding)
                .padding(.vertical, 14)
            if open, !market.plugins.isEmpty {
                // Lazy so a big marketplace does not build every row at once.
                LazyVStack(spacing: 0) {
                    Rectangle().fill(Theme.border).frame(height: Theme.hairline)
                        .padding(.horizontal, Theme.cardPadding)
                    ForEach(market.plugins) { entry in
                        PluginRow(entry: entry, catalog: catalog, running: running, busy: busy,
                                  openHealth: openHealth, confirm: confirm, open: { openPlugin(entry) })
                            .padding(.horizontal, Theme.cardPadding)
                        if entry.id != market.plugins.last?.id {
                            Rectangle().fill(Theme.border).frame(height: Theme.hairline)
                                .padding(.horizontal, Theme.cardPadding)
                        }
                    }
                }
                .padding(.bottom, 8)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(hovering ? Theme.cardHover : Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius)
            .strokeBorder(hovering ? Theme.borderStrong : Theme.border, lineWidth: 1))
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(name)
                .font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
            if canRename, hovering {
                Button("Rename", systemImage: "pencil", action: onRename)
                    .labelStyle(.iconOnly)
                    .font(Theme.font(size: 10))
                    .foregroundStyle(Theme.textSecondary)
                    .buttonStyle(.plain)
                    .help("Rename \(name)")
            }
            if name != market.name {
                Text(market.name).font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textSecondary).lineLimit(1)
            }
            if let tag = market.tag { SourceTag(text: tag) }
            if market.tag == "github" {
                Image(systemName: "lock.fill")
                    .font(Theme.font(size: 10))
                    .foregroundStyle(Theme.textMuted)
                    .hoverTip("Updates from GitHub would undo edits, so this marketplace is read-only.")
            }
            Spacer(minLength: 8)
            if market.plugins.isEmpty {
                count
            } else {
                Button {
                    withAnimation(.easeOut(duration: Theme.fadeDuration)) { toggle() }
                } label: {
                    HStack(spacing: 6) {
                        count
                        Image(systemName: open ? "chevron.up" : "chevron.down")
                            .font(Theme.font(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .contentShape(.rect)
                }
                .plainButton()
                .help(open ? "Show only the header" : "Show this marketplace's plugins")
                .accessibilityLabel(open ? "Hide plugins" : "Show plugins")
                .accessibilityValue(market.countLine(searching: searching))
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isHeader)
        .contextMenu {
            if canRename { Button("Rename…", action: onRename) }
        }
    }

    /// "2 plugins · 2 installed", or "4 matches" while searching.
    private var count: some View {
        Text(market.countLine(searching: searching))
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .monospacedDigit()
            .lineLimit(1)
            .fixedSize()
    }
}

/// Where a marketplace comes from (local, github, git, cache): quieter
/// than a skill badge, a hairline and secondary text.
private struct SourceTag: View {
    var text: String

    var body: some View {
        Text(text)
            .font(Theme.font(size: 11, weight: .medium))
            .foregroundStyle(Theme.textSecondary)
            .lineLimit(1)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
            .fixedSize()
    }
}

private struct PluginRow: View {
    @Environment(AppModel.self) private var app
    var entry: ExplorePlugin
    var catalog: PluginCatalog
    var running: PluginAction?
    /// An action or a Health fix is running: one change at a time.
    var busy: Bool
    var openHealth: () -> Void
    var confirm: (PluginAction) -> Void
    /// The name block opens the plugin's detail page.
    var open: () -> Void

    private static let shownSkills = 8

    var body: some View {
        let row = entry.row
        let plugin = row.plugin
        let shown = app.primitiveNames.name(forPlugin: plugin.ref) ?? plugin.displayName ?? plugin.name
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Button(action: open) {
                        Text(shown).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                    }
                    .plainButton()
                    .help("Open \(shown)")
                    // The marketplace is the header above; the version is all that is left.
                    if let version = plugin.version {
                        Text("v\(version)").font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
                    }
                    if let drift = plugin.worstDrift {
                        DriftBadge(state: drift.state, harnesses: drift.harnesses, action: openHealth)
                    }
                }
                if shown != plugin.name {
                    Text(plugin.ref).font(Theme.font(size: 12, design: .monospaced)).foregroundStyle(Theme.textMuted).lineLimit(1)
                }
                if let description = plugin.description, !description.isEmpty {
                    Text(description)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                        .hoverTip(description)
                }
                if !row.skills.isEmpty {
                    FlowLayout(spacing: 4, lineSpacing: 4) {
                        ForEach(row.skills.prefix(Self.shownSkills)) { skill in
                            Badge(skill.name)
                                .hoverTip(skill.description.isEmpty ? "No description." : skill.description, title: skill.name)
                        }
                        if row.skills.count > Self.shownSkills {
                            let rest = row.skills.dropFirst(Self.shownSkills)
                            Badge("+\(rest.count) more")
                                .hoverTip(rest.map(\.name).joined(separator: ", "), title: "\(rest.count) more skills")
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
            .font(Theme.font(size: 12, weight: .medium))
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
        .hoverTip("\(harnessNames(harnesses)): \(state.title.lowercased()). Open Health to fix it.", replacesLabel: false)
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
            label.hoverTip(help, replacesLabel: false)
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
                Image(systemName: "chevron.down").font(Theme.font(size: 9, weight: .semibold))
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .font(Theme.font(size: 11, weight: .medium))
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
        case .enabled: Circle().fill(Theme.accentFill).frame(width: Theme.statusDot, height: Theme.statusDot)
        case .installed: Circle().fill(Theme.textSecondary).frame(width: Theme.statusDot, height: Theme.statusDot)
        case .disabled, .cached: Circle().fill(Theme.borderStrong).frame(width: Theme.statusDot, height: Theme.statusDot)
        case .available, .absent: Circle().strokeBorder(Theme.textMuted, lineWidth: 1.2).frame(width: Theme.statusDot, height: Theme.statusDot)
        }
    }
}
