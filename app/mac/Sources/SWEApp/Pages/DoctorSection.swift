import SWECore
import SwiftUI

/// Settings › Doctor: the `swe doctor` report, each finding with its fix
/// button. Refresh and retry live on the state cards, so they are there
/// whenever the section is.
struct DoctorSection: View {
    @Environment(AppModel.self) private var app
    /// The findings card folds past this many rows.
    private static let foldLimit = 8
    /// ⌘R re-runs the checks on every state card, like the old page header.
    private static let refreshShortcut = KeyboardShortcut("r", modifiers: .command)
    @State private var showsAll = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let done = model.installed {
                installedCard(done, model: model)
            }
            if model.fixAllResult != nil || model.fixAllError != nil {
                fixAllCard
            }
            states
        }
        .task(id: app.swePath) {
            // An install in flight re-reads the report when it ends.
            if model.running.isEmpty { await model.load() }
        }
    }

    private var model: DoctorModel { app.doctor }

    /// An install that worked takes its finding with it, so what it printed
    /// shows here until dismissed.
    private func installedCard(_ done: (harness: String, output: String), model: DoctorModel) -> some View {
        Card(title: "Installed \(HarnessMark(done.harness).name)",
             accessory: {
                 Button("Dismiss", systemImage: "xmark") { model.dismissInstalled() }
                     .labelStyle(.iconOnly)
                     .iconButton()
                     .help("Hide this output")
             }) {
            if done.output.isEmpty {
                Text("swe install finished without printing anything.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            } else {
                InstallOutputView(text: DoctorModel.outputTail(done.output))
            }
        }
    }

    /// What Fix all did: how many findings it fixed, the fixes that
    /// failed, and how many are left for a person.
    private var fixAllCard: some View {
        Card(title: "Fix all",
             accessory: {
                 Button("Dismiss", systemImage: "xmark") { model.dismissFixAll() }
                     .labelStyle(.iconOnly)
                     .iconButton()
                     .hoverTip("Hide this summary")
             }) {
            VStack(alignment: .leading, spacing: 6) {
                if let error = model.fixAllError {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                }
                if let run = model.fixAllResult {
                    Text(Self.fixAllLine(run)).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    ForEach(Array(run.ran.enumerated()), id: \.offset) { _, step in
                        if let error = step.error {
                            Text("\(step.fix ?? step.action): \(error)")
                                .font(Theme.caption).foregroundStyle(Theme.dangerText)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// "Fixed 3 findings; 2 left for you below."
    static func fixAllLine(_ run: FixRun) -> String {
        guard !run.ran.isEmpty else { return "Nothing to fix here: what is left needs you." }
        let failed = run.ran.filter { $0.error != nil }.count
        let left = run.stillFailing.count
        var line = "Fixed \(run.fixed.count) finding\(run.fixed.count == 1 ? "" : "s")"
        if failed > 0 { line += ", \(failed) fix\(failed == 1 ? "" : "es") failed" }
        if left > 0 { line += "; \(left) left for you below" }
        return line + "."
    }

    private var states: some View {
        Group {
            switch model.state {
            case .idle, .loading:
                SkeletonRows(rows: 6, shape: .check, label: "Running checks…")
            case .failed(let message):
                ErrorCard(message: message, actionTitle: "Try again",
                          actionShortcut: Self.refreshShortcut, action: reload)
            case .loaded(let report) where report.findings.isEmpty
                    && app.issues.findings.isEmpty:
                EmptyCard(text: "Everything checks out.", icon: "checkmark.seal",
                          actionTitle: "Check again", actionShortcut: Self.refreshShortcut,
                          action: reload)
            case .loaded(let report):
                findingsCard(report)
            }
        }
    }

    private func reload() {
        Task { await model.load() }
    }

    private var isLoading: Bool {
        switch model.state {
        case .idle, .loading: true
        case .loaded, .failed: false
        }
    }

    private func findingsCard(_ report: DoctorReport) -> some View {
        // Live issue rows come first: a poll that just recorded a failure
        // shows here at once, before the next `swe doctor` run — which
        // reads the same store, so its copy of the row dedupes by check.
        var seen = Set<String>()
        let findings = (app.issues.findings + report.findings)
            .filter { seen.insert($0.check).inserted }
        let shown = RowFold.visible(findings, limit: Self.foldLimit, expanded: showsAll)
        let batch = DoctorModel.batchCount(report)
        return Card(title: findings.count == 1 ? "1 finding" : "\(findings.count) findings",
                    accessory: {
                        HStack(spacing: 8) {
                            if batch > 0 { fixAllButton(batch) }
                            refreshButton
                        }
                    }) {
            ForEach(shown) { finding in
                FindingRow(finding: finding, model: model)
                if finding != shown.last { Divider().overlay(Theme.border) }
            }
            if RowFold.hiddenCount(total: findings.count, limit: Self.foldLimit) > 0 {
                Divider().overlay(Theme.border)
                ExpandRow(isExpanded: showsAll,
                          hidden: RowFold.hiddenCount(total: findings.count, limit: Self.foldLimit),
                          help: (expand: "Show every finding", collapse: "Show only the first \(Self.foldLimit)"),
                          edge: .inset) {
                    withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                }
            }
        }
    }

    /// Runs `swe doctor --fix`: the local repairs, in order. Installs and
    /// fixes that delete or overwrite keep their own Run.
    private func fixAllButton(_ count: Int) -> some View {
        Group {
            if model.running.contains(DoctorModel.fixAllID) {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Fixing…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else {
                Button("Fix all (\(count))") {
                    Task {
                        await model.fixAll()
                        await app.others.load()
                        await app.skills.load(for: app.swePath)
                    }
                }
                .secondaryButton()
                .disabled(!model.running.isEmpty || isLoading)
                .hoverTip("Runs swe doctor --fix: setup, discover, init and plugin installs, in order. Installs and fixes that delete or overwrite wait for their own Run.")
            }
        }
    }

    private var refreshButton: some View {
        Button("Refresh", systemImage: "arrow.clockwise", action: reload)
            .iconButton()
            .disabled(isLoading)
            .keyboardShortcut(Self.refreshShortcut)
            .help("Run the checks again (⌘R)")
    }
}

private struct FindingRow: View {
    @Environment(AppModel.self) private var app
    var finding: DoctorReport.Finding
    @Bindable var model: DoctorModel

    /// The remedy a Run was clicked for and the agent remedy's picker.
    @State private var confirmingIndex: Int?
    @State private var agentIndex: Int?
    /// The picker's harness list, its pick, and the launch it followed.
    @State private var agentMachine: HubMachine?
    @State private var agentHarness: String?
    @State private var agentLoading = false
    @State private var agentError: String?
    @State private var agentNote: String?
    @State private var flow = IssueLaunchFlow()
    /// Kept so a row that leaves the page stops following the launch
    /// (the agent itself keeps running; there is just no chat to open).
    @State private var launchTask: Task<Void, Never>?

    /// 12/16 mono, for check ids and fix commands.
    private static var mono: Font { Theme.font(size: 12, design: .monospaced) }

    var color: Color {
        switch finding.severity {
        case .error: Theme.danger
        case .warning: Theme.warning
        case .info: Theme.textMuted
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(color).frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .top, spacing: 6) {
                    // A finding about one harness leads with its logo.
                    if let harness = finding.harness, HarnessIcon.exists(for: harness) {
                        HarnessIcon(harness: harness, size: 16).padding(.top, 1).help(HarnessMark(harness).name)
                    }
                    Text(finding.message).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                }
                Text(finding.check).font(Self.mono).foregroundStyle(Theme.textSecondary)
                // Where the failure lives, e.g. "declared in
                // ~/.claude/settings.json" (the web's source line).
                if let source = finding.source {
                    Text(source).font(Self.mono).foregroundStyle(Theme.textSecondary)
                }
                // A finding with remedies shows the box INSTEAD of the
                // fix strip (the web's `doctorRemedies || doctorLegacyFix`):
                // the finding's own action is mirrored as an action remedy.
                if !remedies.isEmpty {
                    remediesBlock
                } else if let fix = finding.fix {
                    HStack(spacing: 8) {
                        Text(fix)
                            .font(Self.mono)
                            .lineSpacing(2)
                            .foregroundStyle(Theme.textPrimary)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if finding.action != nil {
                            if model.running.contains(finding.id) {
                                ProgressView().controlSize(.small)
                                if finding.action == .install, let started = model.runStarted {
                                    // An installer can take minutes: count them.
                                    TimelineView(.periodic(from: started, by: 1)) { context in
                                        Text("Installing… \(Self.elapsed(from: started, to: context.date))")
                                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                            .monospacedDigit()
                                    }
                                } else {
                                    Text("Running…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                }
                            } else {
                                Button("Run") {
                                    Task {
                                        await model.run(finding)
                                        await app.others.load()
                                        await app.skills.load(for: app.swePath)
                                    }
                                }
                                .primaryButton()
                                .disabled(!model.running.isEmpty)
                                .hoverTip(finding.action == .install
                                          ? "Runs \(fix) here and shows what it prints" : "Runs \(fix)")
                            }
                            // A failed install may have stopped at a password
                            // prompt the app can't answer: offer a terminal.
                            if finding.action == .install, model.installOutput[finding.id] != nil,
                               !model.running.contains(finding.id) {
                                terminalButton(fix)
                            }
                        } else if fix.hasPrefix("swe ") {
                            terminalButton(fix)
                        } else if fix.hasPrefix("x-apple.systempreferences:"),
                                  let url = URL(string: fix) {
                            // A usage issue's permission fix is a settings
                            // pane, not a command.
                            Button("Open System Settings") { NSWorkspace.shared.open(url) }
                                .secondaryButton()
                        }
                        CopyButton(help: "Copy the command") { copyToPasteboard(fix) }
                            .iconButton()
                    }
                    .padding(.leading, 12)
                    .padding(.trailing, 6)
                    .padding(.vertical, 6)
                    .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
                    // bg.code is the card's own #171717 in dark; the hairline keeps the block's edge.
                    .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
                    .padding(.top, 4)
                    if let error = model.runErrors[finding.id] {
                        Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                            .padding(.leading, 12)
                    }
                    if let output = model.installOutput[finding.id], !output.isEmpty {
                        InstallOutputView(text: DoctorModel.outputTail(output))
                    }
                }
                if !remedies.isEmpty, let error = model.runErrors[finding.id] {
                    Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
                        .padding(.leading, 12)
                }
                if !remedies.isEmpty, let output = model.installOutput[finding.id], !output.isEmpty {
                    InstallOutputView(text: DoctorModel.outputTail(output))
                }
                if let idx = confirmingIndex, remedies.indices.contains(idx),
                   remedies[idx].kind == .action, let name = remedies[idx].knownAction {
                    confirmPanel(remedies[idx], action: name)
                }
                if let idx = agentIndex, remedies.indices.contains(idx),
                   remedies[idx].kind == .agent {
                    agentPanel(remedies[idx])
                }
                if let note = agentNote {
                    Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .padding(.leading, 12)
                }
            }
        }
        .onDisappear { launchTask?.cancel() }
    }

    private var remedies: [DoctorReport.Finding.Remedy] { finding.remedies ?? [] }

    /// The ways out, best first (the web's `.doctor-remedies`): one row
    /// per remedy with its own affordance.
    private var remediesBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(remedies.enumerated()), id: \.offset) { index, remedy in
                remedyRow(remedy, index: index)
                if index != remedies.count - 1 { Divider().overlay(Theme.border) }
            }
        }
        .padding(.leading, 12)
        .padding(.trailing, 6)
        .padding(.vertical, 6)
        .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
        .padding(.top, 4)
    }

    private func remedyRow(_ remedy: DoctorReport.Finding.Remedy, index: Int) -> some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(remedy.title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                if let command = remedy.command {
                    Text(command).font(Self.mono).foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                }
                if let detail = remedy.detail {
                    Text(detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                ForEach(remedy.steps ?? [], id: \.self) { step in
                    Text(step).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            Spacer(minLength: 0)
            switch remedy.kind {
            case .action:
                if let command = remedy.command {
                    CopyButton(help: "Copy the command") { copyToPasteboard(command) }
                        .iconButton()
                }
                if remedy.knownAction != nil {
                    if model.running.contains(finding.id) {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            if remedy.knownAction == .install, let started = model.runStarted {
                                // An installer can take minutes: count them.
                                TimelineView(.periodic(from: started, by: 1)) { context in
                                    Text("Installing… \(Self.elapsed(from: started, to: context.date))")
                                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                        .monospacedDigit()
                                }
                            } else {
                                Text("Running…").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            }
                        }
                    } else {
                        Button("Run") {
                            confirmingIndex = confirmingIndex == index ? nil : index
                        }
                        .primaryButton()
                        .disabled(!model.running.isEmpty)
                    }
                }
            case .agent:
                Button(remedy.title) {
                    agentIndex = agentIndex == index ? nil : index
                }
                .secondaryButton()
                .disabled(flow.busy)
            case .command:
                if let command = remedy.command {
                    CopyButton(help: "Copy the command") { copyToPasteboard(command) }
                        .iconButton()
                }
            case .steps, .other:
                EmptyView()
            }
        }
        .padding(.vertical, 4)
    }

    /// The action's what-will-happen line and the Cancel/Run pair, as the
    /// web's confirm strip renders them.
    private func confirmPanel(_ remedy: DoctorReport.Finding.Remedy,
                              action name: DoctorReport.Finding.Action) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(DoctorConfirm.text(action: name, finding: finding, remedy: remedy))
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button("Cancel") { confirmingIndex = nil }
                    .secondaryButton()
                Button("Run \(remedy.title)") {
                    confirmingIndex = nil
                    Task {
                        await model.run(finding, action: name)
                        await app.others.load()
                        await app.skills.load(for: app.swePath)
                    }
                }
                .primaryButton()
            }
        }
        .padding(.leading, 12)
        .padding(.top, 6)
    }

    /// The Fix-with-agent picker (the web's prompt preview and harness
    /// chips): Launch posts `/v1/sessions` on this Mac's hub with the
    /// remedy's prompt and follows it.
    private func agentPanel(_ remedy: DoctorReport.Finding.Remedy) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Launch an agent on this Mac. It gets this prompt:")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            Text(remedy.prompt ?? "")
                .font(Self.mono).foregroundStyle(Theme.textSecondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxHeight: 90)
            if agentLoading {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Asking the hub what it can start…")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            } else if let machine = agentMachine, let list = machine.launchable, !list.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 170))], spacing: 8) {
                    ForEach(list, id: \.self) { id in
                        HarnessTile(harness: id, isOn: agentHarness == id) { agentHarness = id }
                    }
                }
                if let pick = agentHarness, (machine.untrackedHarnesses ?? []).contains(pick) {
                    Text("SWE can't read \(machine.harnessNames?[pick] ?? HarnessMark(pick).name) sessions yet: it starts, but no chat opens.")
                        .font(Theme.caption).foregroundStyle(Theme.warningText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("No harness can start on this Mac: install one, or turn one on in Settings › Providers.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
            if let error = agentError {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            HStack(spacing: 8) {
                if let note = flow.note {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(note).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
                }
                Spacer(minLength: 0)
                Button("Cancel") { agentIndex = nil }
                    .secondaryButton()
                    .disabled(flow.busy)
                Button("Launch \(agentHarness.map { agentMachine?.harnessNames?[$0] ?? HarnessMark($0).name } ?? "")") {
                    launchTask = Task { await launch(remedy) }
                }
                .primaryButton()
                .disabled(flow.busy || agentHarness == nil || (remedy.prompt ?? "").isEmpty)
            }
        }
        .padding(.leading, 12)
        .padding(.top, 6)
        .task(id: agentIndex) { await loadAgentHarnesses() }
    }

    /// What the hub can start on this machine, for the chips; a failed
    /// read leaves the empty-picker row and its error.
    private func loadAgentHarnesses() async {
        agentLoading = true
        defer { agentLoading = false }
        agentError = nil
        let home = FileManager.default.homeDirectoryForCurrentUser
        let local = await offMain { IssueResolve.localMachineId(home: home) }
        do {
            let machines = try await app.hub.withClient { try await $0.machines() }
            agentMachine = machines.first { $0.id == local } ?? machines.first
            if agentHarness == nil || !(agentMachine?.launchable ?? []).contains(agentHarness ?? "") {
                agentHarness = agentMachine?.launchable?.first
            }
        } catch {
            agentError = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    /// `POST /v1/sessions` on this Mac's hub with the remedy's prompt,
    /// polled through `IssueLaunchFlow` like the New-session sheet's.
    private func launch(_ remedy: DoctorReport.Finding.Remedy) async {
        guard let harness = agentHarness, let prompt = remedy.prompt, !prompt.isEmpty else { return }
        agentError = nil
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let key = await flow.run(
            post: {
                try await app.hub.withClient {
                    try await $0.startSession(harness: harness, cwd: home, prompt: prompt)
                }
            },
            fetch: { id in try await app.hub.withClient { try await $0.launch(id) } })
        if let key {
            // A cancelled follow (the row left) still started the agent;
            // it just has no chat to open from here.
            guard !Task.isCancelled else { return }
            agentIndex = nil
            app.openSessionChat(key: key)
        } else if case .unconfirmed(let text) = flow.phase {
            // Untracked or queued: started, but there's no chat to open.
            agentIndex = nil
            agentNote = text
        } else if case .failed(let text) = flow.phase {
            agentError = text
        }
    }

    private func terminalButton(_ fix: String) -> some View {
        Button("Run in Terminal", systemImage: "terminal") {
            Task {
                do {
                    try await app.accounts.runInTerminal(
                        line: DoctorModel.terminalLine(
                            fix: fix,
                            swe: SweLocation.executable(override: app.swePath)))
                } catch {
                    model.noteError(error.localizedDescription, for: finding.id)
                }
            }
        }
        .secondaryButton()
        .hoverTip("Runs \(fix) in a new terminal")
    }

    /// 0:07, 1:42: how long the run in flight has taken.
    static func elapsed(from start: Date, to now: Date) -> String {
        let seconds = max(0, Int(now.timeIntervalSince(start)))
        return "\(seconds / 60):\(String(format: "%02d", seconds % 60))"
    }
}

/// What an install printed: mono, selectable, the end of it in view.
private struct InstallOutputView: View {
    var text: String

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Text(text)
                    .font(Theme.font(size: 11, design: .monospaced))
                    .foregroundStyle(Theme.textSecondary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .id("end")
            }
            // A ScrollView takes all the height it is offered: size it to
            // the lines it holds, up to about ten.
            .frame(height: min(160, CGFloat(text.split(separator: "\n", omittingEmptySubsequences: false).count) * 15 + 16))
            .background(Theme.code, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
            .onAppear { proxy.scrollTo("end", anchor: .bottom) }
        }
    }
}
