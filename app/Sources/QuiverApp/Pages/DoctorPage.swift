import QuiverCore
import SwiftUI

struct DoctorPage: View {
    @Environment(AppModel.self) private var app
    @State private var model: DoctorModel?
    /// The findings card folds past this many rows.
    private static let foldLimit = 8
    @State private var showsAll = false

    var body: some View {
        Page(title: "Doctor", subtitle: "Checks on PATH, Node and drift between quiver and your harnesses.") {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model?.load() } }
                .iconButton()
                .disabled(isLoading)
                .keyboardShortcut("r", modifiers: .command)
                .help("Run the checks again (⌘R)")
        } content: {
            switch model?.state ?? .idle {
            case .idle, .loading:
                LoadingCard(text: "Running checks…")
            case .failed(let message):
                ErrorCard(message: message)
            case .loaded(let report) where report.findings.isEmpty:
                EmptyCard(text: "Everything checks out.", icon: "checkmark.seal")
            case .loaded(let report):
                let shown = RowFold.visible(report.findings, limit: Self.foldLimit, expanded: showsAll)
                Card(title: report.findings.count == 1 ? "1 finding" : "\(report.findings.count) findings") {
                    ForEach(shown) { finding in
                        FindingRow(finding: finding)
                        if finding != shown.last { Divider().overlay(Theme.border) }
                    }
                    if RowFold.hiddenCount(total: report.findings.count, limit: Self.foldLimit) > 0 {
                        Divider().overlay(Theme.border)
                        ExpandRow(isExpanded: showsAll,
                                  hidden: RowFold.hiddenCount(total: report.findings.count, limit: Self.foldLimit),
                                  help: (expand: "Show every finding", collapse: "Show only the first \(Self.foldLimit)"),
                                  edge: .inset) {
                            withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsAll.toggle() }
                        }
                    }
                }
            }
        }
        .task(id: app.swePath) {
            let doctor = DoctorModel(swe: app.swe)
            model = doctor
            await doctor.load()
        }
    }

    private var isLoading: Bool {
        switch model?.state ?? .idle {
        case .idle, .loading: true
        case .loaded, .failed: false
        }
    }
}

private struct FindingRow: View {
    var finding: DoctorReport.Finding

    /// 12/16 mono, for check ids and fix commands.
    private static let mono = Font.system(size: 12, design: .monospaced)

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
                }
            }
        }
    }
}
