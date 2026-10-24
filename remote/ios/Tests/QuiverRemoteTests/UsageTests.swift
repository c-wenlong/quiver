import XCTest
@testable import QuiverRemote

/// Trimmed from real `/v1/usage` and `/v1/app/overview` answers (built by
/// `quiver.remote.usage.snapshot` and `quiver.ledger.overview.build`),
/// with machine names and ids replaced. Kept fields the app ignores so
/// decoding is shown to skip them.
enum UsageFixtures {
    static let serverTime: Double = 1_792_827_652.9

    static let usage = #"""
    {
      "harnesses": [
        {"name": "claude", "display_name": "Claude Code", "command": "claude", "version": "2.1.0", "notes": null,
         "aliases": ["cc"], "tags": ["agentic"], "primary_color": "#D97757", "state": "starred", "sessions_100d": 773,
         "rate_limit": {"status": "ok", "used_percent": 11, "remaining_percent": 89, "limit_reached": false,
           "reset_at": 1793304000.481687, "window": "7d", "window_seconds": null, "plan_type": null,
           "remaining_units": null, "total_units": null, "windows": [], "extra_usage_usd": null, "on_demand": null,
           "fetched_at": 1792753303.8406641},
         "installed": true, "archived": null},
        {"name": "codex", "display_name": "Codex", "state": "starred", "sessions_100d": 5,
         "rate_limit": {"status": "ok", "used_percent": 33, "remaining_percent": 67, "limit_reached": false,
           "reset_at": 1793322577.0, "window": null, "window_seconds": 604800, "plan_type": "self_serve_business_prolite",
           "remaining_units": null, "total_units": null, "windows": [], "extra_usage_usd": null, "on_demand": null,
           "fetched_at": 1792827600.0},
         "installed": true, "archived": null},
        {"name": "opencode", "display_name": "OpenCode", "state": "active", "sessions_100d": 54, "rate_limit": null,
         "installed": true, "archived": null},
        {"name": "cursor", "display_name": null, "state": "starred", "sessions_100d": 71,
         "rate_limit": {"status": "ok", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
           "reset_at": 1794460593.0, "window": "included", "window_seconds": 2678400, "plan_type": "free",
           "remaining_units": null, "total_units": null,
           "windows": [
             {"window": "included", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": 1794460593.0, "window_seconds": 2678400},
             {"window": "grok_bot", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": null, "window_seconds": 604800, "unavailable": true, "as_of": 1792820000.0}],
           "extra_usage_usd": null,
           "on_demand": {"state": "unlimited", "scope": "team-member", "used_usd": 0.0, "limit_usd": null},
           "fetched_at": 1792827635.018888},
         "installed": true, "archived": null},
        {"name": "devin", "display_name": null, "state": "starred", "sessions_100d": 88,
         "rate_limit": {"status": "ok", "used_percent": 98, "remaining_percent": 2, "limit_reached": false,
           "reset_at": 1792915200.0, "window": "7d", "window_seconds": 604800, "plan_type": "teams",
           "remaining_units": null, "total_units": null,
           "windows": [
             {"window": "1d", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": 1792828800.0, "window_seconds": 86400},
             {"window": "7d", "used_percent": 98, "remaining_percent": 2, "limit_reached": false,
              "reset_at": 1792915200.0, "window_seconds": 604800}],
           "extra_usage_usd": 8219.74, "on_demand": null, "fetched_at": 1792827635.0189388},
         "installed": true, "archived": null},
        {"name": "qwen-code", "display_name": "Qwen Code", "state": "active", "sessions_100d": null, "rate_limit": null,
         "installed": true, "archived": null}
      ],
      "window_days": 100,
      "server_time": 1792827652.9,
      "machine": {"id": "hub0001", "name": "studio.local", "os": "darwin", "online": true},
      "machines": [
        {"machine": {"id": "hub0001", "name": "studio.local", "os": "darwin", "online": true},
         "harnesses": [], "window_days": 100, "reported_at": 1792827652.9, "received_at": 1792827652.9, "hub": true},
        {"machine": {"id": "sat0002", "name": "laptop.local", "online": false},
         "harnesses": [
           {"name": "gemini", "display_name": "Gemini CLI", "state": "starred", "sessions_100d": 3,
            "rate_limit": {"status": "auth-required", "used_percent": 0, "remaining_percent": 100, "limit_reached": false,
              "reset_at": null, "window": null, "window_seconds": null, "plan_type": null, "remaining_units": null,
              "total_units": null, "windows": [], "extra_usage_usd": null, "on_demand": null, "fetched_at": null}}],
         "window_days": 100, "reported_at": 1792826000.0, "received_at": 1792826010.0, "hub": false}
      ]
    }
    """#

    static let overview = #"""
    {
      "period": "7d", "time_zone": "Asia/Singapore",
      "range": {"start_day": "2026-10-17", "end_day": "2026-10-24"},
      "first_day": "2026-02-25",
      "totals": {"tokens": 12997498697, "input": 202181994, "output": 41547602, "cache_read": 12564563122,
                 "cache_write": 182487527, "reasoning": 6718452, "cost_usd": 5403.8346387200045,
                 "unpriced_tokens": 1504919, "unpriced_models": ["x-test-1"]},
      "harnesses": [
        {"harness": "claude", "tokens": 8417112203, "cost_usd": 2913.8321453000012, "share": 0.6475947718265811, "models": 2},
        {"harness": "devin", "tokens": 4111253778, "cost_usd": 2120.8234083999987, "share": 0.31631115138706906, "models": 6},
        {"harness": "codex", "tokens": 460666536, "cost_usd": 363.40516354000033, "share": 0.03544270684222712, "models": 3},
        {"harness": "opencode", "tokens": 8466180, "cost_usd": 5.77392148, "share": 0.0006513699441227188, "models": 4}
      ],
      "days": [
        {"date": "2026-10-24", "tokens": 1547584944, "input": 10176, "output": 2994923, "cached": 1543402576, "cost_usd": 704.0862635999996},
        {"date": "2026-10-23", "tokens": 2106741649, "input": 34207204, "output": 5126653, "cached": 2066398436, "cost_usd": 825.4763561600001},
        {"date": "2026-10-22", "tokens": 1466534661, "input": 73997865, "output": 9956954, "cached": 1382557096, "cost_usd": 795.3936153999999},
        {"date": "2026-10-20", "tokens": 1400000000, "cost_usd": 610.5},
        {"date": "2026-10-19", "tokens": 1900000000, "cost_usd": 720.25},
        {"date": "2026-10-18", "tokens": 2500000000, "cost_usd": 980.0},
        {"date": "2026-10-17", "tokens": 2076637443, "cost_usd": 768.13}
      ],
      "quick": {"active_days": 7, "tokens_per_day": 1856785528, "busiest_day": "2026-10-18"},
      "top_models": [{"model": "claude-opus-4-1", "tokens": 8000000000, "share": 0.6, "harnesses": ["claude"]}],
      "heatmap": {"weeks": 26, "days": [{"date": "2026-10-25", "future": true}]},
      "scanned_at": 1792827600.0, "notes": [], "machine": "studio.local"
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
        XCTAssertEqual(rows[0].sessions100d, 773)
        XCTAssertNil(rows[5].sessions100d)
        XCTAssertNil(rows[2].rateLimit)
        XCTAssertEqual(rows[3].title, "Cursor")
        XCTAssertEqual(rows[0].title, "Claude Code")
        XCTAssertEqual(body.windowDays, 100)
        XCTAssertEqual(body.machine?.displayName, "studio")
    }

    func testSingleWindowToolsUseTheRowItself() throws {
        let rows = try XCTUnwrap(UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self).harnesses)
        let codex = try XCTUnwrap(rows[1].rateLimit?.displayWindows)
        XCTAssertEqual(codex.count, 1)
        XCTAssertEqual(codex[0].label, "Weekly")
        XCTAssertEqual(codex[0].usedPercent, 33)
        XCTAssertEqual(rows[0].rateLimit?.displayWindows.map(\.label), ["Weekly"])
    }

    func testMultiWindowAndExtras() throws {
        let rows = try XCTUnwrap(UsageFixtures.decode(UsageFixtures.usage, as: UsageResponse.self).harnesses)
        let devin = try XCTUnwrap(rows[4].rateLimit)
        XCTAssertEqual(devin.displayWindows.map(\.label), ["Daily", "Weekly"])
        XCTAssertEqual(devin.extraUsageUsd, 8219.74)
        XCTAssertEqual(LimitCard.extraLines(devin), ["$8,220 extra usage left"])
        let cursor = try XCTUnwrap(rows[3].rateLimit)
        XCTAssertEqual(cursor.onDemand?.state, "unlimited")
        XCTAssertEqual(cursor.displayWindows[1].unavailable, true)
        XCTAssertEqual(cursor.displayWindows[1].asOf, 1_792_820_000)
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
        XCTAssertEqual(body.totals?.tokens, 12_997_498_697)
        XCTAssertEqual(body.totals?.unpricedTokens, 1_504_919)
        XCTAssertEqual(body.harnesses?.map(\.harness), ["claude", "devin", "codex", "opencode"])
        XCTAssertEqual(body.days?.count, 7)
        XCTAssertEqual(body.range?.startDay, "2026-10-17")
        XCTAssertEqual(body.machine, "studio.local")
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
        XCTAssertEqual(UsageFormat.tokens(12_997_498_697), "13.0B")
        XCTAssertEqual(UsageFormat.tokens(999_960), "1.0M")
        XCTAssertEqual(UsageFormat.tokens(999_400), "999K")
        XCTAssertEqual(UsageFormat.tokens(99_960), "100K")
        XCTAssertEqual(UsageFormat.tokens(nil), "0")
    }

    func testUSD() {
        XCTAssertEqual(UsageFormat.usd(0.42), "$0.42")
        XCTAssertEqual(UsageFormat.usd(12.3), "$12.30")
        XCTAssertEqual(UsageFormat.usd(999.994), "$999.99")
        XCTAssertEqual(UsageFormat.usd(5403.83), "$5,404")
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
        XCTAssertEqual(UsageFormat.plan("self_serve_business_prolite"), "Self serve business prolite")
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
        XCTAssertEqual(series.first?.cost, 768.13)
        XCTAssertEqual(series[4].cost, 0, "the 21st had no usage")
        XCTAssertEqual(series.last?.tokens, 1_547_584_944)
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
