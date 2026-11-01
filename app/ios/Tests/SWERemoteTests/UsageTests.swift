import XCTest
@testable import SWERemote

/// The shape of real `/v1/usage` and `/v1/app/overview` answers (built by
/// `swe.remote.usage.snapshot` and `swe.ledger.overview.build`),
/// with every figure, name and time made up. Fields the app ignores are
/// kept so decoding is shown to skip them.
enum UsageFixtures {
    static let usage = #"""
    {
      "harnesses": [
        {"name": "claude", "display_name": "Claude Code", "command": "claude", "version": "1.0.0", "notes": null,
         "aliases": ["cc"], "tags": ["agentic"], "primary_color": "#D97757", "state": "starred", "sessions_100d": 120,
         "rate_limit": {"status": "ok", "used_percent": 10, "remaining_percent": 90, "limit_reached": false,
           "reset_at": 1800400000.0, "window": "7d", "window_seconds": null, "plan_type": null,
           "remaining_units": null, "total_units": null, "windows": [], "extra_usage_usd": null, "on_demand": null,
           "fetched_at": 1799990000.0},
         "installed": true, "archived": null},
        {"name": "codex", "display_name": "Codex", "state": "starred", "sessions_100d": 5,
         "rate_limit": {"status": "ok", "used_percent": 30, "remaining_percent": 70, "limit_reached": false,
           "reset_at": 1800500000.0, "window": null, "window_seconds": 604800, "plan_type": "pro",
           "remaining_units": null, "total_units": null, "windows": [], "extra_usage_usd": null, "on_demand": null,
           "fetched_at": 1799999900.0},
         "installed": true, "archived": null},
        {"name": "opencode", "display_name": "OpenCode", "state": "active", "sessions_100d": 50, "rate_limit": null,
         "installed": true, "archived": null},
        {"name": "cursor", "display_name": null, "state": "starred", "sessions_100d": 70,
         "rate_limit": {"status": "ok", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
           "reset_at": 1801000000.0, "window": "included", "window_seconds": 2678400, "plan_type": "free",
           "remaining_units": null, "total_units": null,
           "windows": [
             {"window": "included", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": 1801000000.0, "window_seconds": 2678400},
             {"window": "grok_bot", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": null, "window_seconds": 604800, "unavailable": true, "as_of": 1799990000.0}],
           "extra_usage_usd": null,
           "on_demand": {"state": "unlimited", "scope": "team-member", "used_usd": 0.0, "limit_usd": null},
           "fetched_at": 1799999900.0},
         "installed": true, "archived": null},
        {"name": "devin", "display_name": null, "state": "starred", "sessions_100d": 80,
         "rate_limit": {"status": "ok", "used_percent": 90, "remaining_percent": 10, "limit_reached": false,
           "reset_at": 1800300000.0, "window": "7d", "window_seconds": 604800, "plan_type": "teams",
           "remaining_units": null, "total_units": null,
           "windows": [
             {"window": "1d", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": 1800050000.0, "window_seconds": 86400},
             {"window": "7d", "used_percent": 90, "remaining_percent": 10, "limit_reached": false,
              "reset_at": 1800300000.0, "window_seconds": 604800}],
           "extra_usage_usd": 100.0, "on_demand": null, "fetched_at": 1799999900.0},
         "installed": true, "archived": null},
        {"name": "qwen-code", "display_name": "Qwen Code", "state": "active", "sessions_100d": null, "rate_limit": null,
         "installed": true, "archived": null}
      ],
      "window_days": 100,
      "server_time": 1800000000.0,
      "machine": {"id": "hub0001", "name": "hub.local", "os": "darwin", "online": true},
      "machines": [
        {"machine": {"id": "hub0001", "name": "hub.local", "os": "darwin", "online": true},
         "harnesses": [], "window_days": 100, "reported_at": 1800000000.0, "received_at": 1800000000.0, "hub": true},
        {"machine": {"id": "sat0002", "name": "laptop.local", "online": false},
         "harnesses": [
           {"name": "gemini", "display_name": "Gemini CLI", "state": "starred", "sessions_100d": 3,
            "rate_limit": {"status": "auth-required", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": null, "window": null, "window_seconds": null, "plan_type": null, "remaining_units": null,
              "total_units": null, "windows": [], "extra_usage_usd": null, "on_demand": null, "fetched_at": null}}],
         "window_days": 100, "reported_at": 1799998000.0, "received_at": 1799998010.0, "hub": false}
      ]
    }
    """#

    static let overview = #"""
    {
      "period": "7d", "time_zone": "UTC",
      "range": {"start_day": "2026-01-01", "end_day": "2026-01-08"},
      "first_day": "2025-12-01",
      "totals": {"tokens": 20000000, "input": 1000000, "output": 500000, "cache_read": 18000000,
                 "cache_write": 500000, "reasoning": 0, "cost_usd": 1234.5,
                 "unpriced_tokens": 2000000, "unpriced_models": ["x-test-1"]},
      "harnesses": [
        {"harness": "claude", "tokens": 10000000, "cost_usd": 600.0, "share": 0.5, "models": 2},
        {"harness": "devin", "tokens": 6000000, "cost_usd": 400.0, "share": 0.3, "models": 3},
        {"harness": "codex", "tokens": 3000000, "cost_usd": 200.0, "share": 0.15, "models": 2},
        {"harness": "opencode", "tokens": 1000000, "cost_usd": 34.5, "share": 0.05, "models": 1}
      ],
      "days": [
        {"date": "2026-01-08", "tokens": 3000000, "input": 100000, "output": 50000, "cached": 2850000, "cost_usd": 200.0},
        {"date": "2026-01-07", "tokens": 2000000, "input": 100000, "output": 50000, "cached": 1850000, "cost_usd": 150.0},
        {"date": "2026-01-06", "tokens": 2000000, "cost_usd": 150.0},
        {"date": "2026-01-04", "tokens": 3000000, "cost_usd": 200.0},
        {"date": "2026-01-03", "tokens": 4000000, "cost_usd": 250.0},
        {"date": "2026-01-02", "tokens": 3000000, "cost_usd": 134.5},
        {"date": "2026-01-01", "tokens": 3000000, "cost_usd": 150.0}
      ],
      "quick": {"active_days": 7, "tokens_per_day": 2857142, "busiest_day": "2026-01-03"},
      "top_models": [{"model": "model-a", "tokens": 10000000, "share": 0.5, "harnesses": ["claude"]}],
      "heatmap": {"weeks": 26, "days": [{"date": "2026-01-09", "future": true}]},
      "scanned_at": 1800000000.0, "notes": [], "machine": "hub.local"
    }
    """#

    static func decode<T: Decodable>(_ json: String, as: T.Type = T.self) throws -> T {
        try Hub.decoder.decode(T.self, from: Data(json.utf8))
    }
}

final class UsageDecodingTests: XCTestCase {
    func testUsageRowsDecode() throws {
        let body = try UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self)
        let rows = try XCTUnwrap(body.harnesses)
        XCTAssertEqual(rows.map(\.name), ["claude", "codex", "opencode", "cursor", "devin", "qwen-code"])
        XCTAssertEqual(rows[0].sessions100d, 120)
        XCTAssertNil(rows[5].sessions100d)
        XCTAssertNil(rows[2].rateLimit)
        XCTAssertEqual(rows[3].title, "Cursor")
        XCTAssertEqual(rows[0].title, "Claude Code")
        XCTAssertEqual(body.windowDays, 100)
        XCTAssertEqual(body.machine?.displayName, "hub")
    }

    func testSingleWindowToolsUseTheRowItself() throws {
        let rows = try XCTUnwrap(UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self).harnesses)
        let codex = try XCTUnwrap(rows[1].rateLimit?.displayWindows)
        XCTAssertEqual(codex.count, 1)
        XCTAssertEqual(codex[0].label, "Weekly")
        XCTAssertEqual(codex[0].usedPercent, 30)
        XCTAssertEqual(rows[0].rateLimit?.displayWindows.map(\.label), ["Weekly"])
    }

    func testMultiWindowAndExtras() throws {
        let rows = try XCTUnwrap(UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self).harnesses)
        let devin = try XCTUnwrap(rows[4].rateLimit)
        XCTAssertEqual(devin.displayWindows.map(\.label), ["Daily", "Weekly"])
        XCTAssertEqual(devin.extraUsageUsd, 100)
        XCTAssertEqual(LimitCard.extraLines(devin), ["$100 extra usage left"])
        let cursor = try XCTUnwrap(rows[3].rateLimit)
        XCTAssertEqual(cursor.onDemand?.state, "unlimited")
        XCTAssertEqual(cursor.displayWindows[1].unavailable, true)
        XCTAssertEqual(cursor.displayWindows[1].asOf, 1_799_990_000)
        XCTAssertEqual(cursor.displayWindows.map(\.label), ["Included", "Grok bot"])
        XCTAssertEqual(LimitCard.extraLines(cursor), ["On-demand: $0, no limit"])
    }

    func testMachinesAndStatus() throws {
        let body = try UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self)
        let machines = body.allMachines
        XCTAssertEqual(machines.map(\.id), ["hub0001", "sat0002"])
        XCTAssertEqual(machines[1].machine?.online, false)
        let gemini = try XCTUnwrap(machines[1].rows.first?.rateLimit)
        XCTAssertFalse(gemini.isOK)
        XCTAssertTrue(gemini.displayWindows.isEmpty)
        XCTAssertEqual(UsageFormat.limitStatus(gemini.status, machine: "laptop"),
                       "Signed out. Log in to it again on laptop.")
    }

    func testOlderHubWithoutMachinesFallsBackToItsOwnRows() throws {
        let body = try UsageFixtures.decode(#"{"harnesses": [{"name": "codex"}], "server_time": 10}"#, as: UsageResponse.self)
        XCTAssertEqual(body.allMachines.count, 1)
        XCTAssertEqual(body.allMachines[0].hub, true)
        XCTAssertEqual(body.allMachines[0].rows.map(\.name), ["codex"])
        XCTAssertEqual(body.allMachines[0].reportedAt, 10)
    }

    func testOverviewDecodes() throws {
        let body = try UsageFixtures.decode(UsageFixtures.overview, as: OverviewResponse.self)
        XCTAssertEqual(body.totals?.tokens, 20_000_000)
        XCTAssertEqual(body.totals?.unpricedTokens, 2_000_000)
        XCTAssertEqual(body.harnesses?.map(\.harness), ["claude", "devin", "codex", "opencode"])
        XCTAssertEqual(body.days?.count, 7)
        XCTAssertEqual(body.range?.startDay, "2026-01-01")
        XCTAssertEqual(body.machine, "hub.local")
        XCTAssertEqual(body.firstDay, "2025-12-01")
        XCTAssertEqual(body.days?.first?.cached, 2_850_000)
        XCTAssertEqual(body.quick?.busiestDay, "2026-01-03")
        XCTAssertEqual(body.topModels?.first?.harnesses, ["claude"])
        XCTAssertEqual(body.heatmap?.days?.first?.future, true)
    }

    @MainActor func testOverviewMachinesDecodeAndWordTheSource() throws {
        let json = #"""
        {"machine": "Home", "machine_filter": "all", "machines": [
          {"id": "hub", "name": "Home", "hub": true, "tokens": 300, "cost_usd": 1.0, "reported_at": null},
          {"id": "mbp", "name": "MacBook", "hub": false, "tokens": 100, "cost_usd": 2.0, "reported_at": 5}]}
        """#
        let all = try UsageFixtures.decode(json, as: OverviewResponse.self)
        XCTAssertEqual(all.machines?.map(\.id), ["hub", "mbp"])
        XCTAssertEqual(all.machines?.last?.costUsd, 2.0)
        XCTAssertEqual(OverviewFormat.source(all), "From the logs on 2 machines")
        let one = try UsageFixtures.decode(json.replacingOccurrences(of: "\"all\"", with: "\"mbp\""),
                                           as: OverviewResponse.self)
        XCTAssertEqual(OverviewFormat.source(one), "From the daily totals MacBook reports")
        let machines = try XCTUnwrap(all.machines)
        XCTAssertEqual(MachineChips.title(machines[1], total: 400), "MacBook 25%")
        XCTAssertEqual(MachineChips.title(machines[1], total: 0), "MacBook")
        // The chips show the period on screen's own machines.
        let week = try UsageFixtures.decode(json.replacingOccurrences(of: "300", with: "900"),
                                            as: OverviewResponse.self)
        let model = UsageModel(overviews: [.week: week, .today: all], period: .today)
        XCTAssertEqual(model.shownMachines.first?.tokens, 300)
        model.period = .week
        XCTAssertEqual(model.shownMachines.first?.tokens, 900)
        // An older hub sends no machines: the hub's own line, as before.
        let old = try UsageFixtures.decode(#"{"machine": "Home"}"#, as: OverviewResponse.self)
        XCTAssertNil(old.machines)
        XCTAssertEqual(OverviewFormat.source(old)?.hasPrefix("From the logs on"), true)
    }

    func testEmptyBodiesDecode() throws {
        let overview = try UsageFixtures.decode("{}", as: OverviewResponse.self)
        XCTAssertNil(overview.totals)
        let usage = try UsageFixtures.decode("{}", as: UsageResponse.self)
        XCTAssertEqual(usage.allMachines.count, 1)
        XCTAssertTrue(usage.allMachines[0].rows.isEmpty)
    }

    @MainActor func testHarnessNameFromUsageRows() throws {
        let model = UsageModel(usage: try UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self))
        XCTAssertEqual(model.harnessName("claude"), "Claude Code")
        XCTAssertEqual(model.harnessName("gemini"), "Gemini CLI")
        XCTAssertEqual(model.harnessName("pi"), "Pi")
    }
}

final class UsageFormatTests: XCTestCase {
    func testPercentClampsAndRounds() {
        XCTAssertEqual(UsageFormat.percent(42.4), "42%")
        XCTAssertEqual(UsageFormat.percent(130), "100%")
        XCTAssertEqual(UsageFormat.percent(-3), "0%")
        XCTAssertEqual(UsageFormat.percent(nil), "0%")
    }

    func testToneThresholds() {
        XCTAssertEqual(UsageFormat.tone(usedPercent: 80), .normal)
        XCTAssertEqual(UsageFormat.tone(usedPercent: 81), .warning)
        XCTAssertEqual(UsageFormat.tone(usedPercent: 99), .warning)
        XCTAssertEqual(UsageFormat.tone(usedPercent: 100), .danger)
        XCTAssertEqual(UsageFormat.tone(usedPercent: 40, limitReached: true), .danger)
    }

    func testResetCountdown() {
        let now = 1_000_000.0
        XCTAssertEqual(UsageFormat.resetCountdown(now + 2 * 3600 + 10 * 60 + 30, now: now), "resets in 2 h 10 m")
        XCTAssertEqual(UsageFormat.resetCountdown(now + 20, now: now), "resets in 1 m")
        XCTAssertEqual(UsageFormat.resetCountdown(now + 45 * 60, now: now), "resets in 45 m")
        XCTAssertEqual(UsageFormat.resetCountdown(now + 3 * 86_400 + 4 * 3600 + 100, now: now), "resets in 3 d 4 h")
        XCTAssertEqual(UsageFormat.resetCountdown(now + 86_400 + 600, now: now), "resets in 1 d")
        XCTAssertEqual(UsageFormat.resetCountdown(now + 2 * 3600 + 30, now: now), "resets in 2 h")
        XCTAssertEqual(UsageFormat.resetCountdown(now - 5, now: now), "resets now")
        XCTAssertEqual(UsageFormat.resetCountdown(nil, now: now), "")
        XCTAssertEqual(UsageFormat.resetCountdown(0, now: now), "")
    }

    func testWindowLabels() {
        XCTAssertEqual(UsageFormat.windowLabel("5h", seconds: nil), "5 h")
        XCTAssertEqual(UsageFormat.windowLabel("7d", seconds: nil), "Weekly")
        XCTAssertEqual(UsageFormat.windowLabel("1d", seconds: nil), "Daily")
        XCTAssertEqual(UsageFormat.windowLabel("30d", seconds: nil), "30 days")
        XCTAssertEqual(UsageFormat.windowLabel("7d_sonnet", seconds: nil), "Weekly, Sonnet")
        XCTAssertEqual(UsageFormat.windowLabel("7d_oauth_apps", seconds: nil), "Weekly, Oauth apps")
        XCTAssertEqual(UsageFormat.windowLabel("included", seconds: 2_678_400), "Included")
        XCTAssertEqual(UsageFormat.windowLabel(nil, seconds: 604_800), "Weekly")
        XCTAssertEqual(UsageFormat.windowLabel(nil, seconds: 18_000), "5 h")
        XCTAssertEqual(UsageFormat.windowLabel(nil, seconds: nil), "Quota")
    }

    func testCompactTokens() {
        XCTAssertEqual(UsageFormat.tokens(0), "0")
        XCTAssertEqual(UsageFormat.tokens(842), "842")
        XCTAssertEqual(UsageFormat.tokens(12_340), "12.3K")
        // The Mac's and the web's figures: no ".0", and a decimal past 100.
        XCTAssertEqual(UsageFormat.tokens(12_000), "12K")
        XCTAssertEqual(UsageFormat.tokens(150_400), "150.4K")
        XCTAssertEqual(UsageFormat.tokens(1_234_567), "1.2M")
        XCTAssertEqual(UsageFormat.tokens(123_456_789), "123.5M")
        XCTAssertEqual(UsageFormat.tokens(13_000_000_000), "13B")
        XCTAssertEqual(UsageFormat.tokens(999_960), "1M")
        XCTAssertEqual(UsageFormat.tokens(999_400), "999.4K")
        XCTAssertEqual(UsageFormat.tokens(99_960), "100K")
        XCTAssertEqual(UsageFormat.tokens(-5), "0")
        XCTAssertEqual(UsageFormat.tokens(nil), "0")
    }

    func testUSD() {
        XCTAssertEqual(UsageFormat.usd(0.42), "$0.42")
        XCTAssertEqual(UsageFormat.usd(12.3), "$12.30")
        XCTAssertEqual(UsageFormat.usd(99.5), "$99.50")
        // Whole dollars from $100, as the Mac and the web print a spend.
        XCTAssertEqual(UsageFormat.usd(123.45), "$123")
        XCTAssertEqual(UsageFormat.usd(5432.1), "$5,432")
        XCTAssertEqual(UsageFormat.usd(0.004), "<$0.01")
        XCTAssertEqual(UsageFormat.usd(0), "$0")
        XCTAssertEqual(UsageFormat.usd(nil), "$0")
        XCTAssertEqual(UsageFormat.usdAxis(800), "$800")
        XCTAssertEqual(UsageFormat.usdAxis(1260), "$1.3K")
        XCTAssertEqual(UsageFormat.usdAxis(0), "$0")
        // A $1 axis ticks at $0.50; rounding printed "$1" for both.
        XCTAssertEqual(UsageFormat.usdAxis(0.5), "$0.50")
        XCTAssertEqual(UsageFormat.usdAxis(1), "$1")
        XCTAssertEqual(UsageFormat.usdAxis(0.25), "$0.25")
        XCTAssertEqual(UsageFormat.usdAxis(12.5), "$12.5")
    }

    func testAgoAndStaleness() {
        XCTAssertEqual(UsageFormat.ago(30), "just now")
        XCTAssertEqual(UsageFormat.ago(4 * 60 + 10), "4 m ago")
        XCTAssertEqual(UsageFormat.ago(3 * 3600), "3 h ago")
        XCTAssertEqual(UsageFormat.ago(2 * 86_400 + 5), "2 d ago")
        XCTAssertFalse(UsageFormat.isStale(fetchedAt: 1000, snapshotAt: 1300))
        XCTAssertTrue(UsageFormat.isStale(fetchedAt: 1000, snapshotAt: 1400))
        XCTAssertFalse(UsageFormat.isStale(fetchedAt: nil, snapshotAt: 1400))
        XCTAssertFalse(UsageFormat.isStale(fetchedAt: 0, snapshotAt: 1400))
    }

    func testReadingAgeAddsTimeOnTheHub() {
        // Read 100 s before the satellite's snapshot, which then sat on the
        // hub for 50 s; the two clocks are a minute apart and it doesn't matter.
        XCTAssertEqual(UsageFormat.readingAge(fetchedAt: 900, reportedAt: 1000, receivedAt: 1060, serverTime: 1110), 150)
        XCTAssertEqual(UsageFormat.readingAge(fetchedAt: 900, reportedAt: nil, receivedAt: nil, serverTime: nil), 0)
    }

    func testPlanAndNames() {
        XCTAssertEqual(UsageFormat.plan("pro_team"), "Pro team")
        XCTAssertNil(UsageFormat.plan(nil))
        XCTAssertNil(UsageFormat.plan(""))
        XCTAssertEqual(UsageFormat.harnessName(id: "cursor", displayName: nil), "Cursor")
        XCTAssertEqual(UsageFormat.harnessName(id: "cursor", displayName: ""), "Cursor")
        XCTAssertEqual(UsageFormat.harnessName(id: "claude", displayName: "Claude Code"), "Claude Code")
    }

    func testDailySeriesFillsDaysOff() throws {
        let body = try UsageFixtures.decode(UsageFixtures.overview, as: OverviewResponse.self)
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let series = UsageFormat.dailySeries(body.days ?? [], start: body.range?.startDay, end: body.range?.endDay, timeZone: utc)
        XCTAssertEqual(series.count, 8)
        XCTAssertEqual(series.first?.cost, 150)
        XCTAssertEqual(series[4].cost, 0, "the 5th had no usage")
        XCTAssertEqual(series.last?.tokens, 3_000_000)
        XCTAssertEqual(series[1].date.timeIntervalSince(series[0].date), 86_400)
    }

    func testDailySeriesWithoutRangeUsesTheDaysSent() {
        let days = [OverviewResponse.Day(date: "2026-10-03", tokens: 5, costUsd: 1),
                    OverviewResponse.Day(date: "2026-10-01", tokens: 2, costUsd: 0.5)]
        let series = UsageFormat.dailySeries(days, start: nil, end: nil)
        XCTAssertEqual(series.map(\.tokens), [2, 0, 5])
        XCTAssertTrue(UsageFormat.dailySeries([], start: nil, end: nil).isEmpty)
        XCTAssertTrue(UsageFormat.dailySeries([], start: "2026-10-05", end: "2026-10-01").isEmpty)
    }
}

final class OverviewFormatTests: XCTestCase {
    private let en = Locale(identifier: "en_US")

    func testThePeriodsMatchTheHubAndTheMac() {
        XCTAssertEqual(UsagePeriod.allCases.map(\.rawValue), ["today", "7d", "month", "30d", "90d", "365d", "all"])
        XCTAssertEqual(UsagePeriod.allCases.map(\.title),
                       ["Today", "7 days", "This month", "30 days", "90 days", "365 days", "All time"])
    }

    func testQuickChips() throws {
        let body = try UsageFixtures.decode(UsageFixtures.overview, as: OverviewResponse.self)
        XCTAssertEqual(OverviewFormat.quickChips(body.quick, locale: en), [
            .init(value: "7", label: "active days"),
            .init(value: "2.9M", label: "a day"),
            .init(value: "Jan 3", label: "busiest"),
        ])
        let one = OverviewResponse.Quick(activeDays: 1, tokensPerDay: 5, busiestDay: nil)
        XCTAssertEqual(OverviewFormat.quickChips(one, locale: en).map(\.label), ["active day", "a day", "busiest"])
        XCTAssertEqual(OverviewFormat.quickChips(one, locale: en)[2].value, "None")
    }

    func testModelShareAndHarnessMarks() {
        XCTAssertEqual(OverviewFormat.share(0.5), "50.0%")
        XCTAssertEqual(OverviewFormat.share(0.1234), "12.3%")
        let marks = OverviewFormat.harnessMarks(["claude", "codex", "devin", "opencode", "pi"])
        XCTAssertEqual(marks.shown, ["claude", "codex", "devin"])
        XCTAssertEqual(marks.more, "+2")
        XCTAssertNil(OverviewFormat.harnessMarks(["claude"]).more)
    }

    func testAllTimeChartsFromTheFirstDay() throws {
        let body = try UsageFixtures.decode(UsageFixtures.overview, as: OverviewResponse.self)
        XCTAssertEqual(OverviewFormat.chartRange(body).start, "2026-01-01")
        let all = try UsageFixtures.decode("""
        {"period": "all", "range": null, "first_day": "2025-12-01",
         "days": [{"date": "2026-01-08", "tokens": 1}, {"date": "2025-12-02", "tokens": 1}]}
        """, as: OverviewResponse.self)
        XCTAssertEqual(OverviewFormat.chartRange(all).start, "2025-12-01")
        XCTAssertEqual(OverviewFormat.chartRange(all).end, "2026-01-08")
    }

    func testALongSeriesKeepsTheNewestDays() throws {
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let days = [OverviewResponse.Day(date: "2026-01-10", tokens: 9, costUsd: 1)]
        let series = UsageFormat.dailySeries(days, start: "2020-01-01", end: "2026-01-10", timeZone: utc, maxDays: 30)
        XCTAssertEqual(series.count, 30)
        XCTAssertEqual(series.last?.tokens, 9, "the newest day survives the cap")
    }

    /// 21 cells: three Sunday-first weeks, the last two days still to come.
    private func heatmap() throws -> OverviewResponse.Heatmap {
        var cells: [String] = []
        var date = try XCTUnwrap(OverviewFormat.day("2026-01-25"))
        for i in 0..<21 {
            let iso = ISO8601DateFormatter.string(from: date, timeZone: TimeZone(identifier: "UTC")!,
                                                  formatOptions: [.withFullDate])
            cells.append(i >= 19 ? #"{"date": "\#(iso)", "future": true}"#
                         : #"{"date": "\#(iso)", "tokens": \#(i * 1000), "cost_usd": 1.5, "level": \#(i % 5)}"#)
            date = date.addingTimeInterval(86_400)
        }
        return try UsageFixtures.decode(#"{"weeks": 3, "days": [\#(cells.joined(separator: ","))]}"#,
                                        as: OverviewResponse.Heatmap.self)
    }

    func testHeatmapColumnsKeepTheNewestWeeks() throws {
        let map = try heatmap()
        let all = OverviewFormat.columns(map, weeks: 3)
        XCTAssertEqual(all.count, 3)
        XCTAssertEqual(all[0][0]?.date, "2026-01-25")
        XCTAssertNil(all[2][5], "a future day is a gap")
        XCTAssertNil(all[2][6])
        let two = OverviewFormat.columns(map, weeks: 2)
        XCTAssertEqual(two.first?[0]?.date, "2026-02-01")
    }

    func testWeeksThatFitTheCard() {
        // 24pt labels, then 15pt a week.
        XCTAssertEqual(OverviewFormat.weeksThatFit(width: 24 + 15 * 20, available: 26), 20)
        XCTAssertEqual(OverviewFormat.weeksThatFit(width: 2000, available: 26), 26)
        XCTAssertEqual(OverviewFormat.weeksThatFit(width: 50, available: 26), 8)
        XCTAssertEqual(OverviewFormat.weeksThatFit(width: 50, available: 3), 3)
        XCTAssertEqual(OverviewFormat.weeksThatFit(width: 300, available: 0), 0)
    }

    func testMonthLabelsSitOverEachMonthsFirstWeek() throws {
        let columns = OverviewFormat.columns(try heatmap(), weeks: 3)
        // Jan 25 starts week 0, but Feb starts week 1: the first label gives way.
        XCTAssertEqual(OverviewFormat.monthLabels(columns, locale: en), [nil, "Feb", nil])
    }

    func testCellDetailAndDays() throws {
        let map = try heatmap()
        let cells = try XCTUnwrap(map.days)
        XCTAssertEqual(OverviewFormat.cellDetail(cells[0]), "No usage")
        XCTAssertEqual(OverviewFormat.cellDetail(cells[3]), "3K tokens · $1.50")
        XCTAssertEqual(OverviewFormat.longDay("2026-01-03", locale: en), "Sat, Jan 3")
        XCTAssertNil(OverviewFormat.shortDay("junk", locale: en))
    }

    func testDailyTablePagesInThirties() throws {
        let days = (1...14).map { OverviewResponse.Day(date: String(format: "2026-01-%02d", 15 - $0), tokens: $0, costUsd: 0) }
        XCTAssertEqual(OverviewFormat.dailyRows(days, shown: 10).count, 10)
        XCTAssertEqual(OverviewFormat.dailyRows(days, shown: 10).first?.date, "2026-01-14")
        XCTAssertEqual(OverviewFormat.dailyRows(days, shown: 40).count, 14)
        XCTAssertEqual(OverviewFormat.dailyMore(total: 14, shown: 10), 4)
        XCTAssertEqual(OverviewFormat.dailyMore(total: 400, shown: 10), 30)
        XCTAssertEqual(OverviewFormat.dailyMore(total: 400, shown: 400), 0)
        let body = try UsageFixtures.decode(UsageFixtures.overview, as: OverviewResponse.self)
        XCTAssertEqual(OverviewFormat.dailyCells(try XCTUnwrap(body.days?.first)),
                       ["2026-01-08", "3M", "100K", "50K", "2.9M", "$200"])
    }
}
