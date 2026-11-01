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
    /// The swe chosen in Settings right now, read at call time so a path
    /// change reaches the feed instead of staying on the launch-time one.
    private let swe: @Sendable () -> SweClient
    private let accounts: @Sendable () -> [Account]
    /// `cache/cursor-feed.json`: which account each profile answers as,
    /// and each account's newest imported timestamp.
    private let progressFile: URL
    private var progress: CursorFeedProgress
    /// An swe without `usage-events` is asked once, then left alone for the
    /// rest of the session.
    private var sweTooOld = false
    /// Bumped by every poll round and swe-path change; replies from an
    /// older generation are dropped so a stale fetch cannot reinstate
    /// `sweTooOld` or write data after the path moved on.
    private var requestGeneration = 0
    /// The most recent per-account poll failure, for later display. The
    /// stored rows are untouched either way; this only says why nothing
    /// new arrived.
    public private(set) var lastError: String?
    /// Runs after a poll round that inserted or changed rows, so the
    /// Overview and menu bar reload like they do after a scan.
    private let onIngested: (@Sendable () async -> Void)?
    private static let log = Logger(subsystem: "com.cwenlong.quiver", category: "cursor-feed")

    public init(ledger: Ledger, swe: @escaping @Sendable () -> SweClient,
                accounts: @escaping @Sendable () -> [Account],
                progressFile: URL? = nil,
                onIngested: (@Sendable () async -> Void)? = nil) {
        self.ledger = ledger
        self.swe = swe
        self.accounts = accounts
        self.progressFile = progressFile ?? FileManager.default.homeDirectoryForCurrentUser
            .appending(path: ".swe/cache/cursor-feed.json")
        self.progress = (try? JSONDecoder().decode(
            CursorFeedProgress.self,
            from: (try? Data(contentsOf: self.progressFile)) ?? Data())) ?? CursorFeedProgress()
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
        requestGeneration += 1
        let generation = requestGeneration
        var seenAccounts = Set<String>()
        var changed = 0
        var dirty = false
        for account in accounts() {
            do {
                changed += try await poll(
                    account, seenAccounts: &seenAccounts, dirty: &dirty,
                    generation: generation)
                // A stale success must not clear a newer generation's
                // too-old verdict.
                guard generation == requestGeneration else { break }
                sweTooOld = false
            } catch SweError.commandFailed(_, let message)
                where message.localizedCaseInsensitiveContains("unknown harness subcommand")
                    || message.localizedCaseInsensitiveContains("unknown argument") {
                guard generation == requestGeneration else { break }
                sweTooOld = true
                Self.log.info("installed swe has no harness usage-events; skipping the Cursor feed")
                return
            } catch {
                guard generation == requestGeneration else { break }
                note(error, for: account)
            }
        }
        // Rows a superseded round already wrote still count and still
        // notify; only the progress save belongs to the current one.
        if generation == requestGeneration, dirty { saveProgress() }
        if changed > 0 { await onIngested?() }
    }

    /// The swe path changed in Settings: the "too old" verdict was about
    /// the old build, so clear it and retry the feed at once.
    public func swePathChanged() async {
        requestGeneration += 1
        sweTooOld = false
        await poll()
    }

    /// Polls one profile and ingests its events, returning how many rows
    /// changed.
    private func poll(_ account: Account,
                      seenAccounts: inout Set<String>,
                      dirty: inout Bool,
                      generation: Int) async throws -> Int {
        let fullWindow = Date().addingTimeInterval(-Self.window).timeIntervalSince1970
        // The window each account is read through: its high-water mark
        // minus the overlap, or the whole feed window when unknown.
        func window(for accountId: String?) -> TimeInterval {
            accountId.flatMap { progress.accounts[$0]?.through }
                .map { max(0, $0 - Self.overlap) } ?? fullWindow
        }
        let expected = progress.profiles[account.home]
        var reply = try await swe().usageEvents(
            accountDir: account.accountDir, since: window(for: expected))
        guard generation == requestGeneration else { return 0 }
        // A switched or newly seen account reads through its own mark —
        // the profile's old account's overlap would skip the new one's
        // history.
        if let accountId = reply.accountId, accountId != expected {
            let own = window(for: accountId)
            if own != window(for: expected) {
                reply = try await swe().usageEvents(
                    accountDir: account.accountDir, since: own)
                guard generation == requestGeneration else { return 0 }
            }
        }
        if let accountId = reply.accountId, seenAccounts.contains(accountId) {
            Self.log.info("cursor feed: a second profile on the same account; skipping it")
            return 0
        }
        // signed-out / auth-required / unavailable all keep the stored
        // data — and, because `seenAccounts` only records an id after its
        // import ran, an expired profile never blocks a working sibling
        // on the same account.
        guard reply.status == "ok" || reply.status == "incomplete" else { return 0 }
        if reply.status == "incomplete" {
            Self.log.warning(
                "cursor feed: part of the window hit the page cap; ingesting what arrived")
        }
        // The key is the account when swe reports one, the profile's home
        // when it cannot. Two profiles on one account therefore write the
        // same keys, and `accountHome` stays with the first-seen profile.
        let accountKey = reply.accountId ?? account.home
        // An instant that isn't finite, or lies past tomorrow, is garbage
        // from the feed: its millisecond key would overflow Int64 (a trap),
        // and as the `through` mark it would push every later window, and
        // `--since`, past all real events.
        let latest = Date().timeIntervalSince1970 + 86_400
        let events = reply.events.filter {
            $0.timestamp.isFinite && $0.timestamp >= 0 && $0.timestamp <= latest
        }
        guard generation == requestGeneration else { return 0 }
        let changed = try await ledger.ingest(events.map {
            Self.record(for: $0, accountKey: accountKey, accountHome: account.home)
        })
        if let accountId = reply.accountId {
            seenAccounts.insert(accountId)
            progress.profiles[account.home] = accountId
            // `through` advances only on `ok`: `incomplete` means part of
            // the window may be missing, so the next poll asks for the
            // same range again (re-ingest is idempotent).
            if reply.status == "ok",
               let newest = events.map(\.timestamp).max() {
                progress.accounts[accountId, default: .init()].through = max(
                    progress.accounts[accountId]?.through ?? 0, newest)
            }
            dirty = true
        }
        return changed
    }

    private func saveProgress() {
        guard let data = try? JSONEncoder().encode(progress) else { return }
        try? FileManager.default.createDirectory(
            at: progressFile.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        try? data.write(to: progressFile, options: .atomic)
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
    /// The key names the account, event instant, model and conversation —
    /// the event's identity — and never its counts: Cursor reports a
    /// correction as new counts on the same event, and a key that embedded
    /// them stored it as a second event instead of replacing the row. An
    /// overlapping refetch — or a second profile on the same account —
    /// writes the same rows again rather than adding new ones, and the
    /// store's lookalike cleanup removes rows the old key scheme left.
    static func record(for event: CursorUsageEvent, accountKey: String,
                       accountHome: String) -> LedgerRecord {
        let harness = ModelBrands.forModel(event.model).id == "grok-bot" ? "grok-bot" : "cursor"
        let milliseconds = Int64((event.timestamp * 1000).rounded())
        let session = event.conversationId ?? "cursor-" + dayLabel(for: event.timestamp)
        let key = "cursor-feed:\(accountKey):\(milliseconds):\(event.model):" +
            "\(event.conversationId ?? "")"
        return LedgerRecord(
            key: key, messageId: nil, authoritative: true,
            // A real account_id in the key means a sibling profile's stale
            // row is the same event; keyed by profile home instead, each
            // profile's rows belong to it alone.
            accountSpansProfiles: accountKey != accountHome,
            event: UsageEvent(
                harness: harness, accountHome: accountHome, sessionId: session,
                model: event.model,
                timestamp: Date(timeIntervalSince1970: event.timestamp),
                inputTokens: event.inputTokens, outputTokens: event.outputTokens,
                cacheReadTokens: event.cacheReadTokens, cacheWriteTokens: event.cacheWriteTokens,
                costUSD: event.costUsd))
    }

    /// Whether `session` is a `dayLabel` bucket rather than a conversation id.
    /// The prefix test first: the Sessions page asks this of every Cursor
    /// usage row on each join, and a conversation id never gets as far as
    /// the regex.
    static func isDayBucket(_ session: String) -> Bool {
        session.hasPrefix("cursor-") && session.wholeMatch(of: /cursor-\d{4}-\d{2}-\d{2}/) != nil
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

/// `cache/cursor-feed.json`: each profile's last-seen `account_id`, and
/// each account's newest imported timestamp in epoch seconds.
struct CursorFeedProgress: Codable, Sendable {
    var schema = 1
    var profiles: [String: String] = [:]
    var accounts: [String: Through] = [:]
    struct Through: Codable, Sendable {
        var through: TimeInterval = 0
    }
}

public struct CursorUsageEventsReply: Decodable, Sendable {
    public var status: String
    /// The Cursor user id the token claims (`sub`), nil when swe had no
    /// token to read it from. Not a credential.
    public var accountId: String?
    public var events: [CursorUsageEvent]
}
