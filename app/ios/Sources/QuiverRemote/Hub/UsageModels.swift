import Foundation

/// Wire types for `GET /v1/usage` (`src/quiver/remote/usage.py`, rows from
/// `HarnessRow.to_json` and `RateLimitInfo.to_json`) and
/// `GET /v1/app/overview` (`src/quiver/ledger/overview.py`). Every field
/// is optional, so a hub that drops or adds one never breaks the page.

// MARK: - /v1/usage

struct UsageResponse: Decodable {
    let harnesses: [UsageHarness]?
    let windowDays: Int?
    let serverTime: Double?
    let machine: UsageMachineInfo?
    /// The hub first, then each satellite's last report. Absent from a hub
    /// older than satellite usage.
    let machines: [UsageMachine]?

    /// Every machine's report, falling back to the hub's own rows.
    var allMachines: [UsageMachine] {
        if let machines, !machines.isEmpty { return machines }
        return [UsageMachine(machine: machine, harnesses: harnesses, windowDays: windowDays,
                             reportedAt: serverTime, receivedAt: serverTime, hub: true)]
    }
}

/// The machine record the hub attaches: `{"id", "name", "online", ...}`.
struct UsageMachineInfo: Decodable, Hashable {
    let id: String?
    let name: String?
    let online: Bool?

    var displayName: String {
        let n = name ?? id ?? "Unknown machine"
        return n.hasSuffix(".local") ? String(n.dropLast(6)) : n
    }
}

/// One machine's harness rows. `reportedAt` is the reporting machine's
/// clock when it took the snapshot, `receivedAt` the hub's when it got it.
struct UsageMachine: Decodable, Identifiable {
    let machine: UsageMachineInfo?
    let harnesses: [UsageHarness]?
    let windowDays: Int?
    let reportedAt: Double?
    let receivedAt: Double?
    let hub: Bool?

    var id: String { machine?.id ?? machine?.name ?? "hub" }
    var rows: [UsageHarness] { harnesses ?? [] }
}

/// One `swe list --usage --json` row.
struct UsageHarness: Decodable, Identifiable, Hashable {
    let name: String
    let displayName: String?
    let state: String?
    let sessions100d: Int?
    let rateLimit: RateLimit?

    var id: String { name }

    /// `.convertFromSnakeCase` capitalises each word after an underscore,
    /// digits included, so `sessions_100d` arrives as `sessions100D`.
    enum CodingKeys: String, CodingKey {
        case name, displayName, state, rateLimit
        case sessions100d = "sessions100D"
    }

    /// swe leaves `display_name` null when the registry names none; names
    /// always start with a capital, as in the Mac app.
    var title: String { UsageFormat.harnessName(id: name, displayName: displayName) }
}

struct RateLimit: Decodable, Hashable {
    /// "ok", or "no-sub", "auth-required", "timeout".
    let status: String?
    let usedPercent: Double?
    let limitReached: Bool?
    let resetAt: Double?
    let window: String?
    let windowSeconds: Double?
    let planType: String?
    let remainingUnits: Double?
    let totalUnits: Double?
    let windows: [LimitWindow]?
    let extraUsageUsd: Double?
    let onDemand: OnDemand?
    let fetchedAt: Double?

    var isOK: Bool { (status ?? "ok") == "ok" }

    /// The windows to draw. Single-window tools (Codex) send none, and
    /// their one reading sits on the row itself.
    var displayWindows: [LimitWindow] {
        if let windows, !windows.isEmpty { return windows }
        guard isOK else { return [] }
        return [LimitWindow(window: window, usedPercent: usedPercent, limitReached: limitReached,
                            resetAt: resetAt, windowSeconds: windowSeconds, unavailable: nil, asOf: nil)]
    }
}

struct LimitWindow: Decodable, Hashable {
    let window: String?
    let usedPercent: Double?
    let limitReached: Bool?
    let resetAt: Double?
    let windowSeconds: Double?
    /// The provider should report this window but didn't this time.
    let unavailable: Bool?
    /// When a carried-over figure was read.
    let asOf: Double?

    var label: String { UsageFormat.windowLabel(window, seconds: windowSeconds) }
}

/// Cursor's usage billed beyond the plan.
struct OnDemand: Decodable, Hashable {
    /// "fixed", "unlimited", "disabled" or "unavailable".
    let state: String?
    let scope: String?
    let usedUsd: Double?
    let limitUsd: Double?
}

// MARK: - /v1/app/overview

enum UsagePeriod: String, CaseIterable, Identifiable {
    case today
    case week = "7d"
    case month = "30d"

    var id: String { rawValue }
    var title: String {
        switch self {
        case .today: "Today"
        case .week: "7 days"
        case .month: "30 days"
        }
    }
}

struct OverviewResponse: Decodable {
    struct Range: Decodable, Hashable { let startDay: String?; let endDay: String? }

    struct Totals: Decodable, Hashable {
        let tokens: Int?
        let input: Int?
        let output: Int?
        let cacheRead: Int?
        let cacheWrite: Int?
        let costUsd: Double?
        let unpricedTokens: Int?
        let unpricedModels: [String]?
    }

    struct Harness: Decodable, Hashable, Identifiable {
        let harness: String
        let tokens: Int?
        let costUsd: Double?
        let share: Double?
        let models: Int?
        var id: String { harness }
    }

    struct Day: Decodable, Hashable, Identifiable {
        let date: String
        let tokens: Int?
        let costUsd: Double?
        var id: String { date }
    }

    let period: String?
    let timeZone: String?
    let range: Range?
    let totals: Totals?
    let harnesses: [Harness]?
    /// Active days only, newest first.
    let days: [Day]?
    let scannedAt: Double?
    let notes: [String]?
    /// The hub machine's name: the ledger reads that machine's logs only.
    let machine: String?
}
