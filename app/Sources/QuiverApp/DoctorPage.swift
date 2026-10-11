import AppKit
import QuiverCore
import SwiftUI

struct DoctorPage: View {
    @Environment(AppModel.self) private var app
    @State private var model: DoctorModel?

    var body: some View {
        Page(title: "Doctor", subtitle: "Checks on PATH, Node and drift between quiver and your harnesses.") {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await model?.load() } }
                .iconButton()
                .help("Run the checks again")
        } content: {
            switch model?.state ?? .idle {
            case .idle, .loading:
                ProgressView().frame(maxWidth: .infinity)
            case .failed(let message):
                ErrorCard(message: message)
            case .loaded(let report) where report.findings.isEmpty:
                Card { Label("Everything checks out.", systemImage: "checkmark.seal").foregroundStyle(Theme.textPrimary) }
            case .loaded(let report):
                Card(title: report.findings.count == 1 ? "1 finding" : "\(report.findings.count) findings") {
                    ForEach(report.findings) { finding in
                        FindingRow(finding: finding)
                        if finding != report.findings.last { Divider().overlay(Theme.border) }
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
}

private struct FindingRow: View {
    var finding: DoctorReport.Finding

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
                Text(finding.message).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Text(finding.check).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                if let fix = finding.fix {
                    HStack {
                        Text(fix).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        Button("Copy", systemImage: "doc.on.doc") {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(fix, forType: .string)
                        }
                        .labelStyle(.iconOnly)
                        .plainButton(cornerRadius: 4)
                    }
                }
            }
        }
    }
}
