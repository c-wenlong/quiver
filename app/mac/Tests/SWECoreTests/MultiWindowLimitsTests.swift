import Foundation
import Testing
@testable import SWECore

private func decode(_ json: String) throws -> RateLimit {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    decoder.dateDecodingStrategy = .secondsSince1970
    return try decoder.decode(RateLimit.self, from: Data(json.utf8))
}

private let devin = #"""
{"status":"ok","used_percent":4,"remaining_percent":96,"limit_reached":false,"reset_at":1789286400,
 "window":"7d","window_seconds":604800,"plan_type":"teams","remaining_units":null,"total_units":null,
 "windows":[
  {"window":"1d","used_percent":0,"remaining_percent":100,"limit_reached":false,"reset_at":1789200000,"window_seconds":86400},
  {"window":"7d","used_percent":4,"remaining_percent":96,"limit_reached":false,"reset_at":1789286400,"window_seconds":604800}],
 "extra_usage_usd":1234.56,"fetched_at":1789190000}
"""#

/// What an older swe prints: one window, no list, no balance.
private let older = #"""
{"status":"ok","used_percent":5,"remaining_percent":95,"limit_reached":false,"reset_at":null,
 "window":"1d","window_seconds":null,"plan_type":"teams","remaining_units":null,"total_units":null,"fetched_at":1791700000}
"""#

@Suite struct MultiWindowLimitsTests {
    @Test func everyWindowAndTheBalanceDecode() throws {
        let limit = try decode(devin)
        #expect(limit.allWindows.map(\.window) == ["1d", "7d"])
        #expect(limit.allWindows.map(\.usedPercent) == [0, 4])
        #expect(limit.allWindows.first?.resetAt == Date(timeIntervalSince1970: 1789200000))
        #expect(limit.extraUsageUSD == 1234.56)
    }

    @Test func anOlderSweStillGivesItsOneWindow() throws {
        let limit = try decode(older)
        #expect(limit.allWindows.map(\.window) == ["1d"])
        #expect(limit.allWindows.map(\.usedPercent) == [5])
        #expect(limit.extraUsageUSD == nil)
    }

    @Test func theEmailDecodesAndBlankReadsAsNone() throws {
        let with = older.replacingOccurrences(of: #""fetched_at""#, with:
            #""email":" dev@example.com ","fetched_at""#)
        #expect(try decode(with).email == "dev@example.com")
        let blank = older.replacingOccurrences(of: #""fetched_at""#, with:
            #""email":"  ","fetched_at""#)
        #expect(try decode(blank).email == nil)
        #expect(try decode(older).email == nil)
    }

    @Test func eachWindowBecomesItsOwnReading() throws {
        let row = OtherLimitsModel.Row(name: "devin", limit: try decode(devin))
        let readings = LimitReading.all(cards: [], others: [row], now: Date(timeIntervalSince1970: 1789190000))
        #expect(readings.map(\.id) == ["devin|1d", "devin|7d"])
        #expect(readings.map(\.title) == ["Devin", "Devin"])
        #expect(readings.last?.resetsAt == Date(timeIntervalSince1970: 1789286400))
    }

    @Test func anUnavailableWindowDecodesWithItsCarriedAge() throws {
        let raw = #"""
        {"status":"ok","used_percent":40,"remaining_percent":60,"limit_reached":false,
         "reset_at":null,"window":"7d","window_seconds":604800,"plan_type":"teams",
         "remaining_units":null,"total_units":null,"fetched_at":1791700000,
         "windows":[{"window":"grok_bot","used_percent":12,"reset_at":1791760000,
                     "window_seconds":604800,"unavailable":true,"as_of":1791600000}]}
        """#
        let limit = try decode(raw)
        let window = try #require(limit.windows?.first)
        #expect(window.unavailable)
        #expect(window.asOf == Date(timeIntervalSince1970: 1791600000))
        #expect(window.usedPercent == 12)

        // A reading from an unavailable window counts as stale, so the
        // widget and menu bar dim it like swe's day-old fallback.
        let row = OtherLimitsModel.Row(
            name: "cursor",
            limit: try decode(raw.replacingOccurrences(of: "1791700000", with: "1791800000")))
        let readings = LimitReading.all(cards: [], others: [row],
                                        now: Date(timeIntervalSince1970: 1791800000))
        #expect(readings.first?.isStale == true)

        // And a window without the flag defaults to available.
        let plain = try #require(try decode(devin).windows?.first)
        #expect(!plain.unavailable && plain.asOf == nil)
    }

    @Test func aMalformedWindowListOrBalanceLeavesTheReadingIntact() throws {
        let bad = older.replacingOccurrences(of: #""fetched_at""#,
            with: #""windows":[{"window":7}],"extra_usage_usd":"lots","fetched_at""#)
        let limit = try decode(bad)
        #expect(limit.usedPercent == 5)
        #expect(limit.allWindows.map(\.window) == ["1d"])
        #expect(limit.extraUsageUSD == nil)
    }

    @Test func anEmptyWindowListFallsBackToTheTopLevelReading() throws {
        let empty = older.replacingOccurrences(of: #""fetched_at""#, with: #""windows":[],"fetched_at""#)
        #expect(try decode(empty).allWindows.map(\.window) == ["1d"])
    }

    @Test func savedCursorAutoChoicesMoveToIncludedOnce() {
        let store = TestDefaults("MultiWindowLimitsTests")
        store.set("cursor|auto", forKey: MenuBarDisplay.Key.watchedLimit)
        store.set(["cursor": ["auto", "api"], "claude": ["auto"]], forKey: MenuBarPopup.Key.hiddenWindows)
        MenuBarMigrations.run(on: store)
        #expect(MenuBarDisplay(defaults: store).watchedLimit == "cursor|included")
        let popup = MenuBarPopup(defaults: store)
        let cursorWindows = ["included", "auto", "api"]
        #expect(!popup.shows(window: "included", source: "cursor", harness: "cursor",
                             allWindows: cursorWindows))
        #expect(popup.shows(window: "auto", source: "cursor", harness: "cursor",
                            allWindows: cursorWindows))
        #expect(!popup.shows(window: "api", source: "cursor", harness: "cursor",
                             allWindows: cursorWindows))
        #expect(!popup.shows(window: "auto", source: "claude-default", harness: "claude",
                             allWindows: ["5h", "auto", "7d"]))
        // A choice made after the upgrade is left alone.
        store.set("cursor|auto", forKey: MenuBarDisplay.Key.watchedLimit)
        MenuBarMigrations.run(on: store)
        #expect(MenuBarDisplay(defaults: store).watchedLimit == "cursor|auto")
    }

    @Test func onDemandDecodesAndReadsLikeTheCli() throws {
        let team = older.replacingOccurrences(of: #""fetched_at""#, with:
            #""on_demand":{"state":"unlimited","scope":"team-member","used_usd":0.0,"limit_usd":null},"fetched_at""#)
        let onDemand = try #require(try decode(team).onDemand)
        #expect(onDemand.value == "$0")
        #expect(onDemand.detail == "No personal limit")
        let cases: [(RateLimit.OnDemand, String, String)] = [
            (.init(state: "fixed", scope: "personal", usedUSD: 5.5, limitUSD: 20), "$5.50 / $20", "$14.50 remaining"),
            (.init(state: "unlimited", scope: "personal", usedUSD: 12, limitUSD: nil), "$12", "No monthly limit"),
            (.init(state: "disabled", scope: "personal", usedUSD: 0, limitUSD: nil), "Disabled", "On-demand usage is off"),
            (.init(state: "disabled", scope: "personal", usedUSD: 3.25, limitUSD: nil), "$3.25", "On-demand usage is off"),
            (.init(state: "unavailable", scope: "personal", usedUSD: 0, limitUSD: nil), "Unavailable", "On-demand limit unavailable"),
        ]
        for (onDemand, value, detail) in cases {
            #expect(onDemand.value == value)
            #expect(onDemand.detail == detail)
        }
        #expect(try decode(older).onDemand == nil)
    }

    @Test func aBalanceShowsToTheCent() {
        #expect(UsageFormat.balance(1234.56) == "$1,234.56")
        #expect(UsageFormat.balance(0) == "$0.00")
    }

    @Test func cursorBucketsReadLikeTheCli() {
        #expect(["included", "auto", "api", "grok_bot", "5h", "7d Fable"].map(UsageFormat.window)
                == ["Included", "Auto", "API", "Grok Bot", "5h", "7d Fable"])
    }
}
