import AppKit
import QuiverCore
import SwiftUI

/// The Plugins page's two views.
enum PluginsTab: Hashable {
    /// Every plugin in every marketplace, with install, uninstall, enable
    /// and disable per harness.
    case explore
    /// Where the copies Claude Code and Codex run no longer match
    /// `~/.quiver/plugins`, each with the fix swe would run.
    case health
}

struct PluginsPage: View {
    @Environment(AppModel.self) private var app
    @State private var confirming: PluginDrift?
    /// What the Fix all confirmation listed; only these run.
    @State private var confirmingAll: [String]?
    @State private var confirmingAction: PluginAction?
    /// Drift rows a harness card shows before folding.
    private static let foldLimit = 8
    /// Harnesses whose drift cards are unfolded.
    @State private var opened: Set<String> = []
    /// The Add marketplace sheet's visibility.
    @State private var addingMarketplace = false

    /// App-wide, so a check outlives a trip to another page and the last
    /// report shows at once.
    private var model: PluginsModel { app.plugins }
    private var catalog: PluginCatalogModel { app.pluginCatalog }

    var body: some View {
        @Bindable var app = app
        Page(title: "Plugins",
             subtitle: "Every plugin in every marketplace, and where Claude Code and Codex no longer match ~/.quiver/plugins.") {
            switch app.pluginsTab {
            case .explore:
                Button("Add marketplace", systemImage: "plus") { addingMarketplace = true }
                    .primaryButton()
                Button("Refresh", systemImage: "arrow.clockwise") { Task { await refreshExplore() } }
                    .iconButton()
                    .disabled(catalog.running != nil)
                    .keyboardShortcut("r", modifiers: .command)
                    .help("Read the marketplaces again (⌘R)")
            case .health:
                if model.fixAllCount > 0 || model.fixAllProgress != nil {
                    Button(fixAllTitle) { confirmingAll = model.fixAllIDs }
                        .primaryButton()
                        // Also while an Explore action runs: one change at a time.
                        .disabled(model.work.isBusy)
                        .hoverTip("Run every fix swe can run, one at a time", replacesLabel: false)
                }
                Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.load(swe: app.swe, for: app.swePath) } }
                    .iconButton()
                    .disabled(model.fixing != nil)
                    .keyboardShortcut("r", modifiers: .command)
                    .help("Check again (⌘R)")
            }
        } content: {
            Tabs(options: [(PluginsTab.explore, "Explore"), (PluginsTab.health, "Health")],
                 selection: $app.pluginsTab, style: .underline,
                 // Reselecting Explore while a plugin detail is open goes
                 // back to the list, through the detail's unsaved ask.
                 onSelect: { tab in
                    if tab == .explore { app.navReselects += 1 }
                 })
            switch app.pluginsTab {
            case .explore:
                PluginExplorer(model: catalog, openHealth: { app.pluginsTab = .health },
                               addMarketplace: { addingMarketplace = true }) { confirmingAction = $0 }
            case .health: health
            }
        }
        .task(id: app.swePath) {
            // Health's report stays warm behind Explore, the tab on show.
            await model.loadIfStale(swe: app.swe, for: app.swePath)
        }
        .quiverSheet(isPresented: $addingMarketplace) {
            AddMarketplaceSheet()
        }
        .task(id: ExploreLoad(swePath: app.swePath, open: app.pluginsTab == .explore)) {
            if app.pluginsTab == .explore { await openExplore() }
        }
        .confirmationDialog("Run this fix?", isPresented: .init(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } }), presenting: confirming) { finding in
            Button("Run") { Task { await fix(finding) } }
        } message: { finding in
            Text("swe will check the finding still applies, then run:\n\(finding.fix)")
        }
        .confirmationDialog(confirmingAll?.count == 1 ? "Run 1 fix?" : "Run \(confirmingAll?.count ?? 0) fixes?",
                            isPresented: .init(get: { confirmingAll != nil }, set: { if !$0 { confirmingAll = nil } }),
                            presenting: confirmingAll) { ids in
            Button("Run all") { Task { await fixAll(ids) } }
        } message: { ids in
            Text("One at a time, stopping at the first that fails. swe checks each still applies before it runs:\n\n"
                 + fixes(for: ids).joined(separator: "\n"))
        }
        .confirmationDialog(confirmingAction?.question ?? "",
                            isPresented: .init(get: { confirmingAction != nil }, set: { if !$0 { confirmingAction = nil } }),
                            presenting: confirmingAction) { action in
            Button(action.verb.title, role: action.verb == .uninstall ? .destructive : nil) {
                Task { await run(action) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { action in
            Text(action.isEdit
                 ? "swe checks the current state, then makes this edit, and writes it only once it is proven safe:\n\n\(action.command)"
                 : "swe checks the current state, then runs:\n\n\(action.command)")
        }
    }

    /// The drift report, as before Explore: one card per harness.
    @ViewBuilder private var health: some View {
        if let error = model.fixError {
            ErrorCard(message: error)
        } else if let fixed = model.lastFixed {
            Card {
                Label("Ran \(fixed)", systemImage: "checkmark.circle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            }
        }
        switch model.state {
        case .idle, .loading:
            SkeletonRows(rows: 5, shape: .check, label: "Checking plugin drift…")
        case .failed(let message):
            ErrorCard(message: message)
        case .loaded(let report) where report.sources == 0:
            Card { Label("No plugins in ~/.quiver/plugins, so there is nothing to keep in step.", systemImage: "puzzlepiece.extension").foregroundStyle(Theme.textSecondary) }
        case .loaded(let report) where report.drift.isEmpty:
            Card { Label("Claude Code and Codex run what ~/.quiver/plugins holds.", systemImage: "checkmark.seal").foregroundStyle(Theme.textPrimary) }
        case .loaded(let report):
            let harnesses = report.drift.map(\.harness).reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
            ForEach(harnesses, id: \.self) { harness in
                let rows = report.drift.filter { $0.harness == harness }
                let shown = RowFold.visible(rows, limit: PluginsPage.foldLimit,
                                            expanded: opened.contains(harness))
                Card {
                    HarnessTitle(harness: harness, findings: rows.count)
                    ForEach(shown) { finding in
                        DriftRow(finding: finding, running: model.fixing == finding.id,
                                 busy: model.work.isBusy) { confirming = finding }
                        if finding != shown.last { Divider().overlay(Theme.border) }
                    }
                    if RowFold.hiddenCount(total: rows.count, limit: Self.foldLimit) > 0 {
                        Divider().overlay(Theme.border)
                        ExpandRow(isExpanded: opened.contains(harness),
                                  hidden: RowFold.hiddenCount(total: rows.count, limit: Self.foldLimit),
                                  help: (expand: "Show every finding", collapse: "Show only the first \(Self.foldLimit)"),
                                  edge: .inset) {
                            withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                                if opened.contains(harness) { opened.remove(harness) }
                                else { opened.insert(harness) }
                            }
                        }
                    }
                }
            }
        }
    }

    private var fixAllTitle: String {
        guard let progress = model.fixAllProgress else { return "Fix all" }
        return "Fixing \(min(progress.done + 1, progress.total)) of \(progress.total)…"
    }

    private func fixes(for ids: [String]) -> [String] {
        guard case .loaded(let report) = model.state else { return [] }
        return ids.compactMap { id in report.drift.first { $0.id == id }?.fix }
    }

    private func fix(_ finding: PluginDrift) async {
        await model.fix(finding, swe: app.swe, for: app.swePath)
        await refreshCatalog()
    }

    private func fixAll(_ ids: [String]) async {
        await model.fixAll(confirmed: ids, swe: app.swe, for: app.swePath)
        await refreshCatalog()
    }

    /// The catalog and the skills: each plugin row lists the installed
    /// copies the Skills page's inventory found inside it.
    private func openExplore() async {
        async let skills: Void = app.skills.loadIfStale(for: app.swePath)
        await catalog.loadIfStale(swe: app.swe, for: app.swePath)
        await skills
    }

    private func refreshExplore() async {
        async let skills: Void = app.skills.load(for: app.swePath)
        await catalog.load(swe: app.swe, for: app.swePath)
        await skills
    }

    /// A fix changes what Explore shows, and the skills a plugin ships;
    /// read them again if they have been read.
    private func refreshCatalog() async {
        guard catalog.index != nil else { return }
        async let skills: Void = refreshSkills()
        await catalog.load(swe: app.swe, for: app.swePath)
        await skills
    }

    /// Always a new read, even with one in flight: that one may have begun
    /// before the change, and the newer read is the one that shows.
    private func refreshSkills() async {
        await app.skills.load(for: app.swePath)
    }

    /// Runs a confirmed action, then lists the skills again: an install or
    /// uninstall adds or removes the ones the plugin ships. One in the hub
    /// can open or close a gap the Health tab reports, so that is checked
    /// again too.
    private func run(_ action: PluginAction) async {
        let hub = catalog.index?.rows.first { $0.id == action.ref }?.isHub == true
        await catalog.run(action, swe: app.swe, for: app.swePath)
        await refreshSkills()
        if hub { await model.load(swe: app.swe, for: app.swePath) }
    }
}

/// Explore reads the catalog when it opens, and again for another swe.
private struct ExploreLoad: Hashable {
    var swePath: String
    var open: Bool
}

/// The card's heading: the harness logo, its name and how many findings.
private struct HarnessTitle: View {
    var harness: String
    var findings: Int

    var body: some View {
        HStack(spacing: 8) {
            HarnessIcon(harness: harness, size: 16)
            Text(UsageFormat.harness(harness)).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Text(findings == 1 ? "1 finding" : "\(findings) findings")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        }
    }
}

private struct DriftRow: View {
    var finding: PluginDrift
    var running: Bool
    var busy: Bool
    var fix: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle().fill(finding.isSevere ? Theme.danger : Theme.warning)
                .frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(finding.ref).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    Badge(finding.kindTitle)
                }
                Text(finding.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if !finding.paths.isEmpty {
                    Text(pathsLine).font(Theme.caption).foregroundStyle(Theme.textMuted)
                        .lineLimit(2).truncationMode(.middle)
                }
                HStack(alignment: .top, spacing: 6) {
                    // A code block: bg.code, 6/8 padding, radius 4, mono 12/16.
                    Text(finding.fix)
                        .font(Theme.font(size: 12, design: .monospaced))
                        .lineSpacing(2)
                        .foregroundStyle(Theme.textSecondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.vertical, 6)
                        .padding(.horizontal, 8)
                        .background(Theme.code, in: .rect(cornerRadius: Theme.smallRadius))
                        // bg.code is the card's own colour in dark; the hairline keeps the block visible.
                        .overlay(RoundedRectangle(cornerRadius: Theme.smallRadius).strokeBorder(Theme.border, lineWidth: 1))
                    CopyButton { copyToPasteboard(finding.fix) }
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 24, height: 28)
                        .contentShape(.rect)
                        .plainButton(cornerRadius: Theme.smallRadius)
                }
                if !finding.runnable {
                    Text("No command does this, so make the edit by hand.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
            Spacer(minLength: 8)
            if finding.runnable {
                if running {
                    ProgressView().controlSize(.small).frame(height: Theme.controlHeight + 4)
                } else {
                    Button("Fix", action: fix)
                        .secondaryButton()
                        .disabled(busy)
                }
            }
        }
    }

    private var pathsLine: String {
        let shown = finding.paths.prefix(3).joined(separator: ", ")
        let more = finding.paths.count - 3
        return more > 0 ? "\(shown) and \(more) more" : shown
    }
}
