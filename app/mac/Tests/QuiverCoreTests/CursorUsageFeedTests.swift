import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// The Cursor usage feed: `swe harness usage-events cursor --json` mapped
/// into ledger records, deduplicated across overlapping fetches.
@Suite struct CursorUsageFeedTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    let allTime = DateInterval(start: .distantPast, end: .distantFuture)
    let account = "/home/user/Library/Application Support/Cursor"

    private func reply(_ status: String, _ events: String = "",
                       accountId: String? = nil, code: Int32 = 0) -> CommandResult {
        let id = accountId.map { #""account_id": "\#($0)", "# } ?? ""
        return CommandResult(status: code, stdout: Data("""
            {"schema": 1, "data": {"status": "\(status)", \(id)"events": [\(events)]}}
            """.utf8), stderr: "")
    }

    private let twoEvents = """
        {"timestamp": 1791700000.5, "model": "composer-1", "kind": "USAGE_EVENT_KIND_INCLUDED",
         "conversation_id": "conv-1", "headless": false,
         "input_tokens": 1000, "output_tokens": 50,
         "cache_read_tokens": 200, "cache_write_tokens": 30, "cost_usd": 0.04},
        {"timestamp": 1791700100, "model": "grok-bot-default", "kind": "USAGE_EVENT_KIND_FREE_CREDIT",
         "conversation_id": null, "headless": true,
         "input_tokens": 500, "output_tokens": 20,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": null}
        """

    /// Replies chosen by whether `--account-dir` is in the args, so the
    /// default profile and an extra can answer differently.
    private final class SplitRunner: CommandRunning, @unchecked Sendable {
        var replies: [String?: CommandResult] = [:]
        private let lock = NSLock()
        private var recorded: [[String]] = []
        var calls: [[String]] { lock.withLock { recorded } }
        func run(_ arguments: [String]) async throws -> CommandResult {
            lock.withLock { recorded.append(arguments) }
            let dir = arguments.first { $0.hasPrefix("--account-dir=") }
                .map { String($0.dropFirst("--account-dir=".count)) }
            return replies[dir]
                ?? CommandResult(status: 1, stdout: Data(), stderr: "unscripted")
        }
    }

    private func feed(_ runner: any CommandRunning, ledger: Ledger,
                      accounts: [CursorUsageFeed.Account],
                      progressFile: URL? = nil) -> CursorUsageFeed {
        CursorUsageFeed(ledger: ledger, swe: { SweClient(runner: runner) },
                        accounts: { accounts },
                        progressFile: progressFile
                            ?? scratch.url
                                .appending(path: "cursor-feed-\(UUID().uuidString).json"))
    }

    /// A progress file that already knows `profiles[home] = accountId`
    /// and that account's `through` mark.
    private func progressFile(_ home: String, _ accountId: String,
                            through: Double, in temp: TempDir) throws -> URL {
        let url = temp.url.appending(path: "cache/cursor-feed.json")
        let body: [String: Any] = [
            "schema": 1,
            "profiles": [home: accountId],
            "accounts": [accountId: ["through": through]],
        ]
        let data = try JSONSerialization.data(withJSONObject: body)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url)
        return url
    }

    /// The `--since` argument a recorded call carried.
    private func sinceArg(_ call: [String]) -> Double {
        Double(call.first { $0.hasPrefix("--since=") }!.dropFirst("--since=".count))!
    }

    /// The first call parks until `finishCall`; later calls answer `good`
    /// at once. Lets a test hold the old generation's fetch in flight
    /// while a swe-path change runs a newer poll. `claimed` picks the
    /// first call, `called` marks it parked — set under the same lock
    /// section that drains `waiter`, so `waitUntilCalled` can neither
    /// miss nor hang.
    private final class FirstHeldRunner: CommandRunning, @unchecked Sendable {
        let good: CommandResult
        private let lock = NSLock()
        private var recorded: [[String]] = []
        private var held: CheckedContinuation<CommandResult, Never>?
        private var waiter: CheckedContinuation<Void, Never>?
        private var called = false
        private var claimed = false

        init(good: CommandResult) { self.good = good }
        var calls: [[String]] { lock.withLock { recorded } }

        func run(_ arguments: [String]) async throws -> CommandResult {
            lock.withLock { recorded.append(arguments) }
            let isFirst = lock.withLock { () -> Bool in
                defer { claimed = true }
                return !claimed
            }
            if isFirst {
                return await withCheckedContinuation {
                    (c: CheckedContinuation<CommandResult, Never>) in
                    let pending = lock.withLock { () -> CheckedContinuation<Void, Never>? in
                        held = c
                        called = true
                        defer { waiter = nil }
                        return waiter
                    }
                    pending?.resume()
                }
            }
            return good
        }

        func waitUntilCalled() async {
            await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                let ready = lock.withLock { () -> Bool in
                    if called { return true }
                    waiter = c
                    return false
                }
                if ready { c.resume() }
            }
        }

        func finishCall(_ result: CommandResult) {
            let pending = lock.withLock { () -> CheckedContinuation<CommandResult, Never>? in
                defer { held = nil }
                return held
            }
            pending?.resume(returning: result)
        }
    }

    /// Parks call `park` (0-based, counting every call) until
    /// `finishCall`; every other call answers `good` at once.
    private final class NthHeldRunner: CommandRunning, @unchecked Sendable {
        let park: Int
        let good: CommandResult
        private let lock = NSLock()
        private var recorded: [[String]] = []
        private var held: CheckedContinuation<CommandResult, Never>?
        private var waiters: [CheckedContinuation<Void, Never>] = []
        private var heldStarted = false

        init(park: Int, good: CommandResult) { self.park = park; self.good = good }
        var calls: [[String]] { lock.withLock { recorded } }

        func run(_ arguments: [String]) async throws -> CommandResult {
            let isHeld = lock.withLock { () -> Bool in
                recorded.append(arguments)
                return recorded.count - 1 == park
            }
            if isHeld {
                return await withCheckedContinuation {
                    (c: CheckedContinuation<CommandResult, Never>) in
                    let pending = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
                        held = c
                        heldStarted = true
                        defer { waiters.removeAll() }
                        return waiters
                    }
                    pending.forEach { $0.resume() }
                }
            }
            return good
        }

        func waitUntilHeld() async {
            await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                let ready = lock.withLock { () -> Bool in
                    if heldStarted { return true }
                    waiters.append(c)
                    return false
                }
                if ready { c.resume() }
            }
        }

        func finishCall(_ result: CommandResult) {
            let pending = lock.withLock { () -> CheckedContinuation<CommandResult, Never>? in
                defer { held = nil }
                return held
            }
            pending?.resume(returning: result)
        }
    }

    private func accts() -> [CursorUsageFeed.Account] { [CursorUsageFeed.Account(home: account)] }

    @Test func eventsMapToLedgerRecords() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", twoEvents)),
        ])
        await feed(runner, ledger: ledger, accounts: accts()).poll()

        let events = await ledger.events(in: allTime).sorted { $0.timestamp < $1.timestamp }
        #expect(events.count == 2)
        let composer = events[0]
        #expect(composer.harness == "cursor")
        #expect(composer.accountHome == account)
        #expect(composer.sessionId == "conv-1")
        #expect(composer.model == "composer-1")
        #expect(composer.inputTokens == 1000)
        #expect(composer.outputTokens == 50)
        #expect(composer.cacheReadTokens == 200)
        #expect(composer.cacheWriteTokens == 30)
        #expect(composer.costUSD == 0.04)
        let grok = events[1]
        #expect(grok.harness == "grok-bot")
        #expect(grok.sessionId.hasPrefix("cursor-"))
        #expect(grok.costUSD == nil)
        #expect(grok.isSessionTotal == false)
    }

    @Test func anAbsurdTimestampIsDroppedAndDoesNotMoveThrough() async throws {
        // 1e30 s would overflow the Int64 millisecond key and, as the
        // `through` mark, the next poll's `--since`.
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let file = try progressFile(account, "uid-1", through: 1791700100, in: temp)
        let absurd = """
            {"timestamp": 1e30, "model": "composer-1", "kind": "USAGE_EVENT_KIND_INCLUDED",
             "conversation_id": "conv-x", "headless": false, "input_tokens": 9, "output_tokens": 1,
             "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": null}
            """
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             reply("ok", twoEvents + "," + absurd, accountId: "uid-1")),
        ])
        await feed(runner, ledger: ledger, accounts: accts(), progressFile: file).poll()
        #expect(await ledger.events(in: allTime).count == 2)
        let saved = try JSONSerialization.jsonObject(
            with: Data(contentsOf: file)) as! [String: Any]
        #expect((saved["accounts"] as? [String: [String: Double]])?["uid-1"]?["through"]
                == 1791700100)
    }

    @Test func anOverlappingRefetchDoesNotDuplicate() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", twoEvents)),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts())
        await feed.poll()
        await feed.poll()
        let events = await ledger.events(in: allTime)
        #expect(events.count == 2)
        let totals = await ledger.totals(in: allTime)
        #expect(totals.inputTokens == 1500)
        #expect(totals.costUSD == 0.04)
    }

    /// The dedup key names the event, not its counts: when Cursor corrects
    /// an event's counts, the row is replaced rather than doubled.
    @Test func aCountCorrectionReplacesTheRow() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        func event(input: Int, output: Int) -> CursorUsageEvent {
            CursorUsageEvent(
                timestamp: 1791700000.5, model: "composer-1",
                kind: "USAGE_EVENT_KIND_INCLUDED", conversationId: "conv-1", headless: false,
                inputTokens: input, outputTokens: output,
                cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: nil)
        }
        try await ledger.ingest([CursorUsageFeed.record(
            for: event(input: 1_000, output: 50), accountKey: "uid-1", accountHome: account)])
        try await ledger.ingest([CursorUsageFeed.record(
            for: event(input: 2_000, output: 60), accountKey: "uid-1", accountHome: account)])
        let events = await ledger.events(in: allTime)
        #expect(events.count == 1)
        #expect(events[0].inputTokens == 2_000)
        #expect(events[0].outputTokens == 60)
    }

    /// Rows written under the old key — which embedded the counts — are
    /// removed when the corrected event lands, not left double-counting.
    @Test func aRowKeyedByTheOldCountsSchemeIsCleanedUp() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let stale = LedgerRecord(
            key: "cursor-feed:uid-1:1791700000500:composer-1:conv-1:1000:50",
            authoritative: true,
            event: UsageEvent(
                harness: "cursor", accountHome: account, sessionId: "conv-1",
                model: "composer-1", timestamp: Date(timeIntervalSince1970: 1791700000.5),
                inputTokens: 1_000, outputTokens: 50))
        try await ledger.ingest([stale])
        try await ledger.ingest([CursorUsageFeed.record(
            for: CursorUsageEvent(
                timestamp: 1791700000.5, model: "composer-1",
                kind: "USAGE_EVENT_KIND_INCLUDED", conversationId: "conv-1", headless: false,
                inputTokens: 2_000, outputTokens: 60,
                cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: nil),
            accountKey: "uid-1", accountHome: account)])
        let events = await ledger.events(in: allTime)
        #expect(events.count == 1)
        #expect(events[0].inputTokens == 2_000)
    }

    /// The cleanup matches the event's identity, not the profile that
    /// read it: a row profile A stored under the old counts scheme is
    /// removed when profile B re-imports the same account's event.
    @Test func anOldSchemeRowFromAnotherProfileIsCleanedUp() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let removedProfile = "/t/cursor-gone/desktop"
        let stale = LedgerRecord(
            key: "cursor-feed:uid-1:1791700000500:composer-1:conv-1:1000:50",
            authoritative: true,
            event: UsageEvent(
                harness: "cursor", accountHome: removedProfile, sessionId: "conv-1",
                model: "composer-1", timestamp: Date(timeIntervalSince1970: 1791700000.5),
                inputTokens: 1_000, outputTokens: 50))
        try await ledger.ingest([stale])
        // The same account read through this profile — even with the same
        // counts, the new key differs from the counts-bearing old one.
        try await ledger.ingest([CursorUsageFeed.record(
            for: CursorUsageEvent(
                timestamp: 1791700000.5, model: "composer-1",
                kind: "USAGE_EVENT_KIND_INCLUDED", conversationId: "conv-1", headless: false,
                inputTokens: 1_000, outputTokens: 50,
                cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: nil),
            accountKey: "uid-1", accountHome: account)])
        let events = await ledger.events(in: allTime)
        #expect(events.count == 1)
        #expect(events[0].accountHome == account)
    }

    /// The cross-profile match must not reach another account: a row for
    /// the same day bucket, model and instant but keyed under a different
    /// account id survives the cleanup.
    @Test func anotherAccountsLookalikeRowKeepsItsUsage() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        // uid-2's event, as its profile stored it under the old scheme:
        // no conversation, so the day bucket and the rest of the event
        // identity coincide with uid-1's.
        let otherAccount = LedgerRecord(
            key: "cursor-feed:uid-2:1791700000500:composer-1::700:20",
            authoritative: true,
            event: UsageEvent(
                harness: "cursor", accountHome: account,
                sessionId: "cursor-" + CursorUsageFeed.dayLabel(for: 1791700000.5),
                model: "composer-1", timestamp: Date(timeIntervalSince1970: 1791700000.5),
                inputTokens: 700, outputTokens: 20))
        try await ledger.ingest([otherAccount])
        try await ledger.ingest([CursorUsageFeed.record(
            for: CursorUsageEvent(
                timestamp: 1791700000.5, model: "composer-1",
                kind: "USAGE_EVENT_KIND_INCLUDED", conversationId: nil, headless: false,
                inputTokens: 1_000, outputTokens: 50,
                cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: nil),
            accountKey: "uid-1", accountHome: "/t/cursor-side/desktop")])
        let events = await ledger.events(in: allTime)
        #expect(events.count == 2)
        #expect(Set(events.map(\.inputTokens)) == [700, 1_000])
    }

    @Test func theWindowStartsAt180DaysThenOverlapsTwo() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             reply("ok", twoEvents, accountId: "uid-1")),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts(),
                        progressFile: temp.url.appending(path: "cache/cursor-feed.json"))

        let before = Date().addingTimeInterval(-180 * 86_400).timeIntervalSince1970
        await feed.poll()
        // First poll asks for the whole 180-day window (--since is whole
        // seconds, so it truncates up to a second).
        #expect((before - 1)...before + 60 ~= sinceArg(runner.calls[0]))

        await feed.poll()
        // Later polls resume from the account's newest event minus the
        // two-day overlap.
        let expected = 1791700100 - 2 * 86_400
        #expect(abs(sinceArg(runner.calls[1]) - Double(expected)) < 2)

        // ... and the progress file round-trips: the profile names the
        // account, the account carries the newest timestamp.
        let saved = try JSONSerialization.jsonObject(
            with: Data(contentsOf: temp.url.appending(path: "cache/cursor-feed.json")))
            as! [String: Any]
        #expect((saved["profiles"] as? [String: String]) == [account: "uid-1"])
        #expect((saved["accounts"] as? [String: [String: Double]])?["uid-1"]?["through"]
                == 1791700100)
    }

    @Test func authRequiredAndSignedOutKeepTheStoredRows() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        try await ledger.ingest([CursorUsageFeed.record(for: CursorUsageEvent(
            timestamp: 1791700000, model: "composer-1", kind: "k", conversationId: "c",
            headless: false, inputTokens: 5, outputTokens: 1,
            cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: nil),
            accountKey: account, accountHome: account)])
        for status in ["auth-required", "signed-out", "unavailable"] {
            let runner = LockedRunner([
                (["harness", "usage-events", "cursor"],
                 // `unavailable` exits non-zero but still answers JSON.
                 reply(status, "", code: status == "unavailable" ? 1 : 0)),
            ])
            await feed(runner, ledger: ledger, accounts: accts()).poll()
        }
        let events = await ledger.events(in: allTime)
        #expect(events.count == 1)
        #expect(events[0].inputTokens == 5)
    }

    @Test func anOlderSweIsAskedOnceThenLeftAlone() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             CommandResult(status: 1, stdout: Data(),
                           stderr: "Unknown harness subcommand: usage-events")),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts())
        await feed.poll()
        await feed.poll()
        #expect(runner.calls.count == 1)
    }

    @Test func extraAccountsPassTheirAccountDir() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", "")),
        ])
        let extra = CursorUsageFeed.Account(home: "/t/cursor-side/desktop",
                                            accountDir: "/t/cursor-side/desktop")
        await feed(runner, ledger: ledger, accounts: accts() + [extra]).poll()
        #expect(runner.calls.contains {
            $0.contains("--account-dir=/t/cursor-side/desktop") })
        #expect(runner.calls.contains { !$0.contains { $0.hasPrefix("--account-dir") } })
    }

    @Test func grokBotIsOnTheCalendarButCursorIsNot() async throws {
        #expect(Ledger.calendarHarnesses.contains("grok-bot"))
        #expect(!Ledger.calendarHarnesses.contains("cursor"))
        #expect(HarnessMark("grok-bot") == HarnessMark(icon: "grok", word: nil, name: "Grok Bot"))

        // End to end: an ingested grok-bot event becomes an activity block.
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        try await ledger.ingest([CursorUsageFeed.record(for: CursorUsageEvent(
            timestamp: Date().timeIntervalSince1970 - 60, model: "grok-bot-cua",
            kind: "k", conversationId: "c9", headless: true,
            inputTokens: 10, outputTokens: 200,
            cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0.001),
            accountKey: account, accountHome: account)])
        let range = DateInterval(start: .now.addingTimeInterval(-3600), duration: 3600)
        let blocks = try ledger.activityBlocks(in: range, gap: 900, force: true)
        #expect(blocks.contains { $0.harness == "grok-bot" })
    }

    @MainActor @Test func anIngestedChangeBumpsTheOverviewRevision() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", twoEvents)),
        ])
        let overview = OverviewModel(ledger: ledger, roots: { LedgerRoots() },
                                     accountLabel: { $0 })
        let before = overview.revision
        let feed = CursorUsageFeed(
            ledger: ledger, swe: { SweClient(runner: runner) }, accounts: { self.accts() },
            onIngested: { await overview.reloadAfterLedgerWrite() })
        await feed.poll()
        #expect(overview.revision == before + 1)
        // The calendar watches the same counter the scan path bumps.
        #expect(overview.ledgerWrites == 1)
        // An identical refetch changes no rows, so no reload fires.
        await feed.poll()
        #expect(overview.revision == before + 1)
        #expect(overview.ledgerWrites == 1)
    }

    @Test func twoProfilesOnOneAccountStoreOneCopy() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             reply("ok", twoEvents, accountId: "uid-1")),
        ])
        let extra = CursorUsageFeed.Account(home: "/t/cursor-side/desktop",
                                            accountDir: "/t/cursor-side/desktop")
        await feed(runner, ledger: ledger, accounts: accts() + [extra]).poll()
        let events = await ledger.events(in: allTime)
        // The second profile's identical events never land.
        #expect(events.count == 2)
        // accountHome stays the profile the account was first seen on.
        #expect(events.allSatisfy { $0.accountHome == account })
    }

    @Test func aReplyWithoutAccountIdKeysByProfileHome() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", twoEvents)),
        ])
        let extra = CursorUsageFeed.Account(home: "/t/cursor-side/desktop",
                                            accountDir: "/t/cursor-side/desktop")
        await feed(runner, ledger: ledger, accounts: accts() + [extra]).poll()
        let events = await ledger.events(in: allTime)
        // No account_id means no deduplication: each profile's rows key on
        // its own home, so both copies are stored.
        #expect(events.count == 4)
        #expect(Set(events.map(\.accountHome)).count == 2)
    }

    @Test func aPollFailureSetsLastError() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             CommandResult(status: 1, stdout: Data(), stderr: "database is locked")),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts())
        await feed.poll()
        let error = await feed.lastError
        #expect(error?.contains("database is locked") == true)
    }

    @Test func anIncompleteReplyStillIngests() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("incomplete", twoEvents)),
        ])
        await feed(runner, ledger: ledger, accounts: accts()).poll()
        let events = await ledger.events(in: allTime)
        #expect(events.count == 2)
    }

    @Test func anExpiredDefaultDoesNotBlockAWorkingProfileOnTheSameAccount() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let runner = SplitRunner()
        // The default profile is signed into the same account but its
        // session is stale; the extra profile's session works.
        runner.replies[nil] = reply("auth-required", "", accountId: "uid-1")
        runner.replies["/t/cursor-side/desktop"] =
            reply("ok", twoEvents, accountId: "uid-1")
        let extra = CursorUsageFeed.Account(home: "/t/cursor-side/desktop",
                                            accountDir: "/t/cursor-side/desktop")
        await feed(runner, ledger: ledger, accounts: accts() + [extra]).poll()
        let events = await ledger.events(in: allTime)
        // The expired default never marked the account, so the working
        // extra imported it once, under its own home.
        #expect(events.count == 2)
        #expect(events.allSatisfy { $0.accountHome == "/t/cursor-side/desktop" })
    }

    @Test func anAccountSwitchFetchesTheNewAccountsFullWindow() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        // This profile used to answer as uid-old, which has a mark.
        let file = try progressFile(account, "uid-old", through: 1791700100, in: temp)
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             reply("ok", twoEvents, accountId: "uid-new")),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts(), progressFile: file)
        // The full window starts 180 days before the poll; bracket the
        // poll itself so a slow run can't land outside (the feed sends
        // whole seconds, hence the second of slack below).
        let window = CursorUsageFeed.window
        let start = Date().timeIntervalSince1970 - window
        await feed.poll()
        let end = Date().timeIntervalSince1970 - window
        // First call used uid-old's mark minus the overlap...
        #expect(abs(sinceArg(runner.calls[0]) - Double(1791700100 - 2 * 86_400)) < 2)
        // ... and the switch to uid-new re-asked for its full window.
        #expect((start - 1)...end ~= sinceArg(runner.calls[1]))
        #expect(await ledger.events(in: allTime).count == 2)
    }

    @Test func aStaleReplyCannotReinstateTheTooOldVerdict() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        // Call 1 (the old path's poll) parks; the path change's poll
        // answers "ok" and ingests before the parked reply lands.
        let runner = FirstHeldRunner(good: reply("ok", twoEvents, accountId: "uid-1"))
        let feed = feed(runner, ledger: ledger, accounts: accts())
        let stale = Task { await feed.poll() }
        await runner.waitUntilCalled()
        await feed.swePathChanged()
        #expect(await ledger.events(in: allTime).count == 2)
        // The old path's fetch fails "unknown subcommand" only now: its
        // generation is stale, so the verdict must not come back.
        runner.finishCall(CommandResult(
            status: 1, stdout: Data(),
            stderr: "Unknown harness subcommand: usage-events"))
        await stale.value
        // sweTooOld stayed off: the next poll still asks swe.
        await feed.poll()
        #expect(runner.calls.count == 3)
        let lastError = await feed.lastError
        #expect(lastError == nil)
    }

    @Test func aStaleSuccessCannotClearTheNewerTooOldVerdict() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        // Call 0 parks; the path change's poll hears "unknown subcommand"
        // and sets the verdict. The parked old reply answering "ok" must
        // not clear it.
        let runner = NthHeldRunner(
            park: 0,
            good: CommandResult(status: 1, stdout: Data(),
                                stderr: "Unknown harness subcommand: usage-events"))
        let feed = feed(runner, ledger: ledger, accounts: accts())
        let stale = Task { await feed.poll() }
        await runner.waitUntilHeld()
        await feed.swePathChanged()
        runner.finishCall(reply("ok", twoEvents, accountId: "uid-1"))
        await stale.value
        // The verdict stuck: the next poll returns without asking swe.
        await feed.poll()
        #expect(runner.calls.count == 2)
    }

    @Test func aSupersededRoundStillReportsItsIngestedRows() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let extra = CursorUsageFeed.Account(home: "/t/cursor-side/desktop",
                                            accountDir: "/t/cursor-side/desktop")
        // The old round's first account answers and ingests; its second
        // parks while a swe-path change supersedes the round.
        let runner = NthHeldRunner(park: 1, good: reply("ok", twoEvents, accountId: "uid-1"))
        let ingests = Mutex(0)
        let feed = CursorUsageFeed(
            ledger: ledger, swe: { SweClient(runner: runner) },
            accounts: { self.accts() + [extra] },
            progressFile: temp.url.appending(path: "cache/cursor-feed.json"),
            onIngested: { ingests.withLock { $0 += 1 } })
        let stale = Task { await feed.poll() }
        await runner.waitUntilHeld()
        await feed.swePathChanged()
        runner.finishCall(reply("ok", twoEvents, accountId: "uid-2"))
        await stale.value
        // The new round re-ingests the same keys, so its own callback is
        // a no-op — the one notification is the superseded round's
        // already-written rows.
        #expect(ingests.withLock { $0 } == 1)
        #expect(await ledger.events(in: allTime).count == 2)
    }

    @Test func anIncompleteReplyIngestsButDoesNotAdvanceThrough() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"))
        let file = try progressFile(account, "uid-1", through: 1791700100, in: temp)
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             reply("incomplete", twoEvents, accountId: "uid-1")),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts(), progressFile: file)
        await feed.poll()
        // The events still landed...
        #expect(await ledger.events(in: allTime).count == 2)
        // ... but `through` stayed put, so the next poll asks for the same
        // range again rather than skipping what the cap hid.
        let saved = try JSONSerialization.jsonObject(
            with: Data(contentsOf: file)) as! [String: Any]
        #expect((saved["accounts"] as? [String: [String: Double]])?["uid-1"]?["through"]
                == 1791700100)
        await feed.poll()
        let expected = 1791700100 - 2 * 86_400
        #expect(abs(sinceArg(runner.calls[1]) - Double(expected)) < 2)
    }
}

