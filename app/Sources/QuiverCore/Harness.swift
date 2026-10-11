import Foundation

/// One row of `swe list --json`.
public struct Harness: Decodable, Sendable, Identifiable, Equatable {
    public enum State: String, Decodable, Sendable { case active, starred, archived }

    public var id: String { name }
    public let name: String
    public let command: String
    public let description: String
    public let version: String?
    public let aliases: [String]
    public let tags: [String]
    public let state: State
    public let sessions100d: Int?
    public let rateLimit: RateLimit?
    /// Whether its command is on PATH. Nil from a swe that predates the field.
    public let installed: Bool?
    /// Why and when it was archived; nil unless archived.
    public let archived: Archive?
    /// Free-form notes from harness.json. Nil when unset or from an older swe.
    public let notes: String?

    enum CodingKeys: String, CodingKey {
        case name, command, description, version, aliases, tags, state
        // convertFromSnakeCase capitalises "100d" to "100D", so the
        // wire key `sessions_100d` arrives as `sessions100D`.
        case sessions100d = "sessions100D"
        case rateLimit, installed, archived, notes
    }

    public struct Archive: Decodable, Sendable, Equatable {
        public let reason: String
        /// ISO 8601, as swe wrote it.
        public let archivedAt: String
        /// unknown, none, trial, used or heavy.
        public let usage: String
    }
}

/// A harness's quota reading. `fetchedAt` is when it was actually taken,
/// which can be minutes old when swe served it from its cache.
public struct RateLimit: Decodable, Sendable, Equatable {
    public enum Status: String, Decodable, Sendable {
        case ok
        case noSubscription = "no-sub"
        case authRequired = "auth-required"
        case timeout
    }

    public let status: Status
    public let usedPercent: Int
    public let remainingPercent: Int
    public let limitReached: Bool
    public let resetAt: Date?
    public let window: String?
    public let windowSeconds: Int?
    public let planType: String?
    public let remainingUnits: Double?
    public let totalUnits: Double?
    public let fetchedAt: Date?

    enum CodingKeys: String, CodingKey {
        case status, usedPercent, remainingPercent, limitReached, resetAt, window
        case windowSeconds, planType, remainingUnits, totalUnits, fetchedAt
    }

    /// For tests and previews; the app only ever decodes these from swe.
    init(status: Status, usedPercent: Int, window: String? = nil, resetAt: Date? = nil,
         planType: String? = nil, fetchedAt: Date? = nil) {
        self.status = status
        self.usedPercent = usedPercent
        remainingPercent = 100 - usedPercent
        limitReached = usedPercent >= 100
        self.resetAt = resetAt
        self.window = window
        windowSeconds = nil
        self.planType = planType
        remainingUnits = nil
        totalUnits = nil
        self.fetchedAt = fetchedAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decode(Status.self, forKey: .status)
        usedPercent = try c.decode(Int.self, forKey: .usedPercent)
        remainingPercent = try c.decode(Int.self, forKey: .remainingPercent)
        limitReached = try c.decode(Bool.self, forKey: .limitReached)
        resetAt = try c.decodeIfPresent(Date.self, forKey: .resetAt)
        window = try c.decodeIfPresent(String.self, forKey: .window)
        windowSeconds = try c.decodeIfPresent(Int.self, forKey: .windowSeconds)
        // swe writes "—" when a provider names no plan; that is no plan.
        let plan = try c.decodeIfPresent(String.self, forKey: .planType)?
            .trimmingCharacters(in: .whitespaces)
        planType = (plan == nil || plan == "" || plan == "\u{2014}" || plan == "-") ? nil : plan
        remainingUnits = try c.decodeIfPresent(Double.self, forKey: .remainingUnits)
        totalUnits = try c.decodeIfPresent(Double.self, forKey: .totalUnits)
        fetchedAt = try c.decodeIfPresent(Date.self, forKey: .fetchedAt)
    }
}
