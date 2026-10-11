import Foundation
import Testing
@testable import QuiverCore

@Suite struct WidgetSnapshotTests {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        c.firstWeekday = 1
        return c
    }
    // Sunday 2026-10-11, 15:30 SGT.
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    func day(_ offset: Int) -> Date {
        calendar.date(byAdding: .day, value: offset, to: calendar.startOfDay(for: now))!
    }

    func model(_ name: String, _ tokens: Int, _ share: Double) -> ModelShare {
        ModelShare(model: name, totals: totals(tokens: tokens, cost: 0), share: share)
    }

    func make(activity: [DayRow] = [], models: [ModelShare] = [], limits: [LimitReading] = []) -> WidgetSnapshot {
        WidgetSnapshot.make(activity: activity, models: models, limits: limits, now: now, calendar: calendar)
    }

    @Test func todayAndTheLastSevenDaysAddUpTheirDays() {
        let activity = [
            DayRow(day: day(-7), totals: totals(tokens: 1_000, cost: 10)),  // eight days ago: out
            DayRow(day: day(-6), totals: totals(tokens: 200, cost: 2)),
            DayRow(day: day(-1), totals: totals(tokens: 30, cost: 0.3)),
            DayRow(day: day(0), totals: totals(tokens: 4, cost: 0.04)),
        ]
        let snapshot = make(activity: activity)
        #expect(snapshot.today == .init(tokens: 4, costUSD: 0.04))
        #expect(snapshot.week.tokens == 234)
        #expect(abs(snapshot.week.costUSD - 2.34) < 1e-9)
        #expect(snapshot.generatedAt == now)
    }

    @Test func aSnapshotFromYesterdayShowsNothingForToday() {
        // The app may not have run since midnight; its "today" is not today.
        let snapshot = make(activity: [DayRow(day: day(0), totals: totals(tokens: 4, cost: 1))])
        #expect(snapshot.today(at: now, calendar: calendar) == .init(tokens: 4, costUSD: 1))
        let tomorrow = now + 86_400
        #expect(snapshot.today(at: tomorrow, calendar: calendar) == .init(tokens: 0, costUSD: 0))
        #expect(snapshot.age(at: tomorrow) == 86_400)
    }

    @Test func daysCoverTheHeatmapAndSkipEmptyOnes() {
        let activity = [
            DayRow(day: day(-400), totals: totals(tokens: 9, cost: 0)),
            DayRow(day: day(-3), totals: totals(tokens: 0, cost: 0)),
            DayRow(day: day(-2), totals: totals(tokens: 5, cost: 1)),
        ]
        let snapshot = make(activity: activity)
        #expect(snapshot.days.map(\.day) == [day(-2)])
        #expect(snapshot.days.first?.tokens == 5)
        #expect(snapshot.activeDays == 1)
        #expect(snapshot.activityTokens == 5)
    }

    @Test func theSparklineFillsQuietDaysWithZero() {
        let snapshot = make(activity: [DayRow(day: day(-2), totals: totals(tokens: 5, cost: 0)),
                                       DayRow(day: day(0), totals: totals(tokens: 7, cost: 0))])
        #expect(snapshot.sparkline(days: 4, now: now, calendar: calendar) == [0, 5, 0, 7])
    }

    @Test func theHeatmapIsRebuiltFromTheDays() {
        let snapshot = make(activity: [DayRow(day: day(-1), totals: totals(tokens: 5, cost: 0))])
        let map = snapshot.heatmap(weeks: 4, now: now, calendar: calendar)
        #expect(map.columns.count == 4)
        let cells = map.columns.flatMap { $0 }.compactMap { $0 }
        #expect((cells.first { $0.day == day(-1) }?.level ?? 0) > 0)
        #expect(cells.filter { $0.level > 0 }.count == 1)
    }

    @Test func topModelsKeepTheFourLargest() {
        let models = [model("a", 50, 0.5), model("b", 20, 0.2), model("c", 15, 0.15),
                      model("d", 10, 0.1), model("e", 5, 0.05)]
        let snapshot = make(models: models)
        #expect(snapshot.models.map(\.name) == ["a", "b", "c", "d"])
        #expect(snapshot.models.first == .init(name: "a", tokens: 50, share: 0.5))
    }

    @Test func limitsAreMostUsedFirstAndCapped() {
        let limits = (0..<8).map { reading("s\($0)", used: Double($0 * 10)) }
        let snapshot = make(limits: limits)
        #expect(snapshot.limits.map(\.source) == ["s7", "s6", "s5", "s4", "s3", "s2"])
    }

    @Test func theFileHoldsNoCredentialsOrEmails() throws {
        // Only these keys reach the widget. A new field must be added here on purpose.
        var r = reading("claude-work", used: 40, resetsAt: now, fetchedAt: now)
        r.title = "Claude: Work"
        let snapshot = make(activity: [DayRow(day: day(0), totals: totals(tokens: 1, cost: 1))],
                            models: [model("m", 1, 1)], limits: [r])
        let json = try JSONSerialization.jsonObject(with: WidgetSnapshot.encoder.encode(snapshot)) as! [String: Any]
        #expect(Set(json.keys) == ["schema", "generatedAt", "today", "week", "days", "models", "limits",
                                   "activeDays", "activityTokens"])
        let limit = (json["limits"] as! [[String: Any]])[0]
        #expect(Set(limit.keys) == ["source", "harness", "title", "window", "usedPercent", "resetsAt",
                                    "fetchedAt", "isStale"])
    }

    @Test func theStoreWritesAndReadsBack() throws {
        let temp = try TempDir()
        let store = WidgetSnapshotStore(file: temp.url.appending(path: "widgets/snapshot.json"))
        #expect(store.read() == nil)
        let snapshot = make(activity: [DayRow(day: day(0), totals: totals(tokens: 3, cost: 0.5))],
                            limits: [reading("a", used: 91, resetsAt: now)])
        try store.write(snapshot)
        #expect(store.read() == snapshot)
    }

    @Test func aSnapshotFromANewerAppIsIgnored() throws {
        let temp = try TempDir()
        let file = temp.url.appending(path: "snapshot.json")
        try Data(#"{"schema": 99}"#.utf8).write(to: file)
        #expect(WidgetSnapshotStore(file: file).read() == nil)
    }

    @Test func theDefaultFileIsUnderTheRealHomeQuiverCache() {
        let file = WidgetSnapshotStore.defaultFile(home: URL(fileURLWithPath: "/Users/k"))
        #expect(file.path == "/Users/k/.quiver/cache/widgets/snapshot.json")
    }
}
