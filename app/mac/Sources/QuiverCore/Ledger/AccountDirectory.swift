import Foundation

/// Which homes the ledger reads, and what to call each one.
public struct AccountDirectory: Sendable {
    public let roots: LedgerRoots
    private let names: [String: String]
    /// Homes only an extra (non-default) profile owns, keyed the way the
    /// ledger and feeds name the account: profile `home`s, and for Cursor
    /// the user-data dirs activity is recorded under.
    private let extraHomes: Set<String>
    /// The account homes each harness can carry, for the calendar
    /// legend's always-on account split: default homes plus every extra
    /// profile's, keyed the way activity names the account.
    private let harnessHomes: [String: [String]]
    /// `harnessHomes` flattened the other way, so a stock home no profile
    /// names can still read as its harness rather than a path.
    private let homeHarness: [String: String]
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
        var extraHomes: Set<String> = []
        for profile in profiles {
            let profileHome = paths(profile).home
            names[profileHome.standardizedFileURL.path] = "\(profile.vendor.title): \(profile.name)"
            if !profile.isDefault {
                extra[profile.vendor, default: []].append(profileHome)
                extraHomes.insert(profileHome.standardizedFileURL.path)
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
                        extraHomes.insert(dir.standardizedFileURL.path)
                        harnessHomes["cursor", default: []]
                            .append(dir.standardizedFileURL.path)
                    }
                }
            }
        }
        self.names = names
        self.extraHomes = extraHomes
        self.harnessHomes = harnessHomes
        homeHarness = Dictionary(
            harnessHomes.flatMap { harness, homes in
                homes.map { (URL(fileURLWithPath: $0).standardizedFileURL.path, harness) }
            },
            uniquingKeysWith: { first, _ in first })
        roots = .standard(home: home, claudeProfiles: extra[.claude] ?? [], codexProfiles: extra[.codex] ?? [])
    }

    /// Every account home a harness can have: its default plus the extra
    /// profiles' homes (Cursor's user-data dirs for Cursor), so the
    /// legend can list accounts that were idle in the range.
    public func homes(for harness: String) -> [String] {
        harnessHomes[harness] ?? []
    }

    /// The profile's label for an extra account's home ("Claude: Cortex"),
    /// nil for a default home or one no extra profile owns. The Sessions
    /// page shows the account only where it differs from the stock home.
    public func extraLabel(for accountHome: String) -> String? {
        let path = URL(fileURLWithPath: accountHome).standardizedFileURL.path
        return extraHomes.contains(path) ? names[path] : nil
    }

    /// The profile's name, else the owning harness's name, else a short
    /// form of the path for homes neither claims.
    public func label(for accountHome: String) -> String {
        // Not a folder: cloud sessions have no home on this Mac.
        if accountHome == DevinCloud.accountHome { return HarnessMark(DevinCloud.harness).name }
        let path = URL(fileURLWithPath: accountHome).standardizedFileURL.path
        if let name = names[path] { return name }
        // A stock harness home (the unprofiled ~/.claude, Cursor's own
        // user-data dir) has no profile; it reads as the harness.
        if let harness = homeHarness[path] { return HarnessMark.accountName(harness) }
        let share = home.appendingPathComponent(".local/share").path
        if path == share + "/opencode" { return HarnessMark("opencode").name }
        let base = home.path
        if path.hasPrefix(base + "/") { return "~" + path.dropFirst(base.count) }
        return path
    }
}

/// How the Overview and the menu bar print numbers and names.
public enum UsageFormat {
    public static func tokens(_ count: Int) -> String {
        let value = Double(count)
        let units: [(limit: Double, suffix: String)] = [(1e3, "K"), (1e6, "M"), (1e9, "B")]
        guard var unit = units.lastIndex(where: { value >= $0.limit }) else { return String(count) }
        // The unit after rounding: 999,950 rounds to 1000K, which reads
        // as 1M.
        while unit + 1 < units.count, ((value / units[unit].limit) * 10).rounded() / 10 >= 1000 {
            unit += 1
        }
        return compact(value / units[unit].limit) + units[unit].suffix
    }

    public static func dollars(_ amount: Double) -> String {
        if amount == 0 { return "$0" }
        if amount < 0.01 { return "<$0.01" }
        let style = FloatingPointFormatStyle<Double>.Currency(code: "USD", locale: Locale(identifier: "en_US"))
            .precision(.fractionLength(amount < 100 ? 2 : 0))
        return amount.formatted(style)
    }

    /// A harness's display name: `HarnessMark`'s table, the one source.
    public static func harness(_ id: String) -> String {
        HarnessMark(id).name
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
        case "grok_bot": HarnessMark("grok-bot").name
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

    /// "Sign-in expires at 22:41", "Sign-in expired yesterday at 00:08":
    /// when a sign-in lapses, said as a clock time, with the day named
    /// only when it is not today.
    public static func signIn(expiresAt: Date, now: Date, calendar: Calendar = .current) -> String {
        let verb = expiresAt > now ? "expires" : "expired"
        let time = calendar.dateComponents([.hour, .minute], from: expiresAt)
        let clock = String(format: "%02d:%02d", time.hour ?? 0, time.minute ?? 0)
        let day: String
        if calendar.isDate(expiresAt, inSameDayAs: now) {
            day = ""
        } else if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
                  calendar.isDate(expiresAt, inSameDayAs: yesterday) {
            day = "yesterday "
        } else if let tomorrow = calendar.date(byAdding: .day, value: 1, to: now),
                  calendar.isDate(expiresAt, inSameDayAs: tomorrow) {
            day = "tomorrow "
        } else {
            let formatter = DateFormatter()
            formatter.calendar = calendar
            formatter.timeZone = calendar.timeZone
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.dateFormat = "d MMM"
            day = "on \(formatter.string(from: expiresAt)) "
        }
        return "Sign-in \(verb) \(day)at \(clock)"
    }

    private static func compact(_ value: Double) -> String {
        let rounded = (value * 10).rounded() / 10
        return rounded == rounded.rounded() ? String(Int(rounded)) : String(format: "%.1f", rounded)
    }
}
