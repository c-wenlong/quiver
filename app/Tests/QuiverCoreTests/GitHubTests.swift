import Foundation
import Testing
@testable import QuiverCore

/// `swe gh … --json` contracts: fixtures captured from a real swe run
/// against the live GitHub account (c-wenlong/quiver pinned), plus the
/// model's per-repo board behaviour over FakeRunner.
@Suite struct GitHubContractTests {
    private func client(_ fixtureName: String) throws -> SweClient {
        SweClient(runner: FakeRunner(.init(status: 0, stdout: try fixture(fixtureName), stderr: "")))
    }

    @Test func overviewDecodesEnvAndPins() async throws {
        let overview = try await client("swe-gh").ghOverview()
        #expect(overview.github.status == .ok)
        #expect(overview.github.login == "c-wenlong")
        #expect(overview.repos.map(\.name) == ["c-wenlong/quiver"])
        #expect(overview.repos[0].addedAt == Date(timeIntervalSince1970: 1_792_528_202))
    }

    @Test func pullRequestsDecodeFullRows() async throws {
        let reply = try await client("swe-gh-prs").ghPullRequests(repo: "c-wenlong/quiver")
        #expect(reply.github.status == .ok)
        let entry = try #require(reply.results.first)
        #expect(entry.repo == "c-wenlong/quiver")
        #expect(entry.status == .ok)
        let pr = try #require(entry.pullRequests?.first)
        #expect(pr.number == 459)
        #expect(pr.state == .open)
        #expect(pr.draft == false)
        #expect(pr.author == "c-wenlong")
        #expect(pr.branch == "feat/search-index-scan")
        #expect(pr.base == "main")
        #expect(pr.mergeable == .mergeable)
        #expect(pr.reviewDecision == GhPr.Review.none)
        #expect(pr.checks == .passing)
        // ISO 8601 timestamps land as Dates.
        #expect(pr.updatedAt.date != nil)
        #expect(pr.url.hasPrefix("https://github.com/c-wenlong/quiver/pull/"))
        // The draft row in the fixture marks `draft: true`.
        #expect(entry.pullRequests?.contains { $0.draft } == true)
    }

    @Test func repoListAndPrStatusDecode() async throws {
        let pins = try await client("swe-gh-repos").ghRepoList()
        #expect(pins.repos.map(\.name) == ["c-wenlong/quiver"])

        let status = try await client("swe-gh-pr-status").ghPrStatus(repo: "c-wenlong/quiver")
        let entry = try #require(status.results.first)
        #expect(entry.currentBranch == nil)
        #expect(entry.authored?.first?.number == 459)
        #expect(entry.reviewRequested?.first?.number == 106)
    }

    @Test func issuesDecodeLabelsAndCounts() async throws {
        let reply = try await client("swe-gh-issues").ghIssues(repo: "c-wenlong/quiver")
        let issue = try #require(reply.results.first?.issues?.first)
        #expect(issue.number == 378)
        #expect(issue.state == .open)
        #expect(issue.labels == ["bug"])
        #expect(issue.assignees == [])
        #expect(issue.updatedAt.date != nil)
    }

    @Test func runsDecodeInProgressAndConclusion() async throws {
        let reply = try await client("swe-gh-runs").ghRuns(repo: "c-wenlong/quiver")
        let run = try #require(reply.results.first?.runs?.first)
        #expect(run.id == 39_496_880_542)
        #expect(run.workflow == "CI")
        #expect(run.status == "in_progress")
        #expect(run.conclusion == nil)
        #expect(run.createdAt.date != nil)
    }

    @Test func pagesDecodeSiteAndNullBuild() async throws {
        let reply = try await client("swe-gh-pages").ghPages(repo: "c-wenlong/quiver")
        let entry = try #require(reply.results.first)
        #expect(entry.status == .ok)
        let pages = try #require(entry.pages)
        #expect(pages.url == "https://c-wenlong.github.io/quiver/")
        #expect(pages.sourceBranch == "main")
        #expect(pages.latestBuild == nil)
    }

    @Test func releasesTagsBranchesDecode() async throws {
        let releases = try await client("swe-gh-releases").ghReleases(repo: "c-wenlong/quiver")
        let release = try #require(releases.results.first?.releases?.first)
        #expect(release.tag == "v1.0.0")
        #expect(release.latest == true)
        #expect(release.publishedAt?.date != nil)

        let tags = try await client("swe-gh-tags").ghTags(repo: "c-wenlong/quiver")
        #expect(tags.results.first?.tags?.first?.name == "v1.0.0")
        #expect(tags.results.first?.tags?.first?.sha == "c338858")

        let branches = try await client("swe-gh-branches").ghBranches(repo: "c-wenlong/quiver")
        let entry = try #require(branches.results.first)
        #expect(entry.defaultBranch == "main")
        // `main` sorts past the fixture's first page, so no row is
        // flagged default; the name/url fields still decode.
        #expect(entry.branches?.first?.name == "app-provider-visibility")
        #expect(entry.branches?.first?.url.hasPrefix("https://github.com/") == true)
    }

    @Test func inboxDecodesNotifications() async throws {
        let inbox = try await client("swe-gh-inbox").ghInbox()
        #expect(inbox.github.status == .ok)
        #expect(inbox.status == "ok")
        let note = try #require(inbox.notifications.first)
        #expect(note.repo == "octocat/sources")
        #expect(note.type == "CheckSuite")
        #expect(note.reason == "ci_activity")
        #expect(note.unread == true)
        #expect(note.updatedAt.date != nil)
        // The API subject URL was rewritten to a github.com page.
        #expect(note.url.hasPrefix("https://github.com/"))
    }

    @Test func repoSearchDecodesHits() async throws {
        let reply = try await client("swe-gh-repo-search").ghSearchRepos("quiver")
        #expect(reply.query == "quiver")
        let hit = try #require(reply.results.first)
        #expect(hit.name == "keplr-io/quiver")
        #expect(hit.stars == 1784)
        #expect(hit.updatedAt.date != nil)
    }

    @Test func ghCommandsPassExpectedArguments() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("swe-gh-prs"), stderr: ""))
        let swe = SweClient(runner: runner)
        _ = try await swe.ghPullRequests(repo: "o/r", state: "merged", limit: 10)
        #expect(runner.calls.last == ["gh", "pr", "--state=merged", "--limit=10", "--repo=o/r", "--json"])
    }
}

/// The model over canned replies: pins drive the board map, per-repo
/// entry failures land on that surface only, mutations follow swe's
/// returned pin list.
@MainActor
@Suite struct GitHubModelTests {
    /// A runner that answers by argv suffix so one fake can serve the
    /// overview, a surface and the inbox in one test.
    final class Router: CommandRunning, @unchecked Sendable {
        var replies: [String: CommandResult] = [:]
        private(set) var calls: [[String]] = []
        init(_ replies: [String: CommandResult]) { self.replies = replies }
        func run(_ arguments: [String]) async throws -> CommandResult {
            calls.append(arguments)
            for (suffix, reply) in replies where arguments.joined(separator: " ").hasSuffix(suffix) {
                return reply
            }
            return .init(status: 1, stdout: Data(), stderr: "no route")
        }
        func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
            try await run(arguments)
        }
    }

    private func envelope(_ data: String) -> Data {
        Data("{\"schema\":1,\"data\":{\(data)}}".utf8)
    }

    private func router(_ pairs: [String: String]) -> Router {
        Router(pairs.mapValues { .init(status: 0, stdout: envelope($0), stderr: "") })
    }

    private let env = "\"github\":{\"status\":\"ok\",\"login\":\"me\",\"error\":null}"

    @Test func overviewLoadsPinsAndSelectsFirst() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100},{\"name\":\"o/b\",\"added_at\":200}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        #expect(model.probed)
        #expect(model.pins.map(\.name) == ["o/a", "o/b"])
        #expect(model.selected == "o/a")
    }

    @Test func surfaceRowsLandOnTheRepoBoard() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":["
                + "{\"number\":7,\"title\":\"Fix\",\"state\":\"open\",\"draft\":false,"
                + "\"author\":\"me\",\"branch\":\"f\",\"base\":\"main\",\"labels\":[],"
                + "\"mergeable\":\"mergeable\",\"review_decision\":\"approved\",\"checks\":\"passing\","
                + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/7\"}]}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadIfStale(swe: swe)
        guard case .loaded(let prs) = model.board(for: "o/a").pulls else {
            Issue.record("pulls not loaded")
            return
        }
        #expect(prs.first?.number == 7)
        #expect(prs.first?.reviewDecision == .approved)
    }

    @Test func repoLevelFailureMarksOnlyThatSurface() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"upstream-error\",\"error\":\"gh timed out\"}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadIfStale(swe: swe)
        guard case .failed(let message) = model.board(for: "o/a").pulls else {
            Issue.record("pulls should be failed")
            return
        }
        #expect(message == "gh timed out")
        // Other surfaces stay idle rather than inheriting the failure.
        if case .idle = model.board(for: "o/a").issues {} else {
            Issue.record("issues should still be idle")
        }
    }

    @Test func staleSurfaceRefetchesAfterInterval() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "--repo=o/a --json": "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":[]}]",
        ])
        var now = Date(timeIntervalSince1970: 1_000)
        let model = GitHubModel(now: { now }, staleAfter: 60)
        await model.load(swe: SweClient(runner: calls))
        await model.loadIfStale(swe: SweClient(runner: calls))
        let first = calls.calls.count
        // Inside the stale window: no refetch.
        now = now.addingTimeInterval(30)
        await model.loadIfStale(swe: SweClient(runner: calls))
        #expect(calls.calls.count == first)
        // Past it: the visible tab refetches.
        now = now.addingTimeInterval(31)
        await model.loadIfStale(swe: SweClient(runner: calls))
        #expect(calls.calls.count == first + 1)
    }

    @Test func addAndRemoveTrackSwePinList() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh repo add o/b --json": "\"repo\":\"o/b\",\"pinned\":[\"o/a\",\"o/b\"]",
            "gh repo remove o/a --json": "\"removed\":true,\"repo\":\"o/a\",\"pinned\":[\"o/b\"]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        let added = await model.addRepo("o/b", swe: swe)
        #expect(added == "o/b")
        #expect(model.pins.map(\.name) == ["o/a", "o/b"])
        let removed = await model.removeRepo("o/a", swe: swe)
        #expect(removed)
        #expect(model.pins.map(\.name) == ["o/b"])
        #expect(model.selected == "o/b")
    }

    /// Opening Inbox first marks `probed` without fetching pins; the
    /// Repositories page must still load them.
    @Test func inboxFirstStillLoadsPinsLater() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh inbox --limit=50 --all --json": "\(env),\"status\":\"ok\",\"notifications\":[]",
        ])
        let model = GitHubModel()
        let swe = SweClient(runner: calls)
        await model.loadInbox(swe: swe)
        #expect(model.probed)
        #expect(model.pins.isEmpty)
        await model.loadIfStale(swe: swe)
        #expect(model.pins.map(\.name) == ["o/a"])
        #expect(model.selected == "o/a")
    }

    /// A failed overview leaves the pin list unloaded; refresh retries it.
    @Test func failedOverviewRetriesOnRefresh() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
        ])
        calls.replies["gh --json"] = .init(
            status: 1, stdout: Data(), stderr: "swe exploded")
        let model = GitHubModel()
        let swe = SweClient(runner: calls)
        await model.load(swe: swe)
        #expect(model.probeError != nil)
        #expect(model.pins.isEmpty)
        calls.replies["gh --json"] = .init(
            status: 0,
            stdout: envelope("\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]"),
            stderr: "")
        await model.refresh(swe: swe)
        #expect(model.pins.map(\.name) == ["o/a"])
    }

    /// A cancelled first inbox read must release `.loading`, or every
    /// later poll (even a forced refresh) early-returns on the guard.
    @Test func cancelledInboxReadDoesNotStickLoading() async {
        final class Hanging: CommandRunning, @unchecked Sendable {
            func run(_ arguments: [String]) async throws -> CommandResult {
                try await Task.sleep(for: .seconds(60))
                return .init(status: 0, stdout: Data(), stderr: "")
            }
        }
        let model = GitHubModel()
        let task = Task { await model.loadInbox(swe: SweClient(runner: Hanging())) }
        try? await Task.sleep(for: .milliseconds(50))
        task.cancel()
        await task.value
        if case .loading = model.inbox { Issue.record("inbox stuck on .loading") }
    }

    @Test func envBlockedOnlyForCertainStates() async {
        let swe = SweClient(runner: router([
            "gh --json": "\"github\":{\"status\":\"auth-required\",\"login\":null,\"error\":\"not authenticated\"},\"repos\":[]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        #expect(model.envBlocked?.contains("gh auth login") == true)
    }

    @Test func inboxLoadsNotificationsAndPolls() async {
        let swe = SweClient(runner: router([
            "gh inbox --limit=50 --all --json":
                "\(env),\"status\":\"ok\",\"notifications\":[{"
                + "\"id\":\"n1\",\"repo\":\"o/a\",\"title\":\"PR #7\",\"type\":\"PullRequest\","
                + "\"reason\":\"review_requested\",\"unread\":true,"
                + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/7\"}]",
        ]))
        var now = Date(timeIntervalSince1970: 1_000)
        let model = GitHubModel(now: { now }, staleAfter: 60)
        await model.loadInbox(swe: swe)
        guard case .loaded(let notes) = model.inbox else {
            Issue.record("inbox not loaded")
            return
        }
        #expect(notes.first?.id == "n1")
        #expect(model.inboxFetchedAt == Date(timeIntervalSince1970: 1_000))
        // Fresh: a non-forced reload is a no-op.
        now = now.addingTimeInterval(30)
        await model.loadInbox(swe: swe)
        // Forced always refetches.
        await model.loadInbox(swe: swe, force: true)
        guard case .loaded(let again) = model.inbox else {
            Issue.record("inbox failed after forced reload")
            return
        }
        #expect(again.count == 1)
    }
}
