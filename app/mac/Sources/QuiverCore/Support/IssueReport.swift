import Foundation

/// Help › Report a Bug and Settings › Feedback: GitHub issue links with the
/// reporter's versions filled in, and the diagnostics text they can paste.
/// The query names are the field ids in .github/ISSUE_TEMPLATE, and `area`
/// is one of the forms' dropdown options word for word; `swe bug` builds the
/// same links (src/quiver/bug.py).
public enum IssueReport {
    public static let repository = URL(string: "https://github.com/c-wenlong/quiver")!
    public static let documentation = URL(string: "https://c-wenlong.github.io/quiver/")!

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

    /// What Copy Diagnostics puts on the pasteboard: the versions and swe
    /// doctor's findings. The home folder becomes ~, the user name in any
    /// other path (Nix's /etc/profiles/per-user/<name>) becomes <user>, and
    /// email addresses are masked, so a paste into a public issue names no one.
    public static func diagnostics(version: String, os: String, doctor: DoctorReport?,
                                   home: String = NSHomeDirectory(),
                                   user: String = NSUserName()) -> String {
        var lines = ["Version: \(version)", "OS: \(os)"]
        switch doctor?.findings {
        case nil:
            lines.append("swe doctor: not run yet")
        case let findings? where findings.isEmpty:
            lines.append("swe doctor: everything checks out")
        case let findings?:
            lines.append("swe doctor:")
            for finding in findings {
                lines.append("  \(finding.severity.rawValue) \(finding.check): "
                             + redact(finding.message, home: home, user: user))
            }
        }
        return lines.joined(separator: "\n")
    }

    static func redact(_ text: String, home: String, user: String = "") -> String {
        var text = text
        // "/" or "" would turn every path into a ~.
        if home.count > 1 {
            let trimmed = home.hasSuffix("/") ? String(home.dropLast()) : home
            text = text.replacingOccurrences(of: trimmed, with: "~")
        }
        // Only as a whole path component: a short name like "ana" must not
        // eat "banana" or "/var/analytics".
        if !user.isEmpty {
            let name = NSRegularExpression.escapedPattern(for: user)
            text = text.replacingOccurrences(of: "(?<=/)\(name)(?=/|$|\\s)", with: "<user>",
                                             options: .regularExpression)
        }
        return text.replacingOccurrences(of: #"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"#,
                                         with: "<email>", options: .regularExpression)
    }
}
