import Foundation

/// Which homes the ledger reads, and what to call each one.
public struct AccountDirectory: Sendable {
    public let roots: LedgerRoots
    private let names: [String: String]
    /// The account homes each harness can carry, for the calendar
    /// legend's always-on account split: default homes plus every extra
    /// profile's, keyed the way activity names the account.
    private let harnessHomes: [String: [String]]
    private let home: URL

    public init(profiles: [Profile], paths: (Profile) -> ProfilePaths,
                home: URL = FileManager.default.homeDirectoryForCurrentUser) {
        self.home = home
        var names: [String: String] = [:]
        var extra: [Vendor: [URL]] = [:]
        var harnessHomes: [String: [String]] = [
            "claude": [home.appendingPathComponent(".claude").path],
            "codex": [home.appendingPathComponent(".codex").path],
            "devin": [home.appendingPathComponent(".local/share/devin/cli").path],
            "cursor": [home.appendingPathComponent("Library/Application Support/Cursor").path],
        ]
        for profile in profiles {
            let profileHome = paths(profile).home
            names[profileHome.standardizedFileURL.path] = "\(profile.vendor.title): \(profile.name)"
            if !profile.isDefault {
                extra[profile.vendor, default: []].append(profileHome)
                // Cursor blocks name the user-data dir, added below;
                // the CLI home never appears as a Cursor account.
                if profile.vendor != .cursor {
                    harnessHomes[profile.vendor.rawValue, default: []]
                        .append(profileHome.standardizedFileURL.path)
                }
            }
            // Cursor activity is keyed on the app's user-data directory,
            // not the profile home, so the legend labels it too.
            if profile.vendor == .cursor {
                let dir = profile.isDefault
                    ? home.appendingPathComponent("Library/Application Support/Cursor")
                    : paths(profile).desktopData
                if let dir {
                    names[dir.standardizedFileURL.path] = "Cursor: \(profile.name)"
                    if !profile.isDefault {
                        harnessHomes["cursor", default: []]
                            .append(dir.standardizedFileURL.path)
                    }
                }
            }
        }
        self.names = names
        self.harnessHomes = harnessHomes
        roots = .standard(home: home, claudeProfiles: extra[.claude] ?? [], codexProfiles: extra[.codex] ?? [])
    }

    /// Every account home a harness can have: its default plus the extra
    /// profiles' homes (Cursor's user-data dirs for Cursor), so the
    /// legend can list accounts that were idle in the range.
    public func homes(for harness: String) -> [String] {
        harnessHomes[harness] ?? []
    }

    /// The profile's name, or a short form of the path for homes no profile owns.
    public func label(for accountHome: String) -> String {
        // Not a folder: cloud sessions have no home on this Mac.
        if accountHome == DevinCloud.accountHome { return "Devin cloud" }
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
        case "grok-bot": "Grok Bot"
        case DevinCloud.harness: "Devin cloud"
        default: id.prefix(1).uppercased() + id.dropFirst()
        }
    }

    /// Money that is a balance, not a spend: always to the cent, "$9,868.98".
    public static func balance(_ amount: Double) -> String {
        amount.formatted(FloatingPointFormatStyle<Double>.Currency(code: "USD", locale: Locale(identifier: "en_US"))
            .precision(.fractionLength(2)))
    }

    /// A limit window's name as the harness's own CLI prints it: Cursor's
    /// "included", "auto", "api" and "grok_bot" read "Included", "Auto",
    /// "API" and "Grok Bot"; "5h" or "7d Fable" stay as they are.
    public static func window(_ label: String) -> String {
        switch label {
        case "included": "Included"
        case "auto": "Auto"
        case "api": "API"
        case "grok_bot": "Grok Bot"
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
