import AppKit
import QuiverCore
import SwiftUI

/// Every MCP server the harnesses configure, as a server × harness grid:
/// where each is missing, where two definitions disagree, and a sync
/// through `swe mcp sync` to even them out.
struct McpServersPage: View {
    @Environment(AppModel.self) private var app
    @State private var model = McpModel()
    @State private var filter: McpFilter = .all
    @State private var selected: McpServer?
    /// Harnesses left out of the comparison, remembered on this Mac.
    @State private var hidden = Set(UserDefaults.standard.stringArray(forKey: Self.hiddenKey) ?? [])

    static let hiddenKey = "mcpHiddenHarnesses"

    var body: some View {
        Page(title: "MCP servers",
             subtitle: "Every server your harnesses configure, where each is missing, and where two disagree.") {
            Button(model.checkingHealth ? "Checking…" : "Check health", systemImage: "waveform.path.ecg") {
                Task { await model.load(swe: app.swe, health: true) }
            }
            .secondaryButton()
            .disabled(model.checkingHealth)
            .help("Run each server's command check and reach each URL")
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe, health: model.hasHealth) }
            }
            .iconButton()
            .help("Read the configs again")
        } content: {
            switch model.state {
            case .idle, .loading:
                ProgressView().frame(maxWidth: .infinity)
            case .failed(let message):
                ErrorCard(message: message)
            case .loaded(let inventory) where inventory.servers.isEmpty:
                Card { Label("No MCP servers in any harness config.", systemImage: "powerplug").foregroundStyle(Theme.textSecondary) }
            case .loaded(let inventory):
                let compared = inventory.activeHarnesses.filter { !hidden.contains($0) }
                HStack(alignment: .center, spacing: 12) {
                    Tabs(options: McpFilter.allCases.map {
                        ($0, "\($0.title) \(inventory.servers($0, across: compared).count)")
                    }, selection: $filter, style: .period)
                    Spacer(minLength: 8)
                }
                HarnessChips(harnesses: inventory.activeHarnesses, hidden: $hidden)
                    .onChange(of: hidden) { UserDefaults.standard.set(hidden.sorted(), forKey: Self.hiddenKey) }
                let rows = inventory.servers(filter, across: compared)
                if rows.isEmpty {
                    Card { Text("Every server is in every compared harness, defined the same way.").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary) }
                } else {
                    Card {
                        ServerGrid(harnesses: compared, servers: rows,
                                   showHealth: model.hasHealth) { selected = $0 }
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
        }
        .task(id: app.swePath) { await model.load(swe: app.swe) }
        .sheet(item: $selected) { server in
            if case .loaded(let inventory) = model.state {
                ServerSheet(initial: server, harnesses: inventory.harnesses,
                            active: inventory.activeHarnesses.filter { !hidden.contains($0) }, model: model) {
                    await model.load(swe: app.swe, health: model.hasHealth)
                }
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
                        if on { Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)) }
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
    var servers: [McpServer]
    var showHealth: Bool
    var open: (McpServer) -> Void

    /// The name column takes whatever the cells leave, but never less than this.
    static let nameMinWidth: CGFloat = 180
    static let cellWidth: CGFloat = 88

    /// At least 88pt, wider when a heading needs it (a logo and a word,
    /// or the name of a harness with no logo), so it shows whole.
    static func columnWidth(_ harness: String) -> CGFloat {
        let mark = HarnessMark(harness)
        let font = NSFont.systemFont(ofSize: 12, weight: .medium)
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
                ForEach(servers) { server in
                    ServerRow(server: server, harnesses: harnesses, showHealth: showHealth) { open(server) }
                    if server != servers.last { Divider().overlay(Theme.border) }
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
    var harnesses: [String]
    var showHealth: Bool
    var open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(spacing: 0) {
                HStack(spacing: 8) {
                    if showHealth, let health = server.health {
                        Circle().fill(healthColor(health)).frame(width: 7, height: 7)
                            .help(server.detail ?? "Healthy")
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(server.name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                        Text(server.transport).font(Theme.caption).foregroundStyle(Theme.textMuted)
                    }
                }
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
        .plainButton(cornerRadius: 4)
        .onHover { hovering = $0 }
        .accessibilityLabel("\(server.name), in \(harnessNames(server.harnesses))")
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
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(group == 0 ? Theme.textPrimary : Theme.warningText)
                    .frame(width: 20, height: 20)
                    .background(Theme.subtle, in: .rect(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Theme.border, lineWidth: 1))
                    .help("Definition \(variantLetter(group))")
            } else {
                Image(systemName: "checkmark").font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
            }
        } else {
            Text("—").foregroundStyle(Theme.borderStrong).help("Not in \(HarnessMark(harness).name)")
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
    @Environment(\.dismiss) private var dismiss
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

    /// The server as the latest load has it, so a finished sync shows its effect.
    var server: McpServer { model.server(named: initial.name) ?? initial }
    var source: String {
        if let sourceHarness, server.harnesses.contains(sourceHarness) { return sourceHarness }
        return server.groups.first?.first ?? ""
    }
    var targets: SyncTargets { model.targets(for: server, from: source, in: harnesses) }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 8) {
                Text(server.name).font(Theme.sectionTitle).tracking(-0.5)
                Badge(server.transport)
                if let health = server.health {
                    Circle().fill(healthColor(health)).frame(width: 7, height: 7)
                    Text(server.detail ?? "Healthy").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            Text(server.summary).font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
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
            Divider().overlay(Theme.border)
            syncForm
        }
        .padding(24)
        .frame(width: 520)
        .onAppear(perform: resetChoice)
        .confirmationDialog("Replace their definitions?", isPresented: $confirmingOverwrite) {
            Button("Replace", role: .destructive) { Task { await run(dryRun: false) } }
        } message: {
            Text("\(harnessNames(approvedReplace.intersection(targets.differing).sorted())) will get \(HarnessMark(source).name)'s definition of \(server.name) instead of their own.")
        }
    }

    @ViewBuilder private var syncForm: some View {
        Text("Sync").font(Theme.cardTitle)
        if !server.canSyncAlone {
            Text("swe can't sync a server whose name has a comma on its own. Use swe mcp sync in Terminal.")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        } else if targets.missing.isEmpty && targets.differing.isEmpty && server.groups.count == 1 {
            Text("Every harness already has this definition.").font(Theme.caption).foregroundStyle(Theme.textSecondary)
        } else {
            if server.groups.count > 1 {
                HStack(spacing: 10) {
                    Text("Copy from").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .frame(width: 96, alignment: .leading)
                    SelectMenu(options: server.groups.enumerated().map { index, group in
                                   (group.first ?? "", "Definition \(variantLetter(index)) (\(harnessNames(group)))")
                               },
                               selection: Binding(
                                   get: { server.group(of: source).flatMap { server.groups[$0].first } ?? source },
                                   set: { sourceHarness = $0; resetChoice() }),
                               width: 366)
                }
            } else {
                HarnessFacts(label: "Copy from", harnesses: [source])
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
            if running { ProgressView().controlSize(.small) }
            Spacer()
            Button(done == nil ? "Cancel" : "Close", role: .cancel) { dismiss() }
                .secondaryButton()
            if done == nil && server.canSyncAlone {
                Button("Preview") { Task { await run(dryRun: true) } }
                    .secondaryButton()
                    .disabled(chosen.isEmpty || running)
                Button("Sync") {
                    if targets.needsOverwrite(approvedReplace) {
                        confirmingOverwrite = true
                    } else {
                        Task { await run(dryRun: false) }
                    }
                }
                .primaryButton()
                .keyboardShortcut(.defaultAction)
                .disabled(chosen.isEmpty || running)
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
                .toggleStyle(GreenSwitchStyle())
        }
    }
}

/// A sheet fact whose value is a set of harnesses, shown as their logos.
private struct HarnessFacts: View {
    var label: String
    var harnesses: [String]

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary).frame(width: 96, alignment: .leading)
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
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary).frame(width: 96, alignment: .leading)
            Text(value).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
