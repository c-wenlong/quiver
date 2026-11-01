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
    /// The harness's brand colour as "#RRGGBB", for charts. Nil from a swe
    /// that predates the field.
    public let primaryColor: String?
    /// Why `rateLimit` is nil, when swe could tell. Nil with a reading,
    /// with no known reason, or from a swe that predates the field.
    public let noReading: NoReading?
    /// The actionable version of a failure: its status in the remedies
    /// vocabulary plus the fix swe names for it. Nil on a healthy row,
    /// or from a swe that predates the field.
    public let issue: HarnessIssue?

    public enum NoReading: String, Decodable, Sendable {
        /// No credential the fetcher could read with.
        case signedOut = "signed_out"
        /// Credentials are there, but the fetch failed.
        case error
    }

    enum CodingKeys: String, CodingKey {
        case name, displayName, command, description, version, aliases, tags, state
        // convertFromSnakeCase capitalises "100d" to "100D", so the
        // wire key `sessions_100d` arrives as `sessions100D`.
        case sessions100d = "sessions100D"
        case rateLimit, installed, archived, notes, primaryColor, noReading, issue
    }

    /// What the app calls it: the display name when set, else the brand
    /// name SWE knows, else the id capitalised.
    public var title: String {
        let set = displayName?.trimmingCharacters(in: .whitespaces) ?? ""
        return set.isEmpty ? UsageFormat.harness(name) : set
    }

    /// Identity fields stay strict — a row without them is dropped by
    /// HarnessList's lossy decode. Everything optional tolerates a bad
    /// value so one malformed field can't cost the row, and a malformed
    /// reading degrades to nil rather than killing the whole page.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        command = try c.decode(String.self, forKey: .command)
        description = try c.decode(String.self, forKey: .description)
        aliases = try c.decode([String].self, forKey: .aliases)
        tags = try c.decode([String].self, forKey: .tags)
        state = try c.decode(State.self, forKey: .state)
        displayName = (try? c.decodeIfPresent(String.self, forKey: .displayName)) ?? nil
        version = (try? c.decodeIfPresent(String.self, forKey: .version)) ?? nil
        sessions100d = (try? c.decodeIfPresent(Int.self, forKey: .sessions100d)) ?? nil
        rateLimit = (try? c.decodeIfPresent(RateLimit.self, forKey: .rateLimit)) ?? nil
        installed = (try? c.decodeIfPresent(Bool.self, forKey: .installed)) ?? nil
        archived = (try? c.decodeIfPresent(Archive.self, forKey: .archived)) ?? nil
        notes = (try? c.decodeIfPresent(String.self, forKey: .notes)) ?? nil
        primaryColor = (try? c.decodeIfPresent(String.self, forKey: .primaryColor)) ?? nil
        noReading = rateLimit == nil
            ? (try? c.decodeIfPresent(NoReading.self, forKey: .noReading)) ?? nil : nil
        issue = (try? c.decodeIfPresent(HarnessIssue.self, forKey: .issue)) ?? nil
    }

    public struct Archive: Decodable, Sendable, Equatable {
        public let reason: String
        /// ISO 8601, as swe wrote it.
        public let archivedAt: String
        /// unknown, none, trial, used or heavy.
        public let usage: String
    }
}

/// The `issue` object swe puts on a `swe list` row: the failure's status
/// in the remedies vocabulary and the fix it names. An unknown status
/// decodes as nil rather than costing the row.
public struct HarnessIssue: Codable, Sendable, Equatable {
    public let status: IssueStatus
    public let remedy: UsageRemedy?

    public init(status: IssueStatus, remedy: UsageRemedy? = nil) {
        self.status = status
        self.remedy = remedy
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        status = try box.decode(IssueStatus.self, forKey: .status)
        remedy = try box.decodeIfPresent(UsageRemedy.self, forKey: .remedy)
    }

    enum CodingKeys: String, CodingKey { case status, remedy }
}

/// A harness's quota reading. `fetchedAt` is when it was actually taken,
/// which can be minutes old when swe served it from its cache.
public struct RateLimit: Codable, Sendable, Equatable {
    public enum Status: String, Codable, Sendable {
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
    /// Var so a caller whose swe predates the field can stamp the fetch
    /// time after decoding.
    public var fetchedAt: Date?
    /// Every window the provider reported (Devin's 1d and 7d, Cursor's
    /// included, auto and api); nil from an older swe, or a tool with one.
    /// Var so an outage fallback can mark the kept values unread.
    public var windows: [Window]?
    /// Prepaid usage left beyond the plan, in USD (Devin's extra usage balance).
    public let extraUsageUSD: Double?
    /// Usage billed beyond the plan (Cursor's on-demand), nil when not reported.
    public let onDemand: OnDemand?
    /// The sign-in's own address where swe could read one (Cursor's state
    /// db, Devin's userStatus); nil from an older swe or a credential
    /// that names no account.
    public let email: String?

    /// On-demand usage as swe reports it, worded the way `cursor-agent
    /// usage` words it: a value and a one-line detail.
    public struct OnDemand: Codable, Sendable, Equatable {
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
    public struct Window: Codable, Sendable, Equatable {
        public let window: String
        public let usedPercent: Int
        public let resetAt: Date?
        public let windowSeconds: Int?
        /// The provider should have this meter but left it unread; the
        /// figures are the last known values, dated `asOf`.
        public let unavailable: Bool
        /// When the carried figures were actually read; nil for a current
        /// window or one with no saved value.
        public let asOf: Date?

        public init(window: String, usedPercent: Int, resetAt: Date?, windowSeconds: Int? = nil,
                    unavailable: Bool = false, asOf: Date? = nil) {
            self.window = window
            self.usedPercent = usedPercent
            self.resetAt = resetAt
            self.windowSeconds = windowSeconds
            self.unavailable = unavailable
            self.asOf = asOf
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            window = try c.decode(String.self, forKey: .window)
            usedPercent = try c.decode(Int.self, forKey: .usedPercent)
            resetAt = try c.decodeIfPresent(Date.self, forKey: .resetAt)
            windowSeconds = try c.decodeIfPresent(Int.self, forKey: .windowSeconds)
            unavailable = try c.decodeIfPresent(Bool.self, forKey: .unavailable) ?? false
            asOf = try c.decodeIfPresent(Date.self, forKey: .asOf)
        }
    }

    /// The windows to list: all of them when swe sent the list, else the one
    /// top-level reading.
    public var allWindows: [Window] {
        if let windows, !windows.isEmpty { return windows }
        return [Window(window: window ?? "", usedPercent: usedPercent, resetAt: resetAt, windowSeconds: windowSeconds)]
    }

    /// The reading as swe reports one kept after a failed fetch: same
    /// status and last-known figures, every window flagged `unavailable`
    /// with the original read time as `as_of`. Readers (LimitReading, the
    /// cards) then treat it exactly like swe's own outage fallback.
    public func markedUnavailable() -> RateLimit {
        var copy = self
        copy.windows = allWindows.map { window in
            Window(window: window.window, usedPercent: window.usedPercent,
                   resetAt: window.resetAt, windowSeconds: window.windowSeconds,
                   unavailable: true, asOf: window.asOf ?? fetchedAt)
        }
        return copy
    }

    enum CodingKeys: String, CodingKey {
        case status, usedPercent, remainingPercent, limitReached, resetAt, window
        case windowSeconds, planType, remainingUnits, totalUnits, fetchedAt, windows
        case extraUsageUSD = "extraUsageUsd"
        case onDemand, email
    }

    /// For tests and previews; the app only ever decodes these from swe.
    init(status: Status, usedPercent: Int, window: String? = nil, resetAt: Date? = nil,
         planType: String? = nil, fetchedAt: Date? = nil, windows: [Window]? = nil) {
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
        self.windows = windows
        extraUsageUSD = nil
        onDemand = nil
        email = nil
    }

    /// Every field tolerates a malformed value: one bad field degrades to
    /// a default or nil instead of failing the whole HarnessList decode.
    /// An unreadable status reads as `.timeout`, the "couldn't be read"
    /// state a card already knows how to show.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = (try? c.decode(Status.self, forKey: .status)) ?? .timeout
        usedPercent = (try? c.decode(Int.self, forKey: .usedPercent)) ?? 0
        // The derived fallback is clamped first: 100 - Int.min traps.
        remainingPercent = (try? c.decode(Int.self, forKey: .remainingPercent))
            ?? (100 - min(max(usedPercent, 0), 100))
        limitReached = (try? c.decode(Bool.self, forKey: .limitReached)) ?? (usedPercent >= 100)
        resetAt = (try? c.decodeIfPresent(Date.self, forKey: .resetAt)) ?? nil
        window = (try? c.decodeIfPresent(String.self, forKey: .window)) ?? nil
        windowSeconds = (try? c.decodeIfPresent(Int.self, forKey: .windowSeconds)) ?? nil
        // swe writes "—" when a provider names no plan; that is no plan.
        let plan = ((try? c.decodeIfPresent(String.self, forKey: .planType)) ?? nil)?
            .trimmingCharacters(in: .whitespaces)
        planType = (plan == nil || plan == "" || plan == "\u{2014}" || plan == "-") ? nil : plan
        remainingUnits = (try? c.decodeIfPresent(Double.self, forKey: .remainingUnits)) ?? nil
        totalUnits = (try? c.decodeIfPresent(Double.self, forKey: .totalUnits)) ?? nil
        fetchedAt = (try? c.decodeIfPresent(Date.self, forKey: .fetchedAt)) ?? nil
        // Newer fields: a malformed list must not cost the whole reading.
        windows = (try? c.decodeIfPresent([Window].self, forKey: .windows)) ?? nil
        extraUsageUSD = (try? c.decodeIfPresent(Double.self, forKey: .extraUsageUSD)) ?? nil
        onDemand = (try? c.decodeIfPresent(OnDemand.self, forKey: .onDemand)) ?? nil
        let address = ((try? c.decodeIfPresent(String.self, forKey: .email)) ?? nil)?
            .trimmingCharacters(in: .whitespaces)
        email = address?.isEmpty == false ? address : nil
    }
}
