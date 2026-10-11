import Foundation

/// One row of `swe list --json`.
public struct Harness: Decodable, Sendable, Identifiable, Equatable {
    public enum State: String, Decodable, Sendable { case active, starred, archived }

    public var id: String { name }
    public let name: String
    /// The name to show, from harness.json; nil or blank when unset.
    public let displayName: String?
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
        case name, displayName, command, description, version, aliases, tags, state
        // convertFromSnakeCase capitalises "100d" to "100D", so the
        // wire key `sessions_100d` arrives as `sessions100D`.
        case sessions100d = "sessions100D"
        case rateLimit, installed, archived, notes
    }

    /// What the app calls it: the display name when set, else the brand
    /// name Quiver knows, else the id capitalised.
    public var title: String {
        let set = displayName?.trimmingCharacters(in: .whitespaces) ?? ""
        return set.isEmpty ? UsageFormat.harness(name) : set
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
    /// Every window the provider reported (Devin's 1d and 7d, Cursor's
    /// included, auto and api); nil from an older swe, or a tool with one.
    public let windows: [Window]?
    /// Prepaid usage left beyond the plan, in USD (Devin's extra usage balance).
    public let extraUsageUSD: Double?
    /// Usage billed beyond the plan (Cursor's on-demand), nil when not reported.
    public let onDemand: OnDemand?

    /// On-demand usage as swe reports it, worded the way `cursor-agent
    /// usage` words it: a value and a one-line detail.
    public struct OnDemand: Decodable, Sendable, Equatable {
        /// "fixed", "unlimited", "disabled" or "unavailable".
        public let state: String
        /// "team-member" or "personal".
        public let scope: String
        public let usedUSD: Double
        public let limitUSD: Double?

        enum CodingKeys: String, CodingKey {
            case state, scope
            case usedUSD = "usedUsd"
            case limitUSD = "limitUsd"
        }

        public init(state: String, scope: String, usedUSD: Double, limitUSD: Double?) {
            self.state = state
            self.scope = scope
            self.usedUSD = usedUSD
            self.limitUSD = limitUSD
        }

        public var value: String {
            switch state {
            case "fixed": "\(Self.dollars(usedUSD)) / \(Self.dollars(limitUSD ?? 0))"
            case "unlimited": Self.dollars(usedUSD)
            case "disabled": usedUSD > 0 ? Self.dollars(usedUSD) : "Disabled"
            default: usedUSD > 0 ? Self.dollars(usedUSD) : "Unavailable"
            }
        }

        public var detail: String {
            switch state {
            case "fixed": "\(Self.dollars(max((limitUSD ?? 0) - usedUSD, 0))) remaining"
            case "unlimited": scope == "team-member" ? "No personal limit" : "No monthly limit"
            case "disabled": "On-demand usage is off"
            default: "On-demand limit unavailable"
            }
        }

        /// Whole dollars without cents, anything else to the cent, as the CLI prints.
        static func dollars(_ amount: Double) -> String {
            let whole = amount == amount.rounded()
            return amount.formatted(FloatingPointFormatStyle<Double>.Currency(code: "USD", locale: Locale(identifier: "en_US"))
                .precision(.fractionLength(whole ? 0 : 2)))
        }
    }

    /// One quota window.
    public struct Window: Decodable, Sendable, Equatable {
        public let window: String
        public let usedPercent: Int
        public let resetAt: Date?
        public let windowSeconds: Int?

        public init(window: String, usedPercent: Int, resetAt: Date?, windowSeconds: Int? = nil) {
            self.window = window
            self.usedPercent = usedPercent
            self.resetAt = resetAt
            self.windowSeconds = windowSeconds
        }
    }

    /// The windows to list: all of them when swe sent the list, else the one
    /// top-level reading.
    public var allWindows: [Window] {
        if let windows, !windows.isEmpty { return windows }
        return [Window(window: window ?? "", usedPercent: usedPercent, resetAt: resetAt, windowSeconds: windowSeconds)]
    }

    enum CodingKeys: String, CodingKey {
        case status, usedPercent, remainingPercent, limitReached, resetAt, window
        case windowSeconds, planType, remainingUnits, totalUnits, fetchedAt, windows
        case extraUsageUSD = "extraUsageUsd"
        case onDemand
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
        windows = nil
        extraUsageUSD = nil
        onDemand = nil
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
        // Newer fields: a malformed list must not cost the whole reading.
        windows = (try? c.decodeIfPresent([Window].self, forKey: .windows)) ?? nil
        extraUsageUSD = (try? c.decodeIfPresent(Double.self, forKey: .extraUsageUSD)) ?? nil
        onDemand = (try? c.decodeIfPresent(OnDemand.self, forKey: .onDemand)) ?? nil
    }
}
