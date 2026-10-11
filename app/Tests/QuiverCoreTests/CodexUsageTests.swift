import Foundation
import Testing
@testable import QuiverCore

@Suite struct CodexUsageTests {
    @Test func aMissingAuthFileIsSignedOutAndSendsNothing() async {
        let h = UsageHarness()
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .signedOut)
        #expect(usage.windows.isEmpty)
        #expect(usage.fetchedAt == nil)
        #expect(usage.isStale == false)
        #expect(h.transport.requests.isEmpty)
    }

    @Test func readsBothWindowsWithTheTokenAndAccountHeader() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: try fixture("codex-usage"))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        let request = try #require(h.transport.requests.first)
        #expect(request.url?.absoluteString == "https://chatgpt.com/backend-api/wham/usage")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer codex-access-SECRET-1")
        #expect(request.value(forHTTPHeaderField: "ChatGPT-Account-Id") == "acct-123")
        #expect(usage == AccountUsage(
            status: .ok, plan: "Pro 20x", email: "dev@example.com",
            windows: [
                UsageWindow(label: "5h", usedPercent: 23, resetsAt: Date(timeIntervalSince1970: 1_791_727_200)),
                UsageWindow(label: "7d", usedPercent: 41.5, resetsAt: Date(timeIntervalSince1970: 1_792_051_200)),
            ],
            fetchedAt: usageEpoch, isStale: false))
    }

    @Test func aSoleWeeklyWindowInThePrimarySlotIsLabelledByItsLength() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: try fixture("codex-usage-weekly-only"))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.plan == "Plus")
        #expect(usage.windows == [
            // No reset_at, so reset_after_seconds counts from the fetch.
            UsageWindow(label: "7d", usedPercent: 100, resetsAt: usageEpoch.addingTimeInterval(43_200)),
        ])
    }

    @Test func identityComesFromTheIDTokenWhenTheResponseLacksIt() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON(accountID: nil, email: "other@example.com", plan: "prolite"))
        let body = #"""
        {"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000, "reset_at": 1791727200}},
         "additional_rate_limits": [{"limit_name": "GPT-5.3-Codex-Spark", "rate_limit": {
            "primary_window": {"used_percent": 9, "limit_window_seconds": 18000, "reset_at": 1791727200},
            "secondary_window": null}}]}
        """#
        h.transport.reply(200, body: Data(body.utf8))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        let request = try #require(h.transport.requests.first)
        #expect(request.value(forHTTPHeaderField: "ChatGPT-Account-Id") == "acct-from-jwt")
        #expect(usage.email == "other@example.com")
        #expect(usage.plan == "Pro 5x")
        #expect(usage.windows.map(\.label) == ["5h", "5.3 Spark 5h"])
        #expect(usage.windows.map(\.usedPercent) == [5, 9])
    }

    @Test func anAPIKeyLoginHasNoLimitsToRead() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", #"{"auth_mode": "apikey", "OPENAI_API_KEY": "sk-SECRET"}"#)
        let usage = await h.makeService().usage(for: codexKey, force: false)
        #expect(usage.status == .error("an API-key login has no usage limits"))
        #expect(h.transport.requests.isEmpty)
    }
}
