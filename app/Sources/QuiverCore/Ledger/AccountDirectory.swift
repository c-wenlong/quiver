import Foundation

/// Which homes the ledger reads, and what to call each one.
public struct AccountDirectory: Sendable {
    public let roots: LedgerRoots
    private let names: [String: String]
    private let home: URL

    public init(profiles: [Profile], paths: (Profile) -> ProfilePaths,
                home: URL = FileManager.default.homeDirectoryForCurrentUser) {
        self.home = home
        var names: [String: String] = [:]
        var extra: [Vendor: [URL]] = [:]
        for profile in profiles {
            let profileHome = paths(profile).home
            names[profileHome.standardizedFileURL.path] = "\(profile.vendor.title): \(profile.name)"
            if !profile.isDefault { extra[profile.vendor, default: []].append(profileHome) }
        }
        self.names = names
        roots = .standard(home: home, claudeProfiles: extra[.claude] ?? [], codexProfiles: extra[.codex] ?? [])
    }

    /// The profile's name, or a short form of the path for homes no profile owns.
    public func label(for accountHome: String) -> String {
        let path = URL(fileURLWithPath: accountHome).standardizedFileURL.path
        if let name = names[path] { return name }
        let share = home.appendingPathComponent(".local/share").path
        if path == share + "/opencode" { return "OpenCode" }
        if path == share + "/devin/cli" { return "Devin" }
        let base = home.path
        if path.hasPrefix(base + "/") { return "~" + path.dropFirst(base.count) }
        return path
    }
}

/// How the Overview and the menu bar print numbers and names.
public enum UsageFormat {
    public static func tokens(_ count: Int) -> String {
        let value = Double(count)
        for (limit, suffix) in [(1e9, "B"), (1e6, "M"), (1e3, "K")] where value >= limit {
            return compact(value / limit) + suffix
        }
        return String(count)
    }

    public static func dollars(_ amount: Double) -> String {
        if amount == 0 { return "$0" }
        if amount < 0.01 { return "<$0.01" }
        let style = FloatingPointFormatStyle<Double>.Currency(code: "USD", locale: Locale(identifier: "en_US"))
            .precision(.fractionLength(amount < 100 ? 2 : 0))
        return amount.formatted(style)
    }

    public static func harness(_ id: String) -> String {
        switch id {
        case "claude": "Claude Code"
        case "codex": "Codex"
        case "opencode": "OpenCode"
        case "devin": "Devin"
        default: id.prefix(1).uppercased() + id.dropFirst()
        }
    }

    /// Money that is a balance, not a spend: always to the cent, "$9,868.98".
    public static func balance(_ amount: Double) -> String {
        amount.formatted(FloatingPointFormatStyle<Double>.Currency(code: "USD", locale: Locale(identifier: "en_US"))
            .precision(.fractionLength(2)))
    }

    /// A limit window's name as the harness's own CLI prints it: Cursor's
    /// "included", "auto" and "api" read "Included", "Auto" and "API";
    /// "5h" or "7d Fable" stay as they are.
    public static func window(_ label: String) -> String {
        switch label {
        case "included": "Included"
        case "auto": "Auto"
        case "api": "API"
        default: label
        }
    }

    /// "in 12m", "in 4h 28m", "in 25d": how long until a limit resets.
    public static func resetIn(_ date: Date, now: Date) -> String {
        let seconds = date.timeIntervalSince(now)
        guard seconds > 0 else { return "now" }
        let minutes = Int((seconds / 60).rounded(.up))
        if minutes < 60 { return "in \(minutes)m" }
        if minutes < 24 * 60 {
            let rest = minutes % 60
            return rest == 0 ? "in \(minutes / 60)h" : "in \(minutes / 60)h \(rest)m"
        }
        return "in \(minutes / (24 * 60))d"
    }

    /// "5m ago", "1h ago", "3d ago": how old something is, whole units only.
    public static func ago(_ date: Date, now: Date) -> String {
        let minutes = Int(now.timeIntervalSince(date) / 60)
        if minutes < 1 { return "just now" }
        if minutes < 60 { return "\(minutes)m ago" }
        if minutes < 24 * 60 { return "\(minutes / 60)h ago" }
        return "\(minutes / (24 * 60))d ago"
    }

    private static func compact(_ value: Double) -> String {
        let rounded = (value * 10).rounded() / 10
        return rounded == rounded.rounded() ? String(Int(rounded)) : String(format: "%.1f", rounded)
    }
}
