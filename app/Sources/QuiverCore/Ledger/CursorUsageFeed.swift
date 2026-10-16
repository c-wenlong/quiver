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
    /// The most recent per-account poll failure, for later display. The
    /// stored rows are untouched either way; this only says why nothing
    /// new arrived.
    public private(set) var lastError: String?
    /// Runs after a poll round that inserted or changed rows, so the
    /// Overview and menu bar reload like they do after a scan.
    private let onIngested: (@Sendable () async -> Void)?
    private static let log = Logger(subsystem: "com.cwenlong.quiver", category: "cursor-feed")

    public init(ledger: Ledger, swe: SweClient,
                accounts: @escaping @Sendable () -> [Account],
                onIngested: (@Sendable () async -> Void)? = nil) {
        self.ledger = ledger
        self.swe = swe
        self.accounts = accounts
        self.onIngested = onIngested
    }

    /// Poll on a timer until cancelled: once at launch, then every
    /// `interval` (15 minutes in the app).
    public func keepFresh(every interval: Duration = .seconds(900)) async {
        while !Task.isCancelled {
            await poll()
            do { try await Task.sleep(for: interval) } catch { return }
        }
    }

    /// Fetch and ingest every account once, in `accounts` order (the
    /// default profile first). Two profiles can sign in to the same
    /// Cursor account, so a reply's `account_id` that a sibling already
    /// imported this round is skipped: the events would key identically
    /// anyway, and the first-seen profile keeps them as `accountHome`.
    /// Any other per-account miss — signed out, an expired session, swe
    /// reporting the endpoint down, a process that failed to run — keeps
    /// the stored rows, is logged and retried next round.
    public func poll() async {
        guard !sweTooOld else { return }
        var seenAccounts = Set<String>()
        var changed = 0
        for account in accounts() {
            do {
                changed += try await poll(account, seenAccounts: &seenAccounts)
            } catch SweError.commandFailed(_, let message)
                where message.localizedCaseInsensitiveContains("unknown harness subcommand")
                    || message.localizedCaseInsensitiveContains("unknown argument") {
                sweTooOld = true
                Self.log.info("installed swe has no harness usage-events; skipping the Cursor feed")
                return
            } catch {
                note(error, for: account)
            }
        }
        if changed > 0 { await onIngested?() }
    }

    /// Polls one profile and ingests its events, returning how many rows
    /// changed. A reply whose `account_id` another profile already
    /// imported this round is left alone.
    private func poll(_ account: Account, seenAccounts: inout Set<String>) async throws -> Int {
        let latest = try await ledger.latestEventTimestamp(
            harnesses: Self.harnesses, accountHome: account.home)
        // First run asks for the full window; afterwards the newest stored
        // event minus the overlap.
        let since = max(0, latest?.addingTimeInterval(-Self.overlap).timeIntervalSince1970
            ?? Date().addingTimeInterval(-Self.window).timeIntervalSince1970)
        let reply = try await swe.usageEvents(accountDir: account.accountDir, since: since)
        if let accountId = reply.accountId {
            guard seenAccounts.insert(accountId).inserted else {
                Self.log.info("cursor feed: a second profile on the same account; skipping it")
                return 0
            }
        }
        // signed-out / auth-required / unavailable all keep the stored data.
        guard reply.status == "ok" || reply.status == "incomplete" else { return 0 }
        if reply.status == "incomplete" {
            Self.log.warning(
                "cursor feed: part of the window hit the page cap; ingesting what arrived")
        }
        // The key is the account when swe reports one, the profile's home
        // when it cannot. Two profiles on one account therefore write the
        // same keys, and `accountHome` stays with the first-seen profile.
        let accountKey = reply.accountId ?? account.home
        return try await ledger.ingest(reply.events.map {
            Self.record(for: $0, accountKey: accountKey, accountHome: account.home)
        })
    }

    /// Record a per-account poll failure for logs and later display. The
    /// message names the error and its description only: command arguments
    /// (which carry `--account-dir` paths) never appear in it.
    private func note(_ error: Error, for account: Account) {
        let description = String(describing: type(of: error))
        let detail = error.localizedDescription
        lastError = detail.isEmpty ? description : "\(description): \(detail)"
        Self.log.error("cursor feed poll failed: \(self.lastError ?? description)")
    }

    /// One feed event as a ledger record. `accountKey` is the account the
    /// profile reports (its `account_id`), or the profile home when swe
    /// cannot name one; `accountHome` is always the profile that read it.
    /// The key names the account, event instant and token counts, so an
    /// overlapping refetch — or a second profile on the same account —
    /// writes the same rows again rather than adding new ones.
    static func record(for event: CursorUsageEvent, accountKey: String,
                       accountHome: String) -> LedgerRecord {
        let harness = event.model.hasPrefix("grok-bot") ? "grok-bot" : "cursor"
        let milliseconds = Int64((event.timestamp * 1000).rounded())
        let session = event.conversationId ?? "cursor-" + dayLabel(for: event.timestamp)
        let key = "cursor-feed:\(accountKey):\(milliseconds):\(event.model):"
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
    /// The Cursor user id the token claims (`sub`), nil when swe had no
    /// token to read it from. Not a credential.
    public var accountId: String?
    public var events: [CursorUsageEvent]
}
