import QuiverCore
import SwiftUI

/// What the starred harnesses are running and what it costs the Mac,
/// sampled every three seconds while the page is open. Clean up suggests
/// orphans and hogs; nothing is quit without a confirmation.
struct ComputePage: View {
    @Environment(AppModel.self) private var app
    @State private var showingSettings = false
    @State private var cleaning = false
    /// The list as it was when the sheet opened: the page refreshes
    /// every three seconds, and the list must not move under the user.
    @State private var cleanupSnapshot: [CleanupItem] = []

    private var model: ComputeModel { app.compute }

    var body: some View {
        Page(title: "Compute",
             subtitle: "What your starred harnesses are running, and what it costs your Mac.") {
            Button("Refresh", systemImage: "arrow.clockwise") {
                Task { await model.loadMatchers(); await model.refresh() }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Refresh (⌘R)")
            FanButton()
            Button("Settings", systemImage: "gearshape") { showingSettings = true }
                .iconButton()
                .help("Settings")
                .popover(isPresented: $showingSettings, arrowEdge: .bottom) {
                    CleanupLimitsView()
                        .padding(16)
                        .frame(width: 440)
                }
            Button {
                cleanupSnapshot = model.cleanup
                cleaning = true
            } label: {
                Label { Text("Clean up") } icon: { BroomIcon() }
            }
            .iconButton()
            .disabled(model.cleanup.isEmpty)
            .help(model.cleanup.isEmpty ? "Clean up" : "Clean up (\(model.cleanup.count))")
            .overlay(alignment: .topTrailing) {
                if !model.cleanup.isEmpty {
                    Text("\(model.cleanup.count)")
                        .font(Theme.font(size: 10, weight: .medium))
                        .monospacedDigit()
                        .foregroundStyle(Theme.textLabel)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(Theme.subtle, in: .capsule)
                        .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
                        .offset(x: 6, y: -6)
                        .allowsHitTesting(false)
                }
            }
            .quiverSheet(isPresented: $cleaning) {
                CleanupSheet(items: cleanupSnapshot, model: model)
            }
        } content: {
            Text(totalsLine)
                .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            TrendCard()
            if let error = model.error { ErrorCard(message: error) }
            if model.usages.isEmpty && model.orphans.isEmpty {
                // Only a successful read of the stars can say there are none.
                if model.matchersLoaded && model.matchers.isEmpty {
                    EmptyCard(text: "Nothing is starred. Star a harness to watch what it runs.",
                              icon: "cpu",
                              actionTitle: "Open Harnesses") { app.selection = .harnesses }
                } else {
                    EmptyCard(text: "Nothing a starred harness runs is up right now.", icon: "cpu")
                }
            }
            ForEach(model.usages) { usage in
                HarnessCard(usage: usage, rows: model.processes[usage.harness] ?? [])
            }
            if !model.orphans.isEmpty { OrphansCard(rows: model.orphans) }
            ForEach(model.notRunning, id: \.self) { harness in
                IdleHarnessCard(harness: harness)
            }
        }
        // Sampling is this page's only: it ticks while the page is open and
        // stops when the view goes away.
        .task {
            await model.loadMatchers()
            while !Task.isCancelled {
                await model.refresh()
                await app.fans.refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    private var totalsLine: String { ComputeSummary.totalsLine(model.totals) }

    /// Whole percent once it is two digits, one decimal below it.
    static func cpu(_ percent: Double) -> String {
        ComputeSummary.percent(percent)
    }
}

// MARK: - Trends

/// RAM, CPU, GPU and the whole Mac's temperatures over time behind tabs:
/// one line per harness that has gone above the page's flag threshold,
/// or one per sensor component on the Temperature tab. Points come from
/// ComputeModel's trend sampler, which ticks whether the page is open or not.
private struct TrendCard: View {
    @Environment(AppModel.self) private var app
    /// A TrendMetric's raw value.
    @State private var tab = UserDefaults.standard.string(forKey: Self.tabKey) ?? TrendMetric.ram.rawValue
    @State private var hovered: Date?

    private static let tabKey = "computeTrendTab"

    private var model: ComputeModel { app.compute }

    /// The harnesses the card draws: above the flag threshold at any
    /// retained point, the same set on every tab.
    private var shown: [String] { model.history.shown(above: model.limits.memoryBytes) }

    private var tabSelection: Binding<String> {
        Binding(get: { tab },
                set: { tab = $0; UserDefaults.standard.set($0, forKey: Self.tabKey) })
    }

    private var interval: Binding<TrendInterval> {
        Binding(get: { model.trendInterval }, set: { model.trendInterval = $0 })
    }

    private var tabOptions: [(value: String, title: String)] {
        TrendMetric.allCases.map { ($0.rawValue, $0.title) }
    }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    Tabs(options: tabOptions, selection: tabSelection, style: .segmented)
                    Spacer(minLength: 8)
                    SelectMenu(options: TrendInterval.allCases.map { ($0, $0.title) },
                               selection: interval, width: 150)
                }
                let metric = TrendMetric(rawValue: tab) ?? .ram
                if metric == .temperature {
                    temperature
                } else if model.history.isEmpty {
                    placeholder("Collecting, the first points arrive within \(model.trendInterval.span).")
                } else if metric == .gpu && !model.gpuAvailable {
                    placeholder("GPU time isn't available on this Mac.")
                } else if shown.isEmpty {
                    placeholder("No harness has gone above "
                                + ComputeSummary.bytes(model.limits.memoryBytes) + " yet.")
                } else {
                    TrendChart(metric: metric, harnesses: shown, hovered: $hovered)
                }
            }
        }
    }

    @ViewBuilder private var temperature: some View {
        if !model.history.temperatureWindow(interval: model.trendInterval).isEmpty {
            TemperatureChart(hovered: $hovered)
        } else if model.history.lastTick == nil {
            placeholder("Collecting, the first points arrive within \(model.trendInterval.span).")
        } else {
            placeholder("Temperature sensors aren't available on this Mac.")
        }
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .frame(maxWidth: .infinity, minHeight: 180)
    }
}

/// The thresholds, behind the header's gear; changing one reflags the
/// current sample at once (ComputeModel.limits.didSet republishes).
private struct CleanupLimitsView: View {
    @Environment(AppModel.self) private var app
    @State private var ageValue: Int
    @State private var ageUnit: OrphanAgeUnit
    /// Lets the field write on focus loss as well as on Return.
    @FocusState private var ageFocused: Bool

    init() {
        let (value, unit) = OrphanAgeUnit.split(24 * 3600)
        _ageValue = State(initialValue: value)
        _ageUnit = State(initialValue: unit)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            SettingRow(title: "Orphans count as old after",
                       detail: "Orphans and duplicate servers older than this are suggested for clean-up.") {
                HStack(spacing: 6) {
                    TextField("", value: $ageValue, format: .number)
                        .quiverField()
                        .frame(width: 56)
                        .multilineTextAlignment(.trailing)
                        .focused($ageFocused)
                        .onSubmit(commitAge)
                        .onChange(of: ageFocused) { _, focused in
                            if !focused { commitAge() }
                        }
                    SelectMenu(options: unitOptions, selection: unit, width: 84)
                }
            }
            SettingsDivider()
            SettingRow(title: "Flag a process above",
                       detail: "A process using more memory than this is flagged, and can be suggested for clean-up.") {
                SelectMenu(options: memoryOptions, selection: memory, width: 120)
            }
            SettingsDivider()
            SettingRow(title: "Auto clean",
                       detail: "Orphans older than the age above get a quit signal, without asking.") {
                SettingSwitch(title: "Auto clean", isOn: autoClean)
            }
            if app.compute.limits.autoClean {
                SettingsDivider()
                SettingRow(title: "Force quit",
                           detail: "An orphan still running \(Int(ComputeModel.autoCleanGrace)) seconds "
                               + "after its quit signal is force quit.") {
                    SettingSwitch(title: "Force quit", isOn: autoCleanForce)
                }
            }
        }
        .onAppear {
            let (value, unit) = OrphanAgeUnit.split(app.compute.limits.orphanAge)
            ageValue = value
            ageUnit = unit
        }
        .onDisappear(perform: commitAge)
    }

    /// The field only writes on Return, focus loss or the popover
    /// closing, never per keystroke. Zero or less snaps back to what is
    /// saved; anything else clamps into the range. A valid entry stays as
    /// typed, so "60" then picking Hour means 60 hours, not 1.
    private func commitAge() {
        guard let seconds = OrphanAgeUnit.resolve(value: ageValue, unit: ageUnit) else {
            (ageValue, ageUnit) = OrphanAgeUnit.split(app.compute.limits.orphanAge)
            return
        }
        if seconds != app.compute.limits.orphanAge { app.compute.limits.orphanAge = seconds }
        if seconds != TimeInterval(ageValue) * ageUnit.seconds {
            (ageValue, ageUnit) = OrphanAgeUnit.split(seconds)
        }
    }

    private var unit: Binding<OrphanAgeUnit> {
        Binding(get: { ageUnit },
                set: {
                    ageUnit = $0
                    commitAge()
                })
    }

    private var unitOptions: [(value: OrphanAgeUnit, title: String)] {
        OrphanAgeUnit.allCases.map { ($0, $0.title) }
    }

    private var memory: Binding<UInt64> {
        Binding(get: { app.compute.limits.memoryBytes },
                set: { app.compute.limits.memoryBytes = $0 })
    }

    private var autoClean: Binding<Bool> {
        Binding(get: { app.compute.limits.autoClean },
                set: { app.compute.limits.autoClean = $0 })
    }

    private var autoCleanForce: Binding<Bool> {
        Binding(get: { app.compute.limits.autoCleanForce },
                set: { app.compute.limits.autoCleanForce = $0 })
    }

    private var memoryOptions: [(value: UInt64, title: String)] {
        [(UInt64(1) << 30, "1 GB"), (UInt64(2) << 30, "2 GB"),
         (UInt64(4) << 30, "4 GB"), (UInt64(8) << 30, "8 GB")]
    }
}

// MARK: - Harness cards

/// One running starred harness: name, totals, and a disclosure with a row
/// per process. The rows carry their own Quit menu; the footer's Quit all
/// quits the whole set at once. Quitting the harness's root asks first,
/// since that closes the app or CLI itself.
private struct HarnessCard: View {
    static let visibleRows = 5

    @Environment(AppModel.self) private var app
    var usage: HarnessUsage
    var rows: [ComputeModel.ProcessRow]
    @State private var expanded = true
    @State private var showsAll = false
    @State private var confirmingQuitAll = false

    /// Rows are already sorted by memory, largest first: the first five,
    /// or all of them once Show more opens.
    private var shown: [ComputeModel.ProcessRow] {
        RowFold.visible(rows, limit: Self.visibleRows, expanded: showsAll)
    }

    /// "N flagged" for the expand row while a busy or over-limit process
    /// is hidden away.
    private var hiddenNote: String? {
        let flagged = RowFold.hiddenFlagged(rows, limit: Self.visibleRows,
                                            expanded: showsAll) { !$0.flags.isEmpty }
        return flagged == 0 ? nil : "\(flagged) flagged"
    }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 0) {
                Button {
                    withAnimation(.easeOut(duration: Theme.fadeDuration)) { expanded.toggle() }
                } label: {
                    header
                }
                .plainButton()
                if expanded {
                    VStack(spacing: 0) {
                        ForEach(shown) { row in
                            ProcessRowView(row: row, harness: usage.harness)
                            if row.id != shown.last?.id { Divider().overlay(Theme.border) }
                        }
                        if RowFold.hiddenCount(total: rows.count, limit: Self.visibleRows) > 0 {
                            Divider().overlay(Theme.border)
                            ExpandRow(isExpanded: showsAll,
                                      hidden: RowFold.hiddenCount(total: rows.count, limit: Self.visibleRows),
                                      help: (expand: "Show every process",
                                             collapse: "Show the top \(Self.visibleRows) by memory"),
                                      edge: .inset, note: hiddenNote) {
                                withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                            }
                        }
                        if !rows.isEmpty {
                            Divider().overlay(Theme.border)
                            HStack {
                                Spacer(minLength: 8)
                                Button("Quit all") { confirmingQuitAll = true }
                                    .secondaryButton()
                                    .help("Quit every process \(UsageFormat.harness(usage.harness)) runs")
                            }
                            .padding(.top, 10)
                        }
                    }
                    .padding(.top, 10)
                }
            }
        }
        .alert("Quit all \(UsageFormat.harness(usage.harness)) processes?",
               isPresented: $confirmingQuitAll) {
            Button("Quit all", role: .destructive) { quitAll() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Its \(rows.count) \(rows.count == 1 ? "process gets" : "processes get") a quit signal."
                 + (rows.contains(where: \.isMain) ? " Its open windows close." : ""))
        }
    }

    private func quitAll() {
        Task { await app.compute.quitHarness(usage.harness) }
    }

    private var header: some View {
        HStack(spacing: 10) {
            HarnessIcon(harness: usage.harness, size: 20)
            Text(UsageFormat.harness(usage.harness))
                .font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
            Spacer(minLength: 8)
            Text("\(ComputeSummary.bytes(usage.memoryBytes)) · CPU \(ComputePage.cpu(usage.cpuPercent))% "
                 + "· \(usage.processCount) \(usage.processCount == 1 ? "process" : "processes") "
                 + "· \(ComputeSummary.threads(usage.threads)) · running for \(ComputeSummary.duration(usage.runningSince.distance(to: .now)))")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                .lineLimit(1).truncationMode(.tail)
            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                .font(Theme.font(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textMuted)
        }
        .contentShape(.rect)
    }
}

/// A starred harness with nothing running: the same card shape as a
/// running one, the header's figures replaced by a muted "Not running".
/// No processes, so no disclosure chevron and no Show more row.
private struct IdleHarnessCard: View {
    var harness: String

    var body: some View {
        Card {
            HStack(spacing: 10) {
                HarnessIcon(harness: harness, size: 20)
                Text(UsageFormat.harness(harness))
                    .font(Theme.cardTitle).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 8)
                Text("Not running")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
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
                Text("PID \(row.sample.pid) · \(ComputeSummary.threads(row.sample.threads)) · running for "
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
                    .font(Theme.font(size: 12))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 24, height: 24)
                    .contentShape(.rect)
            }
            .menuIndicator(.hidden)
            .menuStyle(.borderlessButton)
            .accessibilityLabel("More for \(row.sample.name)")
        }
        // The web app's 9px: without it each name sat on the hairline
        // above, where orphan rows (10pt) had room.
        .padding(.vertical, 9)
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
    /// Rows before the card folds behind a Show more row.
    private static let foldLimit = 8
    @State private var showsAll = false

    var body: some View {
        let shown = RowFold.visible(rows, limit: Self.foldLimit, expanded: showsAll)
        Card(title: "Orphaned",
             subtitle: "Processes whose parent has exited, usually dev servers or MCP servers an agent started and left behind.") {
            VStack(spacing: 0) {
                ForEach(shown) { row in
                    OrphanRowView(row: row)
                    if row.id != shown.last?.id { Divider().overlay(Theme.border) }
                }
                if RowFold.hiddenCount(total: rows.count, limit: Self.foldLimit) > 0 {
                    Divider().overlay(Theme.border)
                    ExpandRow(isExpanded: showsAll,
                              hidden: RowFold.hiddenCount(total: rows.count, limit: Self.foldLimit),
                              help: (expand: "Show every orphaned process",
                                     collapse: "Show only the first \(Self.foldLimit)"),
                              edge: .inset) {
                        withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                    }
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

/// One group in the sheet: Orphaned first, then each harness in the
/// matchers' order. The switch ticks every row in the group at once;
/// the header's chevron fold keeps long lists scannable.
private struct CleanupSheet: View {
    var items: [CleanupItem]
    var model: ComputeModel
    @Environment(\.dismissSheet) private var dismiss
    @State private var selected: Set<String>
    /// Ids whose quits are being given their five seconds to take.
    @State private var quitting: Set<String> = []
    /// item id -> true when the process is still there after the quit.
    @State private var stillThere: [String: Bool] = [:]
    @State private var working = false
    /// Groups start collapsed; a key is the harness id, or "orphaned".
    @State private var expanded: Set<String> = []

    init(items: [CleanupItem], model: ComputeModel) {
        self.items = items
        self.model = model
        _selected = State(initialValue: Set(items.filter(\.preselected).map(\.id)))
    }

    private struct Group: Identifiable {
        var id: String
        var harness: String?
        var items: [CleanupItem]
    }

    /// Groups in the items' own order (ComputeSummary.cleanup already
    /// sorts orphans first, then swe's order), so every selected item is
    /// always in a visible group.
    private var groups: [Group] {
        var out: [Group] = []
        for item in items {
            let id = item.harness ?? "orphaned"
            if let last = out.indices.last, out[last].id == id {
                out[last].items.append(item)
            } else {
                out.append(Group(id: id, harness: item.harness, items: [item]))
            }
        }
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Clean up").font(Theme.sectionTitle).tracking(-0.5)
                    .foregroundStyle(Theme.textPrimary)
                Text("Orphaned processes and leftovers the rules found. Nothing is quit until you confirm.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ScrollView {
                VStack(spacing: 8) {
                    ForEach(groups) { group in
                        groupCard(group)
                    }
                }
            }
            .scrollIndicators(.automatic)
            .frame(minHeight: 160, maxHeight: 480)
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
        .padding(20)
        .frame(width: 560)
        .background(Theme.panel)
    }

    private func groupCard(_ group: Group) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            groupHeader(group)
            if expanded.contains(group.id) {
                VStack(spacing: 0) {
                    ForEach(group.items) { item in
                        CleanupRow(item: item,
                                   isOn: selected.contains(item.id),
                                   quitting: quitting.contains(item.id),
                                   stillThere: stillThere[item.id]) {
                            if !working {
                                if selected.contains(item.id) { selected.remove(item.id) }
                                else { selected.insert(item.id) }
                            }
                        } force: {
                            Task { await forceQuit(item) }
                        }
                        if item.id != group.items.last?.id { Divider().overlay(Theme.border) }
                    }
                }
                .padding(.leading, 26)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }

    /// "N" normally, "k of N" when the group is only partly ticked.
    private func groupCountLabel(_ group: Group) -> String {
        let on = group.items.count { selected.contains($0.id) }
        return on == 0 || on == group.items.count
            ? "\(group.items.count)" : "\(on) of \(group.items.count)"
    }

    private func groupHeader(_ group: Group) -> some View {
        HStack(spacing: 8) {
            Button {
                withAnimation(.easeOut(duration: Theme.fadeDuration)) {
                    if expanded.contains(group.id) { expanded.remove(group.id) }
                    else { expanded.insert(group.id) }
                }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: expanded.contains(group.id) ? "chevron.down" : "chevron.right")
                        .font(Theme.font(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.textMuted)
                    if let harness = group.harness {
                        HarnessIcon(harness: harness, size: 16)
                    } else {
                        Image(systemName: "circle.dashed")
                            .font(Theme.font(size: 16))
                            .foregroundStyle(Theme.textNav)
                    }
                    Text(group.harness.map(UsageFormat.harness) ?? "Orphaned")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    Text(groupCountLabel(group))
                        .font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                }
                .contentShape(.rect)
            }
            .plainButton()
            Spacer(minLength: 8)
            Toggle(isOn: Binding(
                get: { group.items.allSatisfy { selected.contains($0.id) } },
                set: { on in
                    for item in group.items {
                        if on { selected.insert(item.id) } else { selected.remove(item.id) }
                    }
                })) { EmptyView() }
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.mini)
                .disabled(working)
                .accessibilityLabel("Select every \(group.harness.map(UsageFormat.harness) ?? "orphaned") process")
        }
    }

    private func quitSelected() async {
        working = true
        quitting = selected
        await model.quit(items.filter { selected.contains($0.id) })
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
    /// The Quit / result column, so the toggle's replacement holds its place.
    static let statusWidth: CGFloat = 76

    /// Always inside a harness group, whose header already says whose it
    /// is: no icon and no "(Codex)" suffix on the row.
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
        case .process(let sample, _):
            return sample.name
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
                    .frame(width: Self.statusWidth, alignment: .leading)
            } else if quitting {
                Text("Quitting…")
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: Self.statusWidth, alignment: .leading)
            } else {
                Toggle(isOn: Binding(get: { isOn }, set: { _ in toggle() })) { EmptyView() }
                    .labelsHidden()
                    .frame(width: 20)
                    .accessibilityLabel("Select \(name)")
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
