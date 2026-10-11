import Foundation
import Testing
@testable import QuiverCore

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
 "extra_usage_usd":9868.98,"fetched_at":1789190000}
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
        #expect(limit.extraUsageUSD == 9868.98)
    }

    @Test func anOlderSweStillGivesItsOneWindow() throws {
        let limit = try decode(older)
        #expect(limit.allWindows.map(\.window) == ["1d"])
        #expect(limit.allWindows.map(\.usedPercent) == [5])
        #expect(limit.extraUsageUSD == nil)
    }

    @Test func eachWindowBecomesItsOwnReading() throws {
        let row = OtherLimitsModel.Row(name: "devin", limit: try decode(devin))
        let readings = LimitReading.all(cards: [], others: [row], now: Date(timeIntervalSince1970: 1789190000))
        #expect(readings.map(\.id) == ["devin|1d", "devin|7d"])
        #expect(readings.map(\.title) == ["Devin", "Devin"])
        #expect(readings.last?.resetsAt == Date(timeIntervalSince1970: 1789286400))
    }

    @Test func aBalanceShowsToTheCent() {
        #expect(UsageFormat.balance(9868.98) == "$9,868.98")
        #expect(UsageFormat.balance(0) == "$0.00")
    }

    @Test func cursorBucketsReadLikeTheCli() {
        #expect(["included", "auto", "api", "5h", "7d Fable"].map(UsageFormat.window)
                == ["Included", "Auto", "API", "5h", "7d Fable"])
    }
}
