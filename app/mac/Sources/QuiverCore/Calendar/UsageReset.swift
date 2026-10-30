import Foundation

/// One account limit window's scheduled reset: a marker the calendar
/// draws at its reset time once the account is ticked in the side
/// panel's Usage section. Never carries a credential.
public struct UsageReset: Equatable, Sendable, Identifiable {
    /// The marker follows the reported time: a fresh reading that moves
    /// the reset replaces it rather than sliding one around.
    public var id: String { "\(account)|\(window)|\(at.timeIntervalSince1970)" }
    /// The profile's id: the Usage section's per-account toggle key.
    public var account: String
    /// The profile's name ("Work"), the marker's label when several
    /// accounts show.
    public var accountName: String
    /// The account's title as limits give it: "Claude: Work".
    public var title: String
    /// The profile's vendor, for the legend's icon.
    public var harness: String
    /// The window's label as the provider reports it: "5h", "7d Opus".
    public var window: String
    /// When the window resets.
    public var at: Date
    /// True when the reset comes from a kept older reading (a failed or
    /// skipped fetch): drawn and listed dimmed, as last-known rather
    /// than live.
    public var isStale: Bool

    public init(account: String, accountName: String, title: String, harness: String,
                window: String, at: Date, isStale: Bool) {
        self.account = account
        self.accountName = accountName
        self.title = title
        self.harness = harness
        self.window = window
        self.at = at
        self.isStale = isStale
    }
}

extension AccountsModel.Card {
    /// The limit title the menu bar and notifications use: "Claude: Work".
    public var usageTitle: String { "\(profile.vendor.title): \(profile.name)" }

    /// Every window with a scheduled reset; windows that report none
    /// leave no marker on the grid.
    public var usageResets: [UsageReset] {
        guard let usage else { return [] }
        return usage.windows.compactMap { window in
            guard let at = window.resetsAt else { return nil }
            return UsageReset(account: profile.id, accountName: profile.name,
                              title: usageTitle, harness: profile.vendor.rawValue,
                              window: window.label, at: at, isStale: usage.isStale)
        }
    }
}
