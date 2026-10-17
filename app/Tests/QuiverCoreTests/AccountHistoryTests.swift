import Foundation
import Testing
@testable import QuiverCore

/// The Accounts page's per-card history: six weeks and six months from
/// the ledger, built only for the keys asked for.
@MainActor
@Suite struct AccountHistoryTests {
    var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Singapore")!
        return calendar
    }()

    func day(_ month: Int, _ day: Int) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: month, day: day))!
    }

    @Test func daysLandInMondayWeeksAndMonthsEndingNow() {
        // Friday 16 October 2026: its week starts Monday 12 October.
        let now = day(10, 16).addingTimeInterval(3600)
        let history = AccountHistory.history(
            days: [day(10, 12): 5, day(10, 16): 7, day(10, 11): 100, day(9, 1): 40, day(3, 1): 999],
            now: now, calendar: calendar)
        #expect(history.weeks.count == 6)
        #expect(history.weeks.last?.start == day(10, 12))
        #expect(history.weeks.last?.tokens == 12)
        #expect(history.weeks.last?.inProgress == true)
        // Sunday 11 October belongs to the week before.
        #expect(history.weeks[4].start == day(10, 5))
        #expect(history.weeks[4].tokens == 100)
        #expect(history.weeks[4].inProgress == false)
        #expect(history.months.map(\.start) == [day(5, 1), day(6, 1), day(7, 1), day(8, 1), day(9, 1), day(10, 1)])
        #expect(history.months.map(\.tokens) == [0, 0, 0, 0, 40, 112])
    }

    /// The ledger stand-in: each key answers its queued figures in
    /// order; `hold` parks the next call for that key until `release`.
    @MainActor final class Days {
        var queued: [String: [Int]]
        var asked: [AccountHistory.Key] = []
        var hold: String?
        private(set) var holding = false
        private var parked: CheckedContinuation<Void, Never>?

        init(_ queued: [String: [Int]]) { self.queued = queued }

        func answer(_ key: AccountHistory.Key, at day: Date) async -> [Date: Int] {
            asked.append(key)
            let value = queued[key.harness]!.removeFirst()
            if hold == key.harness {
                hold = nil
                holding = true
                await withCheckedContinuation { parked = $0 }
            }
            return [day: value]
        }

        func release() { parked?.resume(); parked = nil }
    }

    @Test func requestBuildsOnlyMissingKeysAndRecomputeRebuildsThem() async {
        let now = day(10, 16)
        let days = Days(["claude": [10, 25], "cursor": [4, 9]])
        let history = AccountHistory(days: { key, _ in await days.answer(key, at: now) },
                                     now: { now }, calendar: calendar)
        let claude = AccountHistory.Key(harness: "claude", homes: ["/h/.claude"])
        let cursor = AccountHistory.Key(harness: "cursor", homes: nil, excluding: ["/x"])
        await history.request([claude, cursor])
        await history.request([claude])
        #expect(days.asked == [claude, cursor])
        #expect(history.histories[claude]?.weeks.last?.tokens == 10)
        await history.recompute()
        #expect(days.asked.count == 4)
        #expect(history.histories[claude]?.weeks.last?.tokens == 25)
        #expect(history.histories[cursor]?.weeks.last?.tokens == 9)
    }

    /// An older rebuild that finishes late never overwrites a newer one.
    @Test func anOlderBuildNeverOverwritesNewerFigures() async {
        let now = day(10, 16)
        let days = Days(["claude": [1, 5, 7]])
        let history = AccountHistory(days: { key, _ in await days.answer(key, at: now) },
                                     now: { now }, calendar: calendar)
        let claude = AccountHistory.Key(harness: "claude", homes: nil)
        await history.request([claude])
        days.hold = "claude"
        let older = Task { await history.recompute() }
        while !days.holding { await Task.yield() }
        await history.recompute()
        #expect(history.histories[claude]?.weeks.last?.tokens == 7)
        days.release()
        await older.value
        #expect(history.histories[claude]?.weeks.last?.tokens == 7)
    }
}
