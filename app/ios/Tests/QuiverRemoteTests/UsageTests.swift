import XCTest
@testable import QuiverRemote

/// The shape of real `/v1/usage` and `/v1/app/overview` answers (built by
/// `quiver.remote.usage.snapshot` and `quiver.ledger.overview.build`),
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
        XCTAssertEqual(LimitCard.extraLines(devin), ["$100.00 extra usage left"])
        let cursor = try XCTUnwrap(rows[3].rateLimit)
        XCTAssertEqual(cursor.onDemand?.state, "unlimited")
        XCTAssertEqual(cursor.displayWindows[1].unavailable, true)
        XCTAssertEqual(cursor.displayWindows[1].asOf, 1_799_990_000)
        XCTAssertEqual(cursor.displayWindows.map(\.label), ["Included", "Grok bot"])
        XCTAssertEqual(LimitCard.extraLines(cursor), ["On-demand: $0.00, no limit"])
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
        XCTAssertEqual(UsageFormat.tokens(1_234_567), "1.2M")
        XCTAssertEqual(UsageFormat.tokens(123_456_789), "123M")
        XCTAssertEqual(UsageFormat.tokens(13_000_000_000), "13.0B")
        XCTAssertEqual(UsageFormat.tokens(999_960), "1.0M")
        XCTAssertEqual(UsageFormat.tokens(999_400), "999K")
        XCTAssertEqual(UsageFormat.tokens(99_960), "100K")
        XCTAssertEqual(UsageFormat.tokens(nil), "0")
    }

    func testUSD() {
        XCTAssertEqual(UsageFormat.usd(0.42), "$0.42")
        XCTAssertEqual(UsageFormat.usd(12.3), "$12.30")
        XCTAssertEqual(UsageFormat.usd(999.994), "$999.99")
        XCTAssertEqual(UsageFormat.usd(5432.1), "$5,432")
        XCTAssertEqual(UsageFormat.usd(nil), "$0.00")
        XCTAssertEqual(UsageFormat.usdAxis(800), "$800")
        XCTAssertEqual(UsageFormat.usdAxis(1260), "$1.3K")
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
