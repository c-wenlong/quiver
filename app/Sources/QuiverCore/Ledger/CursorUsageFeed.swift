import Foundation
import os

/// Cursor's per-event usage feed, polled through `swe harness usage-events
/// cursor --json` and ingested into the ledger. The feed covers the editor,
/// cursor-agent, cloud agents and Grok Bot, which signs in with the Cursor
/// account; Grok Bot's own models are stored under a `grok-bot` harness so
/// they price and chart separately from Cursor's.
public actor CursorUsageFeed {
    /// One Cursor sign-in the feed reads: `home` is the ledger's account
    /// dim (the desktop user-data directory) and `accountDir` is what swe's
    /// `--account-dir` reads. The default account passes no dir, so swe
    /// uses its own state.db lookup and the keychain.
    public struct Account: Sendable, Equatable {
        public var home: String
        public var accountDir: String?
        public init(home: String, accountDir: String? = nil) {
            self.home = home
            self.accountDir = accountDir
        }
    }

    /// Cursor serves at most the last 180 days; a first poll asks for all
    /// of it and each later one overlaps by two days so an event that lands
    /// late on an old page is still picked up (re-ingest is idempotent).
    static let window: TimeInterval = 180 * 86_400
    static let overlap: TimeInterval = 2 * 86_400
    /// The harness names feed events are stored under.
    static let harnesses = ["cursor", "grok-bot"]

    private let ledger: Ledger
    private let swe: SweClient
    private let accounts: @Sendable () -> [Account]
    /// An swe without `usage-events` is asked once, then left alone for the
    /// rest of the session.
    private var sweTooOld = false
    private static let log = Logger(subsystem: "com.cwenlong.quiver", category: "cursor-feed")

    public init(ledger: Ledger, swe: SweClient, accounts: @escaping @Sendable () -> [Account]) {
        self.ledger = ledger
        self.swe = swe
        self.accounts = accounts
    }

    /// Poll on a timer until cancelled: once at launch, then every
    /// `interval` (15 minutes in the app).
    public func keepFresh(every interval: Duration = .seconds(900)) async {
        while !Task.isCancelled {
            await poll()
            do { try await Task.sleep(for: interval) } catch { return }
        }
    }

    /// Fetch and ingest every account once. Any per-account miss — signed
    /// out, an expired session, swe reporting the endpoint down, a process
    /// that failed to run — keeps the stored rows and is retried next round.
    public func poll() async {
        guard !sweTooOld else { return }
        for account in accounts() {
            do {
                try await poll(account)
            } catch SweError.commandFailed(_, let message)
                where message.localizedCaseInsensitiveContains("unknown harness subcommand")
                    || message.localizedCaseInsensitiveContains("unknown argument") {
                sweTooOld = true
                Self.log.info("installed swe has no harness usage-events; skipping the Cursor feed")
                return
            } catch {
                continue
            }
        }
    }

    private func poll(_ account: Account) async throws {
        let latest = try await ledger.latestEventTimestamp(
            harnesses: Self.harnesses, accountHome: account.home)
        // First run asks for the full window; afterwards the newest stored
        // event minus the overlap.
        let since = max(0, latest?.addingTimeInterval(-Self.overlap).timeIntervalSince1970
            ?? Date().addingTimeInterval(-Self.window).timeIntervalSince1970)
        let reply = try await swe.usageEvents(accountDir: account.accountDir, since: since)
        // signed-out / auth-required / unavailable all keep the stored data.
        guard reply.status == "ok" else { return }
        try await ledger.ingest(reply.events.map { Self.record(for: $0, accountHome: account.home) })
    }

    /// One feed event as a ledger record. The key names the account, event
    /// instant and token counts, so an overlapping refetch writes the same
    /// rows again rather than adding new ones.
    static func record(for event: CursorUsageEvent, accountHome: String) -> LedgerRecord {
        let harness = event.model.hasPrefix("grok-bot") ? "grok-bot" : "cursor"
        let milliseconds = Int64((event.timestamp * 1000).rounded())
        let session = event.conversationId ?? "cursor-" + dayLabel(for: event.timestamp)
        let key = "cursor-feed:\(accountHome):\(milliseconds):\(event.model):"
            + "\(event.conversationId ?? ""):\(event.inputTokens):\(event.outputTokens)"
        return LedgerRecord(
            key: key, messageId: nil, authoritative: true,
            event: UsageEvent(
                harness: harness, accountHome: accountHome, sessionId: session,
                model: event.model,
                timestamp: Date(timeIntervalSince1970: event.timestamp),
                inputTokens: event.inputTokens, outputTokens: event.outputTokens,
                cacheReadTokens: event.cacheReadTokens, cacheWriteTokens: event.cacheWriteTokens,
                costUSD: event.costUsd))
    }

    /// `yyyy-mm-dd` in the local calendar, for the session label of events
    /// Cursor does not give a conversation id to.
    static func dayLabel(for timestamp: TimeInterval) -> String {
        let parts = Calendar.current.dateComponents(
            [.year, .month, .day], from: Date(timeIntervalSince1970: timestamp))
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}

/// One event from `swe harness usage-events cursor --json`.
public struct CursorUsageEvent: Decodable, Sendable, Equatable {
    public var timestamp: Double
    public var model: String
    public var kind: String
    public var conversationId: String?
    public var headless: Bool
    public var inputTokens: Int
    public var outputTokens: Int
    public var cacheReadTokens: Int
    public var cacheWriteTokens: Int
    public var costUsd: Double?
}

extension SweClient {
    /// `swe harness usage-events cursor --since=<s> --json`. `unavailable`
    /// exits non-zero but still answers JSON, so failure is allowed and the
    /// status is read out of the reply.
    public func usageEvents(accountDir: String?, since: TimeInterval) async throws -> CursorUsageEventsReply {
        var args = ["harness", "usage-events", "cursor", "--since=\(Int(since))", "--json"]
        if let accountDir { args.append("--account-dir=\(accountDir)") }
        return try await call(args, allowFailure: true)
    }
}

public struct CursorUsageEventsReply: Decodable, Sendable {
    public var status: String
    public var events: [CursorUsageEvent]
}
