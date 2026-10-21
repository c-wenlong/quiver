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
        let catalog = try #require(overview.catalog)
        #expect(catalog.repos == 128)
        #expect(catalog.owners == ["c-wenlong", "test-bot"])
        #expect(catalog.syncedAt?.date != nil)
    }

    @Test func syncDecodesSummaryAndFailures() async throws {
        let reply = try await client("swe-gh-sync").ghSync()
        #expect(reply.repos == 128)
        #expect(reply.owners == ["c-wenlong", "test-bot"])
        #expect(reply.syncedAt?.date != nil)
        #expect(reply.failed.map(\.owner) == ["dead-org"])
    }

    @Test func worktreeDecodesPathAndCreated() async throws {
        let reply = try await client("swe-gh-worktree").ghPrWorktree(repo: "c-wenlong/quiver", pr: 459)
        #expect(reply.repo == "c-wenlong/quiver")
        #expect(reply.pr == 459)
        #expect(reply.path == "/Users/k/.quiver/worktrees/c-wenlong__quiver-pr459")
        #expect(reply.head == "a9463720b1c2")
        #expect(reply.created)
        #expect(reply.error == nil)
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

    @Test func prViewDecodesTheDetailRow() async throws {
        let reply = try await client("swe-gh-pr-view").ghPrView(repo: "c-wenlong/quiver", ref: "472")
        #expect(reply.github.status == .ok)
        #expect(reply.status == "ok")
        let pr = try #require(reply.pr)
        #expect(pr.number == 472)
        #expect(pr.mergeState == "clean")
        #expect(pr.additions == 44)
        #expect(pr.changedFiles == 2)
        // CheckRun and StatusContext rows land in one vocabulary.
        #expect(pr.checkRuns[0].state == "passing")
        #expect(pr.checkRuns[1].status == "queued")
        #expect(pr.checkRuns[1].state == "running")
        #expect(pr.checkRuns[1].conclusion == nil)
        #expect(pr.reviews.first?.author == "greptile-apps[bot]")
        #expect(pr.reviewRequests == ["devin-ai-integration[bot]"])
        #expect(pr.autoMerge == nil)
    }

    @Test func writeCommandsPassExpectedArguments() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("swe-gh-pr-view"), stderr: ""))
        let swe = SweClient(runner: runner)
        _ = try await swe.ghPrView(repo: "o/r", ref: "7")
        #expect(runner.calls.last == ["gh", "pr", "view", "7", "--repo=o/r", "--json"])
        _ = try? await swe.ghPrMerge(repo: "o/r", ref: "7", method: "rebase", auto: true)
        #expect(runner.calls.last == ["gh", "pr", "merge", "7", "--repo=o/r", "--method=rebase", "--auto", "--json"])
        _ = try? await swe.ghRunRerun(repo: "o/r", run: 42, failedOnly: true)
        #expect(runner.calls.last == ["gh", "run", "rerun", "42", "--repo=o/r", "--failed", "--json"])
        _ = try? await swe.ghInboxRead(ids: ["1", "2"])
        #expect(runner.calls.last == ["gh", "inbox", "read", "1", "2", "--json"])
        _ = try? await swe.ghInboxRead(all: true)
        #expect(runner.calls.last == ["gh", "inbox", "read", "--all", "--json"])
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

    /// Reorder applies the new order immediately, then the CLI reply —
    /// the store's `pinned` — is authoritative even when it disagrees
    /// with the request. A failed call restores the previous order.
    @Test func reorderPersistsAndRestoresOnFailure() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":["
                + "{\"name\":\"o/a\",\"added_at\":100},"
                + "{\"name\":\"o/b\",\"added_at\":200},"
                + "{\"name\":\"o/c\",\"added_at\":300}]",
            // The store lands a different order than asked — its reply,
            // not the request, is what the model keeps.
            "gh repo reorder o/c o/a o/b --json":
                "\"pinned\":[\"o/c\",\"o/b\",\"o/a\"]",
        ])
        let model = GitHubModel()
        let swe = SweClient(runner: calls)
        await model.load(swe: swe)
        #expect(model.pins.map(\.name) == ["o/a", "o/b", "o/c"])

        await model.reorderPins(["o/c", "o/a", "o/b"], swe: swe)
        #expect(model.pins.map(\.name) == ["o/c", "o/b", "o/a"])

        calls.replies["gh repo reorder o/b o/a o/c --json"] = .init(
            status: 1, stdout: Data(), stderr: "disk full")
        await model.reorderPins(["o/b", "o/a", "o/c"], swe: swe)
        #expect(model.pins.map(\.name) == ["o/c", "o/b", "o/a"])
        #expect(model.pinError != nil)
    }

    /// A drop while the first reorder's save is in flight used to be
    /// discarded: the optimistic order showed but nothing persisted it.
    /// The second order queues behind the first and lands when it settles.
    @Test func reorderDropDuringSaveQueues() async {
        final class Gate: CommandRunning, @unchecked Sendable {
            var replies: [String: CommandResult] = [:]
            private(set) var calls: [[String]] = []
            private var hold: CheckedContinuation<Void, Never>?
            private var resumed = false
            func run(_ arguments: [String]) async throws -> CommandResult {
                calls.append(arguments)
                let joined = arguments.joined(separator: " ")
                if joined.contains("repo reorder o/c"), !resumed {
                    await withCheckedContinuation { hold = $0 }
                }
                for (suffix, reply) in replies where joined.hasSuffix(suffix) {
                    return reply
                }
                return .init(status: 1, stdout: Data(), stderr: "no route")
            }
            func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
                try await run(arguments)
            }
            func resume() { resumed = true; hold?.resume(); hold = nil }
        }
        let gate = Gate()
        gate.replies["gh --json"] = .init(status: 0, stdout: envelope(
            "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100},"
            + "{\"name\":\"o/b\",\"added_at\":200},"
            + "{\"name\":\"o/c\",\"added_at\":300}]"), stderr: "")
        gate.replies["gh repo reorder o/c o/a o/b --json"] = .init(
            status: 0, stdout: envelope("\"pinned\":[\"o/c\",\"o/a\",\"o/b\"]"), stderr: "")
        gate.replies["gh repo reorder o/b o/c o/a --json"] = .init(
            status: 0, stdout: envelope("\"pinned\":[\"o/b\",\"o/c\",\"o/a\"]"), stderr: "")
        let model = GitHubModel()
        let swe = SweClient(runner: gate)
        await model.load(swe: swe)

        let first = Task { await model.reorderPins(["o/c", "o/a", "o/b"], swe: swe) }
        try? await Task.sleep(for: .milliseconds(50))
        await model.reorderPins(["o/b", "o/c", "o/a"], swe: swe)
        // The second drop shows immediately even though its save waits.
        #expect(model.pins.map(\.name) == ["o/b", "o/c", "o/a"])
        gate.resume()
        await first.value

        let reorders = gate.calls.map { $0.joined(separator: " ") }
            .filter { $0.contains("repo reorder") }
        #expect(reorders.count == 2)
        #expect(reorders.last?.contains("o/b o/c o/a") == true)
        #expect(model.pins.map(\.name) == ["o/b", "o/c", "o/a"])
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

    // MARK: - warm-start snapshot

    private func tempSnapshot() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "gh-test-\(UUID().uuidString).json")
    }

    private let prRows = "[{\"number\":7,\"title\":\"Fix\",\"state\":\"open\",\"draft\":false,"
        + "\"author\":\"me\",\"branch\":\"f\",\"base\":\"main\",\"labels\":[],"
        + "\"mergeable\":\"mergeable\",\"review_decision\":\"approved\",\"checks\":\"passing\","
        + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/7\"}]"

    /// A quit-and-relaunch shows the last rows before any fetch lands —
    /// pins, selection and boards included, so the page opens on its last
    /// view instead of a probe spinner.
    @Test func snapshotRestoresBoardsAndInbox() async {
        let file = tempSnapshot()
        defer { try? FileManager.default.removeItem(at: file) }
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":\(prRows)}]",
            "gh inbox --limit=50 --all --json":
                "\(env),\"status\":\"ok\",\"notifications\":[{"
                + "\"id\":\"n1\",\"repo\":\"o/a\",\"title\":\"PR #7\",\"type\":\"PullRequest\","
                + "\"reason\":\"review_requested\",\"unread\":true,"
                + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/7\"}]",
        ]))
        let first = GitHubModel(snapshotFile: file)
        await first.load(swe: swe)
        await first.loadTab(.pulls, repo: "o/a", swe: swe)
        await first.loadInbox(swe: swe)
        first.saveNow()

        let second = GitHubModel(snapshotFile: file)
        #expect(second.pins.map(\.name) == ["o/a"])
        #expect(second.selected == "o/a")
        // Restored state counts as probed: the page renders rows, not
        // the "Checking gh…" spinner, while the first-look refetch runs.
        #expect(second.probed)
        guard case .loaded(let prs) = second.board(for: "o/a").pulls else {
            Issue.record("restored board should hold pulls")
            return
        }
        #expect(prs.first?.number == 7)
        guard case .loaded(let notes) = second.inbox else {
            Issue.record("restored inbox should hold notifications")
            return
        }
        #expect(notes.first?.id == "n1")
    }

    private func boardSurfaces(repo: String, pulls: String) -> String {
        "\"pull_requests\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"pull_requests\":\(pulls)},"
            + "\"issues\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"issues\":[]},"
            + "\"runs\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"runs\":[]},"
            + "\"pages\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"pages\":null},"
            + "\"releases\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"releases\":[]},"
            + "\"tags\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"tags\":[]},"
            + "\"branches\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"branches\":[]}"
    }

    /// First look fills all seven surfaces with one `swe gh board` call;
    /// afterwards the visible tab alone owns staleness.
    @Test func firstLookHydratesAllSurfacesViaBoard() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh board --repo=o/a --json":
                "\(env),\"boards\":[{\"repo\":\"o/a\",\"surfaces\":{"
                + boardSurfaces(repo: "o/a", pulls: prRows) + "}}]",
        ])
        let swe = SweClient(runner: calls)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadIfStale(swe: swe)
        let board = model.board(for: "o/a")
        guard case .loaded(let prs) = board.pulls else {
            Issue.record("pulls not loaded: \(board.pulls) calls=\(calls.calls)")
            return
        }
        #expect(prs.first?.number == 7)
        if case .loaded = board.issues {} else { Issue.record("issues not loaded") }
        if case .loaded = board.runs {} else { Issue.record("runs not loaded") }
        if case .loaded = board.pages {} else { Issue.record("pages not loaded") }
        if case .loaded = board.releases {} else { Issue.record("releases not loaded") }
        if case .loaded = board.tags {} else { Issue.record("tags not loaded") }
        if case .loaded = board.branches {} else { Issue.record("branches not loaded") }
        // Overview + board: the whole board cost two calls, not eight —
        // and the hydrated visible tab is fresh, so a second look is free.
        #expect(calls.calls.count == 2)
        await model.loadIfStale(swe: swe)
        #expect(calls.calls.count == 2)
    }

    /// A surface the board reports failed refetches on its tab's next
    /// visit through the per-tab path.
    @Test func boardFailureDegradesToPerTabFetch() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh board --repo=o/a --json":
                "\(env),\"boards\":[{\"repo\":\"o/a\",\"surfaces\":{"
                + "\"pull_requests\":{\"repo\":\"o/a\",\"status\":\"upstream-error\",\"error\":\"gh timed out\"},"
                + "\"issues\":{\"repo\":\"o/a\",\"status\":\"ok\",\"issues\":[]},"
                + "\"runs\":{\"repo\":\"o/a\",\"status\":\"ok\",\"runs\":[]},"
                + "\"pages\":{\"repo\":\"o/a\",\"status\":\"ok\",\"pages\":null},"
                + "\"releases\":{\"repo\":\"o/a\",\"status\":\"ok\",\"releases\":[]},"
                + "\"tags\":{\"repo\":\"o/a\",\"status\":\"ok\",\"tags\":[]},"
                + "\"branches\":{\"repo\":\"o/a\",\"status\":\"ok\",\"branches\":[]}}}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":\(prRows)}]",
        ])
        let swe = SweClient(runner: calls)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadIfStale(swe: swe)
        guard case .loaded(let prs) = model.board(for: "o/a").pulls else {
            Issue.record("pulls should be refetched per-tab")
            return
        }
        #expect(prs.first?.number == 7)
        // The other surfaces came from the board and did not refetch.
        if case .loaded = model.board(for: "o/a").issues {} else {
            Issue.record("issues should be board-loaded")
        }
        #expect(calls.calls.count == 3)
    }

    /// Hydrated rows are warm for display but cold for fetching: no
    /// fetch times survive a relaunch, so the first look always
    /// refetches — a restart can never sit inside a stale window.
    @Test func restoredRowsRefetchOnFirstLook() async {
        let file = tempSnapshot()
        defer { try? FileManager.default.removeItem(at: file) }
        var board = RepoBoard()
        board.issues = .loaded([])
        let snap = GitHubSnapshot(
            savedAt: Date(timeIntervalSince1970: 10_000),
            pins: [PinnedRepo(name: "o/a", addedAt: Date(timeIntervalSince1970: 100))],
            selected: "o/a", boards: ["o/a": board], inbox: [],
            inboxLoaded: false)
        try? JSONEncoder().encode(snap).write(to: file)

        // Seconds after the snapshot: a cold-start file must not skip
        // the refresh even inside staleAfter.
        let now = Date(timeIntervalSince1970: 10_030)
        let model = GitHubModel(now: { now }, staleAfter: 60, snapshotFile: file)
        #expect(model.selected == "o/a")
        model.tab = .issues
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "--repo=o/a --json":
                "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"issues\":[]}]",
        ])
        await model.loadIfStale(swe: SweClient(runner: calls))
        // The overview reloads pins (pinsFetchedAt is not restored), the
        // session's first board hydrate is attempted (its reply decodes
        // as the wrong shape and degrades), and the visible tab refetches
        // despite rows being there.
        #expect(calls.calls.count == 3)
        #expect(calls.calls.last?.contains("--repo=o/a") == true)
    }

    /// A refresh that fails after a good read must not overwrite the
    /// snapshot's rows: relaunch restores the last good inbox.
    @Test func failedRefreshKeepsLastGoodInbox() async {
        let file = tempSnapshot()
        defer { try? FileManager.default.removeItem(at: file) }
        let replies = router([
            "gh inbox --limit=50 --all --json":
                "\(env),\"status\":\"ok\",\"notifications\":[{"
                + "\"id\":\"n1\",\"repo\":\"o/a\",\"title\":\"PR #7\",\"type\":\"PullRequest\","
                + "\"reason\":\"review_requested\",\"unread\":true,"
                + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/7\"}]",
        ])
        let swe = SweClient(runner: replies)
        let first = GitHubModel(snapshotFile: file)
        await first.loadInbox(swe: swe)
        // The next poll fails: the model goes .failed but the snapshot
        // keeps the rows it had.
        replies.replies["gh inbox --limit=50 --all --json"] =
            .init(status: 1, stdout: Data(), stderr: "gh timed out")
        await first.loadInbox(swe: swe, force: true)
        if case .failed = first.inbox {} else {
            Issue.record("inbox should be failed after the bad reply")
        }
        first.saveNow()

        let second = GitHubModel(snapshotFile: file)
        guard case .loaded(let notes) = second.inbox else {
            Issue.record("restored inbox should hold the last good rows")
            return
        }
        #expect(notes.first?.id == "n1")
    }

    /// An overview load that never touched the inbox must not restore
    /// it as `.loaded([])` — the page would flash "No unread
    /// notifications" while its first request was still running.
    @Test func neverFetchedInboxRestoresIdle() async {
        let file = tempSnapshot()
        defer { try? FileManager.default.removeItem(at: file) }
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
        ]))
        let first = GitHubModel(snapshotFile: file)
        await first.load(swe: swe)
        first.saveNow()

        let second = GitHubModel(snapshotFile: file)
        if case .idle = second.inbox {} else {
            Issue.record("inbox that never loaded should restore as .idle")
        }
    }

    @Test func corruptSnapshotIsAColdStart() {
        let file = tempSnapshot()
        defer { try? FileManager.default.removeItem(at: file) }
        try? Data("not json".utf8).write(to: file)
        let model = GitHubModel(snapshotFile: file)
        #expect(model.boards.isEmpty)
        if case .idle = model.inbox {} else {
            Issue.record("inbox should be idle after a corrupt snapshot")
        }
    }

    /// `.loading` has no reply coming after a quit; it must restore as
    /// `.idle`, not a stuck spinner.
    @Test func loadingSurfaceRestoresAsIdle() throws {
        var board = RepoBoard()
        board.pulls = .loading
        let data = try JSONEncoder().encode(board)
        let decoded = try JSONDecoder().decode(RepoBoard.self, from: data)
        if case .idle = decoded.pulls {} else {
            Issue.record(".loading must decode as .idle")
        }
    }

    /// `invalidate` drops the repo's fetch times; with that repo off
    /// screen nothing refetches until it is looked at again — inside the
    /// stale window included.
    @Test func invalidateDropsFetchTimesForOffscreenRepo() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100},{\"name\":\"o/b\",\"added_at\":200}]",
            "gh board --repo=o/b --json":
                "\(env),\"boards\":[{\"repo\":\"o/b\",\"surfaces\":{"
                + boardSurfaces(repo: "o/b", pulls: "[]") + "}}]",
            "gh pr --state=open --limit=50 --repo=o/b --json":
                "\(env),\"results\":[{\"repo\":\"o/b\",\"status\":\"ok\",\"pull_requests\":[]}]",
        ])
        let swe = SweClient(runner: calls)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadTab(.pulls, repo: "o/b", swe: swe)
        let filled = calls.calls.count
        // o/b is not selected: no immediate reload.
        model.invalidate(repo: "o/b", swe: swe)
        try? await Task.sleep(for: .milliseconds(50))
        #expect(calls.calls.count == filled)
        // But its times are gone: looking at it refetches despite the
        // stale window.
        model.selected = "o/b"
        await model.loadIfStale(swe: swe)
        #expect(calls.calls.count == filled + 1)
    }

    /// With the notified repo on screen, `invalidate` refreshes the
    /// visible tab immediately so the row moves with the banner.
    @Test func invalidateRefreshesTheSelectedRepoNow() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "--repo=o/a --json": "\(env),\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":[]}]",
        ])
        let swe = SweClient(runner: calls)
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadIfStale(swe: swe)
        let filled = calls.calls.count
        model.invalidate(repo: "o/a", swe: swe)
        for _ in 0..<200 where calls.calls.count == filled {
            try? await Task.sleep(for: .milliseconds(10))
        }
        #expect(calls.calls.count == filled + 1)
    }

    /// `invalidate` during an in-flight fetch must not drop the refresh:
    /// the in-flight reply predates the notification, so the surface
    /// fetches again after it lands.
    @Test func invalidateQueuesBehindAnInFlightFetch() async {
        /// First `--repo` call holds until released; later calls return
        /// immediately.
        final class Gated: CommandRunning, @unchecked Sendable {
            private(set) var calls: [[String]] = []
            var released = false
            private var held = 0
            func run(_ arguments: [String]) async throws -> CommandResult {
                calls.append(arguments)
                if arguments.joined(separator: " ").contains("--repo=") {
                    held += 1
                    if held == 1 {
                        while !released { try? await Task.sleep(for: .milliseconds(5)) }
                    }
                }
                let payload = "{\"schema\":1,\"data\":{\"github\":{\"status\":\"ok\","
                    + "\"login\":\"me\",\"error\":null},"
                    + "\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\",\"pull_requests\":[]}]}}"
                return .init(status: 0, stdout: Data(payload.utf8), stderr: "")
            }
            func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
                try await run(arguments)
            }
        }
        let calls = Gated()
        let swe = SweClient(runner: calls)
        let model = GitHubModel()
        model.selected = "o/a"
        let inFlight = Task { await model.loadTab(.pulls, repo: "o/a", swe: swe) }
        for _ in 0..<200 where calls.calls.isEmpty {
            try? await Task.sleep(for: .milliseconds(10))
        }
        model.invalidate(repo: "o/a", swe: swe)
        calls.released = true
        await inFlight.value
        for _ in 0..<200 where calls.calls.count < 2 {
            try? await Task.sleep(for: .milliseconds(10))
        }
        #expect(calls.calls.count == 2)
    }

    /// The badge count follows the loaded rows: only `unread` lines
    /// count, and `.idle`/`.failed` read as zero rather than blocking.
    @Test func unreadCountTracksLoadedInbox() async {
        let calls = router([
            "gh inbox --limit=50 --all --json":
                "\(env),\"status\":\"ok\",\"notifications\":["
                + "{\"id\":\"n1\",\"repo\":\"o/a\",\"title\":\"a\",\"type\":\"PullRequest\","
                + "\"reason\":\"mention\",\"unread\":true,"
                + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/1\"},"
                + "{\"id\":\"n2\",\"repo\":\"o/a\",\"title\":\"b\",\"type\":\"Issue\","
                + "\"reason\":\"comment\",\"unread\":false,"
                + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/issues/2\"}]",
        ])
        let model = GitHubModel()
        #expect(model.unreadCount == 0)
        await model.loadInbox(swe: SweClient(runner: calls))
        #expect(model.unreadCount == 1)
    }

    // MARK: - filter

    /// A query matching a repo name narrows the sections to that repo;
    /// empty and whitespace-only filters show every pin.
    @Test func filterNarrowsReposByName() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/alpha\",\"added_at\":100},{\"name\":\"o/beta\",\"added_at\":200}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        #expect(model.visibleRepos.map(\.name) == ["o/alpha", "o/beta"])
        model.filter = "  "
        #expect(model.visibleRepos.map(\.name) == ["o/alpha", "o/beta"])
        model.filter = "BETA"
        #expect(model.visibleRepos.map(\.name) == ["o/beta"])
        #expect(model.showsAllRows("o/beta"))
    }

    /// A query matching no repo name is a pure row search: every
    /// section stays up so a PR title or branch anywhere can match.
    @Test func filterWithNoRepoMatchKeepsEverySection() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/alpha\",\"added_at\":100},{\"name\":\"o/beta\",\"added_at\":200}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        model.filter = "fix the crash"
        #expect(model.visibleRepos.map(\.name) == ["o/alpha", "o/beta"])
        #expect(!model.showsAllRows("o/alpha"))
    }

    /// Row text matches case-insensitively across title, branch and
    /// number; a name-matched repo keeps every row regardless.
    @Test func rowFilterMatchesAnyField() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100},{\"name\":\"o/b\",\"added_at\":200}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        model.filter = "feat"
        // "o/b" isn't a name match under "feat", so its rows filter.
        let rows: Loadable<[String]> = .loaded(["feat/login", "main"])
        guard case .loaded(let kept) = model.filtered(rows, repo: "o/b", { [$0] }) else {
            Issue.record("rows should stay loaded")
            return
        }
        #expect(kept == ["feat/login"])
        // nil fields don't match and don't crash the scan.
        #expect(!model.matches([nil, "main"]))
        #expect(model.matches(["Fix", "FEAT/login", nil]))
        // But a repo the query names shows its whole board.
        model.filter = "o/b"
        guard case .loaded(let all) = model.filtered(rows, repo: "o/b", { [$0] }) else {
            Issue.record("rows should stay loaded")
            return
        }
        #expect(all == ["feat/login", "main"])
    }

    /// `hasRows` mirrors `filtered`'s emptiness for callers that only
    /// need presence: name-matched repos check row count, fallback
    /// repos check for any match, and unloaded surfaces read as
    /// non-empty so their state card still shows.
    @Test func hasRowsMirrorsFilteredEmptiness() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)

        let rows: Loadable<[String]> = .loaded(["feat/login", "main"])
        let empty: Loadable<[String]> = .loaded([])
        // No query: presence is just "are there rows".
        #expect(model.hasRows(rows, repo: "o/a") { [$0] })
        #expect(!model.hasRows(empty, repo: "o/a") { [$0] })
        // Idle/failed surfaces are not "empty": their cards must show.
        #expect(model.hasRows(Loadable<[String]>.idle, repo: "o/a") { [$0] })
        #expect(model.hasRows(.failed("nope"), repo: "o/a") { [$0] })

        // A fallback row search: any matching field keeps the card.
        model.filter = "login"
        #expect(model.hasRows(rows, repo: "o/a") { [$0] })
        model.filter = "zzz"
        #expect(!model.hasRows(rows, repo: "o/a") { [$0] })
        // A name-matched repo ignores the filter on its rows.
        model.filter = "o/a"
        #expect(model.hasRows(rows, repo: "o/a") { [$0] })
        #expect(!model.hasRows(empty, repo: "o/a") { [$0] })
    }

    /// Filtering is pure: reading `visibleRepos`/`filtered` issues no
    /// `swe` calls, so typing in the box never searches GitHub.
    @Test func filteringIssuesNoRemoteCalls() async {
        let calls = router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
        ])
        let swe = SweClient(runner: calls)
        let model = GitHubModel()
        await model.load(swe: swe)
        let filled = calls.calls.count
        model.filter = "anything"
        _ = model.visibleRepos
        _ = model.filtered(Loadable<[String]>.loaded(["x"]), repo: "o/a") { [$0] }
        // trailing closure fine here: not inside a guard condition
        _ = model.matches(["x"])
        #expect(calls.calls.count == filled)
    }

    /// `load` picks up the catalog the overview reports; `syncGitHub`
    /// replaces it with the resync's totals and clears the warning.
    @Test func syncFillsCatalogAndClearsWarning() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[],"
                + "\"catalog\":{\"repos\":2,\"synced_at\":1700000000,\"owners\":[\"me\"]}",
            "gh sync --json":
                "\"repos\":5,\"synced_at\":1700000100,\"owners\":[\"me\",\"org\"],"
                + "\"failed\":[]",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        #expect(model.catalog?.repos == 2)

        await model.syncGitHub(swe: swe)
        #expect(model.catalog?.repos == 5)
        #expect(model.catalog?.owners == ["me", "org"])
        #expect(model.syncError == nil)
        #expect(!model.syncing)
    }

    /// Owners a resync couldn't list are a warning, not an error — the
    /// skipped slice keeps its old rows.
    @Test func syncSkippedOwnersSurfaceAsWarning() async {
        let swe = SweClient(runner: router([
            "gh sync --json":
                "\"repos\":3,\"synced_at\":1700000100,\"owners\":[\"me\"],"
                + "\"failed\":[{\"owner\":\"dead-org\",\"error\":\"HTTP 404\"}]",
        ]))
        let model = GitHubModel()
        await model.syncGitHub(swe: swe)
        #expect(model.catalog?.repos == 3)
        #expect(model.syncError?.contains("dead-org") == true)
    }

    /// The setup card gates on the certain states only: unavailable and
    /// auth-required show it; ok and a probe failure keep the pages up.
    @Test func envSetupCoversOnlyCertainBlocks() async {
        func load(_ status: String) async -> GitHubModel {
            let swe = SweClient(runner: router([
                "gh --json": "\"github\":{\"status\":\"\(status)\",\"login\":\"me\",\"error\":null},\"repos\":[]",
            ]))
            let model = GitHubModel()
            await model.load(swe: swe)
            return model
        }
        #expect(await load("unavailable").envSetup == .unavailable)
        #expect(await load("auth-required").envSetup == .authRequired)
        #expect(await load("ok").envSetup == nil)
        #expect(await load("upstream-error").envSetup == nil)
    }

    /// A `load` that was in flight while a sync finished must not paint
    /// the sync's fresh catalog back to the older overview numbers.
    @Test func staleOverviewDoesNotOverwriteASync() async {
        /// Holds `gh --json` until released; `gh sync` answers at once.
        final class Gated: CommandRunning, @unchecked Sendable {
            private(set) var calls = 0
            var released = false
            func run(_ arguments: [String]) async throws -> CommandResult {
                calls += 1
                let joined = arguments.joined(separator: " ")
                var payload: String
                if joined.hasSuffix("gh sync --json") {
                    payload = "\"repos\":9,\"synced_at\":1700000100,"
                        + "\"owners\":[\"me\"],\"failed\":[]"
                } else {
                    while !released { try? await Task.sleep(for: .milliseconds(5)) }
                    payload = "\"github\":{\"status\":\"ok\",\"login\":\"me\","
                        + "\"error\":null},\"repos\":[],"
                        + "\"catalog\":{\"repos\":2,\"synced_at\":1700000000,"
                        + "\"owners\":[\"me\"]}"
                }
                let out = "{\"schema\":1,\"data\":{\(payload)}}"
                return .init(status: 0, stdout: Data(out.utf8), stderr: "")
            }
            func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
                try await run(arguments)
            }
        }
        let gate = Gated()
        let swe = SweClient(runner: gate)
        let model = GitHubModel()
        let loading = Task { await model.load(swe: swe) }
        for _ in 0..<200 where gate.calls == 0 {
            try? await Task.sleep(for: .milliseconds(10))
        }
        await model.syncGitHub(swe: swe)
        #expect(model.catalog?.repos == 9)
        gate.released = true
        await loading.value
        #expect(model.catalog?.repos == 9)
    }

    // MARK: - PR detail + write actions

    private var detailPayload: String {
        env + ",\"repo\":\"o/a\",\"status\":\"ok\",\"pr\":{"
            + "\"repo\":\"o/a\",\"number\":7,\"title\":\"Fix\",\"state\":\"open\","
            + "\"draft\":false,\"author\":\"me\",\"branch\":\"f\",\"base\":\"main\","
            + "\"labels\":[],\"mergeable\":\"mergeable\",\"review_decision\":\"approved\","
            + "\"checks\":\"passing\",\"updated_at\":1700000000,"
            + "\"url\":\"https://github.com/o/a/pull/7\",\"body\":\"text\","
            + "\"merge_state\":\"clean\",\"additions\":1,\"deletions\":0,"
            + "\"changed_files\":1,\"check_runs\":[{\"name\":\"ci\",\"workflow\":\"CI\","
            + "\"status\":\"completed\",\"conclusion\":\"success\",\"state\":\"passing\","
            + "\"url\":\"https://x/1\"}],\"reviews\":[],\"review_requests\":[],"
            + "\"auto_merge\":null,\"created_at\":1700000000,\"merged_at\":null}"
    }

    @Test func prDetailLoadsIntoTheKeyedSlot() async {
        let swe = SweClient(runner: router([
            "gh pr view 7 --repo=o/a --json": detailPayload,
        ]))
        let model = GitHubModel()
        await model.loadPrDetail(repo: "o/a", number: 7, swe: swe)
        guard case .loaded(let pr) = model.prDetails["o/a#7"] else {
            Issue.record("expected a loaded detail"); return
        }
        #expect(pr.number == 7)
        #expect(pr.mergeState == "clean")
        #expect(pr.checkRuns.first?.state == "passing")
    }

    @Test func prDetailFailureLandsTyped() async {
        let swe = SweClient(runner: router([
            "gh pr view 7 --repo=o/a --json":
                env + ",\"repo\":\"o/a\",\"status\":\"upstream-error\","
                + "\"pr\":null,\"error\":\"no pull requests found\"",
        ]))
        let model = GitHubModel()
        await model.loadPrDetail(repo: "o/a", number: 7, swe: swe)
        guard case .failed(let message) = model.prDetails["o/a#7"] else {
            Issue.record("expected a failed detail"); return
        }
        #expect(message == "no pull requests found")
    }

    @Test func mergeSuccessDropsTheRowAndClearsError() async {
        let swe = SweClient(runner: router([
            "gh --json": "\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":100}]",
            "gh pr --state=open --limit=50 --repo=o/a --json":
                env + ",\"results\":[{\"repo\":\"o/a\",\"status\":\"ok\","
                + "\"pull_requests\":[{\"number\":7,\"title\":\"Fix\",\"state\":\"open\","
                + "\"draft\":false,\"author\":\"me\",\"branch\":\"f\",\"base\":\"main\","
                + "\"labels\":[],\"mergeable\":\"mergeable\",\"review_decision\":\"approved\","
                + "\"checks\":\"passing\",\"updated_at\":1700000000,"
                + "\"url\":\"https://github.com/o/a/pull/7\"}]}]",
            "gh pr merge 7 --repo=o/a --method=squash --json":
                env + ",\"repo\":\"o/a\",\"ref\":\"7\",\"method\":\"squash\","
                + "\"auto\":false,\"merged\":true",
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadTab(.pulls, repo: "o/a", swe: swe)
        let error = await model.mergePr(repo: "o/a", ref: "7",
                                        method: "squash", auto: false, swe: swe)
        #expect(error == nil)
        #expect(model.actionError == nil)
        guard case .loaded(let prs) = model.board(for: "o/a").pulls else {
            Issue.record("expected pulls to stay loaded"); return
        }
        #expect(prs.isEmpty)
    }

    @Test func mergeFailureReturnsThePayloadError() async {
        let swe = SweClient(runner: router([
            "gh pr merge 7 --repo=o/a --method=squash --json":
                env + ",\"repo\":\"o/a\",\"ref\":\"7\",\"method\":\"squash\","
                + "\"auto\":false,\"merged\":false,"
                + "\"error\":\"Pull request is not mergeable\"",
        ]))
        let model = GitHubModel()
        let error = await model.mergePr(repo: "o/a", ref: "7",
                                        method: "squash", auto: false, swe: swe)
        #expect(error == "Pull request is not mergeable")
        #expect(model.actionError == "Pull request is not mergeable")
    }

    @Test func rerunSuccessDropsTheActionsFetchTime() async {
        let swe = SweClient(runner: router([
            "gh run rerun 42 --repo=o/a --failed --json":
                env + ",\"repo\":\"o/a\",\"run\":42,\"rerun\":true,\"failed_only\":true",
        ]))
        let model = GitHubModel()
        let error = await model.rerunRun(repo: "o/a", run: 42,
                                         failedOnly: true, swe: swe)
        #expect(error == nil)
        #expect(model.actionError == nil)
    }

    @Test func markReadFlipsUnreadLocally() async {
        let inboxPayload = env + ",\"status\":\"ok\",\"notifications\":["
            + "{\"id\":\"1\",\"repo\":\"o/a\",\"title\":\"A\",\"type\":\"Issue\","
            + "\"reason\":\"mention\",\"unread\":true,\"updated_at\":1700000000,"
            + "\"url\":\"https://github.com/o/a/issues/1\"},"
            + "{\"id\":\"2\",\"repo\":\"o/a\",\"title\":\"B\",\"type\":\"PullRequest\","
            + "\"reason\":\"review_requested\",\"unread\":true,\"updated_at\":1700000000,"
            + "\"url\":\"https://github.com/o/a/pull/2\"}]"
        let swe = SweClient(runner: router([
            "gh inbox --limit=50 --all --json": inboxPayload,
            "gh inbox read 1 --json":
                env + ",\"all\":false,\"marked\":[\"1\"]",
        ]))
        let model = GitHubModel()
        await model.loadInbox(swe: swe)
        #expect(model.unreadCount == 2)
        let error = await model.markInboxRead(["1"], swe: swe)
        #expect(error == nil)
        #expect(model.unreadCount == 1)
    }

    @Test func markAllReadClearsTheInbox() async {
        let inboxPayload = env + ",\"status\":\"ok\",\"notifications\":["
            + "{\"id\":\"1\",\"repo\":\"o/a\",\"title\":\"A\",\"type\":\"Issue\","
            + "\"reason\":\"mention\",\"unread\":true,\"updated_at\":1700000000,"
            + "\"url\":\"https://github.com/o/a/issues/1\"}]"
        let swe = SweClient(runner: router([
            "gh inbox --limit=50 --all --json": inboxPayload,
            "gh inbox read --all --json":
                env + ",\"all\":true,\"marked\":[\"*\"]",
        ]))
        let model = GitHubModel()
        await model.loadInbox(swe: swe)
        let error = await model.markInboxRead([], all: true, swe: swe)
        #expect(error == nil)
        #expect(model.unreadCount == 0)
    }

    @Test func prWorktreeReturnsPathAndClearsError() async {
        let swe = SweClient(runner: router([
            "gh pr worktree 7 --repo=o/a --json":
                "\"repo\":\"o/a\",\"pr\":7,\"path\":\"/x/o__a-pr7\","
                + "\"head\":\"abc\",\"created\":true,\"error\":null",
        ]))
        let model = GitHubModel()
        let path = await model.prWorktree(repo: "o/a", pr: 7, swe: swe)
        #expect(path == "/x/o__a-pr7")
        #expect(model.sessionError == nil)
        #expect(model.worktreeBusy.isEmpty)
    }

    @Test func prWorktreeFailureSurfacesError() async {
        let swe = SweClient(runner: router([
            "gh pr worktree 9 --repo=o/a --json":
                "\"repo\":\"o/a\",\"pr\":9,\"path\":null,\"created\":false,"
                + "\"error\":\"Fetch failed: no such ref\"",
        ]))
        let model = GitHubModel()
        let path = await model.prWorktree(repo: "o/a", pr: 9, swe: swe)
        #expect(path == nil)
        #expect(model.sessionError == "Fetch failed: no such ref")
    }

    @Test func sessionHarnessesKeepInstalledOnly() async {
        let swe = SweClient(runner: router([
            "list --json": "\"harnesses\":["
                + "{\"name\":\"claude\",\"command\":\"claude\",\"description\":\"\","
                + "\"aliases\":[],\"tags\":[],\"state\":\"active\",\"installed\":true},"
                + "{\"name\":\"codex\",\"command\":\"codex\",\"description\":\"\","
                + "\"aliases\":[],\"tags\":[],\"state\":\"active\",\"installed\":false},"
                + "{\"name\":\"old\",\"command\":\"old\",\"description\":\"\","
                + "\"aliases\":[],\"tags\":[],\"state\":\"archived\",\"installed\":true}"
                + "]",
        ]))
        let model = GitHubModel()
        await model.loadSessionHarnesses(swe: swe)
        #expect(model.sessionHarnesses.map(\.name) == ["claude"])
    }
}
