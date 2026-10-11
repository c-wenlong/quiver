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

    var body: some View {
        Page(title: "MCP servers",
             subtitle: "Every server your harnesses configure, where each is missing, and where two disagree.") {
            Button(model.checkingHealth ? "Checking…" : "Check health", systemImage: "waveform.path.ecg") {
                Task { await model.load(swe: app.swe, health: true) }
            }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(model.checkingHealth)
            .help("Run each server's command check and reach each URL")
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.load(swe: app.swe, health: model.hasHealth) }
            }
            .buttonStyle(IconButtonStyle())
            .help("Read the configs again")
        } content: {
            switch model.state {
            case .idle, .loading:
                ProgressView().frame(maxWidth: .infinity)
            case .failed(let message):
                Card { Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.danger) }
            case .loaded(let inventory) where inventory.servers.isEmpty:
                Card { Label("No MCP servers in any harness config.", systemImage: "powerplug").foregroundStyle(Theme.textSecondary) }
            case .loaded(let inventory):
                Tabs(options: McpFilter.allCases.map { ($0, "\($0.title) \(inventory.servers($0).count)") },
                     selection: $filter)
                let rows = inventory.servers(filter)
                if rows.isEmpty {
                    Card { Text("Every server is in every harness, defined the same way.").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary) }
                } else {
                    Card {
                        ServerGrid(harnesses: inventory.harnesses, servers: rows,
                                   showHealth: model.hasHealth) { selected = $0 }
                    }
                }
                Text("Letters mark different definitions of one server. Env and header values never leave swe; only their names show here.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
        .task(id: app.swePath) { await model.load(swe: app.swe) }
        .sheet(item: $selected) { server in
            if case .loaded(let inventory) = model.state {
                ServerSheet(server: server, harnesses: inventory.harnesses, model: model) {
                    await model.load(swe: app.swe, health: model.hasHealth)
                }
            }
        }
    }
}

private struct ServerGrid: View {
    var harnesses: [String]
    var servers: [McpServer]
    var showHealth: Bool
    var open: (McpServer) -> Void

    static let nameWidth: CGFloat = 240
    static let cellWidth: CGFloat = 78

    var body: some View {
        ScrollView(.horizontal) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 0) {
                    Text("Server").frame(width: Self.nameWidth, alignment: .leading)
                    ForEach(harnesses, id: \.self) { harness in
                        Text(harness).lineLimit(1).truncationMode(.tail)
                            .frame(width: Self.cellWidth)
                            .help(harness)
                    }
                }
                .font(Theme.tableHeader)
                .foregroundStyle(Theme.textSecondary)
                .padding(.bottom, 8)
                Divider().overlay(Theme.border)
                ForEach(servers) { server in
                    ServerRow(server: server, harnesses: harnesses, showHealth: showHealth) { open(server) }
                    if server != servers.last { Divider().overlay(Theme.border) }
                }
            }
        }
        .scrollIndicators(.automatic)
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
                .frame(width: ServerGrid.nameWidth, alignment: .leading)
                ForEach(harnesses, id: \.self) { harness in
                    Cell(server: server, harness: harness).frame(width: ServerGrid.cellWidth)
                }
            }
            .padding(.vertical, 8)
            .background(hovering ? Theme.rowHover : .clear)
            .contentShape(.rect)
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: 4))
        .onHover { hovering = $0 }
        .accessibilityLabel("\(server.name), in \(server.harnesses.joined(separator: ", "))")
    }
}

private struct Cell: View {
    var server: McpServer
    var harness: String

    var body: some View {
        if let group = server.group(of: harness) {
            if server.groups.count > 1 {
                Text(variantLetter(group))
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(group == 0 ? Theme.textPrimary : Theme.warning)
                    .frame(width: 20, height: 20)
                    .background(Theme.subtle, in: .rect(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Theme.border, lineWidth: 1))
                    .help("Definition \(variantLetter(group))")
            } else {
                Image(systemName: "checkmark").font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
            }
        } else {
            Text("—").foregroundStyle(Theme.borderStrong).help("Not in \(harness)")
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
    var server: McpServer
    var harnesses: [String]
    var model: McpModel
    var reload: () async -> Void

    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var sourceGroup = 0
    @State private var chosen: Set<String> = []
    @State private var preview: McpSyncReport?
    @State private var done: McpSyncReport?
    @State private var error: String?
    @State private var running = false
    @State private var confirmingOverwrite = false

    var source: String { server.groups[sourceGroup].first ?? "" }
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
            if !server.envKeys.isEmpty {
                Facts(label: "Env vars", value: server.envKeys.joined(separator: ", "))
            }
            if !server.headerKeys.isEmpty {
                Facts(label: "Headers", value: server.headerKeys.joined(separator: ", "))
            }
            Divider().overlay(Theme.border)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(server.groups.enumerated()), id: \.offset) { index, group in
                    Facts(label: server.groups.count > 1 ? "Definition \(variantLetter(index))" : "In",
                          value: group.joined(separator: ", "))
                }
                let missing = server.missing(from: harnesses)
                if !missing.isEmpty { Facts(label: "Missing from", value: missing.joined(separator: ", ")) }
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
            Text("\(chosen.intersection(targets.differing).sorted().joined(separator: ", ")) will get \(source)'s definition of \(server.name) instead of their own.")
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
                Picker("Copy from", selection: $sourceGroup) {
                    ForEach(Array(server.groups.enumerated()), id: \.offset) { index, group in
                        Text("Definition \(variantLetter(index)) (\(group.joined(separator: ", ")))").tag(index)
                    }
                }
                .onChange(of: sourceGroup) { resetChoice() }
            } else {
                Facts(label: "Copy from", value: source)
            }
            VStack(alignment: .leading, spacing: 6) {
                ForEach(targets.missing, id: \.self) { harness in
                    Toggle(harness, isOn: binding(harness))
                }
                ForEach(targets.differing, id: \.self) { harness in
                    Toggle(isOn: binding(harness)) {
                        Text("\(harness)  \(Text("replaces its own definition").foregroundStyle(Theme.textMuted))")
                    }
                }
            }
            .toggleStyle(.checkbox)
            .font(Theme.bodySmall)
        }
        if let report = done ?? preview {
            Label(report.sentence, systemImage: done != nil ? "checkmark.circle" : "eye")
                .font(Theme.caption).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let error {
            Text(error).font(Theme.caption).foregroundStyle(Theme.danger).fixedSize(horizontal: false, vertical: true)
        }
        HStack {
            if running { ProgressView().controlSize(.small) }
            Spacer()
            Button(done == nil ? "Cancel" : "Close", role: .cancel) { dismiss() }
                .buttonStyle(SecondaryButtonStyle())
            if done == nil && server.canSyncAlone {
                Button("Preview") { Task { await run(dryRun: true) } }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(chosen.isEmpty || running)
                Button("Sync") {
                    if targets.needsOverwrite(chosen) {
                        confirmingOverwrite = true
                    } else {
                        Task { await run(dryRun: false) }
                    }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(chosen.isEmpty || running)
            }
        }
    }

    private func binding(_ harness: String) -> Binding<Bool> {
        Binding(get: { chosen.contains(harness) }, set: { on in
            if on { chosen.insert(harness) } else { chosen.remove(harness) }
            preview = nil
        })
    }

    private func resetChoice() {
        chosen = Set(targets.missing)
        preview = nil
    }

    private func run(dryRun: Bool) async {
        running = true
        error = nil
        defer { running = false }
        let ordered = harnesses.filter { chosen.contains($0) }
        do {
            let report = try await app.swe.mcpSync(server: server, from: source, to: ordered,
                                                   overwrite: targets.needsOverwrite(chosen), dryRun: dryRun)
            if dryRun {
                preview = report
            } else {
                done = report
                await reload()
            }
        } catch {
            self.error = error.localizedDescription
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
