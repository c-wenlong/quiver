import Foundation

/// A vendor with both a CLI and a desktop app that can run under a profile.
public enum Vendor: String, Codable, Sendable, CaseIterable {
    case claude, codex, devin, cursor

    /// "Claude", "Codex", …: the account name from the harness name table.
    public var title: String { HarnessMark.accountName(rawValue) }

    /// Whether the vendor owns an account of its own (its `~/.claude`,
    /// `~/.codex`): only those synthesise a default profile and appear as
    /// Quiver account cards. Devin and Cursor only ever exist as extra
    /// profiles; their default sign-in is the harness's own card.
    public var hasDefaultProfile: Bool {
        switch self {
        case .claude, .codex: true
        case .devin, .cursor: false
        }
    }

    /// The CLI command on PATH (`cursor`'s CLI is `cursor-agent`).
    public var cliCommand: String {
        switch self {
        case .cursor: "cursor-agent"
        default: rawValue
        }
    }
}

/// One account slot for one vendor.
///
/// The default profile of each vendor is the vendor's own login in its usual
/// place (`~/.claude`, `~/.codex`). Every other profile owns an isolated
/// directory tree under `~/.swe/accounts`, so its CLI login, desktop
/// session and history never touch another profile's.
public struct Profile: Codable, Sendable, Identifiable, Equatable, Hashable {
    /// `<vendor>-<slug>`; the defaults are `claude-default` and `codex-default`.
    public var id: String
    public var vendor: Vendor
    public var name: String
    public var isDefault: Bool
    /// `#rrggbb`, shown as the profile's dot.
    public var color: String
    public var createdAt: Date?

    public init(
        id: String, vendor: Vendor, name: String, isDefault: Bool, color: String,
        createdAt: Date? = nil
    ) {
        self.id = id
        self.vendor = vendor
        self.name = name
        self.isDefault = isDefault
        self.color = color
        self.createdAt = createdAt
    }
}

/// Where a profile's state lives on disk.
public struct ProfilePaths: Equatable, Sendable {
    /// The CLI/agent config home: what `CLAUDE_CONFIG_DIR` or `CODEX_HOME` points at.
    public var home: URL
    /// The Chromium `--user-data-dir` for the desktop app. Nil means the
    /// desktop app's own default location.
    public var desktopData: URL?

    public init(home: URL, desktopData: URL? = nil) {
        self.home = home
        self.desktopData = desktopData
    }
}
