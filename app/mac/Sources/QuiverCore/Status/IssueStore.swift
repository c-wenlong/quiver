import Foundation
import Observation

/// Why a usage read failed, in swe's `remedies` vocabulary — each one a
/// different fix where the old fetchFailed collapse had none.
public enum IssueStatus: String, Codable, Sendable, CaseIterable {
    case ok
    case signedOut = "signed-out"
    case authRequired = "auth-required"
    case permissionRequired = "permission-required"
    case unavailable
    case timeout

    /// Whether the user can do something about it: outages wait or need a
    /// refresh, the rest want a click — only they toast and badged.
    public var actionable: Bool {
        switch self {
        case .signedOut, .authRequired, .permissionRequired: true
        case .ok, .unavailable, .timeout: false
        }
    }
}

/// The fix swe names for a failure, verbatim from its REMEDIES table:
/// `kind` is `run-command`, `open-app`, `relogin`, `grant-permission` or
/// `retry`; `command`, when set, is what the row offers to copy or run.
public struct UsageRemedy: Codable, Sendable, Equatable {
    public let kind: String
    public let title: String
    public let summary: String
    public let steps: [String]
    public let command: String?

    public init(kind: String, title: String, summary: String,
                steps: [String], command: String? = nil) {
        self.kind = kind
        self.title = title
        self.summary = summary
        self.steps = steps
        self.command = command
    }
}

/// One open issue: a usage read that needs the user, kept so repeated
/// polls deduplicate and a restart remembers it was already told.
public struct Issue: Codable, Sendable, Equatable, Identifiable {
    /// `harness/status` or `harness/account/status` — stable across
    /// polls, so the same failure never becomes a new issue.
    public let id: String
    public var harness: String
    /// The extra account's label, when the issue is about one.
    public var account: String?
    /// The account's stable id the issue id embeds (`cursor/work/…`);
    /// nil for the harness's own row. Kept beside the label so slot
    /// matching never parses display text.
    public var accountKey: String?
    public var status: IssueStatus
    public var remedy: UsageRemedy?
    public var firstSeen: Date
    public var lastSeen: Date
    /// When a toast last went up for this failure; nil means never.
    public var lastNotified: Date?
    public var occurrences: Int
    public var resolved: Bool

    public init(id: String, harness: String, account: String? = nil,
                accountKey: String? = nil,
                status: IssueStatus, remedy: UsageRemedy? = nil,
                at: Date) {
        self.id = id
        self.harness = harness
        self.account = account
        self.accountKey = accountKey
        self.status = status
        self.remedy = remedy
        firstSeen = at
        lastSeen = at
        lastNotified = nil
        occurrences = 1
        resolved = false
    }

    /// The summary line a toast or card shows: the remedy's when swe
    /// named a fix, else a plain reading of the status. An extra
    /// account's label tags along so two accounts' identical failures
    /// still read differently.
    public var message: String {
        var base: String
        if let text = remedy?.summary, !text.isEmpty {
            base = text
        } else {
            base = switch status {
            case .signedOut: "\(harness) isn't signed in"
            case .authRequired: "\(harness)'s stored login is stale"
            case .permissionRequired: "macOS won't let Quiver read \(harness)'s credentials"
            case .unavailable: "\(harness)'s usage couldn't be fetched"
            case .timeout: "\(harness)'s usage fetch timed out"
            case .ok: "\(harness) is fine"
            }
        }
        if let account, !account.isEmpty { return "\(base) (\(account))" }
        return base
    }
}

/// The app's durable issue list at `~/.quiver/data/issues.json` — the same
/// file `swe doctor` reads, so the CLI's Issues section and the app's
/// cards describe one truth. Read-modify-write under a flock, atomic
/// rename on save, like `UsageCacheStore`.
public struct IssueStore: Sendable {
    public let url: URL
    /// The caller's clock; tests move it by hand.
    public var now: @Sendable () -> Date = Date.init

    public init(url: URL) {
        self.url = url
    }

    /// Under the app's own `~/.quiver`, not cache: an issue is state, not
    /// a snapshot a cache clear should lose.
    public static func file(home: URL) -> URL {
        home.appending(path: ".quiver/data/issues.json")
    }

    private struct File: Codable {
        var version = 1
        var issues: [String: Issue]
    }

    public static func id(harness: String, status: IssueStatus,
                          account: String? = nil) -> String {
        account.map { "\(harness)/\($0)/\(status.rawValue)" }
            ?? "\(harness)/\(status.rawValue)"
    }

    /// Every stored issue, resolved ones included, newest first.
    public func all() -> [Issue] {
        load().values.sorted { $0.firstSeen > $1.firstSeen }
    }

    /// The issues still open, for the badge and Doctor.
    public func open() -> [Issue] {
        all().filter { !$0.resolved }
    }

    /// Whether a record should raise the user: the failure is new, came
    /// back after resolving, or a different failure took the slot.
    public struct Outcome: Sendable, Equatable {
        public let issue: Issue
        /// True when this state has never been told — `markNotified` sets
        /// the marker that suppresses the repeat.
        public let shouldNotify: Bool
    }

    /// Record a failed read. Returns the issue as stored and whether it
    /// warrants a toast: a brand-new failure, a recurrence after it
    /// resolved, or a status change (its previous issue auto-resolves so
    /// the store holds one open issue per harness+account).
    @discardableResult
    public func record(harness: String, status: IssueStatus,
                       remedy: UsageRemedy?, account: String? = nil,
                       accountKey: String? = nil) -> Outcome {
        var notify: Outcome!
        withFileLock {
            var issues = load()
            let key = accountKey ?? account
            let id = Self.id(harness: harness, status: status, account: key)
            let now = now()
            // One open issue per slot: whatever else is open for this
            // harness+account closes, whether this verdict reopens an
            // older issue in the slot or opens a new one.
            for (other, issue) in issues where !issue.resolved
                && issue.harness == harness && issue.accountKey == key
                && other != id {
                var closed = issue
                closed.resolved = true
                issues[other] = closed
            }
            if var existing = issues[id] {
                if !existing.resolved {
                    // Same failure still failing: count it, refresh its
                    // fix, never re-announce it.
                    existing.lastSeen = now
                    existing.occurrences += 1
                    existing.remedy = remedy ?? existing.remedy
                    issues[id] = existing
                    notify = Outcome(issue: existing, shouldNotify: false)
                } else {
                    // It cleared and came back: a new episode, told again.
                    existing.resolved = false
                    existing.lastSeen = now
                    existing.lastNotified = nil
                    existing.occurrences += 1
                    existing.remedy = remedy ?? existing.remedy
                    issues[id] = existing
                    notify = Outcome(issue: existing, shouldNotify: true)
                }
            } else {
                let issue = Issue(id: id, harness: harness, account: account,
                                  accountKey: key, status: status,
                                  remedy: remedy, at: now)
                issues[id] = issue
                notify = Outcome(issue: issue, shouldNotify: true)
            }
            write(issues)
        }
        return notify
    }

    /// An account that no longer exists can't fail again: its open
    /// issues close. `live` maps each harness to the account keys still
    /// registered for it; an issue slot absent there is gone, not fixed.
    public func resolveMissing(live: [String: Set<String>]) {
        withFileLock {
            var issues = load()
            var changed = false
            for (id, issue) in issues where !issue.resolved
                && issue.accountKey != nil
                && !(live[issue.harness]?.contains(issue.accountKey!) ?? false) {
                var closed = issue
                closed.resolved = true
                issues[id] = closed
                changed = true
            }
            if changed { write(issues) }
        }
    }

    /// A successful read closes that harness's (or account's) open issues.
    public func resolve(harness: String, accountKey: String? = nil) {
        withFileLock {
            var issues = load()
            var changed = false
            for (id, issue) in issues where !issue.resolved
                && issue.harness == harness && issue.accountKey == accountKey {
                var closed = issue
                closed.resolved = true
                issues[id] = closed
                changed = true
            }
            if changed { write(issues) }
        }
    }

    /// The toast went up; a restart does not raise this episode again.
    public func markNotified(_ id: String) {
        withFileLock {
            var issues = load()
            guard var issue = issues[id], issue.lastNotified == nil else { return }
            issue.lastNotified = now()
            issues[id] = issue
            write(issues)
        }
    }

    private func load() -> [String: Issue] {
        guard let data = try? Data(contentsOf: url) else { return [:] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return (try? decoder.decode(File.self, from: data))?.issues ?? [:]
    }

    private func write(_ issues: [String: Issue]) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(File(issues: issues)) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        guard (try? data.write(to: url, options: .atomic)) != nil else { return }
        try? manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    /// Read, change and write under one lock shared with other running
    /// copies, like UsageCacheStore: an atomic rename alone would let a
    /// second app write back the file it read before this save.
    private func withFileLock(_ body: () -> Void) {
        let lockURL = url.appendingPathExtension("lock")
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        let fd = Darwin.open(lockURL.path, O_RDWR | O_CREAT, 0o600)
        guard fd >= 0 else { return body() }
        defer { close(fd) }
        guard flock(fd, LOCK_EX) == 0 else { return body() }
        defer { flock(fd, LOCK_UN) }
        body()
    }
}

/// The live face of `IssueStore`: polls record outcomes into it, the
/// window's toast and the Settings badge read it, and Doctor's rows come
/// from it between `swe doctor` runs.
@MainActor
@Observable
public final class IssuesModel {
    /// Open issues, newest first; resolved ones drop off the surfaces.
    public private(set) var open: [Issue] = []
    /// The issue the window's toast is showing; nil while nothing new
    /// needs telling.
    public var toast: Issue? { queue.first }
    /// Actionable issues not yet shown: one poll can find several, and
    /// each waits for the one on screen to dismiss before its turn.
    private var queue: [Issue] = []
    private let store: IssueStore

    public init(store: IssueStore) {
        self.store = store
        open = store.open()
    }

    /// Issues the user can act on: what the Settings badge counts.
    public var actionableCount: Int {
        open.filter { $0.status.actionable }.count
    }

    /// Record one poll outcome: a failure (its issue may toast) or a
    /// success (its open issues resolve).
    public func record(harness: String, status: IssueStatus,
                       remedy: UsageRemedy?, account: String? = nil,
                       accountKey: String? = nil) {
        if status == .ok {
            store.resolve(harness: harness, accountKey: accountKey)
        } else {
            let outcome = store.record(harness: harness, status: status,
                                       remedy: remedy, account: account,
                                       accountKey: accountKey)
            if outcome.shouldNotify, outcome.issue.status.actionable {
                queue.append(outcome.issue)
                store.markNotified(outcome.issue.id)
            }
        }
        open = store.open()
    }

    /// Accounts that were removed can't fail again: their open issues
    /// close. `live` maps each harness to its remaining account keys.
    public func dropMissingAccounts(live: [String: Set<String>]) {
        store.resolveMissing(live: live)
        open = store.open()
    }

    public func dismissToast() {
        if !queue.isEmpty { queue.removeFirst() }
    }

    /// The doctor findings these issues would make: the same check id,
    /// severity and fix text `swe doctor` reports, so the section shows
    /// them the moment a poll records them rather than after a re-run.
    public var findings: [DoctorReport.Finding] {
        open.map { issue in
            DoctorReport.Finding(
                check: "usage.\(issue.id)",
                severity: issue.status.actionable ? .warning : .info,
                message: issue.message,
                fix: issue.remedy?.command ?? issue.remedy?.steps.first,
                harness: issue.harness)
        }
    }
}
