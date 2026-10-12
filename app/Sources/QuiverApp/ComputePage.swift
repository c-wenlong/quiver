import QuiverCore
import SwiftUI

/// What the starred harnesses are running and what it costs the Mac,
/// sampled every three seconds while the page is open. Clean up suggests
/// orphans and hogs; nothing is quit without a confirmation.
struct ComputePage: View {
    @Environment(AppModel.self) private var app
    @State private var cleaning = false

    private var model: ComputeModel { app.compute }

    var body: some View {
        Page(title: "Compute",
             subtitle: "What your starred harnesses are running, and what it costs your Mac.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.loadMatchers(); await model.refresh() }
            }
            .iconButton()
            .help("Refresh")
            Button(model.cleanup.isEmpty ? "Clean up" : "Clean up (\(model.cleanup.count))") { cleaning = true }
                .primaryButton()
                .disabled(model.cleanup.isEmpty)
        } content: {
            Text(totalsLine)
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            if let error = model.error { ErrorCard(message: error) }
            if model.usages.isEmpty && model.orphans.isEmpty {
                EmptyCard(text: "Nothing a starred harness runs is up right now.", icon: "cpu")
            }
            ForEach(model.usages) { usage in
                HarnessCard(usage: usage, rows: model.processes[usage.harness] ?? [])
            }
            if !model.orphans.isEmpty { OrphansCard(rows: model.orphans) }
            if !model.notRunning.isEmpty {
                Text("Not running: \(model.notRunning.map(UsageFormat.harness).joined(separator: ", "))")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            limitsCard
        }
        // Sampling is this page's only: it ticks while the page is open and
        // stops when the view goes away.
        .task {
            await model.loadMatchers()
            while !Task.isCancelled {
                await model.refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
        .sheet(isPresented: $cleaning) { CleanupSheet(items: model.cleanup, model: model) }
    }

    private var totalsLine: String {
        let t = model.totals
        return "\(ComputeSummary.bytes(t.memoryBytes)) across \(t.processCount) processes "
            + "· CPU \(Self.cpu(t.cpuPercent))% · \(t.threads) threads"
    }

    private var limitsCard: some View {
        LimitsCard().padding(.top, 8)
    }

    /// Whole percent once it is two digits, one decimal below it.
    static func cpu(_ percent: Double) -> String {
        percent.formatted(.number.precision(.fractionLength(percent >= 10 ? 0 : 1)))
    }
}

/// The thresholds at the bottom of the page; changing one reflags the
/// current sample at once (ComputeModel.limits.didSet republishes).
private struct LimitsCard: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        SettingsCard {
            SettingRow(title: "Orphans count as old after",
                       detail: "Orphaned helpers older than this are suggested for clean-up.") {
                SelectMenu(options: orphanAgeOptions, selection: orphanAge, width: 120)
            }
            SettingsDivider()
            SettingRow(title: "Flag a process above",
                       detail: "A process using more memory than this is flagged, and can be suggested for clean-up.") {
                SelectMenu(options: memoryOptions, selection: memory, width: 120)
            }
        }
    }

    private var orphanAge: Binding<TimeInterval> {
        Binding(get: { app.compute.limits.orphanAge },
                set: { app.compute.limits.orphanAge = $0 })
    }

    private var memory: Binding<UInt64> {
        Binding(get: { app.compute.limits.memoryBytes },
                set: { app.compute.limits.memoryBytes = $0 })
    }

    private var orphanAgeOptions: [(value: TimeInterval, title: String)] {
        [(6 * 3600, "6h"), (24 * 3600, "24h"), (72 * 3600, "72h"), (7 * 86400, "7 days")]
    }

    private var memoryOptions: [(value: UInt64, title: String)] {
        [(UInt64(1) << 30, "1 GB"), (UInt64(2) << 30, "2 GB"),
         (UInt64(4) << 30, "4 GB"), (UInt64(8) << 30, "8 GB")]
    }
}

// MARK: - Harness cards

/// One running starred harness: name, totals, and a disclosure with a row
/// per process. The rows carry their own Quit menu; quitting the harness's
/// root asks first, since that closes the app or CLI itself.
private struct HarnessCard: View {
    var usage: HarnessUsage
    var rows: [ComputeModel.ProcessRow]
    @State private var expanded = true

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() }
                } label: {
                    header
                }
                .plainButton()
                if expanded {
                    VStack(spacing: 0) {
                        ForEach(rows) { row in
                            ProcessRowView(row: row, harness: usage.harness)
                            if row.id != rows.last?.id { Divider().overlay(Theme.border) }
                        }
                    }
                    .padding(.top, 10)
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            HarnessIcon(harness: usage.harness, size: 20)
            Text(UsageFormat.harness(usage.harness))
                .font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 8)
            Text("\(ComputeSummary.bytes(usage.memoryBytes)) · CPU \(ComputePage.cpu(usage.cpuPercent))% "
                 + "· \(usage.processCount) \(usage.processCount == 1 ? "process" : "processes") "
                 + "· \(usage.threads) threads · running for \(ComputeSummary.duration(usage.runningSince.distance(to: .now)))")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                .lineLimit(1).truncationMode(.tail)
            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textMuted)
        }
        .contentShape(.rect)
    }
}

private struct ProcessRowView: View {
    @Environment(AppModel.self) private var app
    var row: ComputeModel.ProcessRow
    var harness: String
    @State private var confirmingQuit = false
    @State private var confirmingForce = false

    private var limit: UInt64 { app.compute.limits.memoryBytes }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(row.sample.name)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                    if row.flags.contains(.overMemory) { Badge("Over \(ComputeSummary.bytes(limit))") }
                    if row.flags.contains(.busy) { Badge("Busy") }
                }
                Text("PID \(row.sample.pid) · \(row.sample.threads) threads · running for "
                     + ComputeSummary.duration(row.sample.startedAt.distance(to: .now)))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
            }
            Spacer(minLength: 8)
            Text(ComputeSummary.bytes(row.sample.memoryBytes))
                .font(Theme.caption).foregroundStyle(Theme.textPrimary).monospacedDigit()
            Text(row.cpuPercent.map { "\(ComputePage.cpu($0))%" } ?? "–")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                .frame(minWidth: 44, alignment: .trailing)
            Menu {
                Button("Quit") { confirmingQuit = true }
                if row.stillRunning {
                    Button("Force quit", role: .destructive) { confirmingForce = true }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 24, height: 24)
                    .contentShape(.rect)
            }
            .menuIndicator(.hidden)
            .menuStyle(.borderlessButton)
        }
        .help(row.sample.commandLine)
        .alert(row.isMain ? "Quit \(UsageFormat.harness(harness))?" : "Quit \(row.sample.name)?",
               isPresented: $confirmingQuit) {
            Button("Quit", role: .destructive) { quit(force: false) }
            Button("Cancel", role: .cancel) {}
        } message: {
            if row.isMain { Text("Its open windows close.") }
        }
        .alert("Force quit \(row.sample.name)?", isPresented: $confirmingForce) {
            Button("Force quit", role: .destructive) { quit(force: true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It stops at once without saving.")
        }
    }

    private func quit(force: Bool) {
        let item = CleanupItem.process(row.sample, harness: harness, isMain: row.isMain)
        Task { await app.compute.quit(item, force: force) }
    }
}

// MARK: - Orphans

private struct OrphansCard: View {
    var rows: [ComputeModel.OrphanRow]

    var body: some View {
        Card(title: "Orphaned",
             subtitle: "Processes whose parent has exited, usually dev servers or MCP servers an agent started and left behind.") {
            VStack(spacing: 0) {
                ForEach(rows) { row in
                    OrphanRowView(row: row)
                    if row.id != rows.last?.id { Divider().overlay(Theme.border) }
                }
            }
        }
    }
}

private struct OrphanRowView: View {
    @Environment(AppModel.self) private var app
    var row: ComputeModel.OrphanRow
    @State private var confirmingQuit = false
    @State private var confirmingForce = false

    private var limit: UInt64 { app.compute.limits.memoryBytes }
    private var root: ProcessSample { row.tree.root }

    /// "npm run studio": argv[0]'s basename and up to two more words.
    private var command: String {
        guard !root.arguments.isEmpty else { return root.name }
        var words = [(root.arguments[0] as NSString).lastPathComponent]
        words += root.arguments.dropFirst().prefix(2)
        return words.joined(separator: " ")
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(command)
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                    if row.flags.contains(.old) { Badge("Old") }
                    if row.flags.contains(.overMemory) { Badge("Over \(ComputeSummary.bytes(limit))") }
                }
                Text(([cwdFolder, "PID \(root.pid)",
                       "running for \(ComputeSummary.duration(root.startedAt.distance(to: .now)))"] as [String?])
                    .compactMap { $0 }.joined(separator: " · "))
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
            }
            Spacer(minLength: 8)
            Text(ComputeSummary.bytes(row.tree.memoryBytes))
                .font(Theme.caption).foregroundStyle(Theme.textPrimary).monospacedDigit()
            Button("Quit") { confirmingQuit = true }
                .secondaryButton()
            if row.stillRunning {
                Button("Force quit", role: .destructive) { confirmingForce = true }
                    .secondaryButton()
            }
        }
        .padding(.vertical, 10)
        .help(root.commandLine)
        .alert("Quit \(command)?", isPresented: $confirmingQuit) {
            Button("Quit", role: .destructive) { quit(force: false) }
            Button("Cancel", role: .cancel) {}
        } message: {
            let n = row.tree.descendants.count
            Text(n == 0 ? "It gets a quit signal."
                 : "It and its \(n) child process\(n == 1 ? "" : "es") get a quit signal.")
        }
        .alert("Force quit \(command)?", isPresented: $confirmingForce) {
            Button("Force quit", role: .destructive) { quit(force: true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It stops at once without saving.")
        }
    }

    private func quit(force: Bool) {
        Task { await app.compute.quit(.orphanTree(row.tree), force: force) }
    }

    private var cwdFolder: String? {
        guard let cwd = root.cwd else { return nil }
        let last = (cwd as NSString).lastPathComponent
        return last.isEmpty ? nil : "in \(last)"
    }
}

// MARK: - Clean up

/// The suggested quits: each candidate with a checkbox, ticked the way the
/// rules marked it. Results land inline after Quit selected runs.
private struct CleanupSheet: View {
    var items: [CleanupItem]
    var model: ComputeModel
    @Environment(\.dismiss) private var dismiss
    @State private var selected: Set<String> = []
    /// Ids whose quits are being given their five seconds to take.
    @State private var quitting: Set<String> = []
    /// item id -> true when the process is still there after the quit.
    @State private var stillThere: [String: Bool] = [:]
    @State private var working = false

    init(items: [CleanupItem], model: ComputeModel) {
        self.items = items
        self.model = model
        _selected = State(initialValue: Set(items.filter(\.preselected).map(\.id)))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Clean up").font(Theme.sectionTitle).tracking(-0.5)
                    .foregroundStyle(Theme.textPrimary)
                Text("Suggested by what a process costs or how long it has been orphaned. Nothing is quit until you confirm.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(items) { item in
                        CleanupRow(item: item, isOn: selected.contains(item.id),
                                   quitting: quitting.contains(item.id),
                                   stillThere: stillThere[item.id]) {
                            if selected.contains(item.id) { selected.remove(item.id) }
                            else { selected.insert(item.id) }
                        } force: {
                            Task { await forceQuit(item) }
                        }
                        if item.id != items.last?.id { Divider().overlay(Theme.border) }
                    }
                }
                .padding(.horizontal, 20).padding(.vertical, 4)
                .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
            }
            .frame(maxHeight: 320)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .secondaryButton()
                    .keyboardShortcut(.cancelAction)
                Button(working ? "Quitting…"
                       : selected.isEmpty ? "Quit selected" : "Quit selected (\(selected.count))") {
                    Task { await quitSelected() }
                }
                .primaryButton()
                .disabled(working || selected.isEmpty)
            }
        }
        .padding(24)
        .frame(width: 560)
        .background(Theme.panel)
    }

    private func quitSelected() async {
        working = true
        quitting = selected
        for item in items where quitting.contains(item.id) {
            await model.quit(item)
        }
        // SIGTERM is not instant; give it five seconds and a fresh sample
        // before calling anything still running.
        try? await Task.sleep(for: .seconds(5))
        await model.refresh()
        for item in items where quitting.contains(item.id) {
            stillThere[item.id] = !model.alivePids(of: item).isEmpty
        }
        quitting = []
        selected = []
        working = false
    }

    private func forceQuit(_ item: CleanupItem) async {
        await model.quit(item, force: true)
        stillThere[item.id] = !model.alivePids(of: item).isEmpty
    }
}

private struct CleanupRow: View {
    var item: CleanupItem
    var isOn: Bool
    var quitting: Bool
    var stillThere: Bool?
    var toggle: () -> Void
    var force: () -> Void

    /// What it is: the process name, or the orphan root's short command.
    private var name: String {
        switch item.kind {
        case .orphanTree(let tree):
            guard !tree.root.arguments.isEmpty else { return tree.root.name }
            var words = [(tree.root.arguments[0] as NSString).lastPathComponent]
            words += tree.root.arguments.dropFirst().prefix(2)
            return words.joined(separator: " ")
        case .process(let sample, let harness):
            return "\(sample.name) (\(UsageFormat.harness(harness)))"
        }
    }

    private var memory: UInt64 {
        switch item.kind {
        case .orphanTree(let tree): tree.memoryBytes
        case .process(let sample, _): sample.memoryBytes
        }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            if let stillThere {
                // After Quit selected ran, the checkbox gives way to the result.
                Text(stillThere ? "Still running" : "Quit")
                    .font(Theme.caption)
                    .foregroundStyle(stillThere ? Theme.warningText : Theme.textSecondary)
                    .frame(width: 76, alignment: .leading)
            } else if quitting {
                Text("Quitting…")
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 76, alignment: .leading)
            } else {
                Toggle(isOn: Binding(get: { isOn }, set: { _ in toggle() })) { EmptyView() }
                    .labelsHidden()
                    .frame(width: 20)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Text(item.reason).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            Text(ComputeSummary.bytes(memory))
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
            if stillThere == true {
                Button("Force quit", role: .destructive, action: force)
                    .secondaryButton()
            }
        }
        .padding(.vertical, 10)
    }
}
