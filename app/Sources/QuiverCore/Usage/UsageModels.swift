import Foundation

/// Which CLI's subscription limits an account reports.
public enum UsageProvider: String, Sendable, Codable {
    case claude, codex
}

/// One rate-limit window, such as the five-hour session or the weekly cap.
public struct UsageWindow: Sendable, Equatable, Codable {
    /// Short name for the window: "5h", "7d", "7d Opus".
    public var label: String
    /// How much of the window is used, 0 to 100.
    public var usedPercent: Double
    public var resetsAt: Date?

    public init(label: String, usedPercent: Double, resetsAt: Date?) {
        self.label = label
        self.usedPercent = usedPercent
        self.resetsAt = resetsAt
    }
}

/// Whether the last attempt to read an account's usage worked, and if not why.
public enum UsageStatus: Sendable, Equatable, Codable {
    case ok
    /// No credential on disk or in the keychain.
    case signedOut
    /// The credential has expired or the provider rejected it (401).
    case expired
    /// The provider asked us to back off; no request is sent before `until`.
    case rateLimited(until: Date)
    /// Anything else. The message never carries a credential.
    case error(String)
}

/// What one account has used, as best known right now.
public struct AccountUsage: Sendable, Equatable, Codable {
    public var status: UsageStatus
    public var plan: String?
    public var email: String?
    public var windows: [UsageWindow]
    /// When the reading was actually taken. A reused reading keeps its own time.
    public var fetchedAt: Date?
    /// True when `windows` is an older reading shown because this fetch failed
    /// or was skipped (cooldown, expired login, network error).
    public var isStale: Bool
    /// When the sign-in this reading used stops working, as the CLI stored
    /// it with the credential. Claude only; Codex records none.
    public var signInExpiresAt: Date?

    public init(
        status: UsageStatus, plan: String? = nil, email: String? = nil,
        windows: [UsageWindow] = [], fetchedAt: Date? = nil, isStale: Bool = false,
        signInExpiresAt: Date? = nil
    ) {
        self.status = status
        self.plan = plan
        self.email = email
        self.windows = windows
        self.fetchedAt = fetchedAt
        self.isStale = isStale
        self.signInExpiresAt = signInExpiresAt
    }
}

/// Names one signed-in account: a provider plus the config home its CLI uses.
public struct AccountKey: Hashable, Sendable, Codable {
    public var provider: UsageProvider
    /// The directory the CLI keeps its state in (`~/.claude`, `CLAUDE_CONFIG_DIR`,
    /// `~/.codex`, `CODEX_HOME`), spelled exactly as the CLI was given it.
    public var configHome: String
    /// True when the CLI runs without a config-home override. Claude Code keys
    /// its keychain item on this, not on the path.
    public var isDefault: Bool

    public init(provider: UsageProvider, configHome: String, isDefault: Bool) {
        self.provider = provider
        self.configHome = configHome
        self.isDefault = isDefault
    }
}

/// Sends one HTTP request. The seam tests replace so nothing hits the network.
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

/// Reads a generic-password item from the login keychain, or nil when absent.
public protocol SecretReading: Sendable {
    func genericPassword(service: String, account: String) async -> String?
}

/// Reads, writes and deletes generic-password items. Where the value
/// travels is the implementation's choice; `SecItemKeychain` keeps it
/// in-process, never on a tool's argv.
public protocol SecretStoring: SecretReading {
    func setGenericPassword(service: String, account: String, value: String) async throws
    func deleteGenericPassword(service: String, account: String) async throws
}

/// Reads a whole file, or nil when it is missing or unreadable.
public protocol FileReading: Sendable {
    func read(_ path: String) -> Data?
}
