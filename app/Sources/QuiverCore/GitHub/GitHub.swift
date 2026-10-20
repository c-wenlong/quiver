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
public struct GhTime: Decodable, Equatable, Sendable {
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
}

/// A repo the user pinned in `swe gh repo list`.
public struct PinnedRepo: Decodable, Equatable, Sendable, Identifiable {
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

public struct GhPr: Decodable, Equatable, Sendable, Identifiable {
    public enum State: String, Decodable, Sendable { case open, closed, merged }
    /// The statusCheckRollup folded to one bucket by swe.
    public enum Checks: String, Decodable, Sendable { case none, passing, running, failing, unknown }
    public enum Review: String, Decodable, Sendable {
        case approved, none
        case changesRequested = "changes_requested"
        case reviewRequired = "review_required"
    }
    public enum Mergeable: String, Decodable, Sendable { case mergeable, conflicting, unknown }

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

public struct GhIssue: Decodable, Equatable, Sendable, Identifiable {
    public enum State: String, Decodable, Sendable { case open, closed }

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
public struct GhRun: Decodable, Equatable, Sendable, Identifiable {
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
public struct GhPages: Decodable, Equatable, Sendable {
    public struct Build: Decodable, Equatable, Sendable {
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

public struct GhRelease: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { tag }
    public let tag: String
    public let name: String?
    public let latest: Bool
    public let draft: Bool
    public let prerelease: Bool
    public let publishedAt: GhTime?
    public let url: String
}

public struct GhTag: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let sha: String
    public let url: String
}

public struct GhBranch: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let protected: Bool
    public let `default`: Bool
    public let sha: String
    public let url: String
}

/// A line of the GitHub notifications inbox. `url` was rewritten by swe
/// from the API subject URL to the github.com page where one exists.
public struct GhNotification: Decodable, Equatable, Sendable, Identifiable {
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

/// `swe gh repo search <q> --json`.
public struct GhRepoSearch: Decodable, Sendable {
    public let query: String
    public let results: [RepoHit]
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
}
