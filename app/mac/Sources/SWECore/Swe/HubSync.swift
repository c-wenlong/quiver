import Foundation

/// One changed path in the hub repo, from `swe sync status --json` or
/// the commit `swe sync --json` made.
public struct HubSyncChange: Decodable, Sendable, Equatable {
    /// git's one-letter status: A, M, D.
    public let status: String
    public let path: String
    /// The hub primitive: skills, plugins, hooks, mcp, AGENTS.md,
    /// mcp.json or config.
    public let group: String

    public init(status: String, path: String, group: String) {
        self.status = status
        self.path = path
        self.group = group
    }
}

/// `swe sync status --json`: the `~/.swe` repo's branch mode, how far it
/// is from the remote, and what a sync would commit. A hub sync can't
/// work with carries only `path`, `ready: false` and `problem`.
public struct HubSyncStatus: Decodable, Sendable, Equatable {
    public let path: String
    public let ready: Bool
    public let problem: String?
    /// "device" on a `device/<machine>` branch, else "single".
    public let mode: String?
    public let branch: String?
    public let machine: String?
    /// The remote URL, credentials stripped.
    public let remote: String?
    /// `origin/<branch>`, nil until the branch is on the remote.
    public let upstream: String?
    /// `origin/main` in device mode, else nil.
    public let base: String?
    public let ahead: Int?
    public let behind: Int?
    public let baseBehind: Int?
    public let changes: [HubSyncChange]?
    public let groups: [String: Int]?
    /// Changed paths that never sync (secrets/, cache/, data/ ...) which
    /// an older hub .gitignore lets through.
    public let heldBack: [String]?
    public let fetched: Bool?
    public let fetchError: String?

    public init(path: String, ready: Bool, problem: String? = nil, mode: String? = nil,
                branch: String? = nil, machine: String? = nil, remote: String? = nil,
                upstream: String? = nil, base: String? = nil, ahead: Int? = nil,
                behind: Int? = nil, baseBehind: Int? = nil, changes: [HubSyncChange]? = nil,
                groups: [String: Int]? = nil, heldBack: [String]? = nil,
                fetched: Bool? = nil, fetchError: String? = nil) {
        self.path = path
        self.ready = ready
        self.problem = problem
        self.mode = mode
        self.branch = branch
        self.machine = machine
        self.remote = remote
        self.upstream = upstream
        self.base = base
        self.ahead = ahead
        self.behind = behind
        self.baseBehind = baseBehind
        self.changes = changes
        self.groups = groups
        self.heldBack = heldBack
        self.fetched = fetched
        self.fetchError = fetchError
    }

    /// The card's lines, worded as the web card's `hubSyncLines` words
    /// them (`static/pages/settings.js`).
    public var lines: (repo: String, changes: String, warning: String?) {
        guard let mode, let branch else {
            return (problem ?? "~/.swe isn't a git repository.", "", problem)
        }
        let repo = mode == "device"
            ? "Device branch \(branch): merges \(base ?? "origin/main") in, pushes only \(branch)."
            : "Branch \(branch), shared by every machine."
        let changes = changes ?? []
        let grouped = (groups ?? [:]).sorted { $0.key < $1.key }
            .map { "\($0.key) \($0.value)" }.joined(separator: ", ")
        let changeLine = changes.isEmpty
            ? "Nothing to commit."
            : "\(Self.count(changes.count, "uncommitted file")): \(grouped)."
        var remote: [String] = []
        if upstream == nil {
            remote.append("\(branch) isn't on the remote yet")
        } else {
            if let ahead, ahead > 0 { remote.append("\(Self.count(ahead, "commit")) to push") }
            if let behind, behind > 0 { remote.append("\(Self.count(behind, "commit")) to pull") }
        }
        if let baseBehind, baseBehind > 0, let base {
            remote.append("\(Self.count(baseBehind, "new commit")) on \(base)")
        }
        let syncLine = !remote.isEmpty
            ? remote.joined(separator: " · ") + "."
            : fetched == true ? "In step with the remote." : "In step with the remote as of the last fetch."
        let held = heldBack ?? []
        let heldLine = held.isEmpty ? nil
            : "\(Self.count(held.count, "changed file")) under paths that never sync stay out: the hub's .gitignore is older than swe's."
        return (repo, "\(changeLine) \(syncLine)", problem ?? fetchError ?? heldLine)
    }

    static func count(_ n: Int, _ word: String) -> String {
        "\(n) \(word)\(n == 1 ? "" : "s")"
    }
}

/// `swe sync --json`: what one sync did, or why it stopped.
public struct HubSyncResult: Decodable, Sendable, Equatable {
    /// synced, up-to-date, would-sync, conflict or push-failed.
    public let result: String
    public let dryRun: Bool
    public let mode: String
    public let branch: String
    public let commit: Commit?
    public let merges: [Merge]
    public let conflicts: [String]
    public let heldBack: [String]
    public let pushed: Bool
    public let pushError: String?
    public let relinked: Relinked?

    public struct Commit: Decodable, Sendable, Equatable {
        public let message: String
        public let files: [HubSyncChange]
    }

    public struct Merge: Decodable, Sendable, Equatable {
        public let ref: String
        public let commits: Int
    }

    public struct Relinked: Decodable, Sendable, Equatable {
        /// Incoming files the merge brought.
        public let changed: Int
        public let notes: [String]
    }

    /// One line for the card, and whether it is a failure. Worded as the
    /// web card's `hubSyncOutcome`.
    public var outcome: (text: String, failed: Bool) {
        switch result {
        case "conflict":
            return ("Conflict in \(conflicts.joined(separator: ", ")). Nothing changed; resolve it in ~/.swe, then sync again.", true)
        case "push-failed":
            return ("Committed and merged, but the push failed: \(pushError ?? "git push failed"). Sync again to retry.", true)
        case "up-to-date":
            return ("Already in sync.", false)
        default:
            var done: [String] = []
            if let commit { done.append("committed \(HubSyncStatus.count(commit.files.count, "file"))") }
            let merged = merges.reduce(0) { $0 + $1.commits }
            if merged > 0 { done.append("merged \(HubSyncStatus.count(merged, "commit"))") }
            if pushed { done.append("pushed \(branch)") }
            if relinked != nil { done.append("re-linked every harness") }
            let text = done.joined(separator: ", ")
            let line = text.isEmpty ? "Synced." : text.prefix(1).uppercased() + text.dropFirst() + "."
            // Relink problems don't fail the sync, but they need the person.
            let notes = relinked?.notes ?? []
            return (notes.isEmpty ? line : line + " " + notes.joined(separator: " "), !notes.isEmpty)
        }
    }
}

extension SweClient {
    /// The hub repo's status. `fetch` asks the remote first, which can
    /// take a few seconds.
    public func hubSyncStatus(fetch: Bool = false) async throws -> HubSyncStatus {
        try await call(fetch ? ["sync", "status", "--fetch", "--json"] : ["sync", "status", "--json"],
                       timeout: .seconds(240))
    }

    /// Runs `swe sync`. A conflict or a refused push exits 1 with the
    /// document still printed, so the run may fail and the result inside
    /// says why; a repo it can't sync at all throws with swe's reason.
    public func hubSync() async throws -> HubSyncResult {
        try await call(["sync", "--json"], allowFailure: true, timeout: .seconds(600))
    }
}
