import Foundation
import os

/// `swe harness usage-daily codex --account-dir=<home> --json`.
public struct CodexDailyReport: Codable, Sendable, Equatable {
    public var status: String
    public var summary: Summary?
    public var days: [Day]

    public struct Summary: Codable, Sendable, Equatable {
        public var lifetimeTokens: Int
        public var peakDailyTokens: Int
        public var currentStreakDays: Int
        public var longestStreakDays: Int
    }

    public struct Day: Codable, Sendable, Equatable {
        public var date: String
        public var tokens: Int
    }
}

/// One comparison column: the account's total against this Mac's for a
/// whole week or month. `inProgress` means the period is still running,
/// so its bars render hatched.
public struct CodexComparePeriod: Sendable, Equatable {
    public var start: Date
    public var account: Int
    public var mac: Int
    public var inProgress: Bool
    /// Account minus Mac: positive reads "Not on this Mac", negative
    /// reads "More on this Mac".
    public var difference: Int { account - mac }
}

/// The last four weeks summed, for the chip row.
public struct CodexCompareChips: Sendable, Equatable {
    public var account: Int
    public var mac: Int
    public var difference: Int
    /// `difference` as a share of the account total, 0–1.
    public var fraction: Double
}

/// The section's whole model: which state it is in, and — when the
/// account answered — both comparisons.
public struct CodexAccountSection: Sendable, Equatable {
    public enum Kind: Sendable, Equatable {
        /// Nothing cached or fetched yet.
        case loading
        case data
        case authRequired
        case unsupported
        /// The first read failed and there is no cached data to show.
        case failed
    }
    public var kind: Kind
    /// The account could not be read this round; the data shown is the
    /// last good reply.
    public var stale: Bool
    /// Why a `.failed` read failed, shown as hover help.
    public var failure: String? = nil
    public var weeks: [CodexComparePeriod]
    public var months: [CodexComparePeriod]
    public var chips: CodexCompareChips

    public var hasNegativeWeek: Bool { weeks.contains { $0.difference < 0 } }
    public var hasNegativeMonth: Bool { months.contains { $0.difference < 0 } }

    /// Nothing was used on either side anywhere in the shown range —
    /// the section renders one muted line instead of zeroed figures.
    public static func isEmpty(_ periods: [CodexComparePeriod]) -> Bool {
        periods.allSatisfy { $0.account == 0 && $0.mac == 0 }
    }
}

/// Every Codex account's per-day usage, read through
/// `swe harness usage-daily codex --account-dir=<home> --json` at launch
/// and every six hours, compared against what this Mac's ledger logged
/// for the same account home.
///
/// Replies cache in `~/.quiver/cache/codex-account-usage.json` so the
/// section paints instantly on launch. An swe old enough to lack the
/// command hides the section rather than showing an error, and an
/// `unavailable` reply keeps the cached numbers with a "couldn't
/// refresh" flag instead of blanking them.
@MainActor
@Observable
public final class CodexAccountUsage {
    /// A fetched reply and when it was fetched.
    public struct Entry: Codable, Sendable, Equatable {
        public var report: CodexDailyReport
        public var fetchedAt: Date
    }

    private(set) var entries: [String: Entry]
    /// The built section per account home, recomputed after each fetch.
    public private(set) var sections: [String: CodexAccountSection] = [:]
    /// An swe without `usage-daily` is asked once, then the section
    /// hides for the rest of the session.
    public private(set) var sweTooOld = false
    /// Accounts being fetched right now.
    public private(set) var fetching: Set<String> = []
    /// Bumped by every refresh and swe-path change; replies from an
    /// older generation are dropped so a stale fetch cannot overwrite
    /// newer data or reinstate `sweTooOld` after the path moved on.
    @ObservationIgnored private var requestGeneration = 0

    private let fetch: (String) async throws -> CodexDailyReport
    /// This Mac's codex tokens per local day for an account home — the
    /// ledger's `dailyTokens`. The ledger is an actor, so building a
    /// section is async; `sections` holds the last built value for the
    /// synchronous view.
    private let macDays: (String, DateInterval) async -> [Date: Int]
    private let cacheFile: URL
    private let now: () -> Date
    private let calendar: Calendar
    /// The homes the last refresh covered, so the Accounts page's
    /// Refresh and a swe-path change can retry them.
    private var lastHomes: () -> [String] = { [] }
    /// The most recent per-home failure with no cached data behind it —
    /// the section shows "couldn't read" until a retry lands.
    private var failures: [String: String] = [:]
    /// Refresh cadence: at launch and then every six hours.
    public static let interval: TimeInterval = 6 * 3600
    /// The ledger is only asked for this much history: six months plus
    /// the week's worth OpenAI's totals can lag by.
    private static let lookback: TimeInterval = 200 * 86_400
    private static let log = Logger(subsystem: "com.cwenlong.quiver",
                                    category: "codex-account-usage")

    public init(cacheFile: URL,
                fetch: @escaping (String) async throws -> CodexDailyReport,
                macDays: @escaping (String, DateInterval) async -> [Date: Int],
                now: @escaping () -> Date = Date.init,
                calendar: Calendar = .current) {
        self.cacheFile = cacheFile
        self.fetch = fetch
        self.macDays = macDays
        self.now = now
        self.calendar = calendar
        struct Cache: Codable { var schema: Int; var accounts: [String: Entry] }
        let decoded = (try? JSONDecoder().decode(
            Cache.self, from: (try? Data(contentsOf: cacheFile)) ?? Data()))
        entries = decoded?.accounts ?? [:]
    }

    /// Poll at launch and then every `interval` until cancelled. The
    /// first pass builds sections from the cache so a cold start still
    /// paints the last run's numbers while swe answers.
    public func keepFresh(homes: @escaping () -> [String]) async {
        lastHomes = homes
        await recompute()
        while !Task.isCancelled {
            // Not refresh(homes:): that would pin `lastHomes` to this
            // pass's list, and Refresh would miss an account added later.
            await fetchAll(homes())
            do {
                try await Task.sleep(for: .seconds(Self.interval))
            } catch { return }
        }
    }

    /// The Accounts page's Refresh: fetch every known account now.
    /// `force` just documents that this is the user asking, not the
    /// six-hour timer; both refetch every account.
    public func refresh(force: Bool) async {
        await fetchAll(lastHomes())
    }

    /// The swe path changed in Settings: the "too old" verdict was about
    /// the old build, so clear it and retry the known accounts at once.
    public func swePathChanged() async {
        requestGeneration += 1
        fetching.removeAll()
        sweTooOld = false
        await fetchAll(lastHomes())
    }

    /// Rebuild `sections` from the cached entries and the ledger, for
    /// every home with an entry. Runs no swe fetch — the "This Mac" side
    /// is rebuilt after each ledger scan or write.
    public func recompute() async {
        let generation = requestGeneration
        for home in entries.keys {
            let section = await build(home)
            guard generation == requestGeneration else { return }
            sections[home] = section
        }
    }

    /// The section for one home's stored entry.
    private func build(_ home: String) async -> CodexAccountSection {
        guard let entry = entries[home] else {
            let failure = failures[home]
            return CodexAccountSection(
                kind: failure == nil ? .loading : .failed, stale: false,
                failure: failure, weeks: [], months: [],
                chips: .init(account: 0, mac: 0, difference: 0, fraction: 0))
        }
        switch entry.report.status {
        case "ok":
            let days = Self.parseDays(entry.report, calendar: calendar)
            let range = DateInterval(start: now().addingTimeInterval(-Self.lookback), end: now())
            let mac = await macDays(home, range)
            let (weeks, months, chips) = Self.compare(
                account: days, mac: mac, now: now(), calendar: calendar)
            return CodexAccountSection(kind: .data, stale: stale.contains(home),
                                       weeks: weeks, months: months, chips: chips)
        case "auth-required":
            return CodexAccountSection(kind: .authRequired, stale: false,
                                       weeks: [], months: [],
                                       chips: .init(account: 0, mac: 0, difference: 0, fraction: 0))
        case "unsupported":
            return CodexAccountSection(kind: .unsupported, stale: false,
                                       weeks: [], months: [],
                                       chips: .init(account: 0, mac: 0, difference: 0, fraction: 0))
        default:
            // `unavailable` with no cached data: nothing to show.
            return CodexAccountSection(kind: .failed, stale: true,
                                       failure: failures[home]
                                           ?? "swe reported this account's usage as unavailable.",
                                       weeks: [], months: [],
                                       chips: .init(account: 0, mac: 0, difference: 0, fraction: 0))
        }
    }

    /// Read every account once. An `unavailable` reply keeps the cached
    /// entry marked stale; `auth-required` and `unsupported` replace it;
    /// an swe that does not know the command disables the feature for
    /// the session and logs once.
    public func refresh(homes: [String], force: Bool = false) async {
        lastHomes = { homes }
        await fetchAll(homes)
    }

    private func fetchAll(_ homes: [String]) async {
        guard !sweTooOld else { return }
        requestGeneration += 1
        let generation = requestGeneration
        fetching.removeAll()
        for home in homes {
            fetching.insert(home)
            defer { if generation == requestGeneration { fetching.remove(home) } }
            do {
                let report = try await fetch(home)
                guard generation == requestGeneration else { return }
                sweTooOld = false
                if report.status == "unavailable" {
                    if entries[home] != nil {
                        // Keep the last good numbers; the section flags
                        // them as stale rather than blanking.
                        markStale(home)
                    } else {
                        failures[home] = "swe reported this account's usage as unavailable."
                    }
                } else {
                    entries[home] = Entry(report: report, fetchedAt: now())
                    clearStale(home)
                    failures[home] = nil
                }
            } catch SweError.commandFailed(_, let message)
                where message.localizedCaseInsensitiveContains("unknown harness subcommand")
                    || message.localizedCaseInsensitiveContains("unknown argument") {
                guard generation == requestGeneration else { return }
                sweTooOld = true
                Self.log.info("installed swe has no harness usage-daily; hiding the Codex totals")
                return
            } catch {
                guard generation == requestGeneration else { return }
                if entries[home] != nil {
                    markStale(home)
                } else {
                    failures[home] = error.localizedDescription
                }
                Self.log.error("codex usage-daily failed for an account: \(error.localizedDescription)")
            }
            let section = await build(home)
            guard generation == requestGeneration else { return }
            sections[home] = section
        }
        save()
    }

    @ObservationIgnored private var stale: Set<String> = []
    private func markStale(_ home: String) { stale.insert(home) }
    private func clearStale(_ home: String) { stale.remove(home) }

    /// What a card's section shows for one account home: the last built
    /// value, or `.loading` until the first refresh lands.
    public func section(for home: String) -> CodexAccountSection? {
        guard !sweTooOld else { return nil }
        if let section = sections[home] { return section }
        let failure = failures[home]
        return CodexAccountSection(
            kind: failure == nil ? .loading : .failed, stale: false,
            failure: failure, weeks: [], months: [],
            chips: .init(account: 0, mac: 0, difference: 0, fraction: 0))
    }

    private func save() {
        struct Cache: Codable { var schema: Int; var accounts: [String: Entry] }
        guard let data = try? JSONEncoder().encode(Cache(schema: 1, accounts: entries)) else { return }
        try? FileManager.default.createDirectory(
            at: cacheFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: cacheFile, options: .atomic)
    }

    /// The report's `YYYY-MM-DD` strings as local days; unparseable
    /// dates are dropped rather than misdated.
    static func parseDays(_ report: CodexDailyReport, calendar: Calendar) -> [Date: Int] {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        // Read the date in the calendar's own zone: a formatter on the
        // system zone put each day on the previous one for a calendar
        // ahead of it.
        formatter.timeZone = calendar.timeZone
        var days: [Date: Int] = [:]
        for day in report.days {
            guard let date = formatter.date(from: day.date) else { continue }
            days[calendar.startOfDay(for: date), default: 0] += day.tokens
        }
        return days
    }

    /// Account and this-Mac day maps into both comparisons.
    ///
    /// The window is the account's days — a day with no local events
    /// counts as 0 for This Mac, and the account data's last day cuts
    /// the window (OpenAI's totals lag by about a day, so the newest
    /// local day would only invent a difference).
    /// Weeks start Monday; months use the caller's calendar. Six of
    /// each, ending at the current one, which is `inProgress`.
    public static func compare(account: [Date: Int], mac: [Date: Int],
                               now: Date, calendar: Calendar)
        -> (weeks: [CodexComparePeriod], months: [CodexComparePeriod], chips: CodexCompareChips) {
        // Both sides land on local day starts, so a timestamp anywhere
        // in the day matches its day's bucket.
        var accountDays: [Date: Int] = [:]
        for (day, tokens) in account {
            accountDays[calendar.startOfDay(for: day), default: 0] += tokens
        }
        var macDays: [Date: Int] = [:]
        for (day, tokens) in mac {
            macDays[calendar.startOfDay(for: day), default: 0] += tokens
        }
        guard let lastAccountDay = accountDays.keys.max() else {
            return ([], [], .init(account: 0, mac: 0, difference: 0, fraction: 0))
        }
        // The window is the account's days: a day the Mac has no events
        // for counts as 0, it is not missing. The account's last day
        // still cuts the window — OpenAI's totals lag by about a day.
        var shared: [Date: (account: Int, mac: Int)] = [:]
        for (day, tokens) in accountDays where day <= lastAccountDay {
            shared[day] = (tokens, macDays[day] ?? 0)
        }
        var monday = calendar
        monday.firstWeekday = 2
        let weeks = periods(over: shared, now: now, calendar: monday,
                            component: .weekOfYear, count: 6)
        let months = periods(over: shared, now: now, calendar: calendar,
                             component: .month, count: 6)
        let last4 = weeks.suffix(4)
        let accountTotal = last4.reduce(0) { $0 + $1.account }
        let macTotal = last4.reduce(0) { $0 + $1.mac }
        let chips = CodexCompareChips(
            account: accountTotal, mac: macTotal,
            difference: accountTotal - macTotal,
            fraction: accountTotal > 0
                ? Double(accountTotal - macTotal) / Double(accountTotal) : 0)
        return (weeks, months, chips)
    }

    /// `count` calendar periods ending at the current one, each summing
    /// the shared days that fall in it.
    private static func periods(over days: [Date: (account: Int, mac: Int)],
                                now: Date, calendar: Calendar,
                                component: Calendar.Component, count: Int)
        -> [CodexComparePeriod] {
        guard let current = calendar.dateInterval(of: component, for: now) else { return [] }
        return (0..<count).reversed().map { back in
            let start = calendar.date(byAdding: component, value: -back, to: current.start)!
            var period = CodexComparePeriod(start: start, account: 0, mac: 0,
                                            inProgress: back == 0)
            for (day, totals) in days where day >= start
                && day < calendar.date(byAdding: component, value: 1, to: start)! {
                period.account += totals.account
                period.mac += totals.mac
            }
            return period
        }
    }
}

extension SweClient {
    /// `swe harness usage-daily codex --account-dir=<home> --json`. The
    /// command exits zero for every status, so the reply is read out of
    /// the document.
    public func codexUsageDaily(accountDir: String) async throws -> CodexDailyReport {
        try await call(["harness", "usage-daily", "codex",
                        "--account-dir=\(accountDir)", "--json"], allowFailure: true)
    }
}
