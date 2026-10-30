import Foundation
import Testing
@testable import QuiverCore

/// The Codex account-vs-Mac comparison: window cutting, Monday weeks,
/// local months, the negative-difference flag, every status's mapping
/// and the cache round-trip.
@Suite @MainActor struct CodexAccountUsageTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    /// noon UTC on Thursday 2026-10-15: the current week is Oct 12–18,
    /// the current month October.
    private let now = utc("2026-10-15T12:00:00Z")
    /// Pinned to UTC: the fixture days are noon UTC, which a machine at
    /// UTC+12 or later would put on the next local day.
    private let calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }()

    private func day(_ text: String) -> Date {
        utc("\(text)T12:00:00Z")
    }

    private func model(reports: [String: CodexDailyReport] = [:],
                       mac: [Date: Int] = [:],
                       file: String = "usage.json") -> (CodexAccountUsage, URL) {
        let dir = scratch.url
            .appendingPathComponent("codex-usage-\(UUID().uuidString)", isDirectory: true)
        let url = dir.appendingPathComponent(file)
        var queue = reports
        let model = CodexAccountUsage(
            cacheFile: url,
            fetch: { home in
                guard let report = queue.removeValue(forKey: home) else {
                    throw SweError.commandFailed(status: 1, message: "boom")
                }
                return report
            },
            macDays: { _, _ in mac },
            now: { self.now },
            calendar: calendar)
        return (model, url)
    }

    private func report(_ days: [(String, Int)], status: String = "ok") -> CodexDailyReport {
        CodexDailyReport(status: status, summary: nil,
                         days: days.map { CodexDailyReport.Day(date: $0.0, tokens: $0.1) })
    }

    @Test func theWindowKeepsOnlyDaysBothSidesHave() {
        let account = [
            day("2026-10-10"): 10, day("2026-10-11"): 20, day("2026-10-12"): 30,
            day("2026-10-13"): 40, day("2026-10-14"): 50]
        let mac = [
            day("2026-10-10"): 1, day("2026-10-11"): 2, day("2026-10-12"): 3,
            day("2026-10-13"): 4, day("2026-10-14"): 5, day("2026-10-15"): 6,
            day("2026-10-16"): 7]  // local days ahead of the account's lag
        let (weeks, _, _) = CodexAccountUsage.compare(
            account: account, mac: mac, now: now, calendar: calendar)
        let current = weeks.last!
        // Oct 15/16 are Mac-only: they stay out of the comparison.
        #expect(current.account == 30 + 40 + 50)
        #expect(current.mac == 3 + 4 + 5)
    }

    @Test func weeksStartOnMondayAndTheRunningWeekIsFlagged() {
        // Oct 12, 2026 is a Monday; Oct 18 a Sunday.
        let account = [day("2026-10-11"): 10, day("2026-10-12"): 20, day("2026-10-18"): 30]
        let mac = [day("2026-10-11"): 1, day("2026-10-12"): 2, day("2026-10-18"): 3]
        let (weeks, _, _) = CodexAccountUsage.compare(
            account: account, mac: mac, now: now, calendar: calendar)
        #expect(weeks.count == 6)
        // Oct 11 is Sunday: it belongs to the week starting Oct 5.
        #expect(weeks[4].account == 10)
        // Oct 12 and Oct 18 share the current week, which is in progress.
        #expect(weeks[5].account == 50)
        #expect(weeks[5].inProgress)
        #expect(!weeks[4].inProgress)
        // Weekday 2 is Monday in the Gregorian calendar, in any locale.
        for period in weeks {
            #expect(calendar.component(.weekday, from: period.start) == 2)
        }
    }

    /// The report's dates read in the calendar's own zone, whatever zone
    /// the machine is in: a calendar far from the system clock (Kiritimati
    /// at UTC+14, then Baker Island at UTC-12) still keys each day on its
    /// own midnight.
    @Test func reportDatesReadInTheCalendarsZone() {
        for zone in ["Pacific/Kiritimati", "Etc/GMT+12"] {
            var far = Calendar(identifier: .gregorian)
            far.timeZone = TimeZone(identifier: zone)!
            let days = CodexAccountUsage.parseDays(report([("2026-10-13", 10)]), calendar: far)
            var parts = DateComponents()
            (parts.year, parts.month, parts.day) = (2026, 10, 13)
            #expect(days == [far.date(from: parts)!: 10], "\(zone)")
        }
    }

    @Test func monthsUseTheLocalCalendar() {
        let account = [day("2026-08-03"): 10, day("2026-09-30"): 20, day("2026-10-15"): 30]
        let mac = [day("2026-08-03"): 1, day("2026-09-30"): 2, day("2026-10-15"): 3]
        let (_, months, _) = CodexAccountUsage.compare(
            account: account, mac: mac, now: now, calendar: calendar)
        #expect(months.count == 6)
        // Six periods run May–October; August sits at index 3.
        #expect(months[3].account == 10)   // August
        #expect(months[4].account == 20)   // September
        #expect(months[5].account == 30)   // October, running
        #expect(months[5].inProgress)
        #expect(calendar.component(.month, from: months[3].start) == 8)
        #expect(calendar.component(.day, from: months[4].start) == 1)
    }

    @Test func theChipsSumTheLastFourWeeks() {
        var account: [Date: Int] = [:]
        var mac: [Date: Int] = [:]
        // Ten tokens every day for seven weeks, Mac short five a day
        // for one of the last four weeks.
        for back in 0..<49 {
            let date = Calendar(identifier: .gregorian)
                .date(byAdding: .day, value: -back, to: day("2026-10-18"))!
            account[date] = 10
            mac[date] = back >= 21 && back < 28 ? 5 : 10
        }
        let (_, _, chips) = CodexAccountUsage.compare(
            account: account, mac: mac, now: now, calendar: calendar)
        // Last four weeks: Sep 21–Oct 18, 28 days × 10 on the account
        // side; the Mac lost 5 a day for one of those weeks.
        #expect(chips.account == 280)
        #expect(chips.mac == 280 - 35)
        #expect(chips.difference == 35)
        #expect(chips.fraction == 0.125)
    }

    @Test func anAccountWithNoLocalLogsStillShowsItsTotals() async {
        // The Mac has nothing for this home: the account's days still
        // make the window, each with This Mac = 0.
        let (model, _) = model(
            reports: ["/h": report([("2026-10-13", 10), ("2026-10-14", 20)])],
            mac: [:])
        await model.refresh(homes: ["/h"])
        let section = model.section(for: "/h")!
        #expect(section.weeks.last?.account == 30)
        #expect(section.weeks.last?.mac == 0)
        #expect(!CodexAccountSection.isEmpty(section.weeks))
    }

    @Test func anAllZeroAccountShowsTheEmptyState() async {
        let (model, _) = model(
            reports: ["/h": report([], status: "ok")], mac: [:])
        await model.refresh(homes: ["/h"])
        let section = model.section(for: "/h")!
        #expect(section.kind == .data)
        #expect(CodexAccountSection.isEmpty(section.weeks))
        #expect(CodexAccountSection.isEmpty(section.months))
    }

    @Test func aNegativePeriodSetsTheFlag() async {
        let mac = [day("2026-10-13"): 20, day("2026-10-07"): 5]
        let (model, _) = model(
            reports: ["/h": report([("2026-10-13", 10), ("2026-10-07", 10)])],
            mac: mac)
        await model.refresh(homes: ["/h"])
        let section = model.section(for: "/h")!
        // Oct 13 runs 10 vs 20: more on this Mac than the account knows.
        #expect(section.hasNegativeWeek)
        #expect(section.weeks.last?.difference == -10)
        // Months sum both days: 20 vs 25, still negative here.
        #expect(section.hasNegativeMonth)
    }

    @Test func sectionStatesMapEachStatus() async {
        let (model, _) = model(reports: [
            "/ok": report([("2026-10-13", 10)]),
            "/auth": report([], status: "auth-required"),
            "/old": report([], status: "unsupported"),
        ])
        await model.refresh(homes: ["/ok", "/auth", "/old"])
        #expect(model.section(for: "/ok")?.kind == .data)
        #expect(model.section(for: "/auth")?.kind == .authRequired)
        #expect(model.section(for: "/old")?.kind == .unsupported)
        #expect(model.section(for: "/never")?.kind == .loading)
    }

    @Test func anUnavailableReplyKeepsTheCachedDataMarkedStale() async {
        var calls = 0
        let dir = scratch.url
            .appendingPathComponent("codex-usage-\(UUID().uuidString)", isDirectory: true)
        let url = dir.appendingPathComponent("usage.json")
        let model = CodexAccountUsage(
            cacheFile: url,
            fetch: { _ in
                calls += 1
                if calls == 1 {
                    return self.report([("2026-10-13", 10), ("2026-10-14", 20)])
                }
                return self.report([], status: "unavailable")
            },
            macDays: { _, _ in [self.day("2026-10-13"): 5, self.day("2026-10-14"): 6] },
            now: { self.now }, calendar: calendar)
        await model.refresh(homes: ["/h"])
        #expect(model.section(for: "/h")?.stale == false)
        await model.refresh(homes: ["/h"])
        let section = model.section(for: "/h")!
        #expect(section.stale)
        #expect(section.kind == .data)
        // The cached numbers still aggregate.
        #expect(section.weeks.last?.account == 30)
    }

    @Test func sweTooOldHidesTheSection() async {
        let (model, _) = model()
        let tooOld = CodexAccountUsage(
            cacheFile: scratch.url
                .appendingPathComponent("\(UUID().uuidString).json"),
            fetch: { _ in
                throw SweError.commandFailed(
                    status: 1, message: "Unknown harness subcommand: 'usage-daily'")
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        await tooOld.refresh(homes: ["/h"])
        #expect(tooOld.sweTooOld)
        #expect(tooOld.section(for: "/h") == nil)
        _ = model
    }

    @Test func aFailedFirstFetchShowsTheFailedStateThenRetries() async {
        var fail = true
        let model = CodexAccountUsage(
            cacheFile: scratch.url
                .appendingPathComponent("\(UUID().uuidString).json"),
            fetch: { _ in
                if fail {
                    throw SweError.commandFailed(status: 1, message: "connection refused")
                }
                return self.report([("2026-10-13", 10)])
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        await model.refresh(homes: ["/h"])
        let failed = model.section(for: "/h")!
        #expect(failed.kind == .failed)
        #expect(failed.failure?.contains("connection refused") == true)
        // A retry (the Refresh button's force refresh) recovers it.
        fail = false
        await model.refresh(force: true)
        #expect(model.section(for: "/h")?.kind == .data)
    }

    @Test func refreshForceRefetchesTheKnownHomes() async {
        var calls: [String] = []
        let model = CodexAccountUsage(
            cacheFile: scratch.url
                .appendingPathComponent("\(UUID().uuidString).json"),
            fetch: { home in
                calls.append(home)
                return self.report([("2026-10-13", 10)])
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        await model.refresh(homes: ["/h", "/i"])
        #expect(calls == ["/h", "/i"])
        // The button asks without naming homes: the last set is reused.
        await model.refresh(force: true)
        #expect(calls == ["/h", "/i", "/h", "/i"])
    }

    /// keepFresh reads its homes on each pass, and Refresh reads them
    /// live too: an account added after the last pass is fetched.
    @Test func refreshAfterKeepFreshSeesANewAccount() async throws {
        var calls: [String] = []
        var homes = ["/h"]
        let fetched = CountGate()
        let model = CodexAccountUsage(
            cacheFile: scratch.url
                .appendingPathComponent("\(UUID().uuidString).json"),
            fetch: { home in
                calls.append(home)
                await fetched.bump()
                return self.report([("2026-10-13", 10)])
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        let poller = Task { await model.keepFresh(homes: { homes }) }
        defer { poller.cancel() }
        #expect(await fetched.reach(1))
        #expect(calls == ["/h"])
        homes.append("/i")
        await model.refresh(force: true)
        #expect(calls == ["/h", "/h", "/i"])
    }

    @Test func recomputeRebuildsTheMacSideWithoutFetching() async {
        var calls = 0
        var mac: [Date: Int] = [day("2026-10-13"): 5]
        let model = CodexAccountUsage(
            cacheFile: scratch.url
                .appendingPathComponent("\(UUID().uuidString).json"),
            fetch: { _ in
                calls += 1
                return self.report([("2026-10-13", 10)])
            },
            macDays: { _, _ in mac }, now: { self.now }, calendar: calendar)
        await model.refresh(homes: ["/h"])
        #expect(model.section(for: "/h")?.weeks.last?.mac == 5)
        // What the completedScans/ledgerWrites watcher calls after a
        // scan: the account side isn't refetched, the local side is.
        mac[day("2026-10-13")] = 9
        await model.recompute()
        #expect(calls == 1)
        #expect(model.section(for: "/h")?.weeks.last?.mac == 9)
    }

    @Test func aNewSwePathClearsTheTooOldVerdict() async {
        var tooOld = true
        let model = CodexAccountUsage(
            cacheFile: scratch.url
                .appendingPathComponent("\(UUID().uuidString).json"),
            fetch: { _ in
                if tooOld {
                    throw SweError.commandFailed(
                        status: 1, message: "Unknown harness subcommand: 'usage-daily'")
                }
                return self.report([("2026-10-13", 10)])
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        await model.refresh(homes: ["/h"])
        #expect(model.sweTooOld)
        #expect(model.section(for: "/h") == nil)
        // What AppModel calls when swePath changes: the verdict was about
        // the old build, so the new one is tried straight away.
        tooOld = false
        await model.swePathChanged()
        #expect(!model.sweTooOld)
        #expect(model.section(for: "/h")?.kind == .data)
    }

    @Test func theCacheRoundTrips() async {
        let (model, url) = model(reports: ["/h": report([("2026-10-13", 42)])])
        await model.refresh(homes: ["/h"])
        // A second model on the same file sees the entry without fetching.
        let reloaded = CodexAccountUsage(
            cacheFile: url,
            fetch: { _ in throw SweError.commandFailed(status: 1, message: "no fetch") },
            macDays: { _, _ in [self.day("2026-10-13"): 7] },
            now: { self.now }, calendar: calendar)
        await reloaded.recompute()
        let section = reloaded.section(for: "/h")!
        #expect(section.kind == .data)
        #expect(section.weeks.last?.account == 42)
        #expect(section.weeks.last?.mac == 7)
    }

    @Test func aStaleTooOldReplyCannotReinstateTheVerdict() async {
        // Fetch 1 is the old swe path's: it parks on a gate while the
        // path change's forced refresh runs and succeeds.
        var gate: CheckedContinuation<CodexDailyReport, Error>?
        var entered = false
        let parked = CountGate()
        let url = scratch.url
            .appendingPathComponent("codex-usage-\(UUID().uuidString).json")
        let model = CodexAccountUsage(
            cacheFile: url,
            fetch: { _ in
                if !entered {
                    entered = true
                    return try await withCheckedThrowingContinuation {
                        (c: CheckedContinuation<CodexDailyReport, Error>) in
                        gate = c
                        Task { await parked.bump() }
                    }
                }
                return self.report([("2026-10-13", 10)])
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        let stale = Task { await model.refresh(homes: ["/h"]) }
        #expect(await parked.reach(1))
        await model.swePathChanged()
        #expect(model.section(for: "/h")?.kind == .data)
        // Only now does the old path's fetch fail "unknown subcommand":
        // its generation is stale, so it must not set sweTooOld.
        gate?.resume(throwing: SweError.commandFailed(
            status: 1, message: "Unknown harness subcommand: 'usage-daily'"))
        await stale.value
        #expect(!model.sweTooOld)
        let section = model.section(for: "/h")!
        #expect(section.kind == .data)
        #expect(section.weeks.last?.account == 10)
        #expect(model.entries["/h"]?.report.days.first?.tokens == 10)
    }

    @Test func anOlderReplyCannotOverwriteANewerFetch() async {
        // Both fetches park: the older refresh's reply lands while the
        // newer one is still in flight.
        var gates: [CheckedContinuation<CodexDailyReport, Error>] = []
        let parked = CountGate()
        let url = scratch.url
            .appendingPathComponent("codex-usage-\(UUID().uuidString).json")
        let model = CodexAccountUsage(
            cacheFile: url,
            fetch: { _ in
                try await withCheckedThrowingContinuation {
                    (c: CheckedContinuation<CodexDailyReport, Error>) in
                    gates.append(c)
                    Task { await parked.bump() }
                }
            },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        let stale = Task { await model.refresh(homes: ["/h"]) }
        #expect(await parked.reach(1))
        let fresh = Task { await model.refresh(homes: ["/h"]) }
        #expect(await parked.reach(2))
        #expect(model.fetching == ["/h"])
        // The old reply lands first: dropped, and it must not clear the
        // newer refresh's in-flight flag.
        gates[0].resume(returning: report([("2026-10-14", 999)]))
        await stale.value
        #expect(model.fetching == ["/h"])
        #expect(model.section(for: "/h")?.kind == .loading)
        gates[1].resume(returning: report([("2026-10-13", 10)]))
        await fresh.value
        #expect(model.fetching.isEmpty)
        #expect(model.section(for: "/h")?.weeks.last?.account == 10)
        // The cache kept the newer reply, not the 999.
        let reloaded = CodexAccountUsage(
            cacheFile: url,
            fetch: { _ in throw SweError.commandFailed(status: 1, message: "no fetch") },
            macDays: { _, _ in [:] }, now: { self.now }, calendar: calendar)
        #expect(reloaded.entries["/h"]?.report.days.first?.tokens == 10)
    }
}
