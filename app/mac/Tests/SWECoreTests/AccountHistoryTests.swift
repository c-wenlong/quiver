import Foundation
import Testing
@testable import SWECore

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

    /// The ledger stand-in: each read answers every key it is given
    /// with that harness's next queued figure; `hold` parks the next read
    /// that includes that harness until `release`.
    @MainActor final class Days {
        var queued: [String: [Int]]
        var reads: [Set<AccountHistory.Key>] = []
        var hold: String?
        /// Bumped when a held read parks, so a test awaits the park.
        let holding = CountGate()
        private var parked: CheckedContinuation<Void, Never>?

        init(_ queued: [String: [Int]]) { self.queued = queued }

        func answer(_ keys: [AccountHistory.Key], at day: Date) async -> [AccountHistory.Key: [Date: Int]] {
            reads.append(Set(keys))
            var result: [AccountHistory.Key: [Date: Int]] = [:]
            for key in keys { result[key] = [day: queued[key.harness]!.removeFirst()] }
            if let hold, keys.contains(where: { $0.harness == hold }) {
                self.hold = nil
                let holding = self.holding
                await withCheckedContinuation {
                    parked = $0
                    Task { await holding.bump() }
                }
            }
            return result
        }

        func release() { parked?.resume(); parked = nil }
    }

    let claude = AccountHistory.Key(harness: "claude", homes: ["/h/.claude"])
    let cursor = AccountHistory.Key(harness: "cursor", homes: nil, excluding: ["/x"])

    func model(_ days: Days, now: Date) -> AccountHistory {
        AccountHistory(days: { keys, _ in await days.answer(keys, at: now) },
                       now: { now }, calendar: calendar)
    }

    /// Every shown card comes from one ledger read, and a scan rebuilds
    /// them all in one more.
    @Test func shownKeysShareOneReadAndRebuildOnRecompute() async {
        let now = day(10, 16)
        let days = Days(["claude": [10, 25], "cursor": [4, 9]])
        let history = model(days, now: now)
        await history.show([claude, cursor])
        #expect(days.reads == [[claude, cursor]])
        #expect(history.histories[claude]?.weeks.last?.tokens == 10)
        await history.recompute()
        #expect(days.reads.count == 2)
        #expect(history.histories[claude]?.weeks.last?.tokens == 25)
        #expect(history.histories[cursor]?.weeks.last?.tokens == 9)
    }

    /// Hidden history, or a page left behind, costs nothing on a scan;
    /// the last figures stay cached for when it shows again.
    @Test func hiddenKeysAreNotRebuilt() async {
        let now = day(10, 16)
        let days = Days(["claude": [10, 25]])
        let history = model(days, now: now)
        await history.show([claude])
        history.hide()
        await history.recompute()
        #expect(days.reads.count == 1)
        #expect(history.histories[claude]?.weeks.last?.tokens == 10)
        await history.show([claude])
        #expect(history.histories[claude]?.weeks.last?.tokens == 25)
    }

    /// A scan that lands while a card's first figures are still loading
    /// rebuilds it, and the first read finishing late is dropped.
    @Test(.timeLimit(.minutes(1))) func aScanDuringTheFirstBuildWins() async {
        let now = day(10, 16)
        let days = Days(["claude": [1, 7]])
        days.hold = "claude"
        let history = model(days, now: now)
        let first = Task { await history.show([claude]) }
        #expect(await days.holding.reach(1))
        await history.recompute()
        #expect(history.histories[claude]?.weeks.last?.tokens == 7)
        days.release()
        await first.value
        #expect(history.histories[claude]?.weeks.last?.tokens == 7)
    }
}
