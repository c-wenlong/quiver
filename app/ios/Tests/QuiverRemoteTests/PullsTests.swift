import XCTest
@testable import QuiverRemote

final class PullsDecodingTests: XCTestCase {
    /// `GET /v1/pulls` as the hub answers it.
    private static let listJSON = """
    {
      "repos": [
        {"name": "o/r", "status": "ok", "pulls": [
          {"number": 5, "title": "Fix the thing", "state": "open",
           "draft": false, "author": "octocat", "branch": "fix-thing",
           "base": "main", "labels": ["bug"], "mergeable": "mergeable",
           "review_decision": "approved", "checks": "passing",
           "updated_at": "2026-01-02T03:04:05Z",
           "url": "https://github.com/o/r/pull/5",
           "mine": true, "review_requested": false},
          {"number": 7, "title": "Widen the net", "state": "open",
           "draft": true, "author": "other", "branch": "widen",
           "base": "main", "labels": [], "mergeable": "conflicting",
           "review_decision": "review_required", "checks": "failing",
           "updated_at": "2026-01-02T03:04:05.123Z",
           "url": "https://github.com/o/r/pull/7",
           "mine": false, "review_requested": true}
        ]},
        {"name": "o/s", "status": "upstream-error", "pulls": [],
         "error": "GitHub is down"}
      ],
      "machine": "kai-macbook",
      "machine_id": "m-1",
      "github": {"status": "ok", "login": "octocat", "error": null},
      "fetched_at": 1767323045.0
    }
    """

    func testDecodesTheListReply() throws {
        let body = try Hub.decoder.decode(PullsResponse.self, from: Data(Self.listJSON.utf8))
        XCTAssertEqual(body.repos.count, 2)
        XCTAssertEqual(body.machineId, "m-1")
        XCTAssertEqual(body.github?.login, "octocat")
        XCTAssertNil(body.envBlocked)

        let repo = body.repos[0]
        XCTAssertEqual(repo.status, "ok")
        let five = repo.pulls![0]
        XCTAssertEqual(five.number, 5)
        XCTAssertEqual(five.mine, true)
        XCTAssertEqual(five.reviewRequested, false)
        XCTAssertEqual(five.checks, "passing")
        XCTAssertEqual(five.reviewDecision, "approved")
        XCTAssertEqual(five.labels, ["bug"])
        // ISO-8601, plain and fractional-second forms.
        XCTAssertEqual(five.updatedDate, GitHubTime.date("2026-01-02T03:04:05Z"))
        XCTAssertEqual(repo.pulls![1].updatedDate,
                       GitHubTime.date("2026-01-02T03:04:05.123Z"))
        XCTAssertNotNil(repo.pulls![1].updatedDate)

        let failed = body.repos[1]
        XCTAssertEqual(failed.status, "upstream-error")
        XCTAssertEqual(failed.error, "GitHub is down")
        XCTAssertEqual(failed.pulls?.count, 0)
    }

    func testDecodesTheDetailReply() throws {
        let json = """
        {
          "github": {"status": "ok", "login": "octocat", "error": null},
          "repo": "o/r", "machine": "kai-macbook", "machine_id": "m-1",
          "status": "ok",
          "pr": {
            "number": 5, "title": "Fix the thing", "state": "open",
            "draft": false, "author": "octocat", "branch": "fix-thing",
            "base": "main", "labels": ["bug"], "mergeable": "mergeable",
            "review_decision": "approved", "checks": "failing",
            "updated_at": "2026-01-02T03:04:05Z",
            "url": "https://github.com/o/r/pull/5",
            "repo": "o/r", "body": "What broke.", "merge_state": "clean",
            "additions": 10, "deletions": 2, "changed_files": 3,
            "check_runs": [
              {"name": "tests", "workflow": "CI", "status": "completed",
               "conclusion": "failure", "state": "failing",
               "url": "https://github.com/o/r/actions/runs/9"},
              {"name": "lint", "workflow": "CI", "status": "completed",
               "conclusion": "success", "state": "passing", "url": ""}
            ],
            "reviews": [{"author": "rev", "state": "changes_requested",
                         "submitted_at": "2026-01-02T00:00:00Z"}],
            "review_requests": ["octocat"],
            "auto_merge": {"method": "squash", "enabled_at": "2026-01-02T00:00:00Z"},
            "worktree": "/home/u/.swe/worktrees/o__r-pr5",
            "suggested_prompt": "Review https://github.com/o/r/pull/5\\n…"
          },
          "fetched_at": 1767323045.0
        }
        """
        let body = try Hub.decoder.decode(PullResponse.self, from: Data(json.utf8))
        XCTAssertEqual(body.status, "ok")
        let pr = try XCTUnwrap(body.pr)
        XCTAssertEqual(pr.number, 5)
        XCTAssertEqual(pr.body, "What broke.")
        XCTAssertEqual(pr.checkRuns?.count, 2)
        XCTAssertEqual(pr.checkRuns?[0].state, "failing")
        XCTAssertEqual(pr.reviews?.first?.state, "changes_requested")
        XCTAssertEqual(pr.reviewRequests, ["octocat"])
        XCTAssertEqual(pr.autoMerge?.method, "squash")
        XCTAssertEqual(pr.worktree, "/home/u/.swe/worktrees/o__r-pr5")
        XCTAssertTrue(pr.suggestedPrompt?.hasPrefix("Review https://") == true)
    }

    func testAFailedRefreshKeepsThePullShown() throws {
        let shown = try Hub.decoder.decode(PullResponse.self, from: Data("""
        {"status": "ok", "pr": {"number": 5, "title": "Fix the thing"}}
        """.utf8))
        let outage = try Hub.decoder.decode(PullResponse.self, from: Data("""
        {"status": "auth-required", "pr": null, "error": "gh isn't signed in"}
        """.utf8))
        let kept = PullDetailView.landing(outage, over: shown)
        XCTAssertEqual(kept.reply?.pr?.title, "Fix the thing")
        XCTAssertEqual(kept.error, "gh isn't signed in")
        // With nothing shown yet, the failure is the page.
        let first = PullDetailView.landing(outage, over: nil)
        XCTAssertNil(first.reply?.pr)
        XCTAssertEqual(first.reply?.status, "auth-required")
        XCTAssertNil(first.error)
        XCTAssertEqual(PullDetailView.landing(shown, over: nil).reply?.pr?.number, 5)
    }

    func testGitHubTimeParsesBothStampShapes() {
        XCTAssertNotNil(GitHubTime.date("2026-01-02T03:04:05Z"))
        XCTAssertNotNil(GitHubTime.date("2026-01-02T03:04:05.123Z"))
        XCTAssertNil(GitHubTime.date(nil))
        XCTAssertNil(GitHubTime.date(""))
        XCTAssertNil(GitHubTime.date("not a date"))
    }
}

final class PullGroupingTests: XCTestCase {
    private func repo(_ name: String, status: String = "ok",
                      pulls: [PullRow]? = nil, error: String? = nil) -> PullRepo {
        PullRepo(name: name, status: status, pulls: pulls ?? [], error: error)
    }

    private func row(_ number: Int, mine: Bool? = false,
                     requested: Bool? = false) -> PullRow {
        PullRow(number: number, title: "PR \(number)", state: "open",
                draft: false, author: "a", branch: "b", base: "main",
                labels: [], mergeable: "mergeable",
                reviewDecision: "none", checks: "passing",
                updatedAt: nil, url: "", mine: mine,
                reviewRequested: requested)
    }

    func testFiltersPickByViewerFlags() {
        XCTAssertTrue(PullFilter.all.includes(row(1)))
        XCTAssertFalse(PullFilter.mine.includes(row(1, mine: false)))
        XCTAssertTrue(PullFilter.mine.includes(row(1, mine: true)))
        XCTAssertTrue(PullFilter.reviewRequested.includes(row(1, requested: true)))
        // A PR whose review flag never arrived (status fetch failed)
        // doesn't pretend it was requested.
        XCTAssertFalse(PullFilter.reviewRequested.includes(row(1, requested: nil)))
        XCTAssertFalse(PullFilter.mine.includes(row(1, mine: nil)))
    }

    func testSectionsKeepErrorsAndDropQuietRepos() {
        let repos = [
            repo("o/r", pulls: [row(5, mine: true), row(7, requested: true)]),
            repo("o/quiet", pulls: []),
            repo("o/down", status: "upstream-error", error: "down"),
        ]
        let all = PullGrouping.sections(of: repos, filter: .all)
        XCTAssertEqual(all.map(\.name), ["o/r", "o/down"])
        XCTAssertEqual(all[0].rows?.map(\.number), [5, 7])
        XCTAssertEqual(all[1].error, "down")

        let mine = PullGrouping.sections(of: repos, filter: .mine)
        XCTAssertEqual(mine.first?.rows?.map(\.number), [5])
        // Under a filter the errored repo still reports itself.
        XCTAssertEqual(mine.map(\.name), ["o/r", "o/down"])

        // A repo whose pulls all filter out drops its section too.
        let other = PullGrouping.sections(
            of: [repo("o/r", pulls: [row(5)])], filter: .reviewRequested)
        XCTAssertTrue(other.isEmpty)
    }

    func testCountsDriveTheFilterChips() {
        let repos = [repo("o/r", pulls: [row(5, mine: true), row(7, requested: true)]),
                     repo("o/s", pulls: [row(3)])]
        XCTAssertEqual(PullGrouping.count(repos, filter: .all), 3)
        XCTAssertEqual(PullGrouping.count(repos, filter: .mine), 1)
        XCTAssertEqual(PullGrouping.count(repos, filter: .reviewRequested), 1)
    }
}

final class PullActionsTests: XCTestCase {
    private func pr(_ fields: String) throws -> PullDetail {
        try Hub.decoder.decode(PullDetail.self, from: Data("""
        {"number": 5, "title": "Fix the thing", "base": "main", "state": "open", \(fields)}
        """.utf8))
    }

    func testMergeShowsOnOpenPullsOnly() throws {
        XCTAssertTrue(PullActions.offersMerge(try pr(#""draft": false"#)))
        let merged = try Hub.decoder.decode(PullDetail.self, from: Data("""
        {"number": 5, "state": "merged"}
        """.utf8))
        XCTAssertFalse(PullActions.offersMerge(merged))
    }

    func testBlocksUseTheMacAndWebWords() throws {
        let clean = try pr(#""draft": false, "mergeable": "mergeable""#)
        XCTAssertNil(PullActions.mergeBlock(clean))
        XCTAssertNil(PullActions.autoBlock(clean))
        XCTAssertNil(PullActions.blockNote(clean))

        let draft = try pr(#""draft": true, "mergeable": "mergeable""#)
        XCTAssertEqual(PullActions.mergeBlock(draft), "Draft PRs can't merge")
        XCTAssertEqual(PullActions.autoBlock(draft), "Draft PRs can't merge")
        // One reason, said once.
        XCTAssertEqual(PullActions.blockNote(draft), "Draft PRs can't merge")

        // Conflicts stop Merge only: auto-merge can still be armed.
        let conflicts = try pr(#""draft": false, "mergeable": "conflicting""#)
        XCTAssertEqual(PullActions.block(.merge, conflicts), "Resolve the conflicts first")
        XCTAssertNil(PullActions.block(.auto, conflicts))

        let armed = try pr(#""draft": false, "mergeable": "conflicting", "auto_merge": {"method": "rebase"}"#)
        XCTAssertEqual(PullActions.autoBlock(armed), "Auto-merge is already armed")
        XCTAssertEqual(PullActions.blockNote(armed),
                       "Resolve the conflicts first. Auto-merge is already armed")
    }

    func testTheConfirmNamesExactlyWhatHappens() throws {
        let open = try pr(#""draft": false"#)
        XCTAssertEqual(
            PullActions.confirmMessage(.merge, open, repo: "o/r", method: .squash),
            "Merge #5 Fix the thing into main of o/r with Squash and merge? This happens on GitHub now and can't be undone from here.")
        XCTAssertEqual(
            PullActions.confirmMessage(.auto, open, repo: "o/r", method: .rebase),
            "Arm auto-merge for #5 Fix the thing in o/r with Rebase and merge? GitHub merges it into main once its checks pass.")
        XCTAssertEqual(PullActions.confirmButton(.merge, open), "Merge #5")
        XCTAssertEqual(PullActions.confirmButton(.auto, open), "Arm auto-merge")
        XCTAssertEqual(MergeMethod.allCases.map(\.title),
                       ["Squash and merge", "Rebase and merge", "Create a merge commit"])
    }

    func testRunIdComesFromTheActionsJobLink() {
        XCTAssertEqual(PullActions.runId("https://github.com/o/r/actions/runs/123456/job/789", repo: "o/r"), 123456)
        XCTAssertEqual(PullActions.runId("https://github.com/O/R/actions/runs/9", repo: "o/r"), 9)
        // Another repo's run, another app's check, a status context, junk.
        XCTAssertNil(PullActions.runId("https://github.com/fork/r/actions/runs/9/job/1", repo: "o/r"))
        XCTAssertNil(PullActions.runId("https://github.com/o/r/runs/42", repo: "o/r"))
        XCTAssertNil(PullActions.runId("https://ci.example.com/o/r/actions/runs/9", repo: "o/r"))
        XCTAssertNil(PullActions.runId("https://github.com/o/r/actions/runs/abc", repo: "o/r"))
        XCTAssertNil(PullActions.runId("", repo: "o/r"))
        XCTAssertNil(PullActions.runId(nil, repo: "o/r"))
    }

    func testReRunFailedNeedsAFailingCheckInThatRun() throws {
        let checks = try Hub.decoder.decode([CheckRun].self, from: Data("""
        [{"name": "tests", "state": "failing", "url": "https://github.com/o/r/actions/runs/9/job/1"},
         {"name": "lint", "state": "passing", "url": "https://github.com/o/r/actions/runs/9/job/2"},
         {"name": "build", "state": "passing", "url": "https://github.com/o/r/actions/runs/10/job/3"}]
        """.utf8))
        XCTAssertTrue(PullActions.runFailed(9, in: checks, repo: "o/r"))
        XCTAssertFalse(PullActions.runFailed(10, in: checks, repo: "o/r"))
    }

    func testWritesPostTheWebAppsBodies() throws {
        let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c")
        let merge = client.mergeRequest(repo: "o/r", number: 5, method: .rebase, auto: true)
        XCTAssertEqual(merge.httpMethod, "POST")
        XCTAssertEqual(merge.url?.path, "/v1/app/repositories/merge")
        let mergeBody = try XCTUnwrap(JSONSerialization.jsonObject(with: merge.httpBody ?? Data()) as? [String: Any])
        XCTAssertEqual(mergeBody["repo"] as? String, "o/r")
        XCTAssertEqual(mergeBody["number"] as? Int, 5)
        XCTAssertEqual(mergeBody["method"] as? String, "rebase")
        XCTAssertEqual(mergeBody["auto"] as? Bool, true)

        let rerun = client.rerunRequest(repo: "o/r", run: 123, failed: true)
        XCTAssertEqual(rerun.url?.path, "/v1/app/repositories/rerun")
        let rerunBody = try XCTUnwrap(JSONSerialization.jsonObject(with: rerun.httpBody ?? Data()) as? [String: Any])
        XCTAssertEqual(rerunBody["run"] as? Int, 123)
        XCTAssertEqual(rerunBody["failed"] as? Bool, true)
    }
}
