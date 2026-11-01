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
              limits: [LimitReading] = [], compute: WidgetSnapshot.ComputeSection? = nil,
              selection: WidgetSelection = WidgetSelection()) -> WidgetSnapshot {
        WidgetSnapshot.make(activity: activity, models: models, limits: limits,
                            compute: compute, selection: selection, now: now, calendar: calendar)
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

    @Test func theSummarySeriesCoverEachCountedHarnessInOrder() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "cursor", in: .summary)
        let activity = [row(-1, tokens: 100, harness: "codex"),
                        row(-1, tokens: 50, harness: "cursor"),
                        row(-2, tokens: 30, harness: "claude"),
                        row(-1, tokens: 70, harness: "claude")]
        let snapshot = WidgetSnapshot.make(
            activity: activity, models: [], limits: [], selection: selection,
            harnessOrder: ["claude", "codex", "cursor"],
            harnessColors: ["claude": "#4ec9b0", "codex": "#5aa2ff"],
            now: now, calendar: calendar)
        #expect(snapshot.summary.series.map(\.harness) == ["claude", "codex"])
        #expect(snapshot.summary.series.allSatisfy { !$0.colorHex.isEmpty })
        #expect(snapshot.summary.series[0].days.map(\.tokens) == [30, 70])
        #expect(snapshot.summary.series[1].days.map(\.tokens) == [100])
        // The aggregate stays over the counted harnesses only.
        #expect(snapshot.summary.days.map(\.tokens) == [30, 170])
    }

    @Test func theSummaryTotalFollowsTheSelection() {
        var selection = WidgetSelection()
        selection.summaryShowsTotal = false
        let snapshot = make(activity: [row(-1, tokens: 100)], selection: selection)
        #expect(!snapshot.summary.showsTotal)
    }

    @Test func aSeriesSparklineZeroFillsQuietDays() {
        let series = WidgetSnapshot.HarnessSeries(
            harness: "claude", colorHex: "#4ec9b0",
            days: [.init(day: day(-2), tokens: 40), .init(day: day(0), tokens: 10)])
        let snapshot = make()
        #expect(snapshot.sparkline(series, days: 4, now: now, calendar: calendar) == [0, 40, 0, 10])
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
        #expect(snapshot.models.first == .init(name: "a", tokens: 50, share: 0.5, harnesses: ["claude"]))
    }

    @Test func topModelsCarryTheirHarnesses() {
        // Two harnesses ran opus; the logos show heaviest first.
        let models = [row(-1, tokens: 50, harness: "claude", model: "opus"),
                      row(-1, tokens: 30, harness: "codex", model: "opus"),
                      row(-1, tokens: 20, harness: "cursor", model: "composer")]
        let snapshot = make(models: models)
        #expect(snapshot.models.first?.harnesses == ["claude", "codex"])
        #expect(snapshot.models.last?.harnesses == ["cursor"])
    }

    @Test func aSnapshotWithoutModelHarnessesDecodes() throws {
        // A file an older build wrote has no harnesses key per model.
        let snapshot = make(models: [row(-1, tokens: 50, harness: "claude", model: "opus")])
        var json = try JSONSerialization.jsonObject(with: WidgetSnapshot.encoder.encode(snapshot)) as! [String: Any]
        json["models"] = (json["models"] as! [[String: Any]]).map {
            var model = $0
            model["harnesses"] = nil
            return model
        }
        let decoded = try WidgetSnapshot.decoder.decode(WidgetSnapshot.self,
                                                        from: JSONSerialization.data(withJSONObject: json))
        #expect(decoded.models.map(\.harnesses) == [[]])
        #expect(decoded.summary == snapshot.summary)
    }

    @Test func aSnapshotWithoutSeriesDecodesWithDefaults() throws {
        // A file an older build wrote has no series or showsTotal.
        let snapshot = make(activity: [row(-1, tokens: 50)])
        var json = try JSONSerialization.jsonObject(with: WidgetSnapshot.encoder.encode(snapshot)) as! [String: Any]
        var summary = json["summary"] as! [String: Any]
        summary["series"] = nil
        summary["showsTotal"] = nil
        json["summary"] = summary
        let decoded = try WidgetSnapshot.decoder.decode(WidgetSnapshot.self,
                                                        from: JSONSerialization.data(withJSONObject: json))
        #expect(decoded.summary.series.isEmpty)
        #expect(decoded.summary.showsTotal)
        #expect(decoded.summary.days == snapshot.summary.days)
    }

    @Test func limitsAreMostUsedFirstAndCapped() {
        let limits = (0..<14).map { reading("s\($0)", used: Double($0 * 5)) }
        let snapshot = make(limits: limits)
        // The cap covers the large widget's rows.
        #expect(snapshot.limits.count == 12)
        #expect(snapshot.limits.map(\.source) == (0..<12).reversed().map { "s\($0 + 2)" })
    }

    @Test func limitsGroupBySourceInPressureOrderUntilDragged() {
        // a's top reading is heavier than b's, so a's whole group lands
        // first even where a b reading sits between two a readings.
        let limits = [reading("a", "5h", used: 80), reading("b", "5h", used: 70),
                      reading("a", "7d", used: 20)]
        let snapshot = make(limits: limits)
        #expect(snapshot.limits.map(\.id) == ["a|5h", "a|7d", "b|5h"])
    }

    @Test func hidingAWindowKeepsTheGroupsRank() {
        // a's 90% window is hidden; its group keeps the rank that window
        // earned, so hiding never moves a source.
        var selection = WidgetSelection()
        selection.limits.setShown(false, window: "5h", source: "a")
        let limits = [reading("a", "5h", used: 90), reading("b", "5h", used: 80),
                      reading("a", "7d", used: 5)]
        let snapshot = make(limits: limits, selection: selection)
        #expect(snapshot.limits.map(\.id) == ["a|7d", "b|5h"])
    }

    @Test func theDraggedOrderDecidesTheSourceGroups() {
        var selection = WidgetSelection()
        selection.limits.move("b", before: "a", in: ["a", "b"])
        let limits = [reading("a", "5h", used: 90), reading("b", "5h", used: 10)]
        let snapshot = make(limits: limits, selection: selection)
        #expect(snapshot.limits.map(\.id) == ["b|5h", "a|5h"])
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
        var history = ComputeHistory()
        history.append("claude", TrendPoint(at: now, memoryBytes: 1, cpuPercent: 2, gpuPercent: 3))
        let compute = WidgetSnapshot.ComputeSection(history: history, order: ["claude"],
                                                    temperatureLines: [.cpu],
                                                    selection: WidgetSelection())
        let snapshot = make(activity: [row(0, tokens: 1, cost: 1)],
                            models: [row(-1, tokens: 1, model: "m")], limits: [r],
                            compute: compute)
        let json = try JSONSerialization.jsonObject(with: WidgetSnapshot.encoder.encode(snapshot)) as! [String: Any]
        #expect(Set(json.keys) == ["schema", "generatedAt", "summary", "heatmap", "models", "limits",
                                   "compute"])
        let summary = json["summary"] as! [String: Any]
        #expect(Set(summary.keys) == ["today", "week", "days", "series", "showsTotal"])
        let series = (summary["series"] as! [[String: Any]])[0]
        #expect(Set(series.keys) == ["harness", "colorHex", "days"])
        let heatmap = json["heatmap"] as! [String: Any]
        #expect(Set(heatmap.keys) == ["days", "activeDays", "activityTokens"])
        let limit = (json["limits"] as! [[String: Any]])[0]
        #expect(Set(limit.keys) == ["source", "harness", "title", "window", "usedPercent", "resetsAt",
                                    "fetchedAt", "isStale"])
        let model = (json["models"] as! [[String: Any]])[0]
        #expect(Set(model.keys) == ["name", "tokens", "share", "harnesses"])
        let computeJson = json["compute"] as! [String: Any]
        #expect(Set(computeJson.keys) == ["sampledAt", "harnesses", "temperatures"])
        let harness = (computeJson["harnesses"] as! [[String: Any]])[0]
        #expect(Set(harness.keys) == ["harness", "memoryBytes", "cpuPercent", "gpuPercent"])
    }

    @Test func theComputeSectionTakesOnlyWhatRanAtTheLastTick() {
        var history = ComputeHistory()
        // codex stopped a tick ago; its old point must not draw.
        history.append("codex", TrendPoint(at: now - 10, memoryBytes: 9, cpuPercent: 9, gpuPercent: nil))
        history.append("claude", TrendPoint(at: now, memoryBytes: 4, cpuPercent: 80, gpuPercent: 20))
        let section = WidgetSnapshot.ComputeSection(history: history,
                                                    order: ["codex", "claude"],
                                                    temperatureLines: [],
                                                    selection: WidgetSelection())
        #expect(section.sampledAt == now)
        #expect(section.harnesses.map(\.harness) == ["claude"])
    }

    @Test func theComputeSectionHonoursHiddenHarnessesAndChosenLines() {
        var selection = WidgetSelection()
        selection.setShown(false, harness: "codex", in: .compute)
        var history = ComputeHistory()
        history.append("claude", TrendPoint(at: now, memoryBytes: 4, cpuPercent: 80, gpuPercent: nil))
        history.append("codex", TrendPoint(at: now, memoryBytes: 2, cpuPercent: 10, gpuPercent: nil))
        history.appendTemperature(TemperaturePoint(at: now,
                                                   values: [.cpu: 61, .battery: 33]))
        let section = WidgetSnapshot.ComputeSection(history: history, order: ["claude", "codex"],
                                                    temperatureLines: [.battery],
                                                    selection: selection)
        #expect(section.harnesses.map(\.harness) == ["claude"])
        // The cpu line is off; only the chosen battery reading ships.
        #expect(section.temperatures == ["battery": 33])
    }

    @Test func theComputeSectionDropsTemperaturesBehindTheTick() {
        var history = ComputeHistory()
        history.appendTemperature(TemperaturePoint(at: now - 10,
                                                   values: [.cpu: 61]))
        // The newest tick read no sensors; the old point must not ship.
        history.recordTick(now)
        history.append("claude", TrendPoint(at: now, memoryBytes: 4, cpuPercent: 80,
                                            gpuPercent: nil))
        let section = WidgetSnapshot.ComputeSection(history: history, order: ["claude"],
                                                    temperatureLines: [.cpu],
                                                    selection: WidgetSelection())
        #expect(section.temperatures.isEmpty)
    }

    @Test func aComputeSectionWithoutTicksIsCollectingNotOld() {
        let section = WidgetSnapshot.ComputeSection(history: ComputeHistory(), order: [],
                                                    temperatureLines: [],
                                                    selection: WidgetSelection())
        #expect(section.sampledAt == nil)
        #expect(section.harnesses.isEmpty)
        #expect(!section.isOld(now: now))
        var stale = section
        stale.sampledAt = now - LimitReading.widgetMaxAge - 1
        #expect(stale.isOld(now: now))
    }

    @Test func aSnapshotWithoutComputeDecodes() throws {
        // A file an older build wrote has no compute key.
        let snapshot = make(activity: [row(-1, tokens: 50)])
        var json = try JSONSerialization.jsonObject(with: WidgetSnapshot.encoder.encode(snapshot)) as! [String: Any]
        json["compute"] = nil
        let decoded = try WidgetSnapshot.decoder.decode(WidgetSnapshot.self,
                                                        from: JSONSerialization.data(withJSONObject: json))
        #expect(decoded.compute == nil)
        #expect(decoded.limits == snapshot.limits)
    }

    @Test func theStoreWritesAndReadsBack() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
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
        defer { withExtendedLifetime(temp) {} }
        let file = temp.url.appending(path: "snapshot.json")
        try Data(#"{"schema": 1}"#.utf8).write(to: file)
        #expect(WidgetSnapshotStore(file: file).read() == nil)
    }

    @Test func aSnapshotFromANewerAppIsIgnored() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let file = temp.url.appending(path: "snapshot.json")
        try Data(#"{"schema": 99}"#.utf8).write(to: file)
        #expect(WidgetSnapshotStore(file: file).read() == nil)
    }

    @Test func theDefaultFileIsUnderTheRealHomeQuiverCache() {
        let file = WidgetSnapshotStore.defaultFile(home: URL(fileURLWithPath: "/Users/k"))
        #expect(file.path == "/Users/k/.swe/cache/widgets/snapshot.json")
    }
}
