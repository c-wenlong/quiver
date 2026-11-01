import Foundation
import Testing
@testable import SWECore

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

    @Test func anEduWorkspacesMonthlyCreditCapIsA30dWindow() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        // The shape the Codex CLI parses: used credits can pass the cap.
        let body = #"""
        {"plan_type": "edu",
         "rate_limit": {"primary_window": {"used_percent": 0, "limit_window_seconds": 18000, "reset_at": 1791727200},
                        "secondary_window": {"used_percent": 86, "limit_window_seconds": 604800, "reset_at": 1792051200}},
         "spend_control": {"reached": true, "individual_limit": {
            "source": "workspace_spend_controls", "limit": "2500", "used": "2504", "remaining": "0",
            "used_percent": 101, "remaining_percent": 0, "reset_after_seconds": 86400, "reset_at": 1792300000}}}
        """#
        h.transport.reply(200, body: Data(body.utf8))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.plan == "Edu")
        #expect(usage.windows == [
            UsageWindow(label: "5h", usedPercent: 0, resetsAt: Date(timeIntervalSince1970: 1_791_727_200)),
            UsageWindow(label: "7d", usedPercent: 86, resetsAt: Date(timeIntervalSince1970: 1_792_051_200)),
            UsageWindow(label: "30d", usedPercent: 100, resetsAt: Date(timeIntervalSince1970: 1_792_300_000)),
        ])
    }

    @Test(arguments: [
        ("education", "Education"), ("edu", "Edu"), ("edu_plus", "Edu Plus"), ("edu_pro", "Edu Pro"),
        ("pro", "Pro 20x"), ("self_serve_business_prolite", "Business 5x"),
    ])
    func educationPlansKeepTheirOwnNames(slug: String, name: String) {
        #expect(PlanNames.name(.codex, slug) == name)
    }

    @Test func aSpendCapAloneStillCounts() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        let body = #"{"spend_control": {"reached": false, "individual_limit": {"used_percent": 32, "reset_after_seconds": 43200}}}"#
        h.transport.reply(200, body: Data(body.utf8))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.status == .ok)
        #expect(usage.windows == [UsageWindow(label: "30d", usedPercent: 32, resetsAt: usageEpoch.addingTimeInterval(43_200))])
    }

    @Test func aReachedCapWithNoFiguresIsAFullWindow() throws {
        // What an education seat at its cap sends: reached, but no individual_limit.
        let body = #"{"plan_type": "education", "rate_limit": {"primary_window": {"used_percent": 0, "limit_window_seconds": 18000}}, "spend_control": {"reached": true, "individual_limit": null}}"#
        let parsed = try #require(CodexUsageAPI.parse(Data(body.utf8), credential: UsageCredential(
            token: "t", accountID: nil, expiresAt: nil, plan: nil, email: nil), now: usageEpoch))
        #expect(parsed.plan == "Education")
        #expect(parsed.windows.last == UsageWindow(label: "30d", usedPercent: 100, resetsAt: nil))
    }

    @Test func noSpendControlAddsNoWindow() throws {
        let body = #"{"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000}}, "spend_control": {"reached": false, "individual_limit": null}}"#
        let parsed = try #require(CodexUsageAPI.parse(Data(body.utf8), credential: UsageCredential(
            token: "t", accountID: nil, expiresAt: nil, plan: nil, email: nil), now: usageEpoch))
        #expect(parsed.windows.map(\.label) == ["5h"])
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
