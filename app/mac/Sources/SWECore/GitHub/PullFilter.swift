import Foundation

/// The pull request list's row filters, as the iPhone's Pulls tab has
/// them: every PR, the ones the signed-in login authored, the ones it
/// was asked to review.
public enum PullFilter: String, CaseIterable, Identifiable, Sendable {
    case all, mine, reviewRequested

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .all: "All"
        case .mine: "Mine"
        case .reviewRequested: "Review requested"
        }
    }

    /// The filters that can answer for `repos`. Mine needs the `gh`
    /// login; Review requested needs every repo's `gh pr status`
    /// reply, so a repo whose reply failed never reads as "nothing to
    /// review". Empty when only All is left: the picker then hides.
    public static func available(login: String?, requests: [Set<Int>?]) -> [PullFilter] {
        var out: [PullFilter] = [.all]
        if login?.isEmpty == false { out.append(.mine) }
        if !requests.isEmpty, requests.allSatisfy({ $0 != nil }) { out.append(.reviewRequested) }
        return out.count > 1 ? out : []
    }

    /// The filter in force: the picked one while it can still answer,
    /// else All, so flags that went unknown never empty the list.
    public static func effective(_ picked: PullFilter, available: [PullFilter]) -> PullFilter {
        available.contains(picked) ? picked : .all
    }

    /// Whether `pr` stays. `requested` is the repo's open PR numbers
    /// waiting on the login's review; a closed PR asks nobody.
    public func keeps(_ pr: GhPr, login: String?, requested: Set<Int>?) -> Bool {
        switch self {
        case .all: true
        case .mine: login.map { !$0.isEmpty && pr.author.caseInsensitiveCompare($0) == .orderedSame } ?? false
        case .reviewRequested: pr.state == .open && requested?.contains(pr.number) == true
        }
    }
}

/// One repo's `gh pr status` answer: which open PRs ask for the
/// login's review. `numbers` is nil until a reply lands ok; a later
/// failure keeps the last good set.
public struct PrReviewRequests: Equatable, Sendable {
    public var numbers: Set<Int>?
    public var fetchedAt: Date

    public init(numbers: Set<Int>?, fetchedAt: Date) {
        self.numbers = numbers
        self.fetchedAt = fetchedAt
    }
}

extension GitHubModel {
    /// The review-requested numbers for `repo`, nil while unknown.
    public func reviewRequested(_ repo: String) -> Set<Int>? {
        reviewRequests[repo]?.numbers
    }

    /// The filters the dashboard's pull request list can offer for the
    /// repos it lists; empty hides the picker.
    public var pullFilters: [PullFilter] {
        PullFilter.available(login: env?.login,
                             requests: listedRepos.map { reviewRequested($0.name) })
    }

    /// The dashboard's filter in force.
    public var activePullFilter: PullFilter {
        PullFilter.effective(pullFilter, available: pullFilters)
    }

    /// Whether the dashboard's list keeps `pr` of `repo` under its filter.
    public func keepsPull(_ pr: GhPr, repo: String) -> Bool {
        activePullFilter.keeps(pr, login: env?.login, requested: reviewRequested(repo))
    }

    /// How many open PRs the dashboard lists under `filter`, after the
    /// search: the picker's counts.
    public func pullCount(_ filter: PullFilter) -> Int {
        listedRepos.reduce(0) { sum, pin in
            guard case .loaded(let prs) = board(for: pin.name).pulls else { return sum }
            let requested = reviewRequested(pin.name)
            let everyRow = showsAllRows(pin.name)
            return sum + prs.count {
                filter.keeps($0, login: env?.login, requested: requested) && (everyRow || matches($0.searchText))
            }
        }
    }

    /// Reads `swe gh pr status --repo=<repo>` unless a reply landed
    /// within `maxAge`; `force` (Refresh) reads regardless. Only the
    /// review-requested bucket is kept: Mine reads the login against
    /// each row's author, which also covers the focus page's closed and
    /// merged PRs.
    public func loadReviewRequests(repo: String, swe: SweClient, force: Bool = false,
                                   now: Date = .now, maxAge: TimeInterval = 60) async {
        if !force, let last = reviewRequests[repo], now.timeIntervalSince(last.fetchedAt) < maxAge { return }
        let previous = reviewRequests[repo]?.numbers
        // Stamped before the call, so concurrent loads share this one.
        reviewRequests[repo] = PrReviewRequests(numbers: previous, fetchedAt: now)
        var numbers = previous
        do {
            let reply = try await swe.ghPrStatus(repo: repo)
            try Task.checkCancellation()
            if let entry = reply.results.first(where: { $0.repo == repo }), entry.status == .ok {
                numbers = Set((entry.reviewRequested ?? []).map(\.number))
            }
        } catch is CancellationError {
            // A cancelled read (the page's task restarting as the search
            // changes) proves nothing: keep the last good set, but leave
            // it stale so the next load reads again.
            guard reviewRequests[repo]?.fetchedAt == now else { return }
            reviewRequests[repo] = previous.map { PrReviewRequests(numbers: $0, fetchedAt: .distantPast) }
            return
        } catch {
            // A broken swe leaves the flags as they were.
        }
        reviewRequests[repo] = PrReviewRequests(numbers: numbers, fetchedAt: now)
    }
}
