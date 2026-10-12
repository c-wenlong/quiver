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

    func row(_ offset: Int, tokens: Int, cost: Double = 0, harness: String = "claude",
             model: String = "m") -> DailyUsage {
        var totals = UsageTotals()
        totals.inputTokens = tokens
        totals.costUSD = cost
        return DailyUsage(day: day(offset), harness: harness, model: model, totals: totals)
    }

    func make(activity: [DailyUsage] = [], models: [DailyUsage] = [],
              limits: [LimitReading] = [], selection: WidgetSelection = WidgetSelection()) -> WidgetSnapshot {
        WidgetSnapshot.make(activity: activity, models: models, limits: limits,
                            selection: selection, now: now, calendar: calendar)
    }

    @Test func todayAndTheLastSevenDaysAddUpTheirDays() {
        let activity = [
            row(-7, tokens: 1_000, cost: 10),  // eight days ago: out
            row(-6, tokens: 200, cost: 2),
            row(-1, tokens: 30, cost: 0.3),
            row(0, tokens: 4, cost: 0.04),
        ]
        let snapshot = make(activity: activity)
        #expect(snapshot.summary.today == .init(tokens: 4, costUSD: 0.04))
        #expect(snapshot.summary.week.tokens == 234)
        #expect(abs(snapshot.summary.week.costUSD - 2.34) < 1e-9)
        #expect(snapshot.generatedAt == now)
    }

    @Test func aSnapshotFromYesterdayShowsNothingForToday() {
        // The app may not have run since midnight; its "today" is not today.
        let snapshot = make(activity: [row(0, tokens: 4, cost: 1)])
        #expect(snapshot.today(at: now, calendar: calendar) == .init(tokens: 4, costUSD: 1))
        let tomorrow = now + 86_400
        #expect(snapshot.today(at: tomorrow, calendar: calendar) == .init(tokens: 0, costUSD: 0))
        #expect(snapshot.age(at: tomorrow) == 86_400)
    }

    @Test func daysCoverTheHeatmapAndSkipEmptyOnes() {
        let activity = [
            row(-400, tokens: 9),
            row(-3, tokens: 0),
            row(-2, tokens: 5, cost: 1),
        ]
        let snapshot = make(activity: activity)
        #expect(snapshot.heatmap.days.map(\.day) == [day(-2)])
        #expect(snapshot.heatmap.days.first?.tokens == 5)
        #expect(snapshot.heatmap.activeDays == 1)
        #expect(snapshot.heatmap.activityTokens == 5)
    }

    @Test func rowsFromTwoHarnessesOnOneDayMergeIntoOneHeatmapDay() {
        let snapshot = make(activity: [row(-1, tokens: 3, harness: "claude"),
                                       row(-1, tokens: 4, harness: "codex")])
        #expect(snapshot.heatmap.days.map(\.tokens) == [7])
        #expect(snapshot.heatmap.activeDays == 1)
    }

    @Test func theSparklineFillsQuietDaysWithZero() {
        let snapshot = make(activity: [row(-2, tokens: 5), row(0, tokens: 7)])
        #expect(snapshot.sparkline(days: 4, now: now, calendar: calendar) == [0, 5, 0, 7])
    }

    @Test func theHeatmapIsRebuiltFromTheDays() {
        let snapshot = make(activity: [row(-1, tokens: 5)])
        let map = snapshot.heatmap(weeks: 4, now: now, calendar: calendar)
        #expect(map.columns.count == 4)
        let cells = map.columns.flatMap { $0 }.compactMap { $0 }
        #expect((cells.first { $0.day == day(-1) }?.level ?? 0) > 0)
        #expect(cells.filter { $0.level > 0 }.count == 1)
    }

    @Test func topModelsKeepTheFourLargest() {
        let models = [row(-1, tokens: 50, model: "a"), row(-2, tokens: 20, model: "b"),
                      row(-1, tokens: 15, model: "c"), row(-3, tokens: 10, model: "d"),
                      row(-1, tokens: 5, model: "e")]
        let snapshot = make(models: models)
        #expect(snapshot.models.map(\.name) == ["a", "b", "c", "d"])
        #expect(snapshot.models.first == .init(name: "a", tokens: 50, share: 0.5))
    }

    @Test func limitsAreMostUsedFirstAndCapped() {
        let limits = (0..<8).map { reading("s\($0)", used: Double($0 * 10)) }
        let snapshot = make(limits: limits)
        #expect(snapshot.limits.map(\.source) == ["s7", "s6", "s5", "s4", "s3", "s2"])
    }

    @Test func theLimitsSelectionFiltersSourcesAndWindows() {
        var selection = WidgetSelection()
        selection.limits.setShown(false, source: "claude-work")
        selection.limits.setShown(false, window: "5h", source: "codex")
        let limits = [reading("claude-work", used: 90), reading("codex", "5h", used: 50),
                      reading("codex", "7d", used: 40)]
        let snapshot = make(limits: limits, selection: selection)
        #expect(snapshot.limits.map(\.id) == ["codex|7d"])
    }

    @Test func eachWidgetCountsOnlyItsOwnHarnesses() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "codex", in: .summary)
        selection.setShown(false, harness: "claude", in: .heatmap)
        selection.setShown(false, harness: "codex", in: .models)
        let activity = [row(-1, tokens: 100, harness: "claude"), row(-1, tokens: 900, harness: "codex")]
        let models = [row(-1, tokens: 100, harness: "claude", model: "opus"),
                      row(-1, tokens: 900, harness: "codex", model: "gpt")]
        let snapshot = make(activity: activity, models: models, selection: selection)
        // Summary dropped codex, heatmap dropped claude, models dropped codex.
        #expect(snapshot.summary.week.tokens == 100)
        #expect(snapshot.heatmap.activityTokens == 900)
        #expect(snapshot.models.map(\.name) == ["opus"])
    }

    @Test func theFileHoldsNoCredentialsOrEmails() throws {
        // Only these keys reach the widget. A new field must be added here on purpose.
        var r = reading("claude-work", used: 40, resetsAt: now, fetchedAt: now)
        r.title = "Claude: Work"
        let snapshot = make(activity: [row(0, tokens: 1, cost: 1)],
                            models: [row(-1, tokens: 1, model: "m")], limits: [r])
        let json = try JSONSerialization.jsonObject(with: WidgetSnapshot.encoder.encode(snapshot)) as! [String: Any]
        #expect(Set(json.keys) == ["schema", "generatedAt", "summary", "heatmap", "models", "limits"])
        let summary = json["summary"] as! [String: Any]
        #expect(Set(summary.keys) == ["today", "week", "days"])
        let heatmap = json["heatmap"] as! [String: Any]
        #expect(Set(heatmap.keys) == ["days", "activeDays", "activityTokens"])
        let limit = (json["limits"] as! [[String: Any]])[0]
        #expect(Set(limit.keys) == ["source", "harness", "title", "window", "usedPercent", "resetsAt",
                                    "fetchedAt", "isStale"])
    }

    @Test func theStoreWritesAndReadsBack() throws {
        let temp = try TempDir()
        let store = WidgetSnapshotStore(file: temp.url.appending(path: "widgets/snapshot.json"))
        #expect(store.read() == nil)
        let snapshot = make(activity: [row(0, tokens: 3, cost: 0.5)],
                            limits: [reading("a", used: 91, resetsAt: now)])
        try store.write(snapshot)
        #expect(store.read() == snapshot)
    }

    @Test func aSnapshotFromAnOlderSchemaIsIgnored() throws {
        // The widgets show "Open Quiver once" until the app rewrites the file.
        let temp = try TempDir()
        let file = temp.url.appending(path: "snapshot.json")
        try Data(#"{"schema": 1}"#.utf8).write(to: file)
        #expect(WidgetSnapshotStore(file: file).read() == nil)
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
