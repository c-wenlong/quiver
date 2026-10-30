import XCTest
@testable import QuiverRemote

final class TasksDecodingTests: XCTestCase {
    /// `GET /v1/tasks` as the hub answers it: the store over the
    /// sources' open items, with a failed repo and a blocked Linear.
    private static let listJSON = """
    {
      "tasks": [
        {"id": "github:issue:o/r:5", "source": "github", "kind": "issue",
         "repo": "o/r", "ref": "5", "title": "Fix the thing",
         "url": "https://github.com/o/r/issues/5",
         "state": "in_progress", "status": "Agent running", "sessions": ["claude@/w:abc"],
         "launches": ["l-1"], "agent_running": true, "linked": true,
         "last_active_at": 1767323000.0, "created_at": 1767320000.0,
         "updated_at": "2026-01-02T03:04:05Z",
         "pr": {"number": 9, "title": "fix", "state": "open",
                "draft": false, "url": "https://github.com/o/r/pull/9"}},
        {"id": "github:pull:o/r:9", "source": "github", "kind": "pull",
         "repo": "o/r", "ref": "9", "title": "fix",
         "url": "https://github.com/o/r/pull/9",
         "state": "open", "sessions": [], "launches": [],
         "agent_running": false, "linked": false,
         "last_active_at": 1767322000.0,
         "pr": {"number": 9, "title": "fix", "state": "open",
                "draft": true, "url": "https://github.com/o/r/pull/9"}},
        {"id": "linear:issue:qui:qui-123", "source": "linear", "kind": "issue",
         "project": "QUI", "ref": "QUI-123", "title": "Linear card",
         "url": "https://linear.app/t/QUI-123",
         "state": "open", "sessions": [], "launches": [],
         "agent_running": false, "linked": false,
         "last_active_at": 1767321000.0}
      ],
      "repos": [{"name": "o/s", "status": "upstream-error",
                 "error": "GitHub is down"}],
      "machine": "kai-macbook",
      "machine_id": "m-1",
      "github": {"status": "ok", "login": "octocat", "error": null},
      "linear": {"status": "auth-required", "login": null,
                 "error": "linear.key_ref is unset"},
      "fetched_at": 1767323045.0
    }
    """

    func testDecodesTheListReply() throws {
        let body = try Hub.decoder.decode(TasksResponse.self, from: Data(Self.listJSON.utf8))
        XCTAssertEqual(body.tasks.count, 3)
        XCTAssertEqual(body.machineId, "m-1")
        XCTAssertNil(body.envBlocked)
        XCTAssertEqual(body.linear?.status, "auth-required")
        XCTAssertEqual(body.fetchedAt, 1767323045.0)

        let issue = body.tasks[0]
        XCTAssertEqual(issue.id, "github:issue:o/r:5")
        XCTAssertEqual(issue.scope, "o/r")
        XCTAssertEqual(issue.refLabel, "o/r#5")
        XCTAssertEqual(issue.number, 5)
        XCTAssertEqual(issue.state, "in_progress")
        XCTAssertEqual(issue.status, "Agent running")
        XCTAssertEqual(issue.stateLabel, "Agent running")
        XCTAssertEqual(issue.sessions, ["claude@/w:abc"])
        XCTAssertEqual(issue.agentRunning, true)
        XCTAssertEqual(issue.linked, true)
        XCTAssertEqual(issue.pr?.number, 9)
        XCTAssertEqual(issue.pr?.state, "open")
        XCTAssertFalse(issue.isDone)

        let pull = body.tasks[1]
        XCTAssertEqual(pull.kind, "pull")
        XCTAssertEqual(pull.pr?.draft, true)

        let linear = body.tasks[2]
        XCTAssertEqual(linear.scope, "QUI")
        XCTAssertEqual(linear.refLabel, "QUI-123")
        XCTAssertNil(linear.repo)
        XCTAssertNil(linear.number)

        let failed = body.repos?.first
        XCTAssertEqual(failed?.name, "o/s")
        XCTAssertEqual(failed?.error, "GitHub is down")
    }

    func testRefBodyIsWhatTheLinkRouteTakes() throws {
        let body = try Hub.decoder.decode(TasksResponse.self, from: Data(Self.listJSON.utf8))
        let issue = body.tasks[0].refBody
        XCTAssertEqual(issue["source"] as? String, "github")
        XCTAssertEqual(issue["kind"] as? String, "issue")
        XCTAssertEqual(issue["repo"] as? String, "o/r")
        XCTAssertEqual(issue["ref"] as? String, "5")
        XCTAssertNil(issue["project"])
        let linear = body.tasks[2].refBody
        XCTAssertEqual(linear["project"] as? String, "QUI")
        XCTAssertNil(linear["repo"])
    }
}

final class TaskRowTests: XCTestCase {
    private func row(_ id: String, source: String? = "github",
                     kind: String? = "issue", ref: String? = "5",
                     repo: String? = "o/r", project: String? = nil,
                     title: String? = "t", url: String? = "u",
                     state: String? = "open", running: Bool? = false) -> TaskRow {
        TaskRow(id: id, source: source, kind: kind, ref: ref, title: title,
                url: url, repo: repo, project: project, state: state,
                sessions: nil, launches: nil, agentRunning: running,
                linked: nil, lastActiveAt: nil, createdAt: nil,
                updatedAt: nil, pr: nil)
    }

    func testAnEmptyPageSaysGitHubIsOutOnce() {
        let signedOut = "gh is not authenticated: run `gh auth login`"
        // Rows on screen: the banner says they're last-known, and why.
        XCTAssertEqual(TaskGrouping.githubBanner(envBlocked: signedOut, githubFailed: nil, hasRows: true), signedOut)
        XCTAssertEqual(TaskGrouping.githubBanner(envBlocked: nil, githubFailed: "timed out", hasRows: true), "timed out")
        // No rows: the empty card says it; a banner would repeat it.
        XCTAssertNil(TaskGrouping.githubBanner(envBlocked: signedOut, githubFailed: nil, hasRows: false))
        XCTAssertNil(TaskGrouping.githubBanner(envBlocked: nil, githubFailed: "timed out", hasRows: false))
        XCTAssertNil(TaskGrouping.githubBanner(envBlocked: nil, githubFailed: nil, hasRows: true))
    }

    func testFiltersPickByStateAndAgent() {
        let running = row("a", state: "in_progress", running: true)
        let prOpen = row("b", state: "pr_open")
        let pull = row("c", kind: "pull", state: "open")
        let merged = row("d", state: "merged")
        let open = row("e")

        XCTAssertTrue(TaskFilter.running.includes(running))
        XCTAssertFalse(TaskFilter.running.includes(open))
        // A row whose flag never arrived doesn't count as running.
        XCTAssertFalse(TaskFilter.running.includes(row("f", running: nil)))

        XCTAssertTrue(TaskFilter.prOpen.includes(prOpen))
        // A pull task is an open PR until it lands.
        XCTAssertTrue(TaskFilter.prOpen.includes(pull))
        XCTAssertTrue(TaskFilter.prOpen.includes(row("g", kind: "pull", state: "in_progress")))
        XCTAssertFalse(TaskFilter.prOpen.includes(open))
        XCTAssertFalse(TaskFilter.prOpen.includes(merged))

        XCTAssertTrue(TaskFilter.done.includes(merged))
        XCTAssertTrue(TaskFilter.done.includes(row("h", state: "closed")))
        XCTAssertFalse(TaskFilter.done.includes(open))
        XCTAssertTrue(TaskFilter.all.includes(open))
    }

    func testSectionsGroupByScopeInReplyOrder() {
        let tasks = [
            row("b", repo: "o/s"),
            row("a", repo: "o/r"),
            row("c", repo: "o/r"),
            row("d", source: "linear", ref: "QUI-1", repo: nil, project: "QUI"),
            row("e", source: "notion", kind: "page", ref: "x", repo: nil),
        ]
        let sections = TaskGrouping.sections(of: tasks, filter: .all)
        // A row with no repo or project lists under its source's name.
        XCTAssertEqual(sections.map(\.name), ["o/s", "o/r", "QUI", "Notion"])
        XCTAssertEqual(sections[1].rows?.map(\.id), ["a", "c"])
        XCTAssertEqual(TaskGrouping.count(tasks, filter: .all), 5)

        // A scope whose rows all filter out drops its section.
        let none = TaskGrouping.sections(
            of: [row("a", running: false)], filter: .running)
        XCTAssertTrue(none.isEmpty)
    }

    /// Scope-less rows group as the web and the Mac group them: GitHub
    /// spelled GitHub, Linear under "Linear", and "Other" only for a row
    /// that names no source at all. An empty repo counts as none.
    func testScopelessRowsGroupUnderTheirSourceName() {
        let tasks = [
            row("a", source: "linear", ref: "QUI-1", repo: nil, project: nil),
            row("b", repo: nil),
            row("c", source: "jira", ref: "J-1", repo: "", project: nil),
            row("d", source: nil, kind: "page", ref: "x", repo: nil),
            row("e", source: "linear", ref: "QUI-2", repo: "", project: "QUI"),
        ]
        XCTAssertEqual(TaskGrouping.sections(of: tasks, filter: .all).map(\.name),
                       ["Linear", "GitHub", "Jira", "Other", "QUI"])
    }

    func testSourceLineAndLabels() {
        XCTAssertEqual(row("a", kind: "issue").sourceLine, "GitHub issue · o/r")
        XCTAssertEqual(row("a", kind: "pull").sourceLine, "GitHub pull request · o/r")
        XCTAssertEqual(row("a", source: "linear", repo: nil, project: "QUI").sourceLine, "Linear · QUI")
        // The ladder is never the text (#875): an issue whose agent has
        // finished reads Open, a running one Agent running.
        XCTAssertEqual(row("a", state: "in_progress", running: false).stateLabel, "Open")
        XCTAssertEqual(row("a", state: "in_progress", running: true).stateLabel, "Agent running")
        XCTAssertEqual(row("a", state: "pr_open").stateLabel, "PR open")
        XCTAssertEqual(row("a", state: "weird").stateLabel, "Open")
        // The hub's own status wins, in the source's words.
        var linear = row("a", source: "linear", repo: nil, project: "QUI", state: "in_progress")
        linear.status = "In Review"
        XCTAssertEqual(linear.stateLabel, "In Review")
        // An older hub sends no status: started Linear work still reads so.
        linear.status = nil
        XCTAssertEqual(linear.stateLabel, "Started")
        // A ref under a repo reads "o/r#5"; a bare ref stays itself.
        XCTAssertEqual(row("a", ref: "QUI-1", repo: nil, project: "QUI").refLabel, "QUI-1")
    }

    func testCopyLinkPrefersTheDeepLinkForIssues() {
        let issue = row("a", url: "https://github.com/o/r/issues/5")
        XCTAssertEqual(issue.copyLink?.absoluteString, "quiver://issue/o/r/5")
        let pull = row("b", kind: "pull", url: "https://github.com/o/r/pull/5")
        XCTAssertEqual(pull.copyLink?.absoluteString, "https://github.com/o/r/pull/5")
        let linear = row("c", source: "linear", ref: "QUI-1", repo: nil, project: "QUI",
                         url: "https://linear.app/t/QUI-1")
        XCTAssertEqual(linear.copyLink?.absoluteString, "https://linear.app/t/QUI-1")
    }
}

final class TaskStepperTests: XCTestCase {
    private func row(kind: String? = "issue", state: String? = "open", source: String? = "github") -> TaskRow {
        TaskRow(id: "t", source: source, kind: kind, ref: "5",
                title: "t", url: "u", repo: "o/r", project: nil,
                state: state, sessions: nil, launches: nil,
                agentRunning: false, linked: nil, lastActiveAt: nil,
                createdAt: nil, updatedAt: nil, pr: nil)
    }

    private func rungs(_ task: TaskRow) -> [TaskStepper.Step] {
        TaskStepper.steps(for: task)
    }

    private func step(_ title: String, _ reached: Bool, _ current: Bool) -> TaskStepper.Step {
        TaskStepper.Step(title: title, reached: reached, current: current)
    }

    func testIssueLadderReachesUpToTheState() {
        XCTAssertEqual(rungs(row(state: "pr_open")),
                       [step("Open", true, false), step("Agent", true, false),
                        step("PR open", true, true), step("Merged", false, false)])
        XCTAssertEqual(rungs(row(state: "merged")).last,
                       step("Merged", true, true))
        XCTAssertEqual(rungs(row()).first, step("Open", true, true))
    }

    func testNonGithubIssueLadderSkipsThePrRung() {
        // Only a GitHub issue's own PR can stand between "an agent is
        // on it" and done; Linear cards and Notion pages use their own
        // started and done.
        let steps = rungs(row(state: "in_progress", source: "linear"))
        XCTAssertEqual(steps.map(\.title), ["Open", "Started", "Done"])
    }

    func testPullLadderSkipsThePrRung() {
        let steps = rungs(row(kind: "pull", state: "in_progress"))
        XCTAssertEqual(steps.map(\.title), ["Open", "Agent", "Merged"])
        XCTAssertEqual(steps[1], step("Agent", true, true))
    }

    func testClosedReplacesTheTerminalRung() {
        let steps = rungs(row(state: "closed"))
        XCTAssertEqual(steps.last, step("Closed", true, true))
        // How far a hand-closed task got isn't inferable: only its
        // ends light up.
        XCTAssertEqual(steps.map(\.reached), [true, false, false, true])
        let pull = rungs(row(kind: "pull", state: "closed"))
        XCTAssertEqual(pull.last, step("Closed", true, true))
    }

    func testUnknownStatesReadAsOpen() {
        XCTAssertEqual(rungs(row(state: nil)).first, step("Open", true, true))
        XCTAssertEqual(rungs(row(state: "triage")).first, step("Open", true, true))
    }
}
