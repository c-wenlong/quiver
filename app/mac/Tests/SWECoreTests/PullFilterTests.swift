import Foundation
import Testing
@testable import SWECore

private func pr(_ number: Int, author: String = "me", state: String = "open", title: String = "Fix") -> String {
    "{\"number\":\(number),\"title\":\"\(title)\",\"state\":\"\(state)\",\"draft\":false,"
        + "\"author\":\"\(author)\",\"branch\":\"b\(number)\",\"base\":\"main\",\"labels\":[],"
        + "\"mergeable\":\"mergeable\",\"review_decision\":\"none\",\"checks\":\"passing\","
        + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/\(number)\"}"
}

private func decodePr(_ json: String) throws -> GhPr {
    let d = JSONDecoder()
    d.keyDecodingStrategy = .convertFromSnakeCase
    return try d.decode(GhPr.self, from: Data(json.utf8))
}

@Suite struct PullFilterTests {
    @Test func filtersShowOnlyWhenTheirFlagsAreKnown() {
        #expect(PullFilter.available(login: nil, requests: [nil]).isEmpty)
        #expect(PullFilter.available(login: "", requests: []).isEmpty)
        #expect(PullFilter.available(login: "me", requests: [nil]) == [.all, .mine])
        #expect(PullFilter.available(login: "me", requests: [[1], []]) == [.all, .mine, .reviewRequested])
        // One repo whose gh pr status failed hides Review requested for
        // the whole list: its PRs would otherwise read "nothing to review".
        #expect(PullFilter.available(login: "me", requests: [[1], nil]) == [.all, .mine])
        #expect(PullFilter.available(login: nil, requests: [[]]) == [.all, .reviewRequested])
    }

    @Test func aPickWhoseFlagsWentUnknownFallsBackToAll() {
        #expect(PullFilter.effective(.reviewRequested, available: [.all, .mine]) == .all)
        #expect(PullFilter.effective(.mine, available: []) == .all)
        #expect(PullFilter.effective(.mine, available: [.all, .mine]) == .mine)
    }

    @Test func rowsKeepByAuthorAndReviewRequest() throws {
        let mine = try decodePr(pr(1, author: "Me"))
        let theirs = try decodePr(pr(2, author: "you"))
        let merged = try decodePr(pr(3, author: "you", state: "merged"))
        #expect(PullFilter.mine.keeps(mine, login: "me", requested: nil))
        #expect(!PullFilter.mine.keeps(theirs, login: "me", requested: nil))
        #expect(!PullFilter.mine.keeps(mine, login: nil, requested: nil))
        #expect(PullFilter.reviewRequested.keeps(theirs, login: "me", requested: [2]))
        #expect(!PullFilter.reviewRequested.keeps(theirs, login: "me", requested: nil))
        // A closed or merged PR asks nobody for review any more.
        #expect(!PullFilter.reviewRequested.keeps(merged, login: "me", requested: [3]))
        #expect(PullFilter.all.keeps(merged, login: nil, requested: nil))
    }
}

@MainActor
@Suite struct PullFilterModelTests {
    /// The GitHub tests' suffix router, each reply wrapped in swe's envelope.
    func router(_ replies: [String: String]) -> GitHubModelTests.Router {
        GitHubModelTests.Router(replies.mapValues {
            CommandResult(status: 0, stdout: Data("{\"schema\":1,\"data\":{\($0)}}".utf8), stderr: "")
        })
    }

    let env = "\"github\":{\"status\":\"ok\",\"login\":\"me\",\"error\":null}"

    func pulls(_ repo: String, _ rows: [String]) -> String {
        "\(env),\"results\":[{\"repo\":\"\(repo)\",\"status\":\"ok\",\"pull_requests\":[\(rows.joined(separator: ","))]}]"
    }

    func status(_ repo: String, requested: [Int], ok: Bool = true) -> String {
        let rows = requested.map { pr($0, author: "you") }.joined(separator: ",")
        return "\(env),\"results\":[{\"repo\":\"\(repo)\",\"status\":\"\(ok ? "ok" : "upstream-error")\","
            + "\"current_branch\":null,\"authored\":[],\"review_requested\":[\(rows)]}]"
    }

    @Test func theDashboardFiltersAndCountsOnceTheFlagsLand() async {
        let runner = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                pulls("o/a", [pr(1), pr(2, author: "you"), pr(3, author: "you", title: "Docs")]),
            "gh pr status --repo=o/a --json": status("o/a", requested: [2]),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadIfStale(swe: swe)
        // The login is known, the review requests not yet.
        #expect(model.pullFilters == [.all, .mine])
        model.pullFilter = .reviewRequested
        #expect(model.activePullFilter == .all)
        #expect(model.rowCount("o/a", tab: .pulls) == 3)

        await model.loadReviewRequests(repo: "o/a", swe: swe)
        #expect(model.pullFilters == [.all, .mine, .reviewRequested])
        #expect(model.activePullFilter == .reviewRequested)
        #expect(model.rowCount("o/a", tab: .pulls) == 1)
        #expect(model.pullCount(.all) == 3)
        #expect(model.pullCount(.mine) == 1)
        // The search narrows the counts too.
        model.filter = "Docs"
        #expect(model.pullCount(.all) == 1)
        #expect(model.pullCount(.reviewRequested) == 0)
    }

    /// swe skips `gh auth status` when every surface read came back ok,
    /// so a pull request reply's probe has no login. Mine stays offered.
    @Test func aSurfaceReplyWithoutALoginKeepsMine() async {
        let anonymous = "\"github\":{\"status\":\"ok\",\"login\":null,\"error\":null}"
        let runner = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                "\(anonymous),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":[\(pr(1, author: "me"))]}]",
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadTab(.pulls, repo: "o/a", swe: swe)
        #expect(model.env?.login == "me")
        #expect(model.pullFilters == [.all, .mine])
        #expect(model.pullCount(.mine) == 1)
    }

    @Test func aFreshReplyIsReusedAndAFailureKeepsTheLastGoodSet() async {
        let runner = router(["gh pr status --repo=o/a --json": status("o/a", requested: [4])])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        let start = Date(timeIntervalSince1970: 1_000)
        await model.loadReviewRequests(repo: "o/a", swe: swe, now: start)
        #expect(model.reviewRequested("o/a") == [4])
        await model.loadReviewRequests(repo: "o/a", swe: swe, now: start.addingTimeInterval(30))
        #expect(runner.calls.count == 1)

        runner.replies["gh pr status --repo=o/a --json"] = CommandResult(
            status: 0,
            stdout: Data("{\"schema\":1,\"data\":{\(status("o/a", requested: [], ok: false))}}".utf8),
            stderr: "")
        await model.loadReviewRequests(repo: "o/a", swe: swe, now: start.addingTimeInterval(90))
        #expect(runner.calls.count == 2)
        #expect(model.reviewRequested("o/a") == [4])
    }

    /// The dashboard's load task restarts as the search changes, which
    /// cancels a status read in flight; the next task must read again
    /// rather than skip a reply that never came.
    @Test func aCancelledReadLeavesTheRepoToBeReadAgain() async {
        final class Cancelling: CommandRunning, @unchecked Sendable {
            func run(_ arguments: [String]) async throws -> CommandResult { throw CancellationError() }
            func run(_ arguments: [String], input: Data?) async throws -> CommandResult { throw CancellationError() }
        }
        let model = GitHubModel()
        await model.loadReviewRequests(repo: "o/a", swe: SweClient(runner: Cancelling()))
        #expect(model.reviewRequests["o/a"] == nil)

        let runner = router(["gh pr status --repo=o/a --json": status("o/a", requested: [5])])
        await model.loadReviewRequests(repo: "o/a", swe: SweClient(runner: runner))
        #expect(model.reviewRequested("o/a") == [5])
        // With a known set, a cancelled read keeps it but marks it stale.
        await model.loadReviewRequests(repo: "o/a", swe: SweClient(runner: Cancelling()), force: true)
        #expect(model.reviewRequested("o/a") == [5])
        await model.loadReviewRequests(repo: "o/a", swe: SweClient(runner: runner))
        #expect(runner.calls.count == 2)
    }

    /// The rail counts every visible repo while the filter was offered
    /// for the one listed: an unknown repo has no count, not zero.
    @Test func anUnknownRepoHasNoReviewCountInTheRail() async {
        let runner = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100},{\"name\":\"o/b\",\"added_at\":200}]",
            "gh pr --state=open --limit=50 --repo=o/a --json": pulls("o/a", [pr(1)]),
            "gh pr --state=open --limit=50 --repo=o/b --json": pulls("o/b", [pr(2)]),
            "gh pr status --repo=o/a --json": status("o/a", requested: [1]),
            "gh pr status --repo=o/b --json": status("o/b", requested: [], ok: false),
        ])
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadTab(.pulls, repo: "o/a", swe: swe)
        await model.loadTab(.pulls, repo: "o/b", swe: swe)
        await model.loadReviewRequests(repo: "o/a", swe: swe)
        await model.loadReviewRequests(repo: "o/b", swe: swe)
        #expect(!model.pullFilters.contains(.reviewRequested))
        model.shownRepo = "o/a"
        model.pullFilter = .reviewRequested
        #expect(model.activePullFilter == .reviewRequested)
        #expect(model.rowCount("o/a", tab: .pulls) == 1)
        #expect(model.rowCount("o/b", tab: .pulls) == nil)
    }

    @Test func aRepoThatNeverAnsweredStaysUnknown() async {
        let swe = SweClient(runner: router([
            "gh pr status --repo=o/a --json": status("o/a", requested: [], ok: false),
        ]))
        let model = GitHubModel()
        await model.loadReviewRequests(repo: "o/a", swe: swe)
        #expect(model.reviewRequested("o/a") == nil)
    }
}
