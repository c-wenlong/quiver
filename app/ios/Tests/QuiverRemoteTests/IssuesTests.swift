import XCTest
@testable import QuiverRemote

final class IssuesDecodingTests: XCTestCase {
    /// `GET /v1/issues` as the hub answers it.
    private static let listJSON = """
    {
      "repos": [
        {"name": "o/r", "status": "ok", "issues": [
          {"number": 5, "title": "Fix the thing", "state": "open",
           "author": "octocat", "labels": ["bug"], "assignees": ["bob"],
           "comments": 3, "updated_at": "2026-01-02T03:04:05Z",
           "url": "https://github.com/o/r/issues/5",
           "mine": true, "agent_running": true},
          {"number": 7, "title": "Widen the net", "state": "open",
           "author": "other", "labels": [], "assignees": [],
           "comments": 0, "updated_at": "2026-01-02T03:04:05.123Z",
           "url": "https://github.com/o/r/issues/7",
           "mine": false}
        ]},
        {"name": "o/s", "status": "upstream-error", "issues": [],
         "error": "GitHub is down"}
      ],
      "machine": "kai-macbook",
      "machine_id": "m-1",
      "github": {"status": "ok", "login": "octocat", "error": null},
      "fetched_at": 1767323045.0
    }
    """

    func testDecodesTheListReply() throws {
        let body = try Hub.decoder.decode(IssuesResponse.self, from: Data(Self.listJSON.utf8))
        XCTAssertEqual(body.repos.count, 2)
        XCTAssertEqual(body.machineId, "m-1")
        XCTAssertEqual(body.github?.login, "octocat")
        XCTAssertNil(body.envBlocked)

        let repo = body.repos[0]
        XCTAssertEqual(repo.status, "ok")
        let five = repo.issues![0]
        XCTAssertEqual(five.number, 5)
        XCTAssertEqual(five.mine, true)
        // `agent_running` decodes when a newer hub sends it…
        XCTAssertEqual(five.agentRunning, true)
        // …and stays absent on rows and hubs that don't.
        XCTAssertNil(repo.issues![1].agentRunning)
        XCTAssertEqual(five.labels, ["bug"])
        XCTAssertEqual(five.assignees, ["bob"])
        XCTAssertEqual(five.comments, 3)
        XCTAssertEqual(five.updatedDate, GitHubTime.date("2026-01-02T03:04:05Z"))
        XCTAssertEqual(repo.issues![1].updatedDate,
                       GitHubTime.date("2026-01-02T03:04:05.123Z"))

        let failed = body.repos[1]
        XCTAssertEqual(failed.status, "upstream-error")
        XCTAssertEqual(failed.error, "GitHub is down")
        XCTAssertEqual(failed.issues?.count, 0)
    }

    func testDecodesTheDetailReply() throws {
        let json = """
        {
          "github": {"status": "ok", "login": "octocat", "error": null},
          "repo": "o/r", "machine": "kai-macbook", "machine_id": "m-1",
          "status": "ok",
          "issue": {
            "number": 5, "title": "Fix the thing", "state": "open",
            "author": "alice", "labels": ["bug"], "assignees": ["bob"],
            "url": "https://github.com/o/r/issues/5",
            "body": "It breaks.", "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T03:04:05Z",
            "comments": [{"author": "carol", "body": "Same here.",
                          "created_at": "2026-01-02T00:00:00Z",
                          "url": "https://github.com/o/r/issues/5#issuecomment-1"}],
            "branch": "quiver/issue-5",
            "worktree": "/home/u/.quiver/worktrees/o__r-issue5",
            "suggested_prompt": "Resolve o/r#5 — Fix the thing\\n…"
          },
          "fetched_at": 1767323045.0
        }
        """
        let body = try Hub.decoder.decode(IssueResponse.self, from: Data(json.utf8))
        XCTAssertEqual(body.status, "ok")
        let issue = try XCTUnwrap(body.issue)
        XCTAssertEqual(issue.number, 5)
        XCTAssertEqual(issue.body, "It breaks.")
        XCTAssertEqual(issue.comments?.first?.author, "carol")
        XCTAssertEqual(issue.branch, "quiver/issue-5")
        XCTAssertEqual(issue.worktree, "/home/u/.quiver/worktrees/o__r-issue5")
        XCTAssertTrue(issue.suggestedPrompt?.hasPrefix("Resolve o/r#5") == true)
    }
}

final class IssuesModelTests: XCTestCase {
    @MainActor
    func testAFailedClosedFetchLeavesTheOpenListsErrorAlone() async {
        let model = IssuesModel()
        // Nothing listens here, so the fetch fails at once.
        await model.loadClosed(client: HubClient(baseURL: URL(string: "http://127.0.0.1:9")!, credential: "c"))
        XCTAssertNotNil(model.closedError)
        XCTAssertNil(model.error, "the open rows aren't stale")
        XCTAssertTrue(model.closedLoaded)
    }
}

final class IssueGroupingTests: XCTestCase {
    private func repo(_ name: String, status: String = "ok",
                      issues: [IssueRow]? = nil, error: String? = nil) -> IssueRepo {
        IssueRepo(name: name, status: status, issues: issues ?? [], error: error)
    }

    private func row(_ number: Int, mine: Bool? = false,
                     running: Bool? = nil, state: String? = "open") -> IssueRow {
        IssueRow(number: number, title: "Issue \(number)", state: state,
                 author: "a", labels: [], assignees: [], comments: 0,
                 updatedAt: nil, url: "", mine: mine, agentRunning: running)
    }

    func testFiltersPickByTheMineFlag() {
        XCTAssertTrue(IssueFilter.all.includes(row(1)))
        XCTAssertFalse(IssueFilter.mine.includes(row(1, mine: false)))
        XCTAssertTrue(IssueFilter.mine.includes(row(1, mine: true)))
        // A row whose flag never arrived doesn't pretend it was written
        // by the hub's login.
        XCTAssertFalse(IssueFilter.mine.includes(row(1, mine: nil)))
    }

    func testTheClosedChipPicksByState() {
        XCTAssertTrue(IssueFilter.closed.includes(row(1, state: "closed")))
        XCTAssertFalse(IssueFilter.closed.includes(row(1)))
        XCTAssertFalse(IssueFilter.closed.includes(row(1, state: nil)))
        let repos = [repo("o/r", issues: [row(5, state: "closed"), row(7)])]
        let closed = IssueGrouping.sections(of: repos, filter: .closed)
        XCTAssertEqual(closed.first?.rows?.map(\.number), [5])
        XCTAssertEqual(IssueGrouping.count(repos, filter: .closed), 1)
    }

    func testSectionsKeepErrorsAndDropQuietRepos() {
        let repos = [
            repo("o/r", issues: [row(5, mine: true), row(7)]),
            repo("o/quiet", issues: []),
            repo("o/down", status: "upstream-error", error: "down"),
        ]
        let all = IssueGrouping.sections(of: repos, filter: .all)
        XCTAssertEqual(all.map(\.name), ["o/r", "o/down"])
        XCTAssertEqual(all[0].rows?.map(\.number), [5, 7])
        XCTAssertEqual(all[1].error, "down")

        let mine = IssueGrouping.sections(of: repos, filter: .mine)
        XCTAssertEqual(mine.first?.rows?.map(\.number), [5])
        // Under a filter the errored repo still reports itself.
        XCTAssertEqual(mine.map(\.name), ["o/r", "o/down"])

        // A repo whose issues all filter out drops its section too.
        let other = IssueGrouping.sections(
            of: [repo("o/r", issues: [row(5)])], filter: .mine)
        XCTAssertTrue(other.isEmpty)
    }

    func testCountsDriveTheFilterChips() {
        let repos = [repo("o/r", issues: [row(5, mine: true), row(7)]),
                     repo("o/s", issues: [row(3)])]
        XCTAssertEqual(IssueGrouping.count(repos, filter: .all), 3)
        XCTAssertEqual(IssueGrouping.count(repos, filter: .mine), 1)
    }
}

final class IssueLinkTests: XCTestCase {
    func testParsesTheDeepLink() {
        let link = IssueLink(URL(string: "quiver://issue/octo/hello-world/42")!)
        XCTAssertEqual(link, IssueLink(repo: "octo/hello-world", number: 42))
        XCTAssertEqual(link?.url?.absoluteString,
                       "quiver://issue/octo/hello-world/42")
        // A trailing slash or a query changes nothing.
        XCTAssertEqual(IssueLink(URL(string: "quiver://issue/o/r/5/?from=x")!),
                       IssueLink(repo: "o/r", number: 5))
    }

    func testRejectsWhatIsNotAnIssueLink() {
        XCTAssertNil(IssueLink(URL(string: "quiver://pair?host=mac&token=t")!))
        XCTAssertNil(IssueLink(URL(string: "https://github.com/o/r/issues/5")!))
        XCTAssertNil(IssueLink(URL(string: "quiver://issue/o/r")!))
        XCTAssertNil(IssueLink(URL(string: "quiver://issue/o/r/5/x")!))
        XCTAssertNil(IssueLink(URL(string: "quiver://issue/o/r/abc")!))
        XCTAssertNil(IssueLink(URL(string: "quiver://issue/o/r/0")!))
    }

    @MainActor
    func testHandleKeepsAnIssueLinkForTheTabs() async {
        let app = AppModel(hub: nil)
        await app.handle(URL(string: "quiver://issue/o/r/5")!)
        XCTAssertEqual(app.pendingIssue, IssueLink(repo: "o/r", number: 5))
        // A URL the app doesn't read leaves a pending link alone.
        await app.handle(URL(string: "https://example.com/")!)
        XCTAssertEqual(app.pendingIssue, IssueLink(repo: "o/r", number: 5))
    }
}
