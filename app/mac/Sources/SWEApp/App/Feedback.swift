import AppKit
import SWECore

/// Help › Report a Bug and Settings › Feedback: both open the same GitHub
/// forms and copy the same diagnostics.
@MainActor
enum Feedback {
    /// Opens the form in the browser with the versions filled in. A bug
    /// report reads swe's version first when nothing has asked for it yet,
    /// so it never says "swe unknown" just because Settings was never open.
    static func openIssueForm(_ kind: IssueReport.Kind, app: AppModel) {
        Task {
            if kind == .bug, !app.updates.sweVersionKnown { await app.updates.refreshSweVersion() }
            NSWorkspace.shared.open(IssueReport.newIssueURL(
                kind, version: app.updates.versionLine, os: IssueReport.osLine()))
        }
    }

    /// What the Doctor card shows right now, live issue rows included.
    static func diagnostics(app: AppModel) -> String {
        let report: DoctorReport? = if case .loaded(let report) = app.doctor.state { report } else { nil }
        return IssueReport.diagnostics(
            version: app.updates.versionLine, os: IssueReport.osLine(),
            findings: IssueReport.merged(issues: app.issues.findings, report: report),
            accounts: accountNames(app: app))
    }

    /// Every name an account goes by in a check id or message: the profile's
    /// label, id and slug, and the label and key of each extra account an
    /// open issue is about. The harness half of a key stays.
    private static func accountNames(app: AppModel) -> [String] {
        var names: [String] = []
        for card in app.accounts.cards {
            let profile = card.profile
            names += [profile.name, profile.id]
            if let slug = profile.id.split(separator: "-", maxSplits: 1).dropFirst().first {
                names.append(String(slug))
            }
        }
        for issue in app.issues.open {
            if let account = issue.account { names.append(account) }
            if let key = issue.accountKey {
                names += key.split(separator: "/").map(String.init).filter { $0 != issue.harness }
            }
        }
        return names
    }

    /// The menu item has no button to show progress on, so it runs the
    /// checks when they have not run, and copies once they finish. It is
    /// disabled while checks or a fix are in flight.
    static func copyDiagnostics(app: AppModel) {
        Task {
            if !app.updates.sweVersionKnown { await app.updates.refreshSweVersion() }
            if case .loaded = app.doctor.state {} else { await app.doctor.load() }
            copyToPasteboard(diagnostics(app: app))
        }
    }
}
