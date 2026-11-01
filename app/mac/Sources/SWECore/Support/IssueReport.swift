import Foundation

/// Help › Report a Bug and Settings › Feedback: GitHub issue links with the
/// reporter's versions filled in, and the diagnostics text they can paste.
/// The query names are the field ids in .github/ISSUE_TEMPLATE, and `area`
/// is one of the forms' dropdown options word for word; `swe bug` builds the
/// same links (src/swe/bug.py).
public enum IssueReport {
    public static let repository = URL(string: "https://github.com/c-wenlong/swe")!
    public static let documentation = repository

    /// The forms' Area option for this app.
    public static let macArea = "Mac app"

    public enum Kind: Sendable {
        case bug
        case feature

        var template: String {
            switch self {
            case .bug: "bug_report.yml"
            case .feature: "feature_request.yml"
            }
        }
    }

    /// The new-issue form for `kind`, area picked. A bug also gets the
    /// version and OS fields; a feature request has neither.
    public static func newIssueURL(_ kind: Kind, version: String, os: String) -> URL {
        var items = [URLQueryItem(name: "template", value: kind.template),
                     URLQueryItem(name: "area", value: macArea)]
        if kind == .bug {
            items.append(URLQueryItem(name: "version", value: version))
            items.append(URLQueryItem(name: "os", value: os))
        }
        var parts = URLComponents(url: repository.appending(path: "issues/new"),
                                  resolvingAgainstBaseURL: false)!
        parts.queryItems = items
        // URLComponents leaves "+" bare, and GitHub reads a bare "+" in a
        // query as a space.
        parts.percentEncodedQuery = parts.percentEncodedQuery?
            .replacingOccurrences(of: "+", with: "%2B")
        return parts.url!
    }

    /// "macOS 26.1", with the patch number only when there is one.
    public static func osLine(_ version: OperatingSystemVersion
                              = ProcessInfo.processInfo.operatingSystemVersion) -> String {
        var line = "macOS \(version.majorVersion).\(version.minorVersion)"
        if version.patchVersion > 0 { line += ".\(version.patchVersion)" }
        return line
    }

    /// What Copy Diagnostics puts on the pasteboard: the versions and the
    /// findings the Doctor card shows, nil when Doctor has not run. The
    /// home folder becomes ~, the user name in any other path (Nix's
    /// /etc/profiles/per-user/<name>) becomes <user>, each account name in a
    /// check id or message becomes <account>, and email addresses are
    /// masked, so a paste into a public issue names no one.
    public static func diagnostics(version: String, os: String,
                                   findings: [DoctorReport.Finding]?,
                                   accounts: [String] = [],
                                   home: String = NSHomeDirectory(),
                                   user: String = NSUserName()) -> String {
        var lines = ["Version: \(version)", "OS: \(os)"]
        let mask = { (text: String) in redact(text, home: home, user: user, accounts: accounts) }
        switch findings {
        case nil:
            lines.append("swe doctor: not run yet")
        case let findings? where findings.isEmpty:
            lines.append("swe doctor: everything checks out")
        case let findings?:
            lines.append("swe doctor:")
            for finding in findings {
                lines.append("  \(finding.severity.rawValue) \(mask(finding.check)): \(mask(finding.message))")
            }
        }
        return lines.joined(separator: "\n")
    }

    /// The Doctor card's rows: live issue findings first, then the report's,
    /// one per check id, as `DoctorSection` lists them.
    public static func merged(issues: [DoctorReport.Finding],
                              report: DoctorReport?) -> [DoctorReport.Finding]? {
        guard let report else { return nil }
        var seen = Set<String>()
        return (issues + report.findings).filter { seen.insert($0.check).inserted }
    }

    static func redact(_ text: String, home: String, user: String = "",
                       accounts: [String] = []) -> String {
        var text = text
        // "/" or "" would turn every path into a ~.
        if home.count > 1 {
            let trimmed = home.hasSuffix("/") ? String(home.dropLast()) : home
            // Only a whole path at its start, as HookFinding.displayMessage
            // does: a plain substring turned /Users/anabel/x into ~bel/x
            // and /backup/Users/ana/x into /backup~/x.
            let root = NSRegularExpression.escapedPattern(for: trimmed)
            text = text.replacingOccurrences(of: #"(?<![^\s("'=:,\[])"# + root + #"(?=/|$|[\s"'),:;\]])"#,
                                             with: "~", options: .regularExpression)
        }
        // Only as a whole path component: a short name like "ana" must not
        // eat "banana" or "/var/analytics".
        if !user.isEmpty {
            let name = NSRegularExpression.escapedPattern(for: user)
            text = text.replacingOccurrences(of: "(?<=/)\(name)(?=/|$|\\s)", with: "<user>",
                                             options: .regularExpression)
        }
        // Emails first, so an account named after one is masked whole.
        text = text.replacingOccurrences(of: #"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#,
                                         with: "<email>", options: .regularExpression)
        // Whole names only, longest first so "work-2" goes before "work".
        // Two letters or fewer would mask ordinary words.
        for account in Set(accounts).filter({ $0.count > 2 }).sorted(by: { $0.count > $1.count }) {
            let name = NSRegularExpression.escapedPattern(for: account)
            text = text.replacingOccurrences(of: "(?<![A-Za-z0-9_-])\(name)(?![A-Za-z0-9_-])",
                                             with: "<account>", options: [.regularExpression, .caseInsensitive])
        }
        return text
    }
}
