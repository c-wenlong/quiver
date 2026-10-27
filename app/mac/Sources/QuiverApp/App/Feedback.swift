import AppKit
import QuiverCore

/// Help › Report a Bug and Settings › Feedback: both open the same GitHub
/// forms and copy the same diagnostics.
@MainActor
enum Feedback {
    /// Opens the form in the browser with the versions filled in. swe's
    /// version is read first when nothing has asked for it yet, so the
    /// report never says "swe unknown" just because Settings was never open.
    static func openIssueForm(_ kind: IssueReport.Kind, app: AppModel) {
        Task {
            if !app.updates.sweVersionKnown { await app.updates.refresh(force: false) }
            NSWorkspace.shared.open(IssueReport.newIssueURL(
                kind, version: app.updates.versionLine, os: IssueReport.osLine()))
        }
    }

    static func diagnostics(app: AppModel) -> String {
        let report: DoctorReport? = if case .loaded(let report) = app.doctor.state { report } else { nil }
        return IssueReport.diagnostics(version: app.updates.versionLine, os: IssueReport.osLine(),
                                       doctor: report)
    }

    /// The menu item has no button to show progress on, so it runs the
    /// checks it is missing before it copies, and copies either way.
    static func copyDiagnostics(app: AppModel) {
        Task {
            if !app.updates.sweVersionKnown { await app.updates.refresh(force: false) }
            if case .loaded = app.doctor.state {} else if app.doctor.running.isEmpty {
                await app.doctor.load()
            }
            copyToPasteboard(diagnostics(app: app))
        }
    }
}
