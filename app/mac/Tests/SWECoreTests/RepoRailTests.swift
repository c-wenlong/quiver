import Foundation
import Testing
@testable import SWECore

/// The Repositories rail and list: the chips, the per-repo counts the
/// rail shows and folds quiet repos by, the CI dot, and a drag that
/// reorders only within its rail group.
@MainActor
@Suite struct RepoRailTests {
    private let env = "\"github\":{\"status\":\"ok\",\"login\":\"me\",\"error\":null}"

    private func pr(_ n: Int, author: String = "me", draft: Bool = false,
                    checks: String = "passing", title: String? = nil) -> String {
        "{\"number\":\(n),\"title\":\"\(title ?? "Fix \(n)")\",\"state\":\"open\",\"draft\":\(draft),"
            + "\"author\":\"\(author)\",\"branch\":\"f\(n)\",\"base\":\"main\",\"labels\":[],"
            + "\"mergeable\":\"mergeable\",\"review_decision\":\"none\",\"checks\":\"\(checks)\","
            + "\"updated_at\":\"2026-10-20T00:00:00Z\",\"url\":\"https://github.com/o/a/pull/\(n)\"}"
    }

    private func run(_ id: Int, branch: String, status: String = "completed",
                     conclusion: String?) -> String {
        "{\"id\":\(id),\"workflow\":\"CI\",\"title\":\"t\",\"branch\":\"\(branch)\",\"event\":\"push\","
            + "\"status\":\"\(status)\",\"conclusion\":\(conclusion.map { "\"\($0)\"" } ?? "null"),"
            + "\"created_at\":\"2026-10-20T00:00:00Z\",\"updated_at\":\"2026-10-20T00:00:00Z\","
            + "\"url\":\"https://github.com/o/a/actions/runs/\(id)\"}"
    }

    private func branch(_ name: String, isDefault: Bool) -> String {
        "{\"name\":\"\(name)\",\"protected\":false,\"default\":\(isDefault),\"sha\":\"abc1234\","
            + "\"url\":\"https://github.com/o/a/tree/\(name)\"}"
    }

    private func board(_ repo: String, pulls: [String], runs: [String] = [],
                       branches: [String] = []) -> String {
        "{\"repo\":\"\(repo)\",\"surfaces\":{"
            + "\"pull_requests\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"pull_requests\":[\(pulls.joined(separator: ","))]},"
            + "\"issues\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"issues\":[]},"
            + "\"runs\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"runs\":[\(runs.joined(separator: ","))]},"
            + "\"pages\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"pages\":null},"
            + "\"releases\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"releases\":[]},"
            + "\"tags\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"tags\":[]},"
            + "\"branches\":{\"repo\":\"\(repo)\",\"status\":\"ok\",\"branches\":[\(branches.joined(separator: ","))]}"
            + "}}"
    }

    /// Pins o/a, o/b and x/c; o/a and o/b hydrated from boards, x/c
    /// never fetched.
    private func model(a: String, b: String) async -> GitHubModel {
        func reply(_ data: String) -> CommandResult {
            .init(status: 0, stdout: Data("{\"schema\":1,\"data\":{\(data)}}".utf8), stderr: "")
        }
        let swe = SweClient(runner: GitHubModelTests.Router([
            "gh --json": reply("\(env),\"repos\":[{\"name\":\"o/a\",\"added_at\":1},"
                               + "{\"name\":\"o/b\",\"added_at\":2},{\"name\":\"x/c\",\"added_at\":3}]"),
            "gh board --repo=o/a --json": reply("\(env),\"boards\":[\(a)]"),
            "gh board --repo=o/b --json": reply("\(env),\"boards\":[\(b)]"),
        ]))
        let model = GitHubModel()
        await model.load(swe: swe)
        await model.loadBoards(["o/a"], swe: swe)
        await model.loadBoards(["o/b"], swe: swe)
        return model
    }

    @Test func botsAreDependencyBotsInEitherSpelling() {
        #expect(GitHubBots.isBot("app/dependabot"))
        #expect(GitHubBots.isBot("dependabot[bot]"))
        #expect(GitHubBots.isBot("app/renovate"))
        // Coding agents open real work from app accounts: not "bot".
        #expect(!GitHubBots.isBot("app/devin-ai-integration"))
        #expect(!GitHubBots.isBot("c-wenlong"))
    }

    @Test func dragMovesWithinItsGroupAndLeavesOtherSlots() {
        let pins = ["o/a", "x/c", "o/b", "o/d"]
        // o/d before o/a inside the o/ group; x/c keeps slot 1.
        #expect(PinOrder.move("o/d", before: "o/a", in: ["o/a", "o/b", "o/d"], pins: pins)
                == ["o/d", "x/c", "o/a", "o/b"])
        // To the end of the group.
        #expect(PinOrder.move("o/a", before: nil, in: ["o/a", "o/b", "o/d"], pins: pins)
                == ["o/b", "x/c", "o/d", "o/a"])
    }

    @Test func countsFollowSearchStateWordsAndUnloadedReposStayUnknown() async {
        let model = await model(
            a: board("o/a", pulls: [pr(1), pr(2, author: "app/dependabot", checks: "failing"),
                                    pr(3, draft: true)]),
            b: board("o/b", pulls: []))
        #expect(model.rowCount("o/a", tab: .pulls) == 3)
        // Loaded and empty is quiet; never fetched is unknown, not quiet.
        #expect(model.rowCount("o/b", tab: .pulls) == 0)
        #expect(model.rowCount("x/c", tab: .pulls) == nil)
        // The search matches the words the row's icons stand for.
        model.filter = "bot"
        #expect(model.rowCount("o/a", tab: .pulls) == 1)
        model.filter = "failing"
        #expect(model.rowCount("o/a", tab: .pulls) == 1)
        model.filter = "draft"
        #expect(model.rowCount("o/a", tab: .pulls) == 1)
        model.filter = "passing"
        #expect(model.rowCount("o/a", tab: .pulls) == 2)
        // A repo shown by fallback filters its rows by the text too.
        model.filter = "f2"
        #expect(model.rowCount("o/a", tab: .pulls) == 1)
    }

    @Test func ciDotReadsTheDefaultBranchsNewestRun() async {
        let model = await model(
            a: board("o/a", pulls: [],
                     runs: [run(3, branch: "feature", conclusion: "failure"),
                            run(2, branch: "main", conclusion: "success")],
                     branches: [branch("main", isDefault: true)]),
            b: board("o/b", pulls: [],
                     runs: [run(5, branch: "dev", status: "in_progress", conclusion: nil)]))
        let other = await self.model(
            a: board("o/a", pulls: [],
                     runs: [run(7, branch: "feature", conclusion: "failure")],
                     branches: [branch("main", isDefault: true)]),
            b: board("o/b", pulls: [],
                     runs: [run(8, branch: "main", conclusion: "timed_out")]))
        // Default branch known but absent from the page: unknown, not
        // the feature branch's red.
        #expect(other.ciState("o/a") == .unknown)
        // A timeout is a failure for the dot and the chip alike.
        #expect(other.ciState("o/b") == .failing)
        other.filter = "failing"
        #expect(other.rowCount("o/b", tab: .actions) == 1)
        // A failing feature branch doesn't paint the repo red.
        #expect(model.ciState("o/a") == .passing)
        // No default branch known: the newest run at all.
        #expect(model.ciState("o/b") == .running)
        #expect(model.ciState("x/c") == nil)
    }

    @Test func listNarrowsToTheRailPickAndAllReposDropsOneRepoTabs() async {
        let model = await model(a: board("o/a", pulls: [pr(1)]), b: board("o/b", pulls: []))
        #expect(model.listedRepos.map(\.name) == ["o/a", "o/b", "x/c"])
        model.shownRepo = "o/b"
        model.tab = .releases
        #expect(model.listedRepos.map(\.name) == ["o/b"])
        #expect(model.tab == .releases)
        model.shownRepo = nil
        #expect(model.tab == .pulls)
        // A tab every view has stays put.
        model.tab = .issues
        model.shownRepo = "o/a"
        model.shownRepo = nil
        #expect(model.tab == .issues)
    }
}
