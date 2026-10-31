import AppKit
import QuiverCore
import SystemConfiguration
import SwiftUI

/// The caption column a server sheet aligns its labelled rows on.
private let detailLabelWidth: CGFloat = 96

/// Every MCP server the harnesses configure, as a server × harness grid:
/// where each is missing, where two definitions disagree, and a sync
/// through `swe mcp sync` to even them out.
struct McpServersPage: View {
    @Environment(AppModel.self) private var app
    @State private var filter: McpFilter = .all
    @State private var selected: McpServer?
    @State private var adding = false
    /// Harnesses left out of the comparison, remembered on this Mac.
    @State private var hidden = Set(UserDefaults.standard.stringArray(forKey: Self.hiddenKey) ?? [])

    static let hiddenKey = "mcpHiddenHarnesses"

    /// App-wide, so a read outlives a trip to another page and the last
    /// list shows at once.
    private var model: McpModel { app.mcp }

    /// The folder the folder scan reads, from the Paths setting.
    private var repoRoot: URL { AppModel.mcpServersFolder(path: app.mcpServersPath) }

    /// The Local folder rows on their own, for states with no server grid
    /// to put them in (the inventory is empty or failed to read). With the
    /// hub unknown a row cannot claim "Not in the hub", so the caption hides.
    @ViewBuilder private func folderOnlyRows(hubKnown: Bool) -> some View {
        let local = McpServerRepo.grouped(servers: [], folders: app.mcpRepo.servers).local
        if !local.isEmpty {
            Card {
                ServerGrid(harnesses: [], local: local, remote: [],
                           showHealth: false, hubKnown: hubKnown, editor: app.editor) { _ in }
            }
        }
    }

    var body: some View {
        Page(title: "MCP servers",
             subtitle: "Every server your harnesses configure, where each is missing, and where two disagree.") {
            Button(model.checkingHealth ? "Checking…" : "Check health", systemImage: "waveform.path.ecg") {
                Task { await model.load(swe: app.swe, for: app.swePath, health: true) }
            }
            .secondaryButton()
            .disabled(model.checkingHealth)
            .hoverTip("Run each server's command check and reach each URL", replacesLabel: false)
            Button("Add server", systemImage: "plus") { adding = true }
                .primaryButton()
                .hoverTip("Add a server to the Quiver hub, and optionally to harnesses")
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe, for: app.swePath, health: model.hasHealth) }
                Task { await app.mcpRepo.load() }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Read the configs again (⌘R)")
        } content: {
            switch model.state {
            case .idle, .loading:
                SkeletonRows(rows: 6)
            case .failed(let message):
                ErrorCard(message: message)
                folderOnlyRows(hubKnown: false)
            case .loaded(let inventory) where inventory.servers.isEmpty:
                Card { VStack(alignment: .leading, spacing: 10) {
                    Label("No MCP servers in any harness config.", systemImage: "powerplug").foregroundStyle(Theme.textSecondary)
                    Button("Add server", systemImage: "plus") { adding = true }.secondaryButton()
                } }
                folderOnlyRows(hubKnown: true)
            case .loaded(let inventory):
                let compared = inventory.activeHarnesses.filter { !hidden.contains($0) }
                HStack(alignment: .center, spacing: 12) {
                    Tabs(options: McpFilter.allCases.map {
                        ($0, "\($0.title) \(inventory.servers($0, across: compared).count)")
                    }, selection: $filter, style: .period, onPanel: true)
                    Spacer(minLength: 8)
                }
                HarnessChips(harnesses: inventory.activeHarnesses, hidden: $hidden)
                    .onChange(of: hidden) { UserDefaults.standard.set(hidden.sorted(), forKey: Self.hiddenKey) }
                let rows = inventory.servers(filter, across: compared)
                let grouped = McpServerRepo.grouped(servers: rows, folders: app.mcpRepo.servers,
                                                    usageFrom: inventory.servers)
                if grouped.local.isEmpty && grouped.remote.isEmpty {
                    Card { Text("Every server is in every compared harness, defined the same way.").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary) }
                } else {
                    Card {
                        ServerGrid(harnesses: compared, local: grouped.local, remote: grouped.remote,
                                   showHealth: model.hasHealth, editor: app.editor) { selected = $0 }
                    }
                }
                VStack(alignment: .leading, spacing: 4) {
                    if !inventory.idleHarnesses.isEmpty {
                        Text("No servers in \(harnessNames(inventory.idleHarnesses)), so they have no column. A sync can still copy servers there.")
                    }
                    Text("Letters mark different definitions of one server. Env and header values never leave swe; only their names show here.")
                }
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            McpRepoSection(model: app.mcpRepo, root: repoRoot)
        }
        .task(id: app.swePath) { await model.loadIfStale(swe: app.swe, for: app.swePath) }
        .task(id: repoRoot) {
            app.mcpRepo.root = repoRoot
            await app.mcpRepo.load()
        }
        .quiverSheet(item: $selected) { server in
            if case .loaded(let inventory) = model.state {
                ServerSheet(initial: server, harnesses: inventory.harnesses,
                            active: inventory.activeHarnesses.filter { !hidden.contains($0) }, model: model) {
                    await model.load(swe: app.swe, for: app.swePath, health: model.hasHealth)
                }
            }
        }
        .quiverSheet(isPresented: $adding) {
            McpAddServerSheet(hidden: hidden) {
                await model.load(swe: app.swe, for: app.swePath, health: model.hasHealth)
            }
        }
    }
}

/// Which harnesses the grid compares: on chips are columns, off ones are left out.
private struct HarnessChips: View {
    var harnesses: [String]
    @Binding var hidden: Set<String>

    var body: some View {
        // Wraps in a narrow window rather than squeezing the names.
        FlowLayout(spacing: 6, lineSpacing: 6) {
            Text("Compare").font(Theme.caption).foregroundStyle(Theme.textSecondary).padding(.trailing, 4)
            ForEach(harnesses, id: \.self) { harness in
                let on = !hidden.contains(harness)
                Button {
                    if on { hidden.insert(harness) } else { hidden.remove(harness) }
                } label: {
                    HStack(spacing: 4) {
                        if on { Image(systemName: "checkmark").font(Theme.font(size: 9, weight: .bold)) }
                        HarnessMarkView(harness: harness, size: 14)
                    }
                    .font(Theme.control)
                    .foregroundStyle(on ? Theme.textPrimary : Theme.textMuted)
                    .padding(.horizontal, 10)
                    .frame(height: Theme.controlHeight)
                    .background(on ? Theme.selected : .clear, in: .capsule)
                    .overlay(Capsule().strokeBorder(on ? Theme.borderStrong : Theme.border, lineWidth: 1))
                    .contentShape(.capsule)
                }
                .plainButton(cornerRadius: Theme.controlHeight / 2)
                .accessibilityAddTraits(on ? .isSelected : [])
                .help(on ? "Leave \(HarnessMark(harness).name) out of the comparison" : "Compare \(HarnessMark(harness).name) too")
            }
        }
    }
}

private struct ServerGrid: View {
    var harnesses: [String]
    /// The grouped list: local servers and unused folders, then remotes.
    var local: [McpServerRepo.Row]
    var remote: [McpServerRepo.Row]
    var showHealth: Bool
    /// False when the inventory never loaded: an unused-looking folder
    /// cannot claim "Not in the hub", so its caption hides.
    var hubKnown = true
    var editor: CodeEditor?
    var open: (McpServer) -> Void

    /// The groups in display order, skipping empty ones. Local names the
    /// machine whose harnesses launch the server, as the Add sheet does.
    private var sections: [(title: String, rows: [McpServerRepo.Row])] {
        var out: [(String, [McpServerRepo.Row])] = []
        if !local.isEmpty { out.append((mcpLocalTitle, local)) }
        if !remote.isEmpty { out.append(("Remote", remote)) }
        return out
    }

    /// The name column takes whatever the cells leave, but never less than this.
    static let nameMinWidth: CGFloat = 180
    static let cellWidth: CGFloat = 88
    /// The gap between a Local row's folder buttons and the first harness
    /// cell, the same on a server's row and an unused folder's.
    static let actionsGap: CGFloat = 8
    /// Rows a section shows before folding behind a Show more row.
    private static let foldLimit = 10
    /// Section titles the user has unfolded.
    @State private var opened: Set<String> = []

    /// At least 88pt, wider when a heading needs it (a logo and a word,
    /// or the name of a harness with no logo), so it shows whole.
    static func columnWidth(_ harness: String) -> CGFloat {
        let mark = HarnessMark(harness)
        let font = Theme.nsFont(size: 12, weight: .medium)
        let text = mark.word.map { ($0 as NSString).size(withAttributes: [.font: font, .kern: 0.12]).width } ?? 0
        let logo: CGFloat = mark.icon == nil ? 0 : 16 + (mark.word == nil ? 0 : 4)
        return max(cellWidth, ceil(logo + text) + 16)
    }

    /// The card's inner width: the grid fills it, and scrolls only past it.
    @State private var available: CGFloat = 0

    var body: some View {
        ScrollView(.horizontal) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 0) {
                    Text("Server").frame(minWidth: Self.nameMinWidth, maxWidth: .infinity, alignment: .leading)
                    ForEach(harnesses, id: \.self) { harness in
                        HarnessMarkView(harness: harness)
                            .frame(width: Self.columnWidth(harness))
                    }
                }
                .font(Theme.tableHeader)
                .tracking(0.12)
                .foregroundStyle(Theme.tableHeaderText)
                .padding(.bottom, 8)
                Divider().overlay(Theme.border)
                ForEach(Array(sections.enumerated()), id: \.offset) { _, section in
                    let shown = RowFold.visible(section.rows, limit: Self.foldLimit,
                                                expanded: opened.contains(section.title))
                    Text("\(section.title) · \(section.rows.count)")
                        .font(Theme.cardTitle).foregroundStyle(Theme.textSecondary)
                        .padding(.top, 14).padding(.bottom, 6)
                    ForEach(Array(shown.enumerated()), id: \.element.id) { index, row in
                        switch row {
                        case .server(let server, let folders):
                            ServerRow(server: server, folders: folders, harnesses: harnesses,
                                      showHealth: showHealth, editor: editor) { open(server) }
                        case .folder(let folder):
                            FolderRow(folder: folder, harnesses: harnesses,
                                      hubKnown: hubKnown, editor: editor)
                        }
                        if index < shown.count - 1 { Divider().overlay(Theme.border) }
                    }
                    if RowFold.hiddenCount(total: section.rows.count, limit: Self.foldLimit) > 0 {
                        Divider().overlay(Theme.border)
                        ExpandRow(isExpanded: opened.contains(section.title),
                                  hidden: RowFold.hiddenCount(total: section.rows.count, limit: Self.foldLimit),
                                  help: (expand: "Show every \(section.title.lowercased()) row",
                                         collapse: "Show only the first \(Self.foldLimit)"),
                                  edge: .inset) {
                            withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                                if opened.contains(section.title) { opened.remove(section.title) }
                                else { opened.insert(section.title) }
                            }
                        }
                    }
                }
            }
            // Exactly the card's width, or the columns' minimum if wider, so a
            // long name truncates instead of pushing the harness columns away.
            .frame(width: max(available, Self.nameMinWidth + harnesses.map(Self.columnWidth).reduce(0, +)),
                   alignment: .leading)
        }
        .scrollIndicators(.automatic)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { available = $0 }
    }
}

private struct ServerRow: View {
    var server: McpServer
    /// The folders in the servers folder this server runs from; empty for
    /// a remote one.
    var folders: [McpRepoServer] = []
    var harnesses: [String]
    var showHealth: Bool
    var editor: CodeEditor?
    var open: () -> Void
    @State private var hovering = false

    /// The harness cells' total width: the folder actions overlay the row
    /// just left of them, so the checkmark columns never move.
    private var cellsWidth: CGFloat {
        harnesses.map(ServerGrid.columnWidth).reduce(0, +)
    }

    /// What the name column leaves for the folder actions.
    private var actionsWidth: CGFloat {
        folders.isEmpty ? 0 : CGFloat(editor == nil ? 1 : 2) * Theme.iconButton + 24
    }

    var body: some View {
        Button(action: open) {
            HStack(spacing: 0) {
                HStack(alignment: .top, spacing: 8) {
                    if showHealth, let health = server.health {
                        Circle().fill(healthColor(health)).frame(width: Theme.statusDot, height: Theme.statusDot)
                            .padding(.top, 4)
                            .hoverTip(server.detail ?? "Healthy")
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 5) {
                            Text(server.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                            if !server.secretsMissing.isEmpty {
                                Image(systemName: "exclamationmark.triangle")
                                    .font(Theme.font(size: 10))
                                    .foregroundStyle(Theme.warningText)
                                    .hoverTip("Needs a value for \(server.secretsMissing.joined(separator: ", "))")
                            }
                        }
                        secondary
                    }
                }
                .padding(.trailing, actionsWidth)
                .frame(minWidth: ServerGrid.nameMinWidth, maxWidth: .infinity, alignment: .leading)
                let differs = server.conflicts(among: harnesses)
                ForEach(harnesses, id: \.self) { harness in
                    Cell(server: server, harness: harness, differs: differs).frame(width: ServerGrid.columnWidth(harness))
                }
            }
            .padding(.vertical, 8)
            .background(hovering ? Theme.rowHover : .clear)
            .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.smallRadius)
        .onHover { hovering = $0 }
        .accessibilityLabel("\(server.name), in \(harnessNames(server.harnesses))")
        // The Finder/editor buttons sit on the open button, above it in
        // hit order, so they stay clickable without breaking the cells'
        // alignment or losing the row's click to open.
        .overlay(alignment: .trailing) {
            if !folders.isEmpty {
                FolderActions(folders: folders, editor: editor)
                    .padding(.trailing, cellsWidth + ServerGrid.actionsGap)
            }
        }
    }

    /// "stdio · checkout · linear-mcp · main ↑1 ↓45 · Local changes": the
    /// transport, a checkout tag and the folder name(s) in mono when it runs
    /// from the servers folder, then the git state when there is one
    /// folder; several folders leave their git state to the menus.
    @ViewBuilder private var secondary: some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(server.transport)
            if !folders.isEmpty {
                Text("·")
                Text("checkout")
                    .font(Theme.font(size: 10, weight: .medium))
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(Theme.subtle, in: .rect(cornerRadius: 4))
                    .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Theme.border, lineWidth: 1))
                    .hoverTip("Runs from a folder in your MCP servers folder")
                Text("·")
                Text(folders.map(\.name).joined(separator: ", "))
                    .font(Theme.font(size: 12, design: .monospaced))
                if folders.count == 1 {
                    Text("·")
                    GitPieces(folder: folders[0])
                }
            }
        }
        .font(Theme.caption).foregroundStyle(Theme.textMuted)
        .lineLimit(1)
    }
}

private struct Cell: View {
    var server: McpServer
    var harness: String
    /// Whether the compared harnesses disagree; letters show only then.
    var differs: Bool

    var body: some View {
        if let group = server.group(of: harness) {
            if differs {
                Text(variantLetter(group))
                    .font(Theme.font(size: 11, weight: .semibold))
                    .foregroundStyle(group == 0 ? Theme.textPrimary : Theme.warningText)
                    .frame(width: 20, height: 20)
                    .background(Theme.subtle, in: .rect(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Theme.border, lineWidth: 1))
                    .hoverTip("Definition \(variantLetter(group))")
            } else {
                Image(systemName: "checkmark").font(Theme.font(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
            }
        } else {
            Text("—").foregroundStyle(Theme.borderStrong).hoverTip("Not in \(HarnessMark(harness).name)")
        }
    }
}

func variantLetter(_ index: Int) -> String {
    index < 26 ? String(UnicodeScalar(UInt8(65 + index))) : "\(index + 1)"
}

func healthColor(_ health: String) -> Color {
    switch health {
    case "ok": Theme.success
    case "error": Theme.danger
    default: Theme.textMuted
    }
}

/// One server: what it is, who has which definition, and a sync.
private struct ServerSheet: View {
    var initial: McpServer
    /// Every harness swe reads, the sync targets.
    var harnesses: [String]
    /// The harnesses the grid compares; missing ones are picked by default.
    var active: [String]
    var model: McpModel
    var reload: () async -> Void

    @Environment(AppModel.self) private var app
    @Environment(\.dismissSheet) private var dismiss
    /// The harness whose definition is copied. A name, not a group index, so
    /// a reload that regroups definitions can't switch it to another one.
    @State private var sourceHarness: String?
    @State private var chosen: Set<String> = []
    /// Harnesses ticked while shown as having their own definition: the only
    /// ones a sync may replace.
    @State private var approvedReplace: Set<String> = []
    @State private var preview: McpSyncReport?
    @State private var done: McpSyncReport?
    @State private var error: String?
    @State private var running = false
    @State private var confirmingOverwrite = false
    /// The pasted value per missing ${NAME}, cleared after it is saved.
    @State private var secretDrafts: [String: String] = [:]
    @State private var savingSecret: String?
    @State private var confirmingRemove = false

    /// The server as the latest load has it, so a finished sync shows its effect.
    var server: McpServer { model.server(named: initial.name) ?? initial }
    var source: String {
        if let sourceHarness, model.sources(for: server).contains(sourceHarness) { return sourceHarness }
        return model.sources(for: server).first ?? ""
    }
    var targets: SyncTargets { model.targets(for: server, from: source, in: harnesses) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Text(server.name).font(Theme.sectionTitle).tracking(-0.5)
                Badge("\(server.isRemote ? "Remote" : "Local") · \(server.transport)")
                if let health = server.health {
                    Circle().fill(healthColor(health)).frame(width: Theme.statusDot, height: Theme.statusDot)
                    Text(server.detail ?? "Healthy").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            Text(server.summary).font(Theme.font(size: 12, design: .monospaced)).textSelection(.enabled)
                .foregroundStyle(Theme.textSecondary).lineLimit(3)
            if server.groups.count > 1 {
                // swe describes a server by the first harness that defines it.
                Text("Command, URL and key names are definition A's.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            if !server.envKeys.isEmpty {
                Facts(label: "Env vars", value: server.envKeys.joined(separator: ", "))
            }
            if !server.headerKeys.isEmpty {
                Facts(label: "Headers", value: server.headerKeys.joined(separator: ", "))
            }
            Divider().overlay(Theme.border)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(server.groups.enumerated()), id: \.offset) { index, group in
                    HarnessFacts(label: server.groups.count > 1 ? "Definition \(variantLetter(index))" : "In",
                                 harnesses: group)
                }
                let missing = server.missing(from: active)
                if !missing.isEmpty { HarnessFacts(label: "Missing from", harnesses: missing) }
            }
            if !server.secretsMissing.isEmpty {
                Divider().overlay(Theme.border)
                needsValues
            }
            Divider().overlay(Theme.border)
            syncForm
        }
        .sheetScaffold()
        .onAppear(perform: resetChoice)
        .confirmationDialog("Remove \(server.name)?", isPresented: $confirmingRemove) {
            Button("Remove", role: .destructive) { Task { await removeServer() } }
        } message: {
            let places = server.harnesses.map { HarnessMark($0).name }
                + (server.inHub ? ["Quiver hub"] : [])
            Text("It goes from \(places.joined(separator: ", ")).")
        }
        .confirmationDialog("Replace their definitions?", isPresented: $confirmingOverwrite) {
            Button("Replace", role: .destructive) { Task { await run(dryRun: false) } }
        } message: {
            let sourceName = source == "quiver" ? "Quiver hub" : HarnessMark(source).name
            Text("\(harnessNames(approvedReplace.intersection(targets.differing).sorted())) will get \(sourceName)'s definition of \(server.name) instead of their own.")
        }
    }

    /// Paste fields for the hub's unresolved ${NAME} references. swe
    /// refuses to sync while one is missing, so these come first.
    private var needsValues: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Needs values").font(Theme.cardTitle)
            ForEach(server.secretsMissing, id: \.self) { name in
                HStack(spacing: 8) {
                    Text(name)
                        .font(Theme.font(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.textPrimary).lineLimit(1)
                    SecureField("Paste the value",
                                text: Binding(get: { secretDrafts[name] ?? "" },
                                              set: { secretDrafts[name] = $0 }))
                        .quiverField(monospaced: true)
                    Button(savingSecret == name ? "Saving…" : "Save") {
                        Task { await saveSecret(name) }
                    }
                    .secondaryButton()
                    .disabled((secretDrafts[name] ?? "").isEmpty || savingSecret != nil)
                }
            }
            Text("Saved to ~/.quiver/secrets/mcp/.api_keys, never versioned. "
                 + "Sync fills these in when it writes a harness config.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func saveSecret(_ name: String) async {
        let value = secretDrafts[name] ?? ""
        guard !value.isEmpty else { return }
        savingSecret = name
        error = nil
        defer { savingSecret = nil }
        do {
            try await app.swe.mcpSecretSet(name: name, value: value)
            secretDrafts[name] = ""
            await reload()
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Sync from the hub is refused by swe while a ${NAME} has no value.
    private var secretsBlockSync: Bool {
        source == "quiver" && !server.secretsMissing.isEmpty
    }

    @ViewBuilder private var syncForm: some View {
        Text("Sync").font(Theme.cardTitle)
        if !server.canSyncAlone {
            Text("swe can't sync a server whose name has a comma on its own. Use swe mcp sync in Terminal.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        } else {
            // The picker must come first: with several sources (a hub
            // copy plus agreeing harnesses) the choice is still live even
            // when there's nothing to copy from the current source.
            let sourceChoices = model.sources(for: server)
            if sourceChoices.count > 1 {
                HStack(spacing: 10) {
                    Text("Copy from").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .frame(width: detailLabelWidth, alignment: .leading)
                    SelectMenu(options: sourceChoices.map { choice in
                                   (choice, choice == "quiver" ? "Quiver hub" : HarnessMark(choice).name, choice)
                               },
                               selection: Binding(
                                   get: { source },
                                   set: { sourceHarness = $0; resetChoice() }),
                               width: 366)
                }
            } else if !sourceChoices.isEmpty {
                HarnessFacts(label: "Copy from", harnesses: [source])
            }
            if targets.missing.isEmpty && targets.differing.isEmpty {
                Text(source == "quiver"
                     ? "Every harness already has this server."
                     : "Every harness already has this definition.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            VStack(alignment: .leading, spacing: 8) {
                ForEach(targets.missing, id: \.self) { harness in
                    TargetToggle(harness: harness, replaces: false, isOn: binding(harness))
                }
                ForEach(targets.differing, id: \.self) { harness in
                    TargetToggle(harness: harness, replaces: true, isOn: binding(harness))
                }
            }
        }
        if let report = done ?? preview {
            Label(report.sentence, systemImage: done != nil ? "checkmark.circle" : "eye")
                .font(Theme.caption).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let error {
            Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText).fixedSize(horizontal: false, vertical: true)
        }
        HStack {
            Button("Remove…", role: .destructive) { confirmingRemove = true }
                .secondaryButton()
            if running { ProgressView().controlSize(.small) }
            if secretsBlockSync {
                Text("Set the values above first.")
                    .font(Theme.caption).foregroundStyle(Theme.warningText)
            }
            Spacer()
            Button(done == nil ? "Cancel" : "Close", role: .cancel) { dismiss() }
                .secondaryButton()
            if done == nil && server.canSyncAlone {
                Button("Preview") { Task { await run(dryRun: true) } }
                    .secondaryButton()
                    .disabled(chosen.isEmpty || running || secretsBlockSync)
                Button("Sync") {
                    if targets.needsOverwrite(approvedReplace) {
                        confirmingOverwrite = true
                    } else {
                        Task { await run(dryRun: false) }
                    }
                }
                .primaryButton()
                .keyboardShortcut(.defaultAction)
                .disabled(chosen.isEmpty || running || secretsBlockSync)
            }
        }
    }

    private func binding(_ harness: String) -> Binding<Bool> {
        Binding(get: { chosen.contains(harness) }, set: { on in
            if on { chosen.insert(harness) } else { chosen.remove(harness) }
            if on && targets.differing.contains(harness) {
                approvedReplace.insert(harness)
            } else if !on {
                approvedReplace.remove(harness)
            }
            preview = nil
        })
    }

    private func resetChoice() {
        // Harnesses that use MCP and lack the server; empty ones stay opt-in.
        chosen = Set(targets.missing).intersection(active)
        approvedReplace = []
        preview = nil
    }

    private func removeServer() async {
        running = true
        error = nil
        defer { running = false }
        do {
            _ = try await app.swe.mcpRemove(
                name: server.name,
                from: server.harnesses + (server.inHub ? ["quiver"] : []))
            await reload()
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func run(dryRun: Bool) async {
        running = true
        error = nil
        defer { running = false }
        // Recomputed from the latest inventory: a harness that gained its own
        // definition since it was ticked is neither added nor replaced.
        let adding = harnesses.filter { chosen.contains($0) && targets.missing.contains($0) }
        let replacing = harnesses.filter { approvedReplace.contains($0) && targets.differing.contains($0) }
        do {
            let report = try await model.sync(server, from: source, adding: adding, replacing: replacing,
                                              dryRun: dryRun, swe: app.swe)
            if dryRun {
                preview = report
            } else {
                done = report
                await reload()
            }
        } catch let partial as McpPartialSync {
            self.error = partial.message
            if dryRun {
                preview = partial.report
            } else {
                done = partial.report
                await reload()
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// A sync target: the harness, a note when it would lose its own
/// definition, and the themed switch.
private struct TargetToggle: View {
    var harness: String
    var replaces: Bool
    @Binding var isOn: Bool

    var body: some View {
        HStack(spacing: 8) {
            let mark = HarnessMark(harness)
            HarnessIcon(harness: mark.icon ?? harness, size: 16)
            Text(mark.name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            if replaces {
                Text("replaces its own definition").font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            Spacer(minLength: 8)
            Toggle(replaces ? "\(mark.name), replaces its own definition" : mark.name, isOn: $isOn)
                .toggleStyle(AccentSwitchStyle())
        }
    }
}

/// A sheet fact whose value is a set of harnesses, shown as their logos.
private struct HarnessFacts: View {
    var label: String
    var harnesses: [String]

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary).frame(width: detailLabelWidth, alignment: .leading)
            FlowLayout(spacing: 10, lineSpacing: 6) {
                ForEach(harnesses, id: \.self) { HarnessMarkView(harness: $0) }
            }
            .font(Theme.bodySmall)
            .foregroundStyle(Theme.textPrimary)
        }
    }
}

private struct Facts: View {
    var label: String
    var value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary).frame(width: detailLabelWidth, alignment: .leading)
            Text(value).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Server folders

/// What is left of the old "Server folders" section: only the states the
/// grouped list cannot show. A good scan renders nothing here, since its
/// folders are the Local rows up in the grid.
private struct McpRepoSection: View {
    var model: McpServerRepoModel
    var root: URL

    var body: some View {
        if let error = model.error {
            ErrorCard(message: error)
        } else if !FileManager.default.fileExists(atPath: root.path) {
            Card {
                Label("\(displayPath(root)) doesn't exist. Set the folder in Settings > Paths.",
                      systemImage: "folder.badge.questionmark")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
        } else if model.servers.isEmpty && model.isLoading {
            SkeletonRows(rows: 4)
        } else if model.servers.isEmpty {
            Card {
                Text("No server folders in \(displayPath(root)).")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

/// A folder in the servers folder that no hub server uses yet: the
/// folder's details in the name column and nothing in the harness cells.
private struct FolderRow: View {
    var folder: McpRepoServer
    var harnesses: [String]
    /// False when the hub never loaded: the caption hides rather than
    /// claim "Not in the hub" on a guess.
    var hubKnown = true
    var editor: CodeEditor?

    var body: some View {
        HStack(spacing: 0) {
            RepoFolderRow(folder: folder, usedBy: hubKnown ? [] : nil, editor: editor)
                // Its buttons end where a server row's do, not 8pt further right.
                .padding(.trailing, ServerGrid.actionsGap)
                .frame(minWidth: ServerGrid.nameMinWidth, maxWidth: .infinity, alignment: .leading)
            ForEach(harnesses, id: \.self) { harness in
                Spacer().frame(width: ServerGrid.columnWidth(harness))
            }
        }
        .padding(.vertical, 8)
    }
}

/// The Local rows' folder buttons: Show in Finder and Open in the
/// editor. One folder gets plain buttons; a server backed by several
/// gives each button a menu to pick the folder.
private struct FolderActions: View {
    var folders: [McpRepoServer]
    var editor: CodeEditor?

    private var single: McpRepoServer? { folders.count == 1 ? folders[0] : nil }

    var body: some View {
        HStack(spacing: 8) {
            if let folder = single {
                Button("Show in Finder", systemImage: "folder") {
                    NSWorkspace.shared.activateFileViewerSelecting([folder.path])
                }
                .iconButton()
                .help("Show \(folder.name) in Finder")
            } else if !folders.isEmpty {
                Menu {
                    ForEach(folders) { folder in
                        Button(Self.menuTitle(folder)) {
                            NSWorkspace.shared.activateFileViewerSelecting([folder.path])
                        }
                    }
                } label: { actionIcon("folder") }
                .menuStyle(.button).menuIndicator(.hidden)
                .plainButton(cornerRadius: Theme.iconButtonRadius).fixedSize()
                .help("Show in Finder")
            }
            if let editor {
                if let folder = single {
                    Button("Open in \(editor.name)", systemImage: "chevron.left.forwardslash.chevron.right") {
                        NSWorkspace.shared.open([folder.path], withApplicationAt: editor.appURL,
                                                configuration: .init())
                    }
                    .iconButton()
                    .help("Open \(folder.name) in \(editor.name)")
                } else if !folders.isEmpty {
                    Menu {
                        ForEach(folders) { folder in
                            Button(Self.menuTitle(folder)) {
                                NSWorkspace.shared.open([folder.path], withApplicationAt: editor.appURL,
                                                        configuration: .init())
                            }
                        }
                    } label: { actionIcon("chevron.left.forwardslash.chevron.right") }
                    .menuStyle(.button).menuIndicator(.hidden)
                    .plainButton(cornerRadius: Theme.iconButtonRadius).fixedSize()
                    .help("Open in \(editor.name)")
                }
            }
        }
    }

    /// "linear-mcp · main ↑1 ↓2 · Local changes" so a folder picker names
    /// its checkout the way the row's caption does, or just the name
    /// while git is still answering.
    private static func menuTitle(_ folder: McpRepoServer) -> String {
        var parts = [folder.name]
        if let git = folder.git {
            if git.isRepo {
                parts.append(git.branch ?? "detached HEAD")
                let counts = [git.ahead > 0 ? "↑\(git.ahead)" : nil,
                              git.behind > 0 ? "↓\(git.behind)" : nil].compactMap { $0 }
                if !counts.isEmpty { parts.append(counts.joined(separator: " ")) }
                if git.hasChanges { parts.append("Local changes") }
            } else {
                parts.append("Not a git repo")
            }
        }
        return parts.joined(separator: " · ")
    }

    /// The icon buttons' chrome, applied by hand since IconButtonStyle is
    /// a ButtonStyle and a Menu cannot take it.
    private func actionIcon(_ name: String) -> some View {
        Image(systemName: name)
            .font(Theme.font(size: 16))
            .foregroundStyle(Theme.textNav)
            .frame(width: Theme.iconButton, height: Theme.iconButton)
            .overlay(RoundedRectangle(cornerRadius: Theme.iconButtonRadius).strokeBorder(Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.iconButtonRadius))
    }
}

/// A folder's git state as dot-separated caption pieces: the branch, the
/// ahead/behind counts and a Local changes badge; "Not a git repo" when
/// the folder isn't a repository, or a spinner while git answers.
private struct GitPieces: View {
    var folder: McpRepoServer

    var body: some View {
        if let git = folder.git {
            if git.isRepo {
                Text(git.branch ?? "detached HEAD").foregroundStyle(Theme.textSecondary)
                let counts = [git.ahead > 0 ? "↑\(git.ahead)" : nil,
                              git.behind > 0 ? "↓\(git.behind)" : nil].compactMap { $0 }
                if !counts.isEmpty {
                    Text(counts.joined(separator: " ")).foregroundStyle(Theme.textSecondary).monospacedDigit()
                }
                if git.hasChanges {
                    Text("·")
                    Badge("Local changes")
                }
            } else {
                Text("Not a git repo")
            }
        } else {
            ProgressView().controlSize(.mini)
        }
    }
}

/// A folder in the servers folder that no hub server uses yet: its name
/// as the title, "Not in the hub · main" folded into the secondary line,
/// then Show in Finder and Open in editor.
private struct RepoFolderRow: View {
    var folder: McpRepoServer
    /// nil hides the hub caption (the inventory never loaded); [] shows
    /// "Not in the hub".
    var usedBy: [String]?
    var editor: CodeEditor?

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(folder.name)
                    .font(Theme.font(size: 14, weight: .medium, design: .monospaced))
                    .foregroundStyle(Theme.textPrimary).lineLimit(1)
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    if let usedBy {
                        Text(usedBy.isEmpty ? "Not in the hub" : "Used by \(usedBy.joined(separator: ", "))")
                            .foregroundStyle(usedBy.isEmpty ? Theme.textMuted : Theme.textSecondary)
                            .lineLimit(1)
                        Text("·")
                    }
                    GitPieces(folder: folder)
                }
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            Spacer(minLength: 8)
            FolderActions(folders: [folder], editor: editor)
        }
    }
}

/// The Local group's title: the machine whose harnesses launch a command
/// server. The Mac app manages this Mac's harnesses.
/// Read once: `Host.current()` can stall for seconds on a misconfigured
/// network, and this is read in view bodies on the main thread.
let mcpLocalTitle: String = {
    let name = SCDynamicStoreCopyComputerName(nil, nil) as String?
    return "Local: runs on \(name ?? "this Mac")"
}()
