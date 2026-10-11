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

    enum CodingKeys: String, CodingKey {
        case name, command, description, version, aliases, tags, state
        case sessions100d = "sessions100d"
        case rateLimit
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
