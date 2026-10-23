import Foundation

/// The Repositories list's quick filters. The rows and the rail's
/// per-repo counts both read it, so a count never disagrees with the
/// rows it stands for.
public struct RepoRowFilter: Equatable, Sendable {
    public enum Option: String, CaseIterable, Identifiable, Sendable {
        case failing, drafts, hideBots
        public var id: String { rawValue }
        public var title: String {
            switch self {
            case .failing: "Failing CI"
            case .drafts: "Drafts"
            case .hideBots: "Hide bots"
            }
        }
    }

    public var on: Set<Option> = []

    public init(_ on: Set<Option> = []) { self.on = on }

    public var isActive: Bool { !on.isEmpty }

    /// The chips a tab offers. Every other option leaves its rows alone.
    public static func options(for tab: RepoTab) -> [Option] {
        switch tab {
        case .pulls: [.failing, .drafts, .hideBots]
        case .issues: [.hideBots]
        case .actions: [.failing]
        case .pages, .releases, .tags, .branches: []
        }
    }

    public func keeps(_ pr: GhPr) -> Bool {
        (!on.contains(.failing) || pr.checks == .failing)
            && (!on.contains(.drafts) || pr.draft)
            && (!on.contains(.hideBots) || !Self.isBot(pr.author))
    }

    public func keeps(_ issue: GhIssue) -> Bool {
        !on.contains(.hideBots) || !Self.isBot(issue.author)
    }

    public func keeps(_ run: GhRun) -> Bool {
        !on.contains(.failing) || Self.failed(run)
    }

    /// The conclusions that count as a failed run, for the chip and the
    /// rail's CI dot alike.
    public static let failingConclusions: Set<String> = ["failure", "timed_out", "startup_failure"]

    public static func failed(_ run: GhRun) -> Bool {
        run.conclusion.map(failingConclusions.contains) ?? false
    }

    /// Dependency-update bots only. Coding agents (Devin, Copilot) also
    /// open PRs from app accounts, but theirs are real work to review.
    static let bots: Set<String> = ["dependabot", "dependabot-preview", "renovate",
                                     "github-actions", "pre-commit-ci", "snyk-bot"]

    /// `gh` reports an app author as `app/dependabot`; the REST API as
    /// `dependabot[bot]`. Both name the same bot.
    public static func isBot(_ login: String) -> Bool {
        var name = login.lowercased()
        if name.hasPrefix("app/") { name.removeFirst(4) }
        if name.hasSuffix("[bot]") { name.removeLast(5) }
        return bots.contains(name)
    }
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
    /// The text the page's filter searches.
    public var searchText: [String?] { [title, "\(number)", branch, base, author] + labels }
}

extension GhIssue {
    public var searchText: [String?] { [title, "\(number)", author] + labels + assignees }
}

extension GhRun {
    public var searchText: [String?] { [title, workflow, branch, event] }
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

    /// How many rows the list shows for `repo` on `tab`, after the text
    /// filter and the chips. nil while the surface has not loaded or
    /// failed, so the rail never calls an unknown repo quiet.
    public func rowCount(_ repo: String, tab: RepoTab) -> Int? {
        let board = board(for: repo)
        let filter = rowFilter
        switch tab {
        case .pulls: return count(board.pulls, repo: repo, \.searchText, filter.keeps)
        case .issues: return count(board.issues, repo: repo, \.searchText, filter.keeps)
        case .actions: return count(board.runs, repo: repo, \.searchText, filter.keeps)
        case .releases: return count(board.releases, repo: repo, \.searchText) { _ in true }
        case .tags: return count(board.tags, repo: repo, \.searchText) { _ in true }
        case .branches: return count(board.branches, repo: repo, \.searchText) { _ in true }
        case .pages:
            guard case .loaded(let site) = board.pages else { return nil }
            return site == nil ? 0 : 1
        }
    }

    private func count<Row>(_ state: Loadable<[Row]>, repo: String,
                            _ parts: (Row) -> [String?],
                            _ keep: (Row) -> Bool) -> Int? {
        guard case .loaded(let rows) = state else { return nil }
        let all = showsAllRows(repo)
        return rows.reduce(0) { $0 + (keep($1) && (all || matches(parts($1))) ? 1 : 0) }
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
        if run.status == "in_progress" || run.status == "queued" { return .running }
        if run.conclusion == "success" { return .passing }
        return RepoRowFilter.failed(run) ? .failing : .unknown
    }
}
