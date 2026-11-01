import Foundation
import Testing
@testable import SWECore

@Suite struct ClaudeKeychainNamingTests {
    @Test func theDefaultHomeUsesTheBareServiceName() {
        #expect(ClaudeKeychain.service(configHome: "/Users/test/.claude", isDefault: true)
            == "Claude Code-credentials")
    }

    @Test func anOverriddenHomeAddsTheFirstEightHexOfItsSHA256() {
        // sha256("/Users/test/claude-work") starts ba8216bd.
        #expect(ClaudeKeychain.service(configHome: "/Users/test/claude-work", isDefault: false)
            == "Claude Code-credentials-ba8216bd")
        // Keyed on the override being set, not on the path: CLAUDE_CONFIG_DIR
        // pointing at ~/.claude still gets a suffix.
        #expect(ClaudeKeychain.service(configHome: "/Users/test/.claude", isDefault: false)
            != "Claude Code-credentials")
    }

    @Test func thePathIsHashedInNFCLikeClaudeCodeDoes() {
        let precomposed = "/Users/test/Caf\u{E9}"
        let decomposed = "/Users/test/Cafe\u{301}"
        #expect(ClaudeKeychain.service(configHome: precomposed, isDefault: false) == "Claude Code-credentials-e6258296")
        #expect(ClaudeKeychain.service(configHome: decomposed, isDefault: false) == "Claude Code-credentials-e6258296")
    }

    @Test func theAccountIsTheUserNameOrClaudeCodesFallback() {
        #expect(ClaudeKeychain.account(environmentUser: "dev", systemUser: "other") == "dev")
        #expect(ClaudeKeychain.account(environmentUser: nil, systemUser: "dev.chen") == "dev.chen")
        #expect(ClaudeKeychain.account(environmentUser: "", systemUser: "dev") == "dev")
        #expect(ClaudeKeychain.account(environmentUser: "dev chen", systemUser: "dev") == "claude-code-user")
    }
}

@Suite struct ClaudeUsageTests {
    let user = ClaudeKeychain.currentAccount

    @Test func readsTheKeychainItemAndMapsEveryWindow() async throws {
        let h = UsageHarness()
        h.secrets.set(service: "Claude Code-credentials", account: user, claudeCredentialsJSON())
        h.transport.reply(200, body: try fixture("claude-usage"))

        let usage = await h.makeService().usage(for: claudeKey, force: false)

        #expect(h.secrets.lookups == ["Claude Code-credentials|\(user)"])
        let request = try #require(h.transport.requests.first)
        #expect(request.url?.absoluteString == "https://api.anthropic.com/api/oauth/usage")
        #expect(request.httpMethod == "GET")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer claude-access-SECRET-1")
        #expect(request.value(forHTTPHeaderField: "anthropic-beta") == "oauth-2025-04-20")
        #expect(usage.status == .ok)
        #expect(usage.plan == "Max 20x")
        #expect(usage.fetchedAt == usageEpoch)
        #expect(usage.isStale == false)
        #expect(usage.windows.map(\.label) == ["5h", "7d", "7d Opus"])
        #expect(usage.windows.map(\.usedPercent) == [37, 62, 12.5])
        let fiveHourReset = try #require(usage.windows[0].resetsAt)
        #expect(abs(fiveHourReset.timeIntervalSince1970 - 1_791_727_200.512312) < 0.000_01)
        #expect(usage.windows[1].resetsAt == Date(timeIntervalSince1970: 1_792_051_200))
        #expect(usage.windows[2].resetsAt == Date(timeIntervalSince1970: 1_792_051_200))
        #expect(usage.signInExpiresAt == usageEpoch.addingTimeInterval(3600))
    }

    @Test func scopedWeeklyLimitsFromTheLimitsArrayAreAdded() async throws {
        let h = UsageHarness()
        h.secrets.set(service: "Claude Code-credentials", account: user, claudeCredentialsJSON())
        h.transport.reply(200, body: try fixture("claude-usage-limits"))

        let usage = await h.makeService().usage(for: claudeKey, force: false)

        #expect(usage.windows == [
            UsageWindow(label: "5h", usedPercent: 37, resetsAt: Date(timeIntervalSince1970: 1_791_727_200)),
            UsageWindow(label: "7d", usedPercent: 62, resetsAt: Date(timeIntervalSince1970: 1_792_051_200)),
            UsageWindow(label: "7d Fable", usedPercent: 7, resetsAt: Date(timeIntervalSince1970: 1_792_051_200)),
        ])
    }

    @Test func anOverriddenHomeReadsItsOwnKeychainItem() async throws {
        let h = UsageHarness()
        let key = AccountKey(provider: .claude, configHome: "/Users/test/claude-work", isDefault: false)
        h.secrets.set(service: "Claude Code-credentials-ba8216bd", account: user, claudeCredentialsJSON())
        h.transport.reply(200, body: try fixture("claude-usage"))

        let usage = await h.makeService().usage(for: key, force: false)

        #expect(h.secrets.lookups == ["Claude Code-credentials-ba8216bd|\(user)"])
        #expect(usage.status == .ok)
    }

    @Test func fallsBackToTheCredentialsFileWhenTheKeychainHasNothing() async throws {
        let h = UsageHarness()
        h.files.set("\(claudeHome)/.credentials.json",
                    claudeCredentialsJSON(accessToken: "file-token-SECRET", subscriptionType: "pro", rateLimitTier: "default_claude_pro"))
        h.transport.reply(200, body: try fixture("claude-usage"))

        let usage = await h.makeService().usage(for: claudeKey, force: false)

        #expect(h.transport.requests.first?.value(forHTTPHeaderField: "Authorization") == "Bearer file-token-SECRET")
        #expect(usage.plan == "Pro")
    }

    @Test func aHexEncodedKeychainItemIsDecoded() async throws {
        let h = UsageHarness()
        let hex = Data(claudeCredentialsJSON().utf8).map { String(format: "%02x", $0) }.joined()
        h.secrets.set(service: "Claude Code-credentials", account: user, hex)
        h.transport.reply(200, body: try fixture("claude-usage"))

        let usage = await h.makeService().usage(for: claudeKey, force: false)

        #expect(usage.status == .ok)
    }

    @Test func theEmailComesFromClaudeCodesGlobalConfig() async throws {
        let h = UsageHarness()
        h.secrets.set(service: "Claude Code-credentials", account: user, claudeCredentialsJSON())
        // Without an override Claude Code keeps it beside the home: ~/.claude.json.
        h.files.set("/Users/test/.claude.json", #"{"oauthAccount": {"emailAddress": "dev@example.com"}}"#)
        h.transport.reply(200, body: try fixture("claude-usage"))
        #expect(await h.makeService().usage(for: claudeKey, force: false).email == "dev@example.com")

        let custom = AccountKey(provider: .claude, configHome: "/Users/test/claude-work", isDefault: false)
        h.secrets.set(service: "Claude Code-credentials-ba8216bd", account: user, claudeCredentialsJSON())
        h.files.set("/Users/test/claude-work/.claude.json", #"{"oauthAccount": {"emailAddress": "work@example.com"}}"#)
        h.transport.reply(200, body: try fixture("claude-usage"))
        #expect(await h.makeService().usage(for: custom, force: false).email == "work@example.com")
    }

    @Test func noCredentialAnywhereIsSignedOut() async {
        let h = UsageHarness()
        let usage = await h.makeService().usage(for: claudeKey, force: true)
        #expect(usage == AccountUsage(status: .signedOut))
        #expect(h.transport.requests.isEmpty)
    }

    @Test func aNewerLoginInTheFileBeatsAnExpiredKeychainToken() async throws {
        let h = UsageHarness()
        h.secrets.set(service: "Claude Code-credentials", account: user,
                      claudeCredentialsJSON(accessToken: "old-SECRET", expiresAt: usageEpoch.addingTimeInterval(-60)))
        h.files.set("\(claudeHome)/.credentials.json",
                    claudeCredentialsJSON(accessToken: "new-SECRET", expiresAt: usageEpoch.addingTimeInterval(3600)))
        h.transport.reply(200, body: try fixture("claude-usage"))

        let usage = await h.makeService().usage(for: claudeKey, force: false)

        #expect(usage.status == .ok)
        #expect(h.transport.requests.first?.value(forHTTPHeaderField: "Authorization") == "Bearer new-SECRET")
    }

    @Test func anExpiredTokenIsExpiredWithoutARequest() async {
        let h = UsageHarness()
        h.secrets.set(service: "Claude Code-credentials", account: user,
                      claudeCredentialsJSON(expiresAt: usageEpoch.addingTimeInterval(-1)))

        let usage = await h.makeService().usage(for: claudeKey, force: true)

        #expect(usage.status == .expired)
        #expect(usage.plan == "Max 20x")
        #expect(usage.windows.isEmpty)
        #expect(usage.signInExpiresAt == usageEpoch.addingTimeInterval(-1))
        #expect(h.transport.requests.isEmpty)
    }
}
