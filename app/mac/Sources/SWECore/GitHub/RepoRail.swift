import Foundation

/// Bot accounts. Dependency-update bots only: coding agents (Devin,
/// Copilot) also open PRs from app accounts, but theirs are real work.
public enum GitHubBots {
    static let names: Set<String> = ["dependabot", "dependabot-preview", "renovate",
                                     "github-actions", "pre-commit-ci", "snyk-bot"]

    /// `gh` reports an app author as `app/dependabot`; the REST API as
    /// `dependabot[bot]`. Both name the same bot.
    public static func isBot(_ login: String) -> Bool {
        var name = login.lowercased()
        if name.hasPrefix("app/") { name.removeFirst(4) }
        if name.hasSuffix("[bot]") { name.removeLast(5) }
        return names.contains(name)
    }
}

extension GhRun {
    /// The conclusions that count as a failed run, for the search words
    /// and the rail's CI dot alike.
    public static let failingConclusions: Set<String> = ["failure", "timed_out", "startup_failure"]

    public var failed: Bool { conclusion.map(Self.failingConclusions.contains) ?? false }

    public var running: Bool { status == "in_progress" || status == "queued" }
}

/// Reordering inside one rail group. The rail splits the pins by owner
/// and into active and quiet repos, so a drag only ever moves a repo
/// within its own group; every other pin keeps its slot.
public enum PinOrder {
    /// The full pin order after moving `id` to sit before `before` (nil:
    /// the end) inside `group`. The group's members are written back
    /// into the slots they held in `pins`, in their new order.
    public static func move(_ id: String, before: String?, in group: [String],
                            pins: [String]) -> [String] {
        var reordered = group
        reordered.removeAll { $0 == id }
        if let before, let idx = reordered.firstIndex(of: before) {
            reordered.insert(id, at: idx)
        } else {
            reordered.append(id)
        }
        let members = Set(group)
        var next = reordered.makeIterator()
        return pins.map { members.contains($0) ? (next.next() ?? $0) : $0 }
    }
}

extension GhPr {
    /// The text the page's search matches: the row's own fields plus
    /// words for its state, so "failing", "draft", "conflict",
    /// "approved" or "bot" find the PRs the row's icons mark.
    public var searchText: [String?] {
        var words = [title, "\(number)", "#\(number)", branch, base, author] + labels
        words.append(draft ? "draft" : state.rawValue)
        switch checks {
        case .passing: words.append("passing")
        case .failing: words.append("failing failed")
        case .running: words.append("running pending")
        case .unknown, .none?, nil: break
        }
        if mergeable == .conflicting { words.append("conflicts merge conflict") }
        switch reviewDecision {
        case .approved: words.append("approved")
        case .changesRequested: words.append("changes requested")
        case .reviewRequired: words.append("review needed required")
        case .none?, nil: break
        }
        if GitHubBots.isBot(author) { words.append("bot") }
        return words
    }
}

extension GhIssue {
    public var searchText: [String?] {
        [title, "\(number)", "#\(number)", author, state.rawValue]
            + labels + assignees + (GitHubBots.isBot(author) ? ["bot"] : [])
    }
}

extension GhRun {
    public var searchText: [String?] {
        [title, workflow, branch, event, conclusion, status,
         failed ? "failing failed" : nil, running ? "running" : nil]
    }
}

extension GhRelease {
    public var searchText: [String?] { [name, tag] }
}

extension GhTag {
    public var searchText: [String?] { [name, sha] }
}

extension GhBranch {
    public var searchText: [String?] { [name, sha] }
}

extension GitHubModel {
    /// The tabs the list offers: every surface for one repo; across all
    /// repos only the three that read well merged into one list.
    public static func tabs(oneRepo: Bool) -> [RepoTab] {
        oneRepo ? RepoTab.allCases : [.pulls, .issues, .actions]
    }

    /// The repos the list shows, in pin order: the rail's pick, else
    /// every repo the filter leaves.
    public var listedRepos: [PinnedRepo] {
        if let shownRepo {
            return pins.filter { $0.name == shownRepo }
        }
        return visibleRepos
    }

    /// How many rows the list shows for `repo` on `tab` after the
    /// search and, for pull requests, the Mine / Review requested
    /// filter. nil while the surface has not loaded or failed, so the
    /// rail never calls an unknown repo quiet.
    public func rowCount(_ repo: String, tab: RepoTab) -> Int? {
        let board = board(for: repo)
        switch tab {
        case .pulls:
            guard case .loaded(let prs) = board.pulls else { return nil }
            // The rail lists every visible repo, not only the listed
            // one the filter was offered for: a repo whose review set
            // is unknown has no count rather than a false zero.
            if activePullFilter == .reviewRequested, reviewRequested(repo) == nil { return nil }
            return count(.loaded(prs.filter { keepsPull($0, repo: repo) }), repo: repo, \.searchText)
        case .issues: return count(board.issues, repo: repo, \.searchText)
        case .actions: return count(board.runs, repo: repo, \.searchText)
        case .releases: return count(board.releases, repo: repo, \.searchText)
        case .tags: return count(board.tags, repo: repo, \.searchText)
        case .branches: return count(board.branches, repo: repo, \.searchText)
        case .pages:
            guard case .loaded(let site) = board.pages else { return nil }
            return site == nil ? 0 : 1
        }
    }

    private func count<Row>(_ state: Loadable<[Row]>, repo: String,
                            _ parts: (Row) -> [String?]) -> Int? {
        guard case .loaded(let rows) = state else { return nil }
        guard !showsAllRows(repo) else { return rows.count }
        return rows.reduce(0) { $0 + (matches(parts($1)) ? 1 : 0) }
    }

    /// The rail's CI dot: the newest run on the default branch, or the
    /// newest run at all when the default branch is unknown. A known
    /// default branch with no run in the loaded page reads unknown, so
    /// a feature branch's failure never paints the repo. nil when the
    /// repo has no runs loaded.
    public func ciState(_ repo: String) -> GhPr.Checks? {
        let board = board(for: repo)
        guard case .loaded(let runs) = board.runs, !runs.isEmpty else { return nil }
        var main: String?
        if case .loaded(let branches) = board.branches {
            main = branches.first(where: \.default)?.name
        }
        let run: GhRun
        if let main {
            guard let latest = runs.first(where: { $0.branch == main }) else { return .unknown }
            run = latest
        } else {
            run = runs[0]
        }
        if run.running { return .running }
        if run.conclusion == "success" { return .passing }
        return run.failed ? .failing : .unknown
    }
}
