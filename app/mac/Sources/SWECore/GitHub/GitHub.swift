import Foundation

/// `swe gh … --json` replies, decoded per docs/json-contracts.md.
///
/// All GitHub state comes from the ambient `gh` login; swe stores no
/// tokens and the app reads none. Rows keyed by repo are per-repo
/// `results` entries — every entry carries `status` so a repo that
/// failed upstream still shows as its own row state rather than
/// vanishing or looking empty.

/// Whether `gh` can be used at all, from the probe swe runs first.
public struct GhEnv: Decodable, Equatable, Sendable {
    public enum Status: String, Decodable, Sendable {
        case ok
        /// `gh` is not installed.
        case unavailable
        /// `gh` exists but `gh auth status` says nobody is logged in.
        case authRequired = "auth-required"
        /// The `gh auth status` probe itself failed (timeout, spawn
        /// error) — not proof the login is gone.
        case upstreamError = "upstream-error"
    }
    public let status: Status
    public let login: String?
    /// The probe failure detail when status is `upstream-error`.
    public let error: String?
}

/// One repo's answer inside a `swe gh <surface>` reply. `status` is `ok`
/// when the row payload is real; anything else means GitHub or `gh`
/// failed and `error` says why — `ok` with an empty list is the only
/// "repo has none" signal.
public enum GhEntryStatus: String, Decodable, Sendable {
    case ok, unavailable
    case notFound = "not-found"
    case authRequired = "auth-required"
    case upstreamError = "upstream-error"
}

/// A timestamp from `swe gh`: ISO 8601 for resource times, epoch seconds
/// for local pin times. Decodes either into `date` (nil when the text
/// is not a date we understand).
public struct GhTime: Codable, Equatable, Sendable {
    public var date: Date?

    public init(_ date: Date?) { self.date = date }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.singleValueContainer()
        if let text = try? box.decode(String.self) {
            date = Timestamp.parse(text)
        } else if let epoch = try? box.decode(Double.self) {
            date = Date(timeIntervalSince1970: epoch)
        } else {
            date = nil
        }
    }

    /// Epoch seconds — the decode path already accepts it.
    public func encode(to encoder: any Encoder) throws {
        var box = encoder.singleValueContainer()
        try box.encode(date?.timeIntervalSince1970)
    }
}

/// A repo the user pinned in `swe gh repo list`.
public struct PinnedRepo: Codable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    /// `owner/repo`.
    public let name: String
    public let addedAt: Date
}

/// A repo found by `swe gh repo search`.
public struct RepoHit: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let description: String?
    public let stars: Int
    public let updatedAt: GhTime
    public let url: String
}

public struct GhPr: Codable, Equatable, Sendable, Identifiable {
    public enum State: String, Codable, Sendable { case open, closed, merged }
    /// The statusCheckRollup folded to one bucket by swe.
    public enum Checks: String, Codable, Sendable { case none, passing, running, failing, unknown }
    public enum Review: String, Codable, Sendable {
        case approved, none
        case changesRequested = "changes_requested"
        case reviewRequired = "review_required"
    }
    public enum Mergeable: String, Codable, Sendable { case mergeable, conflicting, unknown }

    public var id: Int { number }
    public let number: Int
    public let title: String
    public let state: State
    public let draft: Bool
    public let author: String
    /// Head branch name.
    public let branch: String
    public let base: String
    public let labels: [String]
    /// GitHub's `mergeable` lowercased; `conflicting` means merge conflicts.
    public let mergeable: Mergeable?
    public let reviewDecision: Review?
    public let checks: Checks?
    public let updatedAt: GhTime
    public let url: String
}

public struct GhIssue: Codable, Equatable, Sendable, Identifiable {
    public enum State: String, Codable, Sendable { case open, closed }

    public var id: Int { number }
    public let number: Int
    public let title: String
    public let state: State
    public let author: String
    public let labels: [String]
    public let assignees: [String]
    public let comments: Int
    public let updatedAt: GhTime
    public let url: String
}

/// A workflow run from `gh run list`.
public struct GhRun: Codable, Equatable, Sendable, Identifiable {
    public let id: Int64
    public let workflow: String
    public let title: String
    public let branch: String
    public let event: String
    /// `queued` | `in_progress` | `completed` | …; nil while GitHub is deciding.
    public let status: String?
    /// `success` | `failure` | `cancelled` | …; null until the run ends.
    public let conclusion: String?
    public let createdAt: GhTime
    public let updatedAt: GhTime
    public let url: String
}

/// The repo's GitHub Pages site, when it has one.
public struct GhPages: Codable, Equatable, Sendable {
    public struct Build: Codable, Equatable, Sendable {
        /// `built` | `building` | `errored` | `fetch-failed` (the build
        /// read itself failed; `error` has gh's stderr).
        public let status: String
        public let error: String?
        public let commit: String?
        public let updatedAt: GhTime?
    }
    public let url: String?
    public let cname: String?
    public let status: String?
    public let sourceBranch: String?
    public let latestBuild: Build?
}

public struct GhRelease: Codable, Equatable, Sendable, Identifiable {
    public var id: String { tag }
    public let tag: String
    public let name: String?
    public let latest: Bool
    public let draft: Bool
    public let prerelease: Bool
    public let publishedAt: GhTime?
    public let url: String
}

public struct GhTag: Codable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let sha: String
    public let url: String
}

public struct GhBranch: Codable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let protected: Bool
    public let `default`: Bool
    public let sha: String
    public let url: String
}

/// A line of the GitHub notifications inbox. `url` was rewritten by swe
/// from the API subject URL to the github.com page where one exists.
public struct GhNotification: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    /// `owner/repo` the subject lives in.
    public let repo: String
    public let title: String
    /// GitHub's subject type: `PullRequest`, `Issue`, `CheckSuite`, …
    public let type: String
    /// `review_requested`, `mention`, `ci_activity`, …
    public let reason: String
    public let unread: Bool
    public let updatedAt: GhTime
    public let url: String
}

// MARK: - Envelopes (what `data` decodes to)

/// `swe gh --json`: the probe plus the pin list.
public struct GhOverview: Decodable, Sendable {
    public let github: GhEnv
    public let repos: [PinnedRepo]
    /// The synced catalog summary — nil until the first `swe gh sync`.
    public let catalog: GhCatalog?
}

/// `swe gh`'s synced catalog: the local repo store `repo search` reads,
/// written only by `swe gh sync`.
public struct GhCatalog: Decodable, Sendable {
    public let repos: Int
    public let syncedAt: GhTime?
    public let owners: [String]
}

/// `swe gh sync --json`: a resync's outcome — `failed` names the owners
/// whose `gh repo list` errored; their previous rows stayed.
public struct GhSync: Decodable, Sendable {
    public struct FailedOwner: Decodable, Sendable {
        public let owner: String
        public let error: String?
    }
    public let repos: Int
    public let syncedAt: GhTime?
    public let owners: [String]
    public let failed: [FailedOwner]
}

/// `swe gh repo list --json`.
public struct GhPinList: Decodable, Sendable {
    public let repos: [PinnedRepo]
}

/// `swe gh repo add --json`: the verified repo plus the new pin list.
public struct GhPinChange: Decodable, Sendable {
    public let repo: String
    public let pinned: [String]
}

/// `swe gh repo remove --json`.
public struct GhPinRemoval: Decodable, Sendable {
    public let removed: Bool
    public let repo: String
    public let pinned: [String]
}

/// `swe gh repo reorder --json`.
public struct GhPinOrder: Decodable, Sendable {
    public let pinned: [String]
}

/// `swe gh repo search <q> --json`.
public struct GhRepoSearch: Decodable, Sendable {
    public let query: String
    public let results: [RepoHit]
}

/// `swe gh pr worktree <N> --repo=R --json`: the checkout a session
/// launches from. `path` is null on failure, `created` false when the
/// worktree already existed.
public struct GhPrWorktree: Decodable, Sendable {
    public let repo: String
    public let pr: Int
    public let path: String?
    public let head: String?
    public let created: Bool
    public let error: String?
}

/// The per-repo entry shape differs only in which key holds the rows;
/// each surface gets a named entry so the contract stays explicit.
public struct GhPrEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    public let pullRequests: [GhPr]?
}
public struct GhIssueEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    public let issues: [GhIssue]?
}
public struct GhRunEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    public let runs: [GhRun]?
}
public struct GhPagesEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    /// null when the repo has no Pages site.
    public let pages: GhPages?
}
public struct GhReleaseEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    public let releases: [GhRelease]?
}
public struct GhTagEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    public let tags: [GhTag]?
}
public struct GhBranchEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    public let branches: [GhBranch]?
    /// The repo's default branch name, when known.
    public let defaultBranch: String?
}

/// `swe gh <surface> --json`: env probe plus one entry per repo asked.
public struct GhResults<Entry: Decodable & Sendable>: Decodable, Sendable {
    public let github: GhEnv
    public let results: [Entry]
}

/// `swe gh board --json`: all seven surfaces per repo in one reply, so
/// the page's cold load costs the slowest single fetch instead of one
/// call per tab.
public struct GhBoards: Decodable, Sendable {
    public let github: GhEnv
    public let boards: [GhBoardPayload]
}

public struct GhBoardPayload: Decodable, Sendable {
    public let repo: String
    public let surfaces: GhSurfaces
}

public struct GhSurfaces: Decodable, Sendable {
    public let pullRequests: GhPrEntry?
    public let issues: GhIssueEntry?
    public let runs: GhRunEntry?
    public let pages: GhPagesEntry?
    public let releases: GhReleaseEntry?
    public let tags: GhTagEntry?
    public let branches: GhBranchEntry?
}

/// `swe gh pr status --json` groups PRs three ways per repo.
public struct GhPrStatusEntry: Decodable, Sendable {
    public let repo: String
    public let status: GhEntryStatus
    public let error: String?
    /// The PR for the checked-out branch, when the repo was resolved
    /// from a working directory and the branch has one.
    public let currentBranch: GhPr?
    public let authored: [GhPr]?
    public let reviewRequested: [GhPr]?
}

/// `swe gh inbox --json`: account-wide, not per-repo.
public struct GhInbox: Decodable, Sendable {
    public let github: GhEnv
    /// `ok` or an `GhEntryStatus`-style failure word.
    public let status: String
    public let notifications: [GhNotification]
}

// MARK: - One-object commands (detail + actions)

/// One check row inside `GhPrDetail.checkRuns`, normalized across
/// CheckRun and StatusContext shapes. `state` is swe's rollup word —
/// `passing`|`running`|`failing`|`none` — so pending contexts and odd
/// conclusions still draw a warning.
public struct GhCheck: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { "\(workflow)/\(name)" }
    public let name: String
    public let workflow: String
    public let status: String
    public let conclusion: String?
    public let state: String
    public let url: String
}

/// One submitted review inside `GhPrDetail.reviews`.
public struct GhReview: Decodable, Equatable, Sendable {
    public let author: String
    /// `approved` | `changes_requested` | `commented` | `dismissed` | …
    public let state: String
    public let submittedAt: GhTime?
}

/// An armed auto-merge on the PR.
public struct GhAutoMerge: Decodable, Equatable, Sendable {
    public let method: String
    public let enabledAt: GhTime?
}

/// `swe gh pr view`'s detail row: every `GhPr` field plus the body,
/// per-check rows, reviews, and the merge-state fields the pane draws.
public struct GhPrDetail: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int { number }
    public let repo: String
    public let number: Int
    public let title: String
    public let state: GhPr.State
    public let draft: Bool
    public let author: String
    public let branch: String
    public let base: String
    public let labels: [String]
    public let mergeable: GhPr.Mergeable?
    public let reviewDecision: GhPr.Review?
    public let checks: GhPr.Checks?
    public let updatedAt: GhTime
    public let url: String
    public let body: String
    /// GitHub's `mergeStateStatus` lowercased: `clean`|`blocked`|`dirty`|…
    public let mergeState: String
    public let additions: Int?
    public let deletions: Int?
    public let changedFiles: Int?
    public let checkRuns: [GhCheck]
    public let reviews: [GhReview]
    /// User logins and team slugs still awaiting review.
    public let reviewRequests: [String]
    public let autoMerge: GhAutoMerge?
    public let createdAt: GhTime?
    public let mergedAt: GhTime?
}

/// `swe gh pr view --json`: `pr` is null on any failure — a blocked
/// env reads through `github`, a fetch failure through `error`.
public struct GhPrView: Decodable, Sendable {
    public let github: GhEnv
    public let repo: String?
    public let status: String
    public let pr: GhPrDetail?
    public let error: String?
}

/// One comment on an issue, oldest first in `GhIssueDetail.comments`.
public struct GhIssueComment: Decodable, Equatable, Sendable {
    public let author: String
    public let body: String
    public let createdAt: GhTime?
    public let url: String?

    public init(author: String, body: String, createdAt: GhTime? = nil, url: String? = nil) {
        self.author = author
        self.body = body
        self.createdAt = createdAt
        self.url = url
    }
}

/// `swe gh issue view`'s detail row: the list row's fields plus the
/// body, every comment, and where a resolve run would start — the
/// `quiver/issue-<n>` branch, its worktree path on the hub machine, and
/// the first prompt the hub would send (the body and comments folded in).
public struct GhIssueDetail: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int { number }
    public let number: Int
    public let title: String
    public let state: GhIssue.State
    public let author: String
    public let labels: [String]
    public let assignees: [String]
    public let url: String
    public let body: String
    public let createdAt: GhTime?
    public let updatedAt: GhTime?
    public let comments: [GhIssueComment]
    /// `quiver/issue-<n>`, off the repo's default branch.
    public let branch: String?
    /// The checkout's absolute path under ~/.swe/worktrees.
    public let worktree: String?
    public let suggestedPrompt: String?

    private enum CodingKeys: String, CodingKey {
        case number, title, state, author, labels, assignees, url, body
        case createdAt, updatedAt, comments, branch, worktree, suggestedPrompt
    }

    /// Lists and text the CLI may leave out decode as empty, so an older
    /// or trimmed reply still opens the sheet.
    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        number = try box.decode(Int.self, forKey: .number)
        title = try box.decode(String.self, forKey: .title)
        state = try box.decode(GhIssue.State.self, forKey: .state)
        author = try box.decodeIfPresent(String.self, forKey: .author) ?? ""
        labels = try box.decodeIfPresent([String].self, forKey: .labels) ?? []
        assignees = try box.decodeIfPresent([String].self, forKey: .assignees) ?? []
        url = try box.decodeIfPresent(String.self, forKey: .url) ?? ""
        body = try box.decodeIfPresent(String.self, forKey: .body) ?? ""
        createdAt = try box.decodeIfPresent(GhTime.self, forKey: .createdAt)
        updatedAt = try box.decodeIfPresent(GhTime.self, forKey: .updatedAt)
        comments = try box.decodeIfPresent([GhIssueComment].self, forKey: .comments) ?? []
        branch = try box.decodeIfPresent(String.self, forKey: .branch)
        worktree = try box.decodeIfPresent(String.self, forKey: .worktree)
        suggestedPrompt = try box.decodeIfPresent(String.self, forKey: .suggestedPrompt)
    }
}

/// `swe gh issue view --json`: `issue` is null on any failure, read the
/// same way as `GhPrView`.
public struct GhIssueView: Decodable, Sendable {
    public let github: GhEnv?
    public let repo: String?
    public let status: String
    public let issue: GhIssueDetail?
    public let error: String?
}

/// `swe gh issue comment --json`: the posted comment, or `error`.
public struct GhIssueCommentReply: Decodable, Sendable {
    public let github: GhEnv?
    public let repo: String?
    public let status: String?
    public let issue: Int?
    public let comment: GhIssueComment?
    public let error: String?
}

/// `swe gh pr merge --json`: `merged` is false under `--auto` —
/// arming is not merging.
public struct GhMerge: Decodable, Sendable {
    public let github: GhEnv
    public let repo: String?
    public let ref: String
    public let method: String
    public let auto: Bool
    public let merged: Bool
    public let error: String?
}

/// `swe gh run rerun --json`.
public struct GhRerun: Decodable, Sendable {
    public let github: GhEnv
    public let repo: String?
    public let run: Int
    public let rerun: Bool
    public let failedOnly: Bool?
    public let error: String?
}

/// `swe gh inbox read --json`: `marked` lists the threads flipped;
/// `--all` reports `marked: ["*"]`.
public struct GhInboxRead: Decodable, Sendable {
    public struct Failed: Decodable, Sendable {
        public let id: String
        public let error: String
    }
    public let github: GhEnv
    public let all: Bool
    public let marked: [String]
    public let failed: [Failed]?
    public let error: String?
}

// MARK: - SweClient

extension SweClient {
    /// `swe gh --json` — probe state plus pinned repos.
    public func ghOverview() async throws -> GhOverview {
        try await call(["gh", "--json"], allowFailure: true)
    }

    /// `swe gh repo list --json` — the pin list alone (no env probe).
    public func ghRepoList() async throws -> GhPinList {
        try await call(["gh", "repo", "list", "--json"], allowFailure: true)
    }

    /// `swe gh repo add <input> --json` — verifies the repo exists before pinning.
    public func ghAddRepo(_ input: String) async throws -> GhPinChange {
        try await call(["gh", "repo", "add", input, "--json"])
    }

    /// `swe gh repo remove <name> --json`.
    public func ghRemoveRepo(_ name: String) async throws -> GhPinRemoval {
        try await call(["gh", "repo", "remove", name, "--json"])
    }

    /// `swe gh repo reorder <name>... --json` — rewrites the pin order.
    public func ghReorderRepos(_ order: [String]) async throws -> GhPinOrder {
        try await call(["gh", "repo", "reorder"] + order + ["--json"])
    }

    /// `swe gh sync --json` — rewrites the local repo catalog in place.
    public func ghSync() async throws -> GhSync {
        try await call(["gh", "sync", "--json"], allowFailure: true)
    }

    /// `swe gh repo search <query> --json`.
    public func ghSearchRepos(_ query: String, limit: Int = 10) async throws -> GhRepoSearch {
        try await call(["gh", "repo", "search", query, "--limit=\(limit)", "--json"],
                       allowFailure: true)
    }

    /// `swe gh pr --repo=<repo> --json`. A nil repo resolves from the
    /// caller's cwd, then the pins — which in the app means "every pin".
    public func ghPullRequests(repo: String? = nil, state: String = "open", limit: Int = 50) async throws -> GhResults<GhPrEntry> {
        var args = ["gh", "pr", "--state=\(state)", "--limit=\(limit)"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh pr status --repo=<repo> --json` — current-branch,
    /// authored and review-requested buckets in one reply.
    public func ghPrStatus(repo: String? = nil) async throws -> GhResults<GhPrStatusEntry> {
        var args = ["gh", "pr", "status"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh pr worktree <n> --repo=<repo> --json` — fetch the PR head
    /// into a shared mirror and keep a detached checkout under
    /// ~/.swe/worktrees. A failed reply still decodes (path nil, the
    /// error in `error`), so `allowFailure` stays on.
    public func ghPrWorktree(repo: String, pr: Int) async throws -> GhPrWorktree {
        try await call(["gh", "pr", "worktree", "\(pr)", "--repo=\(repo)", "--json"],
                       allowFailure: true)
    }

    /// `swe gh board --repo=<a,b> --json` — every surface of every repo
    /// in one reply; the CLI fans the repos out itself. `repos` nil means
    /// "every pin", relying on resolve's pin fallback — pass the explicit
    /// list when the caller knows it, since a checkout cwd would win.
    public func ghBoard(repos: [String]? = nil) async throws -> GhBoards {
        var args = ["gh", "board"]
        if let repos, !repos.isEmpty {
            args.append("--repo=\(repos.joined(separator: ","))")
        }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh issue --repo=<repo> --json`.
    public func ghIssues(repo: String? = nil, state: String = "open", limit: Int = 50) async throws -> GhResults<GhIssueEntry> {
        var args = ["gh", "issue", "--state=\(state)", "--limit=\(limit)"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh run --repo=<repo> --json`.
    public func ghRuns(repo: String? = nil, limit: Int = 30) async throws -> GhResults<GhRunEntry> {
        var args = ["gh", "run", "--limit=\(limit)"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh pages --repo=<repo> --json`; `pages` is null for repos without a site.
    public func ghPages(repo: String? = nil) async throws -> GhResults<GhPagesEntry> {
        var args = ["gh", "pages"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh releases --repo=<repo> --json`.
    public func ghReleases(repo: String? = nil, limit: Int = 20) async throws -> GhResults<GhReleaseEntry> {
        var args = ["gh", "releases", "--limit=\(limit)"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh tags --repo=<repo> --json`.
    public func ghTags(repo: String? = nil, limit: Int = 30) async throws -> GhResults<GhTagEntry> {
        var args = ["gh", "tags", "--limit=\(limit)"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh branches --repo=<repo> --json`.
    public func ghBranches(repo: String? = nil, limit: Int = 50) async throws -> GhResults<GhBranchEntry> {
        var args = ["gh", "branches", "--limit=\(limit)"]
        if let repo { args.append("--repo=\(repo)") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh inbox --json`. `all` includes read notifications so the
    /// page's unread filter can widen without another fetch.
    public func ghInbox(limit: Int = 50, all: Bool = true) async throws -> GhInbox {
        var args = ["gh", "inbox", "--limit=\(limit)"]
        if all { args.append("--all") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh pr view <ref> --repo=<repo> --json` — the detail pane's read.
    public func ghPrView(repo: String, ref: String) async throws -> GhPrView {
        try await call(["gh", "pr", "view", ref, "--repo=\(repo)", "--json"],
                       allowFailure: true)
    }

    /// `swe gh issue view <n> --repo=<repo> --json` — the issue sheet's read.
    public func ghIssueView(repo: String, number: Int) async throws -> GhIssueView {
        try await call(["gh", "issue", "view", "\(number)", "--repo=\(repo)", "--json"],
                       allowFailure: true)
    }

    /// `swe gh issue comment <n> --repo=<repo> --body-file=- --json`, the
    /// text on stdin — posts as the `gh` login on this Mac. Never on argv:
    /// every process on the Mac can read another's arguments. A refusal
    /// exits non-zero with `error` in the reply, so `allowFailure` stays on.
    public func ghIssueComment(repo: String, number: Int, body: String) async throws -> GhIssueCommentReply {
        try await call(["gh", "issue", "comment", "\(number)", "--repo=\(repo)",
                        "--body-file=-", "--json"],
                       input: Data(body.utf8), allowFailure: true)
    }

    /// `swe gh pr merge <ref> --repo=<repo> --json`. `auto` arms
    /// auto-merge rather than merging now.
    public func ghPrMerge(repo: String, ref: String, method: String,
                          auto: Bool = false) async throws -> GhMerge {
        var args = ["gh", "pr", "merge", ref, "--repo=\(repo)",
                    "--method=\(method)"]
        if auto { args.append("--auto") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh run rerun <id> --repo=<repo> --json`; `failedOnly`
    /// re-runs just the failed jobs.
    public func ghRunRerun(repo: String, run: Int64,
                           failedOnly: Bool = false) async throws -> GhRerun {
        var args = ["gh", "run", "rerun", "\(run)", "--repo=\(repo)"]
        if failedOnly { args.append("--failed") }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }

    /// `swe gh inbox read <id>...|--all --json` — mark threads read.
    public func ghInboxRead(ids: [String] = [], all: Bool = false) async throws -> GhInboxRead {
        var args = ["gh", "inbox", "read"]
        if all { args.append("--all") } else { args.append(contentsOf: ids) }
        args.append("--json")
        return try await call(args, allowFailure: true)
    }
}
