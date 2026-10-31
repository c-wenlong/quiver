import QuiverCore
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
        return Card(title: findings.count == 1 ? "1 finding" : "\(findings.count) findings",
                    accessory: { refreshButton }) {
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
                if let fix = finding.fix {
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
            }
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
