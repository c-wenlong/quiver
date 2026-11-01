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

    /// A login or permission the user has to fix by hand: it always
    /// toasts, counts on the Settings badge and puts the card in its
    /// "Action needed" state. Nothing else does.
    public var needsAction: Bool {
        switch self {
        case .authRequired, .permissionRequired: true
        case .ok, .signedOut, .unavailable, .timeout: false
        }
    }

    /// A failed fetch with nothing wrong on the user's side: card only,
    /// and a Doctor row once it persists (`IssuesModel.persistentPolls`).
    public var isOutage: Bool {
        self == .unavailable || self == .timeout
    }
}

/// The fix swe names for a failure, verbatim from its REMEDIES table:
/// `kind` is `run-command`, `open-app`, `relogin`, `grant-permission` or
/// `retry`; `command`, when set, is what the row offers to copy or run,
/// and `url` a link to open (the privacy pane for `grant-permission`).
public struct UsageRemedy: Codable, Sendable, Equatable {
    public let kind: String
    public let title: String
    public let summary: String
    public let steps: [String]
    public let command: String?
    public let url: String?

    public init(kind: String, title: String, summary: String,
                steps: [String], command: String? = nil, url: String? = nil) {
        self.kind = kind
        self.title = title
        self.summary = summary
        self.steps = steps
        self.command = command
        self.url = url
    }

    /// What the Fix sheet offers, in order: the command with Run in
    /// Terminal and Copy, then the link with an Open button named for
    /// where it goes. A link that isn't a URL is left out.
    public var actions: [RemedyAction] {
        var actions: [RemedyAction] = []
        if let command, !command.isEmpty { actions.append(.command(command)) }
        if let url, let link = URL(string: url), link.scheme != nil {
            let label = link.scheme == "x-apple.systempreferences"
                ? "Open System Settings" : "Open Link"
            actions.append(.open(link, label: label))
        }
        return actions
    }
}

/// One button row in the Fix sheet.
public enum RemedyAction: Sendable, Equatable {
    /// A line to run in a terminal or copy.
    case command(String)
    /// A link to open, with the button's label.
    case open(URL, label: String)
}

extension HarnessIssue {
    /// The card's "Action needed" line, amber beside the Fix button: the
    /// remedy's summary, else a plain reading of the status. Nil for a
    /// status the card shows its own way (signed out, an outage).
    public var actionNeededLine: String? {
        guard status.needsAction else { return nil }
        if let summary = remedy?.summary, !summary.isEmpty { return summary }
        return status == .authRequired
            ? "Its stored login is stale"
            : "macOS won't let SWE read its credentials"
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
    /// When this episode began: a recurrence after it resolved starts a
    /// new one.
    public var firstSeen: Date
    public var lastSeen: Date
    /// When a toast last went up for this failure; nil means never.
    public var lastNotified: Date?
    /// Polls in a row that found this failure, this episode.
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
            case .permissionRequired: "macOS won't let SWE read \(harness)'s credentials"
            case .unavailable: "\(harness)'s usage couldn't be fetched"
            case .timeout: "\(harness)'s usage fetch timed out"
            case .ok: "\(harness) is fine"
            }
        }
        if let account, !account.isEmpty { return "\(base) (\(account))" }
        return base
    }
}

/// The app's durable issue list at `~/.swe/data/issues.json` — the same
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

    /// Under the app's own `~/.swe`, not cache: an issue is state, not
    /// a snapshot a cache clear should lose.
    public static func file(home: URL) -> URL {
        home.appending(path: ".swe/data/issues.json")
    }

    private struct File: Codable {
        /// The newest layout this build writes; a higher one is kept.
        static let version = 1
        var version = File.version
        var issues: [String: Issue]
        /// Each slot (`harness` or `harness/account`) that has had a
        /// successful read, and when it first did: a sign-out there is a
        /// regression worth a toast, one that was never signed in is not.
        /// Absent in files from before it; `swe doctor` ignores it.
        var healthy: [String: Date]?
    }

    public static func id(harness: String, status: IssueStatus,
                          account: String? = nil) -> String {
        account.map { "\(harness)/\($0)/\(status.rawValue)" }
            ?? "\(harness)/\(status.rawValue)"
    }

    /// The harness's own row, or one extra account of it.
    static func slot(harness: String, accountKey: String?) -> String {
        accountKey.map { "\(harness)/\($0)" } ?? harness
    }

    /// Every stored issue, resolved ones included, newest first.
    public func all() -> [Issue] {
        (load() ?? File(issues: [:])).issues.values.sorted { $0.firstSeen > $1.firstSeen }
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
        /// Whether the slot had a successful read before: a sign-out
        /// there is a regression, not an account that was never set up.
        public var wasHealthy: Bool = false
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
            guard var file = load() else {
                // A file this build can't read is kept, not replaced, so
                // nothing is remembered: a toast now would repeat every
                // poll, so the issue stays quiet.
                notify = Outcome(issue: Issue(
                    id: Self.id(harness: harness, status: status, account: accountKey ?? account),
                    harness: harness, account: account, accountKey: accountKey ?? account,
                    status: status, remedy: remedy, at: now()), shouldNotify: false)
                return
            }
            var issues = file.issues
            let key = accountKey ?? account
            let id = Self.id(harness: harness, status: status, account: key)
            let now = now()
            // One open issue per slot: whatever else is open for this
            // harness+account closes, whether this verdict reopens an
            // older issue in the slot or opens a new one.
            var outagePolls = 0
            for (other, issue) in issues where !issue.resolved
                && issue.harness == harness && issue.accountKey == key
                && other != id {
                var closed = issue
                closed.resolved = true
                issues[other] = closed
                // An outage that flips between timeout and unavailable is
                // one outage: its poll count carries over, so Doctor's
                // "persistent" threshold still counts every failed poll.
                if issue.status.isOutage && status.isOutage {
                    outagePolls = max(outagePolls, issue.occurrences)
                }
            }
            if var existing = issues[id] {
                if !existing.resolved {
                    // Same failure still failing: count it, refresh its
                    // fix, never re-announce it.
                    existing.lastSeen = now
                    existing.occurrences += 1
                    // A renamed account keeps its slot; its label follows.
                    existing.account = account ?? existing.account
                    existing.remedy = remedy ?? existing.remedy
                    issues[id] = existing
                    notify = Outcome(issue: existing, shouldNotify: false)
                } else {
                    // It cleared and came back: a new episode, counted
                    // from one and told again.
                    existing.resolved = false
                    existing.firstSeen = now
                    existing.lastSeen = now
                    existing.lastNotified = nil
                    existing.occurrences = 1
                    existing.account = account ?? existing.account
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
            if outagePolls > 0, var carried = issues[id] {
                carried.occurrences += outagePolls
                issues[id] = carried
                notify = Outcome(issue: carried, shouldNotify: notify.shouldNotify)
            }
            notify.wasHealthy = file.healthy?[Self.slot(harness: harness, accountKey: key)] != nil
            file.issues = issues
            write(file)
        }
        return notify
    }

    /// A slot that is no longer polled can't fail again, or recover:
    /// its open issues close, gone rather than fixed. That is a harness
    /// that was unstarred (absent from `starred`, when given) and an extra
    /// account that was removed (absent from `accounts[harness]`).
    public func resolveMissing(starred: Set<String>? = nil,
                               accounts: [String: Set<String>]) {
        withFileLock {
            guard var file = load() else { return }
            var changed = false
            for (id, issue) in file.issues where !issue.resolved {
                let unstarred = starred.map { !$0.contains(issue.harness) } ?? false
                let removed = issue.accountKey.map {
                    !(accounts[issue.harness]?.contains($0) ?? false)
                } ?? false
                guard unstarred || removed else { continue }
                var closed = issue
                closed.resolved = true
                file.issues[id] = closed
                changed = true
            }
            if changed { write(file) }
        }
    }

    /// A successful read closes that harness's (or account's) open issues
    /// and marks the slot healthy, so a later sign-out reads as a
    /// regression.
    public func resolve(harness: String, accountKey: String? = nil) {
        withFileLock {
            guard var file = load() else { return }
            var changed = false
            for (id, issue) in file.issues where !issue.resolved
                && issue.harness == harness && issue.accountKey == accountKey {
                var closed = issue
                closed.resolved = true
                file.issues[id] = closed
                changed = true
            }
            let slot = Self.slot(harness: harness, accountKey: accountKey)
            if file.healthy?[slot] == nil {
                file.healthy = (file.healthy ?? [:]).merging([slot: now()]) { old, _ in old }
                changed = true
            }
            if changed { write(file) }
        }
    }

    /// The toast went up; a restart does not raise this episode again.
    public func markNotified(_ id: String) {
        withFileLock {
            guard var file = load(), var issue = file.issues[id],
                  issue.lastNotified == nil else { return }
            issue.lastNotified = now()
            file.issues[id] = issue
            write(file)
        }
    }

    /// The stored file; empty when there is none. Nil when it is there
    /// but this build can't read it (corrupt, a newer `version`, a status
    /// it doesn't know, unreadable): callers that write then leave it
    /// alone, since writing back an empty list would wipe every issue.
    private func load() -> File? {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile
                    || error.code == .fileNoSuchFile {
            return File(issues: [:])
        } catch {
            return nil
        }
        if data.allSatisfy({ [0x20, 0x09, 0x0A, 0x0D].contains($0) }) { return File(issues: [:]) }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        guard let file = try? decoder.decode(File.self, from: data),
              file.version <= File.version else { return nil }
        return file
    }

    private func write(_ file: File) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(file) else { return }
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
        FileLock.whileHeld(beside: url, body)
    }
}

/// The live face of `IssueStore`: polls record outcomes into it, the
/// window's toast and the Settings badge read it, and Doctor's rows come
/// from it between `swe doctor` runs.
///
/// Every store call (a flock and a file read or write) runs on one serial
/// queue off the main actor, in the order the calls were made; a result
/// from an older call never overwrites a newer one's `open`.
@MainActor
@Observable
public final class IssuesModel {
    /// How many polls in a row an outage must fail before Doctor lists
    /// it. swe doctor's `remedies.PERSISTENT_POLLS` is the same number.
    public static let persistentPolls = 3

    /// One poll's verdict for a harness or one of its extra accounts.
    public struct Poll: Sendable, Equatable {
        public var harness: String
        public var status: IssueStatus
        public var remedy: UsageRemedy?
        public var account: String?
        public var accountKey: String?

        public init(harness: String, status: IssueStatus, remedy: UsageRemedy? = nil,
                    account: String? = nil, accountKey: String? = nil) {
            self.harness = harness
            self.status = status
            self.remedy = remedy
            self.account = account
            self.accountKey = accountKey
        }
    }

    /// Open issues, newest first; resolved ones drop off the surfaces.
    public private(set) var open: [Issue] = []
    /// The issue the window's toast is showing; nil while nothing new
    /// needs telling.
    public var toast: Issue? { queue.first }
    /// Issues to toast not yet shown: one poll can find several, and
    /// each waits for the one on screen to dismiss before its turn.
    private var queue: [Issue] = []
    private let store: IssueStore
    @ObservationIgnored private var issued = 0
    @ObservationIgnored private var applied = 0
    private nonisolated static let storeQueue = DispatchQueue(label: "app.swe.issues")

    public init(store: IssueStore) {
        self.store = store
        Task { [weak self] in await self?.reload() }
    }

    /// Issues only the user can fix: what the Settings badge counts.
    public var badgeCount: Int {
        open.filter { $0.status.needsAction }.count
    }

    /// Whether a recorded failure raises a toast: a new episode of a
    /// login or permission problem, or a sign-out where a read used to
    /// work. A never-signed-in account and outages stay quiet.
    nonisolated static func toasts(_ outcome: IssueStore.Outcome) -> Bool {
        guard outcome.shouldNotify else { return false }
        let status = outcome.issue.status
        return status.needsAction || (status == .signedOut && outcome.wasHealthy)
    }

    /// Re-read the store, for launch and tests.
    public func reload() async {
        let store = store
        let (ticket, open) = await onStore { store.open() }
        publish(open, ticket: ticket)
    }

    /// Record one poll outcome: a failure (its issue may toast) or a
    /// success (its open issues resolve).
    public func record(harness: String, status: IssueStatus,
                       remedy: UsageRemedy?, account: String? = nil,
                       accountKey: String? = nil) async {
        await record([Poll(harness: harness, status: status, remedy: remedy,
                           account: account, accountKey: accountKey)])
    }

    /// Record a whole poll's verdicts in one pass. `starred` and
    /// `accounts`, when given, are what is still polled: issues of an
    /// unstarred harness or a removed account close, since they can
    /// never fail again or recover.
    public func record(_ polls: [Poll], starred: Set<String>? = nil,
                       accounts: [String: Set<String>]? = nil) async {
        let store = store
        let (ticket, result) = await onStore { () -> ([Issue], [Issue]) in
            if starred != nil || accounts != nil {
                store.resolveMissing(starred: starred, accounts: accounts ?? [:])
            }
            var told: [Issue] = []
            for poll in polls {
                if poll.status == .ok {
                    store.resolve(harness: poll.harness, accountKey: poll.accountKey)
                    continue
                }
                let outcome = store.record(harness: poll.harness, status: poll.status,
                                           remedy: poll.remedy, account: poll.account,
                                           accountKey: poll.accountKey)
                if Self.toasts(outcome) {
                    told.append(outcome.issue)
                    store.markNotified(outcome.issue.id)
                }
            }
            return (told, store.open())
        }
        queue += result.0
        publish(result.1, ticket: ticket)
    }

    public func dismissToast() {
        if !queue.isEmpty { queue.removeFirst() }
    }

    /// The doctor findings these issues make: the same check id, severity
    /// and fix text `swe doctor` reports, so the section shows them the
    /// moment a poll records them rather than after a re-run. An outage
    /// joins only once it has lasted `persistentPolls` polls.
    public var findings: [DoctorReport.Finding] {
        open.compactMap { issue in
            if issue.status.isOutage, issue.occurrences < Self.persistentPolls { return nil }
            return DoctorReport.Finding(
                check: "usage.\(issue.id)",
                severity: issue.status.isOutage ? .info : .warning,
                message: issue.message,
                fix: issue.remedy?.command ?? issue.remedy?.url ?? issue.remedy?.steps.first,
                harness: issue.harness)
        }
    }

    private func publish(_ issues: [Issue], ticket: Int) {
        guard ticket > applied else { return }
        applied = ticket
        open = issues
    }

    /// Runs `work` on the store's serial queue. The ticket is taken and
    /// the work queued in one main-actor step, so tickets follow queue
    /// order and the newest ticket holds the newest state.
    private func onStore<Value: Sendable>(
        _ work: @escaping @Sendable () -> Value
    ) async -> (Int, Value) {
        issued += 1
        let ticket = issued
        let value = await withCheckedContinuation { (done: CheckedContinuation<Value, Never>) in
            Self.storeQueue.async { done.resume(returning: work()) }
        }
        return (ticket, value)
    }
}
