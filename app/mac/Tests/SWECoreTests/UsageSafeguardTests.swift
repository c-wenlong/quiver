import Foundation
import Synchronization
import Testing
@testable import SWECore

/// The safeguards carried over from swe's rate_limits.py: honour the
/// provider's cooldown, reuse a reading with its real age, never pass off a
/// day-old reading as current, and never keep a credential.
@Suite struct UsageSafeguardTests {
    /// A harness with a Codex login and one good reading already taken at `usageEpoch`.
    func harnessWithReading() async throws -> (UsageHarness, AccountUsage) {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: try fixture("codex-usage"))
        let first = await h.makeService().usage(for: codexKey, force: true)
        try #require(first.status == .ok)
        return (h, first)
    }

    // MARK: HTTP outcomes

    @Test func a401IsExpired() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(401)
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .expired)
        #expect(usage.windows.isEmpty)
    }

    @Test func a429WithRetryAfterSecondsCoolsDownForThatLong() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(429, headers: ["Retry-After": "600"])
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage == AccountUsage(
            status: .rateLimited(until: usageEpoch.addingTimeInterval(600)),
            plan: "Plus", email: "dev@example.com"))
    }

    @Test func a429WithAnHTTPDateCoolsDownUntilThatDate() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(429, headers: ["Retry-After": "Sun, 11 Oct 2026 13:00:00 GMT"])
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .rateLimited(until: Date(timeIntervalSince1970: 1_791_723_600)))
    }

    @Test func a429WithoutRetryAfterCoolsDownForFifteenMinutes() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(429)
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .rateLimited(until: usageEpoch.addingTimeInterval(15 * 60)))
    }

    @Test func aTinyOrHugeRetryAfterIsClamped() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(429, headers: ["Retry-After": "0"])
        let service = h.makeService()
        #expect(await service.usage(for: codexKey, force: true).status
            == .rateLimited(until: usageEpoch.addingTimeInterval(60)))

        h.clock.advance(61)
        h.transport.reply(429, headers: ["Retry-After": "99999999"])
        #expect(await service.usage(for: codexKey, force: true).status
            == .rateLimited(until: h.clock.now.addingTimeInterval(24 * 60 * 60)))
    }

    @Test func anotherHTTPErrorKeepsTheLastReadingAsStale() async throws {
        let (h, first) = try await harnessWithReading()
        h.clock.advance(600)
        h.transport.reply(503)
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .error("usage API returned HTTP 503"))
        #expect(usage.windows == first.windows)
        #expect(usage.fetchedAt == usageEpoch)
        #expect(usage.isStale)
    }

    @Test func anUnreadableBodyIsAnError() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: Data("<html>".utf8))
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .error("unexpected response from the usage API"))
    }

    @Test func anUnparseableReplyIsRememberedAsTheLastFailure() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(120)
        h.transport.reply(200, body: Data("not the usage json".utf8))
        let usage = await h.makeService().usage(for: codexKey, force: true)
        #expect(usage.status == .error("unexpected response from the usage API"))

        let cached = await h.makeService().cachedUsage(for: codexKey)
        #expect(cached?.status == .error("unexpected response from the usage API"))
        #expect(cached?.fetchedAt == usageEpoch)
    }

    // MARK: Cooldown

    @Test func aCooldownIsHonouredEvenWhenForced() async throws {
        let (h, first) = try await harnessWithReading()
        let service = h.makeService()
        h.clock.advance(600)
        h.transport.reply(429, headers: ["Retry-After": "900"])
        let limited = await service.usage(for: codexKey, force: true)
        let until = usageEpoch.addingTimeInterval(600 + 900)
        #expect(limited.status == .rateLimited(until: until))

        h.clock.advance(300)
        let requestsBefore = h.transport.requests.count
        let during = await service.usage(for: codexKey, force: true)

        #expect(h.transport.requests.count == requestsBefore)
        #expect(during == AccountUsage(
            status: .rateLimited(until: until), plan: "Pro 20x", email: "dev@example.com",
            windows: first.windows, fetchedAt: usageEpoch, isStale: true))
    }

    @Test func aRequestIsSentOnceTheCooldownEnds() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(429, headers: ["Retry-After": "120"])
        let service = h.makeService()
        _ = await service.usage(for: codexKey, force: true)

        h.clock.advance(120)
        h.transport.reply(200, body: try fixture("codex-usage"))
        let usage = await service.usage(for: codexKey, force: false)

        #expect(h.transport.requests.count == 2)
        #expect(usage.status == .ok)
        #expect(usage.fetchedAt == usageEpoch.addingTimeInterval(120))
    }

    @Test func aNewLoginBypassesTheCooldownAndDropsTheOldReading() async throws {
        let (h, _) = try await harnessWithReading()
        let service = h.makeService()
        h.clock.advance(600)
        h.transport.reply(429, headers: ["Retry-After": "3600"])
        _ = await service.usage(for: codexKey, force: true)

        h.files.set("\(codexHome)/auth.json", codexAuthJSON(accessToken: "codex-access-SECRET-2", email: "new@example.com"))
        h.transport.fail()
        let usage = await service.usage(for: codexKey, force: false)

        #expect(h.transport.requests.count == 3)
        #expect(h.transport.requests.last?.value(forHTTPHeaderField: "Authorization") == "Bearer codex-access-SECRET-2")
        // The first login's reading must not be shown for the second.
        #expect(usage == AccountUsage(
            status: .error("could not reach the usage API"), plan: "Plus", email: "new@example.com"))
    }

    // MARK: Cache

    @Test func aReadingUnderFiveMinutesOldIsReusedWithoutARequest() async throws {
        let (h, first) = try await harnessWithReading()
        let service = h.makeService()
        h.clock.advance(299)

        let cached = await service.usage(for: codexKey, force: false)

        #expect(h.transport.requests.count == 1)
        #expect(cached == first)
        #expect(cached.fetchedAt == usageEpoch)
    }

    @Test func forceOrFiveMinutesSendsANewRequest() async throws {
        let (h, _) = try await harnessWithReading()
        let service = h.makeService()
        h.clock.advance(60)
        h.transport.reply(200, body: try fixture("codex-usage"))
        #expect(await service.usage(for: codexKey, force: true).fetchedAt == usageEpoch.addingTimeInterval(60))

        h.clock.advance(300)
        h.transport.reply(200, body: try fixture("codex-usage"))
        #expect(await service.usage(for: codexKey, force: false).fetchedAt == usageEpoch.addingTimeInterval(360))
        #expect(h.transport.requests.count == 3)
    }

    /// A forced refresh that arrives while a non-forced call is still
    /// looking up the credential must not take that call's cached answer.
    @Test(.timeLimit(.minutes(1))) func aForcedCallDoesNotJoinANonForcedOne() async throws {
        let h = UsageHarness()
        let user = ClaudeKeychain.currentAccount
        h.secrets.set(service: "Claude Code-credentials", account: user, claudeCredentialsJSON())
        let secrets = GatedSecrets(h.secrets)
        let clock = h.clock
        let service = UsageService(transport: h.transport, secrets: secrets, files: h.files,
                                   cacheURL: h.cacheURL, now: { clock.now })
        h.transport.reply(200, body: try fixture("claude-usage"))
        let first = await service.usage(for: claudeKey, force: true)
        try #require(first.fetchedAt == usageEpoch)

        h.clock.advance(60)
        secrets.close()
        let background = Task { await service.usage(for: claudeKey, force: false) }
        try #require(await secrets.parks.reach(1))
        h.transport.reply(200, body: try fixture("claude-usage"))
        // The forced call must find the background one still in flight.
        let overlapped = AsyncStream<Void>.makeStream()
        await service.setOnFindsInFlight { overlapped.continuation.yield() }
        let forced = Task { await service.usage(for: claudeKey, force: true) }
        for await _ in overlapped.stream { break }
        secrets.open()

        #expect(await background.value.fetchedAt == usageEpoch)
        #expect(await forced.value.fetchedAt == usageEpoch.addingTimeInterval(60))
        #expect(h.transport.requests.count == 2)
    }

    @Test func aNetworkFailureReturnsTheLastReadingWithItsOriginalTime() async throws {
        let (h, first) = try await harnessWithReading()
        h.clock.advance(3 * 60 * 60)
        h.transport.fail(.timedOut)

        let usage = await h.makeService().usage(for: codexKey, force: true)

        #expect(usage == AccountUsage(
            status: .error("could not reach the usage API"), plan: "Pro 20x", email: "dev@example.com",
            windows: first.windows, fetchedAt: usageEpoch, isStale: true))
    }

    @Test func aReadingOlderThanADayIsNeverShown() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(24 * 60 * 60 + 1)
        h.transport.fail()
        let failed = await h.makeService().usage(for: codexKey, force: true)
        #expect(failed == AccountUsage(
            status: .error("could not reach the usage API"), plan: "Plus", email: "dev@example.com"))

        h.transport.reply(429)
        let limited = await h.makeService().usage(for: codexKey, force: true)
        #expect(limited.windows.isEmpty)
        #expect(limited.fetchedAt == nil)
        #expect(limited.isStale == false)
    }

    @Test func anExpiredClaudeLoginShowsTheLastReadingAsStale() async throws {
        let h = UsageHarness()
        let user = ClaudeKeychain.currentAccount
        h.secrets.set(service: "Claude Code-credentials", account: user,
                      claudeCredentialsJSON(expiresAt: usageEpoch.addingTimeInterval(600)))
        h.transport.reply(200, body: try fixture("claude-usage"))
        let first = await h.makeService().usage(for: claudeKey, force: true)

        h.clock.advance(900)
        let usage = await h.makeService().usage(for: claudeKey, force: true)

        #expect(h.transport.requests.count == 1)
        #expect(usage.status == .expired)
        #expect(usage.windows == first.windows)
        #expect(usage.fetchedAt == usageEpoch)
        #expect(usage.isStale)
    }

    @Test func theCooldownAndReadingSurviveANewServiceInstance() async throws {
        let (h, first) = try await harnessWithReading()
        h.clock.advance(120)
        #expect(await h.makeService().usage(for: codexKey, force: false) == first)

        h.clock.advance(400)
        h.transport.reply(429, headers: ["Retry-After": "600"])
        _ = await h.makeService().usage(for: codexKey, force: true)
        h.clock.advance(10)
        let fresh = await h.makeService().usage(for: codexKey, force: true)

        #expect(h.transport.requests.count == 2)
        #expect(fresh.status == .rateLimited(until: usageEpoch.addingTimeInterval(520 + 600)))
        #expect(fresh.fetchedAt == usageEpoch)
    }

    @Test func accountsAreCachedSeparately() async throws {
        let (h, _) = try await harnessWithReading()
        let otherHome = "/Users/test/codex-work"
        let other = AccountKey(provider: .codex, configHome: otherHome, isDefault: false)
        h.files.set("\(otherHome)/auth.json", codexAuthJSON(accessToken: "codex-access-SECRET-9"))
        h.transport.reply(200, body: try fixture("codex-usage-weekly-only"))

        let usage = await h.makeService().usage(for: other, force: false)

        #expect(h.transport.requests.count == 2)
        #expect(usage.windows.map(\.label) == ["7d"])
    }

    @Test func aCorruptCacheFileIsIgnoredAndReplaced() async throws {
        let h = UsageHarness()
        try FileManager.default.createDirectory(
            at: h.cacheURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("{not json".utf8).write(to: h.cacheURL)
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.reply(200, body: try fixture("codex-usage"))

        let usage = await h.makeService().usage(for: codexKey, force: false)

        #expect(usage.status == .ok)
        let reread = try JSONSerialization.jsonObject(with: Data(contentsOf: h.cacheURL))
        #expect(reread is [String: Any])
    }

    @Test func theCacheFileNeverHoldsAToken() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(600)
        h.transport.reply(429, headers: ["Retry-After": "60"])
        _ = await h.makeService().usage(for: codexKey, force: true)

        let user = ClaudeKeychain.currentAccount
        h.secrets.set(service: "Claude Code-credentials", account: user, claudeCredentialsJSON())
        h.transport.reply(200, body: try fixture("claude-usage"))
        _ = await h.makeService().usage(for: claudeKey, force: true)

        let text = h.cacheText
        #expect(!text.isEmpty)
        #expect(!text.contains("SECRET"))
        #expect(!text.contains("codex-access"))
        #expect(!text.contains("claude-access"))
        #expect(!text.contains("refresh"))
        let attributes = try FileManager.default.attributesOfItem(atPath: h.cacheURL.path)
        #expect((attributes[.posixPermissions] as? Int) == 0o600)
    }

    @Test(.timeLimit(.minutes(1))) func concurrentCallsForOneAccountShareOneRequest() async throws {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        let slow = HeldUsageTransport(body: try fixture("codex-usage"))
        let clock = h.clock
        let service = UsageService(
            transport: slow, secrets: h.secrets, files: h.files, cacheURL: h.cacheURL, now: { clock.now })

        let a = Task { await service.usage(for: codexKey, force: true) }
        #expect(await slow.gate.arrived(1))
        // The second call must find the first still in flight.
        let overlapped = AsyncStream<Void>.makeStream()
        await service.setOnFindsInFlight { overlapped.continuation.yield() }
        let b = Task { await service.usage(for: codexKey, force: true) }
        for await _ in overlapped.stream { break }
        slow.gate.open()
        let (first, second) = await (a.value, b.value)

        #expect(slow.count == 1)
        #expect(first == second)
    }

    // MARK: The refresh window and the launch cache

    @Test func freshForDecidesWhetherAGoodReadingIsReused() async throws {
        let (h, first) = try await harnessWithReading()
        h.clock.advance(120)
        // Inside the window, no request is sent.
        #expect(await h.makeService().usage(for: codexKey, force: false, freshFor: 300) == first)
        #expect(h.transport.requests.count == 1)

        // Past it, the same call fetches.
        h.transport.reply(200, body: try fixture("codex-usage"))
        _ = await h.makeService().usage(for: codexKey, force: false, freshFor: 60)
        #expect(h.transport.requests.count == 2)
    }

    @Test func aFailedFetchIsRememberedWithItsStatusAndClearedByASuccess() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(120)
        h.transport.fail()
        let failed = await h.makeService().usage(for: codexKey, force: true)
        #expect(failed.status == .error("could not reach the usage API"))

        // The file remembers the failure: a new service (a fresh launch)
        // sees it without any network or keychain.
        let service = h.makeService()
        let cached = await service.cachedUsage(for: codexKey)
        #expect(cached?.status == .error("could not reach the usage API"))
        #expect(cached?.fetchedAt == usageEpoch)
        #expect(cached?.isStale == true)
        #expect(h.secrets.lookups.isEmpty)
        #expect(h.transport.requests.count == 2)

        // And the next good fetch clears it.
        h.transport.reply(200, body: try fixture("codex-usage"))
        _ = await service.usage(for: codexKey, force: true)
        #expect(await service.cachedUsage(for: codexKey)?.status == .ok)
        #expect(h.cacheText.contains("lastFailure") == false)
    }

    @Test func cachedUsageCarriesARecordedExpiry() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(120)
        h.transport.reply(401)
        _ = await h.makeService().usage(for: codexKey, force: true)
        let cached = await h.makeService().cachedUsage(for: codexKey)
        #expect(cached?.status == .expired)
        #expect(h.secrets.lookups.isEmpty)
    }

    @Test func anExpiredTokenOutranksAFreshCachedReading() async throws {
        let h = UsageHarness()
        let user = ClaudeKeychain.currentAccount
        h.secrets.set(service: "Claude Code-credentials", account: user,
                      claudeCredentialsJSON(expiresAt: usageEpoch.addingTimeInterval(600)))
        h.transport.reply(200, body: try fixture("claude-usage"))
        _ = await h.makeService().usage(for: claudeKey, force: true)

        // The token lapsed since; the reading is minutes old and would
        // serve as fresh, but the expired sign-in must win so a refresh
        // can renew it.
        h.clock.advance(700)
        let usage = await h.makeService().usage(for: claudeKey, force: false,
                                                freshFor: 8 * 3600)
        #expect(usage.status == .expired)
        #expect(h.transport.requests.count == 1)
    }

    @Test func aRecordedFailureMeansTheFreshWindowDoesNotServeTheReading() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(120)  // inside any reasonable refresh window
        h.transport.fail()
        _ = await h.makeService().usage(for: codexKey, force: true)

        // The next plain read fetches: lastFailure says the card is red.
        h.transport.reply(200, body: try fixture("codex-usage"))
        let usage = await h.makeService().usage(for: codexKey, force: false, freshFor: 300)
        #expect(usage.status == .ok)
        #expect(h.transport.requests.count == 3)
    }

    @Test func aCooldownStillAppliesWithARecordedFailure() async throws {
        let (h, _) = try await harnessWithReading()
        h.clock.advance(120)
        h.transport.reply(429, headers: ["Retry-After": "600"])
        _ = await h.makeService().usage(for: codexKey, force: true)
        // In cooldown: no request even though the entry carries a failure.
        let usage = await h.makeService().usage(for: codexKey, force: false)
        #expect(usage.status == .rateLimited(until: usageEpoch.addingTimeInterval(720)))
        #expect(h.transport.requests.count == 2)
    }

    @Test func cachedUsageShowsAFailureThatNeverProducedAReading() async {
        let h = UsageHarness()
        h.files.set("\(codexHome)/auth.json", codexAuthJSON())
        h.transport.fail()
        _ = await h.makeService().usage(for: codexKey, force: true)

        let cached = await h.makeService().cachedUsage(for: codexKey)
        #expect(cached?.status == .error("could not reach the usage API"))
        #expect(cached?.windows.isEmpty == true)
        #expect(cached?.isStale == true)
        #expect(h.secrets.lookups.isEmpty)
    }
}

/// Answers every request with the same body once `gate` opens, counting them.
private final class HeldUsageTransport: HTTPTransport {
    private let body: Data
    private let calls = Mutex(0)
    let gate = HoldGate()
    init(body: Data) { self.body = body }
    var count: Int { calls.withLock { $0 } }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        calls.withLock { $0 += 1 }
        try await gate.pass()
        return (body, HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

/// Holds every keychain lookup while closed, then answers from `inner`.
final class GatedSecrets: SecretReading, @unchecked Sendable {
    private let inner: FakeSecrets
    private let lock = NSLock()
    private var closed = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    init(_ inner: FakeSecrets) { self.inner = inner }

    /// Bumped each time a lookup parks, so the test awaits the park.
    let parks = CountGate()

    func close() { lock.withLock { closed = true } }

    func open() {
        let parked = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            closed = false
            defer { waiting = [] }
            return waiting
        }
        parked.forEach { $0.resume() }
    }

    func genericPassword(service: String, account: String) async -> String? {
        await withCheckedContinuation { continuation in
            let park = lock.withLock { () -> Bool in
                if closed { waiting.append(continuation) }
                return closed
            }
            if park {
                let parks = self.parks
                Task { await parks.bump() }
            } else {
                continuation.resume()
            }
        }
        return await inner.genericPassword(service: service, account: account)
    }
}
