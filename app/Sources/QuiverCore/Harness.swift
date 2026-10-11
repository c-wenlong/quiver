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
}
